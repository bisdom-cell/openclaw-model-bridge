#!/usr/bin/env python3
"""wa_channel_status.py — V37.9.162 wa_keepalive WhatsApp 频道链接状态监控 helper

2026-06-16 血案闭环：WhatsApp session 被服务端登出后静默 7 小时
- 凌晨 6 小时重连风暴 (428/499/503) → 08:34 `session logged out` → channel exited
- Gateway HTTP:18789 全程健康 (200)，Discord 全程 connected，但 WhatsApp 频道已死
- wa_keepalive 只探 Gateway HTTP 端口 → 对频道链接状态完全盲 → 零告警 → 用户上午发现

根因：wa_keepalive 名为"WhatsApp 保活"实际只查 Gateway 端口存活，与频道链接状态解耦。
  Gateway 健康 ≠ WhatsApp 频道在线（频道是 Gateway 内的一个 channel，会独立 logged out）。
修复：解析 `openclaw channels status` 输出的 WhatsApp 行，当频道含明确负面信号
  (not linked / disconnected / stopped / error:) 而 Gateway 仍健康时，由 wa_keepalive
  升级到 Discord #alerts（MR-14 alert-path-must-not-depend-on-failing-subject —
  WhatsApp 已死必须走 Discord，绝不走 WhatsApp 自身）。

设计要点（与 movespeed_incident_monitor.py 同款 FAIL-OPEN 哲学）：
- 只在"频道存在 + 有明确负面信号"时 escalate=1，避免格式变化/不确定状态误报。
- WhatsApp 行缺失 → present=0, escalate=0（FAIL-OPEN：输出格式变了不告警，但可观测）。
- 解析异常 → escalate=0（绝不因 helper 自身 bug 制造告警）。
- 输出 reason 绝不包含手机号（只拼负面信号词，allow:+... token 永不进 reason）。

`openclaw channels status` 是轻量调用（查询运行中的 Gateway 守护进程，CLI 进程不加载
插件、无 plugin staging churn），适合每 30min keepalive 调用。

Output 格式 (single line stdout): "{escalate}|{present}|{action}|{auto}|{reason}"
  escalate: "1" 当 WhatsApp 行存在且含明确负面信号；否则 "0"
  present:  "1" 当找到 WhatsApp 行；否则 "0"
  action:   告警该建议的恢复动作 — "login" | "restart" | "none"（V37.9.374）
  auto:     "1" 当且仅当 wa_keepalive 可以替人执行 restart.sh（V37.9.376）：
            action=restart 且行内有精确 token "linked" 与 "stopped"——这是 4.27 建连阶段
            失败后的终止态（405 / 408 两次血案的形态），重启无损且一次即恢复；
            其余告警态（如仅 disconnected、未知形态）不自动重启，只给人工建议。
  reason:   人类可读状态摘要（不含手机号）；放最后一段，内含 "|" 也不会切错

V37.9.376（2026-10-27 用户决策 b）：10-24 22:44 第二次建连终止（408 握手超时）跨周末
  停摆约 60h，告警每 3h 送达 Discord 无人处理，而恢复动作与 10-20 完全相同（restart.sh，
  不扫码）。wa_keepalive 据本字段有界自动执行 restart.sh（每 6h 至多 1 次 / 24h 至多 2 次，
  仍发告警并写明结果）。判定刻意收窄到「linked + stopped」：not linked / 已登出 → 扫码是
  人工动作；仅 disconnected 可能是重连中的瞬态，不该被一次重启打断。

V37.9.374（2026-10-20 血案）：告警原本不分状态一律建议「扫码登录」。10-20 02:28
  服务端 428 断开 → 4.27 重建连接时取 WhatsApp Web 版本号失败、静默回退到内置旧版本
  → 握手 405 → 建连阶段的失败不重试，监听循环直接退出 → 频道 stopped 12.5h。
  当时频道仍 linked、凭据有效，重启 Gateway 一次即恢复；扫码不仅无效（登录走同一段
  取版本代码，同样 405），还会丢掉仍然有效的会话（6 月 408 血案就是反复扫码撞出来的）。
  判据对齐上游自己的提示：not linked / 会话被登出 → 扫码；仍 linked 但停了 → 先重启。

CLI:
  openclaw channels status | python3 wa_channel_status.py
  python3 wa_channel_status.py < status.txt
"""

from __future__ import annotations

import re
import sys


# 频道行内可能出现的明确负面信号 token（comma-split 后精确匹配，避免 "linked" 子串误判）
_NEGATIVE_TOKENS = ("not linked", "disconnected", "stopped")

# 会话被服务端登出的错误特征（V37.9.374）：Baileys UNAUTHORIZED_CODES = 401/403/419，
# 以及 4.27 的 "session logged out" 文案。刻意要求 "logged out" 带空格 —— 错误 JSON 里的
# isLoggedOut/loggedOut 字段（值可能是 false）不得误判成已登出。
_LOGGED_OUT_RE = re.compile(
    r'statusCode"?\s*:\s*(?:401|403|419)\b|\blogged out\b', re.IGNORECASE
)


def _find_whatsapp_line(status_text: str) -> str | None:
    """Find the WhatsApp channel line in `openclaw channels status` output.

    扫描每行，返回第一个同时包含 "whatsapp"(大小写不敏感) 和 ":" 的行。
    Gateway 装饰行 (│ ◇ "Gateway reachable.") 和 Discord 行天然不含 "whatsapp"，被跳过。

    Returns:
        匹配行 (str)，或 None（未找到 → 调用方按 FAIL-OPEN 处理）
    """
    if not isinstance(status_text, str) or not status_text:
        return None
    for line in status_text.splitlines():
        low = line.lower()
        if "whatsapp" in low and ":" in line:
            return line
    return None


def parse_whatsapp_state(status_text: str) -> dict:
    """Parse `openclaw channels status` output for WhatsApp channel health.

    Returns dict:
      present:        bool — WhatsApp 行是否找到
      linked:         bool — 精确 token "linked"（"not linked" 不计）
      connected:      bool — 精确 token "connected"（"disconnected" 不计）
      disconnected:   bool
      stopped:        bool
      error:          str | None — "error:" 之后的错误文本（无则 None）
      should_escalate: bool — present 且含明确负面信号才 True（FAIL-OPEN）
      action:         str  — "login"（未链接/已登出，需扫码）| "restart"（仍 linked，
                             先重启 Gateway）| "none"（不告警）
      auto_restart_ok: bool — V37.9.376：action=restart 且 linked 且 stopped，
                             wa_keepalive 可据此有界自动执行 restart.sh
      reason:         str  — 人类可读摘要（不含手机号）
    """
    line = _find_whatsapp_line(status_text)
    if line is None:
        return {
            "present": False,
            "linked": False,
            "connected": False,
            "disconnected": False,
            "stopped": False,
            "error": None,
            "should_escalate": False,
            "action": "none",
            "auto_restart_ok": False,
            "reason": "whatsapp_line_not_found",
        }

    # 取第一个 ":" 之后的部分（"- WhatsApp default:" 之后），再按 "," 切 token
    after = line.split(":", 1)[1] if ":" in line else line
    tokens = [t.strip().lower() for t in after.split(",")]

    not_linked = "not linked" in tokens
    linked = ("linked" in tokens) and not not_linked
    disconnected = "disconnected" in tokens
    connected = ("connected" in tokens) and not disconnected
    stopped = "stopped" in tokens

    # error 提取用 rfind 取行尾（更稳健：错误文本可能含逗号，token 切分会断开）
    low_line = line.lower()
    error = None
    if "error:" in low_line:
        idx = low_line.rfind("error:")
        candidate = line[idx + len("error:"):].strip()
        error = candidate if candidate else None

    has_negative = not_linked or disconnected or stopped or (error is not None)
    has_positive = linked and connected

    if has_negative:
        parts = []
        if not_linked:
            parts.append("not linked")
        if disconnected:
            parts.append("disconnected")
        if stopped:
            parts.append("stopped")
        if error is not None:
            parts.append(f"error={error}")
        reason = "; ".join(parts)
        should_escalate = True
    elif has_positive:
        reason = "connected"
        should_escalate = False
    else:
        # 频道行存在但既无明确正面也无明确负面 → 不确定 → FAIL-OPEN 不告警
        reason = "indeterminate"
        should_escalate = False

    if not should_escalate:
        action = "none"
    elif not_linked or (error is not None and _LOGGED_OUT_RE.search(error)):
        action = "login"
    else:
        action = "restart"

    # V37.9.376: 自动重启只认「仍 linked 且已 stopped」= 4.27 建连阶段失败的终止态；
    # 这里 linked 已排除 "not linked"，action=restart 已排除登出码。
    auto_restart_ok = (action == "restart") and linked and stopped

    return {
        "present": True,
        "linked": linked,
        "connected": connected,
        "disconnected": disconnected,
        "stopped": stopped,
        "error": error,
        "should_escalate": should_escalate,
        "action": action,
        "auto_restart_ok": auto_restart_ok,
        "reason": reason,
    }


def format_keepalive_output(state: dict) -> str:
    """Format keepalive-consumable output: '{escalate}|{present}|{action}|{auto}|{reason}'."""
    escalate = "1" if state.get("should_escalate") else "0"
    present = "1" if state.get("present") else "0"
    action = state.get("action") or "none"
    auto = "1" if state.get("auto_restart_ok") else "0"
    reason = state.get("reason", "")
    return f"{escalate}|{present}|{action}|{auto}|{reason}"


def _cli() -> None:
    # 读 stdin（管道）或文件参数
    try:
        if len(sys.argv) >= 2:
            with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            text = sys.stdin.read()
    except (IOError, OSError):
        # FAIL-OPEN：读取失败不告警
        print("0|0|none|0|read_error")
        return

    try:
        state = parse_whatsapp_state(text)
        print(format_keepalive_output(state))
    except Exception:  # noqa: BLE001 — FAIL-OPEN：解析 bug 绝不制造告警
        print("0|0|none|0|parse_error")


if __name__ == "__main__":
    _cli()

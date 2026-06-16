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

Output 格式 (single line stdout): "{escalate}|{present}|{reason}"
  escalate: "1" 当 WhatsApp 行存在且含明确负面信号；否则 "0"
  present:  "1" 当找到 WhatsApp 行；否则 "0"
  reason:   人类可读状态摘要（不含手机号）

CLI:
  openclaw channels status | python3 wa_channel_status.py
  python3 wa_channel_status.py < status.txt
"""

from __future__ import annotations

import sys


# 频道行内可能出现的明确负面信号 token（comma-split 后精确匹配，避免 "linked" 子串误判）
_NEGATIVE_TOKENS = ("not linked", "disconnected", "stopped")


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

    return {
        "present": True,
        "linked": linked,
        "connected": connected,
        "disconnected": disconnected,
        "stopped": stopped,
        "error": error,
        "should_escalate": should_escalate,
        "reason": reason,
    }


def format_keepalive_output(state: dict) -> str:
    """Format keepalive-consumable output: '{escalate}|{present}|{reason}'."""
    escalate = "1" if state.get("should_escalate") else "0"
    present = "1" if state.get("present") else "0"
    reason = state.get("reason", "")
    return f"{escalate}|{present}|{reason}"


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
        print("0|0|read_error")
        return

    try:
        state = parse_whatsapp_state(text)
        print(format_keepalive_output(state))
    except Exception:  # noqa: BLE001 — FAIL-OPEN：解析 bug 绝不制造告警
        print("0|0|parse_error")


if __name__ == "__main__":
    _cli()

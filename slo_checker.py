#!/usr/bin/env python3
"""
slo_checker.py — SLO 合规检查器（V32: SLO 最小集）

读取 proxy_stats.json 中的 SLO 指标，对比 config.yaml 目标，输出达标/违规状态。
可由 watchdog 定期调用，违规时触发 WhatsApp 告警。

用法：
  python3 slo_checker.py              # 检查并打印 JSON 结果
  python3 slo_checker.py --alert      # 违规时输出告警文本（供 watchdog 使用）
  python3 slo_checker.py --update     # 写入 status.json

退出码（V37.9.322 起三档）：
  0 = 有指标被判定且全部达标（--alert 同时输出一行实测摘要，含 n/a 明细）
  2 = 有违规（--alert 输出告警文本；job_watchdog 只在此档读 stdout）
  3 = 无数据可判（judged_count == 0，如刚重启的 proxy）——既非达标也非违规
"""
import json
import os
import sys
import time

from config_loader import load_config

STATS_FILE = os.path.expanduser("~/proxy_stats.json")
STATUS_JSON = os.path.expanduser("~/.kb/status.json")


def read_stats():
    """读取 proxy_stats.json"""
    if not os.path.exists(STATS_FILE):
        return None
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _metric(name, value, target, unit, direction, ok, samples=None, na_reason=""):
    """构造一条 SLO 结果。

    V37.9.322: 单一规则 —— `samples == 0` 即样本域为空 → `n_a=True`「无数据不可判」。
    `samples is None` = 该 metric 的样本域计数不可得 (旧 schema) → FAIL-OPEN 按可判处理。
    一处构造五个 metric = 一物一形 (原五处各写各的 dict, degradation/timeout/recovery
    连 `samples` 字段都没有, 正是"没有样本概念"才让默认值冒充达标)。
    """
    n_a = samples == 0
    return {
        "name": name,
        "value": value,
        "target": target,
        "unit": unit,
        "direction": direction,
        "ok": ok,
        "samples": samples,
        "n_a": n_a,
        "n_a_reason": na_reason if n_a else "",
    }


def check_slo(stats, config):
    """评估 SLO 达标情况，返回 (results_list, all_ok)。

    V37.9.322 (对抗审计 F1) — 区分「达标」与「无数据可判」:
    统一规则 = 每个 metric 声明自己的**样本域计数**, 计数为 0 → `n_a=True`
    (不可判: 既不算达标也不算违规)。此前 5 个 metric 全部用"默认值 <= 目标"判 ok,
    于是「0 次工具调用」与「0 次失败恢复」这两个**从未被测量过**的指标被算作满分
    通过, 计入 preflight 19/19 那行「✅ SLO 全部达标」。

    实证 (同一份 proxy_stats, 复刻 V37.9.315 生产 E2E 形状: 60 请求 / 0 工具调用 /
    0 失败 streak): slo_benchmark 报「3/5 passed, **2 n/a**」而本模块报「5/5 全部达标」
    —— 两个消费同一 producer 的工具对同一时刻给出不同口径, 且 preflight 与
    job_watchdog 读的是乐观的那个。

    N/A 规则与 slo_benchmark 同源 (镜像 N_A_NO_TOOL_CALLS / N_A_NO_FAILURE_STREAKS,
    V37.9.143/303), 不发明第三套判据 —— 本模块只是把"样本域计数为 0"这一条统一到
    全部 5 个 metric (原 tool_success 已有 tool_total==0 豁免, 其余三项连样本字段都没有)。

    🔴 `ok` 语义与退出码逐字不变 (n_a 的 metric 仍 ok=True, 不产生违规):
    watchdog 告警噪声零增量 —— 镜像 V37.9.320 AL-2 纪律 (不改 ok, 只让呈现层诚实)。
    诚实边界: `failure_streaks` 字段缺失 (V37.9.229 前的旧 schema) → FAIL-OPEN 按可判
    处理, 保持旧行为。
    """
    slo_cfg = config.get("slo", {})
    slo_data = stats.get("slo", {})
    latency = slo_data.get("latency", {})
    total_requests = stats.get("total_requests", 0)
    no_traffic = total_requests == 0  # 无流量时所有 SLO 视为达标
    results = []

    # V37.9.28 F4: 每个 metric 显式声明 direction —
    #   "<" 意为 "actual 应 <= target" (越小越好: latency / 各种 rate 中的"坏率")
    #   ">" 意为 "actual 应 >= target" (越大越好: success_rate / recovery_rate)
    # 修正之前 bug: format_alert 用纠缠 if-else 推断 direction, 导致 3/5 metric
    # 显示反向 (latency_p95 显示 ">" 实际应 "<"; tool_success/auto_recovery 显示 "<"
    # 实际应 ">"). 用户 5/5 周一观察现场: "latency_p95: 54040ms (目标: >30000ms)"
    # — 该方向暗示 ">30000ms 是目标"实际应是"<30000ms 是目标".

    # 1. 延迟 p95 (越小越好)
    p95 = latency.get("p95", 0)
    target_p95 = slo_cfg.get("latency_p95_ms", 30000)
    sample_count = latency.get("count", 0)
    ok = p95 <= target_p95 or sample_count < 5  # 样本太少不告警
    results.append(_metric(
        "latency_p95", p95, target_p95, "ms", "<", ok,
        samples=sample_count, na_reason="无延迟样本"))

    # 2. 工具成功率 (越大越好)
    tool_rate = slo_data.get("tool_success_rate_pct", 100.0)
    target_tool = slo_cfg.get("tool_success_rate_pct", 95.0)
    tool_total = slo_data.get("tool_calls_total", 0)
    ok = tool_rate >= target_tool or tool_total == 0 or no_traffic  # 无数据不告警
    results.append(_metric(
        "tool_success_rate", tool_rate, target_tool, "%", ">", ok,
        samples=tool_total, na_reason="无工具调用"))

    # 3. 降级率 (越小越好)
    deg_rate = slo_data.get("degradation_rate_pct", 0.0)
    target_deg = slo_cfg.get("degradation_rate_pct", 5.0)
    ok = deg_rate <= target_deg
    results.append(_metric(
        "degradation_rate", deg_rate, target_deg, "%", "<", ok,
        samples=total_requests, na_reason="无请求流量"))

    # 4. 超时率 (越小越好)
    timeout_rate = slo_data.get("timeout_rate_pct", 0.0)
    target_timeout = slo_cfg.get("timeout_rate_pct", 3.0)
    ok = timeout_rate <= target_timeout
    results.append(_metric(
        "timeout_rate", timeout_rate, target_timeout, "%", "<", ok,
        samples=total_requests, na_reason="无请求流量"))

    # 5. 自动恢复率 (越大越好)
    recovery = slo_data.get("auto_recovery_rate_pct", 100.0)
    target_recovery = slo_cfg.get("auto_recovery_rate_pct", 90.0)
    ok = recovery >= target_recovery
    # 样本域 = failure_streaks (非请求域) —— 与 slo_benchmark V37.9.303 F3 同源。
    # producer 在 streaks==0 时写死 auto_recovery_rate_pct=100.0 (哨兵值, 不是测量值),
    # 旧代码把这个哨兵当"达标"计入总分 = 从未被验证的能力呈现为满分通过。
    # 字段缺失 (V37.9.229 前旧 schema) → None → FAIL-OPEN 按可判处理, 保持旧行为。
    rec_streaks = slo_data.get("failure_streaks")
    results.append(_metric(
        "auto_recovery_rate", recovery, target_recovery, "%", ">", ok,
        samples=rec_streaks, na_reason="无失败可恢复"))

    # n_a 的 metric 仍 ok=True (见 docstring): all_ok 与退出码逐字不变
    all_ok = all(r["ok"] for r in results)
    return results, all_ok


def judged_summary(results):
    """(judged_count, na_count, na_names) — 供呈现层区分「达标」与「无数据可判」。"""
    na = [r for r in results if r.get("n_a")]
    return len(results) - len(na), len(na), [r["name"] for r in na]


def format_alert(results):
    """格式化违规告警文本.

    V37.9.28 F4: 直接读 v["direction"] (check_slo 写入), 不再用纠缠 if-else 推断.
    旧 bug: 3/5 metric 方向显示错 (latency_p95/tool_success/auto_recovery 颠倒).
    用户 5/5 周一观察 "latency_p95: 54040ms (目标: >30000ms)" 是这个 bug 的现场.
    """
    violations = [r for r in results if not r["ok"]]
    if not violations:
        return ""

    lines = ["⚠️ SLO 违规告警:"]
    for v in violations:
        # direction 由 check_slo 在每个 metric 创建时显式写入 ("<" 或 ">"),
        # 旧 fallback "<" 仅为了向后兼容: 如有旧代码路径未写 direction 字段, 不崩.
        direction = v.get("direction", "<")
        lines.append(f"  🔴 {v['name']}: {v['value']}{v['unit']} (目标: {direction}{v['target']}{v['unit']})")
    return "\n".join(lines)


def _summary_line(results, judged, na_count, na_names):
    """无违规时的实测摘要 —— 让「达标」与「无数据可判」在同一行里分得开。"""
    if na_count == 0:
        return f"SLO 全部达标 ({judged}/{len(results)} 指标已判定)"
    detail = ", ".join(
        f"{r['name']}={r['n_a_reason']}" for r in results if r.get("n_a"))
    return (f"SLO {judged}/{len(results)} 达标, {na_count} 项无数据不可判 ({detail})")


def main():
    stats = read_stats()
    if not stats:
        print(json.dumps({"error": "proxy_stats.json not found or empty"}))
        return 1

    if "slo" not in stats:
        print(json.dumps({"error": "No SLO data in proxy_stats.json (proxy may need restart)"}))
        return 1

    config = load_config()
    results, all_ok = check_slo(stats, config)
    judged, na_count, na_names = judged_summary(results)

    output = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "all_ok": all_ok,
        "total_requests": stats.get("total_requests", 0),
        # V37.9.322: 「达标」与「无数据可判」在数据模型层就分开, 消费方不必猜
        "judged_count": judged,
        "na_count": na_count,
        "results": results,
    }

    if "--alert" in sys.argv:
        alert = format_alert(results)
        if alert:
            print(alert)
            return 2  # 退出码 2 表示有违规
        # V37.9.322 (对抗审计 F1): 无违规时也输出一行**实测**摘要 —— 消费方
        # (preflight 19/19) 此前打印硬编码字符串「SLO 全部达标」而不看实际判了几项,
        # 与 V37.9.319 preflight 硬编码「KB 索引 100% 覆盖」同一 bug 类 (声称的
        # 数字 ≠ 测到的数字), 同文件同家族。
        # 契约扩展安全性: watchdog 只在 RC==2 时读 stdout (job_watchdog.sh:797
        # `if [ "$SLO_RC" -eq 2 ] && [ -n "$SLO_ALERT" ]`), RC==0/3 走 stdout-unused
        # 分支 → 本行对 watchdog 零影响。
        if judged == 0:
            print(f"SLO 无数据可判: 0/{len(results)} 指标有样本 —— 未验证任何 SLO "
                  f"({', '.join(na_names)})")
            return 3  # 退出码 3 = 无数据可判 (既非达标也非违规)
        print(_summary_line(results, judged, na_count, na_names))
        return 0

    if "--update" in sys.argv:
        # V37.9.308 (对抗审计 B-F8, MR-9 state-writes-go-through-helper):
        # 此前是裸 read-modify-write —— 写本身原子 (tmp + os.replace), 但 **RMW 窗口
        # 无锁**: 读(load) 与 replace 之间任何并发写者 (kb_status_refresh 每小时 /
        # full_regression 证据回写 / status_update CLI) 的改动会被整份覆盖静默丢失
        # (V37.9.238 审计 finding C lost-update 家族)。且固定 tmp 名 `.tmp` 在两个
        # slo_checker 并发时互相踩 (V37.9.237 已为此改 pid 后缀)。
        # 日落法 #34 一物一形: 不自建第二套写路径, 收编既有 status_lock + save_status
        # (二者本就是为 "load → mutate → save" 循环设计的)。
        # 顺带闭合一个 dev 脚枪: 本模块的 STATUS_JSON 恒指 ~/.kb/status.json, 而
        # status_update.STATUS_FILE 是 "~/.kb 存在则用之, 否则用仓库副本" —— dev 上
        # 裸写会凭空造出 ~/.kb/status.json, 反过来劫持此后所有写者的路径解析。
        # 生产 (Mac Mini, ~/.kb/status.json 存在) 两者逐字同路径, 零行为变化。
        try:
            import status_update

            with status_update.status_lock():
                status = status_update.load_status()
                status.setdefault("health", {})["slo"] = {
                    "checked_at": output["checked_at"],
                    "all_ok": all_ok,
                    # V37.9.322: status.json 快照同样区分 —— all_ok=True 可能只是
                    # "没有任何指标被判定过", 读者需要看得见分母
                    "judged_count": judged,
                    "na_count": na_count,
                    "summary": {
                        r["name"]: {"value": r["value"], "target": r["target"], "ok": r["ok"]}
                        for r in results
                    },
                }
                status_update.save_status(status, updated_by="slo_checker")
            print(f"SLO status written to {status_update.STATUS_FILE}")
        except (OSError, ImportError) as e:
            print(f"Failed to update status.json: {e}", file=sys.stderr)

    print(json.dumps(output, indent=2, ensure_ascii=False))
    if not all_ok:
        return 2
    return 3 if judged == 0 else 0


if __name__ == "__main__":
    sys.exit(main())

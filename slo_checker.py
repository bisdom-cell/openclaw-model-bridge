#!/usr/bin/env python3
"""
slo_checker.py — SLO 合规检查器（V32: SLO 最小集）

读取 proxy_stats.json 中的 SLO 指标，对比 config.yaml 目标，输出达标/违规状态。
可由 watchdog 定期调用，违规时触发 WhatsApp 告警。

用法：
  python3 slo_checker.py              # 检查并打印 JSON 结果
  python3 slo_checker.py --alert      # 违规时输出告警文本（供 watchdog 使用）
  python3 slo_checker.py --update     # 写入 status.json
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


def check_slo(stats, config):
    """评估 SLO 达标情况，返回 (results_list, all_ok)"""
    slo_cfg = config.get("slo", {})
    slo_data = stats.get("slo", {})
    latency = slo_data.get("latency", {})
    total_requests = stats.get("total_requests", 0)
    no_traffic = total_requests == 0  # 无流量时所有 SLO 视为达标
    results = []

    # 1. 延迟 p95
    p95 = latency.get("p95", 0)
    target_p95 = slo_cfg.get("latency_p95_ms", 30000)
    sample_count = latency.get("count", 0)
    ok = p95 <= target_p95 or sample_count < 5  # 样本太少不告警
    results.append({
        "name": "latency_p95",
        "value": p95,
        "target": target_p95,
        "unit": "ms",
        "ok": ok,
        "samples": sample_count,
    })

    # 2. 工具成功率
    tool_rate = slo_data.get("tool_success_rate_pct", 100.0)
    target_tool = slo_cfg.get("tool_success_rate_pct", 95.0)
    tool_total = slo_data.get("tool_calls_total", 0)
    ok = tool_rate >= target_tool or tool_total == 0 or no_traffic  # 无数据不告警
    results.append({
        "name": "tool_success_rate",
        "value": tool_rate,
        "target": target_tool,
        "unit": "%",
        "ok": ok,
        "samples": tool_total,
    })

    # 3. 降级率
    deg_rate = slo_data.get("degradation_rate_pct", 0.0)
    target_deg = slo_cfg.get("degradation_rate_pct", 5.0)
    ok = deg_rate <= target_deg
    results.append({
        "name": "degradation_rate",
        "value": deg_rate,
        "target": target_deg,
        "unit": "%",
        "ok": ok,
    })

    # 4. 超时率
    timeout_rate = slo_data.get("timeout_rate_pct", 0.0)
    target_timeout = slo_cfg.get("timeout_rate_pct", 3.0)
    ok = timeout_rate <= target_timeout
    results.append({
        "name": "timeout_rate",
        "value": timeout_rate,
        "target": target_timeout,
        "unit": "%",
        "ok": ok,
    })

    # 5. 自动恢复率
    recovery = slo_data.get("auto_recovery_rate_pct", 100.0)
    target_recovery = slo_cfg.get("auto_recovery_rate_pct", 90.0)
    ok = recovery >= target_recovery
    results.append({
        "name": "auto_recovery_rate",
        "value": recovery,
        "target": target_recovery,
        "unit": "%",
        "ok": ok,
    })

    all_ok = all(r["ok"] for r in results)
    return results, all_ok


def format_alert(results):
    """格式化违规告警文本"""
    violations = [r for r in results if not r["ok"]]
    if not violations:
        return ""

    lines = ["⚠️ SLO 违规告警:"]
    for v in violations:
        direction = ">" if v["unit"] == "ms" else "<" if "rate" in v["name"] and "recovery" not in v["name"] else "<"
        if "recovery" in v["name"] or "success" in v["name"]:
            direction = "<"
        lines.append(f"  🔴 {v['name']}: {v['value']}{v['unit']} (目标: {direction}{v['target']}{v['unit']})")
    return "\n".join(lines)


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

    output = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "all_ok": all_ok,
        "total_requests": stats.get("total_requests", 0),
        "results": results,
    }

    if "--alert" in sys.argv:
        alert = format_alert(results)
        if alert:
            print(alert)
            return 2  # 退出码 2 表示有违规
        return 0

    if "--update" in sys.argv:
        # 写入 status.json
        try:
            if os.path.exists(STATUS_JSON):
                with open(STATUS_JSON) as f:
                    status = json.load(f)
            else:
                status = {}
            if "health" not in status:
                status["health"] = {}
            status["health"]["slo"] = {
                "checked_at": output["checked_at"],
                "all_ok": all_ok,
                "summary": {r["name"]: {"value": r["value"], "target": r["target"], "ok": r["ok"]} for r in results},
            }
            tmp = STATUS_JSON + ".tmp"
            with open(tmp, "w") as f:
                json.dump(status, f, indent=2, ensure_ascii=False)
            os.replace(tmp, STATUS_JSON)
            print(f"SLO status written to {STATUS_JSON}")
        except OSError as e:
            print(f"Failed to update status.json: {e}", file=sys.stderr)

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())

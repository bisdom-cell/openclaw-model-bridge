#!/usr/bin/env python3
"""minimal_runtime.py — 10-minute minimal Core Runtime demo (V37.9.144 P1(b)).

外部评审2 "10 分钟最小例子 + 完整生产例子双入口" 兑现:
  - 本 demo = Core Runtime 最小入口 (1 provider + tool governance + 1 policy +
    SLO mini-stats + golden trace), 与 examples/minimal_consumer (governance
    engine 消费方 WeatherBot demo) 互补.
  - 零网络调用 / 零 API key 需求 / Core 步骤零第三方依赖 (stdlib only).
  - Layer-2 policy 步骤需要 PyYAML — 缺失时优雅降级并给安装提示,
    这本身就是 "依赖边界" (README Dependency Boundary) 的活演示.

Golden trace 契约:
  demo 跑完后把本次运行的确定性决策 (工具过滤结果 / fallback 链 / policy limit)
  与已提交的 golden_trace.json 比对 — MATCH 即证明 "你这台机器上的 control plane
  行为与作者生产环境一致". 这是可复现证据, 不是单测替代品.

用法 (从仓库任意位置):
  python3 examples/minimal_runtime/minimal_runtime.py            # 跑 demo + 自校验
  python3 examples/minimal_runtime/minimal_runtime.py --json     # 额外输出 trace JSON
  python3 examples/minimal_runtime/minimal_runtime.py --write-golden  # 重写 golden (维护者)
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# Core 步骤的工具治理走 config/builtin fallback — 确定性且零第三方依赖.
# Layer-2 (ontology engine) 由第 3 步单独演示, 二者解耦 (宪法第一条).
os.environ["ONTOLOGY_MODE"] = "off"

GOLDEN_PATH = os.path.join(_HERE, "golden_trace.json")

# 24 个工具的样例请求: 12 个在治理白名单内 (含 1 个 browser_ 前缀), 12 个越权工具.
# Core Runtime 的第一职责就是把这种"工具洪水"硬截断到模型可驾驭的 ≤12 个.
SAMPLE_TOOL_NAMES = [
    # 白名单内 (proxy_filters.ALLOWED_TOOLS + ALLOWED_PREFIXES)
    "web_search", "web_fetch", "read", "write", "edit", "exec",
    "sessions_spawn", "sessions_history", "cron", "message",
    "memory_search", "browser_navigate",
    # 越权 (应被全部过滤)
    "canvas", "gmail_send", "calendar_create", "spotify_play",
    "db_query", "k8s_deploy", "crypto_trade", "sms_blast",
    "ftp_upload", "telnet_open", "rm_rf", "shell_eval",
]


def _sample_tools():
    return [{"type": "function",
             "function": {"name": n,
                          "description": f"demo tool {n}",
                          "parameters": {"type": "object", "properties": {}}}}
            for n in SAMPLE_TOOL_NAMES]


def step1_provider_registry():
    """1 provider + capability 路由: registry / 矩阵行 / fallback 链 — 全离线."""
    from providers import get_registry
    reg = get_registry()
    names = reg.list_names()
    primary = reg.get("qwen")
    chain = reg.build_fallback_chain("qwen", require_available=False)
    best = reg.find_best_provider(required={"text": True}, prefer=["reasoning"],
                                  require_available=False)
    return {
        "provider_count": len(names),
        "primary": primary.name,
        "primary_models": [m.model_id for m in primary.models],
        "fallback_chain": [p.name for p in chain],
        "best_for_text_prefer_reasoning": best.name if best else None,
    }


def step2_tool_governance():
    """工具治理: 24 个工具进 → 白名单过滤 + schema 清洗 + 自定义工具注入 + 硬截断.

    三类落选/新增必须分开展示 (语义不同):
      whitelist_rejected — 越权工具, 白名单直接拒绝
      cap_truncated      — 白名单放行但被 ≤12 硬截断挤掉 (policy max-tools-per-agent)
      injected_custom    — proxy 注入的自定义工具 (data_clean / search_kb / ...)
    """
    from proxy_filters import filter_tools, is_allowed
    filtered, all_names, _kept = filter_tools(_sample_tools())
    final = [t["function"]["name"] for t in filtered]
    return {
        "input_count": len(all_names),
        "output_count": len(filtered),
        "final_tool_names": final,
        "whitelist_rejected": sorted(n for n in all_names if not is_allowed(n)),
        "cap_truncated": [n for n in all_names if is_allowed(n) and n not in final],
        "injected_custom": [n for n in final if n not in all_names],
    }


def step3_policy_ontology():
    """1 policy: ontology 引擎查询 max-tools-per-agent (Layer 2, 需 PyYAML)."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return {"status": "skipped_no_pyyaml",
                "hint": ("PyYAML 未安装 — 这是 ontology 引擎层依赖, Core Runtime 不需要. "
                         "安装: pip install 'pyyaml>=5.4' 或 pip install openclaw-ontology-engine")}
    try:
        from ontology import engine
        res = engine.evaluate_policy("max-tools-per-agent")
        if not res.get("found"):
            return {"status": "engine_load_failed", "reason": res.get("reason")}
        return {"status": "ok", "policy_id": res["policy_id"], "limit": res["limit"],
                "hard_limit": res["hard_limit"], "type": res["type"]}
    except Exception as e:  # FAIL-OPEN: 引擎任何异常不阻塞 core demo
        return {"status": "engine_error", "reason": f"{type(e).__name__}: {e}"}


def run_demo():
    steps = []
    print("== Minimal Core Runtime demo (Layer 1 core + optional Layer 2 policy) ==")

    t0 = time.time()
    s1 = step1_provider_registry()
    ms1 = int((time.time() - t0) * 1000)
    steps.append(("provider_registry", ms1, True))
    print(f"[1/4] provider registry: {s1['provider_count']} providers, "
          f"primary={s1['primary']}, fallback_chain={s1['fallback_chain']}, "
          f"best(text, prefer reasoning)={s1['best_for_text_prefer_reasoning']}  ({ms1}ms)")

    t0 = time.time()
    s2 = step2_tool_governance()
    ms2 = int((time.time() - t0) * 1000)
    steps.append(("tool_governance", ms2, True))
    print(f"[2/4] tool governance: {s2['input_count']} tools in → "
          f"{s2['output_count']} out — whitelist rejected "
          f"{len(s2['whitelist_rejected'])} (e.g. {s2['whitelist_rejected'][:4]}), "
          f"hard-cap truncated {s2['cap_truncated']}, "
          f"custom injected {s2['injected_custom']}  ({ms2}ms)")

    t0 = time.time()
    s3 = step3_policy_ontology()
    ms3 = int((time.time() - t0) * 1000)
    steps.append(("policy_ontology", ms3, s3["status"] in ("ok", "skipped_no_pyyaml")))
    if s3["status"] == "ok":
        print(f"[3/4] policy (ontology layer): {s3['policy_id']} → limit={s3['limit']} "
              f"hard_limit={s3['hard_limit']}  ({ms3}ms)")
    else:
        print(f"[3/4] policy (ontology layer): SKIP — {s3.get('hint') or s3.get('reason')}")

    ok_steps = sum(1 for _, _, ok in steps if ok)
    total_ms = sum(ms for _, ms, _ in steps)
    max_ms = max(ms for _, ms, _ in steps)
    print(f"[4/4] SLO mini-stats: steps ok {ok_steps}/{len(steps)}, "
          f"total {total_ms}ms, slowest step {max_ms}ms")

    trace = {
        "demo": "minimal_runtime",
        "deterministic": {
            "tool_input_count": s2["input_count"],
            "tool_output_count": s2["output_count"],
            "final_tool_names": s2["final_tool_names"],
            "whitelist_rejected_count": len(s2["whitelist_rejected"]),
            "cap_truncated": s2["cap_truncated"],
            "injected_custom": s2["injected_custom"],
            "fallback_chain": s1["fallback_chain"],
            "best_for_text_prefer_reasoning": s1["best_for_text_prefer_reasoning"],
            "policy": ({"policy_id": s3["policy_id"], "limit": s3["limit"]}
                       if s3["status"] == "ok" else {"status": s3["status"]}),
        },
        "non_deterministic": {
            "provider_count": s1["provider_count"],  # 随插件数变化, 不参与比对
            "step_timings_ms": {name: ms for name, ms, _ in steps},
        },
    }
    return trace


def check_golden(trace):
    """与已提交 golden trace 比对确定性决策. policy 任一侧 skip 则跳过 policy 比对."""
    if not os.path.isfile(GOLDEN_PATH):
        print("golden trace: MISSING (run --write-golden to create)")
        return False
    golden = json.load(open(GOLDEN_PATH, encoding="utf-8"))
    g, c = dict(golden["deterministic"]), dict(trace["deterministic"])
    g_pol, c_pol = g.pop("policy", {}), c.pop("policy", {})
    mismatches = [k for k in g if g[k] != c.get(k)]
    if "limit" in g_pol and "limit" in c_pol and g_pol != c_pol:
        mismatches.append("policy")
    if mismatches:
        print(f"golden trace: MISMATCH on {mismatches} — control plane 行为与参考不一致")
        return False
    skipped = " (policy 比对跳过: 本机无 PyYAML)" if "limit" not in c_pol else ""
    print(f"golden trace: MATCH — 确定性决策与提交的 golden_trace.json 一致{skipped}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Minimal Core Runtime demo (V37.9.144)")
    ap.add_argument("--json", action="store_true", help="额外输出完整 trace JSON")
    ap.add_argument("--write-golden", action="store_true",
                    help="把本次确定性决策写为 golden_trace.json (维护者用)")
    args = ap.parse_args()

    trace = run_demo()
    if args.json:
        print(json.dumps(trace, ensure_ascii=False, indent=2))

    if args.write_golden:
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"golden trace written: {GOLDEN_PATH}")
        return 0

    return 0 if check_golden(trace) else 1


if __name__ == "__main__":
    sys.exit(main())

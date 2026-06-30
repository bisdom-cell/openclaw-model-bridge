#!/usr/bin/env python3
"""llm_observer_selfcheck.py — Observer 自验证 harness + scorecard (研究攻关 #1 Stage 4).

机械化人眼的 sabotage-validate 回路 (论文 arXiv:2606.14589 §5.2 铁律: Observer 自己是
LLM 组件继承全 taxonomy → 须和它评判的组件同等 sabotage 验证)。

复用 adversarial_chaos_audit 的【概念框架】(Category A 回归 / Category B 探索 /
expected_catch / sabotage-validate) —— 但机制不同 (fixture 评估 detect_fail_plausible vs
文件突变 + governance subprocess), 故独立模块非 import (design §5.2)。

scorecard 度量 (design §5.4), 全部【离线可测】(零 LLM/零网络, Layer 1 确定性):
  defense_rate   Category A 回归 case 被 flag 的比例 (sabotage 守护 → 目标 100%)
  fp_rate        干净输出被误 flag 的比例 (原则 #32 噪声本身是问题 → 目标 0%)
  fn_rate_B      Category B 持留/探索集盲区比例 (诚实报告 held-out recall —— design §11
                 核心开放问题: Layer 1 是【回归引擎】非【预测引擎】, 对没专门设计过的
                 novel fail-plausible 模式 FN 高是预期, 印证 audit-as-regression §7.2)
  sabotage       每个 S-detector 关掉后, 它守护的 golden case 立即漏检 (证 load-bearing,
                 §3.5/§5.2 "未验证的探测器和空检测器无法区分")

诚实边界 (design §5.4 + §11):
  - Layer 2 (LLM-judge) 的 real-LLM 准确性/calibration 需 live LLM → 离线不可测, 由
    Mac Mini E2E (Stage 5/6) 度量。本 harness 的 scorecard headline = Layer 1 确定性。
  - detection_latency 需生产数据 (Observer 抢在用户前几小时发现) → 离线标 N/A。
  - confidence_calibration: Layer 1 确定性命中 confidence=1.0, 真校准需 Layer 2 → deferred。

CORPUS = bench 种子 (Stage 6 扩展为社区可跑 silent-failure/fail-plausible 检测 bench)。
Category A 条目绑回 docs/llm_observer_ground_truth.yaml 的 golden case (单一真理源; 守卫
测试核对每条 A 的 id 在 ground_truth 是 golden_seed 且确实被抓)。

CLI: python3 llm_observer_selfcheck.py [--json] [--save] [--sabotage]
"""
import os
import sys
import unittest.mock as _mock

import llm_observer as obs

_REPO = os.path.dirname(os.path.abspath(__file__))

# ══════════════════════════════════════════════════════════════════════════════
# CORPUS — bench 种子 (Category A 绑 ground_truth golden / clean FP 控制 / B 探索持留)
# ══════════════════════════════════════════════════════════════════════════════
# 每条: id / category (A|clean|B) / source / text / expect_flag /
#       (A: gt_id 绑回 ground_truth; only_signal 标该 case 唯一确定性信号供 sabotage)

_CORPUS = [
    # ── Category A: 回归 (Observer 必须经 Layer 1 确定性抓到, defense 目标 100%) ──
    {
        "id": "dream_self_referential", "category": "A", "source": "dream",
        "gt_id": "dream_self_referential", "only_signal": "detect_pollution_signal",
        "expect_flag": True,
        "text": ("信号一：Papers with Code 的'完全沉默'是平台危机前兆\n"
                 "行动一：立即启动对 Hugging Face 平台可用性的 72 小时监控机制\n"
                 "证据引用：当前已观测到平台返回 'Bad JSON' 和 '400 错误'，若持续超过 72 小时..."),
    },
    {
        "id": "kb_review_silent_degradation", "category": "A", "source": "kb_review",
        "gt_id": "kb_review_silent_degradation", "only_signal": "detect_coherence_structural",
        "expect_flag": True,
        "text": ("# 本周知识回顾\n## 今日arXiv精选(2026-04-04)\n## 今日HF精选(2026-04-04)\n"
                 "## 今日DBLP精选(2026-04-04)\n## 今日ACL精选(2026-04-04)\n## 今日HN精选(2026-04-04)"),
    },
    {
        "id": "dream_quota_blast_radius", "category": "A", "source": "hn",
        "gt_id": "dream_quota_blast_radius", "only_signal": "detect_coherence_structural",
        "expect_flag": True,
        "text": ("## HN 头版精选\n"
                 + "\n".join(f"{i}. Some Tech News Title\n   要点：技术内容，详见原文"
                             for i in range(1, 6))),
    },
    {
        "id": "pa_alert_contamination", "category": "A", "source": None,
        "gt_id": "pa_alert_contamination", "only_signal": "detect_pollution_signal",
        "expect_flag": True,
        "text": ("已收到系统告警跟进任务，正在跟进。\n请您完成以下操作后我再运行 cron_doctor.sh：\n"
                 "1. 打开系统偏好设置 → 安全性与隐私 → 隐私\n"
                 "2. 在'完全磁盘访问权限'中添加 /usr/sbin/cron"),
    },
    {
        "id": "pa_echo_chamber", "category": "A", "source": None,
        "gt_id": "pa_echo_chamber", "only_signal": "detect_provenance_gap",
        "expect_flag": True,
        "text": ("您提出的五维模型很有价值，与知识库中的'本体-代理-Token 工业软件新范式'有"
                 "异曲同工之妙，已按要求永久保存至知识库。"),
    },
    {
        "id": "D4_fabricated_release", "category": "A", "source": "dream",
        "gt_id": None, "only_signal": "detect_fabrication_phrase",   # known_gap, 经 S3
        "expect_flag": True,
        "text": "今日项目动态：OpenClaw 社区发布 v26，达成重要项目里程碑。",
    },

    # ── clean: FP 控制 (Observer 不得 flag, fp 目标 0%) ──
    {"id": "clean_arxiv", "category": "clean", "source": "arxiv_monitor", "expect_flag": False,
     "text": "今日 arXiv 精选：Qwen3 团队提出新注意力机制，在长上下文任务上提升 12%，实验覆盖 8 个 benchmark。"},
    {"id": "clean_hn", "category": "clean", "source": "hn", "expect_flag": False,
     "text": "## HN 热帖\n1. Rust 异步运行时对比\n   要点：作者实测 tokio 与 async-std 在高并发下的尾延迟差异，tokio 在 p99 上领先。"},
    {"id": "clean_finance", "category": "clean", "source": "finance_news", "expect_flag": False,
     "text": "财经简报：美联储维持利率不变，市场预期年内降息两次，科技股普涨。"},
    {"id": "clean_s2", "category": "clean", "source": "semantic_scholar", "expect_flag": False,
     "text": "深度分析：本文提出新的检索增强方法，通过分块嵌入和重排序提升问答准确率，论证链完整，实验对比三个基线。"},

    # ── Category B: 探索/持留 (novel fail-plausible, Layer 1 无专设检测 → 度量 FN 盲区) ──
    # 这些是真 fail-plausible 但【没专门为它们设计过 S 规则】, Layer 1 大概率漏 (诚实 held-out recall)。
    # 它们的检出靠 Layer 2 grounding/intent (需 live LLM) 或未来新 S 规则 (盲区反哺)。
    {"id": "B_fabricated_acceptance", "category": "B", "source": "semantic_scholar", "expect_flag": True,
     "text": "本文提出的方法已被 NeurIPS 2026 正式接收，预计引用量破千，是该领域里程碑式工作。",
     "blind_spot": "编造会议接收 + 编造指标, 无系统 artifact/无血案字眼/无牵强成语 → Layer 1 盲, 需 Layer 2 grounding"},
    {"id": "B_unsupported_strong_claim", "category": "B", "source": "arxiv_monitor", "expect_flag": True,
     "text": "深度分析：本研究证实了量子计算将在 2027 年实现大规模商业化，这一结论已成为学术界共识。",
     "blind_spot": "高档来源(arxiv) → S2 不触发; 无成语 → S4 不触发; 强断言无证据 → 需 Layer 2 grounding"},
    {"id": "B_paraphrased_pollution", "category": "B", "source": "dream", "expect_flag": True,
     "text": "信号：检索服务今日多次返回异常状态，疑似底层平台进入不稳定周期，建议持续关注其可用性走向。",
     "blind_spot": "把系统错误【意译】成'平台不稳定'(无 'Bad JSON'/错误码字面量) → S1 字面指纹漏, 需 Layer 2 pollution-evidence 语义判定"},
    {"id": "B_fabricated_author", "category": "B", "source": "dblp", "expect_flag": True,
     "text": "据 Geoffrey Hinton 与 Yann LeCun 2026 年合著论文，符号主义将全面回归主导地位。",
     "blind_spot": "编造合著 + 编造立场, 无任何确定性指纹 → Layer 1 盲, 需 Layer 2 grounding (源材料无此论文)"},
]


def get_corpus():
    """返回 CORPUS 副本 (供 bench/测试, 不可变 contract)。"""
    return [dict(c) for c in _CORPUS]


# ══════════════════════════════════════════════════════════════════════════════
# 评估
# ══════════════════════════════════════════════════════════════════════════════
def evaluate_entry(entry):
    """对单条 corpus 跑 Observer Layer 1 (确定性, 零 LLM)。返回结果 dict。

    Layer 1 only (离线可测真值)。flagged = run_prefilter verdict=='flagged'。
    correct = (flagged == expect_flag)。
    """
    r = obs.run_prefilter(entry["text"], source_id=entry.get("source"))
    flagged = r["verdict"] == "flagged"
    return {
        "id": entry["id"],
        "category": entry["category"],
        "expect_flag": entry["expect_flag"],
        "flagged": flagged,
        "correct": flagged == entry["expect_flag"],
        "fired": r["fired"],
    }


def evaluate_corpus(corpus=None):
    """跑全 corpus, 返回 per-entry 结果列表。"""
    corpus = corpus if corpus is not None else _CORPUS
    return [evaluate_entry(e) for e in corpus]


def compute_scorecard(results):
    """从评估结果算 scorecard 指标。

    defense_rate = A 中被正确 flag 的比例; fp_rate = clean 中误 flag 的比例;
    fn_rate_B = B 中漏检 (该 flag 但没 flag) 的比例。比率为该类样本数 0 时记 None。
    """
    def _rate(items, pred):
        items = list(items)
        if not items:
            return None, 0, 0
        hits = sum(1 for x in items if pred(x))
        return hits / len(items), hits, len(items)

    a = [r for r in results if r["category"] == "A"]
    clean = [r for r in results if r["category"] == "clean"]
    b = [r for r in results if r["category"] == "B"]

    defense_rate, a_caught, a_n = _rate(a, lambda r: r["flagged"])
    fp_rate, clean_fp, clean_n = _rate(clean, lambda r: r["flagged"])
    fn_rate_B, b_missed, b_n = _rate(b, lambda r: not r["flagged"])

    return {
        "defense_rate": defense_rate, "a_caught": a_caught, "a_total": a_n,
        "fp_rate": fp_rate, "clean_fp": clean_fp, "clean_total": clean_n,
        "fn_rate_B": fn_rate_B, "b_missed": b_missed, "b_total": b_n,
        # 离线不可测 (诚实标注, design §5.4)
        "detection_latency": None,            # 需生产数据
        "confidence_calibration": None,       # 需 Layer 2 live LLM
    }


# ══════════════════════════════════════════════════════════════════════════════
# sabotage-validate (证每个 detector load-bearing, §3.5/§5.2)
# ══════════════════════════════════════════════════════════════════════════════
def run_sabotage_suite(corpus=None):
    """对每个 Category A case, 关掉它 only_signal 标的 detector → 必须从 flagged 漏成 clean。

    证明该 detector 是 load-bearing (非 tautology): 若关掉它 case 仍 flagged, 说明检测来自
    别的信号 (该 case 选错或 detector 冗余); 若 baseline 就没 flag, 说明 detector 根本没在守。
    """
    corpus = corpus if corpus is not None else _CORPUS
    out = []
    for e in corpus:
        if e["category"] != "A":
            continue
        det = e.get("only_signal")
        if not det:
            continue
        base = obs.run_prefilter(e["text"], e.get("source"))["verdict"] == "flagged"
        with _mock.patch.object(obs, det, return_value=[]):
            sab = obs.run_prefilter(e["text"], e.get("source"))["verdict"] == "flagged"
        out.append({
            "case": e["id"], "detector": det,
            "baseline_flagged": base,
            "flagged_when_disabled": sab,
            "load_bearing": base and not sab,
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# scorecard 报告
# ══════════════════════════════════════════════════════════════════════════════
def _pct(x):
    return "N/A" if x is None else f"{x * 100:.0f}%"


def build_scorecard_markdown(scorecard, sabotage, results):
    """生成 scorecard markdown 报告 (docs/llm_observer_scorecard.md)。"""
    sc, lines = scorecard, []
    lines.append("# 🔬 LLM-Observer Scorecard — 自验证回路 (Stage 4)\n")
    lines.append("> 机械化人眼 Observer 的 sabotage-validate scorecard。**全部离线可测** "
                 "(零 LLM/零网络, Layer 1 确定性)。论文 arXiv:2606.14589 §5.4 度量。\n")
    lines.append("> Layer 2 (LLM-judge) real-LLM 准确性/calibration 需 live LLM → Mac Mini "
                 "E2E (Stage 5/6) 度量, 本表标 N/A。\n")

    lines.append("\n## 核心指标\n")
    lines.append("| 指标 | 值 | 样本 | 目标 | 含义 |")
    lines.append("|------|-----|------|------|------|")
    lines.append(f"| **defense rate** | {_pct(sc['defense_rate'])} | {sc['a_caught']}/{sc['a_total']} | →100% | "
                 "Category A 回归 case 被 Layer 1 确定性抓到 (sabotage 守护) |")
    lines.append(f"| **false-positive rate** | {_pct(sc['fp_rate'])} | {sc['clean_fp']}/{sc['clean_total']} | →0% | "
                 "干净输出被误 flag (噪声本身是问题, 原则 #32) |")
    lines.append(f"| **false-negative rate (Category B)** | {_pct(sc['fn_rate_B'])} | {sc['b_missed']}/{sc['b_total']} | 诚实报告 | "
                 "持留/探索集盲区 (held-out recall, design §11 核心开放问题) |")
    lines.append(f"| detection latency | {_pct(sc['detection_latency'])} | — | 论文 #2 | 需生产数据 (抢用户前几小时), 离线 N/A |")
    lines.append(f"| confidence calibration | {_pct(sc['confidence_calibration'])} | — | Stage 5/6 | 需 Layer 2 live LLM, 离线 N/A |")

    lines.append("\n## 解读 (audit-as-regression 的自我应用, design §7.2)\n")
    lines.append("- **defense rate →100% + FP →0%**: Layer 1 对【已知】fail-plausible 模式是"
                 "可靠的回归探测器, 对干净输出零噪声。")
    lines.append(f"- **Category B FN rate {_pct(sc['fn_rate_B'])} (高是预期)**: Layer 1 确定性规则是"
                 "【回归引擎】非【预测引擎】—— 对没专门为它设计过的 novel fail-plausible 模式系统性漏检。"
                 "这正是论文 §5.6 audit-as-regression 对 Observer 自身的应用: 新型模式的检出必须来自"
                 "【别处】(Layer 2 语义 grounding / 人眼 / 新 S 规则反哺), 不来自任何回归引擎。")
    lines.append("- **诚实定位**: Observer 退役'人工逐条扫【已知】fail-plausible 模式', 不退役"
                 "'人发现【新型】模式' (design §7.4)。")

    lines.append("\n## Category B 盲区登记 (反哺新 S 规则 / Layer 2)\n")
    b_results = {r["id"]: r for r in results if r["category"] == "B"}
    corpus_by_id = {c["id"]: c for c in _CORPUS}
    for cid, r in b_results.items():
        status = "✅ 抓到 (意外)" if r["flagged"] else "❌ 漏检 (预期盲区)"
        blind = corpus_by_id.get(cid, {}).get("blind_spot", "")
        lines.append(f"- `{cid}` — {status}: {blind}")

    lines.append("\n## sabotage 验证 (证每个 detector load-bearing)\n")
    lines.append("> 关掉某 detector → 它守护的 golden case 必须从 flagged 漏成 clean。"
                 "全 ✅ = 没有冗余/空检测器 (论文 §6 pillar 2: 未验证的探测器和空检测器无法区分)。\n")
    lines.append("| golden case | detector | baseline | 关掉后 | load-bearing |")
    lines.append("|-------------|----------|----------|--------|--------------|")
    for s in sabotage:
        lb = "✅" if s["load_bearing"] else "❌"
        lines.append(f"| `{s['case']}` | `{s['detector']}` | "
                     f"{'flagged' if s['baseline_flagged'] else 'clean'} | "
                     f"{'flagged' if s['flagged_when_disabled'] else 'clean'} | {lb} |")

    all_lb = all(s["load_bearing"] for s in sabotage) if sabotage else False
    lines.append(f"\n**sabotage 结论**: {'✅ 全部 detector load-bearing' if all_lb else '❌ 存在非 load-bearing detector'}\n")

    lines.append("\n---\n*Generated by llm_observer_selfcheck.py (Stage 4, design §5.4). "
                 "CORPUS 绑回 docs/llm_observer_ground_truth.yaml (单一真理源)。*\n")
    return "\n".join(lines)


def build_scorecard(corpus=None):
    """一站式: 评估 + scorecard + sabotage。返回 dict (供 --json / 测试)。"""
    results = evaluate_corpus(corpus)
    scorecard = compute_scorecard(results)
    sabotage = run_sabotage_suite(corpus)
    return {"scorecard": scorecard, "sabotage": sabotage, "results": results,
            "all_load_bearing": all(s["load_bearing"] for s in sabotage) if sabotage else False}


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description="LLM-Observer 自验证 harness + scorecard (Stage 4)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--save", action="store_true",
                    help="写 docs/llm_observer_scorecard.md")
    ap.add_argument("--sabotage", action="store_true", help="仅跑 sabotage suite")
    args = ap.parse_args()

    full = build_scorecard()
    if args.sabotage:
        for s in full["sabotage"]:
            mark = "✅" if s["load_bearing"] else "❌"
            print(f"{mark} {s['case']} / {s['detector']}: "
                  f"baseline={s['baseline_flagged']} disabled={s['flagged_when_disabled']}")
        return 0 if full["all_load_bearing"] else 1

    if args.json:
        print(json.dumps(full, ensure_ascii=False, indent=2))
    else:
        md = build_scorecard_markdown(full["scorecard"], full["sabotage"], full["results"])
        print(md)

    if args.save:
        out = os.path.join(_REPO, "docs", "llm_observer_scorecard.md")
        md = build_scorecard_markdown(full["scorecard"], full["sabotage"], full["results"])
        with open(out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n[saved] {out}", file=sys.stderr)

    sc = full["scorecard"]
    # exit 1 if defense rate < 100% or FP > 0 (回归门禁); Category B FN 不算失败 (诚实盲区)
    ok = (sc["defense_rate"] == 1.0 and (sc["fp_rate"] in (0.0, None))
          and full["all_load_bearing"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

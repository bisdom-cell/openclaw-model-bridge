#!/usr/bin/env python3
"""test_llm_observer.py — LLM-Observer Layer 1 pre-filter 守卫 (研究攻关 #1 Stage 2).

验证 llm_observer.py 的确定性 S1-S5: 对 5 黄金 ground-truth case 的指纹命中、对干净
synthesis 输出零误报 (FP 是一等度量, 论文 §5.4)、FAIL-OPEN、sabotage 反向验证。

关键守卫 TestGoldenCasesAgainstGroundTruth: 把 Layer 1 绑回 Stage 1 的
docs/llm_observer_ground_truth.yaml —— 每个 golden case 必须被 run_prefilter 经它
expected_signal 的【确定性子集】(S1-S5, 排除 layer2_*) 至少一个抓到。这把"Layer 1 真
抓得到历史 fail-plausible"从声明升级为机器可证。

反向验证 (手动 sabotage 已确认真有效, 见 changelog): 清空 _S1_ERROR_PATTERNS → D1
golden 测试 + 常量守卫双 FAIL; 改 _S5_BOILERPLATE_MIN_REPEAT=99 → dream_quota 漏检。
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

import llm_observer as obs

_REPO = os.path.dirname(os.path.abspath(__file__))
_GT_PATH = os.path.join(_REPO, "docs", "llm_observer_ground_truth.yaml")

try:
    import yaml  # noqa: F401
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

# ── 黄金 case 的代表性 fixture (从 case docs 重建的 fail-plausible 输出片段) ─────
# 注: D1 的 expected_signal 含 S5, 但那是【语义】主题断裂 (signal vs action) = Layer 2;
# Layer 1 经 S1 (pollution) 抓到 D1。每个 case 只需被【至少一个确定性 expected signal】抓到。
_FIXTURES = {
    "dream_self_referential": (
        "信号一：Papers with Code 的'完全沉默'是平台危机前兆\n"
        "行动一：立即启动对 Hugging Face 平台可用性的 72 小时监控机制\n"
        "证据引用：当前已观测到平台返回 'Bad JSON' 和 '400 错误'，若持续超过 72 小时...",
        "dream"),
    "kb_review_silent_degradation": (
        "# 本周知识回顾\n## 今日arXiv精选(2026-04-04)\n## 今日HF精选(2026-04-04)\n"
        "## 今日DBLP精选(2026-04-04)\n## 今日ACL精选(2026-04-04)\n## 今日HN精选(2026-04-04)",
        "kb_review"),
    "dream_quota_blast_radius": (
        "## HN 头版精选\n"
        + "\n".join(f"{i}. Some Tech News Title\n   要点：技术内容，详见原文" for i in range(1, 6)),
        "hn"),
    "pa_alert_contamination": (
        "已收到系统告警跟进任务，正在跟进。\n请您完成以下操作后我再运行 cron_doctor.sh：\n"
        "1. 打开系统偏好设置 → 安全性与隐私 → 隐私\n"
        "2. 在'完全磁盘访问权限'中添加 /usr/sbin/cron",
        None),
    "pa_echo_chamber": (
        "您提出的五维模型很有价值，与知识库中的'本体-代理-Token 工业软件新范式'有异曲同工之妙，"
        "已按要求永久保存至知识库。",
        None),
}
# D4 (known_gap, 无独立 case 文件) fixture
_D4_FIXTURE = "今日项目动态：OpenClaw 社区发布 v26，达成重要项目里程碑。"

# 干净 synthesis 输出 (FP 控制, 必须 verdict=clean)
_CLEAN_FIXTURES = [
    ("今日 arXiv 精选：Qwen3 团队提出新注意力机制，在长上下文任务上提升 12%，"
     "实验覆盖 8 个 benchmark。", "arxiv_monitor"),
    ("## HN 热帖\n1. Rust 异步运行时对比\n   要点：作者实测 tokio 与 async-std 在高并发下"
     "的尾延迟差异，tokio 在 p99 上领先。", "hn"),
    ("财经简报：美联储维持利率不变，市场预期年内降息两次，科技股普涨。", "finance_news"),
    ("深度分析：本文提出新的检索增强方法，通过分块嵌入和重排序提升问答准确率，"
     "论证链完整，实验对比三个基线。", "semantic_scholar"),
]


class TestPollutionSignalS1(unittest.TestCase):
    def test_bad_json_error_page_fires(self):
        sigs = obs.detect_pollution_signal("平台返回 'Bad JSON' 和 '400 错误'")
        self.assertTrue(any(s["signal"] == "S1_pollution_signal" for s in sigs))

    def test_error_code_and_retry_log_fire(self):
        self.assertTrue(obs.detect_pollution_signal("Error code: 400"))
        self.assertTrue(obs.detect_pollution_signal("Waiting 3s before retry..."))

    def test_html_error_page_and_traceback_fire(self):
        self.assertTrue(obs.detect_pollution_signal("<!DOCTYPE HTML PUBLIC ..."))
        self.assertTrue(obs.detect_pollution_signal("Traceback (most recent call last):"))

    def test_internal_alert_artifact_fires(self):
        self.assertTrue(obs.detect_pollution_signal("已收到系统告警跟进任务"))
        self.assertTrue(obs.detect_pollution_signal("运行 cron_doctor.sh 排查"))

    def test_clean_text_no_fire(self):
        self.assertEqual(obs.detect_pollution_signal("API 返回 200, 一切正常运行"), [])
        self.assertEqual(obs.detect_pollution_signal("讨论分布式系统的错误处理设计"), [])

    def test_locus_and_snippet_present(self):
        sigs = obs.detect_pollution_signal("line one\nBad JSON here")
        self.assertEqual(sigs[0]["locus"], 2)
        self.assertIn("Bad JSON", sigs[0]["snippet"])

    def test_bad_input_safe(self):
        for bad in (None, "", 123, []):
            self.assertEqual(obs.detect_pollution_signal(bad), [])


class TestCredibilityMismatchS2(unittest.TestCase):
    def test_low_tier_high_claim_fires(self):
        sigs = obs.detect_credibility_mismatch("研究表明该方法最优", source_id="rss_blogs")
        self.assertTrue(any(s["signal"] == "S2_credibility_mismatch" for s in sigs))

    def test_high_tier_source_no_fire(self):
        # arxiv (rank 2) 用高档措辞合法
        self.assertEqual(obs.detect_credibility_mismatch("研究表明X", source_id="arxiv_monitor"), [])

    def test_no_source_fail_open(self):
        self.assertEqual(obs.detect_credibility_mismatch("研究表明X", source_id=None), [])

    def test_low_tier_no_high_claim_no_fire(self):
        self.assertEqual(obs.detect_credibility_mismatch("作者分享了一些观察", source_id="rss_blogs"), [])

    def test_source_credibility_unavailable_fail_open(self):
        with mock.patch.object(obs, "detect_credibility_mismatch", wraps=obs.detect_credibility_mismatch):
            with mock.patch("source_credibility.get_credibility", side_effect=RuntimeError("boom")):
                self.assertEqual(obs.detect_credibility_mismatch("研究表明X", source_id="hn"), [])


class TestFabricationPhraseS3(unittest.TestCase):
    def test_blocked_phrase_fires(self):
        import hallucination_guards
        phrase = hallucination_guards.get_blocked_phrases()[0]
        sigs = obs.detect_fabrication_phrase(f"今日动态：{phrase} 已上线")
        self.assertTrue(any(s["signal"] == "S3_fabrication_phrase" for s in sigs))

    def test_d4_fabricated_release_fires(self):
        self.assertTrue(obs.detect_fabrication_phrase(_D4_FIXTURE))

    def test_clean_no_fire(self):
        self.assertEqual(obs.detect_fabrication_phrase("Qwen3 团队发布技术报告"), [])

    def test_reuses_seed_not_hardcoded(self):
        # S3 必须复用 hallucination_guards.get_blocked_phrases() (MR-8 单一真理源)
        import hallucination_guards
        for phrase in hallucination_guards.get_blocked_phrases():
            self.assertTrue(obs.detect_fabrication_phrase(f"x {phrase} y"),
                            f"blocked phrase '{phrase}' should fire S3")

    def test_unavailable_fail_open(self):
        with mock.patch("hallucination_guards.get_blocked_phrases", side_effect=RuntimeError):
            self.assertEqual(obs.detect_fabrication_phrase("OpenClaw v26"), [])


class TestProvenanceGapS4(unittest.TestCase):
    def test_over_association_idiom_fires(self):
        self.assertTrue(obs.detect_provenance_gap("两者有异曲同工之妙"))

    def test_evidence_tag_cancels(self):
        # 带 [强证据]/[弱关联] 标注 = LEVEL_6 契约满足, 不报
        self.assertEqual(obs.detect_provenance_gap("两者异曲同工 [弱关联]"), [])

    def test_clean_no_fire(self):
        self.assertEqual(obs.detect_provenance_gap("作者对比了两种方法的差异"), [])


class TestCoherenceStructuralS5(unittest.TestCase):
    def test_boilerplate_repetition_fires(self):
        txt = "\n".join(["要点：技术内容，详见原文"] * 5)
        sigs = obs.detect_coherence_structural(txt)
        self.assertTrue(any("boilerplate" in s["snippet"] for s in sigs))

    def test_all_headings_no_body_fires(self):
        txt = "# T\n## 今日arXiv精选\n## 今日HF精选\n## 今日DBLP精选\n## 今日ACL精选"
        self.assertTrue(obs.detect_coherence_structural(txt))

    def test_field_value_is_separator_fires(self):
        self.assertTrue(obs.detect_coherence_structural("标题: ---"))

    def test_normal_content_no_fire(self):
        txt = ("# 深度分析\n本文提出新方法。第一段论证检索增强的动机，第二段给出"
               "具体算法，第三段实验对比三个基线，结果显示提升明显。")
        self.assertEqual(obs.detect_coherence_structural(txt), [])

    def test_two_repeats_below_threshold_no_fire(self):
        txt = "\n".join(["要点：技术内容，详见原文"] * 2)
        self.assertEqual([s for s in obs.detect_coherence_structural(txt)
                          if "boilerplate" in s["snippet"]], [])


class TestRunPrefilter(unittest.TestCase):
    def test_clean_fixtures_no_false_positive(self):
        for txt, src in _CLEAN_FIXTURES:
            r = obs.run_prefilter(txt, source_id=src)
            self.assertEqual(r["verdict"], "clean",
                             f"FALSE POSITIVE on clean output: fired={r['fired']}\n{txt[:60]}")

    def test_flagged_verdict_and_fired_dedup_sorted(self):
        r = obs.run_prefilter("Bad JSON\nBad JSON", source_id=None)
        self.assertEqual(r["verdict"], "flagged")
        self.assertEqual(r["fired"], sorted(set(r["fired"])))

    def test_empty_input_clean(self):
        self.assertEqual(obs.run_prefilter("", source_id=None)["verdict"], "clean")

    def test_signals_mergeable_into_anomalies(self):
        # Stage 5 把 signals 映射进 daily_observer anomalies[]: 每条须有 severity
        r = obs.run_prefilter(_D4_FIXTURE, source_id="dream")
        for s in r["signals"]:
            self.assertIn(s["severity"], ("HIGH", "MED", "LOW"))
            self.assertIn("signal", s)


@unittest.skipUnless(_HAS_YAML, "PyYAML required for ground-truth tie")
class TestGoldenCasesAgainstGroundTruth(unittest.TestCase):
    """把 Layer 1 绑回 Stage 1 ground-truth: 每 golden case 必须被它 expected_signal
    的确定性子集 (S1-S5) 至少一个抓到 (layer2_* 是 Stage 3 的活, 不要求 Layer 1 产)。"""

    @classmethod
    def setUpClass(cls):
        with open(_GT_PATH) as f:
            cls.gt = yaml.safe_load(f)
        cls.by_id = {c["id"]: c for c in cls.gt["cases"]}

    def _deterministic(self, expected_signal):
        return {s for s in expected_signal if s.startswith("S")}

    def test_every_golden_seed_has_a_fixture(self):
        golden = [c["id"] for c in self.gt["cases"] if c["golden_seed"]]
        for cid in golden:
            self.assertIn(cid, _FIXTURES, f"golden seed {cid} needs a Stage 2 fixture")

    def test_golden_cases_caught_by_expected_deterministic_signal(self):
        for cid, (text, src) in _FIXTURES.items():
            case = self.by_id[cid]
            det = self._deterministic(case["expected_signal"])
            self.assertTrue(det, f"{cid}: no deterministic expected_signal to test")
            r = obs.run_prefilter(text, source_id=src)
            self.assertEqual(r["verdict"], "flagged", f"{cid}: Layer 1 missed it entirely")
            caught = set(r["fired"]) & det
            self.assertTrue(caught,
                            f"{cid}: Layer 1 fired {r['fired']} but none in expected "
                            f"deterministic {det}")

    def test_d4_known_gap_caught_by_s3(self):
        gap = next(g for g in self.gt["known_gaps"] if g["id"] == "D4_fabricated_release")
        self.assertIn("S3_fabrication_phrase", gap["expected_signal"])
        r = obs.run_prefilter(_D4_FIXTURE, source_id="dream")
        self.assertIn("S3_fabrication_phrase", r["fired"])

    def test_fixtures_only_for_in_scope_golden(self):
        # 不给 out-of-scope case 造 fixture (诚实边界: Layer 1 不假装抓结构事故)
        for cid in _FIXTURES:
            self.assertNotEqual(self.by_id[cid]["observer_in_scope"], "no",
                                f"{cid} is out-of-scope, should not have a Layer 1 fixture")


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3: Layer 2 LLM-judge 守卫
# ══════════════════════════════════════════════════════════════════════════════

def _fake_caller_json(verdict="fail_plausible", confidence=80, findings=None):
    """构造 fake llm_caller (零网络): 返回 (ok, content, reason) 三元组。"""
    import json
    body = json.dumps({"verdict": verdict, "confidence": confidence,
                       "findings": findings or []}, ensure_ascii=False)

    def caller(system, user):
        return True, body, ""
    return caller


class TestFailPlausibleSystemPrompt(unittest.TestCase):
    """build_fail_plausible_system: 注入 seeds (复用非硬编码) + 4 judge 维度 + 采样规则。"""

    def setUp(self):
        self.sysp = obs.build_fail_plausible_system("dream")

    def test_injects_level6_guard_not_hardcoded(self):
        import hallucination_guards
        lvl6 = hallucination_guards.get_guard(obs._HALLUCINATION_GUARD_LEVEL)
        self.assertIn(lvl6.strip()[:30], self.sysp)
        self.assertIn("强证据", self.sysp)
        self.assertIn("弱关联", self.sysp)

    def test_injects_credibility_block(self):
        import source_credibility
        cred = source_credibility.format_credibility_block()
        self.assertIn(cred.strip()[:30], self.sysp)

    def test_has_four_judge_dimensions(self):
        for dim in ("grounding", "intent_alignment", "pollution_evidence",
                    "fabricated_success"):
            self.assertIn(dim, self.sysp)

    def test_has_sampling_rule_and_json_format(self):
        self.assertIn("V37.9.93", self.sysp)   # 采样规则 (防 truncation 误判)
        self.assertIn("json", self.sysp.lower())
        self.assertIn("逐字", self.sysp)        # 证据逐字摘录铁律

    def test_fail_open_when_seeds_unavailable(self):
        # seed import 失败 → core 仍返回, 不抛异
        with mock.patch.dict("sys.modules",
                             {"hallucination_guards": None, "source_credibility": None}):
            p = obs.build_fail_plausible_system("dream")
        self.assertIn("fail-plausible", p)


class TestFailPlausibleUserPrompt(unittest.TestCase):
    def test_sampling_flag_adds_warning(self):
        u = obs.build_fail_plausible_user("内容", sampled=True)
        self.assertIn("采样", u)
        self.assertIn("V37.9.93", u)

    def test_normal_no_sampling_warning(self):
        u = obs.build_fail_plausible_user("内容", sampled=False)
        self.assertNotIn("采样提示", u)

    def test_layer1_signals_included(self):
        sigs = [{"signal": "S1_pollution_signal", "locus": 3, "snippet": "Bad JSON"}]
        u = obs.build_fail_plausible_user("内容", layer1_signals=sigs)
        self.assertIn("S1_pollution_signal", u)
        self.assertIn("Bad JSON", u)

    def test_content_present(self):
        u = obs.build_fail_plausible_user("待评估正文ABC")
        self.assertIn("待评估正文ABC", u)


class TestParseFpVerdict(unittest.TestCase):
    def test_fenced_json(self):
        c = ('一些前言\n```json\n{"verdict":"fail_plausible","confidence":75,'
             '"findings":[{"judge":"grounding","evidence":"x","rationale":"y"}]}\n```')
        v, conf, f = obs.parse_fp_verdict(c)
        self.assertEqual(v, "fail_plausible")
        self.assertEqual(conf, 0.75)
        self.assertEqual(len(f), 1)

    def test_bare_json(self):
        v, conf, f = obs.parse_fp_verdict('{"verdict":"clean","confidence":10,"findings":[]}')
        self.assertEqual(v, "clean")
        self.assertEqual(conf, 0.1)

    def test_unparseable_defaults_clean(self):
        # 保守: 无法解析 → clean, 不编造 flag
        self.assertEqual(obs.parse_fp_verdict("完全不是 JSON 的散文"), ("clean", None, []))
        self.assertEqual(obs.parse_fp_verdict(""), ("clean", None, []))

    def test_verdict_normalization(self):
        self.assertEqual(obs.parse_fp_verdict('{"verdict":"FAIL_PLAUSIBLE"}')[0], "fail_plausible")
        self.assertEqual(obs.parse_fp_verdict('{"verdict":"flagged"}')[0], "fail_plausible")
        self.assertEqual(obs.parse_fp_verdict('{"verdict":"ok"}')[0], "clean")

    def test_findings_non_dict_filtered(self):
        v, conf, f = obs.parse_fp_verdict('{"verdict":"fail_plausible","findings":["bad",{"judge":"x"}]}')
        self.assertEqual(len(f), 1)


class TestEvidenceGrounding(unittest.TestCase):
    """🔴 反幻觉核心: 证据必须能在原文逐字 ground。"""

    def test_grounded_substring_true(self):
        self.assertTrue(obs._evidence_grounded("Bad JSON 错误", "平台返回 Bad JSON 错误码"))

    def test_ungrounded_false(self):
        self.assertFalse(obs._evidence_grounded("根本不存在的编造片段", "正常内容"))

    def test_too_short_false(self):
        # < _MIN_EVIDENCE_LEN: 太模糊无法 ground
        self.assertFalse(obs._evidence_grounded("xy", "xy 出现在这里"))

    def test_whitespace_tolerant(self):
        self.assertTrue(obs._evidence_grounded("Bad   JSON", "...Bad JSON..."))

    def test_non_string_safe(self):
        self.assertFalse(obs._evidence_grounded(None, "text"))
        self.assertFalse(obs._evidence_grounded("evidence", None))


class TestRunLlmJudge(unittest.TestCase):
    def test_grounded_finding_kept(self):
        text = "平台返回 Bad JSON 和 400 错误，被当成危机信号"
        caller = _fake_caller_json(findings=[
            {"judge": "pollution_evidence", "evidence": "Bad JSON 和 400 错误", "rationale": "r"}])
        findings, conf = obs.run_llm_judge(text, "dream", [], caller)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["_grounded"])
        self.assertEqual(conf, 0.8)

    def test_ungrounded_finding_dropped(self):
        # Observer 幻觉一个不在原文的证据 → drop → 空 (反幻觉铁律)
        caller = _fake_caller_json(findings=[
            {"judge": "grounding", "evidence": "原文里根本没有的编造证据", "rationale": "r"}])
        findings, conf = obs.run_llm_judge("正常干净内容", "hn", [], caller)
        self.assertEqual(findings, [])

    def test_verdict_clean_no_findings(self):
        caller = _fake_caller_json(verdict="clean", findings=[])
        findings, conf = obs.run_llm_judge("text", "hn", [], caller)
        self.assertEqual(findings, [])

    def test_caller_not_ok_fail_open(self):
        def caller(s, u):
            return False, "", "HTTP 500"
        self.assertEqual(obs.run_llm_judge("text", "hn", [], caller), ([], None))

    def test_caller_raises_fail_open(self):
        def caller(s, u):
            raise RuntimeError("proxy down")
        self.assertEqual(obs.run_llm_judge("text", "hn", [], caller), ([], None))


class TestDetectFailPlausible(unittest.TestCase):
    """两层管道 orchestrator。"""

    def test_cheap_path_clean_no_llm_call(self):
        # Layer 1 clean + not force → 不调 LLM (caller 绝不被触发)
        called = {"n": 0}

        def caller(s, u):
            called["n"] += 1
            return True, "{}", ""
        clean = "今日 arXiv 精选：Qwen3 提出新注意力机制，实验覆盖 8 个 benchmark。"
        r = obs.detect_fail_plausible(clean, source_id="arxiv_monitor", llm_caller=caller)
        self.assertEqual(r, [])
        self.assertEqual(called["n"], 0)

    def test_layer1_fired_triggers_layer2_consolidated(self):
        d1 = ("信号一：平台返回 'Bad JSON' 和 '400 错误'\n"
              "行动一：启动 72 小时监控")
        caller = _fake_caller_json(confidence=85, findings=[
            {"judge": "pollution_evidence", "evidence": "'Bad JSON' 和 '400 错误'",
             "rationale": "把系统错误码当平台危机信号"}])
        r = obs.detect_fail_plausible(d1, source_id="dream", artifact="dream/x.md",
                                      llm_caller=caller)
        self.assertEqual(len(r), 1)
        v = r[0]
        self.assertEqual(v["severity"], "HIGH")
        self.assertEqual(v["category"], "pollution_signal")
        self.assertTrue(any(e["layer"] == 1 for e in v["evidence"]))
        self.assertTrue(any(e["layer"] == 2 for e in v["evidence"]))
        self.assertEqual(v["confidence"], 0.85)
        self.assertEqual(v["artifact"], "dream/x.md")

    def test_force_judge_on_clean_text_runs_layer2(self):
        # Layer 1 clean 但 force_judge → Layer 2 抓到只有它能抓的 (D2 intent)
        text = "关于三平面架构的回答里，模型声称要执行用户没要求的后续任务"
        caller = _fake_caller_json(confidence=70, findings=[
            {"judge": "intent_alignment", "evidence": "执行用户没要求的后续任务",
             "rationale": "用户问架构，模型却做未被要求的事"}])
        r = obs.detect_fail_plausible(text, source_id="hn", force_judge=True, llm_caller=caller)
        self.assertEqual(len(r), 1)
        self.assertIn("intent_alignment", r[0]["fired"])
        self.assertTrue(all(e["layer"] == 2 for e in r[0]["evidence"]))

    def test_caller_raises_layer1_verdict_survives(self):
        # Layer 2 FAIL-OPEN, 但 Layer 1 命中 → 仍产 Layer1-only verdict
        d1 = "平台返回 Bad JSON 错误"

        def caller(s, u):
            raise RuntimeError("down")
        r = obs.detect_fail_plausible(d1, source_id="dream", llm_caller=caller)
        self.assertEqual(len(r), 1)
        self.assertTrue(all(e["layer"] == 1 for e in r[0]["evidence"]))
        self.assertEqual(r[0]["confidence"], 1.0)  # 确定性 Layer 1

    def test_clean_and_layer2_rejects_returns_empty(self):
        # Layer 1 clean + force, Layer 2 verdict clean → []
        caller = _fake_caller_json(verdict="clean", findings=[])
        r = obs.detect_fail_plausible("干净内容", source_id="hn", force_judge=True,
                                      llm_caller=caller)
        self.assertEqual(r, [])

    def test_enable_layer2_false_layer1_only(self):
        # Stage 5 dry_run: enable_layer2=False → Layer 1 命中也不调 LLM
        d1 = "平台返回 Bad JSON 错误"
        called = {"n": 0}

        def caller(s, u):
            called["n"] += 1
            return True, "{}", ""
        r = obs.detect_fail_plausible(d1, source_id="dream", llm_caller=caller,
                                      enable_layer2=False)
        self.assertEqual(called["n"], 0)
        self.assertEqual(len(r), 1)
        self.assertTrue(all(e["layer"] == 1 for e in r[0]["evidence"]))


class TestScanFailPlausible(unittest.TestCase):
    """Stage 5 collection-level wrapper (daily_observer.run() 接口)。"""

    def test_iterates_push_and_sources(self):
        push = {
            "dream": {"found": True, "length": 60,
                      "content": "信号：平台返回 'Bad JSON' 和 '400 错误'，疑似危机。"},
            "evening": {"found": True, "length": 30, "content": "今日分析覆盖多个领域，论证完整。"},
            "deep_dive": {"found": False, "content": "", "length": 0},
        }
        src = [{"source": "arxiv_monitor", "section_text": "Qwen3 新机制，实验充分。",
                "char_count": 20}]
        caller = _fake_caller_json(findings=[
            {"judge": "pollution_evidence", "evidence": "'Bad JSON' 和 '400 错误'",
             "rationale": "r"}])
        v = obs.scan_fail_plausible(push, src, llm_caller=caller)
        self.assertEqual(len(v), 1)   # 仅 dream 命中
        self.assertEqual(v[0]["artifact"], "dream")

    def test_missing_or_empty_skipped(self):
        push = {"dream": {"found": False, "content": "", "length": 0}}
        self.assertEqual(obs.scan_fail_plausible(push, []), [])
        self.assertEqual(obs.scan_fail_plausible(None, None), [])

    def test_sampled_flag_from_length(self):
        # length > len(content) → 采样 → 传 sampled=True 给 Layer 2 (此处验证不崩 + 命中)
        push = {"dream": {"found": True, "length": 99999,
                          "content": "平台返回 Bad JSON 错误"}}
        captured = {}

        def caller(s, u):
            captured["sampled_in_prompt"] = "采样" in u
            return True, '{"verdict":"clean","findings":[]}', ""
        obs.scan_fail_plausible(push, [], llm_caller=caller)
        self.assertTrue(captured.get("sampled_in_prompt"))

    def test_enable_layer2_false_no_llm(self):
        push = {"dream": {"found": True, "length": 30, "content": "平台返回 Bad JSON 错误"}}
        called = {"n": 0}

        def caller(s, u):
            called["n"] += 1
            return True, "{}", ""
        obs.scan_fail_plausible(push, [], llm_caller=caller, enable_layer2=False)
        self.assertEqual(called["n"], 0)

    def test_fail_open_artifact_exception(self):
        # 单 artifact 异常 → skip 不阻塞 (这里用畸形 push entry)
        push = {"dream": {"found": True, "length": 10, "content": 12345}}  # content 非 str
        # detect_fail_plausible 对非 str text 安全 (run_prefilter 返回 clean) → 不崩
        self.assertEqual(obs.scan_fail_plausible(push, []), [])


@unittest.skipUnless(_HAS_YAML, "PyYAML required for golden Layer 2 tie")
class TestGoldenCasesLayer2(unittest.TestCase):
    """把 Layer 2 绑回 Stage 1 ground-truth: 每 golden case 的 layer2_* 期望 judge,
    当 fake judge 用 grounded 证据报出时, orchestrator 必须 surface 它 (验证 wiring,
    非验证 LLM 准确性——那是 Mac Mini E2E / Stage 5)。"""

    @classmethod
    def setUpClass(cls):
        with open(_GT_PATH) as f:
            cls.gt = yaml.safe_load(f)
        cls.by_id = {c["id"]: c for c in cls.gt["cases"]}

    def test_golden_layer2_judges_surface(self):
        tested = 0
        for cid, (text, src) in _FIXTURES.items():
            case = self.by_id[cid]
            l2_judges = [s[len("layer2_"):] for s in case["expected_signal"]
                         if s.startswith("layer2_")]
            if not l2_judges:
                continue
            tested += 1
            grounded = obs._norm_ws(text)[:20]   # 保证在原文 (反幻觉 ground 通过)
            findings = [{"judge": j, "evidence": grounded, "rationale": "r"}
                        for j in l2_judges]
            caller = _fake_caller_json(findings=findings)
            r = obs.detect_fail_plausible(text, source_id=src, llm_caller=caller,
                                          artifact=cid)
            self.assertEqual(len(r), 1, f"{cid}: expected a verdict")
            for j in l2_judges:
                self.assertIn(j, r[0]["fired"],
                              f"{cid}: layer2 judge {j} must surface in fired")
        self.assertGreaterEqual(tested, 4, "should test ≥4 golden cases with layer2 expectations")

    def test_judge_to_category_covers_all_layer2_signals(self):
        # ground-truth signals 字典里每个 layer2_X 都有 _JUDGE_TO_CATEGORY 映射 (MR-8 drift guard)
        l2_sigs = [k for k in self.gt["signals"] if k.startswith("layer2_")]
        for sig in l2_sigs:
            judge = sig[len("layer2_"):]
            self.assertIn(judge, obs._JUDGE_TO_CATEGORY,
                          f"{sig}: judge {judge} missing from _JUDGE_TO_CATEGORY")


class TestStage3SabotageReverseValidation(unittest.TestCase):
    """证明 grounding 守卫真有效 (非 tautology): 关掉 grounding → 幻觉证据泄漏。"""

    def test_grounding_guard_is_load_bearing(self):
        # ungrounded 证据正常应被 drop
        caller = _fake_caller_json(findings=[
            {"judge": "grounding", "evidence": "原文绝对没有的编造片段", "rationale": "r"}])
        baseline, _ = obs.run_llm_judge("干净内容", "hn", [], caller)
        self.assertEqual(baseline, [], "grounding 守卫应 drop 幻觉证据")
        # sabotage: 关掉 grounding (恒 True) → 幻觉证据泄漏 (证明守卫 load-bearing)
        with mock.patch.object(obs, "_evidence_grounded", return_value=True):
            leaked, _ = obs.run_llm_judge("干净内容", "hn", [], caller)
        self.assertEqual(len(leaked), 1,
                         "关掉 grounding 后幻觉证据应泄漏 (证明守卫真有效)")


class TestStage3SourceLevelGuards(unittest.TestCase):
    def test_stage3_marker(self):
        with open(os.path.join(_REPO, "llm_observer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Stage 3", src)        # Layer 2 (Stage 3, LLM-judge)
        self.assertIn("反幻觉铁律", src)

    def test_design_locked_layer2_constants(self):
        self.assertEqual(obs._MIN_EVIDENCE_LEN, 4)
        self.assertEqual(obs._HALLUCINATION_GUARD_LEVEL, "LEVEL_6_DREAM_CROSS_DOMAIN_AWARE")
        self.assertEqual(obs._JUDGE_TO_CATEGORY["pollution_evidence"], "pollution_signal")
        self.assertEqual(obs._JUDGE_TO_CATEGORY["fabricated_success"], "fabricated_success")
        self.assertEqual(obs._JUDGE_SEVERITY["pollution_evidence"], "HIGH")
        self.assertEqual(obs._SEV_ORDER["HIGH"], 3)

    def test_seeds_reused_not_hardcoded_in_source(self):
        # LEVEL_6 守卫文本不应被复制进 llm_observer 源码 (复用 hallucination_guards)
        with open(os.path.join(_REPO, "llm_observer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("hallucination_guards.get_guard", src)
        self.assertIn("source_credibility.format_credibility_block", src)
        # 反 copy-paste: LEVEL_6 的长篇守卫正文不应整段出现在本模块
        self.assertNotIn("反幻觉守卫 (V37.9.89 LEVEL_6", src)

    def test_default_caller_lazy_binds_daily_observer(self):
        with open(os.path.join(_REPO, "llm_observer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("import daily_observer", src)
        self.assertIn("call_llm_critique", src)


class TestSourceLevelGuards(unittest.TestCase):
    def test_stage2_marker(self):
        with open(os.path.join(_REPO, "llm_observer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("研究攻关 #1 Stage 2", src)
        self.assertIn("FAIL-OPEN", src)

    def test_design_locked_constants(self):
        self.assertEqual(obs._S5_BOILERPLATE_MIN_REPEAT, 3)
        self.assertEqual(obs._S2_LOW_TIER_RANK_MIN, 4)
        self.assertIn("异曲同工", obs._S4_OVER_ASSOCIATION)
        self.assertTrue(any("Bad JSON" in p for p in obs._S1_ERROR_PATTERNS))
        self.assertTrue(obs._S1_ERROR_PATTERNS, "S1 patterns must be non-empty (sabotage anchor)")

    def test_log_writes_stderr(self):
        # MR-11: 诊断写 stderr 防命令替换污染 (D1 血案根因)
        import io
        from contextlib import redirect_stderr, redirect_stdout
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            obs.log("probe")
        self.assertIn("probe", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_all_five_detectors_callable(self):
        for fn in (obs.detect_pollution_signal, obs.detect_fabrication_phrase,
                   obs.detect_provenance_gap, obs.detect_coherence_structural):
            self.assertEqual(fn(""), [])
        self.assertEqual(obs.detect_credibility_mismatch("", None), [])


class TestCli(unittest.TestCase):
    def test_cli_flagged_json(self):
        r = subprocess.run([sys.executable, os.path.join(_REPO, "llm_observer.py"),
                            "--text", "Bad JSON 400 错误", "--json"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)  # flagged -> exit 1
        self.assertIn("S1_pollution_signal", r.stdout)

    def test_cli_clean(self):
        r = subprocess.run([sys.executable, os.path.join(_REPO, "llm_observer.py"),
                            "--text", "Qwen3 发布新模型，性能提升明显"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)  # clean -> exit 0
        self.assertIn("clean", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

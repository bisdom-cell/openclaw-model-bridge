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

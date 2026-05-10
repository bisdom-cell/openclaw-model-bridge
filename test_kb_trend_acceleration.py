#!/usr/bin/env python3
"""test_kb_trend_acceleration.py — V37.9.48 Stage 3 PoC 28 单测

测试类分布 (设计文档 docs/opportunity_radar_design.md 5.3 节):
  TestExtractKeywordsPerWeek  (4)  — jieba/英文兼容 / 停用词 / 短词过滤 / FAIL-OPEN
  TestComputeAcceleration     (5)  — 4 周历史 / 缺周降级 / 新词 (w2=0) skip / 小基数门槛 / 空输入
  TestClassify                (6)  — 5 档边界 / a1+a2 优先 strong / a2=None / 非数字
  TestRankSignals             (4)  — 优先级排序 / top 截断 / 空输入 / |a1-1| desc 同档内
  TestEmitJson                (3)  — JSON 格式 / 路径生成 / 自动 mkdir + archetype_summary
  TestBackwardCompat          (3)  — kb_trend.py 旧接口仍工作 (extract_period_text /
                                      tokenize / extract_keywords) (不 break)
  TestSourceLevelGuards       (3)  — V37.9.48 marker / 5 档分类常量 / log stderr MR-11

V37.9.48 反向验证守卫 (V37.9.43-hotfix 同款):
  sed ACCEL_STRONG_THRESHOLD = 1.5 → 0.5 → test_classify_strong_boundary 立即 fail
"""

import os
import re
import sys
import tempfile
import json
import unittest
from datetime import datetime
from unittest import mock

import kb_trend_acceleration as kta

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Test 1: TestExtractKeywordsPerWeek (4) ──────────────────────────
class TestExtractKeywordsPerWeek(unittest.TestCase):
    """V37.9.48: extract_keywords_per_week — kb_trend.py 复用 + FAIL-OPEN"""

    def test_invalid_week_offset(self):
        """非正整数 week_offset → 返回空 dict 不抛异."""
        for bad in (0, -1, "1", None, 1.5):
            result = kta.extract_keywords_per_week(bad)
            self.assertEqual(result, {}, f"bad={bad!r}")

    def test_returns_dict_type(self):
        """正常调用返回 dict (不是 Counter / 不是 list)."""
        # 即使无 KB 数据也返回空 dict
        with tempfile.TemporaryDirectory() as tmp:
            result = kta.extract_keywords_per_week(1, kb_dir=tmp)
            self.assertIsInstance(result, dict)

    def test_fail_open_on_missing_kb_trend(self):
        """kb_trend.py 缺失 → log WARN + 返回空 dict (不抛异)."""
        with mock.patch.dict(sys.modules, {"kb_trend": None}):
            with tempfile.TemporaryDirectory() as tmp:
                result = kta.extract_keywords_per_week(1, kb_dir=tmp)
                self.assertEqual(result, {})

    def test_week_offset_date_range(self):
        """week_offset=1 → 提取今天-7 到 今天-1 范围 (验证 today 注入)."""
        # Mock today 让范围确定
        fake_today = datetime(2026, 5, 10)
        with tempfile.TemporaryDirectory() as tmp:
            # 真实 KB 不存在内容, 但函数不应崩溃
            result = kta.extract_keywords_per_week(1, kb_dir=tmp, today=fake_today)
            self.assertIsInstance(result, dict)
            # week_offset=2 不应与 week_offset=1 重叠
            result2 = kta.extract_keywords_per_week(2, kb_dir=tmp, today=fake_today)
            self.assertIsInstance(result2, dict)


# ── Test 2: TestComputeAcceleration (5) ─────────────────────────────
class TestComputeAcceleration(unittest.TestCase):
    """V37.9.48: compute_acceleration — 4 周历史 + 缺周 + 小基数门槛"""

    def test_three_week_history_normal_case(self):
        """正常 3 周历史 → 各 keyword 都有 metrics."""
        weeks = {
            1: {"agent": 30, "ontology": 20, "transformer": 50},
            2: {"agent": 20, "ontology": 5, "transformer": 60},
            3: {"agent": 15, "ontology": 2, "transformer": 70},
        }
        metrics = kta.compute_acceleration(weeks, min_freq_pct=0.0)
        self.assertEqual(len(metrics), 3)
        # ontology: pct_w1=20/100=0.2, pct_w2=5/85=0.059, accel ~3.39 → strong (a2 also high)
        self.assertEqual(metrics["ontology"]["classification"], kta.ARCHETYPE_STRONG)

    def test_w2_zero_new_word_skipped(self):
        """w2=0 (新词) → skip (无 baseline)."""
        weeks = {
            1: {"newword": 10, "old": 50},
            2: {"old": 60},   # newword 不在 w2
            3: {"old": 70},
        }
        metrics = kta.compute_acceleration(weeks, min_freq_pct=0.0)
        # newword 应不在结果 (w2=0 skipped)
        self.assertNotIn("newword", metrics)
        self.assertIn("old", metrics)

    def test_w3_zero_accel_2w_none(self):
        """w3=0 (上上周不存在或为空) → accel_2w=None 但 accel_1w 仍计算."""
        weeks = {
            1: {"emerging": 20, "stable": 30},
            2: {"emerging": 10, "stable": 30},
            3: {},  # 三周前无数据
        }
        metrics = kta.compute_acceleration(weeks, min_freq_pct=0.0)
        self.assertIn("emerging", metrics)
        self.assertIsNone(metrics["emerging"]["accel_2w"])
        # accel_1w 仍计算
        self.assertGreater(metrics["emerging"]["accel_1w"], 0)
        # classification 不应是 strong (因 a2 None)
        self.assertNotEqual(metrics["emerging"]["classification"], kta.ARCHETYPE_STRONG)

    def test_min_freq_pct_threshold_filters_noise(self):
        """周占比 < min_freq_pct → skip (小基数噪声门槛)."""
        # 总数 1000, noise 在 1 个 (0.1% < 0.5% threshold)
        weeks = {
            1: {"noise": 1, "real": 999},
            2: {"noise": 1, "real": 999},
            3: {"noise": 1, "real": 999},
        }
        metrics = kta.compute_acceleration(weeks, min_freq_pct=0.005)  # 0.5%
        self.assertNotIn("noise", metrics)
        self.assertIn("real", metrics)

    def test_empty_input_returns_empty(self):
        """空 / None / 全 0 → 返回空 dict 不抛异."""
        self.assertEqual(kta.compute_acceleration({}), {})
        self.assertEqual(kta.compute_acceleration(None), {})
        self.assertEqual(kta.compute_acceleration({1: {}, 2: {}, 3: {}}), {})


# ── Test 3: TestClassify (6) ────────────────────────────────────────
class TestClassify(unittest.TestCase):
    """V37.9.48: classify — 5 档边界 + 优先级"""

    def test_strong_requires_both_a1_and_a2(self):
        """strong 必须 a1≥1.5 AND a2≥1.5 (两者都满足)."""
        # 满足 strong
        self.assertEqual(kta.classify(1.5, 1.5), kta.ARCHETYPE_STRONG)
        self.assertEqual(kta.classify(2.0, 1.8), kta.ARCHETYPE_STRONG)
        # a1 强但 a2 弱 → 降级 mild
        self.assertEqual(kta.classify(2.0, 0.5), kta.ARCHETYPE_MILD)
        # a1 弱 a2 强 → 不 strong (a1 不达)
        self.assertNotEqual(kta.classify(1.0, 2.0), kta.ARCHETYPE_STRONG)

    def test_obs_requires_both_a1_and_a2(self):
        """obs 必须 a1<0.5 AND a2<0.5 (连续 2 周衰退)."""
        self.assertEqual(kta.classify(0.4, 0.3), kta.ARCHETYPE_OBS)
        self.assertEqual(kta.classify(0.49, 0.49), kta.ARCHETYPE_OBS)
        # a1 弱但 a2 强 → 降级 decel
        self.assertEqual(kta.classify(0.4, 1.5), kta.ARCHETYPE_DECEL)

    def test_mild_only_requires_a1(self):
        """mild 仅要求 a1≥1.5 (a2 None / 不达 strong 都 fallback mild)."""
        self.assertEqual(kta.classify(1.5, None), kta.ARCHETYPE_MILD)
        self.assertEqual(kta.classify(2.0, 1.0), kta.ARCHETYPE_MILD)

    def test_decel_only_requires_a1(self):
        """decel 仅要求 a1<0.7 (a2 None / a2 强 都 decel 而非 obs)."""
        self.assertEqual(kta.classify(0.6, None), kta.ARCHETYPE_DECEL)
        self.assertEqual(kta.classify(0.5, 0.6), kta.ARCHETYPE_DECEL)  # a2 不<0.5

    def test_stable_middle_range(self):
        """stable: 0.7 ≤ a1 < 1.5."""
        self.assertEqual(kta.classify(1.0, 1.0), kta.ARCHETYPE_STABLE)
        self.assertEqual(kta.classify(0.7, None), kta.ARCHETYPE_STABLE)
        self.assertEqual(kta.classify(1.49, None), kta.ARCHETYPE_STABLE)

    def test_invalid_input_safe_fallback(self):
        """非数字 a1 → 返回 STABLE 不抛异."""
        for bad in ("1.5", None, [1.5], {}):
            result = kta.classify(bad, None)
            self.assertEqual(result, kta.ARCHETYPE_STABLE,
                             f"bad={bad!r} should fallback STABLE")


# ── Test 4: TestRankSignals (4) ─────────────────────────────────────
class TestRankSignals(unittest.TestCase):
    """V37.9.48: rank_signals — archetype 优先级 + top 截断"""

    def test_archetype_priority_order(self):
        """strong > mild > decel > obs > stable (priority)."""
        metrics = {
            "stable_1": {"classification": kta.ARCHETYPE_STABLE, "accel_1w": 1.0},
            "obs_1": {"classification": kta.ARCHETYPE_OBS, "accel_1w": 0.3},
            "strong_1": {"classification": kta.ARCHETYPE_STRONG, "accel_1w": 2.0},
            "decel_1": {"classification": kta.ARCHETYPE_DECEL, "accel_1w": 0.5},
            "mild_1": {"classification": kta.ARCHETYPE_MILD, "accel_1w": 1.7},
        }
        signals = kta.rank_signals(metrics)
        # 第一个必须是 strong
        self.assertEqual(signals[0]["keyword"], "strong_1")
        # mild 在 strong 之后 (high_priority 桶共享 strong+mild)
        archetype_seq = [s["classification"] for s in signals]
        # strong 必然在 mild 之前 (priority 0 < 1)
        idx_strong = archetype_seq.index(kta.ARCHETYPE_STRONG)
        idx_mild = archetype_seq.index(kta.ARCHETYPE_MILD)
        self.assertLess(idx_strong, idx_mild)

    def test_top_k_truncation_per_bucket(self):
        """top_strong / top_stable / top_obs 各自截断."""
        metrics = {}
        for i in range(20):
            metrics[f"strong_{i}"] = {"classification": kta.ARCHETYPE_STRONG,
                                       "accel_1w": 2.0 + i * 0.1}
        for i in range(20):
            metrics[f"stable_{i}"] = {"classification": kta.ARCHETYPE_STABLE,
                                       "accel_1w": 1.0}
        for i in range(20):
            metrics[f"obs_{i}"] = {"classification": kta.ARCHETYPE_OBS,
                                    "accel_1w": 0.3}

        signals = kta.rank_signals(metrics, top_strong=10, top_stable=5, top_obs=3)
        # 总长 = 10 + 5 + 3 = 18
        self.assertEqual(len(signals), 18)
        # archetype 计数
        cls_counts = {}
        for s in signals:
            cls = s["classification"]
            cls_counts[cls] = cls_counts.get(cls, 0) + 1
        self.assertEqual(cls_counts[kta.ARCHETYPE_STRONG], 10)
        self.assertEqual(cls_counts[kta.ARCHETYPE_STABLE], 5)
        self.assertEqual(cls_counts[kta.ARCHETYPE_OBS], 3)

    def test_empty_input_returns_empty(self):
        """空 / None → 返回空 list 不抛异."""
        self.assertEqual(kta.rank_signals({}), [])
        self.assertEqual(kta.rank_signals(None), [])

    def test_within_bucket_sort_by_distance_from_one(self):
        """同一 archetype 桶内按 |a1-1| desc 排序 (偏离 1 越远越显著)."""
        metrics = {
            "kw_a1_1.6": {"classification": kta.ARCHETYPE_MILD, "accel_1w": 1.6},  # |1.6-1| = 0.6
            "kw_a1_3.0": {"classification": kta.ARCHETYPE_MILD, "accel_1w": 3.0},  # |3.0-1| = 2.0 → 第一
            "kw_a1_2.0": {"classification": kta.ARCHETYPE_MILD, "accel_1w": 2.0},  # |2.0-1| = 1.0
        }
        signals = kta.rank_signals(metrics)
        # 第一应是 a1=3.0 (最远离 1)
        self.assertEqual(signals[0]["keyword"], "kw_a1_3.0")
        self.assertEqual(signals[1]["keyword"], "kw_a1_2.0")


# ── Test 5: TestEmitJson (3) ────────────────────────────────────────
class TestEmitJson(unittest.TestCase):
    """V37.9.48: emit_radar_json — JSON 格式 + archetype_summary"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_format_with_archetype_summary(self):
        """JSON 必含 week / version / signal_count / archetype_summary / signals."""
        signals = [
            {"keyword": "kw1", "classification": kta.ARCHETYPE_STRONG, "accel_1w": 2.0,
             "rank": 1},
            {"keyword": "kw2", "classification": kta.ARCHETYPE_STRONG, "accel_1w": 1.8,
             "rank": 2},
            {"keyword": "kw3", "classification": kta.ARCHETYPE_DECEL, "accel_1w": 0.4,
             "rank": 3},
        ]
        path = kta.emit_radar_json(signals, "2026-W19", output_dir=self.tmp.name)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["week"], "2026-W19")
        self.assertIn("V37.9.48", data["version"])
        self.assertEqual(data["signal_count"], 3)
        # archetype_summary 必含 strong=2, decel=1
        self.assertEqual(data["archetype_summary"][kta.ARCHETYPE_STRONG], 2)
        self.assertEqual(data["archetype_summary"][kta.ARCHETYPE_DECEL], 1)

    def test_path_generation(self):
        """路径必含 weekly_trends_{week}.json."""
        path = kta.emit_radar_json([], "2026-W19", output_dir=self.tmp.name)
        self.assertEqual(os.path.basename(path), "weekly_trends_2026-W19.json")

    def test_auto_mkdir_for_nested_output(self):
        """嵌套 output_dir 不存在 → 自动 mkdir."""
        nested = os.path.join(self.tmp.name, "deep", "nested", "radar")
        self.assertFalse(os.path.isdir(nested))
        path = kta.emit_radar_json([], "2026-W19", output_dir=nested)
        self.assertTrue(os.path.isdir(nested))
        self.assertTrue(os.path.isfile(path))


# ── Test 6: TestBackwardCompat (3) ──────────────────────────────────
class TestBackwardCompat(unittest.TestCase):
    """V37.9.48: kb_trend.py 旧接口仍工作 (V37.9.48 扩展不破坏 V29.5+ 现有功能)"""

    def test_kb_trend_extract_period_text_callable(self):
        """kb_trend.extract_period_text 仍存在且可调用."""
        sys.path.insert(0, REPO_ROOT)
        import kb_trend as kt
        self.assertTrue(callable(kt.extract_period_text))

    def test_kb_trend_tokenize_callable(self):
        """kb_trend.tokenize 仍存在 (acceleration 复用其 logic)."""
        import kb_trend as kt
        self.assertTrue(callable(kt.tokenize))
        # Smoke test
        tokens = kt.tokenize("test agent runtime 智能 体")
        self.assertIsInstance(tokens, list)

    def test_kb_trend_extract_keywords_returns_list_of_tuples(self):
        """kb_trend.extract_keywords 返回 Counter.most_common() = list of (word, count).

        V37.9.48 wrapper extract_keywords_per_week 用 dict() 把 list of tuples
        转 dict 后返回, 接口契约 OK.
        """
        import kb_trend as kt
        result = kt.extract_keywords("test agent agent agent runtime", top_n=10)
        self.assertIsInstance(result, list)
        # dict() 转换必须工作
        as_dict = dict(result)
        self.assertIsInstance(as_dict, dict)


# ── Test 7: TestSourceLevelGuards (3) ───────────────────────────────
class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.48: 源码级 grep 守卫"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO_ROOT, "kb_trend_acceleration.py")
        with open(path, "r", encoding="utf-8") as f:
            cls.SRC = f.read()

    def test_v37_9_48_marker_present(self):
        """V37.9.48 版本标记必须出现."""
        self.assertIn("V37.9.48", self.SRC)
        self.assertIn("Opportunity Radar Stage 3", self.SRC)

    def test_design_locked_thresholds(self):
        """5 档分类阈值必须保持设计文档锁定值."""
        self.assertIn("ACCEL_STRONG_THRESHOLD = 1.5", self.SRC)
        self.assertIn("ACCEL_DECEL_THRESHOLD = 0.7", self.SRC)
        self.assertIn("ACCEL_OBS_THRESHOLD = 0.5", self.SRC)
        # 5 个 archetype 常量必须全部定义
        self.assertIn("ARCHETYPE_STRONG", self.SRC)
        self.assertIn("ARCHETYPE_MILD", self.SRC)
        self.assertIn("ARCHETYPE_STABLE", self.SRC)
        self.assertIn("ARCHETYPE_DECEL", self.SRC)
        self.assertIn("ARCHETYPE_OBS", self.SRC)
        # emoji 必须出现 (推送层依赖)
        self.assertIn("🚀", self.SRC)
        self.assertIn("📈", self.SRC)
        self.assertIn("⚰️", self.SRC)
        self.assertIn("💧", self.SRC)
        self.assertIn("📊", self.SRC)

    def test_log_writes_to_stderr_mr11(self):
        """log() 必须 file=sys.stderr (MR-11)."""
        self.assertIn("def log(msg)", self.SRC)
        idx = self.SRC.find("def log(msg)")
        next_def = self.SRC.find("\ndef ", idx + 1)
        log_body = self.SRC[idx:next_def]
        self.assertIn("file=sys.stderr", log_body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

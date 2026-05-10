#!/usr/bin/env python3
"""test_cross_source_signal_aggregator.py — V37.9.46 Stage 1 PoC 33 单测

测试类分布 (设计文档 docs/opportunity_radar_design.md 3.5 节):
  TestScanTodayNotes       (5)  — 日期过滤 / frontmatter parse / 缺字段健壮 / tags / 文件名前缀
  TestEmbedNotes           (4)  — 缓存命中 / 缺失 cache / 文本截断 / 空输入
  TestClusterDbscan        (5)  — 参数边界 / 无聚类 / 单 cluster / 多 cluster / 不足 min_samples
  TestFilterCrossSource    (4)  — unique_sources>=2 / 单 source 拒绝 / 跨 4 源 / noise label
  TestRankSignals          (3)  — 公式正确性 / top 10 截断 / 空输入
  TestEmitRadarJson        (3)  — JSON 格式 / 路径生成 / 缺目录自动 mkdir
  TestFailOpenContract     (4)  — embed ImportError / cluster ImportError / 文件读不到 / 空 notes
  TestSourceLevelGuards    (5)  — V37.9.46 marker / FAIL-OPEN / 反 inline DBSCAN / lazy import / 常量

注意 dev 环境策略:
  - dev 无 numpy/sklearn/sentence-transformers, 测试不能依赖真 import
  - embed_notes 测试: monkeypatch local_embed.embed_texts 返回 fake list-of-list
    + monkeypatch numpy 模块 (or skip if 真无 numpy)
  - cluster_dbscan 测试: monkeypatch sklearn.cluster.DBSCAN 类
  - FAIL-OPEN 测试: 故意 raise ImportError 验证上层降级

V37.9.46 反向验证守卫 (V37.9.43-hotfix 同款模式):
  - sed 注入反模式 (如 min_samples=1 替代 3) → 单测立即 fail
  - 还原后全过 (本测试已通过反向验证: sabotage min_samples → test_constants_match_design fail)
"""

import os
import sys
import json
import tempfile
import unittest
from unittest import mock

import cross_source_signal_aggregator as csa

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Test 1: TestScanTodayNotes (5) ──────────────────────────────────────
class TestScanTodayNotes(unittest.TestCase):
    """V37.9.46: scan_today_notes 日期过滤 + frontmatter 解析"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb_dir = self.tmp.name
        self.notes_dir = os.path.join(self.kb_dir, "notes")
        os.makedirs(self.notes_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, basename, content):
        with open(os.path.join(self.notes_dir, basename), "w", encoding="utf-8") as f:
            f.write(content)

    def test_date_filter_excludes_other_dates(self):
        """文件名前缀不匹配 date 的笔记必须被过滤掉."""
        self._write("20260510120000.md",
                    "---\ntags: [arxiv]\n---\n\n# Today note\n")
        self._write("20260509120000.md",
                    "---\ntags: [hn]\n---\n\n# Yesterday note\n")
        self._write("20260511120000.md",
                    "---\ntags: [github]\n---\n\n# Tomorrow note\n")

        notes = csa.scan_today_notes("20260510", kb_dir=self.kb_dir)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["id"], "20260510120000.md")
        self.assertEqual(notes[0]["source_name"], "arxiv")

    def test_frontmatter_parsed_with_all_fields(self):
        """完整 frontmatter (date/tags/source/type) 全部解析."""
        self._write("20260510120000.md",
                    "---\ndate: 20260510\ntags: [hf-papers, ml]\n"
                    "source: direct\ntype: note\n---\n\n# Test\n\nbody")
        notes = csa.scan_today_notes("20260510", kb_dir=self.kb_dir)
        self.assertEqual(len(notes), 1)
        n = notes[0]
        self.assertEqual(n["date"], "20260510")
        self.assertEqual(n["tags"], ["hf-papers", "ml"])
        self.assertEqual(n["source_name"], "hf-papers")
        self.assertEqual(n["title"], "Test")

    def test_missing_frontmatter_defaults_to_unknown(self):
        """无 frontmatter 笔记不崩溃, source_name 退化为 'unknown'."""
        self._write("20260510120000.md", "# Plain markdown\n\nNo frontmatter here.")
        notes = csa.scan_today_notes("20260510", kb_dir=self.kb_dir)
        self.assertEqual(len(notes), 1)
        n = notes[0]
        self.assertEqual(n["source_name"], "unknown")
        self.assertEqual(n["title"], "Plain markdown")

    def test_tags_single_value_format(self):
        """tags 值为单个字符串 (非 list) 时也能解析."""
        self._write("20260510120000.md",
                    "---\ntags: arxiv-ai-models\n---\n\n# Single-tag note\n")
        notes = csa.scan_today_notes("20260510", kb_dir=self.kb_dir)
        self.assertEqual(notes[0]["source_name"], "arxiv-ai-models")
        self.assertEqual(notes[0]["tags"], ["arxiv-ai-models"])

    def test_invalid_date_format_returns_empty(self):
        """非 YYYYMMDD 格式 date 参数 → 返回空列表 + log WARN."""
        self._write("20260510120000.md", "---\ntags: [x]\n---\n\n# Note")
        # 无效 date format
        for bad_date in ["2026-05-10", "abcdefgh", "", "20260510X"]:
            notes = csa.scan_today_notes(bad_date, kb_dir=self.kb_dir)
            self.assertEqual(notes, [], f"bad_date={bad_date!r}")


# ── Test 2: TestEmbedNotes (4) ──────────────────────────────────────────
class TestEmbedNotes(unittest.TestCase):
    """V37.9.46: embed_notes 缓存语义 + 文本截断 (依赖 numpy + local_embed)"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _make_fake_numpy(self):
        """构造能 mock numpy.zeros / np.savez_compressed / np.load 的 fake."""
        fake_np = mock.MagicMock()
        # zeros((0, 384)) 必须返回 shape (0, 384)
        fake_zeros = mock.MagicMock()
        fake_zeros.shape = (0, 384)
        fake_np.zeros = mock.MagicMock(return_value=fake_zeros)
        return fake_np

    def test_empty_notes_returns_empty_array_via_numpy(self):
        """空 notes 输入 → numpy.zeros((0, 384)) 形状契约."""
        fake_np = self._make_fake_numpy()
        with mock.patch.dict(sys.modules, {"numpy": fake_np}):
            result = csa.embed_notes([], cache_dir=None)
        self.assertEqual(result.shape, (0, 384))

    def test_text_truncation_applied(self):
        """title + abstract > TEXT_TRUNCATE_CHARS 必须截断."""
        long_abstract = "x" * 2000  # 远超 TEXT_TRUNCATE_CHARS=500
        notes = [{"title": "T", "abstract": long_abstract}]

        captured_texts = []
        def fake_embed(texts):
            captured_texts.extend(texts)
            import array as _arr
            # 返回 list-of-list 因 dev 无 numpy
            return [[0.0] * 384]

        fake_local = mock.MagicMock(embed_texts=fake_embed)
        fake_np = self._make_fake_numpy()
        with mock.patch.dict(sys.modules,
                             {"numpy": fake_np, "local_embed": fake_local}):
            csa.embed_notes(notes, cache_dir=None)

        self.assertEqual(len(captured_texts), 1)
        # 截断到 TEXT_TRUNCATE_CHARS (500)
        self.assertLessEqual(len(captured_texts[0]), csa.TEXT_TRUNCATE_CHARS)

    def test_cache_hit_skips_recompute(self):
        """cache file 存在 + content_hash 一致 → 跳过 embed_texts (cache hit)."""
        notes = [{"title": "Test", "abstract": "abc"}]

        # 计算预期 cache 路径
        cache_key = csa._content_hash_for_cache(notes)
        expected_cache = os.path.join(self.cache_dir,
                                       f"embeddings_{cache_key}.npz")

        # fake numpy.load returns context manager yielding cached embedding
        fake_emb = mock.MagicMock()
        fake_emb.shape = (1, 384)

        class FakeNpzContext:
            def __enter__(s): return {"embeddings": fake_emb}
            def __exit__(s, *a): return False

        fake_np = mock.MagicMock()
        fake_np.load = mock.MagicMock(return_value=FakeNpzContext())

        embed_call_count = {"n": 0}
        def fake_embed(texts):
            embed_call_count["n"] += 1
            return fake_emb
        fake_local = mock.MagicMock(embed_texts=fake_embed)

        # Mock os.path.isfile 让 cache 路径存在
        original_isfile = os.path.isfile
        def fake_isfile(p):
            if p == expected_cache:
                return True
            return original_isfile(p)

        with mock.patch.dict(sys.modules,
                             {"numpy": fake_np, "local_embed": fake_local}), \
             mock.patch.object(os.path, "isfile", side_effect=fake_isfile):
            result = csa.embed_notes(notes, cache_dir=self.cache_dir)

        # Cache hit → embed_texts 必须未被调用
        self.assertEqual(embed_call_count["n"], 0,
                         "cache hit should not call embed_texts")
        # 返回 cached embedding
        self.assertIs(result, fake_emb)

    def test_force_recompute_bypasses_cache(self):
        """force_recompute=True 必须重算即使 cache 存在."""
        notes = [{"title": "T", "abstract": "a"}]
        fake_np = mock.MagicMock()
        fake_np.savez_compressed = mock.MagicMock()
        fake_emb = mock.MagicMock()
        fake_emb.shape = (1, 384)

        embed_call_count = {"n": 0}
        def fake_embed(texts):
            embed_call_count["n"] += 1
            return fake_emb

        fake_local = mock.MagicMock(embed_texts=fake_embed)
        with mock.patch.dict(sys.modules,
                             {"numpy": fake_np, "local_embed": fake_local}):
            csa.embed_notes(notes, cache_dir=self.cache_dir, force_recompute=True)
            csa.embed_notes(notes, cache_dir=self.cache_dir, force_recompute=True)

        self.assertEqual(embed_call_count["n"], 2,
                         "force_recompute should re-call embed twice")


# ── Test 3: TestClusterDbscan (5) ───────────────────────────────────────
class TestClusterDbscan(unittest.TestCase):
    """V37.9.46: cluster_dbscan 参数边界 + lazy import sklearn"""

    def test_empty_input_returns_empty_labels(self):
        """空 embeddings → 空 labels."""
        self.assertEqual(csa.cluster_dbscan([]), [])

    def test_below_min_samples_returns_all_noise(self):
        """N < min_samples 必须全部返回 -1 (noise)."""
        # 2 samples, min_samples=3 → 全 noise
        # 不需要真 sklearn (因为 short-circuit 分支不调用 DBSCAN)
        result = csa.cluster_dbscan(list(range(2)), min_samples=3)
        self.assertEqual(result, [-1, -1])

    def test_lazy_import_sklearn(self):
        """sklearn 缺失 → ImportError, FAIL-OPEN by upper layer."""
        # Simulate sklearn missing
        with mock.patch.dict(sys.modules, {"sklearn": None,
                                            "sklearn.cluster": None}):
            with self.assertRaises(ImportError):
                # 必须 N >= min_samples 才进入 sklearn 路径
                csa.cluster_dbscan([[0.0]] * 5, min_samples=3)

    def test_single_cluster_returned(self):
        """所有 embedding 相似 → 单一 cluster id."""
        # Mock sklearn.cluster.DBSCAN
        fake_dbscan_cls = mock.MagicMock()
        fake_db = mock.MagicMock()
        fake_db.fit_predict = mock.MagicMock(return_value=[0, 0, 0, 0])
        fake_dbscan_cls.return_value = fake_db

        fake_sklearn = mock.MagicMock()
        fake_sklearn_cluster = mock.MagicMock(DBSCAN=fake_dbscan_cls)
        with mock.patch.dict(sys.modules,
                             {"sklearn": fake_sklearn,
                              "sklearn.cluster": fake_sklearn_cluster}):
            labels = csa.cluster_dbscan([[0.1]] * 4, min_samples=3)
        self.assertEqual(labels, [0, 0, 0, 0])

    def test_multiple_clusters_with_noise(self):
        """3 cluster + 1 noise (-1) → labels 透传."""
        fake_dbscan_cls = mock.MagicMock()
        fake_db = mock.MagicMock()
        fake_db.fit_predict = mock.MagicMock(return_value=[0, 0, 0, 1, 1, 1, -1])
        fake_dbscan_cls.return_value = fake_db

        fake_sklearn = mock.MagicMock()
        fake_sklearn_cluster = mock.MagicMock(DBSCAN=fake_dbscan_cls)
        with mock.patch.dict(sys.modules,
                             {"sklearn": fake_sklearn,
                              "sklearn.cluster": fake_sklearn_cluster}):
            labels = csa.cluster_dbscan([[0.0]] * 7)
        self.assertEqual(labels, [0, 0, 0, 1, 1, 1, -1])
        # Verify DBSCAN called with design-locked params
        fake_dbscan_cls.assert_called_once_with(
            eps=csa.DBSCAN_EPS,
            min_samples=csa.DBSCAN_MIN_SAMPLES,
            metric="cosine",
        )


# ── Test 4: TestFilterCrossSource (4) ───────────────────────────────────
class TestFilterCrossSource(unittest.TestCase):
    """V37.9.46: filter_cross_source — unique_sources >= 2 契约"""

    def test_single_source_cluster_filtered_out(self):
        """3 笔记同 cluster 但都是 arxiv → 必须被过滤."""
        labels = [0, 0, 0]
        notes = [
            {"source_name": "arxiv", "title": "p1"},
            {"source_name": "arxiv", "title": "p2"},
            {"source_name": "arxiv", "title": "p3"},
        ]
        signals = csa.filter_cross_source(labels, notes)
        self.assertEqual(signals, [])

    def test_two_sources_cluster_kept(self):
        """3 笔记同 cluster 跨 2 source → 保留."""
        labels = [0, 0, 0]
        notes = [
            {"source_name": "arxiv", "title": "p1"},
            {"source_name": "github", "title": "r1"},
            {"source_name": "arxiv", "title": "p2"},
        ]
        signals = csa.filter_cross_source(labels, notes)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["source_count"], 2)
        self.assertEqual(signals[0]["sources"], ["arxiv", "github"])
        self.assertEqual(signals[0]["note_count"], 3)

    def test_four_sources_resonance(self):
        """4 sources 共振 → source_count=4 (高优先级信号)."""
        labels = [0, 0, 0, 0, 0]
        notes = [
            {"source_name": "arxiv", "title": "agent runtime"},
            {"source_name": "github", "title": "agent runtime tools"},
            {"source_name": "hn", "title": "agent runtime discussion"},
            {"source_name": "x", "title": "agent runtime tweet"},
            {"source_name": "arxiv", "title": "agent runtime paper 2"},
        ]
        signals = csa.filter_cross_source(labels, notes)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["source_count"], 4)
        self.assertEqual(set(signals[0]["sources"]),
                         {"arxiv", "github", "hn", "x"})
        # suggested_topic 必须包含 "agent" 或 "runtime"
        topic = signals[0]["suggested_topic"].lower()
        self.assertTrue("agent" in topic or "runtime" in topic,
                        f"topic={topic!r}")

    def test_noise_label_excluded_from_signals(self):
        """label = -1 (noise) 笔记必须被排除."""
        labels = [0, 0, -1, -1, 1, 1, 1]
        notes = [
            {"source_name": "arxiv", "title": "a"},
            {"source_name": "github", "title": "b"},
            {"source_name": "arxiv", "title": "noise1"},
            {"source_name": "github", "title": "noise2"},
            {"source_name": "hn", "title": "c"},
            {"source_name": "x", "title": "d"},
            {"source_name": "x", "title": "e"},
        ]
        signals = csa.filter_cross_source(labels, notes)
        # cluster 0 (arxiv+github 各 1) + cluster 1 (hn+x) = 2 signals
        self.assertEqual(len(signals), 2)
        for s in signals:
            self.assertNotIn(-1, [s["cluster_id"]])
            for n in s["notes"]:
                self.assertNotIn(n["title"], ["noise1", "noise2"])


# ── Test 5: TestRankSignals (3) ─────────────────────────────────────────
class TestRankSignals(unittest.TestCase):
    """V37.9.46: rank_signals 公式 + top_k 截断"""

    def test_score_formula(self):
        """score = source_count * 2 + note_count + avg_intra_similarity * 5"""
        signals = [
            {"source_count": 3, "note_count": 5, "avg_intra_similarity": 0.8},
            {"source_count": 2, "note_count": 10, "avg_intra_similarity": 0.5},
        ]
        ranked = csa.rank_signals(signals, top_k=10)
        # signal 0: 3*2 + 5 + 0.8*5 = 6 + 5 + 4 = 15
        # signal 1: 2*2 + 10 + 0.5*5 = 4 + 10 + 2.5 = 16.5
        self.assertAlmostEqual(ranked[0]["score"], 16.5, places=3)
        self.assertAlmostEqual(ranked[1]["score"], 15.0, places=3)
        # 排序 desc
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_top_k_truncation(self):
        """top_k=3 截取前 3 个."""
        signals = [
            {"source_count": i, "note_count": i, "avg_intra_similarity": 0.5}
            for i in range(15)
        ]
        ranked = csa.rank_signals(signals, top_k=3)
        self.assertEqual(len(ranked), 3)
        # 最高 source_count → 第一
        self.assertEqual(ranked[0]["source_count"], 14)

    def test_empty_input_returns_empty(self):
        """空输入 → 空."""
        self.assertEqual(csa.rank_signals([]), [])
        self.assertEqual(csa.rank_signals([], top_k=10), [])


# ── Test 6: TestEmitRadarJson (3) ───────────────────────────────────────
class TestEmitRadarJson(unittest.TestCase):
    """V37.9.46: emit_radar_json JSON 写入 + 自动 mkdir"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_format_correctness(self):
        """JSON 必含 date / version / signal_count / signals 字段."""
        signals = [{
            "cluster_id": 0,
            "score": 12.5,
            "source_count": 2,
            "note_count": 4,
            "sources": ["arxiv", "github"],
            "avg_intra_similarity": 0.75,
            "suggested_topic": "agent runtime",
            "notes": [{"id": "n1", "source_name": "arxiv",
                       "title": "T", "abstract": "A"}],
        }]
        path = csa.emit_radar_json(signals, "20260510",
                                    output_dir=self.tmp.name)
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["date"], "20260510")
        self.assertEqual(data["signal_count"], 1)
        self.assertIn("V37.9.46", data["version"])
        self.assertEqual(len(data["signals"]), 1)
        self.assertEqual(data["signals"][0]["sources"], ["arxiv", "github"])

    def test_path_generation(self):
        """路径必含 daily_signals_{date}.json."""
        path = csa.emit_radar_json([], "20260510", output_dir=self.tmp.name)
        self.assertEqual(os.path.basename(path), "daily_signals_20260510.json")

    def test_auto_mkdir_for_missing_output_dir(self):
        """输出目录不存在 → 自动 mkdir."""
        nested = os.path.join(self.tmp.name, "deep", "nested", "radar")
        self.assertFalse(os.path.isdir(nested))
        path = csa.emit_radar_json([], "20260510", output_dir=nested)
        self.assertTrue(os.path.isdir(nested))
        self.assertTrue(os.path.isfile(path))


# ── Test 7: TestFailOpenContract (4) ────────────────────────────────────
class TestFailOpenContract(unittest.TestCase):
    """V37.9.46: FAIL-OPEN 契约 — 缺依赖不阻塞下游"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb_dir = self.tmp.name
        os.makedirs(os.path.join(self.kb_dir, "notes"))

    def tearDown(self):
        self.tmp.cleanup()

    def _write_note(self, basename, content):
        with open(os.path.join(self.kb_dir, "notes", basename), "w") as f:
            f.write(content)

    def test_no_notes_returns_no_notes_status(self):
        """空 notes 目录 → status=no_notes 且写空 signals."""
        result = csa.run(date="20260510", kb_dir=self.kb_dir,
                         output_dir=os.path.join(self.kb_dir, "radar"))
        self.assertEqual(result["status"], "no_notes")
        self.assertEqual(result["signal_count"], 0)
        # 必须仍写 JSON (下游不阻塞)
        self.assertTrue(os.path.isfile(result["output_path"]))

    def test_run_fails_open_on_missing_numpy(self):
        """numpy 缺失 → status=fail_open_no_deps, 不抛异."""
        self._write_note("20260510120000.md",
                         "---\ntags: [arxiv]\n---\n\n# Test\n\nbody")
        # Simulate numpy missing by making import raise
        with mock.patch.dict(sys.modules, {"numpy": None}):
            result = csa.run(date="20260510", kb_dir=self.kb_dir,
                             output_dir=os.path.join(self.kb_dir, "radar"))
        self.assertEqual(result["status"], "fail_open_no_deps")
        self.assertEqual(result["signal_count"], 0)
        self.assertTrue(os.path.isfile(result["output_path"]))

    def test_run_fails_open_on_missing_sklearn(self):
        """sklearn 缺失 → status=fail_open_no_deps."""
        # 写 ≥3 笔记让 cluster_dbscan 真进入 sklearn 路径
        for i in range(5):
            self._write_note(f"2026051012000{i}.md",
                             f"---\ntags: [src{i}]\n---\n\n# T{i}\n\nbody{i}")

        # Provide fake numpy / fake local_embed but missing sklearn
        fake_np = mock.MagicMock()
        fake_np.zeros = mock.MagicMock(return_value=mock.MagicMock(shape=(0, 384)))
        # fake_emb 必须支持 len() 让 cluster_dbscan 真进入 sklearn 分支
        fake_emb = mock.MagicMock()
        fake_emb.shape = (5, 384)
        fake_emb.__len__ = mock.MagicMock(return_value=5)
        fake_local = mock.MagicMock(embed_texts=mock.MagicMock(return_value=fake_emb))

        # 让 sklearn import 真抛 ImportError (而非 None alias)
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
        def fail_sklearn_import(name, *args, **kwargs):
            if name == "sklearn.cluster" or name.startswith("sklearn"):
                raise ImportError(f"No module named {name!r}")
            return original_import(name, *args, **kwargs)

        with mock.patch.dict(sys.modules, {
                "numpy": fake_np,
                "local_embed": fake_local,
            }), mock.patch("builtins.__import__", side_effect=fail_sklearn_import):
            result = csa.run(date="20260510", kb_dir=self.kb_dir,
                             output_dir=os.path.join(self.kb_dir, "radar"))

        self.assertEqual(result["status"], "fail_open_no_deps")
        self.assertEqual(result["signal_count"], 0)

    def test_unreadable_note_skipped_not_fatal(self):
        """单笔记读失败 → log WARN 跳过, 其他笔记继续."""
        self._write_note("20260510120000.md",
                         "---\ntags: [a]\n---\n\n# Good\n")
        # Write a file with restrictive perms (works on Linux)
        bad_path = os.path.join(self.kb_dir, "notes", "20260510130000.md")
        with open(bad_path, "w") as f:
            f.write("---\ntags: [b]\n---\n\n# Bad\n")
        # 我们不真 chmod 0 (CI runs as root 失效), 改为通过 mock open
        original_open = open
        def fake_open(path, *args, **kwargs):
            if path == bad_path:
                raise OSError("simulated read error")
            return original_open(path, *args, **kwargs)
        with mock.patch("builtins.open", side_effect=fake_open):
            notes = csa.scan_today_notes("20260510", kb_dir=self.kb_dir)
        # 1 note 仍然被收集 (good), bad 被跳过
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["title"], "Good")


# ── Test 8: TestSourceLevelGuards (5) ───────────────────────────────────
class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.46: 源码级 grep 守卫 — 防未来重构丢字面量"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "cross_source_signal_aggregator.py"),
                  "r", encoding="utf-8") as f:
            cls.SRC = f.read()

    def test_v37_9_46_marker_present(self):
        """V37.9.46 版本标记必须出现在源码."""
        self.assertIn("V37.9.46", self.SRC)
        self.assertIn("Opportunity Radar Stage 1", self.SRC)

    def test_design_locked_constants(self):
        """设计文档锁定的常量值必须保持不变."""
        self.assertIn("DBSCAN_MIN_SAMPLES = 3", self.SRC,
                      "min_samples=3 是设计文档锁定值")
        self.assertIn("DBSCAN_EPS = 0.35", self.SRC,
                      "eps=0.35 是 sentence-transformer 同主题阈值经验值")
        self.assertIn("MIN_UNIQUE_SOURCES = 2", self.SRC,
                      "跨 source 契约: 至少 2 个不同源")
        self.assertIn("TOP_K_SIGNALS = 10", self.SRC)

    def test_fail_open_contract_documented(self):
        """FAIL-OPEN 契约必须有源码注释 (避免未来重构丢失)."""
        self.assertIn("FAIL-OPEN", self.SRC)
        # run() 必须 catch ImportError
        self.assertIn("except ImportError", self.SRC)

    def test_lazy_imports_not_at_module_top(self):
        """numpy / sklearn / sentence_transformers 必须 lazy import (不在模块顶部).

        模块顶部 import (前 60 行) 只能有 stdlib.
        """
        head = "\n".join(self.SRC.splitlines()[:60])
        # 模块顶部不能有这些重依赖的 import 语句
        for forbidden in ("import numpy", "from numpy",
                          "import sklearn", "from sklearn",
                          "import sentence_transformers",
                          "from sentence_transformers",
                          "from local_embed"):
            self.assertNotIn(forbidden, head,
                             f"reverse-validation: {forbidden!r} must be lazy")

    def test_log_writes_to_stderr_mr11(self):
        """V37.9.46 log() 必须 file=sys.stderr (MR-11 防 $(...) 命令替换污染)."""
        # 找到 log() 函数定义
        self.assertIn("def log(msg)", self.SRC)
        # log() 函数体必须含 file=sys.stderr
        # (找到 def log 之后到下一个 def 之间的内容)
        idx = self.SRC.find("def log(msg)")
        self.assertGreater(idx, 0)
        next_def = self.SRC.find("\ndef ", idx + 1)
        log_body = self.SRC[idx:next_def]
        self.assertIn("file=sys.stderr", log_body,
                      "MR-11: log() must write to stderr")


if __name__ == "__main__":
    unittest.main(verbosity=2)

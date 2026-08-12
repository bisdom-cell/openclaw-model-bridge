#!/usr/bin/env python3
"""test_v37_9_293_kb_ghost_vectors.py — KB 幽灵向量 + indexed_at 断链守卫（V37.9.293，对抗审计 B-F4/B-F5）

unfinished [32] 登记的三机制（MED）：
  (a) B-F4 幽灵向量: kb_dedup 每晚 os.remove 重复笔记但 text_index 从不剪除 →
      已删 3 个月的内容带相关度分数被检索, PA 引用当最新（纯语义假输出零日志）
      → kb_embed 每轮磁盘直查剪除 + 权威重建（复用 _rebuild_vectors 自包含文本）
  (b) B-F4 indexed_at 三级丢弃: kb_rag 语义结果不含 / memory_plane metadata 不含 →
      tool_proxy time_info 恒空 + memory_plane FRESHNESS_DECAY 整段死代码
      → 生产端两级补透传（消费端 tool_proxy:467/543 早已在读）
  (c) B-F5 load_vectors 只防短不防长: 超前孤儿行静默截断（张冠李戴零日志）
      → 长/短双侧 stderr WARN + kb_embed 启动对齐校验（尺寸不符 → 权威重建）

附带修复: kb_embed 旧 changed_files 反推逻辑漏"变更后 0 chunks/读失败"场景
（旧 chunks 已移除但文件不在 batch_meta → 不重建 → 位置整体位移张冠李戴）
→ removed_files 在移除动作发生点登记（一物一形）。

行为级测试用 fake local_embed（dev 无 sentence-transformers/numpy 可跑）+
expanduser 补丁做 HOME 级隔离（防 Mac Mini 上扫到真实 ~/.openclaw/workspace）。
"""

import contextlib
import io
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

KB_EMBED_PATH = os.path.join(REPO, "kb_embed.py")
KB_RAG_PATH = os.path.join(REPO, "kb_rag.py")
MEMORY_PLANE_PATH = os.path.join(REPO, "memory_plane.py")
TOOL_PROXY_PATH = os.path.join(REPO, "tool_proxy.py")

FAKE_DIM = 8

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class _FakeVectorArray(list):
    """ndarray-faithful fake 返回类型（V37.9.299 血案防线）。

    真 local_embed.embed_texts 返回 numpy ndarray；多元素 ndarray 的 __bool__
    抛 ValueError。V37.9.293 的 `if vectors:` 在 dev 恒为 True（fake 返回裸
    list）却在生产每晚崩溃 → 索引静默停更 4 天。让 fake 忠实于真类型的真值
    语义后，任何对向量数组做裸真值判断的代码在 dev 立即炸 = 接缝闭合。
    """

    def __bool__(self):
        if len(self) > 1:
            raise ValueError(
                "The truth value of an array with more than one element is "
                "ambiguous. Use a.any() or a.all()")
        return len(self) == 1


def _install_fake_local_embed():
    """注入 fake local_embed（kb_embed.main 的 lazy import 消费）"""
    fake = SimpleNamespace(
        get_embedder=lambda: None,
        embed_texts=lambda texts, batch_size=64: _FakeVectorArray(
            [0.5] * FAKE_DIM for _ in texts),
        EMBED_DIM=FAKE_DIM,
        MODEL_NAME="fake-model-v1",
    )
    saved = sys.modules.get("local_embed")
    sys.modules["local_embed"] = fake
    return saved


class _KbEmbedEnvBase(unittest.TestCase):
    """tmp KB 目录 + fake local_embed + expanduser HOME 隔离（零真实 ~/.kb / workspace 污染）"""

    def setUp(self):
        import kb_embed
        self.kb_embed = kb_embed
        self.tmp = tempfile.mkdtemp(prefix="kb293_")
        self.kb_dir = os.path.join(self.tmp, "kb")
        self.notes = os.path.join(self.kb_dir, "notes")
        os.makedirs(self.notes)
        self.idx = os.path.join(self.kb_dir, "text_index")
        self.fake_home = os.path.join(self.tmp, "home")
        os.makedirs(self.fake_home)

        self._orig = dict(
            KB_DIR=kb_embed.KB_DIR, INDEX_DIR=kb_embed.INDEX_DIR,
            META_FILE=kb_embed.META_FILE, VECS_FILE=kb_embed.VECS_FILE,
            LOCK_FILE=kb_embed.LOCK_FILE, NOTES_DIR=kb_embed.NOTES_DIR,
            SOURCES_DIR=kb_embed.SOURCES_DIR,
        )
        kb_embed.KB_DIR = self.kb_dir
        kb_embed.INDEX_DIR = self.idx
        kb_embed.META_FILE = os.path.join(self.idx, "meta.json")
        kb_embed.VECS_FILE = os.path.join(self.idx, "vectors.bin")
        kb_embed.LOCK_FILE = os.path.join(self.idx, ".lock")
        kb_embed.NOTES_DIR = self.notes
        kb_embed.SOURCES_DIR = os.path.join(self.kb_dir, "sources")

        # HOME 隔离: scan_kb_files 运行时 expanduser ~/.openclaw/workspace →
        # 重定向到空 fake_home（Mac Mini 上跑测试时防扫真实 workspace .md）
        self._orig_expanduser = os.path.expanduser
        fake_home = self.fake_home

        def _fake_expanduser(p):
            if isinstance(p, str) and p.startswith("~"):
                return fake_home + p[1:].replace("~", "", 1) if False else os.path.join(
                    fake_home, p.lstrip("~/").lstrip("~"))
            return p

        # 简化实现: ~ / ~/xxx → fake_home / fake_home/xxx
        def _fake_expanduser2(p):
            if isinstance(p, str) and p == "~":
                return fake_home
            if isinstance(p, str) and p.startswith("~/"):
                return os.path.join(fake_home, p[2:])
            return p

        os.path.expanduser = _fake_expanduser2

        self._saved_le = _install_fake_local_embed()
        self._argv = sys.argv
        sys.argv = ["kb_embed.py"]

    def tearDown(self):
        os.path.expanduser = self._orig_expanduser
        for k, v in self._orig.items():
            setattr(self.kb_embed, k, v)
        if self._saved_le is None:
            sys.modules.pop("local_embed", None)
        else:
            sys.modules["local_embed"] = self._saved_le
        sys.argv = self._argv
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def _write_note(self, name, sentence="深度学习模型压缩与量化技术在边缘设备部署中的应用综述。"):
        p = os.path.join(self.notes, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(sentence * 12)  # ~300 chars → 1 chunk
        return p

    def _run_main(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.kb_embed.main()
        return out.getvalue()

    def _meta(self):
        with open(self.kb_embed.META_FILE) as f:
            return json.load(f)

    def _vec_size(self):
        p = self.kb_embed.VECS_FILE
        return os.path.getsize(p) if os.path.isfile(p) else 0

    def _assert_aligned(self, meta=None):
        meta = meta or self._meta()
        self.assertEqual(self._vec_size(), len(meta["chunks"]) * FAKE_DIM * 4,
                         "vectors.bin 与 meta.chunks 必须对齐")


class TestGhostPruneBehavioral(_KbEmbedEnvBase):
    """(a) B-F4 幽灵剪除 + 对齐 行为级端到端"""

    def test_ghost_pruned_on_prune_only_night(self):
        """血案回归: 文件被 kb_dedup 删除 + 当晚无新内容 → 幽灵 chunks 仍被剪除且重建对齐"""
        pa = self._write_note("a.md", "第一篇关于向量检索系统语义索引结构设计的长篇技术笔记内容。")
        pb = self._write_note("b.md", "第二篇关于消息推送链路失败队列重放机制的完整实现说明文档。")
        self._run_main()
        m1 = self._meta()
        self.assertEqual(len({c["file"] for c in m1["chunks"]}), 2)
        self._assert_aligned(m1)

        os.remove(pb)  # 模拟 kb_dedup 夜间删除
        out2 = self._run_main()  # 无任何新内容
        self.assertIn("🪦", out2)
        self.assertIn("重建向量文件", out2)
        m2 = self._meta()
        files2 = {c["file"] for c in m2["chunks"]}
        self.assertEqual(files2, {pa}, "幽灵文件的 chunks 必须被剪除")
        self._assert_aligned(m2)

    def test_noop_run_early_returns(self):
        self._write_note("a.md")
        self._run_main()
        st = os.stat(self.kb_embed.META_FILE).st_mtime_ns
        time.sleep(0.02)
        out2 = self._run_main()
        self.assertIn("无新内容需要索引", out2)
        self.assertEqual(os.stat(self.kb_embed.META_FILE).st_mtime_ns, st,
                         "无变更 run 不得重写 meta.json")

    def test_changed_to_empty_file_no_misalignment(self):
        """历史洞回归: 文件变更为 0-chunk + 同轮有其他新内容 → 旧逻辑不重建 = 位移张冠李戴"""
        self._write_note("a.md", "第一篇关于治理审计不变式执行引擎的设计与实现的详细记录。")
        pb = self._write_note("b.md", "第二篇关于多模态记忆索引对齐自愈机制的完整设计文档内容。")
        self._run_main()

        with open(pb, "w", encoding="utf-8") as f:
            f.write("")  # 空文件 → chunk_text 返回 [] (0-chunk 路径; 注意 "太短"
            # 不行 — chunk_text 零内容丢失设计会保留唯一短内容为 1 chunk)
        self._write_note("c.md", "第三篇新增的关于对话精华提炼分段合并管道的技术实现笔记。")
        out2 = self._run_main()
        m2 = self._meta()
        files2 = {c["file"] for c in m2["chunks"]}
        self.assertNotIn(pb, files2, "变空文件的旧 chunks 必须移除")
        self.assertTrue(any(f.endswith("c.md") for f in files2))
        self._assert_aligned(m2)  # 修复前: meta 2 文件 vs vectors 3 行 → 此断言 FAIL
        self.assertIn("重建向量文件", out2)

    def test_orphan_rows_trigger_alignment_rebuild(self):
        """(c) 对齐校验: vectors.bin 孤儿尾行 → 下轮启动检出并权威重建"""
        self._write_note("a.md")
        self._run_main()
        with open(self.kb_embed.VECS_FILE, "ab") as f:
            f.write(struct.pack(f"{FAKE_DIM}f", *([0.9] * FAKE_DIM)))
        out2 = self._run_main()
        self.assertIn("对齐校验失败", out2)
        self._assert_aligned()

    def test_all_files_deleted_truncates_vectors(self):
        pa = self._write_note("a.md")
        self._run_main()
        os.remove(pa)
        out2 = self._run_main()
        self.assertIn("🪦", out2)
        self.assertEqual(len(self._meta()["chunks"]), 0)
        self.assertEqual(self._vec_size(), 0, "全部剪除后向量文件必须截空（保持不变式）")

    def test_chunks_carry_indexed_at(self):
        """(b) 链路根: 索引 chunk 必须带 indexed_at（透传链的源头）"""
        self._write_note("a.md")
        self._run_main()
        for c in self._meta()["chunks"]:
            self.assertTrue(c.get("indexed_at"), "chunk 缺 indexed_at → 整条透传链断")


class TestMemoryPlaneIndexedAt(unittest.TestCase):
    """(b) memory_plane 透传 + FRESHNESS_DECAY 激活"""

    def test_kb_search_metadata_carries_indexed_at(self):
        saved = sys.modules.get("kb_rag")
        try:
            fake = SimpleNamespace(search=lambda query, top_k=5, output_mode="json",
                                   source=None, recent_hours=None: [{
                                       "score": 0.9, "text": "x", "file": "/kb/notes/n.md",
                                       "filename": "n.md", "source_type": "note",
                                       "chunk_idx": 0, "indexed_at": "2026-08-01T03:30:00",
                                   }])
            sys.modules["kb_rag"] = fake
            import memory_plane as mp
            results = mp._kb_search("q")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].metadata.get("indexed_at"), "2026-08-01T03:30:00",
                             "metadata 缺 indexed_at → tool_proxy time_info 恒空 + decay 死代码")
        finally:
            if saved is None:
                sys.modules.pop("kb_rag", None)
            else:
                sys.modules["kb_rag"] = saved

    def test_freshness_decay_now_live(self):
        """decay 是 V36 v2 设计行为, 因字段缺失死代码至今 — 透传后必须真衰减"""
        from datetime import datetime, timedelta
        import memory_plane as mp
        fresh = mp.MemoryResult(layer="kb", score=1.0, text="f", source="note",
                                metadata={"indexed_at": datetime.now().isoformat()})
        old = mp.MemoryResult(layer="kb", score=1.0, text="o", source="note",
                              metadata={"indexed_at": (datetime.now() - timedelta(days=30)).isoformat()})
        mp.apply_confidence([fresh, old])
        self.assertLess(old.score, fresh.score, "30 天旧结果必须被新鲜度衰减")
        self.assertAlmostEqual(old.score, fresh.score * 0.5, places=3,
                               msg="30 天远超 72h 阈值 → 应触底 50% 地板")

    def test_no_indexed_at_no_decay(self):
        """向后兼容: 旧索引 chunk 无 indexed_at → 不衰减不崩溃"""
        import memory_plane as mp
        r = mp.MemoryResult(layer="kb", score=1.0, text="x", source="note", metadata={})
        mp.apply_confidence([r])
        self.assertGreater(r.score, 0)


class TestKbRagGuards(unittest.TestCase):
    """(b)(c) kb_rag 生产端 + 读侧守卫"""

    def setUp(self):
        self.src = _read(KB_RAG_PATH)

    def test_all_result_dicts_include_indexed_at(self):
        """kb_rag 全部 results.append 块（关键词 + 语义）都必须透传 indexed_at。
        血案精确性: 关键词分支 (:159) 一直有, 唯独语义分支 (:213, search_kb 主路径)
        缺 — 首版守卫只匹配第一个 append 块被关键词分支满足 = sabotage S3 漏网,
        改为全块断言（任何新增 append 分支也自动被要求透传）"""
        blocks = re.findall(r"results\.append\(\{(.*?)\}\)", self.src, re.S)
        self.assertGreaterEqual(len(blocks), 2, "kb_rag 应至少有关键词+语义两个结果构造点")
        for i, b in enumerate(blocks):
            self.assertIn("indexed_at", b,
                          f"第 {i + 1} 个 results.append 块不含 indexed_at → "
                          f"该路径 time_info 恒空（血案 b 回归）")

    def test_load_vectors_warns_both_sides(self):
        m = re.search(r"def load_vectors.*?(?=\ndef )", self.src, re.S)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn("短缺", body)
        self.assertIn("超前", body)
        self.assertEqual(body.count("sys.stderr"), 2,
                         "长/短双侧都必须 stderr 打点（旧版长侧静默截断张冠李戴零日志）")

    @unittest.skipUnless(HAS_NUMPY, "numpy 不可用（dev 环境预期跳过, Mac Mini 全跑）")
    def test_load_vectors_behavior(self):
        import kb_rag
        tmp = tempfile.mkdtemp(prefix="rag293_")
        try:
            orig = kb_rag.VECS_FILE
            kb_rag.VECS_FILE = os.path.join(tmp, "vectors.bin")
            dim = 4
            with open(kb_rag.VECS_FILE, "wb") as f:
                for _ in range(3):
                    f.write(struct.pack(f"{dim}f", *([0.1] * dim)))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                v = kb_rag.load_vectors(2, dim)  # 3 行文件读 2 行 → 超前
            self.assertEqual(v.shape, (2, dim))
            self.assertIn("超前", err.getvalue())
            err2 = io.StringIO()
            with contextlib.redirect_stderr(err2):
                v2 = kb_rag.load_vectors(5, dim)  # 3 行文件读 5 行 → 短缺
            self.assertIsNone(v2)
            self.assertIn("短缺", err2.getvalue())
        finally:
            kb_rag.VECS_FILE = orig
            shutil.rmtree(tmp, ignore_errors=True)


class TestConsumerContract(unittest.TestCase):
    """跨文件契约: 消费端早已在读, 生产端三处必须持续喂（MR-8）"""

    def test_tool_proxy_consumers_pinned(self):
        src = _read(TOOL_PROXY_PATH)
        self.assertIn('r.get("indexed_at", "")', src, "subprocess 路径消费端被移除")
        self.assertIn('r.metadata.get("indexed_at", "")', src, "in-process 路径消费端被移除")

    def test_memory_plane_producer_and_decay_reader(self):
        src = _read(MEMORY_PLANE_PATH)
        m = re.search(r"def _kb_search.*?(?=\ndef )", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn('"indexed_at"', m.group(0), "_kb_search metadata 不再透传 indexed_at")
        self.assertIn('metadata.get("indexed_at"', src, "FRESHNESS_DECAY 读端被移除")


class TestV37_9_299_NumpyTruthiness(unittest.TestCase):
    """V37.9.299 血案: `if vectors:` 对 numpy ndarray 抛 ValueError。

    生产每晚跑完 118s embedding 后崩在写入前一行 → 索引静默停更 4 天 (95 笔记
    未索引, PA 语义检索看不到新内容)。dev fake 返回裸 list 真值判断正常 = 经典
    dev-production 接缝 (论文 Class A/B)。修复两层: 代码用 len() + fake 忠实于
    ndarray 真值语义 (后者让既有行为级测试全部成为该 bug 类的探测器)。
    """

    def test_fake_is_ndarray_faithful(self):
        """fake 必须在多元素时 __bool__ 抛 ValueError（守卫的守卫）。

        若后人把 fake 简化回裸 list, 本测试红灯 — 否则整个套件会重新对这个
        bug 类失明 (V37.9.293 当时正是如此)。
        """
        multi = _FakeVectorArray([[0.5] * FAKE_DIM, [0.5] * FAKE_DIM])
        with self.assertRaises(ValueError) as ctx:
            bool(multi)
        self.assertIn("ambiguous", str(ctx.exception))
        self.assertEqual(len(multi), 2, "len() 必须照常可用（修复后的判定方式）")
        self.assertFalse(bool(_FakeVectorArray()), "空数组真值 False（不抛）")

    def test_append_guard_is_len_based(self):
        """append_vectors 的门控必须 len-based，绝不裸真值判断 ndarray"""
        src = _read(KB_EMBED_PATH)
        m = re.search(r"append_vectors\(vectors, dim\)", src)
        self.assertIsNotNone(m, "append_vectors 调用点必须存在")
        block = src[max(0, m.start() - 700):m.start()]
        self.assertRegex(block, r"if len\(vectors\) > 0:",
                         "门控必须 len-based（numpy-safe）")
        self.assertNotRegex(block, r"\n\s+if vectors:\s*\n",
                            "回退到 `if vectors:` = 生产每晚 ValueError 崩溃复现")


class TestKbEmbedSourceGuards(unittest.TestCase):
    """kb_embed 源码级防回退"""

    def setUp(self):
        self.src = _read(KB_EMBED_PATH)

    def test_marker(self):
        self.assertIn("V37.9.293", self.src)

    def test_prune_uses_disk_truth(self):
        self.assertIn("dead_files", self.src)
        self.assertRegex(self.src, r"not os\.path\.isfile\(p\)",
                         "剪除判定必须磁盘直查（不依赖扫描根配置）")
        self.assertIn("🪦", self.src)

    def test_removal_registered_at_action_site(self):
        m = re.search(r"已变更，移除", self.src)
        self.assertIsNotNone(m)
        block = self.src[m.start() - 600:m.start()]
        self.assertIn("removed_files.add(path)", block,
                      "变更移除点必须登记 removed_files（修 0-chunk 洞）")

    def test_early_return_considers_removals(self):
        self.assertRegex(self.src,
                         r"if not batch_texts and not removed_files and not force_rebuild:",
                         "早退条件不含移除/对齐 → 剪除在无新内容夜被静默跳过")

    def test_old_changed_files_logic_retired(self):
        # 只匹配可执行形态, 不匹配退役注释里的名词提及 (V37.9.178 教训: 守卫别被
        # 自己的注释咬)
        self.assertNotIn("changed_files = set()", self.src,
                         "旧 changed_files 反推逻辑必须退役（漏 0-chunk/读失败场景 = 张冠李戴源头）")
        self.assertNotIn("if changed_files:", self.src,
                         "旧 changed_files 触发条件必须退役")
        self.assertNotIn("remaining_old", self.src, "死代码 remaining_old 块必须退役")

    def test_alignment_check_present(self):
        self.assertIn("对齐校验失败", self.src)
        self.assertRegex(self.src, r"len\(meta\[.chunks.\]\) \* dim \* 4")

    def test_rebuild_truncates_on_empty(self):
        m = re.search(r"def _rebuild_vectors.*?(?=\nif __name__|$)", self.src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("if not all_texts:", m.group(0))
        self.assertNotRegex(m.group(0), r"if not all_texts:\s*\n\s+return",
                            "空 chunks 直接 return → 残留旧向量让对齐校验永远告警")


if __name__ == "__main__":
    unittest.main(verbosity=1)

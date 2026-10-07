#!/usr/bin/env python3
"""V37.9.370 — dev 镜像装上 numpy/pdfplumber/bs4（2026-10-03）后，干净 main 上三个套件
变红暴露的三件事（同一家族：测试/代码把「dev 当时缺什么依赖」写死成了假设）：

(1) local_embed 库路径 `print("ERROR: …") + sys.exit(1)`：
    「有 numpy、无 sentence-transformers」时 SystemExit 穿透
    cross_source_signal_aggregator 的 `except ImportError` FAIL-OPEN → 进程 rc=1，
    `--json` 的 stdout 被 "ERROR: …" 行污染（MR-11）。只在 aggregator 接 SystemExit
    反而更糟：rc 变 0 但 stdout 非纯 JSON → kb_dream 内层 json.load 静默失败。
    根治：库只 raise ImportError；CLI 入口 `__main__` 接住走 stderr + rc=1。
(2) memory_plane kb 层「可导入 ≠ 可用」：无 text_index/meta.json 时报 [OK] kb 却零
    字段（_kb_stats 把异常吞成 {}）。根治：_kb_available 镜像 _mm_available 检查 meta。
(3) test_kb_deep_dive 把 "pdfplumber not installed" 写死进断言 → 环境变了走到真网络
    （代理 403）。根治：stub pdfplumber + 假 _urlopen（hermetic）。

本守卫全部 hermetic：不依赖 numpy / sentence-transformers / pdfplumber 是否安装
（需要 kb_rag/local_embed 可 import 的用例按 V37.9.144 惯例 skipUnless(numpy)）。
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.abspath(__file__))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def _stub_dir(tmp):
    """PYTHONPATH 前置一个假 sentence_transformers：任何环境下 import 都抛 ImportError。"""
    stub = os.path.join(tmp, "stub")
    os.makedirs(stub, exist_ok=True)
    with open(os.path.join(stub, "sentence_transformers.py"), "w", encoding="utf-8") as f:
        f.write('raise ImportError("stubbed out by test_v37_9_370")\n')
    env = dict(os.environ)
    env["PYTHONPATH"] = stub + os.pathsep + env.get("PYTHONPATH", "")
    return env


# ---------------------------------------------------------------------------
# (1) local_embed 库路径：raise 不 exit、stdout 干净
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_NUMPY, "local_embed 顶层 import numpy")
class TestLocalEmbedLibraryRaises(unittest.TestCase):
    def test_get_embedder_raises_import_error_with_clean_stdout(self):
        import local_embed
        buf = io.StringIO()
        with patch.dict(sys.modules, {"sentence_transformers": None}), \
             patch.object(local_embed, "_model", None), \
             contextlib.redirect_stdout(buf):
            with self.assertRaises(ImportError) as cm:
                local_embed.get_embedder()
        self.assertEqual(buf.getvalue(), "", "库路径不得向 stdout 打印 (MR-11)")
        self.assertIn("sentence-transformers", str(cm.exception))

    def test_embed_texts_propagates_import_error_not_system_exit(self):
        import local_embed
        with patch.dict(sys.modules, {"sentence_transformers": None}), \
             patch.object(local_embed, "_model", None):
            # 血案形态：此前这里抛的是 SystemExit（不是 Exception 子类）
            with self.assertRaises(ImportError):
                local_embed.embed_texts(["x"])

    def test_cli_entry_reports_on_stderr_with_rc1(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _stub_dir(tmp)
            proc = subprocess.run([sys.executable, os.path.join(REPO, "local_embed.py"), "--bench"],
                                  capture_output=True, text=True, env=env, timeout=120, cwd=tmp)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR:", proc.stderr)
        self.assertIn("sentence-transformers", proc.stderr)
        self.assertNotIn("ERROR", proc.stdout, "友好提示只走 stderr")
        self.assertNotIn("Traceback", proc.stderr, "CLI 入口须接住 ImportError 给友好提示")


# ---------------------------------------------------------------------------
# (1') 消费方契约：aggregator --json 在部分依赖缺失时仍是纯 JSON + rc=0
# ---------------------------------------------------------------------------
class TestAggregatorFailOpenWithPartialDeps(unittest.TestCase):
    def test_json_contract_holds_when_sentence_transformers_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _stub_dir(tmp)
            kb = os.path.join(tmp, "kb")
            notes = os.path.join(kb, "notes")
            os.makedirs(notes)
            with open(os.path.join(notes, "20260813120000_v370.md"), "w", encoding="utf-8") as f:
                f.write("---\ndate: 2026-08-13\ntags: [t]\nsource: arxiv\n---\n# v370 probe\nbody\n")
            out_dir = os.path.join(kb, "radar")
            proc = subprocess.run(
                [sys.executable, os.path.join(REPO, "cross_source_signal_aggregator.py"),
                 "--date", "20260813", "--json", "--kb-dir", kb, "--output-dir", out_dir],
                capture_output=True, text=True, env=env, timeout=120, cwd=tmp)
            # 血案回归：此前 rc=1 且 stdout == "ERROR: 请安装 sentence-transformers…"
            self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
            self.assertNotIn("ERROR", proc.stdout, "stdout 必须是纯 JSON (MR-11)")
            data = json.loads(proc.stdout)
            self.assertEqual(data["status"], "fail_open_no_deps")
            self.assertTrue(os.path.isfile(data["output_path"]), "FAIL-OPEN 须写空 signals 文件")
            self.assertIn("FAIL-OPEN", proc.stderr)

    def test_run_catches_import_error_at_embed_stage(self):
        src = _strip_comments(_read("cross_source_signal_aggregator.py"))
        body = src[src.index("def run("):]
        self.assertGreaterEqual(body.count("except ImportError"), 2,
                                "run() 的 embed/cluster 两段 FAIL-OPEN 须保留")
        self.assertIn('"fail_open_no_deps"', body)


# ---------------------------------------------------------------------------
# (2) memory_plane kb 层：可导入 ≠ 可用
# ---------------------------------------------------------------------------
class TestMemoryPlaneKbAvailabilityHonest(unittest.TestCase):
    def test_unavailable_when_index_meta_missing(self):
        import memory_plane
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "text_index", "meta.json")
            if HAS_NUMPY:
                import kb_rag
                ctx = patch.object(kb_rag, "META_FILE", missing)
            else:
                ctx = contextlib.nullcontext()
            with ctx:
                avail, reason = memory_plane._kb_available()
        self.assertFalse(avail)
        self.assertTrue(reason, "不可用必须带 reason")
        if HAS_NUMPY:
            self.assertIn("meta.json", reason)

    @unittest.skipUnless(HAS_NUMPY, "kb_rag 顶层 import numpy")
    def test_available_when_index_meta_exists(self):
        import memory_plane, kb_rag
        with tempfile.TemporaryDirectory() as tmp:
            meta = os.path.join(tmp, "text_index", "meta.json")
            os.makedirs(os.path.dirname(meta))
            with open(meta, "w", encoding="utf-8") as f:
                f.write("{}")
            with patch.object(kb_rag, "META_FILE", meta):
                avail, reason = memory_plane._kb_available()
        self.assertTrue(avail, reason)

    @unittest.skipUnless(HAS_NUMPY, "kb_rag 顶层 import numpy")
    def test_stats_never_says_ok_with_zero_fields(self):
        """血案回归：此前 dev 有 numpy 无索引 → stats()['kb'] == {'available': True}。"""
        import memory_plane, kb_rag
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(kb_rag, "META_FILE", os.path.join(tmp, "nope", "meta.json")):
                kb = memory_plane.stats()["kb"]
        self.assertFalse(kb["available"])
        self.assertIn("reason", kb)
        self.assertNotEqual(kb, {"available": True})

    def test_kb_and_mm_use_same_availability_judgement(self):
        """一物一形：两层都按「索引 meta 文件存在」判可用。"""
        src = _strip_comments(_read("memory_plane.py"))
        kb = src[src.index("def _kb_available"):src.index("def _kb_search")]
        mm = src[src.index("def _mm_available"):src.index("def _mm_search")]
        self.assertIn("META_FILE", kb)
        self.assertIn("os.path.exists", kb)
        self.assertIn("meta.json not found", kb)
        self.assertIn("os.path.exists", mm)
        self.assertIn("meta.json not found", mm)


# ---------------------------------------------------------------------------
# 源码守卫（剥注释行，V37.9.178 家族）
# ---------------------------------------------------------------------------
class TestSourceGuards(unittest.TestCase):
    def test_local_embed_library_path_has_no_exit_or_print(self):
        src = _strip_comments(_read("local_embed.py"))
        body = src[src.index("def get_embedder"):src.index("def embed_texts")]
        self.assertNotIn("sys.exit", body)
        self.assertNotIn("print(", body)
        self.assertIn("raise ImportError", body)

    def test_local_embed_cli_entry_catches_import_error(self):
        src = _strip_comments(_read("local_embed.py"))
        tail = src[src.index('if __name__ == "__main__"'):]
        self.assertIn("except ImportError", tail)
        self.assertIn("file=sys.stderr", tail)
        self.assertIn("sys.exit(1)", tail)

    def test_kb_embed_preload_keeps_error_line_for_watchdog(self):
        src = _strip_comments(_read("kb_embed.py"))
        self.assertRegex(src, r"try:\s*\n\s*get_embedder\(\)\s*\n\s*except ImportError as e:\s*\n\s*log\(f\"ERROR: \{e\}\"\)")

    def test_kb_deep_dive_test_no_longer_pins_dev_env(self):
        src = _read("test_kb_deep_dive.py")
        self.assertNotIn('assertIn("pdfplumber not installed"', src)
        self.assertIn('patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber})', src)

    def test_marker(self):
        for name in ("local_embed.py", "kb_embed.py", "memory_plane.py", "test_kb_deep_dive.py"):
            self.assertIn("V37.9.370", _read(name), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)

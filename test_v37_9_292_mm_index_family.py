#!/usr/bin/env python3
"""test_v37_9_292_mm_index_family.py — mm_index 静默失效家族守卫（V37.9.292，对抗审计 B-F1）

unfinished [31] 登记的四机制（MED-HIGH，多模态记忆层可 100% 静默失效无界时长）：
  (a) mm_index_cron.sh set -e 让 RC=$? 与 FAILED 分支成死代码
      → RC=0 + `|| RC=$?` 捕获（V37.9.274/282 PARSE_RC 同款）+ exit "$RC"
  (b) save_meta 无条件执行 → meta.json mtime 每 2h 假刷新，任何新鲜度判断结构失效
      → 条件写（镜像 kb_embed 无新内容早退不刷 meta）
  (c) file_hash 在 try 外（TOCTOU：scan 与 hash 之间文件被删）→ 整轮崩溃 →
      已 append 的向量成孤儿 → vectors.bin 超前 meta 永久错位 → mm_search reshape
      ValueError 被 memory_plane 吞成 [] 永不自愈（仅 --reindex 能修）
      → hash 入 try + 启动时对齐校验自愈（截到一致前缀）
  (d) run 级失败不匹配 watchdog err_pattern 且 mm_index.log 根本不在 HOME_LOGS
      错误扫描名单（只有 LOG_FRESHNESS 4h mtime——cron 每 2h 写 start 让它永远满足）
      → FAILED:/ERROR: 格式对齐 err_pattern + mm_index.log 加入 HOME_LOGS；
      per-file ❌/⚠️ 刻意保持不匹配（FAIL-OPEN 惯例，poison 文件不产生每小时噪声）

行为级测试用 fake google-genai 模块（dev 无 google-genai/numpy 也可跑全部用例）。
"""

import contextlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import mm_index  # noqa: E402

CRON_PATH = os.path.join(REPO, "mm_index_cron.sh")
WATCHDOG_PATH = os.path.join(REPO, "job_watchdog.sh")
MM_INDEX_PATH = os.path.join(REPO, "mm_index.py")
MEMORY_PLANE_PATH = os.path.join(REPO, "memory_plane.py")

ROW_BYTES = mm_index.EMBED_DIM * 4


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_err_pattern():
    """从 job_watchdog.sh 提取 err_pattern 字面量（MR-8 跨文件契约：watchdog 改格式本测试先 fail）"""
    src = _read(WATCHDOG_PATH)
    m = re.search(r"local err_pattern='([^']+)'", src)
    if not m:
        raise AssertionError("job_watchdog.sh err_pattern 提取失败（格式变更？）")
    return m.group(1)


class _FakeModels:
    """fake genai client.models — 内容含 FAILME 的文件模拟 embed 失败（API/配额类）"""

    def embed_content(self, model, contents, config):
        data = contents[0]
        if isinstance(data, (bytes, bytearray)) and b"FAILME" in data:
            raise RuntimeError("simulated embed failure (quota exceeded)")
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * mm_index.EMBED_DIM)])


class _FakeClient:
    def __init__(self, api_key=None):
        self.models = _FakeModels()


def _install_fake_genai():
    """向 sys.modules 注入 fake google/google.genai（main 内部 from google import genai）"""
    fake_types = SimpleNamespace(
        Part=SimpleNamespace(from_bytes=staticmethod(lambda data, mime_type: data)),
        EmbedContentConfig=lambda **kw: kw,
    )
    fake_genai = SimpleNamespace(Client=_FakeClient, types=fake_types)
    saved = {k: sys.modules.get(k) for k in ("google", "google.genai")}
    sys.modules["google"] = SimpleNamespace(genai=fake_genai)
    sys.modules["google.genai"] = fake_genai
    return saved


def _restore_modules(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


class _MmEnvBase(unittest.TestCase):
    """公共环境：tmp 媒体目录 + tmp 索引目录 + fake genai + argv/env 隔离（不碰真实 ~/.kb）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mm292_")
        self.media = os.path.join(self.tmp, "media")
        os.makedirs(self.media)
        self.idx = os.path.join(self.tmp, "mm_index")
        self._orig = dict(
            MEDIA_DIRS=mm_index.MEDIA_DIRS,
            INDEX_DIR=mm_index.INDEX_DIR,
            META_FILE=mm_index.META_FILE,
            VECS_FILE=mm_index.VECS_FILE,
            BATCH_PAUSE=mm_index.BATCH_PAUSE,
        )
        mm_index.MEDIA_DIRS = [self.media]
        mm_index.INDEX_DIR = self.idx
        mm_index.META_FILE = os.path.join(self.idx, "meta.json")
        mm_index.VECS_FILE = os.path.join(self.idx, "vectors.bin")
        mm_index.BATCH_PAUSE = 0
        self._env_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key-not-real"
        self._argv = sys.argv
        sys.argv = ["mm_index.py"]
        self._saved_mods = _install_fake_genai()
        self._orig_hash = mm_index.file_hash

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(mm_index, k, v)
        mm_index.file_hash = self._orig_hash
        if self._env_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._env_key
        sys.argv = self._argv
        _restore_modules(self._saved_mods)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def _write_media(self, name, data=b"\x89PNG fakebytes"):
        p = os.path.join(self.media, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def _run_main(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mm_index.main()
        return out.getvalue()

    def _meta(self):
        with open(mm_index.META_FILE) as f:
            return json.load(f)

    def _vec_size(self):
        return os.path.getsize(mm_index.VECS_FILE) if os.path.isfile(mm_index.VECS_FILE) else 0


class TestMainBehavioral(_MmEnvBase):
    """(b)(c)(d) 行为级端到端（fake genai，零真实 API/零真实 ~/.kb）"""

    def test_first_run_indexes_and_aligned(self):
        self._write_media("a.png")
        out = self._run_main()
        self.assertEqual(len(self._meta()["entries"]), 1)
        self.assertEqual(self._vec_size(), ROW_BYTES)
        self.assertIn("新增 1", out)

    def test_noop_run_does_not_rewrite_meta(self):
        """血案 (b) 回归：无变更 run 不得刷 meta.json mtime（此前每 2h 无条件重写）"""
        self._write_media("a.png")
        self._run_main()
        st_before = os.stat(mm_index.META_FILE).st_mtime_ns
        import time as _t
        _t.sleep(0.02)
        out = self._run_main()
        st_after = os.stat(mm_index.META_FILE).st_mtime_ns
        self.assertEqual(st_before, st_after, "无变更 run 重写了 meta.json（血案 b 回归）")
        self.assertIn("无变更", out)

    def test_toctou_vanished_file_does_not_crash_run(self):
        """血案 (c) 回归：scan 与 hash 之间文件被删 → 整轮不得崩溃，其余文件正常索引"""
        self._write_media("ok.png")
        gone = self._write_media("gone.png")
        orig = self._orig_hash

        def flaky(path):
            if path.endswith("gone.png"):
                raise FileNotFoundError(2, "No such file or directory", path)
            return orig(path)

        mm_index.file_hash = flaky
        out = self._run_main()  # 修复前: FileNotFoundError 直接炸出 main
        self.assertIn("读取失败", out)
        entries = self._meta()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["path"].endswith("ok.png"))
        self.assertEqual(self._vec_size(), ROW_BYTES, "vectors 与 meta 必须对齐")
        _ = gone  # silence lint

    def test_vanished_only_is_not_all_fail(self):
        """消失文件（本地 OSError）不算系统性失败 → 不得 exit 2 触发告警（良性竞态零噪声）"""
        self._write_media("gone.png")

        def always_gone(path):
            raise FileNotFoundError(2, "gone", path)

        mm_index.file_hash = always_gone
        out = self._run_main()  # 不应 SystemExit
        self.assertNotIn("ERROR: 全部", out)

    def test_all_embed_fail_exits_2_with_error_line(self):
        """(d) 全部新文件 embed 失败 = run 级系统性故障（配额/模型退役）→ ERROR: + exit 2"""
        self._write_media("bad.png", data=b"FAILME bytes")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                mm_index.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("ERROR: 全部", out.getvalue())

    def test_partial_fail_stays_fail_open(self):
        """per-file FAIL-OPEN 惯例：部分失败不 exit，失败文件 ❌ 记录，成功文件正常入索引"""
        self._write_media("bad.png", data=b"FAILME bytes")
        self._write_media("ok.png")
        out = self._run_main()  # 不应 SystemExit
        self.assertIn("❌", out)
        self.assertEqual(len(self._meta()["entries"]), 1)
        self.assertEqual(self._vec_size(), ROW_BYTES)

    def test_heal_ahead_end_to_end(self):
        """血案 (c) 自愈端到端：vectors.bin 超前（孤儿尾）→ 下一轮启动即截断恢复"""
        self._write_media("a.png")
        self._run_main()
        with open(mm_index.VECS_FILE, "ab") as f:
            f.write(struct.pack(f"{mm_index.EMBED_DIM}f", *([0.1] * mm_index.EMBED_DIM)))
        self.assertEqual(self._vec_size(), 2 * ROW_BYTES)
        out = self._run_main()
        self.assertIn("对齐自愈", out)
        self.assertEqual(self._vec_size(), ROW_BYTES)
        self.assertEqual(len(self._meta()["entries"]), 1)

    def test_heal_short_reindexes_dropped_tail(self):
        """自愈端到端：vectors.bin 短缺 → meta 截到向量数，尾部文件本轮重新索引收敛"""
        self._write_media("a.png", data=b"AAA")
        self._run_main()
        self._write_media("b.png", data=b"BBB")
        self._run_main()
        self.assertEqual(len(self._meta()["entries"]), 2)
        with open(mm_index.VECS_FILE, "r+b") as f:
            f.truncate(ROW_BYTES)  # 丢掉第二行向量
        out = self._run_main()
        self.assertIn("对齐自愈", out)
        self.assertEqual(len(self._meta()["entries"]), 2, "被丢弃的尾部文件应本轮重新索引")
        self.assertEqual(self._vec_size(), 2 * ROW_BYTES)


class TestVerifyAlignmentUnit(_MmEnvBase):
    """verify_alignment 纯单元分支覆盖"""

    def _mk_meta(self, n):
        return {"version": 1, "dim": mm_index.EMBED_DIM,
                "entries": [{"hash": f"h{i}", "path": f"/x/{i}.png"} for i in range(n)]}

    def _mk_vecs(self, rows, extra_bytes=0):
        os.makedirs(self.idx, exist_ok=True)
        with open(mm_index.VECS_FILE, "wb") as f:
            for _ in range(rows):
                f.write(struct.pack(f"{mm_index.EMBED_DIM}f", *([0.2] * mm_index.EMBED_DIM)))
            if extra_bytes:
                f.write(b"\x00" * extra_bytes)

    def test_aligned_noop(self):
        meta = self._mk_meta(2)
        self._mk_vecs(2)
        self.assertFalse(mm_index.verify_alignment(meta))
        self.assertEqual(len(meta["entries"]), 2)

    def test_empty_both_noop(self):
        meta = self._mk_meta(0)
        self.assertFalse(mm_index.verify_alignment(meta))

    def test_vectors_missing_but_meta_has_entries(self):
        meta = self._mk_meta(3)
        with contextlib.redirect_stdout(io.StringIO()):
            changed = mm_index.verify_alignment(meta)
        self.assertTrue(changed)
        self.assertEqual(meta["entries"], [])

    def test_vectors_ahead_truncated_meta_untouched(self):
        meta = self._mk_meta(1)
        self._mk_vecs(3)
        with contextlib.redirect_stdout(io.StringIO()):
            changed = mm_index.verify_alignment(meta)
        self.assertFalse(changed, "超前场景只截 vectors，meta 不变")
        self.assertEqual(self._vec_size(), ROW_BYTES)
        self.assertEqual(len(meta["entries"]), 1)

    def test_torn_write_truncates_to_row_boundary(self):
        meta = self._mk_meta(2)
        self._mk_vecs(1, extra_bytes=13)  # 撕裂写：1 整行 + 13 残字节
        with contextlib.redirect_stdout(io.StringIO()):
            changed = mm_index.verify_alignment(meta)
        self.assertTrue(changed)
        self.assertEqual(len(meta["entries"]), 1)
        self.assertEqual(self._vec_size(), ROW_BYTES)


class TestCronRcCapture(unittest.TestCase):
    """(a) mm_index_cron.sh：set -e 下 FAILED 分支必须可达"""

    def setUp(self):
        self.src = _read(CRON_PATH)

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", CRON_PATH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rc_captured_with_or_pattern(self):
        """调用行必须 `|| RC=$?`（V37.9.274/282 PARSE_RC 同款），否则 set -e 直接杀脚本"""
        m = re.search(r'^\$MM_PYTHON "\$SCRIPT_DIR/mm_index\.py".*$', self.src, re.M)
        self.assertIsNotNone(m, "mm_index.py 调用行缺失")
        self.assertIn("|| RC=$?", m.group(0), "调用行未捕获 RC → set -e 让 FAILED 分支死代码（血案 a）")

    def test_rc_initialized(self):
        self.assertRegex(self.src, r"(?m)^RC=0$", "RC 必须先初始化（|| 分支不执行时 RC 未定义）")

    def test_failed_line_has_colon(self):
        """FAILED: 带冒号才匹配 watchdog err_pattern 的 FAIL(ED)?:

        行锚定跳过注释行（V37.9.178/285 教训：守卫不能被自己的注释咬）。
        """
        code_lines = [l for l in self.src.splitlines() if not l.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        self.assertIn("FAILED: rc=", code)
        self.assertNotIn("FAILED (rc=", code, "旧无冒号格式不匹配 err_pattern，必须退役")

    def test_exits_with_rc(self):
        self.assertRegex(self.src, r'(?m)^exit "\$RC"$', "cron 脚本须透传 mm_index.py 退出码")

    def test_v37_9_292_marker(self):
        self.assertIn("V37.9.292", self.src)


class TestWatchdogScanContract(unittest.TestCase):
    """(d) 跨文件契约：producer 日志格式 ↔ watchdog err_pattern（MR-8，镜像 V37.9.240）"""

    def setUp(self):
        self.pattern = _extract_err_pattern()

    def _matches(self, line):
        return re.search(self.pattern, line, re.IGNORECASE) is not None

    def test_cron_failed_line_matches(self):
        self.assertTrue(self._matches("[2026-08-10 12:00:00] === mm_index FAILED: rc=2 ==="))

    def test_all_fail_error_line_matches(self):
        self.assertTrue(self._matches(
            "[2026-08-10 12:00:00] mm_index: ERROR: 全部 3 个待索引文件失败 (API/配额/模型退役?), 索引未推进"))

    def test_per_file_error_stays_quiet(self):
        """per-file ❌ 刻意不匹配：poison 文件每 2h 重试不该每小时告警（FAIL-OPEN 惯例）"""
        self.assertFalse(self._matches("[2026-08-10 12:00:00] mm_index:   ❌ cat.png: quota exceeded"))

    def test_vanished_file_line_stays_quiet(self):
        self.assertFalse(self._matches(
            "[2026-08-10 12:00:00] mm_index:   ⚠️ cat.png: 读取失败(可能已被删除): [Errno 2] gone"))

    def test_self_heal_line_stays_quiet(self):
        """自愈是已恢复态，不 actionable，不该告警"""
        self.assertFalse(self._matches(
            "[2026-08-10 12:00:00] mm_index: ⚠️ 对齐自愈: vectors.bin 超前 meta (6144B > 3072B), 已截断孤儿向量"))

    def test_old_dead_format_would_not_match(self):
        """旧死代码格式即使可达也不匹配（双重失效的历史证据，防回退）"""
        self.assertFalse(self._matches("[2026-08-10 12:00:00] === mm_index FAILED (rc=2) ==="))

    def test_mm_index_log_in_home_logs(self):
        """mm_index.log 必须在 HOME_LOGS 错误扫描名单（此前只有 LOG_FRESHNESS mtime = 盲区核心）"""
        src = _read(WATCHDOG_PATH)
        m = re.search(r"HOME_LOGS=\((.*?)\n\)", src, re.S)
        self.assertIsNotNone(m, "HOME_LOGS 数组提取失败")
        self.assertIn("mm_index.log|mm_index", m.group(1),
                      "mm_index.log 不在 HOME_LOGS → run 级失败零可见（血案 d 回归）")


class TestMemoryPlaneMmStderr(unittest.TestCase):
    """(c) 消费端：mm 层故障吞成 [] 必须留 stderr 痕迹（镜像 V37.9.288 _kb_search B-F3）"""

    def test_mm_search_failure_prints_stderr(self):
        saved = {k: sys.modules.get(k) for k in ("google", "google.genai", "mm_search", "memory_plane")}
        try:
            fake_mm = SimpleNamespace(
                search=None,
                load_meta=lambda: (_ for _ in ()).throw(ValueError("simulated corrupt meta")),
                load_vectors=lambda n: None,
                embed_text=lambda c, q: [0.0],
                cosine_similarity=lambda a, b: [],
            )
            sys.modules["mm_search"] = fake_mm
            fake_genai = SimpleNamespace(Client=_FakeClient)
            sys.modules["google"] = SimpleNamespace(genai=fake_genai)
            sys.modules["google.genai"] = fake_genai
            sys.modules.pop("memory_plane", None)
            env_saved = os.environ.get("GEMINI_API_KEY")
            os.environ["GEMINI_API_KEY"] = "test-key-not-real"
            try:
                import memory_plane as mp
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    result = mp._mm_search("query")
                self.assertEqual(result, [])
                self.assertIn("multimodal layer search failed", err.getvalue(),
                              "mm 层故障必须 stderr 打点（此前完全静默 = 层坏了与没结果同形）")
            finally:
                if env_saved is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = env_saved
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_mm_search_catches_system_exit(self):
        """mm_search 库路径含 sys.exit(1)（:110-127）→ 第二道防线须收编 SystemExit（B-F3 同款）"""
        src = _read(MEMORY_PLANE_PATH)
        m = re.search(r"def _mm_search.*?\n(def |\Z)", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("except (Exception, SystemExit)", m.group(0))


class TestSourceGuards(unittest.TestCase):
    """mm_index.py 源码级守卫（防回退）"""

    def setUp(self):
        self.src = _read(MM_INDEX_PATH)

    def test_marker(self):
        self.assertIn("V37.9.292", self.src)

    def test_hash_inside_try(self):
        """血案 (c)：fhash = file_hash(path) 必须在 try 内（TOCTOU 防御）"""
        self.assertRegex(self.src, r"try:\s*\n\s+fhash = file_hash\(path\)",
                         "file_hash 回到 try 外 → TOCTOU 崩溃 → 永久错位（血案 c 回归）")

    def test_conditional_save_meta(self):
        """血案 (b)：save_meta 必须由 meta_dirty 守卫，不得无条件"""
        self.assertRegex(self.src, r"if meta_dirty:\s*\n\s+save_meta\(meta\)")
        body = self.src.split("def main", 1)[1]
        bare = re.findall(r"^    save_meta\(meta\)", body, re.M)
        self.assertEqual(len(bare), 0, "main() 顶层出现无条件 save_meta(meta)（血案 b 回归）")

    def test_scan_getsize_guarded(self):
        """scan_media_files 内 getsize 必须走 _try_size（同族 TOCTOU）"""
        self.assertIn("def _try_size", self.src)
        m = re.search(r"def scan_media_files.*?(?=\ndef )", self.src, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("os.path.getsize", m.group(0),
                         "scan_media_files 内裸 getsize → listdir 与 getsize 之间文件被删即崩溃")

    def test_verify_alignment_exists_and_called(self):
        self.assertIn("def verify_alignment", self.src)
        self.assertRegex(self.src, r"heal_modified = verify_alignment\(meta\)")

    def test_all_fail_exit_2(self):
        self.assertRegex(self.src, r"error_count > 0 and new_count == 0")
        m = re.search(r"error_count > 0 and new_count == 0.*?sys\.exit\(2\)", self.src, re.S)
        self.assertIsNotNone(m, "全失败必须 exit 2（run 级 fail-loud）")


if __name__ == "__main__":
    unittest.main(verbosity=1)

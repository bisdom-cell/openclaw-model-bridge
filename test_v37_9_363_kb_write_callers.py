#!/usr/bin/env python3
"""V37.9.363 kb_write.sh 调用契约守卫。

kb_write.sh 只收三个**位置参数**：内容 / 标签 / 类型。它从不解析
`--title` `--tags` 这类选项，也不读 stdin。finance_news（V37.8.2 起）与
chaspark（V37.8.x 起）却按选项写法 + 管道传正文调用它：

    echo "$LLM_CONTENT" | bash "$KB_WRITE_SCRIPT" --title "财经简报 ${DAY}" --tags "..."

于是 `$1="--title"` 成了正文、`$2="财经简报 <日期>"` 成了标签、`$3="--tags"`
成了类型，管道里的真实内容被丢弃。每天各产出一条正文为 "--title" 的
垃圾笔记，外加一个以「标签第一段」命名的 `topics/财经简报 <日期>.md`
垃圾文件。生产 2026-09-28 实测累计 327 条笔记 + 287 个 topics 文件，
这些笔记一直在进入语义索引、周回顾和 Dream 取样；真实内容因为另有
sources 归档才没丢。

之所以数月无人发现：kb_write.sh 对误用**静默成功**（打印「已记录到知识库」
并 rc=0），调用方又都带 `2>/dev/null` 或不看返回值。修复两层：

  1. 两个调用点改为兄弟 job 的位置参数写法（一物一形）；
  2. kb_write.sh 遇到 `--xxx` 形态的参数直接报错 rc=2，不再写入
     （PA 也会调用它，测试扫描覆盖不到 LLM 的调用）。

本守卫的调用点清单**从源码发现**，不写死脚本名单：未来新增的调用点
自动进入检查。
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
KB_WRITE = os.path.join(ROOT, "kb_write.sh")
FINANCE = os.path.join(ROOT, "jobs", "finance_news", "run_finance_news.sh")
CHASPARK = os.path.join(ROOT, "jobs", "chaspark", "run_chaspark.sh")

# 2026-09-28 之前生产里真实存在的两条调用，逐字保留作血案 fixture。
BLOOD_FINANCE = ('echo "$LLM_CONTENT" | bash "$KB_WRITE_SCRIPT" '
                 '--title "财经简报 ${DAY}" --tags "finance,policy,daily" '
                 '--source "finance_news"')
BLOOD_CHASPARK = ('echo "$KB_CONTENT" | bash "$KB_WRITE_SCRIPT" '
                  '--title "茶思屋深度分析 $DAY" --tags "chaspark,华为,科技前沿"')

_INVOKE_RE = re.compile(
    r'(?:\$\{?KB_WRITE_SCRIPT\}?|kb_write\.sh)"?(?P<rest>.*)$')
_OPTION_RE = re.compile(r'(?:^|\s)--[A-Za-z]')
_PIPED_RE = re.compile(
    r'\|\s*bash\s+\S*(?:KB_WRITE_SCRIPT|kb_write\.sh)')


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _code_lines(text):
    """去掉整行注释；保留行号。"""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        out.append((i, line))
    return out


def _runtime_files():
    """仓库内会被运行的 .sh/.py（排除测试、文档、examples）。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = rel.split(os.sep)
        if parts[0] in (".git", "docs", "examples", "node_modules") or \
                any(p.startswith(".") and p != "." for p in parts):
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith((".sh", ".py")):
                continue
            if fn.startswith("test_") or "tests" in parts:
                continue
            found.append(os.path.join(dirpath, fn))
    return found


def _shell_call_sites():
    """返回 [(path, lineno, line)]：所有 shell 形态的 kb_write 调用行。"""
    sites = []
    for path in _runtime_files():
        if not path.endswith(".sh"):
            continue
        if os.path.abspath(path) == KB_WRITE:
            continue
        for lineno, line in _code_lines(_read(path)):
            if re.search(r'\bbash\s+\S*(?:KB_WRITE_SCRIPT|kb_write\.sh)', line):
                sites.append((path, lineno, line))
    return sites


def _run_kb_write(kb_base, args, stdin_text=""):
    env = dict(os.environ, KB_BASE=kb_base, HOME=kb_base)
    return subprocess.run(["bash", KB_WRITE] + list(args), input=stdin_text,
                          capture_output=True, text=True, env=env, timeout=30)


def _notes(kb_base):
    d = os.path.join(kb_base, "notes")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def _topics(kb_base):
    d = os.path.join(kb_base, "topics")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def _extract_block(path, opener):
    """从真源码抽出以 opener 开头、到第一个列 0 `fi` 为止的块。"""
    text = _read(path)
    start = text.index(opener)
    end = text.index("\nfi\n", start) + len("\nfi\n")
    return text[start:end]


class _TmpKb(unittest.TestCase):
    def setUp(self):
        self.kb = tempfile.mkdtemp(prefix="kbw363_")

    def tearDown(self):
        shutil.rmtree(self.kb, ignore_errors=True)


class TestKbWriteRejectsOptions(_TmpKb):
    """kb_write.sh 自身：选项写法必须响亮失败，且不留下任何写入。"""

    def test_blood_invocation_rejected_and_writes_nothing(self):
        r = _run_kb_write(self.kb, ["--title", "财经简报 2026-09-28",
                                    "--tags", "finance,policy,daily",
                                    "--source", "finance_news"],
                          stdin_text="美联储维持利率不变\n")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ERROR", r.stderr)
        self.assertIn("--title", r.stderr)
        self.assertEqual(_notes(self.kb), [])
        self.assertEqual(_topics(self.kb), [])
        self.assertFalse(os.path.exists(os.path.join(self.kb, "index.json")))

    def test_option_in_tag_or_type_slot_rejected(self):
        for args in (["正文", "--tags", "x"], ["正文", "tag", "--type"]):
            with self.subTest(args=args):
                r = _run_kb_write(self.kb, args)
                self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(_notes(self.kb), [])

    def test_rejection_leaves_no_lock_behind(self):
        _run_kb_write(self.kb, ["--title", "x"])
        self.assertFalse(os.path.exists(os.path.join(self.kb, ".write.lockdir")))

    def test_positional_call_writes_real_content(self):
        r = _run_kb_write(self.kb, ["# 财经简报 2026-09-28\n\n美联储维持利率不变",
                                    "finance-news", "note"])
        self.assertEqual(r.returncode, 0, r.stderr)
        notes = _notes(self.kb)
        self.assertEqual(len(notes), 1)
        body = _read(os.path.join(self.kb, "notes", notes[0]))
        self.assertIn("美联储维持利率不变", body)
        self.assertIn("tags: [finance-news]", body)
        self.assertIn("type: note", body)
        self.assertEqual(_topics(self.kb), ["finance-news.md"])

    def test_no_false_positive_on_dash_leading_content(self):
        for content in ("--- 分隔线开头的正文", "-- 单个破折号", "- 列表项"):
            with self.subTest(content=content):
                r = _run_kb_write(self.kb, [content, "t", "note"])
                self.assertEqual(r.returncode, 0, r.stderr)


class TestCallSitesArePositional(unittest.TestCase):
    """全部调用点从源码发现：不得出现选项参数，不得靠管道传正文。"""

    @classmethod
    def setUpClass(cls):
        cls.sites = _shell_call_sites()

    def test_discovery_not_vacuous(self):
        self.assertGreaterEqual(len(self.sites), 15, self.sites)
        names = {os.path.basename(p) for p, _, _ in self.sites}
        for must in ("run_finance_news.sh", "run_chaspark.sh",
                     "run_arxiv.sh", "run_rss_blogs.sh", "kb_inject.sh"):
            self.assertIn(must, names)

    def test_no_option_arguments(self):
        bad = []
        for path, lineno, line in self.sites:
            m = _INVOKE_RE.search(line)
            self.assertIsNotNone(m, line)
            if _OPTION_RE.search(m.group("rest")):
                bad.append(f"{os.path.relpath(path, ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual(bad, [], "kb_write.sh 只收位置参数:\n" + "\n".join(bad))

    def test_no_content_piped_via_stdin(self):
        bad = [f"{os.path.relpath(p, ROOT)}:{n}: {l.strip()}"
               for p, n, l in self.sites if _PIPED_RE.search(l)]
        self.assertEqual(bad, [], "kb_write.sh 不读 stdin:\n" + "\n".join(bad))

    def test_detectors_catch_blood_forms(self):
        """反向证据：两条血案调用逐字放回，判据必须抓住（防守卫空转）。"""
        for blood in (BLOOD_FINANCE, BLOOD_CHASPARK):
            with self.subTest(blood=blood[:40]):
                m = _INVOKE_RE.search(blood)
                self.assertIsNotNone(m)
                self.assertTrue(_OPTION_RE.search(m.group("rest")))
                self.assertTrue(_PIPED_RE.search(blood))

    def test_python_callers_pass_three_positionals(self):
        text = _read(os.path.join(ROOT, "kb_harvest_chat.py"))
        m = re.search(r'\["bash", KB_WRITE_SCRIPT, (?P<args>[^\]]+)\]', text)
        self.assertIsNotNone(m)
        args = [a.strip() for a in m.group("args").split(",")]
        self.assertEqual(len(args), 3, args)
        self.assertFalse(any(a.strip('"').startswith("--") for a in args))


class TestFixedBlocksEndToEnd(_TmpKb):
    """从两个 job 的真源码抽出 KB notes 块，接真 kb_write.sh 跑。"""

    def _run_block(self, block, env_extra):
        script = 'log() { echo "[t] $*"; }\n' + block
        env = dict(os.environ, KB_BASE=self.kb, HOME=self.kb,
                   KB_WRITE_SCRIPT=KB_WRITE, **env_extra)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, env=env, timeout=30)

    def _assert_one_real_note(self, marker, tag):
        notes = _notes(self.kb)
        self.assertEqual(len(notes), 1, notes)
        body = _read(os.path.join(self.kb, "notes", notes[0]))
        self.assertIn(marker, body)
        self.assertIn(f"tags: [{tag}]", body)
        self.assertNotIn("# --title", body)
        self.assertNotIn("type: --tags", body)
        self.assertEqual(_topics(self.kb), [f"{tag}.md"])

    def test_finance_block_writes_real_note(self):
        block = _extract_block(FINANCE, 'if [ -f "$KB_WRITE_SCRIPT" ]; then')
        self.assertIn("finance-news", block)
        r = self._run_block(block, {
            "DAY": "2026-09-28",
            "LLM_CONTENT": "## 📰 今日要闻\n- 美联储维持利率不变 UNIQUE_FIN_LINE"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self._assert_one_real_note("UNIQUE_FIN_LINE", "finance-news")

    def test_chaspark_block_writes_real_note(self):
        block = _extract_block(CHASPARK, 'if [ -x "$KB_WRITE_SCRIPT" ]')
        self.assertIn('"chaspark"', block)
        r = self._run_block(block, {
            "DAY": "2026-09-28",
            "KB_CONTENT": "# 茶思屋深度分析 2026-09-28\n\nUNIQUE_CHA_LINE"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self._assert_one_real_note("UNIQUE_CHA_LINE", "chaspark")

    def test_blood_block_would_now_fail_loud_not_write_garbage(self):
        """血案调用在修复后的 kb_write.sh 下：不再产出垃圾笔记。"""
        r = self._run_block(BLOOD_FINANCE + "\n", {
            "DAY": "2026-09-28", "LLM_CONTENT": "真实内容"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(_notes(self.kb), [])
        self.assertEqual(_topics(self.kb), [])


class TestSourceGuards(unittest.TestCase):
    def test_marker_present(self):
        for path in (KB_WRITE, FINANCE, CHASPARK):
            with self.subTest(path=os.path.basename(path)):
                self.assertIn("V37.9.363", _read(path))

    def test_guard_runs_before_lock(self):
        text = _read(KB_WRITE)
        self.assertLess(text.index("--[A-Za-z]*)"), text.index('mkdir "$LOCKDIR"'))

    def test_scripts_parse(self):
        for path in (KB_WRITE, FINANCE, CHASPARK):
            with self.subTest(path=os.path.basename(path)):
                r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

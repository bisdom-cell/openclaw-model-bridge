#!/usr/bin/env python3
"""V37.9.362 sources 永久归档「段契约」守卫。

kb_append_source.sh（V37.6）的契约只有一句话：**标记 = 写进文件的那一行
H2**，它同时是（1）幂等键（`grep -Fxq` 找它）和（2）按日期窗口读的消费方
（晚间整理 / 周回顾 / 深度分析 / 观察者 / Dream 取样）切段的依据。
V37.6 的守卫只断言「job 脚本里出现了 kb_append_source.sh 字样」——
调用了 helper ≠ 守了它的契约（V37.9.320 静态 grep 给分家族）。三个调用点
各自违反了它，全部静默：

  1. arxiv（cron 08:00/20:00 一天两次）标记 "## ${DAY}" 不带班次 →
     20:00 那次的新论文被当成「同一天重复」跳过 → 永久归档只剩早班。
     ontology_sources（同样一天两次）V37.6 就用了 "## DATE HH:MM"，
     arxiv 迁移时漏了（原则 #31 跨消费者未全量同步）。
  2. finance_news 传裸 "$DAY"，内容不带日期标题 → 归档里从无
     "## YYYY-MM-DD" 段 → 当日财经对日期窗口读取方永远不可见
     （晚间整理报「今日无更新」），幂等标记也永不命中。
  3. chaspark 传裸 "11:00"，内容只有 H1 → 归档无任何 H2 →
     extract_recent_sections 退回「取最后 50 行」→ 茶思屋停跑多日时
     仍把旧文章当「今日覆盖源」（非今日内容冒充今日，V37.9.56-hotfix3 家族）。

修复同时发现两处读取侧问题：finance 的 LLM 模板自带 4 个内层 "## " 标题，
补上日期标题后会把当日段切成「只有标题正文为空」（helper 现把非标记的
"## " 降为 "### "）；daily_observer.scan_source_sections 每遇到一个当日
H2 就重置、遇到其他 H2 就 break → 同日多班次只看得到最后一段。

守卫全部**从真源码抽**（调用点清单 / 标记表达式 / 归档块本身），不写死
脚本名单：未来新增的归档调用点自动进入检查，一天多跑的 job 由
jobs_registry.yaml 的 cron 表达式推导，不靠人记。
"""
import datetime
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(ROOT, "kb_append_source.sh")
JOBS_DIR = os.path.join(ROOT, "jobs")

# 两种调用形态：$HOME/kb_append_source.sh（14 个）/ $KB_APPEND_SCRIPT（chaspark、finance）
CALL_RE = re.compile(
    r'\|\s*bash\s+"(?:\$HOME/kb_append_source\.sh|\$KB_APPEND_SCRIPT)"'
    r'\s+"\$KB_SRC"\s+"([^"]+)"'
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _job_scripts():
    out = []
    for d in sorted(os.listdir(JOBS_DIR)):
        full = os.path.join(JOBS_DIR, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(os.listdir(full)):
            if f.endswith(".sh"):
                out.append(os.path.join(full, f))
    return out


def _block_before(lines, idx):
    """调用行向上回溯到打开管道组的 `{` 行（单行形态即调用行本身）。"""
    for j in range(idx, max(-1, idx - 40), -1):
        if lines[j].lstrip().startswith("{"):
            return "\n".join(lines[j:idx + 1])
    return lines[idx]


def call_sites():
    """[(path, lineno, marker_expr, block_text)] —— 全部归档调用点。"""
    sites = []
    for path in _job_scripts():
        lines = _read(path).split("\n")
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            m = CALL_RE.search(line)
            if m:
                sites.append((path, i + 1, m.group(1), _block_before(lines, i)))
    return sites


def _var_def(src, name):
    """脚本里变量的最后一次赋值（双引号形态）。"""
    defs = re.findall(r'^\s*%s="(.*)"\s*$' % re.escape(name), src, re.M)
    return defs[-1] if defs else None


def resolve_marker(src, expr):
    m = re.fullmatch(r'\$\{?(\w+)\}?', expr)
    if m:
        v = _var_def(src, m.group(1))
        return v if v is not None else expr
    return expr


def marker_problems(src, expr, block):
    """契约 (a) 标记是 H2 行；(b) 标记确实被写进文件（块里 echo 了同一表达式）。"""
    problems = []
    if not resolve_marker(src, expr).startswith("## "):
        problems.append("标记不是 H2 行")
    if 'echo "%s"' % expr not in block:
        problems.append("标记没有写进文件")
    return problems


def marker_has_slot(src, expr):
    """标记能区分同一天的多次运行：含 SLOT_TAG，或所引用的日期变量本身含 %H。"""
    resolved = resolve_marker(src, expr)
    if "SLOT_TAG" in resolved:
        return True
    for var in re.findall(r'\$\{?(\w+)\}?', resolved):
        d = _var_def(src, var)
        if d and "%H" in d:
            return True
    return False


def runs_per_day(cron):
    parts = str(cron).split()
    if len(parts) < 2:
        return 1

    def count(field, span):
        if field == "*":
            return span
        n = 0
        for piece in field.split(","):
            if piece.startswith("*/"):
                n += span // int(piece[2:])
            elif "-" in piece:
                a, b = piece.split("-", 1)
                n += int(b) - int(a) + 1
            else:
                n += 1
        return n

    return count(parts[0], 60) * count(parts[1], 24)


def _registry_multi_run_entries():
    import yaml
    with open(os.path.join(ROOT, "jobs_registry.yaml"), encoding="utf-8") as f:
        reg = yaml.safe_load(f)
    out = {}
    for j in reg.get("jobs", []):
        if j.get("enabled", True) is False:
            continue
        entry = str(j.get("entry", "")).split()[0] if j.get("entry") else ""
        if entry.startswith("jobs/") and runs_per_day(j.get("interval", "")) > 1:
            out[os.path.join(ROOT, entry)] = j["id"]
    return out


def _run_helper(src_file, marker, stdin):
    return subprocess.run(
        ["bash", HELPER, src_file, marker], input=stdin, capture_output=True,
        text=True, env={**os.environ, "KB_APPEND_SOURCE_QUIET": "1"},
    )


# ══════════════════════════════════════════════════════════════════════
class TestCallSiteContract(unittest.TestCase):
    """全部归档调用点逐一守契约（从源码发现，不写死名单）。"""

    @classmethod
    def setUpClass(cls):
        cls.sites = call_sites()

    def test_call_sites_discovered(self):
        # 防空转：两种调用形态都被认出，且覆盖本次三个血案文件
        self.assertGreaterEqual(len(self.sites), 17, self.sites)
        names = {os.path.relpath(p, ROOT) for p, _, _, _ in self.sites}
        for must in ("jobs/arxiv_monitor/run_arxiv.sh",
                     "jobs/finance_news/run_finance_news.sh",
                     "jobs/chaspark/run_chaspark.sh",
                     "jobs/ontology_sources/run_ontology_sources.sh"):
            self.assertIn(must, names)

    def test_every_marker_is_an_h2_line_written_by_its_block(self):
        bad = []
        for path, ln, expr, block in self.sites:
            probs = marker_problems(_read(path), expr, block)
            if probs:
                bad.append("%s:%d %s → %s" % (os.path.relpath(path, ROOT), ln, expr, probs))
        self.assertEqual(bad, [], "归档契约违反:\n" + "\n".join(bad))

    def test_multi_run_jobs_distinguish_runs(self):
        multi = _registry_multi_run_entries()
        # 防空转：registry 推导出的一天多跑 job 必须含 arxiv 与 ontology_sources
        self.assertIn(os.path.join(ROOT, "jobs/arxiv_monitor/run_arxiv.sh"), multi)
        self.assertIn(os.path.join(ROOT, "jobs/ontology_sources/run_ontology_sources.sh"), multi)
        bad = []
        checked = 0
        for path, ln, expr, _ in self.sites:
            if path in multi:
                checked += 1
                if not marker_has_slot(_read(path), expr):
                    bad.append("%s:%d (%s) 标记 %s 不区分班次" % (
                        os.path.relpath(path, ROOT), ln, multi[path], expr))
        self.assertGreaterEqual(checked, 2)
        self.assertEqual(bad, [], "\n".join(bad))

    def test_runs_per_day_parser(self):
        self.assertEqual(runs_per_day("0 8,20 * * *"), 2)
        self.assertEqual(runs_per_day("45 8,14,20 * * *"), 3)
        self.assertEqual(runs_per_day("0 */2 * * *"), 12)
        self.assertEqual(runs_per_day("30 9 * * 3"), 1)
        self.assertEqual(runs_per_day("*/30 * * * *"), 48)

    def test_reverse_evidence_old_forms_are_flagged(self):
        """把三个血案形态逐字放回判据，必须被抓住（证明判据不是空转）。"""
        arxiv_src = 'DAY="$(date \'+%Y-%m-%d\')"\n'
        self.assertFalse(marker_has_slot(arxiv_src, "## ${DAY}"))
        fin_block = 'echo "$LLM_CONTENT" | bash "$KB_APPEND_SCRIPT" "$KB_SRC" "$DAY" "finance_news"'
        self.assertEqual(marker_problems('DAY="$(date \'+%Y-%m-%d\')"\n', "$DAY", fin_block),
                         ["标记不是 H2 行", "标记没有写进文件"])
        cha_src = '    SLOT_TAG="11:00"\n'
        cha_block = 'echo "$KB_CONTENT" | bash "$KB_APPEND_SCRIPT" "$KB_SRC" "$SLOT_TAG"'
        self.assertEqual(marker_problems(cha_src, "$SLOT_TAG", cha_block),
                         ["标记不是 H2 行", "标记没有写进文件"])
        # 对照：ontology_sources 的正确形态必须通过
        onto = _read(os.path.join(ROOT, "jobs/ontology_sources/run_ontology_sources.sh"))
        self.assertTrue(marker_has_slot(onto, "${SECTION_MARKER}"))

    def test_old_forms_retired_from_source(self):
        fin = _read(os.path.join(ROOT, "jobs/finance_news/run_finance_news.sh"))
        self.assertNotIn('"$KB_SRC" "$DAY" "finance_news"', fin)
        cha = _read(os.path.join(ROOT, "jobs/chaspark/run_chaspark.sh"))
        self.assertNotIn('SLOT_TAG="11:00"', cha)


# ══════════════════════════════════════════════════════════════════════
class TestHelperBehavior(unittest.TestCase):
    """真跑 kb_append_source.sh。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = os.path.join(self.tmp, "a.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reverse_same_day_marker_drops_second_run(self):
        # 血案机制：同一个 "## DAY" 标记第二次运行被跳过
        _run_helper(self.f, "## 2026-09-28", "\n## 2026-09-28\n- morning A\n")
        _run_helper(self.f, "## 2026-09-28", "\n## 2026-09-28\n- evening B\n")
        c = _read(self.f)
        self.assertIn("morning A", c)
        self.assertNotIn("evening B", c)

    def test_slot_markers_keep_both_runs(self):
        _run_helper(self.f, "## 2026-09-28 08:00", "\n## 2026-09-28 08:00\n- morning A\n")
        _run_helper(self.f, "## 2026-09-28 20:00", "\n## 2026-09-28 20:00\n- evening B\n")
        c = _read(self.f)
        self.assertIn("morning A", c)
        self.assertIn("evening B", c)

    def test_same_slot_rerun_still_idempotent(self):
        _run_helper(self.f, "## 2026-09-28 08:00", "\n## 2026-09-28 08:00\n- A\n")
        r = _run_helper(self.f, "## 2026-09-28 08:00", "\n## 2026-09-28 08:00\n- A\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(_read(self.f).count("- A"), 1)

    def test_inner_h2_demoted_marker_kept(self):
        _run_helper(self.f, "## 2026-09-28",
                    "\n## 2026-09-28\n## 📰 今日要闻\n1. x\n### 子标题\n## ⚠️ 风险提示\n- y\n")
        lines = _read(self.f).split("\n")
        self.assertIn("## 2026-09-28", lines)
        self.assertIn("### 📰 今日要闻", lines)
        self.assertIn("### ⚠️ 风险提示", lines)
        self.assertIn("### 子标题", lines)          # 原 H3 不被再降
        h2 = [l for l in lines if l.startswith("## ")]
        self.assertEqual(h2, ["## 2026-09-28"])

    def test_marker_with_regex_and_backslash_chars_kept(self):
        # ENVIRON 传标记：awk -v 会吃掉反斜杠，正则元字符也不得影响比较
        marker = r"## 📊 客户画像 2026-09-28 14:00 [a.b]\t"
        _run_helper(self.f, marker, "\n%s\n- p\n" % marker)
        self.assertIn(marker + "\n", _read(self.f))

    def test_helper_uses_environ_not_awk_v(self):
        src = "\n".join(l for l in _read(HELPER).split("\n")
                        if not l.lstrip().startswith("#"))
        self.assertIn('ENVIRON["H2_MARKER"]', src)
        self.assertNotIn("awk -v", src)


# ══════════════════════════════════════════════════════════════════════
def _extract(path, start, end):
    src = _read(path)
    i = src.index(start)
    j = src.index(end, i) + len(end)
    return src[i:j]


class TestRealBlocksThroughConsumers(unittest.TestCase):
    """从三个脚本抽出真实归档块跑真 bash，再交给真实读取方。"""

    TODAY = datetime.datetime(2026, 9, 28, 22, 0)

    def setUp(self):
        self.home = tempfile.mkdtemp()
        shutil.copy(HELPER, os.path.join(self.home, "kb_append_source.sh"))
        self.src_dir = os.path.join(self.home, ".kb", "sources")
        os.makedirs(self.src_dir)
        self.fakebin = os.path.join(self.home, "fakebin")
        os.makedirs(self.fakebin)
        real_date = shutil.which("date")
        with open(os.path.join(self.fakebin, "date"), "w") as f:
            f.write('#!/bin/bash\nif [ "$1" = "+%%H:%%M" ]; then echo "$FAKE_HM"; '
                    'else exec %s "$@"; fi\n' % real_date)
        os.chmod(os.path.join(self.fakebin, "date"), 0o755)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _bash(self, script, **env):
        e = {**os.environ, "HOME": self.home, "KB_APPEND_SOURCE_QUIET": "1",
             "PATH": self.fakebin + os.pathsep + os.environ["PATH"]}
        e.update(env)
        r = subprocess.run(["bash", "-c", "set -eo pipefail\n" + script],
                           capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _recent(self, name, days=1, budget=30000):
        import kb_review_collect as rc
        return rc.extract_recent_sections(
            _read(os.path.join(self.src_dir, name)), days, budget, today=self.TODAY)

    def test_arxiv_both_runs_archived_and_visible(self):
        block = _extract(os.path.join(ROOT, "jobs/arxiv_monitor/run_arxiv.sh"),
                         'SLOT_TAG="$(TZ=', '"$KB_SRC" "${SECTION_MARKER}"')
        kb = os.path.join(self.src_dir, "arxiv_daily.md")
        for hm, paper in (("08:00", "morning-paper"), ("20:00", "evening-paper")):
            msg = os.path.join(self.home, "msg.txt")
            with open(msg, "w") as f:
                f.write("*%s*\n" % paper)
            self._bash(block, DAY="2026-09-28", KB_SRC=kb, MSG_FILE=msg, FAKE_HM=hm)
        got = self._recent("arxiv_daily.md")
        self.assertIn("morning-paper", got)
        self.assertIn("evening-paper", got)   # 血案回归：晚班不再丢

    def test_finance_today_section_visible_with_body(self):
        block = _extract(os.path.join(ROOT, "jobs/finance_news/run_finance_news.sh"),
                         'KB_APPEND_SCRIPT="$HOME/kb_append_source.sh"',
                         '"$KB_SRC" "## ${DAY}"')
        block += "\nfi"
        kb = os.path.join(self.src_dir, "finance_daily.md")
        content = "## 📰 今日要闻（按价值排序）\n1. 美联储维持利率\n## ⚠️ 风险提示\n- 通胀粘性"
        self._bash(block, DAY="2026-09-28", KB_SRC=kb, LLM_CONTENT=content)
        got = self._recent("finance_daily.md")
        self.assertIn("美联储维持利率", got)     # 修复前：''（晚间报「今日无更新」）
        self.assertIn("通胀粘性", got)          # 内层标题不再把正文切出当日段

    def test_chaspark_stale_content_not_presented_as_today(self):
        kb = os.path.join(self.src_dir, "chaspark.md")
        # 修复前遗留的 H1-only 历史内容（无 H2，但行内带日期）
        with open(kb, "w") as f:
            f.write("# 茶思屋深度分析 2026-09-18\n\n📌 旧文章 X\n")
        # 血案形态：原「最后 50 行」回退把 9-18 的旧文当今日；现按窗口日期过滤
        self.assertNotIn("旧文章 X", self._recent("chaspark.md"))
        block = _extract(os.path.join(ROOT, "jobs/chaspark/run_chaspark.sh"),
                         'if [ -f "$KB_APPEND_SCRIPT" ]; then\n    {',
                         '"$KB_SRC" "## ${DAY}"')
        block += "\nfi"
        self._bash(block, DAY="2026-09-27", KB_SRC=kb,
                   KB_APPEND_SCRIPT=os.path.join(self.home, "kb_append_source.sh"),
                   KB_CONTENT="# 茶思屋深度分析 2026-09-27\n\n📌 前日文章 Y")
        # 有了日期段之后：今日没跑 → 今日窗口为空（诚实），昨日窗口能读到昨日
        self.assertEqual(self._recent("chaspark.md"), "")
        self.assertIn("前日文章 Y", self._recent("chaspark.md", days=2))

    def test_observer_sees_every_same_day_slot(self):
        import daily_observer
        with open(os.path.join(self.src_dir, "arxiv_daily.md"), "w") as f:
            f.write("## 2026-09-27 20:00\n- old\n\n## 2026-09-28 08:00\n- morning-A\n\n"
                    "## 2026-09-28 20:00\n- evening-B\n\n## 2026-09-29 08:00\n- next-day\n")
        res = daily_observer.scan_source_sections(
            os.path.join(self.home, ".kb"), datetime.date(2026, 9, 28), max_chars_per=5000)
        text = res[0]["section_text"]
        self.assertIn("morning-A", text)      # 修复前只剩最后一段
        self.assertIn("evening-B", text)
        self.assertNotIn("old", text)
        self.assertNotIn("next-day", text)


# ══════════════════════════════════════════════════════════════════════
class TestSectionBudget(unittest.TestCase):
    """extract_recent_sections 预算按段公平分配（周回顾 / 晚间整理的真实参数）。"""

    FRI = datetime.datetime(2026, 9, 25, 21, 0)   # kb_review 周五 21:00

    @staticmethod
    def _days(text):
        return sorted(set(re.findall(r"## (\d{4}-\d{2}-\d{2})", text)))

    def _week(self, per_day_lines=60):
        parts = []
        for i in range(9, -1, -1):
            d = (self.FRI - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            parts.append("## %s\n" % d + ("*paper-%s* %s\n" % (d, "x" * 60)) * per_day_lines)
        return "\n".join(parts)

    def test_weekly_review_sees_every_day(self):
        import kb_review_collect as rc
        out = rc.extract_recent_sections(self._week(), 7, rc.MAX_SOURCE_CHARS, today=self.FRI)
        # 血案：原实现只返回 2026-09-19（窗口里最旧那天）
        self.assertEqual(len(self._days(out)), 7, self._days(out))
        self.assertIn("2026-09-25", self._days(out))
        self.assertLessEqual(len(out), rc.MAX_SOURCE_CHARS)

    def test_evening_sees_both_arxiv_slots(self):
        import kb_review_collect as rc
        import kb_evening_collect as ev
        arx = ("## 2026-09-25 08:00\n" + ("*m* %s\n" % ("x" * 80)) * 40 +
               "\n## 2026-09-25 20:00\n" + ("*e* %s\n" % ("y" * 80)) * 40)
        out = rc.extract_recent_sections(arx, 1, ev.MAX_SOURCE_CHARS, today=self.FRI)
        self.assertIn("## 2026-09-25 08:00", out)
        self.assertIn("## 2026-09-25 20:00", out)      # 原实现：晚班被早班挤掉
        self.assertLessEqual(len(out), ev.MAX_SOURCE_CHARS)

    def test_output_stays_chronological(self):
        import kb_review_collect as rc
        out = rc.extract_recent_sections(self._week(), 7, 3000, today=self.FRI)
        days = re.findall(r"## (\d{4}-\d{2}-\d{2})", out)
        self.assertEqual(days, sorted(days))

    def test_within_budget_unchanged_no_truncation(self):
        import kb_review_collect as rc
        c = "## 2026-09-24\n- a\n\n## 2026-09-25\n- b\n"
        out = rc.extract_recent_sections(c, 7, 3000, today=self.FRI)
        self.assertIn("- a", out)
        self.assertIn("- b", out)
        self.assertNotIn("[truncated]", out)

    def test_floor_drops_oldest_first(self):
        import kb_review_collect as rc
        parts = ["## 2026-09-25 s%02d\n%s\n" % (i, "z" * 900) for i in range(20)]
        out = rc.extract_recent_sections("\n".join(parts), 1, 1500, today=self.FRI)
        self.assertIn("s19", out)                      # 最新必在
        self.assertNotIn("s10", out)                   # 份额不足时最旧的先舍
        self.assertLessEqual(len(out), 1500)

    def test_fair_cap(self):
        import kb_review_collect as rc
        self.assertEqual(rc._fair_cap([100, 100], 1000), 100)
        self.assertEqual(rc._fair_cap([100, 5000, 5000], 1100), 500)
        self.assertEqual(rc._fair_cap([10], 0), 0)
        self.assertEqual(rc._fair_cap([], 100), 0)


class TestNoH2Fallback(unittest.TestCase):
    """无 H2 归档：行内带日期按窗口过滤，真·无日期文件才取尾部。"""

    TODAY = datetime.datetime(2026, 9, 28, 22, 0)

    def test_hn_dated_lines_filtered_to_window(self):
        import kb_review_collect as rc
        c = ("- **[old](u)** | 2026-09-20 | ⭐3\n"
             "- **[yesterday](u)** | 2026-09-27 | ⭐4\n"
             "- **[today](u)** | 2026-09-28 | ⭐5\n")
        out = rc.extract_recent_sections(c, 1, 2500, today=self.TODAY)
        self.assertIn("today", out)
        self.assertNotIn("yesterday", out)
        self.assertNotIn("old", out)

    def test_hn_stopped_source_is_empty_not_stale(self):
        import kb_review_collect as rc
        c = "- **[old](u)** | 2026-09-20 | ⭐3\n" * 30
        self.assertEqual(rc.extract_recent_sections(c, 1, 2500, today=self.TODAY), "")

    def test_dateless_file_keeps_tail_fallback(self):
        import kb_review_collect as rc
        c = "just\nsome\nplain\nlines\n"
        self.assertIn("lines", rc.extract_recent_sections(c, 1, 2500, today=self.TODAY))

    def test_budget_keeps_newest_lines(self):
        import kb_review_collect as rc
        c = "".join("- **[item-%02d](u)** | 2026-09-28 | %s\n" % (i, "w" * 80) for i in range(40))
        out = rc.extract_recent_sections(c, 1, 500, today=self.TODAY)
        self.assertIn("item-39", out)
        self.assertNotIn("item-00", out)
        self.assertLessEqual(len(out), 500)


if __name__ == "__main__":
    unittest.main()

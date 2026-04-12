#!/usr/bin/env python3
"""test_kb_evening.py — V37.6 kb_evening_collect 单测

V37.6 评价契约：kb_evening 必须继承 V37.5 kb_review 的 6 条架构保证，
并叠加 evening 特有行为（1 天窗口 + evening prompt + 文件名 evening_*.md）。

覆盖维度：
  1. 复用性：kb_evening_collect 导入 kb_review_collect 的所有原语（不复制代码）
  2. 1-day 窗口：DAYS 默认 1，date filter 只保留今日章节
  3. LLM prompt evening-specific：字段名"今日要闻"/"一条行动"/"明日关注"/"健康度"
  4. 文件名区分：生成 evening_YYYYMMDD.md（不是 review_）
  5. fail-fast：LLM 失败 → status=llm_failed，不伪装 ok / 不产出 evening_markdown
  6. Wrapper 契约：kb_evening.sh fail-fast + [SYSTEM_ALERT] + dedup 报告拼接
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb_evening_collect as ev
import kb_review_collect as rc


# ══════════════════════════════════════════════════════════════════════
# 1. 复用性 — kb_evening_collect 不复制 kb_review_collect 的代码
# ══════════════════════════════════════════════════════════════════════
class TestReusesKbReviewHelpers(unittest.TestCase):
    def test_imports_kb_review_collect(self):
        """kb_evening_collect 必须通过 import 复用 kb_review helpers"""
        with open(ev.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("import kb_review_collect", source)

    def test_does_not_redefine_load_sources_from_registry(self):
        """registry 解析器必须是从 kb_review 导入的，不能复制"""
        with open(ev.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn(
            "def load_sources_from_registry",
            source,
            "不允许复制 load_sources_from_registry — 必须复用 kb_review_collect",
        )

    def test_does_not_redefine_extract_recent_sections(self):
        """H2 drill-down 必须是从 kb_review 导入的，不能复制"""
        with open(ev.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn(
            "def extract_recent_sections",
            source,
            "不允许复制 extract_recent_sections — 必须复用 kb_review_collect",
        )

    def test_does_not_redefine_call_llm(self):
        """LLM 调用必须是从 kb_review 导入的，不能复制"""
        with open(ev.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn(
            "def call_llm(",
            source,
            "不允许复制 call_llm — 必须复用 kb_review_collect.call_llm",
        )


# ══════════════════════════════════════════════════════════════════════
# 2. 1-day 窗口契约
# ══════════════════════════════════════════════════════════════════════
class TestOneDayWindow(unittest.TestCase):
    def test_default_days_is_1(self):
        """kb_evening 默认窗口必须是 1 天，不是 7"""
        # main() 从 env DAYS 读取，默认 "1"
        with patch.dict(os.environ, {"DAYS": ""}, clear=False):
            os.environ.pop("DAYS", None)
            # Read from source to verify the default literal
            with open(ev.__file__, "r", encoding="utf-8") as f:
                source = f.read()
            self.assertIn('os.environ.get("DAYS") or "1"', source)

    def test_run_with_days_1_filters_to_today(self):
        """DAYS=1 时窗口只含今日 + 最多昨日（registry 的章节日期过滤）"""
        tmp = tempfile.mkdtemp(prefix="test_ev_")
        try:
            os.makedirs(os.path.join(tmp, "notes"))
            os.makedirs(os.path.join(tmp, "sources"))
            # Today's note
            today = datetime(2026, 4, 11)
            with open(
                os.path.join(tmp, "notes", "20260411090000.md"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write("Today note signal")
            # Yesterday's note (within 1-day window)
            with open(
                os.path.join(tmp, "notes", "20260410090000.md"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write("Yesterday note signal")
            # 3-days-ago note (outside 1-day window)
            with open(
                os.path.join(tmp, "notes", "20260408090000.md"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write("Old note signal")

            captured = {}

            def mock_llm(prompt):
                captured["prompt"] = prompt
                return (
                    True,
                    "1. 今日要闻\n2. 一条行动\n3. 明日关注\n4. 健康度" * 3,
                    "",
                )

            # Use an empty registry so collect_sources returns empty
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8"
            ) as rf:
                rf.write("jobs:\n")
                reg_path = rf.name
            try:
                result = ev.run(
                    tmp, days=1, registry_path=reg_path, today=today,
                    llm_caller=mock_llm,
                )
            finally:
                os.unlink(reg_path)

            self.assertEqual(result["status"], "ok")
            # Today's note must be in prompt
            self.assertIn("Today note signal", captured["prompt"])
            # 3-days-ago note must NOT be in prompt
            self.assertNotIn("Old note signal", captured["prompt"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
# 3. Evening-specific prompt structure
# ══════════════════════════════════════════════════════════════════════
class TestBuildEveningPrompt(unittest.TestCase):
    def test_prompt_has_evening_sections(self):
        """evening prompt 必须包含四个 evening-specific 字段"""
        prompt = ev.build_evening_prompt(
            "note", "source", 1, 100, 298, 5, "AI"
        )
        self.assertIn("今日要闻", prompt)
        self.assertIn("一条行动", prompt)
        self.assertIn("明日关注", prompt)
        self.assertIn("健康度", prompt)

    def test_prompt_does_not_ask_for_weekly_sections(self):
        """evening prompt 不应该要求"跨领域关联"(那是 weekly review 的职责)"""
        prompt = ev.build_evening_prompt(
            "note", "source", 1, 100, 298, 5, "AI"
        )
        self.assertNotIn("跨领域关联", prompt)
        self.assertNotIn("3-5个要点", prompt)  # kb_review 的结构

    def test_prompt_includes_stats(self):
        """V37.7: stats 同时包含"笔记总数"和"今日新增"两个字段"""
        prompt = ev.build_evening_prompt("n", "s", 1, 99, 298, 7, "AI/ML")
        self.assertIn("99", prompt)     # index_total
        self.assertIn("298", prompt)    # note_count (total)
        self.assertIn("7", prompt)      # today_note_count
        self.assertIn("AI/ML", prompt)
        self.assertIn("笔记总数", prompt)
        self.assertIn("今日新增", prompt)

    def test_prompt_handles_empty_notes(self):
        prompt = ev.build_evening_prompt("", "", 1, 100, 0, 0, "")
        self.assertIn("今日无新增笔记", prompt)
        self.assertIn("今日无来源归档更新", prompt)

    def test_prompt_has_anti_hallucination_constraint(self):
        """V37.8.1: prompt 必须包含反幻觉约束（禁止虚构、要求来源标注）"""
        prompt = ev.build_evening_prompt("note", "source", 1, 100, 298, 5, "AI")
        self.assertIn("严禁虚构", prompt)
        self.assertIn("标注来源", prompt)
        self.assertIn("明确出现", prompt)

    def test_prompt_requires_source_labels(self):
        """V37.8.1: 今日要闻每条必须要求标注来源标签"""
        prompt = ev.build_evening_prompt("note", "source", 1, 100, 298, 5, "AI")
        # 要闻条目要求包含来源标签示例
        self.assertIn("[ArXiv]", prompt)
        self.assertIn("[HN]", prompt)


# ══════════════════════════════════════════════════════════════════════
# 4. Evening markdown 输出格式
# ══════════════════════════════════════════════════════════════════════
class TestBuildEveningMarkdown(unittest.TestCase):
    def test_markdown_has_evening_frontmatter(self):
        md = ev.build_evening_markdown(
            "20260411", 1, "test content", 100, 298, 5, "AI",
            ["arxiv"], [], [],
        )
        self.assertIn("type: evening", md)
        self.assertIn("date: 20260411", md)
        self.assertIn("period: 1days", md)

    def test_markdown_does_not_use_review_title(self):
        """evening 文件标题必须是'晚间整理'，不能是'知识回顾'"""
        md = ev.build_evening_markdown(
            "20260411", 1, "test", 100, 298, 5, "AI", [], [], [],
        )
        self.assertIn("# 晚间整理", md)
        self.assertNotIn("# 知识回顾", md)

    def test_markdown_shows_source_coverage(self):
        md = ev.build_evening_markdown(
            "20260411", 1, "test", 100, 298, 5, "AI",
            ["arxiv", "hn"], ["freight"], ["missing_src"],
        )
        self.assertIn("今日覆盖源", md)
        self.assertIn("arxiv", md)
        self.assertIn("hn", md)
        self.assertIn("freight", md)
        self.assertIn("missing_src", md)

    def test_markdown_distinguishes_total_vs_today_counts(self):
        """V37.7: markdown 基础统计必须同时显示"笔记总数"和"今日新增"两列"""
        md = ev.build_evening_markdown(
            "20260411", 1, "test", 100, 298, 5, "AI", ["arxiv"], [], [],
        )
        # Frontmatter 记录 today_note_count
        self.assertIn("today_note_count: 5", md)
        # 基础统计两列分列
        self.assertIn("笔记总数：298", md)
        self.assertIn("今日新增：5", md)


# ══════════════════════════════════════════════════════════════════════
# 5. Evening WA 消息
# ══════════════════════════════════════════════════════════════════════
class TestBuildEveningWaMessage(unittest.TestCase):
    def test_header_uses_evening_emoji(self):
        """晚间整理消息使用 🌙 而非 📚"""
        msg = ev.build_evening_wa_message(
            "20260411", 1, 100, 298, 5, "content here", 3
        )
        self.assertIn("🌙", msg)
        self.assertNotIn("📚", msg)  # 那是 kb_review

    def test_header_uses_evening_title(self):
        msg = ev.build_evening_wa_message(
            "20260411", 1, 100, 298, 5, "content", 3
        )
        self.assertIn("晚间整理", msg)
        self.assertIn("20260411", msg)

    def test_body_truncated_at_1400(self):
        long = "x" * 5000
        msg = ev.build_evening_wa_message(
            "20260411", 1, 100, 298, 5, long, 3
        )
        # Header + \n\n + truncated body
        self.assertLess(len(msg), 1500 + 200)  # header buffer

    def test_header_distinguishes_total_vs_today(self):
        """V37.7 label bug fix: header 必须同时显示"笔记总数 298 | 今日新增 5"
        而不是 V37.6 的错误标签"今日笔记 298 篇" — 298 是历史总数, 不是今日"""
        msg = ev.build_evening_wa_message(
            "20260411", 1, 100, 298, 5, "content", 3
        )
        self.assertIn("笔记总数 298", msg)
        self.assertIn("今日新增 5", msg)
        # V37.6 的错误标签必须消失
        self.assertNotIn("今日笔记 298", msg)


# ══════════════════════════════════════════════════════════════════════
# 5b. count_today_notes (V37.7 label-fix helper)
# ══════════════════════════════════════════════════════════════════════
class TestCountTodayNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_ev_today_")
        os.makedirs(os.path.join(self.tmp, "notes"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name):
        with open(os.path.join(self.tmp, "notes", name), "w") as f:
            f.write("# x")

    def test_counts_only_today_prefix(self):
        """V37.7: 只数文件名前缀 YYYYMMDD 匹配今天的 .md 文件"""
        self._touch("20260411090000.md")
        self._touch("20260411120000.md")
        self._touch("20260410090000.md")  # yesterday, not counted
        self._touch("20260101000000.md")  # old, not counted
        today = datetime(2026, 4, 11)
        n = ev.count_today_notes(self.tmp, today=today)
        self.assertEqual(n, 2)

    def test_returns_zero_when_notes_dir_missing(self):
        tmp = tempfile.mkdtemp(prefix="test_ev_empty_")
        try:
            n = ev.count_today_notes(tmp, today=datetime(2026, 4, 11))
            self.assertEqual(n, 0)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ignores_non_md_files(self):
        self._touch("20260411090000.md")
        # Non-md file with today prefix — should be ignored
        with open(os.path.join(self.tmp, "notes", "20260411090000.txt"), "w") as f:
            f.write("nope")
        today = datetime(2026, 4, 11)
        n = ev.count_today_notes(self.tmp, today=today)
        self.assertEqual(n, 1)


# ══════════════════════════════════════════════════════════════════════
# 6. run() orchestrator — fail-fast contract
# ══════════════════════════════════════════════════════════════════════
class TestRunOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_ev_run_")
        os.makedirs(os.path.join(self.tmp, "notes"))
        os.makedirs(os.path.join(self.tmp, "sources"))
        # Minimal index.json
        with open(
            os.path.join(self.tmp, "index.json"), "w", encoding="utf-8"
        ) as f:
            json.dump({"entries": []}, f)
        # Empty registry (valid parse, 0 sources)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as rf:
            rf.write("jobs:\n")
            self.reg = rf.name

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        if os.path.exists(self.reg):
            os.unlink(self.reg)

    def test_llm_failed_status_not_ok(self):
        """LLM 失败时 run() 返回 llm_failed，不伪装 ok"""
        def mock_llm(prompt):
            return False, "", "HTTP 500"

        result = ev.run(
            self.tmp, days=1, registry_path=self.reg,
            today=datetime(2026, 4, 11), llm_caller=mock_llm,
        )
        self.assertEqual(result["status"], "llm_failed")
        self.assertIn("HTTP 500", result["reason"])

    def test_llm_failed_does_not_produce_evening_markdown(self):
        """LLM 失败路径不应产出任何伪造产物"""
        def mock_llm(prompt):
            return False, "", "timeout"

        result = ev.run(
            self.tmp, days=1, registry_path=self.reg,
            today=datetime(2026, 4, 11), llm_caller=mock_llm,
        )
        self.assertNotIn("evening_markdown", result)
        self.assertNotIn("wa_message", result)
        self.assertNotIn("llm_content", result)

    def test_ok_path_produces_all_artifacts(self):
        """LLM 成功时产出三件套"""
        def mock_llm(prompt):
            return (
                True,
                "今日要闻：测试信号" * 10,
                "",
            )

        result = ev.run(
            self.tmp, days=1, registry_path=self.reg,
            today=datetime(2026, 4, 11), llm_caller=mock_llm,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("evening_markdown", result)
        self.assertIn("wa_message", result)
        self.assertIn("llm_content", result)
        self.assertIn("# 晚间整理", result["evening_markdown"])

    def test_collector_failed_when_registry_missing(self):
        result = ev.run(
            self.tmp, days=1, registry_path="/nonexistent/reg.yaml",
            today=datetime(2026, 4, 11), llm_caller=lambda p: (True, "x" * 100, ""),
        )
        self.assertEqual(result["status"], "collector_failed")
        self.assertIn("reason", result)


# ══════════════════════════════════════════════════════════════════════
# 7. kb_evening.sh wrapper shell-level guards (V37.5 pattern compliance)
# ══════════════════════════════════════════════════════════════════════
class TestKbEveningShellGuards(unittest.TestCase):
    def setUp(self):
        self.script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "kb_evening.sh"
        )

    def test_kb_evening_sh_exists(self):
        self.assertTrue(os.path.isfile(self.script_path))

    def test_calls_collector(self):
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("kb_evening_collect.py", source)

    def test_fail_fast_on_llm_failure(self):
        """wrapper 在 llm_failed 分支必须 send_alert + exit 1"""
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        # Find llm_failed branch
        idx = source.find('STATUS" = "llm_failed"')
        self.assertGreater(idx, 0, "kb_evening.sh 必须处理 llm_failed 分支")
        branch = source[idx : idx + 500]
        self.assertIn("send_alert", branch)
        self.assertIn("exit 1", branch)

    def test_uses_system_alert_marker_via_send_alert(self):
        """告警必须经 send_alert，注入 [SYSTEM_ALERT] 前缀 (V37.4.3 规则 10)"""
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("[SYSTEM_ALERT]", source)
        self.assertIn("send_alert()", source)

    def test_alerts_topic_for_failures(self):
        """告警路径必须用 --topic alerts，不能混进 daily"""
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        # send_alert body should include --topic alerts
        idx = source.find("send_alert() {")
        self.assertGreater(idx, 0)
        body = source[idx : idx + 800]
        self.assertIn("--topic alerts", body)

    def test_dedup_report_integration_preserved(self):
        """evening 仍要拼接 kb_dedup 报告作为健康附注"""
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("kb_dedup.py", source)
        self.assertIn("DEDUP_REPORT", source)

    def test_no_pipe_heredoc_stdin_collision(self):
        """V37.5.1 反模式守卫：不允许 `| python3 - << 'PYEOF'`

        `python3 -` = 从 stdin 读代码；与 heredoc 冲突会吞 JSON 数据。
        `python3 -c 'code'` = 从参数读代码，不读 stdin，安全。
        只匹配前者（`-` 后跟空格/行尾/引号，不跟 `c`）。
        """
        import re
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        for ln_no, line in enumerate(source.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            match = re.search(r"\|\s*python3\s+-(\s|$|\")", line)
            self.assertIsNone(
                match,
                f"kb_evening.sh:{ln_no} 命中 V37.5.1 pipe+heredoc 反模式"
                f"（`python3 -` 从 stdin 读代码，与 heredoc 冲突）: {line!r}",
            )

    def test_writes_evening_file_not_review_file(self):
        """输出文件必须是 evening_*.md，不是 review_*.md"""
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("evening_${DATE}.md", source)
        self.assertNotIn("review_${DATE}.md", source)

    def test_log_rotation_preserved(self):
        """日志轮转（V37 前功能）必须保留"""
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("_rotate_if_large", source)
        self.assertIn("LOG_ROTATE_LIMIT", source)

    def test_status_file_path(self):
        with open(self.script_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("last_run_evening.json", source)


# ══════════════════════════════════════════════════════════════════════
# 8. Shell runtime E2E — 真实 subprocess（MR-6 运行时验证深度）
# ══════════════════════════════════════════════════════════════════════
class TestKbEveningShellRuntime(unittest.TestCase):
    """V37.5.1 教训：声明层 grep 守卫不够，必须真实 subprocess 跑一次 shell。"""

    def setUp(self):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.script_path = os.path.join(self.script_dir, "kb_evening.sh")
        self.tmp_home = tempfile.mkdtemp(prefix="test_ev_rt_")
        os.makedirs(os.path.join(self.tmp_home, ".kb", "daily"))
        os.makedirs(os.path.join(self.tmp_home, ".kb", "notes"))
        os.makedirs(os.path.join(self.tmp_home, ".kb", "sources"))
        with open(
            os.path.join(self.tmp_home, ".kb", "index.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"entries": []}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def test_end_to_end_mock_collector_produces_evening_file(self):
        """kb_evening.sh 真实 subprocess + mock collector → evening 文件落盘"""
        import subprocess
        # Stub collector that emits ok JSON
        stub_collector = os.path.join(self.tmp_home, "kb_evening_collect.py")
        with open(stub_collector, "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "import json,sys\n"
                "print(json.dumps({"
                "'status':'ok',"
                "'date':'20260411',"
                "'days':1,"
                "'index_total':100,"
                "'note_count':3,"
                "'themes':'AI',"
                "'sources_used':['arxiv'],"
                "'sources_skipped':[],"
                "'sources_missing':[],"
                "'llm_content':'今日要闻 测试',"
                "'evening_markdown':'# 晚间整理 20260411\\n\\n测试内容',"
                "'wa_message':'🌙 晚间整理 20260411\\n\\n测试消息'"
                "}))\n"
            )
            os.chmod(stub_collector, 0o755)

        # Stub kb_dedup.py (called by evening.sh for health note)
        stub_dedup = os.path.join(self.tmp_home, "kb_dedup.py")
        with open(stub_dedup, "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "print('[kb_dedup] mock')\n"
                "print('KB健康度: OK')\n"
            )
            os.chmod(stub_dedup, 0o755)

        # Minimal registry
        reg = os.path.join(self.tmp_home, "jobs_registry.yaml")
        with open(reg, "w", encoding="utf-8") as f:
            f.write("jobs:\n")

        # Stub notify.sh so notify() is a no-op that returns 0
        stub_notify = os.path.join(self.tmp_home, "notify.sh")
        with open(stub_notify, "w", encoding="utf-8") as f:
            f.write(
                "notify() { echo \"[mock notify] $*\" >&2; return 0; }\n"
                "export -f notify\n"
            )

        env = {
            "HOME": self.tmp_home,
            "KB_BASE": os.path.join(self.tmp_home, ".kb"),
            "KB_EVENING_REGISTRY": reg,
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TZ": "Asia/Hong_Kong",
        }
        # kb_evening.sh resolves collector via SCRIPT_DIR or $HOME — we put
        # the stub at $HOME/kb_evening_collect.py so the 3rd fallback catches it.
        # But SCRIPT_DIR (where the real kb_evening.sh lives) also has the real
        # collector. To force the stub, copy the script to tmp_home.
        import shutil
        tmp_script = os.path.join(self.tmp_home, "kb_evening.sh")
        shutil.copy(self.script_path, tmp_script)

        result = subprocess.run(
            ["bash", tmp_script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Should not contain JSONDecodeError (V37.5.1 blood lesson guard)
        self.assertNotIn("JSONDecodeError", result.stderr)
        # Evening file should exist
        evening_file = os.path.join(
            self.tmp_home, ".kb", "daily", "evening_20260411.md"
        )
        # Must match today's actual date — use real date
        today_date = datetime.now().strftime("%Y%m%d")
        evening_file_real = os.path.join(
            self.tmp_home, ".kb", "daily", f"evening_{today_date}.md"
        )
        self.assertTrue(
            os.path.isfile(evening_file_real),
            f"evening file not created: stdout={result.stdout[:500]} stderr={result.stderr[:500]}",
        )
        with open(evening_file_real, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("晚间整理", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)

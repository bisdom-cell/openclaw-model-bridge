#!/usr/bin/env python3
"""test_kb_review.py — V37.5 kb_review_collect 单测

V37.5 fail-fast + registry-driven + H2 drill-down 的回归锁定。
覆盖 6-issue silent degradation bug class 的每一个维度：
  1. shell scope bug class → 全 Python 化 (此文件本身即证据)
  2. 硬编码源枚举 → load_sources_from_registry 从 registry 读取
  3. 机械 fallback → run() 对 LLM 失败 fail-fast，不伪装成功
  4. status.json 不诚实 → llm_failed 路径不产出 review 产物
  5. 行级日期匹配漏章节 → extract_recent_sections 按 H2 drill-down
  6. 悬空 follow-up 承诺 → build_wa_message 不含该字符串
"""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb_review_collect as m


# ══════════════════════════════════════════════════════════════════════
# 1. load_sources_from_registry — registry-driven 单一真理源
# ══════════════════════════════════════════════════════════════════════
class TestLoadSourcesFromRegistry(unittest.TestCase):
    def setUp(self):
        self.registry_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "jobs_registry.yaml"
        )

    def test_registry_exists(self):
        self.assertTrue(
            os.path.isfile(self.registry_path),
            "jobs_registry.yaml 必须存在于项目根",
        )

    def test_discovers_at_least_12_sources(self):
        srcs = m.load_sources_from_registry(self.registry_path)
        self.assertGreaterEqual(
            len(srcs), 12, f"V37.5 基线 12 源，实际 {len(srcs)}"
        )

    def test_all_sources_have_kb_source_file(self):
        srcs = m.load_sources_from_registry(self.registry_path)
        for s in srcs:
            self.assertTrue(
                s.get("kb_source_file"),
                f"{s['id']} 缺少 kb_source_file",
            )

    def test_ai_leaders_x_discovered(self):
        """V37.5 修复前硬编码遗漏 ai_leaders_x 源"""
        srcs = m.load_sources_from_registry(self.registry_path)
        ids = {s["id"] for s in srcs}
        self.assertIn("ai_leaders_x", ids)

    def test_ontology_sources_discovered(self):
        """V37.1 新增 ontology_sources 此前未被 kb_review 硬编码覆盖"""
        srcs = m.load_sources_from_registry(self.registry_path)
        ids = {s["id"] for s in srcs}
        self.assertIn("ontology_sources", ids)

    def test_missing_registry_raises(self):
        with self.assertRaises(FileNotFoundError):
            m.load_sources_from_registry("/nonexistent/registry.yaml")

    def test_disabled_jobs_not_included(self):
        """enabled=false 的 job 即使有 kb_source_file 也不应返回"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                "jobs:\n"
                "  - id: enabled_job\n"
                "    enabled: true\n"
                "    kb_source_file: a.md\n"
                "    kb_source_label: A\n"
                "  - id: disabled_job\n"
                "    enabled: false\n"
                "    kb_source_file: b.md\n"
                "    kb_source_label: B\n"
            )
            tmp_path = f.name
        try:
            srcs = m.load_sources_from_registry(tmp_path)
            ids = [s["id"] for s in srcs]
            self.assertIn("enabled_job", ids)
            self.assertNotIn("disabled_job", ids)
        finally:
            os.unlink(tmp_path)


# ══════════════════════════════════════════════════════════════════════
# 2. extract_recent_sections — H2 drill-down 替代行级匹配
# ══════════════════════════════════════════════════════════════════════
class TestExtractRecentSections(unittest.TestCase):
    def setUp(self):
        self.today = datetime(2026, 4, 11)

    def test_empty_content_returns_empty(self):
        result = m.extract_recent_sections("", days=7, max_chars=1000, today=self.today)
        self.assertEqual(result, "")

    def test_window_inside_section_kept(self):
        content = (
            "## 2026-04-10 arxiv report\n"
            "Paper A: inside window\n"
        )
        result = m.extract_recent_sections(
            content, days=3, max_chars=1000, today=self.today
        )
        self.assertIn("Paper A", result)

    def test_window_outside_section_filtered(self):
        content = (
            "## 2026-04-05 arxiv report\n"
            "Paper B: outside window\n"
        )
        result = m.extract_recent_sections(
            content, days=3, max_chars=1000, today=self.today
        )
        self.assertNotIn("Paper B", result)

    def test_three_sections_mixed(self):
        """关键场景：H2 parser 必须过滤窗口外，保留窗口内+今日"""
        content = (
            "# title\n\n"
            "## 2026-04-10 arxiv report\n"
            "Paper A: day-1\n\n"
            "## 2026-04-05 arxiv report\n"
            "Paper B: old\n\n"
            "## 2026-04-11 arxiv report\n"
            "Paper C: today\n"
        )
        result = m.extract_recent_sections(
            content, days=3, max_chars=5000, today=self.today
        )
        self.assertIn("Paper A", result)
        self.assertIn("Paper C", result)
        self.assertNotIn("Paper B", result)

    def test_no_h2_fallback_to_tail(self):
        """无 H2 结构时 fallback 到尾部行（避免完全空白输出）"""
        content = "just\nsome\nplain\nlines\nwithout\nheaders\n"
        result = m.extract_recent_sections(
            content, days=7, max_chars=1000, today=self.today
        )
        # 应返回非空（fallback），包含至少一行原文
        self.assertTrue(any(w in result for w in ["just", "plain", "lines"]))

    def test_compact_date_format_yyyymmdd(self):
        """支持紧凑日期格式 YYYYMMDD（非标准分隔符）"""
        content = (
            "## 20260410 report\n"
            "Entry K: compact format\n"
        )
        result = m.extract_recent_sections(
            content, days=3, max_chars=1000, today=self.today
        )
        self.assertIn("Entry K", result)

    def test_budget_respected(self):
        """超过 max_chars 时必须在章节边界截断"""
        # 生成 20 个 section, 每个 ~300 chars
        parts = []
        for i in range(20):
            parts.append(
                f"## 2026-04-{10 if i % 2 == 0 else 11} section {i}\n"
                + ("x" * 300) + "\n"
            )
        content = "\n".join(parts)
        result = m.extract_recent_sections(
            content, days=3, max_chars=1500, today=self.today
        )
        # 输出不应远超 max_chars (允许 truncation 标记)
        self.assertLess(len(result), 2200)

    def test_header_only_section_date_in_body_kept(self):
        """日期出现在 body 前 5 行也应被保留（header 描述性）"""
        content = (
            "## ArXiv Daily\n"
            "Updated: 2026-04-10\n"
            "Paper D: body-matched\n"
        )
        result = m.extract_recent_sections(
            content, days=3, max_chars=1000, today=self.today
        )
        self.assertIn("Paper D", result)


# ══════════════════════════════════════════════════════════════════════
# 3. collect_notes — KB notes 采集
# ══════════════════════════════════════════════════════════════════════
class TestCollectNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_kb_notes_")
        self.notes_dir = os.path.join(self.tmp, "notes")
        os.makedirs(self.notes_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_notes_dir_returns_empty(self):
        empty_tmp = tempfile.mkdtemp(prefix="test_kb_empty_")
        try:
            result = m.collect_notes(empty_tmp, days=7, max_chars=5000)
            self.assertEqual(result, "")
        finally:
            import shutil
            shutil.rmtree(empty_tmp, ignore_errors=True)

    def test_recent_note_included(self):
        today = datetime(2026, 4, 11)
        note_name = "20260410120000.md"
        with open(
            os.path.join(self.notes_dir, note_name), "w", encoding="utf-8"
        ) as f:
            f.write("# My Note\nSome content here")
        result = m.collect_notes(
            self.tmp, days=3, max_chars=5000, today=today
        )
        self.assertIn("Some content", result)
        self.assertIn("20260410", result)

    def test_old_note_filtered(self):
        today = datetime(2026, 4, 11)
        with open(
            os.path.join(self.notes_dir, "20260301120000.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("# Old Note\nExpired content")
        result = m.collect_notes(
            self.tmp, days=3, max_chars=5000, today=today
        )
        self.assertNotIn("Expired content", result)

    def test_frontmatter_stripped(self):
        today = datetime(2026, 4, 11)
        with open(
            os.path.join(self.notes_dir, "20260410120000.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("---\ntitle: test\ntags: [a, b]\n---\nactual body content")
        result = m.collect_notes(
            self.tmp, days=3, max_chars=5000, today=today
        )
        self.assertIn("actual body content", result)
        self.assertNotIn("tags: [a, b]", result)


# ══════════════════════════════════════════════════════════════════════
# 4. call_llm — LLM 契约（空/短响应/网络错误 fail-fast）
# ══════════════════════════════════════════════════════════════════════
class TestCallLlm(unittest.TestCase):
    def _mock_response(self, body_dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(body_dict).encode("utf-8")
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = lambda self, *a: None
        return mock_resp

    def test_happy_path(self):
        """合规响应返回 (True, content, '')"""
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "本期亮点：Paper A 值得关注。" + "内容详情" * 20
                    }
                }
            ]
        }
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            ok, content, reason = m.call_llm("test prompt")
        self.assertTrue(ok)
        self.assertIn("Paper A", content)
        self.assertEqual(reason, "")

    def test_empty_content_rejected(self):
        payload = {"choices": [{"message": {"content": ""}}]}
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            ok, content, reason = m.call_llm("test prompt")
        self.assertFalse(ok)
        self.assertIn("empty", reason.lower())

    def test_short_content_rejected(self):
        """V37.5 最小内容阈值：<80 chars 判失败"""
        payload = {"choices": [{"message": {"content": "too short"}}]}
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            ok, content, reason = m.call_llm("test prompt")
        self.assertFalse(ok)
        self.assertIn("too short", reason)

    def test_invalid_response_structure_rejected(self):
        payload = {"unexpected": "shape"}
        with patch("urllib.request.urlopen", return_value=self._mock_response(payload)):
            ok, content, reason = m.call_llm("test prompt")
        self.assertFalse(ok)
        self.assertIn("invalid response structure", reason)

    def test_http_error_reason_included(self):
        import urllib.error
        def raise_http_error(*args, **kwargs):
            raise urllib.error.HTTPError(
                "http://test", 500, "Internal Server Error", {}, None
            )
        with patch("urllib.request.urlopen", side_effect=raise_http_error):
            ok, content, reason = m.call_llm("test prompt")
        self.assertFalse(ok)
        self.assertIn("500", reason)

    def test_url_error_reason_included(self):
        import urllib.error
        def raise_url_error(*args, **kwargs):
            raise urllib.error.URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=raise_url_error):
            ok, content, reason = m.call_llm("test prompt")
        self.assertFalse(ok)
        self.assertIn("URLError", reason)


# ══════════════════════════════════════════════════════════════════════
# 5. run — orchestrator fail-fast 契约
# ══════════════════════════════════════════════════════════════════════
class TestRunOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_kb_run_")
        self.registry_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "jobs_registry.yaml"
        )
        self.today = datetime(2026, 4, 11)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failing_llm_produces_llm_failed_status(self):
        """mock 注入失败 → status=llm_failed，不降级为 ok"""
        def mock_fail(prompt):
            return (False, "", "mock_proxy_down")
        result = m.run(
            self.tmp, 7, self.registry_path,
            today=self.today, llm_caller=mock_fail,
        )
        self.assertEqual(result["status"], "llm_failed")
        self.assertEqual(result["reason"], "mock_proxy_down")

    def test_failing_llm_does_not_emit_artifacts(self):
        """关键不变式：LLM 失败后 run() 不得产出 review_markdown/wa_message"""
        def mock_fail(prompt):
            return (False, "", "any_reason")
        result = m.run(
            self.tmp, 7, self.registry_path,
            today=self.today, llm_caller=mock_fail,
        )
        self.assertNotIn("review_markdown", result)
        self.assertNotIn("wa_message", result)

    def test_happy_path_emits_artifacts(self):
        """成功路径：产出完整 envelope"""
        ok_content = "本期亮点：Paper A、B、C。" + "更多细节内容" * 10
        def mock_ok(prompt):
            return (True, ok_content, "")
        result = m.run(
            self.tmp, 7, self.registry_path,
            today=self.today, llm_caller=mock_ok,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("review_markdown", result)
        self.assertIn("wa_message", result)
        self.assertIn("Paper A", result["llm_content"])

    def test_wa_message_no_dangling_followup_promise(self):
        """V37.5: 不再追加悬空 follow-up 承诺字符串"""
        def mock_ok(prompt):
            return (True, "本期亮点内容" + "x" * 80, "")
        result = m.run(
            self.tmp, 7, self.registry_path,
            today=self.today, llm_caller=mock_ok,
        )
        # Load forbidden phrase from its own source to avoid literal repeat
        forbidden = "回复任何话题" + "可深入讨论"
        self.assertNotIn(forbidden, result["wa_message"])
        self.assertNotIn(forbidden, result["review_markdown"])

    def test_missing_registry_returns_collector_failed(self):
        def mock_ok(prompt):
            return (True, "x" * 100, "")
        result = m.run(
            self.tmp, 7, "/nonexistent/registry.yaml",
            today=self.today, llm_caller=mock_ok,
        )
        self.assertEqual(result["status"], "collector_failed")
        self.assertIn("registry", result["reason"].lower())

    def test_prompt_built_from_collected_content(self):
        """验证 llm_caller 收到的 prompt 包含采集到的结构元素"""
        received = {}
        def capture(prompt):
            received["prompt"] = prompt
            return (True, "x" * 100, "")
        m.run(
            self.tmp, 7, self.registry_path,
            today=self.today, llm_caller=capture,
        )
        self.assertIn("本期亮点", received["prompt"])
        self.assertIn("跨领域关联", received["prompt"])
        self.assertIn("知识库总条目", received["prompt"])


# ══════════════════════════════════════════════════════════════════════
# 6. build_review_markdown / build_wa_message — 输出契约
# ══════════════════════════════════════════════════════════════════════
class TestBuildOutputs(unittest.TestCase):
    def test_review_markdown_has_required_sections(self):
        md = m.build_review_markdown(
            date_str="20260411", days=7,
            llm_content="LLM analysis text",
            index_total=100, note_count=5, themes="AI / RAG",
            sources_used=["ArXiv", "HN"],
            sources_skipped=["RSS"],
            sources_missing=["DeadSource"],
        )
        self.assertIn("知识回顾 20260411", md)
        self.assertIn("LLM 深度分析", md)
        self.assertIn("LLM analysis text", md)
        self.assertIn("✓ ArXiv", md)
        self.assertIn("○ RSS", md)
        self.assertIn("✗ DeadSource", md)
        self.assertIn("llm_analyzed: true", md)

    def test_wa_message_has_header_and_body(self):
        msg = m.build_wa_message(
            date_str="20260411", days=7,
            index_total=100, note_count=5,
            llm_content="body content here",
            sources_count=3,
        )
        self.assertIn("📚 知识回顾 20260411", msg)
        self.assertIn("7天", msg)
        self.assertIn("KB总条目 100", msg)
        self.assertIn("本期笔记 5篇", msg)
        self.assertIn("覆盖 3 源", msg)
        self.assertIn("body content here", msg)

    def test_wa_message_truncates_long_body(self):
        long = "x" * 3000
        msg = m.build_wa_message(
            "20260411", 7, 100, 5, long, 3,
        )
        # body truncated to 1400 chars
        body_part = msg.split("\n\n", 1)[1]
        self.assertLessEqual(len(body_part), 1400)

    def test_wa_message_no_dangling_promise(self):
        msg = m.build_wa_message(
            "20260411", 7, 100, 5, "analysis content", 3,
        )
        forbidden = "回复任何话题" + "可深入讨论"
        self.assertNotIn(forbidden, msg)


# ══════════════════════════════════════════════════════════════════════
# 7. collect_sources — sources 采集集成
# ══════════════════════════════════════════════════════════════════════
class TestCollectSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_kb_src_")
        self.sources_dir = os.path.join(self.tmp, "sources")
        os.makedirs(self.sources_dir)

        # Mini registry with 2 sources: one exists, one missing
        self.reg = os.path.join(self.tmp, "reg.yaml")
        with open(self.reg, "w", encoding="utf-8") as f:
            f.write(
                "jobs:\n"
                "  - id: exists_src\n"
                "    enabled: true\n"
                "    kb_source_file: exists.md\n"
                "    kb_source_label: EXISTS\n"
                "  - id: missing_src\n"
                "    enabled: true\n"
                "    kb_source_file: missing.md\n"
                "    kb_source_label: MISSING\n"
                "  - id: skipped_src\n"
                "    enabled: true\n"
                "    kb_source_file: skipped.md\n"
                "    kb_source_label: SKIPPED\n"
            )

        # exists.md has content in window
        with open(
            os.path.join(self.sources_dir, "exists.md"), "w", encoding="utf-8"
        ) as f:
            f.write("## 2026-04-10 test\nFresh content here\n")
        # skipped.md has content out of window
        with open(
            os.path.join(self.sources_dir, "skipped.md"), "w", encoding="utf-8"
        ) as f:
            f.write("## 2026-03-01 test\nOld content only\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_categorization(self):
        today = datetime(2026, 4, 11)
        info = m.collect_sources(
            self.tmp, self.reg, days=3, max_chars_per_source=2000, today=today
        )
        self.assertIn("EXISTS", info["used"])
        self.assertIn("SKIPPED", info["skipped"])
        self.assertIn("MISSING", info["missing"])
        self.assertIn("Fresh content here", info["text"])
        self.assertNotIn("Old content only", info["text"])


# ══════════════════════════════════════════════════════════════════════
# 8. kb_review.sh source-level guards (V37.5 grep 锁)
# ══════════════════════════════════════════════════════════════════════
class TestKbReviewShellGuards(unittest.TestCase):
    def setUp(self):
        self.sh_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "kb_review.sh"
        )
        with open(self.sh_path, encoding="utf-8") as f:
            self.content = f.read()

    def test_v37_5_version_marker(self):
        self.assertIn("V37.5", self.content)

    def test_system_alert_marker_present(self):
        self.assertIn("[SYSTEM_ALERT]", self.content)

    def test_no_mechanical_fallback_label(self):
        self.assertNotIn("LLM 不可用, 基础整理", self.content)

    def test_no_dangling_followup_promise(self):
        forbidden = "回复任何话题" + "可深入讨论"
        self.assertNotIn(forbidden, self.content)

    def test_llm_failed_branch_fail_fast(self):
        """llm_failed 分支后 500 字符内必须 exit 1"""
        idx = self.content.find('STATUS" = "llm_failed"')
        self.assertNotEqual(idx, -1, "kb_review.sh 缺少 llm_failed 分支")
        exit_idx = self.content.find("exit 1", idx)
        self.assertNotEqual(exit_idx, -1)
        self.assertLess(
            exit_idx - idx, 500,
            f"llm_failed 分支必须立即 exit 1，距离 {exit_idx - idx}"
        )

    def test_collector_failed_branch_fail_fast(self):
        idx = self.content.find('STATUS" = "collector_failed"')
        if idx != -1:
            exit_idx = self.content.find("exit 1", idx)
            self.assertNotEqual(exit_idx, -1)
            self.assertLess(exit_idx - idx, 500)

    def test_calls_python_collector(self):
        self.assertIn("kb_review_collect.py", self.content)

    def test_no_export_notes_content_bug(self):
        """V29 bug: shell 变量 export 后才填充，Python 子进程拿到空值"""
        # V37.5 不应再使用 shell 变量传递 prompt 内容
        self.assertNotIn("export NOTES_CONTENT", self.content)
        self.assertNotIn("export SOURCE_CONTENT", self.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)

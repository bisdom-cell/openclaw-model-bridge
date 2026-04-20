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
# 4b. V37.8.10 _compose_http_reason — upstream body exposure
# ══════════════════════════════════════════════════════════════════════
class TestComposeHttpReason(unittest.TestCase):
    """Blood lesson regression (2026-04-14/15 kb_evening 22:00):
    告警 "HTTP 502: Bad Gateway" 丢弃了 proxy 的 upstream body，
    真实原因 "gemini 429 quota exhausted" 对用户不可见。
    """

    def _make_http_error(self, code, reason, body_bytes):
        import urllib.error
        import io
        fp = io.BytesIO(body_bytes) if body_bytes is not None else None
        return urllib.error.HTTPError("http://proxy:5002", code, reason, {}, fp)

    def test_json_error_field_extracted(self):
        """adapter-style JSON body → upstream 字段拼进 reason"""
        body = json.dumps({"error": "ALL 1 FALLBACKS FAILED: gemini HTTP 429"}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        reason = m._compose_http_reason(e)
        self.assertIn("HTTP 502", reason)
        self.assertIn("Bad Gateway", reason)
        self.assertIn("upstream:", reason)
        self.assertIn("gemini HTTP 429", reason)

    def test_blood_lesson_kb_evening_alert_actually_contains_gemini_429(self):
        """如果 2026-04-14/15 血案场景再演，用户告警会看到 gemini 429。"""
        body = json.dumps({
            "error": "HTTP Error 502: Bad Gateway | upstream: ALL 1 FALLBACKS FAILED: gemini HTTP Error 429: Too Many Requests"
        }).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        reason = m._compose_http_reason(e)
        self.assertIn("gemini", reason)
        self.assertIn("429", reason)
        self.assertIn("Too Many Requests", reason)

    def test_non_json_body_raw_text_fallback(self):
        """adapter 返回 text/html 错误页时 fallback 到原始文本"""
        body = b"<html><body>502 Bad Gateway nginx/1.21.0</body></html>"
        e = self._make_http_error(502, "Bad Gateway", body)
        reason = m._compose_http_reason(e)
        self.assertIn("502 Bad Gateway nginx", reason)

    def test_empty_body_falls_back_to_status_phrase(self):
        """body 为空时保持原行为（向后兼容）"""
        e = self._make_http_error(502, "Bad Gateway", b"")
        reason = m._compose_http_reason(e)
        self.assertEqual(reason, "HTTP 502: Bad Gateway")
        self.assertNotIn("upstream:", reason)

    def test_none_fp_falls_back_gracefully(self):
        """HTTPError with fp=None → read() 返回空，reason 无 upstream 字段"""
        e = self._make_http_error(502, "Bad Gateway", None)
        reason = m._compose_http_reason(e)
        # HTTPError.read() with None fp may raise AttributeError or return b""
        # 契约: 任何失败都 fallback 到状态短语，不得崩溃
        self.assertIn("HTTP 502", reason)

    def test_body_read_exception_is_caught(self):
        """e.read() 抛异常时 reason 仍包含状态短语（fail-open 契约）"""
        e = self._make_http_error(502, "Bad Gateway", b"whatever")
        # Sabotage: make .read() throw
        def boom():
            raise RuntimeError("disk on fire")
        e.read = boom
        reason = m._compose_http_reason(e)
        self.assertIn("HTTP 502", reason)
        self.assertIn("Bad Gateway", reason)
        # Must not crash test runner
        self.assertIsInstance(reason, str)

    def test_body_over_400_chars_truncated(self):
        """超长 body 截断到 MAX_UPSTREAM_BODY_CHARS + truncation marker"""
        long_err = "x" * 1000
        body = json.dumps({"error": long_err}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        reason = m._compose_http_reason(e)
        self.assertIn("[truncated]", reason)
        # 总长度受控：status phrase + upstream budget + marker
        self.assertLess(len(reason), m.MAX_UPSTREAM_BODY_CHARS + 100)

    def test_utf8_decode_errors_replaced(self):
        """non-utf8 bytes → errors='replace' → reason 仍可读"""
        # Invalid UTF-8 sequence
        body = b'\xff\xfe\x00garbage' + json.dumps({"error": "valid_after_garbage"}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        reason = m._compose_http_reason(e)
        # No crash, reason contains status phrase
        self.assertIn("HTTP 502", reason)

    def test_json_without_error_field_falls_back_to_raw(self):
        """JSON 合法但无 error 字段 → upstream = raw text (不遗漏信息)"""
        body = json.dumps({"detail": "something else", "code": 502}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        reason = m._compose_http_reason(e)
        # Raw JSON text fallback contains the fields
        self.assertIn("detail", reason)

    def test_call_llm_integration_http_error_with_body(self):
        """call_llm 端到端: HTTPError + body → reason 正确拼接 upstream"""
        import urllib.error
        import io
        body = json.dumps({"error": "gemini: HTTP Error 429: Too Many Requests"}).encode()

        def raise_http_with_body(*args, **kwargs):
            fp = io.BytesIO(body)
            raise urllib.error.HTTPError(
                "http://proxy", 502, "Bad Gateway", {}, fp
            )

        with patch("urllib.request.urlopen", side_effect=raise_http_with_body):
            ok, content, reason = m.call_llm("test prompt")
        self.assertFalse(ok)
        self.assertEqual(content, "")
        self.assertIn("HTTP 502", reason)
        self.assertIn("gemini", reason)
        self.assertIn("429", reason)


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

    def test_prompt_has_anti_hallucination_constraint(self):
        """V37.8.1: review prompt 必须包含反幻觉约束"""
        prompt = m.build_prompt("notes", "sources", 7, 100, 50, "AI")
        self.assertIn("严禁虚构", prompt)
        self.assertIn("标注来源", prompt)
        self.assertIn("明确出现", prompt)


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

    def test_no_pipe_heredoc_stdin_collision(self):
        """V37.5.1 回归: 禁止 `| python3 - ... << PYEOF` 反模式

        Bug: echo "$X" | python3 - "$arg" << 'PYEOF' 同时把 pipe 和 heredoc
        写入 python3 的 stdin, heredoc 覆盖 pipe, python3 - 读代码用掉 stdin,
        代码内的 json.load(sys.stdin) 读到空 → JSONDecodeError。
        2026-04-11 Mac Mini E2E 首次触发。修复: 用环境变量传数据, heredoc 只传代码。
        """
        # 匹配 `python3 -` 后跟空格/结尾/引号 (即 "read code from stdin" 模式)
        # 注意区别于 `python3 -c` (inline code, 不读 stdin)
        # 跳过 shell 注释行 (# 开头), 允许文档里描述 bug
        for lineno, line in enumerate(self.content.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            m = re.search(r"\|\s*python3\s+-(\s|$|\")", line)
            self.assertIsNone(
                m,
                f"kb_review.sh:{lineno} 不得使用 `| python3 -` (stdin-as-code) 反模式, "
                f"与 heredoc 冲突会吞 JSON 数据 (V37.5.1 blood lesson). "
                f"修复: 改用 env var + `python3 << PYEOF`. 行内容: {line!r}",
            )


# ══════════════════════════════════════════════════════════════════════
# 9. V37.5.1 Subprocess Runtime 测试 — 真实执行 shell 脚本关键路径
#    (TestKbReviewShellGuards 是 grep 守卫, 本类是运行时守卫, MR-6 要求)
# ══════════════════════════════════════════════════════════════════════
class TestKbReviewShellRuntime(unittest.TestCase):
    """V37.5.1 新增: 真实 subprocess 执行验证 heredoc+env var 数据传递能跑通。

    MR-6 元规则: grep 守卫不足, 必须有运行时回归。
    2026-04-11 血案: TestKbReviewShellGuards 的 8 个检查全过, 但 Mac Mini
    第一次真实 run 就炸 JSONDecodeError — 因为 shell 脚本的 IO 行为
    grep 看不见。
    """

    def test_env_var_heredoc_pattern_writes_file(self):
        """V37.5.1 fix 验证: env var + heredoc 模式成功写文件 (不再吞 JSON)"""
        import subprocess
        tmp = tempfile.mkdtemp(prefix="kb_review_runtime_")
        try:
            review_file = os.path.join(tmp, "review.md")
            collector_output = json.dumps({
                "status": "ok",
                "review_markdown": "# V37.5.1 runtime test\n\n内容正确写入。",
                "wa_message": "x",
                "note_count": 1,
                "sources_used": [],
            })
            # 复刻 kb_review.sh:155-161 (V37.5.1 fix 后的模式)
            script = (
                'COLLECTOR_OUTPUT="$COLLECTOR_OUTPUT" '
                'REVIEW_FILE="$REVIEW_FILE" '
                "python3 << 'PYEOF'\n"
                "import json, os\n"
                'data = json.loads(os.environ["COLLECTOR_OUTPUT"])\n'
                'with open(os.environ["REVIEW_FILE"], "w", encoding="utf-8") as f:\n'
                '    f.write(data["review_markdown"])\n'
                "PYEOF\n"
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "COLLECTOR_OUTPUT": collector_output,
                    "REVIEW_FILE": review_file,
                },
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(
                result.returncode, 0,
                f"env var + heredoc 模式执行失败: {result.stderr}",
            )
            self.assertNotIn("JSONDecodeError", result.stderr)
            with open(review_file, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("V37.5.1 runtime test", content)
            self.assertIn("内容正确写入", content)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pipe_heredoc_antipattern_actually_fails(self):
        """负向证据: V37.5.1 前的反模式在 subprocess 中确实炸 JSONDecodeError

        留作文档: 为什么这是 bug 不是"偶尔不稳定"。
        """
        import subprocess
        tmp = tempfile.mkdtemp(prefix="kb_review_antipattern_")
        try:
            review_file = os.path.join(tmp, "review.md")
            collector_output = json.dumps({"review_markdown": "should NOT write"})
            # V37.5 原始 buggy 模式: echo | python3 - + heredoc
            script = (
                f'echo {json.dumps(collector_output)} | '
                f'python3 - {json.dumps(review_file)} << \'PYEOF\'\n'
                "import json, sys\n"
                "review_file = sys.argv[1]\n"
                "data = json.load(sys.stdin)\n"
                'with open(review_file, "w", encoding="utf-8") as f:\n'
                '    f.write(data["review_markdown"])\n'
                "PYEOF\n"
            )
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=15,
            )
            # 反模式应该失败, 不然说明 bash 语义变了, 这条测试需要重写
            self.assertNotEqual(
                result.returncode, 0,
                "反模式应该 JSONDecodeError 失败, 但退出码=0 — 重新评估 shell 语义",
            )
            self.assertTrue(
                "JSONDecodeError" in result.stderr or "Expecting value" in result.stderr,
                f"期望 JSONDecodeError, 实际 stderr: {result.stderr[:500]}",
            )
            # review 文件不应被写入 (pipe 数据被吞掉)
            self.assertFalse(
                os.path.exists(review_file),
                "反模式竟然写成了 review file — shell 语义变更?",
            )
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_kb_review_sh_end_to_end_mock_collector(self):
        """真 E2E: 用 mock collector 驱动完整 kb_review.sh, 验证 review 文件落盘

        这是 TestKbReviewShellGuards 的 runtime 对应: 前者 grep, 本测真跑。
        只验证到 "写 review 文件" 这一步; 推送阶段无 openclaw 会失败, 不关心。
        """
        import subprocess
        import shutil
        tmp = tempfile.mkdtemp(prefix="kb_review_e2e_")
        try:
            repo_dir = os.path.dirname(os.path.abspath(__file__))
            # 1. 拷贝 kb_review.sh 到 tmp (SCRIPT_DIR 会指到 tmp)
            shutil.copy(os.path.join(repo_dir, "kb_review.sh"), tmp)
            # 2. 写 mock collector: 打印 canned JSON, 不访问 LLM
            mock_collector = os.path.join(tmp, "kb_review_collect.py")
            with open(mock_collector, "w", encoding="utf-8") as f:
                f.write(
                    "import json\n"
                    "print(json.dumps({\n"
                    '    "status": "ok",\n'
                    '    "review_markdown": "# E2E mock review\\n\\n笔记签名: alpha.",\n'
                    '    "wa_message": "E2E 回顾",\n'
                    '    "note_count": 42,\n'
                    '    "sources_used": ["arxiv"],\n'
                    '    "sources_skipped": [],\n'
                    '    "sources_missing": []\n'
                    "}))\n"
                )
            # 3. 写 stub registry (kb_review.sh 只校验存在, 不解析内容在 shell 层)
            with open(os.path.join(tmp, "jobs_registry.yaml"), "w") as f:
                f.write("jobs:\n  - id: stub\n    enabled: true\n")
            # 4. mock KB dir
            kb_dir = os.path.join(tmp, "kb")
            os.makedirs(os.path.join(kb_dir, "daily"), exist_ok=True)
            # 5. 执行 (notify/openclaw 都不存在, 推送会失败 → exit 1,
            #    但 review 文件在推送之前就写好了)
            result = subprocess.run(
                ["bash", os.path.join(tmp, "kb_review.sh")],
                env={
                    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                    "KB_BASE": kb_dir,
                    "KB_REVIEW_REGISTRY": os.path.join(tmp, "jobs_registry.yaml"),
                    "OPENCLAW_PHONE": "+85200000000",
                    "HOME": tmp,
                },
                capture_output=True, text=True, timeout=30,
            )
            # JSONDecodeError 是 V37.5.1 修复的 bug — 必须不出现
            self.assertNotIn(
                "JSONDecodeError", result.stderr,
                f"V37.5.1 pipe+heredoc bug 回归! stderr: {result.stderr[:500]}",
            )
            self.assertNotIn("Expecting value", result.stderr)
            # review 文件必须已经落盘 (写文件在推送之前)
            daily_dir = os.path.join(kb_dir, "daily")
            review_files = [
                f for f in os.listdir(daily_dir) if f.startswith("review_")
            ]
            self.assertEqual(
                len(review_files), 1,
                f"期望 1 份 review 文件, 实际: {review_files}. stderr: {result.stderr[:500]}",
            )
            with open(os.path.join(daily_dir, review_files[0]), encoding="utf-8") as f:
                content = f.read()
            self.assertIn("E2E mock review", content)
            self.assertIn("笔记签名: alpha", content)
        finally:
            import shutil as _sh
            _sh.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

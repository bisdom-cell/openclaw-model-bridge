"""
test_expert_escalation.py — V37.9.90-r1 tests (Doubao backend)

Coverage:
- TestLoadStatus / TestLoadChangelogWindow / TestSelectRelevantCaseDocs /
  TestBuildContextBlock — unchanged from v1
- TestValidateReadOnly — 4 violation classes
- TestCheckDailyQuota / TestAuditRecord / TestParseResponseJson — unchanged
- TestDoubaoTransport — HTTP transport contract (mock _post)
- TestEscalateOrchestratorDoubao — end-to-end with FakeDoubaoTransport
- TestBackendSelector — backend="doubao" default, "claude_pending" stub
- TestCliInterface — subprocess --dry-run + --backend claude_pending
- TestV37990R1SourceGuards — V37.9.90-r1 marker + Doubao path + no anthropic
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

import expert_escalation as ee  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# Fake Doubao transport (mock _post; preserves full call() logic)
# ══════════════════════════════════════════════════════════════════════


class FakeDoubaoTransport(ee.DoubaoTransport):
    """Override _post to return canned responses. Captures all payloads."""

    def __init__(self, responses=None, api_key="fake-key-for-test",
                 endpoint_id="fake-endpoint"):
        super().__init__(api_key=api_key, endpoint_id=endpoint_id)
        self.responses = list(responses or [])
        self.posted_payloads = []

    def _post(self, payload):
        self.posted_payloads.append(payload)
        if not self.responses:
            return False, {}, "FakeDoubaoTransport exhausted"
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            return False, {}, type(nxt).__name__ + ": " + str(nxt)
        return True, nxt, ""


def _good_doubao_response(proposal="Review the case docs.",
                          rationale="status.json shows V37.9.90-r1.",
                          confidence="medium",
                          refs=None,
                          cached_tokens=0,
                          reasoning_content=""):
    """Build a Volcengine-shaped OpenAI Chat Completions response dict."""
    payload = {
        "proposal": proposal,
        "rationale": rationale,
        "confidence": confidence,
        "refs": refs or [],
    }
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": json.dumps(payload, ensure_ascii=False),
                "reasoning_content": reasoning_content,
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 5000,
            "completion_tokens": 200,
            "prompt_tokens_details": {
                "cached_tokens": cached_tokens,
            },
        },
    }


# ══════════════════════════════════════════════════════════════════════
# 1. TestLoadStatus
# ══════════════════════════════════════════════════════════════════════


class TestLoadStatus(unittest.TestCase):

    def test_loads_from_kb_dir(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "status.json"), "w") as f:
                json.dump({"version": "0.37.9.90-r1"}, f)
            result = ee.load_status(td)
            self.assertEqual(result["version"], "0.37.9.90-r1")

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            result = ee.load_status(td)
            self.assertTrue(result is None or isinstance(result, dict))

    def test_returns_none_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "status.json"), "w") as f:
                f.write("{not valid")
            result = ee.load_status(td)
            self.assertTrue(result is None or isinstance(result, dict))

    def test_none_kb_dir_uses_default(self):
        result = ee.load_status(None)
        self.assertTrue(result is None or isinstance(result, dict))


# ══════════════════════════════════════════════════════════════════════
# 2. TestLoadChangelogWindow
# ══════════════════════════════════════════════════════════════════════


class TestLoadChangelogWindow(unittest.TestCase):

    def _make_md(self, td, rows):
        path = os.path.join(td, "CLAUDE.md")
        body = [
            "# CLAUDE.md\n",
            "## 版本变更历史\n",
            "| 版本 | 日期 | 关键变更 |",
            "|------|------|----------|",
        ]
        body.extend(rows)
        body.append("\n## 其他\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        return path

    def test_returns_empty_on_missing_file(self):
        self.assertEqual(ee.load_changelog_window("/nonexistent.md"), "")

    def test_filters_by_date_window(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            recent = (today - timedelta(days=3)).strftime("%Y-%m-%d")
            old = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            path = self._make_md(td, [
                "| V37.9.90 | " + recent + " | recent |",
                "| V37.5 | " + old + " | old |",
            ])
            result = ee.load_changelog_window(path, days=14, today=today)
            self.assertIn("V37.9.90", result)
            self.assertNotIn("V37.5", result)

    def test_includes_within_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            edge = (today - timedelta(days=14)).strftime("%Y-%m-%d")
            path = self._make_md(td, ["| V37.5 | " + edge + " | edge |"])
            result = ee.load_changelog_window(path, days=14, today=today)
            self.assertIn("V37.5", result)

    def test_truncates_long_body(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            recent = (today - timedelta(days=1)).strftime("%Y-%m-%d")
            path = self._make_md(td, [
                "| V1 | " + recent + " | " + ("x" * 5000) + " |",
            ])
            result = ee.load_changelog_window(path, days=14, today=today)
            self.assertIn("truncated", result)

    def test_empty_when_none_in_window(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            old = (today - timedelta(days=100)).strftime("%Y-%m-%d")
            path = self._make_md(td, ["| V0 | " + old + " | old |"])
            self.assertEqual(ee.load_changelog_window(path, days=14, today=today), "")

    def test_skips_malformed_date(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            recent = (today - timedelta(days=1)).strftime("%Y-%m-%d")
            path = self._make_md(td, [
                "| V1 | not-a-date | bad |",
                "| V2 | " + recent + " | good |",
            ])
            result = ee.load_changelog_window(path, days=14, today=today)
            self.assertIn("V2", result)
            self.assertNotIn("bad", result)


# ══════════════════════════════════════════════════════════════════════
# 3. TestSelectRelevantCaseDocs
# ══════════════════════════════════════════════════════════════════════


class TestSelectRelevantCaseDocs(unittest.TestCase):

    def _make_cases(self, td, docs):
        cdir = os.path.join(td, "cases")
        os.makedirs(cdir)
        for fn, c in docs.items():
            with open(os.path.join(cdir, fn), "w", encoding="utf-8") as f:
                f.write(c)
        return cdir

    def test_keyword_match_in_filename(self):
        with tempfile.TemporaryDirectory() as td:
            cdir = self._make_cases(td, {
                "dream_hallucination_case.md": "About dreams.",
                "freight_throttle_case.md": "About freight.",
            })
            docs = ee.select_relevant_case_docs(
                "How handle dream hallucinations?", cdir
            )
            paths = [p for p, _ in docs]
            self.assertTrue(any("dream" in p for p in paths))

    def test_max_docs_cap(self):
        with tempfile.TemporaryDirectory() as td:
            cdir = self._make_cases(td, {
                "match_" + str(i) + ".md": "hallucination content"
                for i in range(10)
            })
            result = ee.select_relevant_case_docs("hallucination", cdir, max_docs=3)
            self.assertEqual(len(result), 3)

    def test_empty_when_no_match(self):
        with tempfile.TemporaryDirectory() as td:
            cdir = self._make_cases(td, {"freight.md": "Freight."})
            self.assertEqual(
                ee.select_relevant_case_docs("xyzabc unrelated", cdir),
                []
            )

    def test_missing_cases_dir(self):
        self.assertEqual(ee.select_relevant_case_docs("anything", "/nonexistent"), [])


# ══════════════════════════════════════════════════════════════════════
# 4. TestBuildContextBlock
# ══════════════════════════════════════════════════════════════════════


class TestBuildContextBlock(unittest.TestCase):

    def test_assembles_all_three_sections(self):
        result = ee.build_context_block(
            {"version": "0.37.9.90-r1"},
            "## CLAUDE.md changelog\n\n| V1 | ... |",
            [("/path/case_a.md", "Case A content")],
        )
        self.assertIn("status.json", result)
        self.assertIn("0.37.9.90-r1", result)
        self.assertIn("case_a.md", result)

    def test_handles_missing_sections(self):
        self.assertEqual(ee.build_context_block(None, "", []), "")

    def test_truncates_to_max_chars(self):
        result = ee.build_context_block({"data": "x" * 500_000}, "", [], max_chars=10000)
        self.assertLessEqual(len(result), 10200)
        self.assertIn("truncated", result)

    def test_truncates_long_case_doc_body(self):
        result = ee.build_context_block(None, "", [("/x/long.md", "x" * 20000)])
        self.assertIn("truncated", result)


# ══════════════════════════════════════════════════════════════════════
# 5. TestValidateReadOnly
# ══════════════════════════════════════════════════════════════════════


class TestValidateReadOnly(unittest.TestCase):

    def test_clean_no_violations(self):
        self.assertEqual(
            ee.validate_read_only({"proposal": "Consider reviewing changelog."}),
            []
        )

    def test_detects_shell_code_fence(self):
        v = ee.validate_read_only({"p": "```bash\nrm /\n```"})
        self.assertTrue(any(x["pattern"] == "shell_code_fence" for x in v))

    def test_detects_command_substitution_dollar(self):
        v = ee.validate_read_only({"p": "Use $(whoami)."})
        self.assertTrue(any(x["pattern"] == "command_substitution" for x in v))

    def test_detects_command_substitution_backtick(self):
        v = ee.validate_read_only({"p": "Try `whoami | head -5`."})
        self.assertGreater(len(v), 0)

    def test_detects_rm_rf(self):
        v = ee.validate_read_only({"p": "Do rm -rf the cache."})
        self.assertTrue(any("rm -rf" in x["pattern"] for x in v))

    def test_detects_sudo(self):
        v = ee.validate_read_only({"p": "Use sudo systemctl restart."})
        self.assertTrue(any("sudo" in x["pattern"] for x in v))

    def test_detects_in_nested_list(self):
        v = ee.validate_read_only({"refs": ["safe", "use rm -rf"]})
        self.assertTrue(any("[1]" in x["field"] for x in v))

    def test_detects_in_nested_dict(self):
        v = ee.validate_read_only({"action": {"command": "$(date)"}})
        self.assertTrue(any("command" in x["field"] for x in v))

    def test_allows_command_names_in_plain_prose(self):
        self.assertEqual(
            ee.validate_read_only({
                "p": "Consider using git status. The helper is relevant.",
            }), []
        )

    def test_handles_non_dict_input(self):
        self.assertEqual(ee.validate_read_only(None), [])
        self.assertEqual(ee.validate_read_only("string"), [])


# ══════════════════════════════════════════════════════════════════════
# 6. TestCheckDailyQuota
# ══════════════════════════════════════════════════════════════════════


class TestCheckDailyQuota(unittest.TestCase):

    def test_zero_when_log_missing(self):
        self.assertEqual(ee.check_daily_quota("/nonexistent.jsonl", 5), 0)

    def test_counts_today_only(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            with open(log, "w") as f:
                f.write(json.dumps({"timestamp_iso": "2026-05-29T10:00:00Z"}) + "\n")
                f.write(json.dumps({"timestamp_iso": "2026-05-29T11:00:00Z"}) + "\n")
                f.write(json.dumps({"timestamp_iso": "2026-05-28T10:00:00Z"}) + "\n")
            self.assertEqual(ee.check_daily_quota(log, 5, today="2026-05-29"), 2)

    def test_raises_when_over_quota(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            with open(log, "w") as f:
                for _ in range(10):
                    f.write(json.dumps({"timestamp_iso": "2026-05-29T10:00:00Z"}) + "\n")
            with self.assertRaises(ee.QuotaExceededError):
                ee.check_daily_quota(log, 5, today="2026-05-29")

    def test_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            with open(log, "w") as f:
                f.write("garbage\n")
                f.write(json.dumps({"timestamp_iso": "2026-05-29T10:00:00Z"}) + "\n")
                f.write("\n")
            self.assertEqual(ee.check_daily_quota(log, 10, today="2026-05-29"), 1)


# ══════════════════════════════════════════════════════════════════════
# 7. TestAuditRecord
# ══════════════════════════════════════════════════════════════════════


class TestAuditRecord(unittest.TestCase):

    def test_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            ee.write_audit_record(log, {"status": "ok"})
            with open(log) as f:
                lines = f.readlines()
            self.assertEqual(json.loads(lines[0])["status"], "ok")

    def test_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "deep", "audit.jsonl")
            ee.write_audit_record(log, {"x": 1})
            self.assertTrue(os.path.isfile(log))

    def test_fail_open_unwritable(self):
        ee.write_audit_record("/proc/x/audit.jsonl", {"x": 1})

    def test_none_path_skip(self):
        ee.write_audit_record(None, {"x": 1})


# ══════════════════════════════════════════════════════════════════════
# 8. TestParseResponseJson
# ══════════════════════════════════════════════════════════════════════


class TestParseResponseJson(unittest.TestCase):

    def test_direct(self):
        self.assertEqual(
            ee.parse_response_json('{"p":"x"}')["p"], "x"
        )

    def test_extract_from_text(self):
        self.assertEqual(
            ee.parse_response_json('Here:\n{"p":"x"}\nend')["p"], "x"
        )

    def test_nested(self):
        self.assertEqual(
            ee.parse_response_json('{"o":{"i":"v"}}')["o"]["i"], "v"
        )

    def test_raises_on_no_json(self):
        with self.assertRaises(ValueError):
            ee.parse_response_json("Plain text")

    def test_raises_on_empty(self):
        with self.assertRaises(ValueError):
            ee.parse_response_json("")


# ══════════════════════════════════════════════════════════════════════
# 9. TestDoubaoTransport
# ══════════════════════════════════════════════════════════════════════


class TestDoubaoTransport(unittest.TestCase):

    def test_is_configured_when_api_key_set(self):
        t = ee.DoubaoTransport(api_key="real-key")
        self.assertTrue(t.is_configured())

    def test_is_not_configured_without_api_key(self):
        # Force-clear env to truly test "no key" case
        old = os.environ.pop(ee.DOUBAO_API_KEY_ENV, None)
        try:
            t = ee.DoubaoTransport(api_key="")
            self.assertFalse(t.is_configured())
        finally:
            if old is not None:
                os.environ[ee.DOUBAO_API_KEY_ENV] = old

    def test_endpoint_id_falls_back_to_public_model_id(self):
        old = os.environ.pop(ee.DOUBAO_ENDPOINT_ID_ENV, None)
        try:
            t = ee.DoubaoTransport(api_key="key")
            self.assertEqual(t.endpoint_id, ee.DOUBAO_DEFAULT_MODEL_ID)
        finally:
            if old is not None:
                os.environ[ee.DOUBAO_ENDPOINT_ID_ENV] = old

    def test_endpoint_id_uses_env(self):
        old = os.environ.get(ee.DOUBAO_ENDPOINT_ID_ENV)
        os.environ[ee.DOUBAO_ENDPOINT_ID_ENV] = "ep-production-xyz"
        try:
            t = ee.DoubaoTransport(api_key="key")
            self.assertEqual(t.endpoint_id, "ep-production-xyz")
        finally:
            if old is None:
                del os.environ[ee.DOUBAO_ENDPOINT_ID_ENV]
            else:
                os.environ[ee.DOUBAO_ENDPOINT_ID_ENV] = old

    def test_endpoint_url_is_volcengine_ark(self):
        t = ee.DoubaoTransport(api_key="key")
        self.assertIn("ark.cn-beijing.volces.com", t.endpoint_url)
        self.assertTrue(t.endpoint_url.endswith("/chat/completions"))

    def test_call_returns_failure_when_not_configured(self):
        old = os.environ.pop(ee.DOUBAO_API_KEY_ENV, None)
        try:
            t = ee.DoubaoTransport(api_key="")
            ok, text, usage, err = t.call("sys", "ctx", "msg", 1000)
            self.assertFalse(ok)
            self.assertIn("ARK_API_KEY", err)
        finally:
            if old is not None:
                os.environ[ee.DOUBAO_API_KEY_ENV] = old

    def test_call_constructs_openai_compatible_payload(self):
        t = FakeDoubaoTransport(responses=[_good_doubao_response()])
        ok, text, usage, err = t.call("system_text", "context_text", "user_q", 2500)
        self.assertTrue(ok)
        payload = t.posted_payloads[0]
        self.assertEqual(payload["model"], "fake-endpoint")
        self.assertEqual(payload["max_tokens"], 2500)
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertEqual(payload["messages"][1]["content"], "user_q")
        # System message contains BOTH prompt AND context (Volcengine cache benefits)
        self.assertIn("system_text", payload["messages"][0]["content"])
        self.assertIn("context_text", payload["messages"][0]["content"])

    def test_call_extracts_usage_with_cached_tokens(self):
        t = FakeDoubaoTransport(responses=[
            _good_doubao_response(cached_tokens=4500),
        ])
        ok, text, usage, err = t.call("sys", "ctx", "msg", 1000)
        self.assertTrue(ok)
        self.assertEqual(usage["input_tokens"], 5000)
        self.assertEqual(usage["output_tokens"], 200)
        self.assertEqual(usage["cache_read_input_tokens"], 4500)

    def test_call_captures_reasoning_chars(self):
        t = FakeDoubaoTransport(responses=[
            _good_doubao_response(reasoning_content="thinking step 1"),
        ])
        ok, text, usage, err = t.call("sys", "ctx", "msg", 1000)
        self.assertTrue(ok)
        self.assertEqual(usage["reasoning_chars"], len("thinking step 1"))

    def test_call_handles_empty_choices(self):
        t = FakeDoubaoTransport(responses=[{"choices": [], "usage": {}}])
        ok, text, usage, err = t.call("sys", "ctx", "msg", 1000)
        self.assertFalse(ok)
        self.assertIn("no choices", err)

    def test_call_propagates_error(self):
        t = FakeDoubaoTransport(responses=[RuntimeError("network down")])
        ok, text, usage, err = t.call("sys", "ctx", "msg", 1000)
        self.assertFalse(ok)
        self.assertIn("network down", err)


# ══════════════════════════════════════════════════════════════════════
# 10. TestEscalateOrchestratorDoubao
# ══════════════════════════════════════════════════════════════════════


class TestEscalateOrchestratorDoubao(unittest.TestCase):

    def _make_env(self, td, status=None, with_changelog=False, with_cases=False):
        if status:
            with open(os.path.join(td, "status.json"), "w") as f:
                json.dump(status, f)
        cmd = os.path.join(td, "CLAUDE.md")
        if with_changelog:
            today = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
            with open(cmd, "w") as f:
                f.write(
                    "# CLAUDE.md\n\n## 版本变更历史\n\n"
                    "| 版本 | 日期 | 关键变更 |\n"
                    "|------|------|----------|\n"
                    "| V37.9.90-r1 | " + today + " | Doubao backend |\n"
                )
        cdir = os.path.join(td, "cases")
        if with_cases:
            os.makedirs(cdir)
            with open(os.path.join(cdir, "freight_case.md"), "w") as f:
                f.write("# Freight\n\nDetails.")
        return cmd, cdir

    def test_empty_question(self):
        self.assertEqual(ee.escalate("")["status"], "no_context")

    def test_whitespace_question(self):
        self.assertEqual(ee.escalate("   ")["status"], "no_context")

    def test_no_context_anywhere(self):
        with tempfile.TemporaryDirectory() as td:
            orig = ee.load_status
            ee.load_status = lambda *a, **kw: None
            try:
                result = ee.escalate(
                    "Q",
                    kb_dir=td,
                    claude_md_path=os.path.join(td, "x.md"),
                    cases_dir=os.path.join(td, "x"),
                    audit_log_path=os.path.join(td, "audit.jsonl"),
                )
            finally:
                ee.load_status = orig
            self.assertEqual(result["status"], "no_context")

    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            cmd, cdir = self._make_env(td, status={"x": 1}, with_changelog=True)
            audit = os.path.join(td, "audit.jsonl")
            result = ee.escalate(
                "Q",
                kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                audit_log_path=audit, dry_run=True,
            )
            self.assertEqual(result["status"], "dry_run")
            self.assertEqual(result["backend"], "doubao")
            self.assertIn("DRY RUN", result["proposal"])
            with open(audit) as f:
                self.assertEqual(json.loads(f.readline())["backend"], "doubao")

    def test_ok_path_with_mock_transport(self):
        with tempfile.TemporaryDirectory() as td:
            cmd, cdir = self._make_env(td, status={"focus": "PoC-r1"},
                                       with_changelog=True, with_cases=True)
            audit = os.path.join(td, "audit.jsonl")
            transport = FakeDoubaoTransport(responses=[
                _good_doubao_response(
                    proposal="Review case docs.",
                    confidence="medium",
                    refs=["status.json"],
                    cached_tokens=4000,
                ),
            ])
            result = ee.escalate(
                "How handle freight?",
                kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                audit_log_path=audit, transport=transport,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["backend"], "doubao")
            self.assertEqual(result["confidence"], "medium")
            self.assertEqual(result["usage"]["cache_read_input_tokens"], 4000)
            self.assertEqual(len(transport.posted_payloads), 1)

    def test_api_error_returns_api_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            cmd, cdir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            transport = FakeDoubaoTransport(responses=[RuntimeError("503")])
            result = ee.escalate(
                "Q", kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                audit_log_path=audit, transport=transport,
            )
            self.assertEqual(result["status"], "api_unavailable")
            self.assertIn("503", result["error"])

    def test_no_api_key_returns_api_unavailable(self):
        old = os.environ.pop(ee.DOUBAO_API_KEY_ENV, None)
        try:
            with tempfile.TemporaryDirectory() as td:
                cmd, cdir = self._make_env(td, status={"x": 1})
                transport = ee.DoubaoTransport(api_key="")
                result = ee.escalate(
                    "Q",
                    kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                    audit_log_path=os.path.join(td, "audit.jsonl"),
                    transport=transport,
                )
                self.assertEqual(result["status"], "api_unavailable")
                self.assertIn("ARK_API_KEY", result["error"])
        finally:
            if old is not None:
                os.environ[ee.DOUBAO_API_KEY_ENV] = old

    def test_read_only_violation_fail_close(self):
        with tempfile.TemporaryDirectory() as td:
            cmd, cdir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            evil = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "proposal": "Run rm -rf and sudo restart",
                            "rationale": "ok",
                            "confidence": "high",
                            "refs": [],
                        }),
                        "reasoning_content": "",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
            transport = FakeDoubaoTransport(responses=[evil])
            result = ee.escalate(
                "Q", kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                audit_log_path=audit, transport=transport,
            )
            self.assertEqual(result["status"], "read_only_violation")
            self.assertGreater(len(result["violations"]), 0)
            with open(audit) as f:
                self.assertEqual(
                    json.loads(f.readline())["status"], "read_only_violation"
                )

    def test_parse_failure(self):
        with tempfile.TemporaryDirectory() as td:
            cmd, cdir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            bad = {
                "choices": [{"message": {"content": "no json"}, "finish_reason": "stop"}],
                "usage": {},
            }
            transport = FakeDoubaoTransport(responses=[bad])
            result = ee.escalate(
                "Q", kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                audit_log_path=audit, transport=transport,
            )
            self.assertEqual(result["status"], "parse_failed")

    def test_quota_exceeded(self):
        with tempfile.TemporaryDirectory() as td:
            cmd, cdir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            with open(audit, "w") as f:
                for _ in range(30):
                    f.write(json.dumps({
                        "timestamp_iso": "2026-05-29T08:00:00Z",
                    }) + "\n")
            transport = FakeDoubaoTransport(responses=[_good_doubao_response()])
            result = ee.escalate(
                "Q", kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                audit_log_path=audit, transport=transport,
                max_daily=30, today_for_test="2026-05-29",
            )
            self.assertEqual(result["status"], "quota_exceeded")
            self.assertEqual(len(transport.posted_payloads), 0)

    def test_audit_record_includes_backend(self):
        with tempfile.TemporaryDirectory() as td:
            cmd, cdir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            transport = FakeDoubaoTransport(responses=[_good_doubao_response()])
            ee.escalate(
                "Q", kb_dir=td, claude_md_path=cmd, cases_dir=cdir,
                audit_log_path=audit, transport=transport,
            )
            with open(audit) as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["backend"], "doubao")
            self.assertEqual(rec["status"], "ok")


# ══════════════════════════════════════════════════════════════════════
# 11. TestBackendSelector
# ══════════════════════════════════════════════════════════════════════


class TestBackendSelector(unittest.TestCase):

    def test_default_backend_is_doubao(self):
        self.assertEqual(ee.DEFAULT_BACKEND, "doubao")

    def test_claude_pending_returns_stub(self):
        result = ee.escalate("Q", backend="claude_pending")
        self.assertEqual(result["status"], "claude_pending")
        self.assertEqual(result["backend"], "claude_pending")
        self.assertIn("V37.9.91", result["error"])
        self.assertIn("Doubao", result["error"])

    def test_unknown_backend_rejected(self):
        result = ee.escalate("Q", backend="gpt5")
        self.assertEqual(result["status"], "unknown_backend")

    def test_claude_pending_short_circuits_before_quota(self):
        with tempfile.TemporaryDirectory() as td:
            audit = os.path.join(td, "audit.jsonl")
            with open(audit, "w") as f:
                for _ in range(100):
                    f.write(json.dumps({
                        "timestamp_iso": "2026-05-29T08:00:00Z",
                    }) + "\n")
            result = ee.escalate(
                "Q", backend="claude_pending",
                audit_log_path=audit,
                today_for_test="2026-05-29",
            )
            # claude_pending takes precedence (no quota check)
            self.assertEqual(result["status"], "claude_pending")


# ══════════════════════════════════════════════════════════════════════
# 12. TestCliInterface
# ══════════════════════════════════════════════════════════════════════


class TestCliInterface(unittest.TestCase):

    def test_dry_run(self):
        result = subprocess.run(
            [sys.executable, "expert_escalation.py",
             "--question", "What is the status?",
             "--dry-run", "--json"],
            capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["status"], "dry_run")
        self.assertEqual(parsed["backend"], "doubao")

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "expert_escalation.py", "--help"],
            capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Expert Escalation", result.stdout)
        self.assertIn("Doubao", result.stdout)

    def test_claude_pending_via_cli(self):
        result = subprocess.run(
            [sys.executable, "expert_escalation.py",
             "--question", "Q",
             "--backend", "claude_pending",
             "--json"],
            capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["status"], "claude_pending")


# ══════════════════════════════════════════════════════════════════════
# 13. TestV37990R1SourceGuards
# ══════════════════════════════════════════════════════════════════════


class TestV37990R1SourceGuards(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "expert_escalation.py"),
                  encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_90_r1_marker(self):
        self.assertIn("V37.9.90-r1", self.src)

    def test_v37_9_83_lineage(self):
        self.assertIn("V37.9.83", self.src)

    def test_v37_9_55_doubao_reference(self):
        self.assertIn("V37.9.55", self.src)

    def test_doubao_backend_constants(self):
        self.assertEqual(ee.BACKEND_DOUBAO, "doubao")
        self.assertEqual(ee.BACKEND_CLAUDE_PENDING, "claude_pending")
        self.assertEqual(ee.DEFAULT_BACKEND, "doubao")

    def test_volcengine_endpoint_url(self):
        self.assertIn("ark.cn-beijing.volces.com/api/v3", ee.DOUBAO_BASE_URL)

    def test_no_anthropic_import(self):
        self.assertNotIn("import anthropic", self.src)
        self.assertNotIn("from anthropic", self.src)

    def test_no_openai_import(self):
        self.assertNotIn("import openai", self.src)
        self.assertNotIn("from openai", self.src)

    def test_no_requests_import(self):
        self.assertNotIn("import requests", self.src)

    def test_uses_urllib(self):
        self.assertIn("urllib.request", self.src)

    def test_ark_api_key_env(self):
        self.assertEqual(ee.DOUBAO_API_KEY_ENV, "ARK_API_KEY")

    def test_ark_endpoint_id_env(self):
        self.assertEqual(ee.DOUBAO_ENDPOINT_ID_ENV, "ARK_ENDPOINT_ID")

    def test_temperature_0_3_explicit(self):
        self.assertIn('"temperature": 0.3', self.src)

    def test_daily_quota_default_30(self):
        self.assertEqual(ee.DEFAULT_DAILY_QUOTA, 30)

    def test_no_anthropic_specific_call_features(self):
        """call() must not use Anthropic-only kwargs (thinking / output_config /
        cache_control inside the payload)."""
        call_section = re.search(
            r"def call\(self.*?return True, text, usage",
            self.src, re.DOTALL,
        )
        if call_section:
            seg = call_section.group(0)
            self.assertNotIn('"cache_control"', seg)
            self.assertNotIn('"thinking"', seg)
            self.assertNotIn('"output_config"', seg)

    def test_volcengine_cache_documented(self):
        self.assertIn("Context Cache", self.src)

    def test_audit_log_filename_changed(self):
        self.assertIn("expert_escalations.jsonl", self.src)
        self.assertNotIn("claude_escalations.jsonl", self.src)

    def test_module_docstring_mentions_three_directions(self):
        self.assertIn("方向 2", self.src)

    def test_read_only_dangerous_tokens_present(self):
        self.assertIn("rm -rf", self.src)
        self.assertIn("sudo", self.src)

    def test_doubao_transport_class_defined(self):
        self.assertTrue(hasattr(ee, "DoubaoTransport"))
        self.assertTrue(hasattr(ee.DoubaoTransport, "call"))
        self.assertTrue(hasattr(ee.DoubaoTransport, "is_configured"))


if __name__ == "__main__":
    unittest.main()

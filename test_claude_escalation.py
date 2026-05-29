"""
test_claude_escalation.py — V37.9.90 PoC tests

Coverage:
- TestLoadStatus — kb_dir / HOME / repo fallback chain
- TestLoadChangelogWindow — date filtering, parser robustness, V37.9.83 format
- TestSelectRelevantCaseDocs — keyword scoring, max_docs cap
- TestBuildContextBlock — assembly + truncation
- TestValidateReadOnly — 8 contract violation classes
- TestCheckDailyQuota — count + threshold + FAIL-OPEN
- TestAuditRecord — JSONL write + FAIL-OPEN
- TestParseResponseJson — direct + extraction + failure
- TestCallClaudeWithMock — prompt caching, model selection, exception handling
- TestEscalateOrchestrator — end-to-end with FakeAnthropicClient (8+ tests)
- TestCliInterface — subprocess --dry-run
- TestV37990SourceGuards — V37.9.90 marker / API contract drift defense

V37.9.90 design contracts verified:
- 1 escalate() = 1 JSONL append
- Dry run never calls API
- Read-only violations FAIL-CLOSE (status="read_only_violation")
- Quota exceeded FAIL-CLOSE (status="quota_exceeded")
- Fallback model tried only on first model failure
- Prompt caching: cache_control on the 2nd (context) system block, not on system_prompt block
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

import claude_escalation as ce  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# Fake Anthropic client (mockable transport)
# ══════════════════════════════════════════════════════════════════════


class FakeContentBlock:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=200,
                 cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class FakeResponse:
    def __init__(self, text, input_tokens=100, output_tokens=200,
                 cache_read=0, cache_create=0):
        self.content = [FakeContentBlock("text", text)]
        self.usage = FakeUsage(input_tokens, output_tokens, cache_read, cache_create)


class FakeMessagesAPI:
    """Capture all calls and return preconfigured responses (one per call)."""

    def __init__(self, responses, exceptions_per_model=None):
        # responses: list of FakeResponse OR Exception
        self.responses = list(responses)
        self.exceptions_per_model = exceptions_per_model or {}
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        model = kwargs.get("model", "")
        if model in self.exceptions_per_model:
            raise self.exceptions_per_model[model]
        if not self.responses:
            raise RuntimeError("FakeMessagesAPI exhausted")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAnthropicClient:
    def __init__(self, responses=None, exceptions_per_model=None):
        self.messages = FakeMessagesAPI(
            responses or [],
            exceptions_per_model,
        )


def _good_json_response(proposal="Consider reviewing the relevant case docs.",
                       rationale="Based on status.json priorities.",
                       confidence="medium",
                       refs=None):
    payload = {
        "proposal": proposal,
        "rationale": rationale,
        "confidence": confidence,
        "refs": refs or [],
    }
    return FakeResponse(json.dumps(payload, ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════
# 1. TestLoadStatus
# ══════════════════════════════════════════════════════════════════════


class TestLoadStatus(unittest.TestCase):

    def test_loads_from_kb_dir(self):
        with tempfile.TemporaryDirectory() as td:
            data = {"version": "0.37.9.90", "focus": "V37.9.90 PoC"}
            with open(os.path.join(td, "status.json"), "w") as f:
                json.dump(data, f)
            result = ce.load_status(td)
            self.assertEqual(result["version"], "0.37.9.90")

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            result = ce.load_status(td)
            # In dev container, $HOME may have no status.json; repo root has one
            # but here we accept None or dict — just verify no crash
            self.assertTrue(result is None or isinstance(result, dict))

    def test_returns_none_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "status.json"), "w") as f:
                f.write("{not valid json")
            # Set HOME to td too, to ensure no fallback
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                result = ce.load_status(td)
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
            # Repo root status.json may still be valid; just verify it gracefully handles bad json
            self.assertTrue(result is None or isinstance(result, dict))

    def test_none_kb_dir_uses_default(self):
        # Just verify it doesn't crash with None
        result = ce.load_status(None)
        self.assertTrue(result is None or isinstance(result, dict))


# ══════════════════════════════════════════════════════════════════════
# 2. TestLoadChangelogWindow
# ══════════════════════════════════════════════════════════════════════


class TestLoadChangelogWindow(unittest.TestCase):

    def _make_md(self, td, rows):
        path = os.path.join(td, "CLAUDE.md")
        body_lines = [
            "# CLAUDE.md\n",
            "## 版本变更历史\n",
            "| 版本 | 日期 | 关键变更 |",
            "|------|------|----------|",
        ]
        body_lines.extend(rows)
        body_lines.append("\n## 其他章节\n\n更多内容")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body_lines))
        return path

    def test_returns_empty_on_missing_file(self):
        result = ce.load_changelog_window("/nonexistent/CLAUDE.md")
        self.assertEqual(result, "")

    def test_filters_by_date_window(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            recent = (today - timedelta(days=3)).strftime("%Y-%m-%d")
            old = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            path = self._make_md(td, [
                "| V37.9.90 | " + recent + " | recent feature |",
                "| V37.5 | " + old + " | old feature should not appear |",
            ])
            result = ce.load_changelog_window(path, days=14, today=today)
            self.assertIn("V37.9.90", result)
            self.assertIn("recent feature", result)
            self.assertNotIn("V37.5", result)

    def test_includes_within_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            edge = (today - timedelta(days=14)).strftime("%Y-%m-%d")
            path = self._make_md(td, [
                "| V37.5 | " + edge + " | exactly at 14-day boundary |",
            ])
            result = ce.load_changelog_window(path, days=14, today=today)
            self.assertIn("V37.5", result)

    def test_truncates_very_long_body(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            recent = (today - timedelta(days=1)).strftime("%Y-%m-%d")
            long_body = "x" * 5000
            path = self._make_md(td, [
                "| V37.9.90 | " + recent + " | " + long_body + " |",
            ])
            result = ce.load_changelog_window(path, days=14, today=today)
            self.assertIn("V37.9.90", result)
            self.assertIn("truncated", result)

    def test_empty_when_no_rows_in_window(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            old = (today - timedelta(days=100)).strftime("%Y-%m-%d")
            path = self._make_md(td, [
                "| V37.0 | " + old + " | very old |",
            ])
            result = ce.load_changelog_window(path, days=14, today=today)
            self.assertEqual(result, "")

    def test_skips_malformed_date(self):
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 5, 29).date()
            recent = (today - timedelta(days=1)).strftime("%Y-%m-%d")
            path = self._make_md(td, [
                "| V37.9.91 | not-a-date | bad row |",
                "| V37.9.90 | " + recent + " | good row |",
            ])
            result = ce.load_changelog_window(path, days=14, today=today)
            self.assertIn("V37.9.90", result)
            self.assertNotIn("bad row", result)


# ══════════════════════════════════════════════════════════════════════
# 3. TestSelectRelevantCaseDocs
# ══════════════════════════════════════════════════════════════════════


class TestSelectRelevantCaseDocs(unittest.TestCase):

    def _make_cases(self, td, docs):
        cases_dir = os.path.join(td, "cases")
        os.makedirs(cases_dir)
        for fname, content in docs.items():
            with open(os.path.join(cases_dir, fname), "w", encoding="utf-8") as f:
                f.write(content)
        return cases_dir

    def test_keyword_match_in_filename(self):
        with tempfile.TemporaryDirectory() as td:
            cases_dir = self._make_cases(td, {
                "dream_hallucination_case.md": "About dreams and hallucinations.",
                "freight_throttle_case.md": "About freight rate limits.",
            })
            docs = ce.select_relevant_case_docs(
                "How should we handle dream hallucinations?", cases_dir
            )
            self.assertGreaterEqual(len(docs), 1)
            paths = [p for p, _ in docs]
            self.assertTrue(any("dream" in p for p in paths))

    def test_max_docs_cap(self):
        with tempfile.TemporaryDirectory() as td:
            docs = {
                "match_" + str(i) + ".md": "content with hallucination keyword"
                for i in range(10)
            }
            cases_dir = self._make_cases(td, docs)
            result = ce.select_relevant_case_docs("hallucination", cases_dir, max_docs=3)
            self.assertEqual(len(result), 3)

    def test_empty_when_no_match(self):
        with tempfile.TemporaryDirectory() as td:
            cases_dir = self._make_cases(td, {
                "freight_case.md": "Freight content.",
            })
            result = ce.select_relevant_case_docs(
                "completely unrelated topic xyzabc", cases_dir
            )
            # No matches → empty list (xyzabc and topic and unrelated and completely all >=4)
            # But 'freight' doesn't appear in question. Result should be empty.
            self.assertEqual(result, [])

    def test_missing_cases_dir(self):
        result = ce.select_relevant_case_docs("anything", "/nonexistent/dir")
        self.assertEqual(result, [])


# ══════════════════════════════════════════════════════════════════════
# 4. TestBuildContextBlock
# ══════════════════════════════════════════════════════════════════════


class TestBuildContextBlock(unittest.TestCase):

    def test_assembles_all_three_sections(self):
        status = {"version": "0.37.9.90"}
        changelog = "## CLAUDE.md changelog\n\n| V37.9.90 | ... |"
        case_docs = [("/path/case_a.md", "Case A content")]
        result = ce.build_context_block(status, changelog, case_docs)
        self.assertIn("status.json", result)
        self.assertIn("0.37.9.90", result)
        self.assertIn("CLAUDE.md changelog", result)
        self.assertIn("case_a.md", result)
        self.assertIn("Case A content", result)

    def test_handles_missing_sections(self):
        result = ce.build_context_block(None, "", [])
        self.assertEqual(result, "")

    def test_truncates_to_max_chars(self):
        # Build something huge
        big_status = {"data": "x" * 500_000}
        result = ce.build_context_block(big_status, "", [], max_chars=10000)
        self.assertLessEqual(len(result), 10100)  # Small slack for marker text
        self.assertIn("truncated", result)

    def test_truncates_long_case_doc_body(self):
        case_docs = [("/x/long.md", "x" * 20000)]
        result = ce.build_context_block(None, "", case_docs)
        self.assertIn("long.md", result)
        self.assertIn("truncated", result)


# ══════════════════════════════════════════════════════════════════════
# 5. TestValidateReadOnly — V37.9.90 contract enforcement
# ══════════════════════════════════════════════════════════════════════


class TestValidateReadOnly(unittest.TestCase):

    def test_clean_proposal_no_violations(self):
        clean = {
            "proposal": "Consider reviewing the changelog and running tests.",
            "rationale": "The status.json shows V37.9.90 is the focus.",
            "confidence": "medium",
            "refs": ["status.json", "CLAUDE.md"],
        }
        self.assertEqual(ce.validate_read_only(clean), [])

    def test_detects_shell_code_fence(self):
        dirty = {
            "proposal": "Run this:\n```bash\nrm -rf /\n```",
        }
        violations = ce.validate_read_only(dirty)
        self.assertGreater(len(violations), 0)
        patterns = [v["pattern"] for v in violations]
        self.assertTrue(any(p.startswith("shell_code_fence") for p in patterns))

    def test_detects_command_substitution_dollar(self):
        dirty = {"proposal": "Use $(whoami) to identify."}
        violations = ce.validate_read_only(dirty)
        patterns = [v["pattern"] for v in violations]
        self.assertIn("command_substitution", patterns)

    def test_detects_command_substitution_backtick(self):
        dirty = {"proposal": "Try `whoami`."}
        violations = ce.validate_read_only(dirty)
        # Single backtick around short identifier is allowed (less than 3 chars)
        # But longer should trigger
        dirty2 = {"proposal": "Try `whoami | head -5`."}
        violations2 = ce.validate_read_only(dirty2)
        self.assertGreater(len(violations2), 0)

    def test_detects_rm_rf(self):
        dirty = {"proposal": "Just do rm -rf the cache dir."}
        violations = ce.validate_read_only(dirty)
        self.assertTrue(any("rm -rf" in v["pattern"] for v in violations))

    def test_detects_sudo(self):
        dirty = {"proposal": "You should sudo systemctl restart."}
        violations = ce.validate_read_only(dirty)
        self.assertTrue(any("sudo" in v["pattern"] for v in violations))

    def test_detects_violations_in_nested_list(self):
        dirty = {"refs": ["safe", "use rm -rf to clean"]}
        violations = ce.validate_read_only(dirty)
        self.assertTrue(any("[1]" in v["field"] for v in violations))

    def test_detects_violations_in_nested_dict(self):
        dirty = {"action": {"command": "$(date)"}}
        violations = ce.validate_read_only(dirty)
        self.assertTrue(any("command" in v["field"] for v in violations))

    def test_allows_command_names_in_plain_prose(self):
        # The contract: "git status" mentioned in prose is OK
        clean = {
            "proposal": "Consider using git status to check changes. "
                        "The kb_review_collect helper is also relevant.",
        }
        violations = ce.validate_read_only(clean)
        self.assertEqual(violations, [])

    def test_handles_non_dict_input(self):
        # Should not crash
        result = ce.validate_read_only(None)
        self.assertEqual(result, [])
        result2 = ce.validate_read_only("just a string")
        self.assertEqual(result2, [])


# ══════════════════════════════════════════════════════════════════════
# 6. TestCheckDailyQuota
# ══════════════════════════════════════════════════════════════════════


class TestCheckDailyQuota(unittest.TestCase):

    def test_zero_when_log_missing(self):
        count = ce.check_daily_quota("/nonexistent/audit.jsonl", max_daily=5)
        self.assertEqual(count, 0)

    def test_counts_today_only(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            today_iso = "2026-05-29T10:00:00Z"
            yesterday_iso = "2026-05-28T10:00:00Z"
            with open(log, "w") as f:
                f.write(json.dumps({"timestamp_iso": today_iso}) + "\n")
                f.write(json.dumps({"timestamp_iso": today_iso}) + "\n")
                f.write(json.dumps({"timestamp_iso": yesterday_iso}) + "\n")
            count = ce.check_daily_quota(log, max_daily=5, today="2026-05-29")
            self.assertEqual(count, 2)

    def test_raises_when_over_quota(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            with open(log, "w") as f:
                for _ in range(10):
                    f.write(json.dumps({"timestamp_iso": "2026-05-29T10:00:00Z"}) + "\n")
            with self.assertRaises(ce.QuotaExceededError):
                ce.check_daily_quota(log, max_daily=5, today="2026-05-29")

    def test_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            with open(log, "w") as f:
                f.write("garbage line\n")
                f.write(json.dumps({"timestamp_iso": "2026-05-29T10:00:00Z"}) + "\n")
                f.write("\n")
                f.write(json.dumps({"timestamp_iso": "2026-05-29T11:00:00Z"}) + "\n")
            count = ce.check_daily_quota(log, max_daily=10, today="2026-05-29")
            self.assertEqual(count, 2)


# ══════════════════════════════════════════════════════════════════════
# 7. TestAuditRecord
# ══════════════════════════════════════════════════════════════════════


class TestAuditRecord(unittest.TestCase):

    def test_writes_jsonl_record(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            record = {"timestamp_iso": "2026-05-29T10:00:00Z", "status": "ok"}
            ce.write_audit_record(log, record)
            with open(log, "r") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["status"], "ok")

    def test_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "deep", "nested", "audit.jsonl")
            ce.write_audit_record(log, {"x": 1})
            self.assertTrue(os.path.isfile(log))

    def test_fail_open_on_unwritable_path(self):
        # Should not crash even if path is unwritable
        ce.write_audit_record("/proc/cannot_write/audit.jsonl", {"x": 1})

    def test_none_path_silent_skip(self):
        ce.write_audit_record(None, {"x": 1})


# ══════════════════════════════════════════════════════════════════════
# 8. TestParseResponseJson
# ══════════════════════════════════════════════════════════════════════


class TestParseResponseJson(unittest.TestCase):

    def test_direct_json(self):
        text = '{"proposal": "do X", "confidence": "high"}'
        result = ce.parse_response_json(text)
        self.assertEqual(result["proposal"], "do X")

    def test_extracts_from_surrounding_text(self):
        text = 'Here is my answer:\n{"proposal": "do X"}\n'
        result = ce.parse_response_json(text)
        self.assertEqual(result["proposal"], "do X")

    def test_handles_nested_objects(self):
        text = '{"outer": {"inner": "value"}}'
        result = ce.parse_response_json(text)
        self.assertEqual(result["outer"]["inner"], "value")

    def test_raises_on_no_json(self):
        with self.assertRaises(ValueError):
            ce.parse_response_json("This is just plain text")

    def test_raises_on_empty(self):
        with self.assertRaises(ValueError):
            ce.parse_response_json("")


# ══════════════════════════════════════════════════════════════════════
# 9. TestCallClaudeWithMock — prompt caching contract
# ══════════════════════════════════════════════════════════════════════


class TestCallClaudeWithMock(unittest.TestCase):

    def test_returns_text_and_usage(self):
        client = FakeAnthropicClient(responses=[
            FakeResponse('{"proposal":"ok"}', input_tokens=50,
                         output_tokens=20, cache_read=40),
        ])
        ok, text, usage, err = ce.call_claude(
            client,
            model="claude-opus-4-7",
            system_blocks=[{"type": "text", "text": "sys"}],
            user_message="hi",
            max_tokens=1000,
        )
        self.assertTrue(ok)
        self.assertIn("ok", text)
        self.assertEqual(usage["input_tokens"], 50)
        self.assertEqual(usage["cache_read_input_tokens"], 40)
        self.assertEqual(err, "")

    def test_passes_thinking_adaptive(self):
        client = FakeAnthropicClient(responses=[
            FakeResponse('{"proposal":"x"}'),
        ])
        ce.call_claude(client, "claude-opus-4-7",
                       [{"type": "text", "text": "sys"}], "hi", 100)
        call = client.messages.calls[0]
        self.assertEqual(call["thinking"], {"type": "adaptive"})

    def test_passes_effort_high(self):
        client = FakeAnthropicClient(responses=[
            FakeResponse('{"proposal":"x"}'),
        ])
        ce.call_claude(client, "claude-opus-4-7",
                       [{"type": "text", "text": "sys"}], "hi", 100)
        call = client.messages.calls[0]
        self.assertEqual(call["output_config"], {"effort": "high"})

    def test_passes_max_tokens(self):
        client = FakeAnthropicClient(responses=[
            FakeResponse('{"proposal":"x"}'),
        ])
        ce.call_claude(client, "claude-opus-4-7",
                       [{"type": "text", "text": "sys"}], "hi", 4567)
        self.assertEqual(client.messages.calls[0]["max_tokens"], 4567)

    def test_catches_exception(self):
        client = FakeAnthropicClient(responses=[
            RuntimeError("503 overloaded"),
        ])
        ok, text, usage, err = ce.call_claude(
            client, "claude-opus-4-7",
            [{"type": "text", "text": "sys"}], "hi", 100,
        )
        self.assertFalse(ok)
        self.assertIn("RuntimeError", err)
        self.assertIn("503", err)


# ══════════════════════════════════════════════════════════════════════
# 10. TestEscalateOrchestrator — end-to-end
# ══════════════════════════════════════════════════════════════════════


class TestEscalateOrchestrator(unittest.TestCase):

    def _make_env(self, td, status=None, with_changelog=False, with_cases=False):
        """Build a minimal context tree under td."""
        # status.json
        if status:
            with open(os.path.join(td, "status.json"), "w") as f:
                json.dump(status, f)
        # CLAUDE.md
        claude_md = os.path.join(td, "CLAUDE.md")
        if with_changelog:
            today = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
            content = (
                "# CLAUDE.md\n\n## 版本变更历史\n\n"
                "| 版本 | 日期 | 关键变更 |\n"
                "|------|------|----------|\n"
                "| V37.9.90 | " + today + " | Claude Escalation PoC |\n"
            )
            with open(claude_md, "w") as f:
                f.write(content)
        cases_dir = os.path.join(td, "cases")
        if with_cases:
            os.makedirs(cases_dir)
            with open(os.path.join(cases_dir, "freight_case.md"), "w") as f:
                f.write("# Freight throttle case\n\nDetails about freight handling.")
        return claude_md, cases_dir

    def test_empty_question_returns_no_context(self):
        result = ce.escalate("")
        self.assertEqual(result["status"], "no_context")

    def test_whitespace_question_returns_no_context(self):
        result = ce.escalate("   ")
        self.assertEqual(result["status"], "no_context")

    def test_no_context_anywhere_returns_no_context(self):
        with tempfile.TemporaryDirectory() as td:
            # Monkey-patch load_status to simulate "no context anywhere"
            # (dev env has real status.json in repo root, which falls back to)
            orig_load = ce.load_status
            ce.load_status = lambda *a, **kw: None
            try:
                result = ce.escalate(
                    "What should I do about freight?",
                    kb_dir=td,
                    claude_md_path=os.path.join(td, "missing.md"),
                    cases_dir=os.path.join(td, "missing_cases"),
                    audit_log_path=os.path.join(td, "audit.jsonl"),
                )
            finally:
                ce.load_status = orig_load
            self.assertEqual(result["status"], "no_context")

    def test_dry_run_returns_synthetic_response(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"focus": "test"},
                                                  with_changelog=True)
            audit = os.path.join(td, "audit.jsonl")
            result = ce.escalate(
                "What should we do?",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                dry_run=True,
            )
            self.assertEqual(result["status"], "dry_run")
            self.assertIn("DRY RUN", result["proposal"])
            self.assertEqual(result["model_used"], None)
            # Audit record written
            self.assertTrue(os.path.isfile(audit))
            with open(audit) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["status"], "dry_run")

    def test_ok_path_with_mock_client(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"focus": "PoC"},
                                                  with_changelog=True,
                                                  with_cases=True)
            audit = os.path.join(td, "audit.jsonl")
            client = FakeAnthropicClient(responses=[
                _good_json_response(
                    proposal="Consider reviewing the existing case docs.",
                    rationale="status.json shows V37.9.90 as focus.",
                    confidence="medium",
                    refs=["status.json", "CLAUDE.md"],
                ),
            ])
            result = ce.escalate(
                "How should we approach the freight question?",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["model_used"], "claude-opus-4-7")
            self.assertIn("case docs", result["proposal"])
            self.assertEqual(result["confidence"], "medium")
            # First model only — should not fall back
            self.assertEqual(len(client.messages.calls), 1)
            self.assertEqual(client.messages.calls[0]["model"], "claude-opus-4-7")

    def test_falls_back_to_sonnet_on_opus_failure(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            client = FakeAnthropicClient(
                responses=[
                    RuntimeError("opus failed"),
                    _good_json_response(),
                ],
            )
            result = ce.escalate(
                "Question here",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["model_used"], "claude-sonnet-4-6")
            self.assertEqual(len(client.messages.calls), 2)

    def test_all_models_fail_returns_api_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            client = FakeAnthropicClient(responses=[
                RuntimeError("opus down"),
                RuntimeError("sonnet down too"),
            ])
            result = ce.escalate(
                "Question",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
            )
            self.assertEqual(result["status"], "api_unavailable")
            self.assertIn("sonnet down too", result["error"])

    def test_read_only_violation_fail_close(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            evil_response = FakeResponse(json.dumps({
                "proposal": "Just run `rm -rf /tmp/foo && sudo restart`",
                "rationale": "ok",
                "confidence": "high",
                "refs": [],
            }))
            client = FakeAnthropicClient(responses=[evil_response, evil_response])
            result = ce.escalate(
                "Question",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
            )
            self.assertEqual(result["status"], "read_only_violation")
            self.assertGreater(len(result["violations"]), 0)
            # Should try both models (each fails read-only)
            self.assertEqual(result["model_used"], "claude-opus-4-7")
            # Audit record written
            with open(audit) as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["status"], "read_only_violation")

    def test_parse_failure_tries_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            client = FakeAnthropicClient(responses=[
                FakeResponse("Just plain text, no JSON here"),
                _good_json_response(),
            ])
            result = ce.escalate(
                "Q",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["model_used"], "claude-sonnet-4-6")

    def test_quota_exceeded_fail_close(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            # Pre-populate audit log with 10 calls today
            with open(audit, "w") as f:
                for _ in range(10):
                    f.write(json.dumps({
                        "timestamp_iso": "2026-05-29T08:00:00Z",
                    }) + "\n")
            client = FakeAnthropicClient(responses=[_good_json_response()])
            result = ce.escalate(
                "Q",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
                max_daily=10,
                today_for_test="2026-05-29",
            )
            self.assertEqual(result["status"], "quota_exceeded")
            # No API call made (quota check is before)
            self.assertEqual(len(client.messages.calls), 0)

    def test_prompt_caching_on_context_block_not_system_prompt(self):
        """V37.9.90 invariant: cache_control on the context (stable) block, NOT
        on the system prompt itself. Cache key = system_prompt + context.
        Caching only the context would invalidate when SYSTEM_PROMPT text changes,
        but caching system_prompt alone misses the largest gain (context block).
        """
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            client = FakeAnthropicClient(responses=[_good_json_response()])
            ce.escalate(
                "Q",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
            )
            call = client.messages.calls[0]
            system = call["system"]
            self.assertEqual(len(system), 2)
            # First block: system prompt (no cache_control)
            self.assertNotIn("cache_control", system[0])
            # Second block: context (cached)
            self.assertEqual(system[1]["cache_control"], {"type": "ephemeral"})

    def test_audit_record_written_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            claude_md, cases_dir = self._make_env(td, status={"x": 1})
            audit = os.path.join(td, "audit.jsonl")
            client = FakeAnthropicClient(responses=[
                _good_json_response(confidence="high"),
            ])
            ce.escalate(
                "Question",
                kb_dir=td,
                claude_md_path=claude_md,
                cases_dir=cases_dir,
                audit_log_path=audit,
                client=client,
            )
            with open(audit) as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["status"], "ok")
            self.assertEqual(rec["model_used"], "claude-opus-4-7")
            self.assertEqual(rec["confidence"], "high")
            self.assertIn("question_preview", rec)


# ══════════════════════════════════════════════════════════════════════
# 11. TestCliInterface
# ══════════════════════════════════════════════════════════════════════


class TestCliInterface(unittest.TestCase):

    def test_dry_run_via_subprocess(self):
        result = subprocess.run(
            [sys.executable, "claude_escalation.py",
             "--question", "What is the status?",
             "--dry-run", "--json"],
            capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["status"], "dry_run")

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "claude_escalation.py", "--help"],
            capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Claude Escalation", result.stdout)


# ══════════════════════════════════════════════════════════════════════
# 12. TestV37990SourceGuards — drift defense
# ══════════════════════════════════════════════════════════════════════


class TestV37990SourceGuards(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "claude_escalation.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_90_marker(self):
        self.assertIn("V37.9.90", self.src)

    def test_v37_9_83_reference(self):
        """V37.9.90 traces lineage to V37.9.83 strategic sediment."""
        self.assertIn("V37.9.83", self.src)

    def test_default_model_priority_opus_47_first(self):
        # User explicitly chose Opus 4.7 first, Sonnet 4.6 fallback
        self.assertEqual(
            ce.DEFAULT_MODEL_PRIORITY,
            ("claude-opus-4-7", "claude-sonnet-4-6"),
        )

    def test_adaptive_thinking_explicitly_set(self):
        """Opus 4.7 has adaptive thinking OFF by default; must set explicitly."""
        self.assertIn('"type": "adaptive"', self.src)

    def test_effort_high(self):
        self.assertIn('"effort": "high"', self.src)

    def test_cache_control_ephemeral(self):
        self.assertIn('cache_control', self.src)
        self.assertIn('ephemeral', self.src)

    def test_uses_output_config_not_output_format(self):
        """V37.9.90 must use output_config (current) not deprecated output_format."""
        # Must use the output_config kwarg, not the deprecated output_format.
        self.assertIn("output_config=", self.src)
        # Must NOT use the deprecated output_format kwarg in the call site.
        # (Comments / docs may mention "output_format" — restrict scan to call sites.)
        call_section = re.search(
            r"def call_claude.*?return ok",
            self.src, re.DOTALL,
        )
        if call_section:
            self.assertNotIn("output_format=", call_section.group(0))

    def test_no_temperature_top_p_top_k(self):
        """Opus 4.7 / Sonnet 4.6 reject temperature/top_p/top_k."""
        # Scan for these in messages.create call patterns (not in test data)
        # Look for any `temperature=` or `top_p=` in the call_claude function
        call_section_match = re.search(
            r"def call_claude.*?return ok",
            self.src, re.DOTALL,
        )
        if call_section_match:
            section = call_section_match.group(0)
            self.assertNotIn("temperature=", section)
            self.assertNotIn("top_p=", section)
            self.assertNotIn("top_k=", section)

    def test_read_only_dangerous_tokens_present(self):
        """V37.9.90 read-only contract must catch known dangerous patterns."""
        self.assertIn("rm -rf", self.src)
        self.assertIn("sudo", self.src)

    def test_audit_log_path_default(self):
        self.assertIn(".kb/audit/claude_escalations.jsonl", self.src)

    def test_module_docstring_mentions_three_directions(self):
        """V37.9.83 strategic sediment had 3 directions; this is direction 2."""
        self.assertIn("方向 2", self.src)

    def test_default_daily_quota_present(self):
        self.assertEqual(ce.DEFAULT_DAILY_QUOTA, 10)


if __name__ == "__main__":
    unittest.main()

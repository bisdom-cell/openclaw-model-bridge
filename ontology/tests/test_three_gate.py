#!/usr/bin/env python3
"""
test_three_gate.py — Phase 4 P3 three-gate scaffolding contract (V37.9.15)

Locks the behavior of ontology/three_gate.py:
  - gates_mode() env-var parsing (off / shadow / on / unknown)
  - GateFinding namedtuple shape
  - pre_check / runtime_gate / post_verify return list[GateFinding]
  - FAIL-OPEN: any engine exception never propagates
  - shadow mode never sets enforced=True
  - context-missing returns applicable=None → verdict=pass

Companion tests:
  - test_engine_phase4.py (locks evaluate_policy / context evaluators)
  - test_tool_proxy.py TestThreeGateWiring (locks tool_proxy call sites)
"""

import os
import sys
import unittest
from unittest import mock

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

import three_gate  # noqa: E402
from three_gate import (  # noqa: E402
    GateFinding,
    gates_mode,
    pre_check,
    runtime_gate,
    post_verify,
    format_findings_for_log,
    _safe_evaluate_policy,
    _extract_signal_for_limit,
    _extract_assistant_text,
)


class EnvModeContext:
    """Context manager: set ONTOLOGY_GATES_MODE env, restore on exit."""
    def __init__(self, value):
        self.value = value
        self._prev = None
        self._had = False

    def __enter__(self):
        self._had = "ONTOLOGY_GATES_MODE" in os.environ
        if self._had:
            self._prev = os.environ["ONTOLOGY_GATES_MODE"]
        if self.value is None:
            if "ONTOLOGY_GATES_MODE" in os.environ:
                del os.environ["ONTOLOGY_GATES_MODE"]
        else:
            os.environ["ONTOLOGY_GATES_MODE"] = self.value
        return self

    def __exit__(self, *exc):
        if self._had:
            os.environ["ONTOLOGY_GATES_MODE"] = self._prev
        elif "ONTOLOGY_GATES_MODE" in os.environ:
            del os.environ["ONTOLOGY_GATES_MODE"]


# ===========================================================================
# gates_mode()
# ===========================================================================
class TestGatesMode(unittest.TestCase):
    def test_default_is_shadow(self):
        with EnvModeContext(None):
            self.assertEqual(gates_mode(), "shadow")

    def test_off_respected(self):
        with EnvModeContext("off"):
            self.assertEqual(gates_mode(), "off")

    def test_on_respected(self):
        with EnvModeContext("on"):
            self.assertEqual(gates_mode(), "on")

    def test_shadow_respected(self):
        with EnvModeContext("shadow"):
            self.assertEqual(gates_mode(), "shadow")

    def test_unknown_falls_back_to_shadow(self):
        # Unknown modes must NOT silently disable gates — fall back to observe.
        with EnvModeContext("disabled"):
            self.assertEqual(gates_mode(), "shadow")
        with EnvModeContext(""):
            self.assertEqual(gates_mode(), "shadow")

    def test_case_insensitive(self):
        with EnvModeContext("OFF"):
            self.assertEqual(gates_mode(), "off")
        with EnvModeContext("Shadow"):
            self.assertEqual(gates_mode(), "shadow")

    def test_whitespace_tolerated(self):
        with EnvModeContext("  on  "):
            self.assertEqual(gates_mode(), "on")


# ===========================================================================
# GateFinding namedtuple shape
# ===========================================================================
class TestGateFinding(unittest.TestCase):
    def test_all_fields_present(self):
        f = GateFinding(
            gate="pre_check",
            policy_id="x",
            verdict="pass",
            action="a",
            reason="r",
            enforced=False,
        )
        self.assertEqual(f.gate, "pre_check")
        self.assertEqual(f.policy_id, "x")
        self.assertEqual(f.verdict, "pass")
        self.assertEqual(f.action, "a")
        self.assertEqual(f.reason, "r")
        self.assertFalse(f.enforced)

    def test_immutable(self):
        f = GateFinding("g", "p", "pass", "a", "r", False)
        with self.assertRaises(AttributeError):
            f.verdict = "flag"  # type: ignore

    def test_field_order_stable(self):
        # Regression guard: tests/tooling may rely on positional unpacking.
        self.assertEqual(
            GateFinding._fields,
            ("gate", "policy_id", "verdict", "action", "reason", "enforced"),
        )


# ===========================================================================
# Off mode short-circuits (all 3 gates return [])
# ===========================================================================
class TestOffModeShortCircuit(unittest.TestCase):
    def test_pre_check_off_returns_empty(self):
        with EnvModeContext("off"):
            self.assertEqual(pre_check({"messages": [{"role": "user",
                                                     "content": "[SYSTEM_ALERT] x"}]}), [])

    def test_runtime_gate_off_returns_empty(self):
        with EnvModeContext("off"):
            self.assertEqual(runtime_gate({"tool_count": 100}), [])

    def test_post_verify_off_returns_empty(self):
        with EnvModeContext("off"):
            self.assertEqual(post_verify({}, {"choices": []}), [])

    def test_off_does_not_call_engine(self):
        # Contract: off mode must NOT invoke evaluate_policy. Proves zero
        # runtime cost when ontology engine unavailable.
        with EnvModeContext("off"):
            with mock.patch("three_gate._safe_evaluate_policy") as mocked:
                pre_check({})
                runtime_gate({})
                post_verify({}, None)
                self.assertEqual(mocked.call_count, 0)


# ===========================================================================
# Shadow mode: evaluates but enforced=False
# ===========================================================================
class TestShadowModeNeverEnforces(unittest.TestCase):
    def test_flag_verdict_has_enforced_false_in_shadow(self):
        # tool_count=50 clearly exceeds max-tools limit=12 → flag.
        with EnvModeContext("shadow"):
            findings = runtime_gate({"tool_count": 50})
        tool_findings = [f for f in findings if f.policy_id == "max-tools-per-agent"]
        self.assertTrue(len(tool_findings) >= 1)
        for f in tool_findings:
            if f.verdict == "flag":
                self.assertFalse(f.enforced, f"shadow must not enforce: {f}")

    def test_on_mode_flag_has_enforced_true(self):
        with EnvModeContext("on"):
            findings = runtime_gate({"tool_count": 50})
        tool_findings = [f for f in findings if f.policy_id == "max-tools-per-agent"]
        flagged = [f for f in tool_findings if f.verdict == "flag"]
        self.assertTrue(len(flagged) >= 1)
        for f in flagged:
            self.assertTrue(f.enforced, f"on mode flag must mark enforced=True: {f}")


# ===========================================================================
# pre_check() behavior
# ===========================================================================
class TestPreCheck(unittest.TestCase):
    def test_returns_list_of_gate_findings(self):
        with EnvModeContext("shadow"):
            findings = pre_check({})
        self.assertIsInstance(findings, list)
        for f in findings:
            self.assertIsInstance(f, GateFinding)
            self.assertEqual(f.gate, "pre_check")

    def test_evaluates_alert_isolation(self):
        with EnvModeContext("shadow"):
            findings = pre_check({
                "messages": [{"role": "user", "content": "[SYSTEM_ALERT] cron-fail"}]
            })
        policies = {f.policy_id for f in findings}
        self.assertIn("alert-context-isolation", policies)

    def test_alert_present_flags_policy(self):
        with EnvModeContext("shadow"):
            findings = pre_check({
                "messages": [{"role": "assistant", "content": "[SYSTEM_ALERT] cron"}]
            })
        alert = [f for f in findings if f.policy_id == "alert-context-isolation"]
        self.assertEqual(len(alert), 1)
        self.assertEqual(alert[0].verdict, "flag")

    def test_no_alert_passes(self):
        with EnvModeContext("shadow"):
            findings = pre_check({
                "messages": [{"role": "user", "content": "hello"}]
            })
        alert = [f for f in findings if f.policy_id == "alert-context-isolation"]
        self.assertEqual(alert[0].verdict, "pass")

    def test_quiet_hours_inside_window(self):
        with EnvModeContext("shadow"):
            findings = pre_check({"hour": 3, "messages": []})
        q = [f for f in findings if f.policy_id == "quiet-hours-00-07"]
        self.assertEqual(q[0].verdict, "flag")

    def test_quiet_hours_outside_window(self):
        with EnvModeContext("shadow"):
            findings = pre_check({"hour": 14, "messages": []})
        q = [f for f in findings if f.policy_id == "quiet-hours-00-07"]
        self.assertEqual(q[0].verdict, "pass")

    def test_missing_hour_reports_context_incomplete(self):
        # Missing required context → applicable=None → verdict=pass (fail-open).
        with EnvModeContext("shadow"):
            findings = pre_check({"messages": []})
        q = [f for f in findings if f.policy_id == "quiet-hours-00-07"]
        self.assertEqual(q[0].verdict, "pass")
        self.assertIn("context_missing_hour", q[0].reason)

    def test_none_context_tolerated(self):
        with EnvModeContext("shadow"):
            findings = pre_check(None)
        self.assertIsInstance(findings, list)


# ===========================================================================
# runtime_gate() behavior
# ===========================================================================
class TestRuntimeGate(unittest.TestCase):
    def test_tool_count_over_limit_flags(self):
        with EnvModeContext("shadow"):
            findings = runtime_gate({"tool_count": 20})
        m = [f for f in findings if f.policy_id == "max-tools-per-agent"]
        self.assertEqual(m[0].verdict, "flag")
        self.assertIn("signal=20", m[0].reason)
        self.assertIn("limit=12", m[0].reason)

    def test_tool_count_under_limit_passes(self):
        with EnvModeContext("shadow"):
            findings = runtime_gate({"tool_count": 5})
        m = [f for f in findings if f.policy_id == "max-tools-per-agent"]
        self.assertEqual(m[0].verdict, "pass")
        self.assertIn("signal=5", m[0].reason)

    def test_tool_count_equal_to_limit_passes(self):
        # Boundary: signal <= limit → pass, not flag.
        with EnvModeContext("shadow"):
            findings = runtime_gate({"tool_count": 12})
        m = [f for f in findings if f.policy_id == "max-tools-per-agent"]
        self.assertEqual(m[0].verdict, "pass")

    def test_tool_call_count_over_limit_flags(self):
        with EnvModeContext("shadow"):
            findings = runtime_gate({"tool_call_count": 5})
        m = [f for f in findings if f.policy_id == "max-tool-calls-per-task"]
        self.assertEqual(m[0].verdict, "flag")

    def test_body_bytes_over_limit_flags(self):
        with EnvModeContext("shadow"):
            findings = runtime_gate({"body_bytes": 300000})
        m = [f for f in findings if f.policy_id == "max-request-body-size"]
        self.assertEqual(m[0].verdict, "flag")
        self.assertIn("limit=200000", m[0].reason)

    def test_missing_signal_does_not_break(self):
        # Context without signals: static policies still applicable=True,
        # but V37.9.15.2 HOTFIX: no measurable signal = pass (not flag).
        # Only contextual/temporal applicable=True can flag.
        with EnvModeContext("shadow"):
            findings = runtime_gate({})
        # Each runtime policy returns one finding (three total).
        self.assertEqual(len(findings), 3)
        for f in findings:
            self.assertIsInstance(f, GateFinding)

    def test_static_policy_no_signal_is_pass_not_flag(self):
        """V37.9.15.2 HOTFIX regression guard.

        Production 2026-04-24 13:01 showed `max-tool-calls-per-task` (static,
        always applicable=True) falsely flag on every request because
        tool_call_count is not in the runtime_gate context. Fix: static
        policies without a matching signal must pass with
        reason='no_signal_in_context', not flag.
        """
        with EnvModeContext("shadow"):
            # Runtime_gate ctx has tool_count and body_bytes, but NOT
            # tool_call_count (which is a cross-request aggregate).
            findings = runtime_gate({
                "tool_count": 5,
                "body_bytes": 100,
                # tool_call_count intentionally absent
            })
        tc = [f for f in findings if f.policy_id == "max-tool-calls-per-task"]
        self.assertEqual(len(tc), 1)
        self.assertEqual(
            tc[0].verdict, "pass",
            f"V37.9.15.2: static policy without signal must be pass, "
            f"got flag. Full finding: {tc[0]}")
        self.assertIn(
            "no_signal_in_context", tc[0].reason,
            f"V37.9.15.2: reason must indicate signal absence, got: {tc[0].reason}")
        self.assertFalse(
            tc[0].enforced,
            "No-signal static findings must never be enforced")

    def test_static_policy_with_signal_still_flags_when_over_limit(self):
        """V37.9.15.2 safety check: the hotfix must NOT regress the real
        flag path. When signal is present and exceeds limit, still flag."""
        with EnvModeContext("shadow"):
            findings = runtime_gate({"tool_count": 999})
        mt = [f for f in findings if f.policy_id == "max-tools-per-agent"]
        self.assertEqual(mt[0].verdict, "flag",
                         "tool_count=999 > limit=12 must still flag")


# ===========================================================================
# post_verify() behavior
# ===========================================================================
class TestPostVerify(unittest.TestCase):
    def test_returns_list(self):
        with EnvModeContext("shadow"):
            findings = post_verify({}, {"choices": []})
        self.assertIsInstance(findings, list)

    def test_none_response_tolerated(self):
        with EnvModeContext("shadow"):
            findings = post_verify({}, None)
        self.assertIsInstance(findings, list)

    def test_alert_echo_in_assistant_output_detected(self):
        # If LLM response contains [SYSTEM_ALERT], alert-isolation should flag.
        resp = {"choices": [{"message": {"role": "assistant",
                                         "content": "[SYSTEM_ALERT] echo"}}]}
        with EnvModeContext("shadow"):
            findings = post_verify({"messages": []}, resp)
        alert = [f for f in findings if f.policy_id == "alert-context-isolation"]
        self.assertEqual(alert[0].verdict, "flag")

    def test_clean_response_passes(self):
        resp = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
        with EnvModeContext("shadow"):
            findings = post_verify({"messages": []}, resp)
        alert = [f for f in findings if f.policy_id == "alert-context-isolation"]
        self.assertEqual(alert[0].verdict, "pass")

    def test_malformed_response_tolerated(self):
        # Unexpected shape must not break the gate.
        with EnvModeContext("shadow"):
            findings = post_verify({}, {"not_choices": "wat"})
        self.assertIsInstance(findings, list)
        for f in findings:
            self.assertEqual(f.gate, "post_verify")


# ===========================================================================
# FAIL-OPEN contract: engine exceptions never propagate
# ===========================================================================
class TestFailOpen(unittest.TestCase):
    def test_engine_import_error_returns_pass(self):
        with EnvModeContext("shadow"):
            with mock.patch("three_gate._safe_evaluate_policy", return_value=None):
                findings = pre_check({"hour": 3})
                for f in findings:
                    self.assertEqual(f.verdict, "pass")
                    self.assertEqual(f.reason, "engine_unavailable")

    def test_runtime_gate_engine_error_returns_pass(self):
        with EnvModeContext("shadow"):
            with mock.patch("three_gate._safe_evaluate_policy", return_value=None):
                findings = runtime_gate({"tool_count": 50})
                self.assertTrue(all(f.verdict == "pass" for f in findings))

    def test_safe_evaluate_policy_catches_exception(self):
        # Directly exercise _safe_evaluate_policy with a raising monkey-patch.
        import engine
        original = engine.evaluate_policy
        try:
            def _boom(*a, **k):
                raise RuntimeError("synthetic")
            engine.evaluate_policy = _boom
            result = _safe_evaluate_policy("max-tools-per-agent", {})
            self.assertIsNone(result)
        finally:
            engine.evaluate_policy = original

    def test_policy_not_found_returns_pass_verdict(self):
        # If evaluate_policy returns found=False, gate still returns finding
        # with verdict=pass, not an exception.
        fake_result = {"policy_id": "x", "found": False, "reason": "policy_id_not_found"}
        with EnvModeContext("shadow"):
            with mock.patch("three_gate._safe_evaluate_policy", return_value=fake_result):
                findings = pre_check({})
                for f in findings:
                    self.assertEqual(f.verdict, "pass")
                    self.assertIn("policy_not_found", f.reason)


# ===========================================================================
# _extract_signal_for_limit() helper
# ===========================================================================
class TestExtractSignal(unittest.TestCase):
    def test_known_policy_extracts_int(self):
        self.assertEqual(_extract_signal_for_limit("max-tools-per-agent",
                                                   {"tool_count": 7}), 7)

    def test_known_policy_extracts_float(self):
        self.assertEqual(_extract_signal_for_limit("max-request-body-size",
                                                   {"body_bytes": 1.5}), 1.5)

    def test_unknown_policy_returns_none(self):
        self.assertIsNone(_extract_signal_for_limit("unknown", {"x": 1}))

    def test_missing_key_returns_none(self):
        self.assertIsNone(_extract_signal_for_limit("max-tools-per-agent", {}))

    def test_none_context_returns_none(self):
        self.assertIsNone(_extract_signal_for_limit("max-tools-per-agent", None))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(_extract_signal_for_limit("max-tools-per-agent",
                                                   {"tool_count": "many"}))

    def test_bool_excluded_despite_python_int(self):
        # bool is a subclass of int in Python but semantically meaningless here.
        self.assertIsNone(_extract_signal_for_limit("max-tools-per-agent",
                                                   {"tool_count": True}))


# ===========================================================================
# _extract_assistant_text() helper
# ===========================================================================
class TestExtractAssistantText(unittest.TestCase):
    def test_string_content(self):
        resp = {"choices": [{"message": {"content": "hello"}}]}
        self.assertEqual(_extract_assistant_text(resp), "hello")

    def test_list_content(self):
        resp = {"choices": [{"message": {"content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}
        ]}}]}
        self.assertEqual(_extract_assistant_text(resp), "a b")

    def test_missing_choices(self):
        self.assertEqual(_extract_assistant_text({}), "")

    def test_empty_choices(self):
        self.assertEqual(_extract_assistant_text({"choices": []}), "")

    def test_none_response(self):
        self.assertEqual(_extract_assistant_text(None), "")

    def test_malformed_first_choice(self):
        self.assertEqual(_extract_assistant_text({"choices": ["not_a_dict"]}), "")


# ===========================================================================
# format_findings_for_log() helper
# ===========================================================================
class TestFormatFindingsForLog(unittest.TestCase):
    def test_empty_list_returns_empty_string(self):
        self.assertEqual(format_findings_for_log([]), "")

    def test_single_finding(self):
        f = GateFinding("pre_check", "x", "flag", "a", "r=1", False)
        out = format_findings_for_log([f])
        self.assertIn("[gate:pre_check]", out)
        self.assertIn("x=flag", out)
        self.assertIn("r=1", out)

    def test_multiple_findings_share_gate_prefix(self):
        fs = [
            GateFinding("runtime_gate", "p1", "pass", "a1", "r1", False),
            GateFinding("runtime_gate", "p2", "flag", "a2", "r2", True),
        ]
        out = format_findings_for_log(fs)
        self.assertIn("[gate:runtime_gate]", out)
        self.assertIn("2 findings", out)
        self.assertIn("p1=pass", out)
        self.assertIn("p2=flag", out)


# ===========================================================================
# Module-level integration sanity
# ===========================================================================
class TestModuleIntegration(unittest.TestCase):
    def test_public_api_exposed(self):
        # Regression: downstream imports rely on these names.
        for name in ("gates_mode", "pre_check", "runtime_gate",
                     "post_verify", "GateFinding", "format_findings_for_log"):
            self.assertTrue(hasattr(three_gate, name),
                            f"three_gate.{name} missing")

    def test_gate_name_convention_stable(self):
        # Downstream log parsers may grep these literal gate names.
        with EnvModeContext("shadow"):
            pre = pre_check({"hour": 3})
            runtime = runtime_gate({"tool_count": 5})
            post = post_verify({}, {"choices": []})
        self.assertTrue(all(f.gate == "pre_check" for f in pre))
        self.assertTrue(all(f.gate == "runtime_gate" for f in runtime))
        self.assertTrue(all(f.gate == "post_verify" for f in post))


class TestEngineLoadStrategies(unittest.TestCase):
    """V37.9.15.1 HOTFIX regression guard.

    Production log on 2026-04-24 showed three_gate loaded via
    `spec_from_file_location("_three_gate", ...)` from tool_proxy.py ends up
    with __package__='' AND sys.path not containing ontology/, so both
    original import paths (`from . import engine` / `import engine`) fail
    and every policy evaluation falls through to engine_unavailable — i.e.
    shadow mode was effectively dark, not observing anything.

    The hotfix adds a __file__-adjacent spec_from_file_location strategy.
    These tests lock that behavior in:
      - path 3 works when path 1+2 both unavailable
      - engine module is cached after first successful load
      - source marker V37.9.15.1 present for grep-ability
    """

    def _isolate_and_load_three_gate(self):
        """Load three_gate.py via spec_from_file_location in an env that
        deliberately strips ontology/ from sys.path — matches tool_proxy.py
        production loading conditions."""
        import importlib.util
        import os
        tg_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "three_gate.py"))
        # Drop any ontology-related sys.path entries for this load.
        saved_path = list(sys.path)
        # Also drop cached modules so module-level _ENGINE_MOD is reset
        saved_modules = {
            k: sys.modules[k] for k in list(sys.modules)
            if k.startswith("_three_gate") or k == "_three_gate_engine_lazy"
        }
        for k in list(saved_modules):
            del sys.modules[k]
        sys.path = [p for p in sys.path if "ontology" not in p]
        try:
            spec = importlib.util.spec_from_file_location(
                "_three_gate_isolated_test", tg_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, saved_path, saved_modules
        except Exception:
            sys.path = saved_path
            for k, v in saved_modules.items():
                sys.modules[k] = v
            raise

    def _restore(self, saved_path, saved_modules):
        sys.path = saved_path
        for k, v in saved_modules.items():
            sys.modules[k] = v

    def test_safe_evaluate_policy_works_without_package_or_syspath(self):
        """HOTFIX regression: production scenario (no __package__, no sys.path
        ontology/) must still return real policy evaluation, not None."""
        mod, saved_path, saved_modules = self._isolate_and_load_three_gate()
        try:
            self.assertEqual(
                mod.__package__, "",
                "Test fixture sanity: __package__ must be empty to exercise hotfix")
            result = mod._safe_evaluate_policy("max-tools-per-agent", {})
            self.assertIsNotNone(
                result, "V37.9.15.1 HOTFIX: engine must load via __file__ path 3")
            self.assertTrue(
                result.get("found"),
                f"max-tools-per-agent policy must be found, got: {result}")
            self.assertEqual(
                result.get("limit"), 12,
                f"max-tools-per-agent limit must be 12, got: {result}")
        finally:
            self._restore(saved_path, saved_modules)

    def test_load_engine_module_helper_exposed(self):
        """V37.9.15.1: _load_engine_module helper exists and is callable.

        Downstream debugging (manual ssh poke) relies on this symbol name.
        """
        mod, saved_path, saved_modules = self._isolate_and_load_three_gate()
        try:
            self.assertTrue(
                hasattr(mod, "_load_engine_module"),
                "Hotfix symbol _load_engine_module must be exposed for diagnostics")
            engine = mod._load_engine_module()
            self.assertIsNotNone(engine)
            self.assertTrue(
                hasattr(engine, "evaluate_policy"),
                "Loaded engine must expose evaluate_policy")
        finally:
            self._restore(saved_path, saved_modules)

    def test_engine_module_is_cached_after_first_load(self):
        """V37.9.15.1: _ENGINE_MOD module-level cache avoids repeated
        exec_module cost on every _safe_evaluate_policy call."""
        mod, saved_path, saved_modules = self._isolate_and_load_three_gate()
        try:
            self.assertIsNone(mod._ENGINE_MOD,
                              "Fresh load: cache must start None")
            mod._safe_evaluate_policy("max-tools-per-agent", {})
            self.assertIsNotNone(
                mod._ENGINE_MOD,
                "After first successful call, _ENGINE_MOD must be populated")
            first_id = id(mod._ENGINE_MOD)
            mod._safe_evaluate_policy("max-tool-calls-per-task", {})
            self.assertEqual(id(mod._ENGINE_MOD), first_id,
                             "Subsequent calls must reuse cached engine module")
        finally:
            self._restore(saved_path, saved_modules)

    def test_hotfix_source_marker_present(self):
        """Grep guard: V37.9.15.1 comment block must be in source so future
        refactors know why the extra strategy exists."""
        import os
        tg_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "three_gate.py"))
        with open(tg_path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.15.1 HOTFIX", src,
                      "Source must retain V37.9.15.1 HOTFIX marker for archaeology")
        self.assertIn("_load_engine_module", src,
                      "Source must define _load_engine_module helper")
        self.assertIn("_ENGINE_MOD", src,
                      "Source must declare _ENGINE_MOD module-level cache")


if __name__ == "__main__":
    unittest.main()

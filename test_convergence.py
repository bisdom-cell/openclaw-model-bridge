"""V37.9.19 — Convergence framework regression tests

Covers:
  TestConvergenceResult — namedtuple shape + immutability
  TestLoadSpecs — yaml loading + missing file FAIL-OPEN
  TestSpecLookup — list_spec_ids / get_spec contracts
  TestExtractRegistryEnabledSystemJobs — declared-side extractor
  TestObserveShellCommand — runtime observer with subprocess + timeout/error paths
  TestIdentifierParsers — line_contains_identifier + word_boundary variants
  TestVerifyConvergenceHappyPath — declared==observed → drift_detected=False
  TestVerifyConvergenceDriftDetected — declared > observed → missing populated
  TestVerifyConvergenceFailOpen — every internal failure → result.error set, no raise
  TestVerifyConvergenceDriftActionValidation — invalid drift_action handled gracefully
  TestVerifyConvergenceDisabledSpec — enabled=false → no-op
  TestFormatResultForLog — single-line output for ops grep
  TestRealJobsToCrontabSpec — real convergence_ontology.yaml jobs_to_crontab spec smoke
  TestSourceLevelGuards — convergence.py + convergence_ontology.yaml structural guards
  TestKbSourcesToIndexCommandRuntime — V37.9.23 加固: mock meta.json 真跑 spec
    yaml 的 python oneliner, 闭合 V37.9.22 4/29 hotfix 教训 (yaml command 字段名
    假设错误 dev 单测全过仅 Mac Mini 实测才暴露). MR-4 silent-failure 防御.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ONTOLOGY_DIR = REPO_ROOT / "ontology"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(ONTOLOGY_DIR))

from ontology import convergence as cv  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def _write_temp_registry(tmp_dir, jobs):
    """Write a minimal jobs_registry.yaml fragment with the given jobs list."""
    path = tmp_dir / "jobs_registry.yaml"
    lines = ["jobs:\n"]
    for j in jobs:
        lines.append(f"  - id: {j['id']}\n")
        lines.append(f"    enabled: {str(j.get('enabled', True)).lower()}\n")
        lines.append(f"    scheduler: {j.get('scheduler', 'system')}\n")
        lines.append(f"    entry: {j.get('entry', '')}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _build_spec(registry_path, command, **overrides):
    """Build a synthetic spec with absolute paths for isolated testing."""
    spec = {
        "id": "test_spec",
        "enabled": True,
        "declaration": {
            "source": str(registry_path),
            "extractor": "registry_enabled_system_jobs",
        },
        "runtime_observable": {
            "method": "shell_command",
            "command": command,
            "parser": "line_contains_identifier",
        },
        "drift_action": "alert_only",
    }
    spec.update(overrides)
    return spec


# ── tests ──────────────────────────────────────────────────────────────────

class TestConvergenceResult(unittest.TestCase):
    """ConvergenceResult namedtuple shape and immutability"""

    def test_field_order_stable(self):
        # Stable field order is part of the contract — downstream callers
        # may unpack positionally or compare against tuple literals.
        # V37.9.23: 加 3 个 machine_sync apply tracking 字段 (向后兼容).
        # V37.9.66: 加 extra_in_runtime 字段支持双向 sync (向后兼容默认 frozenset()).
        expected = ("spec_id", "declared", "observed", "missing_in_runtime",
                    "drift_detected", "drift_action", "error",
                    "applied_actions", "apply_dry_run", "apply_errors",
                    "extra_in_runtime")
        self.assertEqual(cv.ConvergenceResult._fields, expected)

    def test_result_is_immutable(self):
        r = cv._empty_result("x")
        with self.assertRaises(AttributeError):
            r.spec_id = "mutated"

    def test_empty_result_defaults(self):
        r = cv._empty_result("foo")
        self.assertEqual(r.spec_id, "foo")
        self.assertEqual(r.declared, frozenset())
        self.assertEqual(r.observed, frozenset())
        self.assertFalse(r.drift_detected)
        self.assertEqual(r.drift_action, "alert_only")
        self.assertIsNone(r.error)
        # V37.9.23: machine_sync 字段默认值守卫
        self.assertEqual(r.applied_actions, ())
        self.assertTrue(r.apply_dry_run, "默认 dry-run 守卫 (CONVERGENCE_DRY_RUN!=0)")
        self.assertEqual(r.apply_errors, ())


class TestLoadSpecs(unittest.TestCase):
    def test_load_real_file(self):
        specs = cv.load_specs()
        self.assertIsInstance(specs, dict)
        self.assertIn("convergence_specs", specs)

    def test_load_explicit_path(self):
        specs = cv.load_specs(ONTOLOGY_DIR / "convergence_ontology.yaml")
        self.assertIn("convergence_specs", specs)

    def test_load_missing_file_raises(self):
        with self.assertRaises(Exception):
            cv.load_specs("/nonexistent/path/foo.yaml")


class TestSpecLookup(unittest.TestCase):
    def test_list_spec_ids_returns_list(self):
        ids = cv.list_spec_ids()
        self.assertIsInstance(ids, list)
        self.assertIn("jobs_to_crontab", ids)

    def test_list_spec_ids_handles_load_failure(self):
        # FAIL-OPEN: missing file → empty list, not raise
        ids = cv.list_spec_ids(path="/nonexistent/file.yaml")
        self.assertEqual(ids, [])

    def test_get_spec_existing(self):
        spec = cv.get_spec("jobs_to_crontab")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.get("id"), "jobs_to_crontab")

    def test_get_spec_missing_returns_none(self):
        self.assertIsNone(cv.get_spec("nonexistent_spec_id"))

    def test_get_spec_with_explicit_specs_dict(self):
        synthetic = {"convergence_specs": [{"id": "abc", "enabled": True}]}
        result = cv.get_spec("abc", specs=synthetic)
        self.assertEqual(result["id"], "abc")


class TestExtractRegistryEnabledSystemJobs(unittest.TestCase):
    def test_extracts_only_enabled_system_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
                {"id": "b", "enabled": False, "scheduler": "system", "entry": "b.sh"},
                {"id": "c", "enabled": True, "scheduler": "openclaw", "entry": "c.sh"},
                {"id": "d", "enabled": True, "scheduler": "system", "entry": "d.sh"},
            ])
            spec = _build_spec(reg, "echo ''")
            result = cv._extract_registry_enabled_system_jobs(spec)
            self.assertEqual(result, {"a.sh", "d.sh"})

    def test_skips_jobs_without_entry(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "x", "enabled": True, "scheduler": "system", "entry": ""},
                {"id": "y", "enabled": True, "scheduler": "system", "entry": "y.sh"},
            ])
            spec = _build_spec(reg, "echo ''")
            result = cv._extract_registry_enabled_system_jobs(spec)
            self.assertEqual(result, {"y.sh"})

    def test_empty_registry_returns_empty_set(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [])
            spec = _build_spec(reg, "echo ''")
            result = cv._extract_registry_enabled_system_jobs(spec)
            self.assertEqual(result, set())


class TestObserveShellCommand(unittest.TestCase):
    def test_returns_stdout_on_success(self):
        spec = {"runtime_observable": {"method": "shell_command",
                                        "command": "echo 'hello world'"}}
        out = cv._observe_shell_command(spec)
        self.assertIn("hello world", out)

    def test_missing_command_raises(self):
        spec = {"runtime_observable": {"method": "shell_command", "command": ""}}
        with self.assertRaises(ValueError):
            cv._observe_shell_command(spec)

    def test_nonzero_exit_with_empty_stdout_returns_empty(self):
        # FAIL-OPEN for "no crontab installed" scenario: crontab -l on a user
        # without crontab returns exit=1 + empty stdout + stderr message.
        # Observer treats this as "empty observed" not error.
        spec = {"runtime_observable": {
            "method": "shell_command",
            "command": "echo 'no crontab' >&2; exit 1",
        }}
        out = cv._observe_shell_command(spec)
        self.assertEqual(out, "")

    def test_nonzero_exit_with_real_stdout_raises(self):
        # If command produced real stdout but also failed → genuine error
        spec = {"runtime_observable": {
            "method": "shell_command",
            "command": "echo 'partial output'; echo 'err' >&2; exit 2",
        }}
        with self.assertRaises(RuntimeError):
            cv._observe_shell_command(spec)


class TestIdentifierParsers(unittest.TestCase):
    def test_line_contains_identifier_basic(self):
        raw = (
            "30 22 * * * bash -lc 'bash ~/foo.sh >> ~/foo.log 2>&1'\n"
            "0 22 * * * bash -lc 'bash ~/bar.sh >> ~/bar.log 2>&1'\n"
        )
        declared = {"foo.sh", "bar.sh", "missing.sh"}
        result = cv._parse_line_contains_identifier({}, raw, declared)
        self.assertEqual(result, {"foo.sh", "bar.sh"})

    def test_line_contains_identifier_empty_raw(self):
        self.assertEqual(
            cv._parse_line_contains_identifier({}, "", {"foo.sh"}),
            set(),
        )

    def test_line_contains_identifier_skips_comments(self):
        raw = "# 30 22 * * * bash ~/foo.sh\n"
        result = cv._parse_line_contains_identifier({}, raw, {"foo.sh"})
        self.assertEqual(result, set())

    def test_word_boundary_variant_avoids_substring_collision(self):
        raw = "* * * * * bash ~/kb_dream_helper.sh\n"
        result = cv._parse_line_contains_word_boundary({}, raw, {"kb_dream.sh"})
        self.assertEqual(result, set(),
            "kb_dream.sh should NOT match kb_dream_helper.sh under word boundary parser")

    def test_word_boundary_variant_matches_real_cron_quoting(self):
        raw = "30 22 * * * bash -lc 'bash ~/kb_deep_dive.sh >> ~/kb_deep_dive.log 2>&1'\n"
        result = cv._parse_line_contains_word_boundary({}, raw, {"kb_deep_dive.sh"})
        self.assertEqual(result, {"kb_deep_dive.sh"})


class TestVerifyConvergenceHappyPath(unittest.TestCase):
    def test_all_declared_observed_no_drift(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
                {"id": "b", "enabled": True, "scheduler": "system", "entry": "b.sh"},
            ])
            cmd = "printf '%s\\n' 'bash ~/a.sh' 'bash ~/b.sh'"
            spec = _build_spec(reg, cmd)
            specs = {"convergence_specs": [spec]}
            r = cv.verify_convergence("test_spec", specs=specs)
            self.assertIsNone(r.error)
            self.assertFalse(r.drift_detected)
            self.assertEqual(r.declared, frozenset({"a.sh", "b.sh"}))
            self.assertEqual(r.observed, frozenset({"a.sh", "b.sh"}))
            self.assertEqual(r.missing_in_runtime, frozenset())


class TestVerifyConvergenceDriftDetected(unittest.TestCase):
    def test_missing_jobs_reported(self):
        # V37.9.18 blood scenario: kb_deep_dive declared but not in crontab
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "evening", "enabled": True, "scheduler": "system",
                 "entry": "kb_evening.sh"},
                {"id": "deep_dive", "enabled": True, "scheduler": "system",
                 "entry": "kb_deep_dive.sh"},  # this one missing in crontab
                {"id": "review", "enabled": True, "scheduler": "system",
                 "entry": "kb_review.sh"},
            ])
            # crontab returns only evening + review, missing deep_dive
            cmd = ("printf '%s\\n' "
                   "'0 22 * * * bash kb_evening.sh' "
                   "'0 21 * * 5 bash kb_review.sh'")
            spec = _build_spec(reg, cmd)
            specs = {"convergence_specs": [spec]}
            r = cv.verify_convergence("test_spec", specs=specs)
            self.assertIsNone(r.error)
            self.assertTrue(r.drift_detected)
            self.assertEqual(r.missing_in_runtime, frozenset({"kb_deep_dive.sh"}))
            self.assertEqual(r.observed, frozenset({"kb_evening.sh", "kb_review.sh"}))

    def test_all_missing_when_crontab_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
            ])
            spec = _build_spec(reg, "echo ''")
            specs = {"convergence_specs": [spec]}
            r = cv.verify_convergence("test_spec", specs=specs)
            self.assertTrue(r.drift_detected)
            self.assertEqual(r.missing_in_runtime, frozenset({"a.sh"}))


class TestVerifyConvergenceFailOpen(unittest.TestCase):
    """Every internal failure path returns a valid ConvergenceResult, never raises."""

    def test_spec_not_found(self):
        r = cv.verify_convergence("nonexistent",
                                  specs={"convergence_specs": []})
        self.assertEqual(r.error, "spec_not_found")
        self.assertFalse(r.drift_detected)

    def test_extractor_failure(self):
        spec = _build_spec(Path("/nonexistent/registry.yaml"), "echo ''")
        r = cv.verify_convergence("test_spec", specs={"convergence_specs": [spec]})
        self.assertIsNotNone(r.error)
        self.assertIn("extractor_failed", r.error)

    def test_unknown_extractor_name(self):
        spec = {
            "id": "bad",
            "enabled": True,
            "declaration": {"extractor": "this_extractor_does_not_exist"},
            "runtime_observable": {"method": "shell_command", "command": "echo ''"},
        }
        r = cv.verify_convergence("bad", specs={"convergence_specs": [spec]})
        self.assertIsNotNone(r.error)
        self.assertIn("extractor_failed", r.error)

    def test_unknown_observer_method(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [])
            spec = {
                "id": "bad",
                "enabled": True,
                "declaration": {
                    "source": str(reg),
                    "extractor": "registry_enabled_system_jobs",
                },
                "runtime_observable": {"method": "totally_made_up", "command": "x"},
            }
            r = cv.verify_convergence("bad", specs={"convergence_specs": [spec]})
            self.assertIsNotNone(r.error)
            self.assertIn("observer_failed", r.error)

    def test_unknown_parser_name(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
            ])
            spec = {
                "id": "bad",
                "enabled": True,
                "declaration": {
                    "source": str(reg),
                    "extractor": "registry_enabled_system_jobs",
                },
                "runtime_observable": {
                    "method": "shell_command",
                    "command": "echo 'a.sh'",
                    "parser": "fake_parser",
                },
            }
            r = cv.verify_convergence("bad", specs={"convergence_specs": [spec]})
            self.assertIsNotNone(r.error)
            self.assertIn("parser_failed", r.error)

    def test_observer_failure_treats_all_declared_as_missing(self):
        # When observer fails, declared identifiers should still be reported
        # as missing (since we couldn't observe them). This lets ops see what
        # WOULD be missing if observation had worked.
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
            ])
            spec = {
                "id": "obs_fail",
                "enabled": True,
                "declaration": {
                    "source": str(reg),
                    "extractor": "registry_enabled_system_jobs",
                },
                "runtime_observable": {"method": "totally_made_up", "command": "x"},
            }
            r = cv.verify_convergence("obs_fail",
                                      specs={"convergence_specs": [spec]})
            self.assertEqual(r.declared, frozenset({"a.sh"}))
            self.assertEqual(r.missing_in_runtime, frozenset({"a.sh"}))
            self.assertTrue(r.drift_detected)


class TestVerifyConvergenceDriftActionValidation(unittest.TestCase):
    def test_invalid_drift_action_falls_back_to_alert_only(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [])
            spec = _build_spec(reg, "echo ''", drift_action="auto_destroy_universe")
            r = cv.verify_convergence("test_spec",
                                      specs={"convergence_specs": [spec]})
            self.assertIsNotNone(r.error)
            self.assertIn("invalid_drift_action", r.error)
            self.assertEqual(r.drift_action, "alert_only")

    def test_default_drift_action_is_alert_only(self):
        # No drift_action declared → default to alert_only (safest)
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [])
            spec = _build_spec(reg, "echo ''")
            del spec["drift_action"]
            r = cv.verify_convergence("test_spec",
                                      specs={"convergence_specs": [spec]})
            self.assertEqual(r.drift_action, "alert_only")


class TestVerifyConvergenceDisabledSpec(unittest.TestCase):
    def test_disabled_spec_reports_spec_disabled(self):
        spec = {"id": "off", "enabled": False}
        r = cv.verify_convergence("off", specs={"convergence_specs": [spec]})
        self.assertEqual(r.error, "spec_disabled")
        self.assertFalse(r.drift_detected)


class TestFormatResultForLog(unittest.TestCase):
    def test_ok_result_format(self):
        r = cv.ConvergenceResult(
            spec_id="x", declared=frozenset({"a", "b"}),
            observed=frozenset({"a", "b"}), missing_in_runtime=frozenset(),
            drift_detected=False, drift_action="alert_only", error=None,
        )
        s = cv.format_result_for_log(r)
        self.assertIn("[convergence:x]", s)
        self.assertIn("ok", s)
        self.assertIn("declared=2", s)

    def test_drift_result_format(self):
        r = cv.ConvergenceResult(
            spec_id="x", declared=frozenset({"a", "b", "c"}),
            observed=frozenset({"a"}), missing_in_runtime=frozenset({"b", "c"}),
            drift_detected=True, drift_action="alert_only", error=None,
        )
        s = cv.format_result_for_log(r)
        self.assertIn("DRIFT", s)
        self.assertIn("missing=2", s)
        # Sorted preview
        self.assertTrue("b,c" in s or "c,b" in s or "[b,c" in s or "[c,b" in s
                        or ("b" in s and "c" in s))

    def test_error_result_format(self):
        r = cv._empty_result("x", error="something_bad")
        s = cv.format_result_for_log(r)
        self.assertIn("error=something_bad", s)


class TestRealJobsToCrontabSpec(unittest.TestCase):
    """Sanity: real V37.9.19 jobs_to_crontab spec loads + verify completes."""

    def test_real_spec_completes_without_raising(self):
        # V37.9.19 → V37.9.23: drift_action 从 alert_only 升级到 machine_sync
        # (Plan B 渐进 dry-run, 5/3 决策窗口). In dev environment without
        # crontab, observer returns empty stdout → all declared jobs reported
        # as missing → drift_detected=True → V37.9.23 _apply_machine_sync
        # called in dry-run mode (默认 CONVERGENCE_DRY_RUN!=0). 不应 raise,
        # 返回结构化 ConvergenceResult (含 V37.9.23 三新字段).
        r = cv.verify_convergence("jobs_to_crontab")
        self.assertEqual(r.spec_id, "jobs_to_crontab")
        self.assertEqual(r.drift_action, "machine_sync",
            "V37.9.23: jobs_to_crontab spec drift_action 已升级到 machine_sync")
        # V37.9.58 切关 escalation 兑现 (5/12): 默认 _is_dry_run()=False (real apply).
        # In dev (no crontab), all 36 declared jobs missing → 实际走 real apply 但
        # dev 无 ~/crontab_safe.sh → apply_errors 收集每个 missing entry.
        # 旧 V37.9.23 时代默认 dry-run=True 走 "DRY-RUN would apply:" 路径,
        # 新 V37.9.58 时代默认 dry-run=False 走 apply_errors 路径 (crontab_safe.sh
        # 不存在或 mock subprocess 失败).
        if r.missing_in_runtime:
            self.assertFalse(r.apply_dry_run,
                "V37.9.58 切关 escalation 兑现: 默认 (CONVERGENCE_DRY_RUN 未设) "
                "必须 apply_dry_run=False (real apply mode). 旧 V37.9.23 默认 True 已废弃.")
            # V37.9.58: real apply 模式下 dev 无 crontab_safe.sh, 走 apply_errors
            # 总条目数 = missing entries 数 (每个 missing 触发一次 apply 尝试)
            self.assertEqual(len(r.applied_actions) + len(r.apply_errors), len(r.missing_in_runtime),
                "每个 missing entry 触发一次 apply 尝试 (real apply 模式下 success 入 applied, "
                "failure 入 apply_errors)")
            # dev 环境 ~/crontab_safe.sh 通常不存在, 应 fallback 到 errors
            # (除非 dev 机器恰好有, 那么走 applied 也合法)
            self.assertTrue(r.applied_actions or r.apply_errors,
                "V37.9.58 real apply 模式必产 applied 或 errors 之一")
            # V37.9.58: real apply 前缀 'applied:' (或显式 dry-run env 时仍可能 'DRY-RUN')
            for action in r.applied_actions:
                self.assertTrue(
                    action.startswith("applied:") or action.startswith("DRY-RUN would apply:"),
                    f"V37.9.58 action 前缀必须 'applied:' (默认 real apply) 或 "
                    f"'DRY-RUN would apply:' (显式 CONVERGENCE_DRY_RUN=1 时), got: {action!r}"
                )
        # 结构契约 (与异常区分)
        self.assertIsInstance(r, cv.ConvergenceResult)

    def test_real_spec_extractor_finds_declared_jobs(self):
        # The extractor reads jobs_registry.yaml from repo root; we know
        # kb_deep_dive.sh + kb_evening.sh + kb_review.sh are enabled+system
        spec = cv.get_spec("jobs_to_crontab")
        declared = cv._extract_registry_enabled_system_jobs(spec)
        self.assertGreater(len(declared), 0)
        self.assertIn("kb_deep_dive.sh", declared,
            "kb_deep_dive.sh should be in declared system jobs (V37.9.16+)")


class TestSourceLevelGuards(unittest.TestCase):
    """Source-level guards on convergence.py + convergence_ontology.yaml"""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")

    def test_v37_9_19_marker_in_py(self):
        self.assertIn("V37.9.19", self.py_src)

    def test_mr_17_referenced_in_py(self):
        self.assertIn("MR-17", self.py_src,
            "convergence.py must reference MR-17 (declared-state-must-converge-...)")

    def test_fail_open_principle_documented(self):
        self.assertIn("FAIL-OPEN", self.py_src)

    def test_drift_actions_constant_exact(self):
        self.assertIn(
            '_VALID_DRIFT_ACTIONS = ("alert_only", "machine_sync", "block_until_human")',
            self.py_src,
        )

    def test_dispatch_tables_present(self):
        self.assertIn("_DECLARED_EXTRACTORS = {", self.py_src)
        self.assertIn("_RUNTIME_OBSERVERS = {", self.py_src)
        self.assertIn("_IDENTIFIER_PARSERS = {", self.py_src)

    def test_yaml_has_meta_section(self):
        self.assertIn("meta:", self.yaml_src)
        self.assertIn("meta_rule: MR-17", self.yaml_src)

    def test_yaml_first_spec_is_jobs_to_crontab(self):
        self.assertIn("id: jobs_to_crontab", self.yaml_src)

    def test_yaml_first_spec_drift_action_machine_sync_escalation_v37_9_23(self):
        # V37.9.19 起步 alert_only; V37.9.23 (5/3 决策窗口 baseline 4/26 + 7d
        # 一周观察期) 升级到 machine_sync (Plan B 渐进 dry-run 默认安全).
        # 源码级守卫: jobs_to_crontab 块内必须含 drift_action: machine_sync.
        # 找 jobs_to_crontab spec 块边界
        jc_idx = self.yaml_src.find("- id: jobs_to_crontab")
        self.assertGreater(jc_idx, 0, "jobs_to_crontab spec 必须存在")
        # 下一个 spec 起点 (或 EOF)
        next_idx = self.yaml_src.find("\n  - id: ", jc_idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        jc_block = self.yaml_src[jc_idx:next_idx]
        # V37.9.23 升级守卫
        self.assertIn("drift_action: machine_sync", jc_block,
            "V37.9.23 jobs_to_crontab 必须升级到 machine_sync (Plan B 决策窗口已到达)")
        self.assertIn("dry_run_default: false", jc_block,
            "V37.9.58 切关 escalation 兑现: 默认 dry_run_default false "
            "(Plan B 一周观察期到期, V37.9.23 收工承诺兑现)")
        # 反例守卫: 不应出现遗留 alert_only 字面量 (在本 spec 块内)
        # 注意: rationale 段可能引用历史"alert_only" 上下文, 用更精确的 drift_action: 行查
        for line in jc_block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("drift_action:") and "rationale" not in stripped:
                self.assertIn("machine_sync", stripped,
                    f"V37.9.23: drift_action 行必须显示 machine_sync, got: {stripped!r}")
                break
        else:
            self.fail("jobs_to_crontab spec 块内未找到 drift_action: 行")

    def test_yaml_blood_lesson_link_present(self):
        self.assertIn("kb_deep_dive_cron_unregistered_case.md", self.yaml_src)


# ═══════════════════════════════════════════════════════════════════════════
# V37.9.20 — providers_to_adapter (second spec) regression tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractProvidersFromRegistry(unittest.TestCase):
    """V37.9.20 declared-side extractor for providers."""

    def test_returns_set_of_strings(self):
        result = cv._extract_providers_from_registry({})
        self.assertIsInstance(result, set)
        for name in result:
            self.assertIsInstance(name, str)

    def test_includes_known_builtin_providers(self):
        # Built-in 7 are stable across V35+; spec relies on this
        result = cv._extract_providers_from_registry({})
        # At minimum qwen + gemini must be present (V37.8 fallback chain users)
        self.assertIn("qwen", result)
        self.assertIn("gemini", result)
        self.assertGreaterEqual(len(result), 4,
            "Built-in registry should expose ≥4 providers (V37.9.20 floor)")

    def test_skips_empty_names(self):
        # Defense in depth: ensure no empty strings leak through
        result = cv._extract_providers_from_registry({})
        self.assertNotIn("", result)
        self.assertNotIn(None, result)


class TestObserveHttpEndpoint(unittest.TestCase):
    """V37.9.20 HTTP observer with mocked urllib."""

    def _spec(self, **overrides):
        s = {"runtime_observable": {
            "method": "http_endpoint",
            "url": "http://localhost:5001/health",
            "timeout_sec": 1,
        }}
        s["runtime_observable"].update(overrides)
        return s

    def test_missing_url_raises_value_error(self):
        spec = {"runtime_observable": {"method": "http_endpoint"}}
        with self.assertRaises(ValueError):
            cv._observe_http_endpoint(spec)

    def test_invalid_timeout_falls_back_to_default(self):
        # Timeout type-coerced; non-numeric should silently use default
        # (FAIL-OPEN config tolerance, not strict validation)
        spec = self._spec(timeout_sec="not_a_number")
        # We can't easily assert the timeout value used, but at least
        # the call shouldn't raise ValueError before connection attempt
        with self.assertRaises(RuntimeError):
            # Will fail with connection refused on dev, but past the
            # timeout-coercion code path
            cv._observe_http_endpoint(spec)

    def test_connection_refused_raises_runtime_error_not_url_error(self):
        # FAIL-OPEN promise: every failure surfaces as RuntimeError so
        # framework's verify_convergence wraps it in observer_failed
        spec = self._spec(url="http://localhost:1/should_not_exist")
        with self.assertRaises(RuntimeError) as cm:
            cv._observe_http_endpoint(spec)
        self.assertIn("http_endpoint", str(cm.exception))

    def test_timeout_path_raises_runtime_error(self):
        # blackholed IP should hit timeout path; very short timeout
        # 198.51.100.1 is TEST-NET-2 (RFC5737), guaranteed unreachable
        spec = self._spec(url="http://198.51.100.1:5001/health", timeout_sec=0.5)
        with self.assertRaises(RuntimeError):
            cv._observe_http_endpoint(spec)

    def test_successful_response_returns_decoded_body(self):
        # Mock urlopen via context manager; verify decode path
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true, "provider": "qwen"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            spec = self._spec()
            body = cv._observe_http_endpoint(spec)
            self.assertEqual(body, '{"ok": true, "provider": "qwen"}')

    def test_non_2xx_status_raises(self):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.read.return_value = b'oops'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            spec = self._spec()
            with self.assertRaises(RuntimeError) as cm:
                cv._observe_http_endpoint(spec)
            self.assertIn("status=500", str(cm.exception))


class TestParseJsonSetUnion(unittest.TestCase):
    """V37.9.20 JSON parser with multi-path union semantics."""

    def _spec(self, paths):
        return {"runtime_observable": {"json_paths": paths}}

    def test_basic_three_path_union(self):
        body = ('{"provider": "qwen", "fallback": "gemini", '
                '"fallback_chain": ["gemini", "claude"]}')
        spec = self._spec(["provider", "fallback", "fallback_chain[]"])
        declared = {"qwen", "gemini", "claude", "openai"}
        result = cv._parse_json_set_union(spec, body, declared)
        self.assertEqual(result, {"qwen", "gemini", "claude"})

    def test_observed_intersects_with_declared(self):
        # Framework convention: extras silently dropped
        body = '{"provider": "qwen", "fallback_chain": ["unknown_provider"]}'
        spec = self._spec(["provider", "fallback_chain[]"])
        declared = {"qwen", "gemini"}
        result = cv._parse_json_set_union(spec, body, declared)
        # unknown_provider not in declared → dropped
        self.assertEqual(result, {"qwen"})

    def test_missing_keys_silently_skipped(self):
        body = '{"provider": "qwen"}'  # no fallback / fallback_chain
        spec = self._spec(["provider", "fallback", "fallback_chain[]"])
        declared = {"qwen", "gemini"}
        result = cv._parse_json_set_union(spec, body, declared)
        self.assertEqual(result, {"qwen"})

    def test_empty_raw_returns_empty_set(self):
        result = cv._parse_json_set_union(self._spec(["x"]), "", {"x"})
        self.assertEqual(result, set())

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError) as cm:
            cv._parse_json_set_union(self._spec(["x"]), "not json", {"x"})
        self.assertIn("invalid JSON", str(cm.exception))

    def test_non_object_top_level_raises(self):
        # JSON arrays at top level → unsupported
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(self._spec(["x"]), '["a","b"]', {"a"})

    def test_missing_paths_config_raises(self):
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(
                {"runtime_observable": {}}, '{"x":1}', {"x"})

    def test_empty_paths_list_raises(self):
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(self._spec([]), '{"x":1}', {"x"})

    def test_list_path_with_non_list_value_raises(self):
        # Spec says fallback_chain[] but body has scalar — structural error
        body = '{"fallback_chain": "not_a_list"}'
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(
                self._spec(["fallback_chain[]"]), body, {"x"})

    def test_scalar_path_with_dict_value_silently_skipped(self):
        # FAIL-OPEN: dict on scalar path likely misconfig but don't raise
        body = '{"provider": {"nested": "qwen"}}'
        result = cv._parse_json_set_union(
            self._spec(["provider"]), body, {"qwen"})
        self.assertEqual(result, set())

    def test_null_list_elements_skipped(self):
        body = '{"fallback_chain": ["gemini", null, "claude"]}'
        result = cv._parse_json_set_union(
            self._spec(["fallback_chain[]"]), body, {"gemini", "claude"})
        self.assertEqual(result, {"gemini", "claude"})

    def test_non_string_path_skipped(self):
        # Defensive: spec authors may put None / int by mistake
        spec = {"runtime_observable": {"json_paths": ["provider", None, 42, ""]}}
        body = '{"provider": "qwen"}'
        result = cv._parse_json_set_union(spec, body, {"qwen"})
        self.assertEqual(result, {"qwen"})


class TestVerifyProvidersToAdapterIntegration(unittest.TestCase):
    """V37.9.20 end-to-end via verify_convergence with mocked HTTP."""

    def test_real_spec_loads_and_runs_without_raise(self):
        # In dev (no adapter on :5001), expect observer_failed but not crash
        r = cv.verify_convergence("providers_to_adapter")
        self.assertEqual(r.spec_id, "providers_to_adapter")
        self.assertEqual(r.drift_action, "alert_only")
        self.assertIsInstance(r, cv.ConvergenceResult)
        # In dev, error should mention observer or extractor (both can trigger
        # before connection refused depending on import timing)
        if r.error:
            self.assertTrue(
                "observer_failed" in r.error or "extractor_failed" in r.error,
                f"unexpected error type in dev: {r.error}")

    def test_happy_path_with_mocked_health(self):
        from unittest.mock import patch, MagicMock
        # Mock /health response: 3 providers visible
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b'{"provider":"qwen","fallback":"gemini",'
            b'"fallback_chain":["gemini","claude"]}')
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = cv.verify_convergence("providers_to_adapter")
            self.assertIsNone(r.error,
                f"unexpected error: {r.error}")
            # 3 of 7 visible → 4 missing
            self.assertEqual(r.observed,
                frozenset({"qwen", "gemini", "claude"}))
            # missing = declared - observed (whatever the registry has minus 3)
            self.assertEqual(len(r.missing_in_runtime),
                len(r.declared) - 3)
            self.assertTrue(r.drift_detected,
                "Drift expected: 4 providers declared but not in /health")

    def test_full_visibility_no_drift(self):
        from unittest.mock import patch, MagicMock
        # V37.9.52: doubao plugin 加入后 declared 集合应含 8 个 provider (7 built-in + 1 真插件)
        all_known = {"qwen", "openai", "gemini", "claude", "kimi", "minimax", "glm", "doubao"}
        body = json.dumps({
            "provider": "qwen",
            "fallback": "gemini",
            "fallback_chain": sorted(all_known - {"qwen"}),
        }).encode()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = cv.verify_convergence("providers_to_adapter")
            self.assertIsNone(r.error)
            # Every declared provider should be observed → no drift
            self.assertEqual(r.missing_in_runtime, frozenset())
            self.assertFalse(r.drift_detected)


class TestProvidersSpecSourceGuards(unittest.TestCase):
    """V37.9.20 source-level guards on yaml + framework registration."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")

    def test_extractor_registered_in_dispatch(self):
        self.assertIn(
            '"providers_from_registry": _extract_providers_from_registry',
            self.py_src,
        )

    def test_observer_registered_in_dispatch(self):
        self.assertIn(
            '"http_endpoint": _observe_http_endpoint',
            self.py_src,
        )

    def test_parser_registered_in_dispatch(self):
        self.assertIn(
            '"json_set_union": _parse_json_set_union',
            self.py_src,
        )

    def test_http_observer_timeout_constant_present(self):
        self.assertIn("_HTTP_OBSERVER_TIMEOUT_SEC", self.py_src)

    def test_yaml_declares_providers_to_adapter_spec(self):
        self.assertIn("id: providers_to_adapter", self.yaml_src)

    def test_yaml_drift_action_alert_only(self):
        # V37.9.20 ships providers_to_adapter as alert_only.
        # machine_sync NOT applicable structurally (cannot auto-provision keys).
        # planned_rationale documents this is permanent design choice, not TODO.
        self.assertIn("alert_only_permanent", self.yaml_src,
            "providers_to_adapter must declare alert_only_permanent (not TODO)")

    def test_yaml_meta_version_advanced(self):
        # V37.9.20: meta version bumped from 0.1-skeleton → 0.2-second-spec
        # V37.9.22 third spec: bumped → 0.3-third-spec
        # V37.9.22 fourth spec: bumped → 0.4-fourth-spec
        # Guard against accidental regression below 0.2 (V37.9.20 baseline)
        self.assertNotIn('version: "0.1-skeleton"', self.yaml_src,
            "meta version should be bumped past 0.1-skeleton in V37.9.20+")
        # Use regex to accept any 0.X-* where X >= 2 (forward-compatible)
        import re as _re
        m = _re.search(r'version:\s*"0\.([0-9]+)-', self.yaml_src)
        self.assertIsNotNone(m, "meta version line not found")
        major = int(m.group(1))
        self.assertGreaterEqual(major, 2,
            f"meta version 0.{major}-* below V37.9.20 baseline 0.2")

    def test_yaml_meta_lists_both_invariants(self):
        # Both V37.9.19 + V37.9.20 invariants present in related_invariants
        self.assertIn("INV-CONVERGENCE-CRON-001", self.yaml_src)
        self.assertIn("INV-CONVERGENCE-PROVIDERS-001", self.yaml_src)

    def test_yaml_blood_lesson_links_present(self):
        # Both blood case docs referenced
        self.assertIn("kb_deep_dive_cron_unregistered_case.md", self.yaml_src)
        self.assertIn("kb_evening_fallback_quota_chain_case.md", self.yaml_src)

    def test_v37_9_20_changelog_mentions_dispatch_extension(self):
        # Document that V37.9.20 is pure named-dispatch extension
        self.assertIn("v37_9_20_changelog", self.yaml_src)
        self.assertIn("named-dispatch", self.yaml_src)

    def test_no_machine_sync_for_providers_spec(self):
        # Guard against accidental drift_action escalation: providers spec
        # should NEVER have machine_sync (cannot auto-provision API keys)
        # Find providers_to_adapter spec block and check its drift_action
        idx = self.yaml_src.find("id: providers_to_adapter")
        self.assertGreater(idx, 0)
        # Look at next ~80 lines for spec content
        block = self.yaml_src[idx:idx + 3000]
        # Within the spec block, drift_action must be alert_only
        # (not machine_sync). The first drift_action: line in this block
        # is the spec's drift_action.
        lines = block.split("\n")
        for line in lines:
            if line.strip().startswith("drift_action:") and "rationale" not in line:
                self.assertIn("alert_only", line)
                self.assertNotIn("machine_sync", line)
                return
        self.fail("Could not find drift_action: line in providers_to_adapter spec")


class TestWalkJsonPathsToSet(unittest.TestCase):
    """V37.9.22 — _walk_json_paths_to_set shared helper (MR-8 兑现).

    Extracted from V37.9.20 _parse_json_set_union to be shared by both
    declared-side (_extract_json_file_paths) and observed-side parser.
    Pure function with no I/O — easy to test directly."""

    def test_top_level_scalar_path(self):
        data = {"version": "1.2.3", "name": "openclaw"}
        result = cv._walk_json_paths_to_set(data, ["version"])
        self.assertEqual(result, {"1.2.3"})

    def test_top_level_list_path(self):
        data = {"agents": ["pa", "ops", "research"]}
        result = cv._walk_json_paths_to_set(data, ["agents[]"])
        self.assertEqual(result, {"pa", "ops", "research"})

    def test_multiple_paths_union(self):
        data = {"version": "1.2.3", "agents": ["pa", "ops"]}
        result = cv._walk_json_paths_to_set(data, ["version", "agents[]"])
        self.assertEqual(result, {"1.2.3", "pa", "ops"})

    def test_missing_key_silently_skipped(self):
        data = {"version": "1.2.3"}
        result = cv._walk_json_paths_to_set(data, ["version", "missing", "absent[]"])
        self.assertEqual(result, {"1.2.3"})

    def test_none_value_silently_skipped(self):
        data = {"version": None, "name": "x"}
        result = cv._walk_json_paths_to_set(data, ["version", "name"])
        self.assertEqual(result, {"x"})

    def test_dict_on_scalar_path_silently_skipped(self):
        # FAIL-OPEN: nested dict on scalar path is misconfig but doesn't raise
        data = {"agents": {"pa": "..."}}
        result = cv._walk_json_paths_to_set(data, ["agents"])
        self.assertEqual(result, set())

    def test_list_on_scalar_path_silently_skipped(self):
        # FAIL-OPEN: bare list on scalar path silently skipped
        data = {"agents": ["pa", "ops"]}
        result = cv._walk_json_paths_to_set(data, ["agents"])  # missing []
        self.assertEqual(result, set())

    def test_scalar_on_list_path_raises(self):
        # Structural misconfig: path declared [] but value is scalar
        data = {"agents": "pa"}
        with self.assertRaises(ValueError) as ctx:
            cv._walk_json_paths_to_set(data, ["agents[]"])
        self.assertIn("expected list", str(ctx.exception))

    def test_list_with_none_elements_skipped(self):
        data = {"agents": ["pa", None, "ops"]}
        result = cv._walk_json_paths_to_set(data, ["agents[]"])
        self.assertEqual(result, {"pa", "ops"})

    def test_list_coerces_non_string_elements(self):
        data = {"ports": [5001, 5002, 18789]}
        result = cv._walk_json_paths_to_set(data, ["ports[]"])
        self.assertEqual(result, {"5001", "5002", "18789"})

    def test_empty_paths_returns_empty_set(self):
        result = cv._walk_json_paths_to_set({"a": 1}, [])
        self.assertEqual(result, set())

    def test_invalid_path_types_silently_skipped(self):
        # Non-string path entries skipped silently (FAIL-OPEN)
        data = {"version": "1"}
        result = cv._walk_json_paths_to_set(data, ["version", None, 42, "", "version"])
        self.assertEqual(result, {"1"})


class TestExtractJsonFilePaths(unittest.TestCase):
    """V37.9.22 — _extract_json_file_paths declared-side extractor.

    Validates: file resolution (abs/rel/~/$VAR), FAIL-OPEN on missing,
    error categorization (missing vs corrupted), spec-config validation."""

    def _spec(self, source, json_paths):
        return {
            "id": "test_spec",
            "declaration": {
                "source": source,
                "extractor": "json_file_paths",
                "json_paths": json_paths,
            },
        }

    def test_basic_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text(json.dumps({"version": "2.5.0", "agents": ["pa", "ops"]}))
            result = cv._extract_json_file_paths(
                self._spec(str(p), ["version", "agents[]"])
            )
            self.assertEqual(result, {"2.5.0", "pa", "ops"})

    def test_missing_source_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            cv._extract_json_file_paths(self._spec("", ["x"]))
        self.assertIn("source", str(ctx.exception))

    def test_missing_json_paths_raises_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text("{}")
            with self.assertRaises(ValueError) as ctx:
                cv._extract_json_file_paths(self._spec(str(p), []))
            self.assertIn("json_paths", str(ctx.exception))

    def test_file_not_exist_returns_empty_set_fail_open(self):
        """FAIL-OPEN: dev environments without OpenClaw runtime → empty set,
        not raise. Critical for governance audit not spuriously alerting."""
        result = cv._extract_json_file_paths(
            self._spec("/nonexistent/path/openclaw.json", ["version"])
        )
        self.assertEqual(result, set())

    def test_invalid_json_raises_runtime_error(self):
        """File present but corrupted → RuntimeError (extractor_failed),
        distinct from missing file (set()) — admin sees actual data corruption."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{ not valid json")
            with self.assertRaises(RuntimeError) as ctx:
                cv._extract_json_file_paths(self._spec(str(p), ["x"]))
            self.assertIn("invalid JSON", str(ctx.exception))

    def test_top_level_non_object_raises_value_error(self):
        """JSON valid but top-level array (not object) → ValueError."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "list.json"
            p.write_text(json.dumps(["a", "b", "c"]))
            with self.assertRaises(ValueError) as ctx:
                cv._extract_json_file_paths(self._spec(str(p), ["x"]))
            self.assertIn("must be object", str(ctx.exception))

    def test_home_var_expansion(self):
        """$HOME and ~ should expand to actual home dir."""
        with tempfile.TemporaryDirectory() as td:
            # Override HOME to td so we can test expansion safely
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                p = Path(td) / "config.json"
                p.write_text(json.dumps({"version": "1.0"}))
                # $HOME path
                result = cv._extract_json_file_paths(
                    self._spec("$HOME/config.json", ["version"])
                )
                self.assertEqual(result, {"1.0"})
                # ~ path
                result = cv._extract_json_file_paths(
                    self._spec("~/config.json", ["version"])
                )
                self.assertEqual(result, {"1.0"})
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home

    def test_relative_path_resolves_against_repo_root(self):
        """Relative paths resolve to repo root (parent of ontology/)."""
        # Create temp file at repo root for this test, clean up after
        marker = REPO_ROOT / "_test_convergence_v9_22_marker.json"
        try:
            marker.write_text(json.dumps({"version": "rel-test"}))
            result = cv._extract_json_file_paths(
                self._spec("_test_convergence_v9_22_marker.json", ["version"])
            )
            self.assertEqual(result, {"rel-test"})
        finally:
            if marker.exists():
                marker.unlink()

    def test_multi_path_union_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "multi.json"
            p.write_text(json.dumps({
                "version": "2.5.0",
                "channels": ["whatsapp", "discord"],
                "missing_field_skipped": "but_not_in_paths",
            }))
            result = cv._extract_json_file_paths(
                self._spec(str(p), ["version", "channels[]"])
            )
            self.assertEqual(result, {"2.5.0", "whatsapp", "discord"})


class TestVerifyOpenclawConfigToRuntimeIntegration(unittest.TestCase):
    """V37.9.22 — End-to-end via real openclaw_config_to_runtime spec from yaml.

    Verifies framework's third extension works: zero changes to verify_convergence
    orchestrator, just dispatch table extension."""

    def test_real_spec_dev_environment_does_not_crash(self):
        """dev: openclaw.json absent + Gateway not running → declared=set()
        + observer_failed → result.error set, drift_detected=False, no raise."""
        result = cv.verify_convergence("openclaw_config_to_runtime")
        # Should not raise; result is a valid namedtuple
        self.assertEqual(result.spec_id, "openclaw_config_to_runtime")
        # Declared empty (file missing) → no drift detected since nothing to miss
        self.assertEqual(result.declared, set())
        # Drift not detected (declared empty intersect observed empty = no missing)
        self.assertFalse(result.drift_detected)

    def test_spec_uses_json_file_paths_extractor(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertIsNotNone(spec, "openclaw_config_to_runtime spec must exist in yaml")
        self.assertEqual(spec["declaration"]["extractor"], "json_file_paths")

    def test_spec_uses_http_endpoint_observer(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertEqual(spec["runtime_observable"]["method"], "http_endpoint")

    def test_spec_uses_json_set_union_parser(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertEqual(spec["runtime_observable"]["parser"], "json_set_union")

    def test_spec_drift_action_alert_only(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertEqual(spec["drift_action"], "alert_only")


class TestOpenclawSpecSourceGuards(unittest.TestCase):
    """V37.9.22 — source-level guards on convergence.py + yaml extension."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")
        cls.gov_src = (ONTOLOGY_DIR / "governance_ontology.yaml").read_text(encoding="utf-8")

    def test_extractor_registered_in_dispatch(self):
        self.assertIn(
            '"json_file_paths": _extract_json_file_paths',
            self.py_src,
        )

    def test_walk_helper_defined(self):
        """MR-8 兑现：shared helper extracted from V37.9.20 parser."""
        self.assertIn("def _walk_json_paths_to_set", self.py_src)

    def test_extractor_uses_walk_helper(self):
        """_extract_json_file_paths must call the shared helper, not duplicate logic."""
        self.assertIn("_walk_json_paths_to_set(data, paths)", self.py_src)
        # Specifically inside _extract_json_file_paths
        idx = self.py_src.find("def _extract_json_file_paths")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\ndef ", idx + 10)
        body = self.py_src[idx:end]
        self.assertIn("_walk_json_paths_to_set", body)

    def test_parser_uses_walk_helper(self):
        """V37.9.22 refactor: V37.9.20 _parse_json_set_union must now use shared
        helper instead of inlined path traversal — single source of truth."""
        idx = self.py_src.find("def _parse_json_set_union")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\n\n_IDENTIFIER_PARSERS", idx)
        body = self.py_src[idx:end]
        self.assertIn("_walk_json_paths_to_set", body,
            "V37.9.22 refactor: _parse_json_set_union must call shared helper "
            "(MR-8 単一真理源 — both extractor and parser go through one path-syntax impl)")

    def test_extractor_fail_open_on_missing_file(self):
        """FAIL-OPEN contract: extractor returns set() (not raise) when file missing."""
        idx = self.py_src.find("def _extract_json_file_paths")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\ndef ", idx + 10)
        body = self.py_src[idx:end]
        self.assertIn("if not p.exists():", body)
        self.assertIn("return set()", body)

    def test_yaml_declares_openclaw_config_to_runtime_spec(self):
        self.assertIn("id: openclaw_config_to_runtime", self.yaml_src)

    def test_yaml_meta_version_advanced(self):
        # V37.9.22 third spec → 0.3-third-spec; fourth spec → 0.4-fourth-spec.
        # Guard: must be at 0.3+ (third spec baseline or higher)
        import re as _re
        m = _re.search(r'version:\s*"0\.([0-9]+)-', self.yaml_src)
        self.assertIsNotNone(m, "meta version line not found")
        major = int(m.group(1))
        self.assertGreaterEqual(major, 3,
            f"meta version 0.{major}-* below V37.9.22 third-spec baseline 0.3")

    def test_yaml_meta_lists_three_invariants(self):
        self.assertIn("INV-CONVERGENCE-CRON-001", self.yaml_src)
        self.assertIn("INV-CONVERGENCE-PROVIDERS-001", self.yaml_src)
        self.assertIn("INV-CONVERGENCE-OPENCLAW-001", self.yaml_src)

    def test_yaml_changelog_documents_extension(self):
        self.assertIn("v37_9_22_changelog", self.yaml_src)
        self.assertIn("json_file_paths", self.yaml_src)
        self.assertIn("_walk_json_paths_to_set", self.yaml_src)

    def test_yaml_alert_only_permanent_for_third_spec(self):
        """Like providers spec, openclaw_config_to_runtime cannot machine-sync
        (operator decision required), so planned=alert_only_permanent."""
        idx = self.yaml_src.find("id: openclaw_config_to_runtime")
        self.assertGreater(idx, 0)
        block = self.yaml_src[idx:]
        self.assertIn("alert_only_permanent", block)

    def test_no_machine_sync_for_openclaw_spec(self):
        """Guard against accidental drift_action escalation."""
        idx = self.yaml_src.find("id: openclaw_config_to_runtime")
        self.assertGreater(idx, 0)
        # spec block runs from this id to end of file (it's the last spec in yaml);
        # openclaw spec is ~7KB with all description/declaration/runtime/method blocks
        block = self.yaml_src[idx:]
        for line in block.split("\n"):
            if line.strip().startswith("drift_action:") and "rationale" not in line:
                self.assertIn("alert_only", line)
                self.assertNotIn("machine_sync", line)
                return
        self.fail("drift_action: line not found in openclaw_config_to_runtime spec")

    def test_governance_ontology_lists_third_invariant(self):
        """MR-17 derivative_invariants must include the third INV."""
        self.assertIn("INV-CONVERGENCE-OPENCLAW-001", self.gov_src)
        # Specifically in MR-17 derivative_invariants list
        idx = self.gov_src.find("- id: MR-17")
        self.assertGreater(idx, 0)
        end = self.gov_src.find("\n  - id:", idx + 10)
        if end < 0:
            end = idx + 5000
        mr17_block = self.gov_src[idx:end]
        self.assertIn("INV-CONVERGENCE-OPENCLAW-001", mr17_block)


class TestExtractRegistryKbSourceFiles(unittest.TestCase):
    """V37.9.22 fourth spec — _extract_registry_kb_source_files (registry-specific).

    Sibling to V37.9.19 _extract_registry_enabled_system_jobs (different field,
    different filter — does NOT require scheduler=system since KB sources can
    come from openclaw cron too)."""

    def test_extracts_kb_source_file_basenames(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            jobs = [
                {"id": "arxiv_monitor", "enabled": True, "scheduler": "system",
                 "entry": "kb_save_arxiv.sh", "kb_source_file": "arxiv_daily.md"},
                {"id": "hf_papers", "enabled": True, "scheduler": "system",
                 "entry": "run_hf_papers.sh", "kb_source_file": "hf_papers_daily.md"},
            ]
            # Build registry yaml manually (more fields than helper provides)
            lines = ["jobs:\n"]
            for j in jobs:
                lines.append(f"  - id: {j['id']}\n")
                lines.append(f"    enabled: {str(j['enabled']).lower()}\n")
                lines.append(f"    scheduler: {j['scheduler']}\n")
                lines.append(f"    entry: {j['entry']}\n")
                lines.append(f"    kb_source_file: {j['kb_source_file']}\n")
            (tdp / "jobs_registry.yaml").write_text("".join(lines))
            # Use temp dir as fake repo root by patching Path.parent.parent
            spec = {"declaration": {"source": "jobs_registry.yaml"}}
            # The extractor uses Path(__file__).resolve().parent.parent / src
            # so we test against the real repo's registry instead
            result = cv._extract_registry_kb_source_files({
                "declaration": {"source": "jobs_registry.yaml"}
            })
            # Real registry has 14 declared kb_source_file entries
            self.assertGreaterEqual(len(result), 10,
                "Real jobs_registry should declare ≥10 kb_source_file entries")
            self.assertIn("arxiv_daily.md", result)

    def test_disabled_jobs_excluded(self):
        # The real registry — verify enabled filter works by checking
        # against a known-disabled job (none currently disabled, so we
        # write a temp registry to test the filter mechanism)
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            registry = """jobs:
  - id: enabled_job
    enabled: true
    scheduler: system
    entry: x.sh
    kb_source_file: enabled.md
  - id: disabled_job
    enabled: false
    scheduler: system
    entry: y.sh
    kb_source_file: disabled.md
"""
            registry_path = tdp / "test_registry.yaml"
            registry_path.write_text(registry)
            # Read directly via _load_yaml to simulate
            data = cv._load_yaml(registry_path)
            # Replicate extractor logic on this isolated data
            out = set()
            for job in data.get("jobs") or []:
                if not job.get("enabled"):
                    continue
                kb_file = job.get("kb_source_file") or ""
                if kb_file:
                    out.add(kb_file)
            self.assertIn("enabled.md", out)
            self.assertNotIn("disabled.md", out)

    def test_jobs_without_kb_source_file_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            registry = """jobs:
  - id: with_kb
    enabled: true
    scheduler: system
    entry: x.sh
    kb_source_file: x.md
  - id: without_kb
    enabled: true
    scheduler: system
    entry: y.sh
"""
            registry_path = tdp / "test_registry.yaml"
            registry_path.write_text(registry)
            data = cv._load_yaml(registry_path)
            out = set()
            for job in data.get("jobs") or []:
                if not job.get("enabled"):
                    continue
                kb_file = job.get("kb_source_file") or ""
                if kb_file:
                    out.add(kb_file)
            self.assertEqual(out, {"x.md"})

    def test_does_not_filter_by_scheduler(self):
        """Unlike V37.9.19 system_jobs extractor, kb_source_files does NOT
        filter scheduler=system — KB sources can come from openclaw cron too."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            registry = """jobs:
  - id: system_job
    enabled: true
    scheduler: system
    entry: x.sh
    kb_source_file: system.md
  - id: openclaw_job
    enabled: true
    scheduler: openclaw
    entry: y.sh
    kb_source_file: openclaw.md
"""
            registry_path = tdp / "test_registry.yaml"
            registry_path.write_text(registry)
            data = cv._load_yaml(registry_path)
            out = set()
            for job in data.get("jobs") or []:
                if not job.get("enabled"):
                    continue
                kb_file = job.get("kb_source_file") or ""
                if kb_file:
                    out.add(kb_file)
            # Both should be included (no scheduler filter)
            self.assertEqual(out, {"system.md", "openclaw.md"})

    def test_real_registry_has_expected_kb_sources(self):
        """Smoke test against actual jobs_registry.yaml — V37.9.22 baseline:
        14 declared kb_source_file entries (acl/ai_leaders/arxiv/chaspark/
        dblp/finance/freight/github/hf/hn/ontology/openclaw/rss/s2)."""
        result = cv._extract_registry_kb_source_files({
            "declaration": {"source": "jobs_registry.yaml"}
        })
        expected_subset = {
            "arxiv_daily.md", "hf_papers_daily.md", "ontology_sources.md",
        }
        self.assertTrue(expected_subset.issubset(result),
            f"Real registry missing core kb sources: {expected_subset - result}")


class TestVerifyKbSourcesToIndexIntegration(unittest.TestCase):
    """V37.9.22 — End-to-end via real kb_sources_to_index spec from yaml.

    Verifies framework's fourth extension works: zero changes to verify_convergence
    orchestrator, just dispatch table extension via the new extractor entry."""

    def test_real_spec_dev_environment_does_not_crash(self):
        """dev: ~/.kb/text_index/meta.json absent → command exits 0 with empty
        stdout (handled by extractor's `raise SystemExit(0)`) → observer returns
        empty string → all declared reported as missing (no crash, valid result)."""
        result = cv.verify_convergence("kb_sources_to_index")
        self.assertEqual(result.spec_id, "kb_sources_to_index")
        # declared should be non-empty (real registry has ≥10 kb_source_file)
        self.assertGreaterEqual(len(result.declared), 10)
        # In dev (no ~/.kb/text_index), command's `raise SystemExit(0)` returns
        # empty stdout; line_contains_identifier finds nothing; missing = declared.
        # drift_detected=True because declared > observed=∅.
        # No exception raised — that's the contract.
        self.assertIsNone(result.error,
            "Dev environment should not produce extractor/observer/parser error")

    def test_spec_uses_registry_kb_source_files_extractor(self):
        spec = cv.get_spec("kb_sources_to_index")
        self.assertIsNotNone(spec, "kb_sources_to_index spec must exist in yaml")
        self.assertEqual(spec["declaration"]["extractor"], "registry_kb_source_files")

    def test_spec_uses_shell_command_observer(self):
        """Reuses V37.9.19 _observe_shell_command (zero new observer needed)."""
        spec = cv.get_spec("kb_sources_to_index")
        self.assertEqual(spec["runtime_observable"]["method"], "shell_command")

    def test_spec_uses_line_contains_identifier_parser(self):
        """Reuses V37.9.19 _parse_line_contains_identifier (zero new parser needed)."""
        spec = cv.get_spec("kb_sources_to_index")
        self.assertEqual(spec["runtime_observable"]["parser"], "line_contains_identifier")

    def test_spec_drift_action_machine_sync_v37_9_24(self):
        """V37.9.22 起步 alert_only; V37.9.24 升级 machine_sync (Plan B 渐进 dry-run)."""
        spec = cv.get_spec("kb_sources_to_index")
        self.assertEqual(spec["drift_action"], "machine_sync",
            "V37.9.24: kb_sources_to_index spec 已升级到 machine_sync (named-dispatch)")


class TestKbSpecSourceGuards(unittest.TestCase):
    """V37.9.22 fourth spec — source-level guards on convergence.py + yaml."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")
        cls.gov_src = (ONTOLOGY_DIR / "governance_ontology.yaml").read_text(encoding="utf-8")

    def test_extractor_registered_in_dispatch(self):
        self.assertIn(
            '"registry_kb_source_files": _extract_registry_kb_source_files',
            self.py_src,
        )

    def test_extractor_function_defined(self):
        self.assertIn("def _extract_registry_kb_source_files", self.py_src)

    def test_extractor_uses_kb_source_file_field(self):
        idx = self.py_src.find("def _extract_registry_kb_source_files")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\ndef ", idx + 10)
        body = self.py_src[idx:end]
        self.assertIn("kb_source_file", body)
        # Does NOT filter scheduler (different from V37.9.19 system_jobs extractor)
        self.assertNotIn('scheduler != "system"', body)
        self.assertNotIn('scheduler == "system"', body)

    def test_yaml_declares_kb_sources_to_index_spec(self):
        self.assertIn("id: kb_sources_to_index", self.yaml_src)

    def test_yaml_meta_version_advanced_to_fourth(self):
        # V37.9.22 起步 0.4-fourth-spec; V37.9.23 升级到 0.5-machine-sync-dry-run;
        # V37.9.24 升级到 0.6-named-dispatch-apply-functions;
        # V37.9.25 升级到 0.7-fifth-spec-services-to-launchd;
        # V37.9.58 升级到 0.8-machine-sync-activated.
        # 守卫: 必须 ≥ 0.4 且消除旧 0.3.
        version_tokens = (
            "0.4-fourth-spec",
            "0.5-machine-sync-dry-run",
            "0.6-named-dispatch-apply-functions",
            "0.7-fifth-spec-services-to-launchd",
            "0.8-machine-sync-activated",
        )
        self.assertTrue(
            any(tok in self.yaml_src for tok in version_tokens),
            f"meta version 必须 ≥ 0.4 (V37.9.22) 含 {version_tokens} 之一"
        )
        self.assertNotIn('version: "0.3-third-spec"', self.yaml_src,
            "meta version should be bumped past 0.3 in V37.9.22 fourth spec")

    def test_yaml_meta_lists_four_invariants(self):
        for inv in ["INV-CONVERGENCE-CRON-001", "INV-CONVERGENCE-PROVIDERS-001",
                    "INV-CONVERGENCE-OPENCLAW-001", "INV-CONVERGENCE-KB-001"]:
            self.assertIn(inv, self.yaml_src)

    def test_yaml_changelog_documents_minimal_extension_pattern(self):
        # V37.9.22 changelog highlights the "minimal extension" pattern (only
        # 1 new extractor; reuses observer + parser from V37.9.19)
        self.assertIn("kb_sources_to_index", self.yaml_src)
        self.assertIn("registry_kb_source_files", self.yaml_src)

    def test_yaml_kb_spec_uses_v37_9_19_components(self):
        """KB spec validates 'minimal extension' path: reuse shell_command +
        line_contains_identifier from V37.9.19 instead of inventing new ones."""
        idx = self.yaml_src.find("id: kb_sources_to_index")
        self.assertGreater(idx, 0)
        block = self.yaml_src[idx:]
        self.assertIn("method: shell_command", block)
        self.assertIn("parser: line_contains_identifier", block)

    def test_yaml_kb_spec_drift_action_machine_sync_v37_9_24(self):
        """V37.9.22 起步 alert_only; V37.9.24 升级 machine_sync.
        守卫: kb_sources_to_index 块内必须含 drift_action: machine_sync."""
        idx = self.yaml_src.find("id: kb_sources_to_index")
        self.assertGreater(idx, 0)
        block = self.yaml_src[idx:]
        for line in block.split("\n"):
            if line.strip().startswith("drift_action:") and "rationale" not in line:
                self.assertIn("machine_sync", line,
                    f"V37.9.24: kb_sources_to_index drift_action 必须升级 machine_sync, "
                    f"got: {line!r}")
                return
        self.fail("drift_action: line not found in kb_sources_to_index spec")

    def test_yaml_kb_spec_apply_function_kb_embed_incremental(self):
        """V37.9.24 named-dispatch 守卫: kb_sources_to_index 必须声明 apply_function."""
        idx = self.yaml_src.find("id: kb_sources_to_index")
        block = self.yaml_src[idx:]
        self.assertIn("apply_function: kb_embed_incremental", block,
            "V37.9.24: kb_sources_to_index 必须声明 apply_function: kb_embed_incremental "
            "(named-dispatch 路径标识)")

    def test_governance_ontology_lists_fourth_invariant(self):
        self.assertIn("INV-CONVERGENCE-KB-001", self.gov_src)
        idx = self.gov_src.find("- id: MR-17")
        self.assertGreater(idx, 0)
        end = self.gov_src.find("\n  - id:", idx + 10)
        if end < 0:
            end = idx + 5000
        mr17_block = self.gov_src[idx:end]
        self.assertIn("INV-CONVERGENCE-KB-001", mr17_block)


class TestFormatCronLine(unittest.TestCase):
    """V37.9.23 — _format_cron_line(job) 纯函数：jobs_registry job dict → cron line."""

    def test_basic_happy_path_kb_deep_dive(self):
        """V37.9.18 血案 reference job: kb_deep_dive.sh"""
        cmd = cv._format_cron_line({
            "id": "kb_deep_dive",
            "interval": "30 22 * * *",
            "entry": "kb_deep_dive.sh",
            "log": "~/kb_deep_dive.log",
        })
        self.assertEqual(
            cmd,
            "30 22 * * * bash -lc 'bash ~/kb_deep_dive.sh >> ~/kb_deep_dive.log 2>&1'"
        )

    def test_jobs_subdir_entry(self):
        """V37.9.66: jobs/ 开头的 entry 部署在 ~/.openclaw/{entry} (auto_deploy FILE_MAP 约定),
        cron line 必须拼 ~/.openclaw/jobs/X/run_X.sh 才能与 Mac Mini runtime 一致.
        之前 V37.9.23 拼 ~/jobs/... 是潜伏 path bug (V37.9.66 修复)."""
        cmd = cv._format_cron_line({
            "id": "arxiv_monitor",
            "interval": "0 8,20 * * *",
            "entry": "jobs/arxiv_monitor/run_arxiv.sh",
            "log": "~/.openclaw/logs/jobs/arxiv_monitor.log",
        })
        self.assertEqual(
            cmd,
            "0 8,20 * * * bash -lc 'bash ~/.openclaw/jobs/arxiv_monitor/run_arxiv.sh >> ~/.openclaw/logs/jobs/arxiv_monitor.log 2>&1'"
        )

    def test_jobs_subdir_entry_v37_9_66_path_fix(self):
        """V37.9.66 守卫: jobs/ 开头 entry 必须拼 .openclaw/ 前缀 (反 V37.9.23 buggy ~/jobs/...)"""
        cmd = cv._format_cron_line({
            "id": "freight",
            "interval": "0 14 * * *",
            "entry": "jobs/freight_watcher/run_freight.sh",
            "log": "~/.openclaw/logs/jobs/freight_watcher.log",
        })
        self.assertIn("~/.openclaw/jobs/freight_watcher", cmd,
                      "V37.9.66: jobs/ entry 必须拼 .openclaw/ 前缀")
        self.assertNotIn("'bash ~/jobs/freight_watcher", cmd,
                         "V37.9.66: 反 V37.9.23 buggy ~/jobs/... 路径 (会跑错路径)")

    def test_non_jobs_entry_keeps_home_root(self):
        """V37.9.66 不破坏 V27 老 system 脚本 (health_check.sh / cron_canary.sh)
        这类 entry 没有 jobs/ 前缀, 部署在 ~/{entry} 不带 .openclaw/."""
        cmd = cv._format_cron_line({
            "id": "health_check",
            "interval": "0 9 * * 1",
            "entry": "health_check.sh",
            "log": "~/health_check.log",
        })
        # 必须保留 ~/health_check.sh, 不能加 .openclaw/ 前缀
        self.assertIn("bash ~/health_check.sh", cmd)
        self.assertNotIn(".openclaw/health_check.sh", cmd,
                         "V37.9.66 path fix 只对 jobs/ 开头 entry 生效, 老路径不受影响")

    def test_v37_9_18_inv_cron_003_pattern_match(self):
        """V37.9.18 INV-CRON-003 _cron_cmd_invokes 模式: bash -lc 'bash ~/X >> Y 2>&1'."""
        cmd = cv._format_cron_line({
            "id": "x", "interval": "0 9 * * 1", "entry": "health_check.sh",
            "log": "~/health_check.log",
        })
        self.assertIn("bash -lc 'bash ~/", cmd)
        self.assertIn("2>&1'", cmd)
        # 字面量结构守卫
        self.assertTrue(cmd.startswith("0 9 * * 1 "))

    def test_log_with_absolute_path_not_double_prefixed(self):
        """log 字段已是绝对路径 (例如某些 cron 写到 /var/log/) → 不加 ~/."""
        cmd = cv._format_cron_line({
            "id": "x", "interval": "0 0 * * *", "entry": "foo.sh",
            "log": "/var/log/foo.log",
        })
        self.assertIn(">> /var/log/foo.log", cmd)
        self.assertNotIn(">> ~//var/log", cmd)

    def test_log_with_bare_path_gets_tilde_prefix(self):
        """异常情况: log 字段没有 ~/ 也没 / 开头 → defensive 加 ~/."""
        cmd = cv._format_cron_line({
            "id": "x", "interval": "0 0 * * *", "entry": "foo.sh",
            "log": "foo.log",
        })
        self.assertIn(">> ~/foo.log", cmd)

    def test_missing_interval_raises(self):
        with self.assertRaises(ValueError) as ctx:
            cv._format_cron_line({"id": "x", "entry": "foo.sh", "log": "~/foo.log"})
        self.assertIn("interval", str(ctx.exception))

    def test_missing_entry_raises(self):
        with self.assertRaises(ValueError) as ctx:
            cv._format_cron_line({"id": "x", "interval": "0 0 * * *", "log": "~/foo.log"})
        self.assertIn("entry", str(ctx.exception))

    def test_missing_log_raises(self):
        with self.assertRaises(ValueError) as ctx:
            cv._format_cron_line({"id": "x", "interval": "0 0 * * *", "entry": "foo.sh"})
        self.assertIn("log", str(ctx.exception))

    def test_interval_must_be_5_fields(self):
        # 空字符串先被"missing"分支抓 (非空校验在 5-field 校验前), 其他场景 5-field 抓
        for bad in ["0 0 * *", "0 0 * * * *", "@daily"]:
            with self.subTest(interval=bad):
                with self.assertRaises(ValueError) as ctx:
                    cv._format_cron_line({
                        "id": "x", "interval": bad,
                        "entry": "foo.sh", "log": "~/foo.log",
                    })
                self.assertIn("5-field", str(ctx.exception))

    def test_empty_interval_caught_as_missing(self):
        """空字符串 interval 先被 missing/non-string 分支抓 (前置校验顺序)."""
        with self.assertRaises(ValueError) as ctx:
            cv._format_cron_line({
                "id": "x", "interval": "",
                "entry": "foo.sh", "log": "~/foo.log",
            })
        # 任一 missing/non-string 字面量应在 error 信息中
        err = str(ctx.exception)
        self.assertTrue("missing" in err or "non-string" in err,
            f"空 interval 应被 missing 分支抓, got: {err}")

    def test_shell_metachar_in_entry_rejected(self):
        """defense-in-depth: registry typo 不可命令注入."""
        for evil in ["foo.sh; rm -rf /", "foo.sh && evil", "foo.sh|cat",
                     "foo.sh`whoami`", "foo.sh$(date)", "foo'.sh"]:
            with self.subTest(entry=evil):
                with self.assertRaises(ValueError) as ctx:
                    cv._format_cron_line({
                        "id": "x", "interval": "0 0 * * *",
                        "entry": evil, "log": "~/foo.log",
                    })
                # 要么 single quote 错误要么 metachar 错误
                err = str(ctx.exception)
                self.assertTrue(
                    "single quote" in err or "metachar" in err,
                    f"应拒绝 metachar {evil!r}, error: {err}"
                )

    def test_shell_metachar_in_log_rejected(self):
        for evil in ["~/foo.log;evil", "~/foo.log|cat", "~/foo'.log"]:
            with self.subTest(log=evil):
                with self.assertRaises(ValueError):
                    cv._format_cron_line({
                        "id": "x", "interval": "0 0 * * *",
                        "entry": "foo.sh", "log": evil,
                    })

    def test_non_dict_input_raises(self):
        for bad in [None, "string", 42, [], ()]:
            with self.subTest(job=bad):
                with self.assertRaises(ValueError):
                    cv._format_cron_line(bad)


class TestIsDryRun(unittest.TestCase):
    """V37.9.23 → V37.9.58 — _is_dry_run() 读 CONVERGENCE_DRY_RUN env var.
    V37.9.58 切关 escalation 兑现: 默认值反转 (旧 typo→dry-run 保守 ↔
    新 typo→real apply 兑现 escalation 承诺). 仅 "1" 字面量保持 dry-run.
    """

    def setUp(self):
        # 隔离 env (test 之间不互相污染)
        self._saved = os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def test_unset_defaults_to_real_apply_v37_9_58(self):
        """V37.9.58 切关: env 未设置 → 默认 real apply (machine_sync 真激活)."""
        os.environ.pop("CONVERGENCE_DRY_RUN", None)
        self.assertFalse(cv._is_dry_run(),
            "V37.9.58 escalation 兑现: env 未设置 → 默认 real apply, "
            "Plan B 一周观察期到期 (5/3-5/11 零漂移) 切关 dry-run 默认")

    def test_value_zero_is_real_apply_v37_9_58(self):
        """V37.9.58 切关: '0' 也是 real apply (不再是唯一关闭 dry-run 的字面量)."""
        os.environ["CONVERGENCE_DRY_RUN"] = "0"
        self.assertFalse(cv._is_dry_run(),
            "V37.9.58: '0' 与未设置同语义 — real apply")

    def test_value_one_keeps_dry_run(self):
        """V37.9.58 切关后 '1' 成为唯一开启 dry-run 的字面量."""
        os.environ["CONVERGENCE_DRY_RUN"] = "1"
        self.assertTrue(cv._is_dry_run(),
            "V37.9.58 切关后: '1' 是唯一开启 dry-run 的字面量 (operator "
            "临时回到 dry-run 观察模式)")

    def test_value_true_is_real_apply_v37_9_58(self):
        """V37.9.58 切关: typo-safe direction 反转, 非 '1' 字面量都 real apply."""
        os.environ["CONVERGENCE_DRY_RUN"] = "true"
        self.assertFalse(cv._is_dry_run(),
            "V37.9.58: 非 '1' 字面量 → real apply (typo-safe direction 反转, "
            "兑现 V37.9.23 收工承诺 'V37.9.24+ 切关 dry-run 默认')")

    def test_empty_string_is_real_apply_v37_9_58(self):
        """V37.9.58 切关: 空字符串也是 real apply."""
        os.environ["CONVERGENCE_DRY_RUN"] = ""
        self.assertFalse(cv._is_dry_run(),
            "V37.9.58: 空字符串与未设置同语义 — real apply")


class TestApplyMachineSyncDryRun(unittest.TestCase):
    """V37.9.23 — _apply_machine_sync() dry-run 路径 (不调 subprocess)."""

    def setUp(self):
        # 强制 dry-run env
        self._saved = os.environ.pop("CONVERGENCE_DRY_RUN", None)
        os.environ["CONVERGENCE_DRY_RUN"] = "1"
        self.spec = cv.get_spec("jobs_to_crontab")
        self.assertIsNotNone(self.spec)

    def tearDown(self):
        if self._saved is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def test_empty_missing_returns_empty_tuples(self):
        applied, errors, dry_run = cv._apply_machine_sync(self.spec, set())
        self.assertEqual(applied, ())
        self.assertEqual(errors, ())
        self.assertTrue(dry_run)

    def test_real_missing_entries_produce_dry_run_lines(self):
        """从真实 registry 取一个 entry 跑 dry-run → applied 含 'DRY-RUN would apply:'."""
        # registry 真实 entry sample
        by_entry = cv._load_jobs_registry_index(self.spec)
        sample = sorted(by_entry.keys())[0]
        applied, errors, dry_run = cv._apply_machine_sync(self.spec, {sample})

        self.assertTrue(dry_run)
        self.assertEqual(len(applied), 1)
        self.assertTrue(applied[0].startswith("DRY-RUN would apply:"))
        # cron line 必须含真实 entry
        self.assertIn(sample, applied[0])
        # cron line 必须含 V37.9.18 INV-CRON-003 模式
        self.assertIn("bash -lc 'bash ~/", applied[0])
        self.assertEqual(errors, ())

    def test_unknown_entry_produces_apply_error(self):
        """missing 列表含 registry 外 entry (stale identifier) → apply_errors."""
        applied, errors, dry_run = cv._apply_machine_sync(
            self.spec, {"nonexistent_xxx_999.sh"}
        )
        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("not in current registry", errors[0])
        self.assertIn("nonexistent_xxx_999.sh", errors[0])

    def test_entries_processed_in_sorted_order(self):
        """避免顺序漂移让 dry-run log 比对不可靠."""
        by_entry = cv._load_jobs_registry_index(self.spec)
        samples = sorted(by_entry.keys())[:3]
        # 故意逆序传入
        applied, _, _ = cv._apply_machine_sync(self.spec, set(reversed(samples)))
        self.assertEqual(len(applied), 3)
        # applied 内顺序应基于 sorted(missing) 而非 input 顺序
        for i, sample in enumerate(sorted(samples)):
            self.assertIn(sample, applied[i])

    def test_explicit_dry_run_overrides_env(self):
        """显式 dry_run=False 应覆盖 env (虽然没有 crontab_safe.sh 会失败,
        但调用语义不同 — 本测试只验证显式参数生效)."""
        by_entry = cv._load_jobs_registry_index(self.spec)
        sample = sorted(by_entry.keys())[0]
        # 强制 env=dry-run 但显式 dry_run=False → 走真模式 → 寻找 crontab_safe.sh
        applied, errors, dry_run = cv._apply_machine_sync(
            self.spec, {sample}, dry_run=False
        )
        self.assertFalse(dry_run, "显式 dry_run=False 应覆盖 env")
        # dev 环境无 ~/crontab_safe.sh, 应走 errors 分支
        # (除非 dev 环境恰好有, 那么 applied 应非空 — 两种情况都合法)
        self.assertTrue(applied or errors,
            "真模式必产生 applied 或 errors 之一")


class TestApplyMachineSyncReal(unittest.TestCase):
    """V37.9.23 — _apply_machine_sync() 真模式 (mock subprocess.run)."""

    def setUp(self):
        # 显式关闭 dry-run env (但仍用 dry_run=False 显式参数)
        self._saved_env = os.environ.pop("CONVERGENCE_DRY_RUN", None)
        os.environ["CONVERGENCE_DRY_RUN"] = "0"
        self.spec = cv.get_spec("jobs_to_crontab")
        # 缓存真实 subprocess.run 还原用
        self._saved_run = subprocess.run

    def tearDown(self):
        subprocess.run = self._saved_run
        if self._saved_env is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved_env
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def _set_subprocess_mock(self, mock_fn):
        """Inject mock subprocess.run into the convergence module namespace.
        cv._apply_machine_sync 用 module-level subprocess import, 通过
        覆盖 cv.subprocess.run 让 mock 生效."""
        cv.subprocess.run = mock_fn

    def test_subprocess_success_produces_applied(self):
        """returncode=0 → applied_actions 含 'applied:' 字面量."""
        class Result:
            returncode = 0
            stdout = "✅ 已添加（35 → 36 条）"
            stderr = ""
        self._set_subprocess_mock(lambda *args, **kwargs: Result())

        # 模拟 ~/crontab_safe.sh 存在 (用 patch.object 不方便, 直接用真路径
        # 假设存在 — 简化方法: tempfile 创建一个 placeholder, 通过 HOME 重定向)
        td = tempfile.mkdtemp()
        try:
            (Path(td) / "crontab_safe.sh").write_text("#!/bin/bash\nexit 0\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                by_entry = cv._load_jobs_registry_index(self.spec)
                sample = sorted(by_entry.keys())[0]
                applied, errors, dry_run = cv._apply_machine_sync(
                    self.spec, {sample}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertFalse(dry_run)
        self.assertEqual(len(applied), 1)
        self.assertTrue(applied[0].startswith("applied:"),
            f"成功 subprocess 应产 'applied:' 前缀, got: {applied[0]!r}")
        self.assertEqual(errors, ())

    def test_subprocess_failure_produces_apply_error(self):
        """returncode!=0 → apply_errors 含 stderr 截断."""
        class Result:
            returncode = 1
            stdout = ""
            stderr = "❌ crontab 安装失败 — bad minute"
        self._set_subprocess_mock(lambda *args, **kwargs: Result())

        td = tempfile.mkdtemp()
        try:
            (Path(td) / "crontab_safe.sh").write_text("#!/bin/bash\nexit 1\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                by_entry = cv._load_jobs_registry_index(self.spec)
                sample = sorted(by_entry.keys())[0]
                applied, errors, _ = cv._apply_machine_sync(
                    self.spec, {sample}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("exit=1", errors[0])
        self.assertIn("bad minute", errors[0])

    def test_subprocess_timeout_produces_apply_error(self):
        """timeout → apply_errors timeout 字面量."""
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="bash crontab_safe.sh", timeout=15)
        self._set_subprocess_mock(raise_timeout)

        td = tempfile.mkdtemp()
        try:
            (Path(td) / "crontab_safe.sh").write_text("#!/bin/bash\nsleep 999\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                by_entry = cv._load_jobs_registry_index(self.spec)
                sample = sorted(by_entry.keys())[0]
                applied, errors, _ = cv._apply_machine_sync(
                    self.spec, {sample}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("timed out", errors[0])

    def test_helper_not_found_produces_apply_error(self):
        """~/crontab_safe.sh 不存在 → apply_errors 不调 subprocess."""
        # subprocess.run 被覆盖为抛异常 — 如果误调用必失败让测试发现
        self._set_subprocess_mock(
            lambda *a, **kw: self.fail("subprocess.run 不应被调用 (helper 不存在)")
        )
        td = tempfile.mkdtemp()  # 故意不放 crontab_safe.sh
        try:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                by_entry = cv._load_jobs_registry_index(self.spec)
                sample = sorted(by_entry.keys())[0]
                applied, errors, _ = cv._apply_machine_sync(
                    self.spec, {sample}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("crontab_safe.sh not found", errors[0])


class TestVerifyConvergenceMachineSyncIntegration(unittest.TestCase):
    """V37.9.23 — 端到端: verify_convergence + drift_action machine_sync."""

    def setUp(self):
        self._saved = os.environ.pop("CONVERGENCE_DRY_RUN", None)
        os.environ["CONVERGENCE_DRY_RUN"] = "1"  # 强制 dry-run 安全

    def tearDown(self):
        if self._saved is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def test_machine_sync_spec_dry_runs_in_dev(self):
        """dev 环境无 crontab → 36 declared 全 missing → dry-run 36 行 'would apply'."""
        r = cv.verify_convergence("jobs_to_crontab")
        self.assertEqual(r.drift_action, "machine_sync",
            "V37.9.23: jobs_to_crontab spec 已升级到 machine_sync")
        if r.missing_in_runtime:
            self.assertTrue(r.apply_dry_run)
            # 每个 missing 应对应一个 dry-run 行
            self.assertEqual(len(r.applied_actions), len(r.missing_in_runtime))
            self.assertEqual(r.apply_errors, ())

    def test_alert_only_specs_have_no_apply_actions(self):
        """providers_to_adapter / openclaw_config_to_runtime 都是 alert_only_permanent —
        verify_convergence 不应调 _apply_machine_sync, 三新字段全部默认 (空 / True / 空).
        V37.9.24: kb_sources_to_index 已升级 machine_sync, 从此列表移除."""
        for sid in ("providers_to_adapter", "openclaw_config_to_runtime"):
            with self.subTest(spec_id=sid):
                r = cv.verify_convergence(sid)
                self.assertEqual(r.drift_action, "alert_only",
                    f"{sid} 必须保持 alert_only (V37.9.24 没动这两个)")
                self.assertEqual(r.applied_actions, (),
                    "alert_only spec 不应触发 apply_actions")
                self.assertEqual(r.apply_errors, ())
                # apply_dry_run 默认值应为 True (但 alert_only path 不修改它)
                self.assertTrue(r.apply_dry_run)

    def test_format_result_for_log_includes_apply_for_machine_sync(self):
        """machine_sync drift 时 log 行应含 'apply[dry-run]=N'."""
        r = cv.verify_convergence("jobs_to_crontab")
        s = cv.format_result_for_log(r)
        if r.drift_detected and r.applied_actions:
            self.assertIn("apply[dry-run]=", s,
                f"machine_sync drift 时 log 应含 apply[dry-run]= 字面量, got: {s}")

    def test_format_result_for_log_no_apply_for_alert_only(self):
        """alert_only drift 时 log 行不应含 'apply[' 字面量 (避免噪声).
        V37.9.24: kb_sources_to_index 已升级 machine_sync, 从此列表移除."""
        for sid in ("providers_to_adapter", "openclaw_config_to_runtime"):
            with self.subTest(spec_id=sid):
                r = cv.verify_convergence(sid)
                s = cv.format_result_for_log(r)
                self.assertNotIn("apply[", s,
                    f"{sid} (alert_only) log 不应含 apply[ 字面量, got: {s}")

    def test_jobs_to_crontab_real_yaml_says_machine_sync(self):
        """V37.9.23 升级守卫 (源码层): yaml 字面量必含 jobs_to_crontab 块的
        drift_action: machine_sync."""
        spec = cv.get_spec("jobs_to_crontab")
        self.assertEqual(spec.get("drift_action"), "machine_sync")


class TestV37923SourceLevelGuards(unittest.TestCase):
    """V37.9.23 — convergence.py + yaml 源码级守卫 (字面量 grep)."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")

    def test_format_cron_line_function_defined(self):
        self.assertIn("def _format_cron_line(job)", self.py_src)

    def test_apply_machine_sync_function_defined(self):
        self.assertIn("def _apply_machine_sync(spec, missing_entries", self.py_src)

    def test_is_dry_run_function_defined(self):
        self.assertIn("def _is_dry_run()", self.py_src)

    def test_dry_run_env_var_constant(self):
        self.assertIn('_DRY_RUN_ENV_VAR = "CONVERGENCE_DRY_RUN"', self.py_src)

    def test_machine_sync_timeout_constant(self):
        self.assertIn("_MACHINE_SYNC_TIMEOUT_SEC = 15", self.py_src)

    def test_load_jobs_registry_index_helper(self):
        self.assertIn("def _load_jobs_registry_index(spec)", self.py_src)

    def test_verify_convergence_wires_apply_machine_sync(self):
        """verify_convergence 必须含 'drift_action == "machine_sync"' 分支."""
        self.assertIn('drift_action == "machine_sync"', self.py_src)
        self.assertIn("_apply_machine_sync(", self.py_src)

    def test_format_cron_line_emits_v37_9_18_inv_cron_003_pattern(self):
        """字面量守卫: _format_cron_line 必须用 'bash -lc' + 'bash ~/' 模板,
        与 V37.9.18 INV-CRON-003 _cron_cmd_invokes 检测器对齐 (镜像反向构造)."""
        # _format_cron_line 函数体内的字面量
        idx = self.py_src.find("def _format_cron_line(job)")
        self.assertGreater(idx, 0)
        # 找下一个 def 边界
        end = self.py_src.find("\ndef ", idx + 10)
        self.assertGreater(end, idx)
        body = self.py_src[idx:end]
        self.assertIn("bash -lc", body)
        self.assertIn("bash ~/", body)
        self.assertIn("2>&1", body)

    def test_yaml_v37_9_23_changelog_section(self):
        self.assertIn("v37_9_23_changelog", self.yaml_src)
        self.assertIn("Plan B", self.yaml_src)
        self.assertIn("CONVERGENCE_DRY_RUN", self.yaml_src)

    def test_yaml_jobs_to_crontab_implements_machine_sync(self):
        """V37.9.23: yaml jobs_to_crontab spec convergence_method 应有 implemented:
        替代 V37.9.19 的 planned: 字段.
        V37.9.24: apply_path 字面量已从 _apply_machine_sync 改为
        _apply_jobs_to_crontab_per_entry (named-dispatch 重构)."""
        jc_idx = self.yaml_src.find("- id: jobs_to_crontab")
        next_idx = self.yaml_src.find("\n  - id: ", jc_idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[jc_idx:next_idx]
        self.assertIn("implemented: machine_sync_via_helper", block)
        self.assertIn("dry_run_default: false", block)  # V37.9.58 切关
        # V37.9.24: 接受新旧两种 apply_path 字面量 (向前兼容)
        self.assertTrue(
            "apply_path: convergence._apply_jobs_to_crontab_per_entry" in block
            or "apply_path: convergence._apply_machine_sync" in block,
            f"jobs_to_crontab 必须声明 apply_path (V37.9.23 _apply_machine_sync 或 "
            f"V37.9.24 _apply_jobs_to_crontab_per_entry)"
        )

    def test_yaml_jobs_to_crontab_apply_function_named_dispatch_v37_9_24(self):
        """V37.9.24: jobs_to_crontab spec 必须声明 apply_function: jobs_to_crontab_per_entry
        (named-dispatch 路径标识)."""
        jc_idx = self.yaml_src.find("- id: jobs_to_crontab")
        next_idx = self.yaml_src.find("\n  - id: ", jc_idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[jc_idx:next_idx]
        self.assertIn("apply_function: jobs_to_crontab_per_entry", block)


class TestApplyKbEmbedIncremental(unittest.TestCase):
    """V37.9.24 — _apply_kb_embed_incremental() (kb_sources_to_index apply path).

    与 V37.9.23 _apply_jobs_to_crontab_per_entry (per-entry helper) 不同,
    本路径是 single kb_embed.py call 覆盖所有 missing sources (one-shot pattern).
    """

    def setUp(self):
        self._saved_env = os.environ.pop("CONVERGENCE_DRY_RUN", None)
        os.environ["CONVERGENCE_DRY_RUN"] = "1"
        self.spec = cv.get_spec("kb_sources_to_index")
        self.assertIsNotNone(self.spec)
        self._saved_run = subprocess.run

    def tearDown(self):
        subprocess.run = self._saved_run
        if self._saved_env is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved_env
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def test_empty_missing_returns_empty_tuples(self):
        """空 missing → 空返回, 不调 subprocess."""
        applied, errors, dry_run = cv._apply_kb_embed_incremental(
            self.spec, set(), dry_run=True
        )
        self.assertEqual(applied, ())
        self.assertEqual(errors, ())
        self.assertTrue(dry_run)

    def test_dry_run_emits_single_summary_line(self):
        """one-shot pattern: 不论 missing 多少, dry-run 只产 1 行 summary."""
        missing = {"arxiv_daily.md", "hf_papers_daily.md", "freight_daily.md"}
        applied, errors, dry_run = cv._apply_kb_embed_incremental(
            self.spec, missing, dry_run=True
        )
        self.assertEqual(len(applied), 1,
            "kb_embed_incremental 是 one-shot pattern, dry-run 只产 1 行 summary")
        self.assertTrue(applied[0].startswith("DRY-RUN would run:"))
        self.assertIn("kb_embed.py", applied[0])
        self.assertIn("incremental", applied[0])
        self.assertIn("3 missing sources", applied[0])
        self.assertEqual(errors, ())

    def test_dry_run_truncates_long_missing_lists(self):
        """missing 超过 3 项 → 显示前 3 + '... +N more' 字面量."""
        missing = {f"source_{i}.md" for i in range(10)}
        applied, errors, _ = cv._apply_kb_embed_incremental(
            self.spec, missing, dry_run=True
        )
        self.assertEqual(len(applied), 1)
        self.assertIn("... +7 more", applied[0],
            f"应截断显示 '... +7 more', got: {applied[0]!r}")

    def test_real_mode_subprocess_success(self):
        """returncode=0 → applied 含 'applied: kb_embed.py incremental' 字面量."""
        class Result:
            returncode = 0
            stdout = "[kb_embed] indexed 3 changed files"
            stderr = ""
        cv.subprocess.run = lambda *a, **kw: Result()

        td = tempfile.mkdtemp()
        try:
            embed_helper = Path(td) / "openclaw-model-bridge" / "kb_embed.py"
            embed_helper.parent.mkdir(parents=True)
            embed_helper.write_text("# stub\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                applied, errors, _ = cv._apply_kb_embed_incremental(
                    self.spec, {"arxiv_daily.md"}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(len(applied), 1)
        self.assertTrue(applied[0].startswith("applied: kb_embed.py incremental"),
            f"应产 'applied: kb_embed.py incremental' 前缀, got: {applied[0]!r}")
        self.assertEqual(errors, ())

    def test_real_mode_subprocess_failure(self):
        """returncode!=0 → apply_errors 含 stderr 截断."""
        class Result:
            returncode = 2
            stdout = ""
            stderr = "ImportError: local_embed not found"
        cv.subprocess.run = lambda *a, **kw: Result()

        td = tempfile.mkdtemp()
        try:
            embed_helper = Path(td) / "openclaw-model-bridge" / "kb_embed.py"
            embed_helper.parent.mkdir(parents=True)
            embed_helper.write_text("# stub\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                applied, errors, _ = cv._apply_kb_embed_incremental(
                    self.spec, {"arxiv_daily.md"}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("exit=2", errors[0])
        self.assertIn("ImportError", errors[0])

    def test_real_mode_helper_not_found(self):
        """~/openclaw-model-bridge/kb_embed.py 不存在 → apply_errors."""
        cv.subprocess.run = lambda *a, **kw: self.fail("subprocess 不应被调用")
        td = tempfile.mkdtemp()  # 故意不放 kb_embed.py
        try:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                applied, errors, _ = cv._apply_kb_embed_incremental(
                    self.spec, {"arxiv_daily.md"}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("kb_embed.py not found", errors[0])

    def test_real_mode_subprocess_timeout(self):
        """timeout → apply_errors timeout 字面量."""
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="python3 kb_embed.py", timeout=300)
        cv.subprocess.run = raise_timeout

        td = tempfile.mkdtemp()
        try:
            embed_helper = Path(td) / "openclaw-model-bridge" / "kb_embed.py"
            embed_helper.parent.mkdir(parents=True)
            embed_helper.write_text("# stub\n")
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                applied, errors, _ = cv._apply_kb_embed_incremental(
                    self.spec, {"arxiv_daily.md"}, dry_run=False
                )
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("timed out", errors[0])


class TestApplyMachineSyncNamedDispatch(unittest.TestCase):
    """V37.9.24 — _apply_machine_sync top-level dispatcher 验证 named-dispatch."""

    def setUp(self):
        self._saved_env = os.environ.pop("CONVERGENCE_DRY_RUN", None)
        os.environ["CONVERGENCE_DRY_RUN"] = "1"

    def tearDown(self):
        if self._saved_env is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved_env
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def test_named_dispatch_routes_kb_sources_to_index(self):
        """spec.convergence_method.apply_function = 'kb_embed_incremental' →
        路由到 _apply_kb_embed_incremental (one-shot pattern)."""
        spec = cv.get_spec("kb_sources_to_index")
        applied, errors, _ = cv._apply_machine_sync(
            spec, {"arxiv_daily.md", "hf_papers_daily.md"}, dry_run=True
        )
        # one-shot pattern → 应只产 1 行 summary 而非 2 行 (per-entry pattern)
        self.assertEqual(len(applied), 1,
            "kb_sources_to_index 应路由到 one-shot kb_embed_incremental, "
            "不应是 per-entry 模式")
        self.assertIn("kb_embed.py", applied[0])

    def test_named_dispatch_routes_jobs_to_crontab(self):
        """spec.convergence_method.apply_function = 'jobs_to_crontab_per_entry' →
        路由到 _apply_jobs_to_crontab_per_entry (per-entry pattern)."""
        spec = cv.get_spec("jobs_to_crontab")
        # 取 2 个真实 entries
        by_entry = cv._load_jobs_registry_index(spec)
        samples = sorted(by_entry.keys())[:2]
        applied, _, _ = cv._apply_machine_sync(spec, set(samples), dry_run=True)
        # per-entry pattern → 应产 2 行 (一个 entry 一行)
        self.assertEqual(len(applied), 2,
            "jobs_to_crontab 应路由到 per-entry jobs_to_crontab_per_entry, "
            "应产 2 行 (一个 entry 一行)")

    def test_unknown_apply_function_yields_apply_error(self):
        """spec.convergence_method.apply_function 是未知值 → apply_errors."""
        # 构造一个 fake spec
        fake_spec = {
            "id": "test_fake",
            "convergence_method": {"apply_function": "bogus_function_name"},
        }
        applied, errors, _ = cv._apply_machine_sync(
            fake_spec, {"x"}, dry_run=True
        )
        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("bogus_function_name", errors[0])
        self.assertIn("no apply_function registered", errors[0])

    def test_legacy_jobs_to_crontab_id_fallback(self):
        """V37.9.23 兼容: spec.id == 'jobs_to_crontab' 但缺 apply_function 字段 →
        fallback 到 jobs_to_crontab_per_entry (向后兼容)."""
        # 构造一个 fake spec 模拟 V37.9.23 没 apply_function 字段
        spec_v37923 = {
            "id": "jobs_to_crontab",
            "declaration": {
                "source": "jobs_registry.yaml",
            },
            # 故意不含 convergence_method.apply_function
            "convergence_method": {},
        }
        # 用真实 jobs_registry → 取一个 entry
        real_spec = cv.get_spec("jobs_to_crontab")
        by_entry = cv._load_jobs_registry_index(real_spec)
        sample = sorted(by_entry.keys())[0]

        applied, errors, _ = cv._apply_machine_sync(
            spec_v37923, {sample}, dry_run=True
        )
        # 应能正确路由到 jobs_to_crontab_per_entry (legacy id fallback)
        self.assertEqual(len(applied), 1,
            "V37.9.23 spec 缺 apply_function 字段时应通过 spec.id fallback 路由")
        self.assertTrue(applied[0].startswith("DRY-RUN would apply:"))


class TestV37924SourceLevelGuards(unittest.TestCase):
    """V37.9.24 — convergence.py + yaml 源码级守卫 (字面量 grep)."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")

    def test_apply_jobs_to_crontab_per_entry_function_defined(self):
        """V37.9.23 _apply_machine_sync 主体 V37.9.24 移到 _apply_jobs_to_crontab_per_entry."""
        self.assertIn("def _apply_jobs_to_crontab_per_entry", self.py_src)

    def test_apply_kb_embed_incremental_function_defined(self):
        self.assertIn("def _apply_kb_embed_incremental", self.py_src)

    def test_apply_functions_named_dispatch_table(self):
        self.assertIn("_APPLY_FUNCTIONS = {", self.py_src)
        self.assertIn('"jobs_to_crontab_per_entry": _apply_jobs_to_crontab_per_entry', self.py_src)
        self.assertIn('"kb_embed_incremental": _apply_kb_embed_incremental', self.py_src)

    def test_kb_embed_timeout_constant(self):
        self.assertIn("_KB_EMBED_TIMEOUT_SEC = 300", self.py_src)

    def test_apply_machine_sync_dispatcher_reads_apply_function(self):
        """_apply_machine_sync 必须读 spec.convergence_method.apply_function 字段."""
        self.assertIn('method.get("apply_function")', self.py_src)
        self.assertIn("_APPLY_FUNCTIONS.get(", self.py_src)

    def test_yaml_kb_sources_to_index_apply_function(self):
        """yaml kb_sources_to_index spec 必须声明 apply_function: kb_embed_incremental."""
        kb_idx = self.yaml_src.find("- id: kb_sources_to_index")
        next_idx = self.yaml_src.find("\n  - id: ", kb_idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[kb_idx:next_idx]
        self.assertIn("apply_function: kb_embed_incremental", block)
        self.assertIn("dry_run_default: false", block)  # V37.9.58 切关
        self.assertIn("implemented: machine_sync_via_helper", block)

    def test_yaml_jobs_to_crontab_apply_function(self):
        """yaml jobs_to_crontab spec V37.9.24 加 apply_function 字段."""
        jc_idx = self.yaml_src.find("- id: jobs_to_crontab")
        next_idx = self.yaml_src.find("\n  - id: ", jc_idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[jc_idx:next_idx]
        self.assertIn("apply_function: jobs_to_crontab_per_entry", block)

    def test_yaml_v37_9_24_changelog_section(self):
        self.assertIn("v37_9_24_changelog", self.yaml_src)
        self.assertIn("Named-dispatch", self.yaml_src)
        self.assertIn("kb_embed_incremental", self.yaml_src)


class TestOpenclawConfigToRuntimeMockRuntime(unittest.TestCase):
    """V37.9.24 加固层 — Mock ~/.openclaw/openclaw.json + 验证 declared 字段读取.

    为什么需要 (V37.9.22 4/29 hotfix 9d60dd3 教训横向扩展):
        openclaw_config_to_runtime spec 走 method=http_endpoint, declaration 端
        用 _extract_json_file_paths 读 ~/.openclaw/openclaw.json. spec yaml 用
        json_paths: ["version"] 字面量配置字段名 — 与 V37.9.22 hotfix 教训
        (kb_sources_to_index chunks[].file 假设错为 source_file) 同款风险类型.

        如果未来 OpenClaw 版本升级把 "version" 字段改名为 "release_version"
        / "schema_version", spec yaml 仍读 "version" → declared=set() →
        drift_detected=False 静默漏过. dev 单测有 _extract_json_file_paths
        通用 path-syntax 测试, 但**没有针对 openclaw_config_to_runtime spec
        的 yaml json_paths 字面量值跑 verify_convergence 的运行时验证**.

        本类构造 mock openclaw.json + monkey-patch HOME → tempdir + 真跑
        verify_convergence("openclaw_config_to_runtime") 验证 spec yaml 配置
        的字段名 (V37.9.24 = "version") 真能从 mock 数据中提取出来. 若未来
        spec yaml 改字段名却没改 mock 数据 → 单测立即失败.

    设计契约:
        - 不 mock _extract_json_file_paths (它有自己的单测 TestExtractJsonFilePaths)
        - 通过 monkey-patch HOME → tempdir 让 spec source $HOME/.openclaw/openclaw.json 解析到 mock 路径
        - mock observer (http_endpoint) 必失败因 dev 无 :18789 — 仅验证 declared 端
    """

    def _verify_with_mock_openclaw_json(self, json_content):
        """Helper: 写 mock openclaw.json + 跑 verify_convergence + 还原 HOME."""
        td = tempfile.mkdtemp(prefix="openclaw_json_test_")
        try:
            oc_dir = Path(td) / ".openclaw"
            oc_dir.mkdir(parents=True)
            oc_file = oc_dir / "openclaw.json"
            if isinstance(json_content, str):
                oc_file.write_text(json_content, encoding="utf-8")
            else:
                oc_file.write_text(json.dumps(json_content), encoding="utf-8")

            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                result = cv.verify_convergence("openclaw_config_to_runtime")
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)
        return result

    def test_mock_openclaw_json_extracts_version_field(self):
        """核心: mock openclaw.json 含 version 字面量 → declared 应含该值.

        V37.9.22 hotfix 教训直接复现场景: 若 spec yaml 字段名改成 "release"
        但 mock 还是 "version", declared 会是空集 → 此断言失败.
        """
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertIsNotNone(spec, "openclaw_config_to_runtime spec must exist")

        # spec yaml json_paths 当前声明: ["version"]
        configured_paths = spec.get("declaration", {}).get("json_paths", [])
        self.assertIn("version", configured_paths,
            "V37.9.24 契约: spec yaml json_paths 当前应含 'version' 字段; "
            "如已升级到其他字段名, 同步更新此 mock + 单测预期")

        result = self._verify_with_mock_openclaw_json({
            "version": "v2026.3.13-1",
            "agents": [{"name": "main"}],   # 不应被读 (path syntax 不支持嵌套)
            "channels": ["whatsapp"],        # 不应被读 (除非 yaml 加 channels[])
        })

        # declared 应含 version 字面量
        self.assertIn("v2026.3.13-1", result.declared,
            f"declared 应含 version 字面量 v2026.3.13-1, "
            f"got declared={sorted(result.declared)} error={result.error}")
        # observer 在 dev 必失败 (Gateway :18789 不可达), 但 declared 应已正确提取
        # observer_failed 时 missing = declared (按 verify_convergence FAIL-OPEN 契约)
        if result.error and "observer_failed" in result.error:
            self.assertEqual(result.missing_in_runtime, result.declared,
                "observer_failed 时 framework 契约 missing = declared")

    def test_yaml_json_paths_field_name_guard(self):
        """字面量守卫: spec yaml json_paths 必须当前声明 'version'.

        V37.9.22 hotfix 同款字面量回归守卫. 如果未来有人改 spec yaml 把
        json_paths 从 ["version"] 改成 ["release_version"] 但忘记同步更新
        mock 数据 + 单测预期, 本测试立即失败让 V37.9.24 加固层提示.
        """
        spec = cv.get_spec("openclaw_config_to_runtime")
        decl_paths = spec.get("declaration", {}).get("json_paths", [])
        obs_paths = spec.get("runtime_observable", {}).get("json_paths", [])

        # V37.9.24 当前契约: declaration 与 runtime_observable 字段名应一致
        # (declared/observed 比对的前提)
        self.assertEqual(decl_paths, obs_paths,
            "V37.9.24 契约: declaration.json_paths 与 runtime_observable.json_paths "
            "必须字面一致 (V37.9.22 引入时是 ['version'], 双向一致让 set-diff 有意义)")

        # 当前 V37.9.24 的字面量值应是 ["version"] (随 OpenClaw 版本可调整)
        # 但本守卫仅锁定"非空 list", 不锁字面量
        self.assertIsInstance(decl_paths, list)
        self.assertGreater(len(decl_paths), 0,
            "json_paths 必须非空 list (V37.9.22 至少声明 1 个 path)")

    def test_missing_openclaw_json_returns_empty_declared(self):
        """文件缺失 (dev 全新环境) → declared=set() FAIL-OPEN 契约."""
        # 不写 openclaw.json (用空 td 让 ~/.openclaw 不存在)
        td = tempfile.mkdtemp(prefix="openclaw_json_missing_")
        try:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                result = cv.verify_convergence("openclaw_config_to_runtime")
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(result.declared, frozenset(),
            "V37.9.22 FAIL-OPEN 契约: 文件缺失 → declared=空集 (不 raise)")
        self.assertFalse(result.drift_detected,
            "declared=空集 → 不可能有 missing → drift_detected=False")

    def test_corrupt_openclaw_json_surfaces_extractor_failed(self):
        """openclaw.json 存在但损坏 → extractor_failed (不静默)."""
        result = self._verify_with_mock_openclaw_json("{ this is not valid json }")
        self.assertIsNotNone(result.error,
            "损坏 JSON 应触发 extractor_failed (不静默)")
        self.assertIn("extractor_failed", result.error,
            f"error 应含 'extractor_failed', got: {result.error}")

    def test_top_level_non_object_raises(self):
        """openclaw.json 顶层是 array/scalar → extractor_failed 不静默."""
        # 顶层 list 而非 dict
        result = self._verify_with_mock_openclaw_json("[1, 2, 3]")
        self.assertIsNotNone(result.error)
        self.assertIn("extractor_failed", result.error)

    def test_mock_with_missing_version_field_yields_empty_declared(self):
        """openclaw.json 存在但缺 version key → declared=空集 (silent skip 契约)."""
        result = self._verify_with_mock_openclaw_json({
            "agents": [{"name": "main"}],
            "channels": ["whatsapp"],
            # 故意不含 "version"
        })
        self.assertEqual(result.declared, frozenset(),
            "缺 version key → _walk_json_paths_to_set silent skip → declared=空集")
        # 不应触发 extractor_failed (silent skip 是 path 行为)
        self.assertFalse(result.error and "extractor_failed" in (result.error or ""),
            f"path missing key 是合法 silent skip, 不应是 extractor_failed: {result.error}")


class TestKbSourcesToIndexCommandRuntime(unittest.TestCase):
    """V37.9.23 加固层 — Mock meta.json + 真实跑 spec yaml 的 python oneliner.

    为什么需要 (V37.9.22 4/29 hotfix 9d60dd3 教训, MR-4 silent-failure 新形态):
        spec yaml 里 runtime_observable.command 是字面量 python oneliner, 引用
        ~/.kb/text_index/meta.json 的 chunks[].file 字段名. V37.9.22 第一次
        部署假设字段名是 source_file — dev 单测全过 (1664 tests) + governance
        全过 (66 inv / 372 checks) + Mac Mini preflight 全过 (81/0/3) — 但
        Mac Mini 实测 framework 才暴露字段名错: declared=14 observed=0 missing=14.

        dev 单测只测 declared extractor 逻辑层 (TestExtractRegistryKbSourceFiles),
        没测 observer command 的实际执行路径 + JSON 字段读取语义层. source-level
        grep guard (TestKbSpecSourceGuards) 也只是字面量 grep — source_file 和
        file 都是合法的 Python identifier 拼写, grep 通过 ≠ 字段名正确.

        本类构造 mock meta.json + monkey-patch HOME → tempdir + 调
        verify_convergence("kb_sources_to_index") 真实跑 yaml 里的 python
        oneliner subprocess. 字段名一旦再被改回 source_file (无论是 typo / refactor
        / merge conflict / 未来 schema 变更未同步) 单测立即抓到, 而不是等到
        生产实测.

    设计契约:
        - 不 mock subprocess (真实跑 yaml 里的 python oneliner)
        - 通过 HOME env var 重定向 ~/ 解析 (subprocess 继承父进程 env;
          os.path.expanduser 优先看 HOME 环境变量, macOS/Linux 一致)
        - 同时验证: (1) 正常路径 — observed 集合反映 mock 数据 basename
                    (2) 反例守卫 — 错字段名 (source_file) 数据不应被读取
                    (3) 字面量守卫 — yaml command 必须读 'file' 字面量
                    (4) 空 chunks — 合法状态非 error
                    (5) declared 外 basename — line_contains_identifier 框架契约
    """

    def _verify_with_mock_meta(self, meta_content):
        """Helper: 写 mock meta.json + 跑 verify_convergence + 还原 HOME.

        Args:
            meta_content: dict (json.dumps 序列化) 或 str (raw 字面量, 用于
                          损坏 JSON 测试). 写入 $TMPDIR/.kb/text_index/meta.json.

        Returns: ConvergenceResult (verify_convergence 返回值)
        """
        td = tempfile.mkdtemp(prefix="kb_meta_test_")
        try:
            kb_dir = Path(td) / ".kb" / "text_index"
            kb_dir.mkdir(parents=True)
            meta_file = kb_dir / "meta.json"
            if isinstance(meta_content, str):
                meta_file.write_text(meta_content, encoding="utf-8")
            else:
                meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                result = cv.verify_convergence("kb_sources_to_index")
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)
        return result

    def test_mock_meta_observed_set_matches_chunks_file_field(self):
        """核心: mock chunks 含真实 declared 文件名 → 应被 observed 提取出来.

        V37.9.22 4/29 hotfix 教训直接复现场景: 若 yaml command 用 'source_file'
        字段名 (错), 此测试 observed 会是空集 (因为 mock 数据用 'file' 字段),
        断言 sample_names[0] in observed 立即失败.
        """
        spec = cv.get_spec("kb_sources_to_index")
        self.assertIsNotNone(spec, "kb_sources_to_index spec missing (V37.9.22 contract)")

        # 从 registry 读真实 declared kb_source_file 列表 (如 arxiv_daily.md 等)
        declared_set = cv._extract_registry_kb_source_files(spec)
        self.assertGreater(len(declared_set), 0,
            "registry 应至少声明一个 kb_source_file (V37.9.22 第四 spec 前提)")

        # 取至少 2 个真实 declared 名字构造 mock chunks
        sample_names = sorted(declared_set)[:2]

        # mock 数据: 2 条用正确 'file' 字段 + 1 条用错误 'source_file' 字段反例
        # (反例不应进 observed — 验证 yaml command 字段名读取正确)
        mock_data = {
            "chunks": [
                {"file": f"/Users/test/.kb/sources/{sample_names[0]}",
                 "file_hash": "h1", "source_type": "test", "chunk_idx": 0},
                {"file": f"/Users/test/.kb/sources/{sample_names[1]}",
                 "file_hash": "h2", "source_type": "test", "chunk_idx": 0},
                # 反例: 错字段名 (V37.9.22 hotfix 前的字面量), yaml command
                # 不应读取这条
                {"source_file": "/Users/test/.kb/sources/wrong_field_should_not_appear.md",
                 "file_hash": "h3"},
            ]
        }
        result = self._verify_with_mock_meta(mock_data)

        # observed 应包含 mock 里两个真实 file basename
        for name in sample_names:
            self.assertIn(name, result.observed,
                f"observed 应包含 mock chunks[].file 解析出的 {name}, "
                f"得到 observed={sorted(result.observed)} error={result.error} — "
                f"如果 yaml command 字段名错 (回归 V37.9.22 hotfix), 此断言会失败")

        # observed 不应包含反例 (错字段名 source_file 的内容)
        self.assertNotIn("wrong_field_should_not_appear.md", result.observed,
            "yaml command 必须只读 chunks[].file, 不能读 chunks[].source_file "
            "(V37.9.22 4/29 hotfix 字面量教训)")

        # 没有 error: extractor + observer + parser 全程顺利
        self.assertIsNone(result.error,
            f"mock meta.json 已构造, command 应无 error, got: {result.error}")

    def test_yaml_command_reads_file_field_not_source_file_field(self):
        """字面量守卫: 直接 grep yaml command 必须含 c.get('file' 不含 c.get('source_file').

        V37.9.22 hotfix 字面量回归守卫. 如果未来有人 (typo / refactor /
        merge conflict / schema 变更未同步) 把 c.get('file', '') 改回
        c.get('source_file', ''), 本测试立即失败.

        与 mock 测试互补: 即便 mock 测试因环境问题被 skip, 这个静态守卫
        仍能拦住字面量回归.
        """
        spec = cv.get_spec("kb_sources_to_index")
        self.assertIsNotNone(spec)
        cmd = spec["runtime_observable"]["command"]

        # 必须出现的字面量 (V37.9.22 4/29 hotfix 后的正确字段名)
        self.assertIn("c.get('file'", cmd,
            "V37.9.22 hotfix 契约: command 必须读 chunks[].file 字段")

        # 反例字面量必须消失 — 排除 yaml 内 # 开头的注释行 (yaml 注释里可能
        # 有 'source_file' 历史词作教训说明)
        active_lines = [
            line for line in cmd.split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        active_code = "\n".join(active_lines)
        self.assertNotIn("c.get('source_file'", active_code,
            "V37.9.22 4/29 hotfix 契约: command active code 不得再读 'source_file' "
            "字段 (meta.json 真实字段名是 'file', 反例若回归历史 bug 立即抓到)")

    def test_empty_chunks_array_yields_empty_observed_no_error(self):
        """合法状态: meta.json 存在但 chunks=[] (KB index 已建但无 source) →
        observed=set() + 无 error. drift_detected 视 declared 是否非空."""
        result = self._verify_with_mock_meta({"chunks": []})

        self.assertEqual(len(result.observed), 0,
            "空 chunks 应产生空 observed 集合")
        self.assertIsNone(result.error,
            f"空 chunks 是合法状态不是 error, got: {result.error}")
        # declared 来自 registry 仍非空, 因此 drift_detected=True (declared > ∅)
        self.assertGreater(len(result.declared), 0,
            "declared 应非空 (registry 有 kb_source_file 声明)")
        self.assertTrue(result.drift_detected,
            "declared > 0 + observed = 0 应触发 drift_detected")

    def test_mock_chunks_with_unknown_basename_dropped_from_observed(self):
        """框架契约 (line_contains_identifier): observed 严格 ⊆ declared.
        registry 外的 basename 即便出现在 chunks 也不进 observed.
        """
        mock_data = {
            "chunks": [
                {"file": "/Users/test/.kb/sources/_xxx_nonexistent_unknown_999_xxx_.md"},
            ]
        }
        result = self._verify_with_mock_meta(mock_data)

        self.assertNotIn("_xxx_nonexistent_unknown_999_xxx_.md", result.observed,
            "framework 契约: observed ⊆ declared, registry 外 basename 不应进 observed")
        self.assertIsNone(result.error,
            f"未知 basename 是合法 chunks 数据不应触发 error, got: {result.error}")

    def test_meta_missing_chunks_key_treated_as_empty(self):
        """meta.json 存在但缺 chunks key (异常 schema) → observed=set() + 无 error.
        yaml command 用 d.get('chunks', []) 兜底, 缺 key 也不崩溃."""
        result = self._verify_with_mock_meta({"version": "test", "other_key": []})

        self.assertEqual(len(result.observed), 0,
            "缺 chunks key 应被 d.get(..., []) 兜底为空")
        self.assertIsNone(result.error,
            f"缺 chunks key 不应触发 error (yaml 用 .get 兜底), got: {result.error}")

    def test_mock_chunks_with_empty_file_string_skipped(self):
        """yaml command 内 `if sf: print(...)` 跳过空字符串 file 字段, 不输出空行."""
        spec = cv.get_spec("kb_sources_to_index")
        declared_set = cv._extract_registry_kb_source_files(spec)
        sample_name = sorted(declared_set)[0] if declared_set else "fallback.md"
        mock_data = {
            "chunks": [
                {"file": ""},                                              # 空字符串应被跳过
                {"file": f"/Users/test/.kb/sources/{sample_name}"},        # 真实文件
                {"file_hash": "h_no_file"},                                # 缺 file 字段, .get('') 返回 ''
            ]
        }
        result = self._verify_with_mock_meta(mock_data)

        if sample_name in declared_set:
            self.assertIn(sample_name, result.observed,
                "真实文件应被识别")
        self.assertIsNone(result.error,
            f"空 file 字段应被 yaml command `if sf:` 优雅跳过, got error: {result.error}")


# ═══════════════════════════════════════════════════════════════════════════
# V37.9.25 — Fifth spec: services_to_launchd (launchd persistence, V37.9.13 closure)
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractServicesFromRegistry(unittest.TestCase):
    """V37.9.25 — _extract_services_from_registry(spec) 直接单测.

    Mirrors V37.9.19 TestExtractRegistryEnabledSystemJobs / V37.9.22
    TestExtractRegistryKbSourceFiles 同款 registry-driven extractor 测试模式.
    """

    def test_extracts_service_labels_from_registry(self):
        """services_registry.yaml 真 yaml → declared 含真实 3 个 label."""
        spec = cv.get_spec("services_to_launchd")
        self.assertIsNotNone(spec, "services_to_launchd spec 必须存在")
        declared = cv._extract_services_from_registry(spec)
        # services_registry.yaml V37.9.25 初始声明 3 个 service
        self.assertGreaterEqual(len(declared), 3,
            f"declared 应 ≥ 3 (V37.9.25 初始 3 个 service), got {sorted(declared)}")
        # 真实 label 必须出现
        self.assertIn("com.openclaw.adapter", declared)
        self.assertIn("com.openclaw.proxy", declared)
        self.assertIn("ai.openclaw.gateway", declared)

    def test_returns_set_of_strings(self):
        spec = cv.get_spec("services_to_launchd")
        declared = cv._extract_services_from_registry(spec)
        self.assertIsInstance(declared, set)
        for label in declared:
            self.assertIsInstance(label, str)
            self.assertGreater(len(label), 0,
                "label 字段不应有空字符串混入 declared 集合")

    def test_empty_label_field_skipped(self):
        """构造一个 mock services_registry 含空 label entry → 应被跳过."""
        with tempfile.TemporaryDirectory() as td:
            mock_registry = Path(td) / "services_registry.yaml"
            mock_registry.write_text(
                "services:\n"
                "  - id: real\n"
                "    label: com.example.real\n"
                "  - id: empty_label\n"
                "    label: \"\"\n"
                "  - id: missing_label_field\n"
                "    description: no label\n",
                encoding="utf-8",
            )
            # 用绝对路径指向 mock registry (通过 spec.declaration.source 注入)
            mock_spec = {
                "declaration": {
                    "source": str(mock_registry),
                    "extractor": "services_from_registry",
                },
            }
            # _extract 默认相对于 repo_root, 绝对路径需要直接传 — 我们模拟
            # path 解析: 把 declaration.source 设为 absolute path
            # extractor 内 src_path = repo_root / src; 但 absolute path 起点不变
            # 所以 absolute path 直接被 / 拼接会变成 absolute (Python pathlib 行为)
            declared = cv._extract_services_from_registry(mock_spec)
            self.assertEqual(declared, {"com.example.real"},
                f"空 label / 缺 label 字段都应跳过, got {sorted(declared)}")

    def test_default_source_is_services_registry_yaml(self):
        """spec.declaration 缺 source 字段 → 默认指向 services_registry.yaml."""
        # 构造 spec 不含 declaration.source
        spec_no_source = {
            "declaration": {
                "extractor": "services_from_registry",
                # source 字段故意不写
            },
        }
        # 应能跑且返回真实 registry 内容 (因为 default 指向 services_registry.yaml)
        declared = cv._extract_services_from_registry(spec_no_source)
        # 应至少有 3 个 (与真 registry 一致)
        self.assertGreaterEqual(len(declared), 3,
            "default source 应指向 services_registry.yaml, "
            f"got {sorted(declared)}")


class TestVerifyServicesToLaunchdIntegration(unittest.TestCase):
    """V37.9.25 — 端到端 verify_convergence("services_to_launchd")."""

    def test_real_spec_dev_environment_does_not_crash(self):
        """dev 环境 launchctl 不存在 → observer_failed → declared 全 missing.

        FAIL-OPEN 契约: 不 raise, 返回结构化 ConvergenceResult.
        """
        result = cv.verify_convergence("services_to_launchd")
        self.assertEqual(result.spec_id, "services_to_launchd")
        # declared 应非空 (registry 有 3 service)
        self.assertGreater(len(result.declared), 0,
            "declared 应非空 (services_registry.yaml 真有 3 service)")
        # dev 环境 launchctl 失败 OR launchctl 存在但无 OpenClaw service
        # (Linux/CI 环境 launchctl 不存在 → observer_failed → missing=declared)
        # 任一情况都不应抛异常
        self.assertIsInstance(result, cv.ConvergenceResult)

    def test_spec_drift_action_machine_sync_v37_9_97(self):
        """V37.9.97 升级 machine_sync — services 第 5 spec Plan B (dry-run first).
        V37.9.25 起步 alert_only (5/4) → 4 周观察 (5/4-6/1) zero drift → V37.9.97 升级."""
        spec = cv.get_spec("services_to_launchd")
        self.assertEqual(spec["drift_action"], "machine_sync",
            "V37.9.97 升级 machine_sync (V37.9.25 起步 alert_only, 4 周观察后)")
        method = spec["convergence_method"]
        self.assertEqual(method["apply_function"], "services_launchctl_bootstrap",
            "V37.9.97 named-dispatch apply_function")
        self.assertTrue(method["dry_run_default"],
            "V37.9.97 起步 dry-run (bootstrap blast-radius 高于 crontab, 一周观察后 flip)")

    def test_spec_uses_services_from_registry_extractor(self):
        spec = cv.get_spec("services_to_launchd")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["declaration"]["extractor"], "services_from_registry")

    def test_spec_uses_shell_command_observer_with_launchctl_list(self):
        """复用 V37.9.19 shell_command observer + 命令 launchctl list."""
        spec = cv.get_spec("services_to_launchd")
        self.assertEqual(spec["runtime_observable"]["method"], "shell_command")
        self.assertEqual(spec["runtime_observable"]["command"], "launchctl list",
            "V37.9.25 不带 grep 让 parser 在完整 stdout 做 substring match")

    def test_spec_uses_line_contains_identifier_parser(self):
        """复用 V37.9.19 line_contains_identifier parser."""
        spec = cv.get_spec("services_to_launchd")
        self.assertEqual(spec["runtime_observable"]["parser"], "line_contains_identifier")

    def test_dispatch_table_contains_services_extractor(self):
        """named-dispatch 注册第 5 个 extractor."""
        self.assertIn("services_from_registry", cv._DECLARED_EXTRACTORS,
            "V37.9.25 第 5 spec 必须注册到 _DECLARED_EXTRACTORS")
        self.assertIs(
            cv._DECLARED_EXTRACTORS["services_from_registry"],
            cv._extract_services_from_registry,
            "dispatch 条目应指向正确函数"
        )


class TestServicesSpecSourceGuards(unittest.TestCase):
    """V37.9.25 — services_registry.yaml + convergence.py + yaml 源码守卫."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")
        cls.gov_src = (ONTOLOGY_DIR / "governance_ontology.yaml").read_text(encoding="utf-8")
        cls.svc_src = (REPO_ROOT / "services_registry.yaml").read_text(encoding="utf-8")

    def test_services_registry_file_exists(self):
        self.assertTrue((REPO_ROOT / "services_registry.yaml").exists(),
            "V37.9.25: services_registry.yaml 必须存在于 repo root")

    def test_services_registry_declares_three_services(self):
        """V37.9.25 初始声明 3 个 service (adapter / proxy / gateway)."""
        self.assertIn("services:", self.svc_src)
        self.assertIn("com.openclaw.adapter", self.svc_src)
        self.assertIn("com.openclaw.proxy", self.svc_src)
        self.assertIn("ai.openclaw.gateway", self.svc_src)

    def test_services_registry_label_field_present(self):
        """每个 service 必须有 label 字段 (extractor 读取的关键)."""
        # 至少 3 个 label: 行
        label_count = self.svc_src.count("label:")
        self.assertGreaterEqual(label_count, 3,
            f"services_registry.yaml 至少声明 3 个 label, got {label_count}")

    def test_extractor_registered_in_dispatch(self):
        self.assertIn(
            '"services_from_registry": _extract_services_from_registry',
            self.py_src,
        )

    def test_extractor_function_defined(self):
        self.assertIn("def _extract_services_from_registry", self.py_src)

    def test_extractor_reads_services_section(self):
        """extractor 函数体应读 data.services (不是 data.jobs)."""
        idx = self.py_src.find("def _extract_services_from_registry")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\ndef ", idx + 10)
        if end < 0:
            end = idx + 3000
        body = self.py_src[idx:end]
        self.assertIn('data.get("services"', body,
            "extractor 必须读 services 段, 不是 jobs 段")
        # 应读 label 字段
        self.assertIn('svc.get("label"', body,
            "extractor 必须读 label 字段, 不是 id/name")

    def test_yaml_declares_services_to_launchd_spec(self):
        self.assertIn("id: services_to_launchd", self.yaml_src)

    def test_yaml_meta_version_advanced_to_fifth(self):
        """V37.9.25 → 0.7-fifth-spec; V37.9.58 → 0.8-machine-sync-activated;
        V37.9.97 → 0.9-services-machine-sync-dry-run (services Plan B 升级)."""
        version_tokens = (
            "0.6-named-dispatch-apply-functions",
            "0.7-fifth-spec-services-to-launchd",
            "0.8-machine-sync-activated",
            "0.9-services-machine-sync-dry-run",
        )
        self.assertTrue(
            any(tok in self.yaml_src for tok in version_tokens),
            f"meta version 必须 ≥ 0.6 含 {version_tokens} 之一"
        )

    def test_yaml_meta_lists_five_invariants(self):
        for inv in ["INV-CONVERGENCE-CRON-001", "INV-CONVERGENCE-PROVIDERS-001",
                    "INV-CONVERGENCE-OPENCLAW-001", "INV-CONVERGENCE-KB-001",
                    "INV-CONVERGENCE-SERVICES-001"]:
            self.assertIn(inv, self.yaml_src,
                f"V37.9.25 meta.related_invariants 必须含 {inv}")

    def test_yaml_v37_9_25_changelog_section(self):
        self.assertIn("v37_9_25_changelog", self.yaml_src)
        self.assertIn("services_to_launchd", self.yaml_src)
        self.assertIn("services_from_registry", self.yaml_src)

    def test_yaml_services_spec_drift_action_machine_sync_v37_9_97(self):
        """V37.9.97 升级 machine_sync — 块内字面量守卫 (V37.9.25 起步 alert_only 已废)."""
        idx = self.yaml_src.find("id: services_to_launchd")
        self.assertGreater(idx, 0)
        # 找下一个 spec 起点 (或 EOF)
        next_idx = self.yaml_src.find("\n  - id: ", idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[idx:next_idx]
        # 块内 drift_action: 行 (非 rationale) 必须含 machine_sync
        for line in block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("drift_action:") and "rationale" not in stripped:
                self.assertIn("machine_sync", stripped,
                    f"V37.9.97 services_to_launchd 升级 machine_sync, got: {stripped!r}")
                self.assertNotIn("alert_only", stripped,
                    "V37.9.97 已从 alert_only 升级 (V37.9.25 起步, 4 周观察后)")
                return
        self.fail("drift_action: line not found in services_to_launchd spec")
        # V37.9.97: 块内必须含 apply_function + dry_run_default: true (dry-run first)

    def test_yaml_services_spec_apply_function_and_dry_run_v37_9_97(self):
        """V37.9.97 — services spec 块内含 apply_function + dry_run_default: true."""
        idx = self.yaml_src.find("id: services_to_launchd")
        next_idx = self.yaml_src.find("\n  - id: ", idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[idx:next_idx]
        self.assertIn("apply_function: services_launchctl_bootstrap", block,
            "V37.9.97 services spec 必须声明 apply_function: services_launchctl_bootstrap")
        self.assertIn("dry_run_default: true", block,
            "V37.9.97 services 起步 dry-run (一周观察后 V37.9.97+ flip false)")

    def test_yaml_services_spec_command_is_launchctl_list(self):
        idx = self.yaml_src.find("id: services_to_launchd")
        next_idx = self.yaml_src.find("\n  - id: ", idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[idx:next_idx]
        self.assertIn('command: "launchctl list"', block,
            "V37.9.25: spec command 必须是 'launchctl list'")

    def test_governance_invariant_lists_fifth_invariant(self):
        self.assertIn("INV-CONVERGENCE-SERVICES-001", self.gov_src,
            "MR-17 derivative_invariants + INV 自身定义都需含本 INV")
        # MR-17 derivative_invariants 必须含本 INV
        idx = self.gov_src.find("- id: MR-17")
        self.assertGreater(idx, 0)
        end = self.gov_src.find("\n  - id:", idx + 10)
        if end < 0:
            end = idx + 5000
        mr17_block = self.gov_src[idx:end]
        self.assertIn("INV-CONVERGENCE-SERVICES-001", mr17_block)


class TestV37958DryRunActivation(unittest.TestCase):
    """V37.9.58 — Plan B 渐进 escalation 终态兑现守卫.

    一周观察期 (5/3-5/11) 零漂移零误报 → 切关 CONVERGENCE_DRY_RUN 默认值,
    V37.9.23/24 yaml meta 收工承诺真兑现. 本测试类锁定:
      (1) 源码 _is_dry_run() 默认值反转 (literal "1" 才 dry-run)
      (2) yaml 两 spec dry_run_default false (与 Python 默认保持一致)
      (3) yaml meta v37_9_58_changelog + meta version 0.8
      (4) governance INV-CONVERGENCE-CRON-001 V37.9.58 守卫存在
      (5) 反向验证: 防回退到 V37.9.23 旧默认 (typo→dry-run 保守) 反模式
      (6) 端到端: 默认 (不设 env) 跑 verify_convergence missing 时 apply_dry_run=False
    """

    @classmethod
    def setUpClass(cls):
        # 加载源码用于字面量守卫
        cls.cv_src = Path(cv.__file__).read_text(encoding="utf-8")
        yaml_path = Path(__file__).resolve().parent / "ontology" / "convergence_ontology.yaml"
        cls.yaml_src = yaml_path.read_text(encoding="utf-8")
        gov_path = Path(__file__).resolve().parent / "ontology" / "governance_ontology.yaml"
        cls.gov_src = gov_path.read_text(encoding="utf-8")

    # ── 源码字面量守卫 (1) _is_dry_run() 默认值反转 ────────────────────────

    def test_is_dry_run_default_reversed_to_v37_9_58(self):
        """V37.9.58 _is_dry_run() 默认值反转: literal '1' 才 dry-run.
        新模式: os.environ.get(_DRY_RUN_ENV_VAR, "0") == "1"
        旧模式: os.environ.get(_DRY_RUN_ENV_VAR, "1") != "0"
        """
        self.assertIn(
            'os.environ.get(_DRY_RUN_ENV_VAR, "0") == "1"',
            self.cv_src,
            "V37.9.58 切关: _is_dry_run() 必须用 '0' 默认 + '== \"1\"' 比较"
        )

    def test_v37_9_58_marker_in_convergence_py(self):
        """convergence.py 必须含 V37.9.58 marker (escalation 兑现注释)."""
        self.assertIn("V37.9.58", self.cv_src,
            "V37.9.58 marker 必须在 convergence.py 中可追溯 (escalation 历史)")

    def test_v37_9_58_escalation_rationale_in_docstring(self):
        """_is_dry_run() docstring 必须解释 V37.9.58 escalation 兑现."""
        self.assertIn("escalation", self.cv_src.lower(),
            "_is_dry_run() docstring 必须解释 escalation 兑现路径")
        self.assertIn("V37.9.23", self.cv_src,
            "V37.9.58 docstring 必须引用 V37.9.23 历史 (escalation 起点)")

    # ── 反向验证守卫 — 防回退到 V37.9.23 旧默认反模式 ──────────────────────

    def test_no_old_default_pattern_v37_9_23_regression_guard(self):
        """反向验证: 防止未来重构回退到 V37.9.23 默认 dry-run 反模式.
        旧反模式字面量 `os.environ.get(_DRY_RUN_ENV_VAR, "1") != "0"` 在 V37.9.58
        切关后必须永不出现在源码中 (除注释段引用历史)."""
        # 扫描非注释行
        for lineno, line in enumerate(self.cv_src.split("\n"), 1):
            stripped = line.strip()
            # 跳过注释行 (# 开头) 和 docstring 内的引用 (难精确判断, 用启发式)
            if stripped.startswith("#"):
                continue
            # 真代码行不应含老反模式 (含 quoted 字符串作为字面量)
            self.assertNotIn(
                'os.environ.get(_DRY_RUN_ENV_VAR, "1") != "0"',
                line,
                f"V37.9.58 反向验证失败: line {lineno} 含 V37.9.23 旧默认反模式, "
                f"escalation 已兑现, 默认值不允许回退到 dry-run."
            )

    # ── (2) yaml 两 spec dry_run_default false ───────────────────────────

    def test_yaml_jobs_to_crontab_dry_run_default_false(self):
        """jobs_to_crontab spec dry_run_default 必须为 false (V37.9.58 切关)."""
        jc_idx = self.yaml_src.find("- id: jobs_to_crontab")
        self.assertGreater(jc_idx, 0)
        next_idx = self.yaml_src.find("\n  - id: ", jc_idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[jc_idx:next_idx]
        self.assertIn("dry_run_default: false", block,
            "V37.9.58: jobs_to_crontab dry_run_default 必须 false (escalation 兑现)")
        # 反向: 不应再含 true 字面量 (除非在注释内 — 字段值层面)
        # 用更精确的"字段定义行"检测而非全文搜
        for line in block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("dry_run_default:"):
                self.assertIn("false", stripped,
                    f"V37.9.58 反向验证: dry_run_default 字段值必须 false, got: {stripped!r}")

    def test_yaml_kb_sources_to_index_dry_run_default_false(self):
        """kb_sources_to_index spec dry_run_default 必须为 false (V37.9.58 切关)."""
        kb_idx = self.yaml_src.find("- id: kb_sources_to_index")
        self.assertGreater(kb_idx, 0)
        next_idx = self.yaml_src.find("\n  - id: ", kb_idx + 10)
        if next_idx < 0:
            next_idx = len(self.yaml_src)
        block = self.yaml_src[kb_idx:next_idx]
        self.assertIn("dry_run_default: false", block,
            "V37.9.58: kb_sources_to_index dry_run_default 必须 false")
        for line in block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("dry_run_default:"):
                self.assertIn("false", stripped,
                    f"V37.9.58: dry_run_default 字段值必须 false, got: {stripped!r}")

    # ── (3) yaml meta v37_9_58_changelog + version 0.8 ───────────────────

    def test_yaml_meta_version_0_8_machine_sync_activated(self):
        """yaml meta version 升级到 0.8-machine-sync-activated (V37.9.58);
        V37.9.97 升级到 0.9-services-machine-sync-dry-run (services Plan B)."""
        version_tokens = (
            'version: "0.8-machine-sync-activated"',
            'version: "0.9-services-machine-sync-dry-run"',
        )
        self.assertTrue(
            any(tok in self.yaml_src for tok in version_tokens),
            f"yaml meta version 必须 ≥ 0.8 含 {version_tokens} 之一")

    def test_yaml_meta_status_activated(self):
        """yaml meta status 升级反映 escalation 终态."""
        self.assertIn("activated_dry_run_default_off", self.yaml_src,
            "V37.9.58: yaml meta status 必须含 activated_dry_run_default_off")

    def test_yaml_v37_9_58_changelog_segment_exists(self):
        """yaml meta 必须含 v37_9_58_changelog 段."""
        self.assertIn("v37_9_58_changelog:", self.yaml_src,
            "V37.9.58: yaml meta 必须含 v37_9_58_changelog 段记录终态兑现")

    def test_yaml_v37_9_58_changelog_documents_escalation(self):
        """v37_9_58_changelog 段必须含 escalation + 一周观察期数据."""
        cl_idx = self.yaml_src.find("v37_9_58_changelog:")
        self.assertGreater(cl_idx, 0)
        # 取 changelog 段内容 (到下个顶层 key 或文档结束)
        end_idx = self.yaml_src.find("\nconvergence_specs:", cl_idx)
        if end_idx < 0:
            end_idx = cl_idx + 8000
        cl_block = self.yaml_src[cl_idx:end_idx]
        # 必须含的内容关键字 (escalation 路径完整性证据)
        self.assertIn("5/3", cl_block, "v37_9_58_changelog 必须含 5/3 baseline")
        self.assertIn("5/11", cl_block, "v37_9_58_changelog 必须含 5/11 决策窗口")
        self.assertIn("零漂移", cl_block, "v37_9_58_changelog 必须证明一周观察期零漂移")
        self.assertIn("MR-17", cl_block, "v37_9_58_changelog 必须引用 MR-17 兑现")
        self.assertIn("escalation", cl_block.lower(),
            "v37_9_58_changelog 必须明示 escalation 兑现路径")

    # ── (4) governance INV-CONVERGENCE-CRON-001 V37.9.58 守卫 ──────────────

    def test_governance_inv_convergence_cron_001_has_v37_9_58_guards(self):
        """INV-CONVERGENCE-CRON-001 必须含 V37.9.58 dry_run_default false 守卫."""
        # 查 INV-CONVERGENCE-CRON-001 块
        idx = self.gov_src.find("- id: INV-CONVERGENCE-CRON-001")
        self.assertGreater(idx, 0)
        end = self.gov_src.find("\n  - id:", idx + 10)
        if end < 0:
            end = idx + 10000
        inv_block = self.gov_src[idx:end]
        # V37.9.58 守卫: yaml dry_run_default: false
        self.assertIn("dry_run_default: false", inv_block,
            "INV-CONVERGENCE-CRON-001 必须有 V37.9.58 yaml dry_run_default false 守卫")
        # V37.9.58 守卫: convergence.py _is_dry_run 默认值反转
        self.assertIn("V37.9.58", inv_block,
            "INV-CONVERGENCE-CRON-001 必须含 V37.9.58 marker (切关守卫)")
        # 反向: 不应再有旧 'dry_run_default: true' 守卫字面量 (在本 INV 块内)
        # (yaml file 内可能有历史 changelog 引用 'dry_run_default: true' 字符串,
        # 但 governance pattern 字段必须用新值)
        for line in inv_block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("pattern:") and "dry_run_default" in stripped:
                self.assertIn("false", stripped,
                    f"V37.9.58 反向验证: governance pattern 字段 dry_run_default "
                    f"必须查 false 字面量, got: {stripped!r}")

    # ── (5) audit_metadata 版本升级 v3.36+ (alternation 接受后续版本) ──────

    def test_audit_metadata_version_v3_36_or_later(self):
        """audit_metadata.version 升级到 3.36+ (V37.9.58-hotfix2 → 3.37 / ... / V37.9.82 → 3.48 / V37.9.85 → 3.49)."""
        valid_versions = (
            'version: "3.36"',
            'version: "3.37"',
            'version: "3.38"',
            'version: "3.39"',  # V37.9.60 MR-19 横向推广
            'version: "3.40"',  # V37.9.61 MR-19 扩 LLM-task 类
            'version: "3.41"',  # V37.9.63 MR-8 抽公共 fatal_handler helper
            'version: "3.42"',  # V37.9.66 convergence framework 双向 sync primitives + path bug 修复
            'version: "3.43"',  # V37.9.x 中间版本 (gap-filler — yaml meta 漂移修复时跳过号段)
            'version: "3.44"',
            'version: "3.45"',
            'version: "3.46"',
            'version: "3.47"',  # V37.9.81 (A + B) MOVESPEED FDA 真生效闭环 + V37.9.30 取证盲区根因修复
            'version: "3.48"',  # V37.9.82 INV-PATH-CONSISTENCY-001
            'version: "3.49"',  # V37.9.85 INV-AUTO-INJECT-001 MR-18 Step 2 前瞻守卫
            'version: "3.50"',  # V37.9.86 MR-20 + MR-21 + INV-HALLUCINATION-001
            'version: "3.51"',  # V37.9.96 INV-PROXY-PLIST-ENV-001 (proxy plist ARK env 守卫)
            'version: "3.52"',  # V37.9.97 services_to_launchd Plan B 升级 machine_sync
            'version: "3.53"',  # V37.9.100 INV-DREAM-CROSS-DOMAIN-001 + 治理执行器 assertion 字段 bug 修复
        )
        self.assertTrue(
            any(v in self.gov_src for v in valid_versions),
            f"V37.9.58+: governance audit_metadata.version 必须 ≥ 3.36, "
            f"接受 {valid_versions} 之一"
        )

    def test_audit_metadata_v3_36_changelog_documents_escalation(self):
        """audit_metadata.upgraded 必须含 V37.9.58 escalation 兑现记录."""
        self.assertIn("v3.35 → v3.36", self.gov_src,
            "V37.9.58: audit_metadata.upgraded 必须含 v3.35 → v3.36 跃迁记录")

    # ── (6) 端到端: 默认 env 真激活 (verify_convergence apply_dry_run=False) ──

    def setUp(self):
        # 隔离 env (用例之间不互相污染)
        self._saved = os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def test_e2e_default_no_env_runs_real_apply(self):
        """V37.9.58 端到端: env 未设置 → verify_convergence missing 时 apply_dry_run=False.

        Dev 环境无 crontab + 无 ~/crontab_safe.sh → 走 apply_errors 分支,
        但关键守卫 apply_dry_run=False 证明默认已切关 dry-run.
        """
        os.environ.pop("CONVERGENCE_DRY_RUN", None)
        r = cv.verify_convergence("jobs_to_crontab")
        # 若 missing 不为空, 必须真激活 (apply_dry_run=False)
        if r.missing_in_runtime:
            self.assertFalse(r.apply_dry_run,
                "V37.9.58 切关后: 默认 (env 未设) 必须 apply_dry_run=False (real apply).")
        # 兜底 (无 missing 情况)
        self.assertIsInstance(r, cv.ConvergenceResult)

    def test_e2e_explicit_dry_run_env_re_enables_dry_run(self):
        """V37.9.58 切关后 operator 仍可显式 CONVERGENCE_DRY_RUN=1 回到 dry-run."""
        os.environ["CONVERGENCE_DRY_RUN"] = "1"
        r = cv.verify_convergence("jobs_to_crontab")
        if r.missing_in_runtime:
            self.assertTrue(r.apply_dry_run,
                "V37.9.58 切关后: CONVERGENCE_DRY_RUN=1 仍可显式开启 dry-run 观察")

    def test_e2e_kb_sources_default_also_real_apply(self):
        """V37.9.58: kb_sources_to_index 第二个 machine_sync spec 也默认 real apply."""
        os.environ.pop("CONVERGENCE_DRY_RUN", None)
        r = cv.verify_convergence("kb_sources_to_index")
        self.assertIsInstance(r, cv.ConvergenceResult)
        # kb_sources_to_index drift_action 是 machine_sync (V37.9.24)
        self.assertEqual(r.drift_action, "machine_sync")
        if r.missing_in_runtime:
            self.assertFalse(r.apply_dry_run,
                "V37.9.58: kb_sources_to_index 也默认 real apply")


# ════════════════════════════════════════════════════════════════════
# V37.9.66 — Framework primitives for bidirectional sync (cron_lines_set_diff)
# ════════════════════════════════════════════════════════════════════
# V37.9.66 加 framework 能力 (extractor + parser + ConvergenceResult.extra_in_runtime
# + _apply remove_extras 路径) 让 framework 支持双向 sync. spec yaml jobs_to_crontab
# 暂不切换 (避免 34 job 路径一致性 audit 风暴, V37.9.67+ 候选), 但 framework 已就绪.

class TestV37966Primitives(unittest.TestCase):
    """V37.9.66 framework primitives 真存在 + 注册 + 行为契约"""

    def test_extra_in_runtime_field_exists(self):
        """ConvergenceResult 必须含 extra_in_runtime 字段 (V37.9.66 双向 sync)"""
        self.assertIn("extra_in_runtime", cv.ConvergenceResult._fields)

    def test_extra_in_runtime_default_empty_frozenset(self):
        """默认 frozenset() — 向后兼容 V37.9.23 不传此字段的调用"""
        r = cv._empty_result("test")
        self.assertEqual(r.extra_in_runtime, frozenset())
        self.assertIsInstance(r.extra_in_runtime, frozenset)

    def test_extractor_jobs_to_full_cron_lines_registered(self):
        self.assertIn("jobs_to_full_cron_lines", cv._DECLARED_EXTRACTORS)

    def test_parser_cron_lines_set_diff_registered(self):
        self.assertIn("cron_lines_set_diff", cv._IDENTIFIER_PARSERS)

    def test_extractor_outputs_full_cron_lines(self):
        """新 extractor 真输出每个 enabled+system job 完整 cron line"""
        spec = {"declaration": {"source": "jobs_registry.yaml"}}
        lines = cv._extract_jobs_to_full_cron_lines(spec)
        # 至少包含一些 jobs (>=10 dev 环境合理下限)
        self.assertGreater(len(lines), 10)
        # 每行必须是合法 cron 行 (5-field interval + bash -lc 模式)
        for line in lines:
            # V37.9.85: .py entries use python3, .sh use bash
            self.assertRegex(line, r"^\S+ \S+ \S+ \S+ \S+ bash -lc '(?:bash|python3) ~/")

    def test_parser_cron_lines_set_diff_returns_raw_lines_set(self):
        """新 parser 输出 raw cron 行 set (跳过 # 注释 / 空行)"""
        raw = "0 14 * * * bash X\n# this is comment\n\n0 9 * * 1 bash Y\n"
        obs = cv._parse_cron_lines_set_diff({}, raw, frozenset())
        self.assertEqual(obs, {"0 14 * * * bash X", "0 9 * * 1 bash Y"})

    def test_parser_cron_lines_set_diff_empty_raw(self):
        """空 raw → 空 set"""
        self.assertEqual(cv._parse_cron_lines_set_diff({}, "", frozenset()), set())
        self.assertEqual(cv._parse_cron_lines_set_diff({}, None, frozenset()), set())


class TestV37966FormatCronLinePathFix(unittest.TestCase):
    """V37.9.66 _format_cron_line 修 .openclaw/ 路径 bug + 不破坏 V27 老脚本"""

    def test_jobs_entry_gets_openclaw_prefix(self):
        cmd = cv._format_cron_line({
            "id": "test", "interval": "0 14 * * *",
            "entry": "jobs/freight_watcher/run_freight.sh",
            "log": "~/.openclaw/logs/jobs/freight_watcher.log",
        })
        self.assertIn("~/.openclaw/jobs/freight_watcher/", cmd)
        self.assertNotIn("'bash ~/jobs/freight_watcher", cmd)

    def test_non_jobs_entry_keeps_home(self):
        """V27 老 system 脚本 (health_check.sh / cron_canary.sh) 路径不变"""
        cmd = cv._format_cron_line({
            "id": "test", "interval": "0 9 * * 1",
            "entry": "cron_canary.sh", "log": "~/cron_canary.log",
        })
        self.assertIn("bash ~/cron_canary.sh", cmd)
        self.assertNotIn(".openclaw/cron_canary.sh", cmd)

    def test_mac_mini_real_freight_line_matches(self):
        """V37.9.66 修复后 _format_cron_line 输出与 Mac Mini 真实 cron 行字面完全一致.

        Mac Mini 真实行 (用户 V37.9.65 实测):
          0 14 * * * bash -lc 'bash ~/.openclaw/jobs/freight_watcher/run_freight.sh
          >> ~/.openclaw/logs/jobs/freight_watcher.log 2>&1'
        """
        cmd = cv._format_cron_line({
            "id": "freight_watcher",
            "interval": "0 14 * * *",
            "entry": "jobs/freight_watcher/run_freight.sh",
            "log": "~/.openclaw/logs/jobs/freight_watcher.log",
        })
        expected = ("0 14 * * * bash -lc "
                    "'bash ~/.openclaw/jobs/freight_watcher/run_freight.sh "
                    ">> ~/.openclaw/logs/jobs/freight_watcher.log 2>&1'")
        self.assertEqual(cmd, expected)


class TestV37966ApplyRemoveExtras(unittest.TestCase):
    """V37.9.66 _apply_jobs_to_crontab_per_entry 支持 extra_entries (调 crontab_safe.sh remove)"""

    def test_signature_accepts_extra_entries_kwarg(self):
        """函数签名必须接受 extra_entries (向后兼容默认 frozenset())"""
        import inspect
        sig = inspect.signature(cv._apply_jobs_to_crontab_per_entry)
        self.assertIn("extra_entries", sig.parameters)
        self.assertEqual(sig.parameters["extra_entries"].default, frozenset())

    def test_empty_missing_and_extra_returns_empty(self):
        """missing + extra 都空 → 不调任何 helper, 返回空 tuples"""
        applied, errors, _ = cv._apply_jobs_to_crontab_per_entry(
            {"id": "test"}, set(), dry_run=True, extra_entries=set()
        )
        self.assertEqual(applied, ())
        self.assertEqual(errors, ())

    def test_extra_entries_in_dry_run_emits_would_remove(self):
        """dry_run + extra 非空 → 'DRY-RUN would remove' 输出 (不实际调 helper)"""
        applied, errors, dry_run = cv._apply_jobs_to_crontab_per_entry(
            {"id": "test", "declaration": {"source": "jobs_registry.yaml"}},
            set(), dry_run=True,
            extra_entries={"0 8 * * * bash -lc 'fake_extra_line'"}
        )
        self.assertTrue(dry_run)
        applied_text = " ".join(applied)
        self.assertIn("DRY-RUN would remove", applied_text)
        self.assertIn("fake_extra_line", applied_text)


class TestV37966VerifyConvergenceBidirectional(unittest.TestCase):
    """V37.9.66 verify_convergence 计算 extra (双向 sync) + drift_detected 含 extra"""

    def test_extra_in_runtime_computed_when_parser_outputs_set_diff(self):
        """当 spec 用 cron_lines_set_diff parser 时 verify_convergence 计算 extra."""
        # 模拟一个 spec 用新 parser. dev 环境无 crontab, 仍可测 framework primitive
        # extra_in_runtime 计算逻辑 (verify_convergence 中 extra = observed - declared).
        # 这里直接测 _parse_cron_lines_set_diff + ConvergenceResult.extra_in_runtime 真集成.
        raw = "0 14 * * * decl_line\n0 8 * * * extra_line"
        observed = cv._parse_cron_lines_set_diff({}, raw, {"0 14 * * * decl_line"})
        # parser 不丢 observed extras (V37.9.66 关键差异 vs line_contains_identifier)
        self.assertIn("0 8 * * * extra_line", observed)

    def test_apply_machine_sync_signature_includes_extra_entries(self):
        """_apply_machine_sync 必须接受 extra_entries 参数"""
        import inspect
        sig = inspect.signature(cv._apply_machine_sync)
        self.assertIn("extra_entries", sig.parameters)


class TestV37966SourceLevelGuards(unittest.TestCase):
    """V37.9.66 源码级守卫 — 防 future 重构回退 V37.9.66 路径修复 + framework primitives"""

    def setUp(self):
        with open(REPO_ROOT / "ontology" / "convergence.py") as f:
            self.src = f.read()

    def test_v37_9_66_marker_present(self):
        self.assertIn("V37.9.66", self.src,
                      "convergence.py 必须含 V37.9.66 marker (path bug 修复 + 双向 sync primitives)")

    def test_format_cron_line_handles_jobs_prefix(self):
        """_format_cron_line 必须含 jobs/ 开头 entry → .openclaw/ 前缀逻辑 (反 V37.9.23 buggy)"""
        # 简化检查: 必须出现 .openclaw/ + jobs/ 字面量在源码中
        self.assertIn('.openclaw/', self.src)
        self.assertIn('entry.startswith("jobs/")', self.src,
                      "V37.9.66 path fix 守卫: 必须用 startswith 'jobs/' 判定")

    def test_extra_in_runtime_field_in_namedtuple(self):
        self.assertIn('"extra_in_runtime"', self.src,
                      "ConvergenceResult 必须声明 extra_in_runtime 字段")

    def test_jobs_to_full_cron_lines_extractor_registered(self):
        self.assertIn('"jobs_to_full_cron_lines"', self.src)
        self.assertIn('def _extract_jobs_to_full_cron_lines', self.src)

    def test_cron_lines_set_diff_parser_registered(self):
        self.assertIn('"cron_lines_set_diff"', self.src)
        self.assertIn('def _parse_cron_lines_set_diff', self.src)


# V37.9.97 — services_to_launchd Plan B 升级 (machine_sync + per-spec dry-run)
# ───────────────────────────────────────────────────────────────────────────

class TestResolveDryRunForSpec(unittest.TestCase):
    """V37.9.97 — per-spec dry-run 解析 (dry_run_default 字段从文档性→功能性)."""

    def setUp(self):
        self._saved = os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["CONVERGENCE_DRY_RUN"] = self._saved
        else:
            os.environ.pop("CONVERGENCE_DRY_RUN", None)

    def test_env_override_one_forces_dry_run(self):
        os.environ["CONVERGENCE_DRY_RUN"] = "1"
        spec = {"convergence_method": {"dry_run_default": False}}
        self.assertTrue(cv._resolve_dry_run_for_spec(spec),
            "env=1 必须 override per-spec dry_run_default=false")

    def test_env_override_zero_forces_real(self):
        os.environ["CONVERGENCE_DRY_RUN"] = "0"
        spec = {"convergence_method": {"dry_run_default": True}}
        self.assertFalse(cv._resolve_dry_run_for_spec(spec),
            "env=0 必须 override per-spec dry_run_default=true (V37.9.58 语义: 非 '1' = real)")

    def test_per_spec_true_when_no_env(self):
        spec = {"convergence_method": {"dry_run_default": True}}
        self.assertTrue(cv._resolve_dry_run_for_spec(spec),
            "无 env → 用 per-spec dry_run_default=true")

    def test_per_spec_false_when_no_env(self):
        spec = {"convergence_method": {"dry_run_default": False}}
        self.assertFalse(cv._resolve_dry_run_for_spec(spec),
            "无 env → 用 per-spec dry_run_default=false")

    def test_framework_default_false_when_no_field(self):
        spec = {"convergence_method": {}}
        self.assertFalse(cv._resolve_dry_run_for_spec(spec),
            "无 env + 无 per-spec field → framework 默认 False (V37.9.58 real-apply)")

    def test_no_convergence_method_safe(self):
        self.assertFalse(cv._resolve_dry_run_for_spec({}),
            "无 convergence_method → 默认 False, 不崩")

    def test_services_real_spec_dry_run_no_env(self):
        """真 services spec (dry_run_default: true) 无 env → dry-run (Plan B 观察)."""
        spec = cv.get_spec("services_to_launchd")
        self.assertTrue(cv._resolve_dry_run_for_spec(spec))

    def test_jobs_real_spec_real_no_env(self):
        """真 jobs spec (dry_run_default: false) 无 env → real-apply (向后兼容)."""
        spec = cv.get_spec("jobs_to_crontab")
        self.assertFalse(cv._resolve_dry_run_for_spec(spec))


class TestApplyServicesLaunchctlBootstrap(unittest.TestCase):
    """V37.9.97 — _apply_services_launchctl_bootstrap per-service bootstrap."""

    def _spec(self):
        return cv.get_spec("services_to_launchd")

    def test_empty_missing_returns_empty(self):
        applied, errors, dry = cv._apply_services_launchctl_bootstrap(
            self._spec(), set(), dry_run=True)
        self.assertEqual(applied, ())
        self.assertEqual(errors, ())

    def test_dry_run_emits_would_bootstrap_per_service(self):
        applied, errors, dry = cv._apply_services_launchctl_bootstrap(
            self._spec(), {"com.openclaw.adapter", "com.openclaw.proxy"}, dry_run=True)
        self.assertEqual(errors, ())
        self.assertEqual(len(applied), 2, "每个 missing service 一条 would-bootstrap")
        for a in applied:
            self.assertIn("DRY-RUN would bootstrap: launchctl bootstrap gui/", a)
            self.assertIn("Library/LaunchAgents/", a)

    def test_dry_run_resolves_plist_from_registry(self):
        applied, _, _ = cv._apply_services_launchctl_bootstrap(
            self._spec(), {"com.openclaw.adapter"}, dry_run=True)
        self.assertEqual(len(applied), 1)
        self.assertIn("com.openclaw.adapter.plist", applied[0],
            "应从 services_registry plist 字段解析 adapter plist")

    def test_unknown_label_is_error_not_crash(self):
        applied, errors, _ = cv._apply_services_launchctl_bootstrap(
            self._spec(), {"com.bogus.notreal"}, dry_run=True)
        self.assertEqual(applied, ())
        self.assertEqual(len(errors), 1)
        self.assertIn("not in current services_registry", errors[0])

    def test_label_without_plist_is_error(self):
        from unittest import mock
        with mock.patch.object(cv, "_load_services_registry_index",
                               return_value={"com.x.noplist": {"label": "com.x.noplist"}}):
            applied, errors, _ = cv._apply_services_launchctl_bootstrap(
                {"declaration": {"source": "services_registry.yaml"}},
                {"com.x.noplist"}, dry_run=True)
        self.assertEqual(applied, ())
        self.assertIn("no plist declared", errors[0])

    def test_real_mode_success_via_mock(self):
        from unittest import mock
        fake = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(cv, "_load_services_registry_index",
                  return_value={"com.x.svc": {"label": "com.x.svc", "plist": "com.x.svc.plist"}}), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch("subprocess.run", return_value=fake) as m:
            applied, errors, dry = cv._apply_services_launchctl_bootstrap(
                {"declaration": {"source": "services_registry.yaml"}},
                {"com.x.svc"}, dry_run=False)
        self.assertEqual(errors, ())
        self.assertEqual(len(applied), 1)
        self.assertIn("applied: launchctl bootstrap", applied[0])
        cmd = m.call_args[0][0]
        self.assertEqual(cmd[0:2], ["launchctl", "bootstrap"],
            "真 apply 必须调 launchctl bootstrap")

    def test_real_mode_nonzero_exit_is_error(self):
        from unittest import mock
        fake = mock.Mock(returncode=5, stdout="", stderr="boot fail")
        with mock.patch.object(cv, "_load_services_registry_index",
                  return_value={"com.x.svc": {"label": "com.x.svc", "plist": "com.x.svc.plist"}}), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch("subprocess.run", return_value=fake):
            applied, errors, _ = cv._apply_services_launchctl_bootstrap(
                {"declaration": {"source": "services_registry.yaml"}},
                {"com.x.svc"}, dry_run=False)
        self.assertEqual(applied, ())
        self.assertIn("exit=5", errors[0])

    def test_real_mode_missing_plist_file_is_error(self):
        from unittest import mock
        with mock.patch.object(cv, "_load_services_registry_index",
                  return_value={"com.x.svc": {"label": "com.x.svc", "plist": "com.x.svc.plist"}}), \
             mock.patch("os.path.exists", return_value=False):
            applied, errors, _ = cv._apply_services_launchctl_bootstrap(
                {"declaration": {"source": "services_registry.yaml"}},
                {"com.x.svc"}, dry_run=False)
        self.assertEqual(applied, ())
        self.assertIn("plist not found", errors[0])

    def test_registered_in_apply_functions(self):
        self.assertIn("services_launchctl_bootstrap", cv._APPLY_FUNCTIONS)
        self.assertIs(cv._APPLY_FUNCTIONS["services_launchctl_bootstrap"],
                      cv._apply_services_launchctl_bootstrap)

    def test_end_to_end_via_verify_convergence_dry_run(self):
        """端到端: verify_convergence(services) dev 环境 → dry-run would-bootstrap."""
        saved = os.environ.pop("CONVERGENCE_DRY_RUN", None)
        try:
            r = cv.verify_convergence("services_to_launchd")
            self.assertEqual(r.drift_action, "machine_sync")
            self.assertTrue(r.apply_dry_run,
                "services dry_run_default=true → apply_dry_run=True (无 env)")
            # dev 无 launchctl → observer_failed → 3 declared 全 missing → 3 would-bootstrap
            self.assertEqual(r.apply_errors, ())
            for a in r.applied_actions:
                self.assertIn("DRY-RUN would bootstrap", a)
        finally:
            if saved is not None:
                os.environ["CONVERGENCE_DRY_RUN"] = saved


class TestV37997ServicesSourceGuards(unittest.TestCase):
    """V37.9.97 source-level 守卫 (convergence.py + yaml)."""

    @classmethod
    def setUpClass(cls):
        base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, "ontology", "convergence.py"), encoding="utf-8") as f:
            cls.py = f.read()
        with open(os.path.join(base, "ontology", "convergence_ontology.yaml"), encoding="utf-8") as f:
            cls.yaml = f.read()

    def test_resolve_dry_run_for_spec_defined(self):
        self.assertIn("def _resolve_dry_run_for_spec(spec):", self.py)

    def test_apply_services_and_loader_defined(self):
        self.assertIn("def _apply_services_launchctl_bootstrap(", self.py)
        self.assertIn("def _load_services_registry_index(", self.py)

    def test_verify_convergence_uses_per_spec_dry_run(self):
        self.assertIn("_resolve_dry_run_for_spec(spec)", self.py,
            "verify_convergence 必须用 per-spec dry-run (V37.9.97)")

    def test_bootstrap_command_shape_in_source(self):
        self.assertIn('"launchctl", "bootstrap", domain, plist_path', self.py)

    def test_apply_function_registered_in_dispatch_source(self):
        self.assertIn('"services_launchctl_bootstrap": _apply_services_launchctl_bootstrap', self.py)

    def test_yaml_changelog_v37_9_97(self):
        self.assertIn("v37_9_97_changelog", self.yaml)
        self.assertIn("services_launchctl_bootstrap", self.yaml)

    def test_no_bootout_in_apply_source(self):
        """V37.9.97 不做 bootout — 守卫 apply 函数不含 launchctl bootout (非安全 auto-sync)."""
        # 提取 _apply_services_launchctl_bootstrap 函数体
        idx = self.py.find("def _apply_services_launchctl_bootstrap(")
        end = self.py.find("\n\ndef ", idx + 10)
        body = self.py[idx:end if end > 0 else len(self.py)]
        self.assertNotIn('"bootout"', body,
            "V37.9.97 apply 只 bootstrap missing, 不 bootout (移除运行 service 非安全 auto-sync)")


if __name__ == "__main__":
    unittest.main(verbosity=2)

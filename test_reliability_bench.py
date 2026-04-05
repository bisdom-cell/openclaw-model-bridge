#!/usr/bin/env python3
"""
test_reliability_bench.py — Agent Reliability Bench 单测

测试 reliability_bench.py 的 7 个故障场景 + 报告生成 + CLI。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reliability_bench import (
    ScenarioResult, BenchReport,
    scenario_provider_unavailable,
    scenario_tool_timeout,
    scenario_malformed_tool_args,
    scenario_oversized_request,
    scenario_kb_miss_hit,
    scenario_cron_drift,
    scenario_state_corruption,
    run_bench,
    format_markdown,
    format_json,
    ALL_SCENARIOS,
)


class TestScenarioResult(unittest.TestCase):
    """ScenarioResult 数据类测试。"""

    def test_add_check(self):
        r = ScenarioResult(1, "test", "desc")
        r.add_check("check1", True, "ok")
        r.add_check("check2", False, "fail")
        self.assertEqual(r.total_checks, 2)
        self.assertEqual(r.passed_checks, 1)

    def test_default_verdict_skip(self):
        r = ScenarioResult(1, "test", "desc")
        self.assertEqual(r.verdict, "SKIP")

    def test_duration_default(self):
        r = ScenarioResult(1, "test", "desc")
        self.assertEqual(r.duration_ms, 0.0)


class TestBenchReport(unittest.TestCase):
    """BenchReport 汇总测试。"""

    def test_summarize(self):
        report = BenchReport()
        s1 = ScenarioResult(1, "s1", "d1", verdict="PASS")
        s1.add_check("c1", True)
        s2 = ScenarioResult(2, "s2", "d2", verdict="FAIL")
        s2.add_check("c2", False)
        s3 = ScenarioResult(3, "s3", "d3", verdict="SKIP")
        report.scenarios = [s1, s2, s3]
        report.summarize()
        self.assertEqual(report.total_pass, 1)
        self.assertEqual(report.total_fail, 1)
        self.assertEqual(report.total_skip, 1)
        self.assertEqual(report.total_checks, 2)
        self.assertEqual(report.passed_checks, 1)

    def test_empty_report(self):
        report = BenchReport()
        report.summarize()
        self.assertEqual(report.total_pass, 0)
        self.assertEqual(report.total_checks, 0)


class TestScenario1ProviderUnavailable(unittest.TestCase):
    """Scenario 1: 断路器 + Provider 注册表。"""

    def test_passes(self):
        r = scenario_provider_unavailable()
        self.assertEqual(r.verdict, "PASS")
        self.assertGreaterEqual(r.total_checks, 7)

    def test_circuit_breaker_lifecycle(self):
        r = scenario_provider_unavailable()
        check_names = [c["name"] for c in r.checks]
        self.assertIn("initial_state_closed", check_names)
        self.assertIn("opens_after_threshold", check_names)
        self.assertIn("recovers_on_success", check_names)

    def test_provider_registry_checks(self):
        r = scenario_provider_unavailable()
        check_names = [c["name"] for c in r.checks]
        self.assertIn("multiple_providers_available", check_names)
        self.assertIn("primary_and_fallback_registered", check_names)


class TestScenario2ToolTimeout(unittest.TestCase):
    """Scenario 2: 超时处理。"""

    def test_passes(self):
        r = scenario_tool_timeout()
        self.assertEqual(r.verdict, "PASS")

    def test_timeout_detected(self):
        r = scenario_tool_timeout()
        timeout_check = next(c for c in r.checks if c["name"] == "request_timed_out")
        self.assertTrue(timeout_check["passed"])

    def test_within_budget(self):
        r = scenario_tool_timeout()
        budget_check = next(c for c in r.checks if c["name"] == "timeout_within_budget")
        self.assertTrue(budget_check["passed"])


class TestScenario3MalformedArgs(unittest.TestCase):
    """Scenario 3: 工具参数修复。"""

    def test_passes(self):
        r = scenario_malformed_tool_args()
        self.assertEqual(r.verdict, "PASS")
        self.assertEqual(r.total_checks, 7)

    def test_all_alias_fixes(self):
        r = scenario_malformed_tool_args()
        alias_checks = [c for c in r.checks if "alias_fix" in c["name"]]
        self.assertGreaterEqual(len(alias_checks), 3)
        for c in alias_checks:
            self.assertTrue(c["passed"], f"{c['name']} failed: {c['detail']}")

    def test_invalid_json_handled(self):
        r = scenario_malformed_tool_args()
        json_check = next(c for c in r.checks if c["name"] == "invalid_json_no_crash")
        self.assertTrue(json_check["passed"])


class TestScenario4OversizedRequest(unittest.TestCase):
    """Scenario 4: 消息截断。"""

    def test_passes(self):
        r = scenario_oversized_request()
        self.assertEqual(r.verdict, "PASS")

    def test_truncation_effective(self):
        r = scenario_oversized_request()
        limit_check = next(c for c in r.checks if c["name"] == "output_within_limit")
        self.assertTrue(limit_check["passed"])

    def test_system_preserved(self):
        r = scenario_oversized_request()
        sys_check = next(c for c in r.checks if c["name"] == "system_msgs_preserved")
        self.assertTrue(sys_check["passed"])

    def test_aggressive_truncation(self):
        r = scenario_oversized_request()
        agg_check = next(c for c in r.checks if c["name"] == "aggressive_truncation_on_high_context")
        self.assertTrue(agg_check["passed"])


class TestScenario5KBMissHit(unittest.TestCase):
    """Scenario 5: KB 搜索空结果。"""

    def test_passes(self):
        r = scenario_kb_miss_hit()
        self.assertEqual(r.verdict, "PASS")
        self.assertGreaterEqual(r.total_checks, 8)

    def test_custom_tools_injected(self):
        r = scenario_kb_miss_hit()
        inject_check = next(c for c in r.checks if "custom_tools_injected" in c["name"])
        self.assertTrue(inject_check["passed"])


class TestScenario6CronDrift(unittest.TestCase):
    """Scenario 6: Cron 漂移检测。"""

    def test_passes(self):
        r = scenario_cron_drift()
        self.assertEqual(r.verdict, "PASS")

    def test_heartbeat_checks(self):
        r = scenario_cron_drift()
        fresh = next(c for c in r.checks if c["name"] == "fresh_heartbeat_detected")
        stale = next(c for c in r.checks if c["name"] == "stale_heartbeat_detected")
        self.assertTrue(fresh["passed"])
        self.assertTrue(stale["passed"])

    def test_registry_validation(self):
        r = scenario_cron_drift()
        reg_check = next(c for c in r.checks if c["name"] == "registry_loads")
        self.assertTrue(reg_check["passed"])


class TestScenario7StateCorruption(unittest.TestCase):
    """Scenario 7: 状态文件损坏检测。"""

    def test_passes(self):
        r = scenario_state_corruption()
        self.assertEqual(r.verdict, "PASS")
        self.assertGreaterEqual(r.total_checks, 7)

    def test_corrupt_json_detected(self):
        r = scenario_state_corruption()
        corrupt = next(c for c in r.checks if c["name"] == "corrupt_json_detected")
        self.assertTrue(corrupt["passed"])

    def test_atomic_write(self):
        r = scenario_state_corruption()
        atomic = next(c for c in r.checks if c["name"] == "atomic_write_works")
        self.assertTrue(atomic["passed"])


class TestRunBench(unittest.TestCase):
    """run_bench() 集成测试。"""

    def test_run_all(self):
        report = run_bench()
        self.assertEqual(len(report.scenarios), 7)
        self.assertEqual(report.total_fail, 0)
        self.assertGreater(report.total_checks, 40)

    def test_run_single_scenario(self):
        report = run_bench(scenario_ids=[3])
        self.assertEqual(len(report.scenarios), 1)
        self.assertEqual(report.scenarios[0].id, 3)

    def test_run_multiple_scenarios(self):
        report = run_bench(scenario_ids=[1, 7])
        self.assertEqual(len(report.scenarios), 2)


class TestFormatMarkdown(unittest.TestCase):
    """Markdown 输出格式测试。"""

    def test_contains_header(self):
        report = BenchReport(generated_at="2026-04-05")
        s = ScenarioResult(1, "Test", "Desc", verdict="PASS")
        s.add_check("c1", True, "ok")
        report.scenarios = [s]
        report.summarize()
        md = format_markdown(report)
        self.assertIn("# Agent Reliability Bench Report", md)
        self.assertIn("2026-04-05", md)

    def test_contains_table(self):
        report = BenchReport()
        s = ScenarioResult(1, "Test", "Desc", verdict="PASS")
        report.scenarios = [s]
        report.summarize()
        md = format_markdown(report)
        self.assertIn("| # | Scenario |", md)

    def test_contains_detail(self):
        report = BenchReport()
        s = ScenarioResult(1, "MyScenario", "MyDesc", verdict="FAIL")
        s.add_check("my_check", False, "detail info")
        report.scenarios = [s]
        report.summarize()
        md = format_markdown(report)
        self.assertIn("MyScenario", md)
        self.assertIn("my_check", md)
        self.assertIn("FAIL", md)


class TestFormatJSON(unittest.TestCase):
    """JSON 输出格式测试。"""

    def test_valid_json(self):
        report = BenchReport(generated_at="2026-04-05")
        s = ScenarioResult(1, "Test", "Desc", verdict="PASS")
        s.add_check("c1", True)
        report.scenarios = [s]
        report.summarize()
        j = format_json(report)
        data = json.loads(j)
        self.assertIn("summary", data)
        self.assertIn("scenarios", data)

    def test_summary_fields(self):
        report = BenchReport()
        s = ScenarioResult(1, "T", "D", verdict="PASS")
        s.add_check("c", True)
        report.scenarios = [s]
        report.summarize()
        data = json.loads(format_json(report))
        summary = data["summary"]
        self.assertEqual(summary["total_pass"], 1)
        self.assertEqual(summary["total_fail"], 0)
        self.assertEqual(summary["total_checks"], 1)
        self.assertEqual(summary["passed_checks"], 1)


class TestAllScenariosRegistered(unittest.TestCase):
    """确保所有场景已注册。"""

    def test_seven_scenarios(self):
        self.assertEqual(len(ALL_SCENARIOS), 7)

    def test_scenario_ids_sequential(self):
        report = run_bench()
        ids = [s.id for s in report.scenarios]
        self.assertEqual(ids, [1, 2, 3, 4, 5, 6, 7])


if __name__ == "__main__":
    unittest.main()

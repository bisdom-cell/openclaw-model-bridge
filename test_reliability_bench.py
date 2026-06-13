#!/usr/bin/env python3
"""
test_reliability_bench.py — Agent Reliability Bench 单测

测试 reliability_bench.py 的 17 个故障场景 + 报告生成 + CLI。
V37.9.146 (外部评审2 P2(b)): +10 场景 (8-17), 朝行业可引用测试集方向。
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
    # V37.9.146 +10 场景
    scenario_provider_schema_drift,
    scenario_streaming_interruption,
    scenario_tool_result_oversized,
    scenario_json_malformed_repair,
    scenario_all_fallbacks_fail,
    scenario_memory_index_stale,
    scenario_cron_duplicate_fire,
    scenario_config_partial_corruption,
    scenario_dns_failure,
    scenario_long_context_truncation_quality,
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


# ---------------------------------------------------------------------------
# V37.9.146 +10 场景 (8-17)
# ---------------------------------------------------------------------------
def _check(r, name):
    """取指定 check (找不到则 fail-loud)。"""
    return next(c for c in r.checks if c["name"] == name)


class TestScenario8ProviderSchemaDrift(unittest.TestCase):
    """Scenario 8: Provider 定义/响应 schema 漂移。"""

    def test_passes(self):
        r = scenario_provider_schema_drift()
        self.assertEqual(r.verdict, "PASS")
        self.assertGreaterEqual(r.total_checks, 6)

    def test_contract_catches_definition_drift(self):
        r = scenario_provider_schema_drift()
        self.assertTrue(_check(r, "contract_catches_missing_api_key_env")["passed"])
        self.assertTrue(_check(r, "contract_catches_bad_auth_style")["passed"])

    def test_malformed_response_no_crash(self):
        r = scenario_provider_schema_drift()
        self.assertTrue(_check(r, "malformed_response_shape_no_crash")["passed"])


class TestScenario9StreamingInterruption(unittest.TestCase):
    """Scenario 9: SSE 流式中断。"""

    def test_passes(self):
        r = scenario_streaming_interruption()
        self.assertEqual(r.verdict, "PASS")

    def test_detects_incomplete_stream(self):
        r = scenario_streaming_interruption()
        self.assertTrue(_check(r, "consumer_detects_incomplete_stream")["passed"])

    def test_skips_malformed_frame(self):
        r = scenario_streaming_interruption()
        self.assertTrue(_check(r, "consumer_skips_malformed_frame")["passed"])


class TestScenario10ToolResultOversized(unittest.TestCase):
    """Scenario 10: 巨型 tool 结果截断。"""

    def test_passes(self):
        r = scenario_tool_result_oversized()
        self.assertEqual(r.verdict, "PASS")

    def test_bounded_and_recent_kept(self):
        r = scenario_tool_result_oversized()
        self.assertTrue(_check(r, "bounded_within_budget")["passed"])
        self.assertTrue(_check(r, "recent_query_survives")["passed"])
        self.assertTrue(_check(r, "giant_dump_removed")["passed"])


class TestScenario11JsonMalformedRepair(unittest.TestCase):
    """Scenario 11: 畸形 JSON / 幻觉 tool_call 修复。"""

    def test_passes(self):
        r = scenario_json_malformed_repair()
        self.assertEqual(r.verdict, "PASS")

    def test_hallucinated_xml_cleaned(self):
        r = scenario_json_malformed_repair()
        self.assertTrue(_check(r, "hallucinated_xml_cleaned")["passed"])

    def test_fail_open_on_read_failure(self):
        r = scenario_json_malformed_repair()
        self.assertTrue(_check(r, "read_failure_fail_open")["passed"])


class TestScenario12AllFallbacksFail(unittest.TestCase):
    """Scenario 12: 全 fallback 失败。"""

    def test_passes(self):
        r = scenario_all_fallbacks_fail()
        self.assertEqual(r.verdict, "PASS")

    def test_error_chain_not_diluted(self):
        r = scenario_all_fallbacks_fail()
        self.assertTrue(_check(r, "error_chain_preserved")["passed"])
        self.assertTrue(_check(r, "not_diluted_to_bare_502")["passed"])


class TestScenario13MemoryIndexStale(unittest.TestCase):
    """Scenario 13: 记忆索引陈旧。"""

    def test_passes(self):
        r = scenario_memory_index_stale()
        self.assertEqual(r.verdict, "PASS")

    def test_stale_and_coverage_gap(self):
        r = scenario_memory_index_stale()
        self.assertTrue(_check(r, "stale_index_detected")["passed"])
        self.assertTrue(_check(r, "coverage_gap_detected")["passed"])


class TestScenario14CronDuplicateFire(unittest.TestCase):
    """Scenario 14: cron 重复触发。"""

    def test_passes(self):
        r = scenario_cron_duplicate_fire()
        self.assertEqual(r.verdict, "PASS")

    def test_duplicate_and_lock(self):
        r = scenario_cron_duplicate_fire()
        self.assertTrue(_check(r, "duplicate_entry_detected")["passed"])
        self.assertTrue(_check(r, "concurrent_run_blocked")["passed"])
        self.assertTrue(_check(r, "substring_no_false_match")["passed"])


class TestScenario15ConfigPartialCorruption(unittest.TestCase):
    """Scenario 15: config 部分损坏。"""

    def test_passes(self):
        r = scenario_config_partial_corruption()
        self.assertEqual(r.verdict, "PASS")

    def test_resilient_parse_and_defaults(self):
        r = scenario_config_partial_corruption()
        self.assertTrue(_check(r, "malformed_lines_no_crash")["passed"])
        self.assertTrue(_check(r, "real_get_with_default_safe")["passed"])


class TestScenario16DnsFailure(unittest.TestCase):
    """Scenario 16: DNS 解析失败。"""

    def test_passes(self):
        r = scenario_dns_failure()
        self.assertEqual(r.verdict, "PASS")

    def test_fails_fast(self):
        r = scenario_dns_failure()
        self.assertTrue(_check(r, "dns_failure_detected")["passed"])
        self.assertTrue(_check(r, "fails_fast_not_hang")["passed"])


class TestScenario17LongContextTruncationQuality(unittest.TestCase):
    """Scenario 17: 长上下文截断质量。"""

    def test_passes(self):
        r = scenario_long_context_truncation_quality()
        self.assertEqual(r.verdict, "PASS")

    def test_quality_invariants(self):
        r = scenario_long_context_truncation_quality()
        self.assertTrue(_check(r, "message_boundaries_intact")["passed"])
        self.assertTrue(_check(r, "no_content_corruption")["passed"])
        self.assertTrue(_check(r, "system_always_kept")["passed"])
        self.assertTrue(_check(r, "monotonic_with_budget")["passed"])


class TestRunBench(unittest.TestCase):
    """run_bench() 集成测试。"""

    def test_run_all(self):
        report = run_bench()
        self.assertEqual(len(report.scenarios), 17)
        self.assertEqual(report.total_fail, 0)
        self.assertEqual(report.total_skip, 0, "所有场景应可在 dev 跑 (无 SKIP)")
        self.assertGreater(report.total_checks, 90)

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

    def test_seventeen_scenarios(self):
        self.assertEqual(len(ALL_SCENARIOS), 17)

    def test_scenario_ids_sequential(self):
        report = run_bench()
        ids = [s.id for s in report.scenarios]
        self.assertEqual(ids, list(range(1, 18)))


if __name__ == "__main__":
    unittest.main()

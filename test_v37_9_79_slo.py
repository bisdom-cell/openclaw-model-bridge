#!/usr/bin/env python3
"""V37.9.79 SLO 三项修复单测 (主交付):

#1 真 bug: slo_dashboard.py tool_success_pct 0/0 → 显示 0% FAIL (拉 overall=VIOLATIONS)
   - 触发: 2026-05-18 Mac Mini 实测 37 请求 0 工具调用 → tool_calls_total=0 → 0/0=0%
     V36 verdict 用 `current["requests"] < 5` 豁免低样本量, 但 Qwen3 高请求量+零工具
     调用场景下 verdict FAIL 是误判.
   - 修复: extract_snapshot 加 tool_calls_total 字段, verdict 三档 PASS/FAIL/N/A,
     tool_calls_total==0 时 verdict=N/A 不算 FAIL.
   - overall: 含 N/A 不算 FAIL (跳过), 全 N/A 时 overall=N/A.

#2 真数据 + 阈值调整: latency_p95_ms 30000 → 50000ms
   - 触发: 2026-05-18 Mac Mini 实测 proxy_stats.json slo.latency.p95=37570ms,
     p50=26s/p95=37s/p99=53s — 整体而非 outlier. proxy.log Backend 29.7s 单次.
   - 真因: 远端 Qwen3 真实性能 baseline ~30-40s p95, V36 假设 (30000ms) 与生产不符.
   - 调整非掩盖: 注释明记 "long-term 30000 待 multi-provider", 短期 50000ms 减噪声.

#3 设计 debt: slo_history.jsonl 从未注册 cron
   - 触发: 2026-05-18 Mac Mini 实测 `History: 0 snapshots`, ~/.kb/slo_history.jsonl 不存在.
   - V36 设计 slo_dashboard.py --snapshot 写历史快照, 但未注册 cron.
   - 修复: jobs_registry.yaml 新 slo_snapshot job 每小时 :05 跑 slo_snapshot.sh wrapper.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# 让 slo_dashboard 可 import
sys.path.insert(0, REPO_ROOT)


class TestV37979ToolSuccessNAFix(unittest.TestCase):
    """修 #1: tool_success 0/0 → N/A 不算 FAIL."""

    def test_extract_snapshot_includes_tool_calls_total(self):
        """V37.9.79: extract_snapshot 必须把 tool_calls_total 字段加入 snapshot
        (V36 原本只有 tool_success_pct, 无法区分 0/0 vs 真失败)."""
        import slo_dashboard as sd
        fake_stats = {
            "total_requests": 37,
            "total_errors": 0,
            "slo": {
                "latency": {"p50": 100, "p95": 200, "p99": 300, "count": 37},
                "tool_calls_total": 0,
                "tool_success_rate_pct": 0.0,
            }
        }
        snap = sd.extract_snapshot(fake_stats)
        self.assertIn("tool_calls_total", snap,
                      "snapshot 必须含 tool_calls_total 字段 (V37.9.79 新增)")
        self.assertEqual(snap["tool_calls_total"], 0)

    def test_verdict_tools_na_when_tool_calls_zero(self):
        """V37.9.79 核心契约: tool_calls_total==0 时 verdict tools=N/A 不算 FAIL."""
        import slo_dashboard as sd
        fake_stats = {
            "total_requests": 37,
            "total_errors": 0,
            "slo": {
                "latency": {"p50": 100, "p95": 200, "p99": 300, "count": 37},
                "tool_calls_total": 0,
                "tool_success_rate_pct": 0.0,
                "degradation_rate_pct": 0.0,
                "timeout_rate_pct": 0.0,
                "auto_recovery_rate_pct": 100.0,
                "fallback_count": 0,
            }
        }
        # build_dashboard 调 extract_snapshot 内部
        dashboard = sd.build_dashboard(stats=fake_stats)
        verdicts = dashboard["verdicts"]
        self.assertEqual(verdicts.get("tools"), "N/A",
                         f"tool_calls_total=0 时 tools verdict 必须是 N/A, 实际 {verdicts}")

    def test_overall_skips_na_in_pass_calc(self):
        """V37.9.79: overall 计算时 N/A 不算 FAIL, 其他段全 PASS 时 overall=ALL PASS."""
        import slo_dashboard as sd
        fake_stats = {
            "total_requests": 100,
            "total_errors": 0,
            "slo": {
                # latency p95=200ms 远低于 30000ms 阈值
                "latency": {"p50": 100, "p95": 200, "p99": 300, "count": 100},
                "tool_calls_total": 0,  # 零工具调用
                "tool_success_rate_pct": 0.0,
                "degradation_rate_pct": 0.0,
                "timeout_rate_pct": 0.0,
                "auto_recovery_rate_pct": 100.0,
                "fallback_count": 0,
            }
        }
        dashboard = sd.build_dashboard(stats=fake_stats)
        # tools=N/A, latency/success/degradation=PASS → overall=ALL PASS
        self.assertEqual(dashboard["overall"], "ALL PASS",
                         f"tools=N/A 且其他全 PASS 时 overall 应是 ALL PASS, 实际 verdicts={dashboard['verdicts']}")

    def test_verdict_tools_fail_when_real_failure(self):
        """V37.9.79: tool_calls_total>0 + tool_success_pct<95% 时仍正确 FAIL."""
        import slo_dashboard as sd
        fake_stats = {
            "total_requests": 50,
            "total_errors": 0,
            "slo": {
                "latency": {"p50": 100, "p95": 200, "p99": 300, "count": 50},
                "tool_calls_total": 10,  # 真有工具调用
                "tool_success_rate_pct": 50.0,  # 但只 50% 成功 < 95%
                "degradation_rate_pct": 0.0,
                "timeout_rate_pct": 0.0,
                "auto_recovery_rate_pct": 100.0,
                "fallback_count": 0,
            }
        }
        dashboard = sd.build_dashboard(stats=fake_stats)
        self.assertEqual(dashboard["verdicts"]["tools"], "FAIL",
                         "真工具失败时 verdict 仍应 FAIL (不被 V37.9.79 修复误判为 N/A)")
        self.assertEqual(dashboard["overall"], "VIOLATIONS",
                         "真工具失败时 overall 仍 VIOLATIONS")

    def test_verdict_tools_pass_when_real_success(self):
        """V37.9.79: tool_calls_total>0 + tool_success_pct>=95% 时正确 PASS."""
        import slo_dashboard as sd
        fake_stats = {
            "total_requests": 50,
            "total_errors": 0,
            "slo": {
                "latency": {"p50": 100, "p95": 200, "p99": 300, "count": 50},
                "tool_calls_total": 20,
                "tool_success_rate_pct": 97.0,  # 高于 95% 阈值
                "degradation_rate_pct": 0.0,
                "timeout_rate_pct": 0.0,
                "auto_recovery_rate_pct": 100.0,
                "fallback_count": 0,
            }
        }
        dashboard = sd.build_dashboard(stats=fake_stats)
        self.assertEqual(dashboard["verdicts"]["tools"], "PASS")

    def test_source_guard_verdict_uses_tool_calls_not_requests(self):
        """V37.9.79 source-level: slo_dashboard.py 必须用 tool_calls_total==0 豁免
        而非旧 V36 的 current['requests'] < 5 (低样本量豁免)."""
        with open(os.path.join(REPO_ROOT, "slo_dashboard.py"), "r", encoding="utf-8") as f:
            src = f.read()
        # 必须含 V37.9.79 marker
        self.assertIn("V37.9.79", src, "slo_dashboard.py 必须含 V37.9.79 marker")
        # tools_verdict 计算必须含 tool_calls == 0 判定
        self.assertIn("tool_calls == 0", src,
                      "verdict 必须用 tool_calls == 0 而非 requests < 5")
        # N/A 字面量必须存在
        self.assertIn('"N/A"', src, "必须有 N/A verdict")
        # 反退化守卫: extract_snapshot 必须含 tool_calls_total
        self.assertIn('"tool_calls_total":', src,
                      "extract_snapshot 必须含 tool_calls_total 字段")


class TestV37979LatencyThresholdAdjusted(unittest.TestCase):
    """修 #2: config.yaml latency_p95_ms 30000 → 50000ms (Mac Mini Qwen3 真实 baseline)."""

    def test_config_yaml_threshold_is_50000(self):
        """config.yaml latency_p95_ms 必须是 50000 (V37.9.79 调整)."""
        with open(os.path.join(REPO_ROOT, "config.yaml"), "r", encoding="utf-8") as f:
            src = f.read()
        # 必须含 V37.9.79 marker 注释
        self.assertIn("V37.9.79", src, "config.yaml 必须含 V37.9.79 marker")
        # 阈值必须 50000
        self.assertRegex(src, r"latency_p95_ms:\s*50000",
                         "latency_p95_ms 必须是 50000")
        # 反退化守卫: 不能仍是 30000 作为 active 值 (注释中提到 30s/30000 OK)
        # 找 active 配置行
        active_line = re.search(r"^\s*latency_p95_ms:\s*(\d+)", src, re.MULTILINE)
        self.assertIsNotNone(active_line, "必须找到 active latency_p95_ms 配置")
        self.assertEqual(int(active_line.group(1)), 50000,
                         "active latency_p95_ms 必须是 50000")

    def test_config_yaml_threshold_change_explained(self):
        """V37.9.79 调整必须有充分注释解释为何 (不是掩盖问题)."""
        with open(os.path.join(REPO_ROOT, "config.yaml"), "r", encoding="utf-8") as f:
            src = f.read()
        # 必须提及 Mac Mini 真实数据
        self.assertIn("Mac Mini", src, "必须解释 Mac Mini 实测 baseline")
        self.assertIn("37570", src, "必须引用 Mac Mini 真 p95=37570 数据")
        # 必须留长期目标 (恢复 30000)
        self.assertIn("multi-provider", src, "必须提长期目标 multi-provider")
        # 必须明确"不是掩盖问题"
        self.assertTrue(
            "承认" in src or "不是掩盖" in src or "baseline" in src,
            "注释必须明确调整是承认真实 baseline 不是掩盖问题"
        )


class TestV37979SloSnapshotJob(unittest.TestCase):
    """修 #3: 新 cron job slo_snapshot 写 SLO history (V36 设计 debt 闭环)."""

    def test_jobs_registry_has_slo_snapshot(self):
        """jobs_registry.yaml 必须含 slo_snapshot job."""
        with open(os.path.join(REPO_ROOT, "jobs_registry.yaml"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("- id: slo_snapshot", src,
                      "jobs_registry.yaml 必须新增 slo_snapshot job")
        # 调度时段 :05 (错开 kb_status_refresh 整点)
        self.assertRegex(src, r'interval:\s*"5 \* \* \* \*"',
                         "slo_snapshot 必须每小时 :05 触发")
        # entry 是 .sh wrapper (V37.9.79 设计)
        self.assertIn("entry: slo_snapshot.sh", src,
                      "entry 必须是 .sh wrapper (避开 _format_cron_line 假设 bash exec)")
        # scheduler: system
        slo_block = src[src.find("- id: slo_snapshot"):src.find("- id: slo_snapshot") + 800]
        self.assertIn("scheduler: system", slo_block)
        self.assertIn("enabled: true", slo_block)

    def test_slo_snapshot_sh_exists_and_executable(self):
        """slo_snapshot.sh wrapper 必须存在 + 可执行 + 调 slo_dashboard.py --snapshot."""
        path = os.path.join(REPO_ROOT, "slo_snapshot.sh")
        self.assertTrue(os.path.exists(path), "slo_snapshot.sh 必须存在")
        self.assertTrue(os.access(path, os.X_OK), "slo_snapshot.sh 必须可执行")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        # V37.9.79 marker
        self.assertIn("V37.9.79", src)
        # 必须调 slo_dashboard.py --snapshot
        self.assertIn("slo_dashboard.py", src)
        self.assertIn("--snapshot", src)
        # FAIL-OPEN: slo_dashboard.py 不存在时 silently skip
        self.assertIn("不存在", src, "必须有 FAIL-OPEN 文案 (slo_dashboard.py 缺失)")
        # cron PATH 规范
        self.assertIn('export PATH=', src)

    def test_slo_snapshot_sh_bash_syntax_ok(self):
        """slo_snapshot.sh bash -n 语法检查通过."""
        proc = subprocess.run(
            ["bash", "-n", os.path.join(REPO_ROOT, "slo_snapshot.sh")],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(proc.returncode, 0,
                         f"bash -n 失败: {proc.stderr}")

    def test_auto_deploy_file_map_has_slo_snapshot(self):
        """auto_deploy.sh FILE_MAP 必须含 slo_snapshot.sh 和 slo_dashboard.py."""
        with open(os.path.join(REPO_ROOT, "auto_deploy.sh"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"slo_snapshot.sh|$HOME/slo_snapshot.sh"', src,
                      "FILE_MAP 必须含 slo_snapshot.sh")
        self.assertIn('"slo_dashboard.py|$HOME/slo_dashboard.py"', src,
                      "FILE_MAP 必须含 slo_dashboard.py (V37.9.79 闭环 V36 debt)")

    def test_slo_snapshot_runtime_fail_open_when_no_proxy_stats(self):
        """运行时: slo_snapshot.sh 在 proxy_stats.json 不存在时 silently skip exit 0 (FAIL-OPEN)."""
        # 用 tempdir 隔离 HOME, 故意没 proxy_stats.json
        with tempfile.TemporaryDirectory() as tmpdir:
            env = os.environ.copy()
            env["HOME"] = tmpdir
            env["OPENCLAW_REPO_DIR"] = REPO_ROOT  # 仍指向真仓库 slo_dashboard.py
            proc = subprocess.run(
                ["bash", os.path.join(REPO_ROOT, "slo_snapshot.sh")],
                capture_output=True, text=True, timeout=30, env=env
            )
            # FAIL-OPEN: exit 0 即使没 proxy_stats.json
            self.assertEqual(proc.returncode, 0,
                             f"slo_snapshot.sh 应 FAIL-OPEN exit 0, 实际 rc={proc.returncode}, "
                             f"stderr={proc.stderr}")


class TestV37979ReverseValidation(unittest.TestCase):
    """反向 sabotage 验证三项守卫真有效 (V37.9.78 同款方法论)."""

    def test_sabotage_remove_tool_calls_total_caught(self):
        """sabotage 移除 extract_snapshot 中 tool_calls_total 字段 → 守卫立即抓."""
        path = os.path.join(REPO_ROOT, "slo_dashboard.py")
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
        sabotaged = original.replace(
            '"tool_calls_total": slo.get("tool_calls_total", 0),',
            '# tool_calls_total removed by sabotage'
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(sabotaged)
        try:
            tc = TestV37979ToolSuccessNAFix()
            with self.assertRaises(AssertionError):
                tc.test_source_guard_verdict_uses_tool_calls_not_requests()
        finally:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)

    def test_sabotage_threshold_back_to_30000_caught(self):
        """sabotage 把 latency_p95_ms 改回 30000 → 守卫立即抓."""
        path = os.path.join(REPO_ROOT, "config.yaml")
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
        sabotaged = re.sub(
            r"latency_p95_ms:\s*50000",
            "latency_p95_ms: 30000",
            original
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(sabotaged)
        try:
            tc = TestV37979LatencyThresholdAdjusted()
            with self.assertRaises(AssertionError):
                tc.test_config_yaml_threshold_is_50000()
        finally:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)

    def test_sabotage_remove_slo_snapshot_job_caught(self):
        """sabotage 删 jobs_registry.yaml 中 slo_snapshot job → 守卫立即抓."""
        path = os.path.join(REPO_ROOT, "jobs_registry.yaml")
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
        # 找 slo_snapshot block 移除
        sabotaged = re.sub(
            r"  # V37.9.79 新增.*?- id: slo_snapshot.*?description: V37\.9\.79.*?数据驱动决策\n",
            "",
            original,
            count=1,
            flags=re.DOTALL
        )
        # 确保 sabotage 真改变了内容
        self.assertNotEqual(original, sabotaged, "sabotage 必须真改变内容")
        with open(path, "w", encoding="utf-8") as f:
            f.write(sabotaged)
        try:
            tc = TestV37979SloSnapshotJob()
            with self.assertRaises(AssertionError):
                tc.test_jobs_registry_has_slo_snapshot()
        finally:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)


if __name__ == "__main__":
    unittest.main()

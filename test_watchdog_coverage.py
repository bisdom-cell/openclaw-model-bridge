"""V37.9.59 — watchdog 监控覆盖率 + 自监控守卫单测.

血案 (用户 5/12 17:30 询问驱动):
  V37.9.58-hotfix4 后 watchdog 真激活, 但用户问"是否监控所有任务+准确上报".
  对照 jobs_registry.yaml enabled system jobs (34) vs watchdog JOBS 数组 (16),
  仅 47% 覆盖. 漏 18 个 jobs 含 kb_deep_dive (V37.9.16 新增) / kb_dream 三阶段
  / governance_audit / auto_deploy / wa_keepalive / etc. 任何 silent failure 不上报.

V37.9.59 治本三层:
  Step 1: watchdog JOBS 数组 +4 (kb_deep_dive/kb_dream/chaspark/governance_audit)
  Step 2: 新加 LOG_FRESHNESS_JOBS 数组 +11 jobs (无 last_run.json 的 jobs 用 log mtime)
  Step 3: watchdog 自监控 — 检查 watchdog_canary.json mtime > 12h 触发告警

测试契约:
  Tier 1 (源码字面量): 新加 jobs 字面量必须出现
  Tier 2 (函数定义): check_log_freshness() 必须存在 + 行为正确
  Tier 3 (覆盖率): JOBS + LOG_FRESHNESS_JOBS 总数 ≥ 25 (覆盖 ~74%+)
  Tier 4 (自监控): watchdog_canary.json 检查必须存在
  Tier 5 (反向验证): sabotage 移除 V37.9.59 marker → 守卫立即 fail
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
WATCHDOG_SH = os.path.join(REPO_ROOT, "job_watchdog.sh")


def _read():
    with open(WATCHDOG_SH, "r", encoding="utf-8") as f:
        return f.read()


class TestV37959JobsArrayExpansion(unittest.TestCase):
    """V37.9.59 Step 1: watchdog JOBS 数组 +4 jobs (kb_deep_dive/kb_dream/chaspark/governance_audit)."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read()

    def test_kb_deep_dive_in_jobs(self):
        """V37.9.16 新增 kb_deep_dive (每日 22:30) 必须在 JOBS 数组."""
        self.assertIn(
            "kb_deep_dive|$HOME/.kb/last_run_deep_dive.json", self.src,
            "V37.9.59: kb_deep_dive 必须加入 JOBS 数组 (V37.9.16 漏监控至 V37.9.58)"
        )

    def test_kb_dream_in_jobs(self):
        """kb_dream (Agent Dream Reduce 03:00) 必须在 JOBS 数组.

        V37.9.60-hotfix2: 路径修正为 kb_dream.sh:103 实际写入的
        $DREAM_DIR/.last_run.json (= ~/.kb/dreams/.last_run.json).
        V37.9.59 假设 ~/.kb/last_run_dream.json 路径错配, 导致 watchdog 永远
        "状态文件不存在" 误报触发 core alert.
        """
        self.assertIn(
            "kb_dream|$HOME/.kb/dreams/.last_run.json", self.src,
            "V37.9.60-hotfix2: kb_dream 路径必须匹配 kb_dream.sh:103 STATUS_FILE"
        )
        # 反向守卫: 旧 V37.9.59 错配路径不得回归
        self.assertNotIn(
            "kb_dream|$HOME/.kb/last_run_dream.json", self.src,
            "V37.9.60-hotfix2: 禁止回退到 V37.9.59 错配路径 ~/.kb/last_run_dream.json"
        )

    def test_chaspark_in_jobs(self):
        """chaspark (每日 11:00) 必须在 JOBS 数组."""
        self.assertIn(
            "chaspark|$HOME/.openclaw/jobs/chaspark/cache/last_run.json", self.src,
            "V37.9.59: chaspark 必须加入 JOBS 数组"
        )

    def test_governance_audit_in_jobs(self):
        """governance_audit_cron (每日 07:00) 必须在 JOBS 数组."""
        self.assertIn(
            "governance_audit_cron|$HOME/.kb/last_run_governance_audit.json", self.src,
            "V37.9.59: governance_audit_cron 必须加入 JOBS 数组"
        )


class TestV37959LogFreshnessJobs(unittest.TestCase):
    """V37.9.59 Step 2: LOG_FRESHNESS_JOBS 数组 + check_log_freshness() 函数."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read()

    def test_log_freshness_array_defined(self):
        """LOG_FRESHNESS_JOBS=( 数组必须定义."""
        self.assertIn("LOG_FRESHNESS_JOBS=(", self.src,
            "V37.9.59 Step 2: LOG_FRESHNESS_JOBS 数组必须存在")

    def test_check_log_freshness_function_defined(self):
        """check_log_freshness() 函数必须定义."""
        self.assertRegex(
            self.src, r"check_log_freshness\(\)\s*\{",
            "V37.9.59 Step 2: check_log_freshness() 函数必须定义"
        )

    def test_log_freshness_critical_jobs(self):
        """V37.9.59 LOG_FRESHNESS_JOBS 必须含核心 jobs (auto_deploy / wa_keepalive)."""
        # 关键 P0 jobs: auto_deploy (每 2min) / wa_keepalive (每 30min)
        self.assertIn('auto_deploy|$HOME/.openclaw/logs/auto_deploy.log', self.src,
            "auto_deploy (V37.9.59 P0, 每 2min) 必须在 LOG_FRESHNESS")
        self.assertIn('wa_keepalive|$HOME/wa_keepalive.log', self.src,
            "wa_keepalive (V37.9.59 P0, 每 30min, V37.8.13 血案修后) 必须在 LOG_FRESHNESS")

    def test_log_freshness_kb_jobs(self):
        """V37.9.59 LOG_FRESHNESS_JOBS 必须含 KB jobs."""
        for job_key in ["kb_embed", "kb_trend", "kb_status_refresh"]:
            self.assertIn(job_key, self.src,
                f"V37.9.59: {job_key} (KB job 无 last_run.json) 必须在 LOG_FRESHNESS")

    def test_check_log_freshness_iterates_array(self):
        """for entry in LOG_FRESHNESS_JOBS 循环必须存在."""
        self.assertRegex(
            self.src, r'for entry in "\$\{LOG_FRESHNESS_JOBS\[@\]\}"',
            "V37.9.59: 必须有 for entry in LOG_FRESHNESS_JOBS 循环调用 check_log_freshness"
        )


class TestV37959WatchdogSelfMonitor(unittest.TestCase):
    """V37.9.59 Step 3: watchdog 自监控 — canary mtime > 12h 检查."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read()

    def test_watchdog_canary_self_check_present(self):
        """watchdog 自监控段必须检查 watchdog_canary.json."""
        # 找 V37.9.59 自监控段
        self.assertIn("WATCHDOG_CANARY=", self.src,
            "V37.9.59: 必须有 WATCHDOG_CANARY 变量定义自监控段")
        self.assertIn("watchdog 自身: canary", self.src,
            "V37.9.59: watchdog 自监控告警必须含 'watchdog 自身: canary' 关键字")

    def test_self_monitor_threshold_12h(self):
        """自监控阈值必须是 12h (43200s, watchdog cron 每 4h * 3 周期 slack)."""
        self.assertIn("43200", self.src,
            "V37.9.59: watchdog 自监控阈值必须是 43200s (12h)")

    def test_self_monitor_references_mr_19(self):
        """自监控告警必须引用 MR-19 元规则 (元监控盲区上游)."""
        # 找 watchdog 自身告警附近含 MR-19
        canary_section_match = re.search(
            r"WATCHDOG_CANARY=[\s\S]+?(MR-19|第二次演出)",
            self.src
        )
        self.assertIsNotNone(canary_section_match,
            "V37.9.59: watchdog 自监控告警必须引用 MR-19 第二次演出可追溯")


class TestV37959StaleLockExpansion(unittest.TestCase):
    """V37.9.59 Step 4: STALE_LOCK_DIRS 补 kb_dream / chaspark."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read()

    def test_kb_dream_lockdir_added(self):
        """kb_dream lockdir 必须加 (dev grep 确认 kb_dream.sh 用 LOCK=/tmp/kb_dream.lockdir)."""
        self.assertIn("/tmp/kb_dream.lockdir", self.src,
            "V37.9.59: kb_dream.lockdir 必须加入 STALE_LOCK_DIRS (5/12 诊断未覆盖)")

    def test_chaspark_lockdir_added(self):
        """chaspark lockdir 加 (即使 chaspark 可能不用 lockdir, 加入也无害)."""
        self.assertIn("/tmp/chaspark.lockdir", self.src,
            "V37.9.59: chaspark.lockdir 加入 STALE_LOCK_DIRS")


class TestV37959CoverageGoal(unittest.TestCase):
    """V37.9.59 Step 5: 整体覆盖率验证 — JOBS + LOG_FRESHNESS 总数 ≥ 25."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read()

    def test_jobs_array_size_at_least_20(self):
        """V37.9.59 后 JOBS 数组应有 ≥20 jobs (16 + V37.9.59 加 4)."""
        # 找 JOBS=( ... ) 段
        m = re.search(r'^JOBS=\(([\s\S]+?)^\)', self.src, re.MULTILINE)
        self.assertIsNotNone(m, "JOBS 数组必须可解析")
        body = m.group(1)
        # 抓 job_id 模式
        jobs = re.findall(r'"(\w+)\|', body)
        self.assertGreaterEqual(len(jobs), 20,
            f"V37.9.59: JOBS 数组应有 ≥20 jobs (含 V37.9.59 +4), 实际 {len(jobs)}")

    def test_log_freshness_array_size_at_least_9(self):
        """V37.9.59 LOG_FRESHNESS_JOBS 数组应有 ≥9 jobs."""
        m = re.search(r'^LOG_FRESHNESS_JOBS=\(([\s\S]+?)^\)', self.src, re.MULTILINE)
        self.assertIsNotNone(m, "LOG_FRESHNESS_JOBS 数组必须可解析")
        body = m.group(1)
        jobs = re.findall(r'"(\w+)\|', body)
        self.assertGreaterEqual(len(jobs), 9,
            f"V37.9.59 LOG_FRESHNESS_JOBS 应有 ≥9 jobs, 实际 {len(jobs)}")

    def test_total_coverage_at_least_25(self):
        """V37.9.59 后 JOBS + LOG_FRESHNESS 总监控 jobs ≥25 (覆盖 ~74%+)."""
        m_jobs = re.search(r'^JOBS=\(([\s\S]+?)^\)', self.src, re.MULTILINE)
        m_log = re.search(r'^LOG_FRESHNESS_JOBS=\(([\s\S]+?)^\)', self.src, re.MULTILINE)
        total = 0
        if m_jobs:
            total += len(re.findall(r'"(\w+)\|', m_jobs.group(1)))
        if m_log:
            total += len(re.findall(r'"(\w+)\|', m_log.group(1)))
        # 34 enabled jobs * 74% ≈ 25
        self.assertGreaterEqual(total, 25,
            f"V37.9.59: JOBS + LOG_FRESHNESS 总覆盖 ≥25 jobs, 实际 {total} "
            f"(jobs_registry.yaml enabled=true + scheduler=system 共 34)"
        )


class TestV37959RuntimeIntegration(unittest.TestCase):
    """V37.9.59 端到端运行时验证 — watchdog 真跑通+扩展覆盖."""

    def test_watchdog_runs_with_v37959_changes(self):
        """V37.9.59 后 watchdog 仍能 bash -n + 跑通 (修改不破坏 set -eE / ERR trap)."""
        result = subprocess.run(
            ["bash", "-n", WATCHDOG_SH],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0,
            f"V37.9.59 watchdog bash -n 必须通过: {result.stderr}")

    def test_check_log_freshness_triggers_alert_when_stale(self):
        """模拟旧 log 文件触发 check_log_freshness 告警 (函数行为正确)."""
        # 创建一个 mtime 6 年前的临时 log
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name
            f.write(b"test\n")
        try:
            # mtime 改为 2020-04-01
            os.utime(log_path, (1585699200, 1585699200))
            # 注入临时 watchdog 临时副本 (添加测试条目)
            with open(WATCHDOG_SH, 'r') as f:
                src = f.read()
            test_entry = f'    "test_v959|{log_path}|60|V959测试|core"\n'
            new_src = re.sub(
                r'(LOG_FRESHNESS_JOBS=\(\n)',
                r'\1' + test_entry,
                src, count=1
            )
            test_wd = os.path.join(tempfile.gettempdir(), "job_watchdog_v959_test.sh")
            with open(test_wd, 'w') as f: f.write(new_src)
            # 清 lockdir + 跑
            for ld in ['/tmp/job_watchdog.lockdir']:
                if os.path.isdir(ld): os.rmdir(ld)
            result = subprocess.run(
                ['bash', test_wd],
                capture_output=True, text=True, timeout=30
            )
            # check_log_freshness 应触发告警 (V959测试)
            self.assertIn("V959测试", result.stdout + result.stderr,
                "V37.9.59 check_log_freshness 应对 stale log 触发告警")
        finally:
            os.unlink(log_path)
            if os.path.exists(test_wd):
                os.unlink(test_wd)


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.59 源码字面量守卫 + 反向验证."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read()

    def test_v37_9_59_marker_present(self):
        """watchdog 必须含 V37.9.59 marker (历史追溯)."""
        self.assertIn("V37.9.59", self.src,
            "V37.9.59 marker 必须在 watchdog 中可追溯")

    def test_lineage_v37_9_58_hotfix3_preserved(self):
        """V37.9.58-hotfix3 (MR-19 立案) marker 必须保留 (V37.9.59 是 hotfix3 续)."""
        self.assertIn("V37.9.58-hotfix3", self.src,
            "V37.9.58-hotfix3 marker 必须保留 (V37.9.59 不破坏 hotfix3)")

    def test_lineage_v37_9_58_hotfix4_preserved(self):
        """V37.9.58-hotfix4 (set -E + caller || ALERT) 必须保留."""
        self.assertIn("V37.9.58-hotfix4", self.src,
            "V37.9.58-hotfix4 marker 必须保留")
        self.assertIn("set -eEo pipefail", self.src,
            "V37.9.58-hotfix4 set -eEo pipefail 必须保留")

    def test_v37_9_60_hotfix2_marker_present(self):
        """V37.9.60-hotfix2: 修 V37.9.59 我引入的两个 bug (kb_dream 路径 + 整数除法 UX)."""
        self.assertIn("V37.9.60-hotfix2", self.src,
            "V37.9.60-hotfix2 marker 必须在 watchdog 中可追溯")

    def test_v37_9_60_hotfix2_check_log_freshness_uses_minutes(self):
        """V37.9.60-hotfix2: check_log_freshness 必须根据 ELAPSED/max_silence 大小动态切换单位.

        V37.9.59 整数除法 bug — max_silence=600s 永远显示 "0h 阈值 0h" 用户无法判断严重度.
        修法: ELAPSED >= 3600 显示 h, 否则显示 m; max_silence 同理.
        """
        self.assertIn("ELAPSED_UNIT", self.src,
            "V37.9.60-hotfix2: check_log_freshness 必须用 ELAPSED_UNIT 动态单位变量")
        self.assertIn("MAX_UNIT", self.src,
            "V37.9.60-hotfix2: check_log_freshness 必须用 MAX_UNIT 动态单位变量")
        # 反向守卫: 旧整数除法字面量不得回归
        self.assertNotIn('ELAPSED_HOURS=$(( ELAPSED / 3600 ))', self.src,
            "V37.9.60-hotfix2: 禁止回退到 V37.9.59 整数除法 (max_silence < 3600 时永远显示 0h)")
        self.assertNotIn('MAX_HOURS=$(( max_silence / 3600 ))', self.src,
            "V37.9.60-hotfix2: 禁止回退到 V37.9.59 max_silence 整数除法")

    def test_v37_9_60_hotfix2_alert_message_uses_dynamic_unit(self):
        """V37.9.60-hotfix2: ALERTS+= 推送消息也必须用 ELAPSED_UNIT/MAX_UNIT."""
        # 找含 ALERTS+ 且 含 "log" 且 含 "未更新" 的行 (check_log_freshness 内的 ALERTS+=)
        found_dynamic_unit_in_alerts = False
        for line in self.src.split("\n"):
            if "ALERTS+=" in line and "log " in line and "未更新" in line:
                if "ELAPSED_UNIT" in line and "MAX_UNIT" in line:
                    found_dynamic_unit_in_alerts = True
                    break
        self.assertTrue(found_dynamic_unit_in_alerts,
            "V37.9.60-hotfix2: ALERTS+= 推送消息必须用 ELAPSED_UNIT + MAX_UNIT 而非硬编码 h")


if __name__ == "__main__":
    unittest.main(verbosity=2)

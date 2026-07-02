"""V37.9.72 (i) CORE 项调查闭环守卫: 治理审计契约对齐 + auto_deploy 阈值修正.

触发: 2026-05-15 早 8:30 watchdog 报 CORE 项:
  - 🔴 治理审计: 异常状态 (pass)
  - 🔴 自动部署: log 29m 未更新 (阈值 10m, V37.9.59)

调查 (原则 #28 三问全过):
  项 1 真因 = 跨脚本契约不一致:
    governance_audit_cron.sh:87 OVERALL="pass" 写入 last_run.json status:"pass"
    watchdog line 280 期望 "ok|unknown" 作正常状态 → default 分支误报
    7+ 其他 ALIGNED jobs (V37.5/V37.8.10/V37.9.16/V37.9.39/40/41/43/44/45) 都用 "ok"
  项 2 真因 = V37.9.59 阈值与 V37.9.8 心跳错配:
    auto_deploy V37.9.8 仅整点 (minute<2) 写心跳 = 最长 60min 静默
    watchdog V37.9.59 阈值 600s = 10min → 必然误报
    非整点 watchdog 跑时 log mtime 必然 10-60min 超阈值

修复 (最小修复原则):
  项 1: governance_audit_cron.sh OVERALL="pass" → "ok" (单字符串改动)
        fail 不动 (维持告警目的, watchdog default 分支正确触发)
  项 2: job_watchdog.sh auto_deploy 阈值 600 → 4200 (60min + 10min slack)
        与 V37.9.8 设计原意对齐 (低噪声 24 行/天 + 心跳间隔最长 60min)

测试契约:
  Layer 1 (源码静态): 守卫两项修复字面量 + 反 buggy 模式守卫
  Layer 2 (反向验证证明 fix 必要性): 文档化 V37.9.72 前的 buggy 行为
"""
from __future__ import annotations

import subprocess
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
GOV_AUDIT_SH = os.path.join(REPO_ROOT, "governance_audit_cron.sh")
WATCHDOG_SH = os.path.join(REPO_ROOT, "job_watchdog.sh")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── 项 1: governance_audit 契约对齐守卫 ──────────────────────────────

class TestV37972ProjItemOneGovAuditContract(unittest.TestCase):
    """V37.9.72 (i) 项 1: governance_audit OVERALL 必须用 ok 不是 pass."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(GOV_AUDIT_SH)

    def test_overall_default_is_ok_not_pass(self):
        """OVERALL 默认值必须是 "ok" 与 watchdog 契约对齐."""
        # 找 line "OVERALL=" 的初始赋值 (不含 fail 分支)
        m = re.search(r'^OVERALL="(\w+)"', self.src, re.MULTILINE)
        self.assertIsNotNone(m, "OVERALL 默认赋值未找到")
        self.assertEqual(m.group(1), "ok",
            f"V37.9.72 (i) 项 1: OVERALL 默认必须 'ok', 实际 '{m.group(1)}'. "
            f"V37.9.72 前是 'pass' 让 watchdog 报 '治理审计: 异常状态 (pass)' 误告警.")

    def test_no_legacy_pass_default_in_active_code(self):
        """V37.9.72 反 buggy 守卫: OVERALL='pass' 字面量不能在生效代码中残留 (注释豁免)."""
        for line in self.src.split("\n"):
            stripped = line.strip()
            # 跳过注释行
            if stripped.startswith("#"):
                continue
            self.assertNotIn('OVERALL="pass"', line,
                f"V37.9.72 (i) 项 1 反 buggy: OVERALL='pass' 字面量不能在生效代码: '{line}'")

    def test_fail_branches_unchanged(self):
        """V37.9.72 不改 fail 分支 (watchdog default 分支应正确触发告警)."""
        # OVERALL="fail" 应有 2 处 (gov_violations 失败 + engine 失败)
        fail_count = self.src.count('OVERALL="fail"')
        self.assertEqual(fail_count, 2,
            f"V37.9.72 (i) 项 1: OVERALL='fail' 应保留 2 处 (gov+engine), 实际 {fail_count}. "
            f"fail 分支不动维持告警目的.")

    def test_v37_9_72_marker_present(self):
        """V37.9.72 marker 必须出现在 governance_audit_cron.sh (历史追溯)."""
        self.assertIn("V37.9.72", self.src,
            "V37.9.72 (i) marker 必须在 governance_audit_cron.sh")

    def test_marker_references_blood_lesson(self):
        """V37.9.72 注释必须引用 watchdog line 280 契约 + 跨脚本对齐血案."""
        # 关键字检查
        self.assertIn("watchdog", self.src.lower(),
            "V37.9.72 注释必须引用 watchdog (跨脚本契约对齐)")
        self.assertIn("ALIGNED jobs", self.src,
            "V37.9.72 注释必须引用 ALIGNED jobs 对齐")


# ── 项 2: auto_deploy 阈值修正守卫 ──────────────────────────────────

class TestV37972ProjItemTwoAutoDeployThreshold(unittest.TestCase):
    """V37.9.72 (i) 项 2: auto_deploy 阈值 600 → 4200 修正 V37.9.59 设计错配."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(WATCHDOG_SH)

    def test_auto_deploy_threshold_is_4200(self):
        """auto_deploy 阈值必须是 4200 (60min 心跳间隔 + 10min slack)."""
        # 找 "auto_deploy|...|4200|"
        m = re.search(r'"auto_deploy\|[^|]+\|(\d+)\|', self.src)
        self.assertIsNotNone(m, "auto_deploy LOG_FRESHNESS entry 未找到")
        threshold = int(m.group(1))
        self.assertEqual(threshold, 4200,
            f"V37.9.72 (i) 项 2: auto_deploy 阈值必须 4200 (60min+10min slack), 实际 {threshold}. "
            f"V37.9.72 前是 600 让 auto_deploy V37.9.8 整点心跳 (最长 60min 静默) 必然误报.")

    def test_no_legacy_600_threshold(self):
        """V37.9.72 反 buggy 守卫: auto_deploy|...|600 旧阈值不能残留."""
        self.assertNotRegex(
            self.src,
            r'"auto_deploy\|[^|]+\|600\|',
            "V37.9.72 (i) 项 2 反 buggy: auto_deploy|...|600 旧阈值字面量不能残留"
        )

    def test_v37_9_72_marker_in_watchdog(self):
        """V37.9.72 marker 必须在 watchdog auto_deploy 条目附近 (历史追溯)."""
        # 找 auto_deploy entry 周围 500 char
        idx = self.src.find('"auto_deploy|')
        self.assertGreater(idx, 0, "auto_deploy entry 未找到")
        # 前后 500 字符内必须含 V37.9.72 marker
        nearby = self.src[max(0, idx - 500):idx + 500]
        self.assertIn("V37.9.72", nearby,
            "V37.9.72 (i) 项 2 marker 必须在 auto_deploy entry 附近")

    def test_marker_references_v37_9_8_heartbeat_design(self):
        """V37.9.72 注释必须引用 V37.9.8 心跳设计 + 60min 跨脚本对齐."""
        # 跨 watchdog 全文找
        self.assertIn("V37.9.8", self.src,
            "V37.9.72 (i) 注释必须引用 V37.9.8 心跳设计")
        idx = self.src.find('"auto_deploy|')
        self.assertGreater(idx, 0, "auto_deploy entry 未找到")
        nearby = self.src[max(0, idx - 800):idx + 200]
        self.assertIn("60min", nearby,
            "V37.9.72 注释必须引用 60min 心跳间隔上限")

    def test_other_log_freshness_jobs_thresholds_unchanged(self):
        """V37.9.72 不改其他 LOG_FRESHNESS_JOBS 阈值 (scope 严格控制)."""
        # wa_keepalive 应仍是 5400 (1.5h, 30min×3 周期)
        self.assertRegex(self.src, r'"wa_keepalive\|[^|]+\|5400\|',
            "V37.9.72 不改 wa_keepalive 阈值 (维持 V37.9.59 原值 5400)")


# ── V37.9.72 综合契约守卫 ─────────────────────────────────────────

class TestV37972IntegrationContracts(unittest.TestCase):
    """V37.9.72 (i) 两项修复综合契约."""

    def test_governance_checker_pass_literal_untouched(self):
        """V37.9.72 不动 governance_checker.py 内部 'pass' (它是 check 状态真理源)."""
        gov_checker = os.path.join(REPO_ROOT, "ontology", "governance_checker.py")
        with open(gov_checker, "r", encoding="utf-8") as f:
            src = f.read()
        # 应有多处 "status": "pass"
        count = src.count('"status": "pass"')
        self.assertGreaterEqual(count, 5,
            f"V37.9.72 不动 governance_checker.py 内部 status:pass 字面量 (应≥5), 实际 {count}. "
            f"这是 governance 体系真理源, 与 last_run.json 外部契约解耦.")

    def test_both_fixes_can_be_grep_for_audit(self):
        """V37.9.72 两项修复都可被 grep 'V37.9.72' 全局审计."""
        gov_src = _read(GOV_AUDIT_SH)
        wd_src = _read(WATCHDOG_SH)
        self.assertIn("V37.9.72", gov_src,
            "项 1 修复必须含 V37.9.72 marker")
        self.assertIn("V37.9.72", wd_src,
            "项 2 修复必须含 V37.9.72 marker")


# ── V37.9.105-hotfix: governance_audit FATAL line=64 误报修复 ──────────
# 根因: set -eEuo pipefail 的 -E errtrace 让 $(...python3...) 子 shell 继承
# ERR trap, governance_checker 退出 1 (真发现失败) 时子 shell 内误触发 FATAL,
# 尽管外层 || GOV_RC=$? 已捕获. 证据: 用户同一次 07:00 run 收到 FATAL + 真失败
# 两条告警 (若真 abort 不会有第二条). 修复: set +E 包裹两处 python3 命令替换.

class TestV37_9_105_GovAuditFatalFalsePositive(unittest.TestCase):
    """V37.9.105-hotfix: governance_audit_cron 假 FATAL line=64 误报修复."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(GOV_AUDIT_SH)

    def test_v37_9_105_hotfix_marker_present(self):
        self.assertIn("V37.9.105-hotfix", self.src,
            "FATAL 误报修复 marker 必须在 governance_audit_cron.sh")

    def test_governance_checker_wrapped_in_set_plus_E(self):
        """governance_checker.py --full 命令替换前必须 set +E (关 errtrace)."""
        idx = self.src.find("python3 ontology/governance_checker.py --full 2>&1")
        self.assertGreater(idx, 0)
        before = self.src[max(0, idx - 200):idx]
        self.assertIn("set +E", before,
            "governance_checker $(...) 前必须 set +E 防子shell ERR trap 误触发")

    def test_engine_check_under_single_errtrace_off_region(self):
        """V37.9.214: engine.py --check 在单一 set +E errtrace-off 区域内运行
        (governance 块起 set +E 一次, 与 engine $() 之间无 set -E re-enable landmine)."""
        gov_plus = self.src.find("set +E")
        eng_idx = self.src.find("python3 ontology/engine.py --check 2>&1")
        self.assertGreater(gov_plus, 0)
        self.assertGreater(eng_idx, gov_plus,
            "engine $() 必须在 governance set +E 之后 (同一 errtrace-off 区域)")
        between = self.src[gov_plus:eng_idx]
        self.assertNotRegex(between, r'(?m)^[ \t]*set -E[ \t]*(?:#.*)?$',
            "V37.9.214: governance set +E 与 engine $() 之间不得有 set -E re-enable (bash 3.2 landmine)")

    def test_v37_9_214_single_set_plus_E_no_reenable(self):
        """V37.9.214 日落法根治: 单一 set +E (errtrace 关到脚本尾), 0 处 set -E
        re-enable — bash 3.2 每个 set -E re-enable 是 landmine (3 次假 FATAL 复发:
        V37.9.105 line 64 / line 100 / 2026-07-02 line 101). errexit (set -e) +
        顶部 set -eEuo 声明保留 (MR-19 核心 + governance check 7526)."""
        cmd_lines = [ln.strip() for ln in self.src.split("\n")]
        # set +E 可带行尾注释 (V37.9.214); set -eEuo pipefail (顶部声明) 不计入
        plus = sum(1 for ln in cmd_lines if ln == "set +E" or ln.startswith("set +E "))
        minus = sum(1 for ln in cmd_lines if ln == "set -E" or ln.startswith("set -E "))
        self.assertEqual(plus, 1, "V37.9.214: 恰好 1 处 set +E (单一 errtrace-off 区域)")
        self.assertEqual(minus, 0, "V37.9.214: 0 处 set -E re-enable (bash 3.2 landmine 已消除)")
        self.assertIn("set -eEuo pipefail", self.src, "顶部 set -eEuo 声明必须保留 (MR-19 + check 7526)")

    def test_outer_capture_preserved(self):
        """外层 || GOV_RC=$? / || ENGINE_RC=$? 退出码捕获必须保留."""
        self.assertIn("|| GOV_RC=$?", self.src)
        self.assertIn("|| ENGINE_RC=$?", self.src)

    def test_err_trap_still_registered(self):
        """ERR trap 仍注册 (修复只关命令替换处, 不删主脚本 trap)."""
        self.assertIn("trap '_cron_monitor_fatal_handler $LINENO' ERR", self.src)

    def test_set_plus_E_runtime_suppresses_subshell_trap(self):
        """运行时: set +E 后命令替换内失败不触发 ERR trap (bash 行为契约)."""
        script = """
set -eEuo pipefail
trap 'echo TRAP_FIRED' ERR
RC=0
set +E
OUT=$(python3 -c 'import sys; sys.exit(1)' 2>&1) || RC=$?
set -E
echo "RC=$RC DONE"
"""
        proc = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=30)
        self.assertIn("RC=1 DONE", proc.stdout, f"外层应捕获 rc=1: {proc.stdout}")
        self.assertNotIn("TRAP_FIRED", proc.stdout,
            "set +E 后子shell 失败不应触发 ERR trap")


if __name__ == "__main__":
    unittest.main(verbosity=2)

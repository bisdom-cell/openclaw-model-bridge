"""V37.9.58-hotfix3 — job_watchdog.sh 自监控契约 source-level 守卫.

血案: 2026-05-05 16:30 ~ 2026-05-12 watchdog 因 macOS bsd awk multibyte 错误 +
set -eo pipefail + 无 ERR trap → 静默 abort 7 天. 累积告警从未推送, 用户 5/12
视角发现"今天只收到 HN+HF"才暴露.

V37.9.58-hotfix3 治本三层 + 元规则 MR-19 立案:
  Step A: awk LC_ALL=C + `|| true` 容错
  Step B: ERR trap _watchdog_fatal_handler 主动推送 [SYSTEM_ALERT]
  Step C: EXIT trap canary heartbeat (~/watchdog_canary.json + alive 推送)
  Step D: STALE_LOCK_DIRS 补 5 个 jobs (rss/gh/ai_leaders/finance/ontology)

测试契约:
  Layer 1 (源码静态): 守卫所有上述代码字面量存在
  Layer 2 (语法运行时): bash -n + 模拟执行 ERR trap handler
  Layer 3 (反向验证): sabotage 移除 LC_ALL=C → 守卫立即抓到 (本测试套件)

INV-WATCHDOG-SELF-001 (meta_rule=MR-19) derivative test suite.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
WATCHDOG_SH = os.path.join(REPO_ROOT, "job_watchdog.sh")


def _read_src():
    with open(WATCHDOG_SH, "r", encoding="utf-8") as f:
        return f.read()


# ── Tier 1: Step A - awk LC_ALL=C 容错 ───────────────────────────────

class TestStepAAwkMultibyteSafety(unittest.TestCase):
    """V37.9.58-hotfix3 Step A: macOS bsd awk multibyte conversion failure 不挂脚本.

    根因: 5/5 16:30 起 awk 处理某 log 含无效 UTF-8 字节 → towc multibyte 错误 →
    awk exit 1 → pipefail → set -e abort 7 天.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = _read_src()

    def test_awk_uses_lc_all_c(self):
        """awk 调用必须有 LC_ALL=C 前缀防 multibyte conversion failure."""
        self.assertIn("LC_ALL=C awk", self.src,
            "V37.9.58-hotfix3: awk 必须 LC_ALL=C 防 macOS bsd awk towc multibyte")

    def test_awk_pipe_has_fail_open(self):
        """awk pipe 后必须有 `|| true` 容错防 set -e abort."""
        # 用宽松匹配: awk 块之后含 "|| true" 字面量
        # 严格写法: tail | LC_ALL=C awk ... 2>/dev/null || true
        self.assertRegex(
            self.src, r"LC_ALL=C\s+awk[\s\S]+?\|\|\s*true",
            "V37.9.58-hotfix3: awk pipe 必须含 `|| true` 容错"
        )

    def test_v37_9_58_hotfix3_awk_comment_present(self):
        """awk 行附近必须含 V37.9.58-hotfix3 + 血案注释 (历史追溯)."""
        self.assertIn("V37.9.58-hotfix3", self.src,
            "V37.9.58-hotfix3 marker 必须在 watchdog 源码")
        # 注释含 multibyte / towc / awk silent 关键字
        self.assertTrue(
            ("multibyte" in self.src.lower() or "towc" in self.src.lower()),
            "V37.9.58-hotfix3 awk 注释必须含 multibyte 或 towc 血案关键字"
        )


# ── Tier 2: Step B - ERR trap silent abort 变 loud ───────────────────

class TestStepBErrTrapSilentAbortBecomesLoud(unittest.TestCase):
    """V37.9.58-hotfix3 Step B: ERR trap 主动推送告警, 防 watchdog 静默 abort.

    5/5-5/12 7 天 silent failure 根本原因: 没 ERR trap → set -e abort 不告警.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = _read_src()

    def test_fatal_handler_function_defined(self):
        """_watchdog_fatal_handler() 函数必须定义."""
        self.assertIn("_watchdog_fatal_handler", self.src,
            "V37.9.58-hotfix3 Step B: ERR trap handler 函数必须存在")
        self.assertRegex(self.src, r"_watchdog_fatal_handler\(\)\s*\{",
            "_watchdog_fatal_handler 必须有标准 bash 函数定义语法")

    def test_err_trap_registered(self):
        """trap ERR 必须注册 _watchdog_fatal_handler $LINENO."""
        self.assertIn("trap '_watchdog_fatal_handler", self.src,
            "trap ERR 必须注册 _watchdog_fatal_handler")
        # LINENO 必须传给 handler 供精确定位
        self.assertRegex(self.src, r"_watchdog_fatal_handler\s+\$LINENO",
            "ERR trap 必须传 $LINENO 给 handler 供 abort 行号定位")

    def test_fatal_handler_pushes_system_alert(self):
        """ERR trap handler 必须推 [SYSTEM_ALERT] 关键字."""
        self.assertIn("[SYSTEM_ALERT] watchdog FATAL", self.src,
            "V37.9.58-hotfix3: ERR trap 必须推 [SYSTEM_ALERT] watchdog FATAL")

    def test_fatal_handler_has_three_layer_fallback(self):
        """ERR trap 推送必须有 FAIL-OPEN 三层 fallback (notify/openclaw/local file)."""
        # Layer 1: notify 函数
        self.assertIn("command -v notify", self.src,
            "ERR trap 必须检查 notify 函数可用性")
        # Layer 2: openclaw fallback
        self.assertIn("OPENCLAW", self.src,
            "ERR trap 必须有 openclaw fallback 路径")
        # Layer 3: 写本地 alerts log (即使推送失败也有证据)
        self.assertIn(".openclaw_alerts.log", self.src,
            "ERR trap 必须写本地 alerts log 作为最后兜底")

    def test_fatal_handler_references_blood_lesson(self):
        """ERR trap 推送内容必须引用 5/5-5/12 silent 7 天血案 (历史可追)."""
        # 推送消息含日期 / silent / 7 天 / 血案关键字
        self.assertRegex(
            self.src,
            r"5/5[\-—]5/12|silent\s+7\s*天|监控自身死亡",
            "V37.9.58-hotfix3: ERR trap 推送必须引用 silent 7 天血案历史"
        )


# ── Tier 3: Step C - EXIT trap canary heartbeat ──────────────────────

class TestStepCCanaryHeartbeat(unittest.TestCase):
    """V37.9.58-hotfix3 Step C: EXIT trap canary 元监控.

    watchdog 自身死活可被外部检测 — 用户超过 12h 没收到 alert 也没收到 canary
    → watchdog 死了. 这是 MR-19 第三契约 canary_writer 兑现.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = _read_src()

    def test_exit_handler_function_defined(self):
        """_watchdog_exit_handler() 函数必须定义 (合并 rmdir + canary 写)."""
        self.assertIn("_watchdog_exit_handler", self.src,
            "V37.9.58-hotfix3 Step C: EXIT trap handler 必须存在")

    def test_exit_trap_registered(self):
        """trap EXIT 必须用 _watchdog_exit_handler 不再单独 rmdir."""
        self.assertIn("trap '_watchdog_exit_handler' EXIT", self.src,
            "trap EXIT 必须注册 _watchdog_exit_handler (合并 rmdir + canary)")

    def test_canary_file_written(self):
        """EXIT trap 必须写 ~/watchdog_canary.json."""
        self.assertIn("watchdog_canary.json", self.src,
            "V37.9.58-hotfix3 Step C: EXIT trap 必须写 watchdog_canary.json")

    def test_canary_alive_push_when_no_alerts(self):
        """ALERTS=0 时必须推 'alive' 消息到 Discord (元监控钩子)."""
        self.assertIn("watchdog alive", self.src,
            "V37.9.58-hotfix3 Step C: ALERTS=0 时必须推 'watchdog alive' canary")

    def test_canary_only_on_normal_exit(self):
        """canary 仅在正常完成 (exit=0) 时推, 避免 abort 时双重推送."""
        # 找 _watchdog_exit_handler 函数体, 必须有 exit code 检查
        m = re.search(r"_watchdog_exit_handler\(\)\s*\{([\s\S]+?)^\}", self.src, re.MULTILINE)
        self.assertIsNotNone(m, "_watchdog_exit_handler 函数体必须可解析")
        handler_body = m.group(1)
        # 必须有 exit=0 守卫 (避免 abort 时也推 alive 误导)
        self.assertRegex(
            handler_body,
            r'final_exit.*-eq\s*0|exit_code.*=.*0',
            "V37.9.58-hotfix3: canary 推送必须有 exit=0 守卫 (避免 abort 时误推 alive)"
        )

    def test_canary_json_has_required_fields(self):
        """canary JSON 必须含 last_completed / checks / alerts 字段供外部 monitor 消费."""
        # JSON template 必须含这些 keys
        for field in ['"last_completed"', '"checks"', '"alerts"']:
            self.assertIn(field, self.src,
                f"V37.9.58-hotfix3: canary JSON 必须含 {field} 字段")


# ── Tier 4: Step D - STALE_LOCK_DIRS 补齐 5 个 jobs ────────────────

class TestStepDStaleLockListCompleteness(unittest.TestCase):
    """V37.9.58-hotfix3 Step D: STALE_LOCK_DIRS 补齐 5/12 诊断发现的盲区.

    用户 5/12 诊断: rss_blogs.lockdir 残留 45h / github_trending 25h / 都不在
    watchdog 监控列表 = 监控覆盖盲区. V37.9.58-hotfix3 补齐.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = _read_src()

    def test_stale_lock_dirs_contains_rss_blogs(self):
        """STALE_LOCK_DIRS 必须含 rss_blogs (45h 残留盲区)."""
        self.assertIn("/tmp/rss_blogs.lockdir", self.src,
            "V37.9.58-hotfix3 Step D: rss_blogs.lockdir 必须在 STALE list (5/10 18:00 残留 45h 实证)")

    def test_stale_lock_dirs_contains_github_trending(self):
        """STALE_LOCK_DIRS 必须含 github_trending (25h 残留盲区)."""
        self.assertIn("/tmp/github_trending.lockdir", self.src,
            "V37.9.58-hotfix3 Step D: github_trending.lockdir 必须在 STALE list")

    def test_stale_lock_dirs_contains_ai_leaders_x(self):
        """STALE_LOCK_DIRS 必须含 ai_leaders_x."""
        self.assertIn("/tmp/ai_leaders_x.lockdir", self.src,
            "V37.9.58-hotfix3 Step D: ai_leaders_x.lockdir 必须在 STALE list")

    def test_stale_lock_dirs_contains_finance_news(self):
        """STALE_LOCK_DIRS 必须含 finance_news."""
        self.assertIn("/tmp/finance_news.lockdir", self.src,
            "V37.9.58-hotfix3 Step D: finance_news.lockdir 必须在 STALE list")

    def test_stale_lock_dirs_contains_ontology_sources(self):
        """STALE_LOCK_DIRS 必须含 ontology_sources."""
        self.assertIn("/tmp/ontology_sources.lockdir", self.src,
            "V37.9.58-hotfix3 Step D: ontology_sources.lockdir 必须在 STALE list")


# ── Tier 5: 语法 + 集成 ─────────────────────────────────────────────

class TestWatchdogSyntaxAndIntegration(unittest.TestCase):
    """V37.9.58-hotfix3 后 watchdog 语法 + 集成层契约."""

    def test_bash_n_syntax_valid(self):
        """bash -n 必须通过 (watchdog 整脚本语法 OK)."""
        result = subprocess.run(
            ["bash", "-n", WATCHDOG_SH],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0,
            f"bash -n 语法检查失败: {result.stderr}")

    def test_set_eo_pipefail_present(self):
        """set -eEo pipefail 必须保留 (V37.9.58-hotfix4: 加 -E errtrace 让 ERR trap 在 function 内生效).

        历史: V37.9.58-hotfix3 用 set -eo pipefail (无 -E) → ERR trap 在 function
        内 fail 不触发 → silent abort 仍存在. V37.9.58-hotfix4 加 -E (errtrace).
        """
        with open(WATCHDOG_SH, "r") as f:
            src = f.read()
        # alternation: 接受 set -eEo pipefail (V37.9.58-hotfix4) 或 set -eo pipefail (V37.9.58-hotfix3 旧)
        self.assertTrue(
            "set -eEo pipefail" in src or "set -eo pipefail" in src,
            "V37.9.58-hotfix3/4 必须保留 set -e* pipefail (ERR trap 是补充而非替代)"
        )

    def test_set_E_errtrace_present_v37_9_58_hotfix4(self):
        """V37.9.58-hotfix4: set -E (errtrace) 必须加 — 让 ERR trap 在 function 内生效."""
        with open(WATCHDOG_SH, "r") as f:
            src = f.read()
        self.assertIn("set -eEo pipefail", src,
            "V37.9.58-hotfix4: 必须 set -eEo (含 -E errtrace) 让 ERR trap 在 function 内 fail 时真触发. "
            "5/12 16:45 实测 V37.9.58-hotfix3 (无 -E) ERR trap 仍 silent.")

    def test_scan_logs_caller_has_alert_fallback_v37_9_58_hotfix4(self):
        """V37.9.58-hotfix4: scan_logs caller 必须 || ALERTS+= 兜底, 防 scan_logs internal fail 杀整 watchdog."""
        with open(WATCHDOG_SH, "r") as f:
            src = f.read()
        # 匹配 caller `scan_logs ... || ALERTS+=`
        self.assertRegex(
            src,
            r'scan_logs\s+"\$logfile"\s+"\$job_name"\s+\|\|\s+ALERTS\+=',
            "V37.9.58-hotfix4: scan_logs caller 必须有 || ALERTS+= 兜底 (scan_logs internal fail 不杀脚本)"
        )

    def test_trap_err_after_set_e(self):
        """trap ERR 必须在 set -e* 之后定义 (set -e 之前 trap ERR 无效).
        V37.9.58-hotfix4 alternation: 接受 set -eEo pipefail (含 errtrace) 或旧 set -eo pipefail.
        """
        with open(WATCHDOG_SH, "r") as f:
            lines = f.readlines()
        set_e_line = None
        trap_err_line = None
        for i, line in enumerate(lines):
            # V37.9.58-hotfix4 alternation: set -eEo (含 -E) 优先, fallback set -eo
            if "set -eEo pipefail" in line or "set -eo pipefail" in line:
                set_e_line = i
            if "trap '_watchdog_fatal_handler" in line:
                trap_err_line = i
                break
        self.assertIsNotNone(set_e_line, "set -e* pipefail 行未找到")
        self.assertIsNotNone(trap_err_line, "trap ERR 行未找到")
        self.assertGreater(trap_err_line, set_e_line,
            "V37.9.58-hotfix3/4: trap ERR 必须在 set -e* 之后注册 (否则 trap 无效)")


# ── Tier 6: 反向验证 sabotage 守卫真有效 ────────────────────────────

class TestReverseVerificationGuardsAreReal(unittest.TestCase):
    """sabotage watchdog 关键字面量 → 守卫立即抓到 (V37.9.58-hotfix3 守卫真有效)."""

    def setUp(self):
        # 备份 watchdog 源码
        self.backup = _read_src()

    def tearDown(self):
        # 恢复 watchdog 源码 (确保 sabotage 后还原)
        with open(WATCHDOG_SH, "w", encoding="utf-8") as f:
            f.write(self.backup)

    def _sabotage(self, old, new):
        """临时把 old 替换为 new (V37.9.59: replace 所有出现, 防 V37.9.59+ 加重复字面量
        让 sabotage 单点替换无效)."""
        src = self.backup.replace(old, new)  # V37.9.59: 不传 count, replace 所有
        if src == self.backup:
            self.skipTest(f"sabotage target '{old[:40]}' 未找到")
        with open(WATCHDOG_SH, "w", encoding="utf-8") as f:
            f.write(src)

    def test_sabotage_remove_lc_all_c_caught(self):
        """sabotage 移除 LC_ALL=C → test_awk_uses_lc_all_c 立即 fail."""
        self._sabotage("LC_ALL=C awk", "awk")
        # 立即跑 TestStepAAwkMultibyteSafety
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "test_watchdog_self_monitoring.TestStepAAwkMultibyteSafety"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=20
        )
        self.assertNotEqual(result.returncode, 0,
            f"sabotage LC_ALL=C 后守卫应立即 fail, got returncode={result.returncode}\n{result.stderr}")

    def test_sabotage_remove_err_trap_caught(self):
        """sabotage 移除 trap ERR 注册 → test_err_trap_registered 立即 fail."""
        self._sabotage(
            "trap '_watchdog_fatal_handler $LINENO' ERR",
            "# SABOTAGED: removed trap ERR registration"
        )
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "test_watchdog_self_monitoring.TestStepBErrTrapSilentAbortBecomesLoud"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=20
        )
        self.assertNotEqual(result.returncode, 0,
            "sabotage 移除 trap ERR 后守卫必须立即 fail")

    def test_sabotage_remove_canary_caught(self):
        """sabotage 移除 watchdog_canary.json → canary 守卫立即 fail."""
        self._sabotage("watchdog_canary.json", "SABOTAGED_no_canary.json")
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "test_watchdog_self_monitoring.TestStepCCanaryHeartbeat"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=20
        )
        self.assertNotEqual(result.returncode, 0,
            "sabotage 移除 canary 后守卫必须立即 fail")


# ── Tier 7: governance INV-WATCHDOG-SELF-001 引用 ──────────────────

class TestGovernanceLinkage(unittest.TestCase):
    """V37.9.58-hotfix3 watchdog 必须与 INV-WATCHDOG-SELF-001 + MR-19 关联."""

    @classmethod
    def setUpClass(cls):
        gov_path = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
        with open(gov_path, "r", encoding="utf-8") as f:
            cls.gov_src = f.read()

    def test_mr_19_defined_in_governance(self):
        """MR-19 必须定义在 governance_ontology.yaml meta_rules 段."""
        self.assertIn("- id: MR-19", self.gov_src,
            "V37.9.58-hotfix3: MR-19 必须立案在 governance")
        self.assertIn("monitor-must-self-alarm-on-silent-abort", self.gov_src,
            "MR-19 name 必须明确")

    def test_inv_watchdog_self_001_defined(self):
        """INV-WATCHDOG-SELF-001 必须定义且 meta_rule=MR-19."""
        self.assertIn("- id: INV-WATCHDOG-SELF-001", self.gov_src,
            "INV-WATCHDOG-SELF-001 必须立案")
        # 找 INV 块附近 meta_rule 字段
        idx = self.gov_src.find("- id: INV-WATCHDOG-SELF-001")
        self.assertGreater(idx, 0)
        block = self.gov_src[idx:idx + 3000]
        self.assertIn("meta_rule: MR-19", block,
            "INV-WATCHDOG-SELF-001 必须引用 MR-19")
        self.assertIn("severity: critical", block,
            "INV-WATCHDOG-SELF-001 必须 critical 级别 (silent 监控是高风险)")

    def test_audit_metadata_v3_38(self):
        """audit_metadata.version 升级到 3.38 (V37.9.58-hotfix3 兑现)."""
        self.assertIn('version: "3.38"', self.gov_src,
            "V37.9.58-hotfix3: audit_metadata.version 必须升 3.38")

    def test_audit_metadata_meta_rules_19(self):
        """audit_metadata.meta_rules 升级到 19 (MR-1~MR-19)."""
        self.assertRegex(self.gov_src, r"meta_rules:\s*19",
            "V37.9.58-hotfix3: meta_rules 必须升到 19 (含 MR-19)")


if __name__ == "__main__":
    unittest.main(verbosity=2)

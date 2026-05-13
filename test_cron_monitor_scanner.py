#!/usr/bin/env python3
"""test_cron_monitor_scanner.py — V37.9.60 MR-19 err_trap_handler scanner 单测

Coverage:
  - 纯函数: has_set_e_strict / has_err_trap / has_system_alert_marker
  - 端到端: scan_script / scan_repo
  - CLI: --list / --file / 默认全量扫描
  - 反向验证: sabotage 4 个 governed 脚本立即触发 violation
  - 源码守卫: V37.9.60 标记 / FAIL-CLOSE 契约 / 4 个 governed 脚本登记完整

参考 V37.9.58-hotfix2 test_heredoc_import_scanner.py 同款模式。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cron_monitor_scanner as scanner  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class TestHasSetEStrict(unittest.TestCase):
    """纯函数: has_set_e_strict (是否启用 set -e 类)"""

    def test_set_e_plain(self):
        self.assertTrue(scanner.has_set_e_strict("set -e\n"))

    def test_set_eo_pipefail(self):
        self.assertTrue(scanner.has_set_e_strict("set -eo pipefail\n"))

    def test_set_euo_pipefail(self):
        self.assertTrue(scanner.has_set_e_strict("set -euo pipefail\n"))

    def test_set_eEo_pipefail(self):
        self.assertTrue(scanner.has_set_e_strict("set -eEo pipefail\n"))

    def test_set_eEuo_pipefail(self):
        self.assertTrue(scanner.has_set_e_strict("set -eEuo pipefail\n"))

    def test_set_ex(self):
        self.assertTrue(scanner.has_set_e_strict("set -ex\n"))

    def test_set_u_alone_no_e(self):
        """set -u 单独无 e 不算"""
        self.assertFalse(scanner.has_set_e_strict("set -u\n"))

    def test_set_o_pipefail_alone_no_e(self):
        """set -o pipefail 单独无 e 不算"""
        self.assertFalse(scanner.has_set_e_strict("set -o pipefail\n"))

    def test_no_set_at_all(self):
        self.assertFalse(scanner.has_set_e_strict("echo hello\n"))

    def test_set_e_with_leading_whitespace(self):
        """缩进的 set -e 仍算 (函数内 set -e 也常见)"""
        self.assertTrue(scanner.has_set_e_strict("    set -e\n"))

    def test_empty_content(self):
        self.assertFalse(scanner.has_set_e_strict(""))
        self.assertFalse(scanner.has_set_e_strict(None))


class TestHasErrTrap(unittest.TestCase):
    """纯函数: has_err_trap (是否注册 trap ... ERR)"""

    def test_basic_trap_err(self):
        self.assertTrue(scanner.has_err_trap("trap 'cleanup' ERR\n"))

    def test_trap_err_with_inline_handler(self):
        self.assertTrue(scanner.has_err_trap(
            "trap '_my_fatal_handler $LINENO' ERR\n"
        ))

    def test_trap_err_with_double_quotes(self):
        self.assertTrue(scanner.has_err_trap('trap "cleanup" ERR\n'))

    def test_trap_with_multiple_signals_err_first(self):
        self.assertTrue(scanner.has_err_trap("trap 'cleanup' ERR EXIT\n"))

    def test_trap_with_multiple_signals_err_after(self):
        self.assertTrue(scanner.has_err_trap("trap 'cleanup' EXIT ERR\n"))

    def test_trap_exit_only_no_err(self):
        """只有 EXIT 没有 ERR 不算"""
        self.assertFalse(scanner.has_err_trap("trap 'cleanup' EXIT\n"))

    def test_no_trap_at_all(self):
        self.assertFalse(scanner.has_err_trap("echo hello\n"))

    def test_comment_with_err_does_not_count(self):
        """注释行提到 ERR 不算 (避免 docstring 引用误判)"""
        self.assertFalse(scanner.has_err_trap("# trap 'cleanup' ERR\n"))

    def test_empty_content(self):
        self.assertFalse(scanner.has_err_trap(""))
        self.assertFalse(scanner.has_err_trap(None))


class TestHasSystemAlertMarker(unittest.TestCase):
    """纯函数: has_system_alert_marker"""

    def test_marker_present(self):
        self.assertTrue(scanner.has_system_alert_marker(
            'msg="[SYSTEM_ALERT] test"\n'
        ))

    def test_marker_in_comment_still_counts(self):
        """简化检查: 整脚本任意位置出现即合规"""
        self.assertTrue(scanner.has_system_alert_marker(
            "# [SYSTEM_ALERT] referenced\n"
        ))

    def test_marker_absent(self):
        self.assertFalse(scanner.has_system_alert_marker(
            "echo no alert here\n"
        ))

    def test_empty(self):
        self.assertFalse(scanner.has_system_alert_marker(""))


class TestScanScript(unittest.TestCase):
    """端到端: scan_script — 真实脚本扫描"""

    def _write_temp_script(self, content):
        """写临时脚本返回路径 (caller 负责清理)"""
        fd, path = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return path

    def test_compliant_script_no_findings(self):
        path = self._write_temp_script(
            "#!/bin/bash\n"
            "set -eEuo pipefail\n"
            "_fatal_handler() {\n"
            '    echo "[SYSTEM_ALERT] something broke" >&2\n'
            "}\n"
            "trap '_fatal_handler' ERR\n"
            "echo hello\n"
        )
        try:
            findings = scanner.scan_script(path)
            self.assertEqual(findings, [])
        finally:
            os.remove(path)

    def test_violation_missing_err_trap(self):
        path = self._write_temp_script(
            "#!/bin/bash\nset -euo pipefail\necho hello\n"
        )
        try:
            findings = scanner.scan_script(path)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0][1], "missing_err_trap")
        finally:
            os.remove(path)

    def test_violation_trap_no_alert(self):
        """有 trap ERR 但脚本里没 [SYSTEM_ALERT] 字面量 (handler 不推告警)"""
        path = self._write_temp_script(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "trap 'echo crashed >&2' ERR\n"
            "echo hello\n"
        )
        try:
            findings = scanner.scan_script(path)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0][1], "trap_handler_no_alert")
        finally:
            os.remove(path)

    def test_no_set_e_no_findings(self):
        """无 set -e 时不强制 ERR trap"""
        path = self._write_temp_script(
            "#!/bin/bash\necho hello\n"
        )
        try:
            findings = scanner.scan_script(path)
            self.assertEqual(findings, [])
        finally:
            os.remove(path)

    def test_file_not_found(self):
        findings = scanner.scan_script("/nonexistent/path.sh")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][1], "file_not_readable")


class TestScanRepoIntegration(unittest.TestCase):
    """端到端: scan_repo — 真实 governed scripts 整 repo 验证"""

    def test_real_repo_all_compliant(self):
        """V37.9.60 修复后 4 个 governed scripts 全合规"""
        findings = scanner.scan_repo(REPO_ROOT)
        msg = (
            f"V37.9.60 期望 0 violations, 实际: {len(findings)}\n"
            f"详情: {findings}"
        )
        self.assertEqual(findings, [], msg)

    def test_governed_scripts_list_locked(self):
        """SCRIPTS_REQUIRING_ERR_TRAP 必须含 7 个核心脚本 (V37.9.60 4 个 cron 聚合 + V37.9.61 3 个 LLM-task)"""
        required = {
            # V37.9.58-hotfix3 / V37.9.60: cron 类聚合监控
            "job_watchdog.sh",
            "governance_audit_cron.sh",
            "daily_ops_report.sh",
            "auto_deploy.sh",
            # V37.9.61: LLM-task 类 (V37.9.60-hotfix3 kb_deep_dive 5/8-5/12 血案揭露 framework gap)
            "kb_deep_dive.sh",
            "kb_evening.sh",
            "kb_review.sh",
            # kb_dream.sh 不在 (仅 set -o pipefail 无 -e, silent abort 不会发生)
        }
        self.assertEqual(
            set(scanner.SCRIPTS_REQUIRING_ERR_TRAP),
            required,
            "SCRIPTS_REQUIRING_ERR_TRAP 漂移. V37.9.61 锁定 7 个 governed scripts",
        )


class TestReverseVerificationGuardsAreReal(unittest.TestCase):
    """V37.9.58-hotfix3 同款反向验证: sabotage 真实脚本立即触发 violation"""

    def _sabotage_script_remove_err_trap(self, script_name):
        """临时移除指定 governed 脚本的 trap ERR, 验证 scanner 立即抓到"""
        script_path = os.path.join(REPO_ROOT, script_name)
        with open(script_path, "r", encoding="utf-8") as f:
            original = f.read()
        # sabotage: 删除所有 'trap ... ERR' 行 (粗暴但能验证守卫真有效)
        sabotaged = "\n".join(
            line for line in original.split("\n")
            if not (line.lstrip().startswith("trap") and " ERR" in line)
        )
        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(sabotaged)
            findings = scanner.scan_script(script_path)
            # 应该立即触发 missing_err_trap (脚本有 set -e* 但 ERR trap 被删了)
            self.assertGreater(
                len(findings), 0,
                f"sabotage {script_name} 移除 trap ERR 后 scanner 应立即抓到 violation",
            )
            self.assertEqual(findings[0][1], "missing_err_trap")
        finally:
            # 还原
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(original)

    def test_sabotage_job_watchdog_triggers_violation(self):
        self._sabotage_script_remove_err_trap("job_watchdog.sh")
        # 还原后应再次合规
        findings = scanner.scan_script(os.path.join(REPO_ROOT, "job_watchdog.sh"))
        self.assertEqual(findings, [], "还原后 job_watchdog.sh 应再次合规")

    def test_sabotage_governance_audit_triggers_violation(self):
        self._sabotage_script_remove_err_trap("governance_audit_cron.sh")
        findings = scanner.scan_script(
            os.path.join(REPO_ROOT, "governance_audit_cron.sh")
        )
        self.assertEqual(findings, [], "还原后 governance_audit_cron.sh 应再次合规")

    def test_sabotage_daily_ops_triggers_violation(self):
        self._sabotage_script_remove_err_trap("daily_ops_report.sh")
        findings = scanner.scan_script(
            os.path.join(REPO_ROOT, "daily_ops_report.sh")
        )
        self.assertEqual(findings, [], "还原后 daily_ops_report.sh 应再次合规")

    def test_sabotage_auto_deploy_triggers_violation(self):
        self._sabotage_script_remove_err_trap("auto_deploy.sh")
        findings = scanner.scan_script(os.path.join(REPO_ROOT, "auto_deploy.sh"))
        self.assertEqual(findings, [], "还原后 auto_deploy.sh 应再次合规")


class TestCliBehavior(unittest.TestCase):
    """CLI: --list / --file / 默认全量扫描"""

    def test_cli_list(self):
        result = subprocess.run(
            [sys.executable, "cron_monitor_scanner.py", "--list"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        for script in ["job_watchdog.sh", "governance_audit_cron.sh",
                       "daily_ops_report.sh", "auto_deploy.sh"]:
            self.assertIn(script, result.stdout)

    def test_cli_default_scan_passes(self):
        """V37.9.60 修复后整 repo 应 PASS exit 0"""
        result = subprocess.run(
            [sys.executable, "cron_monitor_scanner.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            result.returncode, 0,
            f"全量扫描应 exit 0, 实际 exit {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )
        self.assertIn("MR-19 scan PASSED", result.stdout)

    def test_cli_single_file_pass(self):
        result = subprocess.run(
            [sys.executable, "cron_monitor_scanner.py",
             "--file", "job_watchdog.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0)


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.60 source-level 守卫 (镜像 V37.9.58-hotfix2 模式)"""

    def setUp(self):
        scanner_path = os.path.join(REPO_ROOT, "cron_monitor_scanner.py")
        with open(scanner_path, "r", encoding="utf-8") as f:
            self.scanner_source = f.read()

    def test_v37_9_60_marker_present(self):
        """V37.9.60 marker 锚点必须存在"""
        self.assertIn("V37.9.60", self.scanner_source)

    def test_mr_19_reference_present(self):
        """MR-19 引用必须存在 (溯源 V37.9.58-hotfix3)"""
        self.assertIn("MR-19", self.scanner_source)

    def test_fail_close_contract_documented(self):
        """FAIL-CLOSE 契约必须在源码注释/文档中"""
        self.assertIn("FAIL-CLOSE", self.scanner_source)

    def test_governed_scripts_constant_locked(self):
        """SCRIPTS_REQUIRING_ERR_TRAP 必须含 4 个核心脚本字面量"""
        for script in ["job_watchdog.sh", "governance_audit_cron.sh",
                       "daily_ops_report.sh", "auto_deploy.sh"]:
            self.assertIn(script, self.scanner_source,
                          f"scanner 源码必须含 {script} 字面量守卫")

    def test_v37_9_58_hotfix3_lineage_documented(self):
        """必须引用 V37.9.58-hotfix3 watchdog 同款模式作为 lineage"""
        self.assertIn("V37.9.58-hotfix3", self.scanner_source)

    def test_governed_scripts_have_v37_9_60_marker(self):
        """每个 V37.9.60 修复的脚本必须含 V37.9.60 marker"""
        for script in ["governance_audit_cron.sh",
                       "daily_ops_report.sh",
                       "auto_deploy.sh"]:
            path = os.path.join(REPO_ROOT, script)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn(
                "V37.9.60", content,
                f"{script} 必须含 V37.9.60 marker 表明 MR-19 横向推广",
            )

    def test_governed_scripts_have_fatal_handler(self):
        """每个 V37.9.60 修复的脚本必须有 _*_fatal_handler 函数"""
        expected_handlers = {
            "governance_audit_cron.sh": "_governance_audit_fatal_handler",
            "daily_ops_report.sh": "_daily_ops_fatal_handler",
            "auto_deploy.sh": "_auto_deploy_fatal_handler",
        }
        for script, handler in expected_handlers.items():
            path = os.path.join(REPO_ROOT, script)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn(
                handler, content,
                f"{script} 必须含 {handler} 函数定义",
            )

    def test_governed_scripts_have_eE_errtrace(self):
        """V37.9.58-hotfix4 教训: set -e* 加 -E 让 function 内 fail 传播 ERR trap"""
        # V37.9.61 扩展: LLM-task 类 (kb_deep_dive/kb_evening/kb_review) 同款合规
        for script in ["governance_audit_cron.sh",
                       "daily_ops_report.sh",
                       "auto_deploy.sh",
                       "kb_deep_dive.sh",
                       "kb_evening.sh",
                       "kb_review.sh"]:
            path = os.path.join(REPO_ROOT, script)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # 必须含 -E (errtrace) 选项
            has_errtrace = any(
                marker in content for marker in
                ["set -eEo", "set -eEuo", "set -eE "]
            )
            self.assertTrue(
                has_errtrace,
                f"{script} 必须含 -E (errtrace) 选项 — V37.9.58-hotfix4 教训",
            )

    def test_v37_9_61_llm_task_scripts_have_fatal_handler(self):
        """V37.9.61: 3 个 LLM-task 脚本必须有 fatal handler.

        揭露原因: V37.9.60-hotfix3 抓到 kb_deep_dive 5/8-5/12 5 天 silent abort,
        scope 收敛为 framework 化预防 — 同款 set -euo + 缺 trap ERR + 推 [SYSTEM_ALERT]
        的 LLM-task 脚本都加 trap ERR + handler.

        V37.9.63 alternation: 接受 (A) inline _<script>_fatal_handler (legacy V37.9.61)
        OR (B) source cron_monitor_fatal_handler.sh + trap '_cron_monitor_fatal_handler' (V37.9.63 helper 模式).
        """
        expected_handlers = {
            "kb_deep_dive.sh": "_kb_deep_dive_fatal_handler",
            "kb_evening.sh": "_kb_evening_fatal_handler",
            "kb_review.sh": "_kb_review_fatal_handler",
        }
        for script, legacy_handler in expected_handlers.items():
            path = os.path.join(REPO_ROOT, script)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # alternation: 必须满足任一模式
            has_inline_trap = f"trap '{legacy_handler} $LINENO' ERR" in content
            has_helper_trap = (
                "source" in content
                and "cron_monitor_fatal_handler.sh" in content
                and "trap '_cron_monitor_fatal_handler $LINENO' ERR" in content
            )
            self.assertTrue(
                has_inline_trap or has_helper_trap,
                f"V37.9.61: {script} 必须有 (A) inline trap '{legacy_handler}' "
                f"OR (B) V37.9.63 helper 模式 (source helper + trap _cron_monitor_fatal_handler)",
            )

    def test_v37_9_61_llm_task_scripts_have_v37_9_61_marker(self):
        """V37.9.61 marker 必须在 3 个 LLM-task 脚本中可追溯"""
        for script in ["kb_deep_dive.sh", "kb_evening.sh", "kb_review.sh"]:
            path = os.path.join(REPO_ROOT, script)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn(
                "V37.9.61", content,
                f"V37.9.61: {script} 必须含 V37.9.61 marker (framework 化 LLM-task 类血案 lineage)",
            )

    def test_kb_dream_not_in_governed_scripts(self):
        """kb_dream 不应在 SCRIPTS_REQUIRING_ERR_TRAP (仅 set -o pipefail 无 -e)

        kb_dream.sh 行为不同于其他 LLM-task 脚本: 不启用 set -e, 命令失败不会杀脚本.
        silent abort 风险不存在 (从 set -e 角度), 不需 trap ERR.
        V37.9.61 严格 #28 最小修复原则 — 不无端加防御.
        """
        self.assertNotIn(
            "kb_dream.sh", scanner.SCRIPTS_REQUIRING_ERR_TRAP,
            "kb_dream.sh 仅 set -o pipefail, 不需 trap ERR (V37.9.61 严格 scope)",
        )

    def test_governance_audit_grep_head_pipes_have_or_true(self):
        """V37.9.60-hotfix 反向守卫: grep | head subshell pipe 必须 || true 容错

        V37.9.58-hotfix4 同款 bash quirk: set -eE + grep no-match exit 1 +
        pipefail 让 subshell exit 1 + ERR trap 触发 false-positive FATAL.
        V37.9.60 实测真激活 (governance_audit Mac Mini 上 line 76 推 [SYSTEM_ALERT] 两次).
        防回归: 任何 `=$(... | grep ... | head ...)` 模式必须 || true 兜底.
        """
        gov_path = os.path.join(REPO_ROOT, "governance_audit_cron.sh")
        with open(gov_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 三个 grep | head pipe 必须都有 || true (line 75/76/77 模式)
        for keyword in ["GOV_SUMMARY", "GOV_VIOLATIONS", "GOV_WARNINGS"]:
            # 找该变量赋值行
            for line in content.split("\n"):
                if line.startswith(f"{keyword}=$(echo") and "grep" in line:
                    self.assertIn(
                        "|| true", line,
                        f"V37.9.60-hotfix 反向守卫: governance_audit_cron.sh "
                        f"{keyword} 赋值的 grep | head pipe 必须 || true 兜底, "
                        f"否则 set -eE + grep no-match → ERR trap 触发 false-positive FATAL. "
                        f"line: {line!r}"
                    )
                    break

    def test_auto_deploy_grep_pipes_have_or_true(self):
        """V37.9.60-hotfix: auto_deploy 同款反模式守卫 (FAIL_LINES / CRON_COUNT)"""
        ad_path = os.path.join(REPO_ROOT, "auto_deploy.sh")
        with open(ad_path, "r", encoding="utf-8") as f:
            content = f.read()
        # FAIL_LINES line 519: grep "❌" | head -10 必须 || true
        for line in content.split("\n"):
            if "FAIL_LINES=" in line and "grep" in line and "head" in line:
                self.assertIn(
                    "|| true", line,
                    f"V37.9.60-hotfix: auto_deploy.sh FAIL_LINES grep | head "
                    f"必须 || true. line: {line!r}"
                )
                break
        # CRON_COUNT line 456: crontab -l | grep | wc | tr 必须 || true
        for line in content.split("\n"):
            if line.lstrip().startswith("CRON_COUNT=$(crontab -l"):
                self.assertIn(
                    "|| true", line,
                    f"V37.9.60-hotfix: auto_deploy.sh CRON_COUNT pipeline "
                    f"必须 || true. line: {line!r}"
                )
                break

    def test_daily_ops_grep_v_pipes_have_or_true(self):
        """V37.9.60-hotfix: daily_ops_report grep -v 全匹配时 exit 1 同款守卫"""
        dop_path = os.path.join(REPO_ROOT, "daily_ops_report.sh")
        with open(dop_path, "r", encoding="utf-8") as f:
            content = f.read()
        for line in content.split("\n"):
            if "REPORT=$(echo" in line and "grep -v" in line:
                self.assertIn(
                    "|| true", line,
                    f"V37.9.60-hotfix: daily_ops_report.sh grep -v subshell pipe "
                    f"必须 || true. line: {line!r}"
                )

    def test_v37_9_66_hotfix_watchdog_slo_uses_if_not_andor(self):
        """V37.9.66-hotfix 反向守卫: watchdog SLO 检查必须用 if-then-else 不用 cmd && X || Y.

        Mac Mini 5/13 16:30 实测触发 line 721 abort exit=2: bash quirk
        `cmd && X || Y` + set -eE + ERR trap, 即使 set -e 在 && 上下文豁免不杀脚本,
        ERR trap 仍触发产 false-positive FATAL alert. V37.9.66-hotfix 改 if-then-else
        (bash 文档明确 if condition + set -e + ERR trap 豁免).

        防回归: SLO 检查段必须用 'if SLO_ALERT=$(...)' 不是 'SLO_ALERT=$(...) && SLO_RC=0 || SLO_RC=$?'.
        """
        wd_path = os.path.join(REPO_ROOT, "job_watchdog.sh")
        with open(wd_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 必须出现 if-then-else 形式
        self.assertIn(
            "if SLO_ALERT=$(python3", content,
            "V37.9.66-hotfix: watchdog SLO 检查必须用 'if SLO_ALERT=$(python3 ...)' 形式 "
            "(bash 文档豁免 set -e + ERR trap), 不得用 'cmd && X || Y' 反模式"
        )
        # 同时禁 buggy pattern 回归
        self.assertNotIn(
            "SLO_ALERT=$(python3 \"$SLO_SCRIPT\" --alert 2>/dev/null) && SLO_RC=0 || SLO_RC=",
            content,
            "V37.9.66-hotfix 反向守卫: watchdog 禁回退到 'cmd && X || Y' 反模式"
        )

    def test_v37_9_66_hotfix_auto_deploy_preflight_uses_if_not_andor(self):
        """V37.9.66-hotfix: auto_deploy PREFLIGHT_OUT 同款反模式横向修齐.

        auto_deploy.sh 是 governed cron (V37.9.60 装 trap ERR), 同款 cmd && X || Y
        pattern 高频 2min cron 触发, 必须同步修.
        """
        ad_path = os.path.join(REPO_ROOT, "auto_deploy.sh")
        with open(ad_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(
            "if PREFLIGHT_OUT=$(SKIP_PUSH_TEST=1", content,
            "V37.9.66-hotfix: auto_deploy PREFLIGHT_OUT 必须用 if-then-else 形式"
        )
        self.assertNotIn(
            "PREFLIGHT_OUT=$(SKIP_PUSH_TEST=1 bash \"$PREFLIGHT\" --full 2>&1) && PREFLIGHT_RC=0 ||",
            content,
            "V37.9.66-hotfix 反向守卫: auto_deploy 禁回退到 'cmd && X || Y' 反模式"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

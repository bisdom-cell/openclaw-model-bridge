#!/usr/bin/env python3
"""
test_cron_monitor_fatal_handler.py - V37.9.63 MR-8 抽公共 helper 守卫

测试 cron_monitor_fatal_handler.sh 公共 helper 的契约 + 7 个 governed scripts 集成正确性.

测试矩阵 (7 类):
  1. TestHelperSyntaxAndStructure - helper 自身语法 + sentinel + 函数定义
  2. TestHelperCanonicalCli - canonical CLI 风格 (修 V37.9.60 6 个 fatal handler bug)
  3. TestHelperRuntimeBehavior - 三层 FAIL-OPEN 真生效 (stderr / 本地 log / openclaw 直发)
  4. TestSevenScriptsIntegration - 7 个 governed scripts source helper + 4 变量 + trap
  5. TestInlineHandlersRemoved - 7 个 inline _<script>_fatal_handler 函数已删除
  6. TestReverseVerificationSabotage - sabotage 守卫真有效 (sabotage→fail / 还原→pass)
  7. TestV37963SourceLevelMarkers - V37.9.63 marker + helper 在 FILE_MAP + 反 inline 反模式

反向验证已确认守卫真有效:
  - sabotage helper 删 _cron_monitor_fatal_handler → 7 个脚本 trap 死链 (subprocess 探测)
  - sabotage helper 改回 --channel-id/--content → canonical CLI 守卫立即抓
  - sabotage 某 script 删 source helper 行 → 该 script 集成守卫立即抓
"""

import os
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent
HELPER_PATH = REPO_ROOT / "cron_monitor_fatal_handler.sh"

# 7 个 governed scripts (V37.9.60 4 个 cron 类 + V37.9.61 3 个 LLM-task 类)
GOVERNED_SCRIPTS = [
    "job_watchdog.sh",
    "governance_audit_cron.sh",
    "daily_ops_report.sh",
    "auto_deploy.sh",
    "kb_deep_dive.sh",
    "kb_evening.sh",
    "kb_review.sh",
]


# ════════════════════════════════════════════════════════════════════
# 1. Helper 自身语法 + sentinel + 函数定义
# ════════════════════════════════════════════════════════════════════
class TestHelperSyntaxAndStructure(unittest.TestCase):
    def test_helper_file_exists(self):
        self.assertTrue(HELPER_PATH.exists(), f"helper missing: {HELPER_PATH}")

    def test_helper_syntax_ok(self):
        result = subprocess.run(
            ["bash", "-n", str(HELPER_PATH)],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"bash -n failed: {result.stderr}")

    def test_helper_defines_cron_monitor_fatal_handler_function(self):
        text = HELPER_PATH.read_text()
        self.assertIn("_cron_monitor_fatal_handler()", text,
                      "helper must define _cron_monitor_fatal_handler() function")

    def test_helper_sentinel_marker(self):
        text = HELPER_PATH.read_text()
        # sentinel 让 caller 可以验证 source 成功
        self.assertIn('CRON_MONITOR_FATAL_HANDLER_LOADED="V37.9.63"', text,
                      "helper must set CRON_MONITOR_FATAL_HANDLER_LOADED sentinel")

    def test_helper_reads_four_caller_variables(self):
        """helper 必须从 caller 读 CRON_FATAL_LABEL/LOG/BASH_X/REASON 4 个变量."""
        text = HELPER_PATH.read_text()
        for var in ["CRON_FATAL_LABEL", "CRON_FATAL_LOG", "CRON_FATAL_BASH_X", "CRON_FATAL_REASON"]:
            self.assertIn(f"${{{var}", text,
                          f"helper must read ${var} (caller-provided variable)")

    def test_helper_has_three_layer_fail_open(self):
        """三层 FAIL-OPEN: stderr / 本地告警 log / notify→openclaw 直发"""
        text = HELPER_PATH.read_text()
        # Layer 1: stderr
        self.assertIn(">&2", text, "Layer 1: stderr 写入")
        # Layer 2: 本地告警文件
        self.assertIn(".openclaw_alerts.log", text, "Layer 2: 本地告警文件路径")
        # Layer 3: notify→openclaw chain
        self.assertIn("notify ", text, "Layer 3 first: notify command")
        self.assertIn("openclaw_bin", text.lower(), "Layer 3 fallback: openclaw_bin")

    def test_helper_v37_9_63_marker(self):
        text = HELPER_PATH.read_text()
        self.assertIn("V37.9.63", text, "helper must carry V37.9.63 marker")
        self.assertIn("MR-8", text, "helper must reference MR-8 (single-source-of-truth)")
        self.assertIn("MR-19", text, "helper must reference MR-19 (monitor-must-self-alarm)")


# ════════════════════════════════════════════════════════════════════
# 2. Canonical CLI 风格 (修 V37.9.60 6 个 fatal handler CLI bug)
# ════════════════════════════════════════════════════════════════════
class TestHelperCanonicalCli(unittest.TestCase):
    """canonical CLI 风格守卫 (notify.sh / auto_deploy 主告警 / watchdog 主告警同款).

    V37.9.60 我写的 6 个 fatal_handler 第二层 FAIL-OPEN 用了:
      --channel-id X --content Y   ← 错的 (不是 notify.sh canonical 风格)
    应该是:
      --target X --message Y --json   ← 对的 (canonical)

    helper 必须用 canonical 形式, 不得保留 V37.9.60 的 buggy 形式.
    """

    def test_helper_uses_canonical_cli_target_message_json(self):
        text = HELPER_PATH.read_text()
        # canonical 三参数 (这是 notify.sh / health_check / diagnose / auto_deploy 主告警同款风格)
        self.assertIn("--target", text, "canonical CLI must use --target")
        self.assertIn("--message", text, "canonical CLI must use --message")
        self.assertIn("--json", text, "canonical CLI must use --json")

    def test_helper_forbids_v37_9_60_buggy_cli_pattern(self):
        """禁止 V37.9.60 的 buggy --channel-id + --content 形式 (反例守卫)."""
        text = HELPER_PATH.read_text()
        # 跳过注释行 (注释里说明了 V37.9.60 的 bug 才会出现这些字面量)
        non_comment_lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            non_comment_lines.append(line)
        non_comment_code = "\n".join(non_comment_lines)
        # 真代码中绝不能出现 buggy 形式
        self.assertNotIn("--channel-id", non_comment_code,
                         "buggy V37.9.60 --channel-id 形式禁止出现在 helper 代码")
        self.assertNotIn("--content ", non_comment_code,
                         "buggy V37.9.60 --content 形式禁止出现在 helper 代码")


# ════════════════════════════════════════════════════════════════════
# 3. Helper 运行时行为 - 三层 FAIL-OPEN 真生效
# ════════════════════════════════════════════════════════════════════
class TestHelperRuntimeBehavior(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="helper_test_")
        # 隔离 ~/.openclaw_alerts.log: 用 HOME=tmpdir 让写入不污染真实 home
        self.fake_home = self.tmpdir
        self.alert_log = Path(self.fake_home) / ".openclaw_alerts.log"
        # V37.9.157: stub openclaw — handler Layer 3 (notify→openclaw 直发) 绝不调真 4.27 CLI.
        # 血案: 4.27 冷调用每次 ~10s, 多次 helper 调用 → test 77s > governance check 60s timeout
        # → INV-CRON-MONITOR-001 💥; 且真 openclaw 会往用户 WhatsApp 发真 [SYSTEM_ALERT]
        # (test-pollutes-production, MR-9/MR-23). instant stub 让 test 秒过 + 零真实发送 + env-independent.
        self.stub_bin = os.path.join(self.tmpdir, "bin")
        os.makedirs(self.stub_bin, exist_ok=True)
        stub = os.path.join(self.stub_bin, "openclaw")
        with open(stub, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(stub, 0o755)
        self.stub_openclaw = stub

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _stub_env(self, openclaw_available=True):
        """V37.9.157: 受控 env — 不继承 Mac Mini 真实环境 (真 openclaw / 真推送目标).
        openclaw_available=True → handler Layer 3 调 instant stub (秒过, 零真实发送);
        False → 指向不存在路径, 测 FAIL-OPEN-when-tools-missing (command not found 秒败, 非 10s 冷超时)."""
        oc = self.stub_openclaw if openclaw_available else os.path.join(self.tmpdir, "no_such_openclaw")
        path = (self.stub_bin + ":" if openclaw_available else "") + "/usr/bin:/bin"
        return {
            "HOME": self.fake_home,
            "PATH": path,  # 不含 /opt/homebrew/bin → 真 4.27 openclaw 绝不可达
            "OPENCLAW_BIN": oc,  # 覆盖 ${OPENCLAW_BIN:-${OPENCLAW:-/opt/homebrew/bin/openclaw}} 三档 fallback
            "OPENCLAW": oc,
        }

    def _run_helper_in_subshell(self, label="test_x", line_no="42", openclaw_available=True):
        """在 subshell 中 source helper + 调用 handler. 返回 (stdout, stderr, alert_log_content, rc)."""
        script = f"""
set +e
source {HELPER_PATH}
CRON_FATAL_LABEL="{label}"
CRON_FATAL_LOG="/tmp/x.log"
CRON_FATAL_BASH_X="bash -x /tmp/x.sh"
CRON_FATAL_REASON="test reason V37.9.63"
_cron_monitor_fatal_handler {line_no}
"""
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True,
            env=self._stub_env(openclaw_available), timeout=20,
        )
        log_content = self.alert_log.read_text() if self.alert_log.exists() else ""
        return result.stdout, result.stderr, log_content, result.returncode

    def test_layer1_stderr_written(self):
        stdout, stderr, log, rc = self._run_helper_in_subshell(label="my_script", line_no="123")
        self.assertIn("my_script", stderr, "stderr must contain caller label")
        self.assertIn("FATAL", stderr, "stderr must contain FATAL")
        self.assertIn("line=123", stderr, "stderr must contain explicit line number")

    def test_layer2_local_alert_log_written(self):
        stdout, stderr, log, rc = self._run_helper_in_subshell(label="my_script", line_no="55")
        self.assertIn("my_script", log, "alert log must contain caller label")
        self.assertIn("FATAL abort", log, "alert log must contain FATAL abort marker")
        self.assertIn("line=55", log, "alert log must contain explicit line")

    def test_handler_never_raises_even_if_notify_and_openclaw_missing(self):
        """三层 FAIL-OPEN: 即使 notify + openclaw 都不可用, handler 自身也不 crash."""
        stdout, stderr, log, rc = self._run_helper_in_subshell(label="dev_no_tools", openclaw_available=False)
        # rc 应该是 0 (handler 自身不冒泡), 不管 notify/openclaw 是否可用
        self.assertEqual(rc, 0, f"handler must not crash even if notify+openclaw missing; rc={rc}")

    def test_caller_variables_propagate_to_fatal_msg(self):
        """4 个变量 (LABEL/LOG/BASH_X/REASON) 应该都进 fatal_msg + stderr/log."""
        stdout, stderr, log, rc = self._run_helper_in_subshell(label="my_label_xyz", line_no="99")
        # stderr 含 label + line
        self.assertIn("my_label_xyz", stderr)
        # log 含 label
        self.assertIn("my_label_xyz", log)

    def test_helper_works_with_set_e(self):
        """关键: 在 caller set -e 模式下, handler 调用后 caller 不被进一步 abort."""
        script = f"""
set -eEo pipefail
source {HELPER_PATH}
CRON_FATAL_LABEL="strict"
CRON_FATAL_LOG="/tmp/x.log"
CRON_FATAL_BASH_X="bash -x"
CRON_FATAL_REASON="strict reason"
_cron_monitor_fatal_handler 1
echo "POST_HANDLER_REACHED"
"""
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True,
            env=self._stub_env(), timeout=20,
        )
        # POST_HANDLER_REACHED 必须出现 (handler 不杀 caller)
        self.assertIn("POST_HANDLER_REACHED", result.stdout,
                      "handler 必须不让 caller crash (set -e 下也要继续)")


# ════════════════════════════════════════════════════════════════════
# 4. 7 个 governed scripts 集成 (source helper + 4 vars + trap)
# ════════════════════════════════════════════════════════════════════
class TestSevenScriptsIntegration(unittest.TestCase):

    def test_all_7_scripts_source_helper(self):
        """每个 governed script 必须有真实 source 命令调 helper (不只是注释提及字面量)."""
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            # 找出所有 source 命令调 helper 的非注释行
            source_lines = []
            for line in text.split("\n"):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ("source" in stripped
                        and "cron_monitor_fatal_handler.sh" in stripped):
                    source_lines.append(line)
            self.assertGreater(len(source_lines), 0,
                               f"{fn}: 必须有真实 source 命令调 cron_monitor_fatal_handler.sh "
                               f"(注释提及不算, 必须是可执行 source 命令)")

    def test_all_7_scripts_set_cron_fatal_label(self):
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            self.assertRegex(text, r'CRON_FATAL_LABEL="[a-z_]+"',
                             f"{fn}: 必须设置 CRON_FATAL_LABEL=\"...\"")

    def test_all_7_scripts_set_cron_fatal_log(self):
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            self.assertIn("CRON_FATAL_LOG=", text,
                          f"{fn}: 必须设置 CRON_FATAL_LOG=...")

    def test_all_7_scripts_set_cron_fatal_bash_x(self):
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            self.assertIn("CRON_FATAL_BASH_X=", text,
                          f"{fn}: 必须设置 CRON_FATAL_BASH_X=...")

    def test_all_7_scripts_set_cron_fatal_reason(self):
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            self.assertIn("CRON_FATAL_REASON=", text,
                          f"{fn}: 必须设置 CRON_FATAL_REASON=\"...\"")

    def test_all_7_scripts_register_trap_via_helper(self):
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            # 必须 trap '_cron_monitor_fatal_handler $LINENO' ERR
            self.assertIn("_cron_monitor_fatal_handler", text,
                          f"{fn}: 必须 trap _cron_monitor_fatal_handler")
            self.assertRegex(text, r"trap\s+'_cron_monitor_fatal_handler\s+\$LINENO'\s+ERR",
                             f"{fn}: 必须 'trap _cron_monitor_fatal_handler $LINENO ERR'")

    def test_each_script_has_unique_label(self):
        """每个 script 必须有独立 LABEL (LABEL collision 是 MR-8 反例的 mark)."""
        labels = set()
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            m = re.search(r'CRON_FATAL_LABEL="([a-z_]+)"', text)
            self.assertIsNotNone(m, f"{fn}: CRON_FATAL_LABEL not found")
            label = m.group(1)
            self.assertNotIn(label, labels, f"label '{label}' 重复 - 跨脚本必须唯一")
            labels.add(label)
        self.assertEqual(len(labels), len(GOVERNED_SCRIPTS),
                         "7 个 labels 必须全部唯一")


# ════════════════════════════════════════════════════════════════════
# 5. 7 个 inline _<script>_fatal_handler 函数已删除
# ════════════════════════════════════════════════════════════════════
class TestInlineHandlersRemoved(unittest.TestCase):
    """V37.9.60+V37.9.61 inline copy-paste 必须全部消除 (MR-8 兑现)."""

    def test_no_inline_fatal_handler_function_definitions(self):
        # 7 个被抽出的 inline handler 函数名 (不含 watchdog_exit_handler 是不同函数, 保留)
        forbidden_inline_handlers = [
            "_watchdog_fatal_handler",
            "_governance_audit_fatal_handler",
            "_daily_ops_fatal_handler",
            "_auto_deploy_fatal_handler",
            "_kb_deep_dive_fatal_handler",
            "_kb_evening_fatal_handler",
            "_kb_review_fatal_handler",
        ]
        for fn in GOVERNED_SCRIPTS:
            script_path = REPO_ROOT / fn
            text = script_path.read_text()
            for handler_name in forbidden_inline_handlers:
                # 函数定义模式: "_handler_name() {"
                pattern = rf"^{re.escape(handler_name)}\(\)\s*\{{"
                m = re.search(pattern, text, re.MULTILINE)
                self.assertIsNone(m,
                                  f"{fn}: 不得保留 inline {handler_name}() {{ 函数定义 (MR-8 反模式, 已抽到 helper)")

    def test_watchdog_keeps_exit_handler(self):
        """watchdog 的 _watchdog_exit_handler (canary heartbeat) 必须保留 - 它不是 fatal_handler."""
        text = (REPO_ROOT / "job_watchdog.sh").read_text()
        # 使用 re.MULTILINE 让 ^ 匹配每行开头 (默认 re.search 只匹配字符串开头)
        self.assertRegex(text, r"(?m)^_watchdog_exit_handler\(\)\s*\{",
                         "watchdog 保留 _watchdog_exit_handler() (V37.9.58-hotfix3 canary heartbeat)")
        self.assertIn("trap '_watchdog_exit_handler' EXIT", text,
                      "watchdog 保留 EXIT trap (rmdir LOCK + canary)")


# ════════════════════════════════════════════════════════════════════
# 6. 反向验证 (sabotage 守卫真有效)
# ════════════════════════════════════════════════════════════════════
class TestReverseVerificationSabotage(unittest.TestCase):
    """反向验证: sabotage helper / sabotage script 应该让对应守卫立即 fail."""

    def setUp(self):
        # 备份 helper + 1 个 script 用于 sabotage 测试
        self.tmpdir = tempfile.mkdtemp(prefix="sabotage_")
        self.helper_backup = Path(self.tmpdir) / "helper.sh.bak"
        shutil.copy(HELPER_PATH, self.helper_backup)
        self.script_backup = Path(self.tmpdir) / "watchdog.sh.bak"
        shutil.copy(REPO_ROOT / "job_watchdog.sh", self.script_backup)

    def tearDown(self):
        # 还原
        shutil.copy(self.helper_backup, HELPER_PATH)
        shutil.copy(self.script_backup, REPO_ROOT / "job_watchdog.sh")
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sabotage_helper_remove_function_breaks_handler(self):
        """sabotage: 删 helper 的 _cron_monitor_fatal_handler 函数 → handler 调用失败."""
        # 把 helper 改成空文件
        HELPER_PATH.write_text("# sabotaged\n")
        # 跑一个 caller script source 它
        script = f"""
source {HELPER_PATH} 2>/dev/null
if declare -f _cron_monitor_fatal_handler >/dev/null 2>&1; then
    echo "HAS_HANDLER"
else
    echo "NO_HANDLER"
fi
"""
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertIn("NO_HANDLER", result.stdout,
                      "sabotage helper 后 caller 应该看不到 _cron_monitor_fatal_handler 函数")

    def test_sabotage_helper_revert_to_buggy_cli_caught(self):
        """sabotage: 把 helper 的 canonical CLI 改回 V37.9.60 buggy 形式 → 反 buggy CLI 守卫立即 fail.

        关键: 反 buggy 守卫 (test_helper_forbids_v37_9_60_buggy_cli_pattern) 直接检测代码中
        不能出现 --channel-id / --content. sabotage 引入这些字面量必须被抓.
        """
        text = HELPER_PATH.read_text()
        # 替换 canonical → buggy (引入禁止的字面量)
        sabotaged = text.replace("--target ", "--channel-id ")
        sabotaged = sabotaged.replace("--message ", "--content ")
        sabotaged = sabotaged.replace("--json", "")
        HELPER_PATH.write_text(sabotaged)
        # 反 buggy 守卫应抓 (--channel-id 出现在非注释代码中)
        test = TestHelperCanonicalCli()
        with self.assertRaises(AssertionError,
                               msg="sabotage 引入 buggy CLI 后, 反 buggy 守卫必须抓到"):
            test.test_helper_forbids_v37_9_60_buggy_cli_pattern()

    def test_sabotage_script_remove_source_helper_caught(self):
        """sabotage: 删 watchdog 的 source helper 行 → integration test 立即抓.

        注意: 注释中也会提及 helper 文件名 (历史引用), sabotage 检查不是看字符串完全消失,
        而是看 source 命令是否真被删. integration 守卫扫的也是 source 命令.
        """
        watchdog_path = REPO_ROOT / "job_watchdog.sh"
        text = watchdog_path.read_text()
        # 删掉所有 source ...helper.sh 的 source 命令行 (注释行的提及不动)
        # 关键 source 行模式: 行内含 'source ' + 'cron_monitor_fatal_handler.sh'
        sabotaged_lines = []
        sabotaged_count = 0
        for line in text.split("\n"):
            stripped = line.strip()
            # 跳过注释 + 真删 source 命令行
            if (not stripped.startswith("#")
                    and "source" in stripped
                    and "cron_monitor_fatal_handler.sh" in stripped):
                sabotaged_lines.append("# sabotaged: source removed")
                sabotaged_count += 1
            else:
                sabotaged_lines.append(line)
        sabotaged = "\n".join(sabotaged_lines)
        # 验证 sabotage 真生效 (test 自身合理性)
        self.assertGreater(sabotaged_count, 0, "test 自身: 至少应找到 1 行 source helper 命令并替换")
        # 现在 source 命令应该消失了 (注释中的字面量仍可能存在, 但不算 source)
        for line in sabotaged.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertFalse("source" in stripped and "cron_monitor_fatal_handler.sh" in stripped,
                             f"sabotage 必须真的移除 source 命令: 残留行={stripped!r}")
        watchdog_path.write_text(sabotaged)
        # integration test 应抓 (其守卫用 assertIn "cron_monitor_fatal_handler.sh"
        # 但只看是否出现, 不区分 source vs comment. 我们改为更精准的 source 命令守卫)
        test = TestSevenScriptsIntegration()
        with self.assertRaises(AssertionError,
                               msg="sabotage 删 source helper 命令后, integration 守卫必须抓到"):
            test.test_all_7_scripts_source_helper()


# ════════════════════════════════════════════════════════════════════
# 7. V37.9.63 marker + FILE_MAP + 反 inline 模式
# ════════════════════════════════════════════════════════════════════
class TestV37963SourceLevelMarkers(unittest.TestCase):
    def test_helper_in_auto_deploy_file_map(self):
        """helper 必须在 auto_deploy.sh FILE_MAP 中部署到 Mac Mini."""
        auto_deploy = (REPO_ROOT / "auto_deploy.sh").read_text()
        self.assertIn("cron_monitor_fatal_handler.sh", auto_deploy,
                      "auto_deploy.sh FILE_MAP 必须包含 cron_monitor_fatal_handler.sh 部署")

    def test_each_script_has_v37_9_63_marker(self):
        for fn in GOVERNED_SCRIPTS:
            text = (REPO_ROOT / fn).read_text()
            self.assertIn("V37.9.63", text,
                          f"{fn}: 必须含 V37.9.63 marker (helper 抽公共版本锚点)")

    def test_helper_references_mr_8(self):
        text = HELPER_PATH.read_text()
        self.assertIn("MR-8", text, "helper 必须引用 MR-8 元规则 (copy-paste-is-a-bug-class)")

    def test_governed_scripts_count_locked(self):
        """7 governed scripts 是设计契约, 防止未来漂移 (新增/减少都要更新 GOVERNED_SCRIPTS list)."""
        self.assertEqual(len(GOVERNED_SCRIPTS), 7,
                         "GOVERNED_SCRIPTS 必须正好 7 个 (V37.9.60 4 cron + V37.9.61 3 LLM-task)")


class TestV37_9_214_GovAuditAlertSurfacesErrors(unittest.TestCase):
    """V37.9.214: governance_audit_cron alert must surface BOTH ❌ (fail) and
    💥 (error) checks. A --full check that ERRORS (subprocess timeout under
    load/cold-call → TimeoutExpired → 💥) was invisible when GOV_VIOLATIONS
    grepped only ❌ → alert fired with an empty 不变式违反 section (2026-07-02:
    "THAT it failed but not WHICH check"). Mirrors V37.9.213 F1."""

    @classmethod
    def setUpClass(cls):
        with open(REPO_ROOT / "governance_audit_cron.sh", encoding="utf-8") as f:
            cls.src = f.read()

    def test_gov_violations_greps_both_fail_and_error(self):
        # GOV_VIOLATIONS must grep ❌ AND 💥 (not ❌ only)
        m = re.search(r'GOV_VIOLATIONS=\$\(echo "\$GOV_OUTPUT" \| grep (\S+) "([^"]*)"',
                      self.src)
        self.assertIsNotNone(m, "GOV_VIOLATIONS grep line must exist")
        # the -E pattern must contain both symbols
        self.assertIn("💥", m.group(2),
                      "GOV_VIOLATIONS must grep 💥 (error-class), not ❌ only")
        self.assertIn("❌", m.group(2), "GOV_VIOLATIONS must still grep ❌ (fail)")

    def test_old_fail_only_pattern_retired(self):
        # the exact old anti-pattern (grep "❌" alone for GOV_VIOLATIONS) must be gone
        self.assertNotRegex(
            self.src, r'GOV_VIOLATIONS=\$\(echo "\$GOV_OUTPUT" \| grep "❌" \|',
            "V37.9.214: old ❌-only GOV_VIOLATIONS grep must be retired")

    def test_alert_label_mentions_error(self):
        # alert section label must reflect that 💥 errors are included
        self.assertIn("不变式违反 / 检查出错", self.src,
                      "alert label must be updated to include 检查出错 (error)")
        self.assertRegex(self.src, r"不变式违反 / 检查出错.*💥",
                         "alert label must mention 💥 error")

    def test_v37_9_214_marker(self):
        self.assertIn("V37.9.214", self.src)


class TestV37_9_214_ReviewCheckLoadHardening(unittest.TestCase):
    """V37.9.214 root-cause: INV-REVIEW-001 runtime check (真跑 kb_review.sh
    subprocess) 在重负载下被 CPU 饿死超 timeout=45 → TimeoutExpired → 💥
    (2026-07-02 6× 并行确定性复现; sleep 400 cold 单跑过 → 负载非 cold-call).
    修: timeout 45→90 + 捕获 TimeoutExpired 视为 inconclusive (超时不是这个
    check 守的 JSONDecodeError 回归的证据). Lineage V37.9.145/157 (cold-call)
    → V37.9.214 (load-timeout)."""

    @classmethod
    def setUpClass(cls):
        with open(REPO_ROOT / "ontology" / "governance_ontology.yaml",
                  encoding="utf-8") as f:
            cls.src = f.read()
        # isolate the INV-REVIEW-001 runtime E2E check block
        start = cls.src.find("V37.5.1 runtime: 真实 subprocess 执行 kb_review.sh")
        # slice to the finally cleanup (end of this check's code block)
        end = cls.src.find("shutil.rmtree(tmp, ignore_errors=True)", start)
        cls.block = cls.src[start:end + 60] if start >= 0 and end > start else ""

    def test_review_check_block_found(self):
        self.assertTrue(self.block, "INV-REVIEW-001 runtime check block must exist")

    def test_timeout_bumped_to_90(self):
        self.assertIn("timeout=90", self.block,
                      "V37.9.214: kb_review.sh subprocess timeout must be 90 (load-tolerant)")

    def test_old_timeout_45_retired(self):
        # the old tight 45s timeout must be gone from this check block
        self.assertNotIn("timeout=45", self.block,
                         "V37.9.214: old timeout=45 must be retired (load-fragile)")

    def test_catches_timeout_expired_as_inconclusive(self):
        self.assertIn("subprocess.TimeoutExpired", self.block,
                      "V37.9.214: must catch TimeoutExpired (load timeout ≠ regression)")
        self.assertIn("INCONCLUSIVE", self.block,
                      "V37.9.214: timeout must be treated as inconclusive, not 💥")
        # assertions must be gated on the subprocess having completed
        self.assertIn("if result is not None:", self.block,
                      "V37.9.214: regression assertions must be gated on result (skip on timeout)")


class TestV37_9_214_B2NoErrtraceReEnable(unittest.TestCase):
    """V37.9.214 B2 root-fix: governance_audit_cron.sh must NOT re-enable
    `set -E` after the audit $() calls. In bash 3.2, each `set -E` errtrace
    re-enable was a landmine firing false FATAL "治理审计自身死亡" on the HANDLED
    GOV_RC=1 path (3 recurrences: V37.9.105 line 64 / line 100 / 2026-07-02
    line 101 = whack-a-mole; dev bash 5.x can't reproduce). Root-fix (日落法):
    single `set +E` after setup, errtrace OFF to script end. errexit (set -e)
    + top `set -eEuo` declaration preserved (MR-19 core + governance check)."""

    @classmethod
    def setUpClass(cls):
        with open(REPO_ROOT / "governance_audit_cron.sh", encoding="utf-8") as f:
            cls.src = f.read()

    def test_top_declares_set_eEuo(self):
        # MR-19 contract + governance check need the top declaration intact
        self.assertIn("set -eEuo pipefail", self.src,
                      "top set -eEuo pipefail declaration must remain")

    def test_no_bare_set_dash_E_reenable(self):
        # the landmine: a bare `set -E` re-enable line (NOT the top -eEuo)
        reenables = re.findall(r'(?m)^[ \t]*set -E[ \t]*(?:#.*)?$', self.src)
        self.assertEqual(reenables, [],
            f"V37.9.214: no `set -E` re-enable allowed (bash 3.2 landmine); found {reenables}")

    def test_single_set_plus_E_region(self):
        plus = re.findall(r'(?m)^[ \t]*set \+E\b', self.src)
        self.assertEqual(len(plus), 1,
            f"V37.9.214: exactly one `set +E` region expected, found {len(plus)}")

    def test_v37_9_214_b2_marker(self):
        self.assertIn("V37.9.214 日落法根治", self.src)


if __name__ == "__main__":
    unittest.main()

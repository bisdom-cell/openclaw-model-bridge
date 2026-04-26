"""V37.9.18 — kb_deep_dive cron 未注册血案修复回归测试

血案: V37.9.16 上线 kb_deep_dive 后 4/24+4/25 两次预期触发静默不跑，因为:
  1. preflight_check.sh:63 只 grep "间隔漂移"，漏 "未找到" warning → 假绿
  2. crontab_safe.sh:cmd_add 不检查 crontab 退出码，count 用 < 比较 → 谎报 ✅

本测试覆盖:
  TestPreflightDualWarningCheck — 源码层守卫 preflight 同时检查两种 warning
  TestCrontabSafeStrictExitCheck — 源码层守卫 crontab_safe 退出码 + 严格相等
  TestCrontabSafeRejectedInputBehavior — 行为层用 fake crontab shim 验证拒绝场景
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


class TestPreflightDualWarningCheck(unittest.TestCase):
    """源码层守卫: preflight_check.sh 同时检查 间隔漂移 + 未找到 两种 warning"""

    @classmethod
    def setUpClass(cls):
        cls.src = (REPO_ROOT / "preflight_check.sh").read_text(encoding="utf-8")

    def test_v37_9_18_marker_present(self):
        """V37.9.18 修复注释必须存在，禁止未来无意识回退"""
        self.assertIn("V37.9.18", self.src,
            "preflight_check.sh 必须保留 V37.9.18 修复注释作为回归守卫")
        self.assertIn("kb_deep_dive", self.src,
            "preflight_check.sh 必须保留血案引用便于追溯")

    def test_grep_interval_drift_pattern_kept(self):
        """V36.2 原间隔漂移检查必须保留"""
        self.assertIn('grep -q "间隔漂移"', self.src,
            "preflight 必须保留 V36.2 间隔漂移检查不得回退")

    def test_grep_missing_registration_pattern_added(self):
        """V37.9.18 新增的注册缺失检查"""
        self.assertIn('grep -q "registry 已启用但 crontab 中未找到"', self.src,
            "preflight 必须新增检查 'registry 已启用但 crontab 中未找到' warning，"
            "这正是 V37.9.16 漏掉导致 kb_deep_dive 假绿的关键 grep 模式")

    def test_drift_failed_flag_used(self):
        """两种检查共用 DRIFT_FAILED 标志，统一决定 pass/fail 分支"""
        self.assertIn("DRIFT_FAILED=false", self.src)
        self.assertIn("DRIFT_FAILED=true", self.src)
        self.assertIn("if ! $DRIFT_FAILED", self.src,
            "preflight 必须用 DRIFT_FAILED 标志统一两种检查的 pass/fail 分支")

    def test_fix_hint_mentions_crontab_safe_add(self):
        """注册缺失场景的修复提示必须指向 crontab_safe.sh add"""
        # 修复提示中必须包含 add 命令引用
        self.assertIn("crontab_safe.sh add", self.src,
            "preflight fail 信息必须告诉用户用 crontab_safe.sh add '<cron 行>' 修复")

    def test_pass_message_updated_to_dual_check(self):
        """pass 信息必须从仅说"零漂移"升级为同时声明两个维度都通过"""
        # 旧信息 "crontab 间隔与 registry 一致（零漂移）" 已不能完整表达
        self.assertIn("crontab 与 registry 一致", self.src,
            "pass 信息必须更新为同时声明两个维度都通过")
        # 旧的 misleading 短语必须消除
        self.assertNotIn("crontab 间隔与 registry 一致（零漂移）", self.src,
            "旧的仅说零漂移的 pass 信息会让人误以为只检查了间隔漂移")


class TestCrontabSafeStrictExitCheck(unittest.TestCase):
    """源码层守卫: crontab_safe.sh:cmd_add 退出码检查 + 严格相等"""

    @classmethod
    def setUpClass(cls):
        cls.src = (REPO_ROOT / "crontab_safe.sh").read_text(encoding="utf-8")

    def test_v37_9_18_marker_present(self):
        """V37.9.18 修复注释必须存在"""
        self.assertIn("V37.9.18", self.src,
            "crontab_safe.sh 必须保留 V37.9.18 修复注释")
        self.assertIn("kb_deep_dive", self.src,
            "必须保留血案引用便于追溯（吞退出码 + < 比较谎报 35→35 ✅）")

    def test_install_checks_exit_code(self):
        """关键修复: crontab "$tmp_file" 必须用 if ! 检查退出码"""
        self.assertIn('if ! crontab "$tmp_file"', self.src,
            "crontab_safe.sh:cmd_add 必须用 'if ! crontab' 检查退出码，"
            "之前裸调 crontab 后不检查 $? 是 V37.9.16 血案的直接放大器")

    def test_strict_equality_count_check(self):
        """关键修复: count 比较从 -lt 改为 -ne，严格相等"""
        # 旧的 "[ "$count_after" -lt "$count_before" ]" 让 35→35 漏过
        self.assertNotIn('"$count_after" -lt "$count_before"', self.src,
            "禁止保留 -lt 比较: 35→35 不小于 35，crontab 拒绝安装时仍打 ✅")
        # 新的严格相等检查
        self.assertIn('"$count_after" -ne "$expected"', self.src,
            "必须用 -ne 严格相等检查: count_after 必须正好等于 count_before+1")

    def test_expected_variable_computed_correctly(self):
        """必须有 expected = count_before + 1 的计算"""
        self.assertIn('expected=$((count_before + 1))', self.src,
            "必须显式计算 expected = count_before + 1，让对比逻辑清晰")

    def test_failure_error_message_present(self):
        """安装失败时必须打印明确错误信息（含 'crontab 安装失败' 字样）"""
        self.assertIn("crontab 安装失败", self.src,
            "crontab 拒绝安装时必须打印明确失败原因，不能静默")

    def test_failure_path_exits_nonzero(self):
        """安装失败路径必须 exit 1"""
        # 找到 if ! crontab 块，里面必须有 exit 1
        # 最低限度: 文件中必须存在含 "crontab 安装失败" 的代码块附近有 exit 1
        # 用简化 regex 匹配多行块
        # 用行首 fi 边界避免匹配到 "tmp_file" 内部的 "fi"
        m = re.search(
            r'if !\s+crontab\s+"\$tmp_file".*?\n\s*fi\b',
            self.src, re.DOTALL
        )
        self.assertIsNotNone(m,
            "找不到 if ! crontab 块（修复可能被回退）")
        block = m.group(0)
        self.assertIn("exit 1", block,
            "if ! crontab 块内必须有 exit 1，否则即使检测到失败也会继续走到打 ✅ 的代码")
        self.assertIn("crontab 安装失败", block,
            "失败块必须打印 'crontab 安装失败' 错误信息")


class TestCrontabSafeRejectedInputBehavior(unittest.TestCase):
    """行为层: 用 fake crontab shim 模拟 cron 拒绝场景，验证脚本真的 exit 1 不打 ✅"""

    def setUp(self):
        # 隔离的临时目录作 HOME，避免污染真实 ~/.crontab_backups
        self.tmp = tempfile.mkdtemp()
        # Fake crontab shim: -l 返回空，安装时永远拒绝
        self.shim_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.shim_dir)
        crontab_shim = os.path.join(self.shim_dir, "crontab")
        with open(crontab_shim, "w") as f:
            f.write(
                "#!/bin/bash\n"
                "if [ \"$1\" = \"-l\" ]; then\n"
                "    echo ''\n"
                "    exit 0\n"
                "fi\n"
                "# 任何安装请求都拒绝（模拟 'bad minute' 场景）\n"
                "echo 'bad minute' >&2\n"
                "exit 1\n"
            )
        os.chmod(crontab_shim, 0o755)
        self.crontab_shim_path = crontab_shim

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_crontab_safe_add(self, line):
        env = os.environ.copy()
        env["PATH"] = f"{self.shim_dir}:{env.get('PATH', '')}"
        env["HOME"] = self.tmp
        return subprocess.run(
            ["bash", str(REPO_ROOT / "crontab_safe.sh"), "add", line],
            capture_output=True, text=True, env=env, timeout=10
        )

    def test_rejected_install_exits_nonzero(self):
        """V37.9.18: crontab 拒绝时脚本必须 exit 非 0"""
        result = self._run_crontab_safe_add("30 22 * * * bash ~/test.sh")
        self.assertNotEqual(result.returncode, 0,
            f"crontab 拒绝时 crontab_safe 必须 exit != 0，"
            f"否则 V37.9.16 血案会再演（35→35 仍打 ✅）。\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")

    def test_rejected_install_does_not_print_success(self):
        """V37.9.18: crontab 拒绝时绝不能打 ✅ 已添加"""
        result = self._run_crontab_safe_add("30 22 * * * bash ~/test.sh")
        self.assertNotIn("✅ 已添加", result.stdout,
            f"crontab 拒绝时绝不能打 '✅ 已添加'，"
            f"这正是 V37.9.16 血案的谎报形态。\n"
            f"stdout: {result.stdout}")

    def test_rejected_install_prints_failure_reason(self):
        """V37.9.18: crontab 拒绝时必须打印明确失败原因"""
        result = self._run_crontab_safe_add("invalid cron line")
        # 失败原因可能在 stdout 或 stderr
        combined = result.stdout + result.stderr
        self.assertIn("crontab 安装失败", combined,
            f"crontab 拒绝时必须打印 'crontab 安装失败' 让用户能立即识别。\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

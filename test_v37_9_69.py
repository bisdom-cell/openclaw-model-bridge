"""V37.9.69 双修守卫: B 项 watchdog test alternation + C 项 full_regression 计数修复.

血案: V37.9.69+ 候选 (b) test_watchdog_self_monitoring.py 5 个测试漂移修
      + 候选 (c) full_regression.sh line 30 bash 计数 bug.

V37.9.69 修复:
  B项: test_watchdog_self_monitoring.py 加 V37.9.63 helper alternation 守卫
       (V37.9.58-hotfix3 inline `_watchdog_fatal_handler` → V37.9.63 helper
       `_cron_monitor_fatal_handler` 漂移后, alternation 接受两种形式)
  C项: full_regression.sh line 30 多行 count 算术 syntax error bug —
       `count=$(... | grep -oE '[0-9]+' || echo "0")` 多匹配时 count="5\n10\n20"
       → `$((TOTAL_TESTS + count))` syntax error → TOTAL_TESTS 计数失真.
       V37.9.69 fix: `tail -n1 | grep -oE '[0-9]+' | head -n1` 取末行
       (unittest summary 永远在 stdout 末尾) + `${count:-0}` 算术兜底.

测试契约:
  Layer 1 (源码静态): V37.9.69 字面量守卫 + 反 buggy 模式守卫
  Layer 2 (行为验证): subprocess 真跑 bash + multi-line input 断言 fix 真有效
  Layer 3 (集成验证): subprocess 跑 test_watchdog_self_monitoring.py 31/31 OK
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
FULL_REGRESSION_SH = os.path.join(REPO_ROOT, "full_regression.sh")
WATCHDOG_TEST_PY = os.path.join(REPO_ROOT, "test_watchdog_self_monitoring.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── B 项: test_watchdog_self_monitoring.py V37.9.63 helper alternation ────

class TestWatchdogTestAlternationGuards(unittest.TestCase):
    """V37.9.69 B 项: test_watchdog_self_monitoring.py 必须含 V37.9.63 helper alternation."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(WATCHDOG_TEST_PY)

    def test_helper_detection_function_defined(self):
        """_watchdog_uses_v37_9_63_helper helper 函数必须定义 (alternation 判定核心)."""
        self.assertIn("_watchdog_uses_v37_9_63_helper", self.src,
            "V37.9.69 B 项: 必须有 _watchdog_uses_v37_9_63_helper helper 函数")
        self.assertIn("def _watchdog_uses_v37_9_63_helper", self.src,
            "helper 必须是函数定义而非字符串引用")

    def test_helper_src_reader_defined(self):
        """_read_helper_src() 必须读 cron_monitor_fatal_handler.sh."""
        self.assertIn("_read_helper_src", self.src,
            "V37.9.69 B 项: 必须有 _read_helper_src 读 helper 文件")
        self.assertIn("cron_monitor_fatal_handler.sh", self.src,
            "_read_helper_src 必须引用 cron_monitor_fatal_handler.sh 路径")

    def test_v37_9_69_marker_present(self):
        """V37.9.69 marker 必须出现在测试源码 (历史追溯)."""
        self.assertIn("V37.9.69", self.src,
            "V37.9.69 B 项 marker 必须在 test_watchdog_self_monitoring.py")

    def test_three_failing_tests_use_alternation(self):
        """5 个原 fail 测试必须有 alternation 分支 (V37.9.63 helper OR V37.9.58-hotfix3 inline)."""
        # test_fatal_handler_function_defined / test_err_trap_registered /
        # test_fatal_handler_pushes_system_alert / test_trap_err_after_set_e
        # 必须含 _watchdog_uses_v37_9_63_helper 调用
        for test_name in [
            "test_fatal_handler_function_defined",
            "test_err_trap_registered",
            "test_fatal_handler_pushes_system_alert",
        ]:
            # 找 test 定义起点
            idx = self.src.find(f"def {test_name}(")
            self.assertGreater(idx, 0, f"{test_name} 测试函数未找到")
            # 该函数体 (后 1500 字符内) 必须含 alternation 调用
            block = self.src[idx:idx + 1500]
            self.assertIn("_watchdog_uses_v37_9_63_helper", block,
                f"V37.9.69 B 项: {test_name} 必须用 alternation helper 判定")

    def test_audit_metadata_test_uses_semver_baseline(self):
        """test_audit_metadata_v3_38 必须用 semver 风格 (≥3.38), 不锁死 v3.38."""
        idx = self.src.find("def test_audit_metadata_v3_38(")
        self.assertGreater(idx, 0, "test_audit_metadata_v3_38 未找到")
        block = self.src[idx:idx + 1500]
        # alternation 守卫: assertGreaterEqual baseline + V37.9.69 字样
        self.assertIn("assertGreaterEqual", block,
            "V37.9.69 B 项: test_audit_metadata_v3_38 必须用 assertGreaterEqual semver 风格")
        self.assertIn("3.38", block,
            "baseline 必须仍是 v3.38 (V37.9.58-hotfix3 立 INV-WATCHDOG-SELF-001)")

    def test_watchdog_test_passes_31_of_31(self):
        """行为层: subprocess 真跑 test_watchdog_self_monitoring.py 必须 31/31 OK."""
        result = subprocess.run(
            [sys.executable, WATCHDOG_TEST_PY],
            capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0,
            f"V37.9.69 B 项: test_watchdog_self_monitoring.py 必须 31/31 OK\n"
            f"stderr 尾部: {result.stderr[-500:]}")
        # V37.9.131 alternation: 硬编码 "Ran 31 test" 改 baseline ≥31
        # (V37.9.121 同款教训: 硬编码 count 守卫每次加测试必手改 = 递归接缝.
        # V37.9.131 加 6 个 SLO subshell errtrace 守卫后 31→37)
        m = re.search(r"Ran (\d+) test", result.stderr)
        self.assertIsNotNone(m, "V37.9.69 B 项: 必须有 Ran N tests summary")
        self.assertGreaterEqual(int(m.group(1)), 31,
            f"V37.9.69 B 项: watchdog 测试数必须 ≥31 baseline (实际 {m.group(1)})")
        # 验证 OK (无 FAILED)
        self.assertIn("OK", result.stderr,
            "V37.9.69 B 项: 测试 summary 必须显示 OK")


# ── C 项: full_regression.sh count 多行算术 bug 修复 ──────────────────────

class TestFullRegressionCountFix(unittest.TestCase):
    """V37.9.69 C 项: full_regression.sh line 30 multi-line count 算术 syntax error 修复."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(FULL_REGRESSION_SH)

    def test_v37_9_69_marker_present(self):
        """V37.9.69 marker + fix C 注释必须出现在 full_regression.sh (历史追溯)."""
        self.assertIn("V37.9.69 fix C", self.src,
            "V37.9.69 C 项: full_regression.sh 必须含 V37.9.69 fix C marker")

    def test_count_uses_tail_n1(self):
        """count 提取必须用 `tail -n1` 取末行 (unittest summary 永远在 stdout 末尾)."""
        # alternation 接受 `tail -n1` 或 `tail -n 1` 两种 BSD/GNU 风格
        self.assertTrue(
            "tail -n1" in self.src or "tail -n 1" in self.src,
            "V37.9.69 C 项: count 提取必须用 tail -n1 取末行 (防多匹配)")

    def test_count_has_bash_arith_fallback(self):
        """count 算术必须有 ${count:-0} 兜底 (防 count 空串)."""
        self.assertIn('${count:-0}', self.src,
            "V37.9.69 C 项: count 必须有 ${count:-0} 兜底防空串")

    def test_no_legacy_buggy_pattern(self):
        """V37.9.69 反 buggy 守卫: 旧 line 30 多匹配模式不能保留.

        旧 buggy: `count=$(echo "$output" | grep -oE 'Ran [0-9]+ test' | grep -oE '[0-9]+' || echo "0")`
              ↑ 直接 pipe grep 不加 tail/head, 多匹配 → count 多行 → 算术 syntax error.
        新 fix: 加 tail -n1 (或 tail -n 1) 防多行.
        """
        # 找 'count=' 起点
        idx = self.src.find("count=$(echo \"$output\"")
        self.assertGreater(idx, 0, "count=$(...) 行未找到")
        # 该 count= 行内必须含 tail (V37.9.69 fix C 标志)
        # 找到换行符前的整行
        end = self.src.find("\n", idx)
        line = self.src[idx:end]
        self.assertIn("tail", line,
            f"V37.9.69 C 项反 buggy: count= 行必须含 tail (防多匹配), 实际行: {line}")

    def test_multiline_count_does_not_crash_arith(self):
        """行为层: bash 真跑 V37.9.69 fix 多行场景不抛 syntax error."""
        test_bash = '''
set -uo pipefail
TOTAL_TESTS=0
# 模拟子进程多次跑 unittest 让 grep 抓多行
output='Ran 31 test
Ran 100 test
some unrelated line
Ran 5 test'
# V37.9.69 fix C pattern
count=$(echo "$output" | grep -oE 'Ran [0-9]+ test' | tail -n1 | grep -oE '[0-9]+' | head -n1 || echo "0")
count="${count:-0}"
TOTAL_TESTS=$((TOTAL_TESTS + count))
echo "RESULT count=$count TOTAL_TESTS=$TOTAL_TESTS"
'''
        result = subprocess.run(
            ["bash", "-c", test_bash],
            capture_output=True, text=True, timeout=10
        )
        # 必须 exit 0 (无 arith syntax error)
        self.assertEqual(result.returncode, 0,
            f"V37.9.69 C 项: multi-line count 场景 fix 必须不抛 syntax error\n"
            f"stderr: {result.stderr}")
        # 必须正确取末行 (count=5 是 input 末尾的 'Ran 5 test')
        self.assertIn("count=5", result.stdout,
            f"V37.9.69 C 项: tail -n1 必须取末行 count, 实际: {result.stdout}")
        # TOTAL_TESTS=5 证明算术真生效
        self.assertIn("TOTAL_TESTS=5", result.stdout,
            f"V37.9.69 C 项: 算术 (TOTAL_TESTS + count) 必须真累加, 实际: {result.stdout}")
        # 必须无 'syntax error' (反 buggy 行为契约)
        self.assertNotIn("syntax error", result.stderr,
            f"V37.9.69 C 项 反 buggy: stderr 必须不含 'syntax error'")

    def test_legacy_buggy_pattern_demonstrably_fails(self):
        """反向验证: V37.9.69 fix 前的 buggy 模式确实抛 syntax error (证明 fix 必要性)."""
        # legacy buggy pattern (V37.9.69 fix 前): 不加 tail, 多匹配 → multi-line count
        buggy_bash = '''
set -uo pipefail
TOTAL_TESTS=0
output='Ran 31 test
Ran 100 test
Ran 5 test'
# V37.9.69 fix 前的 buggy pattern (no tail -n1)
count=$(echo "$output" | grep -oE 'Ran [0-9]+ test' | grep -oE '[0-9]+' || echo "0")
TOTAL_TESTS=$((TOTAL_TESTS + count))
echo "FINAL TOTAL_TESTS=$TOTAL_TESTS"
'''
        result = subprocess.run(
            ["bash", "-c", buggy_bash],
            capture_output=True, text=True, timeout=10
        )
        # legacy buggy: bash 算术抛 syntax error
        self.assertIn("syntax error", result.stderr,
            "V37.9.69 C 项反向验证: legacy buggy 模式必须真抛 syntax error (证明 fix 必要性)")


# ── 集成: V37.9.69 修复后 full_regression 仍 bash -n 通过 ──────────────────

class TestV37969Integration(unittest.TestCase):
    """V37.9.69 集成: 修复后 full_regression.sh 仍合法 bash 语法."""

    def test_full_regression_bash_n_syntax(self):
        """bash -n full_regression.sh 必须通过 (V37.9.69 修复未引入语法错误)."""
        result = subprocess.run(
            ["bash", "-n", FULL_REGRESSION_SH],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0,
            f"V37.9.69 集成: bash -n full_regression.sh 必须 OK\n"
            f"stderr: {result.stderr}")


# ── V37.9.140: suites 计数 121/122 抖动修复守卫 (unfinished #27) ──────────────

class TestV379140SuitesCountStability(unittest.TestCase):
    """V37.9.140 (#27): quality.test_suites 计数与对抗层执行条件解耦.

    血案: 对抗审计 layer 3.5 按 git 工作树洁净度条件执行 (不干净跳过), 旧实现
    `--set quality.test_suites "$PASS"` 让 suites 在 121/122 间抖动 →
    gen_readme_badges doc-drift 守卫偶发假阳性 (2026-06-11 收工实测命中:
    doc=121/权威=122 错配窗口 fail). 属"计数抖动→守卫假阳性" silent-failure 家族
    (与本文件 V37.9.69 C 项同族).

    V37.9.140 修复 (unfinished #27 选项 a — 根因层):
      - SUITES_SKIPPED_COUNTED 计数器: 对抗层跳过执行时 +1 (suite 清单数不变)
      - 回写: SUITES_TOTAL=$((PASS + SUITES_SKIPPED_COUNTED)) — 确定值
      - PASS/汇总行语义不变 (只数真执行通过的 suite, 不谎报)

    反向验证 (已确认守卫真有效): 把回写行改回 `"$PASS"` → test_no_legacy_flapping_write
    立即 fail; 删 dirty 分支增量 → test_dirty_branch_increments 立即 fail.
    """

    @classmethod
    def setUpClass(cls):
        with open(FULL_REGRESSION_SH, encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_140_marker_present(self):
        """V37.9.140 修复 marker 必须存在 (可溯源)."""
        self.assertIn("V37.9.140", self.src)

    def test_skipped_counter_initialized(self):
        """SUITES_SKIPPED_COUNTED 计数器必须在顶部初始化为 0."""
        self.assertRegex(self.src, r"(?m)^SUITES_SKIPPED_COUNTED=0")

    def test_dirty_branch_increments_skipped_counter(self):
        """对抗层 dirty-tree 跳过分支必须递增 SUITES_SKIPPED_COUNTED."""
        idx = self.src.find("工作树不干净")
        self.assertGreater(idx, 0, "对抗层 dirty-tree 跳过分支必须存在")
        window = self.src[idx : idx + 300]
        self.assertIn(
            "SUITES_SKIPPED_COUNTED=$((SUITES_SKIPPED_COUNTED + 1))",
            window,
            "跳过分支 300 字符内必须递增计数器 (跳过 ≠ suite 不存在)",
        )

    def test_write_uses_pass_plus_skipped(self):
        """status.json 回写必须用 PASS + SUITES_SKIPPED_COUNTED 确定值."""
        self.assertIn("SUITES_TOTAL=$((PASS + SUITES_SKIPPED_COUNTED))", self.src)
        self.assertIn('--set quality.test_suites "$SUITES_TOTAL"', self.src)

    def test_no_legacy_flapping_write(self):
        """反模式守卫: 旧抖动写法 `quality.test_suites \"$PASS\"` 不得回归."""
        self.assertNotIn('quality.test_suites "$PASS"', self.src)

    def test_arithmetic_line_behaviorally_correct(self):
        """行为验证: 从源码提取真实算术行, bash 真跑断言 dirty/clean 两路径同值.

        dirty 路径 (PASS=121, SKIPPED=1) 与 clean 路径 (PASS=122, SKIPPED=0)
        必须产出相同 SUITES_TOTAL=122 — 这正是 #27 抖动的消除证明.
        """
        m = re.search(r"(?m)^\s*(SUITES_TOTAL=\$\(\(PASS \+ SUITES_SKIPPED_COUNTED\)\))", self.src)
        self.assertIsNotNone(m, "源码必须含 SUITES_TOTAL 算术行")
        arith = m.group(1)
        results = []
        for pass_v, skipped_v in ((121, 1), (122, 0)):
            r = subprocess.run(
                ["bash", "-c",
                 f'PASS={pass_v}; SUITES_SKIPPED_COUNTED={skipped_v}; {arith}; echo "$SUITES_TOTAL"'],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(r.returncode, 0, f"bash 算术执行失败: {r.stderr}")
            results.append(r.stdout.strip())
        self.assertEqual(results[0], results[1],
            f"dirty/clean 两路径 SUITES_TOTAL 必须相同 (抖动消除): {results}")
        self.assertEqual(results[0], "122")


if __name__ == "__main__":
    unittest.main(verbosity=2)

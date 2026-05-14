#!/usr/bin/env python3
"""test_cross_os_quirk_scanner.py — V37.9.67 INV-CROSS-OS-001 scanner 单测

测试矩阵 (5 类):
  1. TestQuirkCmdAndOrChain — 检测 `cmd && X || Y` 反模式
  2. TestQuirkGrepHeadNoOrTrue — 检测 `grep | head` 无 `|| true` 兜底
  3. TestQuirkAwkLogNoLCAll — 检测 awk 处理 log 缺 LC_ALL=C
  4. TestQuirkZshSpecific — 检测 zsh-specific 语法 in .sh
  5. TestRepoIntegration — repo 全量 scan 0 violations + 反向验证守卫

反向验证: sabotage 任一文件回退到 buggy → scanner 立即抓
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent
SCANNER_PATH = REPO_ROOT / "cross_os_quirk_scanner.py"


# ════════════════════════════════════════════════════════════════════
# 1. cmd && X || Y 反模式检测
# ════════════════════════════════════════════════════════════════════
class TestQuirkCmdAndOrChain(unittest.TestCase):
    def _scan(self, content):
        """直接调 scanner 函数测 string."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("scanner", SCANNER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.detect_cmd_and_or_chain(content)

    def test_detect_classic_pattern(self):
        """SLO_ALERT=$(...) && SLO_RC=0 || SLO_RC=$?"""
        content = 'SLO_ALERT=$(python3 slo.py --alert 2>/dev/null) && SLO_RC=0 || SLO_RC=$?'
        findings = self._scan(content)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][1], "cmd_and_or_chain")

    def test_no_false_positive_on_comments(self):
        """注释行不算违反"""
        content = '# SLO_ALERT=$(...) && SLO_RC=0 || SLO_RC=$?'
        self.assertEqual(self._scan(content), [])

    def test_if_then_else_form_clean(self):
        """if-then-else 形式不算违反 (正确修复模式)"""
        content = '''if SLO_ALERT=$(python3 slo.py --alert 2>/dev/null); then
    SLO_RC=0
else
    SLO_RC=$?
fi'''
        self.assertEqual(self._scan(content), [])

    def test_multi_line_block_with_multiple_violations(self):
        """多行块每行独立计数"""
        content = '''A=$(cmd1 2>/dev/null) && X=0 || X=$?
B=$(cmd2 2>&1) && Y=0 || Y=$?
echo "ok"'''
        findings = self._scan(content)
        self.assertEqual(len(findings), 2)


# ════════════════════════════════════════════════════════════════════
# 2. grep | head 反模式检测
# ════════════════════════════════════════════════════════════════════
class TestQuirkGrepHeadNoOrTrue(unittest.TestCase):
    def _scan(self, content):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scanner", SCANNER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.detect_grep_head_no_or_true(content)

    def test_detect_grep_head_no_guard(self):
        """VAR=$(... | grep X | head -1) 无 || true → 违反"""
        content = 'VAR=$(echo "$X" | grep "pattern" | head -1)'
        findings = self._scan(content)
        self.assertEqual(len(findings), 1)

    def test_or_true_guard_exempts(self):
        """末尾 || true 豁免"""
        content = 'VAR=$(echo "$X" | grep "pattern" | head -1 || true)'
        self.assertEqual(self._scan(content), [])

    def test_or_echo_guard_exempts(self):
        """末尾 || echo "" 也豁免 (V37.9.67 升级)"""
        content = 'VAR=$(echo "$X" | grep "pattern" | head -1 || echo "")'
        self.assertEqual(self._scan(content), [])

    def test_no_false_positive_on_comments(self):
        content = '# VAR=$(echo "$X" | grep "pattern" | head -1)'
        self.assertEqual(self._scan(content), [])


# ════════════════════════════════════════════════════════════════════
# 3. awk log 无 LC_ALL=C 检测 (V37.9.58-hotfix3 同款防御)
# ════════════════════════════════════════════════════════════════════
class TestQuirkAwkLogNoLCAll(unittest.TestCase):
    def _scan(self, content):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scanner", SCANNER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.detect_awk_log_no_lc_all(content)

    def test_detect_awk_log_no_lc_all(self):
        """tail X.log | awk ... 缺 LC_ALL=C → 违反"""
        content = 'tail -100 /var/log/x.log | awk \'{print $1}\''
        findings = self._scan(content)
        self.assertEqual(len(findings), 1)

    def test_lc_all_c_prefix_exempts(self):
        """tail X.log | LC_ALL=C awk ... → 豁免 (V37.9.58-hotfix3 正确模式)"""
        content = 'tail -100 /var/log/x.log | LC_ALL=C awk \'{print $1}\''
        self.assertEqual(self._scan(content), [])

    def test_simple_awk_not_in_log_context_no_false_positive(self):
        """简单 awk 不在 log 上下文不报"""
        content = 'echo "a b" | awk \'{print $1}\''
        self.assertEqual(self._scan(content), [])


# ════════════════════════════════════════════════════════════════════
# 4. zsh-specific 语法检测 (V37.9.56-hotfix2 同款防御)
# ════════════════════════════════════════════════════════════════════
class TestQuirkZshSpecific(unittest.TestCase):
    def _scan(self, content):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scanner", SCANNER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.detect_zsh_specific_in_sh(content)

    def test_detect_typeset_a(self):
        """typeset -A (zsh-only) → 违反"""
        content = 'typeset -A MAP'
        findings = self._scan(content)
        self.assertEqual(len(findings), 1)

    def test_detect_setopt(self):
        """setopt (zsh-only) → 违反"""
        content = 'setopt interactive_comments'
        findings = self._scan(content)
        self.assertEqual(len(findings), 1)

    def test_no_false_positive_on_bash_constructs(self):
        """bash 标准语法不报"""
        content = '''declare -A MAP
local X=1
case "$y" in
    *) echo "ok" ;;
esac'''
        self.assertEqual(self._scan(content), [])


# ════════════════════════════════════════════════════════════════════
# 5. V37.9.68 教训: head -c N | tr 切多字节 UTF-8 (Mac Mini bsd tr 报警)
# ════════════════════════════════════════════════════════════════════
class TestQuirkHeadByteTrNoLcAll(unittest.TestCase):
    def _scan(self, content):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scanner", SCANNER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.detect_head_byte_tr_no_lc_all(content)

    def test_detect_head_byte_tr_no_lc_all(self):
        """V37.9.68 血案模式: echo $x | head -c 120 | tr '\\n' ' ' → 违反"""
        content = 'HEAD=$(echo "$DEEP_RESULT" | head -c 120 | tr \'\\n\' \' \')'
        findings = self._scan(content)
        self.assertEqual(len(findings), 1)

    def test_detect_head_byte_tr_with_other_byte_count(self):
        """head -c 200 | tr 同款违反 (V37.9.40-44 LAST_LLM_FAIL_REASON pattern)"""
        content = 'LAST_LLM_FAIL_REASON=$(echo "$parse_err" | head -c 200 | tr \'\\n\' \' \')'
        findings = self._scan(content)
        self.assertEqual(len(findings), 1)

    def test_lc_all_c_tr_exempt(self):
        """合规模式: head -c N | LC_ALL=C tr → 不报 (V37.9.68 修复模式)"""
        content = 'HEAD=$(echo "$x" | head -c 120 | LC_ALL=C tr \'\\n\' \' \')'
        self.assertEqual(self._scan(content), [])

    def test_no_false_positive_on_head_alone(self):
        """head -c 不跟 tr → 不报"""
        content = 'X=$(cat file.txt | head -c 100)'
        self.assertEqual(self._scan(content), [])

    def test_no_false_positive_on_tr_without_head(self):
        """tr 不跟 head -c → 不报"""
        content = 'X=$(echo "abc" | tr a-z A-Z)'
        self.assertEqual(self._scan(content), [])

    def test_comment_line_exempt(self):
        """注释里的反模式不报 (避免血案文档触发)"""
        content = '# 反模式: echo $x | head -c 120 | tr \'\\n\' \' \''
        self.assertEqual(self._scan(content), [])


# ════════════════════════════════════════════════════════════════════
# 6. 全 repo 集成 + 反向验证
# ════════════════════════════════════════════════════════════════════
class TestRepoIntegration(unittest.TestCase):
    def test_repo_scan_zero_violations(self):
        """V37.9.67 收工后 repo 必须 0 violations (FAIL-CLOSE)"""
        result = subprocess.run(
            ["python3", str(SCANNER_PATH)],
            capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        self.assertEqual(
            result.returncode, 0,
            f"INV-CROSS-OS-001 scan 不通过:\n{result.stdout}\n{result.stderr}"
        )
        self.assertIn("0 violations", result.stdout)

    def test_cli_list_quirks(self):
        """--list-quirks 列出所有 4 个 quirk"""
        result = subprocess.run(
            ["python3", str(SCANNER_PATH), "--list-quirks"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        for quirk in ("cmd_and_or_chain", "grep_head_no_or_true",
                      "awk_log_no_lc_all", "zsh_specific_in_sh"):
            self.assertIn(quirk, result.stdout)

    def test_sabotage_reverse_verification(self):
        """反向验证: sabotage watchdog 还原 cmd && X || Y → scanner 必抓"""
        # 创建临时 sabotaged 文件 (不动真实仓库)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write('#!/bin/bash\nset -eEo pipefail\n')
            f.write('SLO_ALERT=$(python3 slo.py 2>/dev/null) && SLO_RC=0 || SLO_RC=$?\n')
            tmp_path = f.name
        try:
            result = subprocess.run(
                ["python3", str(SCANNER_PATH), "--file", tmp_path],
                capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 1, "sabotaged file 必须触发 exit 1")
            self.assertIn("cmd_and_or_chain", result.stdout)
        finally:
            os.unlink(tmp_path)


# ════════════════════════════════════════════════════════════════════
# 6. 源码级守卫
# ════════════════════════════════════════════════════════════════════
class TestSourceLevelGuards(unittest.TestCase):
    def setUp(self):
        with open(SCANNER_PATH) as f:
            self.src = f.read()

    def test_v37_9_67_marker(self):
        self.assertIn("V37.9.67", self.src)
        self.assertIn("INV-CROSS-OS-001", self.src)

    def test_fail_close_documented(self):
        self.assertIn("FAIL-CLOSE", self.src)

    def test_4_quirk_checkers_registered(self):
        """4 个 quirk checker 全部注册"""
        for name in ("detect_cmd_and_or_chain", "detect_grep_head_no_or_true",
                     "detect_awk_log_no_lc_all", "detect_zsh_specific_in_sh"):
            self.assertIn(f"def {name}", self.src)

    def test_blood_lesson_references(self):
        """必须引用具体血案版本"""
        for ver in ("V37.9.66-hotfix", "V37.9.60-hotfix",
                    "V37.9.58-hotfix3", "V37.9.56-hotfix2"):
            self.assertIn(ver, self.src,
                          f"scanner 必须引用 {ver} 血案 (溯源)")


if __name__ == "__main__":
    unittest.main(verbosity=2)

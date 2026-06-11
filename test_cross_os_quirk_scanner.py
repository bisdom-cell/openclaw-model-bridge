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
# 5b. Quirk 6: $(...) 子 shell 继承 ERR trap (V37.9.134 新增)
#     血案: V37.9.105-hotfix (governance_audit ×2) + V37.9.131 (watchdog SLO)
# ════════════════════════════════════════════════════════════════════
class TestQuirkSubshellErrtrace(unittest.TestCase):
    # 文件级前提骨架: errtrace + ERR trap (两个血案脚本的共同特征)
    PREAMBLE = (
        '#!/bin/bash\n'
        'set -eEuo pipefail\n'
        'trap \'_fatal_handler $LINENO\' ERR\n'
    )

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scanner", SCANNER_PATH)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _scan(self, body, preamble=None):
        content = (preamble if preamble is not None else self.PREAMBLE) + body
        return self.mod.detect_subshell_errtrace_designed_nonzero(content)

    def test_detect_unwrapped_governance_checker(self):
        """basename 清单命中: governance_checker.py 无 set +E 包裹"""
        findings = self._scan(
            'GOV_OUTPUT=$(cd "$REPO" && python3 ontology/governance_checker.py --full 2>&1)\n'
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][1], "subshell_errtrace_designed_nonzero")

    def test_detect_alert_flag_with_variable_script_path(self):
        """flag 启发命中: V37.9.131 watchdog 形态 — 行内是 "$SLO_SCRIPT" 变量
        看不到文件名, 必须靠 --alert flag 识别设计性非零命令"""
        findings = self._scan(
            'SLO_ALERT=$(python3 "$SLO_SCRIPT" --alert 2>/dev/null)\n'
        )
        self.assertEqual(len(findings), 1)

    def test_or_capture_does_not_exempt(self):
        """血案核心守卫: `|| RC=$?` **不豁免** — V37.9.105 铁证: 外层 || 已
        正确捕获 GOV_RC, 子 shell 内 trap 仍 fire 推假 'FATAL abort line=64'"""
        findings = self._scan(
            'SLO_ALERT=$(python3 "$SLO_SCRIPT" --alert 2>/dev/null) || SLO_RC=$?\n'
        )
        self.assertEqual(len(findings), 1,
                         "|| 捕获防不了子 shell 内 trap fire, 必须报 violation")

    def test_set_plus_e_wrap_exempts(self):
        """V37.9.131 修复形态 (set +E 三件套) 必须 0 findings"""
        findings = self._scan(
            'SLO_RC=0\n'
            'set +E\n'
            'SLO_ALERT=$(python3 "$SLO_SCRIPT" --alert 2>/dev/null) || SLO_RC=$?\n'
            'set -E\n'
        )
        self.assertEqual(findings, [])

    def test_no_errtrace_no_findings(self):
        """前提不成立: 只有 set -e (无大写 E errtrace) → 子 shell 不继承 trap"""
        preamble = ('#!/bin/bash\nset -eo pipefail\n'
                    'trap \'_h $LINENO\' ERR\n')
        findings = self._scan(
            'GOV=$(python3 ontology/governance_checker.py --full)\n',
            preamble=preamble)
        self.assertEqual(findings, [])

    def test_no_trap_err_no_findings(self):
        """前提不成立: 有 errtrace 但无 ERR trap → trap fire 无从谈起"""
        preamble = '#!/bin/bash\nset -eEuo pipefail\n'
        findings = self._scan(
            'GOV=$(python3 ontology/governance_checker.py --full)\n',
            preamble=preamble)
        self.assertEqual(findings, [])

    def test_plain_json_parse_not_flagged(self):
        """误报口径守卫: `python3 -c 'json.load'` 类只在真异常时非零 —
        那时 trap fire 是**正确告警**非误报, 不应被 scanner 报 (区别于
        governance_checker/slo_checker 把非零 exit 当正常 API 的周期性假 FATAL)"""
        findings = self._scan(
            'STATUS=$(echo "$OUT" | python3 -c \'import json,sys; '
            'print(json.load(sys.stdin).get("status","?"))\')\n'
        )
        self.assertEqual(findings, [])

    def test_comment_line_exempt(self):
        findings = self._scan(
            '# 反模式示例: GOV=$(python3 governance_checker.py --full)\n'
        )
        self.assertEqual(findings, [])

    def test_set_minus_e_reopens_detection(self):
        """状态机守卫: set -E 重新打开 errtrace 后, 后续违规行必须被抓"""
        findings = self._scan(
            'set +E\n'
            'A=$(python3 governance_checker.py --full) || A_RC=$?\n'
            'set -E\n'
            'B=$(python3 "$SLO_SCRIPT" --alert)\n'
        )
        self.assertEqual(len(findings), 1)
        self.assertIn('B=$', findings[0][2])

    def test_check_flag_detected(self):
        """第 3 实证点形态: engine.py --check (V37.9.105-hotfix 第二处)"""
        findings = self._scan(
            'ENGINE_OUTPUT=$(cd "$REPO" && python3 ontology/engine.py --check 2>&1)\n'
        )
        self.assertEqual(len(findings), 1)

    def test_real_watchdog_and_gov_audit_clean(self):
        """真实 repo 两个血案脚本 (已修复) 必须 0 个该 quirk findings"""
        for sh in ("job_watchdog.sh", "governance_audit_cron.sh"):
            with open(REPO_ROOT / sh, encoding="utf-8") as f:
                content = f.read()
            findings = self.mod.detect_subshell_errtrace_designed_nonzero(content)
            self.assertEqual(
                findings, [],
                f"{sh} 已 set +E 包裹, 不应有 findings: {findings}")

    def test_sabotage_remove_set_plus_e_caught(self):
        """反向验证守卫真有效: 把真实 job_watchdog.sh 的 set +E/set -E 包裹
        删掉 (模拟未来重构弄丢包裹), scanner 必须立即抓到 SLO 行"""
        with open(REPO_ROOT / "job_watchdog.sh", encoding="utf-8") as f:
            lines = f.read().split("\n")
        sabotaged = "\n".join(
            l for l in lines
            if l.strip() not in ("set +E", "set -E")
            # 保留文件头的 set -eEo pipefail (errtrace 前提)
            or l.lstrip().startswith("set -eE")
        )
        findings = self.mod.detect_subshell_errtrace_designed_nonzero(sabotaged)
        self.assertGreaterEqual(
            len(findings), 1,
            "sabotage 删 set +E 包裹后 scanner 必须抓到 SLO --alert 行")
        self.assertTrue(
            any("--alert" in f[2] for f in findings),
            f"必须抓到 slo_checker --alert 行: {findings}")


# ════════════════════════════════════════════════════════════════════
# 6. 全 repo 集成 + 反向验证
# ════════════════════════════════════════════════════════════════════
class TestQuirkUnbracedVarCjk(unittest.TestCase):
    """Quirk 7: 未 brace `$VAR` 紧贴 CJK/全角字符 (V37.9.43-hotfix2 + V37.9.141).

    血案: 2026-06-11 22:53 Mac Mini preflight check 16 实测崩溃
    `preflight_check.sh: line 868: PUSH_RC?: unbound variable` — push test 真失败
    首次走 fail 分支才触发 (潜伏), 且崩溃吞掉真实失败信息 + check 17-19 未跑.
    locale 依赖: cron (C locale) 不触发 / 交互终端 (UTF-8) 触发.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scanner_q7", SCANNER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.mod = mod

    def _scan(self, content):
        return self.mod.detect_unbraced_var_adjacent_cjk(content)

    def test_blood_lesson_preflight_line_868(self):
        """V37.9.141 血案场景: preflight line 868 原始形状必须被抓."""
        findings = self._scan(
            'fail "WhatsApp 推送失败（退出码 $PUSH_RC）: $(echo "$PUSH_STDERR" | head -2)"\n'
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][1], "unbraced_var_adjacent_cjk")

    def test_blood_lesson_wa_e2e_chunk_count(self):
        """V37.9.43-hotfix2 第 1 次演出形状 (wa_e2e CHUNK_COUNT) 必须被抓."""
        findings = self._scan(
            'pass "KB 索引可用（$CHUNK_COUNT）"\n'
        )
        self.assertEqual(len(findings), 1)

    def test_fullwidth_open_paren_flagged(self):
        """`$VAR（` (变量后紧贴全角开括号) 同样违反."""
        findings = self._scan(
            'log "WARN: HTTP $HTTP_CODE（第1次）"\n'
        )
        self.assertEqual(len(findings), 1)

    def test_cjk_ideograph_flagged(self):
        """`$VAR中文` (紧贴 CJK 表意文字) 违反."""
        findings = self._scan('echo "$COUNT条记录"\n')
        self.assertEqual(len(findings), 1)

    def test_braced_var_clean(self):
        """`${VAR}）` 显式 brace 是合规修复形式."""
        findings = self._scan(
            'fail "WhatsApp 推送失败（退出码 ${PUSH_RC}）"\n'
            'log "WARN: HTTP ${HTTP_CODE}（第${attempt}次）"\n'
        )
        self.assertEqual(len(findings), 0)

    def test_ascii_adjacent_not_flagged(self):
        """变量后接 ASCII (空格/半角括号/冒号) 不违反 — bash 正常结束变量名."""
        findings = self._scan(
            'echo "exit=$RC) done"\nif [ $PUSH_RC -eq 0 ]; then\n'
        )
        self.assertEqual(len(findings), 0)

    def test_comment_line_exempt(self):
        """注释行豁免 (含血案引用字样的注释不自伤)."""
        findings = self._scan(
            '# 血案: $PUSH_RC）在 bash 3.2 下崩溃\n'
        )
        self.assertEqual(len(findings), 0)

    def test_special_params_not_flagged(self):
        """`$1）` / `$?）` 特殊参数天然豁免 (单字符名, regex 要求 [A-Za-z_] 开头)."""
        findings = self._scan(
            'echo "第 $1）项"\necho "码 $?）"\n'
        )
        self.assertEqual(len(findings), 0)

    def test_real_fixed_files_clean(self):
        """V37.9.141 修复后的 7 个真实文件必须 0 cjk findings (防回退)."""
        for rel in ("preflight_check.sh", "gameday.sh", "job_smoke_test.sh",
                    "jobs/arxiv_monitor/run_arxiv.sh", "jobs/hf_papers/run_hf_papers.sh",
                    "jobs/github_trending/run_github_trending.sh",
                    "jobs/finance_news/run_finance_news.sh"):
            path = REPO_ROOT / rel
            content = path.read_text(encoding="utf-8", errors="replace")
            findings = self._scan(content)
            self.assertEqual(
                findings, [],
                f"{rel} 不得回退到未 brace 形式: {findings[:3]}")

    def test_sabotage_file_caught_by_cli(self):
        """反向验证: 含违反的临时文件经 CLI --file 必 exit 1 + 报 quirk 名."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write('#!/bin/bash\nset -u\nfail "推送失败（退出码 $PUSH_RC）"\n')
            tmp_path = f.name
        try:
            result = subprocess.run(
                ["python3", str(SCANNER_PATH), "--file", tmp_path],
                capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("unbraced_var_adjacent_cjk", result.stdout)
        finally:
            os.unlink(tmp_path)


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
        """--list-quirks 列出所有 7 个 quirk"""
        result = subprocess.run(
            ["python3", str(SCANNER_PATH), "--list-quirks"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        for quirk in ("cmd_and_or_chain", "grep_head_no_or_true",
                      "awk_log_no_lc_all", "zsh_specific_in_sh",
                      "head_byte_tr_no_lc_all",
                      "subshell_errtrace_designed_nonzero",
                      "unbraced_var_adjacent_cjk"):
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

    def test_all_quirk_checkers_registered(self):
        """7 个 quirk checker 全部注册 (V37.9.67 4 个 + V37.9.68/134/141 各 +1)"""
        for name in ("detect_cmd_and_or_chain", "detect_grep_head_no_or_true",
                     "detect_awk_log_no_lc_all", "detect_zsh_specific_in_sh",
                     "detect_head_byte_tr_no_lc_all",
                     "detect_subshell_errtrace_designed_nonzero",
                     "detect_unbraced_var_adjacent_cjk"):
            self.assertIn(f"def {name}", self.src)

    def test_blood_lesson_references(self):
        """必须引用具体血案版本"""
        for ver in ("V37.9.66-hotfix", "V37.9.60-hotfix",
                    "V37.9.58-hotfix3", "V37.9.56-hotfix2",
                    "V37.9.105-hotfix", "V37.9.131",
                    "V37.9.43-hotfix2", "V37.9.141"):
            self.assertIn(ver, self.src,
                          f"scanner 必须引用 {ver} 血案 (溯源)")

    def test_quirk6_or_capture_documented_as_non_exempt(self):
        """Quirk 6 核心语义必须文档化: || 捕获不豁免 (V37.9.105 铁证)"""
        self.assertIn("防不了", self.src)
        self.assertIn("set +E", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

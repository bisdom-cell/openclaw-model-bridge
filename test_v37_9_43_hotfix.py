#!/usr/bin/env python3
"""test_v37_9_43_hotfix.py — V37.9.43-hotfix preflight 3 警告闭环

Mac Mini V37.9.43 部署后 preflight 81/0/3/0, 3 条警告:
  W1: 货代 deep_dive=skipped (generic, V37.9.31 漏改 Step 9 条件分支)
  W2: KB 索引 98% (37 待索引) — 结构性 lag, 自愈无需修复
  W3: wa_e2e_test.sh 未部署 — 长期漏 FILE_MAP entry (PR #458 即存在但从未登记)

V37.9.43-hotfix 修复:
  W1: jobs/freight_watcher/run_freight.sh line 639 generic 'skipped' 改为
      'skipped_no_high_star' (合法跳过: 无 ⭐≥4 高星条目, Step 9 by-design 不进入)
      preflight 加新 case skipped_no_high_star → pass (V37.9.31 三档 status 补齐)
  W3: auto_deploy.sh FILE_MAP 加 'wa_e2e_test.sh|$HOME/wa_e2e_test.sh'
      原则 #15 测试三层第三层 'WhatsApp 业务验证' 脚本本体闭环

W2 不修复 (结构性 KB lag, kb_embed cron 自愈, 98% 覆盖率合理).
"""
import os
import re
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
FREIGHT_SH = os.path.join(REPO_ROOT, "jobs", "freight_watcher", "run_freight.sh")
PREFLIGHT_SH = os.path.join(REPO_ROOT, "preflight_check.sh")
AUTO_DEPLOY_SH = os.path.join(REPO_ROOT, "auto_deploy.sh")
WA_E2E_SH = os.path.join(REPO_ROOT, "wa_e2e_test.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestW1FreightSkippedSchemaFix(unittest.TestCase):
    """W1: freight Step 9 generic 'skipped' → skipped_no_high_star"""

    @classmethod
    def setUpClass(cls):
        cls.freight_src = _read(FREIGHT_SH)
        cls.preflight_src = _read(PREFLIGHT_SH)

    def test_freight_no_generic_skipped_status(self):
        """freight 不再写 generic DEEP_DIVE_STATUS='skipped' (无后缀)"""
        # 必须不存在 DEEP_DIVE_STATUS="skipped" 字面量 (不带 _no_news/_llm_failed/_parse_low/_no_high_star 后缀)
        # 用 regex 锁定 = 后是 "skipped" 单独闭引号
        pattern = re.compile(r'DEEP_DIVE_STATUS\s*=\s*"skipped"')
        for line_no, line in enumerate(self.freight_src.splitlines(), start=1):
            stripped = line.lstrip()
            # 跳过注释行 (V37.9.43-hotfix 注释解释为什么改)
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                self.fail(
                    f"L{line_no} freight 仍有 generic DEEP_DIVE_STATUS='skipped' "
                    f"(应改为 skipped_no_high_star, V37.9.43-hotfix): {line.strip()!r}"
                )

    def test_freight_uses_skipped_no_high_star(self):
        """freight Step 9 必须用具体 status skipped_no_high_star"""
        self.assertIn(
            'DEEP_DIVE_STATUS="skipped_no_high_star"',
            self.freight_src,
            msg="freight 必须写 skipped_no_high_star (V37.9.43-hotfix)"
        )

    def test_freight_v37_9_43_hotfix_marker(self):
        """V37.9.43-hotfix 注释必须存在 (溯源)"""
        self.assertIn("V37.9.43-hotfix", self.freight_src)

    def test_preflight_has_skipped_no_high_star_case(self):
        """preflight 必须识别 skipped_no_high_star 为 pass (V37.9.43-hotfix)"""
        # 必须含 skipped_no_high_star) ... pass 模式
        pattern = re.compile(
            r'skipped_no_high_star\)\s*\n\s*pass\s+',
            re.MULTILINE
        )
        self.assertTrue(
            pattern.search(self.preflight_src),
            msg="preflight 必须有 skipped_no_high_star → pass case (V37.9.43-hotfix)"
        )

    def test_preflight_v37_9_43_hotfix_marker(self):
        """preflight V37.9.43-hotfix 注释必须存在"""
        self.assertIn("V37.9.43-hotfix", self.preflight_src)

    def test_preflight_skipped_no_high_star_before_skipped_no_news(self):
        """skipped_no_high_star case 必须在 skipped_no_news 之前 (V37.9.31 三档之前补齐)"""
        idx_no_high_star = self.preflight_src.find("skipped_no_high_star)")
        idx_no_news = self.preflight_src.find("skipped_no_news)")
        self.assertGreater(idx_no_high_star, 0)
        self.assertGreater(idx_no_news, 0)
        # idx_no_high_star 必须在 idx_no_news 之前 (顺序契约)
        self.assertLess(
            idx_no_high_star, idx_no_news,
            msg="skipped_no_high_star 必须放在 V37.9.31 三档之前 (语义先于 V37.9.31)"
        )

    def test_preflight_legitimate_skip_pass_not_warn(self):
        """skipped_no_high_star 必须用 pass 不是 warn (合法跳过)"""
        # 提取 skipped_no_high_star 块
        idx = self.preflight_src.find("skipped_no_high_star)")
        self.assertGreater(idx, 0)
        block = self.preflight_src[idx:idx+400]
        # 块前 200 chars 必须有 pass 而非 warn
        self.assertTrue(
            re.search(r'pass\s+"', block[:300]),
            msg=f"skipped_no_high_star 必须 pass 不能 warn (V37.9.43-hotfix): {block[:300]!r}"
        )


class TestW3WaE2eTestDeployment(unittest.TestCase):
    """W3: wa_e2e_test.sh 必须在 auto_deploy.sh FILE_MAP"""

    @classmethod
    def setUpClass(cls):
        cls.auto_deploy_src = _read(AUTO_DEPLOY_SH)

    def test_wa_e2e_test_in_file_map(self):
        """wa_e2e_test.sh 必须在 FILE_MAP 中"""
        # FILE_MAP 用 "src|dst" 格式
        self.assertIn(
            'wa_e2e_test.sh|$HOME/wa_e2e_test.sh',
            self.auto_deploy_src,
            msg="auto_deploy.sh FILE_MAP 必须含 wa_e2e_test.sh entry (V37.9.43-hotfix)"
        )

    def test_wa_e2e_test_file_exists(self):
        """wa_e2e_test.sh 文件存在仓库 (前置条件)"""
        self.assertTrue(
            os.path.exists(WA_E2E_SH),
            msg=f"wa_e2e_test.sh 必须在仓库: {WA_E2E_SH}"
        )

    def test_wa_e2e_test_has_v37_9_43_hotfix_marker(self):
        """auto_deploy.sh V37.9.43-hotfix 注释必须存在 (溯源)"""
        # 至少在 wa_e2e_test 附近含 V37.9.43-hotfix 注释
        idx = self.auto_deploy_src.find("wa_e2e_test.sh|")
        self.assertGreater(idx, 0)
        # 上方 300 chars 内必须含 V37.9.43-hotfix
        context = self.auto_deploy_src[max(0, idx-300):idx+200]
        self.assertIn(
            "V37.9.43-hotfix", context,
            msg="wa_e2e_test.sh entry 周围必须含 V37.9.43-hotfix 注释 (溯源)"
        )


class TestW4WaE2eBashCjkVarParsing(unittest.TestCase):
    """W4 (V37.9.43-hotfix2): wa_e2e_test.sh 全角括号紧贴变量 bash 解析 bug.

    Mac Mini 实测发现 (V37.9.43-hotfix 部署后首次运行):
      L140: pass "KB 索引可用（$CHUNK_COUNT）"
      → macOS bash 3.2 把全角 `）` (U+FF09) 的 UTF-8 字节视为变量名一部分
      → set -u 报 'CHUNK_COUNT�: unbound variable' (脚本 exit 1)

    自 PR #458 即存在但因 wa_e2e_test.sh 长期未部署 (V37.9.43-hotfix W3 修复)
    所以从未在 Mac Mini 真跑过, 这是"长期沉睡的真 bug 在新部署时苏醒"典型案例.

    修复: 用 ${VAR} 显式 brace 定界变量名, 防 bash 跨 ASCII/UTF-8 边界混淆.
    """

    @classmethod
    def setUpClass(cls):
        cls.wa_e2e_src = _read(WA_E2E_SH)

    def test_no_unbraced_var_adjacent_to_fullwidth_paren(self):
        """禁止 $VAR 紧贴全角括号 / 中文标点等 (macOS bash 3.2 set -u unbound)"""
        # 匹配 $VAR (无 {}) 后紧跟 CJK / 全角字符 (U+4E00-U+9FFF / U+FF00-U+FFFF)
        pattern = re.compile(r'\$([A-Z_][A-Z_0-9]*)([一-鿿＀-￿])')
        violations = []
        for line_no, line in enumerate(self.wa_e2e_src.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for m in pattern.finditer(line):
                violations.append(
                    f"L{line_no}: ${m.group(1)} 紧贴 '{m.group(2)}' "
                    f"(应改为 ${{{m.group(1)}}}): {line.strip()[:80]!r}"
                )
        self.assertEqual(
            violations, [],
            msg=(
                "wa_e2e_test.sh 仍有 $VAR 紧贴 CJK 字符的 bash 解析 bug "
                "(V37.9.43-hotfix2 修复, 必须用 ${VAR} 显式 brace):\n  "
                + "\n  ".join(violations)
            )
        )

    def test_v37_9_43_hotfix2_marker_present(self):
        """V37.9.43-hotfix2 注释必须存在 (溯源)"""
        self.assertIn("V37.9.43-hotfix2", self.wa_e2e_src)

    def test_chunk_count_uses_brace(self):
        """L140 pass 行必须用 ${CHUNK_COUNT} 显式 brace"""
        self.assertIn("${CHUNK_COUNT}", self.wa_e2e_src)
        # 反向: 不能存在裸 $CHUNK_COUNT 紧贴全角 `）`
        self.assertNotIn("$CHUNK_COUNT）", self.wa_e2e_src)

    def test_kb_result_uses_brace(self):
        """L146 skip 行必须用 ${KB_RESULT} 显式 brace"""
        self.assertNotIn("$KB_RESULT）", self.wa_e2e_src)


class TestHotfixDoesNotBreakV37943Main(unittest.TestCase):
    """hotfix 不得破坏 V37.9.43 主交付 — arxiv_monitor 仍正常"""

    def test_arxiv_monitor_v37_9_43_marker_intact(self):
        """V37.9.43 arxiv_monitor 主修复未被覆盖"""
        arxiv_src = _read(os.path.join(REPO_ROOT, "jobs", "arxiv_monitor", "run_arxiv.sh"))
        self.assertIn("V37.9.43", arxiv_src)
        self.assertIn("call_llm_single_with_retry", arxiv_src)
        self.assertIn("[LLM_DEGRADED]", arxiv_src)

    def test_aligned_scripts_v37_9_43_arxiv_intact(self):
        """ALIGNED_SCRIPTS 仍含 arxiv_monitor V37.9.43"""
        audit_src = _read(os.path.join(REPO_ROOT, "ontology", "llm_cron_audit.py"))
        self.assertIn(
            'jobs/arxiv_monitor/run_arxiv.sh',
            audit_src
        )
        self.assertIn('"V37.9.43"', audit_src)


if __name__ == "__main__":
    unittest.main()

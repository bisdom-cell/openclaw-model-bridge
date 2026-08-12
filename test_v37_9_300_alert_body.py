#!/usr/bin/env python3
"""V37.9.300 守卫 — auto_deploy preflight 告警正文必须含 ⚠️ 顶层警告、排除明细行。

血案（2026-08-12，用户实证）:
    KB 向量索引因 V37.9.299 的 numpy 真值 bug 静默停更 4 天。preflight 每次部署
    后都在报，Discord + WhatsApp 双通道都发了告警——**但用户合理地忽略了它**，
    因为告警正文长这样:

        ❌ docs/config.md: 仓库与运行时不一致（需运行 auto_deploy 或手动 cp）
            ❌ 20260808060240.md [note] — 未索引 (11216 字符)
            ❌ 20260808073219.md [note] — 未索引 (52 字符)
        ❌ PREFLIGHT FAILED: 1 项检查未通过

    机械成因: `grep "❌"` 抓到了 warn() 的**明细行**（明细用 ❌ 作项目符号），
    却丢掉了它们的 ⚠️ 标题——于是明细孤悬在一条无关的 ❌ 失败下面，读起来像
    那条失败的细节；而真正致命的「⚠️ 向量索引已 106h 未更新（kb_embed cron
    是否正常？）」作为 warn 行**从未进入告警**。

    结果: 核心 job 崩溃 4 天的信号，在用户端呈现为一条例行的 doc-sync 唠叨。
    这是「告警疲劳」的机械成因，不是人不够专心 —— 修告警正文比劝人多看一眼有效。

本守卫用**真实血案样本**跑 auto_deploy 里的真实提取模式，任何回退立即红灯。
"""

import os
import re
import subprocess
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
AUTO_DEPLOY = os.path.join(REPO, "auto_deploy.sh")

# 2026-08-12 真实 preflight 输出片段（缩进逐字保留：fail/warn 2 空格，明细 6 空格）
_REAL_SAMPLE = "\n".join([
    "  ❌ docs/config.md: 仓库与运行时不一致（需运行 auto_deploy 或手动 cp）",
    "  ⚠️  KB 索引覆盖 98%（95 个待索引，下次 kb_embed cron 自动补齐）",
    "      ❌ 20260808060240.md [note] — 未索引 (11216 字符)",
    "      ❌ 20260808073219.md [note] — 未索引 (52 字符)",
    "  ⚠️  向量索引已 106h 未更新（kb_embed cron 是否正常？）",
    "  ✅ E2E 测试通过（7 项验证）",
    "❌ PREFLIGHT FAILED: 1 项检查未通过，请修复后再提交",
])


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_pattern():
    """从 auto_deploy.sh 抓真实使用的提取模式（不 hardcode，防守卫与实现漂移）"""
    src = _read(AUTO_DEPLOY)
    m = re.search(r'FAIL_LINES=\$\(echo "\$PREFLIGHT_OUT" \| grep -E "([^"]+)"', src)
    return m.group(1) if m else None


class TestAlertBodyExtraction(unittest.TestCase):
    """行为级: 用真实模式跑真实血案样本"""

    def setUp(self):
        self.pattern = _extract_pattern()
        self.assertIsNotNone(self.pattern,
                             "auto_deploy 必须用 grep -E 提取告警正文（模式可被守卫读取）")

    def _run(self, text):
        proc = subprocess.run(["grep", "-E", self.pattern],
                              input=text, capture_output=True, text=True)
        return proc.stdout

    def test_critical_warning_reaches_alert(self):
        """血案核心: 「向量索引已 106h 未更新」必须进告警（旧版丢失 = 4 天无人知）"""
        out = self._run(_REAL_SAMPLE)
        self.assertIn("向量索引已 106h 未更新", out,
                      "⚠️ 顶层警告被丢弃 = V37.9.300 血案复现（核心 job 死亡信号不送达）")
        self.assertIn("KB 索引覆盖 98%", out, "⚠️ 覆盖率标题必须随行（明细的归属）")

    def test_detail_lines_excluded(self):
        """明细行（6 空格缩进）必须排除——孤悬明细会被误读为上一条失败的细节"""
        out = self._run(_REAL_SAMPLE)
        self.assertNotIn("20260808060240.md", out,
                         "6 空格缩进明细进入告警 = 误导性归属（血案的呈现形态）")
        self.assertNotIn("[note] — 未索引", out)

    def test_real_failures_still_captured(self):
        """真失败行不能被误伤（修复不得削弱原有信号）"""
        out = self._run(_REAL_SAMPLE)
        self.assertIn("docs/config.md", out)
        self.assertIn("PREFLIGHT FAILED", out)

    def test_pass_lines_not_included(self):
        """✅ 通过行不进告警（告警只报异常）"""
        out = self._run(_REAL_SAMPLE)
        self.assertNotIn("E2E 测试通过", out)


class TestSourceGuards(unittest.TestCase):
    def setUp(self):
        self.src = _read(AUTO_DEPLOY)

    def test_bare_fail_only_grep_retired(self):
        """退役 `grep "❌"` 裸模式（只抓失败 → 丢 ⚠️ 标题、抓明细）"""
        self.assertNotRegex(
            self.src, r'FAIL_LINES=\$\(echo "\$PREFLIGHT_OUT" \| grep "❌"',
            "回退到只抓 ❌ = 血案复现（⚠️ 顶层警告不送达 + 明细孤悬误导）")

    def test_marker_and_lineage(self):
        self.assertIn("V37.9.300", self.src, "血案 marker 必须可 grep 追溯")
        self.assertIn("V37.9.60-hotfix", self.src, "|| true 容错的既有 lineage 不得丢失")

    def test_shell_syntax(self):
        proc = subprocess.run(["bash", "-n", AUTO_DEPLOY], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

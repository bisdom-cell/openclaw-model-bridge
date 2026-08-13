#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.301 — 07:00 治理告警正文分层守卫（V37.9.300 告警疲劳同族第二例）

血案（2026-08-13 dev 实证，用真渲染器复现，非推测）:
    governance_audit_cron.sh 旧版 `GOV_VIOLATIONS=... | grep -E "❌|💥" | head -5`
    对 governance_checker 的输出是"抓明细丢标题"。渲染器的层级是

        `  ❌ 🔴 [INV-XXX-001] 名称`                  2 空格 = 不变式级判定
        `       ❌ check 名`                          7 空格 = check 级明细
        `  ❌ N 个不变式被违反, 💥 M 个检查执行出错`   2 空格 = 顶层计数（输出**最后**一行）

    一个 check 多的不变式就吃掉 5 个配额中的 4 个 →
      (1) 排在后面的整条失败不变式静默消失（用户看到 2/4 还以为是全部）
      (2) "一共几个"的顶层计数行排在最末 → 永远第一个被丢 → 连"还有更多"都看不见
      (3) V37.9.214 特意加进 grep 的 💥（让冷启动 flake 自报家门）在多失败时被截掉
          = 那次修复被截断打败
      (4) 无截断提示 → 正文读起来像完整的

修复（与 V37.9.300 auto_deploy 同款判据，一物一形）: 按缩进分层 + 顶层优先 +
显式截断提示；并把一直被算出来却从不送达的 ⚠️ 并存警告附进已经在发的告警。

守卫策略（镜像 V37.9.300）:
  - 血案样本用 **真渲染器** governance_checker.print_results 生成，不手写字面量
  - grep 模式与 helper 源码从 **governance_audit_cron.sh 实际源码抽取**后在真 bash
    子进程里跑，不在测试里复制粘贴（防守卫-实现漂移）
  - 含反向证据: 退役的旧模式作用在同一样本上必须真的丢掉不变式（证样本是真血案，
    非 tautology）
"""

import io
import os
import re
import subprocess
import sys
import unittest
import contextlib

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
GOV_CRON_SH = os.path.join(REPO_ROOT, "governance_audit_cron.sh")
AUTO_DEPLOY_SH = os.path.join(REPO_ROOT, "auto_deploy.sh")

sys.path.insert(0, os.path.join(REPO_ROOT, "ontology"))

try:
    import governance_checker as gc
    _HAS_GC = True
except Exception:  # pragma: no cover - 依赖边界（PyYAML 缺失等）
    _HAS_GC = False


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


SRC = _read(GOV_CRON_SH)


# ────────────────────────────── 共用工具 ──────────────────────────────

def _extract_grep_pattern(var):
    """从真实源码抽取某个变量的 grep -E 模式（不在测试里硬编码）。"""
    m = re.search(
        r'^%s=\$\(echo "\$GOV_OUTPUT" \| grep -E "([^"]*)"' % re.escape(var),
        SRC, re.M)
    assert m, "未在 governance_audit_cron.sh 找到 %s 的 grep -E 提取行" % var
    return m.group(1)


def _extract_helpers():
    """抽取真实 helper 函数源码（_line_count / _bounded_block）。"""
    parts = []
    for name in ("_line_count", "_bounded_block"):
        m = re.search(r"^%s\(\).*?^\}" % name, SRC, re.M | re.S)
        assert m, "未找到 helper %s" % name
        parts.append(m.group(0))
    return "\n".join(parts)


def _blood_lesson_output():
    """用**真渲染器**生成 2026-08-13 血案场景: 4 条不变式失败，
    第一条 check 多（吃配额），后面还有一条最关键的 + 一条 💥 执行出错。"""

    def mk(inv_id, name, checks, status="fail"):
        return {
            "id": inv_id, "name": name, "severity": "critical", "status": status,
            "declaration": name + " 的声明文本", "meta_rule": "MR-1",
            "checks": [{"name": c, "status": s, "message": m} for c, s, m in checks],
        }

    results = [
        mk("INV-AAA-001", "第一个失败不变式", [
            ("a1 通过", "pass", ""),
            ("a2 守卫", "fail", "断言失败: 期望 X 实际 Y"),
            ("a3 守卫", "fail", "文件缺少 marker"),
            ("a4 守卫", "fail", "计数不符"),
        ]),
        mk("INV-BBB-002", "第二个失败不变式", [
            ("b1 守卫", "fail", "regex 不匹配"),
            ("b2 守卫", "fail", "缺少 handler"),
        ]),
        mk("INV-CCC-003", "第三个失败不变式 (最关键)", [
            ("c1 生产契约", "fail", "生产路径断裂"),
        ]),
        mk("INV-DDD-004", "第四个 (执行出错)", [
            ("d1 runtime", "error", "TimeoutExpired"),
        ], status="error"),
    ]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        gc.print_results(results, json_mode=False)
    # 追加真实形态的 ⚠️ 段（MRD 元发现 + convergence 漂移，2 空格缩进）
    return buf.getvalue() + (
        "\n  ⚠️ [MRD-CRON-001] 每个 enabled system job 应有调度治理覆盖\n"
        "     3 个 job 未覆盖\n"
        "  ⚠️ [jobs_to_crontab] — declared=40 observed=37 missing=3 "
        "(drift_action=machine_sync)\n"
        "  💥 [providers_to_adapter] ⚠️  error: observer_failed\n"
    )


def _run_bash(script, stdin_text=""):
    return subprocess.run(["bash", "-c", script], input=stdin_text,
                          capture_output=True, text=True)


def _compose_alert(gov_output):
    """用**真源码**的模式 + helper 在真 bash 子进程里复现告警正文三段。

    Returns (top_block, detail_block, warn_block, rc)
    """
    helpers = _extract_helpers()
    p_top = _extract_grep_pattern("GOV_VIOLATIONS")
    p_detail = _extract_grep_pattern("GOV_VIOLATION_DETAIL")
    p_warn = _extract_grep_pattern("GOV_WARNINGS")
    script = """set -eEuo pipefail
set +E
%s
GOV_OUTPUT=$(cat)
TOP=$(echo "$GOV_OUTPUT" | grep -E "%s" || true)
DET=$(echo "$GOV_OUTPUT" | grep -E "%s" || true)
WRN=$(echo "$GOV_OUTPUT" | grep -E "%s" || true)
echo "===TOP==="
_bounded_block "$TOP" 12 "个不变式" || true
echo "===DETAIL==="
_bounded_block "$DET" 6 "行明细" || true
echo "===WARN==="
_bounded_block "$WRN" 6 "项警告" || true
echo "===END==="
""" % (helpers, p_top, p_detail, p_warn)
    r = _run_bash(script, gov_output)
    out = r.stdout
    def section(a, b):
        return out.split(a, 1)[1].split(b, 1)[0].strip("\n") if a in out else ""
    return (section("===TOP===\n", "===DETAIL==="),
            section("===DETAIL===\n", "===WARN==="),
            section("===WARN===\n", "===END==="),
            r.returncode)


# ───────────────────────── 1. 分层行为（血案回归）─────────────────────────

@unittest.skipUnless(_HAS_GC, "governance_checker 不可导入（PyYAML 依赖边界）")
class TestLayeringBehavior(unittest.TestCase):
    """真渲染器输出 → 真源码模式 → 真 bash: 血案场景每条失败不变式都必须活下来。"""

    @classmethod
    def setUpClass(cls):
        cls.gov_out = _blood_lesson_output()
        cls.top, cls.detail, cls.warn, cls.rc = _compose_alert(cls.gov_out)

    def test_bash_pipeline_survives_pipefail(self):
        """helper + 提取管道在 set -eEuo pipefail 下不得非零退出（SIGPIPE landmine）。"""
        self.assertEqual(self.rc, 0, "告警正文构造在 pipefail 下应 rc=0")

    def test_all_failing_invariants_reach_alert(self):
        """血案核心: 4 条失败不变式一条都不能静默消失。"""
        for inv in ("INV-AAA-001", "INV-BBB-002", "INV-CCC-003", "INV-DDD-004"):
            self.assertIn(inv, self.top,
                          "%s 必须出现在告警顶层段（旧版被明细挤掉）" % inv)

    def test_top_level_count_line_survives(self):
        """"一共几个"的顶层计数行必须在（旧版排在最末永远第一个被丢）。"""
        self.assertRegex(self.top, r"❌ \d+ 个不变式被违反",
                         "顶层计数行必须进告警正文")
        self.assertIn("💥 1 个检查执行出错", self.top)

    def test_error_class_invariant_survives_truncation(self):
        """V37.9.214 意图（💥 冷启动 flake 自报家门）在多失败时不得被截断打败。"""
        self.assertIn("💥", self.top)
        self.assertIn("INV-DDD-004", self.top)

    def test_detail_lines_excluded_from_top_block(self):
        """check 级明细（7 空格）不得混进顶层段占配额。"""
        for line in self.top.splitlines():
            if not line.strip():
                continue
            if line.startswith("（还有"):
                continue
            self.assertLessEqual(
                len(line) - len(line.lstrip(" ")), 3,
                "顶层段只应含 0-3 空格缩进的判定行, 实际: %r" % line)

    def test_detail_block_carries_check_lines(self):
        """明细并非丢弃, 而是单列成段（可行动信息不损失）。"""
        self.assertIn("a2 守卫", self.detail)
        self.assertIn("c1 生产契约", self.detail)

    def test_detail_truncation_leaves_a_trace(self):
        """明细超预算时必须显式说明还有多少 + 去哪看全量（V37.9.300 教训）。"""
        self.assertRegex(self.detail, r"（还有 \d+ 行明细未显示")
        self.assertIn("governance_audit.log", self.detail)

    def test_warnings_reach_the_alert_body(self):
        """一直被算出来却从不送达的 ⚠️（含 crontab 漂移 missing=3）必须进正文。"""
        self.assertIn("MRD-CRON-001", self.warn)
        self.assertIn("missing=3", self.warn)

    def test_convergence_error_line_not_duplicated_in_warnings(self):
        """`  💥 [spec] ⚠️ error:` 以 💥 开头已进顶层段, 不得在警告段重复。"""
        self.assertIn("providers_to_adapter", self.top)
        self.assertNotIn("providers_to_adapter", self.warn)

    def test_retired_pattern_would_have_dropped_invariants(self):
        """反向证据: 退役的旧模式作用在同一样本上必须真的丢掉不变式,
        证明本套件的血案样本是真的（不是恒真的 tautology）。"""
        lines = [l for l in self.gov_out.splitlines() if ("❌" in l or "💥" in l)]
        old_body = "\n".join(lines[:5])  # 旧版: grep -E "❌|💥" | head -5
        self.assertIn("INV-AAA-001", old_body)
        self.assertNotIn("INV-CCC-003", old_body,
                         "旧模式本应丢掉最关键的不变式（样本无效则守卫是空转）")
        self.assertNotIn("INV-DDD-004", old_body)
        self.assertNotRegex(old_body, r"❌ \d+ 个不变式被违反")


# ───────────────────────── 2. 有界渲染 helper ─────────────────────────

class TestBoundedBlockHelper(unittest.TestCase):
    """真 helper 源码在真 bash 子进程里的行为契约。"""

    def _call(self, block, max_n, what="个不变式"):
        script = """set -eEuo pipefail
set +E
%s
BLK=$(cat)
_bounded_block "$BLK" %d "%s" || true
""" % (_extract_helpers(), max_n, what)
        return _run_bash(script, block)

    def test_over_budget_emits_notice_with_exact_remainder(self):
        blk = "\n".join("  ❌ [INV-%03d] x" % i for i in range(1, 16))
        r = self._call(blk, 12)
        self.assertEqual(r.returncode, 0)
        self.assertIn("（还有 3 个不变式未显示", r.stdout)
        self.assertEqual(len([l for l in r.stdout.splitlines()
                              if l.startswith("  ❌")]), 12)

    def test_exact_fit_emits_no_notice(self):
        blk = "\n".join("  ❌ [INV-%03d] x" % i for i in range(1, 13))
        r = self._call(blk, 12)
        self.assertNotIn("还有", r.stdout)

    def test_empty_input_is_silent_and_succeeds(self):
        r = self._call("", 12)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_line_count_zero_for_empty(self):
        script = """set -eEuo pipefail
set +E
%s
_line_count ""
_line_count "a
b
c"
""" % _extract_helpers()
        r = _run_bash(script)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.split(), ["0", "3"])


# ───────────────────── 3. 不新增推送量（不制造噪声）─────────────────────

class TestNoNewAlertVolume(unittest.TestCase):
    """治的病是告警疲劳 —— 修复本身绝不能新增日常推送。"""

    def test_push_still_gated_on_fail_only(self):
        self.assertRegex(
            SRC, r'if \[ "\$OVERALL" = "fail" \] && \$NOTIFY_LOADED; then',
            "推送门槛必须仍是 OVERALL=fail（warn-only 干净轮次不得新开推送）")

    def test_warnings_appended_only_when_already_alerting(self):
        """警告附进正文的分支必须嵌在 OVERALL=fail 判断内。"""
        m = re.search(r'if \[ -n "\$GOV_WARNINGS" \]; then(.*?)\nfi\n', SRC, re.S)
        self.assertIsNotNone(m, "GOV_WARNINGS 处理块必须存在")
        blk = m.group(1)
        self.assertIn('if [ "$OVERALL" = "fail" ]', blk,
                      "并存警告只在告警已经触发时附加")
        self.assertIn("ALERT_MSG", blk, "警告必须真的进 ALERT_MSG（旧版只 log 了计数）")

    def test_retired_misleading_comment_claim(self):
        """旧注释写"附加到报告"但代码从没附加过 —— 该声明必须已兑现或退役。"""
        self.assertNotIn("# 元发现警告（不阻断，但附加到报告）", SRC,
                         "旧的名不副实注释必须退役")


# ───────────────────────── 4. 源码守卫 / 血统 ─────────────────────────

class TestSourceGuards(unittest.TestCase):

    def test_marker(self):
        self.assertIn("V37.9.301", SRC, "血案 marker 必须在源码留痕")

    def test_old_head5_antipattern_retired(self):
        self.assertNotRegex(
            SRC, r'GOV_VIOLATIONS=\$\(echo "\$GOV_OUTPUT" \| grep -E "❌\|💥" \| head -5',
            "V37.9.301: 旧的 `grep -E \"❌|💥\" | head -5` 必须退役")
        self.assertNotRegex(
            SRC, r'GOV_WARNINGS=\$\(echo "\$GOV_OUTPUT" \| grep "⚠️" \| head -5',
            "V37.9.301: 旧的 GOV_WARNINGS head -5 必须退役")

    def test_v37_9_214_lineage_preserved(self):
        """💥（error 类）仍必须被抓 —— 不得在重构里丢掉 V37.9.214 的修复。"""
        pat = _extract_grep_pattern("GOV_VIOLATIONS")
        self.assertIn("💥", pat)
        self.assertIn("❌", pat)

    def test_variable_names_preserved_for_cross_file_pins(self):
        """test_cron_monitor_scanner 按名字 pin 这三个变量, 重命名会静默破坏它。"""
        for var in ("GOV_SUMMARY", "GOV_VIOLATIONS", "GOV_WARNINGS"):
            self.assertIn(var, SRC)

    def test_new_pipelines_have_pipefail_guard(self):
        """本脚本 set -eEuo pipefail: 每条新增提取管道都要 `|| true`。"""
        for var in ("GOV_VIOLATIONS", "GOV_VIOLATION_DETAIL", "GOV_WARNINGS"):
            m = re.search(r'^%s=\$\(echo "\$GOV_OUTPUT" \| grep -E "[^"]*" \|\| true\)'
                          % var, SRC, re.M)
            self.assertIsNotNone(m, "%s 提取行必须带 || true 兜底" % var)

    def test_same_layering_rule_as_auto_deploy(self):
        """一物一形: 两个告警正文用同一套"顶层 0-3 空格"分层判据（V37.9.300 家族）。"""
        auto = _read(AUTO_DEPLOY_SH)
        self.assertIn("^ {0,3}", auto, "V37.9.300 的分层判据应仍在 auto_deploy")
        self.assertIn("^ {0,3}", _extract_grep_pattern("GOV_VIOLATIONS"),
                      "治理告警必须用同款顶层缩进判据")

    def test_shell_syntax_ok(self):
        r = subprocess.run(["bash", "-n", GOV_CRON_SH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

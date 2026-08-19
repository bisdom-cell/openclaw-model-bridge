#!/usr/bin/env python3
"""V37.9.322 守卫 — 对抗审计「哪个 check 在该报警时打绿」三条缺陷。

镜头承接 V37.9.320 (证据与防护机制自身的 fail-plausible)，落在 preflight_check.sh
这个**操作者的眼睛**上 —— 它 86 个 check 从没被系统性审过，而 V37.9.319 刚在它的
KB 段抓到「硬编码 100%」+「四舍五入」两个假安心。

F1 (MED) slo_checker 不区分「达标」与「无数据可判」
    0 次工具调用 / 0 次失败恢复 = **从未被测量过**的指标，被当满分通过计入
    「✅ SLO 全部达标」。同一份 proxy_stats 喂两个消费者：slo_benchmark 报
    「3/5 passed, 2 n/a」，slo_checker 报「5/5 全部达标」，而 preflight 与
    job_watchdog 读的是乐观的那个。V37.9.303 修了两个渲染器、漏了这个判定器
    (原则 #31 跨消费者未全量同步)。

F2 (LOW, 日落法) ProxyStats.get_slo_status() 死代码退役
    零生产消费者 + SLO 计算的第三份拷贝(已与两个活写点漂移) + 内含错单位健康布尔
    (毫秒 <= 上下文 token 上限，注释自称 placeholder 但从未替换)。

F3 (MED) preflight 安全扫描「跑不动」读作「无泄漏」
    git grep rc=1(无匹配) 与 rc>=2(非 git 仓库/git 缺失/index 损坏) 被压成同一个
    空字符串 → 打绿 ✅。实测: 非 git 目录 + 目录里真有一把 sk- key → 仍 ✅。
    V37.9.310「缺工具伪装成否定结果」同族，落在 push 前的安全闸门上。

守卫纪律: 行为级优先(真 producer / 真 CLI / 真 bash 块)，源码断言只做反模式退役，
且**先剥注释行**再断言 —— 本文件涉及的三处修复注释里都写着被退役的字面量
(get_slo_status / latency_p95_ok / SLO 全部达标)，不剥就会被自己咬 (V37.9.178 家族)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import proxy_filters  # noqa: E402
import slo_benchmark  # noqa: E402
import slo_checker  # noqa: E402
from config_loader import load_config  # noqa: E402


def _executable_lines(path, comment="#"):
    """源码去掉纯注释行 —— 守卫不该被自己的血案注释咬 (V37.9.178 家族)。"""
    out = []
    for ln in open(os.path.join(REPO, path), encoding="utf-8"):
        if ln.lstrip().startswith(comment):
            continue
        out.append(ln)
    return "".join(out)


def _fresh_producer_stats():
    """真 producer 的新起快照 (刚重启的 proxy)。

    刻意不用手写 fixture —— V37.9.299 教训: fake 偏离 producer schema 会让整套
    测试对 key 漂移失明。
    """
    return proxy_filters.ProxyStats().get_stats_dict()


def _production_shape_stats():
    """复刻 V37.9.315 生产 E2E 实测形状: 60 请求 / 0 工具调用 / 0 失败 streak。

    用真 producer 逐步喂出来, 不手写 —— 同上。
    """
    ps = proxy_filters.ProxyStats()
    for _ in range(60):
        ps.record_success({"prompt_tokens": 100}, latency_ms=11903)
    return ps.get_stats_dict()


def _fully_measured_stats(timeout_rate_pct=0.5):
    """全部样本域非空的健康数据 (回归对照)。"""
    ps = proxy_filters.ProxyStats()
    for _ in range(50):
        ps.record_success({"prompt_tokens": 100}, latency_ms=900)
    for _ in range(20):
        ps.record_tool_call(success=True)
    # 制造一次真 failure streak 再恢复 → failure_streaks > 0。
    # 注意 producer 语义: `_failure_streaks` 只在 consecutive_errors **首次达到**
    # CONSECUTIVE_ERROR_ALERT 阈值时 +1 (proxy_filters:1412), 单次瞬态错误不算 streak
    # —— 探针必须按 producer 真实语义构造, 不能想当然 (V37.9.299 教训)。
    for _ in range(proxy_filters.CONSECUTIVE_ERROR_ALERT):
        ps.record_error(502, "boom", latency_ms=100)
    ps.record_success({"prompt_tokens": 100}, latency_ms=900)
    stats = ps.get_stats_dict()
    stats["slo"]["timeout_rate_pct"] = timeout_rate_pct
    return stats


class TestF1NaSemantics(unittest.TestCase):
    """F1 — 「无数据可判」必须与「达标」分开 (行为级, 真 producer)。"""

    def setUp(self):
        self.cfg = load_config()

    def test_blood_lesson_production_shape_two_na(self):
        """血案回归: 60 请求/0 工具/0 streak → 2 项 n/a, 不得算作达标。"""
        res, all_ok = slo_checker.check_slo(_production_shape_stats(), self.cfg)
        judged, na, na_names = slo_checker.judged_summary(res)
        self.assertEqual(na, 2, f"应有 2 项无数据可判, 实际 {na}: {na_names}")
        self.assertEqual(sorted(na_names), ["auto_recovery_rate", "tool_success_rate"])
        self.assertEqual(judged, 3)

    def test_recovery_sentinel_not_counted_as_pass(self):
        """producer 在 streaks==0 时写死 100.0 是哨兵值, 不是测量值。"""
        stats = _production_shape_stats()
        self.assertEqual(stats["slo"]["failure_streaks"], 0)
        self.assertEqual(stats["slo"]["auto_recovery_rate_pct"], 100.0)
        res, _ = slo_checker.check_slo(stats, self.cfg)
        rec = next(r for r in res if r["name"] == "auto_recovery_rate")
        self.assertTrue(rec["n_a"], "0 次失败 streak 时 100% 恢复率必须标 n/a")
        self.assertEqual(rec["samples"], 0)

    def test_fresh_proxy_nothing_judged(self):
        """刚重启的 proxy: 5 项全部无数据 → judged_count == 0。"""
        res, all_ok = slo_checker.check_slo(_fresh_producer_stats(), self.cfg)
        judged, na, _ = slo_checker.judged_summary(res)
        self.assertEqual(judged, 0)
        self.assertEqual(na, 5)

    def test_fully_measured_has_no_na(self):
        """回归对照: 样本域全非空时不得凭空产生 n/a。"""
        res, all_ok = slo_checker.check_slo(_fully_measured_stats(), self.cfg)
        judged, na, na_names = slo_checker.judged_summary(res)
        self.assertEqual(na, 0, f"不应有 n/a, 实际: {na_names}")
        self.assertEqual(judged, 5)
        self.assertTrue(all_ok)

    def test_real_violation_still_alerts(self):
        """回归对照: 真违规仍产出告警文本 (n/a 机制不得钝化告警)。"""
        res, all_ok = slo_checker.check_slo(_fully_measured_stats(timeout_rate_pct=9.0), self.cfg)
        self.assertFalse(all_ok)
        self.assertIn("timeout_rate", slo_checker.format_alert(res))

    def test_ok_semantics_unchanged_for_na(self):
        """🔴 n/a 的 metric 仍 ok=True —— watchdog 告警噪声零增量。

        (镜像 V37.9.320 AL-2 纪律: 不改 ok 语义, 只让呈现层诚实。若把 n/a 判成
        违规, job_watchdog 会对「今天没有工具调用」告警 = 正在治的病本身。)
        """
        for stats in (_fresh_producer_stats(), _production_shape_stats()):
            res, all_ok = slo_checker.check_slo(stats, self.cfg)
            self.assertTrue(all_ok)
            self.assertEqual(slo_checker.format_alert(res), "")
            for r in res:
                if r["n_a"]:
                    self.assertTrue(r["ok"], f"{r['name']} n/a 不得变成违规")

    def test_missing_failure_streaks_fail_open(self):
        """旧 schema (V37.9.229 前) 无 failure_streaks 字段 → 按可判处理, 保持旧行为。"""
        stats = _fully_measured_stats()
        del stats["slo"]["failure_streaks"]
        res, _ = slo_checker.check_slo(stats, self.cfg)
        rec = next(r for r in res if r["name"] == "auto_recovery_rate")
        self.assertIsNone(rec["samples"])
        self.assertFalse(rec["n_a"], "样本域不可得时应 FAIL-OPEN 按可判, 不是 n/a")

    def test_every_metric_declares_sample_domain(self):
        """5 个 metric 都必须带 samples 字段 —— 「没有样本概念」正是默认值冒充达标的根。"""
        res, _ = slo_checker.check_slo(_fully_measured_stats(), self.cfg)
        self.assertEqual(len(res), 5)
        for r in res:
            self.assertIn("samples", r)
            self.assertIn("n_a", r)
            self.assertIn("n_a_reason", r)
            if r["n_a"]:
                self.assertTrue(r["n_a_reason"], f"{r['name']} n/a 必须给出理由")
            else:
                self.assertEqual(r["n_a_reason"], "")


class TestF1CrossConsumerParity(unittest.TestCase):
    """F1 — 与 slo_benchmark 的 N/A 判据同源 (MR-8 drift guard)。

    两个消费者对**同一份 producer 数据**必须在这两条共享规则上一致:
      tool_calls_total == 0  → N_A_NO_TOOL_CALLS      (benchmark) / n_a (checker)
      failure_streaks == 0   → N_A_NO_FAILURE_STREAKS (benchmark) / n_a (checker)
    任一侧改判据而另一侧没跟, 本守卫立即 fail —— 这正是 V37.9.303 漏掉 checker
    所造成的口径分裂。
    """

    def setUp(self):
        self.cfg = load_config()

    def _pair(self, stats):
        rep = slo_benchmark.build_report(stats, self.cfg)
        res, _ = slo_checker.check_slo(stats, self.cfg)
        by_name = {r["name"]: r for r in res}
        return rep, by_name

    def test_no_tool_calls_both_na(self):
        rep, by = self._pair(_production_shape_stats())
        self.assertEqual(rep["tools"]["verdict"], "N_A_NO_TOOL_CALLS")
        self.assertTrue(by["tool_success_rate"]["n_a"])

    def test_no_failure_streaks_both_na(self):
        rep, by = self._pair(_production_shape_stats())
        self.assertEqual(rep["recovery"]["verdict"], "N_A_NO_FAILURE_STREAKS")
        self.assertTrue(by["auto_recovery_rate"]["n_a"])

    def test_measured_case_neither_na(self):
        rep, by = self._pair(_fully_measured_stats())
        self.assertFalse(rep["tools"]["verdict"].startswith("N_A"))
        self.assertFalse(rep["recovery"]["verdict"].startswith("N_A"))
        self.assertFalse(by["tool_success_rate"]["n_a"])
        self.assertFalse(by["auto_recovery_rate"]["n_a"])


class TestF1CliContract(unittest.TestCase):
    """F1 — 退出码与 --alert 摘要 (真 CLI subprocess, HOME 隔离不碰生产状态 MR-9)。"""

    def _run(self, stats, args=("--alert",)):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, "proxy_stats.json"), "w") as f:
                json.dump(stats, f)
            env = dict(os.environ, HOME=home)
            p = subprocess.run([sys.executable, "slo_checker.py", *args],
                               cwd=REPO, env=env, capture_output=True, text=True)
            return p.returncode, p.stdout

    def test_exit_3_when_nothing_judged(self):
        rc, out = self._run(_fresh_producer_stats())
        self.assertEqual(rc, 3, f"无数据可判应 exit 3, got {rc}: {out}")
        self.assertIn("无数据可判", out)

    def test_exit_0_prints_measured_summary(self):
        rc, out = self._run(_production_shape_stats())
        self.assertEqual(rc, 0)
        self.assertIn("3/5", out)
        self.assertIn("无数据不可判", out)
        # 血案核心: 不得再输出一个不带分母的「全部达标」
        self.assertNotIn("SLO 全部达标", out)

    def test_exit_0_all_measured_says_so(self):
        rc, out = self._run(_fully_measured_stats())
        self.assertEqual(rc, 0)
        self.assertIn("5/5", out)

    def test_exit_2_on_violation_unchanged(self):
        rc, out = self._run(_fully_measured_stats(timeout_rate_pct=9.0))
        self.assertEqual(rc, 2, "真违规必须仍 exit 2 (watchdog 告警路径)")
        self.assertIn("timeout_rate", out)

    def test_json_mode_exposes_counts(self):
        rc, out = self._run(_production_shape_stats(), args=())
        data = json.loads(out)
        self.assertEqual(data["judged_count"], 3)
        self.assertEqual(data["na_count"], 2)

    def test_no_traffic_json_mode_also_exit_3(self):
        rc, _ = self._run(_fresh_producer_stats(), args=())
        self.assertEqual(rc, 3)


class TestF1PreflightWiring(unittest.TestCase):
    """F1 — preflight 19/19 必须打印实测摘要, 不再硬编码断言。"""

    def setUp(self):
        self.exec_src = _executable_lines("preflight_check.sh")

    def test_renders_measured_summary(self):
        self.assertIn('pass "${SLO_OUT:-SLO 全部达标}"', self.exec_src)

    def test_retired_hardcoded_claim(self):
        """退役反模式: 不看实测结果就断言「SLO 全部达标」(V37.9.319 同一 bug 类)。"""
        self.assertNotIn('pass "SLO 全部达标"', self.exec_src)

    def test_has_exit_3_branch(self):
        self.assertRegex(self.exec_src, r"elif \[ \$SLO_RC -eq 3 \]; then")

    def test_exit_3_warns_not_passes(self):
        """无数据可判必须走 warn —— 镜像 V37.9.320 审计日志「⚠️ 无可校验」。"""
        blk = self.exec_src[self.exec_src.find("elif [ $SLO_RC -eq 3 ]; then"):]
        blk = blk[:blk.find("else")]
        self.assertIn("warn ", blk)
        self.assertNotIn("pass ", blk)

    def test_guard_not_vacuous(self):
        """防空转: 被扫的确实是 SLO 段。"""
        self.assertIn("SLO_RC", self.exec_src)
        self.assertIn("slo_checker.py", self.exec_src)


class TestF2DeadCodeRetired(unittest.TestCase):
    """F2 — 零消费者的 SLO 第三份拷贝 + 错单位健康布尔已退役 (日落法)。"""

    def setUp(self):
        self.exec_src = _executable_lines("proxy_filters.py")

    def test_method_gone(self):
        self.assertNotIn("def get_slo_status", self.exec_src)
        self.assertFalse(hasattr(proxy_filters.ProxyStats, "get_slo_status"))

    def test_wrong_unit_health_boolean_gone(self):
        """毫秒 <= 上下文 token 上限 —— 一个等着被接线的 fail-plausible。"""
        self.assertNotIn("latency_p95_ok", self.exec_src)

    def test_live_path_still_complete(self):
        """退役不得削弱活路径: 两个活写点仍供全部 SLO 字段 + V37.9.229 原始计数。"""
        slo = _fully_measured_stats()["slo"]
        for k in ("latency", "tool_success_rate_pct", "degradation_rate_pct",
                  "timeout_rate_pct", "auto_recovery_rate_pct",
                  "tool_calls_success", "fallback_count", "recovery_total",
                  "failure_streaks"):
            self.assertIn(k, slo)

    def test_guard_not_vacuous(self):
        self.assertIn("def get_stats_dict", self.exec_src)


class TestF3SecurityScanExecutability(unittest.TestCase):
    """F3 — 安全扫描「跑不动」必须响, 不得读作「无泄漏」(行为级真 bash)。"""

    @classmethod
    def setUpClass(cls):
        """从 preflight 真源码抽出扫描块 —— 不 hardcode, 防守卫-实现漂移。"""
        src = open(os.path.join(REPO, "preflight_check.sh"), encoding="utf-8").read().split("\n")
        start = next(i for i, l in enumerate(src) if l.strip() == "GG_RC=0")
        end = None
        seen_pass = False
        for i in range(start, len(src)):
            if "无 API Key 泄漏" in src[i]:
                seen_pass = True
            if seen_pass and src[i].strip() == "fi":
                end = i
                break
        assert end is not None, "未能从 preflight 抽出安全扫描块"
        cls.block = "\n".join(src[start:end + 1])
        # 防空转: 抽到的确实是扫描块
        assert "git grep" in cls.block and "sk-[A-Za-z0-9]" in cls.block, cls.block[:200]

    def _run_block(self, cwd):
        script = ("set -euo pipefail\n"
                  'PASS=0; FAIL=0\n'
                  'pass() { echo "PASS:$1"; PASS=$((PASS+1)); }\n'
                  'fail() { echo "FAIL:$1"; FAIL=$((FAIL+1)); }\n'
                  + self.block + "\n"
                  'echo "COUNTS $PASS $FAIL"\n')
        p = subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def test_blood_lesson_broken_scanner_must_fail(self):
        """血案回归: 非 git 目录 + 目录里真有一把 key → 必须 FAIL (旧版打 ✅)。"""
        with tempfile.TemporaryDirectory() as d:
            # 运行时拼接: 源码里不留完整 key 字面量, 否则 preflight 自己的
            # `git grep` 扫描 (不排除 test_ 文件) 会把本守卫当成真泄漏报红。
            fake_key = "sk-" + "abcdefghijklmnopqrstuv"
            with open(os.path.join(d, "leak.py"), "w") as f:
                f.write(f'KEY = "{fake_key}"\n')
            rc, out = self._run_block(d)
            self.assertIn("FAIL:", out, f"扫描器失效必须响, got: {out}")
            self.assertIn("扫描器失效不等于无泄漏", out)
            self.assertNotIn("PASS:无 API Key 泄漏", out)

    def test_healthy_repo_no_false_alarm(self):
        """回归对照: 在真仓库里不得误报 (否则每次 preflight 都红)。"""
        rc, out = self._run_block(REPO)
        self.assertIn("PASS:无 API Key 泄漏", out, out)
        self.assertNotIn("FAIL:", out)

    def test_set_e_safe_no_and_assignment(self):
        """set -euo pipefail 下 `[ cond ] && VAR=x` 条件为假会杀脚本 (INV-CROSS-OS-001)。"""
        self.assertNotRegex(self.block, r'^\s*\[ "\$GG_RC" -ge 2 \] &&')

    def test_all_three_scans_go_through_helper(self):
        """MR-8: sk / BSA / 手机号三处收编同一 helper, 判据只有一处。"""
        exec_src = _executable_lines("preflight_check.sh")
        self.assertEqual(exec_src.count("git_grep_scan "), 3,
                         "三处扫描都必须经 helper")
        self.assertEqual(exec_src.count("git_grep_scan()"), 1,
                         "helper 只能有一份定义")

    def test_retired_bare_git_grep_swallow(self):
        """退役反模式: `git grep ... || true` 把 rc>=2 与 rc=1 压成同一个空串。"""
        exec_src = _executable_lines("preflight_check.sh")
        self.assertNotRegex(exec_src, r'=\$\(git grep -n[^\n]*\|\| true\)')

    def test_phone_scan_also_guarded(self):
        exec_src = _executable_lines("preflight_check.sh")
        self.assertIn("手机号扫描无法执行", exec_src)


class TestSourceMarkers(unittest.TestCase):
    def test_markers_present(self):
        for path in ("slo_checker.py", "preflight_check.sh", "proxy_filters.py"):
            self.assertIn("V37.9.322", open(os.path.join(REPO, path), encoding="utf-8").read(),
                          f"{path} 缺 V37.9.322 marker")


if __name__ == "__main__":
    unittest.main(verbosity=2)

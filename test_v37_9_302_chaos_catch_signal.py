#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.302 — 混沌审计 catch 判据逐信号守卫（守卫之守卫的度量 bug）

血案（2026-08-13，受控加载实验复现，非推测）:
    adversarial_chaos_audit.run_scenario 旧判据 `caught = sum(mutated) > sum(baseline)`
    把**检出信号**与**环境噪声**加总相消。实测一次 C8 失败的真实数字:

        baseline={'fail': 0, 'error': 2, 'mrd_warn': 4, 'mrd_violations': 56}
        mutated ={'fail': 1, 'error': 2, 'mrd_warn': 3, 'mrd_violations': 55}   sum delta = -1

    mutation 其实被抓到了（fail 0→1 = 不变式由过转挂 = 设计的检出信号），但同轮无关
    MRD 计数器在负载下掉 2 → 求和后总数下降 → 审计报 "BLIND SPOT"。

    症状: full_regression 尾部近乎每次 9/10 且**每次失败场景都不同**
    （C1 2026-08-10 / C6 与 C8 2026-08-13），standalone 复跑必 10/10。
    隔离静态连跑 6 次 governance 计数器完全稳定（60/60/…）→ 噪声来自负载而非 governance。

    这是守卫之守卫自己的 fail-plausible: 一份说"防御退化了"的报告，而防御其实生效了。

修复: 逐信号比较（任一计数器上升即"抓到"），刻意不引入重试/inconclusive 机器
（日落法 #34: 根因修好后那层机器就不需要了）。
"""

import importlib.util
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CHAOS_PATH = os.path.join(REPO_ROOT, "ontology", "tests", "adversarial_chaos_audit.py")


def _load_chaos():
    spec = importlib.util.spec_from_file_location("chaos_audit_under_test", CHAOS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chaos = _load_chaos()

# 2026-08-13 受控加载实验里 C8 真实观测到的两组计数（逐字保留，不美化）
BLOOD_BASELINE = {"fail": 0, "error": 2, "mrd_warn": 4, "mrd_violations": 56}
BLOOD_MUTATED = {"fail": 1, "error": 2, "mrd_warn": 3, "mrd_violations": 55}

# 同日 C6 静态成功那次（多信号同时上升）
C6_BASELINE = {"fail": 0, "error": 2, "mrd_warn": 3, "mrd_violations": 55}
C6_MUTATED = {"fail": 0, "error": 2, "mrd_warn": 5, "mrd_violations": 57}


class TestCatchSignalsBloodLesson(unittest.TestCase):
    """血案回归: 求和为负但存在真检出信号时，必须判为"抓到"。"""

    def test_blood_lesson_sum_is_negative(self):
        """先证样本是真血案: 旧的求和判据在这组数字上确实判 not-caught。"""
        self.assertLess(sum(BLOOD_MUTATED.values()), sum(BLOOD_BASELINE.values()),
                        "样本必须是求和下降的（否则本套件是空转）")

    def test_blood_lesson_detected_by_per_signal(self):
        risen = chaos.catch_signals(BLOOD_BASELINE, BLOOD_MUTATED)
        self.assertIn("fail", risen, "fail 0→1 是设计的检出信号, 必须被识别为抓到")
        self.assertTrue(risen)

    def test_noise_drop_does_not_mask_signal(self):
        """噪声计数器下降不得抵消真信号（血案机制本身）。"""
        risen = chaos.catch_signals(BLOOD_BASELINE, BLOOD_MUTATED)
        self.assertNotIn("mrd_warn", risen)
        self.assertNotIn("mrd_violations", risen)
        self.assertEqual(risen, ["fail"])

    def test_c6_multi_signal_rise_still_caught(self):
        risen = chaos.catch_signals(C6_BASELINE, C6_MUTATED)
        self.assertEqual(risen, ["mrd_violations", "mrd_warn"])


class TestCatchSignalsSemantics(unittest.TestCase):
    """判据本身的边界: 真盲区仍必须 FAIL（检出力不得被削弱）。"""

    def test_true_blind_spot_no_rise(self):
        same = {"fail": 0, "error": 2, "mrd_warn": 3, "mrd_violations": 55}
        self.assertEqual(chaos.catch_signals(same, dict(same)), [],
                         "零计数器上升 = 真盲区, 必须仍判未抓到")

    def test_all_counters_drop_is_not_a_catch(self):
        lower = {"fail": 0, "error": 1, "mrd_warn": 2, "mrd_violations": 50}
        self.assertEqual(chaos.catch_signals(C6_BASELINE, lower), [])

    def test_single_counter_rise_is_enough(self):
        for key in ("fail", "error", "mrd_warn", "mrd_violations"):
            mutated = dict(C6_BASELINE)
            mutated[key] += 1
            self.assertEqual(chaos.catch_signals(C6_BASELINE, mutated), [key])

    def test_missing_key_treated_as_zero(self):
        self.assertEqual(chaos.catch_signals({"fail": 0}, {}), [])
        self.assertEqual(chaos.catch_signals({"fail": 0}, {"fail": 1}), ["fail"])

    def test_pure_function_does_not_mutate_inputs(self):
        b, m = dict(BLOOD_BASELINE), dict(BLOOD_MUTATED)
        chaos.catch_signals(b, m)
        self.assertEqual(b, BLOOD_BASELINE)
        self.assertEqual(m, BLOOD_MUTATED)


class TestRunScenarioWiring(unittest.TestCase):
    """run_scenario 必须真的走逐信号判据（而不是留着求和判据）。"""

    def setUp(self):
        with open(CHAOS_PATH, encoding="utf-8") as f:
            self.src = f.read()

    def test_wired_to_catch_signals(self):
        self.assertIn("risen = catch_signals(", self.src)
        self.assertIn("caught_by_mutation = bool(risen)", self.src)

    def test_old_sum_based_predicate_retired(self):
        self.assertNotIn("caught_by_mutation = delta > 0", self.src,
                         "V37.9.302: 求和判据必须退役")

    def test_delta_still_reported_for_diagnostics(self):
        """delta 仍要报（诊断价值），只是不再作判据。"""
        self.assertIn("delta = mutated_total - baseline_total", self.src)
        self.assertIn('"delta": delta', self.src)

    def test_reason_names_the_risen_signals(self):
        """报告要说清"哪个信号动了"（今日主题: 报告要让人看得懂）。"""
        self.assertIn("signals ↑", self.src)

    def _run_scenario_body(self):
        import re as _re
        m = _re.search(r"^def run_scenario\(.*?(?=^def )", self.src, _re.M | _re.S)
        self.assertIsNotNone(m, "未找到 run_scenario 函数体")
        return m.group(0)

    def test_no_retry_machinery_added(self):
        """日落法 #34: 根因修好, 刻意不加重试那层机器 —— run_scenario 内仍恰好
        两次 governance 调用（baseline + mutated），不是"失败再跑一遍"。

        注: 全文件另有既有的 retry 相关**场景**（改 adapter.py 重试常量）与既有的
        INCONCLUSIVE 图标映射, 与本次修复无关, 故守卫收敛到 run_scenario 函数体内
        （V37.9.178 教训: 守卫别被无关文本咬）。"""
        body = self._run_scenario_body()
        self.assertEqual(body.count("run_governance()"), 2,
                         "run_scenario 应恰好跑 baseline + mutated 两次, 无重试")
        self.assertNotIn("INCONCLUSIVE", body,
                         "不引入 inconclusive 分支（根因已修, 该层机器不需要）")

    def test_marker(self):
        self.assertIn("V37.9.302", self.src)

    def test_module_compiles(self):
        r = subprocess.run([sys.executable, "-m", "py_compile", CHAOS_PATH],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

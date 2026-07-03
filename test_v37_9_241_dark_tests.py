#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.241 — dark-test 对账守卫：全部测试文件必须注册 full_regression。

血案谱系（"未注册测试必然腐烂" bug 类，4 次演出达机器化门槛）:
  1. V37.9.130: test_harvest_chat.py 从 V37.1 起从未注册（28 用例暗跑 2 个月）。
  2. V37.9.241: test_data_clean.py 从 V30.3 起从未注册（80 用例，幸运仍绿）。
  3. V37.9.241: ontology/tests/test_engine.py 从未注册 → V37.9.91 加 expert_escalate
     后 3 个期望腐烂（2→3 / 18→19）近 1 个月无人知。
  4. V37.9.241: ontology/tests/test_notify_activity.py 从未注册 → 代码改
     _MRD["registry_file"] 注入后 literal 守卫 stale 无人知。

机制: 枚举 repo 测试文件全集（根目录 test_*.py + ontology/tests/test_*.py），
对账 full_regression.sh 的两种注册形式（文件名 `test_X.py` / 模块名
`ontology.tests.test_X`）。任何新测试文件漏注册 → 本守卫立即 FAIL（CI 内自指:
本文件也在全集里，必须注册自己）。

豁免清单 EXEMPT 显式登记刻意不进回归的文件（当前为空——加豁免须附理由）。
"""
import os
import re
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))

# 刻意不注册 full_regression 的测试文件（basename, 不含 .py）。加条目必须附理由注释。
EXEMPT = set()


def _all_test_modules():
    """repo 测试文件全集 → module-name 集合（不含 .py）。"""
    names = set()
    for d in (_REPO, os.path.join(_REPO, "ontology", "tests")):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.startswith("test_") and f.endswith(".py"):
                names.add(f[:-3])
    return names


def _registered_modules():
    """full_regression.sh 中已注册的测试 → module-name 集合（两种形式统一）。"""
    with open(os.path.join(_REPO, "full_regression.sh"), encoding="utf-8") as f:
        src = f.read()
    reg = set(m[:-3] for m in re.findall(r"test_[a-z0-9_]+\.py", src))
    reg |= set(m.rsplit(".", 1)[-1]
               for m in re.findall(r"ontology\.tests\.test_[a-z0-9_]+", src))
    return reg


class TestDarkTestReconciliation(unittest.TestCase):
    def test_every_test_file_registered(self):
        """任何测试文件（除显式豁免）必须注册 full_regression（防暗测试腐烂）。"""
        dark = _all_test_modules() - _registered_modules() - EXEMPT
        self.assertEqual(
            sorted(dark), [],
            "暗测试（未注册 full_regression → 期望会静默腐烂, 见本文件血案谱系）: "
            "%s — 注册进 full_regression.sh 或加 EXEMPT 附理由" % sorted(dark))

    def test_exempt_entries_still_exist(self):
        """豁免清单不得含幽灵条目（文件已删则清豁免）。"""
        ghosts = EXEMPT - _all_test_modules()
        self.assertEqual(sorted(ghosts), [])

    def test_formerly_dark_three_registered(self):
        """V37.9.241 三个暗测试必须保持注册（防回退）。"""
        reg = _registered_modules()
        for m in ("test_data_clean", "test_engine", "test_notify_activity"):
            self.assertIn(m, reg, "%s 又变暗了" % m)

    def test_engine_stale_expectations_fixed(self):
        """V37.9.91 腐烂期望已修（3 custom tools / 19 total）。"""
        with open(os.path.join(_REPO, "ontology", "tests", "test_engine.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("self.assertEqual(len(self.onto.custom_tools), 3)", src)
        self.assertIn("expert_escalate", src)
        self.assertNotIn("self.assertEqual(len(result), 18)", src)

    def test_scan_is_not_vacuous(self):
        """对账全集非空且覆盖两个目录（防扫描器自身瘫痪 = vacuous pass）。"""
        mods = _all_test_modules()
        self.assertGreater(len(mods), 100, "全集异常小: %d" % len(mods))
        self.assertIn("test_tool_proxy", mods)          # 根目录
        self.assertIn("test_three_gate", mods)          # ontology/tests
        self.assertIn("test_v37_9_241_dark_tests", mods)  # 自指: 本文件在全集里


if __name__ == "__main__":
    unittest.main(verbosity=2)

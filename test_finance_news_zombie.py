#!/usr/bin/env python3
"""test_finance_news_zombie.py — V37.8.5 三层僵尸检测单测

V37.8.5 血案背景：V37.8.4 首次引入僵尸检测，收工 Mac Mini E2E 验证时
用户实际发现两个边缘盲区：
  - CNS1952: 98/99 超窗口 + 1 过短 → old/total=99% 未触发 old==total
  - SingTaoDaily: 0 tweets stub → total=0 绕过 total>0 门槛

本测试锁定 V37.8.5 的三层检测合约，并防止三类回归：
  1. 向后兼容：V37.8.4 原 100% 用例必须仍被捕获（stale tier）
  2. 新盲区闭合：CNS1952 99% + SingTaoDaily 0-tweet 必须被捕获
  3. 守卫生效：80% 老化 / 有 1 条新鲜内容 / HTTP 异常 都不误报

MR-10 (understand-before-fix) 正向兑现：
  上次血案是"V37.8.3 改 handle 没验证活跃度"，本次更上游：
  V37.8.4 修复本身埋的盲区，次日即被 E2E 发现。
  每次"修复"都可能制造下一个 bug。测试 = 修复契约的持久化。
"""
import os
import subprocess
import sys
import tempfile
import unittest

# 把 jobs/finance_news/ 挂进 sys.path 以导入被测模块
_HERE = os.path.dirname(os.path.abspath(__file__))
_JOBS_DIR = os.path.join(_HERE, "jobs", "finance_news")
sys.path.insert(0, _JOBS_DIR)

from finance_news_zombie import classify_zombie, ZOMBIE_STALE_NUM, ZOMBIE_STALE_DEN


# ══════════════════════════════════════════════════════════════════════
# 1. Tier 1 "stub" — 0 推文空 stub（SingTaoDaily 盲区）
# ══════════════════════════════════════════════════════════════════════
class TestTier1Stub(unittest.TestCase):
    def test_empty_stub_is_zombie(self):
        """no_data=0 + total=0 = 返回了 HTML 结构但无推文 = 僵尸嫌疑 (SingTaoDaily)"""
        is_zombie, tier = classify_zombie({"total": 0, "old": 0, "no_data": 0})
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stub")

    def test_no_data_http_error_not_zombie(self):
        """no_data=1 = HTML 中无 __NEXT_DATA__，可能是 rate limit 或格式变化，不判僵尸（可能是 transient）"""
        is_zombie, tier = classify_zombie({"total": 0, "old": 0, "no_data": 1})
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")

    def test_empty_stub_ignores_count_param(self):
        """stub 判定先于 count 检查（total=0 时 count 必然=0）"""
        is_zombie, tier = classify_zombie({"total": 0, "old": 0, "no_data": 0}, count=0)
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stub")


# ══════════════════════════════════════════════════════════════════════
# 2. Tier 2 "stale 100%" — V37.8.4 原用例必须保留（向后兼容）
# ══════════════════════════════════════════════════════════════════════
class TestTier2Stale100(unittest.TestCase):
    def test_all_tweets_old_flagged(self):
        """V37.8.4 原始模式：old == total 必须被保留（Reuters/WorldBank/BrookingsInst 等）"""
        is_zombie, tier = classify_zombie({"total": 20, "old": 20, "no_data": 0})
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stale")

    def test_single_old_tweet_flagged(self):
        """V37.8.4 原始: total=1, old=1 也触发（边界）"""
        is_zombie, tier = classify_zombie({"total": 1, "old": 1, "no_data": 0})
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stale")


# ══════════════════════════════════════════════════════════════════════
# 3. Tier 3 "stale ≥90%" — CNS1952 盲区闭合（新增）
# ══════════════════════════════════════════════════════════════════════
class TestTier3NearZombie(unittest.TestCase):
    def test_cns1952_case_98_of_99(self):
        """CNS1952 实际案例: 99 推文, 98 超窗口, 1 过短 = 老化率 99%"""
        is_zombie, tier = classify_zombie({"total": 99, "old": 98, "no_data": 0})
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stale")

    def test_exactly_90_percent_triggers(self):
        """≥90% 阈值：整 90% 触发（边界精确 9/10 = 90 >= 90）"""
        is_zombie, tier = classify_zombie({"total": 10, "old": 9, "no_data": 0})
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stale")

    def test_89_percent_does_not_trigger(self):
        """89% 不触发（避免误报活跃但内容低质账号）"""
        is_zombie, tier = classify_zombie({"total": 100, "old": 89, "no_data": 0})
        self.assertFalse(is_zombie)

    def test_80_percent_does_not_trigger(self):
        """80% 老化明显不触发"""
        is_zombie, tier = classify_zombie({"total": 10, "old": 8, "no_data": 0})
        self.assertFalse(is_zombie)


# ══════════════════════════════════════════════════════════════════════
# 4. count 守卫 — 防止"还在低频产出"被误判死亡
# ══════════════════════════════════════════════════════════════════════
class TestCountGuard(unittest.TestCase):
    def test_99_percent_old_but_one_fresh_accepted_not_zombie(self):
        """99% 老化 + 1 条新鲜内容被接受 = 活跃低频账号，不判僵尸"""
        is_zombie, tier = classify_zombie({"total": 99, "old": 98, "no_data": 0}, count=1)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")

    def test_90_percent_old_count_zero_still_zombie(self):
        """90% 老化 + count=0 = 老化内容都被过滤，没接受 = 死账号"""
        is_zombie, tier = classify_zombie({"total": 10, "old": 9, "no_data": 0}, count=0)
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stale")

    def test_default_count_is_zero_preserves_v37_8_4_semantics(self):
        """count 默认值=0 保证 V37.8.4 所有 old==total 用例不变（缺省行为向后兼容）"""
        is_zombie_default, _ = classify_zombie({"total": 5, "old": 5, "no_data": 0})
        is_zombie_explicit, _ = classify_zombie({"total": 5, "old": 5, "no_data": 0}, count=0)
        self.assertEqual(is_zombie_default, is_zombie_explicit)
        self.assertTrue(is_zombie_default)


# ══════════════════════════════════════════════════════════════════════
# 5. 正常账号 — 不误报
# ══════════════════════════════════════════════════════════════════════
class TestAliveAccounts(unittest.TestCase):
    def test_all_fresh_tweets_alive(self):
        """全部新鲜推文 = 健康账号"""
        is_zombie, tier = classify_zombie({"total": 20, "old": 0, "no_data": 0}, count=3)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")

    def test_mixed_fresh_and_old_alive(self):
        """40% 老化 = 正常活跃账号"""
        is_zombie, tier = classify_zombie({"total": 10, "old": 4, "no_data": 0}, count=3)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")

    def test_mostly_short_and_rt_alive(self):
        """过滤量大但都是 RT/过短（old=0）= 非僵尸（只是低价值）"""
        is_zombie, tier = classify_zombie({"total": 20, "old": 0, "no_data": 0}, count=0)
        self.assertFalse(is_zombie)


# ══════════════════════════════════════════════════════════════════════
# 6. 常量契约 — 防止阈值被偷改
# ══════════════════════════════════════════════════════════════════════
class TestConstants(unittest.TestCase):
    def test_stale_threshold_is_90_percent(self):
        """阈值分子/分母常量 = 9/10（90%），防止未来被静默改低"""
        self.assertEqual(ZOMBIE_STALE_NUM, 9)
        self.assertEqual(ZOMBIE_STALE_DEN, 10)

    def test_module_has_docstring(self):
        """模块必须有说明文档（告诉后人为什么有这个文件）"""
        import finance_news_zombie
        self.assertIsNotNone(finance_news_zombie.__doc__)
        self.assertIn("V37.8.5", finance_news_zombie.__doc__)
        self.assertIn("CNS1952", finance_news_zombie.__doc__)
        self.assertIn("SingTaoDaily", finance_news_zombie.__doc__)


# ══════════════════════════════════════════════════════════════════════
# 7. Shell 脚本集成 — 防止 heredoc 不走新逻辑 (V37.5.1 blood lesson class)
# ══════════════════════════════════════════════════════════════════════
class TestShellScriptIntegration(unittest.TestCase):
    def setUp(self):
        self.script = os.path.join(_HERE, "jobs", "finance_news", "run_finance_news.sh")
        with open(self.script, "r", encoding="utf-8") as f:
            self.source = f.read()

    def test_script_imports_zombie_module(self):
        """run_finance_news.sh 必须从 finance_news_zombie 导入 classify_zombie"""
        self.assertIn("from finance_news_zombie import classify_zombie", self.source)

    def test_script_exports_jobs_dir_env(self):
        """必须 export FINANCE_NEWS_JOBS_DIR 让 heredoc Python 找到模块"""
        self.assertIn("export FINANCE_NEWS_JOBS_DIR=", self.source)

    def test_script_calls_classify_with_count(self):
        """必须传 count 参数触发守卫（否则 V37.8.5 守卫是死代码）"""
        self.assertIn("classify_zombie(diag, count)", self.source)

    def test_script_uses_tier_prefix(self):
        """诊断行必须用 tier 变量（stub vs stale 可区分）"""
        self.assertIn("zombie_tier", self.source)
        self.assertIn("ZOMBIE嫌疑[", self.source)

    def test_script_does_not_contain_old_strict_predicate(self):
        """V37.8.4 的严格 old==total 行必须被替换（否则 V37.8.5 逻辑是死代码）"""
        # 只禁止未被注释的原始断言行
        for line in self.source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn(
                'is_zombie_suspect = (diag["total"] > 0 and diag["old"] == diag["total"])',
                stripped,
                f"V37.8.4 严格谓词残留于非注释行：{line}",
            )

    def test_script_has_no_inline_zombie_fallback(self):
        """禁止 inline fallback（避免模块缺失时静默降级回 V37.8.4 行为）"""
        # 找不到 classify_zombie 时就该硬退出，不是 try/except 绕过
        self.assertNotIn("def classify_zombie", self.source,
                         "shell 脚本不应内嵌 classify_zombie 定义——必须从模块导入以保证单一真理源")


# ══════════════════════════════════════════════════════════════════════
# 8. 模块部署 — FILE_MAP 必须含新模块（否则 Mac Mini 拿不到新代码）
# ══════════════════════════════════════════════════════════════════════
class TestAutoDeployMapping(unittest.TestCase):
    def test_finance_news_zombie_py_in_auto_deploy(self):
        """auto_deploy.sh FILE_MAP 必须含 finance_news_zombie.py（否则部署缺失）"""
        with open(os.path.join(_HERE, "auto_deploy.sh"), "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("jobs/finance_news/finance_news_zombie.py", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

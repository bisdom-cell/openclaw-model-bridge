#!/usr/bin/env python3
"""test_finance_news_zombie.py — 僵尸检测单测 (V37.8.5 三层 → V37.9.103 四档「冻结≠死亡」)

V37.8.5 血案背景：V37.8.4 引入僵尸检测，用严格相等 (old == total) 漏掉两个边缘
（CNS1952 99% + SingTaoDaily 0-tweet stub），V37.8.5 用 ≥90% 整数比较闭合。

V37.9.103 血案修复（「冻结 ≠ 死亡」— 来自 V37.9.102 ai_leaders_x 复盘 + 用户 clean+reduce 决策）：
  V37.9.102 实测发现 X Syndication API 平台级退化——把天天发推的活账号（新华/CGTN/
  环球时报/路透商业）快照冻结在 6-10 月前。V37.8.5 的 "stale"（≥90% 超 72h 窗口）无法区分
  「真死(年久)」「快照冻结但活着」「慢但活着」三种情况 → V37.8.14/16 把活账号 XHNews/CGTN
  (97/97、99/99 stale) 当真死错杀了。

  V37.9.103 用最新推文年龄区分（血案精确归因）：
    - frozen (有老推文 total>0, stale, newest < 730 天) = 冻结/低频, 活账号, **绝不建议移除**
      —— 这是被错杀的 XHNews/CGTN 类, 血案核心。
    - dead (stale, newest ≥ 730 天) = 真死/改名, 可移除。
    - stub (0 推文 embed-disabled) = Syndication 永久拿不到, 可移除。
  is_zombie_suspect (= 建议移除候选) = stub OR dead, **frozen 永远 False**。

本测试锁定四类回归：
  1. 向后兼容：stub(SingTaoDaily) 仍可移除, count>0 守卫仍生效, 80% 不触发 stale。
  2. 血案守卫：frozen(XHNews/CGTN 类有老推文快照冻结) 绝不判可移除 (V37.9.103 核心)。
  3. dead 仍可移除：年久 (≥730 天) 真死账号仍被正确识别。
  4. 跨模块一致：DEAD_AGE_DAYS == ai_leaders ZOMBIE_DEAD_DAYS == 730 (防漂移)。

MR-10 (understand-before-fix)：V37.8.4 修复埋盲区 → V37.8.5 闭合 → V37.9.103 发现
V37.8.5 的 "stale=可移除" 本身在 Syndication 退化下错杀活账号。每层修复都可能成为新 bug。
"""
import os
import sys
import unittest

# 把 jobs/finance_news/ 挂进 sys.path 以导入被测模块
_HERE = os.path.dirname(os.path.abspath(__file__))
_JOBS_DIR = os.path.join(_HERE, "jobs", "finance_news")
sys.path.insert(0, _JOBS_DIR)

from finance_news_zombie import (  # noqa: E402
    classify_zombie, ZOMBIE_STALE_NUM, ZOMBIE_STALE_DEN, DEAD_AGE_DAYS,
)


# ══════════════════════════════════════════════════════════════════════
# 1. stub — 0 推文 embed-disabled (SingTaoDaily 盲区), Syndication 永久无用 → 可移除
# ══════════════════════════════════════════════════════════════════════
class TestStubTier(unittest.TestCase):
    def test_empty_stub_is_removable(self):
        """no_data=0 + total=0 = HTML 结构但无推文 = embed-disabled = 可移除 (SingTaoDaily)"""
        is_zombie, tier = classify_zombie({"total": 0, "old": 0, "no_data": 0})
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stub")

    def test_no_data_http_error_not_zombie(self):
        """no_data=1 = HTML 中无 __NEXT_DATA__ (rate limit/格式变化, transient) → 不判僵尸"""
        is_zombie, tier = classify_zombie({"total": 0, "old": 0, "no_data": 1})
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")

    def test_empty_stub_ignores_count_param(self):
        is_zombie, tier = classify_zombie({"total": 0, "old": 0, "no_data": 0}, count=0)
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "stub")


# ══════════════════════════════════════════════════════════════════════
# 2. frozen — V37.9.103 核心血案守卫：有老推文+快照冻结的活账号 (XHNews/CGTN) 绝不可移除
# ══════════════════════════════════════════════════════════════════════
class TestFrozenTier(unittest.TestCase):
    def test_stale_recent_freeze_is_frozen_not_removable(self):
        """99% 超窗口但最新推文 200 天前 = Syndication 冻结的活账号 → frozen, 勿移除"""
        is_zombie, tier = classify_zombie(
            {"total": 99, "old": 98, "no_data": 0, "newest_age_days": 200}, count=0)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "frozen")

    def test_stale_no_newest_age_defaults_frozen(self):
        """stale 但无 newest_age_days → 保守判 frozen (防错杀, V37.8.4 老 100% 用例现归 frozen)"""
        is_zombie, tier = classify_zombie({"total": 20, "old": 20, "no_data": 0}, count=0)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "frozen")

    def test_exactly_90_percent_stale_is_frozen(self):
        """≥90% 触发 stale 检测, 但 newest<730 → frozen 不可移除"""
        is_zombie, tier = classify_zombie(
            {"total": 10, "old": 9, "no_data": 0, "newest_age_days": 100}, count=0)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "frozen")

    def test_blood_lesson_xhnews_cgtn_99_of_99_frozen(self):
        """V37.8.14 错杀回归守卫: XHNews/CGTN 97-99/99 stale 但最新推文几月前 = 活账号 → frozen 绝不移除"""
        # CGTN 实际: 99/99 超窗口, 但 CGTN 真实 Twitter 天天发推, Syndication 冻结 ~6 月
        is_zombie, tier = classify_zombie(
            {"total": 99, "old": 99, "no_data": 0, "newest_age_days": 180}, count=0)
        self.assertFalse(is_zombie, "活账号 CGTN(快照冻结 6 月) 绝不能判可移除 — V37.9.102 血案核心")
        self.assertEqual(tier, "frozen")

    def test_boundary_729_days_is_frozen(self):
        is_zombie, tier = classify_zombie(
            {"total": 10, "old": 10, "no_data": 0, "newest_age_days": 729}, count=0)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "frozen")


# ══════════════════════════════════════════════════════════════════════
# 3. dead — 年久真死/改名 (≥730 天) 仍可移除
# ══════════════════════════════════════════════════════════════════════
class TestDeadTier(unittest.TestCase):
    def test_years_old_is_dead_removable(self):
        """caixin 实际: 99/99 超窗口 + 最新推文 2227 天前 (6 年) = 真死/改名 → dead 可移除"""
        is_zombie, tier = classify_zombie(
            {"total": 99, "old": 98, "no_data": 0, "newest_age_days": 2227}, count=0)
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "dead")

    def test_boundary_730_days_is_dead(self):
        is_zombie, tier = classify_zombie(
            {"total": 10, "old": 10, "no_data": 0, "newest_age_days": 730}, count=0)
        self.assertTrue(is_zombie)
        self.assertEqual(tier, "dead")

    def test_dead_requires_stale_first(self):
        """即使 newest 年久, 若未触发 stale (80% 老化) 也不判 dead (仍 alive)"""
        is_zombie, tier = classify_zombie(
            {"total": 10, "old": 8, "no_data": 0, "newest_age_days": 1000}, count=0)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")


# ══════════════════════════════════════════════════════════════════════
# 4. count 守卫 + 正常账号 — 不误报
# ══════════════════════════════════════════════════════════════════════
class TestCountGuardAndAlive(unittest.TestCase):
    def test_99_percent_old_but_one_fresh_is_alive(self):
        """99% 老化 + count=1 = 低频活账号 → alive (守卫优先于 stale)"""
        is_zombie, tier = classify_zombie(
            {"total": 99, "old": 98, "no_data": 0, "newest_age_days": 200}, count=1)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")

    def test_80_percent_not_stale_is_alive(self):
        is_zombie, tier = classify_zombie(
            {"total": 10, "old": 8, "no_data": 0, "newest_age_days": 100}, count=0)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")

    def test_all_fresh_is_alive(self):
        is_zombie, tier = classify_zombie(
            {"total": 20, "old": 0, "no_data": 0, "newest_age_days": 1}, count=3)
        self.assertFalse(is_zombie)
        self.assertEqual(tier, "alive")


# ══════════════════════════════════════════════════════════════════════
# 5. 常量契约 + 跨模块一致性
# ══════════════════════════════════════════════════════════════════════
class TestConstants(unittest.TestCase):
    def test_stale_threshold_is_90_percent(self):
        self.assertEqual(ZOMBIE_STALE_NUM, 9)
        self.assertEqual(ZOMBIE_STALE_DEN, 10)

    def test_dead_age_days_is_730(self):
        """V37.9.103: dead 阈值 730 天 (~2 年), 远超 Syndication 冻结范围(6-10 月)"""
        self.assertEqual(DEAD_AGE_DAYS, 730)

    def test_cross_module_consistency_with_ai_leaders(self):
        """V37.9.103: finance DEAD_AGE_DAYS 必须 == ai_leaders ZOMBIE_DEAD_DAYS (防漂移)"""
        sys.path.insert(0, os.path.join(_HERE, "jobs", "ai_leaders_x"))
        import ai_leaders_rotation as r  # noqa: E402
        self.assertEqual(DEAD_AGE_DAYS, r.ZOMBIE_DEAD_DAYS)

    def test_module_docstring_has_history(self):
        import finance_news_zombie
        doc = finance_news_zombie.__doc__
        self.assertIsNotNone(doc)
        for marker in ("V37.8.5", "V37.9.103", "CNS1952", "SingTaoDaily", "V37.9.102"):
            self.assertIn(marker, doc)


# ══════════════════════════════════════════════════════════════════════
# 6. Shell 脚本集成 — V37.9.103 wiring 守卫
# ══════════════════════════════════════════════════════════════════════
class TestShellScriptIntegration(unittest.TestCase):
    def setUp(self):
        self.script = os.path.join(_HERE, "jobs", "finance_news", "run_finance_news.sh")
        with open(self.script, "r", encoding="utf-8") as f:
            self.source = f.read()

    def test_script_imports_zombie_module(self):
        self.assertIn("from finance_news_zombie import classify_zombie", self.source)

    def test_script_exports_jobs_dir_env(self):
        self.assertIn("export FINANCE_NEWS_JOBS_DIR=", self.source)

    def test_script_calls_classify_with_count(self):
        self.assertIn("classify_zombie(diag, count)", self.source)

    def test_script_captures_newest_age_days(self):
        """V37.9.103: parser 必须捕捉 newest_age_days (区分 frozen vs dead 的关键信号)"""
        self.assertIn("newest_age_days", self.source)
        self.assertIn("newest_dt", self.source)

    def test_script_routes_frozen_to_degraded_file(self):
        """V37.9.103: frozen 必须分流到 degraded_file (勿移除, 仅可观测)"""
        self.assertIn("degraded_file", self.source)
        self.assertIn("DEGRADED_FILE", self.source)
        self.assertIn('zombie_tier == "frozen"', self.source)

    def test_script_uses_v37_9_103_tier_prefix(self):
        """V37.9.103: 日志用 可移除/冻结 标记, 旧 ZOMBIE嫌疑 已替换"""
        self.assertIn("可移除[", self.source)
        self.assertIn("冻结[", self.source)
        self.assertNotIn("ZOMBIE嫌疑[", self.source)

    def test_script_does_not_contain_old_strict_predicate(self):
        """V37.8.4 严格 old==total 谓词必须已被替换 (非注释行)"""
        for line in self.source.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertNotIn(
                'is_zombie_suspect = (diag["total"] > 0 and diag["old"] == diag["total"])',
                line.strip())

    def test_script_has_no_inline_zombie_fallback(self):
        """禁止 inline classify_zombie 定义 (单一真理源, 避免静默降级)"""
        self.assertNotIn("def classify_zombie", self.source)

    def test_degraded_summary_no_alert(self):
        """V37.9.103: frozen 退化汇总只 log 不推 SYSTEM_ALERT (用户接受退化, 避免 #32 告警疲劳)"""
        self.assertIn("X 渠道退化", self.source)
        # degraded 汇总块不应含 notify --topic alerts (frozen 不告警)
        for line in self.source.splitlines():
            if "X 渠道退化" in line:
                self.assertNotIn("notify", line)


# ══════════════════════════════════════════════════════════════════════
# 7. 3 天连续告警 — 现仅对可移除 (stub+dead) 触发, 措辞已更新
# ══════════════════════════════════════════════════════════════════════
class TestRemovableNotification(unittest.TestCase):
    def setUp(self):
        self.script = os.path.join(_HERE, "jobs", "finance_news", "run_finance_news.sh")
        with open(self.script, "r", encoding="utf-8") as f:
            self.source = f.read()

    def test_persistent_removable_sends_alert(self):
        """连续 3 天可移除账号必须通过 notify 推送 (MR-4: 检测无通知=silent failure)"""
        found = False
        for line in self.source.splitlines():
            if "notify" in line and "可移除" in line and "--topic alerts" in line:
                found = True
                break
        self.assertTrue(found, "3 天连续可移除检测块必须 notify --topic alerts")

    def test_alert_has_system_alert_marker(self):
        found = False
        for line in self.source.splitlines():
            if "notify" in line and "可移除" in line and "[SYSTEM_ALERT]" in line:
                found = True
                break
        self.assertTrue(found, "可移除告警必须带 [SYSTEM_ALERT] 前缀")

    def test_alert_clarifies_frozen_excluded(self):
        """V37.9.103: 告警必须说明 frozen 冻结活账号不在移除之列 (防错杀认知)"""
        self.assertIn("frozen", self.source)
        # 告警文案应提及 frozen 已不在此列
        found = any("frozen" in line and "不在此列" in line
                    for line in self.source.splitlines())
        self.assertTrue(found, "告警须澄清 frozen 活账号已被排除 (V37.9.103 防错杀)")


# ══════════════════════════════════════════════════════════════════════
# 8. 部署映射 — auto_deploy FILE_MAP
# ══════════════════════════════════════════════════════════════════════
class TestAutoDeployMapping(unittest.TestCase):
    def test_finance_news_zombie_py_in_auto_deploy(self):
        with open(os.path.join(_HERE, "auto_deploy.sh"), "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("jobs/finance_news/finance_news_zombie.py", source)


# ══════════════════════════════════════════════════════════════════════
# 9. 反向验证 — 血案守卫真有效 (frozen 若被误判可移除则 fail)
# ══════════════════════════════════════════════════════════════════════
class TestReverseValidation(unittest.TestCase):
    def test_frozen_never_suspect_is_the_core_contract(self):
        """反向验证: 任何 stale + newest<730 的组合都必须 is_zombie_suspect=False。
        若未来有人把 frozen 改回可移除, 这批断言立即 fail。"""
        for age in (8, 30, 100, 200, 365, 500, 729):
            is_zombie, tier = classify_zombie(
                {"total": 50, "old": 50, "no_data": 0, "newest_age_days": age}, count=0)
            self.assertFalse(is_zombie, f"newest={age}天 必须 frozen 不可移除")
            self.assertEqual(tier, "frozen")


if __name__ == "__main__":
    unittest.main(verbosity=2)

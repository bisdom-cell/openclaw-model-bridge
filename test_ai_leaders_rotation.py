"""test_ai_leaders_rotation — V37.9.101 ai_leaders 轮换 + 健康分类单测
                              → V37.9.103 frozen/dead 拆分（冻结≠死亡血案守卫）

覆盖: select_batch 轮换/环绕/全覆盖/边界 + classify_account 五态(rate_limited/stub/
frozen/dead/alive) + is_zombie_suspect 契约 + 反向验证 (rate_limited 绝不判僵尸 — 429
限流不误杀; frozen 绝不判僵尸 — Syndication 冻结活账号不误杀, V37.9.102 血案) + CLI +
跨模块 DEAD 阈值一致性 (ai_leaders ZOMBIE_DEAD_DAYS == finance DEAD_AGE_DAYS).
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs", "ai_leaders_x"))
import ai_leaders_rotation as r  # noqa: E402

_MOD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "jobs", "ai_leaders_x", "ai_leaders_rotation.py")


class TestSelectBatch(unittest.TestCase):
    def test_first_batch(self):
        self.assertEqual(r.select_batch(31, 0, 11), list(range(0, 11)))

    def test_second_batch(self):
        self.assertEqual(r.select_batch(31, 1, 11), list(range(11, 22)))

    def test_last_partial_batch(self):
        # 31/11 → 3 batch, 最后一批 22..30 = 9 个 (不重复填充)
        self.assertEqual(r.select_batch(31, 2, 11), list(range(22, 31)))

    def test_wraps_around(self):
        # idx=3 环绕回 batch 0
        self.assertEqual(r.select_batch(31, 3, 11), r.select_batch(31, 0, 11))

    def test_full_coverage_in_3_runs(self):
        # 连续 3 run (idx 0,1,2) 的并集 = 全部 31 账号
        covered = set()
        for idx in range(3):
            covered |= set(r.select_batch(31, idx, 11))
        self.assertEqual(covered, set(range(31)))

    def test_deterministic(self):
        self.assertEqual(r.select_batch(31, 7, 11), r.select_batch(31, 7, 11))

    def test_batch_ge_num_returns_all(self):
        self.assertEqual(r.select_batch(5, 0, 11), list(range(5)))
        self.assertEqual(r.select_batch(5, 99, 11), list(range(5)))

    def test_num_zero_returns_empty(self):
        self.assertEqual(r.select_batch(0, 0, 11), [])

    def test_batch_zero_returns_empty(self):
        self.assertEqual(r.select_batch(31, 0, 0), [])

    def test_no_duplicate_within_batch(self):
        b = r.select_batch(31, 2, 11)
        self.assertEqual(len(b), len(set(b)))

    def test_indices_in_range(self):
        for idx in range(6):
            for i in r.select_batch(31, idx, 11):
                self.assertTrue(0 <= i < 31)

    def test_default_batch_size_is_11(self):
        self.assertEqual(r.DEFAULT_BATCH_SIZE, 11)
        self.assertEqual(r.select_batch(31, 0), list(range(0, 11)))


class TestClassifyAccount(unittest.TestCase):
    def test_http_fail_is_rate_limited(self):
        self.assertEqual(r.classify_account(False, False, 0, None), r.STATUS_RATE_LIMITED)

    def test_http_ok_no_next_data_is_rate_limited(self):
        # 429 返回 200-ish 但无 __NEXT_DATA__ (或 stub 错误页)
        self.assertEqual(r.classify_account(True, False, 0, None), r.STATUS_RATE_LIMITED)

    def test_has_data_zero_tweets_is_stub(self):
        self.assertEqual(r.classify_account(True, True, 0, None), r.STATUS_STUB)

    def test_old_newest_is_frozen(self):
        # V37.9.103: 7d < newest < 730d → frozen (冻结/低频, 活账号, 非僵尸)
        self.assertEqual(r.classify_account(True, True, 5, 10), r.STATUS_FROZEN)

    def test_months_old_newest_is_frozen(self):
        # V37.9.102 实测: Syndication 把活账号(karpathy)快照冻在 6-10 月前 → frozen 非僵尸
        self.assertEqual(r.classify_account(True, True, 5, 200), r.STATUS_FROZEN)

    def test_recent_newest_is_alive(self):
        self.assertEqual(r.classify_account(True, True, 5, 3), r.STATUS_ALIVE)

    def test_boundary_7_days_is_alive(self):
        # >7 才离开 alive, 恰好 7 天仍 alive
        self.assertEqual(r.classify_account(True, True, 2, 7), r.STATUS_ALIVE)

    def test_boundary_8_days_is_frozen(self):
        # V37.9.103: 8 天 → frozen (不再是僵尸, 防错杀)
        self.assertEqual(r.classify_account(True, True, 2, 8), r.STATUS_FROZEN)

    def test_very_old_newest_is_dead(self):
        # V37.9.103: newest ≥ 730d → dead (真死/改名, 可移除)
        self.assertEqual(r.classify_account(True, True, 5, 800), r.STATUS_DEAD)

    def test_boundary_730_days_is_dead(self):
        self.assertEqual(r.classify_account(True, True, 2, 730), r.STATUS_DEAD)

    def test_boundary_729_days_is_frozen(self):
        self.assertEqual(r.classify_account(True, True, 2, 729), r.STATUS_FROZEN)

    def test_has_tweets_unknown_age_is_alive(self):
        # 有推文但年龄不可知 → 不冒进判僵尸, alive
        self.assertEqual(r.classify_account(True, True, 3, None), r.STATUS_ALIVE)

    def test_zombie_stale_days_is_7(self):
        self.assertEqual(r.ZOMBIE_STALE_DAYS, 7)

    def test_zombie_dead_days_is_730(self):
        self.assertEqual(r.ZOMBIE_DEAD_DAYS, 730)

    def test_cross_module_dead_threshold_consistency(self):
        # V37.9.103: ai_leaders ZOMBIE_DEAD_DAYS 必须 == finance DEAD_AGE_DAYS (跨模块一致, 防漂移)
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "jobs", "finance_news"))
        import finance_news_zombie as fz  # noqa: E402
        self.assertEqual(r.ZOMBIE_DEAD_DAYS, fz.DEAD_AGE_DAYS)


class TestIsZombieSuspect(unittest.TestCase):
    def test_stub_is_suspect(self):
        # stub = embed-disabled, Syndication 永久无用 → 可移除
        self.assertTrue(r.is_zombie_suspect(r.STATUS_STUB))

    def test_dead_is_suspect(self):
        # V37.9.103: dead = 年久真死/改名 → 可移除
        self.assertTrue(r.is_zombie_suspect(r.STATUS_DEAD))

    def test_frozen_not_suspect(self):
        # V37.9.103 核心血案守卫: frozen (Syndication 冻结活账号 XHNews/CGTN 类) 绝不判僵尸
        self.assertFalse(r.is_zombie_suspect(r.STATUS_FROZEN))

    def test_alive_not_suspect(self):
        self.assertFalse(r.is_zombie_suspect(r.STATUS_ALIVE))

    def test_rate_limited_not_suspect(self):
        # 核心血案守卫: 429 限流绝不判僵尸 (不误杀活账号)
        self.assertFalse(r.is_zombie_suspect(r.STATUS_RATE_LIMITED))


class TestBloodLessonRateLimitedNeverZombie(unittest.TestCase):
    """2026-06-03 血案: 31/31 全 no_data (429), naive 检测会误杀全部活账号."""

    def test_all_429_no_false_zombie(self):
        # 模拟 2026-06-03 实测: 31 账号全 429 → 全 rate_limited → 0 僵尸嫌疑
        statuses = [r.classify_account(False, False, 0, None) for _ in range(31)]
        suspects = [s for s in statuses if r.is_zombie_suspect(s)]
        self.assertEqual(len(suspects), 0,
                         "429 限流期间绝不能产生僵尸嫌疑 (V37.8.4 promote 盲区)")
        self.assertTrue(all(s == r.STATUS_RATE_LIMITED for s in statuses))


class TestBloodLessonFrozenNeverZombie(unittest.TestCase):
    """V37.9.102 血案: Syndication 把天天发推的活账号(karpathy/LeCun/Hinton)快照冻结在
    6-10 月前。若把 newest>7d 直接判僵尸会误杀全部活账号(V37.8.14 finance XHNews/CGTN 错杀)。
    V37.9.103: 6-10 月冻结 = frozen，绝不判僵尸；仅 ≥730 天(2年)才判 dead 可移除。
    """

    def test_all_frozen_6_to_10_months_no_false_zombie(self):
        # 模拟 31 账号全部快照冻结在 6-10 月前 (180-300 天) → 全 frozen → 0 僵尸嫌疑
        ages = [180, 200, 240, 270, 300]
        statuses = [r.classify_account(True, True, 5, ages[i % len(ages)]) for i in range(31)]
        suspects = [s for s in statuses if r.is_zombie_suspect(s)]
        self.assertEqual(len(suspects), 0,
                         "Syndication 冻结的活账号(6-10月)绝不能判僵尸 (V37.9.102 血案)")
        self.assertTrue(all(s == r.STATUS_FROZEN for s in statuses))

    def test_only_genuinely_dead_years_old_is_suspect(self):
        # 真死(≥730d) 才判可移除; 冻结(<730d) 不判
        self.assertTrue(r.is_zombie_suspect(r.classify_account(True, True, 5, 2227)))   # caixin 真死
        self.assertFalse(r.is_zombie_suspect(r.classify_account(True, True, 5, 250)))   # Reuters 冻结(8月)勿杀


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, _MOD, *args],
                              capture_output=True, text=True, timeout=20)

    def test_cli_select(self):
        out = self._run("select", "31", "0", "11")
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), " ".join(str(i) for i in range(11)))

    def test_cli_select_wraps(self):
        out = self._run("select", "31", "3", "11")
        self.assertEqual(out.stdout.strip(), " ".join(str(i) for i in range(11)))

    def test_cli_classify_rate_limited(self):
        out = self._run("classify", "0", "0", "0", "-1")
        self.assertEqual(out.stdout.strip(), "rate_limited 0")

    def test_cli_classify_frozen(self):
        # V37.9.103: newest=10d (7<10<730) → frozen, 不判僵尸 (suspect 0)
        out = self._run("classify", "1", "1", "5", "10")
        self.assertEqual(out.stdout.strip(), "frozen 0")

    def test_cli_classify_dead(self):
        # V37.9.103: newest=800d (≥730) → dead, 可移除 (suspect 1)
        out = self._run("classify", "1", "1", "5", "800")
        self.assertEqual(out.stdout.strip(), "dead 1")

    def test_cli_classify_alive(self):
        out = self._run("classify", "1", "1", "5", "3")
        self.assertEqual(out.stdout.strip(), "alive 0")

    def test_cli_usage_on_bad_args(self):
        out = self._run("bogus")
        self.assertEqual(out.returncode, 2)


class TestSourceGuards(unittest.TestCase):
    def test_v37_9_101_marker(self):
        with open(_MOD, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.101", src)

    def test_v37_9_103_marker(self):
        with open(_MOD, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.103", src)

    def test_rate_limited_never_zombie_documented(self):
        with open(_MOD, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("不误杀", src)
        self.assertIn("429", src)

    def test_frozen_not_dead_documented(self):
        # V37.9.103 血案守卫: 源码必须记录 frozen≠死亡 + V37.9.102 复盘来源
        with open(_MOD, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.102", src)
        self.assertIn("frozen", src)
        self.assertIn("勿移除", src)

    def test_no_residual_status_stale_constant(self):
        # V37.9.103: STATUS_STALE 常量已被 frozen/dead 替代, 不得再定义 (防回退)。
        # 仅禁常量定义 (STATUS_STALE = ...), docstring 解释迁移历史可提及该名。
        with open(_MOD, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("STATUS_STALE =", src)
        self.assertNotIn("STATUS_STALE=", src)


if __name__ == "__main__":
    unittest.main()

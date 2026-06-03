"""test_ai_leaders_rotation — V37.9.101 ai_leaders 轮换 + 健康分类单测

覆盖: select_batch 轮换/环绕/全覆盖/边界 + classify_account 四态 + is_zombie_suspect
契约 + 反向验证 (rate_limited 绝不判僵尸 — 429 限流不误杀核心血案守卫) + CLI.
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

    def test_old_newest_is_stale(self):
        self.assertEqual(r.classify_account(True, True, 5, 10), r.STATUS_STALE)

    def test_recent_newest_is_alive(self):
        self.assertEqual(r.classify_account(True, True, 5, 3), r.STATUS_ALIVE)

    def test_boundary_7_days_is_alive(self):
        # >7 才 stale, 恰好 7 天仍 alive
        self.assertEqual(r.classify_account(True, True, 2, 7), r.STATUS_ALIVE)

    def test_boundary_8_days_is_stale(self):
        self.assertEqual(r.classify_account(True, True, 2, 8), r.STATUS_STALE)

    def test_has_tweets_unknown_age_is_alive(self):
        # 有推文但年龄不可知 → 不冒进判僵尸, alive
        self.assertEqual(r.classify_account(True, True, 3, None), r.STATUS_ALIVE)

    def test_zombie_stale_days_is_7(self):
        self.assertEqual(r.ZOMBIE_STALE_DAYS, 7)


class TestIsZombieSuspect(unittest.TestCase):
    def test_stub_is_suspect(self):
        self.assertTrue(r.is_zombie_suspect(r.STATUS_STUB))

    def test_stale_is_suspect(self):
        self.assertTrue(r.is_zombie_suspect(r.STATUS_STALE))

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

    def test_cli_classify_stale(self):
        out = self._run("classify", "1", "1", "5", "10")
        self.assertEqual(out.stdout.strip(), "stale 1")

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

    def test_rate_limited_never_zombie_documented(self):
        with open(_MOD, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("不误杀", src)
        self.assertIn("429", src)


if __name__ == "__main__":
    unittest.main()

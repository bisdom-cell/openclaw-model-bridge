#!/usr/bin/env python3
"""V37.9.99 ai_leaders_x inter-account 节流防 429 单测 (#14 复盘修复).

#14 ai_leaders 31 账号活跃度复盘 (Mac Mini 6/1+6/2 fetch log) 揭示: 不是僵尸问题,
是 V37.9.95 账号 19→31 翻倍后撞 X Syndication HTTP 429 限流 — ~16/31 (21:00) /
~11/31 (09:00) fail, 且失败账号两次 run 轮换 (AndrewYNg/BarrySmith46 一次 fail 一次 ok
= 账号活着非僵尸). 真因: 旧 inter-account `sleep 3` 只在成功路径 (失败 continue 跳过),
限流命中后失败账号 rapid-fire (16 WARN 挤同一秒).

修复: 节流移到循环顶部 (FETCH_IDX 守卫, 第一个不 sleep), 对成功+失败账号都生效, 默认 5s.

反向验证 (手动): sed 把 throttle 移回成功路径 / 删 FETCH_IDX → test fail.
"""

import os
import re
import subprocess
import sys
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_SH = os.path.join(_REPO, "jobs", "ai_leaders_x", "run_ai_leaders_x.sh")


def _read():
    with open(_SH, encoding="utf-8") as f:
        return f.read()


class TestV37_9_99Throttle(unittest.TestCase):
    def setUp(self):
        self.src = _read()

    def test_v37_9_99_marker(self):
        self.assertIn("V37.9.99", self.src)

    def test_fetch_delay_var_defined(self):
        self.assertIn('AI_LEADERS_FETCH_DELAY="${AI_LEADERS_FETCH_DELAY:-5}"', self.src)

    def test_fetch_idx_counter(self):
        self.assertIn("FETCH_IDX=0", self.src)
        self.assertIn("FETCH_IDX=$((FETCH_IDX + 1))", self.src)

    def test_throttle_applies_to_all_before_fetch(self):
        # 节流 sleep 必须在 curl fetch 之前 (循环顶部), 对成功+失败账号都生效
        idx_sleep = self.src.find('[ "$FETCH_IDX" -gt 0 ] && sleep "$AI_LEADERS_FETCH_DELAY"')
        idx_curl = self.src.find("syndication.twitter.com/srv/timeline-profile")
        self.assertGreater(idx_sleep, 0, "节流 sleep 未找到")
        self.assertLess(idx_sleep, idx_curl, "节流必须在 fetch 之前 (循环顶部)")

    def test_old_success_only_sleep3_removed(self):
        # 反退化守卫: 旧 `sleep 3` (成功路径) 不应残留 (是 429 rapid-fire 根因)
        # 用行级扫描排除注释
        for line in self.src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotEqual(stripped, "sleep 3",
                                "旧成功路径 sleep 3 应已移除 (节流移到循环顶部)")

    def test_throttle_before_continue_path(self):
        # 节流必须在 fetch-fail continue 之前 (即失败账号也被节流过)
        idx_sleep = self.src.find('[ "$FETCH_IDX" -gt 0 ] && sleep "$AI_LEADERS_FETCH_DELAY"')
        idx_fail_continue = self.src.find("抓取失败")
        self.assertLess(idx_sleep, idx_fail_continue,
                        "节流必须在失败 continue 之前, 否则失败账号 rapid-fire")

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", _SH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_v37_9_95_31_accounts_preserved(self):
        # 不回退 V37.9.95 的 31 账号扩展 (修的是节流不是账号数)
        self.assertIn("MAX_TOTAL=40", self.src)
        self.assertIn("MAX_PER_PERSON=3", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

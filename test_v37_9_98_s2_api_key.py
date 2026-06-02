#!/usr/bin/env python3
"""V37.9.98 Semantic Scholar API key 集成单测 (unfinished #2 候选兑现).

集成: jobs/semantic_scholar/run_semantic_scholar.sh 加 S2_API_KEY 认证 header.
- 有 S2_API_KEY → 认证模式 (x-api-key header, 100 req/sec, 间隔 1s) 规避 V37.8.13 起
  的 429 daily limit (5/27-5/28 连续 6 关键词 429 全失败).
- FAIL-OPEN: 无 key → 无认证模式 (空 header, 保守 30s 间隔), 当前行为完全不变.

覆盖:
  TestS2ApiKeySourceGuards   — 源码级守卫 (array / 双 curl 注入 / 条件 sleep / FAIL-OPEN)
  TestS2ApiKeyBehavior       — 提取真实检测块 exec, 验证有/无 key 两模式 (零 drift)

反向验证 (手动): 删 S2_CURL_AUTH 注入 → 守卫 fail; 把 sleep "$S2_KW_SLEEP" 改回
sleep 30 → 守卫 fail. 还原后全过.
"""

import os
import re
import subprocess
import sys
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_S2 = os.path.join(_REPO, "jobs", "semantic_scholar", "run_semantic_scholar.sh")


def _read():
    with open(_S2, encoding="utf-8") as f:
        return f.read()


def _extract_detection_block(src):
    """提取 V37.9.98 auth 检测块 (S2_CURL_AUTH=() ... fi, 在 for i 之前)."""
    start = src.index("S2_CURL_AUTH=()")
    end = src.index("for i in", start)
    return src[start:end]


class TestS2ApiKeySourceGuards(unittest.TestCase):
    def setUp(self):
        self.src = _read()

    def test_v37_9_98_marker(self):
        self.assertIn("V37.9.98", self.src)
        self.assertIn("S2_API_KEY", self.src)

    def test_curl_auth_array_defined(self):
        self.assertIn("S2_CURL_AUTH=()", self.src)

    def test_fail_open_reads_env_safely(self):
        # ${S2_API_KEY:-} 让无 key 时不报错 (FAIL-OPEN)
        self.assertIn('"${S2_API_KEY:-}"', self.src)

    def test_x_api_key_header_literal(self):
        self.assertIn('x-api-key: $S2_API_KEY', self.src)

    def test_auth_injected_in_both_curls(self):
        # 两处 S2 curl 都注入 "${S2_CURL_AUTH[@]}"
        cnt = self.src.count('"${S2_CURL_AUTH[@]}" \\')
        self.assertEqual(cnt, 2, f"期望 2 处 curl 注入 auth, 实际 {cnt}")

    def test_conditional_sleep_not_hardcoded_30(self):
        # inter-keyword sleep 用 $S2_KW_SLEEP 变量, 不再硬编码 sleep 30
        self.assertIn('sleep "$S2_KW_SLEEP"', self.src)
        # 反退化守卫: for 循环内不得残留 `&& sleep 30` 硬编码
        loop_region = self.src[self.src.index("for i in"):]
        self.assertNotIn("&& sleep 30", loop_region,
                         "for 循环内不应残留硬编码 sleep 30 (应用 $S2_KW_SLEEP)")

    def test_authenticated_uses_1s_unauthenticated_30s(self):
        block = _extract_detection_block(self.src)
        self.assertIn("S2_KW_SLEEP=1", block)
        self.assertIn("S2_KW_SLEEP=30", block)

    def test_fail_open_documented(self):
        self.assertIn("FAIL-OPEN", self.src)
        self.assertIn("semanticscholar.org/product/api", self.src)

    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", _S2], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestS2ApiKeyBehavior(unittest.TestCase):
    """提取真实检测块 exec, 验证有/无 key 两模式 (测真源码, 零 drift)."""

    def setUp(self):
        self.block = _extract_detection_block(_read())

    def _run(self, env_extra):
        # log() 在脚本后面才定义, 检测块内有 log 调用 → stub 掉
        script = (
            "set -eo pipefail\n"
            "log() { :; }\n"
            + self.block
            + '\necho "SLEEP=$S2_KW_SLEEP"\n'
            + 'echo "AUTHCOUNT=${#S2_CURL_AUTH[@]}"\n'
            + 'echo "AUTHJOINED=${S2_CURL_AUTH[*]}"\n'
        )
        env = {k: v for k, v in os.environ.items() if k != "S2_API_KEY"}
        env.update(env_extra)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, env=env, timeout=15)

    def test_no_key_fail_open(self):
        r = self._run({})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SLEEP=30", r.stdout)        # 保守间隔
        self.assertIn("AUTHCOUNT=0", r.stdout)     # 空 header (无认证, 当前行为)

    def test_with_key_authenticated(self):
        r = self._run({"S2_API_KEY": "fake-test-key-123"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SLEEP=1", r.stdout)         # 认证模式快速间隔
        self.assertIn("AUTHCOUNT=2", r.stdout)     # -H + "x-api-key: KEY"
        self.assertIn("x-api-key: fake-test-key-123", r.stdout)

    def test_empty_key_treated_as_no_key(self):
        # S2_API_KEY="" (空字符串) 应走 FAIL-OPEN 无认证路径
        r = self._run({"S2_API_KEY": ""})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SLEEP=30", r.stdout)
        self.assertIn("AUTHCOUNT=0", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

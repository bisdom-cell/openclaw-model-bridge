#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.238 守卫 — 审计 finding F follow-up: rss_blogs / ai_leaders_blogs / ai_leaders_bsky
全源抓取失败 fail-loud + MR-9 ProxyStats 测试污染隔离。

背景（V37.9.227 诚实登记的 follow-up）:
  ontology_sources / finance_news 已 V37.9.227 修复（consult FETCH_ERRORS → fetch_failed），
  但 rss_blogs / ai_leaders_blogs **无** FETCH_ERRORS 计数器（每 feed FAIL-OPEN WARN+skip
  无计数）→ 全源宕仍写 status:"ok" = watchdog 静默（与"平静无新文章"不可区分，MR-4 家族）。
  ai_leaders_bsky 同 shape（per-account FAIL-OPEN，V37.9.227 未点名但原则 #31 全量同步）。

  修复（镜像 ontology_sources V37.9.227 三件套）: FETCH_ERRORS=0 init + skip 分支 incr +
  new==0 出口 consult（>= 源数 → status:"fetch_failed" + exit 1 → watchdog 显式 case 告警；
  否则 ok + errors 字段可观测）。

第二部分（MR-9 test-pollutes-production 家族，V37.9.229 登记观察兑现）:
  test_config_slo.TestProxyStatsSLO / test_tool_proxy.TestProxyStats /
  test_v37_9_228.TestParseStatsBehavior 直接 ProxyStats()+record_* → 首次调用必 flush
  （_last_flush=0.0）→ 直写真实 ~/proxy_stats.json（Mac Mini 回归时瞬态污染 live proxy
  统计）。修复: 三个类 setUp monkeypatch proxy_filters.STATS_FILE 到临时目录。
"""
import os
import re
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_RSS = os.path.join(_REPO, "jobs/rss_blogs/run_rss_blogs.sh")
_ALB = os.path.join(_REPO, "jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh")
_BSKY = os.path.join(_REPO, "jobs/ai_leaders_bsky/run_ai_leaders_bsky.sh")
_WATCHDOG = os.path.join(_REPO, "job_watchdog.sh")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class _FailLoudContract:
    """三脚本共享契约（mixin）: 计数器 init + skip incr + new==0 consult + 旧形态退役。"""
    SCRIPT = None          # 子类设
    ARRAY = "RSS_FEEDS"    # 源数组名（bsky 覆写）

    def setUp(self):
        self.src = _read(self.SCRIPT)

    def test_counter_initialized(self):
        self.assertIn("FETCH_ERRORS=0", self.src, "缺 FETCH_ERRORS 初始化")

    def test_skip_branch_increments(self):
        # 抓取失败 skip 分支必须 incr（WARN 行到 continue 之间）
        m = re.search(r'抓取失败，跳过"\n(.*?)continue', self.src, re.S)
        self.assertTrue(m, "抓取失败 skip 分支未找到")
        self.assertIn("FETCH_ERRORS=$((FETCH_ERRORS + 1))", m.group(1),
                      "skip 分支未递增 FETCH_ERRORS")

    def test_new0_block_consults_counter(self):
        m = re.search(r'if \[ "\$TOTAL_NEW" -eq 0 \]; then(.*?)\nfi', self.src, re.S)
        self.assertTrue(m, "new==0 块未找到")
        block = m.group(1)
        self.assertIn("FETCH_ERRORS", block, "new==0 块未 consult FETCH_ERRORS")
        self.assertIn('"status":"fetch_failed"', block)
        self.assertIn("${#%s[@]}" % self.ARRAY, block, "未按源数判定系统性失败")
        self.assertIn("exit 1", block, "fetch_failed 未 exit 1（watchdog 依赖非零退出+状态）")

    def test_ok_path_has_errors_field(self):
        # 平静日 ok 也带 errors 计数（可观测，镜像 ontology_sources）
        m = re.search(r'if \[ "\$TOTAL_NEW" -eq 0 \]; then(.*?)\nfi', self.src, re.S)
        block = m.group(1)
        self.assertIn('"status":"ok","new":0,"errors":%d', block)

    def test_old_unconditional_ok_retired(self):
        # 旧无条件 ok 形态（无 errors 字段）必须已退役
        self.assertNotIn('"status":"ok","new":0}\\n\' "$TS"', self.src,
                         "旧无条件 ok（无 errors 字段）仍在位 = 修复被回退")

    def test_v238_marker(self):
        self.assertIn("V37.9.238", self.src)


class TestRssBlogsFailLoud(_FailLoudContract, unittest.TestCase):
    SCRIPT = _RSS


class TestAiLeadersBlogsFailLoud(_FailLoudContract, unittest.TestCase):
    SCRIPT = _ALB


class TestAiLeadersBskyFailLoud(_FailLoudContract, unittest.TestCase):
    SCRIPT = _BSKY
    ARRAY = "BSKY_ACCOUNTS"


class TestWatchdogStatusContract(unittest.TestCase):
    """watchdog 必须识别 fetch_failed（V37.9.227 已建 case，此处防退役）。"""

    def test_fetch_failed_recognized(self):
        src = _read(_WATCHDOG)
        self.assertIn("fetch_failed)", src, "watchdog 缺 fetch_failed 显式 case")


class TestStatsIsolationGuards(unittest.TestCase):
    """MR-9: 三个测试类必须 monkeypatch STATS_FILE（防回退到污染真实 ~/proxy_stats.json）。

    行为级证明在修复时已做（HOME 隔离子进程跑三套件 → 无 proxy_stats.json 创建 +
    sabotage 移除隔离 → 污染复现）。此处源码守卫防未来回退。
    """

    def _assert_isolated(self, test_file, class_name):
        src = _read(os.path.join(_REPO, test_file))
        idx = src.find("class %s" % class_name)
        self.assertGreater(idx, -1, "%s 未找到 %s" % (test_file, class_name))
        # 类体内（到下一个 class 或文件尾）必须 patch STATS_FILE
        nxt = src.find("\nclass ", idx + 1)
        body = src[idx:nxt if nxt > -1 else len(src)]
        self.assertIn("STATS_FILE", body,
                      "%s.%s 未隔离 STATS_FILE（record_* 首次必 flush 会写真实 ~/proxy_stats.json）"
                      % (test_file, class_name))
        self.assertIn("mkdtemp", body, "%s.%s 未用临时目录" % (test_file, class_name))

    def test_config_slo_isolated(self):
        self._assert_isolated("test_config_slo.py", "TestProxyStatsSLO")

    def test_tool_proxy_isolated(self):
        self._assert_isolated("test_tool_proxy.py", "TestProxyStats")

    def test_v228_isolated(self):
        self._assert_isolated("test_v37_9_228_parse_stats_signal.py", "TestParseStatsBehavior")


if __name__ == "__main__":
    unittest.main(verbosity=2)

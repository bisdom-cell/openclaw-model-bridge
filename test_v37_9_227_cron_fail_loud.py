#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.227 守卫 — 审计 finding F: cron 静默状态 fail-loud。

背景（多镜头对抗审计静默失败镜头，2026-07-02）：
  ontology_sources / finance_news 有 FETCH_ERRORS 计数器（每源失败 +1），但 new==0 出口
  **从不 consult 它** → 全部 RSS 源抓取失败时仍写 status:"ok" → watchdog 状态检查静默
  （与"平静无新文章"不可区分）。finance_news 额外: LLM 3 次全失败时无条件写 status:"ok"
  （唯一非 fail-loud 的 LLM-content job，兄弟 job 都写 llm_failed + 告警）。
  修复: new==0 且 FETCH_ERRORS >= RSS 源数（系统性抓取失败）→ status:"fetch_failed" + exit 1;
  LLM_OK != true → status:"llm_failed"。两者都被 watchdog 告警（fetch_failed 显式 case /
  llm_failed 走 catch-all *）。

  诚实边界: rss_blogs / ai_leaders_blogs **无** FETCH_ERRORS 计数器（每 feed FAIL-OPEN
  WARN+skip 无计数）→ 全源宕仍未检出。加计数器更 invasive，登记 follow-up（见 unfinished）。

守卫: 源码级（fix 模式在位 + 旧无条件 ok 退役）+ watchdog 状态兼容契约。
"""
import os
import re
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_ONTO = os.path.join(_REPO, "jobs/ontology_sources/run_ontology_sources.sh")
_FIN = os.path.join(_REPO, "jobs/finance_news/run_finance_news.sh")
_WATCHDOG = os.path.join(_REPO, "job_watchdog.sh")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class TestOntologySourcesFailLoud(unittest.TestCase):
    def setUp(self):
        self.src = _read(_ONTO)

    def test_all_fetch_failed_writes_fetch_failed(self):
        # new==0 块内检测全源失败 → fetch_failed（非 ok）
        m = re.search(r'if \[ "\$TOTAL_NEW" -eq 0 \]; then(.*?)\nfi', self.src, re.S)
        self.assertTrue(m, "new==0 块未找到")
        block = m.group(1)
        self.assertIn("FETCH_ERRORS", block, "new==0 块未 consult FETCH_ERRORS")
        self.assertIn('"status":"fetch_failed"', block)
        self.assertIn("${#RSS_FEEDS[@]}", block, "未按 RSS 源数判定系统性失败")

    def test_v227_marker(self):
        self.assertIn("V37.9.227", self.src)


class TestFinanceNewsFailLoud(unittest.TestCase):
    def setUp(self):
        self.src = _read(_FIN)

    def test_all_fetch_failed_writes_fetch_failed(self):
        m = re.search(r'if \[ "\$TOTAL_NEW" -eq 0 \]; then(.*?)\nfi', self.src, re.S)
        self.assertTrue(m)
        block = m.group(1)
        self.assertIn('"status":"fetch_failed"', block)
        self.assertIn("${#RSS_FEEDS[@]}", block)

    def test_llm_failure_no_longer_unconditional_ok(self):
        # 最终状态记录: LLM_OK != true → llm_failed（旧无条件 status:"ok" 退役）
        self.assertIn('if [ "$LLM_OK" != "true" ]; then\n    RUN_STATUS="llm_failed"', self.src)
        self.assertIn('"status":"%s"', self.src, "status 应参数化 RUN_STATUS")
        # 退役守卫: 最终状态记录段不再硬编码 status:"ok"（旧无条件形态）
        self.assertNotIn('"status":"ok","new":%d,"intl":%d', self.src,
                         "最终状态记录仍硬编码 status:ok（未参数化 RUN_STATUS）")

    def test_v227_marker(self):
        self.assertIn("V37.9.227", self.src)


class TestWatchdogStatusContract(unittest.TestCase):
    """新写的状态值必须被 watchdog 识别为告警（非静默/非崩溃）。"""

    def setUp(self):
        self.wd = _read(_WATCHDOG)

    def test_fetch_failed_recognized(self):
        # watchdog 有 fetch_failed 显式 case
        self.assertIn("fetch_failed)", self.wd)

    def test_llm_failed_hits_catchall(self):
        # llm_failed 不在显式 case 列表 → 走 catch-all *) 告警（非 ok/unknown）
        # catch-all 存在且对非 ok/unknown 告警
        self.assertIn('if [ "$LAST_STATUS" != "ok" ] && [ "$LAST_STATUS" != "unknown" ]', self.wd)
        # llm_failed 确实不在被静默的 ok|unknown 里
        m = re.search(r'ok\|unknown\)\n\s*;;', self.wd)
        self.assertTrue(m, "ok|unknown 静默 case 结构变了")

    def test_fetch_failed_in_logscan_pattern(self):
        # 日志扫描 err_pattern 含 fetch_failed（双覆盖）
        self.assertIn("fetch_failed", re.search(r"err_pattern='([^']+)'", self.wd).group(1))


if __name__ == "__main__":
    unittest.main()

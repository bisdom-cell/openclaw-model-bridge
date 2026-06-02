#!/usr/bin/env python3
"""V37.9.99 Opportunity Radar Stage 5 (kb_radar) 单测.

覆盖:
  TestReadCrossSource    — #1 daily_signals JSON 读取 + FAIL-OPEN
  TestReadTrend          — #3 weekly_trends JSON 读取 (current + glob) + FAIL-OPEN
  TestTopicMatch         — themes_overlap 集合调用 + 子串 fallback (修 V37.9.99 str-vs-set bug)
  TestClassify           — 红/黄/蓝件套交集 (三件套→红 / 二件套→黄 / 减速→蓝 / 跨语言子串)
  TestBuildBriefing      — markdown 三档 section 结构
  TestRunOrchestrator    — no_data / FAIL-OPEN 端到端 (无数据不抛)
  TestKbRadarShellGuards — kb_radar.sh 源码守卫 (set -eEuo / 推送决策 / trap ERR / 3-path)

反向验证 (手动): sed 把 _topics_match 改回直接传 str → TestClassify 跨件套交集失效.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb_radar_collect as kr  # noqa: E402

_REPO = os.path.dirname(os.path.abspath(__file__))
_SH = os.path.join(_REPO, "kb_radar.sh")


def _write_radar(kb_dir, daily=None, weekly=None, daily_date="20260601", weekly_week="2026-W22"):
    radar = os.path.join(kb_dir, "radar")
    os.makedirs(radar, exist_ok=True)
    if daily is not None:
        with open(os.path.join(radar, f"daily_signals_{daily_date}.json"), "w", encoding="utf-8") as f:
            json.dump({"date": daily_date, "signals": daily}, f, ensure_ascii=False)
    if weekly is not None:
        with open(os.path.join(radar, f"weekly_trends_{weekly_week}.json"), "w", encoding="utf-8") as f:
            json.dump({"week": weekly_week, "signals": weekly}, f, ensure_ascii=False)


class TestReadCrossSource(unittest.TestCase):
    def test_missing_file_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(kr.read_cross_source_signals("20260601", d), [])

    def test_reads_signals(self):
        with tempfile.TemporaryDirectory() as d:
            _write_radar(d, daily=[{"suggested_topic": "CoT pruning", "sources": ["arxiv", "github"], "source_count": 2}])
            sigs = kr.read_cross_source_signals("20260601", d)
            self.assertEqual(len(sigs), 1)
            self.assertEqual(sigs[0]["topic"], "CoT pruning")
            self.assertEqual(sigs[0]["source_count"], 2)

    def test_skips_empty_topic(self):
        with tempfile.TemporaryDirectory() as d:
            _write_radar(d, daily=[{"suggested_topic": "", "sources": []}])
            self.assertEqual(kr.read_cross_source_signals("20260601", d), [])

    def test_corrupt_json_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            radar = os.path.join(d, "radar"); os.makedirs(radar)
            with open(os.path.join(radar, "daily_signals_20260601.json"), "w") as f:
                f.write("not json {")
            self.assertEqual(kr.read_cross_source_signals("20260601", d), [])


class TestReadTrend(unittest.TestCase):
    def test_missing_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(kr.read_trend_signals(d), [])

    def test_reads_latest_weekly(self):
        with tempfile.TemporaryDirectory() as d:
            _write_radar(d, weekly=[{"keyword": "speculative decoding", "classification": "strong", "accel_1w": 1.8}])
            t = kr.read_trend_signals(d)
            self.assertEqual(len(t), 1)
            self.assertEqual(t[0]["topic"], "speculative decoding")
            self.assertEqual(t[0]["classification"], "strong")

    def test_current_json_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            radar = os.path.join(d, "radar"); os.makedirs(radar)
            with open(os.path.join(radar, "weekly_trends_current.json"), "w", encoding="utf-8") as f:
                json.dump({"signals": [{"keyword": "from_current", "classification": "mild"}]}, f)
            t = kr.read_trend_signals(d)
            self.assertEqual(t[0]["topic"], "from_current")


class TestTopicMatch(unittest.TestCase):
    def test_same_english_matches(self):
        # 修 V37.9.99 bug: 之前传 str 给 themes_overlap (需 set) → TypeError → 退化子串
        self.assertTrue(kr._topics_match("CoT pruning reasoning", "CoT pruning"))

    def test_cross_lang_substring_fallback(self):
        # 英文术语嵌中文标题 → 子串 fallback 命中
        self.assertTrue(kr._topics_match("speculative decoding", "speculative decoding 投机解码"))

    def test_unrelated_no_match(self):
        self.assertFalse(kr._topics_match("quantum computing hardware", "natural language parsing"))


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.cross = [
            {"topic": "CoT pruning reasoning", "sources": ["arxiv", "github"], "source_count": 2, "score": 9.0},
            {"topic": "agent memory plane", "sources": ["hn"], "source_count": 1, "score": 3.0},
        ]
        self.align = [
            {"topic": "CoT pruning method", "stars": 5, "source_display": "HF", "reason": "control plane"},
            {"topic": "speculative decoding", "stars": 4, "source_display": "arxiv", "reason": ""},
        ]
        self.trend = [
            {"topic": "CoT pruning", "classification": "strong", "accel_1w": 1.8},
            {"topic": "speculative decoding", "classification": "mild", "accel_1w": 1.5},
            {"topic": "static prompt engineering", "classification": "obsolescence", "accel_1w": 0.3},
        ]

    def test_three_way_intersection_is_red(self):
        b = kr.classify_opportunities(self.cross, self.align, self.trend)
        self.assertTrue(any("CoT" in r["topic"] for r in b["red"]),
                        "CoT pruning 命中三件套应进红色")

    def test_two_way_is_yellow(self):
        b = kr.classify_opportunities(self.cross, self.align, self.trend)
        # speculative decoding: align⭐4 + trend mild (无跨源) = 二件套 → 黄
        self.assertTrue(any("speculative" in y["topic"] for y in b["yellow"]))

    def test_decel_is_blue(self):
        b = kr.classify_opportunities(self.cross, self.align, self.trend)
        self.assertTrue(any("static" in t["topic"] for t in b["blue_decel"]))

    def test_red_has_action(self):
        b = kr.classify_opportunities(self.cross, self.align, self.trend)
        for r in b["red"]:
            self.assertIn("deep_dive", r["action"])

    def test_empty_inputs_no_crash(self):
        b = kr.classify_opportunities([], [], [])
        self.assertEqual(b["red"], [])
        self.assertEqual(b["yellow"], [])

    def test_red_sorted_by_importance(self):
        # 用无共享 token 的真实主题 (避免 themes_overlap 误去重)
        cross = [
            {"topic": "CoT pruning reasoning", "sources": ["a", "b", "c"], "source_count": 3, "score": 9},
            {"topic": "speculative decoding", "sources": ["a", "b"], "source_count": 2, "score": 5},
        ]
        align = [{"topic": "CoT pruning method", "stars": 5, "reason": ""},
                 {"topic": "speculative decoding", "stars": 4, "reason": ""}]
        trend = [{"topic": "CoT pruning", "classification": "strong", "accel_1w": 2.0},
                 {"topic": "speculative decoding", "classification": "mild", "accel_1w": 1.5}]
        b = kr.classify_opportunities(cross, align, trend)
        self.assertEqual(len(b["red"]), 2)
        # CoT (source_count 3 + stars 5 = 8) 应排 speculative (2+4=6) 之前
        self.assertIn("CoT", b["red"][0]["topic"])


class TestBuildBriefing(unittest.TestCase):
    def test_sections_present(self):
        b = kr.classify_opportunities(
            [{"topic": "X reasoning", "sources": ["arxiv"], "source_count": 1, "score": 1}], [], [])
        md, wa, dc = kr.build_radar_briefing(b, "2026-06-02", {"chunks": 100, "notes": 50})
        self.assertIn("🛸 早晨机会点雷达", md)
        self.assertIn("红色机会点", md)
        self.assertIn("黄色信号", md)
        self.assertIn("蓝色趋势观察", md)
        self.assertIn("数据复利状态", md)
        self.assertEqual(md, wa)  # WA/Discord 同款全文

    def test_red_rendered(self):
        cross = [{"topic": "CoT pruning", "sources": ["arxiv", "github"], "source_count": 2, "score": 9}]
        align = [{"topic": "CoT pruning", "stars": 5, "reason": ""}]
        trend = [{"topic": "CoT pruning", "classification": "strong", "accel_1w": 1.8}]
        b = kr.classify_opportunities(cross, align, trend)
        md, _, _ = kr.build_radar_briefing(b, "2026-06-02")
        self.assertIn("🚨 [机会点 1]", md)
        self.assertIn("⭐⭐⭐⭐⭐", md)


class TestRunOrchestrator(unittest.TestCase):
    def test_no_data_status(self):
        with tempfile.TemporaryDirectory() as d:
            r = kr.run(today="2026-06-02", kb_dir=d, repo_root=_REPO)
            self.assertEqual(r["status"], "no_data")
            self.assertIn("briefing_markdown", r)

    def test_ok_with_signals(self):
        with tempfile.TemporaryDirectory() as d:
            _write_radar(
                d,
                daily=[{"suggested_topic": "CoT pruning reasoning", "sources": ["arxiv", "github"], "source_count": 2}],
                weekly=[{"keyword": "static prompt eng", "classification": "obsolescence", "accel_1w": 0.3}],
                daily_date="20260601",
            )
            r = kr.run(today="2026-06-02", kb_dir=d, repo_root=_REPO)
            # 有 blue_decel (static) → status ok (total_signals>0)
            self.assertEqual(r["status"], "ok")
            self.assertGreaterEqual(r["blue_decel_count"], 1)

    def test_run_never_raises(self):
        # 即使 kb_dir 非法也 FAIL-OPEN 不抛
        r = kr.run(today="2026-06-02", kb_dir="/nonexistent/xyz", repo_root=_REPO)
        self.assertIn(r["status"], ("no_data", "ok", "collector_failed"))


class TestKbRadarShellGuards(unittest.TestCase):
    def setUp(self):
        with open(_SH, encoding="utf-8") as f:
            self.src = f.read()

    def test_set_eEuo(self):
        self.assertIn("set -eEuo pipefail", self.src)

    def test_v37_9_99_marker(self):
        self.assertIn("V37.9.99", self.src)

    def test_collector_three_path(self):
        self.assertIn("kb_radar_collect.py", self.src)
        self.assertIn("$HOME/openclaw-model-bridge/kb_radar_collect.py", self.src)
        self.assertIn("$HOME/kb_radar_collect.py", self.src)

    def test_notify_sourced(self):
        self.assertIn("notify.sh", self.src)
        self.assertIn("--topic daily", self.src)

    def test_push_only_when_actionable(self):
        # 关键: 仅 red+yellow>0 才推送 (原则 #32 低噪声)
        self.assertIn("ACTIONABLE", self.src)
        self.assertIn("$((RED + YELLOW))", self.src)

    def test_no_signal_not_alerted(self):
        # no_actionable 不应触发 [SYSTEM_ALERT] (radar 无信号非故障)
        self.assertIn("no_actionable", self.src)
        # send_alert 只在 collector_failed 路径调用, 不在 no_actionable
        self.assertNotIn('send_alert "无可操作', self.src)

    def test_trap_err_fatal_handler(self):
        self.assertIn("trap", self.src)
        self.assertIn("ERR", self.src)

    def test_collector_failed_exits_1(self):
        self.assertIn("collector_failed", self.src)
        self.assertIn("exit 1", self.src)

    def test_env_var_heredoc_no_pipe_stdin(self):
        # V37.5.1 血案: 禁 `echo | python3 -` pipe+heredoc stdin 冲突
        for line in self.src.splitlines():
            if "<<" in line and "python3" in line and "|" in line.split("<<")[0]:
                self.fail(f"pipe+heredoc 反模式: {line}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

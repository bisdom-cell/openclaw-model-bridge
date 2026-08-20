#!/usr/bin/env python3
"""test_v37_9_323_preference_learner.py — V37.9.323 对抗审计守卫

镜头: preference_learner.py 每天 07:30 写 status.json.preferences[], 两个消费者
      (kb_status_refresh.sh → SOUL.md "用户偏好（必须遵守）" / memory_plane Preferences 层)
      —— 而它此前【零单测、不在 full_regression、从未被审】。

四条 grounded 缺陷 (全部有确定性复现):
  PL-1 analyze_feedback: feedback[0] 是 append 序最老的一条却标"最新" + json.dumps[:50]
       硬截断 → 不闭合 JSON 残片进 PA 宪法 prompt。生产 status.json 里就是这一条。→ 退役
  PL-2 analyze_activity: 注释写"最小连续小时区间", 代码取高频小时【集合】的 min..max ——
       00 点与 20 点两簇流量被报成 "活跃时段 00:00-21:00"(21h 跨度 19h 零请求)。→ 真连续窗口
  PL-3 analyze_interaction_style: `TEXT: N chars` 是 assistant 自己回复的长度, 却据此断言
       "用户偏好简洁回复"写进"必须遵守" = 自我强化回路。→ 退役
  PL-4 analyze_kb_interests: 日期解析失败后 `pass` 落到计数行 → 400 天前/无日期的条目被
       报成"最近 7 天关注领域"。→ 排除
  PL-6 渲染层: [auto] 统计观察与 [user] 显式偏好一起塞进"必须遵守"。→ 分段
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO)

import preference_learner as pl  # noqa: E402

_PL_SRC = os.path.join(_REPO, "preference_learner.py")
_REFRESH_SRC = os.path.join(_REPO, "kb_status_refresh.sh")
_SOUL = os.path.join(_REPO, "SOUL.md")


def _executable_lines(path):
    """只返回可执行行 (剥整行注释 + 模块 docstring)。

    🔴 本文件的修复注释里【逐字写着】被退役的名字与文案 (analyze_feedback /
    用户偏好简洁回复 / 00:00-21:00), 不剥就会被自己的注释咬 —— V37.9.178 家族。
    """
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # 去模块 docstring
    m = re.match(r'\s*(?:#![^\n]*\n)?\s*"""', src)
    if m:
        end = src.index('"""', m.end())
        src = src[:m.start()] + src[end + 3:]
    out = []
    for line in src.splitlines():
        st = line.strip()
        if st.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def _log_entries(hour_counts, days=2, extra=""):
    """按 {hour: n} 造 proxy log 并解析成 entries。"""
    now = dt.datetime.now()
    lines = []
    for d in range(1, days + 1):
        day = (now - dt.timedelta(days=d)).date()
        for h, n in hour_counts.items():
            for i in range(n):
                ts = dt.datetime.combine(day, dt.time(h, i % 60, 0))
                lines.append(f"[proxy] {ts:%Y-%m-%d %H:%M:%S} [r{i}] {extra or 'TEXT: 120 chars'}")
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "proxy.log")
    open(p, "w").write("\n".join(lines) + "\n")
    return pl.parse_proxy_log(p, days=7)


# ══════════════════════════════════════════════════════════════════════════
class TestActiveWindowBloodLesson(unittest.TestCase):
    """PL-2: 活跃时段必须是真·最小连续窗口, 退化窗口不报。"""

    def test_blood_lesson_two_clusters_not_reported_as_full_day(self):
        # 生产血案: 只有 00 点和 20 点有流量 → 旧逻辑报 "活跃时段 00:00-21:00"
        out = pl.analyze_activity(_log_entries({0: 30, 20: 30}))
        self.assertIsNotNone(out, "应给出跨午夜的连续窗口而非放弃")
        self.assertNotIn("00:00-21:00", out, "退化的 21 小时跨度绝不能再出现")
        self.assertIn("20:00-01:00", out, f"应识别为跨午夜 20:00-01:00, got {out}")

    def test_degenerate_all_day_refuses_to_report(self):
        # 全天均匀 → 任何 80% 窗口都接近全天 = 零信息, 宁可不报
        self.assertIsNone(pl.analyze_activity(_log_entries({h: 5 for h in range(24)})))

    def test_min_requests_gate(self):
        # MIN_REQUESTS 门 —— 旧代码活跃时段【完全无样本门】
        self.assertIsNone(pl.analyze_activity(_log_entries({9: 1})))

    def test_single_day_still_refused(self):
        self.assertIsNone(pl.analyze_activity(_log_entries({9: 30}, days=1)))

    def test_normal_contiguous_window_still_reported(self):
        # 检出力不减: 真有集中窗口时照常报
        out = pl.analyze_activity(_log_entries({9: 30, 10: 30, 11: 30}))
        self.assertIsNotNone(out)
        self.assertIn("09:00-12:00", out)


class TestSmallestActiveWindow(unittest.TestCase):
    """纯函数契约 (可单测, 环形)。"""

    def test_single_cluster(self):
        self.assertEqual(pl.smallest_active_window({9: 10, 10: 10}), (9, 2))

    def test_wraps_midnight(self):
        start, span = pl.smallest_active_window({23: 10, 0: 10})
        self.assertEqual((start, span), (23, 2))

    def test_empty_returns_none(self):
        self.assertIsNone(pl.smallest_active_window({}))

    def test_picks_smallest_span(self):
        # 大簇在 3-4 点, 噪声在 15 点 → 80% 覆盖不需要跨到 15 点
        start, span = pl.smallest_active_window({3: 40, 4: 40, 15: 5})
        self.assertEqual(span, 2)

    def test_coverage_threshold_locked(self):
        self.assertEqual(pl.ACTIVE_COVERAGE, 0.8)
        self.assertEqual(pl.MAX_ACTIVE_SPAN_HOURS, 12)


class TestRetiredEmitters(unittest.TestCase):
    """PL-1 / PL-3: 两个 emitter 已退役 (不是改文案, 是删)。"""

    def test_analyze_feedback_gone(self):
        self.assertFalse(hasattr(pl, "analyze_feedback"))

    def test_analyze_interaction_style_gone(self):
        self.assertFalse(hasattr(pl, "analyze_interaction_style"))

    def test_no_truncated_json_antipattern_in_code(self):
        # 收紧到【真正的反模式】: json.dumps(...)[:N] 硬截断喂进 prompt。
        # 裸 json.dumps 在 main() 的 --json 输出里是合法用途, 不能一刀切。
        code = _executable_lines(_PL_SRC)
        self.assertIsNone(re.search(r"json\.dumps\([^\n]*\)\[:\s*\d+\s*\]", code),
                          "截断 JSON 进 prompt 的反模式不得复活")
        self.assertNotIn("最新:", code)
        # 防空转: 确认 main() 的合法 json.dumps 仍在 (否则上面的正则只是碰巧没匹配)
        self.assertIn("json.dumps", code)

    def test_no_self_referential_style_claim_in_code(self):
        code = _executable_lines(_PL_SRC)
        self.assertNotIn("用户偏好简洁回复", code)
        self.assertNotIn("用户偏好详细回复", code)

    def test_run_analysis_does_not_call_retired(self):
        code = _executable_lines(_PL_SRC)
        body = code[code.index("def run_analysis"):]
        self.assertNotIn("analyze_feedback", body)
        self.assertNotIn("analyze_interaction_style", body)

    def test_guard_helper_actually_strips_comments(self):
        """防空转: 退役名字在注释/docstring 里【确实还在】(留痕), 在可执行行里【确实没了】。

        若不剥注释, 上面几条 assertNotIn 会被本次修复自己写的注释咬 —— V37.9.178 家族。
        """
        with open(_PL_SRC, encoding="utf-8") as f:
            raw = f.read()
        for name in ("analyze_feedback", "analyze_interaction_style", "用户偏好简洁回复"):
            self.assertIn(name, raw, f"{name} 应作为退役留痕保留在注释/docstring")
            self.assertNotIn(name, _executable_lines(_PL_SRC),
                             f"{name} 不得出现在可执行行")


class TestKbInterestWindow(unittest.TestCase):
    """PL-4: 日期解析失败/缺失 → 排除, 不得计入"最近 N 天"。"""

    def _idx(self, entries):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "index.json")
        json.dump({"entries": entries}, open(p, "w"), ensure_ascii=False)
        return p, os.path.join(tmp, "nonexistent_notes")

    def test_unparseable_date_excluded(self):
        idx, notes = self._idx([{"date": "2020-01-01T00:00:00", "tags": ["窗口外"]}] * 12)
        self.assertEqual(pl.analyze_kb_interests(notes, idx, days=7), [])

    def test_missing_date_field_excluded(self):
        idx, notes = self._idx([{"tags": ["无日期"]}] * 12)
        self.assertEqual(pl.analyze_kb_interests(notes, idx, days=7), [])

    def test_out_of_window_date_excluded(self):
        old = (dt.datetime.now() - dt.timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        idx, notes = self._idx([{"date": old, "tags": ["旧"]}] * 12)
        self.assertEqual(pl.analyze_kb_interests(notes, idx, days=7), [])

    def test_in_window_still_counted(self):
        # 检出力不减
        recent = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        idx, notes = self._idx([{"date": recent, "tags": ["本周主题"]}] * 12)
        self.assertEqual(pl.analyze_kb_interests(notes, idx, days=7), ["关注领域：本周主题"])


class TestSoulRenderSplit(unittest.TestCase):
    """PL-6: 从 kb_status_refresh.sh 真源码抽渲染块跑真行为 (防守卫-实现漂移)。"""

    def _render(self, prefs):
        with open(_REFRESH_SRC, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"SOUL_STATUS=\$\(python3 - \"\$HOME/\.kb/status\.json\" << 'PYEOF'\n(.*?)\nPYEOF",
                      src, re.S)
        self.assertIsNotNone(m, "渲染块未找到 (源码结构漂移)")
        tmp = tempfile.mkdtemp()
        sp = os.path.join(tmp, "status.json")
        json.dump({"preferences": prefs, "focus": "t", "priorities": [], "health": {}},
                  open(sp, "w"), ensure_ascii=False)
        r = subprocess.run([sys.executable, "-c", m.group(1), sp],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_auto_not_in_must_obey_section(self):
        out = self._render(["回复用中文", "[auto] 常用工具：read(10次)"])
        must = out.split("**系统观察")[0]
        self.assertIn("回复用中文", must)
        self.assertNotIn("[auto]", must, "机器统计不得进'必须遵守'段")
        self.assertIn("**系统观察（供参考，非用户指令）：**", out)
        self.assertIn("[auto] 常用工具", out.split("**系统观察")[1])

    def test_no_empty_must_obey_header_when_only_auto(self):
        out = self._render(["[auto] 关注领域：AI"])
        self.assertNotIn("必须遵守", out)
        self.assertIn("系统观察", out)

    def test_no_empty_observation_header_when_only_user(self):
        out = self._render(["回复用中文"])
        self.assertIn("必须遵守", out)
        self.assertNotIn("系统观察", out)

    def test_old_lumped_rendering_retired(self):
        with open(_REFRESH_SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("auto_obs", src)
        self.assertIn("user_prefs", src)


class TestSoulSemantics(unittest.TestCase):
    """SOUL.md 静态语义必须与新渲染一致 (否则 PA 仍被告知全部都要遵守)。"""

    def test_soul_declares_two_sections(self):
        with open(_SOUL, encoding="utf-8") as f:
            soul = f.read()
        self.assertIn("系统观察（供参考，非用户指令）", soul)

    def test_soul_retires_obey_everything_claim(self):
        with open(_SOUL, encoding="utf-8") as f:
            soul = f.read()
        self.assertNotIn("所有偏好我都必须遵守", soul)
        self.assertNotIn("> 用户偏好我必须严格遵守。", soul)


class TestGovernanceInvariantActuallyHonored(unittest.TestCase):
    """INV-JOB-PREFLEARN-001 声明"必须有 MIN_REQUESTS 阈值防止数据不足时误判",
    但它是 file_contains grep —— 旧代码里 MIN_REQUESTS 只守着已退役的 interaction_style,
    最显眼的活跃时段完全无门槛 (V37.9.320 家族: grep 过了而目的没达成)。"""

    def test_min_requests_gates_activity(self):
        code = _executable_lines(_PL_SRC)
        body = code[code.index("def analyze_activity"):code.index("def analyze_tool_usage")]
        self.assertIn("MIN_REQUESTS", body,
                      "MIN_REQUESTS 必须真守着 analyze_activity, 不能只是文件里能 grep 到")

    def test_marker(self):
        with open(_PL_SRC, encoding="utf-8") as f:
            self.assertIn("V37.9.323", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)

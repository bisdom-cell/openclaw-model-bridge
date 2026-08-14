#!/usr/bin/env python3
"""V37.9.305 守卫: dream/radar 夜间管道诚实修复 (对抗审计 A 镜头 5 findings)。

  A-F1  Radar #1 日期窗口 — 03:00 Reduce 不传 --date 时 aggregator 扫"今天"
        = 只有 00:00-03:00 的零星笔记 (笔记文件名前缀=写入时刻, 内容 job 绝大多数
        07:30-22:30 跑) → 跨源共振维度结构性恒空却全绿。改扫昨日全天, 输出
        daily_signals_{昨日}.json 恰与 kb_radar_collect 06:00 读昨天契约对齐。
  A-F2  dream 高对齐 Top 5 标签 "今日" → "近期…跨多天累积" (镜像 kb_evening
        V37.9.56-hotfix3 血案修复, dream 侧漏改; picker 无日期过滤, 僵尸源 jsonl
        冻结数天时陈旧条目曾以"今日"身份进 prompt)。
  A-F3  14 天主题 ban-list 空值可观测 — 曾是纯 FAIL-OPEN + 零日志, 部署漂移/
        格式漂移 → ban-list 永久空 → 重复主题回归无人能发现。
  A-F4  sources 缓存 all-miss 落采样分支时, 已成功缓存的 NOTES_SIGNALS 曾被
        静默整体丢弃 (Reduce 路径 NOTES_MATERIAL 恒空)。
  A-F5  kb_radar.sh `2>&1` 把 stderr WARN 混进 JSON 捕获 — 设计上被容忍的
        单行损坏升级成整 job parse_error + 误告警。
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
KB_DREAM = os.path.join(REPO, "kb_dream.sh")
KB_RADAR = os.path.join(REPO, "kb_radar.sh")
AGGREGATOR = os.path.join(REPO, "cross_source_signal_aggregator.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestRadarDateWindow(unittest.TestCase):
    """A-F1: dream 传 --date 昨天 + aggregator 日期过滤行为级。"""

    def test_dream_passes_yesterday_date(self):
        """血案回归: 03:00 调用点必须传 --date (不传 = 扫 00:00-03:00 残窗)。"""
        src = _read(KB_DREAM)
        self.assertIn("_RADAR_DATE", src)
        self.assertRegex(src, r'timedelta\(days=1\)',
                         "昨天日期须用 python timedelta (BSD/GNU date 分歧)")
        self.assertRegex(src, r'--date "\$_RADAR_DATE"')

    def test_dream_labels_honest(self):
        """标签诚实化: 扫描窗口是昨日全天, 旧 '今日' 标签退役。"""
        src = _read(KB_DREAM)
        self.assertIn("(昨日全天无跨 source 共振信号)", src)
        self.assertIn("# 昨日全天跨 source 共振信号", src)
        self.assertNotIn("(今日无跨 source 共振信号)", src)
        self.assertNotIn("# 今日跨 source 共振信号", src)

    def test_aggregator_date_filter_behavior(self):
        """行为级: --date 真实过滤笔记 (dev 无 numpy 时命中日期走到 embedding 阶段
        = fail_open_no_deps ≠ no_notes; 无匹配日期 → no_notes)。依赖无关断言。"""
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "notes")
            os.makedirs(notes)
            with open(os.path.join(notes, "20260813120000_t.md"), "w") as f:
                f.write("---\ndate: 2026-08-13\ntags: [t]\nsource: arxiv\n---\n# n\nbody\n")
            out_dir = os.path.join(tmp, "radar")

            def run_agg(date):
                proc = subprocess.run(
                    ["python3", AGGREGATOR, "--date", date, "--json",
                     "--kb-dir", tmp, "--output-dir", out_dir],
                    capture_output=True, text=True, timeout=120)
                return json.loads(proc.stdout)

            hit = run_agg("20260813")
            miss = run_agg("20260812")
            self.assertNotEqual(hit.get("status"), "no_notes",
                                "命中日期必须扫到笔记 (进入后续阶段)")
            self.assertEqual(miss.get("status"), "no_notes")

    def test_yesterday_contract_aligns_with_collector(self):
        """跨文件契约: kb_radar_collect 06:00 优先读昨天的 daily_signals 文件 —
        dream 03:00 生成昨天的文件后两消费方读同一份 (MR-8)。"""
        collect = _read(os.path.join(REPO, "kb_radar_collect.py"))
        self.assertRegex(collect, r'timedelta\(days=1\)')
        self.assertIn("yesterday_str", collect)


class TestTopPicksLabelHonesty(unittest.TestCase):
    """A-F2: dream 高对齐 Top 5 标签镜像 evening V37.9.56-hotfix3 修复。"""

    def setUp(self):
        self.src = _read(KB_DREAM)

    def test_new_honest_label(self):
        self.assertIn("近期高对齐参考阅读 Top 5", self.src)
        self.assertIn("跨多天累积", self.src)
        self.assertIn("不是今日发生的事件", self.src)

    def test_old_today_label_retired(self):
        """旧 '今日高对齐 Top 5' 标签退役 (与 LEVEL_5/6 guard '跨多天累积' 自相矛盾)。"""
        self.assertNotIn("今日高对齐 Top 5", self.src)


class TestBanListObservability(unittest.TestCase):
    """A-F3: ban-list 空值 WARN (镜像 DREAM_HG_GUARD/DREAM_CREDIBILITY sibling 模式)。"""

    def test_empty_banned_block_logs_warn(self):
        src = _read(KB_DREAM)
        self.assertRegex(
            src, r'\[ -z "\$BANNED_THEMES_BLOCK" \] && log "WARN: 14 天主题 ban-list 为空',
            "ban-list 空值必须 log WARN — 纯 FAIL-OPEN + 零日志曾让静默失效不可发现")

    def test_sibling_guards_still_present(self):
        """sibling 模式对照组不回退 (HG guard / credibility 的同款空值检查)。"""
        src = _read(KB_DREAM)
        self.assertRegex(src, r'\[ -z "\$DREAM_HG_GUARD" \] && log')
        self.assertRegex(src, r'\[ -z "\$DREAM_CREDIBILITY" \] && log')


class TestNotesSignalsFallback(unittest.TestCase):
    """A-F4: 采样分支 notes 信号缓存回填 (不再静默丢弃)。"""

    def setUp(self):
        self.src = _read(KB_DREAM)

    def test_fallback_branch_exists(self):
        self.assertRegex(self.src, r'elif \[ -n "\$\{NOTES_SIGNALS// \}" \]')
        self.assertIn("注入已缓存的用户笔记信号 (V37.9.305 A-F4)", self.src)

    def test_fallback_injects_phase1b_header(self):
        """回填块用与 MapReduce 分支同款 Phase 1b 头 (LLM 侧格式一致)。"""
        idx = self.src.find("V37.9.305 A-F4")
        self.assertGreater(idx, 0)
        after = self.src[idx:idx + 400]
        self.assertIn("Phase 1b: 用户笔记/交互记录信号", after)


class TestRadarStderrSeparation(unittest.TestCase):
    """A-F5: kb_radar collector stderr 不再混进 JSON 捕获。"""

    def setUp(self):
        self.src = _read(KB_RADAR)

    def test_no_stderr_merge_into_json(self):
        """旧 `--json 2>&1)` 捕获模式退役 (WARN 行会打爆 json.load)。"""
        self.assertNotRegex(self.src, r'--json 2>&1\)')

    def test_stderr_captured_to_tempfile_and_forwarded(self):
        self.assertIn("_RADAR_ERR", self.src)
        self.assertRegex(self.src, r'--json 2>"\$_RADAR_ERR"')
        self.assertIn("collector-stderr:", self.src)

    def test_warn_plus_json_parses(self):
        """行为级: 分离后 stderr WARN 不进 stdout — 模拟 collector 同时输出
        stderr WARN + stdout JSON, 用 kb_radar 同款捕获模式验证 JSON 干净。"""
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "fake_collector.py")
            with open(fake, "w") as f:
                f.write("import sys\n"
                        "sys.stderr.write('[top_picker] WARN: line 3 JSON parse error, skip\\n')\n"
                        "print('{\"status\": \"ok\", \"red_count\": 1}')\n")
            err_file = os.path.join(tmp, "err")
            proc = subprocess.run(
                ["bash", "-c", f'OUT=$(python3 "{fake}" --json 2>"{err_file}"); printf "%s" "$OUT"'],
                capture_output=True, text=True, timeout=30)
            parsed = json.loads(proc.stdout)  # WARN 混入时这里会炸 (旧口径的血案)
            self.assertEqual(parsed["status"], "ok")
            with open(err_file) as f:
                self.assertIn("WARN", f.read())

    def test_syntax(self):
        for path in (KB_RADAR, KB_DREAM):
            proc = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"{path}: {proc.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

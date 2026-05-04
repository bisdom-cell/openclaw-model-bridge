"""V37.9.26 — movespeed_incident_monitor 单测 (watchdog 主动告警升级)

覆盖矩阵:
  TestParseIsoToEpoch         — ISO 8601 时间戳解析 (含 Z / 含 offset / naive / 损坏)
  TestExtractCallerBasename   — caller 路径 basename 提取 (绝对/相对/空字符串)
  TestCountRecentIncidents    — 24h 窗口边界 + 损坏行容忍 + 缺字段跳过
  TestFormatWatchdogOutput    — pipe 分隔输出格式 (count|hit|callers)
  TestCliBehavior             — CLI 端到端 (subprocess) + FAIL-OPEN file_read_error
  TestThresholdBoundary       — 反向验证: 阈值边界 (4 不触发 / 5 恰好触发)
  TestWatchdogIntegrationGuards — 源码层: job_watchdog.sh 调用独立模块不内嵌

设计契约:
  - count_recent_incidents 是纯函数, 无网络无副作用 (除 file IO)
  - 时间窗口由 caller 传入 now_epoch (确定性测试不依赖系统时间)
  - 损坏 JSON 行不抛异, 计入 parse_errors 让 caller 决策处理
  - CLI FAIL-OPEN: 文件 IO 失败输出 "0|0|file_read_error" 让 watchdog 降级
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from movespeed_incident_monitor import (  # noqa: E402
    count_recent_incidents,
    extract_caller_basename,
    format_watchdog_output,
    parse_iso_to_epoch,
)

MONITOR_SCRIPT = REPO_ROOT / "movespeed_incident_monitor.py"
WATCHDOG_SCRIPT = REPO_ROOT / "job_watchdog.sh"


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ago_iso(seconds):
    """Return ISO 8601 timestamp (UTC) `seconds` ago."""
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.isoformat().replace("+00:00", "Z")


# ── tests ──────────────────────────────────────────────────────────────────

class TestParseIsoToEpoch(unittest.TestCase):
    """ISO 8601 timestamp parsing (Z suffix / offset / naive / malformed)."""

    def test_parses_z_suffix_utc(self):
        # 用 datetime 动态计算预期 epoch, 避免 magic number
        expected = int(datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(parse_iso_to_epoch("2026-05-04T12:00:00Z"), expected)

    def test_parses_with_explicit_offset(self):
        # 2026-05-04 20:00:00 +08:00 = 12:00:00 UTC
        expected = int(datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(parse_iso_to_epoch("2026-05-04T20:00:00+08:00"), expected)

    def test_parses_naive_as_utc(self):
        expected = int(datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(parse_iso_to_epoch("2026-05-04T12:00:00"), expected)

    def test_z_and_offset_parse_to_same_utc(self):
        """V37.9.26 等价性守卫: '2026-05-04T12:00:00Z' 和带 +00:00 应等价."""
        e1 = parse_iso_to_epoch("2026-05-04T12:00:00Z")
        e2 = parse_iso_to_epoch("2026-05-04T12:00:00+00:00")
        self.assertEqual(e1, e2)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_iso_to_epoch("")

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            parse_iso_to_epoch(None)

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            parse_iso_to_epoch("not a timestamp")


class TestExtractCallerBasename(unittest.TestCase):
    """caller path basename extraction."""

    def test_absolute_path(self):
        self.assertEqual(
            extract_caller_basename("/Users/foo/jobs/run_freight.sh"),
            "run_freight.sh",
        )

    def test_relative_path(self):
        self.assertEqual(
            extract_caller_basename("kb_dream.sh"),
            "kb_dream.sh",
        )

    def test_empty_returns_question(self):
        self.assertEqual(extract_caller_basename(""), "?")

    def test_nested_path(self):
        self.assertEqual(
            extract_caller_basename("/a/b/c/d/e/script.sh"),
            "script.sh",
        )

    def test_trailing_slash_quirk(self):
        # 边界: 末尾 / → rsplit 取 "" — 调用方应该不会传这种值
        self.assertEqual(extract_caller_basename("/a/b/"), "")


class TestCountRecentIncidents(unittest.TestCase):
    """24h window counting + parse error tolerance."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="incident_test_")
        self.f = Path(self._td) / "incidents.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _write(self, lines):
        with open(self.f, "w", encoding="utf-8") as fp:
            for line in lines:
                if isinstance(line, dict):
                    fp.write(json.dumps(line) + "\n")
                else:
                    fp.write(str(line) + "\n")

    def test_empty_file_returns_zero(self):
        self._write([])
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, errors = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 0)
        self.assertEqual(callers, [])
        self.assertEqual(errors, 0)

    def test_recent_incidents_counted(self):
        self._write([
            {"timestamp_iso": _ago_iso(60), "caller": "/a/b/run1.sh"},
            {"timestamp_iso": _ago_iso(3600), "caller": "/a/b/run2.sh"},
            {"timestamp_iso": _ago_iso(7200), "caller": "/a/b/run1.sh"},  # dup caller
        ])
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, errors = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 3)
        # caller dedup: encounter order
        self.assertEqual(callers, ["run1.sh", "run2.sh"])
        self.assertEqual(errors, 0)

    def test_old_incidents_excluded_24h_window(self):
        """超过 24h 的 incident 应被排除."""
        self._write([
            {"timestamp_iso": _ago_iso(60), "caller": "recent.sh"},        # in window
            {"timestamp_iso": _ago_iso(86400 - 60), "caller": "edge_in.sh"},  # 1min before edge
            {"timestamp_iso": _ago_iso(86400 + 60), "caller": "edge_out.sh"}, # 1min after edge
            {"timestamp_iso": _ago_iso(86400 * 30), "caller": "ancient.sh"}, # 30 days ago
        ])
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, _ = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 2,
            f"24h 窗口内应只 2 条 (recent + edge_in), got {count} callers={callers}")
        self.assertIn("recent.sh", callers)
        self.assertIn("edge_in.sh", callers)
        self.assertNotIn("edge_out.sh", callers)
        self.assertNotIn("ancient.sh", callers)

    def test_malformed_json_line_increments_parse_errors(self):
        self._write([
            json.dumps({"timestamp_iso": _ago_iso(60), "caller": "ok.sh"}),
            "not valid json {{{",  # malformed line
            json.dumps({"timestamp_iso": _ago_iso(120), "caller": "ok2.sh"}),
        ])
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, errors = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 2,
            "其他正常行应继续计数 (FAIL-OPEN per-line)")
        self.assertEqual(errors, 1)

    def test_missing_timestamp_field_skipped(self):
        self._write([
            {"caller": "no_ts.sh"},  # 缺 timestamp_iso
            {"timestamp_iso": _ago_iso(60), "caller": "ok.sh"},
        ])
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, errors = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 1)
        self.assertEqual(callers, ["ok.sh"])
        # 缺字段 silent skip 不计 parse_error (与损坏 JSON 区分)
        self.assertEqual(errors, 0)

    def test_non_dict_record_increments_parse_errors(self):
        self._write([
            json.dumps([1, 2, 3]),  # JSON valid 但顶层非 object
            json.dumps({"timestamp_iso": _ago_iso(60), "caller": "ok.sh"}),
        ])
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, errors = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 1)
        self.assertEqual(errors, 1)

    def test_blank_lines_ignored(self):
        with open(self.f, "w", encoding="utf-8") as fp:
            fp.write("\n\n")
            fp.write(json.dumps({"timestamp_iso": _ago_iso(60), "caller": "ok.sh"}) + "\n")
            fp.write("\n")
        now = int(datetime.now(timezone.utc).timestamp())
        count, _, errors = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 1)
        self.assertEqual(errors, 0)


class TestFormatWatchdogOutput(unittest.TestCase):
    """pipe-separated output format for watchdog consumption."""

    def test_threshold_hit(self):
        out = format_watchdog_output(count=5, threshold=5,
                                      callers=["a.sh", "b.sh"])
        self.assertEqual(out, "5|1|a.sh/b.sh")

    def test_threshold_not_hit(self):
        out = format_watchdog_output(count=4, threshold=5, callers=["a.sh"])
        self.assertEqual(out, "4|0|a.sh")

    def test_zero_count_no_callers(self):
        out = format_watchdog_output(count=0, threshold=5, callers=[])
        self.assertEqual(out, "0|0|")

    def test_caller_truncation_at_5(self):
        callers = [f"r{i}.sh" for i in range(10)]
        out = format_watchdog_output(count=10, threshold=5, callers=callers)
        # 应只含前 5 个
        self.assertEqual(out, "10|1|r0.sh/r1.sh/r2.sh/r3.sh/r4.sh")
        self.assertNotIn("r5.sh", out)


class TestCliBehavior(unittest.TestCase):
    """CLI subprocess end-to-end + FAIL-OPEN."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="incident_cli_")
        self.f = Path(self._td) / "incidents.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(MONITOR_SCRIPT)] + list(args),
            capture_output=True, text=True, timeout=15,
        )

    def test_cli_happy_path_threshold_hit(self):
        """6 条最近 + 阈值 5 → 输出 6|1|..."""
        with open(self.f, "w", encoding="utf-8") as fp:
            for i in range(6):
                rec = {"timestamp_iso": _ago_iso(60 * (i + 1)),
                       "caller": f"/a/b/run_{i}.sh"}
                fp.write(json.dumps(rec) + "\n")
        now = int(datetime.now(timezone.utc).timestamp())
        result = self._run(str(self.f), str(now), "5")
        self.assertEqual(result.returncode, 0,
            f"CLI 应成功 exit 0, stderr={result.stderr}")
        parts = result.stdout.strip().split("|")
        self.assertEqual(parts[0], "6")
        self.assertEqual(parts[1], "1")
        self.assertIn("run_0.sh", parts[2])

    def test_cli_threshold_not_hit(self):
        """4 条最近 + 阈值 5 → 输出 4|0|..."""
        with open(self.f, "w", encoding="utf-8") as fp:
            for i in range(4):
                rec = {"timestamp_iso": _ago_iso(60 * (i + 1)),
                       "caller": f"r{i}.sh"}
                fp.write(json.dumps(rec) + "\n")
        now = int(datetime.now(timezone.utc).timestamp())
        result = self._run(str(self.f), str(now), "5")
        parts = result.stdout.strip().split("|")
        self.assertEqual(parts[0], "4")
        self.assertEqual(parts[1], "0")

    def test_cli_missing_args_exits_2(self):
        result = self._run(str(self.f))
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr.lower())

    def test_cli_invalid_now_epoch_exits_2(self):
        result = self._run(str(self.f), "not_an_int", "5")
        self.assertEqual(result.returncode, 2)

    def test_cli_file_read_error_fail_open(self):
        """文件不存在 → CLI 输出 0|0|file_read_error exit 0 (FAIL-OPEN)."""
        nonexistent = Path(self._td) / "does_not_exist.jsonl"
        now = int(datetime.now(timezone.utc).timestamp())
        result = self._run(str(nonexistent), str(now), "5")
        self.assertEqual(result.returncode, 0,
            "FAIL-OPEN: 文件不存在不应让 watchdog 失败 (exit 0)")
        self.assertEqual(result.stdout.strip(), "0|0|file_read_error")


class TestThresholdBoundary(unittest.TestCase):
    """反向验证: 阈值边界确保不漏报不误报."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="incident_threshold_")
        self.f = Path(self._td) / "incidents.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _write_n(self, n):
        with open(self.f, "w", encoding="utf-8") as fp:
            for i in range(n):
                rec = {"timestamp_iso": _ago_iso(60 * (i + 1)),
                       "caller": f"r{i}.sh"}
                fp.write(json.dumps(rec) + "\n")

    def test_exactly_5_triggers_threshold_5(self):
        self._write_n(5)
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, _ = count_recent_incidents(str(self.f), now)
        out = format_watchdog_output(count, 5, callers)
        self.assertTrue(out.startswith("5|1|"),
            f"恰好 5 条应触发阈值 5, got: {out!r}")

    def test_exactly_4_does_not_trigger_threshold_5(self):
        self._write_n(4)
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, _ = count_recent_incidents(str(self.f), now)
        out = format_watchdog_output(count, 5, callers)
        self.assertTrue(out.startswith("4|0|"),
            f"4 条不应触发阈值 5, got: {out!r}")

    def test_huge_count_no_overflow(self):
        """100 条 incident 不崩溃 (内存合理 + 不爆炸 callers 列表)."""
        self._write_n(100)
        now = int(datetime.now(timezone.utc).timestamp())
        count, callers, _ = count_recent_incidents(str(self.f), now)
        self.assertEqual(count, 100)
        # callers 是去重的, 100 个不同 r0~r99 应全收 (但 format 输出限 5)
        self.assertEqual(len(callers), 100)
        out = format_watchdog_output(count, 5, callers)
        # output 应只含前 5 个
        self.assertEqual(out.count("/"), 4)  # 5 个 caller 4 个分隔符


class TestWatchdogIntegrationGuards(unittest.TestCase):
    """V37.9.26 源码守卫: job_watchdog.sh 必须调独立模块不内嵌 heredoc."""

    @classmethod
    def setUpClass(cls):
        cls.watchdog_src = WATCHDOG_SCRIPT.read_text(encoding="utf-8")

    def test_watchdog_calls_monitor_script(self):
        """watchdog 必须调 movespeed_incident_monitor.py (不内嵌 inline heredoc)."""
        self.assertIn("movespeed_incident_monitor.py", self.watchdog_src,
            "V37.9.26: watchdog 必须调独立 monitor 脚本, 不可内嵌 heredoc")

    def test_watchdog_uses_threshold_5(self):
        self.assertIn("INCIDENT_24H_THRESHOLD=5", self.watchdog_src,
            "V37.9.26 阈值守卫: 24h 窗口 ≥5 条触发告警 (常量化)")

    def test_watchdog_has_file_existence_guard(self):
        """watchdog 调监控前应检查 incidents.jsonl 文件 + 监控脚本存在."""
        self.assertIn('[ -f "$INCIDENT_FILE" ]', self.watchdog_src)
        self.assertIn('[ -f "$INCIDENT_MONITOR" ]', self.watchdog_src)

    def test_watchdog_does_not_have_inline_heredoc_for_incidents(self):
        """V37.9.26 反模式守卫: watchdog 内不应再有 incident 处理的 inline
        Python heredoc (一处真理源 — 只在 movespeed_incident_monitor.py)."""
        # 检查没有 inline `import json` + `timestamp_iso` 同时出现的 heredoc 段
        # (movespeed 上下文)
        for line in self.watchdog_src.split("\n"):
            if "import json" in line and "watchdog" not in line:
                # 一般检查: 不允许在 watchdog 主流程内出现裸 import json
                # (除非在 PYEOF 这种内嵌脚本里 — 但本守卫已经禁止 movespeed
                #  的 inline 方式)
                pass
        # 字面量守卫: 不应同时含 timestamp_iso 解析逻辑 (这是 monitor 模块独占)
        watchdog_excluding_comments = "\n".join(
            l for l in self.watchdog_src.split("\n")
            if not l.strip().startswith("#")
        )
        # parse_iso_to_epoch 是 monitor 模块独占函数名, watchdog 不应直接含其逻辑
        self.assertNotIn("def parse_iso_to_epoch", watchdog_excluding_comments,
            "V37.9.26: watchdog 不应内嵌 parse_iso_to_epoch (一处真理源)")
        # window_24h_start 命名是历史 inline 版本痕迹
        self.assertNotIn("window_24h_start = now_epoch - 86400",
                         watchdog_excluding_comments,
            "V37.9.26: watchdog 不应残留 inline 24h 窗口逻辑 (已迁移到 monitor)")

    def test_monitor_script_file_exists(self):
        self.assertTrue(MONITOR_SCRIPT.exists(),
            "V37.9.26: movespeed_incident_monitor.py 必须存在于 repo root")


if __name__ == "__main__":
    unittest.main(verbosity=2)

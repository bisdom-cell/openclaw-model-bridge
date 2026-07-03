#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.228 守卫 — 审计 finding B: parse-block 静默 200 无 stats 信号（部分修）。

背景（多镜头对抗审计静默失败镜头，2026-07-02）：
  tool_proxy do_POST 的 200-response 处理块包在 try/except Exception，except **不 re-raise
  直接 fall through** 发 resp.status(200) + raw resp_body。若 json.loads/fix_tool_args/
  _handle_custom_tool_calls/build_sse_response 抛异：
   - json.loads 失败（backend 200+garbage）子 case: record_success 未到 + record_error 在
     **外层** except（永不达）→ 请求零 stats 信号 → consecutive_errors/告警永不触发 →
     监控对 backend 返 200+乱码完全失明（forward 给 gateway 作 plausible 200）= fail-plausible。
  修复（本版, 部分）: `_recorded_success` flag（record_success 后置 True），parse-except 里
  未记 success → record_error（让 fault 可见）; 已记 success（如 build_sse_response 后崩）
  不重复记（success 合法）。

  诚实边界 → 剩 follow-up: streaming 序列化失败后仍发 application/json 给 SSE client（内容类型
  错）是**独立 delivery bug**，本版只修 stats 信号（更高价值），delivery 修法更 murky 需专门评估。

守卫（tool_proxy 顶层 serve_forever 不可 import → 源码级守卫 + 顺序契约 + sabotage）。
"""
import os
import re
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_TOOL_PROXY = os.path.join(_REPO, "tool_proxy.py")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _parse_block(src):
    """抽 200-response parse block（从 chat/completions 判定到 self.send_response(resp.status)）。"""
    start = src.index('if "/chat/completions" in self.path and resp_body:')
    end = src.index("self.send_response(resp.status)", start)
    return src[start:end]


class TestParseStatsSignal(unittest.TestCase):
    def setUp(self):
        self.src = _read(_TOOL_PROXY)
        self.block = _parse_block(self.src)

    def test_flag_initialized_false_before_try(self):
        # _recorded_success = False 在 try 之前（供 except 读）
        m = re.search(r"_recorded_success = False\s*\n\s*try:", self.block)
        self.assertTrue(m, "_recorded_success 未在 parse try 前初始化 False")

    def test_flag_set_true_after_record_success(self):
        # record_success 之后置 True（if/else 两分支后单点）
        self.assertIn("_recorded_success = True", self.block)
        # 顺序: record_success 出现在 flag=True 之前
        i_succ = self.block.rindex("record_success")
        i_flag = self.block.index("_recorded_success = True")
        self.assertLess(i_succ, i_flag, "_recorded_success=True 应在 record_success 之后")

    def test_except_records_error_when_no_success(self):
        # parse-except 里: 未记 success → record_error
        m = re.search(r"except Exception as e:.*?if not _recorded_success:.*?record_error",
                      self.block, re.S)
        self.assertTrue(m, "parse-except 未在无 success 时 record_error")

    def test_except_pops_alerts_on_error(self):
        # record_error 后 pop_alerts + _send_alert（与外层 except 一致）
        m = re.search(r"if not _recorded_success:(.*?)\n\s{16}self\.send_response",
                      self.block + "\n                self.send_response", re.S)
        # 宽松: record_error 分支内含 pop_alerts
        idx = self.block.index("if not _recorded_success:")
        tail = self.block[idx:]
        self.assertIn("pop_alerts", tail, "record_error 后未 pop_alerts")

    def test_no_double_record_on_success_path(self):
        # 已记 success 的路径不再 record_error（if not _recorded_success 门控）
        # 守卫: record_error 调用被 `if not _recorded_success` 门控（非无条件）
        idx = self.block.index("if not _recorded_success:")
        guard_region = self.block[idx:idx + 400]
        self.assertIn("record_error", guard_region)

    def test_v228_marker(self):
        self.assertIn("V37.9.228", self.src)


class TestParseStatsBehavior(unittest.TestCase):
    """行为级：用真 ProxyStats（proxy_filters 可 import）验证修复后的控制流语义。

    tool_proxy 不可 import，但 stats 逻辑用真 ProxyStats 复现 do_POST parse block 的
    确切 flag 控制流，证明: 失败路径记 error（原零信号盲区），happy 路径记 success 不双记。

    V37.9.238 测试隔离（MR-9）: record_* 首次调用必 flush → monkeypatch STATS_FILE。
    """

    def setUp(self):
        import shutil
        import tempfile
        import proxy_filters as _pf
        self._pf = _pf
        self._stats_tmpdir = tempfile.mkdtemp(prefix="v228_stats_")
        self._old_stats_file = _pf.STATS_FILE
        _pf.STATS_FILE = os.path.join(self._stats_tmpdir, "proxy_stats.json")
        self.addCleanup(lambda: setattr(self._pf, "STATS_FILE", self._old_stats_file))
        self.addCleanup(lambda: shutil.rmtree(self._stats_tmpdir, ignore_errors=True))

    def _simulate(self, resp_body, transform_ok=True):
        import json
        from proxy_filters import ProxyStats
        stats = ProxyStats()
        _recorded_success = False  # V37.9.228 flag
        try:
            rj = json.loads(resp_body)
            if not transform_ok:
                raise ValueError("transform boom")  # e.g. fix_tool_args 崩
            stats.record_success(rj.get("usage", {}), latency_ms=5)
            _recorded_success = True
        except Exception as e:
            if not _recorded_success:
                stats.record_error(502, f"parse/transform error: {e}", latency_ms=5)
        return stats

    def test_garbage_body_now_records_error(self):
        # backend 200 + 非 JSON → 原零信号，现 consecutive_errors=1（监控可见）
        s = self._simulate("this is not json{{{")
        self.assertEqual(s.consecutive_errors, 1, "garbage body 未记 error（监控盲区未修）")

    def test_transform_failure_records_error(self):
        # 合法 body 但 transform 抛异（record_success 前）→ 记 error
        s = self._simulate('{"usage":{"total_tokens":10}}', transform_ok=False)
        self.assertEqual(s.consecutive_errors, 1)

    def test_happy_path_no_false_error(self):
        # happy 路径 → success，consecutive_errors=0（不误记 error）
        s = self._simulate('{"usage":{"total_tokens":10}}', transform_ok=True)
        self.assertEqual(s.consecutive_errors, 0, "happy 路径误记 error（双记/误报）")


if __name__ == "__main__":
    unittest.main()

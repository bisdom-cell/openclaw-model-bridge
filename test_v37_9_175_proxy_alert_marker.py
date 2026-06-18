#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.175 守卫 — tool_proxy._send_alert 收编 notify.sh + [SYSTEM_ALERT] 标记。

背景（Path B 续 — proxy 内部告警路径 .py 盲区闭合）：
  proxy 自身告警 `_send_alert`（"🔴 Proxy 连续 N 次错误"/"context 临界"）此前用裸
  `openclaw message send --target <phone>` 推送，**无 [SYSTEM_ALERT] 标记 + 仅
  WhatsApp**。.py 路径躲过 Path B（V37.9.171-174）的 .sh 收编 + MRD-PUSH-ROUTE-001
  的 .sh-only 扫描。后果：这些告警恰在退化期（连续错误/context 超限）触发 →
  Gateway 写 sessions.json 作 assistant 消息 → filter_system_alerts 因无标记无从
  剥离 → PA 上下文污染 → NO_REPLY（2026-06-16 ev1082）。
  V37.9.175 修复：_send_alert 经 notify.sh --topic alerts 路由（标记自动加 + 重试/
  队列 + 微信/Discord 活跃通道扇出），notify.sh 不可达时 fallback 裸发但**仍手动加
  标记**（两条路径都防污染，标记必 present）。

测试三层（项目惯例）：
  1. 行为级 — 抽 _send_alert 源码 exec-with-mocks，断言 notify 路由 + fallback 标记。
     （tool_proxy.py 模块顶层 serve_forever() 无 __main__ guard，不可直接 import，
      故用 V37.9.132 同款 extract-source + exec 模式做行为验证。）
  2. 源码级守卫 — 镜像 INV-PA-001 新增 check，防回退。
  3. sabotage 反向验证 — 证守卫对旧裸发形态真有效（非 tautology）。
"""
import os
import re
import subprocess
import unittest
from unittest import mock

_REPO = os.path.dirname(os.path.abspath(__file__))
_TOOL_PROXY = os.path.join(_REPO, "tool_proxy.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_send_alert_src():
    """从 tool_proxy.py 抽 _send_alert 函数源码（module 顶层 serve_forever 不可 import）。"""
    src = _read(_TOOL_PROXY)
    m = re.search(r"\ndef _send_alert\(.*?\n(?=\ndef |\nclass )", src, re.S)
    assert m, "tool_proxy.py 未找到 _send_alert 函数（regex 失配）"
    return m.group(0)


def _load_send_alert():
    """把 _send_alert 源码 exec 进隔离 namespace（真 os/subprocess + 静默 log）。"""
    ns = {
        "__file__": "/fake/runtime/tool_proxy.py",  # 让 os.path.dirname 产 /fake/runtime
        "os": os,
        "subprocess": subprocess,
        "log": lambda *a, **k: None,
        "SYSTEM_ALERT_MARKER": "[SYSTEM_ALERT]",
    }
    exec(_extract_send_alert_src(), ns)
    return ns["_send_alert"]


class TestSendAlertBehavior(unittest.TestCase):
    """行为级：exec-with-mocks 验证两条路径都产出带标记的推送。"""

    def test_routes_through_notify_when_present(self):
        fn = _load_send_alert()
        with mock.patch("os.path.exists", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            fn("🔴 Proxy 连续 3 次错误！\n最近错误: HTTP 502")
        self.assertEqual(popen.call_count, 1, "应恰好一次 Popen")
        argv = popen.call_args[0][0]
        self.assertEqual(argv[0], "bash")
        self.assertEqual(argv[1], "-c")
        # 脚本经 notify.sh --topic alerts（标记自动加）
        self.assertIn("source", argv[2])
        self.assertIn("notify", argv[2])
        self.assertIn("--topic alerts", argv[2])
        # notify_sh 路径 + msg 经 argv 位置传参（不经 shell 解析，多行安全）
        self.assertTrue(argv[-2].endswith("notify.sh"),
                        f"应传 notify.sh 路径，实际 argv[-2]={argv[-2]!r}")
        self.assertEqual(argv[-1], "🔴 Proxy 连续 3 次错误！\n最近错误: HTTP 502")

    def test_notify_route_does_not_use_raw_message_send(self):
        """notify 路由不得退化为裸 openclaw message send。"""
        fn = _load_send_alert()
        with mock.patch("os.path.exists", return_value=True), \
             mock.patch("subprocess.Popen") as popen:
            fn("🟡 context 预警")
        argv = popen.call_args[0][0]
        self.assertNotIn("message", argv, "notify 路由不应直调 message send")

    def test_fallback_adds_marker_when_notify_missing(self):
        fn = _load_send_alert()
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.dict(os.environ,
                             {"OPENCLAW": "/x/openclaw", "OPENCLAW_PHONE": "+85200000000"}), \
             mock.patch("subprocess.Popen") as popen:
            fn("🔴 Qwen context 临界！prompt_tokens=250,000")
        self.assertEqual(popen.call_count, 1)
        argv = popen.call_args[0][0]
        self.assertIn("message", argv)
        self.assertIn("send", argv)
        mi = argv.index("--message")
        marked = argv[mi + 1]
        self.assertTrue(marked.lstrip().startswith("[SYSTEM_ALERT]"),
                        f"fallback 推送必须以 [SYSTEM_ALERT] 开头（filter 才能剥离），"
                        f"实际={marked!r}")
        # 原始内容保留
        self.assertIn("context 临界", marked)

    def test_fallback_no_double_marker(self):
        """已带标记的消息进 fallback 不得重复加标记。"""
        fn = _load_send_alert()
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.dict(os.environ,
                             {"OPENCLAW": "/x/openclaw", "OPENCLAW_PHONE": "+85200000000"}), \
             mock.patch("subprocess.Popen") as popen:
            fn("[SYSTEM_ALERT]\n已带标记")
        argv = popen.call_args[0][0]
        mi = argv.index("--message")
        self.assertEqual(argv[mi + 1].count("[SYSTEM_ALERT]"), 1,
                         "不得重复加 [SYSTEM_ALERT] 标记")

    def test_oserror_does_not_raise(self):
        """Popen 抛 OSError 时 _send_alert 不冒泡（hot-path 安全）。"""
        fn = _load_send_alert()
        with mock.patch("os.path.exists", return_value=True), \
             mock.patch("subprocess.Popen", side_effect=OSError("boom")):
            fn("test")  # 不应抛异常


class TestSendAlertSourceGuards(unittest.TestCase):
    """源码级守卫（镜像 INV-PA-001 新增 check，防回退）。"""

    def setUp(self):
        self.body = _extract_send_alert_src()

    def test_routes_topic_alerts(self):
        self.assertIn("--topic alerts", self.body)

    def test_sources_notify(self):
        self.assertIn("source", self.body)
        self.assertIn("notify", self.body)

    def test_fallback_uses_marker_constant(self):
        self.assertIn("SYSTEM_ALERT_MARKER", self.body)

    def test_no_raw_unmarked_send(self):
        # 退役旧裸发反模式：--message 不得直接发未加标记的 msg
        self.assertNotIn('"--message", msg,', self.body)

    def test_marker_imported_from_single_source(self):
        """SYSTEM_ALERT_MARKER 必须从 proxy_filters import（一物一形）。"""
        src = _read(_TOOL_PROXY)
        # import 块含 SYSTEM_ALERT_MARKER（来自 proxy_filters）
        self.assertRegex(src, r"from proxy_filters import[\s\S]*SYSTEM_ALERT_MARKER")

    def test_v37_9_175_marker(self):
        self.assertIn("V37.9.175", self.body)


class TestSabotageReverseValidation(unittest.TestCase):
    """证守卫对旧裸发形态真有效（非 tautology）。"""

    OLD_RAW = (
        '\ndef _send_alert(msg):\n'
        '    """后台发送 WhatsApp 告警（不阻塞请求处理）。"""\n'
        '    try:\n'
        '        openclaw = os.environ.get("OPENCLAW", "/opt/homebrew/bin/openclaw")\n'
        '        phone = os.environ.get("OPENCLAW_PHONE", "+85200000000")\n'
        '        subprocess.Popen(\n'
        '            [openclaw, "message", "send", "--target", phone, "--message", msg, "--json"],\n'
        '            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n'
        '        )\n'
        '    except OSError:\n'
        '        log(f"WARN: Failed to send alert")\n'
        '\ndef _next():\n'
    )

    def _run_source_guards(self, body):
        """复用 INV-PA-001/源码守卫同款断言，对任意 body 跑。"""
        assert "--topic alerts" in body, "缺 notify --topic alerts 路由"
        assert "source" in body and "notify" in body, "缺 source notify.sh"
        assert "SYSTEM_ALERT_MARKER" in body, "fallback 缺标记"
        assert '"--message", msg,' not in body, "残留裸发未加标记 msg"

    def test_guards_pass_on_current_source(self):
        # 当前源码必须通过全部守卫
        self._run_source_guards(_extract_send_alert_src())

    def test_guards_fail_on_old_raw_form(self):
        # 旧裸发形态必须被守卫抓到（否则守卫是 tautology）
        with self.assertRaises(AssertionError):
            self._run_source_guards(self.OLD_RAW)

    def test_old_raw_form_behaviorally_unmarked(self):
        """exec 旧裸发形态 → 复现 bug：推送无 [SYSTEM_ALERT] 标记。"""
        m = re.search(r"\ndef _send_alert\(.*?\n(?=\ndef )", self.OLD_RAW, re.S)
        ns = {"os": os, "subprocess": subprocess, "log": lambda *a, **k: None}
        exec(m.group(0), ns)
        with mock.patch.dict(os.environ,
                             {"OPENCLAW": "/x/openclaw", "OPENCLAW_PHONE": "+85200000000"}), \
             mock.patch("subprocess.Popen") as popen:
            ns["_send_alert"]("🔴 连续 3 次错误")
        argv = popen.call_args[0][0]
        mi = argv.index("--message")
        self.assertFalse(argv[mi + 1].lstrip().startswith("[SYSTEM_ALERT]"),
                         "旧形态应复现 bug：无标记（证明 V37.9.175 修复有意义）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""V37.9.306 守卫: kb_evening LLM 调用韧性 (2026-08-14 12:30 watchdog CORE 告警)。

血案: 08-13 22:00 kb_evening "ERROR: LLM 晚间整理失败: TimeoutError: timed out"
→ status llm_failed → 整日晚间摘要永久丢失 (次日窗口已换日期, 内容不可补)。

根因是**结构性不对称**非单次瞬态 — 同一份告警里 rss_blogs/s2 的 LLM 错误都带
"attempt 1/3" (有重试, 自愈), 唯独 evening 是裸的单次失败:

  | job              | 频率      | 超时  | 重试 | 单次瞬态后果 |
  |------------------|-----------|-------|------|--------------|
  | kb_harvest_chat  | 每日 06:00| 300s  | 1    | 自愈         |
  | kb_evening (旧)  | 每日 22:00| 120s  | 0    | 整日丢失     |

且 120s 客户端预算比**服务端还紧** (config proxy.backend_timeout_seconds=300 /
adapter timeout_ms=300000) → 后端还在推理时客户端先放弃。V37.9.129/130 已为
harvest 上调到 300s (doubao 大请求给足时间), evening 当时漏改。

历史佐证: rc.call_llm 自身注释记 2026-04-14/15 连续两天 22:00 告警;
V37.9.213 F3 记 evening 07-01 缺失 = 真 job 瞬态失败。

修复: 共享 rc.call_llm 加 retries 参数 (默认 0 = 既有调用方零行为变化) +
evening 侧 300s/retry=1 预算, 经 _default_llm_caller 保持 caller(prompt)
单参数注入契约 (MR-8: evening 不自定义 call_llm)。
"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb_evening_collect as ev
import kb_review_collect as rc

REPO = os.path.dirname(os.path.abspath(__file__))


class TestSharedCallLlmRetry(unittest.TestCase):
    """共享 call_llm 的 retries 参数语义 (行为级)。"""

    def test_default_zero_retries_single_attempt(self):
        """默认 retries=0 → 恰 1 次尝试 (kb_review 周报等既有调用方零行为变化)。"""
        calls = []

        def fake_once(prompt, timeout=None, url=None, model=None):
            calls.append(timeout)
            return (False, "", "TimeoutError: timed out")

        with mock.patch.object(rc, "_call_llm_once", fake_once):
            ok, content, reason = rc.call_llm("p")
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1, "默认不得重试 (既有调用方契约)")
        self.assertIn("TimeoutError", reason)

    def test_retry_recovers_transient(self):
        """血案回归: 首次 TimeoutError + 重试成功 → 整体成功 (当日摘要不丢)。"""
        seq = [(False, "", "TimeoutError: timed out"), (True, "x" * 120, "")]
        calls = []

        def fake_once(prompt, timeout=None, url=None, model=None):
            calls.append(1)
            return seq[len(calls) - 1]

        with mock.patch.object(rc, "_call_llm_once", fake_once):
            with mock.patch("sys.stderr", new=io.StringIO()):
                ok, content, reason = rc.call_llm("p", retries=1)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(content, "x" * 120)

    def test_exhausted_retries_stay_honest(self):
        """重试耗尽仍返回诚实失败 (不伪装成功 — call_llm 的 fail-fast 契约不变)。"""
        def fake_once(prompt, timeout=None, url=None, model=None):
            return (False, "", "HTTP 502: Bad Gateway")

        with mock.patch.object(rc, "_call_llm_once", fake_once):
            with mock.patch("sys.stderr", new=io.StringIO()):
                ok, content, reason = rc.call_llm("p", retries=1)
        self.assertFalse(ok)
        self.assertEqual(content, "")
        self.assertIn("502", reason)

    def test_retry_logs_to_stderr(self):
        """重试可观测 (MR-11 stderr, 不污染 stdout 的 JSON 契约)。"""
        def fake_once(prompt, timeout=None, url=None, model=None):
            return (False, "", "TimeoutError: timed out")

        buf = io.StringIO()
        with mock.patch.object(rc, "_call_llm_once", fake_once):
            with mock.patch("sys.stderr", new=buf):
                rc.call_llm("p", retries=1)
        self.assertIn("attempt 1/2", buf.getvalue())

    def test_timeout_passed_through(self):
        """timeout 参数透传到每次尝试 (evening 的 300s 预算真实生效)。"""
        seen = []

        def fake_once(prompt, timeout=None, url=None, model=None):
            seen.append(timeout)
            return (False, "", "TimeoutError")

        with mock.patch.object(rc, "_call_llm_once", fake_once):
            with mock.patch("sys.stderr", new=io.StringIO()):
                rc.call_llm("p", timeout=300, retries=1)
        self.assertEqual(seen, [300, 300])


class TestEveningBudget(unittest.TestCase):
    """evening 侧预算常量 + 默认 caller 接线。"""

    def test_timeout_at_least_backend_budget(self):
        """evening 客户端超时不得紧于服务端预算 (config backend_timeout_seconds=300)
        — 血案根因: 120s 客户端在后端仍推理时先放弃。"""
        self.assertGreaterEqual(ev.EVENING_LLM_TIMEOUT, 300)

    def test_retry_enabled(self):
        """每日 job 必须有重试 (镜像同族 kb_harvest_chat LLM_RETRY=1)。"""
        self.assertGreaterEqual(ev.EVENING_LLM_RETRY, 1)

    def test_default_caller_uses_evening_budget(self):
        """_default_llm_caller 真把 evening 预算传给 rc.call_llm (防接线 inert)。"""
        seen = {}

        def fake_call(prompt, timeout=None, retries=None):
            seen["timeout"] = timeout
            seen["retries"] = retries
            return (True, "y" * 120, "")

        with mock.patch.object(rc, "call_llm", fake_call):
            ok, content, reason = ev._default_llm_caller("p")
        self.assertTrue(ok)
        self.assertEqual(seen["timeout"], ev.EVENING_LLM_TIMEOUT)
        self.assertEqual(seen["retries"], ev.EVENING_LLM_RETRY)

    def test_default_caller_single_arg_contract(self):
        """caller(prompt) 单参数契约 (llm_caller 注入点是 lambda p: (...),
        改成多参数会静默破坏全部注入测试)。"""
        import inspect
        sig = inspect.signature(ev._default_llm_caller)
        required = [p for p in sig.parameters.values()
                    if p.default is inspect.Parameter.empty]
        self.assertEqual(len(required), 1)

    def test_run_uses_default_caller_when_not_injected(self):
        """源码守卫: run() 默认走 _default_llm_caller 而非裸 rc.call_llm
        (裸调 = 退回 120s/无重试的血案配置)。"""
        with open(os.path.join(REPO, "kb_evening_collect.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("else _default_llm_caller", src)
        self.assertNotIn("if llm_caller is not None else rc.call_llm", src)

    def test_evening_still_does_not_redefine_call_llm(self):
        """MR-8 一物一形保持: evening 不得自定义 call_llm
        (与 test_kb_evening.test_does_not_redefine_call_llm 同契约, 防本次修复
        引入第二种 LLM 调用形态)。"""
        with open(os.path.join(REPO, "kb_evening_collect.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("def call_llm(", src)


class TestEndToEndRunResilience(unittest.TestCase):
    """run() 注入契约不变。

    注: "重试耗尽 → status=llm_failed 不伪装成功" 的 V37.5 契约已由
    test_kb_evening.py 端到端覆盖 (注入 mock_llm 返回失败 → 断言
    result["status"]=="llm_failed" + 无 evening_markdown/wa_message),
    此处不重复造等价断言 (避免假覆盖)。
    """

    def test_injected_caller_unaffected(self):
        """注入的单参数 lambda 仍工作 (本次修复不改注入契约)。"""
        import inspect
        sig = inspect.signature(ev.run)
        self.assertIn("llm_caller", sig.parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)

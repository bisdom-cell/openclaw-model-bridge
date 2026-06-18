#!/usr/bin/env python3
"""V37.9.156 → V37.9.174 守卫: preflight check 16 推送 smoke test 路由.

历史: V37.9.156 修 4.27 whatsapp 冷调用假失败（gateway-timeout 签名 → warn 不 fail，
因 exit code 在 4.27 下不可信但消息已送达）。

V37.9.174 (Path B 收尾): WhatsApp 临时禁用后测它无意义 → check 16 改测**真实推送管线**
notify.sh（微信 + Discord + 重试/队列），**退役** V37.9.156 的 4.27 whatsapp exit-code
不可信 hack（notify 返回码权威：发出≥1 即 0）+ 合并原 whatsapp/discord 两段独立测
（notify 一次覆盖两通道）。

本测试守新行为：push test 走 notify、无裸 whatsapp push、4.27 timeout hack 已退役。
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
PREFLIGHT = REPO / "preflight_check.sh"


class TestPushTestRoutesThroughNotify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = PREFLIGHT.read_text(encoding="utf-8")

    def test_v37_9_174_marker(self):
        self.assertIn("V37.9.174 PathB 收尾", self.src, "缺 V37.9.174 收尾 marker")

    def test_push_test_uses_notify(self):
        # check 16 push test 必须走 notify（测真实推送管线），不再裸 whatsapp send
        self.assertIn('notify "🔧 preflight push test', self.src,
                      "preflight push test 应走 notify（测活跃通道）")

    def test_no_raw_whatsapp_push_test(self):
        # 退役：不得再用 openclaw message send --channel whatsapp 做 push test
        self.assertNotIn(
            'message send --channel whatsapp --target "${OPENCLAW_PHONE:-+85200000000}" --message "🔧 preflight push test',
            self.src,
            "preflight push test 仍用裸 whatsapp — 应走 notify",
        )

    def test_4_27_timeout_hack_retired(self):
        # 退役 V37.9.156 的 gateway-timeout→warn elif（notify 返回码权威，不再需要）
        self.assertNotIn(
            'elif echo "$PUSH_STDERR" | grep -qiE "gateway timeout',
            self.src,
            "4.27 whatsapp timeout hack 应已退役（改 notify 后返回码可信）",
        )

    def test_notify_failure_still_fails(self):
        # notify 全通道未发出 → fail（不放过真故障）
        self.assertRegex(self.src, r'fail "推送通道失败（notify',
                         "notify 全失败仍必须 fail")

    def test_sources_notify_before_push_test(self):
        # push test 前 source notify.sh（preflight 之前未 source）
        self.assertIn('source "$_ns"', self.src,
                      "push test 前应 source notify.sh")

    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", str(PREFLIGHT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"preflight_check.sh 语法错误: {r.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

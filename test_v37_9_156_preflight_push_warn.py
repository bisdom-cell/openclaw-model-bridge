#!/usr/bin/env python3
"""V37.9.156 守卫: preflight check 16 推送 smoke test 的 4.27 冷调用假失败修复.

背景 (用户 2026-06-15 实测): 4.27 openclaw CLI 客户端硬编码 10s 超时. 冷 WS 首发 >10s →
CLI 在 10s 放弃返回 `gateway timeout after 10000ms` exit 非0, 但 Gateway 服务端继续把消息
送达 (用户 13:16 真收到 preflight 测试消息). 旧 check 16 信 exit code → 假 FAIL 硬阻塞 preflight.

修复: 超时签名 stderr → 降级 fail→warn (消息已送达, 真推送有 notify.sh 兜底); 真故障
(连接拒绝/未链接等非超时 stderr) 仍 fail. warn 不触发 exit 1 → preflight 不再因 4.27 假失败.
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
PREFLIGHT = REPO / "preflight_check.sh"

# 与 preflight_check.sh 中 elif 条件逐字一致的判别器 (literal-as-guard: drift 则 source 守卫先 fail)
TIMEOUT_PATTERN = r"gateway timeout|timeout after [0-9]+ *ms|timed out"


class TestSourceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = PREFLIGHT.read_text(encoding="utf-8")

    def test_v37_9_156_marker_present(self):
        self.assertIn("V37.9.156", self.src, "缺 V37.9.156 修复 marker")

    def test_timeout_elif_branch_exists(self):
        # 必须有一个 elif 用 grep 匹配超时签名
        self.assertRegex(
            self.src,
            r'elif\s+echo\s+"\$PUSH_STDERR"\s+\|\s+grep\s+-qiE\s+"' + re.escape(TIMEOUT_PATTERN) + r'"',
            "缺 4.27 超时签名 elif 判别分支 (literal-as-guard: 与本测试 TIMEOUT_PATTERN 必须逐字一致)",
        )

    def test_timeout_branch_warns_not_fails(self):
        # elif 分支体必须调 warn 且含'消息通常已送达', 不得调 fail
        m = re.search(
            r'elif\s+echo\s+"\$PUSH_STDERR"\s+\|\s+grep\s+-qiE.*?\n(.*?)\n\s*else',
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "未能定位超时 elif 分支体")
        branch = m.group(1)
        self.assertIn("warn ", branch, "4.27 超时分支必须 warn (不硬阻塞 preflight)")
        self.assertNotRegex(branch, r"\bfail\s+\"", "4.27 超时分支不得调 fail (那正是假失败 bug)")
        self.assertIn("消息通常已送达", branch, "warn 文案应说明消息已送达 exit code 不可信")

    def test_real_failure_still_fails_as_last_branch(self):
        # 最后的 else 分支 (真故障) 仍必须 fail — 降级只对超时签名, 真故障不放过
        self.assertRegex(
            self.src,
            r'else\s*\n\s*fail\s+"WhatsApp 推送失败',
            "真故障 (非超时 stderr) 仍必须走 fail 分支",
        )

    def test_ordering_timeout_before_generic_fail(self):
        # 超时 elif 必须出现在 generic fail 之前 (否则永远走不到 warn)
        elif_idx = self.src.find('elif echo "$PUSH_STDERR" | grep -qiE')
        fail_idx = self.src.find('fail "WhatsApp 推送失败')
        self.assertGreater(elif_idx, 0, "超时 elif 不存在")
        self.assertGreater(fail_idx, 0, "generic fail 不存在")
        self.assertLess(elif_idx, fail_idx, "超时 elif 必须在 generic fail 之前 (否则降级永不触发)")

    def test_timeout_branch_updates_rate_limit_timestamp(self):
        # 消息已送达 → 应更新 PUSH_TEST_LAST 尊重速率限制, 避免下次 preflight 重复发探针
        m = re.search(
            r'elif\s+echo\s+"\$PUSH_STDERR"\s+\|\s+grep\s+-qiE.*?\n(.*?)\n\s*else',
            self.src,
            re.DOTALL,
        )
        self.assertIn('date +%s > "$PUSH_TEST_LAST"', m.group(1),
                      "4.27 超时(已送达)分支应更新速率限制时间戳")

    def test_braced_var_adjacent_to_fullwidth_paren(self):
        # V37.9.141 教训: 全角括号紧贴变量必须 ${VAR} brace (macOS bash 3.2 quirk)
        # 新增 warn 行含 （退出码 ${PUSH_RC}） — 确认 braced
        for m in re.finditer(r"\$\{?PUSH_RC\}?）", self.src):
            self.assertTrue(m.group(0).startswith("${"),
                            f"全角括号紧贴 PUSH_RC 必须 brace: {m.group(0)!r}")

    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", str(PREFLIGHT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"preflight_check.sh 语法错误: {r.stderr}")


class TestTimeoutDiscriminatorBehavior(unittest.TestCase):
    """行为级: 用与 preflight 逐字一致的 grep pattern 验证判别器 — 超时签名→warn, 真故障→fail."""

    def _matches(self, stderr: str) -> bool:
        r = subprocess.run(
            ["grep", "-qiE", TIMEOUT_PATTERN],
            input=stderr, text=True,
        )
        return r.returncode == 0

    def test_v37_9_156_real_signature_matches(self):
        # 用户实测的真实 4.27 签名
        self.assertTrue(self._matches("Error: gateway timeout after 10000ms"))

    def test_timeout_variants_match(self):
        for s in [
            "gateway timeout after 10000ms",
            "GATEWAY TIMEOUT",
            "request timed out",
            "send timeout after 5000 ms",
            "operation timeout after 8000ms",
        ]:
            self.assertTrue(self._matches(s), f"超时签名应匹配(→warn): {s!r}")

    def test_real_failures_do_not_match(self):
        # 真故障必须不匹配 → 走 fail 分支 (不被误降级为 warn)
        for s in [
            "Error: connection refused",
            "Error: channel not linked",
            "Error: gateway not running",
            "Error: authentication failed",
            "Error: invalid target",
            "",
        ]:
            self.assertFalse(self._matches(s), f"真故障不应匹配(应 fail): {s!r}")


class TestReverseValidation(unittest.TestCase):
    def test_sabotage_note(self):
        # 反向验证: 删除超时 elif → 4.27 超时 stderr 落到 generic fail → preflight 假 FAIL 复发.
        # test_timeout_elif_branch_exists + test_ordering 守卫该回归. 此处文档化反向验证意图.
        src = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('grep -qiE "gateway timeout', src,
                      "删除超时 elif 会让 4.27 假失败复发 — 此守卫防回归")


if __name__ == "__main__":
    unittest.main(verbosity=2)

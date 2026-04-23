"""V37.9.13 — restart.sh 架构清理守卫单测

2026-04-22 V37.9.12.1 发现: restart.sh 的 adapter/proxy 用 `nohup python3` 启动
manual 进程，但 launchd 同时通过 com.openclaw.adapter.plist / com.openclaw.proxy.plist
KeepAlive 管理。两路同时抢占 :5001 / :5002 端口，launchd 侧持续 crash-loop。

V37.9.13 修复: 单一 manager 原则 — 所有服务（gateway/adapter/proxy）都走 launchd。
restart.sh 改用 `launchctl kickstart -k` 统一重启。plist 不存在时 fallback 到 nohup
保留向后兼容（dev 环境 / 未装 plist）。

本测试锁定:
  1. restart.sh 包含 restart_via_launchd 辅助函数 + kickstart -k 调用
  2. adapter/proxy 主路径走 launchctl，不直接 nohup
  3. 必要时 fallback 到 nohup (plist 缺失场景)
  4. 每个服务都有 post-start 健康验证（V37.8.13 模式从 Gateway 扩展到 adapter/proxy）
  5. 单一 manager 契约: set -e 下端口清理顺序 + 不双跑 nohup + launchd
"""

import os
import re
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


def _read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


class TestRestartVialaunchdHelper(unittest.TestCase):
    """restart_via_launchd 辅助函数存在且签名正确。"""

    def setUp(self):
        self.src = _read("restart.sh")

    def test_helper_function_defined(self):
        self.assertIn(
            "restart_via_launchd()",
            self.src,
            "restart.sh 必须定义 restart_via_launchd 辅助函数 (V37.9.13)",
        )

    def test_helper_uses_kickstart_k(self):
        """launchctl kickstart -k 是修改后的主路径。"""
        self.assertRegex(
            self.src,
            r"launchctl\s+kickstart\s+-k",
            "helper 必须使用 launchctl kickstart -k (modern idempotent API)",
        )

    def test_helper_falls_back_to_bootstrap(self):
        """kickstart 失败（服务未加载）时必须 bootstrap。"""
        self.assertIn(
            "launchctl bootstrap",
            self.src,
            "helper 必须在 kickstart 失败时 fallback 到 bootstrap",
        )

    def test_helper_has_health_verification(self):
        """helper 必须有 curl /health 健康验证循环（V37.8.13 模式扩展）。"""
        match = re.search(
            r"restart_via_launchd\(\)\s*\{(.*?)\n\}",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "restart_via_launchd 函数体未找到")
        body = match.group(1)
        self.assertIn(
            "curl",
            body,
            "helper 必须包含 curl 健康探测",
        )
        self.assertIn(
            "/health",
            body,
            "helper 必须 probe /health 端点",
        )
        self.assertRegex(
            body,
            r"for\s+_attempt\s+in\s+1\s+2\s+3",
            "helper 必须有 for-loop 重试循环（服务启动需数秒）",
        )

    def test_helper_returns_code_2_when_plist_missing(self):
        """plist 不存在时返回 2，让调用方走 nohup fallback。"""
        match = re.search(
            r"restart_via_launchd\(\)\s*\{(.*?)\n\}",
            self.src,
            re.DOTALL,
        )
        body = match.group(1)
        # 必须有 `return 2` 表示 launchd 不可用
        self.assertRegex(
            body,
            r"return\s+2",
            "plist 不存在时必须 return 2（区分于 return 1=launchd 失败）",
        )

    def test_helper_checks_launchctl_availability(self):
        """helper 必须 defensively 检查 launchctl 可用（dev 环境 / Linux）。"""
        match = re.search(
            r"restart_via_launchd\(\)\s*\{(.*?)\n\}",
            self.src,
            re.DOTALL,
        )
        body = match.group(1)
        self.assertRegex(
            body,
            r"command\s+-v\s+launchctl",
            "helper 必须检测 launchctl 是否可用",
        )


class TestAdapterViaLaunchd(unittest.TestCase):
    """Adapter (:5001) 主路径走 launchd，nohup 仅为 fallback。"""

    def setUp(self):
        self.src = _read("restart.sh")

    def test_adapter_uses_helper(self):
        """Adapter 启动必须调 restart_via_launchd，不直接 nohup。"""
        # 找 Adapter 段落
        adapter_section = re.search(
            r"# ── Adapter.*?(?=# ── Tool Proxy|#\s+──\s+Tool Proxy|$)",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(adapter_section, "Adapter 段落未找到")
        section = adapter_section.group(0)
        self.assertIn(
            'restart_via_launchd "com.openclaw.adapter" 5001',
            section,
            "Adapter 必须通过 restart_via_launchd 启动（label=com.openclaw.adapter, port=5001）",
        )

    def test_adapter_nohup_is_fallback_only(self):
        """nohup adapter 只在 helper 返回 2（plist 缺失）时触发。"""
        # 找 Adapter 段
        section = self._adapter_section()
        # nohup 必须在条件分支内，不是顶层
        nohup_match = re.search(r'nohup\s+python3\s+["\$]*\w*/?adapter\.py', section)
        self.assertIsNotNone(nohup_match, "Adapter fallback nohup 路径必须存在")
        # nohup 之前必须有 `_ad_rc -eq 2` 判定（说明在 fallback 分支里）
        pre_nohup = section[: nohup_match.start()]
        self.assertRegex(
            pre_nohup,
            r"_ad_rc.*-eq\s+2",
            "Adapter nohup 必须在 _ad_rc=2 fallback 分支内（plist 缺失场景）",
        )

    def test_adapter_health_port_is_5001(self):
        """restart_via_launchd 被调时 port 参数正确。"""
        self.assertIn(
            'restart_via_launchd "com.openclaw.adapter" 5001',
            self.src,
            "Adapter port 必须是 5001",
        )

    def _adapter_section(self):
        m = re.search(
            r"# ── Adapter.*?(?=# ── Tool Proxy|#\s+──\s+Tool Proxy)",
            self.src,
            re.DOTALL,
        )
        return m.group(0) if m else ""


class TestProxyViaLaunchd(unittest.TestCase):
    """Tool Proxy (:5002) 主路径走 launchd，nohup 仅为 fallback。"""

    def setUp(self):
        self.src = _read("restart.sh")

    def test_proxy_uses_helper(self):
        section = self._proxy_section()
        self.assertIn(
            'restart_via_launchd "com.openclaw.proxy" 5002',
            section,
            "Tool Proxy 必须通过 restart_via_launchd 启动",
        )

    def test_proxy_nohup_is_fallback_only(self):
        section = self._proxy_section()
        nohup_match = re.search(r'nohup\s+python3\s+["\$]*\w*/?tool_proxy\.py', section)
        self.assertIsNotNone(nohup_match, "Tool Proxy fallback nohup 路径必须存在")
        pre_nohup = section[: nohup_match.start()]
        self.assertRegex(
            pre_nohup,
            r"_px_rc.*-eq\s+2",
            "Tool Proxy nohup 必须在 _px_rc=2 fallback 分支内",
        )

    def _proxy_section(self):
        m = re.search(
            r"# ── Tool Proxy.*?(?=# ── #48703|# ──)",
            self.src,
            re.DOTALL,
        )
        return m.group(0) if m else ""


class TestSingleManagerInvariant(unittest.TestCase):
    """V37.9.13 单一 manager 契约: adapter/proxy 不能同时走 launchd + nohup 主路径。

    具体威胁: 如果有人回退把 `nohup python3 adapter.py` 放到顶层（非 fallback 分支），
    又保留 plist KeepAlive，就会重现 V37.9.12.1 双管理 crash-loop 血案。
    """

    def setUp(self):
        self.src = _read("restart.sh")

    def test_no_unconditional_nohup_adapter_at_top_level(self):
        """nohup adapter.py 不得出现在"未被 plist 检查保护的"顶层位置。

        检查策略: 找所有 nohup python3 ...adapter.py 出现位置，前 300 字符内必须
        包含"plist not found"或"_ad_rc -eq 2"（表明在 fallback 分支里）。
        """
        matches = list(re.finditer(r'nohup\s+python3\s+["\$][^"\n]*/adapter\.py', self.src))
        self.assertGreaterEqual(len(matches), 1, "至少应有 1 个 nohup adapter（作 fallback）")
        for m in matches:
            ctx = self.src[max(0, m.start() - 300) : m.start()]
            self.assertTrue(
                "plist not found" in ctx or "_ad_rc" in ctx,
                f"nohup adapter at offset {m.start()} 不在 fallback 分支内 "
                f"(V37.9.12.1 双管理血案的防线)",
            )

    def test_no_unconditional_nohup_proxy_at_top_level(self):
        matches = list(re.finditer(r'nohup\s+python3\s+["\$][^"\n]*/tool_proxy\.py', self.src))
        self.assertGreaterEqual(len(matches), 1, "至少应有 1 个 nohup tool_proxy（作 fallback）")
        for m in matches:
            ctx = self.src[max(0, m.start() - 300) : m.start()]
            self.assertTrue(
                "plist not found" in ctx or "_px_rc" in ctx,
                f"nohup tool_proxy at offset {m.start()} 不在 fallback 分支内",
            )

    def test_v37_9_13_blood_lesson_comment(self):
        """头部注释必须记录 V37.9.13 架构清理背景。"""
        self.assertIn("V37.9.13", self.src)
        # 应提到双管理或 manual nohup 冲突字样
        self.assertTrue(
            "双管理" in self.src or "double management" in self.src.lower(),
            "注释必须说明双管理冲突背景（V37.9.12.1 血案）",
        )

    def test_gateway_logic_preserved(self):
        """V37.8.13 Gateway 健康验证逻辑必须完整保留（不得回归）。"""
        self.assertIn("V37.8.13", self.src)
        self.assertIn("GATEWAY_HEALTHY=false", self.src)
        self.assertIn("Gateway failed to become healthy", self.src)

    def test_48703_hotfix_preserved(self):
        """#48703 listeners Map hotfix 段必须保留（与 V37.9.13 重构无关）。"""
        self.assertIn("#48703 hotfix", self.src)


class TestShellExecutability(unittest.TestCase):
    """restart.sh 必须通过 bash -n 语法检查。"""

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", os.path.join(REPO, "restart.sh")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"bash -n failed: stderr={result.stderr}",
        )

    def test_set_euo_pipefail(self):
        """严格模式必须保留。"""
        src = _read("restart.sh")
        self.assertIn("set -euo pipefail", src)

    def test_return_code_captured_safely_under_set_e(self):
        """`set -e` 下 `restart_via_launchd || _rc=$?` 模式必须使用 `||` 避免脚本退出。"""
        src = _read("restart.sh")
        # 期望模式: `restart_via_launchd ... || _ad_rc=$?`
        self.assertRegex(
            src,
            r"restart_via_launchd\s+\"com\.openclaw\.adapter\".*\|\|\s+_ad_rc=\$\?",
            "Adapter 调用必须用 || _ad_rc=$? 捕获返回码（set -e 安全）",
        )
        self.assertRegex(
            src,
            r"restart_via_launchd\s+\"com\.openclaw\.proxy\".*\|\|\s+_px_rc=\$\?",
            "Proxy 调用必须用 || _px_rc=$? 捕获返回码",
        )


class TestRuntimeHelperBehavior(unittest.TestCase):
    """运行时验证 helper 在 launchctl 不可用环境（Linux dev）下返回 2。

    直接 source restart.sh 调用 helper 函数会触发 set -e 退出问题，因此改为
    独立 bash subprocess 运行。
    """

    def test_helper_returns_2_when_launchctl_absent(self):
        """在没有 launchctl 或 plist 的环境，helper 必须返回 2（不是崩溃）。"""
        restart_path = os.path.join(REPO, "restart.sh")
        # Use env var to avoid .format() brace conflict with awk's /^}/ pattern
        script = r'''
set -euo pipefail
export PATH="/no/such/dir:/usr/bin:/bin"
# source helper definition only (skip main flow)
helper_def=$(awk '/^restart_via_launchd\(\)/,/^}/' "$RESTART_SH")
eval "$helper_def"
# call with nonexistent plist
set +e
restart_via_launchd "com.fake.label" 9999 "/tmp/definitely_not_a_plist.$$" "FakeSvc" >/dev/null 2>&1
rc=$?
set -e
echo "$rc"
'''
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "RESTART_SH": restart_path},
        )
        self.assertEqual(
            result.stdout.strip(), "2",
            f"helper 应返回 2（launchctl 不可用或 plist 缺失），实际: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

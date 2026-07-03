#!/usr/bin/env python3
"""V37.9.232 守卫 — 治理 runtime 所跑测试必须对生产 env hermetic。

血案背景（2026-07-03 07:00 Mac Mini 治理审计红灯，dev 完整复现）:
    V37.9.218/222 flip 后 Mac Mini 生产 env 有 FALLBACK_ORDER + PROVIDER=doubao_21。
    治理 runtime python_assert 以 subprocess 跑测试文件，子进程继承生产 env →
    两处测试被生产 env 污染:
    ① test_adapter._exec_build 的 mock env 用 `_os.environ.copy()` 起步 →
       FALLBACK_ORDER 泄漏进被测 _build_fallback_chain → 走 V37.9.218 分支调
       reg.get() → V37.9.129 时代的 MockReg 无 .get → AttributeError →
       INV-FALLBACK-EXCLUDE-001 每日 07:00 ❌（其声明"mock 路径 env-independent"为假）
    ② test_v37_9_77_enforcement.TestAdapterResolvePrimaryProvider.setUp 用
       `os.environ.setdefault("PROVIDER", "qwen")` → 生产 env 已有
       PROVIDER=doubao_21 时 setdefault 无效 → import adapter 得 doubao_21 →
       6 个断言 name=="qwen" 的测试全挂 → INV-ROUTER-001 ❌（dev 复现时浮出，
       Mac Mini 07:00 cron env key 集合不同暂未触发 = 潜伏）

    这是 MR-9「测试污染生产」的镜像形态: **生产 env 污染测试**。dev 无这些 env
    永远绿 → 假 hermetic 只在生产环境暴露（dev-production 接缝, 日落法 #34）。

本守卫 = 判别器固化: 用生产同款 env 注入真 subprocess 跑两套件，任何未来改动让
测试重新依赖进程 env 会在 dev CI 立即红灯，不再等 Mac Mini 07:00 审计。
"""

import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# Mac Mini 生产同款 env（V37.9.218 FALLBACK_ORDER + V37.9.222 doubao_21 primary flip;
# key 全用假值 — 测试永不真调任何 provider, 只让 available()/import 路径走生产形态）
_PROD_LIKE_ENV = {
    "FALLBACK_ORDER": "doubao_21,deepseek_full,doubao,deepseek,qwen",
    "PROVIDER": "doubao_21",
    "FALLBACK_PROVIDER": "deepseek_full",
    "ARK_21_API_KEY": "test-fake-key",
    "ARK_API_KEY": "test-fake-key",
    "DEEPSEEK_FULL_API_KEY": "test-fake-key",
    "DEEPSEEK_API_KEY": "test-fake-key",
    "REMOTE_API_KEY": "test-fake-key",
}


def _run_with_prod_env(args):
    env = dict(os.environ)
    env.update(_PROD_LIKE_ENV)
    return subprocess.run(
        [sys.executable] + args,
        capture_output=True, text=True, timeout=120, cwd=REPO, env=env,
    )


class TestGovernanceTestsHermeticUnderProdEnv(unittest.TestCase):
    """两个治理 runtime 所跑套件在生产同款 env 注入下必须绿（判别器固化）。"""

    def test_inv_fallback_exclude_tests_pass_with_prod_env(self):
        """INV-FALLBACK-EXCLUDE-001 runtime 的 3 个测试 — 血案 ① 复现命令"""
        proc = _run_with_prod_env([
            "-m", "unittest",
            "test_adapter.TestHotReloadFunctional.test_v37_9_129_auto_chain_excludes_geo_blocked",
            "test_adapter.TestHotReloadFunctional.test_v37_9_129_no_exclude_keeps_provider",
            "test_adapter.TestHotReloadFunctional.test_v37_9_129_explicit_fallback_also_excluded",
        ])
        self.assertEqual(proc.returncode, 0,
                         f"生产 env 注入下 V37.9.129 测试必须绿（假 hermetic 回归）:\n"
                         f"{proc.stderr[-800:]}")

    def test_inv_router_suite_passes_with_prod_env(self):
        """INV-ROUTER-001 runtime 的 V37.9.77 全套件 — 血案 ② 复现命令"""
        proc = _run_with_prod_env(["test_v37_9_77_enforcement.py"])
        self.assertEqual(proc.returncode, 0,
                         f"生产 env 注入下 V37.9.77 套件必须绿（PROVIDER 泄漏回归）:\n"
                         f"{proc.stderr[-800:]}")


class TestSourceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "test_adapter.py"), encoding="utf-8") as f:
            cls.adapter_tests = f.read()
        with open(os.path.join(REPO, "test_v37_9_77_enforcement.py"), encoding="utf-8") as f:
            cls.router_tests = f.read()

    def _exec_build_region(self):
        i = self.adapter_tests.index("def _exec_build(")
        return self.adapter_tests[i:i + 1800]

    def test_exec_build_starts_from_empty_env(self):
        """_exec_build mock env 必须空字典起步（不 copy 真实进程 env）。
        注: 断言精确代码形态（含赋值前缀）— docstring 引用退役模式 `_os.environ.copy()`
        作文档不误触（V37.9.178 教训: 守卫别被自己的注释咬）。"""
        region = self._exec_build_region()
        self.assertIn("env = {}", region)
        self.assertNotIn("env = _os.environ.copy()", region,
                         "回退到 env = _os.environ.copy() = 生产 env 重新泄漏进 mock")

    def test_router_setup_forces_baseline_env(self):
        """setUp 必须 patch.dict 强制 PROVIDER=qwen（setdefault 在生产 env 下无效）+ pop 路由类 env"""
        i = self.router_tests.index("class TestAdapterResolvePrimaryProvider")
        region = self.router_tests[i:i + 2400]
        self.assertIn('patch.dict(os.environ', region)
        self.assertIn('"PROVIDER": "qwen"', region)
        self.assertIn('"FALLBACK_ORDER"', region)
        self.assertNotIn('os.environ.setdefault("PROVIDER"', region,
                         "回退到 setdefault = 生产 PROVIDER=doubao_21 重新泄漏")

    def test_v37_9_232_markers(self):
        self.assertIn("V37.9.232", self.adapter_tests)
        self.assertIn("V37.9.232", self.router_tests)


if __name__ == "__main__":
    unittest.main(verbosity=2)

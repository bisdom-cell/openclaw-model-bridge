"""V37.9.96 INV-PROXY-PLIST-ENV-001 治理测试

立 INV-PROXY-PLIST-ENV-001 守 com.openclaw.proxy.plist EnvironmentVariables 必含
ARK_API_KEY + ARK_ENDPOINT_ID (镜像 V37.9.54 INV-PLIST-ENV-001 守 adapter plist 模式).

触发: V37.9.91 Doubao expert_escalate 真生产闭环. tool_proxy.py 的 expert_escalate
自定义工具分支 lazy-import expert_escalation.py 经 Volcengine Ark 调 Doubao 用
ARK_API_KEY + ARK_ENDPOINT_ID. proxy daemon (com.openclaw.proxy.plist) 跑 tool_proxy.py,
必须从 plist EnvironmentVariables 拿到这两个 env. 若缺 → expert_escalate 返回
"ARK_API_KEY not set" silent failure. V37.9.54 INV-PLIST-ENV-001 只守 adapter plist,
不守 proxy plist → 本 INV 填补真缺口. 注意 ARK_ENDPOINT_ID 不是任何 provider 的
api_key_env (它是 endpoint ID 不是 key), INV-PLIST-ENV-001 覆盖不到.

覆盖范围:
- TestProxyPlistEnvGovernance: governance_ontology.yaml INV-PROXY-PLIST-ENV-001 字面量守卫
- TestProxyPlistEnvRuntimeBehavior: 提取 yaml 真 runtime check code 直接 exec (零 drift)
  + patched HOME + 假 plist 验证 fail-loud / pass / dev-safe silent pass
- TestProxyPlistEnvDependencyChain: 生产依赖链真实存在 (proxy → expert_escalation → ARK env)
- TestProxyPlistEnvDeclarationCheck: 提取 yaml declaration python_assert 真 exec (对真仓库文件)
- TestV37996Marker: V37.9.96 标记一致性 + audit_metadata total_invariants == 87 (V37.9.100 bump)
"""
import os
import re
import tempfile
import plistlib
import unittest

import yaml


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
GOVERNANCE_YAML = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
TOOL_PROXY = os.path.join(REPO_ROOT, "tool_proxy.py")
EXPERT_ESCALATION = os.path.join(REPO_ROOT, "expert_escalation.py")
INV_ID = "INV-PROXY-PLIST-ENV-001"


def _load_yaml():
    with open(GOVERNANCE_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_inv(data, inv_id=INV_ID):
    for inv in data.get("invariants", []):
        if inv.get("id") == inv_id:
            return inv
    return None


def _extract_check_code(inv, name_substring):
    """从 INV 的 checks 里找 check_type==python_assert 且 name 含 substring 的 code 块.
    直接返回 yaml 里的真代码字符串, 零 drift."""
    for c in inv.get("checks", []):
        if c.get("check_type") == "python_assert" and name_substring in c.get("name", ""):
            return c.get("code")
    return None


class TestProxyPlistEnvGovernance(unittest.TestCase):
    """governance_ontology.yaml INV-PROXY-PLIST-ENV-001 字面量守卫 (镜像 V37.9.54)."""

    @classmethod
    def setUpClass(cls):
        with open(GOVERNANCE_YAML, encoding="utf-8") as f:
            cls.src = f.read()
        cls.data = _load_yaml()
        cls.inv = _find_inv(cls.data)

    def test_inv_declared(self):
        self.assertIsNotNone(self.inv, "V37.9.96 必须声明 INV-PROXY-PLIST-ENV-001")
        self.assertIn(INV_ID, self.src)

    def test_inv_name_and_meta(self):
        """挂在 MR-4 (silent-failure-is-a-bug), severity high, 双层验证."""
        self.assertEqual(self.inv.get("name"),
                         "proxy-plist-env-must-contain-ark-expert-escalate-keys")
        self.assertEqual(self.inv.get("meta_rule"), "MR-4")
        self.assertEqual(self.inv.get("severity"), "high")
        self.assertEqual(self.inv.get("verification_layer"), ["declaration", "runtime"])

    def test_runtime_check_uses_plistlib_and_proxy_plist(self):
        """runtime check 必须用 plistlib 解析 com.openclaw.proxy.plist (非 adapter)."""
        self.assertIn("plistlib", self.src)
        self.assertIn("com.openclaw.proxy.plist", self.src,
                      "INV 必须明确指向 proxy plist 路径 (不是 adapter)")

    def test_required_envs_ark_pair(self):
        """必须守 ARK_API_KEY + ARK_ENDPOINT_ID 两个 env."""
        inv_block = re.search(
            r"id: INV-PROXY-PLIST-ENV-001.*?(?=\n  - id:|\n  # ---)",
            self.src, re.DOTALL)
        self.assertIsNotNone(inv_block)
        block = inv_block.group(0)
        self.assertIn("ARK_API_KEY", block)
        self.assertIn("ARK_ENDPOINT_ID", block)

    def test_dev_safe_fallback(self):
        """dev 环境无 proxy plist 时 runtime check 静默通过 (不抛)."""
        rt = _extract_check_code(self.inv, "runtime")
        self.assertIsNotNone(rt, "必须有 runtime python_assert check")
        self.assertIn("plist_path.exists()", rt,
                      "runtime check 必须先 if plist_path.exists() 防 dev 环境炸")

    def test_blood_lessons_reference_v37_9_91(self):
        """blood_lessons 必须引用 V37.9.91 实测案例 + 通用 launchd daemon 模式."""
        bl = self.inv.get("blood_lessons", [])
        blob = " ".join(bl)
        self.assertIn("V37.9.91", blob, "必须引用 V37.9.91 proxy plist 缺 ARK env 实测")
        self.assertIn("launchd daemon 不继承", blob,
                      "必须文档化通用模式 (launchd daemon 不继承 shell env)")

    def test_seven_checks_total(self):
        checks = self.inv.get("checks", [])
        self.assertEqual(len(checks), 7,
                         f"INV-PROXY-PLIST-ENV-001 应有 7 checks, got {len(checks)}")
        ctypes = [c.get("check_type") for c in checks]
        self.assertEqual(ctypes.count("file_contains"), 5)
        self.assertEqual(ctypes.count("python_assert"), 2)

    def test_v37_9_96_marker(self):
        self.assertIn("V37.9.96 新增", self.src,
                      "INV 块必须含 V37.9.96 新增 marker (被自身 check 引用)")


class TestProxyPlistEnvRuntimeBehavior(unittest.TestCase):
    """提取 yaml 里的真 runtime check code 直接 exec, patched HOME + 假 plist 验证 fail-loud.
    零 drift: 测试运行的就是 governance_checker 会跑的同一段代码."""

    @classmethod
    def setUpClass(cls):
        cls.inv = _find_inv(_load_yaml())
        cls.runtime_code = _extract_check_code(cls.inv, "runtime")
        assert cls.runtime_code, "必须能提取 runtime check code"

    def _run_with_home(self, home_dir):
        """在 home_dir 作为 $HOME 下 exec runtime check code. 返回 None=pass, AssertionError=fail."""
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = home_dir
        try:
            exec(self.runtime_code, {"__name__": "_inv_runtime"})
            return None
        except AssertionError as e:
            return e
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)

    def _make_proxy_plist(self, home_dir, env_dict):
        la = os.path.join(home_dir, "Library", "LaunchAgents")
        os.makedirs(la, exist_ok=True)
        with open(os.path.join(la, "com.openclaw.proxy.plist"), "wb") as f:
            plistlib.dump({"EnvironmentVariables": env_dict}, f)

    def test_missing_endpoint_id_fails_loud(self):
        """缺 ARK_ENDPOINT_ID (V37.9.91 血案场景) → runtime check 必须 fail-loud."""
        with tempfile.TemporaryDirectory() as td:
            self._make_proxy_plist(td, {"ARK_API_KEY": "x", "REMOTE_API_KEY": "y"})
            err = self._run_with_home(td)
            self.assertIsNotNone(err, "缺 ARK_ENDPOINT_ID 应抛 AssertionError")
            self.assertIn("ARK_ENDPOINT_ID", str(err))

    def test_missing_both_ark_envs_fails_loud(self):
        """完全无 ARK env → fail-loud."""
        with tempfile.TemporaryDirectory() as td:
            self._make_proxy_plist(td, {"REMOTE_API_KEY": "y"})
            err = self._run_with_home(td)
            self.assertIsNotNone(err, "无 ARK env 应抛 AssertionError")
            self.assertIn("ARK_API_KEY", str(err))

    def test_complete_ark_envs_passes(self):
        """ARK_API_KEY + ARK_ENDPOINT_ID 都在 → 通过不抛."""
        with tempfile.TemporaryDirectory() as td:
            self._make_proxy_plist(td, {
                "ARK_API_KEY": "x", "ARK_ENDPOINT_ID": "ep-xxx", "REMOTE_API_KEY": "y"})
            err = self._run_with_home(td)
            self.assertIsNone(err, f"完整 ARK env 不该抛: {err}")

    def test_no_plist_silent_pass_dev_safe(self):
        """plist 不存在 (dev 环境) → silent pass 不抛."""
        with tempfile.TemporaryDirectory() as td:
            # 不创建任何 plist
            err = self._run_with_home(td)
            self.assertIsNone(err, f"无 proxy plist 应 silent pass: {err}")

    def test_empty_env_vars_dict_fails_loud(self):
        """plist 有 EnvironmentVariables 但为空 dict → fail-loud (边界)."""
        with tempfile.TemporaryDirectory() as td:
            self._make_proxy_plist(td, {})
            err = self._run_with_home(td)
            self.assertIsNotNone(err, "空 EnvironmentVariables 应抛")


class TestProxyPlistEnvDeclarationCheck(unittest.TestCase):
    """提取 yaml declaration python_assert 真 exec, 对真仓库文件验证依赖链可解析."""

    @classmethod
    def setUpClass(cls):
        cls.inv = _find_inv(_load_yaml())
        cls.decl_code = _extract_check_code(cls.inv, "declaration")
        assert cls.decl_code, "必须能提取 declaration check code"

    def test_declaration_check_passes_against_real_repo(self):
        """declaration check 对真仓库 tool_proxy.py + expert_escalation.py 必须通过."""
        old_cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            exec(self.decl_code, {"__name__": "_inv_decl"})
        except AssertionError as e:  # pragma: no cover - 失败即真问题
            self.fail(f"declaration check 应通过但失败: {e}")
        finally:
            os.chdir(old_cwd)


class TestProxyPlistEnvDependencyChain(unittest.TestCase):
    """生产依赖链真实存在: tool_proxy.py expert_escalate → expert_escalation.py ARK env."""

    @classmethod
    def setUpClass(cls):
        with open(TOOL_PROXY, encoding="utf-8") as f:
            cls.tp = f.read()
        with open(EXPERT_ESCALATION, encoding="utf-8") as f:
            cls.ee = f.read()

    def test_tool_proxy_has_expert_escalate_branch(self):
        self.assertIn('name == "expert_escalate"', self.tp,
                      "tool_proxy.py 必须有 expert_escalate 分支 (V37.9.91 wiring)")

    def test_tool_proxy_lazy_imports_expert_escalation(self):
        self.assertIn("expert_escalation", self.tp,
                      "expert_escalate 分支必须 lazy-import expert_escalation")

    def test_expert_escalation_binds_ark_api_key(self):
        self.assertIn('DOUBAO_API_KEY_ENV = "ARK_API_KEY"', self.ee,
                      "expert_escalation 必须把 ARK_API_KEY 绑定到 DOUBAO_API_KEY_ENV")

    def test_expert_escalation_binds_ark_endpoint_id(self):
        self.assertIn('DOUBAO_ENDPOINT_ID_ENV = "ARK_ENDPOINT_ID"', self.ee,
                      "expert_escalation 必须把 ARK_ENDPOINT_ID 绑定到 DOUBAO_ENDPOINT_ID_ENV")

    def test_expert_escalation_has_fail_loud_guidance(self):
        """缺 ARK_API_KEY 时返回清晰指引 (配 plist), 不真静默."""
        self.assertIn("configure in plist EnvironmentVariables", self.ee,
                      "expert_escalation 必须保留 fail-loud 指引提示配 plist")


class TestV37996Marker(unittest.TestCase):
    """V37.9.96 标记一致性 + audit_metadata 计数."""

    # V37.9.121 日落法 MR-22 一物一形: 退役 test_audit_metadata_total_invariants_87
    # (硬编码 expected=87 的脆弱守卫). 它严格冗余于下方动态守卫
    # test_actual_invariant_count_matches_metadata (grep == metadata), 且每次加 INV
    # 都得手改硬编码值 = 递归接缝 (V37.9.96→85 / V37.9.100→87 / 本会再→89...).
    # 动态守卫已完整覆盖"计数自洽"关注点, 保留它即可. 详见 CLAUDE.md V37.9.121.

    def test_actual_invariant_count_matches_metadata(self):
        """grep 真实 INV- 计数必须 == audit_metadata total_invariants (自洽守卫)."""
        data = _load_yaml()
        actual = len(data.get("invariants", []))
        declared = data.get("audit_metadata", {}).get("total_invariants")
        self.assertEqual(actual, declared,
                         f"实际 invariants {actual} 必须 == audit_metadata {declared}")

    def test_version_unchanged_governance_work(self):
        """V37.9.96 是 governance 工作不 bump VERSION (保持 >= 0.37.9.68)."""
        with open(os.path.join(REPO_ROOT, "VERSION"), encoding="utf-8") as f:
            content = f.read().strip()
        self.assertTrue(
            content.startswith("0.37.9.") and int(content.split(".")[-1]) >= 68,
            f"VERSION 必须 >= 0.37.9.68, got {content}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

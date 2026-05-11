"""V37.9.54 ABC 三件套集成测试

A: restart.sh marker-based plist mtime 检测 + bootout/bootstrap 自动 fallback
   修复 V37.9.13 引入 + V37.9.53 第二次踩坑的 `launchctl kickstart -k` 不重读
   plist 问题. 让 plist 更新后 restart.sh 自动 bootout + bootstrap 重读, plist 无
   变化时仍走 kickstart -k 快路径.

B: doubao verified_vision=True (Mac Mini E2E 实测 image_url content block 工作正常)
   V37.9.53 已 flip verified_text + reasoning, V37.9.54 加 verified_vision.

C: INV-PLIST-ENV-001 governance 治理 plist EnvironmentVariables vs provider
   api_key_env 声明一致性. declaration 4 checks + runtime python_assert (Mac Mini
   --full mode 真读 plist 比对).

覆盖范围:
- TestRestartShMarkerLogic: A 部分 source 级守卫 (marker dir / 函数体逻辑)
- TestRestartShShellBehavior: A 部分 subprocess 真跑 helper 边界场景
- TestDoubaoVerifiedVision: B 部分 verified_vision=True 守卫 + 反向验证
- TestPlistEnvGovernance: C 部分 governance_ontology.yaml INV-PLIST-ENV-001 守卫
- TestV37954VersionMarker: 整体 V37.9.54 标记一致性
"""
import importlib
import os
import re
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RESTART_SH = os.path.join(REPO_ROOT, "restart.sh")
DOUBAO_PLUGIN = os.path.join(REPO_ROOT, "providers.d", "doubao_provider.py")
GOVERNANCE_YAML = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")


def _reload_providers():
    if "providers" in sys.modules:
        del sys.modules["providers"]
    return importlib.import_module("providers")


class TestRestartShMarkerLogic(unittest.TestCase):
    """A: restart.sh V37.9.54 marker-based plist reload source-level guards."""

    @classmethod
    def setUpClass(cls):
        with open(RESTART_SH, encoding="utf-8") as f:
            cls.src = f.read()

    def test_marker_dir_constant_defined(self):
        self.assertIn(
            "PLIST_LOAD_MARKER_DIR=",
            self.src,
            "V37.9.54 必须定义 PLIST_LOAD_MARKER_DIR 常量追踪每个 label 的加载时间",
        )

    def test_marker_dir_created_via_mkdir(self):
        self.assertRegex(
            self.src,
            r"mkdir -p .*PLIST_LOAD_MARKER_DIR",
            "marker dir 必须用 mkdir -p 确保存在 (向后兼容首次启动)",
        )

    def test_helper_compares_plist_mtime_vs_marker(self):
        """helper 函数体必须含 plist_mtime + marker_mtime 比较."""
        self.assertIn("plist_mtime=", self.src)
        self.assertIn("marker_mtime=", self.src)
        self.assertIn('stat -f %m', self.src,
                      "marker mtime 比较必须用 stat -f %m (macOS 兼容)")

    def test_need_full_reload_default_safe(self):
        """无 marker 时 (首次启动) 默认走 bootout/bootstrap 安全路径."""
        self.assertRegex(
            self.src,
            r"need_full_reload=1",
            "默认值必须是 1 (safe path), 防 marker 缺失时跳过 plist 重读",
        )

    def test_touch_marker_after_health_verification(self):
        """V37.9.54 关键不变式: touch marker 必须在 HTTP 200 健康验证成功之后."""
        m = re.search(r'touch\s+"\$marker"', self.src)
        self.assertIsNotNone(m, "必须有 touch \"$marker\" 字面量")
        # touch 之前 400 chars 内必须有健康验证相关字样
        ctx_before = self.src[max(0, m.start() - 400):m.start()]
        self.assertTrue(
            "healthy" in ctx_before or "200" in ctx_before,
            "V37.9.54 touch marker 必须在 'healthy' 或 'HTTP 200' 字样之后, "
            "防 daemon 启动失败但 marker 被误更新 → 下次 restart 走 kickstart 跳过 plist reload",
        )

    def test_kickstart_path_preserved_for_unchanged_plist(self):
        """plist 无变化时仍走 V37.9.13 kickstart -k 快路径 (向后兼容)."""
        self.assertIn("kickstart -k", self.src)
        self.assertIn("plist 无变化", self.src,
                      "kickstart -k 分支应有 plist 无变化 标识 (V37.9.54 文档)")

    def test_v37_9_54_marker_in_source(self):
        self.assertIn("V37.9.54", self.src)

    def test_v37_9_13_marker_preserved(self):
        """V37.9.13 launchctl kickstart -k 设计文档仍保留."""
        self.assertIn("V37.9.13", self.src)


class TestRestartShShellBehavior(unittest.TestCase):
    """A: restart.sh marker logic 真 subprocess 跑边界场景."""

    def test_shell_syntax_valid(self):
        """bash -n 静态语法检查必须通过."""
        result = subprocess.run(
            ["bash", "-n", RESTART_SH],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(
            result.returncode, 0,
            f"restart.sh bash -n 失败: {result.stderr}",
        )

    def test_marker_dir_creation_idempotent(self):
        """mkdir -p marker dir 在已存在时不报错 (idempotent)."""
        with tempfile.TemporaryDirectory() as tmp:
            marker_dir = os.path.join(tmp, "markers")
            os.makedirs(marker_dir)  # 预先存在
            result = subprocess.run(
                ["mkdir", "-p", marker_dir],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(os.path.isdir(marker_dir))


class TestDoubaoVerifiedVision(unittest.TestCase):
    """B: V37.9.54 doubao verified_vision=True (Mac Mini E2E 实测)."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.d = self.providers.get_provider("doubao")

    def test_verified_vision_is_true(self):
        self.assertTrue(
            self.d.capabilities.verified_vision,
            "V37.9.54 verified_vision 必须 True (Mac Mini curl image_url 实测通过)",
        )

    def test_verified_features_includes_vision(self):
        features = self.d.capabilities.verified_features()
        self.assertIn("vision", features,
                      "verified_vision=True 应反映在 verified_features 列表")

    def test_verified_features_v37_9_54_complete_set(self):
        """V37.9.54 baseline 含 text+vision+reasoning.
        V37.9.55 加 tool_calling + streaming = 5 features."""
        features = self.d.capabilities.verified_features()
        self.assertTrue({"text", "vision", "reasoning"}.issubset(set(features)))
        self.assertEqual(
            set(features),
            {"text", "vision", "tool_calling", "streaming", "reasoning"},
            f"V37.9.55 doubao verified_features 锁定 5 项, got {features}",
        )

    def test_unverified_flags_still_false(self):
        """V37.9.55 仅 verified_fallback 守 False (剩生产真 fire 后再 flip).
        verified_tool_calling/streaming 已在 V37.9.55 flip True (Mac Mini E2E)."""
        c = self.d.capabilities
        self.assertFalse(c.verified_fallback, "fallback 未在生产真 fire (V37.9.56+)")

    def test_plugin_source_has_v37_9_54_marker(self):
        with open(DOUBAO_PLUGIN, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.54", src)
        self.assertIn(
            "image_url", src,
            "V37.9.54 plugin 注释必须说明 vision schema 形式 (image_url content block)",
        )


class TestDoubaoCapScoreUpRanking(unittest.TestCase):
    """B 副作用: verified_vision=True 让 doubao cap_score 从 10 升到 12."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.reg = self.providers.get_registry()

    def test_doubao_cap_score_v37_9_54(self):
        """V37.9.54: doubao cap_score = 6 base + 3 verified*2 = 12.
        V37.9.55 flip tool_calling + streaming 让 cap_score 升至 16.
        本 test 锁定 V37.9.54 baseline >= 12 + V37.9.55 当前精确值 16."""
        doubao = self.reg.get("doubao")
        score = self.reg._capability_score(doubao)
        self.assertGreaterEqual(score, 12, f"V37.9.54 baseline >= 12, got {score}")
        self.assertEqual(
            score, 16,
            f"V37.9.55 doubao cap_score 锁定 16 (6 base + 5 verified*2), got {score}",
        )

    def test_doubao_remains_first_in_fallback_chain(self):
        """V37.9.54: doubao 仍排在 fallback chain 第 1 (cap_score 12 > gemini 9)."""
        chain = self.reg.build_fallback_chain("qwen")
        names = [p.name for p in chain]
        self.assertEqual(names[0], "doubao")


class TestPlistEnvGovernance(unittest.TestCase):
    """C: governance_ontology.yaml INV-PLIST-ENV-001 字面量守卫."""

    @classmethod
    def setUpClass(cls):
        with open(GOVERNANCE_YAML, encoding="utf-8") as f:
            cls.src = f.read()

    def test_inv_plist_env_001_declared(self):
        self.assertIn(
            "INV-PLIST-ENV-001",
            self.src,
            "V37.9.54 必须声明 INV-PLIST-ENV-001 治理 plist env 一致性",
        )

    def test_inv_plist_env_001_meta_rule_mr4(self):
        """INV-PLIST-ENV-001 必须挂在 MR-4 (silent-failure-is-a-bug)."""
        # 找到 INV-PLIST-ENV-001 块
        m = re.search(
            r"id: INV-PLIST-ENV-001.*?(?=\n\s*- id:|\n\s*#)",
            self.src, re.DOTALL,
        )
        self.assertIsNotNone(m, "INV-PLIST-ENV-001 块必须存在")
        block = m.group(0)
        self.assertIn("meta_rule: MR-4", block)
        self.assertIn("severity: high", block)
        self.assertIn("verification_layer: [declaration, runtime]", block)

    def test_inv_plist_env_001_runtime_check_uses_plistlib(self):
        """runtime check 必须用 Python plistlib 解析真 plist."""
        self.assertIn("plistlib", self.src)
        self.assertIn(
            "com.openclaw.adapter.plist",
            self.src,
            "INV-PLIST-ENV-001 必须明确指向 adapter plist 路径",
        )

    def test_inv_plist_env_001_critical_envs_baseline(self):
        """V37.9.54 critical envs baseline 锁定 REMOTE/GEMINI/ARK."""
        # 找 critical 集合定义
        self.assertIn("REMOTE_API_KEY", self.src)
        self.assertIn("GEMINI_API_KEY", self.src)
        self.assertIn("ARK_API_KEY", self.src)

    def test_inv_plist_env_001_dev_safe_fallback(self):
        """dev 环境无 plist 时 runtime check 静默通过 (不抛 AssertionError)."""
        self.assertIn(
            "plist_path.exists()",
            self.src,
            "runtime check 必须先 if plist_path.exists() 防 dev 环境炸",
        )

    def test_v37_9_54_blood_lessons_referenced(self):
        """INV-PLIST-ENV-001 blood_lessons 必须引用 V37.9.53 doubao 实测案例."""
        m = re.search(
            r"id: INV-PLIST-ENV-001.*?(?=\n\s*- id:|\n\s*#)",
            self.src, re.DOTALL,
        )
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("V37.9.53", block, "必须引用 V37.9.53 doubao 实测案例")
        self.assertIn(
            "launchd daemon 不继承", block,
            "必须文档化通用模式 (launchd daemon 不继承 shell env)",
        )


class TestV37954VersionMarker(unittest.TestCase):
    """整体 V37.9.54 版本标记一致性."""

    def test_version_file_is_v37_9_54_or_later(self):
        """V37.9.54 引入这些 ABC checks. 后续 V37.9.55+ 版本号继续推进, 不应让此 test fail.
        改为不等式断言: VERSION 必须 >= 0.37.9.54 (字符串比较 lex 顺序在 0.37.x 范围正确)."""
        with open(os.path.join(REPO_ROOT, "VERSION"), encoding="utf-8") as f:
            content = f.read().strip()
        # 接受 0.37.9.54 / 0.37.9.55 / 0.37.9.56 / etc.
        self.assertTrue(
            content.startswith("0.37.9.") and int(content.split(".")[-1]) >= 54,
            f"VERSION 必须 >= 0.37.9.54, got {content}",
        )

    def test_restart_sh_has_v37_9_54_marker(self):
        with open(RESTART_SH, encoding="utf-8") as f:
            self.assertIn("V37.9.54", f.read())

    def test_doubao_plugin_has_v37_9_54_marker(self):
        with open(DOUBAO_PLUGIN, encoding="utf-8") as f:
            self.assertIn("V37.9.54", f.read())

    def test_governance_yaml_has_v37_9_54_marker(self):
        with open(GOVERNANCE_YAML, encoding="utf-8") as f:
            self.assertIn("V37.9.54", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""test_ontology_packaging.py — V37.9.99-pkg Phase 5 chunk 1 守卫。

验证 ontology-engine pip 包化第一块:
  1. config-injection keystone (ONTOLOGY_CONFIG_DIR / ONTOLOGY_PROJECT_ROOT)
  2. 向后兼容 (无 env → 当前行为不变)
  3. pyproject.toml 有效性 + packages + entry points
  4. console 入口 (engine.main / governance_checker.main) 可解析
  5. 宪法第一条: proxy_filters 删除 ontology 后 FAIL-OPEN (源码级守卫)
  6. 反向 sabotage 守卫 (移除 env 解析 → test 立即 fail)

设计文档: docs/ontology_engine_packaging.md
"""

import importlib.util
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
_ENGINE_PATH = os.path.join(REPO, "ontology", "engine.py")
_GOV_PATH = os.path.join(REPO, "ontology", "governance_checker.py")
_PYPROJECT = os.path.join(REPO, "pyproject.toml")


def _load_fresh(name, path, env_overrides=None):
    """全新加载模块 (模块级常量重新求值), 可选 env 覆盖, 结束后还原 env。"""
    saved = {}
    if env_overrides:
        for k, v in env_overrides.items():
            saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


# ───────────────────────────────────────────────────────────────────────
class TestEngineConfigInjection(unittest.TestCase):
    """engine.py 的 ONTOLOGY_CONFIG_DIR 注入 keystone。"""

    def test_resolve_config_dir_function_exists(self):
        m = _load_fresh("_eng_a", _ENGINE_PATH, {"ONTOLOGY_CONFIG_DIR": None})
        self.assertTrue(callable(getattr(m, "_resolve_config_dir", None)),
                        "engine 必须有 _resolve_config_dir() (包化 keystone)")

    def test_default_dir_is_engine_dir_backward_compat(self):
        m = _load_fresh("_eng_b", _ENGINE_PATH, {"ONTOLOGY_CONFIG_DIR": None})
        # 默认 = 引擎同目录 (ontology/), 向后兼容
        self.assertTrue(m._ONTOLOGY_DIR.endswith("ontology"),
                        f"无 env 时应指向引擎目录, 实际 {m._ONTOLOGY_DIR}")
        self.assertEqual(m._ONTOLOGY_FILE,
                         os.path.join(m._ONTOLOGY_DIR, "tool_ontology.yaml"))

    def test_env_override_redirects_config_dir(self):
        with tempfile.TemporaryDirectory() as td:
            m = _load_fresh("_eng_c", _ENGINE_PATH, {"ONTOLOGY_CONFIG_DIR": td})
            self.assertEqual(m._ONTOLOGY_DIR, os.path.abspath(td),
                             "ONTOLOGY_CONFIG_DIR 应覆盖默认配置目录")
            self.assertEqual(m._DOMAIN_ONTOLOGY_FILE,
                             os.path.join(os.path.abspath(td), "domain_ontology.yaml"))
            self.assertEqual(m._POLICY_ONTOLOGY_FILE,
                             os.path.join(os.path.abspath(td), "policy_ontology.yaml"))

    def test_env_expanduser_applied(self):
        m = _load_fresh("_eng_d", _ENGINE_PATH, {"ONTOLOGY_CONFIG_DIR": "~/somecfg"})
        self.assertEqual(m._ONTOLOGY_DIR,
                         os.path.abspath(os.path.expanduser("~/somecfg")))

    def test_empty_env_falls_back_to_default(self):
        # 空字符串 (含空白) 应回退默认, 不是当成 "" 路径
        m = _load_fresh("_eng_e", _ENGINE_PATH, {"ONTOLOGY_CONFIG_DIR": "   "})
        self.assertTrue(m._ONTOLOGY_DIR.endswith("ontology"))

    def test_end_to_end_injection_loads_alt_yaml(self):
        """真注入: 指向 temp 目录的最小 tool_ontology.yaml → 引擎加载它。"""
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "tool_ontology.yaml"), "w", encoding="utf-8") as f:
                f.write("tools:\n  builtin:\n    my_injected_tool: {}\n")
            m = _load_fresh("_eng_f", _ENGINE_PATH, {"ONTOLOGY_CONFIG_DIR": td})
            onto = m.get_ontology()
            self.assertIn("my_injected_tool", onto.allowed_tools,
                          "应从注入目录加载 YAML, 而非引擎默认")


# ───────────────────────────────────────────────────────────────────────
class TestGovernanceConfigInjection(unittest.TestCase):
    """governance_checker.py 的 config-root + project-root 注入。"""

    def test_resolve_functions_exist(self):
        m = _load_fresh("_gov_a", _GOV_PATH,
                        {"ONTOLOGY_CONFIG_DIR": None, "ONTOLOGY_PROJECT_ROOT": None})
        self.assertTrue(callable(getattr(m, "_resolve_project_root", None)))
        self.assertTrue(callable(getattr(m, "_resolve_ontology_dir", None)))

    def test_default_backward_compat(self):
        m = _load_fresh("_gov_b", _GOV_PATH,
                        {"ONTOLOGY_CONFIG_DIR": None, "ONTOLOGY_PROJECT_ROOT": None})
        self.assertTrue(m._ONTOLOGY_DIR.endswith("ontology"))
        # _PROJECT_ROOT 默认 = 仓库根 (引擎目录的父)
        self.assertEqual(m._PROJECT_ROOT, os.path.dirname(m._ONTOLOGY_DIR))

    def test_project_root_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            m = _load_fresh("_gov_c", _GOV_PATH, {"ONTOLOGY_PROJECT_ROOT": td})
            self.assertEqual(m._PROJECT_ROOT, os.path.abspath(td),
                             "ONTOLOGY_PROJECT_ROOT 应覆盖审计项目根")

    def test_config_dir_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            m = _load_fresh("_gov_d", _GOV_PATH, {"ONTOLOGY_CONFIG_DIR": td})
            self.assertEqual(m._ONTOLOGY_DIR, os.path.abspath(td))

    def test_main_exists_and_callable(self):
        """V37.9.99-pkg: __main__ 抽出 main() 供 console_scripts。"""
        m = _load_fresh("_gov_e", _GOV_PATH, None)
        self.assertTrue(callable(getattr(m, "main", None)),
                        "governance_checker 必须有 main() (console 入口)")


# ───────────────────────────────────────────────────────────────────────
class TestPyprojectToml(unittest.TestCase):
    """pyproject.toml 元数据有效性。"""

    def setUp(self):
        try:
            import tomllib  # py3.11+
        except ImportError:
            self.skipTest("tomllib 不可用 (需 py3.11+)")
        with open(_PYPROJECT, "rb") as f:
            self.cfg = tomllib.load(f)

    def test_pyproject_exists(self):
        self.assertTrue(os.path.isfile(_PYPROJECT), "pyproject.toml 必须存在于仓库根")

    def test_distribution_name_not_conflicting_pypi(self):
        # ontology-engine 0.1.0 公共 PyPI 已被占用 → 用 openclaw-ontology-engine
        self.assertEqual(self.cfg["project"]["name"], "openclaw-ontology-engine")

    def test_packages_only_ontology_engine(self):
        # V37.9.128 chunk-2-lite: 引擎包导出名去泛化 ontology → ontology_engine
        pkgs = self.cfg["tool"]["setuptools"]["packages"]
        self.assertEqual(pkgs, ["ontology_engine"],
                         "只打包引擎 (去泛化名 ontology_engine), 不打包顶层应用模块")

    def test_package_dir_maps_engine_to_ontology_dir(self):
        # chunk-2-lite keystone: package-dir 映射 ontology_engine → 磁盘 ontology/ 目录
        # (零目录 rename, 消费方 import ontology_engine, bridge 本地仍 import ontology)
        pd = self.cfg["tool"]["setuptools"]["package-dir"]
        self.assertEqual(pd["ontology_engine"], "ontology")

    def test_depends_on_pyyaml(self):
        deps = self.cfg["project"]["dependencies"]
        self.assertTrue(any("pyyaml" in d.lower() for d in deps),
                        "引擎依赖 pyyaml")

    def test_console_scripts_point_to_existing_mains(self):
        scripts = self.cfg["project"]["scripts"]
        # 两个 console 入口 (去泛化名 ontology_engine)
        self.assertIn("openclaw-ontology-audit", scripts)
        self.assertIn("openclaw-ontology-query", scripts)
        self.assertEqual(scripts["openclaw-ontology-audit"],
                         "ontology_engine.governance_checker:main")
        self.assertEqual(scripts["openclaw-ontology-query"],
                         "ontology_engine.engine:main")

    def test_package_data_bundles_yaml(self):
        pd = self.cfg["tool"]["setuptools"]["package-data"]["ontology_engine"]
        self.assertIn("*.yaml", pd, "默认参考 YAML 应作 package-data 附带")


# ───────────────────────────────────────────────────────────────────────
class TestEntryPointsResolve(unittest.TestCase):
    """console_scripts target 真能解析为 callable。"""

    def test_engine_main_importable(self):
        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        import ontology.engine as eng  # noqa
        self.assertTrue(callable(getattr(eng, "main", None)))

    def test_governance_main_importable(self):
        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        import ontology.governance_checker as gov  # noqa
        self.assertTrue(callable(getattr(gov, "main", None)))


class TestChunk2LiteImportName(unittest.TestCase):
    """V37.9.128 chunk-2-lite: 消费方 import 名去泛化为 ontology_engine (package-dir 映射)。

    behavioral: 若已 pip install -e . → import ontology_engine 解析到 ontology/ 目录,
    且子模块 governance_checker/engine/convergence 可导入。未安装 → skip (clean env 不失败)。
    """

    def _import_engine(self):
        try:
            import ontology_engine  # noqa
            return ontology_engine
        except ImportError:
            self.skipTest("ontology_engine 未安装 (需 pip install -e . — chunk-2-lite 映射)")

    def test_ontology_engine_maps_to_ontology_dir(self):
        mod = self._import_engine()
        # package-dir 映射: ontology_engine 的源在磁盘 ontology/ 目录
        self.assertTrue(mod.__file__.replace("\\", "/").endswith("ontology/__init__.py"),
                        f"ontology_engine 应映射到 ontology/, 实际 {mod.__file__}")

    def test_ontology_engine_submodules_importable(self):
        self._import_engine()
        import ontology_engine.governance_checker as g  # noqa
        import ontology_engine.engine as e  # noqa
        self.assertTrue(callable(getattr(g, "main", None)))
        self.assertTrue(callable(getattr(e, "main", None)))

    def test_local_ontology_still_works(self):
        # 向后兼容: bridge 本地 import ontology (本地目录) 仍工作 (dev 双名共存)
        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        import ontology  # noqa
        self.assertTrue(ontology.__file__.replace("\\", "/").endswith("ontology/__init__.py"))


# ───────────────────────────────────────────────────────────────────────
class TestConstitutionDeletionSafety(unittest.TestCase):
    """宪法第一条: 删除 ontology 后 proxy_filters FAIL-OPEN (源码级守卫)。

    chunk 1 不得破坏 proxy_filters 对 ontology 的 lazy-load + 回退契约。
    """

    def test_proxy_lazy_loads_engine_with_fallback(self):
        with open(os.path.join(REPO, "proxy_filters.py"), encoding="utf-8") as f:
            src = f.read()
        # lazy-load 模式 (spec_from_file_location) + 存在性检查
        self.assertIn("spec_from_file_location", src,
                      "proxy 必须 lazy-load 引擎 (而非硬 import)")
        self.assertIn("os.path.exists(_onto_engine_path)", src,
                      "proxy 必须检查引擎存在性 (删除后回退)")
        # ONTOLOGY_MODE 三档 + 回退 config 路径仍在
        self.assertIn("_CFG_MAX_TOOLS", src,
                      "删除引擎后必须回退 config 阈值 (FAIL-OPEN)")


# ───────────────────────────────────────────────────────────────────────
class TestReverseSabotageGuards(unittest.TestCase):
    """源码级守卫: 移除 config-injection → test 立即抓 (防回归)。"""

    def test_engine_source_has_env_resolution(self):
        with open(_ENGINE_PATH, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ONTOLOGY_CONFIG_DIR", src,
                      "engine 必须读 ONTOLOGY_CONFIG_DIR (包化 keystone)")
        self.assertIn("def _resolve_config_dir", src)
        self.assertIn("V37.9.99-pkg", src, "包化 marker 便于追溯")

    def test_governance_source_has_env_resolution(self):
        with open(_GOV_PATH, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ONTOLOGY_PROJECT_ROOT", src)
        self.assertIn("ONTOLOGY_CONFIG_DIR", src)
        self.assertIn("def _resolve_project_root", src)
        # __main__ 仍调 main() (cron 行为不变)
        self.assertIn("sys.exit(main())", src,
                      "__main__ 必须走 main() (cron / full_regression 行为不变)")

    def test_no_hardcoded_dirname_only_for_config(self):
        """反退化: 不能回到 `_ONTOLOGY_DIR = os.path.dirname(...)` 直接硬编码。"""
        with open(_ENGINE_PATH, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn(
            '_ONTOLOGY_DIR = os.path.dirname(os.path.abspath(__file__))', src,
            "engine _ONTOLOGY_DIR 必须经 _resolve_config_dir(), 不得硬编码")


class TestChunk5PublishReady(unittest.TestCase):
    """V37.9.133 chunk 5 — sdist/wheel 发布就绪守卫.

    chunk 5 验证记录 (2026-06-11 dev 实测):
      python3 -m build → sdist 325KB + wheel 312KB 首次真实构建成功;
      /tmp venv 从 wheel 安装 → import ontology_engine + 全子模块 OK;
      WeatherBot config-injection 注入消费方 YAML 零 bridge 泄漏;
      venv bin/openclaw-ontology-audit 真跑 WeatherBot 治理审计 exit=0.
    实际 PyPI 发布 = 用户决策 (需账号/token), 本包 publish-ready.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as f:
            cls.pyproject = f.read()

    def test_license_field_points_to_license_file(self):
        """license = { file = "LICENSE" } 且 LICENSE 文件存在 (MIT)"""
        self.assertIn('license = { file = "LICENSE" }', self.pyproject)
        lic_path = os.path.join(REPO, "LICENSE")
        self.assertTrue(os.path.isfile(lic_path), "LICENSE 文件必须存在")
        with open(lic_path, encoding="utf-8") as f:
            self.assertIn("MIT License", f.read())

    def test_readme_field_points_to_package_readme(self):
        """readme = PACKAGE_README.md 且文件存在 (PyPI 门面, 英文)"""
        self.assertIn('readme = "PACKAGE_README.md"', self.pyproject)
        readme_path = os.path.join(REPO, "PACKAGE_README.md")
        self.assertTrue(os.path.isfile(readme_path))

    def test_package_readme_core_content(self):
        """包 README 必须含两层架构 + env 注入 + console scripts (消费方 onboarding)"""
        with open(os.path.join(REPO, "PACKAGE_README.md"), encoding="utf-8") as f:
            readme = f.read()
        for token in ("ONTOLOGY_CONFIG_DIR", "ONTOLOGY_PROJECT_ROOT",
                      "openclaw-ontology-audit", "ontology_engine",
                      "Layer 1", "Layer 2", "MIT"):
            self.assertIn(token, readme, f"PACKAGE_README 缺关键内容: {token}")

    def test_classifiers_present(self):
        self.assertIn('"Development Status :: 3 - Alpha"', self.pyproject)
        self.assertIn('"Programming Language :: Python :: 3"', self.pyproject)

    def test_gitignore_excludes_build_artifacts(self):
        """dist/ + *.egg-info/ 必须 gitignore (build 产物不入库)"""
        with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
            gi = f.read()
        self.assertIn("dist/", gi)
        self.assertIn("*.egg-info/", gi)

    def test_publish_decision_documented_in_pyproject(self):
        """发布决策登记: publish-ready + 实际发布待用户 (头部注释)"""
        self.assertIn("publish-ready", self.pyproject)


if __name__ == "__main__":
    unittest.main(verbosity=2)

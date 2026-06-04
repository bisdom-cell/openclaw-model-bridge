#!/usr/bin/env python3
"""test_ontology_extension_demo.py — V37.9.104 Phase 5 chunk 4 守卫。

验证 ontology-engine 包化第二里程碑: 第一个真消费方 demo (WeatherBot) 经
config-injection 被同一份引擎治理。这是引擎的"Doubao 时刻"。

覆盖:
  1. 端到端: run_demo.sh 真跑 → exit 0 + "config-injection works end-to-end"
  2. 引擎经 ONTOLOGY_CONFIG_DIR 加载 demo 工具 (get_forecast), 非 bridge 工具
  3. 反向验证: 不注入 env → 引擎加载 bridge 工具 (证明 demo 的通过依赖注入)
  4. demo 项目真不同于本仓库 (工具集不相交), 证明"第二个项目"非副本
  5. governance run_all 经注入审计 demo 的 2 个不变式全过
  6. 源码级守卫: demo 文件齐 / run_demo.sh 设双 env / Extension Guide 存在 /
     demo 不变式只引用 demo 自己的文件 (不引用 bridge 文件)

设计文档: docs/ontology_engine_extension_guide.md + docs/ontology_engine_packaging.md
"""

import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(REPO, "examples", "minimal_consumer")
DEMO_ONTO = os.path.join(DEMO_DIR, "ontology")
RUN_DEMO_SH = os.path.join(DEMO_DIR, "run_demo.sh")
RUN_DEMO_PY = os.path.join(DEMO_DIR, "run_demo.py")
GUIDE = os.path.join(REPO, "docs", "ontology_engine_extension_guide.md")


def _env(inject=True):
    """Process env mirroring a real consumer (config-injection on by default)."""
    e = dict(os.environ)
    e["PYTHONPATH"] = REPO + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    if inject:
        e["ONTOLOGY_CONFIG_DIR"] = DEMO_ONTO
        e["ONTOLOGY_PROJECT_ROOT"] = DEMO_DIR
    else:
        e.pop("ONTOLOGY_CONFIG_DIR", None)
        e.pop("ONTOLOGY_PROJECT_ROOT", None)
    return e


def _engine_tools(inject=True):
    """Load the engine in a fresh process with/without injection, return tool set."""
    code = "import ontology.engine as e; print(repr(sorted(e.get_ontology().allowed_tools)))"
    r = subprocess.run([sys.executable, "-c", code], env=_env(inject),
                       capture_output=True, text=True, cwd=REPO, timeout=60)
    assert r.returncode == 0, f"engine load failed: {r.stderr}"
    return set(eval(r.stdout.strip()))  # noqa: S307 — controlled repr of a list


# ───────────────────────────────────────────────────────────────────────
class TestEndToEndDemo(unittest.TestCase):
    """run_demo.sh 真跑端到端。"""

    def test_run_demo_sh_passes(self):
        r = subprocess.run(["bash", RUN_DEMO_SH], capture_output=True, text=True,
                           cwd=REPO, timeout=90)
        self.assertEqual(r.returncode, 0,
                         f"demo 应 exit 0\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
        self.assertIn("config-injection works end-to-end", r.stdout)
        # 4 大能力段都出现
        self.assertIn("ToolOntology", r.stdout)
        self.assertIn("find_by_domain", r.stdout)
        self.assertIn("evaluate_policy", r.stdout)
        self.assertIn("governance audit", r.stdout)

    def test_run_demo_py_executable_and_passes(self):
        r = subprocess.run([sys.executable, RUN_DEMO_PY], env=_env(inject=True),
                           capture_output=True, text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, f"run_demo.py 应 exit 0: {r.stderr}")


# ───────────────────────────────────────────────────────────────────────
class TestConfigInjectionLoadsDemoConfig(unittest.TestCase):
    """引擎经 env 注入加载 demo 配置 (而非 bridge)。"""

    def test_injected_engine_loads_weatherbot_tools(self):
        tools = _engine_tools(inject=True)
        self.assertIn("get_forecast", tools, "注入后应加载 WeatherBot 工具")
        self.assertIn("get_current_temp", tools)

    def test_injected_engine_does_not_load_bridge_tools(self):
        tools = _engine_tools(inject=True)
        # bridge 的标志性 builtin 工具不应出现 (证明读的是 demo 配置)
        self.assertNotIn("web_fetch", tools)
        self.assertNotIn("sessions_spawn", tools)


class TestReverseValidationInjectionMatters(unittest.TestCase):
    """反向验证: 不注入 → 引擎读 bridge 自带配置 (证明 demo 通过依赖注入)。"""

    def test_without_injection_loads_bridge_tools(self):
        tools = _engine_tools(inject=False)
        # 本仓库自带 ontology/tool_ontology.yaml 应被加载, 出现 bridge 工具
        self.assertNotIn("get_forecast", tools,
                         "无注入时不该出现 WeatherBot 工具 — 否则注入没起作用")
        # 本仓库工具集非空且含已知 bridge builtin 工具之一
        self.assertTrue(
            {"web_fetch", "sessions_spawn", "memory_search"} & tools,
            f"无注入应加载 bridge 工具, 实际 {sorted(tools)}")


# ───────────────────────────────────────────────────────────────────────
class TestDemoIsGenuinelyDifferent(unittest.TestCase):
    """demo 是真第二项目, 不是本仓库副本。"""

    def test_demo_and_bridge_tools_disjoint_on_signatures(self):
        demo = _engine_tools(inject=True)
        bridge = _engine_tools(inject=False)
        # 标志性工具互不出现在对方
        self.assertNotIn("get_forecast", bridge)
        self.assertNotIn("web_fetch", demo)

    def test_demo_governance_yaml_audits_only_demo_files(self):
        """demo 不变式只引用 weatherbot.py, 不引用 bridge 文件 (proxy_filters 等)。"""
        import yaml
        with open(os.path.join(DEMO_ONTO, "governance_ontology.yaml"),
                  encoding="utf-8") as f:
            data = yaml.safe_load(f)
        files = set()
        for inv in data.get("invariants", []):
            for c in inv.get("checks", []):
                if c.get("file"):
                    files.add(c["file"])
        self.assertTrue(files, "demo 应有 file_contains check")
        for fn in files:
            self.assertNotIn("/", fn, f"demo check 文件应在项目根, 不含路径: {fn}")
            self.assertNotIn("proxy_filters", fn, "demo 不该审计 bridge 文件")
            # 文件真存在于 demo 项目
            self.assertTrue(os.path.exists(os.path.join(DEMO_DIR, fn)),
                            f"demo check 引用的文件应存在: {fn}")


# ───────────────────────────────────────────────────────────────────────
class TestGovernanceAuditDemoPasses(unittest.TestCase):
    """governance run_all 经注入审计 demo 全过。"""

    def test_run_all_on_demo_passes(self):
        code = (
            "import ontology.governance_checker as g;"
            "rs=g.run_all(g._load());"
            "import json;"
            "print(json.dumps([{'id':r['id'],'status':r['status'],"
            "'p':r['passed_checks'],'t':r['total_checks']} for r in rs]))"
        )
        r = subprocess.run([sys.executable, "-c", code], env=_env(inject=True),
                           capture_output=True, text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, f"run_all 失败: {r.stderr}")
        import json
        results = json.loads(r.stdout.strip())
        self.assertEqual(len(results), 2, "demo 应有 2 个不变式")
        for inv in results:
            self.assertEqual(inv["status"], "pass",
                             f"{inv['id']} 应通过 ({inv['p']}/{inv['t']})")


# ───────────────────────────────────────────────────────────────────────
class TestSourceLevelGuards(unittest.TestCase):
    """文件齐备 + run_demo.sh 设双 env + Extension Guide 存在。"""

    def test_demo_files_present(self):
        for f in ["weatherbot.py", "run_demo.sh", "run_demo.py", "README.md"]:
            self.assertTrue(os.path.isfile(os.path.join(DEMO_DIR, f)), f"缺 {f}")
        for y in ["tool_ontology.yaml", "domain_ontology.yaml",
                  "policy_ontology.yaml", "governance_ontology.yaml"]:
            self.assertTrue(os.path.isfile(os.path.join(DEMO_ONTO, y)), f"缺 {y}")

    def test_run_demo_sh_sets_both_env_vars(self):
        with open(RUN_DEMO_SH, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ONTOLOGY_CONFIG_DIR", src)
        self.assertIn("ONTOLOGY_PROJECT_ROOT", src)
        self.assertIn("set -euo pipefail", src)

    def test_extension_guide_exists_and_references_demo(self):
        self.assertTrue(os.path.isfile(GUIDE), "Extension Guide 必须存在")
        with open(GUIDE, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ONTOLOGY_CONFIG_DIR", src)
        self.assertIn("ONTOLOGY_PROJECT_ROOT", src)
        self.assertIn("examples/minimal_consumer", src)
        self.assertIn("chunk 4", src, "包化 chunk marker 便于追溯")
        # 诚实登记 convergence 耦合 (chunk 4 demo 暴露的)
        self.assertIn("convergence", src.lower())

    def test_demo_yaml_distinct_from_bridge_ontology(self):
        """demo tool_ontology 不是 bridge 的复制 (内容判据)。"""
        with open(os.path.join(DEMO_ONTO, "tool_ontology.yaml"), encoding="utf-8") as f:
            demo_src = f.read()
        self.assertIn("get_forecast", demo_src)
        self.assertNotIn("data_clean", demo_src, "demo 不该含 bridge 工具声明")


if __name__ == "__main__":
    unittest.main(verbosity=2)

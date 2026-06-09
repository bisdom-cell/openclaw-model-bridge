#!/usr/bin/env python3
"""test_mrd_config_injection.py — V37.9.126 chunk 3b 守卫。

验证 ontology-engine 包化 chunk 3b: MRD 扫描器的项目特定文件名模式从
governance_checker.py 硬编码移到 Layer 2 config (governance_ontology.yaml::
mrd_scan_patterns). 消费方可 override 自己的文件名; 缺段 → bridge 默认 (字节级一致).
镜像 chunk 3a convergence config-injection 模式.

覆盖:
  TestMrdDefaults          — _MRD_DEFAULTS 含预期键 + 值 = bridge 当前
  TestLoadMrdPatterns      — _load_mrd_patterns 读 yaml / override / partial / FAIL-OPEN observable
  TestMrdInjectionWired    — _MRD 生效 + 白名单派生 + 行为级 registry_file override
  TestYamlSectionDriftGuard — yaml mrd_scan_patterns 段值 == defaults (字节级一致守卫)
  TestSourceGuards         — 扫描器用 _MRD 不硬编码 + chunk 3b marker + except observable + 反向

反向验证 (机器化): override registry_file → _load_registry 读不同文件;
sabotage (扫描器退回硬编码) → 源码守卫立即 fail.

设计文档: docs/ontology_engine_packaging.md 第 6 节 chunk 3b.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "ontology"))

import governance_checker as gc  # noqa: E402

_GC_SRC = os.path.join(_HERE, "ontology", "governance_checker.py")
_GOV_YAML = os.path.join(_HERE, "ontology", "governance_ontology.yaml")


class TestMrdDefaults(unittest.TestCase):
    def test_has_expected_keys(self):
        for k in ("registry_file", "notify_file", "preflight_file", "diagnostic_whitelist"):
            self.assertIn(k, gc._MRD_DEFAULTS)

    def test_defaults_match_bridge_values(self):
        self.assertEqual(gc._MRD_DEFAULTS["registry_file"], "jobs_registry.yaml")
        self.assertEqual(gc._MRD_DEFAULTS["notify_file"], "notify.sh")
        self.assertEqual(gc._MRD_DEFAULTS["preflight_file"], "preflight_check.sh")

    def test_diagnostic_whitelist_is_eleven_items(self):
        wl = gc._MRD_DEFAULTS["diagnostic_whitelist"]
        self.assertEqual(len(wl), 11)
        for must in ("preflight_check.sh", "full_regression.sh", "governance_audit_cron.sh"):
            self.assertIn(must, wl)


class TestLoadMrdPatterns(unittest.TestCase):
    def test_bridge_yaml_returns_defaults(self):
        # bridge yaml 的 mrd_scan_patterns 段值 == defaults → 加载结果 == defaults
        p = gc._load_mrd_patterns()
        self.assertEqual(p["registry_file"], "jobs_registry.yaml")
        self.assertEqual(p["notify_file"], "notify.sh")
        self.assertEqual(p["preflight_file"], "preflight_check.sh")

    def test_full_override(self):
        fake = {"mrd_scan_patterns": {
            "registry_file": "task_registry.yaml",
            "notify_file": "push.sh",
            "preflight_file": "checkup.sh",
            "diagnostic_whitelist": ["my_tool.sh", "my_report.sh"],
        }}
        with mock.patch.object(gc, "_load", return_value=fake):
            p = gc._load_mrd_patterns()
        self.assertEqual(p["registry_file"], "task_registry.yaml")
        self.assertEqual(p["notify_file"], "push.sh")
        self.assertEqual(p["preflight_file"], "checkup.sh")
        self.assertEqual(p["diagnostic_whitelist"], ["my_tool.sh", "my_report.sh"])

    def test_partial_override_falls_back_to_defaults(self):
        fake = {"mrd_scan_patterns": {"registry_file": "task_registry.yaml"}}
        with mock.patch.object(gc, "_load", return_value=fake):
            p = gc._load_mrd_patterns()
        self.assertEqual(p["registry_file"], "task_registry.yaml")  # override
        self.assertEqual(p["notify_file"], "notify.sh")             # default
        self.assertEqual(p["preflight_file"], "preflight_check.sh")  # default
        self.assertEqual(len(p["diagnostic_whitelist"]), 11)        # default

    def test_missing_section_uses_defaults(self):
        with mock.patch.object(gc, "_load", return_value={"invariants": []}):
            p = gc._load_mrd_patterns()
        self.assertEqual(p["registry_file"], "jobs_registry.yaml")

    def test_empty_or_non_string_override_ignored(self):
        # 空字符串/非 list 白名单 → 用默认 (防误配)
        fake = {"mrd_scan_patterns": {"registry_file": "", "diagnostic_whitelist": "not_a_list"}}
        with mock.patch.object(gc, "_load", return_value=fake):
            p = gc._load_mrd_patterns()
        self.assertEqual(p["registry_file"], "jobs_registry.yaml")
        self.assertEqual(len(p["diagnostic_whitelist"]), 11)

    def test_fail_open_observable_on_load_error(self):
        # _load 抛异常 → 用默认 + stderr 警告 (observable 非静默, MR-7 治理自观察)
        buf = io.StringIO()
        with mock.patch.object(gc, "_load", side_effect=RuntimeError("yaml gone")):
            with redirect_stderr(buf):
                p = gc._load_mrd_patterns()
        self.assertEqual(p["registry_file"], "jobs_registry.yaml")  # FAIL-OPEN 默认
        self.assertIn("WARN", buf.getvalue())                       # 可观测
        self.assertIn("mrd_scan_patterns", buf.getvalue())

    def test_returns_copy_not_default_alias(self):
        # 返回的 diagnostic_whitelist 是副本 (改它不污染 _MRD_DEFAULTS)
        p = gc._load_mrd_patterns()
        p["diagnostic_whitelist"].append("__poison__")
        self.assertNotIn("__poison__", gc._MRD_DEFAULTS["diagnostic_whitelist"])


class TestMrdInjectionWired(unittest.TestCase):
    def test_module_mrd_is_loaded_dict(self):
        self.assertIsInstance(gc._MRD, dict)
        self.assertEqual(gc._MRD["registry_file"], "jobs_registry.yaml")

    def test_log_stderr_whitelist_derived_from_mrd(self):
        # _LOG_STDERR_EXEMPT_BASENAMES 从 _MRD 派生 (Layer 2 可 override)
        self.assertEqual(gc._LOG_STDERR_EXEMPT_BASENAMES, set(gc._MRD["diagnostic_whitelist"]))

    def test_behavioral_registry_file_override_changes_load(self):
        # 行为级反向验证: override _MRD["registry_file"] → _load_registry 读不同文件
        orig = gc._MRD["registry_file"]
        try:
            gc._MRD["registry_file"] = "definitely_nonexistent_registry_xyz.yaml"
            jobs = gc._load_registry()
            self.assertEqual(jobs, [], "registry_file override 指向不存在文件 → 应返回 []")
        finally:
            gc._MRD["registry_file"] = orig
        # 还原后正常加载 (bridge 有 jobs_registry.yaml)
        self.assertGreater(len(gc._load_registry()), 0)


class TestYamlSectionDriftGuard(unittest.TestCase):
    """yaml mrd_scan_patterns 段必须存在且值 == _MRD_DEFAULTS (保证 bridge 字节级一致)."""

    def setUp(self):
        import yaml
        with open(_GOV_YAML, encoding="utf-8") as f:
            self.data = yaml.safe_load(f)

    def test_section_exists(self):
        self.assertIn("mrd_scan_patterns", self.data)

    def test_yaml_values_match_defaults(self):
        cfg = self.data["mrd_scan_patterns"]
        self.assertEqual(cfg["registry_file"], gc._MRD_DEFAULTS["registry_file"])
        self.assertEqual(cfg["notify_file"], gc._MRD_DEFAULTS["notify_file"])
        self.assertEqual(cfg["preflight_file"], gc._MRD_DEFAULTS["preflight_file"])
        self.assertEqual(list(cfg["diagnostic_whitelist"]),
                         list(gc._MRD_DEFAULTS["diagnostic_whitelist"]))


class TestSourceGuards(unittest.TestCase):
    def setUp(self):
        with open(_GC_SRC, encoding="utf-8") as f:
            self.src = f.read()

    def test_chunk_3b_marker(self):
        self.assertIn("chunk 3b", self.src)
        self.assertIn("V37.9.126", self.src)

    def test_scanners_use_mrd_not_hardcoded_registry(self):
        # 扫描器用 _MRD["registry_file"], 不硬编码 os.path.join(_PROJECT_ROOT, "jobs_registry.yaml")
        self.assertIn('os.path.join(_PROJECT_ROOT, _MRD["registry_file"])', self.src)
        # 反向守卫: 旧硬编码形式已消除 (仅 _MRD_DEFAULTS 里有 "jobs_registry.yaml" 字面量)
        self.assertNotIn('os.path.join(_PROJECT_ROOT, "jobs_registry.yaml")', self.src)

    def test_scanners_use_mrd_not_hardcoded_notify_preflight(self):
        self.assertIn('os.path.join(_PROJECT_ROOT, _MRD["notify_file"])', self.src)
        self.assertIn('os.path.join(_PROJECT_ROOT, _MRD["preflight_file"])', self.src)
        self.assertNotIn('os.path.join(_PROJECT_ROOT, "notify.sh")', self.src)
        self.assertNotIn('os.path.join(_PROJECT_ROOT, "preflight_check.sh")', self.src)

    def test_whitelist_derived_from_mrd_in_source(self):
        self.assertIn('_LOG_STDERR_EXEMPT_BASENAMES = set(_MRD["diagnostic_whitelist"])', self.src)

    def test_load_mrd_patterns_defined(self):
        self.assertIn("def _load_mrd_patterns():", self.src)
        self.assertIn("_MRD = _load_mrd_patterns()", self.src)

    def test_fail_open_except_is_observable_not_bare_pass(self):
        # MR-7 治理自观察: _load_mrd_patterns 的 except 不是裸 pass (会被 MRD-SILENT-EXCEPT-001 抓)
        # 截取 _load_mrd_patterns 函数体, 断言 except 块含 print(stderr) 不是仅 pass
        i = self.src.find("def _load_mrd_patterns():")
        j = self.src.find("\n_MRD = _load_mrd_patterns()", i)
        body = self.src[i:j]
        self.assertIn("except Exception as e:", body)
        self.assertIn("file=sys.stderr", body)
        # 反向: except 后不应紧跟仅 'pass'
        self.assertNotIn("except Exception:\n        pass", body)


class TestDemoConfigInjection(unittest.TestCase):
    """端到端: WeatherBot demo 经 ONTOLOGY_CONFIG_DIR 注入自己的 mrd_scan_patterns
    (镜像 chunk 3a/4 — 真消费方读自己的配置非 bridge)."""

    _DEMO_ONTO = os.path.join(_HERE, "examples", "minimal_consumer", "ontology")
    _DEMO_ROOT = os.path.join(_HERE, "examples", "minimal_consumer")

    def _read_mrd_registry(self, env_extra):
        import subprocess
        code = (
            "import sys, os;"
            f"sys.path.insert(0, {os.path.join(_HERE, 'ontology')!r});"
            "import governance_checker as gc;"
            "print(gc._MRD['registry_file'])"
        )
        env = dict(os.environ)
        env.update(env_extra)
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60, env=env)
        return r.stdout.strip(), r.stderr

    def test_demo_yaml_has_mrd_patterns(self):
        import yaml
        with open(os.path.join(self._DEMO_ONTO, "governance_ontology.yaml"), encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.assertIn("mrd_scan_patterns", data)
        self.assertEqual(data["mrd_scan_patterns"]["registry_file"], "weatherbot_jobs.yaml")

    def test_injected_engine_reads_demo_patterns(self):
        # ONTOLOGY_CONFIG_DIR=demo → 引擎 _MRD 读 WeatherBot 文件名 (非 bridge)
        out, err = self._read_mrd_registry({
            "ONTOLOGY_CONFIG_DIR": self._DEMO_ONTO,
            "ONTOLOGY_PROJECT_ROOT": self._DEMO_ROOT,
        })
        self.assertEqual(out, "weatherbot_jobs.yaml",
                         f"注入后应读 demo 的 registry_file, 实际 {out!r}, stderr={err[:200]}")

    def test_reverse_no_injection_reads_bridge_default(self):
        # 反向验证: 不注入 ONTOLOGY_CONFIG_DIR → 引擎读 bridge 自带 (jobs_registry.yaml)
        # 证明 demo 的 weatherbot_jobs.yaml 是经依赖注入而非巧合
        env = {k: v for k, v in os.environ.items()
               if k not in ("ONTOLOGY_CONFIG_DIR", "ONTOLOGY_PROJECT_ROOT")}
        import subprocess
        code = (
            "import sys, os;"
            f"sys.path.insert(0, {os.path.join(_HERE, 'ontology')!r});"
            "import governance_checker as gc;"
            "print(gc._MRD['registry_file'])"
        )
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.stdout.strip(), "jobs_registry.yaml",
                         "不注入时应读 bridge 默认 (反向验证注入真起作用)")


if __name__ == "__main__":
    unittest.main(verbosity=2)

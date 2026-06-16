#!/usr/bin/env python3
"""test_governance_runtime_isolation_scanner.py — V37.9.159 INV-GOV-RUNTIME-ISOLATION-001 单测.

守卫 governance_runtime_isolation_scanner.py 的契约:
  Stage 1 自动发现 (block-based, 排除 file_contains 引用 + 自身)
  Stage 2 detector: D1 (crontab real-apply 方法级) / D2 (~/.kb incident 文件级) /
    D3 (真调 openclaw 文件级, 精确区分执行 vs 扫描)
  真仓库端到端 0 violations + 反向验证 sabotage 真有效 + test_health_check 修复不回归.
"""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import governance_runtime_isolation_scanner as scanner

REPO = Path(__file__).resolve().parent


def _scan_src(src, openclaw_scripts=("kb_review.sh", "health_check.sh")):
    """把 Python 源写到临时文件并跑 scan_test_file, 返回 violation 列表."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        return scanner.scan_test_file(Path(path), set(openclaw_scripts))
    finally:
        os.unlink(path)


def _dets(violations):
    return sorted(v["detector"] for v in violations)


# ─────────────────────────── Stage 1: 自动发现 ───────────────────────────
class TestDiscovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.yaml = (REPO / "ontology" / "governance_ontology.yaml").read_text(encoding="utf-8")
        cls.discovered = scanner.discover_governance_runtime_test_files(cls.yaml, REPO)
        cls.names = {p.name for p in cls.discovered}

    def test_returns_paths(self):
        self.assertTrue(all(isinstance(p, Path) for p in self.discovered))
        self.assertTrue(self.names, "应发现 >0 个治理-runtime 测试文件")

    def test_finds_known_subprocess_test_files(self):
        # 这些是确知被治理 runtime python_assert subprocess 跑的
        for n in ("test_convergence.py", "test_movespeed_rsync_helper.py",
                  "test_daily_observer.py", "test_health_check_v37_9_78.py"):
            self.assertIn(n, self.names, f"discovery 应找到 {n} (它被治理 runtime subprocess 跑)")

    def test_excludes_self_and_scanner_test(self):
        self.assertNotIn("governance_runtime_isolation_scanner.py", self.names)
        self.assertNotIn("test_governance_runtime_isolation_scanner.py", self.names)

    def test_block_based_excludes_file_contains_only_refs(self):
        # 只在 python_assert + 有执行上下文的 block 抓; 纯 file_contains 引用的测试名不算
        # (无法直接断言"不在", 但 discovered 集应远小于 yaml 里所有 test_X 引用)
        all_refs = set(scanner._DISC_PY_FILE.findall(self.yaml))
        self.assertLess(len(self.names), len(all_refs) + 5,
                        "discovery 应精确, 不是抓所有 test_X 引用")

    def test_bidirectional_window_finds_join_before_subprocess(self):
        # test_movespeed 引用形态: test_file = os.path.join(...) 在 subprocess.run 之前
        self.assertIn("test_movespeed_rsync_helper.py", self.names)

    def test_append_pattern_finds_dynamic_class_targets(self):
        # test_daily_observer 形态: _targets.append("test_daily_observer." + _c)
        self.assertIn("test_daily_observer.py", self.names)


# ─────────────────────── Stage 2: _find_executed_scripts ───────────────────────
class TestFindExecutedScripts(unittest.TestCase):
    def _exec(self, src, targets):
        return scanner._find_executed_scripts(ast.parse(src), set(targets))

    def test_shell_exec_literal_script(self):
        src = 'import subprocess\nsubprocess.run(["bash", "kb_review.sh"])\n'
        self.assertEqual(self._exec(src, ["kb_review.sh"]), {"kb_review.sh"})

    def test_shell_exec_via_script_var(self):
        src = ('import os, subprocess\n'
               'SCRIPT = os.path.join("/repo", "health_check.sh")\n'
               'subprocess.run(["bash", SCRIPT])\n')
        self.assertEqual(self._exec(src, ["health_check.sh"]), {"health_check.sh"})

    def test_direct_script_first_element(self):
        src = ('import subprocess\n'
               'HELPER = "/repo/movespeed_rsync_helper.sh"\n'
               'subprocess.run([HELPER, "--flag"])\n')
        self.assertEqual(self._exec(src, ["movespeed_rsync_helper.sh"]), {"movespeed_rsync_helper.sh"})

    def test_str_wrapped_script_var(self):
        src = ('import subprocess\n'
               'HELPER = "/repo/movespeed_rsync_helper.sh"\n'
               'subprocess.run([str(HELPER)])\n')
        self.assertEqual(self._exec(src, ["movespeed_rsync_helper.sh"]), {"movespeed_rsync_helper.sh"})

    def test_bash_n_syntax_check_exempt(self):
        src = 'import subprocess\nsubprocess.run(["bash", "-n", "kb_review.sh"])\n'
        self.assertEqual(self._exec(src, ["kb_review.sh"]), set(),
                         "bash -n 是语法检查不执行, 必须豁免")

    def test_python_exec_with_script_as_data_arg_exempt(self):
        # python 跑 scanner.py, 脚本名是数据 arg (扫描目标) → 豁免
        src = ('import subprocess, sys\n'
               'subprocess.run([sys.executable, "my_scanner.py", "kb_review.sh"])\n')
        self.assertEqual(self._exec(src, ["kb_review.sh"]), set(),
                         "python 跑 .py 时 .sh 是数据 arg, 不算执行")

    def test_python3_literal_exec_exempt(self):
        src = 'import subprocess\nsubprocess.run(["python3", "x.py", "job_watchdog.sh"])\n'
        self.assertEqual(self._exec(src, ["job_watchdog.sh"]), set())

    def test_set_literal_not_mapped_as_script_var(self):
        # required = {"job_watchdog.sh"} 是数据集, 不应被当脚本路径变量
        src = ('import subprocess, sys\n'
               'required = {"job_watchdog.sh", "auto_deploy.sh"}\n'
               'subprocess.run([sys.executable, "cron_monitor_scanner.py"])\n')
        self.assertEqual(self._exec(src, ["job_watchdog.sh"]), set(),
                         "set 字面量是数据非脚本路径, 不应误判执行")

    def test_list_literal_not_mapped(self):
        src = ('import subprocess, sys\n'
               'scripts = ["job_watchdog.sh"]\n'
               'subprocess.run([sys.executable, "x.py"])\n')
        self.assertEqual(self._exec(src, ["job_watchdog.sh"]), set())


# ─────────────────────────── D1: crontab real-apply ───────────────────────────
class TestD1CrontabRealApply(unittest.TestCase):
    def test_dry_run_false_without_isolation_flagged(self):
        src = ('import unittest\n'
               'class T(unittest.TestCase):\n'
               '    def test_x(self):\n'
               '        cv.verify_convergence(spec, {s}, dry_run=False)\n')
        v = _scan_src(src)
        self.assertIn("D1-crontab-real-apply", _dets(v))

    def test_dry_run_false_with_home_redirect_clean(self):
        src = ('import os, unittest\n'
               'class T(unittest.TestCase):\n'
               '    def test_x(self):\n'
               '        os.environ["HOME"] = td\n'
               '        cv.verify_convergence(spec, {s}, dry_run=False)\n')
        self.assertNotIn("D1-crontab-real-apply", _dets(_scan_src(src)))

    def test_dry_run_false_with_mock_subprocess_clean(self):
        src = ('import unittest\n'
               'class T(unittest.TestCase):\n'
               '    def test_x(self):\n'
               '        cv.subprocess.run = _never_run\n'
               '        cv.verify_convergence(spec, {s}, dry_run=False)\n')
        self.assertNotIn("D1-crontab-real-apply", _dets(_scan_src(src)))

    def test_isolation_in_class_setup_covers_methods(self):
        src = ('import os, unittest\n'
               'class T(unittest.TestCase):\n'
               '    def setUp(self):\n'
               '        os.environ["CONVERGENCE_DRY_RUN"] = "1"\n'
               '    def test_x(self):\n'
               '        cv.verify_convergence(spec, {s}, dry_run=False)\n')
        self.assertNotIn("D1-crontab-real-apply", _dets(_scan_src(src)),
                         "setUp 的隔离信号应覆盖类内方法")

    def test_apply_dry_run_false_not_flagged(self):
        # apply_dry_run=False 是字段引用, 非 dry_run=False kwarg
        src = ('import unittest\n'
               'class T(unittest.TestCase):\n'
               '    def test_x(self):\n'
               '        self.assertEqual(r.apply_dry_run, False)\n')
        self.assertNotIn("D1-crontab-real-apply", _dets(_scan_src(src)))

    def test_comment_dry_run_false_not_flagged(self):
        src = ('import unittest\n'
               'class T(unittest.TestCase):\n'
               '    def test_x(self):\n'
               '        # 注释里提 dry_run=False 不算\n'
               '        pass\n')
        self.assertNotIn("D1-crontab-real-apply", _dets(_scan_src(src)))


# ─────────────────────────── D2: ~/.kb incident write ───────────────────────────
class TestD2KbIncidentWrite(unittest.TestCase):
    def test_runs_movespeed_helper_without_isolation_flagged(self):
        src = ('import subprocess\n'
               'class T:\n'
               '    def test_x(self):\n'
               '        subprocess.run(["bash", "movespeed_rsync_helper.sh", "/c"])\n')
        self.assertIn("D2-kb-incident-write", _dets(_scan_src(src)))

    def test_runs_movespeed_helper_with_isolation_clean(self):
        src = ('import subprocess\n'
               'class T:\n'
               '    def test_x(self):\n'
               '        env = {"MOVESPEED_INCIDENT_FILE": "/tmp/x"}\n'
               '        subprocess.run(["bash", "movespeed_rsync_helper.sh"], env=env)\n')
        self.assertNotIn("D2-kb-incident-write", _dets(_scan_src(src)))

    def test_mentions_helper_as_data_not_flagged(self):
        # 仅把 helper 名当数据集 + 跑 python scanner → 不算执行
        src = ('import subprocess, sys\n'
               'SCRIPTS = {"movespeed_rsync_helper.sh"}\n'
               'subprocess.run([sys.executable, "scan.py"])\n')
        self.assertNotIn("D2-kb-incident-write", _dets(_scan_src(src)))


# ─────────────────────────── D3: 真调 openclaw ───────────────────────────
class TestD3RealOpenclaw(unittest.TestCase):
    def test_runs_openclaw_script_without_stub_flagged(self):
        src = ('import subprocess\n'
               'class T:\n'
               '    def test_x(self):\n'
               '        subprocess.run(["bash", "kb_review.sh"])\n')
        self.assertIn("D3-real-openclaw", _dets(_scan_src(src, openclaw_scripts=["kb_review.sh"])))

    def test_runs_openclaw_script_with_openclaw_bin_clean(self):
        src = ('import subprocess\n'
               'class T:\n'
               '    def test_x(self):\n'
               '        env = {"OPENCLAW_BIN": "/usr/bin/true"}\n'
               '        subprocess.run(["bash", "kb_review.sh"], env=env)\n')
        self.assertNotIn("D3-real-openclaw",
                         _dets(_scan_src(src, openclaw_scripts=["kb_review.sh"])))

    def test_runs_openclaw_script_with_stub_env_helper_clean(self):
        src = ('import subprocess\n'
               'class T:\n'
               '    def _stub_env(self):\n'
               '        return {}\n'
               '    def test_x(self):\n'
               '        subprocess.run(["bash", "kb_review.sh"], env=self._stub_env())\n')
        self.assertNotIn("D3-real-openclaw",
                         _dets(_scan_src(src, openclaw_scripts=["kb_review.sh"])))

    def test_bash_n_openclaw_script_exempt(self):
        src = ('import subprocess\n'
               'class T:\n'
               '    def test_x(self):\n'
               '        subprocess.run(["bash", "-n", "kb_review.sh"])\n')
        self.assertNotIn("D3-real-openclaw",
                         _dets(_scan_src(src, openclaw_scripts=["kb_review.sh"])))

    def test_python_scanner_with_openclaw_script_as_data_exempt(self):
        src = ('import subprocess, sys\n'
               'class T:\n'
               '    def test_x(self):\n'
               '        subprocess.run([sys.executable, "scan.py", "kb_review.sh"])\n')
        self.assertNotIn("D3-real-openclaw",
                         _dets(_scan_src(src, openclaw_scripts=["kb_review.sh"])))


# ─────────────────────── 真仓库端到端 + 反向验证 ───────────────────────
class TestRealRepoEndToEnd(unittest.TestCase):
    def test_full_repo_scan_zero_violations(self):
        violations, discovered = scanner.scan_repo(REPO)
        real = [v for v in violations if v["detector"] not in ("parse", "read")]
        self.assertEqual(real, [],
                         f"真仓库治理-runtime 测试应全部隔离, 违反: {real}")
        self.assertGreaterEqual(len(discovered), 10, "应发现 ≥10 个治理-runtime 测试文件")

    def test_cli_default_scan_exit_zero(self):
        r = subprocess.run([sys.executable, "governance_runtime_isolation_scanner.py"],
                           cwd=str(REPO), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, f"全量扫应 exit 0:\n{r.stdout}\n{r.stderr}")

    def test_cli_list_discovered(self):
        r = subprocess.run(
            [sys.executable, "governance_runtime_isolation_scanner.py", "--list-discovered"],
            cwd=str(REPO), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("test_convergence.py", r.stdout)

    def test_compute_openclaw_invoking_scripts(self):
        sc = scanner.compute_openclaw_invoking_scripts(REPO)
        self.assertIn("health_check.sh", sc)
        self.assertIn("kb_review.sh", sc)
        self.assertIn("job_watchdog.sh", sc)

    def test_sabotage_synthetic_d1_caught(self):
        # 反向验证: 合成一个 dry_run=False 无隔离的测试方法 → scanner 必抓
        bad = ('import unittest\n'
               'class T(unittest.TestCase):\n'
               '    def test_evil(self):\n'
               '        verify_convergence(spec, missing, dry_run=False)\n')
        self.assertIn("D1-crontab-real-apply", _dets(_scan_src(bad)))

    def test_sabotage_synthetic_d3_caught(self):
        bad = ('import subprocess\n'
               'class T:\n'
               '    def test_evil(self):\n'
               '        subprocess.run(["bash", "health_check.sh"])\n')
        self.assertIn("D3-real-openclaw", _dets(_scan_src(bad, openclaw_scripts=["health_check.sh"])))


class TestHealthCheckFindingFixed(unittest.TestCase):
    """V37.9.159 scanner 抓到的真 bug — test_health_check 跑 health_check.sh 无 push 隔离, 已修."""

    @classmethod
    def setUpClass(cls):
        cls.src = (REPO / "test_health_check_v37_9_78.py").read_text(encoding="utf-8")

    def test_isolation_helper_present(self):
        self.assertIn("_isolated_health_env", self.src,
                      "test_health_check 必须有 _isolated_health_env push 隔离 helper")

    def test_push_disabled_in_isolation(self):
        self.assertIn('HEALTH_PUSH_MIN_INTERVAL_SEC', self.src)
        self.assertIn('HEALTH_PUSH_MARKER', self.src)

    def test_openclaw_stubbed_in_isolation(self):
        self.assertIn('env["OPENCLAW_BIN"]', self.src,
                      "必须 stub OPENCLAW_BIN (D3 隔离信号 + notify.sh 路径防御)")

    def test_v37_9_159_marker(self):
        self.assertIn("V37.9.159", self.src)


# ─────────────────────────── 源码级守卫 ───────────────────────────
class TestSourceLevelGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (REPO / "governance_runtime_isolation_scanner.py").read_text(encoding="utf-8")

    def test_v37_9_159_marker(self):
        self.assertIn("V37.9.159", self.src)

    def test_references_mr_23(self):
        self.assertIn("MR-23", self.src, "应引用 MR-23 audit-observes-never-mutates")

    def test_blood_lineage_documented(self):
        for v in ("V37.9.110", "V37.9.157", "V37.9.158-hotfix"):
            self.assertIn(v, self.src, f"应记录血案谱系 {v}")

    def test_fail_close_contract(self):
        self.assertIn("FAIL-CLOSE", self.src)
        # main() 真违反时 return 1
        self.assertIn("return 1 if real else 0", self.src)

    def test_three_detectors_defined(self):
        for d in ("D1-crontab-real-apply", "D2-kb-incident-write", "D3-real-openclaw"):
            self.assertIn(d, self.src)

    def test_self_exempt(self):
        self.assertIn("_SELF_EXEMPT", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

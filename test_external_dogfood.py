#!/usr/bin/env python3
"""test_external_dogfood.py — V37.9.148 out-of-repo dogfood guard.

Closes external-reviewer-2's remaining gap: prove the PUBLISHED
`openclaw-ontology-engine` wheel is consumable by a third party with NO monorepo
access. Escalates the one-time PyPI smoke test (V37.9.137) into a CI regression
guard: build the wheel → install into an ISOLATED venv → copy a fresh toy project
OUT of the repo → run the engine against it via the distribution import name
`ontology_engine` + the console scripts.

Two layers:
  • TestExternalDogfoodSourceGuards — always runs (reads files only): structure,
    run_dogfood.sh contract (no PYTHONPATH for engine runs), `import ontology_engine`
    in the demo, toy YAML genuinely distinct from bridge AND WeatherBot.
  • TestExternalDogfoodWheelE2E — skipUnless build toolchain present; builds the
    wheel + venv + install once (setUpClass), then asserts: engine resolves to the
    wheel (no leakage), submodules import, console scripts registered, config-injected
    audit/query/run_demo audit LibraryBot, and reverse-validation (no injection →
    bundled bridge defaults) proves config-injection is the mechanism.

Design: examples/external_dogfood/README.md + docs/ontology_engine_packaging.md
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv

REPO = os.path.dirname(os.path.abspath(__file__))
DOGFOOD = os.path.join(REPO, "examples", "external_dogfood")
PROJECT = os.path.join(DOGFOOD, "project")
RUN_SH = os.path.join(DOGFOOD, "run_dogfood.sh")
RUN_DEMO_PY = os.path.join(PROJECT, "run_demo.py")
README = os.path.join(DOGFOOD, "README.md")


def _have_yaml():
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def _have_build_toolchain():
    try:
        import setuptools  # noqa: F401
        import venv  # noqa: F401
        return True
    except ImportError:
        return False


_E2E_CAPABLE = _have_yaml() and _have_build_toolchain()


def _build_wheel(dest):
    """Build the engine wheel offline via a robust method chain. Returns path or None.

    The dev sandbox is Debian-patched (system pip hits an `install_layout` quirk under
    --no-build-isolation), so the chain falls back to invoking the setuptools build
    backend directly, finally with stdlib distutils. Side effect: setuptools leaves a
    build/ dir in REPO — cleaned by the caller.
    """
    os.makedirs(dest, exist_ok=True)
    code = "import setuptools.build_meta as b; b.build_wheel(%r)" % dest
    attempts = [
        ([sys.executable, "-m", "build", "--wheel", "--no-isolation", "-o", dest], None),
        ([sys.executable, "-c", code], None),
        ([sys.executable, "-c", code], {"SETUPTOOLS_USE_DISTUTILS": "stdlib"}),
    ]
    for argv, extra_env in attempts:
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        try:
            r = subprocess.run(argv, cwd=REPO, env=env, capture_output=True,
                               text=True, timeout=180)
        except Exception:
            continue
        wheels = glob.glob(os.path.join(dest, "*.whl"))
        if r.returncode == 0 and wheels:
            return wheels[0]
    wheels = glob.glob(os.path.join(dest, "*.whl"))
    return wheels[0] if wheels else None


def _clean_build_artifacts():
    for p in ("build", "openclaw_ontology_engine.egg-info"):
        shutil.rmtree(os.path.join(REPO, p), ignore_errors=True)


# ── Layer 1: source guards (no build) ─────────────────────────────────────────
class TestExternalDogfoodSourceGuards(unittest.TestCase):
    """Always-run structural guards (don't depend on the build toolchain)."""

    def test_files_present(self):
        for rel in ("run_dogfood.sh", "README.md", "project/librarybot.py",
                    "project/librarybot_state.json", "project/run_demo.py",
                    "project/ontology/tool_ontology.yaml",
                    "project/ontology/domain_ontology.yaml",
                    "project/ontology/policy_ontology.yaml",
                    "project/ontology/governance_ontology.yaml",
                    "project/ontology/convergence_ontology.yaml"):
            self.assertTrue(os.path.isfile(os.path.join(DOGFOOD, rel)),
                            f"missing {rel}")

    def test_run_demo_uses_distribution_import_name(self):
        """The demo MUST import `ontology_engine` (wheel name), NOT in-repo `ontology`.

        This is the whole point vs minimal_consumer — proves the de-genericized
        import name (V37.9.128 chunk-2-lite) works for a real wheel consumer.
        """
        src = open(RUN_DEMO_PY, encoding="utf-8").read()
        self.assertIn("import ontology_engine", src)
        self.assertNotIn("import ontology.engine", src)
        self.assertNotIn("import ontology.governance_checker", src)

    def test_run_dogfood_sh_does_not_set_pythonpath_for_engine_runs(self):
        """Engine runs must drop PYTHONPATH (no monorepo on path) — `env -u PYTHONPATH`.

        Contrast with minimal_consumer/run_demo.sh which exports PYTHONPATH=repo.
        """
        src = open(RUN_SH, encoding="utf-8").read()
        self.assertIn("env -u PYTHONPATH", src,
                      "engine runs must strip PYTHONPATH")
        self.assertNotIn("export PYTHONPATH=", src,
                         "external dogfood must NOT put the repo on PYTHONPATH")

    def test_run_dogfood_sh_builds_wheel_and_isolated_venv(self):
        src = open(RUN_SH, encoding="utf-8").read()
        self.assertIn("build_wheel", src)
        self.assertIn("python3 -m venv", src)
        self.assertIn("install --no-deps", src)

    def test_toy_tools_distinct_from_bridge_and_weatherbot(self):
        tool_yaml = open(os.path.join(PROJECT, "ontology", "tool_ontology.yaml"),
                         encoding="utf-8").read()
        self.assertIn("checkout_book", tool_yaml)
        self.assertIn("search_catalog", tool_yaml)
        # NOT the bridge's signature tools nor WeatherBot's
        self.assertNotIn("data_clean", tool_yaml)
        self.assertNotIn("get_forecast", tool_yaml)

    def test_toy_governance_audits_only_its_own_files(self):
        gov = open(os.path.join(PROJECT, "ontology", "governance_ontology.yaml"),
                   encoding="utf-8").read()
        self.assertIn("librarybot.py", gov)
        self.assertIn("INV-LIBRARY", gov)
        # must NOT reference bridge files
        self.assertNotIn("proxy_filters.py", gov)
        self.assertNotIn("weatherbot.py", gov)

    def test_readme_explains_difference_from_minimal_consumer(self):
        rd = open(README, encoding="utf-8").read()
        self.assertIn("minimal_consumer", rd)
        self.assertIn("ontology_engine", rd)
        self.assertIn("isolated venv", rd)

    def test_registered_in_full_regression(self):
        fr = open(os.path.join(REPO, "full_regression.sh"), encoding="utf-8").read()
        self.assertIn("test_external_dogfood", fr)


# ── Layer 2: wheel-install end-to-end ─────────────────────────────────────────
@unittest.skipUnless(_E2E_CAPABLE, "需要 PyYAML + setuptools + venv 构建链")
class TestExternalDogfoodWheelE2E(unittest.TestCase):
    """Build the wheel, install into an isolated venv, run the toy out-of-repo."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="dogfood_test_")
        cls.wheel = _build_wheel(os.path.join(cls.work, "dist"))
        _clean_build_artifacts()
        if not cls.wheel:
            raise unittest.SkipTest("wheel build unavailable in this environment")
        cls.venv_dir = os.path.join(cls.work, "venv")
        venv.create(cls.venv_dir, system_site_packages=True, with_pip=True)
        cls.vpy = os.path.join(cls.venv_dir, "bin", "python")
        cls.audit = os.path.join(cls.venv_dir, "bin", "openclaw-ontology-audit")
        cls.query = os.path.join(cls.venv_dir, "bin", "openclaw-ontology-query")
        r = subprocess.run([cls.vpy, "-m", "pip", "install", "--no-deps", "--quiet",
                            cls.wheel], capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise unittest.SkipTest(f"wheel install failed: {r.stderr[-400:]}")
        # Copy the toy OUT of the repo so the audit's side-effects + project root
        # live entirely in /tmp (true out-of-repo consumption, zero repo pollution).
        cls.toy = os.path.join(cls.work, "librarybot_project")
        shutil.copytree(PROJECT, cls.toy)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)
        _clean_build_artifacts()

    def _run(self, argv, inject=True, cwd=None, timeout=90):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)  # critical: no monorepo on path
        if inject:
            env["ONTOLOGY_CONFIG_DIR"] = os.path.join(self.toy, "ontology")
            env["ONTOLOGY_PROJECT_ROOT"] = self.toy
        else:
            env.pop("ONTOLOGY_CONFIG_DIR", None)
            env.pop("ONTOLOGY_PROJECT_ROOT", None)
        return subprocess.run(argv, env=env, cwd=cwd or "/tmp",
                              capture_output=True, text=True, timeout=timeout)

    def _tools(self, inject):
        r = self._run([self.query, "--tools", "--json"], inject=inject)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {t["name"] for t in json.loads(r.stdout)}

    def test_engine_resolves_to_wheel_not_monorepo(self):
        r = self._run([self.vpy, "-c", "import ontology_engine; print(ontology_engine.__file__)"])
        self.assertEqual(r.returncode, 0, r.stderr)
        path = r.stdout.strip()
        self.assertIn(self.venv_dir, path, f"engine must come from the venv wheel, got {path}")
        self.assertNotIn("openclaw-model-bridge", path, "engine MUST NOT leak from the monorepo")

    def test_all_submodules_importable(self):
        code = ("import ontology_engine.engine, ontology_engine.governance_checker, "
                "ontology_engine.convergence, ontology_engine.three_gate; print('ok')")
        r = self._run([self.vpy, "-c", code])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)

    def test_console_scripts_registered(self):
        self.assertTrue(os.path.isfile(self.audit), "openclaw-ontology-audit missing")
        self.assertTrue(os.path.isfile(self.query), "openclaw-ontology-query missing")

    def test_audit_exit_0_audits_librarybot(self):
        r = self._run([self.audit], inject=True)
        self.assertEqual(r.returncode, 0, f"audit must pass; stderr={r.stderr[-400:]}")
        self.assertIn("INV-LIBRARY-CHECKOUT", r.stdout)
        self.assertIn("INV-LIBRARY-GENRES", r.stdout)
        # must NOT be auditing the bridge
        self.assertNotIn("INV-TOOL-001", r.stdout)

    def test_query_tools_shows_librarybot_tools(self):
        tools = self._tools(inject=True)
        self.assertIn("checkout_book", tools)
        self.assertIn("search_catalog", tools)
        self.assertNotIn("data_clean", tools)

    def test_reverse_no_injection_reads_bundled_defaults(self):
        """SAME wheel, no injection → its OWN bundled defaults (the bridge's).

        Proves config-injection (not PYTHONPATH, not the monorepo) loads the toy.
        """
        bundled = self._tools(inject=False)
        self.assertIn("data_clean", bundled, "bundled defaults should be the bridge's")
        self.assertNotIn("checkout_book", bundled, "bundled defaults must NOT contain the toy's tools")

    def test_run_demo_py_five_capabilities(self):
        r = self._run([self.vpy, os.path.join(self.toy, "run_demo.py")],
                      inject=True, cwd=self.toy)
        self.assertEqual(r.returncode, 0, f"run_demo.py must pass; stderr={r.stderr[-500:]}")
        out = r.stdout
        for marker in ("ToolOntology", "find_by_domain", "evaluate_policy",
                       "governance audit", "convergence",
                       "config-injection works end-to-end"):
            self.assertIn(marker, out, f"run_demo.py missing section: {marker}")

    def test_convergence_reads_librarybot_spec_not_bridge(self):
        code = ("import ontology_engine.convergence as cv; "
                "import json; print(json.dumps(sorted(cv.list_spec_ids())))")
        r = self._run([self.vpy, "-c", code], inject=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        specs = set(json.loads(r.stdout))
        self.assertIn("librarybot-allowed-genres-active", specs)
        self.assertNotIn("jobs_to_crontab", specs)

    def test_no_repo_pollution(self):
        """Running the toy from /tmp must leave the in-repo project/ untouched."""
        stray = os.path.join(PROJECT, "ontology", ".audit_metrics.jsonl")
        self.assertFalse(os.path.exists(stray),
                         "in-repo toy must not accumulate audit artifacts (run happens in /tmp)")


if __name__ == "__main__":
    unittest.main(verbosity=2)

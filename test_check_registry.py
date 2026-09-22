#!/usr/bin/env python3
"""
Unit tests for check_registry.py — V28 校验器单测
Run: python3 -m pytest test_check_registry.py -v
  or: python3 test_check_registry.py
"""
import json
import os
import tempfile
import textwrap
import unittest

from check_registry import validate, load_yaml, check_filemap_completeness, check_crontab


# ---------------------------------------------------------------------------
# Helper: write temp YAML for testing
# ---------------------------------------------------------------------------

def write_temp_yaml(content, tmpdir=None):
    """Write content to a temp .yaml file, return its path."""
    fd, path = tempfile.mkstemp(suffix=".yaml", dir=tmpdir)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


VALID_YAML = textwrap.dedent("""\
    version: 1
    jobs:
      - id: test_job_1
        scheduler: system
        entry: test_check_registry.py
        interval: "0 * * * *"
        log: ~/test.log
        needs_api_key: false
        enabled: true
        description: A test job
      - id: test_job_2
        scheduler: openclaw
        entry: proxy_filters.py
        interval: "0 8 * * *"
        log: ~/test2.log
        needs_api_key: false
        enabled: false
        description: Disabled test job
""")


# ---------------------------------------------------------------------------
# load_yaml tests
# ---------------------------------------------------------------------------

class TestLoadYaml(unittest.TestCase):

    def test_basic_parse(self):
        path = write_temp_yaml(VALID_YAML)
        try:
            data = load_yaml(path)
            self.assertEqual(data["version"], 1)
            self.assertEqual(len(data["jobs"]), 2)
            self.assertEqual(data["jobs"][0]["id"], "test_job_1")
            self.assertEqual(data["jobs"][1]["id"], "test_job_2")
        finally:
            os.unlink(path)

    def test_boolean_parsing(self):
        """enabled: false should parse as Python False, not string 'false'."""
        path = write_temp_yaml(VALID_YAML)
        try:
            data = load_yaml(path)
            self.assertIs(data["jobs"][0]["enabled"], True)
            self.assertIs(data["jobs"][1]["enabled"], False)
        finally:
            os.unlink(path)

    def test_inline_comment_stripped(self):
        """Inline comments like 'false  # reason' should be stripped."""
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: commented_job
                scheduler: system
                entry: test_check_registry.py
                enabled: false  # disabled for now
                description: test
        """)
        path = write_temp_yaml(yaml_content)
        try:
            data = load_yaml(path)
            self.assertIs(data["jobs"][0]["enabled"], False)
        finally:
            os.unlink(path)

    def test_quoted_value_with_hash(self):
        """Values like interval: '0 9 * * 1' should not be broken by # stripping."""
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: quoted_job
                scheduler: system
                entry: test_check_registry.py
                interval: "0 9 * * 1"
                enabled: true
                description: "a job # with hash in desc"
        """)
        path = write_temp_yaml(yaml_content)
        try:
            data = load_yaml(path)
            self.assertEqual(data["jobs"][0]["interval"], "0 9 * * 1")
        finally:
            os.unlink(path)

    def test_empty_file(self):
        """Empty/comment-only YAML returns None (PyYAML) or empty dict."""
        path = write_temp_yaml("# only comments\n")
        try:
            data = load_yaml(path)
            # PyYAML safe_load returns None for empty files; validate() handles this
            if data is None:
                data = {}
            self.assertEqual(data.get("jobs", []), [])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# validate() tests
# ---------------------------------------------------------------------------

class TestValidate(unittest.TestCase):

    def test_valid_registry_no_errors(self):
        """A well-formed registry should produce zero errors."""
        path = write_temp_yaml(VALID_YAML)
        try:
            errors, warnings = validate(path)
            self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        finally:
            os.unlink(path)

    def test_duplicate_id_detected(self):
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: dup_job
                scheduler: system
                entry: test_check_registry.py
                enabled: false
              - id: dup_job
                scheduler: system
                entry: proxy_filters.py
                enabled: false
        """)
        path = write_temp_yaml(yaml_content)
        try:
            errors, _ = validate(path)
            dup_errors = [e for e in errors if "Duplicate" in e]
            self.assertTrue(len(dup_errors) >= 1, f"Expected duplicate error, got: {errors}")
        finally:
            os.unlink(path)

    def test_missing_required_field(self):
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: incomplete_job
                scheduler: system
        """)
        path = write_temp_yaml(yaml_content)
        try:
            errors, _ = validate(path)
            missing_errors = [e for e in errors if "missing field" in e]
            self.assertTrue(len(missing_errors) >= 1, f"Expected missing field error, got: {errors}")
        finally:
            os.unlink(path)

    def test_invalid_scheduler(self):
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: bad_sched
                scheduler: kubernetes
                entry: test_check_registry.py
                enabled: false
        """)
        path = write_temp_yaml(yaml_content)
        try:
            errors, _ = validate(path)
            sched_errors = [e for e in errors if "invalid scheduler" in e]
            self.assertTrue(len(sched_errors) >= 1)
        finally:
            os.unlink(path)

    def test_nonexistent_entry_warns(self):
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: ghost_job
                scheduler: system
                entry: this_file_does_not_exist.sh
                enabled: true
                log: ~/ghost.log
                description: ghost
        """)
        path = write_temp_yaml(yaml_content)
        try:
            _, warnings = validate(path)
            entry_warnings = [w for w in warnings if "entry not found" in w]
            self.assertTrue(len(entry_warnings) >= 1)
        finally:
            os.unlink(path)

    def test_enabled_without_log_warns(self):
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: no_log_job
                scheduler: system
                entry: test_check_registry.py
                enabled: true
                description: has desc but no log
        """)
        path = write_temp_yaml(yaml_content)
        try:
            _, warnings = validate(path)
            log_warnings = [w for w in warnings if "missing: log" in w]
            self.assertTrue(len(log_warnings) >= 1)
        finally:
            os.unlink(path)

    def test_enabled_without_description_warns(self):
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: no_desc_job
                scheduler: system
                entry: test_check_registry.py
                enabled: true
                log: ~/test.log
        """)
        path = write_temp_yaml(yaml_content)
        try:
            _, warnings = validate(path)
            desc_warnings = [w for w in warnings if "missing: description" in w]
            self.assertTrue(len(desc_warnings) >= 1)
        finally:
            os.unlink(path)

    def test_system_enabled_without_interval_warns(self):
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: no_interval
                scheduler: system
                entry: test_check_registry.py
                enabled: true
                log: ~/test.log
                description: no interval
        """)
        path = write_temp_yaml(yaml_content)
        try:
            _, warnings = validate(path)
            interval_warnings = [w for w in warnings if "without interval" in w]
            self.assertTrue(len(interval_warnings) >= 1)
        finally:
            os.unlink(path)

    def test_no_jobs_is_error(self):
        yaml_content = "version: 1\njobs:\n"
        path = write_temp_yaml(yaml_content)
        try:
            errors, _ = validate(path)
            self.assertTrue(any("No jobs" in e for e in errors))
        finally:
            os.unlink(path)

    def test_unparseable_file(self):
        path = write_temp_yaml("{{{{invalid yaml!!!!!")
        try:
            errors, _ = validate(path)
            self.assertTrue(any("parse error" in e.lower() or "No jobs" in e for e in errors))
        finally:
            os.unlink(path)

    def test_disabled_job_skips_extra_checks(self):
        """Disabled jobs should not trigger 'missing log/description' warnings."""
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: disabled_bare
                scheduler: system
                entry: test_check_registry.py
                enabled: false
        """)
        path = write_temp_yaml(yaml_content)
        try:
            errors, warnings = validate(path)
            self.assertEqual(errors, [])
            log_warns = [w for w in warnings if "missing: log" in w or "missing: description" in w]
            self.assertEqual(log_warns, [], f"Disabled job should not warn: {log_warns}")
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# check_filemap_completeness() tests
# ---------------------------------------------------------------------------

class TestFileMapCompleteness(unittest.TestCase):

    def test_missing_auto_deploy_skips(self):
        """If auto_deploy.sh doesn't exist, should return warning and skip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_content = textwrap.dedent("""\
                version: 1
                jobs:
                  - id: test_job
                    scheduler: system
                    entry: test.sh
                    enabled: true
            """)
            path = os.path.join(tmpdir, "jobs_registry.yaml")
            with open(path, "w") as f:
                f.write(yaml_content)
            errors, warnings = check_filemap_completeness(path)
            self.assertTrue(any("auto_deploy.sh" in w for w in warnings))


# ---------------------------------------------------------------------------
# Tier validation tests (V32: Job 分层治理)
# ---------------------------------------------------------------------------

class TestTierValidation(unittest.TestCase):

    def test_valid_tier_no_error(self):
        """Valid tier values should not produce errors."""
        for tier in ("core", "auxiliary", "experiment"):
            yaml_content = textwrap.dedent(f"""\
                version: 1
                jobs:
                  - id: tier_test
                    scheduler: system
                    entry: test_check_registry.py
                    enabled: true
                    tier: {tier}
                    log: ~/test.log
                    description: test
            """)
            path = write_temp_yaml(yaml_content)
            try:
                errors, _ = validate(path)
                self.assertEqual(errors, [], f"tier={tier} should be valid, got: {errors}")
            finally:
                os.unlink(path)

    def test_invalid_tier_error(self):
        """Invalid tier value should produce an error."""
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: bad_tier
                scheduler: system
                entry: test_check_registry.py
                enabled: true
                tier: critical
                log: ~/test.log
                description: test
        """)
        path = write_temp_yaml(yaml_content)
        try:
            errors, _ = validate(path)
            tier_errors = [e for e in errors if "invalid tier" in e]
            self.assertTrue(len(tier_errors) >= 1, f"Expected tier error, got: {errors}")
        finally:
            os.unlink(path)

    def test_missing_tier_warns(self):
        """Enabled job without tier should produce a warning."""
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: no_tier
                scheduler: system
                entry: test_check_registry.py
                enabled: true
                log: ~/test.log
                description: test
        """)
        path = write_temp_yaml(yaml_content)
        try:
            _, warnings = validate(path)
            tier_warns = [w for w in warnings if "missing tier" in w]
            self.assertTrue(len(tier_warns) >= 1, f"Expected tier warning, got: {warnings}")
        finally:
            os.unlink(path)

    def test_disabled_job_no_tier_warning(self):
        """Disabled jobs should not warn about missing tier."""
        yaml_content = textwrap.dedent("""\
            version: 1
            jobs:
              - id: disabled_no_tier
                scheduler: system
                entry: test_check_registry.py
                enabled: false
        """)
        path = write_temp_yaml(yaml_content)
        try:
            _, warnings = validate(path)
            tier_warns = [w for w in warnings if "missing tier" in w]
            self.assertEqual(tier_warns, [])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# check_crontab() interval drift tests (V36.2)
# ---------------------------------------------------------------------------

class TestIntervalDrift(unittest.TestCase):
    """V36.2: Verify registry interval vs actual crontab drift detection."""

    def _mock_check_crontab(self, yaml_content, crontab_lines):
        """Helper: run check_crontab with mocked crontab output."""
        import subprocess
        import unittest.mock as mock

        path = write_temp_yaml(yaml_content)
        try:
            mock_result = mock.MagicMock()
            mock_result.stdout = "\n".join(crontab_lines)
            with mock.patch("subprocess.run", return_value=mock_result):
                errors, warnings = check_crontab(path)
            return errors, warnings
        finally:
            os.unlink(path)

    def test_matching_interval_no_error(self):
        """When crontab interval matches registry, no drift error."""
        yaml = textwrap.dedent("""\
            version: 1
            jobs:
              - id: arxiv_monitor
                scheduler: system
                entry: run_arxiv.sh
                interval: "0 8,20 * * *"
                enabled: true
                log: ~/test.log
                description: test
        """)
        crontab = ["0 8,20 * * * bash ~/run_arxiv.sh >> ~/test.log 2>&1"]
        errors, warnings = self._mock_check_crontab(yaml, crontab)
        drift_errors = [e for e in errors if "间隔漂移" in e]
        self.assertEqual(drift_errors, [])

    def test_mismatched_interval_detected(self):
        """When crontab has different interval, drift is detected as ERROR."""
        yaml = textwrap.dedent("""\
            version: 1
            jobs:
              - id: arxiv_monitor
                scheduler: system
                entry: run_arxiv.sh
                interval: "0 8,20 * * *"
                enabled: true
                log: ~/test.log
                description: test
        """)
        crontab = ["0 */3 * * * bash ~/run_arxiv.sh >> ~/test.log 2>&1"]
        errors, warnings = self._mock_check_crontab(yaml, crontab)
        drift_errors = [e for e in errors if "间隔漂移" in e]
        self.assertEqual(len(drift_errors), 1)
        self.assertIn("0 8,20 * * *", drift_errors[0])
        self.assertIn("0 */3 * * *", drift_errors[0])

    def test_disabled_job_no_drift_check(self):
        """Disabled jobs should not be checked for drift."""
        yaml = textwrap.dedent("""\
            version: 1
            jobs:
              - id: old_job
                scheduler: system
                entry: old.sh
                interval: "0 8 * * *"
                enabled: false
                log: ~/test.log
                description: test
        """)
        crontab = ["0 */2 * * * bash ~/old.sh >> ~/test.log 2>&1"]
        errors, warnings = self._mock_check_crontab(yaml, crontab)
        drift_errors = [e for e in errors if "间隔漂移" in e]
        self.assertEqual(drift_errors, [])

    def test_missing_from_crontab_still_warns(self):
        """Script not in crontab at all should still produce a warning."""
        yaml = textwrap.dedent("""\
            version: 1
            jobs:
              - id: missing_job
                scheduler: system
                entry: not_in_crontab.sh
                interval: "0 9 * * *"
                enabled: true
                log: ~/test.log
                description: test
        """)
        crontab = ["0 8 * * * bash ~/other.sh >> ~/test.log 2>&1"]
        errors, warnings = self._mock_check_crontab(yaml, crontab)
        missing_warns = [w for w in warnings if "未找到" in w]
        self.assertEqual(len(missing_warns), 1)

    def test_multiple_drift_detected(self):
        """Multiple jobs with drift should each produce an error."""
        yaml = textwrap.dedent("""\
            version: 1
            jobs:
              - id: job_a
                scheduler: system
                entry: a.sh
                interval: "0 8 * * *"
                enabled: true
                log: ~/a.log
                description: a
              - id: job_b
                scheduler: system
                entry: b.sh
                interval: "30 12 * * *"
                enabled: true
                log: ~/b.log
                description: b
        """)
        crontab = [
            "0 4 * * * bash ~/a.sh >> ~/a.log 2>&1",
            "0 12 * * * bash ~/b.sh >> ~/b.log 2>&1",
        ]
        errors, warnings = self._mock_check_crontab(yaml, crontab)
        drift_errors = [e for e in errors if "间隔漂移" in e]
        self.assertEqual(len(drift_errors), 2)


# ---------------------------------------------------------------------------
# Integration: validate the actual jobs_registry.yaml
# ---------------------------------------------------------------------------

class TestRealRegistry(unittest.TestCase):
    """Validate the actual project registry file."""

    def test_actual_registry_no_errors(self):
        real_path = os.path.join(os.path.dirname(__file__), "jobs_registry.yaml")
        if not os.path.exists(real_path):
            self.skipTest("jobs_registry.yaml not found")
        errors, warnings = validate(real_path)
        self.assertEqual(errors, [], f"Real registry has errors: {errors}")


# ---------------------------------------------------------------------------
# V37.9.355 后记 (2026-09-22): registry 管理的 .sh 脚本在 git 索引里必须带执行位
# ---------------------------------------------------------------------------
class TestRegistryEntryExecBit(unittest.TestCase):
    """registry 管理的 .sh 脚本在 git 索引里必须是 100755。

    preflight 4/19 只在 Mac Mini --full 模式才 warn「缺少可执行权限」, dev 永远看不到; 而用 tmp+rename
    方式重写文件的脚本会静默丢执行位——V37.9.334 新建 check_upgrade.sh 漏 +x (V37.9.350 后记抓到),
    V37.9.352 文档刷新重写 finance_news 头注时 100755→100644 (2026-09-22 Mac Mini preflight 抓到)
    = 同一 bug 类三周两次演出 → 把判据前移到 dev CI (镜像 V37.9.338 把 INV-CRON-003 判据前移)。
    检查对象 = git 索引模式 (Mac Mini 同步/preflight 看到的就是它), 不看工作树 (umask/Windows 环境不可靠)。
    cron 本身走 bash -lc 'bash ~/x.sh' 不受执行位影响, 这里守的是 preflight 噪声面与部署一致性。
    """

    @classmethod
    def setUpClass(cls):
        import subprocess
        repo = os.path.dirname(os.path.abspath(__file__))
        try:
            out = subprocess.run(["git", "ls-files", "-s"], capture_output=True, text=True,
                                 cwd=repo, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise unittest.SkipTest("git 不可用")
        if out.returncode != 0:
            raise unittest.SkipTest("非 git 仓库")
        cls.modes = {}
        for line in out.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                cls.modes[parts[1]] = parts[0].split()[0]
        cls.registry = load_yaml(os.path.join(repo, "jobs_registry.yaml"))

    def _sh_entries(self):
        jobs = self.registry.get("jobs", self.registry)
        entries = []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            e = str(j.get("entry", ""))
            if e.endswith(".sh") and e in self.modes:
                entries.append(e)
        return entries

    def test_every_registry_shell_entry_is_executable_in_index(self):
        entries = self._sh_entries()
        self.assertGreaterEqual(len(entries), 30, "防空转: registry .sh entry 应 ≥30 个且都在 git 索引里")
        bad = [e for e in entries if self.modes[e] != "100755"]
        self.assertEqual(bad, [],
                         "缺执行位 (修: git update-index --chmod=+x <path>): " + ", ".join(bad))

    def test_blood_case_files_covered(self):
        """两次血案文件必须在本守卫的检查集合里 (否则守卫对它们空转)。"""
        entries = self._sh_entries()
        self.assertIn("jobs/finance_news/run_finance_news.sh", entries)
        self.assertIn("check_upgrade.sh", entries)


if __name__ == "__main__":
    unittest.main(verbosity=2)

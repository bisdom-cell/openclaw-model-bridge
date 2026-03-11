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

from check_registry import validate, load_yaml, check_filemap_completeness


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

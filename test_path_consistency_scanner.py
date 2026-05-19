"""V37.9.82 INV-PATH-CONSISTENCY-001 — path_consistency_scanner.py 守卫单测.

防御 V37.9.56-hotfix / V37.9.66 同款路径假设错配血案 (Class B failure_modes_catalog).

测试覆盖:
- 纯函数 expected_dst_for_src (V37.9.66 path convention)
- parse_auto_deploy_file_map 解析 robustness
- load_jobs_registry 过滤逻辑
- scan_path_consistency 端到端
- ALLOWED_NON_STANDARD_DST 豁免机制 + reason 必填
- 实际 repo 0 violations 当前快照
- 反向 sabotage 守卫真有效
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))


class TestExpectedDstForSrc(unittest.TestCase):
    """V37.9.66 path convention 纯函数验证."""

    def test_jobs_subdir_prefix_gets_openclaw(self):
        from path_consistency_scanner import expected_dst_for_src
        self.assertEqual(
            expected_dst_for_src("jobs/hf_papers/run_hf_papers.sh"),
            "$HOME/.openclaw/jobs/hf_papers/run_hf_papers.sh",
        )

    def test_root_entry_gets_home_only(self):
        from path_consistency_scanner import expected_dst_for_src
        self.assertEqual(
            expected_dst_for_src("health_check.sh"),
            "$HOME/health_check.sh",
        )

    def test_nested_root_path_no_openclaw(self):
        """providers.d/ 子目录但不在 jobs/ → $HOME/ 直接."""
        from path_consistency_scanner import expected_dst_for_src
        self.assertEqual(
            expected_dst_for_src("providers.d/doubao_provider.py"),
            "$HOME/providers.d/doubao_provider.py",
        )

    def test_empty_string_edge_case(self):
        from path_consistency_scanner import expected_dst_for_src
        # Empty entry shouldn't crash — returns $HOME/
        self.assertEqual(expected_dst_for_src(""), "$HOME/")


class TestParseFileMap(unittest.TestCase):
    """Auto_deploy.sh FILE_MAP 解析鲁棒性."""

    def _write_fixture(self, content: str) -> str:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_parse_minimal_file_map(self):
        from path_consistency_scanner import parse_auto_deploy_file_map
        fixture = textwrap.dedent("""\
            #!/bin/bash
            declare -a FILE_MAP=(
                "a.sh|$HOME/a.sh"
                "jobs/x/run.sh|$HOME/.openclaw/jobs/x/run.sh"
            )
            echo done
        """)
        path = self._write_fixture(fixture)
        result = parse_auto_deploy_file_map(path)
        self.assertEqual(result, {
            "a.sh": "$HOME/a.sh",
            "jobs/x/run.sh": "$HOME/.openclaw/jobs/x/run.sh",
        })

    def test_skip_comment_only_lines(self):
        from path_consistency_scanner import parse_auto_deploy_file_map
        fixture = textwrap.dedent("""\
            declare -a FILE_MAP=(
                # this is a section header
                "a.sh|$HOME/a.sh"
                # another comment
                "b.sh|$HOME/b.sh"
            )
        """)
        path = self._write_fixture(fixture)
        result = parse_auto_deploy_file_map(path)
        self.assertEqual(len(result), 2)
        self.assertIn("a.sh", result)
        self.assertIn("b.sh", result)

    def test_tolerate_trailing_inline_comment(self):
        from path_consistency_scanner import parse_auto_deploy_file_map
        fixture = textwrap.dedent("""\
            declare -a FILE_MAP=(
                "x.sh|$HOME/x.sh"  # V37 some comment
            )
        """)
        path = self._write_fixture(fixture)
        result = parse_auto_deploy_file_map(path)
        self.assertEqual(result.get("x.sh"), "$HOME/x.sh")

    def test_missing_file_map_block_raises(self):
        from path_consistency_scanner import parse_auto_deploy_file_map
        fixture = "#!/bin/bash\necho 'no FILE_MAP here'\n"
        path = self._write_fixture(fixture)
        with self.assertRaises(RuntimeError):
            parse_auto_deploy_file_map(path)


class TestScanIntegration(unittest.TestCase):
    """端到端 scan_path_consistency 测试."""

    def _write_repo(self, registry_content: str, file_map_content: str) -> str:
        """Create temp repo dir with jobs_registry.yaml + auto_deploy.sh."""
        repo = tempfile.mkdtemp(prefix="path_test_")
        self.addCleanup(self._cleanup_repo, repo)
        with open(os.path.join(repo, "jobs_registry.yaml"), "w") as f:
            f.write(registry_content)
        with open(os.path.join(repo, "auto_deploy.sh"), "w") as f:
            f.write(f"#!/bin/bash\ndeclare -a FILE_MAP=(\n{file_map_content}\n)\n")
        return repo

    def _cleanup_repo(self, path: str) -> None:
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def test_all_consistent_returns_empty_findings(self):
        from path_consistency_scanner import scan_path_consistency
        registry = textwrap.dedent("""\
            jobs:
              - id: test1
                scheduler: system
                entry: test1.sh
                enabled: true
              - id: test2
                scheduler: system
                entry: jobs/foo/run_foo.sh
                enabled: true
        """)
        file_map = '    "test1.sh|$HOME/test1.sh"\n    "jobs/foo/run_foo.sh|$HOME/.openclaw/jobs/foo/run_foo.sh"'
        repo = self._write_repo(registry, file_map)
        # Write the source files so MISSING_FILE_ON_DISK doesn't fire
        with open(os.path.join(repo, "test1.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        os.makedirs(os.path.join(repo, "jobs", "foo"))
        with open(os.path.join(repo, "jobs", "foo", "run_foo.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        findings = scan_path_consistency(repo)
        self.assertEqual(findings, [])

    def test_missing_file_map_detected(self):
        from path_consistency_scanner import scan_path_consistency
        registry = textwrap.dedent("""\
            jobs:
              - id: orphan
                scheduler: system
                entry: not_in_filemap.sh
                enabled: true
        """)
        file_map = '    "other.sh|$HOME/other.sh"'
        repo = self._write_repo(registry, file_map)
        with open(os.path.join(repo, "other.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        findings = scan_path_consistency(repo)
        types = [f["type"] for f in findings]
        self.assertIn("MISSING_FILE_MAP", types)

    def test_dst_mismatch_detected(self):
        from path_consistency_scanner import scan_path_consistency
        registry = textwrap.dedent("""\
            jobs: []
        """)
        # src=jobs/X/Y.sh but dst missing .openclaw/ prefix → DST_MISMATCH
        file_map = '    "jobs/x/run.sh|$HOME/wrong/path/run.sh"'
        repo = self._write_repo(registry, file_map)
        os.makedirs(os.path.join(repo, "jobs", "x"))
        with open(os.path.join(repo, "jobs", "x", "run.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        findings = scan_path_consistency(repo)
        types = [f["type"] for f in findings]
        self.assertIn("DST_MISMATCH", types)

    def test_entry_with_cli_args_stripped(self):
        """V37.9.82 fix: entry='kb_dream.sh --map-sources' should split to kb_dream.sh."""
        from path_consistency_scanner import scan_path_consistency
        registry = textwrap.dedent("""\
            jobs:
              - id: cli_args_job
                scheduler: system
                entry: kb_dream.sh --map-sources
                enabled: true
        """)
        file_map = '    "kb_dream.sh|$HOME/kb_dream.sh"'
        repo = self._write_repo(registry, file_map)
        with open(os.path.join(repo, "kb_dream.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        findings = scan_path_consistency(repo)
        # kb_dream.sh IS in FILE_MAP, so MISSING_FILE_MAP should NOT fire
        types = [f["type"] for f in findings]
        self.assertNotIn("MISSING_FILE_MAP", types)

    def test_disabled_job_not_checked(self):
        from path_consistency_scanner import scan_path_consistency
        registry = textwrap.dedent("""\
            jobs:
              - id: disabled_job
                scheduler: system
                entry: missing.sh
                enabled: false
        """)
        file_map = '    "other.sh|$HOME/other.sh"'
        repo = self._write_repo(registry, file_map)
        with open(os.path.join(repo, "other.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        findings = scan_path_consistency(repo)
        types = [f["type"] for f in findings]
        # disabled_job not in enabled list, so its missing entry not flagged
        self.assertNotIn("MISSING_FILE_MAP", types)


class TestAllowedNonStandardDst(unittest.TestCase):
    """V37.9.82 ALLOWED_NON_STANDARD_DST 豁免机制守卫."""

    def test_exemption_dict_structure_complete(self):
        """每条豁免必须有 dst + reason 字段."""
        from path_consistency_scanner import ALLOWED_NON_STANDARD_DST
        for src, info in ALLOWED_NON_STANDARD_DST.items():
            self.assertIn("dst", info, f"{src!r}: missing dst")
            self.assertIn("reason", info, f"{src!r}: missing reason")
            self.assertTrue(info["dst"].startswith("$HOME/"),
                            f"{src!r}: dst must start with $HOME/")
            self.assertGreater(len(info["reason"]), 10,
                               f"{src!r}: reason too short (force documentation)")

    def test_exemption_blocks_legitimate_non_standard_deployments(self):
        """SOUL.md / status.json 等已知非常规部署必须在豁免清单中."""
        from path_consistency_scanner import ALLOWED_NON_STANDARD_DST
        critical_exemptions = [
            "SOUL.md",          # V30.4 PA 宪法 — workspace/SOUL.md
            "status.json",      # V30.4 三方共享 — .kb/status.json
            "ops_soul.md",      # V31 Ops Agent — .openclaw/SOUL.md
            "CLAUDE.md",        # V29 kb_inject — .kb/docs/
        ]
        for src in critical_exemptions:
            self.assertIn(src, ALLOWED_NON_STANDARD_DST,
                          f"{src!r} must be in ALLOWED_NON_STANDARD_DST")

    def test_exemption_dst_drift_detected(self):
        """豁免登记的 dst 与实际 FILE_MAP dst 不匹配 → EXEMPTION_DST_DRIFT."""
        # Mock ALLOWED_NON_STANDARD_DST locally
        import path_consistency_scanner as pcs
        original = pcs.ALLOWED_NON_STANDARD_DST.copy()
        try:
            pcs.ALLOWED_NON_STANDARD_DST = {
                "test_file.txt": {
                    "dst": "$HOME/.expected_loc/test_file.txt",
                    "reason": "test exemption with sufficient length",
                },
            }
            registry = "jobs: []\n"
            # FILE_MAP has different dst than exemption registers
            file_map = '    "test_file.txt|$HOME/.different_loc/test_file.txt"'
            repo = tempfile.mkdtemp(prefix="exempt_drift_test_")
            self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
            with open(os.path.join(repo, "jobs_registry.yaml"), "w") as f:
                f.write(registry)
            with open(os.path.join(repo, "auto_deploy.sh"), "w") as f:
                f.write(f"declare -a FILE_MAP=(\n{file_map}\n)\n")
            findings = pcs.scan_path_consistency(repo)
            types = [f["type"] for f in findings]
            self.assertIn("EXEMPTION_DST_DRIFT", types)
        finally:
            pcs.ALLOWED_NON_STANDARD_DST = original


class TestRealRepoBaseline(unittest.TestCase):
    """V37.9.82 实际 repo 0 violations 当前快照 (反向验证守卫真有效)."""

    def test_real_repo_zero_violations(self):
        """当前 repo 应该 0 violations (V37.9.82 立案时 baseline)."""
        from path_consistency_scanner import scan_path_consistency
        findings = scan_path_consistency(_HERE)
        if findings:
            from path_consistency_scanner import format_findings_human
            self.fail(
                f"V37.9.82 baseline regression: {len(findings)} violations\n"
                f"{format_findings_human(findings)}"
            )

    def test_cli_exit_0_on_clean_repo(self):
        """CLI 在 clean repo 上必须 exit 0 (FAIL-CLOSE 反向: clean → pass)."""
        result = subprocess.run(
            [sys.executable, os.path.join(_HERE, "path_consistency_scanner.py")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0,
                         f"clean repo should exit 0, got {result.returncode}\n"
                         f"stdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertIn("✅", result.stdout)

    def test_cli_json_output(self):
        """CLI --json 必须输出有效 JSON."""
        result = subprocess.run(
            [sys.executable, os.path.join(_HERE, "path_consistency_scanner.py"), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["invariant"], "INV-PATH-CONSISTENCY-001")
        self.assertEqual(data["violation_count"], 0)


class TestReverseSabotage(unittest.TestCase):
    """V37.9.82 反向 sabotage 验证守卫真有效."""

    def test_sabotage_unauthorized_dst_change_caught(self):
        """如果未来有人改 SOUL.md 的 dst 到非豁免位置, scanner 必须抓到."""
        import path_consistency_scanner as pcs
        registry = "jobs: []\n"
        # SOUL.md 豁免登记的 dst 是 $HOME/.openclaw/workspace/SOUL.md
        # 此处模拟有人改成 $HOME/somewhere_else/SOUL.md
        file_map_buggy = '    "SOUL.md|$HOME/somewhere_else/SOUL.md"'

        repo = tempfile.mkdtemp(prefix="sabotage_test_")
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        with open(os.path.join(repo, "jobs_registry.yaml"), "w") as f:
            f.write(registry)
        with open(os.path.join(repo, "auto_deploy.sh"), "w") as f:
            f.write(f"declare -a FILE_MAP=(\n{file_map_buggy}\n)\n")

        findings = pcs.scan_path_consistency(repo)
        # 应该触发 EXEMPTION_DST_DRIFT
        types = [f["type"] for f in findings]
        self.assertTrue(
            "EXEMPTION_DST_DRIFT" in types or "DST_MISMATCH" in types,
            f"sabotaged SOUL.md dst not caught: findings={findings}"
        )


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.82 source-level 字面量守卫 (防回退)."""

    def setUp(self):
        scanner_path = os.path.join(_HERE, "path_consistency_scanner.py")
        with open(scanner_path, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_v37_9_82_marker_in_scanner(self):
        self.assertIn("V37.9.82", self.content,
                      "scanner 必须含 V37.9.82 marker 注释 (溯源)")

    def test_blood_lesson_references(self):
        self.assertIn("V37.9.56-hotfix", self.content,
                      "scanner 必须引用 V37.9.56-hotfix lineage (Class B 同款血案)")
        self.assertIn("V37.9.66", self.content,
                      "scanner 必须引用 V37.9.66 path bug lineage")

    def test_fail_close_contract(self):
        self.assertIn("FAIL-CLOSE", self.content,
                      "scanner 必须显式声明 FAIL-CLOSE 契约")

    def test_path_convention_constants(self):
        self.assertIn('JOBS_SUBDIR_PREFIX = "jobs/"', self.content)
        self.assertIn('OPENCLAW_DEPLOY_PREFIX = "$HOME/.openclaw/"', self.content)
        self.assertIn('ROOT_DEPLOY_PREFIX = "$HOME/"', self.content)

    def test_inv_path_consistency_001_referenced(self):
        self.assertIn("INV-PATH-CONSISTENCY-001", self.content)


if __name__ == "__main__":
    unittest.main()

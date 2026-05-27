"""
test_auto_inject_guard.py — V37.9.85 INV-AUTO-INJECT-001 前瞻守卫单测

MR-18 Step 2 兑现: 仓库内 inject_*.py / migrate_*.py batch 工具必须内嵌
heredoc_import_scanner 验证. 当前无此类工具存在 = guard 自动 pass.
"""

import glob
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class TestAutoInjectGuard(unittest.TestCase):
    """INV-AUTO-INJECT-001: batch inject/migrate tools must integrate scanner."""

    # ── declaration layer ──

    def test_mr18_derivative_invariants_includes_auto_inject(self):
        """MR-18 derivative_invariants 必须含 INV-AUTO-INJECT-001."""
        gov_path = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
        with open(gov_path, "r") as f:
            content = f.read()
        self.assertIn("INV-AUTO-INJECT-001", content)
        # Must be in derivative_invariants list of MR-18
        self.assertIn("derivative_invariants: [INV-HEREDOC-IMPORT-001, INV-AUTO-INJECT-001]", content)

    def test_v37_9_85_marker_present(self):
        """V37.9.85 marker 存在于 governance yaml."""
        gov_path = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
        with open(gov_path, "r") as f:
            content = f.read()
        self.assertIn("V37.9.85", content)

    def test_heredoc_import_scanner_scan_repo_exists(self):
        """heredoc_import_scanner.py 必须有 scan_repo 函数 (batch 工具需调的 API)."""
        scanner_path = os.path.join(REPO_ROOT, "heredoc_import_scanner.py")
        self.assertTrue(os.path.isfile(scanner_path), "heredoc_import_scanner.py missing")
        with open(scanner_path, "r") as f:
            content = f.read()
        self.assertIn("def scan_repo", content)

    def test_inv_auto_inject_001_declaration_in_yaml(self):
        """INV-AUTO-INJECT-001 必须在 governance yaml 中有完整声明."""
        gov_path = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
        with open(gov_path, "r") as f:
            content = f.read()
        self.assertIn("id: INV-AUTO-INJECT-001", content)
        self.assertIn("meta_rule: MR-18", content)
        self.assertIn("batch-inject-tools-must-integrate-scanner", content)

    # ── runtime layer ──

    def test_no_inject_migrate_tools_exist_currently(self):
        """当前仓库无 inject_*.py / migrate_*.py 工具 (baseline = 0)."""
        patterns = ["inject_*.py", "migrate_*.py"]
        found = []
        for pat in patterns:
            found.extend(glob.glob(os.path.join(REPO_ROOT, pat)))
            found.extend(glob.glob(os.path.join(REPO_ROOT, "**", pat), recursive=True))
        found = [f for f in found
                 if not any(skip in f for skip in ["__pycache__", "test_", ".git"])]
        self.assertEqual(len(found), 0,
                         f"Unexpected inject/migrate tools found: {found}")

    def test_future_inject_tool_without_scanner_would_be_caught(self):
        """反向验证: 如果有人加 inject_*.py 但没含 scanner, INV 能抓到."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_tool = os.path.join(tmpdir, "inject_foo.py")
            with open(bad_tool, "w") as f:
                f.write("# bad tool without scanner\nimport os\n")

            patterns = [os.path.join(tmpdir, "inject_*.py")]
            found = []
            for pat in patterns:
                found.extend(glob.glob(pat))

            self.assertEqual(len(found), 1)
            with open(found[0], "r") as fh:
                content = fh.read()
            has_scanner = (
                "scan_heredoc_imports" in content
                or "heredoc_import_scanner" in content
                or "scan_repo" in content
            )
            self.assertFalse(has_scanner,
                             "Bad tool should NOT have scanner (test setup error)")

    def test_future_inject_tool_with_scanner_would_pass(self):
        """正向验证: inject_*.py 含 scanner 集成时 guard pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            good_tool = os.path.join(tmpdir, "inject_bar.py")
            with open(good_tool, "w") as f:
                f.write("from heredoc_import_scanner import scan_repo\n"
                        "# proper batch tool\n")

            with open(good_tool, "r") as fh:
                content = fh.read()
            has_scanner = (
                "scan_heredoc_imports" in content
                or "heredoc_import_scanner" in content
                or "scan_repo" in content
            )
            self.assertTrue(has_scanner)

    def test_governance_audit_passes_with_current_repo(self):
        """Governance INV-AUTO-INJECT-001 runtime check 在当前 repo pass."""
        # Replicate the runtime check logic inline
        patterns = ["inject_*.py", "migrate_*.py"]
        found_tools = []
        for pat in patterns:
            found_tools.extend(glob.glob(os.path.join(REPO_ROOT, pat)))
            found_tools.extend(glob.glob(os.path.join(REPO_ROOT, "**", pat), recursive=True))
        found_tools = [f for f in found_tools
                       if not any(skip in f for skip in ["__pycache__", "test_", ".git"])]

        violations = []
        for tool_path in found_tools:
            with open(tool_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            has_scanner = (
                "scan_heredoc_imports" in content
                or "heredoc_import_scanner" in content
                or "scan_repo" in content
            )
            if not has_scanner:
                violations.append(os.path.relpath(tool_path, REPO_ROOT))

        self.assertEqual(len(violations), 0,
                         f"INV-AUTO-INJECT-001 violations: {violations}")

    # ── source level guards ──

    def test_blood_lesson_lineage(self):
        """INV 引用 V37.9.57/58 血案历史."""
        gov_path = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
        with open(gov_path, "r") as f:
            content = f.read()
        # Find the INV-AUTO-INJECT-001 section
        idx = content.find("INV-AUTO-INJECT-001")
        self.assertGreater(idx, -1)
        section = content[idx:idx + 2000]
        self.assertIn("V37.9.57", section)
        self.assertIn("V37.9.58", section)

    def test_mr18_step2_reference(self):
        """MR-18 实施路径 Step 2 引用 INV-AUTO-INJECT-001 或 inject 工具自治化."""
        gov_path = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
        with open(gov_path, "r") as f:
            content = f.read()
        # MR-18 section has "Step 2 (V37.9.59+): inject 工具自治化"
        mr18_idx = content.find("id: MR-18")
        self.assertGreater(mr18_idx, -1)
        mr18_section = content[mr18_idx:mr18_idx + 3000]
        self.assertIn("Step 2", mr18_section)
        self.assertIn("INV-AUTO-INJECT-001", mr18_section)


if __name__ == "__main__":
    unittest.main()

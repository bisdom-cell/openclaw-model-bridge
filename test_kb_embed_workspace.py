#!/usr/bin/env python3
"""
test_kb_embed_workspace.py — V37.9.5 Route C INV-KB-COVERAGE-001 regression

Locks the workspace .md indexing semantics in kb_embed.scan_kb_files():
  - All workspace/*.md (top-level only) must be picked up
  - MEMORY.md keeps source_type="memory" (backward compat)
  - Other .md get source_type="workspace" (V37.9.5 new)
  - HEARTBEAT.md must be excluded (V37.8.16 INV-HB-001 — OpenClaw control file)
  - *.bak* / *~ must be excluded (backup/editor temp files)
  - venv/ subdirs not affected (glob */.md is top-level only)

Background
----------
V37.9.5 data audit found ~50KB of high-density PA-authored .md files in
~/.openclaw/workspace/ never reached text_index because kb_embed.py only
indexed MEMORY.md as a single special case. Examples uncovered:
  AGENTS.md / BOOTSTRAP.md / IDENTITY.md / SOUL.md / OPENCLAW_*_SUMMARY*

This test exists separately (not in YAML python_assert) because the YAML
exec() runs in a scope that cannot access enclosing-function locals like
`tmp_workspace` (V37.3 exec scope trap). Running as a real subprocess
unittest avoids that entirely.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestWorkspaceIndexing(unittest.TestCase):
    """V37.9.5: workspace/*.md indexing semantics."""

    def setUp(self):
        self.tmp_workspace = tempfile.TemporaryDirectory()
        self.workspace_path = self.tmp_workspace.name
        # Construct 5 file scenarios
        self.fixtures = {
            "MEMORY.md": "memory content",
            "AGENTS.md": "agents doc content",
            "HEARTBEAT.md": "control signal",
            "SOUL.md.bak_old": "backup content",
            "IDENTITY.md": "identity doc",
        }
        for name, content in self.fixtures.items():
            with open(os.path.join(self.workspace_path, name), "w") as f:
                f.write(content)

    def tearDown(self):
        self.tmp_workspace.cleanup()

    def _scan_with_mocked_workspace(self):
        """Run scan_kb_files() with workspace path mocked to our tmp dir."""
        import kb_embed
        orig_expanduser = os.path.expanduser
        ws_path = self.workspace_path  # capture in closure (test class scope OK)

        def mock_expanduser(p):
            if not isinstance(p, str):
                return orig_expanduser(p)
            if p.endswith("openclaw/workspace/MEMORY.md"):
                return os.path.join(ws_path, "MEMORY.md")
            if p.endswith("openclaw/workspace"):
                return ws_path
            return orig_expanduser(p)

        with mock.patch.object(os.path, "expanduser", mock_expanduser):
            result = kb_embed.scan_kb_files()
        # Filter to workspace-related entries only
        ws_files = [(os.path.basename(p), t) for p, t in result if ws_path in p]
        return ws_files

    def test_agents_md_picked_up(self):
        ws_files = self._scan_with_mocked_workspace()
        names = {n for n, _ in ws_files}
        self.assertIn("AGENTS.md", names,
                      f"AGENTS.md not indexed: {names}")

    def test_identity_md_picked_up(self):
        ws_files = self._scan_with_mocked_workspace()
        names = {n for n, _ in ws_files}
        self.assertIn("IDENTITY.md", names,
                      f"IDENTITY.md not indexed: {names}")

    def test_heartbeat_md_excluded(self):
        """V37.8.16 INV-HB-001: HEARTBEAT.md is OpenClaw control file."""
        ws_files = self._scan_with_mocked_workspace()
        names = {n for n, _ in ws_files}
        self.assertNotIn("HEARTBEAT.md", names,
                         f"HEARTBEAT.md must be excluded (control file): {names}")

    def test_bak_files_excluded(self):
        ws_files = self._scan_with_mocked_workspace()
        names = {n for n, _ in ws_files}
        bak_present = [n for n in names if ".bak" in n]
        self.assertEqual(bak_present, [],
                         f".bak files must be excluded: {bak_present}")

    def test_memory_md_source_type_preserved(self):
        """Backward compat: MEMORY.md keeps source_type='memory'."""
        ws_files = self._scan_with_mocked_workspace()
        memory_entries = [(n, t) for n, t in ws_files if n == "MEMORY.md"]
        self.assertEqual(len(memory_entries), 1, "MEMORY.md must appear once")
        self.assertEqual(memory_entries[0][1], "memory",
                         "MEMORY.md source_type must remain 'memory' (V37.9.5 backward compat)")

    def test_other_md_source_type_workspace(self):
        """V37.9.5 new: other workspace .md get source_type='workspace'."""
        ws_files = self._scan_with_mocked_workspace()
        agents_entries = [(n, t) for n, t in ws_files if n == "AGENTS.md"]
        self.assertEqual(len(agents_entries), 1)
        self.assertEqual(agents_entries[0][1], "workspace",
                         f"AGENTS.md source_type must be 'workspace': {agents_entries}")

    def test_no_workspace_dir_does_not_crash(self):
        """If ~/.openclaw/workspace doesn't exist, scan_kb_files runs OK."""
        import kb_embed
        with mock.patch.object(os.path, "isdir", return_value=False):
            with mock.patch.object(os.path, "isfile", return_value=False):
                # Should not raise
                result = kb_embed.scan_kb_files()
                self.assertIsInstance(result, list)


class TestExclusionList(unittest.TestCase):
    """Lock the exact exclusion semantics."""

    def test_exclusion_basenames_constant_present(self):
        """kb_embed.py must declare WORKSPACE_EXCLUDE_BASENAMES with HEARTBEAT.md."""
        with open(os.path.join(_HERE, "kb_embed.py"), "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("WORKSPACE_EXCLUDE_BASENAMES", source)
        # Both must be in the exclusion set
        self.assertIn('"MEMORY.md"', source)
        self.assertIn('"HEARTBEAT.md"', source)

    def test_bak_filter_present(self):
        with open(os.path.join(_HERE, "kb_embed.py"), "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn('".bak" in bn', source)

    def test_v37_8_16_blood_lesson_referenced(self):
        """V37.8.16 INV-HB-001 must be cited in the exclusion comment."""
        with open(os.path.join(_HERE, "kb_embed.py"), "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("V37.8.16", source)
        self.assertIn("INV-HB-001", source)


if __name__ == "__main__":
    unittest.main()

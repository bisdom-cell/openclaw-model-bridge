"""V37.9.58-hotfix2 — heredoc_import_scanner.py 单测 + 反向验证守卫.

测试三层契约 (MR-6 critical-invariants-need-depth):
  1. 单元层: AST extract/collect 纯函数行为
  2. 集成层: scan_heredoc_imports 端到端血案场景重现
  3. 反向验证层: sabotage 已修文件 → scanner 立即抓到 (证明守卫真有效)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 让 scanner 模块可 import
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

import heredoc_import_scanner as h  # noqa: E402


# ── 辅助 ───────────────────────────────────────────────────────────────

def _write_sh(content):
    """写一个临时 .sh 文件返回路径, 测试完需 unlink."""
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


# ── Tier 1: heredoc 提取层 ─────────────────────────────────────────────

class TestExtractHeredocs(unittest.TestCase):
    """heredoc 提取的边界行为."""

    def test_single_heredoc(self):
        tmp = _write_sh("echo hi\npython3 - << 'PYEOF'\nimport sys\nprint(sys.argv)\nPYEOF\necho done\n")
        try:
            heredocs = h.extract_heredocs(tmp)
            self.assertEqual(len(heredocs), 1)
            start_line, end_line, body = heredocs[0]
            self.assertEqual(start_line, 3)  # body 第一行
            self.assertIn("import sys", body[0])
        finally:
            os.unlink(tmp)

    def test_multiple_heredocs(self):
        tmp = _write_sh(
            "python3 - << 'PYEOF'\nimport os\nprint(os.getcwd())\nPYEOF\n"
            "echo mid\n"
            "python3 - << 'PYEOF'\nimport sys\nprint(sys.version)\nPYEOF\n"
        )
        try:
            heredocs = h.extract_heredocs(tmp)
            self.assertEqual(len(heredocs), 2)
        finally:
            os.unlink(tmp)

    def test_unclosed_heredoc_silently_skipped(self):
        """未闭合的 heredoc (语法错误) 不算 heredoc, 不报 violation."""
        tmp = _write_sh("python3 - << 'PYEOF'\nimport sys\nprint(sys.argv)\n")  # 没 PYEOF
        try:
            heredocs = h.extract_heredocs(tmp)
            self.assertEqual(len(heredocs), 0)
        finally:
            os.unlink(tmp)

    def test_non_pyeof_heredoc_ignored(self):
        """`<< 'EOF'` 不是 PYEOF, 不应被识别为 Python heredoc."""
        tmp = _write_sh("cat << 'EOF'\nimport sys  # 这是 cat 的 stdin 不是 Python\nEOF\n")
        try:
            heredocs = h.extract_heredocs(tmp)
            self.assertEqual(len(heredocs), 0)
        finally:
            os.unlink(tmp)


# ── Tier 2: AST 收集函数 ───────────────────────────────────────────────

class TestCollectImportedNames(unittest.TestCase):
    def test_simple_import(self):
        import ast
        tree = ast.parse("import os\nimport sys\nimport json\n")
        self.assertEqual(h.collect_imported_names(tree), {"os", "sys", "json"})

    def test_import_with_alias(self):
        import ast
        tree = ast.parse("import os as O\nfrom collections import deque as DQ\n")
        names = h.collect_imported_names(tree)
        self.assertIn("O", names)
        self.assertIn("DQ", names)
        self.assertNotIn("os", names)  # alias 后原名不在
        self.assertNotIn("collections", names)

    def test_from_import(self):
        import ast
        tree = ast.parse("from pathlib import Path\nfrom typing import Dict, List\n")
        names = h.collect_imported_names(tree)
        self.assertIn("Path", names)
        self.assertIn("Dict", names)
        self.assertIn("List", names)

    def test_import_with_submodule(self):
        import ast
        tree = ast.parse("import os.path\n")
        names = h.collect_imported_names(tree)
        # `import os.path` 实际 binds `os` (访问 os.path.X)
        self.assertIn("os", names)


class TestCollectLocallyDefinedNames(unittest.TestCase):
    def test_function_def(self):
        import ast
        tree = ast.parse("def foo(x, y):\n    return x + y\n")
        names = h.collect_locally_defined_names(tree)
        self.assertIn("foo", names)
        self.assertIn("x", names)
        self.assertIn("y", names)

    def test_assignment_targets(self):
        import ast
        tree = ast.parse("a = 1\nb, c = 2, 3\n*d, e = [1, 2, 3]\n")
        names = h.collect_locally_defined_names(tree)
        for n in ["a", "b", "c", "d", "e"]:
            self.assertIn(n, names)

    def test_for_loop_target(self):
        import ast
        tree = ast.parse("for item in items:\n    pass\n")
        names = h.collect_locally_defined_names(tree)
        self.assertIn("item", names)

    def test_with_as(self):
        import ast
        tree = ast.parse("with open('x') as f:\n    pass\n")
        names = h.collect_locally_defined_names(tree)
        self.assertIn("f", names)

    def test_except_handler_name(self):
        import ast
        tree = ast.parse("try:\n    pass\nexcept Exception as e:\n    pass\n")
        names = h.collect_locally_defined_names(tree)
        self.assertIn("e", names)


class TestCollectReferencedNames(unittest.TestCase):
    def test_simple_reference(self):
        import ast
        tree = ast.parse("import os\nprint(os.environ)\n")
        names = h.collect_referenced_names(tree)
        self.assertIn("os", names)
        # print 也被引用 (但会被 builtins 豁免)
        self.assertIn("print", names)

    def test_attribute_root(self):
        import ast
        tree = ast.parse("foo.bar.baz()\n")
        names = h.collect_referenced_names(tree)
        self.assertIn("foo", names)
        self.assertNotIn("bar", names)  # bar 是 attribute 不是 root


# ── Tier 3: scan_heredoc_imports 集成 ─────────────────────────────────

class TestScanHeredocImports(unittest.TestCase):
    """端到端血案场景重现 + 正向场景."""

    def test_v37_9_50_hotfix_scenario(self):
        """V37.9.50-hotfix 血案: heredoc 调 os.environ 但顶部 import 缺 os."""
        tmp = _write_sh(
            "python3 - << 'PYEOF'\n"
            "import sys, json, re\n"  # 缺 os
            "prompt += os.environ.get('X')\n"
            "PYEOF\n"
        )
        try:
            violations = h.scan_heredoc_imports(tmp)
            self.assertEqual(len(violations), 1)
            start_line, missing, preview = violations[0]
            self.assertIn("os", missing,
                "V37.9.50-hotfix 血案 scanner 必须抓到 missing os")
        finally:
            os.unlink(tmp)

    def test_v37_9_58_hotfix_scenario_multiple_jobs(self):
        """V37.9.58-hotfix 血案: 8 jobs 同款 NameError, scanner 必须每个都抓."""
        for label, content in [
            ("hn_like", "python3 - << 'PYEOF'\nimport sys, json, re\nprompt += os.environ.get('HG_LEVEL_4_TEXT', '')\nPYEOF\n"),
            ("finance_like", "python3 - << 'PYEOF'\nimport sys, json, re\nprompt += os.environ.get('HG_GUARD_TEXT', '')\nPYEOF\n"),
        ]:
            tmp = _write_sh(content)
            try:
                violations = h.scan_heredoc_imports(tmp)
                self.assertEqual(len(violations), 1,
                    f"V37.9.58-hotfix 血案 {label} 必须报 1 violation")
                _, missing, _ = violations[0]
                self.assertIn("os", missing)
            finally:
                os.unlink(tmp)

    def test_correct_heredoc_no_violation(self):
        """完整 import 的 heredoc 不报 violation (V37.9.58-hotfix 修复后状态)."""
        tmp = _write_sh(
            "python3 - << 'PYEOF'\n"
            "import sys, json, os\n"
            "prompt = os.environ.get('X')\n"
            "PYEOF\n"
        )
        try:
            violations = h.scan_heredoc_imports(tmp)
            self.assertEqual(violations, [])
        finally:
            os.unlink(tmp)

    def test_builtins_not_flagged(self):
        """print/len/str/Exception 等内置不算 missing import."""
        tmp = _write_sh(
            "python3 - << 'PYEOF'\n"
            "import sys\n"
            "try:\n"
            "    print(len(sys.argv))\n"
            "    raise ValueError('x')\n"
            "except Exception as e:\n"
            "    print(str(e))\n"
            "PYEOF\n"
        )
        try:
            violations = h.scan_heredoc_imports(tmp)
            self.assertEqual(violations, [],
                "print/len/str/ValueError/Exception 都是 builtin 不应报 missing")
        finally:
            os.unlink(tmp)

    def test_dynamic_import_dunder_builtin_exempt(self):
        """__import__ 是 builtin (dynamic import), 不应被误报."""
        tmp = _write_sh(
            "python3 - << 'PYEOF'\n"
            "mod = __import__('json')\n"
            "PYEOF\n"
        )
        try:
            violations = h.scan_heredoc_imports(tmp)
            self.assertEqual(violations, [],
                "__import__ 是 dynamic import builtin 不应报 missing")
        finally:
            os.unlink(tmp)

    def test_locally_defined_name_not_flagged(self):
        """heredoc 内 def / 赋值定义的名字不算 missing."""
        tmp = _write_sh(
            "python3 - << 'PYEOF'\n"
            "import sys\n"
            "def parse_args(line):\n"
            "    return line.strip()\n"
            "result = parse_args(sys.argv[1])\n"
            "PYEOF\n"
        )
        try:
            violations = h.scan_heredoc_imports(tmp)
            self.assertEqual(violations, [],
                "parse_args 是本地定义不应报 missing")
        finally:
            os.unlink(tmp)

    def test_ast_parse_error_silently_skipped(self):
        """AST 解析失败的 heredoc (非 Python heredoc 但用 PYEOF 边界) silently skip,
        不算 violation (避免 false positive)."""
        tmp = _write_sh(
            "python3 - << 'PYEOF'\n"
            "这不是 Python 代码: { invalid syntax (\n"
            "PYEOF\n"
        )
        try:
            violations = h.scan_heredoc_imports(tmp)
            self.assertEqual(violations, [],
                "AST 解析失败应 silently skip 不算 violation")
        finally:
            os.unlink(tmp)


# ── Tier 4: 整 repo 集成 — V37.9.58-hotfix2 状态守卫 ───────────────────

class TestRepoScanIntegration(unittest.TestCase):
    """V37.9.58-hotfix2 整 repo 实际状态 + 反向验证."""

    def test_v37_9_58_hotfix2_state_zero_violations(self):
        """V37.9.58-hotfix2 部署后整 repo 应 0 violations.
        覆盖 8 ALIGNED jobs (V37.9.58-hotfix) + 2 LEVEL_2 jobs (V37.9.58-hotfix2 修).
        """
        result = subprocess.run(
            [sys.executable,
             os.path.join(REPO_ROOT, "heredoc_import_scanner.py"),
             "--scan-all", "--root", REPO_ROOT],
            capture_output=True, text=True, timeout=60
        )
        self.assertEqual(
            result.returncode, 0,
            f"V37.9.58-hotfix2 状态应 0 violations:\n"
            f"stdout=\n{result.stdout}\n\nstderr=\n{result.stderr}"
        )
        self.assertIn("0 violations", result.stdout)

    def test_v37_9_58_hotfix2_scanner_exists_at_repo_root(self):
        """heredoc_import_scanner.py 必须在 repo root 便于 governance 引用."""
        scanner_path = os.path.join(REPO_ROOT, "heredoc_import_scanner.py")
        self.assertTrue(os.path.exists(scanner_path),
            "V37.9.58-hotfix2: scanner 必须在 repo root")

    def test_scanner_catches_sabotaged_import_immediately(self):
        """反向验证: sabotage 已修文件移除 import → scanner 立即抓到."""
        target = os.path.join(REPO_ROOT, "jobs", "hn_watcher", "run_hn_fixed.sh")
        with open(target, "r", encoding="utf-8") as f:
            original = f.read()
        # 找 V37.9.58-hotfix 修的 import 行 (line ~283)
        # `import sys, json, re, os  # V37.9.58-hotfix:` → `import sys, json, re  # SABOTAGED`
        sabotaged = re.sub(
            r"import sys, json, re, os(\s+#\s*V37\.9\.58-hotfix:[^\n]*)?",
            "import sys, json, re  # SABOTAGED V37.9.58-hotfix2 反向验证测试",
            original, count=1
        )
        if sabotaged == original:
            self.skipTest("无法定位 sabotage 目标 (run_hn_fixed.sh import 行未匹配 V37.9.58-hotfix 模式)")
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(sabotaged)
            result = subprocess.run(
                [sys.executable,
                 os.path.join(REPO_ROOT, "heredoc_import_scanner.py"),
                 "--scan-all", "--root", REPO_ROOT],
                capture_output=True, text=True, timeout=60
            )
            self.assertNotEqual(result.returncode, 0,
                "Scanner 应抓到 sabotage 后的 missing os")
            self.assertIn("'os'", result.stdout,
                "Scanner 输出应含 missing: ['os']")
            self.assertIn("run_hn_fixed.sh", result.stdout,
                "Scanner 输出应指明问题文件")
        finally:
            # 还原
            with open(target, "w", encoding="utf-8") as f:
                f.write(original)


# ── Tier 5: CLI 行为 ──────────────────────────────────────────────────

class TestCliBehavior(unittest.TestCase):
    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "heredoc_import_scanner.py"), "--help"],
            capture_output=True, text=True, timeout=10
        )
        # argparse --help exits 0
        self.assertEqual(result.returncode, 0)
        self.assertIn("INV-HEREDOC-IMPORT-001", result.stdout + result.stderr)

    def test_cli_single_file_clean(self):
        """--file mode 对干净文件 exit 0."""
        tmp = _write_sh("python3 - << 'PYEOF'\nimport os\nprint(os.getcwd())\nPYEOF\n")
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(REPO_ROOT, "heredoc_import_scanner.py"),
                 "--file", tmp],
                capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("0 violations", result.stdout)
        finally:
            os.unlink(tmp)

    def test_cli_single_file_with_violation(self):
        """--file mode 对 broken 文件 exit 1."""
        tmp = _write_sh(
            "python3 - << 'PYEOF'\n"
            "import sys\n"
            "prompt = os.environ.get('X')\n"  # 缺 os
            "PYEOF\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(REPO_ROOT, "heredoc_import_scanner.py"),
                 "--file", tmp],
                capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("os", result.stdout)
        finally:
            os.unlink(tmp)


# ── Tier 6: 源码守卫 ───────────────────────────────────────────────────

class TestSourceLevelGuards(unittest.TestCase):
    """守卫 scanner 源码本身的关键契约."""

    def setUp(self):
        scanner_path = os.path.join(REPO_ROOT, "heredoc_import_scanner.py")
        with open(scanner_path, "r", encoding="utf-8") as f:
            self.src = f.read()

    def test_v37_9_58_hotfix2_marker_present(self):
        """scanner 文件头必须含 V37.9.58-hotfix2 marker (历史追溯)."""
        self.assertIn("V37.9.58-hotfix2", self.src)

    def test_v37_9_50_hotfix_lineage_documented(self):
        """scanner 必须引用 V37.9.50-hotfix 血案历史 (8 天前同款 bug)."""
        self.assertIn("V37.9.50-hotfix", self.src)

    def test_mr_18_referenced(self):
        """scanner 必须引用 MR-18 元规则 (auto-batch-injection 预防层)."""
        self.assertIn("MR-18", self.src)

    def test_fail_close_not_fail_open(self):
        """scanner 必须 FAIL-CLOSE (找到 violation → exit 1), 不能 silently pass."""
        self.assertIn("sys.exit(1)", self.src,
            "scanner 必须有显式 exit 1 路径 (FAIL-CLOSE)")
        self.assertIn("FAIL-CLOSE", self.src,
            "scanner docstring 必须声明 FAIL-CLOSE 契约 (反 MR-4)")

    def test_inv_heredoc_import_001_referenced(self):
        """scanner CLI 输出必须引用 INV-HEREDOC-IMPORT-001 (governance 关联)."""
        self.assertIn("INV-HEREDOC-IMPORT-001", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

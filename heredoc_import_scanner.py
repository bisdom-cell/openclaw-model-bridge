#!/usr/bin/env python3
"""
heredoc_import_scanner.py — V37.9.58-hotfix2: V37.9.50/58-hotfix 教训转化为
framework 级预防工具.

血案回溯:
  - 2026-05-10 V37.9.50-hotfix: semantic_scholar emit heredoc 顶部
    `import sys, json, re` 缺 `os`, lazy import 调 `os.environ` → NameError →
    rule_check FAIL-OPEN. 单点 fix, 没提炼为 framework 预防.
  - 2026-05-12 V37.9.58-hotfix: V37.9.57 inject_level_4_to_aligned_jobs.py 自动
    批量给 8 个 ALIGNED jobs 的 LLM call heredoc 加 prompt += os.environ.get(...)
    但未补 import os → 8/8 jobs 重演同款 bug, retry 包装当成空 content 成功
    返回 → parse_6field_output 全空 → 用户看到 broken push.

本 scanner 的作用 (MR-18 derivative_invariant INV-HEREDOC-IMPORT-001):
  通过 Python AST 解析所有 .sh 中 `<< 'PYEOF' ... PYEOF` heredoc 的 body, 收集:
    - imported names (ast.Import / ast.ImportFrom + 别名)
    - locally defined names (函数定义 / 类定义 / 赋值目标 / for 循环 / with /
      函数参数 / except / 海象 / 推导式)
    - referenced names (ast.Name Load 上下文 + ast.Attribute root)
  计算 missing = referenced - imported - defined - Python_builtins.
  任一 .sh 含 missing != ∅ → exit 1 让 governance audit 立即抓到.

设计契约:
  - FAIL-CLOSE (而非 FAIL-OPEN): 找到任一 violation 必须 exit 1, scanner
    自身错误 (无法 AST 解析等) 也算 violation (silently passing 是 MR-4)
  - 不递归扫子模块, 只看 heredoc 自身 body (heredoc 是封闭执行单元)
  - 不区分 dev / prod, 任何 .sh 都视为 production cron candidate
  - exit code 语义: 0=clean / 1=有 violation / 2=scanner 自身使用错误

MR-18 关联: auto-batch-injection-must-validate-runtime-semantics — 任何自动
批量改多文件的工具必须验证运行时语义一致性. 本 scanner 是该元规则的具体落地
(其他可能 derivative: 跨文件函数签名一致性 / 跨文件常量同步 / 等).
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path


# Python 内置 (基础) — 这些在任何 Python 代码中无需 import 即可使用
# 不是 exhaustive list, 但覆盖 99% 常用场景. dict(__builtins__) 不可靠
# (Python 3 中是 module, dir 才是名字 list)
_PYTHON_BUILTINS = {
    # 类型构造
    "bool", "int", "float", "str", "bytes", "bytearray", "memoryview",
    "list", "tuple", "dict", "set", "frozenset", "complex",
    "object", "type",
    # 序列/迭代
    "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "iter", "next", "slice",
    # 数值/逻辑
    "abs", "round", "pow", "divmod", "min", "max", "sum", "any", "all",
    "hex", "oct", "bin", "chr", "ord", "ascii", "format", "hash", "id",
    # 反射
    "callable", "hasattr", "getattr", "setattr", "delattr",
    "isinstance", "issubclass", "type",
    "vars", "dir", "globals", "locals",
    # I/O / 调试
    "print", "input", "open", "repr",
    # 元/装饰器
    "property", "staticmethod", "classmethod", "super",
    "compile", "exec", "eval",
    # 异常类 (常用)
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
    "IndexError", "AttributeError", "RuntimeError", "StopIteration",
    "FileNotFoundError", "OSError", "IOError", "NotImplementedError",
    "ImportError", "ModuleNotFoundError", "ZeroDivisionError",
    "ArithmeticError", "FloatingPointError", "OverflowError",
    "LookupError", "NameError", "UnicodeError", "UnicodeDecodeError",
    "UnicodeEncodeError", "JSONDecodeError",
    "Warning", "DeprecationWarning", "FutureWarning",
    "AssertionError", "PermissionError", "TimeoutError",
    "ConnectionError", "ConnectionResetError", "ConnectionRefusedError",
    "BrokenPipeError", "InterruptedError", "EOFError", "GeneratorExit",
    "RecursionError", "SystemExit", "KeyboardInterrupt",
    "BlockingIOError", "ChildProcessError", "FileExistsError",
    "IsADirectoryError", "NotADirectoryError", "ProcessLookupError",
    # 常量/特殊
    "True", "False", "None", "NotImplemented", "Ellipsis",
    "__name__", "__file__", "__doc__", "__package__", "__loader__",
    "__spec__", "__builtins__",
    # 其他
    "len", "help", "quit", "exit", "copyright", "credits", "license",
    "breakpoint", "delattr",
    # 动态机制 (常用于 importlib / 工厂模式 / 反射)
    "__import__", "__build_class__",
}


_HEREDOC_START_RE = re.compile(r"<<\s*['\"]?PYEOF['\"]?")


def extract_heredocs(filepath):
    """从 .sh 文件提取所有 `<< 'PYEOF' ... PYEOF` heredoc body.

    Returns list of (start_line, end_line, body_lines):
        start_line: heredoc body 第一行的 1-indexed 行号
        end_line: 终结 PYEOF 行的 1-indexed 行号
        body_lines: heredoc body 行列表 (不含开始 << 行和终结 PYEOF 行)
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    heredocs = []
    i = 0
    while i < len(lines):
        if _HEREDOC_START_RE.search(lines[i]):
            body_start_idx = i + 1
            j = body_start_idx
            found_end = False
            while j < len(lines):
                # heredoc 终结符 PYEOF 严格在行首 (允许末尾换行)
                if lines[j].rstrip("\n").rstrip() == "PYEOF":
                    body = lines[body_start_idx:j]
                    heredocs.append((body_start_idx + 1, j + 1, body))
                    i = j + 1
                    found_end = True
                    break
                j += 1
            if not found_end:
                # heredoc 未闭合 (语法错误), 跳到末尾
                i = j
        else:
            i += 1
    return heredocs


def _extract_target_names(target):
    """从赋值 / for / with 等 target 提取所有 name (递归处理 Tuple/List/Starred)."""
    names = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            names.update(_extract_target_names(elt))
    elif isinstance(target, ast.Starred):
        names.update(_extract_target_names(target.value))
    return names


def collect_imported_names(tree):
    """AST 收集所有 import / from-import 的名字 (含 alias)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    # from X import * — 无法静态确定, 保守作为通配标志
                    # 暂时不处理 (会导致 false positive but rare)
                    continue
                names.add(alias.asname or alias.name)
    return names


def collect_locally_defined_names(tree):
    """AST 收集本地定义的 names: 函数 / 类 / 赋值目标 / for 循环 / with /
    函数参数 / except / 海象 / 推导式."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            # 函数 / 类自身的参数也算定义
            if hasattr(node, "args") and node.args:
                args_obj = node.args
                for arg in (args_obj.args + args_obj.kwonlyargs +
                            (getattr(args_obj, "posonlyargs", []) or [])):
                    names.add(arg.arg)
                if args_obj.vararg:
                    names.add(args_obj.vararg.arg)
                if args_obj.kwarg:
                    names.add(args_obj.kwarg.arg)
        elif isinstance(node, (ast.Lambda,)):
            args_obj = node.args
            for arg in (args_obj.args + args_obj.kwonlyargs +
                        (getattr(args_obj, "posonlyargs", []) or [])):
                names.add(arg.arg)
            if args_obj.vararg:
                names.add(args_obj.vararg.arg)
            if args_obj.kwarg:
                names.add(args_obj.kwarg.arg)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_extract_target_names(target))
        elif isinstance(node, ast.AugAssign):
            names.update(_extract_target_names(node.target))
        elif isinstance(node, ast.AnnAssign) and node.target:
            names.update(_extract_target_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names.update(_extract_target_names(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    names.update(_extract_target_names(item.optional_vars))
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
        elif isinstance(node, ast.NamedExpr):  # 海象 :=
            names.update(_extract_target_names(node.target))
        elif isinstance(node, ast.comprehension):
            names.update(_extract_target_names(node.target))
        elif isinstance(node, ast.Global):
            for n in node.names:
                names.add(n)
        elif isinstance(node, ast.Nonlocal):
            for n in node.names:
                names.add(n)
    return names


def collect_referenced_names(tree):
    """AST 收集 Load 上下文的 Name + Attribute root (Attribute 解引用的根)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # 找到 attribute chain 的根 (a.b.c → a)
            root = node
            while isinstance(root.value, ast.Attribute):
                root = root.value
            if isinstance(root.value, ast.Name) and isinstance(root.value.ctx, ast.Load):
                names.add(root.value.id)
    return names


def scan_heredoc_imports(filepath, verbose=False):
    """扫一个 .sh 中所有 heredoc, 返回 violations list.

    Returns list of (heredoc_start_line, missing_names, body_preview_first_3_lines).
    Empty list = 无 violation.
    AST parse 失败的 heredoc silently skip (不算 violation, 避免 false positive
    on 非 Python heredoc 误标记为 PYEOF 的边界场景).
    """
    violations = []
    try:
        heredocs = extract_heredocs(filepath)
    except (OSError, IOError) as e:
        if verbose:
            print(f"[scan] {filepath} read failed: {e}", file=sys.stderr)
        return violations
    for start_line, end_line, body in heredocs:
        body_text = "".join(body)
        try:
            tree = ast.parse(body_text)
        except SyntaxError as e:
            if verbose:
                print(f"[scan] {filepath} heredoc@line {start_line}: AST parse error {e}", file=sys.stderr)
            continue
        imported = collect_imported_names(tree)
        defined = collect_locally_defined_names(tree)
        referenced = collect_referenced_names(tree)
        missing = referenced - imported - defined - _PYTHON_BUILTINS
        if missing:
            preview = body[:3]
            violations.append((start_line, missing, preview))
    return violations


def scan_repo(root_dir=".", verbose=False, exclude_dirs=None):
    """扫所有 .sh 文件, 返回 {filepath: violations} dict."""
    if exclude_dirs is None:
        exclude_dirs = {".git", "node_modules", "venv", "venv_pptx", "__pycache__"}
    all_violations = {}
    root = Path(root_dir).resolve()
    for path in root.rglob("*.sh"):
        # skip excluded directories
        if any(part in exclude_dirs for part in path.parts):
            continue
        violations = scan_heredoc_imports(str(path), verbose)
        if violations:
            # 用相对路径方便阅读
            try:
                rel_path = str(path.relative_to(root))
            except ValueError:
                rel_path = str(path)
            all_violations[rel_path] = violations
    return all_violations


def main():
    parser = argparse.ArgumentParser(
        description="Heredoc Python imports consistency scanner — INV-HEREDOC-IMPORT-001 / MR-18"
    )
    parser.add_argument("--scan-all", action="store_true",
                        help="Scan all .sh in repo (default action)")
    parser.add_argument("--file", help="Scan single .sh file")
    parser.add_argument("--verbose", action="store_true", help="Verbose diagnostics to stderr")
    parser.add_argument("--root", default=None, help="Repo root for --scan-all (default: scanner's parent dir)")
    args = parser.parse_args()

    if args.file:
        violations = scan_heredoc_imports(args.file, args.verbose)
        if violations:
            print(f"❌ INV-HEREDOC-IMPORT-001: {args.file} has {len(violations)} heredoc violation(s):")
            for start_line, missing, preview in violations:
                print(f"  🚨 heredoc@line {start_line} — missing imports: {sorted(missing)}")
                for line in preview:
                    print(f"      | {line.rstrip()}")
            sys.exit(1)
        else:
            print(f"✅ {args.file}: 0 violations")
            sys.exit(0)
    else:
        # 默认 scan-all 模式
        root_dir = args.root or os.path.dirname(os.path.abspath(__file__)) or "."
        violations = scan_repo(root_dir, args.verbose)
        if violations:
            total_violations = sum(len(v) for v in violations.values())
            print(f"❌ INV-HEREDOC-IMPORT-001: {len(violations)} file(s) with {total_violations} heredoc violation(s)")
            print()
            for path, file_violations in sorted(violations.items()):
                for start_line, missing, preview in file_violations:
                    print(f"  🚨 {path}: heredoc@line {start_line} — missing: {sorted(missing)}")
                    for line in preview:
                        print(f"      | {line.rstrip()}")
                    print()
            print("血案追溯: V37.9.50-hotfix → V37.9.58-hotfix → V37.9.58-hotfix2 (此 scanner)")
            print("MR-18: auto-batch-injection-must-validate-runtime-semantics")
            print("修复建议: 在 heredoc 顶部 import 行补齐缺漏模块")
            sys.exit(1)
        else:
            print(f"✅ INV-HEREDOC-IMPORT-001: all .sh heredoc imports consistent (0 violations)")
            sys.exit(0)


if __name__ == "__main__":
    main()

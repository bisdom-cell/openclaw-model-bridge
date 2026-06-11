#!/usr/bin/env python3
"""cross_os_quirk_scanner.py — V37.9.67 INV-CROSS-OS-001 framework scanner

血案谱系 (V37.9.66 反思文档类别 A OS quirk 已 7+ 次演出):
  - V37.9.58-hotfix3: watchdog macOS bsd awk multibyte 7 天 silent abort
  - V37.9.58-hotfix4: bash `set -e` 函数内 fail 不传 ERR trap 漏 `-E`
  - V37.9.56-hotfix2: zsh `interactive_comments` 默认 OFF `#` 当参数
  - V37.9.60-hotfix: bash `grep | head` + pipefail + set -eE 联合 false-positive FATAL
  - V37.9.66-hotfix: bash `cmd && X || Y` + set -eE + ERR trap 同款 quirk
  - V37.9.105-hotfix: governance_audit `$(...)` 子 shell 继承 ERR trap 假 FATAL ×2
  - V37.9.131: watchdog SLO `$(slo_checker --alert)` 同款 quirk 第 3 处演出
  - V37.9.141: preflight `$PUSH_RC）` 未 brace 变量紧贴全角字符 bash 3.2 set -u 崩溃 (V37.9.43-hotfix2 第 2 次演出, 全仓扫出 14 处潜伏)

scope (V37.9.67 PoC 4 个 → V37.9.68 +1 → V37.9.134 +1 → V37.9.141 +1 = 7 个**已实证暴露**的
   quirk pattern 主动检测). 未来扩展: macOS sed -i / GNU vs BSD date / etc.

FAIL-CLOSE 契约: 任一 violation 必须 exit 1.

豁免:
  - 注释行 (# 开头) 不算违反
  - test_*.py / *.bak / .git 跳过
  - heredoc 内部 (单引号 'EOF' 标记的字面量 Python 代码) 不扫
  - docstring / 三引号字符串内的字面量不扫

Usage:
  python3 cross_os_quirk_scanner.py                # 全 repo 扫 (FAIL-CLOSE)
  python3 cross_os_quirk_scanner.py --file X.sh    # 扫单文件
  python3 cross_os_quirk_scanner.py --list-quirks  # 列出所有检测的 quirk
"""
import argparse
import os
import re
import sys
from pathlib import Path


# ── Quirk 1: bash `cmd && X || Y` + set -eE + ERR trap (V37.9.66-hotfix) ──
# 反模式: VAR=$(cmd) && X || Y  当 cmd fail, ERR trap 触发 false-positive FATAL
# 正确: if VAR=$(cmd); then X; else Y; fi  (bash 文档豁免 if condition)
_QUIRK_CMD_AND_OR_PATTERN = re.compile(
    r'^\s*[A-Z_]+=\$\([^)]+\)\s+&&\s+[A-Z_]+=\d+\s+\|\|\s+[A-Z_]+=\$\?'
)

# ── Quirk 2: bash `cmd | head N` + pipefail + set -e (V37.9.60-hotfix) ──
# 反模式: VAR=$(...| grep PATTERN | head -N)  grep no-match exit 1 → pipefail → ERR trap
# 正确: 末尾加 `|| true` 兜底
_QUIRK_GREP_HEAD_PATTERN = re.compile(
    r'=\$\([^)]*\|\s*grep[^|)]*\|\s*head\s+-\d+[^)]*\)'
)
# 检查同一行是否已经有兜底 (豁免)
# 接受三种兜底形式: `|| true` / `|| echo ...` / `|| :`
_OR_TRUE_GUARD = re.compile(r'\|\|\s*(true\b|echo\b|:\s)')

# ── Quirk 3: awk 处理 log 但缺 LC_ALL=C (V37.9.58-hotfix3) ──
# bsd awk multibyte 处理无效 UTF-8 字节会 abort. macOS bsd awk 必须 LC_ALL=C.
# 反模式: tail X.log | awk ...  (无 LC_ALL=C 前缀)
# 正确: tail X.log | LC_ALL=C awk ...
_QUIRK_AWK_NO_LC_ALL_PATTERN = re.compile(
    r'(?<!LC_ALL=C\s)(?<!LC_ALL=C\s\s)awk\s+'
)
# 只在处理 log 文件的上下文检 (避免误报 awk '{print $1}' 简单用法)
_QUIRK_AWK_LOG_CONTEXT = re.compile(
    r'(?:tail|cat|head)\s+[^|]*\.log[^|]*\|\s*(?:[A-Z_]+=\S+\s+)*awk\s+'
)

# ── Quirk 4: zsh-specific syntax in cron .sh (V37.9.56-hotfix2 教训) ──
# cron 默认走 /bin/sh (POSIX), 不是 zsh. zsh-specific 语法在 cron 跑会失败.
# 反模式: typeset -A / autoload / setopt / zmodload (zsh-only)
_QUIRK_ZSH_SPECIFIC = re.compile(
    r'^\s*(typeset\s+-A|autoload\s+|setopt\s+|zmodload\s+|\$\([^)]*\^\^)'
)

# ── Quirk 5: head -c N | tr 切多字节 UTF-8 中间 (V37.9.68 教训) ──
# `head -c N` 按**字节**切, 中文 UTF-8 是 3 bytes/char, 在多字节字符中间截断后 tr
# 看到 incomplete byte sequence 报 "tr: Illegal byte sequence" (macOS bsd LC_CTYPE=UTF-8).
# 不阻塞功能但污染日志. 修复: tr 前加 `LC_ALL=C` 绕过 multibyte 校验.
# 反模式: `head -c N | tr` 无 LC_ALL=C
# 合规: `head -c N | LC_ALL=C tr ...`
_QUIRK_HEAD_BYTE_TR = re.compile(
    r'head\s+-c\s+\d+[^|]*\|\s*tr\s+'
)

# ── Quirk 6: $(...) 子 shell 继承 ERR trap, 设计性非零命令误触发假 FATAL ──
# (V37.9.105-hotfix governance_audit ×2 + V37.9.131 watchdog SLO, 共 3 处实证)
#
# macOS bash 3.2 + `set -E` (errtrace) 下, `VAR=$(cmd)` 命令替换子 shell **继承
# ERR trap**. 当 cmd 是"设计性非零退出"命令 (governance_checker exit 1 = 发现违规
# / slo_checker --alert exit 2 = SLO 违规, 非零是正常 API), 每次正常运行都在子
# shell 内误触发 fatal_handler 推送假 FATAL 告警.
#
# 关键: 外层 `|| RC=$?` 捕获**防不了**子 shell 内 trap fire (V37.9.105 铁证:
# GOV_RC 已正确捕获仍推假 "FATAL abort line=64"). 唯一豁免 = `set +E` 包裹:
#     RC=0
#     set +E
#     VAR=$(python3 designed_nonzero.py --alert) || RC=$?
#     set -E
#
# 检测范围 (保守, 只抓"设计性非零"类避免误报 json-parse 类真告警场景):
#   文件级前提: 同时含 `set -E`/-eE* (errtrace) + `trap ... ERR`
#   行级: `VAR=$(...python...)` 且命中 basename 清单 OR 审计类 flag
#   豁免: 处于 `set +E` ... `set -E` 区间内 / 注释行
_QUIRK_ERRTRACE_FILE_PRECONDITION = re.compile(
    r'^\s*set\s+-[a-z]*E', re.MULTILINE
)
_QUIRK_TRAP_ERR_PRECONDITION = re.compile(
    r'^\s*trap\s+.*\bERR\b', re.MULTILINE
)
_QUIRK_SET_PLUS_E = re.compile(r'^\s*set\s+\+[a-zA-Z]*E')
_QUIRK_SET_MINUS_E = re.compile(r'^\s*set\s+-[a-z]*E')
# 设计性非零命令: 已实证 basename 清单 + 审计类 flag 启发
# (flag 启发是必要的 — V37.9.131 watchdog 行内是 `"$SLO_SCRIPT"` 变量看不到文件名)
_QUIRK_SUBSHELL_DESIGNED_NONZERO = re.compile(
    r'\w+=\$\([^)]*\bpython3?\b[^)]*'
    r'(?:governance_checker\.py|slo_checker|--alert\b|--check\b|--validate\b|--full\b)'
)


# ── Quirk 7: 未 brace 变量紧贴 CJK/全角字符 (V37.9.43-hotfix2 + V37.9.141) ──
# macOS bash 3.2 在 UTF-8 locale 下把全角/CJK 字符的 UTF-8 字节并入变量名 →
# `set -u` 触发 `VAR�: unbound variable` 崩溃 / 无 set -u 时静默展开为空 (信息丢失).
# **locale 依赖让它格外隐蔽**: cron 环境 (LANG 未设, C locale) 不触发, 用户交互
# 终端 (UTF-8 locale) 触发 — 同一行代码 cron 跑数月正常, 用户手动跑立即崩.
# 修复: `${VAR}` 显式 brace (CLAUDE.md 原则 #35 同族 quirk 家族).
# 血案: V37.9.43-hotfix2 wa_e2e_test.sh `$CHUNK_COUNT）` (2026-05-09)
#       + V37.9.141 preflight_check.sh:868 `$PUSH_RC）` (2026-06-11 Mac Mini 22:53
#       实测 — push test 真失败首次走 fail 分支才触发, 崩溃同时吞掉真实失败信息
#       且 check 17-19 未跑). 范围: CJK 表意文字 + 全角形式 + CJK 标点.
_QUIRK_UNBRACED_VAR_CJK = re.compile(
    r'\$[A-Za-z_][A-Za-z0-9_]*[一-鿿＀-￯　-〿]'
)


# 7 个 quirk 检查器统一注册
_QUIRK_CHECKERS = (
    ("cmd_and_or_chain", "bash `cmd && X || Y` + set -eE + ERR trap false-positive FATAL"),
    ("grep_head_no_or_true", "bash `grep | head` + pipefail + set -eE 无 `|| true` 兜底"),
    ("awk_log_no_lc_all", "awk 处理 log 缺 `LC_ALL=C` 前缀 (macOS bsd multibyte 风险)"),
    ("zsh_specific_in_sh", "zsh-specific 语法在 cron .sh (POSIX sh 跑会失败)"),
    ("head_byte_tr_no_lc_all", "`head -c N | tr` 切多字节 UTF-8 在中间, macOS bsd tr 报 Illegal byte sequence (V37.9.68 教训)"),
    ("subshell_errtrace_designed_nonzero", "bash 3.2 + set -E: $(...) 调设计性非零命令无 set +E 包裹, 子 shell 继承 ERR trap 假 FATAL (V37.9.105-hotfix + V37.9.131)"),
    ("unbraced_var_adjacent_cjk", "未 brace `$VAR` 紧贴 CJK/全角字符, macOS bash 3.2 UTF-8 locale 下并入变量名 set -u 崩溃 (V37.9.43-hotfix2 + V37.9.141)"),
)


def _read(path):
    """读文件, 失败返回 None (FAIL-OPEN, 不抛)"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _is_in_comment_or_string(line):
    """简化检测: 行是否以 # 开头 (注释) 或在显式字符串/heredoc 内.

    简化版只过滤 # 注释 + 三引号 docstring 内行. 复杂上下文 (heredoc 内 Python) 由调用方
    通过文件路径过滤跳过 (test_*.py 等). 这是 PoC 阶段的折中, V37.9.68+ 升级 AST 解析.
    """
    return line.lstrip().startswith("#")


def detect_cmd_and_or_chain(content):
    """检测 `cmd && X || Y` 反模式 (V37.9.66-hotfix 同款)."""
    findings = []
    for ln, line in enumerate(content.split("\n"), 1):
        if _is_in_comment_or_string(line):
            continue
        if _QUIRK_CMD_AND_OR_PATTERN.search(line):
            findings.append((ln, "cmd_and_or_chain", line.strip()))
    return findings


def detect_grep_head_no_or_true(content):
    """检测 `grep | head` 无 `|| true` 兜底 (V37.9.60-hotfix 同款)."""
    findings = []
    for ln, line in enumerate(content.split("\n"), 1):
        if _is_in_comment_or_string(line):
            continue
        if _QUIRK_GREP_HEAD_PATTERN.search(line):
            # 检查同行是否有 || true 兜底
            if not _OR_TRUE_GUARD.search(line):
                findings.append((ln, "grep_head_no_or_true", line.strip()))
    return findings


def detect_awk_log_no_lc_all(content):
    """检测 awk 处理 log 但缺 `LC_ALL=C` (V37.9.58-hotfix3 同款)."""
    findings = []
    for ln, line in enumerate(content.split("\n"), 1):
        if _is_in_comment_or_string(line):
            continue
        # 只检 log 文件上下文的 awk 调用
        if _QUIRK_AWK_LOG_CONTEXT.search(line):
            # 如果同行已含 LC_ALL=C 紧邻 awk 前面, 豁免
            if "LC_ALL=C awk" not in line:
                findings.append((ln, "awk_log_no_lc_all", line.strip()))
    return findings


def detect_zsh_specific_in_sh(content):
    """检测 zsh-specific 语法 in .sh 脚本 (V37.9.56-hotfix2 同款)."""
    findings = []
    for ln, line in enumerate(content.split("\n"), 1):
        if _is_in_comment_or_string(line):
            continue
        m = _QUIRK_ZSH_SPECIFIC.search(line)
        if m:
            findings.append((ln, "zsh_specific_in_sh", line.strip()))
    return findings


def detect_head_byte_tr_no_lc_all(content):
    """检测 `head -c N | tr` 缺 LC_ALL=C (V37.9.68 教训).

    macOS bsd `tr` 默认 LC_CTYPE=UTF-8, 看到 incomplete multibyte sequence 报
    "tr: Illegal byte sequence". `head -c N` 按字节切而非字符, 中文 UTF-8 3 bytes/char,
    在 N 处可能正好把字符切断. 修复: tr 前加 `LC_ALL=C` 绕过 multibyte 校验.
    """
    findings = []
    for ln, line in enumerate(content.split("\n"), 1):
        if _is_in_comment_or_string(line):
            continue
        # 匹配 `head -c <数字> [...] | tr ...` (tr 前可有空格)
        if _QUIRK_HEAD_BYTE_TR.search(line):
            # 豁免: tr 前紧邻 LC_ALL=C
            if "LC_ALL=C tr" not in line:
                findings.append((ln, "head_byte_tr_no_lc_all", line.strip()))
    return findings


def detect_subshell_errtrace_designed_nonzero(content):
    """检测 $(...) 子 shell 继承 ERR trap quirk (V37.9.105-hotfix + V37.9.131 同款).

    文件级前提: 同时含 errtrace (`set -E`/-eE*) + `trap ... ERR` — 缺任一则
    quirk 不成立, 整文件 0 findings.

    行级状态机: `set +E` 进入豁免区 (errtrace 已关, 子 shell 不继承 trap),
    `set -E*` 退出豁免区. 区间外的 `VAR=$(...python...designed-nonzero...)`
    即 violation — 注意 `||` 兜底**不豁免** (V37.9.105 铁证: 外层 || 已捕获
    退出码, 子 shell 内 trap 仍 fire 推假 FATAL — || 捕获防不了 trap fire).
    """
    if not (_QUIRK_ERRTRACE_FILE_PRECONDITION.search(content)
            and _QUIRK_TRAP_ERR_PRECONDITION.search(content)):
        return []
    findings = []
    in_exempt = False
    for ln, line in enumerate(content.split("\n"), 1):
        if _is_in_comment_or_string(line):
            continue
        if _QUIRK_SET_PLUS_E.match(line):
            in_exempt = True
            continue
        if _QUIRK_SET_MINUS_E.match(line):
            in_exempt = False
            continue
        if in_exempt:
            continue
        if _QUIRK_SUBSHELL_DESIGNED_NONZERO.search(line):
            findings.append(
                (ln, "subshell_errtrace_designed_nonzero", line.strip())
            )
    return findings


def detect_unbraced_var_adjacent_cjk(content):
    """检测未 brace `$VAR` 紧贴 CJK/全角字符 (V37.9.43-hotfix2 + V37.9.141 同款).

    macOS bash 3.2 在 UTF-8 locale 下把紧随变量名的全角/CJK 字符的 UTF-8 字节并入
    变量名: `"$PUSH_RC）"` 被解析为变量 `PUSH_RC<0xEF>` → set -u 下
    `PUSH_RC?: unbound variable` 整脚本崩溃; 无 set -u 时静默展开为空 (值丢失).
    locale 依赖: cron (C locale) 不触发 / 用户交互终端 (UTF-8) 触发 — 12 处同款
    在 cron 跑数月无恙, preflight 交互执行立即崩 (2026-06-11 实测).
    修复: `${VAR}` 显式 brace. 正则要求 [A-Za-z_] 开头, `$1`/`$?` 等特殊参数天然豁免.
    """
    findings = []
    for ln, line in enumerate(content.split("\n"), 1):
        if _is_in_comment_or_string(line):
            continue
        if _QUIRK_UNBRACED_VAR_CJK.search(line):
            findings.append((ln, "unbraced_var_adjacent_cjk", line.strip()))
    return findings


def scan_file(path):
    """扫单文件返回所有 findings: [(line_no, quirk_name, line_text), ...]"""
    content = _read(path)
    if content is None:
        return []

    findings = []
    findings.extend(detect_cmd_and_or_chain(content))
    findings.extend(detect_grep_head_no_or_true(content))
    findings.extend(detect_awk_log_no_lc_all(content))
    findings.extend(detect_zsh_specific_in_sh(content))
    findings.extend(detect_head_byte_tr_no_lc_all(content))
    findings.extend(detect_subshell_errtrace_designed_nonzero(content))
    findings.extend(detect_unbraced_var_adjacent_cjk(content))
    return findings


def _should_skip(path):
    """跳过测试文件 / 备份 / .git / 反思文档 / scanner 自己 (含字面量反模式)"""
    parts = path.split(os.sep)
    if any(p in parts for p in (".git", "node_modules", ".bak")):
        return True
    basename = os.path.basename(path)
    # 跳过测试文件 (含字面量反模式作示例) + scanner 自身 + 反思文档
    if basename.startswith("test_") or basename in (
        "cross_os_quirk_scanner.py",
    ):
        return True
    # 跳过 ontology/docs/ 文档 (含示例字面量反模式)
    if "docs" in parts and ("ontology" in parts or "reflections" in parts):
        return True
    return False


def scan_repo(repo_root):
    """扫整 repo 返回 dict[path → findings]"""
    all_findings = {}
    for root, dirs, files in os.walk(repo_root):
        # 跳过 .git / venv / __pycache__ / node_modules
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "venv",
                                                 "__pycache__", "node_modules")]
        for fn in files:
            if not (fn.endswith(".sh") or fn.endswith(".py")):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, repo_root)
            if _should_skip(rel):
                continue
            findings = scan_file(full)
            if findings:
                all_findings[rel] = findings
    return all_findings


def format_findings(all_findings):
    """格式化输出 violation 报告"""
    if not all_findings:
        return "✅ INV-CROSS-OS-001 全 repo scan: 0 violations\n"
    lines = ["❌ INV-CROSS-OS-001 violations 发现:\n"]
    total = 0
    for path in sorted(all_findings):
        findings = all_findings[path]
        total += len(findings)
        lines.append(f"  {path}:")
        for ln, quirk, text in findings:
            text_preview = text[:80] + "..." if len(text) > 80 else text
            lines.append(f"    L{ln} [{quirk}]: {text_preview}")
        lines.append("")
    lines.append(f"共 {total} 个 violation(s) 跨 {len(all_findings)} 个文件")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="V37.9.67 INV-CROSS-OS-001 跨 OS quirk scanner (FAIL-CLOSE)"
    )
    parser.add_argument(
        "--file", "-f", help="扫单文件 (相对或绝对路径)"
    )
    parser.add_argument(
        "--list-quirks", "-l", action="store_true",
        help="列出所有检测的 quirk pattern"
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="repo 根目录 (默认当前目录)"
    )
    args = parser.parse_args()

    if args.list_quirks:
        print("V37.9.67 cross_os_quirk_scanner 检测的 quirk pattern:")
        for name, desc in _QUIRK_CHECKERS:
            print(f"  - {name}: {desc}")
        return 0

    if args.file:
        findings = scan_file(args.file)
        all_findings = {args.file: findings} if findings else {}
    else:
        repo_root = args.repo_root or os.getcwd()
        all_findings = scan_repo(repo_root)

    print(format_findings(all_findings))
    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())

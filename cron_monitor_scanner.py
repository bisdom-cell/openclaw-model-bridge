#!/usr/bin/env python3
"""cron_monitor_scanner.py — V37.9.60 MR-19 err_trap_handler contract enforcement

V37.9.58-hotfix3 立 MR-19 (monitor-must-self-alarm-on-silent-abort) 时只把 job_watchdog.sh
升级为合规。V37.9.60 把 err_trap_handler 契约横向推广到所有 cron 类聚合监控脚本。

契约: 任何启用 set -e* 的 cron monitor 必须 trap '...' ERR 注册 fatal handler 主动推
[SYSTEM_ALERT], 防止 silent abort 多日累积告警 invisible。

FAIL-CLOSE: 找到任一 violation 必须 exit 1。

MR-19 治理对象 (cron 类聚合监控脚本):
  - job_watchdog.sh            ← V37.9.58-hotfix3 合规
  - governance_audit_cron.sh   ← V37.9.60 修复
  - daily_ops_report.sh        ← V37.9.60 修复
  - auto_deploy.sh             ← V37.9.60 修复

不适用 (轻量 probe / heartbeat writer / user-interactive 诊断):
  - wa_keepalive (V37.9.59 LOG_FRESHNESS 已监控)
  - cron_canary (本身 heartbeat writer)
  - health_check (周报无 set -e, 不属 silent abort 类)
  - preflight_check (user-interactive 诊断, fail-fast 给用户看是预期)

Usage:
  python3 cron_monitor_scanner.py                  # 扫所有 governed scripts (FAIL-CLOSE)
  python3 cron_monitor_scanner.py --file X.sh      # 扫单文件
  python3 cron_monitor_scanner.py --list           # 列出所有 governed scripts
"""
import argparse
import os
import re
import sys


# MR-19 治理对象: 必须满足 err_trap_handler 契约
# 路径相对仓库根目录, 按 V37.9.58-hotfix3 起步顺序排列
SCRIPTS_REQUIRING_ERR_TRAP = (
    "job_watchdog.sh",            # V37.9.58-hotfix3 立案的标杆 (already compliant)
    "governance_audit_cron.sh",   # V37.9.60 加入 (set -euo + 推送 alerts, V37.9.58-hotfix3 同款盲点)
    "daily_ops_report.sh",        # V37.9.60 加入 (set -eo + ops 报告聚合)
    "auto_deploy.sh",             # V37.9.60 加入 (set -euo + 高频 2min cron)
)


# 启用 set -e 类的 regex
# 匹配: set -e / set -ex / set -eo pipefail / set -euo pipefail / set -eEo pipefail
# 关键: -开头的字母组合中包含 e
_SET_E_RE = re.compile(r'^\s*set\s+-[a-zA-Z]*e[a-zA-Z]*(\s|$)', re.MULTILINE)


# trap ... ERR 的 regex: 简单可靠的策略
# 匹配以 trap 开头的行, 后续 (单行内) 出现 ERR 作为词边界
# 不限制 handler 形式 (支持单/双引号 + 含特殊字符) 因为单行 ERR 词出现是必要条件
_TRAP_ERR_RE = re.compile(r'^\s*trap\s+.+\bERR\b', re.MULTILINE)


# [SYSTEM_ALERT] 字面量出现 (handler 推送告警的证据)
_SYSTEM_ALERT_MARKER = "[SYSTEM_ALERT]"


def _read(path):
    """读文件, 失败返回 None (FAIL-OPEN, 不抛)"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def has_set_e_strict(content):
    """检查脚本是否启用 set -e / set -eo pipefail / set -eu / set -eEo 等任一含 e 选项。

    覆盖: set -e / set -eo pipefail / set -euo pipefail / set -eEo pipefail / set -ex
    不覆盖: set -u / set -o pipefail (单独, 无 e)
    """
    if not content:
        return False
    return bool(_SET_E_RE.search(content))


def has_err_trap(content):
    """检查脚本是否注册 'trap ... ERR' handler。

    匹配单行内 trap 后任意位置出现 ERR 词边界。
    支持: trap 'handler' ERR / trap 'handler' EXIT ERR / trap 'handler' RETURN ERR DEBUG
    不匹配: trap 'handler' EXIT (无 ERR) / 注释行 # trap ... ERR
    """
    if not content:
        return False
    # 跳过注释行 (# 开头) 避免误判 docstring 引用
    for line in content.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _TRAP_ERR_RE.match(line):
            return True
    return False


def has_system_alert_marker(content):
    """检查脚本是否出现 [SYSTEM_ALERT] 字面量 (handler 推送告警的间接证据)。

    注意: 简化检查 — 整脚本任意位置出现即视为合规。handler 函数定义可能在文件其他段落,
    跨函数边界精确分析超出本 scanner 范围。INV declaration 守卫负责语义级 audit。
    """
    if not content:
        return False
    return _SYSTEM_ALERT_MARKER in content


def scan_script(script_path):
    """扫描单脚本, 返回 violation 列表。

    返回: [(path, category, reason), ...]
    若无 violation 返回 []
    """
    findings = []
    content = _read(script_path)
    if content is None:
        return [(
            script_path,
            "file_not_readable",
            f"Cannot read {script_path}. Scanner cannot validate; manual review required."
        )]

    if not has_set_e_strict(content):
        # 脚本未启用 set -e* → silent abort 不会因 set -e 触发, MR-19 ERR trap 不强制
        return []

    if not has_err_trap(content):
        findings.append((
            script_path,
            "missing_err_trap",
            "Script enables 'set -e*' but no 'trap ... ERR' handler registered. "
            "Silent abort risk: any failing command kills script silently, "
            "alerts accumulated but invisible. "
            "Fix: register `trap '<handler>' ERR` with [SYSTEM_ALERT] push (V37.9.58-hotfix3 pattern)."
        ))
        return findings

    if not has_system_alert_marker(content):
        findings.append((
            script_path,
            "trap_handler_no_alert",
            "ERR trap registered but no [SYSTEM_ALERT] marker found in script. "
            "Handler must push alert to convert silent abort into loud failure. "
            "Fix: ensure handler function includes [SYSTEM_ALERT] notification "
            "(via notify.sh, openclaw send, or local fallback log)."
        ))

    return findings


def scan_repo(repo_root, scripts=None):
    """扫描多脚本, 返回 all violations。"""
    if scripts is None:
        scripts = SCRIPTS_REQUIRING_ERR_TRAP
    all_findings = []
    for script in scripts:
        script_path = os.path.join(repo_root, script)
        all_findings.extend(scan_script(script_path))
    return all_findings


def format_findings(findings):
    """格式化输出 violation 报告"""
    if not findings:
        return ""
    lines = []
    for path, category, reason in findings:
        lines.append(f"  ❌ {path}")
        lines.append(f"     category: {category}")
        lines.append(f"     reason:   {reason}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="MR-19 err_trap_handler contract scanner for cron monitor scripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--file", "-f",
        help="Scan a single file (path relative to cwd or absolute)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all governed scripts and exit",
    )
    parser.add_argument(
        "--repo-root",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Repository root (default: scanner file's parent)",
    )
    args = parser.parse_args()

    if args.list:
        print("MR-19 governed cron monitor scripts:")
        for script in SCRIPTS_REQUIRING_ERR_TRAP:
            print(f"  - {script}")
        print(f"\nTotal: {len(SCRIPTS_REQUIRING_ERR_TRAP)} scripts")
        return 0

    if args.file:
        findings = scan_script(args.file)
        scripts_scanned = 1
    else:
        findings = scan_repo(args.repo_root)
        scripts_scanned = len(SCRIPTS_REQUIRING_ERR_TRAP)

    if not findings:
        print(f"✅ MR-19 scan PASSED — {scripts_scanned} script(s) checked, 0 violations")
        return 0

    print(f"❌ MR-19 scan FAILED — {len(findings)} violation(s) in {scripts_scanned} scripts", file=sys.stderr)
    print("", file=sys.stderr)
    print(format_findings(findings), file=sys.stderr)
    print(
        "Background: V37.9.58-hotfix3 立案 MR-19 (monitor-must-self-alarm-on-silent-abort)。\n"
        "V37.9.60 把 err_trap_handler 契约横向推广到所有 cron 类聚合监控脚本。\n"
        "参考 job_watchdog.sh 的 _watchdog_fatal_handler 模式实现。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

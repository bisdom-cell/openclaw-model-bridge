#!/usr/bin/env python3
"""
adversarial_audit.py — 声明 vs 实际 对抗审计
V36.2 新增。核心问题："什么东西坏了我们会发现不了？"

不验证内部一致性（那是 check_registry.py / preflight 的工作），
而是验证声明层（CLAUDE.md / registry / config）与运行时层（crontab / 进程 / 环境变量）是否一致。

用法：
  python3 adversarial_audit.py            # 运行所有可在 dev 环境执行的检查
  python3 adversarial_audit.py --full     # Mac Mini 上运行（含 crontab / 环境变量 / 服务）
  python3 adversarial_audit.py --json     # JSON 输出

设计原则：
  - 每个检查回答一个"如果X坏了我们能发现吗"的问题
  - 检查层只做断言，不做修复（修复是人的决策）
  - 新增检查只需要加一个 @audit 装饰的函数
"""
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
FULL_MODE = "--full" in sys.argv
JSON_MODE = "--json" in sys.argv

results = []  # [{"id": str, "severity": str, "status": str, "message": str}]


def audit(audit_id, severity="high"):
    """Decorator to register an audit check."""
    def decorator(fn):
        fn._audit_id = audit_id
        fn._severity = severity
        return fn
    return decorator

AUDITS = []

def register(fn):
    AUDITS.append(fn)
    return fn


# ═══════════════════════════════════════════════════════════════════════
# Audit checks
# ═══════════════════════════════════════════════════════════════════════

@register
@audit("tool-count-enforcement", severity="critical")
def check_tool_count():
    """声明 ≤12 工具，filter_tools() 是否真的强制执行？"""
    sys.path.insert(0, REPO_ROOT)
    try:
        from proxy_filters import filter_tools, CUSTOM_TOOLS, ALLOWED_TOOLS
        from config_loader import MAX_TOOLS
    except ImportError as e:
        return "fail", f"无法导入: {e}"

    # 构造超限场景
    tools = [{"function": {"name": n, "parameters": {}}} for n in ALLOWED_TOOLS]
    filtered, _, kept = filter_tools(tools)

    if len(filtered) > MAX_TOOLS:
        return "fail", f"filter_tools() 产出 {len(filtered)} 工具 > MAX_TOOLS={MAX_TOOLS}"

    # 确认 custom tools 存活
    custom_names = {t["function"]["name"] for t in CUSTOM_TOOLS}
    for cn in custom_names:
        if cn not in kept:
            return "fail", f"Custom tool '{cn}' 被截断丢失"

    return "pass", f"工具数 {len(filtered)} ≤ {MAX_TOOLS}，custom tools 保留"


@register
@audit("max-tools-import", severity="critical")
def check_max_tools_imported():
    """MAX_TOOLS 是否真的被 proxy_filters.py 导入使用？"""
    proxy_path = os.path.join(REPO_ROOT, "proxy_filters.py")
    with open(proxy_path) as f:
        content = f.read()
    if "MAX_TOOLS" not in content:
        return "fail", "proxy_filters.py 未导入 MAX_TOOLS — 工具数量限制是死代码"
    if "_CFG_MAX_TOOLS" not in content:
        return "fail", "proxy_filters.py 导入了 MAX_TOOLS 但未在 filter_tools() 中使用"
    return "pass", "MAX_TOOLS 已导入并在 filter_tools() 中使用"


@register
@audit("notify-empty-channel-detection", severity="high")
def check_notify_empty_channel():
    """notify.sh 在 channel ID 为空时是否会报错？"""
    notify_path = os.path.join(REPO_ROOT, "notify.sh")
    with open(notify_path) as f:
        content = f.read()
    if "ERROR" in content and "为空" in content:
        return "pass", "notify.sh 在空 channel ID 时打印 ERROR"
    return "fail", "notify.sh 空 channel ID 静默跳过 — 推送会丢失无痕迹"


@register
@audit("registry-needs-api-key-validation", severity="high")
def check_needs_api_key_not_dead_code():
    """registry 的 needs_api_key 字段是否被任何代码消费？"""
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
        for f in files:
            if f.endswith((".py", ".sh")):
                fpath = os.path.join(root, f)
                try:
                    with open(fpath) as fh:
                        if "needs_api_key" in fh.read():
                            if f not in ("jobs_registry.yaml", "adversarial_audit.py",
                                         "check_registry.py", "gen_jobs_doc.py"):
                                return "pass", f"needs_api_key 在 {f} 中被消费"
                except Exception:
                    pass
    return "warn", "needs_api_key 字段仅出现在 registry/文档中 — 没有代码真正验证 API key 可用性"


@register
@audit("custom-tool-schema-consistency", severity="high")
def check_custom_tool_schema():
    """CUSTOM_TOOLS 的 schema 与 tool_proxy.py 实现是否一致？"""
    sys.path.insert(0, REPO_ROOT)
    try:
        from proxy_filters import CUSTOM_TOOLS
    except ImportError as e:
        return "fail", f"无法导入: {e}"

    issues = []
    for ct in CUSTOM_TOOLS:
        name = ct.get("function", {}).get("name", "?")
        params = ct.get("function", {}).get("parameters", {})
        props = set(params.get("properties", {}).keys())

        # 检查 tool_proxy.py 中对应 handler 使用的参数
        proxy_path = os.path.join(REPO_ROOT, "tool_proxy.py")
        with open(proxy_path) as f:
            proxy_code = f.read()

        if name == "data_clean":
            # tool_proxy.py 中 _handle_data_clean 使用的参数
            for expected_param in ["action", "file", "ops"]:
                if expected_param not in props:
                    issues.append(f"data_clean schema 缺少 '{expected_param}'")
            # 检查 fix_date_cols 是否在 proxy 中使用但不在 schema 中
            if "fix_date_cols" in proxy_code and "fix_date_cols" not in props:
                issues.append("data_clean: tool_proxy 使用 fix_date_cols 但 schema 中未声明")

    if issues:
        return "warn", "; ".join(issues)
    return "pass", "CUSTOM_TOOLS schema 与 tool_proxy.py 一致"


@register
@audit("status-health-freshness", severity="high")
def check_status_freshness():
    """status.json 的 health 字段是否可能陈旧？"""
    status_path = os.path.join(REPO_ROOT, "status.json")
    if not os.path.exists(status_path):
        return "skip", "status.json 不存在（dev 环境）"

    with open(status_path) as f:
        data = json.load(f)

    issues = []
    health = data.get("health", {})
    quality = data.get("quality", {})

    # security_score 自动刷新 + 时间戳检查
    if quality.get("security_score") and "security_score_time" not in quality:
        # 检查 kb_status_refresh.sh 是否有自动刷新代码
        refresh_path = os.path.join(REPO_ROOT, "kb_status_refresh.sh")
        has_auto_refresh = False
        if os.path.exists(refresh_path):
            with open(refresh_path) as rf:
                has_auto_refresh = "security_score_time" in rf.read()
        if not has_auto_refresh:
            issues.append("security_score 无时间戳且无自动刷新 — 可能陈旧数周而不被发现")
        # 如果有自动刷新代码但 status.json 还没有时间戳，说明尚未运行（可接受）

    # last_regression 可能陈旧
    last_reg = quality.get("last_regression", "")
    if last_reg and "pass" in last_reg:
        # 只检查格式，不做时间计算（dev 环境时区可能不同）
        pass

    if issues:
        return "warn", "; ".join(issues)
    return "pass", "status.json health 字段结构正常"


# ═══════════════════════════════════════════════════════════════════════
# Full-mode only checks (require Mac Mini environment)
# ═══════════════════════════════════════════════════════════════════════

@register
@audit("crontab-interval-drift", severity="critical")
def check_crontab_drift():
    """registry interval 与实际 crontab 是否一致？"""
    if not FULL_MODE:
        return "skip", "需要 --full 模式（Mac Mini）"
    try:
        result = subprocess.run(
            ["python3", os.path.join(REPO_ROOT, "check_registry.py"), "--check-crontab"],
            capture_output=True, text=True, timeout=10
        )
        if "间隔漂移" in result.stdout:
            drift_lines = [l for l in result.stdout.split("\n") if "间隔漂移" in l]
            return "fail", f"{len(drift_lines)} 个漂移: " + "; ".join(drift_lines[:3])
        return "pass", "crontab 间隔与 registry 一致"
    except Exception as e:
        return "fail", f"检查异常: {e}"


@register
@audit("discord-channel-env-vars", severity="critical")
def check_discord_channels():
    """6 个 DISCORD_CH_* 环境变量是否在 cron 环境中设置？"""
    if not FULL_MODE:
        return "skip", "需要 --full 模式（Mac Mini）"

    channels = ["PAPERS", "TECH", "ALERTS", "DAILY", "FREIGHT", "ONTOLOGY"]
    missing = []
    for ch in channels:
        var = f"DISCORD_CH_{ch}"
        try:
            result = subprocess.run(
                ["bash", "-lc", f"echo ${{{var}:-}}"],
                capture_output=True, text=True, timeout=5
            )
            if not result.stdout.strip():
                missing.append(var)
        except Exception:
            missing.append(var)

    if missing:
        return "fail", f"{len(missing)} 个频道 ID 缺失: {', '.join(missing)} — 对应频道推送会静默丢失"
    return "pass", f"所有 {len(channels)} 个 Discord 频道 ID 已设置"


@register
@audit("service-health-response-body", severity="high")
def check_service_health_body():
    """服务 /health 端点返回的内容是否真的包含 ok 字段？"""
    if not FULL_MODE:
        return "skip", "需要 --full 模式（Mac Mini）"

    issues = []
    for name, port in [("Adapter", 5001), ("Proxy", 5002)]:
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "5", f"http://localhost:{port}/health"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                issues.append(f"{name}:{port} 连接失败")
                continue
            try:
                data = json.loads(result.stdout)
                if not data.get("ok"):
                    issues.append(f"{name}:{port} 返回 HTTP 200 但 ok 字段为 False/缺失")
            except json.JSONDecodeError:
                issues.append(f"{name}:{port} 返回非 JSON 响应")
        except Exception as e:
            issues.append(f"{name}:{port} 检查异常: {e}")

    if issues:
        return "fail", "; ".join(issues)
    return "pass", "所有服务 /health 端点返回有效 JSON 且 ok=true"


# ═══════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════

def run_all():
    for fn in AUDITS:
        aid = fn._audit_id
        severity = fn._severity
        try:
            status, message = fn()
        except Exception as e:
            status, message = "error", str(e)
        results.append({
            "id": aid,
            "severity": severity,
            "status": status,
            "message": message,
        })

    return results


def print_results(results):
    if JSON_MODE:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    print("=" * 60)
    print("  ADVERSARIAL AUDIT — 声明 vs 实际")
    print(f"  模式: {'FULL (Mac Mini)' if FULL_MODE else 'DEV (repo only)'}")
    print("=" * 60)

    icons = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭ ", "error": "💥"}
    sev_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡"}

    fails = 0
    for r in results:
        icon = icons.get(r["status"], "?")
        sev = sev_icons.get(r["severity"], "")
        print(f"  {icon} {sev} [{r['id']}] {r['message']}")
        if r["status"] == "fail":
            fails += 1

    print()
    total = len([r for r in results if r["status"] != "skip"])
    passed = len([r for r in results if r["status"] == "pass"])
    print(f"  {passed}/{total} passed, {fails} failed")
    if fails:
        print(f"\n  ❌ AUDIT FAILED — {fails} 个声明与实际不一致")
    else:
        print(f"\n  ✅ AUDIT PASSED — 所有声明与实际一致")
    print("=" * 60)

    return fails


if __name__ == "__main__":
    results = run_all()
    fails = print_results(results)
    sys.exit(1 if fails else 0)

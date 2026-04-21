#!/usr/bin/env python3
"""
security_score.py — 系统安全评分体系

量化评估系统安全状态，输出 0-100 分。
可集成到 preflight、status.json、full_regression。

评分维度（6项，各15分 + 可用性10分 = 100分）：
  1. 密钥管理       15分
  2. 测试门禁       15分
  3. 数据完整性     15分
  4. 部署安全       15分
  5. 传输安全       15分
  6. 审计追踪       15分
  7. 可用性         10分

用法：
  python3 security_score.py              # 人类可读报告
  python3 security_score.py --json       # JSON 输出
  python3 security_score.py --update     # 写入 status.json
"""
import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys

SCORE_VERSION = "1.0"


def check_key_management():
    """密钥管理：15分"""
    score = 0
    details = []

    # 检查核心文件无硬编码密钥（5分）
    core_files = ["adapter.py", "tool_proxy.py", "proxy_filters.py", "status_update.py"]
    hardcoded = []
    for f in core_files:
        if not os.path.exists(f):
            continue
        with open(f) as fh:
            content = fh.read()
        if re.search(r'["\']sk-[A-Za-z0-9]{20,}["\']', content):
            hardcoded.append(f)
        if re.search(r'["\']BSA[A-Za-z0-9]{15,}["\']', content):
            hardcoded.append(f)
    if not hardcoded:
        score += 5
        details.append("✅ 核心文件无硬编码密钥")
    else:
        details.append(f"❌ 硬编码密钥: {hardcoded}")

    # 检查环境变量引用（5分）
    env_patterns = ["os.environ", "os.getenv"]
    has_env = False
    if os.path.exists("adapter.py"):
        with open("adapter.py") as f:
            content = f.read()
        has_env = any(p in content for p in env_patterns)
    if has_env:
        score += 5
        details.append("✅ API Key 通过环境变量管理")
    else:
        details.append("❌ 未使用环境变量管理密钥")

    # 自动扫描在 full_regression 中（5分）
    if os.path.exists("full_regression.sh"):
        with open("full_regression.sh") as f:
            content = f.read()
        if "API Key" in content and "泄漏" in content:
            score += 5
            details.append("✅ 自动密钥泄漏扫描已启用")
        else:
            details.append("⚠️ 无自动密钥泄漏扫描")

    return score, 15, details


def check_test_gate():
    """测试门禁：15分"""
    score = 0
    details = []

    # 测试文件数量（5分）
    test_files = glob.glob("test_*.py")
    if len(test_files) >= 8:
        score += 5
        details.append(f"✅ {len(test_files)} 个测试套件")
    elif len(test_files) >= 4:
        score += 3
        details.append(f"⚠️ {len(test_files)} 个测试套件（建议 >= 8）")
    else:
        details.append(f"❌ 仅 {len(test_files)} 个测试套件")

    # full_regression.sh 存在且是门禁（5分）
    if os.path.exists("full_regression.sh"):
        with open("full_regression.sh") as f:
            content = f.read()
        if "禁止推送" in content or "exit 1" in content:
            score += 5
            details.append("✅ 发布门禁已启用（100%通过才允许推送）")
        else:
            score += 2
            details.append("⚠️ full_regression.sh 存在但无门禁")
    else:
        details.append("❌ 无 full_regression.sh")

    # bandit 安全扫描集成（5分）
    if os.path.exists("full_regression.sh"):
        with open("full_regression.sh") as f:
            content = f.read()
        if "bandit" in content:
            score += 5
            details.append("✅ bandit 静态安全分析已集成")
        else:
            details.append("⚠️ 未集成 bandit")

    return score, 15, details


def check_data_integrity():
    """数据完整性：15分"""
    score = 0
    details = []

    # 原子写入（5分）
    atomic_files = ["status_update.py", "proxy_filters.py", "audit_log.py"]
    atomic_count = 0
    for f in atomic_files:
        if not os.path.exists(f):
            continue
        with open(f) as fh:
            content = fh.read()
        if "os.replace" in content or "os.rename" in content:
            atomic_count += 1
    if atomic_count >= 3:
        score += 5
        details.append(f"✅ {atomic_count} 个文件使用原子写入")
    elif atomic_count >= 1:
        score += 3
        details.append(f"⚠️ {atomic_count} 个文件使用原子写入（建议全覆盖）")
    else:
        details.append("❌ 无原子写入保护")

    # SHA256 完整性校验（5分）
    if os.path.exists("kb_integrity.py"):
        with open("kb_integrity.py") as f:
            content = f.read()
        if "sha256" in content.lower():
            score += 5
            details.append("✅ SHA256 文件完整性校验")
        else:
            score += 2
            details.append("⚠️ kb_integrity.py 存在但无 SHA256")
    else:
        details.append("❌ 无文件完整性校验")

    # 备份机制（5分）
    if os.path.exists("openclaw_backup.sh"):
        with open("openclaw_backup.sh") as f:
            content = f.read()
        has_backup = "status_history" in content or "backup" in content.lower()
        if has_backup:
            score += 5
            details.append("✅ 每日自动备份 + 历史保留")
        else:
            score += 3
            details.append("⚠️ 备份脚本存在但缺少历史保留")
    else:
        details.append("❌ 无自动备份")

    return score, 15, details


def check_deploy_security():
    """部署安全：15分"""
    score = 0
    details = []

    # 漂移检测（5分）
    if os.path.exists("auto_deploy.sh"):
        with open("auto_deploy.sh") as f:
            content = f.read()
        if "md5" in content or "drift" in content.lower():
            score += 5
            details.append("✅ 部署漂移检测（md5比对）")
        else:
            details.append("⚠️ auto_deploy 无漂移检测")
    else:
        details.append("❌ 无自动部署")

    # preflight 体检（5分）
    if os.path.exists("preflight_check.sh"):
        with open("preflight_check.sh") as f:
            content = f.read()
        checks = content.count("echo -n") + content.count("echo \"")
        if checks >= 10:
            score += 5
            details.append(f"✅ preflight 多项体检")
        else:
            score += 3
            details.append(f"⚠️ preflight 检查项不足")
    else:
        details.append("❌ 无 preflight 体检")

    # crontab 安全（5分）
    if os.path.exists("crontab_safe.sh"):
        score += 5
        details.append("✅ crontab 安全操作保护")
    else:
        details.append("❌ 无 crontab 安全保护")

    return score, 15, details


def check_transport_security():
    """传输安全：15分"""
    score = 0
    details = []

    # 端口绑定 localhost（5分）
    for name, f in [("adapter", "adapter.py"), ("proxy", "tool_proxy.py")]:
        if not os.path.exists(f):
            continue
        with open(f) as fh:
            content = fh.read()
        if '127.0.0.1' in content:
            score += 2.5
            details.append(f"✅ {name} 绑定 127.0.0.1")
        elif '0.0.0.0' in content:
            details.append(f"❌ {name} 绑定 0.0.0.0（暴露到网络）")
        else:
            score += 2.5
            details.append(f"✅ {name} 默认安全绑定")

    # HTTPS 连接远程（5分）
    if os.path.exists("adapter.py"):
        with open("adapter.py") as f:
            content = f.read()
        if "https://" in content:
            score += 5
            details.append("✅ 远程 GPU 使用 HTTPS")
        else:
            details.append("⚠️ 未确认远程连接加密")

    # Git SSH（5分）
    try:
        result = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True, timeout=5)
        if "git@github.com" in result.stdout:
            score += 5
            details.append("✅ Git 使用 SSH 协议")
        elif "https://" in result.stdout:
            score += 3
            details.append("⚠️ Git 使用 HTTPS（建议 SSH）")
    except Exception:
        details.append("⚠️ 无法检测 Git 协议")

    return int(score), 15, details


def check_audit_trail():
    """审计追踪：15分"""
    score = 0
    details = []

    # audit_log.py 存在（5分）
    if os.path.exists("audit_log.py"):
        with open("audit_log.py") as f:
            content = f.read()
        if "chain" in content.lower() or "prev" in content:
            score += 5
            details.append("✅ 链式哈希审计日志")
        else:
            score += 3
            details.append("⚠️ 审计日志存在但无链式校验")
    else:
        details.append("❌ 无审计日志")

    # 审计集成到 status_update（5分）
    if os.path.exists("status_update.py"):
        with open("status_update.py") as f:
            content = f.read()
        if "audit" in content:
            score += 5
            details.append("✅ 状态变更自动记录审计")
        else:
            details.append("⚠️ 状态变更无审计记录")

    # 审计验证在 full_regression 中（5分）
    if os.path.exists("full_regression.sh"):
        with open("full_regression.sh") as f:
            content = f.read()
        if "审计" in content and "完整性" in content:
            score += 5
            details.append("✅ 审计完整性自动校验")
        else:
            details.append("⚠️ 审计完整性未纳入自动校验")

    return score, 15, details


def check_availability():
    """可用性：10分"""
    score = 0
    details = []

    # Model Fallback（4分）
    if os.path.exists("adapter.py"):
        with open("adapter.py") as f:
            content = f.read()
        if "fallback" in content.lower():
            score += 4
            details.append("✅ Model Fallback 降级链")
        else:
            details.append("❌ 无 Fallback 降级")

    # 陈旧锁自愈（3分）
    if os.path.exists("job_watchdog.sh"):
        with open("job_watchdog.sh") as f:
            content = f.read()
        if "stale" in content.lower() or "rmdir" in content:
            score += 3
            details.append("✅ 陈旧锁自动清理")
        else:
            details.append("⚠️ 无陈旧锁自愈")

    # Cron 三层保护（3分）
    cron_files = ["crontab_safe.sh", "cron_canary.sh", "cron_doctor.sh"]
    cron_count = sum(1 for f in cron_files if os.path.exists(f))
    if cron_count >= 3:
        score += 3
        details.append("✅ Cron 三层保护（预防/检测/诊断）")
    elif cron_count >= 1:
        score += 1
        details.append(f"⚠️ Cron 保护: {cron_count}/3 层")
    else:
        details.append("❌ 无 Cron 保护")

    return score, 10, details


def compute_score():
    """计算完整安全评分。"""
    checks = [
        ("密钥管理", check_key_management),
        ("测试门禁", check_test_gate),
        ("数据完整性", check_data_integrity),
        ("部署安全", check_deploy_security),
        ("传输安全", check_transport_security),
        ("审计追踪", check_audit_trail),
        ("可用性", check_availability),
    ]

    total_score = 0
    total_max = 0
    results = []

    for name, check_fn in checks:
        score, max_score, details = check_fn()
        total_score += score
        total_max += max_score
        results.append({
            "name": name,
            "score": score,
            "max": max_score,
            "details": details,
        })

    return {
        "version": SCORE_VERSION,
        "total": total_score,
        "max": total_max,
        "percentage": round(total_score / total_max * 100, 1) if total_max > 0 else 0,
        "dimensions": results,
    }


def format_report(data):
    """人类可读报告。"""
    lines = [
        f"🔐 安全评分：{data['total']}/{data['max']}（{data['percentage']}%）",
        "",
    ]
    for dim in data["dimensions"]:
        bar = "█" * int(dim["score"] / dim["max"] * 10) + "░" * (10 - int(dim["score"] / dim["max"] * 10))
        icon = "✅" if dim["score"] == dim["max"] else "⚠️" if dim["score"] >= dim["max"] * 0.6 else "❌"
        lines.append(f"{icon} {dim['name']:8s} {dim['score']:2d}/{dim['max']:2d} {bar}")
        for d in dim["details"]:
            lines.append(f"    {d}")
        lines.append("")

    # 改进建议
    low_dims = [d for d in data["dimensions"] if d["score"] < d["max"]]
    if low_dims:
        lines.append("📈 改进建议：")
        for d in sorted(low_dims, key=lambda x: x["score"] / x["max"]):
            gap = d["max"] - d["score"]
            lines.append(f"  • {d['name']}（+{gap}分可提升）")

    return "\n".join(lines)


def load_ontology_thresholds():
    """V37.9.3 路线 C Step 3: 从 governance_ontology.yaml 读 security_config 数据源。

    返回 (total_threshold, per_dimension_thresholds_dict)。
    失败降级：返回 (None, {}) 而非异常，避免破坏 --json/--update 主流程。
    """
    try:
        import yaml
    except ImportError:
        return None, {}
    here = os.path.dirname(os.path.abspath(__file__))
    yaml_path = os.path.join(here, "ontology", "governance_ontology.yaml")
    if not os.path.exists(yaml_path):
        return None, {}
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            onto = yaml.safe_load(f)
    except Exception:
        return None, {}
    sec_cfg = (onto or {}).get("security_config") or {}
    total_threshold = sec_cfg.get("total_threshold")
    per_dim = sec_cfg.get("dimensions") or {}
    if not isinstance(per_dim, dict):
        per_dim = {}
    return total_threshold, per_dim


def check_ontology_thresholds(data):
    """V37.9.3 路线 C Step 3: 用 ontology 声明的阈值自检当前 score。

    输入: compute_score() 返回的 data
    返回: (ok: bool, violations: list[str])

    与 governance INV-SEC-001 runtime check 读同一 YAML，保证两侧判定一致。
    """
    total_threshold, per_dim = load_ontology_thresholds()
    violations = []
    if total_threshold is not None and data.get("total", 0) < total_threshold:
        violations.append(
            f"总分 {data['total']}/{data['max']} < ontology 阈值 {total_threshold}"
        )
    if per_dim:
        for dim in data.get("dimensions", []):
            name = dim.get("name")
            score = dim.get("score", 0)
            min_score = per_dim.get(name)
            if min_score is None:
                continue
            if score < min_score:
                violations.append(
                    f"{name} {score}/{dim.get('max')} < ontology 阈值 {min_score}"
                )
    return (not violations), violations


def main():
    parser = argparse.ArgumentParser(description="系统安全评分")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--update", action="store_true", help="写入 status.json")
    parser.add_argument(
        "--check-ontology-thresholds",
        action="store_true",
        help=(
            "V37.9.3 路线 C Step 3: 用 ontology governance_ontology.yaml 声明的 "
            "security_config 阈值自检当前 score，违反则 exit 1"
        ),
    )
    args = parser.parse_args()

    data = compute_score()

    if args.check_ontology_thresholds:
        ok, violations = check_ontology_thresholds(data)
        total_threshold, per_dim = load_ontology_thresholds()
        if total_threshold is None and not per_dim:
            print(
                "WARN: ontology 阈值未加载（PyYAML 缺失或 governance_ontology.yaml "
                "不存在）— 跳过 ontology-aware 自检",
                file=sys.stderr,
            )
            sys.exit(0)
        if ok:
            print(
                f"OK: 当前 score {data['total']}/{data['max']} 满足 ontology 所有阈值 "
                f"（总分 ≥ {total_threshold}, {len(per_dim)} 维度各自合规）"
            )
            sys.exit(0)
        else:
            print("FAIL: ontology 阈值违反：", file=sys.stderr)
            for v in violations:
                print(f"  - {v}", file=sys.stderr)
            sys.exit(1)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.update:
        # 写入 status.json
        try:
            from status_update import load_status, save_status
            status = load_status()
            score_str = f"{data['total']}/{data['max']} ({data['percentage']}%)"
            if "health" not in status:
                status["health"] = {}
            status["health"]["security_score"] = score_str
            save_status(status, updated_by="security_score",
                       audit_action="score", audit_target="security_score",
                       audit_summary=score_str)
            print(f"OK: security_score={score_str}", file=sys.stderr)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(format_report(data))


if __name__ == "__main__":
    main()

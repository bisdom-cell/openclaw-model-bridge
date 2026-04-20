#!/usr/bin/env python3
"""
governance_checker.py v2 — Ontology-Native 治理不变式执行引擎

从 governance_ontology.yaml 读取不变式和可执行检查，直接运行。
不依赖 adversarial_audit.py — 本体自身就是检查的完整来源。

检查类型：
  python_assert    — 在项目根目录执行 Python 代码，无异常 = pass
  file_contains    — 文件包含 pattern（正则）= pass
  file_not_contains — 文件不包含 pattern = pass
  env_var_exists   — bash -lc 环境变量非空 = pass（需 --full）
  command_succeeds — shell 命令 exit 0 = pass（需 --full）

用法：
  python3 ontology/governance_checker.py              # dev 模式
  python3 ontology/governance_checker.py --full        # Mac Mini
  python3 ontology/governance_checker.py --json        # JSON 输出
  python3 ontology/governance_checker.py --invariant INV-TOOL-001  # 单个
"""
import json
import os
import re
import subprocess
import sys
import textwrap
import time  # V37.9 audit-of-audit self-metric

# V37.9 C16 audit-of-audit: 记录本次 governance 执行耗时起点
_AUDIT_SESSION_START = time.time()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ONTOLOGY_DIR = os.path.dirname(os.path.abspath(__file__))

FULL_MODE = "--full" in sys.argv
JSON_MODE = "--json" in sys.argv
SINGLE = None
for i, a in enumerate(sys.argv):
    if a == "--invariant" and i + 1 < len(sys.argv):
        SINGLE = sys.argv[i + 1]

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)")
    sys.exit(1)


def _load():
    with open(os.path.join(_ONTOLOGY_DIR, "governance_ontology.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ═══════════════════════════════════════════════════════════════════════
# Check executors — one per check_type
# ═══════════════════════════════════════════════════════════════════════

def _exec_python_assert(check):
    """Execute Python code in project root context. No exception = pass."""
    code = check.get("code", "")
    old_cwd = os.getcwd()
    old_path = sys.path[:]
    try:
        os.chdir(_PROJECT_ROOT)
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)
        exec(compile(textwrap.dedent(code), f"<{check.get('name', 'check')}>", "exec"))
        return "pass", ""
    except AssertionError as e:
        return "fail", str(e)
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}"
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path


def _exec_file_contains(check):
    """Check that file contains pattern (regex)."""
    filepath = os.path.join(_PROJECT_ROOT, check.get("file", ""))
    pattern = check.get("pattern", "")
    if not os.path.exists(filepath):
        return "fail", f"文件不存在: {check.get('file')}"
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if re.search(pattern, content):
        return "pass", ""
    return "fail", f"'{pattern}' 不在 {check.get('file')} 中"


def _exec_file_not_contains(check):
    """Check that file does NOT contain pattern."""
    filepath = os.path.join(_PROJECT_ROOT, check.get("file", ""))
    pattern = check.get("pattern", "")
    if not os.path.exists(filepath):
        return "pass", ""  # file doesn't exist = pattern not in it
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if re.search(pattern, content):
        return "fail", f"'{pattern}' 不应出现在 {check.get('file')} 中但存在"
    return "pass", ""


def _exec_env_var_exists(check):
    """Check environment variable is set and non-empty via bash -lc."""
    if not FULL_MODE:
        return "skip", "需要 --full 模式"
    var = check.get("var", "")
    try:
        result = subprocess.run(
            ["bash", "-lc", f"echo ${{{var}:-}}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return "pass", ""
        return "fail", f"${var} 为空或未设置"
    except Exception as e:
        return "error", str(e)


def _exec_command_succeeds(check):
    """Run shell command, exit 0 = pass."""
    if not FULL_MODE and check.get("requires_full"):
        return "skip", "需要 --full 模式"
    cmd = check.get("command", "")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=_PROJECT_ROOT
        )
        if result.returncode == 0:
            return "pass", ""
        return "fail", f"exit {result.returncode}: {result.stderr[:200]}"
    except Exception as e:
        return "error", str(e)


EXECUTORS = {
    "python_assert": _exec_python_assert,
    "file_contains": _exec_file_contains,
    "file_not_contains": _exec_file_not_contains,
    "env_var_exists": _exec_env_var_exists,
    "command_succeeds": _exec_command_succeeds,
}


# ═══════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════

def run_invariant(inv):
    """Run all checks for one invariant. Return (status, check_results)."""
    checks = inv.get("checks", [])
    check_results = []
    worst = "pass"

    for check in checks:
        ct = check.get("check_type", "")
        if check.get("requires_full") and not FULL_MODE:
            check_results.append({"name": check.get("name"), "status": "skip", "message": "需要 --full"})
            continue

        executor = EXECUTORS.get(ct)
        if not executor:
            check_results.append({"name": check.get("name"), "status": "error", "message": f"未知 check_type: {ct}"})
            worst = "error"
            continue

        status, message = executor(check)
        check_results.append({"name": check.get("name"), "status": status, "message": message})

        if status == "fail" and worst != "error":
            worst = "fail"
        elif status == "error":
            worst = "error"

    return worst, check_results


def run_all(data):
    invariants = data.get("invariants", [])
    results = []

    for inv in invariants:
        inv_id = inv.get("id", "?")
        if SINGLE and inv_id != SINGLE:
            continue

        status, check_results = run_invariant(inv)
        results.append({
            "id": inv_id,
            "name": inv.get("name", "?"),
            "severity": inv.get("severity", "medium"),
            "declaration": inv.get("declaration", ""),
            "status": status,
            "checks": check_results,
            "total_checks": len(check_results),
            "passed_checks": sum(1 for c in check_results if c["status"] == "pass"),
            "meta_rule": inv.get("meta_rule", ""),
        })

    return results


# ═══════════════════════════════════════════════════════════════════════
# Meta-Rule Discovery Engine (Phase 0)
# 扫描结构化数据源，自动发现缺失不变式
# ═══════════════════════════════════════════════════════════════════════

def _collect_invariant_coverage(data):
    """从所有不变式的 checks 中收集已覆盖的关键词（脚本名、变量名等）。"""
    covered = set()
    for inv in data.get("invariants", []):
        for check in inv.get("checks", []):
            # 从 python_assert code 中提取引用的文件名/变量名
            code = check.get("code", "")
            pattern = check.get("pattern", "")
            file_ref = check.get("file", "")
            var_ref = check.get("var", "")
            covered.add(file_ref)
            covered.add(var_ref)
            covered.add(pattern)
            # 提取 code 中的 .sh/.py 文件引用
            for word in code.split():
                if word.endswith((".sh", ".py", ".yaml")):
                    covered.add(word.strip("\"'(),"))
    return covered


def run_meta_discovery(data):
    """Phase 0: 扫描 jobs_registry.yaml，发现缺少不变式覆盖的 job。"""
    discoveries = data.get("meta_rule_discovery", [])
    discovery_results = []

    # 收集当前不变式已覆盖的所有关键词
    covered = _collect_invariant_coverage(data)
    # 也把不变式的 checks 中所有 code 拼成一个大字符串供搜索
    all_check_code = ""
    for inv in data.get("invariants", []):
        for check in inv.get("checks", []):
            all_check_code += check.get("code", "") + " "
            all_check_code += check.get("pattern", "") + " "
            all_check_code += check.get("file", "") + " "

    for disc in discoveries:
        disc_id = disc.get("id", "?")
        name = disc.get("name", "?")
        severity = disc.get("severity_when_missing", "warn")

        if disc_id == "MRD-CRON-001":
            # 扫描 registry 中所有 enabled system job
            result = _discover_uncovered_jobs(all_check_code, severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-ENV-001":
            # 扫描 needs_api_key=true 的 job
            result = _discover_uncovered_api_keys(all_check_code, severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-NOTIFY-001":
            # 扫描 notify --topic 使用的 topic
            result = _discover_uncovered_topics(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-ERROR-001":
            # 扫描推送脚本中静默吞错误的模式
            result = _discover_silent_error_suppression(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-NOTIFY-002":
            # 效果层：检查每个 Discord 频道最近是否有推送活动
            result = _discover_silent_channels(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-LAYER-001":
            # 扫描 critical 不变式的验证深度
            result = _discover_shallow_critical(data, severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-LAYER-002":
            # V37.9: 扫描 high 不变式的验证深度（渐进强制 warn）
            result = _discover_shallow_high(data, severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-AUDIT-PERF-001":
            # V37.9 C16 audit-of-audit: 检测 governance 自身性能退化
            result = _discover_audit_performance_regression(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-LOG-STDERR-001":
            # V37.8.9: MR-11 运行时检测 — shell log 函数必须写 stderr
            result = _discover_log_stderr_violations(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-LLM-PARSER-POSITIONAL-001":
            # V37.8.9: MR-12 运行时检测 — LLM 解析器不得用位置索引
            result = _discover_llm_parser_positional_violations(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-RESERVED-FILES-001":
            # V37.8.17: MR-15 运行时检测 — 扫 OpenClaw dist 寻找未登记保留文件
            result = _discover_reserved_files_not_declared(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-ALERT-INDEPENDENCE-001":
            # V37.8.17: MR-14 运行时检测 — 监控脚本告警通道独立性
            result = _discover_alert_path_independence(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-SILENT-EXCEPT-001":
            # V37.8.18: MR-4 运行时检测 — Python 裸 except / except Exception: pass
            result = _discover_silent_except_violations(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-PUSH-ROUTE-001":
            # V37.8.18: MR-4 运行时检测 — 推送必须走 notify.sh 或白名单
            result = _discover_push_route_violations(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

    return discovery_results


def _load_registry():
    """加载 jobs_registry.yaml。"""
    registry_path = os.path.join(_PROJECT_ROOT, "jobs_registry.yaml")
    if not os.path.exists(registry_path):
        return []
    with open(registry_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("jobs", [])


def _discover_uncovered_jobs(all_check_code, severity):
    """MRD-CRON-001: 哪些 enabled system job 没有出现在任何不变式检查中？"""
    jobs = _load_registry()
    uncovered = []
    covered = []

    for job in jobs:
        if not job.get("enabled") or job.get("scheduler") != "system":
            continue
        jid = job.get("id", "?")
        entry = job.get("entry", "")
        script = os.path.basename(entry.split()[0]) if entry else ""

        # 检查 job id 或脚本名是否在任何不变式的检查代码中
        if script and (script in all_check_code or jid in all_check_code):
            covered.append(jid)
        else:
            uncovered.append(jid)

    if uncovered:
        return {
            "status": "warn",
            "severity": severity,
            "message": f"{len(uncovered)} 个 enabled job 未被不变式覆盖: {', '.join(uncovered[:5])}{'...' if len(uncovered) > 5 else ''}",
            "uncovered": uncovered,
            "covered": covered,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {len(covered)} 个 enabled system job 已被不变式覆盖",
        "uncovered": [],
        "covered": covered,
    }


def _discover_uncovered_api_keys(all_check_code, severity):
    """MRD-ENV-001: needs_api_key=true 但 preflight 未检查？"""
    jobs = _load_registry()
    needs_key = [j.get("id") for j in jobs if j.get("enabled") and j.get("needs_api_key")]

    # preflight 已检查 REMOTE_API_KEY 和 GEMINI_API_KEY，覆盖了所有 needs_api_key job
    # 检查 preflight 中是否有 needs_api_key 消费
    preflight_path = os.path.join(_PROJECT_ROOT, "preflight_check.sh")
    if os.path.exists(preflight_path):
        with open(preflight_path) as f:
            if "needs_api_key" in f.read():
                return {
                    "status": "pass",
                    "severity": severity,
                    "message": f"preflight 消费 needs_api_key 字段，覆盖 {len(needs_key)} 个 job",
                }
    return {
        "status": "warn",
        "severity": severity,
        "message": f"preflight 未消费 needs_api_key 字段，{len(needs_key)} 个 job 的 API key 需求未被验证",
    }


def _discover_uncovered_topics(severity):
    """MRD-NOTIFY-001: 脚本中用了哪些 --topic，是否都在路由表中？"""
    import glob as glob_mod
    # 从 notify.sh 提取路由表中的 topic
    notify_path = os.path.join(_PROJECT_ROOT, "notify.sh")
    known_topics = set()
    if os.path.exists(notify_path):
        with open(notify_path) as f:
            for line in f:
                # case 分支：papers) freight) alerts) daily) tech) ontology)
                m = re.match(r'\s+(\w+)\)\s+echo', line)
                if m:
                    known_topics.add(m.group(1))

    # 扫描所有 .sh 文件中 --topic 参数
    used_topics = set()
    for sh_file in glob_mod.glob(os.path.join(_PROJECT_ROOT, "**/*.sh"), recursive=True):
        if ".git" in sh_file:
            continue
        try:
            with open(sh_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = re.search(r'--topic\s+(\w+)', line)
                    if m:
                        used_topics.add(m.group(1))
        except Exception:
            pass

    unrouted = used_topics - known_topics
    if unrouted:
        return {
            "status": "warn",
            "severity": severity,
            "message": f"脚本使用了 {len(unrouted)} 个未路由的 topic: {', '.join(unrouted)}",
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {len(used_topics)} 个 topic 都在路由表中",
    }


def _discover_silent_error_suppression(severity):
    """MRD-ERROR-001: 推送脚本中 openclaw message send 是否被 >/dev/null 2>&1 吞掉？"""
    import glob as glob_mod
    violations = []

    for sh_file in glob_mod.glob(os.path.join(_PROJECT_ROOT, "**/*.sh"), recursive=True):
        if ".git" in sh_file:
            continue
        try:
            with open(sh_file, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            for i, line in enumerate(lines, 1):
                # 查找 openclaw message send ... >/dev/null 2>&1 模式
                if "message send" in line and ">/dev/null 2>&1" in line:
                    rel = os.path.relpath(sh_file, _PROJECT_ROOT)
                    violations.append(f"{rel}:{i}")
        except Exception:
            pass

    if violations:
        return {
            "status": "warn",
            "severity": severity,
            "message": f"{len(violations)} 处推送调用静默吞错误: {', '.join(violations[:5])}{'...' if len(violations) > 5 else ''}",
            "violations": violations,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": "所有推送调用的 stderr 已被正确捕获",
    }


def _discover_silent_channels(severity):
    """MRD-NOTIFY-002: 效果层 — 每个 Discord 频道是否有推送路径？

    V37.8 重写：原实现只 grep `~/*.log` 里的 `--topic X` 字符串，但只有 4/32 个
    job 使用 notify.sh（arxiv/dblp/semantic_scholar/ontology_sources），其余 28
    个 job 直接调用 `openclaw message send --channel-id "$DISCORD_CH_X"`，绕过
    notify.sh 不产生 --topic 日志字样，导致 6 个频道里有多个被误报 silent。

    新实现分两层：
      1. **Source layer（dev + full 都跑）**：扫源码，每个 topic 必须有至少
         一个 caller（notify.sh 的 `--topic T` 或直接调用的 `DISCORD_CH_<T>`）。
         这是 *在 dev 环境也能运行* 的效果层检查——回答"有没有代码路径能写到
         这个频道"。
      2. **Activity layer（仅 --full）**：Mac Mini 上额外检查日志 + notify_queue
         最近 7 天有无活动。dev 环境缺少 ~/.kb 目录直接跳过。
    """
    topics = ["papers", "freight", "alerts", "daily", "tech", "ontology"]
    # topic → 对应的 DISCORD_CH_* 环境变量名（大写）
    topic_to_env = {
        "papers": "DISCORD_CH_PAPERS",
        "freight": "DISCORD_CH_FREIGHT",
        "alerts": "DISCORD_CH_ALERTS",
        "daily": "DISCORD_CH_DAILY",
        "tech": "DISCORD_CH_TECH",
        "ontology": "DISCORD_CH_ONTOLOGY",
    }

    # ── Source layer: 扫描源码查找任何 caller ─────────────────────
    import glob as glob_mod
    source_globs = [
        os.path.join(_PROJECT_ROOT, "jobs", "*", "run_*.sh"),
        os.path.join(_PROJECT_ROOT, "*.sh"),
    ]
    source_files = []
    for g in source_globs:
        source_files.extend(glob_mod.glob(g))

    sourced_topics = set()
    callers_per_topic = {t: [] for t in topics}
    for sf in source_files:
        try:
            with open(sf, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue
        rel = os.path.relpath(sf, _PROJECT_ROOT)
        for topic in topics:
            env_var = topic_to_env[topic]
            # 两种路径都算 caller：notify.sh 路由 或 直接 openclaw send
            if f"--topic {topic}" in content or env_var in content:
                sourced_topics.add(topic)
                callers_per_topic[topic].append(rel)

    source_silent = [t for t in topics if t not in sourced_topics]

    # ── Activity layer: 仅 --full 模式 + 存在 ~/.kb 时执行 ───────
    # V37.8.1 重写：用 registry-driven topic→job log 映射替代 V37.8 的
    # notify_queue + jobs/*/cache 路径（前者只记失败重放，后者不是每个 job
    # 都有 cache/ 目录，导致 6 频道全部 7 天无活动假阳性）。
    # 正确信号源：每个 job 的真实日志文件 mtime。
    activity_silent = []
    activity_note = ""
    home = os.path.expanduser("~")
    kb_dir = os.path.join(home, ".kb")

    # topic → 贡献该频道推送的 job_id 列表（与 source layer 的 callers_per_topic 对应）
    TOPIC_JOB_MAP = {
        "papers": ["arxiv_monitor", "hf_papers", "semantic_scholar", "dblp",
                    "acl_anthology", "ai_leaders_x"],
        "freight": ["freight_watcher"],
        "alerts": ["job_watchdog", "auto_deploy"],
        "daily": ["kb_review", "kb_evening", "kb_trend", "daily_ops_report",
                   "kb_dream"],
        "tech": ["github_trending", "rss_blogs", "run_hn_fixed", "openclaw_run",
                 "run_discussions"],
        "ontology": ["ontology_sources"],
    }

    if FULL_MODE and os.path.isdir(kb_dir):
        import time
        import yaml as yaml_mod
        seven_days_ago = time.time() - 7 * 86400
        active_topics = set()

        # 从 jobs_registry.yaml 加载 job_id → log 路径映射
        registry_path = os.path.join(_PROJECT_ROOT, "jobs_registry.yaml")
        job_log_paths = {}
        try:
            with open(registry_path) as f:
                reg = yaml_mod.safe_load(f)
            for job in reg.get("jobs", []):
                if job.get("enabled") and job.get("log"):
                    log_path = job["log"].replace("~", home)
                    job_log_paths[job["id"]] = log_path
        except Exception:
            pass

        # 对每个 topic，取其映射 job 的日志文件最大 mtime
        for topic in topics:
            job_ids = TOPIC_JOB_MAP.get(topic, [])
            for jid in job_ids:
                log_path = job_log_paths.get(jid)
                if not log_path or not os.path.isfile(log_path):
                    continue
                try:
                    if os.path.getmtime(log_path) >= seven_days_ago:
                        active_topics.add(topic)
                        break  # 一个活跃 job 就够
                except Exception:
                    continue

        # 只对"源码里有 caller 但运行时没信号"的 topic 报 silent
        activity_silent = [
            t for t in topics if t in sourced_topics and t not in active_topics
        ]
        if active_topics:
            activity_note = f" (runtime active: {', '.join(sorted(active_topics))})"
    elif FULL_MODE:
        activity_note = " (runtime check skipped: ~/.kb not found)"
    else:
        activity_note = " (runtime check requires --full)"

    # ── 合并结论 ────────────────────────────────────────────────
    all_silent = source_silent + [t for t in activity_silent if t not in source_silent]
    if all_silent:
        parts_msg = []
        if source_silent:
            parts_msg.append(f"source-layer 无 caller: {', '.join(source_silent)}")
        if activity_silent:
            parts_msg.append(
                f"runtime 7 天无活动 (但 source-layer 有 caller): {', '.join(activity_silent)}"
            )
        return {
            "status": "warn",
            "severity": severity,
            "message": f"{len(all_silent)} 个频道可能沉默 — {'; '.join(parts_msg)}{activity_note}",
            "source_silent": source_silent,
            "activity_silent": activity_silent,
            "sourced_topics": sorted(sourced_topics),
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {len(topics)} 个频道均有 source-layer caller{activity_note}",
    }


def _discover_shallow_critical(data, severity):
    """MRD-LAYER-001: severity=critical 的不变式应有 ≥2 层验证深度。"""
    invariants = data.get("invariants", [])
    shallow = []
    deep = []

    for inv in invariants:
        if inv.get("severity") != "critical":
            continue
        layers = inv.get("verification_layer", [])
        inv_id = inv.get("id", "?")
        if len(layers) < 2:
            shallow.append(f"{inv_id} ({','.join(layers) if layers else 'none'})")
        else:
            deep.append(inv_id)

    if shallow:
        return {
            "status": "warn",
            "severity": severity,
            "message": f"{len(shallow)} 个 critical 不变式仅有单层验证: {', '.join(shallow[:5])}{'...' if len(shallow) > 5 else ''}",
            "shallow": shallow,
            "deep": deep,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {len(deep)} 个 critical 不变式都有 ≥2 层验证深度",
    }


def _discover_audit_performance_regression(severity):
    """MRD-AUDIT-PERF-001 (V37.9 C16): 检测 governance_checker 自身性能退化。

    读 ontology/.audit_metrics.jsonl 历史，对比当前 session wall_time
    vs 最近 5 次（不含当次）均值。>2x 退化 → warn。

    当次 wall_time 尚未写入（写入在 __main__ 结尾），所以这里读的是
    **历史最新**与**历史均值**对比——等价于"上次跑是否比之前慢"。
    对抗场景 C16 中：前 N 次都正常 → 注入 sleep 后第 N+1 次写入时慢 →
    第 N+2 次 MRD 发现退化 → warn。
    """
    metrics_path = os.path.join(_PROJECT_ROOT, "ontology", ".audit_metrics.jsonl")
    if not os.path.exists(metrics_path):
        return {
            "status": "skip",
            "severity": severity,
            "message": "首次运行，无历史 metric 对比基线（下次开始生效）",
        }
    history = []
    try:
        with open(metrics_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except IOError:
        return {
            "status": "skip",
            "severity": severity,
            "message": f"无法读 metric 文件: {metrics_path}",
        }
    if len(history) < 3:
        return {
            "status": "skip",
            "severity": severity,
            "message": f"历史 metric 只有 {len(history)} 条（需 ≥3 条才有基线）",
        }
    latest = history[-1]
    prior = history[-6:-1] if len(history) >= 6 else history[:-1]
    if not prior:
        return {
            "status": "skip",
            "severity": severity,
            "message": "无 prior 历史基线",
        }
    avg_wall = sum(m.get("wall_time_ms", 0) for m in prior) / len(prior)
    avg_checks = sum(m.get("total_checks_executed", 0) for m in prior) / len(prior)
    latest_wall = latest.get("wall_time_ms", 0)
    latest_checks = latest.get("total_checks_executed", 0)
    issues = []
    if avg_wall > 0 and latest_wall / avg_wall > 2.0:
        issues.append(
            f"wall_time {latest_wall}ms vs 历史均值 {int(avg_wall)}ms "
            f"({latest_wall/avg_wall:.1f}x 退化)"
        )
    if avg_checks > 0 and latest_checks / avg_checks < 0.7:
        issues.append(
            f"checks_executed {latest_checks} vs 历史均值 {int(avg_checks)} "
            f"({(1-latest_checks/avg_checks)*100:.0f}% 下降)"
        )
    if issues:
        return {
            "status": "warn",
            "severity": severity,
            "message": f"audit 自身性能退化嫌疑: {'; '.join(issues)}",
            "latest": latest,
            "avg_wall": avg_wall,
            "avg_checks": avg_checks,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": (
            f"audit 性能健康: wall={latest_wall}ms (avg {int(avg_wall)}ms), "
            f"checks={latest_checks} (avg {int(avg_checks)})"
        ),
    }


def _discover_shallow_high(data, severity):
    """MRD-LAYER-002 (V37.9): severity=high 的不变式应有 ≥2 层验证深度。
    渐进强制：当前有单层 high 技术债，只 warn 不 fail-stop。"""
    invariants = data.get("invariants", [])
    shallow = []
    deep = []
    for inv in invariants:
        if inv.get("severity") != "high":
            continue
        layers = inv.get("verification_layer", [])
        inv_id = inv.get("id", "?")
        if not isinstance(layers, list) or len(layers) < 2:
            shallow.append(f"{inv_id} ({','.join(layers) if layers else 'none'})")
        else:
            deep.append(inv_id)
    if shallow:
        return {
            "status": "warn",
            "severity": severity,
            "message": (
                f"{len(shallow)} 个 high 不变式仅有单层验证（渐进强制 warn）: "
                f"{', '.join(shallow[:5])}{'...' if len(shallow) > 5 else ''}"
            ),
            "shallow": shallow,
            "deep": deep,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {len(deep)} 个 high 不变式都有 ≥2 层验证深度",
    }


# ═══════════════════════════════════════════════════════════════════════
# V37.8.9 — MR-11 / MR-12 运行时检测器（把声明层升级为 CI 可检测层）
# ═══════════════════════════════════════════════════════════════════════

# shell 中被视为"诊断输出函数"的名字（必须写 stderr，不是 stdout）
_LOG_FUNC_NAMES = (
    "log", "debug", "status", "warn", "info", "notice",
    "error_log", "err_log", "log_err", "err",
)

# 单行函数定义：NAME() { ... echo ... }
_ONELINE_LOG_FUNC_RE = re.compile(
    r'^\s*(' + '|'.join(_LOG_FUNC_NAMES) + r')\s*\(\)\s*\{(.*?)\}\s*(>&2)?\s*$'
)

# 多行函数开始：NAME() {
_MULTILINE_LOG_FUNC_START_RE = re.compile(
    r'^\s*(' + '|'.join(_LOG_FUNC_NAMES) + r')\s*\(\)\s*\{\s*$'
)

# echo 行是否包含 stderr 重定向（>&2 或 1>&2）
_STDERR_REDIRECT_RE = re.compile(r'(?:^|[^0-9])(?:>&2|1>&2)(?:\s|$|;|&|\|)')


def _is_echo_to_stdout(line):
    """判断一行 echo 是否写入 stdout（无 >&2 重定向）。

    允许的 stderr 模式：
      echo "x" >&2
      echo "x" 1>&2
      echo "x"; echo "y" >&2  ← 只要行里**任一 echo 命令** redirect 到 stderr 就算合规
                                  （保守判断：只要本行含 >&2/1>&2 就豁免）

    不算违反：
      echo >> "$FILE"    → 重定向到文件（不是 stdout）
      echo 2>&1          → stderr 合并到 stdout（这是特殊情况，但
                            不在 log 函数内出现，保持 warn）
    """
    stripped = line.strip()
    # 非 echo 行不管
    if "echo" not in stripped:
        return False
    # 排除 echo >> file（重定向到文件不是 stdout 污染）
    if re.search(r'echo\s+[^|&]*>>\s*[^&]', stripped):
        return False
    # 排除 echo > file（非 stderr 重定向）
    if re.search(r'echo\s+[^|&]*(?<![&>])>\s*(?!&2)', stripped):
        return False
    # 若本行已有 stderr 重定向，合规
    if _STDERR_REDIRECT_RE.search(line):
        return False
    return True


def _scan_shell_file_log_functions(sh_file):
    """返回 [(lineno, func_name, body_preview)] 违规列表。"""
    violations = []
    try:
        with open(sh_file, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return violations

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # 单行函数定义
        m = _ONELINE_LOG_FUNC_RE.match(line)
        if m:
            func_name = m.group(1)
            body = m.group(2)
            func_redir = m.group(3)
            # 整个函数用 `} >&2` 后置重定向也合规
            if func_redir == ">&2":
                i += 1
                continue
            # 体内所有 echo 至少一个写 stdout 则违规
            # 把 body 按 `;` 切开逐条判断
            segments = [s for s in body.split(";") if s.strip()]
            for seg in segments:
                if _is_echo_to_stdout(seg):
                    violations.append((i + 1, func_name, line.strip()[:80]))
                    break
            i += 1
            continue

        # 多行函数定义开始
        m = _MULTILINE_LOG_FUNC_START_RE.match(line)
        if m:
            func_name = m.group(1)
            func_start = i
            # 查找配对的 }，只支持最简单的情况（函数体无嵌套 {}）
            body_lines = []
            j = i + 1
            found_close = False
            while j < n and j - i < 30:  # 限制扫描范围避免失控
                if re.match(r'^\s*\}\s*(>&2)?\s*$', lines[j]):
                    found_close = True
                    close_line = lines[j]
                    break
                body_lines.append(lines[j])
                j += 1
            if not found_close:
                i += 1
                continue
            # 检查关闭行是否有 `} >&2` 后置重定向
            if re.search(r'>&2', close_line):
                i = j + 1
                continue
            # 逐行检查 body
            for body_line in body_lines:
                if _is_echo_to_stdout(body_line):
                    violations.append((func_start + 1, func_name, lines[func_start].strip()[:80]))
                    break
            i = j + 1
            continue

        i += 1

    return violations


# 白名单：用户直接运行的诊断/报告工具，stdout 就是用户终端输出目标
# 这些脚本不被其他脚本用 `$()` 命令替换捕获，log→stdout 无污染风险
# MR-11 核心风险是"被命令替换捕获" — 这些脚本不会被
_LOG_STDERR_EXEMPT_BASENAMES = {
    # 用户交互式诊断工具（直接跑给人看）
    "cron_doctor.sh",
    "preflight_check.sh",
    "job_smoke_test.sh",
    "full_regression.sh",
    "smoke_test.sh",
    "quickstart.sh",
    "gameday.sh",
    # 用户交互式报告工具
    "daily_ops_report.sh",
    "health_check.sh",
    # Cron wrapper (输出进 logfile，不是 $()  捕获)
    "governance_audit_cron.sh",
    "kb_status_refresh.sh",
}


def _discover_log_stderr_violations(severity):
    """MRD-LOG-STDERR-001: 扫所有 shell 文件的 log/debug/status/warn/info
    函数定义，确认诊断输出写入 stderr（>&2），不污染 stdout 供命令替换捕获。

    触发血案：V37.8.6 Dream 自引用幻觉 — kb_dream.sh `log() { echo ...; }`
    写 stdout，`signals=$(llm_call ...)` 命令替换把 LLM 错误日志捕获进 signals
    → cache → Reduce LLM 上下文 → 编造"Hugging Face 危机"推送给用户。

    白名单豁免：用户直接运行的诊断工具（cron_doctor/preflight/...），stdout
    是用户终端的合法输出通道，不存在命令替换污染风险。
    """
    import glob as glob_mod

    violations = []
    scanned = 0
    exempted = 0
    # 扫描所有 shell 脚本（仓库 + jobs/）
    patterns = [
        os.path.join(_PROJECT_ROOT, "*.sh"),
        os.path.join(_PROJECT_ROOT, "jobs", "**", "*.sh"),
    ]
    sh_files = set()
    for patt in patterns:
        sh_files.update(glob_mod.glob(patt, recursive=True))

    for sh_file in sorted(sh_files):
        if ".git" in sh_file or "/test_" in sh_file:
            continue
        basename = os.path.basename(sh_file)
        # V37.8.9: 白名单豁免用户交互式诊断工具
        if basename in _LOG_STDERR_EXEMPT_BASENAMES:
            exempted += 1
            continue
        scanned += 1
        file_violations = _scan_shell_file_log_functions(sh_file)
        rel = os.path.relpath(sh_file, _PROJECT_ROOT)
        for lineno, func_name, preview in file_violations:
            violations.append(f"{rel}:{lineno} {func_name}() 写 stdout")

    if violations:
        return {
            "status": "warn",
            "severity": severity,
            "message": (
                f"{len(violations)} 处 shell log 函数写 stdout（应 >&2），"
                f"扫描 {scanned} 个 .sh 文件（豁免 {exempted} 个诊断工具）: "
                f"{', '.join(violations[:5])}"
                f"{'...' if len(violations) > 5 else ''}"
            ),
            "violations": violations,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {scanned} 个 shell 文件的 log/debug/status/warn 函数均写 stderr（{exempted} 个诊断工具豁免）",
    }


# MR-12 位置索引反模式：LLM 输出解析器不得用严格位置索引
# V37.8.9 refinement:
#   - i += 1 是合法的 while-loop 一行一行遍历，不视为违反
#   - 只匹配 i += N (N>=2) — 跳多行才是 MR-12 核心违反（V37.8.7 血案 i += 3）
#   - lines[i+N] 即使 N=1 也是危险（按偏移读下一行，不理解边界就级联）
_POSITIONAL_PATTERNS = [
    (re.compile(r'lines\[\s*i\s*\+\s*[0-9]+\s*\]'), "lines[i+N]"),
    (re.compile(r'^\s*i\s*\+=\s*([2-9]|[1-9][0-9]+)\s*(?:$|#)'), "i += N 步进 (N≥2)"),
    (re.compile(r'\b(content|text|response|result|output|raw|llm_content)\.split\([^)]*\)\[\s*[0-9]+\s*\]'),
     "var.split()[N]"),
]


def _discover_llm_parser_positional_violations(severity):
    """MRD-LLM-PARSER-POSITIONAL-001: 扫 LLM 调用脚本的位置索引反模式。

    触发血案：V37.8.7 ontology_sources 推送格式错位 — run_ontology_sources.sh
    用 `lines[i], lines[i+1], lines[i+2]` + `i += 3` 步进；LLM 漏一行"要点"
    就让所有后续条目 cn_title/highlight/stars 全部右移一格级联错位。

    扫描范围（从 V37.8.7 MR-12 scan 已知的 LLM 调用文件集合）：
      - jobs/**/run_*.sh  （heredoc 中的 Python）
      - kb_*.py / kb_*.sh / run_hn_fixed.sh
      - 主仓库 .py 文件中显式 LLM 相关的

    跳过：
      - 注释行（# 或 // 开头）
      - 测试断言行（assertNotIn / assertRaises 列违反模式的反例）
      - shell variable slicing `${VAR:offset}` 与本规则无关
    """
    import glob as glob_mod

    # 候选文件集合
    targets = set()
    for patt in [
        "jobs/*/run_*.sh",
        "kb_*.py",
        "kb_*.sh",
        "run_hn_fixed.sh",
    ]:
        targets.update(glob_mod.glob(os.path.join(_PROJECT_ROOT, patt)))
    # 也扫 jobs 下的其他 .py 解析模块
    targets.update(glob_mod.glob(
        os.path.join(_PROJECT_ROOT, "jobs", "**", "*.py"), recursive=True
    ))

    violations = []
    scanned = 0
    for f in sorted(targets):
        if ".git" in f or "/test_" in f or "/tests/" in f:
            continue
        # 排除 test_ 开头的文件（里面故意含反模式作为 assertNotIn 断言）
        base = os.path.basename(f)
        if base.startswith("test_"):
            continue
        scanned += 1
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        # V37.8.9: 跨行 docstring tracking — 进入 """ 或 ''' 块后跳过所有行直到关闭
        in_docstring = False
        docstring_delim = None
        for lineno, line in enumerate(lines, 1):
            stripped = line.lstrip()
            # docstring 状态机
            if in_docstring:
                if docstring_delim in line:
                    in_docstring = False
                    docstring_delim = None
                continue
            # 检测 docstring 开始（行内必须是开放，不是 """text""" 单行形式）
            for delim in ('"""', "'''"):
                if delim in line:
                    # 单行 docstring（delim 出现偶数次）？跳过不变状态
                    count = line.count(delim)
                    if count >= 2:
                        break  # 单行 """..."""，不进入多行模式
                    if count == 1:
                        in_docstring = True
                        docstring_delim = delim
                        break
            if in_docstring:
                continue
            # 跳过注释
            if stripped.startswith(("#", "//", "*")):
                continue
            # 跳过 unittest 断言行（有意列反模式作为负向检查）
            if "assertNotIn" in line or "assertRaises" in line:
                continue
            for patt, desc in _POSITIONAL_PATTERNS:
                if patt.search(line):
                    rel = os.path.relpath(f, _PROJECT_ROOT)
                    violations.append(
                        f"{rel}:{lineno} [{desc}] {stripped.rstrip()[:70]}"
                    )
                    break

    if violations:
        return {
            "status": "warn",
            "severity": severity,
            "message": (
                f"{len(violations)} 处 LLM 解析位置索引反模式，扫描 {scanned} 个脚本: "
                f"{violations[0][:100]}{'...' if len(violations) > 1 else ''}"
            ),
            "violations": violations[:20],
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {scanned} 个 LLM 调用脚本均无位置索引反模式",
    }


# V37.8.17 MR-15 MRD-RESERVED-FILES-001
# OpenClaw dist 里声明有 runtime 语义的 .md 文件名模式
_RESERVED_FILE_PATTERNS = [
    # heartbeat 源码典型模式: params.files.filter(f => f.name === "HEARTBEAT.md")
    re.compile(r'\bf\.name\s*===\s*"([A-Z][A-Z_]*\.md)"'),
    re.compile(r'\bfile\.name\s*===\s*"([A-Z][A-Z_]*\.md)"'),
    # 其他可能模式: (name === "X.md"), name: "X.md"（属性）
    re.compile(r'\bname\s*===\s*"([A-Z][A-Z_]*\.md)"'),
]

# OpenClaw dist 路径（Mac Mini 生产环境）
_OPENCLAW_DIST_PATH = "/opt/homebrew/lib/node_modules/openclaw/dist"


def _discover_reserved_files_not_declared(severity):
    """MRD-RESERVED-FILES-001: 扫 OpenClaw dist/*.js 里的 runtime 保留文件声明
    确保全部登记在 proxy_filters.RESERVED_FILE_BASENAMES。

    触发血案：V37.8.16 HEARTBEAT.md PA 自残 — OpenClaw runtime 保留文件
    当时 RESERVED_FILE_BASENAMES 不存在，audit 完全无知。MR-15 立案后，
    MRD-RESERVED-FILES-001 是第二步跃迁（从单个文件 → 扫源码自动发现）。

    dev 环境 OpenClaw dist 不存在 → skip（info 状态）
    Mac Mini --full 模式 → 真实扫描并对齐
    """
    import glob as glob_mod

    # dev 环境 skip
    if not os.path.isdir(_OPENCLAW_DIST_PATH):
        return {
            "status": "skip",
            "severity": severity,
            "message": (
                f"OpenClaw dist 不存在（dev 环境）: {_OPENCLAW_DIST_PATH} — "
                "Mac Mini --full 模式自动扫描"
            ),
        }

    # 扫 dist/*.js 提取所有保留文件声明
    js_files = glob_mod.glob(os.path.join(_OPENCLAW_DIST_PATH, "*.js"))
    upstream_reserved = set()
    for js in js_files:
        try:
            with open(js, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except (IOError, OSError):
            continue
        for patt in _RESERVED_FILE_PATTERNS:
            for m in patt.finditer(content):
                upstream_reserved.add(m.group(1))

    # 读 proxy_filters.RESERVED_FILE_BASENAMES
    proxy_path = os.path.join(_PROJECT_ROOT, "proxy_filters.py")
    declared = set()
    if os.path.exists(proxy_path):
        try:
            with open(proxy_path, "r", encoding="utf-8") as f:
                src = f.read()
            # 提取 frozenset 字面量里的字符串
            frozen_match = re.search(
                r"RESERVED_FILE_BASENAMES\s*=\s*frozenset\s*\(\s*\[(.*?)\]\s*\)",
                src, re.DOTALL)
            if frozen_match:
                for s in re.findall(r'"([^"]+\.md)"', frozen_match.group(1)):
                    declared.add(s)
        except (IOError, OSError):
            pass

    missing = upstream_reserved - declared
    if missing:
        return {
            "status": "warn",
            "severity": severity,
            "message": (
                f"{len(missing)} 个 OpenClaw runtime 保留文件未登记到 "
                f"RESERVED_FILE_BASENAMES: {sorted(missing)}（扫 {len(js_files)} 个 "
                f".js 文件，已登记 {len(declared)} 个）"
            ),
            "missing": sorted(missing),
            "declared": sorted(declared),
            "upstream": sorted(upstream_reserved),
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": (
            f"所有 {len(upstream_reserved)} 个 OpenClaw runtime 保留文件均已登记 "
            f"({sorted(upstream_reserved)[:3]}{'...' if len(upstream_reserved) > 3 else ''})"
        ),
    }


# V37.8.17 MR-14 MRD-ALERT-INDEPENDENCE-001
# 监控脚本 → 禁止的告警通道（告警链不得依赖失效主体自身）
_ALERT_INDEPENDENCE_RULES = [
    # (脚本 basename pattern, 禁止的通道关键字列表, 描述)
    (re.compile(r"^wa_"),
     ["--channel whatsapp", "--channel whatsapp_only"],
     "wa_* 脚本监控 WhatsApp，告警不得走 WhatsApp（Gateway 宕则 WA 不通）"),
    (re.compile(r"whatsapp.*keepalive|whatsapp.*watchdog", re.IGNORECASE),
     ["--channel whatsapp", "--channel whatsapp_only"],
     "WhatsApp 监控脚本告警不得走 WhatsApp"),
]

# 告警关键字锚点（在这些关键字附近找通道选择）
_ALERT_KEYWORDS = ["alert", "ESCALAT", "WARN_COUNT", "告警", "notify"]


def _discover_alert_path_independence(severity):
    """MRD-ALERT-INDEPENDENCE-001: 扫监控脚本确认告警路径独立于被监控对象。

    触发血案：V37.8.13 Gateway 宕 9h — wa_keepalive 本应告警但告警路径只写日志
    不推送（原实现）。修复后走 Discord。MR-14 立案后，MRD-ALERT-INDEPENDENCE-001
    是第二步跃迁（从 wa_keepalive 一个脚本 → 扫所有监控脚本）。

    白名单豁免：探测路径可以走被监控通道（wa_keepalive 探测 Gateway 必须走 WA
    才能验证存活），只检测"告警"关键字附近的通道选择。
    """
    import glob as glob_mod

    violations = []
    scanned = 0
    # 扫监控脚本
    patterns = [
        os.path.join(_PROJECT_ROOT, "wa_*.sh"),
        os.path.join(_PROJECT_ROOT, "*keepalive*.sh"),
        os.path.join(_PROJECT_ROOT, "*watchdog*.sh"),
    ]
    monitor_files = set()
    for patt in patterns:
        monitor_files.update(glob_mod.glob(patt))

    for sh_file in sorted(monitor_files):
        basename = os.path.basename(sh_file)
        # 匹配适用规则
        applicable_rules = []
        for name_patt, forbidden_channels, description in _ALERT_INDEPENDENCE_RULES:
            if name_patt.search(basename):
                applicable_rules.append((forbidden_channels, description))
        if not applicable_rules:
            continue
        scanned += 1

        try:
            with open(sh_file, "r", encoding="utf-8") as f:
                content = f.read()
        except (IOError, OSError):
            continue

        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            # 只检测告警关键字附近的通道选择
            line_context_hit = any(kw.lower() in line.lower() for kw in _ALERT_KEYWORDS)
            if not line_context_hit:
                continue
            for forbidden_channels, description in applicable_rules:
                for forbidden in forbidden_channels:
                    if forbidden in line:
                        rel = os.path.relpath(sh_file, _PROJECT_ROOT)
                        violations.append(
                            f"{rel}:{lineno} {description} — 出现 '{forbidden}'"
                        )

    if violations:
        return {
            "status": "warn",
            "severity": severity,
            "message": (
                f"{len(violations)} 处监控脚本告警通道依赖被监控对象，"
                f"扫描 {scanned} 个监控脚本: {violations[0][:100]}"
                f"{'...' if len(violations) > 1 else ''}"
            ),
            "violations": violations,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {scanned} 个监控脚本的告警路径均独立于被监控对象",
    }


# V37.8.18 MR-4 MRD-SILENT-EXCEPT-001
# Python 裸 except / except Exception: pass 反模式
# 白名单：ImportError pass / test_*.py

def _discover_silent_except_violations(severity):
    """MRD-SILENT-EXCEPT-001: 扫所有 .py 文件的裸 except / silent pass 反模式。

    触发血案：Route B C11 (2026-04-20) — adapter.py 注入 `try: ... except: pass`
    静默吞错模式后 audit 完全抓不到。MR-4 silent-failure 源头之一是裸 except
    pass，但历史只修具体案例，从未普适化。

    检测模式（AST 扫描）：
      1. `except:` (bare except) → 违反
      2. `except Exception:` + 函数体只有 pass → 违反
      3. `except BaseException:` + pass → 违反

    允许（不违反）：
      - `except SpecificError:` 有具体类型
      - `except Exception: log.error(...)` 有日志/处理
      - `except Exception: return fallback` 有降级
      - `except: raise` 虽然裸但 re-raise
      - `except ImportError: pass` 可选依赖探测

    跳过文件：test_*.py / __pycache__ / .git / adversarial_chaos_audit.py 本身
    （后者含 CHAOS_MUTATED 反模式示例）
    """
    import ast
    import glob as glob_mod

    violations = []
    scanned = 0
    # 扫项目里所有 .py（排除常见噪音）
    py_files = glob_mod.glob(os.path.join(_PROJECT_ROOT, "**", "*.py"), recursive=True)

    for f in sorted(py_files):
        rel = os.path.relpath(f, _PROJECT_ROOT)
        if any(x in rel for x in [".git/", "__pycache__/", "/test_", "test_"]):
            continue
        # 跳过对抗审计脚本自身（含 CHAOS_MUTATED 示例）
        if rel.endswith("adversarial_chaos_audit.py"):
            continue
        scanned += 1
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=f)
        except (SyntaxError, IOError, OSError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            exc_type = node.type
            body = node.body
            lineno = node.lineno

            # 判定类型名（或 None 为裸 except）
            type_name = None
            if exc_type is None:
                type_name = "bare_except"
            elif isinstance(exc_type, ast.Name):
                type_name = exc_type.id

            # 允许 ImportError pass（可选依赖探测）
            if type_name == "ImportError":
                continue

            # 判定 body 是否仅为 pass
            body_is_just_pass = (
                len(body) == 1 and isinstance(body[0], ast.Pass)
            )
            # 判定是否含 raise（re-raise 允许）
            has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(node))

            is_violation = False
            if type_name == "bare_except":
                # 裸 except 除非有 raise
                if not has_raise:
                    is_violation = True
            elif type_name in ("Exception", "BaseException"):
                # 宽泛捕获 + body 只有 pass
                if body_is_just_pass:
                    is_violation = True

            if is_violation:
                violations.append(f"{rel}:{lineno} except {type_name or 'bare'}")

    if violations:
        return {
            "status": "warn",
            "severity": severity,
            "message": (
                f"{len(violations)} 处 Python 裸 except/silent pass 反模式，"
                f"扫描 {scanned} 个 .py 文件: {', '.join(violations[:5])}"
                f"{'...' if len(violations) > 5 else ''}"
            ),
            "violations": violations,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": f"所有 {scanned} 个 .py 文件无裸 except/silent pass 反模式",
    }


# V37.8.18 MR-4 MRD-PUSH-ROUTE-001
# 推送白名单：允许直接 openclaw message send 的脚本 basename
# 白名单原则：主仓库级合法推送入口（路由层 / 监控告警 / 周期性汇总）
# 新增脚本推送必须走 notify.sh，否则加入白名单需 review
_PUSH_ROUTE_WHITELIST = {
    "notify.sh",           # 路由层自身
    "run_hn_fixed.sh",     # 历史合法直推（V27 前已稳定）
    "wa_keepalive.sh",     # 告警升级走 openclaw 直发 Discord
    "job_watchdog.sh",     # watchdog 告警直发
    "auto_deploy.sh",      # quiet_alert 实现层
    "daily_ops_report.sh", # 日报直推
    "health_check.sh",     # 周报直推
    "kb_status_refresh.sh",  # 状态刷新
    "cron_canary.sh",      # cron 心跳金丝雀（不推消息但保险）
    "preflight_check.sh",  # 体检推送（含白名单豁免）
    "smoke_test.sh",       # 测试
    "quickstart.sh",       # 一键启动
    "gameday.sh",          # 故障演练
    "kb_evening.sh",       # KB 晚间整理推送
    "kb_inject.sh",        # 每日 KB 摘要推送
    "kb_review.sh",        # 周度回顾推送
    "kb_dream.sh",         # Dream 跨域关联推送
    "check_upgrade.sh",    # OpenClaw 升级 SOP 推送
    "diagnose.sh",         # 系统诊断推送
    "upgrade_openclaw.sh", # Gateway 升级推送
    "restart.sh",          # 服务重启确认
    "openclaw_backup.sh",  # 备份结果推送
}


def _discover_push_route_violations(severity):
    """MRD-PUSH-ROUTE-001: 扫所有 .sh 文件的 `openclaw message send` 直调用。
    白名单外的脚本直接调用 = 绕过 notify.sh 治理层 = warn.

    触发血案：Route B C15 (2026-04-20) — 新建 chaos_rogue_pusher.sh 直接
    调 `openclaw message send --channel whatsapp` 绕过 notify.sh，audit 当时
    无任何检测手段。本 MRD 扫所有 shell 脚本的推送调用，非白名单即 warn。
    """
    import glob as glob_mod

    violations = []
    scanned = 0

    # 扫所有 .sh（仓库 + jobs/）
    patterns = [
        os.path.join(_PROJECT_ROOT, "*.sh"),
        os.path.join(_PROJECT_ROOT, "jobs", "**", "*.sh"),
    ]
    sh_files = set()
    for patt in patterns:
        sh_files.update(glob_mod.glob(patt, recursive=True))

    for sh_file in sorted(sh_files):
        if ".git" in sh_file:
            continue
        basename = os.path.basename(sh_file)
        if basename.startswith("test_"):
            continue
        # jobs/**/*.sh 是合法 job 推送实现（走 topic 模式），不在白名单但
        # 仍被 MRD-NOTIFY-001 按 topic 覆盖；此处只检测"新增 rogue 推送"
        normalized_path = sh_file.replace("\\", "/")
        if "/jobs/" in normalized_path:
            continue
        scanned += 1

        if basename in _PUSH_ROUTE_WHITELIST:
            continue

        try:
            with open(sh_file, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except (IOError, OSError):
            continue

        # 扫非注释行的 `openclaw message send` 调用
        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "openclaw message send" in line:
                rel = os.path.relpath(sh_file, _PROJECT_ROOT)
                violations.append(f"{rel}:{lineno} bypass notify.sh")

    if violations:
        return {
            "status": "warn",
            "severity": severity,
            "message": (
                f"{len(violations)} 处 rogue 推送绕过 notify.sh，"
                f"扫描 {scanned} 个非白名单 .sh: {', '.join(violations[:5])}"
                f"{'...' if len(violations) > 5 else ''}"
            ),
            "violations": violations,
        }
    return {
        "status": "pass",
        "severity": severity,
        "message": (
            f"所有 {scanned} 个非白名单 .sh 无绕过 notify.sh 的推送调用"
            f"（白名单 {len(_PUSH_ROUTE_WHITELIST)} 个脚本豁免）"
        ),
    }


def print_results(results, json_mode=None):
    """Print governance check results.

    V37.7: `json_mode` parameter allows tests (INV-GOV-001 check 2) to
    specify output format without mutating the module-level JSON_MODE
    global. Defaults to the global for backwards compat / CLI use.
    """
    if json_mode is None:
        json_mode = JSON_MODE
    if json_mode:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    sev_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡"}
    status_icons = {"pass": "✅", "fail": "❌", "skip": "⏭ ", "error": "💥"}

    print("=" * 70)
    print("  GOVERNANCE CHECKER v2 — Ontology-Native 执行引擎")
    print(f"  模式: {'FULL (Mac Mini)' if FULL_MODE else 'DEV (repo only)'}")
    print("=" * 70)

    total_checks = 0
    passed_checks = 0
    failed_invs = 0

    for r in results:
        icon = status_icons.get(r["status"], "?")
        sev = sev_icons.get(r["severity"], "")
        print(f"\n  {icon} {sev} [{r['id']}] {r['name']}")
        print(f"     声明: {r['declaration'][:70]}")

        for c in r["checks"]:
            ci = status_icons.get(c["status"], "?")
            total_checks += 1
            if c["status"] == "pass":
                passed_checks += 1
                print(f"       {ci} {c['name']}")
            elif c["status"] == "skip":
                print(f"       {ci} {c['name']} ({c['message']})")
            else:
                print(f"       {ci} {c['name']}")
                if c["message"]:
                    print(f"          → {c['message']}")

        # Count both hard failures (fail) and execution errors (error) as
        # not-passing. Previously `error` status was silently ignored, so a
        # broken check (exception in Python code) looked identical to a pass.
        if r["status"] in ("fail", "error"):
            failed_invs += 1

    # Summary
    # V37.8.9: mr_used 现在同时包含 invariants 和 meta_rule_discovery 引用的元规则
    # （MRD 也是"执行中的元规则检测器"，应该被计入 "used" 集合）
    mr_used = set(r["meta_rule"] for r in results if r["meta_rule"])
    try:
        for mrd in _load().get("meta_rule_discovery", []):
            mr = mrd.get("meta_rule")
            if mr:
                mr_used.add(mr)
    except Exception:
        pass
    skipped = sum(1 for r in results for c in r["checks"] if c["status"] == "skip")
    executed = total_checks - skipped
    errored_invs = sum(1 for r in results if r["status"] == "error")
    hard_fail_invs = failed_invs - errored_invs
    # V37.7: pull meta_rules total from audit_metadata instead of hardcoding
    try:
        mr_total = _load().get("audit_metadata", {}).get("meta_rules", len(mr_used))
    except Exception:
        mr_total = len(mr_used)

    print()
    print("─" * 70)
    print(f"  不变式: {len(results)} | 检查: {executed} 执行, {skipped} 跳过")
    print(f"  通过: {passed_checks}/{executed} checks | 元规则: {len(mr_used)}/{mr_total}")

    if failed_invs:
        if errored_invs and hard_fail_invs:
            print(f"\n  ❌ {hard_fail_invs} 个不变式被违反, 💥 {errored_invs} 个检查执行出错")
        elif errored_invs:
            print(f"\n  💥 {errored_invs} 个不变式检查执行出错")
        else:
            print(f"\n  ❌ {failed_invs} 个不变式被违反")
    else:
        print(f"\n  ✅ 所有不变式成立")
    print("=" * 70)

    return failed_invs


def print_discovery(discovery_results):
    """Print meta-rule discovery results."""
    if not discovery_results:
        return

    status_icons = {"pass": "✅", "warn": "⚠️", "fail": "❌"}

    if not JSON_MODE:
        print()
        print("─" * 70)
        print("  META-RULE DISCOVERY (Phase 0) — 自动发现缺失不变式")
        print("─" * 70)

        for d in discovery_results:
            icon = status_icons.get(d["status"], "?")
            print(f"  {icon} [{d['id']}] {d['name']}")
            print(f"     {d['message']}")

            if d.get("uncovered"):
                for u in d["uncovered"][:8]:
                    print(f"       📌 {u} — 建议新增不变式")


def _write_audit_metrics(results, discovery):
    """V37.9 C16 audit-of-audit: 记录本次 governance 执行的 metric 到历史文件，
    供 MRD-AUDIT-PERF-001 下次 run 时对比识别性能退化。

    历史文件: ontology/.audit_metrics.jsonl (每行 1 次 run, 最多保留最近 20 条)
    字段:
      timestamp, wall_time_ms, total_invariants, total_checks_executed,
      total_checks_skipped, pass_count, fail_count, error_count
    """
    try:
        wall_time_ms = int((time.time() - _AUDIT_SESSION_START) * 1000)
        pass_count = sum(1 for r in results if r.get("status") == "pass")
        fail_count = sum(1 for r in results if r.get("status") == "fail")
        error_count = sum(1 for r in results if r.get("status") == "error")
        checks_exec = sum(r.get("total_checks", 0) for r in results)
        checks_passed = sum(r.get("passed_checks", 0) for r in results)
        checks_skipped = checks_exec - checks_passed  # 近似（含 fail+error+skip）
        metric = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "wall_time_ms": wall_time_ms,
            "total_invariants": len(results),
            "total_checks_executed": checks_exec,
            "total_checks_skipped": checks_skipped,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "error_count": error_count,
            "discovery_count": len(discovery),
        }
        metrics_path = os.path.join(_PROJECT_ROOT, "ontology", ".audit_metrics.jsonl")
        # 读历史
        history = []
        if os.path.exists(metrics_path):
            try:
                with open(metrics_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                history.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except IOError:
                pass
        # 保留最近 19 + 本次 = 20 条
        history = history[-19:]
        history.append(metric)
        # 原子写
        tmp = metrics_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for m in history:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        os.replace(tmp, metrics_path)
    except Exception:
        # V37.9: 静默 fail — audit self-metric 失败不能阻塞治理主流程
        pass


if __name__ == "__main__":
    data = _load()
    results = run_all(data)
    discovery = run_meta_discovery(data)
    fails = print_results(results)
    print_discovery(discovery)

    # V37.9 C16 audit-of-audit: 写本次 run 的 metric 供下次对比
    _write_audit_metrics(results, discovery)

    if JSON_MODE:
        combined = {"invariants": results, "discovery": discovery}
        print(json.dumps(combined, indent=2, ensure_ascii=False))

    sys.exit(1 if fails else 0)

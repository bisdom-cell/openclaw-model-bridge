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

        elif disc_id == "MRD-LOG-STDERR-001":
            # V37.8.9: MR-11 运行时检测 — shell log 函数必须写 stderr
            result = _discover_log_stderr_violations(severity)
            discovery_results.append({"id": disc_id, "name": name, **result})

        elif disc_id == "MRD-LLM-PARSER-POSITIONAL-001":
            # V37.8.9: MR-12 运行时检测 — LLM 解析器不得用位置索引
            result = _discover_llm_parser_positional_violations(severity)
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


if __name__ == "__main__":
    data = _load()
    results = run_all(data)
    discovery = run_meta_discovery(data)
    fails = print_results(results)
    print_discovery(discovery)

    if JSON_MODE:
        combined = {"invariants": results, "discovery": discovery}
        print(json.dumps(combined, indent=2, ensure_ascii=False))

    sys.exit(1 if fails else 0)

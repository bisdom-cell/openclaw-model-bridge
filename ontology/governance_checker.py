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
        script = os.path.basename(entry) if entry else ""

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
    activity_silent = []
    activity_note = ""
    home = os.path.expanduser("~")
    kb_dir = os.path.join(home, ".kb")
    queue_dir = os.path.join(kb_dir, "notify_queue")

    if FULL_MODE and os.path.isdir(kb_dir):
        import time
        seven_days_ago = time.time() - 7 * 86400
        active_topics = set()
        # 用 mtime 作信号：notify_queue/*.json 最近 7 天内被触碰 = 该 topic 有活动
        # （成功路径也会经过 queue 短暂写入/立刻删除，失败路径会留下）
        queue_activity_topics = set()
        if os.path.isdir(queue_dir):
            for qf in os.listdir(queue_dir):
                qfp = os.path.join(queue_dir, qf)
                try:
                    if os.path.getmtime(qfp) < seven_days_ago:
                        continue
                    with open(qfp) as f:
                        import json as json_mod
                        data = json_mod.load(f)
                    t = data.get("topic", "")
                    if t:
                        queue_activity_topics.add(t)
                except Exception:
                    pass
        active_topics |= queue_activity_topics

        # jobs/*/cache/*.log 的 mtime 说明该 job 最近跑过；若该 job 的脚本是
        # 某个 topic 的 caller，就把这个 topic 标记为 active
        job_log_glob = os.path.join(home, "jobs", "*", "cache", "*.log")
        for lf in glob_mod.glob(job_log_glob):
            try:
                if os.path.getmtime(lf) < seven_days_ago:
                    continue
            except Exception:
                continue
            # 从路径回推 job name → 匹配 source-layer 的 callers_per_topic
            parts = lf.split(os.sep)
            try:
                idx = parts.index("jobs")
                job_name = parts[idx + 1]
            except (ValueError, IndexError):
                continue
            for topic, callers in callers_per_topic.items():
                if any(job_name in c for c in callers):
                    active_topics.add(topic)

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
    mr_used = set(r["meta_rule"] for r in results if r["meta_rule"])
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

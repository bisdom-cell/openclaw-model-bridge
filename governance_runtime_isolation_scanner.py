#!/usr/bin/env python3
"""governance_runtime_isolation_scanner.py — V37.9.159 INV-GOV-RUNTIME-ISOLATION-001

血案谱系 (MR-9 测试污染生产 / MR-23 audit-observes-never-mutates, 已 4 次演出):
  - V37.9.110: governance INV-RETRY-001 runtime check 跑 test_movespeed_rsync_helper.py
    子进程, 失败路径测试调真 helper 写真 ~/.kb/movespeed_incidents.jsonl (caller=测试桩
    灌爆假告警) — 因 _run_helper 未设 MOVESPEED_INCIDENT_FILE 隔离.
  - V37.9.157: governance INV-CRON-MONITOR-001 + INV-REVIEW-001 runtime check 跑
    test_cron_monitor_fatal_handler.py / kb_review.sh 子进程调真 4.27 openclaw (冷调用
    77s 劫持 + 真发 [SYSTEM_ALERT] 到用户 WhatsApp) — 因未 stub OPENCLAW.
  - V37.9.158-hotfix: governance INV-CONVERGENCE-CRON-001 runtime check 跑 test_convergence.py
    子进程, test_explicit_dry_run_overrides_env 传 dry_run=False 走 real-apply → 真
    crontab_safe.sh add 重加 auto_deploy 双行 — 因未 HOME-redirect/mock subprocess.

核心契约 (MR-23 audit-observes-never-mutates): 治理审计的 subprocess 绝不 mutate 生产 state.
每个被治理 runtime python_assert check 跑的测试文件, 凡有 real-apply / incident-write /
真调-openclaw 的测试, 必须携带隔离信号.

本 scanner 把 V37.9.110/157/158-hotfix 的 N 个窄 per-bug forward-scan 收敛为一个
**自动发现 + 跨文件** 的机器守卫 (一物一形 + 缩 dev-production 接缝, 日落法北极星 原则 #34).
靠记忆"为每个新治理-runtime-test 加窄 scan" 已 4 次失败 → 机器化此契约.

Stage 1 (自动发现): 从 governance_ontology.yaml 提取所有被 runtime python_assert
  subprocess 跑的测试文件 (subprocess.run([..., "test_X.py"]) / -m unittest test_X /
  -m unittest 列表里的 "test_X.TestClass").
Stage 2 (逐文件扫): 对每个发现的测试文件, 3 个血案验证的 detector:
  D1 (crontab real-apply, 方法级): 测试方法含 dry_run=False 真 kwarg (排除 apply_dry_run)
     但该方法 + 所属类 setUp 都无隔离信号 (HOME-redirect / mock subprocess.run /
     mock os.path.exists / _set_subprocess_mock / CONVERGENCE_DRY_RUN). 镜像 V37.9.158-hotfix.
  D2 (~/.kb incident write, 文件级): 文件以 subprocess 跑 movespeed_rsync_helper.sh /
     movespeed_incident_capture.sh 但全文无 MOVESPEED_INCIDENT_FILE 重定向. 镜像 V37.9.110.
  D3 (真调 openclaw → WhatsApp, 文件级): 文件以 subprocess 跑 openclaw-invoking 脚本
     (动态扫 *.sh 得 openclaw/notify 调用集) 或直调 openclaw/message send, 但全文无
     OPENCLAW stub (OPENCLAW_BIN / OPENCLAW=stub / _stub_env) 也无 HOME-redirect. 镜像 V37.9.157.

FAIL-CLOSE 契约: 任一 violation 必须 exit 1.

豁免:
  - scanner 自身 + 它的单测文件 (test_governance_runtime_isolation_scanner.py) 不扫
  - 注释行 (逐行剥 # 之后) 不算 danger
  - 只扫被治理 runtime check 真 subprocess 跑的测试文件 (Stage 1 发现的精确集)

Usage:
  python3 governance_runtime_isolation_scanner.py                # 全量扫 (FAIL-CLOSE)
  python3 governance_runtime_isolation_scanner.py --file X.py    # 扫单文件 (用全集 detector)
  python3 governance_runtime_isolation_scanner.py --list-discovered  # 列出发现的治理-runtime 测试集
  python3 governance_runtime_isolation_scanner.py --json         # JSON 输出
"""
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
GOV_YAML = REPO / "ontology" / "governance_ontology.yaml"

# scanner 自身 + 单测 不扫 (它们引用 danger/isolation 字面量作守卫, 非真违反)
_SELF_EXEMPT = {
    "governance_runtime_isolation_scanner.py",
    "test_governance_runtime_isolation_scanner.py",
}

# ── Stage 1: 自动发现治理 runtime check 跑的测试文件 ──
# Pattern A: subprocess 跑 "test_X.py" 字面量 (含 os.path.join(..., "test_X.py"))
_DISC_PY_FILE = re.compile(r'["\'](test_[a-z0-9_]+)\.py["\']')
# Pattern B: -m unittest test_X (模块名, 无 .py)
_DISC_UNITTEST_MODULE = re.compile(r'unittest["\',\s]+["\'](test_[a-z0-9_]+)["\']')
# Pattern C: -m unittest 列表里的 "test_X.TestClass" (取模块名)
_DISC_UNITTEST_CLASS = re.compile(r'["\'](test_[a-z0-9_]+)\.[A-Za-z_][A-Za-z0-9_]*["\']')
# Pattern D: 动态 class append "test_X." + _c (尾点 + 闭引号, V37.9.124 INV-OBSERVER 形态)
_DISC_UNITTEST_APPEND = re.compile(r'["\'](test_[a-z0-9_]+)\.["\']')
# 块内执行上下文判定 (该 check 真 subprocess 跑测试, 非 file_contains 引用测试名)
_EXEC_CONTEXT = re.compile(r'subprocess\.(run|Popen|call|check_call|check_output)\(|-m["\',\s]+unittest')


def discover_governance_runtime_test_files(yaml_text, repo_root):
    """从 governance_ontology.yaml 提取被 runtime python_assert subprocess 跑的测试文件.

    block-based: 按 check 条目 (- name:) 切块, 只在 python_assert + 有执行上下文
    (subprocess / -m unittest) 的块里抓测试名. 避免误抓 file_contains check 里的测试名引用,
    同时双向覆盖 'test_file = os.path.join(...)' 在 subprocess.run 之前 + 动态 class append.
    返回存在于 repo 的 test_*.py 文件 Path 集 (按文件名排序).
    """
    repo_root = Path(repo_root)
    names = set()
    # 按 check 条目切块 (任意缩进的 '- name:')
    blocks = re.split(r"\n\s+- name:", yaml_text)
    for blk in blocks:
        if "python_assert" not in blk:
            continue
        if not _EXEC_CONTEXT.search(blk):
            continue
        for rx in (_DISC_PY_FILE, _DISC_UNITTEST_MODULE, _DISC_UNITTEST_CLASS, _DISC_UNITTEST_APPEND):
            for hit in rx.findall(blk):
                names.add(hit)
    found = set()
    for n in names:
        p = repo_root / f"{n}.py"
        if p.is_file() and p.name not in _SELF_EXEMPT:
            found.add(p)
    return sorted(found, key=lambda p: p.name)


# ── Stage 2 detector 信号 ──
# D1 (crontab real-apply) 隔离信号 — 镜像 test_v37_9_157 _ISOLATION_SIGNALS + setUp 扩展
_D1_ISOLATION_SIGNALS = (
    'os.environ["HOME"]',                                # HOME 重定向 (real helper 路径→tempdir)
    "subprocess.run =",                                  # 直接 mock subprocess (含 cv.subprocess.run =)
    "_set_subprocess_mock",                              # TestApplyMachineSyncReal helper mock
    "_never_run",                                        # mock subprocess 防御函数
    'mock.patch("subprocess.run"',                       # 上下文 mock subprocess
    "mock.patch('subprocess.run'",                       # 单引号变体
    'mock.patch("os.path.exists", return_value=False)',  # helper-missing 短路 → 永不到 subprocess
    "CONVERGENCE_DRY_RUN",                               # 强制 dry-run env (setUp/方法内)
)
# D1 danger: dry_run=False 真 kwarg (负向 lookbehind 排除 apply_dry_run=False)
_D1_DANGER = re.compile(r"(?<![A-Za-z_])dry_run\s*=\s*False")
# D1 context gate (V37.9.198): dry_run=False 只在 crontab-apply 上下文才是 real-apply 危险。
# convergence/crontab 测试的 dry_run=False 会真改 crontab (V37.9.158-hotfix 血案); 但别的
# dry_run (如 daily_observer.run() 的 dry_run = 跳过 LLM) 与 crontab 无关 → 文件无 crontab
# 上下文则 D1 不适用 (精度修复: 消除非-crontab dry_run 的误报, 不弱化 convergence 守卫)。
_D1_CRONTAB_CONTEXT = ("crontab", "convergence", "machine_sync", "verify_convergence")

# D2 (~/.kb incident write) — 跑 movespeed helper 脚本即写 ~/.kb/movespeed_incidents.jsonl
_D2_HELPER_SCRIPTS = ("movespeed_rsync_helper.sh", "movespeed_incident_capture.sh")
_D2_ISOLATION = "MOVESPEED_INCIDENT_FILE"

# D3 (真调 openclaw) 隔离信号
_D3_ISOLATION_SIGNALS = (
    "OPENCLAW_BIN",          # stub openclaw 三档 fallback 之首
    '"OPENCLAW":',           # OPENCLAW env stub (dict 形式)
    "OPENCLAW=",             # OPENCLAW env stub (env 字符串形式)
    "_stub_env",             # test_cron_monitor 受控环境 helper
    'os.environ["HOME"]',    # HOME 重定向也能拦真 openclaw 路径
)
_SUBPROCESS_ATTRS = ("run", "Popen", "call", "check_call", "check_output")


_SHELL_INTERP = {"bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/bin/zsh", "/usr/bin/bash"}


def _build_script_vars(tree, target_scripts):
    """var → target 脚本名 映射. 仅认"单一脚本路径"赋值, 跳过 collection (set/list/dict/tuple).

    SCRIPT_PATH = os.path.join(REPO, "health_check.sh")  → 认 (Call)
    required = {"job_watchdog.sh", ...}                   → 跳 (Set, 数据集非路径)
    """
    script_vars = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if isinstance(val, (ast.Set, ast.List, ast.Dict, ast.Tuple)):
            continue  # 脚本名集合是数据, 非可执行路径
        try:
            seg = ast.unparse(val)
        except Exception:
            continue
        hit = next((s for s in target_scripts if s in seg), None)
        if hit:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    script_vars[tgt.id] = hit
    return script_vars


def _elt_script(elt, script_vars, target_scripts):
    """解析单个 cmd 元素 → 它引用的 target 脚本名 (Constant 字面量 / Name 变量 / str(Name) 包装)."""
    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
        return next((t for t in target_scripts if t in elt.value), None)
    if isinstance(elt, ast.Name):
        return script_vars.get(elt.id)
    # str(VAR) 包装
    if (isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name) and elt.func.id == "str"
            and elt.args and isinstance(elt.args[0], ast.Name)):
        return script_vars.get(elt.args[0].id)
    return None


def _find_executed_scripts(tree, target_scripts):
    """AST: 找 subprocess Call **执行** (非 bash -n / 非 python 扫描) target_scripts 里的脚本.

    精确区分"执行生产脚本" vs "扫描/语法检查/数据引用":
      - subprocess.run(["bash", SCRIPT_VAR])       shell 执行 (SCRIPT_VAR→target) → 命中
      - subprocess.run([SCRIPT_VAR, ...])          直接执行脚本 (首元素即 target) → 命中
      - subprocess.run(["bash", "-n", X])          语法检查 (-n) → 豁免
      - subprocess.run([sys.executable,"x.py", "job_watchdog.sh"]) python 跑 .py, .sh 是数据 arg → 豁免
      - required = {"job_watchdog.sh"} 数据集 / read(SCRIPT) grep → 豁免

    返回被执行的 target 脚本名集.
    """
    script_vars = _build_script_vars(tree, target_scripts)
    executed = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in _SUBPROCESS_ATTRS
                and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        elts = node.args[0].elts
        if not elts:
            continue
        str_elts = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        # 首元素 = executable. python/sys.executable → 跑 .py (脚本名是数据 arg) → 豁免整个调用
        first = elts[0]
        is_python = (
            (isinstance(first, ast.Attribute) and first.attr == "executable")  # sys.executable
            or (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and first.value in ("python", "python3"))
        )
        if is_python:
            continue
        first_is_shell = isinstance(first, ast.Constant) and first.value in _SHELL_INTERP
        if first_is_shell:
            if "-n" in str_elts:   # bash -n 语法检查, 不执行
                continue
            for e in elts[1:]:     # shell 后续元素里的 target 脚本即被执行
                sc = _elt_script(e, script_vars, target_scripts)
                if sc:
                    executed.add(sc)
        else:
            # 首元素直接是脚本 (subprocess.run([SCRIPT_VAR, ...]) 直接可执行)
            sc = _elt_script(first, script_vars, target_scripts)
            if sc:
                executed.add(sc)
    return executed


def compute_openclaw_invoking_scripts(repo_root):
    """动态扫 *.sh 得"调 openclaw/notify"的脚本集 (自维护, 不硬编码).

    跑这些脚本的 subprocess 在 Mac Mini 会触达真 4.27 openclaw → 必须 stub.
    """
    repo_root = Path(repo_root)
    invoking = set()
    inv_rx = re.compile(r'\$OPENCLAW\b|\bopenclaw\s+message\b|\bopenclaw\s+\$|message\s+send|source\s+\S*notify\.sh|\bnotify\s+"')
    for sh in sorted(repo_root.glob("*.sh")) + sorted(repo_root.glob("jobs/**/*.sh")):
        try:
            txt = sh.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if inv_rx.search(txt):
            invoking.add(sh.name)
    return invoking


def _strip_line_comments(src):
    """逐行剥行尾 # 注释 (避免注释里的 danger 字面量误触). 简化: 不处理 '#' in string."""
    return "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))


def _class_setup_signals(class_node, lines):
    """收集 ClassDef 的 setUp/setUpClass 体内出现的隔离信号 (供方法级 D1 共享)."""
    found = set()
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
            "setUp", "setUpClass", "asyncSetUp",
        ):
            seg = _node_source(node, lines)
            for sig in _D1_ISOLATION_SIGNALS:
                if sig in seg:
                    found.add(sig)
    return found


def _node_source(node, lines):
    start = node.lineno - 1
    end = getattr(node, "end_lineno", node.lineno)
    return "\n".join(lines[start:end])


def scan_test_file(path, openclaw_scripts):
    """对单个治理-runtime 测试文件跑 D1/D2/D3, 返回 violation dict 列表."""
    path = Path(path)
    violations = []
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"file": path.name, "detector": "read", "line": 0,
                 "msg": f"无法读取: {e}"}]
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        # 语法错误的测试文件 silent skip (非本 scanner 职责), verbose 记录
        return [{"file": path.name, "detector": "parse", "line": getattr(e, "lineno", 0),
                 "msg": f"AST 解析失败 (skip): {e}"}]
    lines = src.split("\n")

    # ── D1 (crontab real-apply, 方法级) ──
    # V37.9.198 context gate: 仅当文件有 crontab-apply 上下文时 D1 适用 (dry_run=False 在
    # 无 crontab 上下文的文件里不是 crontab real-apply, 如 daily_observer LLM-skip dry_run)。
    _file_has_crontab_context = any(c in src for c in _D1_CRONTAB_CONTEXT)
    for cls in ([n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                if _file_has_crontab_context else []):
        setup_sigs = _class_setup_signals(cls, lines)
        for node in cls.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method_src = _strip_line_comments(_node_source(node, lines))
            if not _D1_DANGER.search(method_src):
                continue
            has_iso = any(sig in method_src for sig in _D1_ISOLATION_SIGNALS) or bool(setup_sigs)
            if not has_iso:
                violations.append({
                    "file": path.name, "detector": "D1-crontab-real-apply",
                    "line": node.lineno, "method": node.name,
                    "msg": (f"{path.name}::{cls.name}.{node.name} 含 dry_run=False real-apply 但无隔离信号 "
                            "(HOME-redirect / mock subprocess.run / _set_subprocess_mock / CONVERGENCE_DRY_RUN) "
                            "— 治理 subprocess 在 Mac Mini 跑时会真改 crontab (V37.9.158-hotfix 同款)"),
                })

    # ── D2 (~/.kb incident write, 文件级): subprocess 执行 movespeed helper 脚本但无 MOVESPEED_INCIDENT_FILE ──
    runs_movespeed = _find_executed_scripts(tree, _D2_HELPER_SCRIPTS)
    if runs_movespeed and _D2_ISOLATION not in src:
        violations.append({
            "file": path.name, "detector": "D2-kb-incident-write", "line": 0,
            "msg": (f"{path.name} 以 subprocess 执行 {sorted(runs_movespeed)} 但全文无 {_D2_ISOLATION} 重定向 "
                    "— 治理 subprocess 在 Mac Mini 跑时会写真 ~/.kb/movespeed_incidents.jsonl (V37.9.110 同款)"),
        })

    # ── D3 (真调 openclaw → WhatsApp, 文件级): subprocess 执行 openclaw-invoking 脚本但无 OPENCLAW stub ──
    runs_openclaw_script = _find_executed_scripts(tree, openclaw_scripts)
    if runs_openclaw_script:
        has_iso = any(sig in src for sig in _D3_ISOLATION_SIGNALS)
        if not has_iso:
            violations.append({
                "file": path.name, "detector": "D3-real-openclaw", "line": 0,
                "msg": (f"{path.name} 以 subprocess 执行 openclaw-invoking 脚本 {sorted(runs_openclaw_script)} "
                        "但全文无 OPENCLAW stub (OPENCLAW_BIN / _stub_env / OPENCLAW=stub) 也无 HOME-redirect "
                        "— 治理 subprocess 在 Mac Mini 跑时会调真 4.27 openclaw + 发真消息 (V37.9.157 同款)"),
            })

    return violations


def scan_repo(repo_root):
    """全量: 发现治理-runtime 测试集 → 逐文件扫. 返回 (violations, discovered_files)."""
    repo_root = Path(repo_root)
    yaml_text = (repo_root / "ontology" / "governance_ontology.yaml").read_text(
        encoding="utf-8", errors="replace")
    discovered = discover_governance_runtime_test_files(yaml_text, repo_root)
    openclaw_scripts = compute_openclaw_invoking_scripts(repo_root)
    violations = []
    for f in discovered:
        violations.extend(scan_test_file(f, openclaw_scripts))
    return violations, discovered


def main():
    ap = argparse.ArgumentParser(description="治理 runtime 测试隔离 scanner (MR-9/MR-23)")
    ap.add_argument("--file", help="扫单个测试文件 (用全集 detector)")
    ap.add_argument("--list-discovered", action="store_true",
                    help="只列出发现的治理-runtime 测试集")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    repo_root = REPO

    if args.list_discovered:
        yaml_text = GOV_YAML.read_text(encoding="utf-8", errors="replace")
        discovered = discover_governance_runtime_test_files(yaml_text, repo_root)
        if args.json:
            print(json.dumps([p.name for p in discovered], ensure_ascii=False, indent=2))
        else:
            print(f"治理 runtime check 跑的测试文件 ({len(discovered)} 个):")
            for p in discovered:
                print(f"  {p.name}")
        return 0

    if args.file:
        openclaw_scripts = compute_openclaw_invoking_scripts(repo_root)
        violations = scan_test_file(Path(args.file), openclaw_scripts)
        discovered = [Path(args.file)]
    else:
        violations, discovered = scan_repo(repo_root)

    if args.json:
        print(json.dumps({
            "discovered_count": len(discovered),
            "discovered": [p.name for p in discovered],
            "violations": violations,
        }, ensure_ascii=False, indent=2))
    else:
        # 把 parse/read skip 与真 violation 分开 (skip 不算 FAIL-CLOSE)
        real = [v for v in violations if v["detector"] not in ("parse", "read")]
        skips = [v for v in violations if v["detector"] in ("parse", "read")]
        for v in skips:
            print(f"  ⚠️  skip {v['file']}: {v['msg']}", file=sys.stderr)
        if real:
            print(f"❌ governance-runtime-isolation: {len(real)} 处违反 "
                  f"(扫 {len(discovered)} 个治理-runtime 测试文件):")
            for v in real:
                print(f"  [{v['detector']}] {v['msg']}")
        else:
            print(f"✅ governance-runtime-isolation: {len(discovered)} 个治理-runtime "
                  "测试文件全部隔离 (MR-9/MR-23 test-pollutes-production 0 violations)")

    real = [v for v in violations if v["detector"] not in ("parse", "read")]
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())

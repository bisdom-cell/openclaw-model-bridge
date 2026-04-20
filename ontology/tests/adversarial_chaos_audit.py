#!/usr/bin/env python3
"""
Adversarial Chaos Audit — 对抗性混沌审计（Route B）

目标：手工构造"故意破坏"场景，看 governance audit 能抓到几个。
能抓到的比例 = **audit 真实防御率**（不是理论值）。

两类场景：
  Category A (回归攻击)：已知血案 → audit 应该 100% 抓到（无漏则合格）
  Category B (探测攻击)：未知维度 → audit 大概率抓不到（暴露新盲区）

技术路径：
  1. 对真实仓库文件做临时破坏性 mutation
  2. 跑 governance_checker 看是否报错（exit code != 0）
  3. 无论成功与否用 try/finally 还原
  4. git status 验证现场干净

安全承诺：
  - 每个 mutation 都用 try/finally 还原
  - 脚本结束时 git status 必须 clean（若脏则报 FAIL）
  - 不 commit / 不 push / 不动 .git

用法：
  python3 ontology/tests/adversarial_chaos_audit.py              # 跑全部
  python3 ontology/tests/adversarial_chaos_audit.py --scenario 1 # 跑单个
  python3 ontology/tests/adversarial_chaos_audit.py --category a # 跑一组
"""

import argparse
import os
import subprocess
import sys
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, List, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class ChaosScenario:
    """一个混沌破坏场景。"""
    id: str              # "C1" / "C2" / ...
    category: str        # "A" (regression) / "B" (exploratory)
    name: str            # 人类可读名称
    description: str     # 破坏的内容
    expected_catch: bool # True = audit 应该抓到 / False = audit 可能抓不到
    mutate_fn: Callable[[], None]  # 注入破坏（修改文件）
    restore_fn: Callable[[], None] # 还原现场


def run_governance(mode: str = "--json") -> dict:
    """跑 governance_checker 返回结构化结果。

    Returns:
        {
          'exit_code': int,
          'stdout': str,
          'stderr': str,
          'found_violations': bool,  # 简单启发：stdout 含 ❌ 或 💥 即视为 catch
        }
    """
    cmd = ["python3", os.path.join(PROJECT_ROOT, "ontology", "governance_checker.py")]
    if mode:
        cmd.append(mode)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=120)
    combined = proc.stdout + proc.stderr
    # "catch" 判定：non-zero exit 或 stdout 含 fail/error 标记
    found = (
        proc.returncode != 0
        or "❌" in combined
        or "💥" in combined
        or '"status": "fail"' in combined
        or '"status": "error"' in combined
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "found_violations": found,
    }


@contextmanager
def file_mutation(path: str, mutate: Callable[[str], str]):
    """上下文管理器：临时修改文件，结束时还原原始内容。

    Args:
      path: 要修改的文件绝对路径
      mutate: 接受原内容返回新内容的函数
    """
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()
    try:
        new_content = mutate(original)
        if new_content == original:
            # mutation 无效 → 场景本身可能已失效（例如模式已不在文件里）
            raise RuntimeError(f"Mutation produced no change in {path} — scenario stale?")
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        yield
    finally:
        # 恢复原始内容
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)


@contextmanager
def file_temp_write(path: str, temp_content: str):
    """临时创建/覆盖文件，结束时还原（或删除）。"""
    existed = os.path.exists(path)
    original = None
    if existed:
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(temp_content)
        yield
    finally:
        if existed:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
        else:
            if os.path.exists(path):
                os.remove(path)


# ═══════════════════════════════════════════════════════════════════════
# Category A: 回归攻击场景（已知血案的 regression test）
# ═══════════════════════════════════════════════════════════════════════


def _c1_delete_soul_rule_10():
    """删除 SOUL.md 规则 10 一行 → INV-PA-002 应报警"""
    soul_path = os.path.join(PROJECT_ROOT, "SOUL.md")

    def mutate(content: str) -> str:
        # 删除"告警消息不跟进（2026-04-11 血案规则）"这行
        pattern = r"10\. \*\*🔴 告警消息不跟进（2026-04-11 血案规则）\*\*.*?(?=\n\n)"
        new = re.sub(pattern, "10. ~~(已删除)~~", content, count=1, flags=re.DOTALL)
        return new

    mutation_cm = file_mutation(soul_path, mutate)
    restore_cm = None
    return mutation_cm


def _c2_inflate_max_tools():
    """把 max_tools 从 12 改到 999 → INV-TOOL-001/002 应报警"""
    config_path = os.path.join(PROJECT_ROOT, "config.yaml")

    def mutate(content: str) -> str:
        # config.yaml 里 `max_tools: 12` → 999
        return re.sub(
            r'max_tools:\s*\d+',
            'max_tools: 999',
            content,
            count=1,
        )

    return file_mutation(config_path, mutate)


def _c3_delete_system_alert_marker():
    """删除 proxy_filters.SYSTEM_ALERT_MARKER 常量 → INV-PA-001 应报警"""
    proxy_path = os.path.join(PROJECT_ROOT, "proxy_filters.py")

    def mutate(content: str) -> str:
        # 把常量定义换成 None（让所有消费者拿到的不是原值）
        return re.sub(
            r'^SYSTEM_ALERT_MARKER\s*=\s*"\[SYSTEM_ALERT\]"',
            'SYSTEM_ALERT_MARKER = None  # CHAOS_MUTATED',
            content,
            count=1,
            flags=re.MULTILINE,
        )

    return file_mutation(proxy_path, mutate)


def _c4_heartbeat_md_actionable():
    """在 workspace HEARTBEAT.md 写非注释内容 → INV-HB-001 runtime 检查
    应该捕获（在 dev 环境这个文件不存在 → 测 proxy_filters 的拦截逻辑）"""
    # 注意：workspace/HEARTBEAT.md 是 Mac Mini 的文件，dev 环境可能不存在
    # 所以这里改为测试 proxy_filters.RESERVED_FILE_SAFE_CONTENT 被篡改的场景
    proxy_path = os.path.join(PROJECT_ROOT, "proxy_filters.py")

    def mutate(content: str) -> str:
        # 让 SAFE_CONTENT 含非注释行（破坏"保证 isHeartbeatContentEffectivelyEmpty=true"）
        return re.sub(
            r'RESERVED_FILE_SAFE_CONTENT\s*=\s*\(\s*\n',
            'RESERVED_FILE_SAFE_CONTENT = (\n    "任务完成 CHAOS_MUTATED\\n"\n',
            content,
            count=1,
        )

    return file_mutation(proxy_path, mutate)


def _c5_delete_reserved_file_basenames():
    """清空 RESERVED_FILE_BASENAMES → INV-HB-001 应报警"""
    proxy_path = os.path.join(PROJECT_ROOT, "proxy_filters.py")

    def mutate(content: str) -> str:
        # 把 frozenset(["HEARTBEAT.md"]) 改为空
        return re.sub(
            r'RESERVED_FILE_BASENAMES\s*=\s*frozenset\(\s*\[[^\]]*\]\s*\)',
            'RESERVED_FILE_BASENAMES = frozenset([])  # CHAOS_MUTATED',
            content,
            count=1,
            flags=re.DOTALL,
        )

    return file_mutation(proxy_path, mutate)


# ═══════════════════════════════════════════════════════════════════════
# 场景注册表
# ═══════════════════════════════════════════════════════════════════════


SCENARIOS = [
    ChaosScenario(
        id="C1", category="A", name="delete_soul_rule_10",
        description="删除 SOUL.md 规则 10（告警消息不跟进）一行",
        expected_catch=True,
        mutate_fn=_c1_delete_soul_rule_10,
        restore_fn=lambda: None,  # file_mutation 自己管
    ),
    ChaosScenario(
        id="C2", category="A", name="inflate_max_tools",
        description="把 MAX_TOOLS 从 12 改到 999",
        expected_catch=True,
        mutate_fn=_c2_inflate_max_tools,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C3", category="A", name="delete_system_alert_marker",
        description="删除 proxy_filters.SYSTEM_ALERT_MARKER 常量",
        expected_catch=True,
        mutate_fn=_c3_delete_system_alert_marker,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C4", category="A", name="corrupt_reserved_safe_content",
        description="在 RESERVED_FILE_SAFE_CONTENT 中插入非注释行（破坏空等效性）",
        expected_catch=True,
        mutate_fn=_c4_heartbeat_md_actionable,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C5", category="A", name="empty_reserved_file_basenames",
        description="清空 RESERVED_FILE_BASENAMES frozenset",
        expected_catch=True,
        mutate_fn=_c5_delete_reserved_file_basenames,
        restore_fn=lambda: None,
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════


def check_git_clean() -> bool:
    """验证 git 工作树干净。"""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    return not proc.stdout.strip()


def run_scenario(scenario: ChaosScenario, verbose: bool = False) -> dict:
    """跑单个场景，返回结果 dict。"""
    # 1. 确认 git clean
    if not check_git_clean():
        return {
            "id": scenario.id,
            "status": "SKIP",
            "reason": "git working tree not clean — aborting to avoid damage",
        }

    # 2. 跑 baseline（无 mutation）记录初始状态
    baseline = run_governance()

    # 3. 注入 mutation → 跑 audit → 还原
    try:
        with scenario.mutate_fn():
            mutated = run_governance()
    except Exception as e:
        # mutation 执行失败（例如 pattern 未命中）→ 场景失效
        return {
            "id": scenario.id,
            "status": "STALE",
            "reason": f"mutation failed: {e}",
        }

    # 4. 还原后再验证 git clean
    if not check_git_clean():
        return {
            "id": scenario.id,
            "status": "DIRTY",
            "reason": "git status NOT clean after restore — manual check needed",
        }

    # 5. 评估：audit 是否抓到了破坏？
    baseline_caught = baseline["found_violations"]
    mutated_caught = mutated["found_violations"]

    caught_by_mutation = (not baseline_caught) and mutated_caught
    # 或者 baseline 也有 violation，但 mutated 增加了新的 fail → 更难判定，降级：
    # 如果 baseline 干净 + mutated fail → caught
    # 如果 baseline 本来就 fail → 无法区分，返回 INCONCLUSIVE

    if baseline_caught:
        status = "INCONCLUSIVE"
        reason = "baseline has pre-existing violations — cannot isolate mutation effect"
    elif scenario.expected_catch and caught_by_mutation:
        status = "PASS"  # audit 符合预期 catch 了破坏
        reason = "audit caught mutation as expected"
    elif scenario.expected_catch and not caught_by_mutation:
        status = "FAIL"  # audit 应该抓但没抓到 — 真实盲区
        reason = "audit DID NOT catch mutation (expected to catch) — BLIND SPOT"
    elif not scenario.expected_catch and caught_by_mutation:
        status = "UNEXPECTED"  # 意外 catch 了（加分项）
        reason = "audit caught mutation (not expected)"
    else:
        status = "PASS"  # 符合预期没抓到（探测类）
        reason = "audit did not catch (as expected for exploratory)"

    return {
        "id": scenario.id,
        "category": scenario.category,
        "name": scenario.name,
        "description": scenario.description,
        "expected_catch": scenario.expected_catch,
        "status": status,
        "reason": reason,
        "baseline_caught": baseline_caught,
        "mutated_caught": mutated_caught,
    }


def print_result(r: dict, verbose: bool = False):
    """打印单个结果。"""
    icon = {
        "PASS": "✅",
        "FAIL": "❌",
        "STALE": "⚠️",
        "SKIP": "⏭",
        "DIRTY": "🔴",
        "INCONCLUSIVE": "❓",
        "UNEXPECTED": "🎉",
    }.get(r["status"], "?")
    print(f"{icon} [{r['id']}] {r.get('name','?')} — {r['status']}")
    print(f"     {r['reason']}")
    if verbose and r.get("status") in ("FAIL", "UNEXPECTED"):
        print(f"     baseline_caught={r.get('baseline_caught')} mutated_caught={r.get('mutated_caught')}")


def main():
    parser = argparse.ArgumentParser(description="Adversarial Chaos Audit")
    parser.add_argument("--scenario", help="只跑指定 id (C1/C2/...)")
    parser.add_argument("--category", choices=["a", "b", "A", "B"], help="只跑某类 (a/b)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    # 前置检查
    if not check_git_clean():
        print("🔴 git working tree NOT clean — commit or stash first")
        sys.exit(2)

    # 过滤场景
    to_run = SCENARIOS
    if args.scenario:
        to_run = [s for s in to_run if s.id == args.scenario]
    if args.category:
        to_run = [s for s in to_run if s.category.upper() == args.category.upper()]

    if not to_run:
        print("(no matching scenarios)")
        return

    print(f"═══ Adversarial Chaos Audit — {len(to_run)} scenario(s) ═══")
    print()

    results = []
    for s in to_run:
        r = run_scenario(s, verbose=args.verbose)
        print_result(r, verbose=args.verbose)
        results.append(r)
        print()

    # 汇总
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    print("═══ Summary ═══")
    for status, rs in sorted(by_status.items()):
        print(f"  {status}: {len(rs)}")

    # Defense rate (for Category A expected_catch=True 场景)
    a_scenarios = [r for r in results if r.get("category") == "A" and r.get("expected_catch")]
    if a_scenarios:
        caught = sum(1 for r in a_scenarios if r["status"] == "PASS")
        inconclusive = sum(1 for r in a_scenarios if r["status"] == "INCONCLUSIVE")
        total = len(a_scenarios)
        print()
        print(f"  Category A 真实防御率: {caught}/{total - inconclusive} "
              f"(INCONCLUSIVE: {inconclusive})")

    # Exit code
    fails = by_status.get("FAIL", []) + by_status.get("DIRTY", [])
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()

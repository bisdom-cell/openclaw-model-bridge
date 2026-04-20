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


def _count_violations(combined: str) -> dict:
    """数 violation 信号：fail (❌) / error (💥) / mrd_warn (⚠️ [MRD-)。

    V37.8.18: MRD warn 消息通常形如 "N 处 ..." ，同一 warn 内含多违规。
    之前只数 warn 行数 → baseline 已有 warn 时新增违规 delta=0（盲区）。
    现在解析每条 warn 的 "N 处" 数字，累计成精确 violation count。

    为了区分 baseline 已有 vs mutation 新增，需要 count 而不是 bool。
    """
    lines = combined.splitlines()
    fail_count = sum(1 for l in lines if "❌" in l and "[INV-" in l)
    error_count = sum(1 for l in lines if "💥" in l)
    # MRD warn 内部 "N 处" 数字累加（比简单数 warn 行精准）
    mrd_warn_lines = [l for l in lines if "⚠️ [MRD-" in l]
    mrd_detail_lines = []
    # MRD 消息通常在 warn 行之后的下一行（格式 "     N 处 ..."）
    for i, l in enumerate(lines):
        if "⚠️ [MRD-" in l:
            # 看下一行找 "N 处"
            if i + 1 < len(lines):
                mrd_detail_lines.append(lines[i + 1])
    mrd_violation_count = 0
    for dl in mrd_detail_lines:
        m = re.search(r'(\d+)\s*处', dl)
        if m:
            mrd_violation_count += int(m.group(1))
        else:
            # 无具体数字的 warn 也算 1
            mrd_violation_count += 1
    return {
        "fail": fail_count,
        "error": error_count,
        "mrd_warn": len(mrd_warn_lines),
        "mrd_violations": mrd_violation_count,
    }


def run_governance(mode: str = "") -> dict:
    """跑 governance_checker 返回结构化结果。"""
    cmd = ["python3", os.path.join(PROJECT_ROOT, "ontology", "governance_checker.py")]
    if mode:
        cmd.append(mode)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=120)
    combined = proc.stdout + proc.stderr
    violations = _count_violations(combined)
    total = sum(violations.values())
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "violations": violations,
        "total_violations": total,
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


def _c6_wa_keepalive_alerts_via_whatsapp():
    """改 wa_keepalive.sh 告警通道为 --channel whatsapp
    → INV-WA-001 runtime 断言 / MRD-ALERT-INDEPENDENCE-001 应报警"""
    wa_path = os.path.join(PROJECT_ROOT, "wa_keepalive.sh")

    def mutate(content: str) -> str:
        # 在 ESCALATE 附近注入 --channel whatsapp（违反告警独立性）
        return re.sub(
            r'(ESCALATE_FIRST=\d+)',
            r'\1\n# CHAOS_MUTATED ESCALAT via --channel whatsapp',
            content,
            count=1,
        )

    return file_mutation(wa_path, mutate)


def _c7_remove_quiet_alert_discord():
    """删除 quiet_alert 的 Discord 推送 → INV-QUIET-001 应报警"""
    auto_path = os.path.join(PROJECT_ROOT, "auto_deploy.sh")

    def mutate(content: str) -> str:
        return re.sub(
            r'静默期跳过WhatsApp.*Discord仍推',
            'CHAOS_MUTATED 静默期全部跳过',
            content,
            count=1,
        )

    return file_mutation(auto_path, mutate)


def _c8_dream_log_to_stdout():
    """改 kb_dream.sh log() 从 >&2 移除 → MRD-LOG-STDERR-001 应报警"""
    dream_path = os.path.join(PROJECT_ROOT, "kb_dream.sh")

    def mutate(content: str) -> str:
        # 找 log() 单行定义含 >&2，去掉 >&2
        return re.sub(
            r'^log\(\)\s*\{\s*echo\s+"([^"]*)"[^}]*>&2[^}]*\}',
            r'log() { echo "\1"; }  # CHAOS_MUTATED: removed >&2',
            content,
            count=1,
            flags=re.MULTILINE,
        )

    return file_mutation(dream_path, mutate)


def _c9_introduce_positional_parser():
    """在 LLM 解析脚本引入 `i += 3` 位置索引反模式 → MRD-LLM-PARSER-POSITIONAL-001 应报警"""
    # 选一个 MRD 扫描覆盖的脚本
    target = os.path.join(PROJECT_ROOT, "kb_review_collect.py")

    def mutate(content: str) -> str:
        # 在文件末尾追加违规代码块（独立函数避免影响运行）
        return content.rstrip() + "\n\n# CHAOS_MUTATED positional violation\ndef _chaos():\n    content = 'abc'\n    lines = content.split('\\n')\n    i = 0\n    i += 3\n    return lines[i+1]\n"

    return file_mutation(target, mutate)


def _c10_reinstate_zombie_handle():
    """重新加入僵尸 PDChina → INV-X-001 file_not_contains 守卫应报警"""
    fn_path = os.path.join(PROJECT_ROOT, "jobs/finance_news/run_finance_news.sh")

    def mutate(content: str) -> str:
        # 在 FINANCE_X_ACCOUNTS 里加回 PDChina
        return re.sub(
            r'("asahi\|朝日新闻\(X\)\|cn")',
            r'"PDChina|人民日报英文(X)|cn"  # CHAOS_MUTATED\n    \1',
            content,
            count=1,
        )

    return file_mutation(fn_path, mutate)


# ═══════════════════════════════════════════════════════════════════════
# Category B: 探测攻击场景（未知维度，旨在暴露新盲区）
# expected_catch=False → audit 抓到反而是惊喜
# ═══════════════════════════════════════════════════════════════════════


def _c11_silent_error_swallow():
    """在 adapter.py 引入裸 try: ... except: pass 静默吞错误模式。
    预期盲区：audit 无"裸 except 禁止"不变式，应抓不到"""
    target = os.path.join(PROJECT_ROOT, "adapter.py")

    def mutate(content: str) -> str:
        # 在文件末尾加入 silent swallow 反模式
        return content.rstrip() + "\n\n# CHAOS_MUTATED silent error swallow\ndef _chaos_silent():\n    try:\n        x = 1/0\n    except:\n        pass\n"

    return file_mutation(target, mutate)


def _c12_llm_cost_runaway():
    """把 adapter.py 某处 retry 从 3 改到 999，模拟 LLM 成本失控。
    预期盲区：audit 无'LLM 成本上限'不变式，应抓不到"""
    target = os.path.join(PROJECT_ROOT, "adapter.py")

    def mutate(content: str) -> str:
        # 寻找 retry 相关常量并膨胀（常见命名 MAX_RETRIES / retries / max_attempts）
        new, n = re.subn(
            r'(max_retries|MAX_RETRIES|max_attempts|MAX_ATTEMPTS)\s*=\s*\d+',
            r'\1 = 999  # CHAOS_MUTATED',
            content,
            count=1,
        )
        if n == 0:
            # fallback: 在文件加入 CHAOS 常量
            return content.rstrip() + "\n\n# CHAOS_MUTATED cost runaway\nMAX_RETRIES = 999\n"
        return new

    return file_mutation(target, mutate)


def _c13_missing_last_run_write():
    """删除某 job 的 last_run_*.json 写入。
    预期盲区：非 kb_inject/kb_harvest_chat 的 job 无此不变式，应抓不到"""
    # 选一个已知写 last_run 的脚本，删除其写入
    target = os.path.join(PROJECT_ROOT, "kb_inject.sh")

    def mutate(content: str) -> str:
        # 把 last_run_inject.json 字样改掉
        return re.sub(
            r'last_run_inject\.json',
            'CHAOS_MUTATED_last_run.json',
            content,
        )

    return file_mutation(target, mutate)


def _c14_kb_write_dict_repr():
    """让 flatten_content 对 dict 返回 str(content) repr 污染。
    V37.8.18 后 INV-KB-001 新增 runtime check 应能 catch（flatten_content(dict)
    应返回 '' 或不含 {，mutation 让它返回 repr 即触发断言失败）"""
    target = os.path.join(PROJECT_ROOT, "proxy_filters.py")

    def mutate(content: str) -> str:
        # 把 flatten_content 的 unknown type fallback `return ""` 改为 `return str(content)`
        # 这会让 dict 输入返回 repr 字符串，INV-KB-001 runtime check 应触发
        return re.sub(
            r'(# Unknown type — safest fallback is empty string.*\n\s*)return ""',
            r'\1return str(content)  # CHAOS_MUTATED dict repr leak',
            content,
            count=1,
        )

    return file_mutation(target, mutate)


def _c15_push_bypass_notify_sh():
    """新建一个伪造推送脚本，直接 openclaw message send 绕过 notify.sh。
    MRD-NOTIFY-001 按 topic 扫 caller，应能部分覆盖 / MR-4 可能抓不到"""
    # 在临时文件里写一个"推送脚本"（不 commit，file_temp_write 管理）
    target = os.path.join(PROJECT_ROOT, "chaos_rogue_pusher.sh")

    @contextmanager
    def _cm():
        temp_content = """#!/bin/bash
# CHAOS_MUTATED: rogue pusher bypassing notify.sh
openclaw message send --channel whatsapp "rogue alert bypass"
"""
        with file_temp_write(target, temp_content):
            yield

    return _cm()


def _c16_audit_performance_regression():
    """让 governance_checker 变慢（启动时 sleep）。
    预期盲区：audit 无自身性能不变式（observer blind spot — MR-7 只覆盖 summary 正确性）"""
    target = os.path.join(PROJECT_ROOT, "ontology", "governance_checker.py")

    def mutate(content: str) -> str:
        # 在 `if __name__` 入口注入 sleep 膨胀启动时间
        return re.sub(
            r'if __name__ == "__main__":\n',
            'if __name__ == "__main__":\n    # CHAOS_MUTATED artificial slowdown\n    import time\n    time.sleep(0.5)\n',
            content,
            count=1,
        )

    return file_mutation(target, mutate)


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
    ChaosScenario(
        id="C6", category="A", name="wa_keepalive_alerts_via_whatsapp",
        description="wa_keepalive.sh 告警路径注入 --channel whatsapp（违反告警独立性）",
        expected_catch=True,
        mutate_fn=_c6_wa_keepalive_alerts_via_whatsapp,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C7", category="A", name="remove_quiet_alert_discord",
        description="删除 quiet_alert 静默期 Discord 推送注释",
        expected_catch=True,
        mutate_fn=_c7_remove_quiet_alert_discord,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C8", category="A", name="dream_log_to_stdout",
        description="kb_dream.sh log() 移除 >&2（回到写 stdout）",
        expected_catch=True,
        mutate_fn=_c8_dream_log_to_stdout,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C9", category="A", name="positional_parser_in_kb_review",
        description="kb_review_collect.py 引入 i += 3 位置索引反模式",
        expected_catch=True,
        mutate_fn=_c9_introduce_positional_parser,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C10", category="A", name="reinstate_zombie_pdchina",
        description="finance_news 重新加入僵尸 PDChina handle",
        expected_catch=True,
        mutate_fn=_c10_reinstate_zombie_handle,
        restore_fn=lambda: None,
    ),
    # Category B: 探测攻击 — 暴露 audit 未覆盖的维度
    ChaosScenario(
        id="C11", category="B", name="silent_error_swallow",
        description="adapter.py 引入裸 `try: ... except: pass` 静默吞错误",
        expected_catch=False,  # 预期盲区
        mutate_fn=_c11_silent_error_swallow,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C12", category="B", name="llm_cost_runaway",
        description="adapter.py 把 retry 改到 999（模拟 LLM 成本失控）",
        expected_catch=False,  # 预期盲区
        mutate_fn=_c12_llm_cost_runaway,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C13", category="B", name="missing_last_run_write",
        description="kb_inject.sh 删除 last_run_inject.json 写入",
        expected_catch=False,  # 预期盲区
        mutate_fn=_c13_missing_last_run_write,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C14", category="B", name="kb_write_dict_repr",
        description="tool_proxy 引入 dict 直接 str() 污染变种（非 list blocks）",
        expected_catch=False,  # 预期盲区（INV-KB-001 只防 list）
        mutate_fn=_c14_kb_write_dict_repr,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C15", category="B", name="push_bypass_notify_sh",
        description="新建伪造脚本绕过 notify.sh 直接 openclaw message send",
        expected_catch=False,  # 预期盲区
        mutate_fn=_c15_push_bypass_notify_sh,
        restore_fn=lambda: None,
    ),
    ChaosScenario(
        id="C16", category="B", name="audit_performance_regression",
        description="governance_checker 注入 sleep(0.5) 性能退化",
        expected_catch=False,  # 预期盲区（MR-7 自观察盲区）
        mutate_fn=_c16_audit_performance_regression,
        restore_fn=lambda: None,
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════


def check_git_clean() -> bool:
    """验证 git 工作树干净（status.json 豁免，因为 full_regression 会 touch 它）。"""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    # 豁免 status.json（full_regression 证据回写，非实质改动）
    filtered = [l for l in lines if not l.strip().endswith("status.json")]
    return not filtered


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

    # 5. 评估：audit 是否"新增"catch 了破坏？
    baseline_total = baseline["total_violations"]
    mutated_total = mutated["total_violations"]
    delta = mutated_total - baseline_total

    # delta > 0 意味着 mutation 让 audit 触发了**更多**违规（抓到了）
    caught_by_mutation = delta > 0

    if scenario.expected_catch and caught_by_mutation:
        status = "PASS"
        reason = f"audit caught mutation (violations {baseline_total} → {mutated_total}, +{delta})"
    elif scenario.expected_catch and not caught_by_mutation:
        status = "FAIL"
        reason = f"audit DID NOT catch mutation (violations {baseline_total} → {mutated_total}) — BLIND SPOT"
    elif not scenario.expected_catch and caught_by_mutation:
        status = "UNEXPECTED"
        reason = f"audit unexpectedly caught (violations {baseline_total} → {mutated_total}, +{delta})"
    else:
        status = "PASS"
        reason = f"audit did not catch (as expected for exploratory, violations {baseline_total} → {mutated_total})"

    return {
        "id": scenario.id,
        "category": scenario.category,
        "name": scenario.name,
        "description": scenario.description,
        "expected_catch": scenario.expected_catch,
        "status": status,
        "reason": reason,
        "baseline_violations": baseline["violations"],
        "mutated_violations": mutated["violations"],
        "delta": delta,
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
    if verbose:
        print(f"     baseline={r.get('baseline_violations')} mutated={r.get('mutated_violations')} delta={r.get('delta')}")


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
        total = len(a_scenarios)
        print()
        print(f"  Category A 真实防御率: {caught}/{total}")

    # Category B (expected_catch=False) 意外 catch 的数
    b_scenarios = [r for r in results if r.get("category") == "B"]
    if b_scenarios:
        unexpected = sum(1 for r in b_scenarios if r["status"] == "UNEXPECTED")
        total_b = len(b_scenarios)
        print(f"  Category B 意外 catch: {unexpected}/{total_b} (越多 = audit 覆盖维度越广)")

    # Exit code
    fails = by_status.get("FAIL", []) + by_status.get("DIRTY", [])
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()

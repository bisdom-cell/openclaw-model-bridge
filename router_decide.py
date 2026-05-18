#!/usr/bin/env python3
"""V37.9.76 Capability-Based Dynamic Router — Shadow Mode Decision Logger.

Used by Capability Router PoC to record router decisions for tasks WITHOUT
actually changing LLM call routing behavior. Caller invokes this script
before LLM call, decision is appended to ~/.kb/router_decisions.jsonl,
but caller still uses adapter's default PROVIDER_NAME/fallback_chain.

After 1-week shadow observation, V37.9.77+ evaluates flipping to enforcement
mode (caller passes chosen provider to adapter via `?provider=X` query param).

Architecture (镜像 V37.9.19 → V37.9.23 → V37.9.58 Plan B 同款渐进策略):
    declarative (shadow mode 决策可见) → on (enforcement 真路由) — 一周观察期保险

Design契约:
    - FAIL-OPEN: 任何错误（registry 缺失/job_id 未声明/find_best_provider None）→
      仍 exit 0 + log "no_router_profile" reason, 绝不阻塞 caller LLM 调用
    - 纯 stdlib + lazy import providers (避 dev 环境 module 加载失败时炸 caller)
    - JSONL append-only (V37.9.18 audit_log 同款风格, mtime-stable)

Usage:
    python3 router_decide.py --job-id kb_dream --task radar_retry
    python3 router_decide.py --job-id hf_papers --task per_paper --exclude doubao,gemini

CLI 输出:
    stdout: chosen provider name (或 "no_router_profile" / "no_matching_provider")
    stderr: 简短状态日志 (MR-11 stderr 防 $(...) 污染)
    exit code: 0 always (FAIL-OPEN, V37.9.76 shadow 不阻塞)

JSONL schema (~/.kb/router_decisions.jsonl):
    {
      "ts": "2026-05-18T09:15:23+08:00",
      "task": "kb_dream/radar_retry",
      "job_id": "kb_dream",
      "required": {"text": true},
      "prefer": ["reasoning"],
      "cost_tier": "low",
      "exclude": [],
      "chosen": "doubao",
      "chosen_cap_score": 16,
      "alternatives": ["qwen", "gemini", "claude"],
      "mode": "shadow",
      "reason": "ok",
      "v37_9_76": true
    }

V37.9.77+ 升级路径 (一周观察后):
    1. adapter.py 加 ?provider=X query param 支持 (V37.9.55+ 候选 B)
    2. caller 把 router 选的 chosen 通过 ?provider=X 传给 adapter
    3. enforcement 真生效, JSONL mode 字段从 "shadow" 升级为 "on"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any


# Constants locked V37.9.76 PoC
JSONL_PATH = os.path.expanduser("~/.kb/router_decisions.jsonl")
JOBS_REGISTRY_PATH = os.path.expanduser("~/jobs_registry.yaml")
# V37.9.76-hotfix: Mac Mini 部署后 router_decide.py 在 ~/, dirname(__file__) = ~,
# 让 FALLBACK 与 PATH 重合 → yaml 找不到 → chosen=None silent failure.
# 加 Mac Mini canonical repo 路径作第二候选, 部署不必 deploy yaml 到 ~/.
JOBS_REGISTRY_MAC_MINI = os.path.expanduser("~/openclaw-model-bridge/jobs_registry.yaml")
JOBS_REGISTRY_FALLBACK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "jobs_registry.yaml"
)
_VERSION_MARKER = "V37.9.76"
_MODE_DEFAULT = "shadow"  # V37.9.77+ 切换为 "on" 启用 enforcement


def log(msg: str) -> None:
    """MR-11: 写 stderr 防 $(...) 命令替换污染 caller stdout."""
    print(f"[router_decide] {msg}", file=sys.stderr)


def _hkt_iso_now() -> str:
    """HKT (UTC+8) ISO 8601 timestamp — 与 Dream / cron 日志格式一致."""
    hkt = timezone(timedelta(hours=8))
    return datetime.now(hkt).isoformat(timespec="seconds")


def _load_yaml_job_profile(job_id: str) -> dict | None:
    """读 jobs_registry.yaml 找指定 job_id 的 capability profile.

    返回 dict {'required_capabilities': [...], 'prefer': [...], 'cost_tier': str}
    或 None 表示找不到 job 或 job 未声明 capability fields.

    FAIL-OPEN: yaml 缺失/损坏/job 未声明 → 返回 None, 不抛异常.
    """
    # Lazy import yaml — dev 环境无 PyYAML 时不炸
    try:
        import yaml  # type: ignore
    except ImportError:
        log("WARN: PyYAML not installed, router cannot read profile")
        return None

    # 找 jobs_registry.yaml 三档候选 (V37.9.76-hotfix Mac Mini layout 修复):
    #   1. $HOME/jobs_registry.yaml — 若 FILE_MAP 添加部署到 ~ (当前未加)
    #   2. $HOME/openclaw-model-bridge/jobs_registry.yaml — Mac Mini canonical repo (auto_deploy 同步源)
    #   3. dirname(__file__)/jobs_registry.yaml — dev 环境 router_decide.py 在 repo 同目录
    candidates = [JOBS_REGISTRY_PATH, JOBS_REGISTRY_MAC_MINI, JOBS_REGISTRY_FALLBACK]
    yaml_path = next((p for p in candidates if os.path.isfile(p)), None)
    if yaml_path is None:
        log(f"WARN: jobs_registry.yaml not found in {candidates}")
        return None

    try:
        with open(yaml_path, "r", encoding="utf-8", errors="replace") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        log(f"WARN: failed to parse {yaml_path}: {type(e).__name__}")
        return None

    jobs = data.get("jobs", [])
    if not isinstance(jobs, list):
        return None

    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("id") != job_id:
            continue
        # 找到匹配的 job, 提取 capability profile fields (可选, 缺失 → None)
        req_caps = job.get("required_capabilities")
        prefer = job.get("prefer")
        cost_tier = job.get("cost_tier")
        # 若全缺失 → 返回 None ("no_router_profile")
        if req_caps is None and prefer is None and cost_tier is None:
            return None
        return {
            "required_capabilities": req_caps if isinstance(req_caps, list) else [],
            "prefer": prefer if isinstance(prefer, list) else [],
            "cost_tier": cost_tier if isinstance(cost_tier, str) else "",
        }
    return None  # job_id 不在 registry


def _call_find_best_provider(
    required: dict, prefer: list, exclude: list, require_available: bool = False
) -> tuple[str | None, list[str], int | None]:
    """调 providers.find_best_provider, 返回 (chosen_name, alternatives, chosen_cap_score).

    FAIL-OPEN: providers 模块导入失败/无匹配 → (None, [], None).
    require_available=False default — dev 环境无 API key 时 router 仍可决策 (shadow).
    """
    # Lazy import providers — dev 环境无依赖时不炸
    try:
        from providers import _default_registry as _reg
    except ImportError as e:
        log(f"WARN: cannot import providers module: {e}")
        return None, [], None

    try:
        chosen = _reg.find_best_provider(
            required=required if required else None,
            prefer=prefer if prefer else None,
            exclude=exclude if exclude else None,
            require_available=require_available,
        )
    except Exception as e:
        log(f"WARN: find_best_provider raised {type(e).__name__}: {e}")
        return None, [], None

    if chosen is None:
        return None, [], None

    # Build alternatives list (top 5 other matches, excluding chosen)
    try:
        all_matches = []
        for p in _reg._providers.values():  # type: ignore
            if p.name == chosen.name:
                continue
            if exclude and p.name in exclude:
                continue
            caps = p.capabilities
            ok = True
            if required:
                for k, v in required.items():
                    if not hasattr(caps, k) or getattr(caps, k) != v:
                        ok = False
                        break
            if ok:
                all_matches.append(p.name)
        # Limit to 5 alternatives for log brevity
        alternatives = all_matches[:5]
    except Exception:
        alternatives = []

    try:
        chosen_score = _reg._capability_score(chosen)  # type: ignore
    except Exception:
        chosen_score = None

    return chosen.name, alternatives, chosen_score


def decide(
    job_id: str,
    task: str | None = None,
    exclude: list[str] | None = None,
    profile_override: dict | None = None,
    require_available: bool = False,
) -> dict:
    """V37.9.76 主入口: 读 profile + 调 find_best_provider + 组装 JSONL record.

    Returns: dict 形 JSONL record (已含 ts + version marker), caller 决定是否写入文件.

    FAIL-OPEN: 任何错误都返回结构化 record (含 reason 字段说明), 不抛异常.
    """
    task_label = f"{job_id}/{task}" if task else job_id

    record: dict[str, Any] = {
        "ts": _hkt_iso_now(),
        "task": task_label,
        "job_id": job_id,
        "required": {},
        "prefer": [],
        "cost_tier": "",
        "exclude": list(exclude or []),
        "chosen": None,
        "chosen_cap_score": None,
        "alternatives": [],
        "mode": _MODE_DEFAULT,
        "reason": "ok",
        "v37_9_76": True,
    }

    # 1. Load profile (from registry or override)
    profile = profile_override
    if profile is None:
        profile = _load_yaml_job_profile(job_id)

    if profile is None:
        record["reason"] = "no_router_profile"
        return record

    req_caps_list = profile.get("required_capabilities", []) or []
    prefer = profile.get("prefer", []) or []
    cost_tier = profile.get("cost_tier", "") or ""

    record["required"] = {c: True for c in req_caps_list if isinstance(c, str)}
    record["prefer"] = [c for c in prefer if isinstance(c, str)]
    record["cost_tier"] = cost_tier

    # 2. Call find_best_provider
    chosen_name, alternatives, chosen_score = _call_find_best_provider(
        required=record["required"],
        prefer=record["prefer"],
        exclude=record["exclude"],
        require_available=require_available,
    )

    record["chosen"] = chosen_name
    record["alternatives"] = alternatives
    record["chosen_cap_score"] = chosen_score
    if chosen_name is None:
        record["reason"] = "no_matching_provider"
    return record


def append_jsonl(record: dict, path: str = JSONL_PATH) -> bool:
    """Append record to JSONL file (mkdir -p parent, FAIL-OPEN on IO error).

    Returns: True if write succeeded, False otherwise (caller can log but not abort).
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        log(f"WARN: failed to append to {path}: {type(e).__name__}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="V37.9.76 Capability Router shadow mode decision logger"
    )
    parser.add_argument("--job-id", required=True, help="Job ID in jobs_registry.yaml")
    parser.add_argument("--task", default=None, help="Optional task label (e.g. 'radar_retry')")
    parser.add_argument(
        "--exclude", default="", help="Comma-separated provider names to exclude"
    )
    parser.add_argument(
        "--no-log", action="store_true", help="Skip JSONL write (dry-run/simulate)"
    )
    parser.add_argument(
        "--require-available",
        action="store_true",
        help="Only consider providers with API key set (default: include all)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output full record JSON to stdout instead of just name"
    )
    args = parser.parse_args()

    exclude = [x.strip() for x in args.exclude.split(",") if x.strip()] if args.exclude else []

    record = decide(
        job_id=args.job_id,
        task=args.task,
        exclude=exclude,
        require_available=args.require_available,
    )

    if not args.no_log:
        append_jsonl(record)

    if args.json:
        print(json.dumps(record, ensure_ascii=False))
    else:
        # Default: output chosen provider name (caller can use in shadow comparison)
        print(record.get("chosen") or record.get("reason") or "unknown")

    log(
        f"job={args.job_id} task={args.task or '-'} chosen={record.get('chosen')} "
        f"reason={record.get('reason')} mode={record.get('mode')}"
    )
    # FAIL-OPEN: exit 0 always (V37.9.76 shadow not blocking caller)
    return 0


if __name__ == "__main__":
    sys.exit(main())

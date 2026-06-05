#!/usr/bin/env python3
"""V37.9.56 Sub-Stage 4c — Top 5 高对齐推送 picker (Opportunity Radar #2 兑现)

读今日所有 ALIGNED source 的 cache/llm_results.jsonl, 解析 6 字段 LLM 输出, 抽取
🎚️ 项目对齐度 ⭐ 数 + cn_title + alignment reason, 按 ⭐ 数排序取 Top N, emit
紧凑推送段供 kb_dream / kb_evening / kb_radar 三处推送场景注入.

═══════════════════════════════════════════════════════════════════════════
背景: V37.9.45 hf_papers / V37.9.50 semantic_scholar / V37.9.51 6 脚本批量
迁移 5→6 字段含 🎚️ 项目对齐度评分 (⭐1-5 + 一句话原因). V37.9.51 收工承诺
Sub-Stage 4c = 聚合所有 ⭐≥4 高对齐数据到 Dream/Radar/Evening 推送段, 让用户
在每日推送中"一眼看见"今日真正值得读的 paper/repo/blog/tweet, 不再淹没在 14
source × 50 entries 信息洪流里.

═══════════════════════════════════════════════════════════════════════════
数据源 (10 ALIGNED sources, V37.9.45/50/51 全量迁移 + V37.9.108/112 大神观点):
  - jobs/hf_papers/cache/llm_results.jsonl          (V37.9.45)
  - jobs/semantic_scholar/cache/llm_results.jsonl   (V37.9.50)
  - jobs/dblp/cache/llm_results.jsonl               (V37.9.51 1/6)
  - jobs/arxiv_monitor/cache/llm_results.jsonl      (V37.9.51 3/6)
  - jobs/github_trending/cache/llm_results.jsonl    (V37.9.51 4/6)
  - jobs/rss_blogs/cache/llm_results.jsonl          (V37.9.51 1/6)
  - jobs/ai_leaders_blogs/cache/llm_results.jsonl   (V37.9.108 大神长文)
  - jobs/ai_leaders_x/cache/llm_results.jsonl       (V37.9.51 5/6)
  - jobs/ai_leaders_bsky/cache/llm_results.jsonl    (V37.9.112 大神实时短帖)
  - cache/llm_results.jsonl (HN, run_hn_fixed.sh)   (V37.9.51 6/6)

每条 result 是 JSON line: {"idx": N, "content": "<6-field LLM 输出>", "failed": bool}
6 字段包括: 📌 中文标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 / ⭐ 评级 / 🎚️ 项目对齐度

═══════════════════════════════════════════════════════════════════════════
FAIL-OPEN 契约 (V37.9.46 cross_source_signal_aggregator 同款模式):
  - cache_dir 缺失 → 该 source 0 picks 不阻塞其他 source
  - llm_results.jsonl 损坏 / 单条 JSON parse 错误 → 跳过该条继续
  - content 字段缺失或 failed=True → 该条不计 picks
  - alignment 字段缺失或 ⭐ 数 < min_stars → 该条不计 picks
  - 无任何 picks → emit 空 block, 调用方静默不推送 (不阻塞 Dream/evening 主流程)
  - project_alignment_scorer 缺失 → fallback 到 inline regex 抽 ⭐ 数

═══════════════════════════════════════════════════════════════════════════
排序契约:
  - 主排: ⭐ 数 desc (5 > 4 > 3)
  - tie-break 1: source priority (论文类 > repo 类 > 博客/tweet 类)
  - tie-break 2: cn_title 长度 (信息密度代理, longer = more informative)

═══════════════════════════════════════════════════════════════════════════
集成路径 (V37.9.56 三处推送场景):
  1. kb_dream.sh Phase 1.5+ — RADAR + TREND 之后注入 TOP_ALIGNMENT_BLOCK
     作为 "═══ Opportunity Radar #2 (今日高对齐 Top 5) ═══" 段进 REDUCE_DATA
  2. kb_evening_collect.py build_evening_prompt — top_alignment_picks 可选参数,
     在 "本日高对齐 Top 5" 段加进 prompt 让 LLM "今日要闻"段优先引用
  3. (V37.9.57+) kb_radar.sh 06:00 cron — 综合 #1+#2+#3 三件套早间推送

═══════════════════════════════════════════════════════════════════════════
MR-11 兑现 (V37.8.6 stdout 污染血案教训): log() 写 stderr 防 `$(...)` 命令替换污染.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any


# V37.9.56 marker (源码级守卫识别用)
_V37_9_56_MARKER = "V37.9.56 Sub-Stage 4c Top 5 高对齐推送"

# 默认推送阈值: 项目对齐度 ⭐≥4 才入选, 防止低质量"勉强相关"占位
DEFAULT_MIN_STARS = 4

# 默认推送 Top N: 5 条 (用户一屏可见, 不淹没 Dream/Evening 主分析)
DEFAULT_TOP_N = 5

# Source registry: 8 ALIGNED sources (V37.9.45/50/51 全量迁移完毕)
# priority 数字越小越高: 论文类 1-3 > repo 类 4 > 博客 5 > tweet 类 6-7
#
# V37.9.57 hotfix: cache_paths list 替代 cache_dir_rel (单路径)
# 真实部署差异 (V37.9.56 Mac Mini 实测发现 0 picks bug):
#   - dev mode: jobs/X/cache (相对 repo_root)
#   - Mac Mini production: ~/.openclaw/jobs/X/cache (auto_deploy FILE_MAP 真路径)
#   - HN 例外: dev=repo_root/cache, prod=~/.openclaw/jobs/hn_watcher/cache (子目录名差异)
# Picker 按 cache_paths 顺序 try, 第一个存在的目录就用 (FAIL-OPEN: 全空时返回首个候选).
ALIGNED_SOURCES: list[dict[str, Any]] = [
    {"id": "hf_papers", "display": "HF精选", "priority": 1,
     "cache_paths": ["jobs/hf_papers/cache", "~/.openclaw/jobs/hf_papers/cache"]},
    {"id": "semantic_scholar", "display": "S2引用", "priority": 2,
     "cache_paths": ["jobs/semantic_scholar/cache", "~/.openclaw/jobs/semantic_scholar/cache"]},
    {"id": "arxiv", "display": "ArXiv", "priority": 3,
     "cache_paths": ["jobs/arxiv_monitor/cache", "~/.openclaw/jobs/arxiv_monitor/cache"]},
    {"id": "dblp", "display": "DBLP", "priority": 3,
     "cache_paths": ["jobs/dblp/cache", "~/.openclaw/jobs/dblp/cache"]},
    {"id": "github_trending", "display": "GitHub", "priority": 4,
     "cache_paths": ["jobs/github_trending/cache", "~/.openclaw/jobs/github_trending/cache"]},
    {"id": "rss_blogs", "display": "博客", "priority": 5,
     "cache_paths": ["jobs/rss_blogs/cache", "~/.openclaw/jobs/rss_blogs/cache"]},
    # V37.9.108: AI 大神长文观点 (博客/Substack RSS, 替代退化的 ai_leaders_x).
    # 博客档 priority 5 (长文 > tweet), 让 ⭐≥4 学者观点进 Top 5 机会点雷达.
    {"id": "ai_leaders_blogs", "display": "大神观点", "priority": 5,
     "cache_paths": ["jobs/ai_leaders_blogs/cache", "~/.openclaw/jobs/ai_leaders_blogs/cache"]},
    {"id": "ai_leaders_x", "display": "AI Leaders", "priority": 6,
     "cache_paths": ["jobs/ai_leaders_x/cache", "~/.openclaw/jobs/ai_leaders_x/cache"]},
    # V37.9.112: AI 大神 Bluesky 实时短观点 (getAuthorFeed JSON, V37.9.110/111 上线).
    # 社媒档 priority 6 (实时短帖同 tweet 类, 同 ai_leaders_x, > 博客 5), 让 ⭐≥4 大神实时观点进 Top 5 机会点雷达.
    # 下游 100% 复用 blogs 管道, llm_results.jsonl 格式逐字一致 → picker scan 零改 (MR-8).
    {"id": "ai_leaders_bsky", "display": "大神实时", "priority": 6,
     "cache_paths": ["jobs/ai_leaders_bsky/cache", "~/.openclaw/jobs/ai_leaders_bsky/cache"]},
    {"id": "hn", "display": "HN", "priority": 7,
     "cache_paths": ["cache", "~/.openclaw/jobs/hn_watcher/cache"]},
]

# llm_results.jsonl 文件名 (V37.9.45+ 8 source 统一使用)
RESULTS_FILENAME = "llm_results.jsonl"


def _resolve_cache_dir(repo_root: str, src: dict[str, Any]) -> str:
    """V37.9.57: 按 cache_paths 顺序选第一个存在的目录.

    Args:
        repo_root: 仓库根目录 (相对路径基准)
        src: ALIGNED_SOURCES 条目, 含 cache_paths list

    Returns:
        str: 第一个 isdir 为真的候选 (展开 ~ + 拼接相对路径).
             全部不存在 → 返回首个候选 (FAIL-OPEN, scan_source_results 会返回空 list).
             cache_paths 缺失/空 → 返回 "" (FAIL-OPEN).

    路径解析规则:
        - 以 `~` 开头 → os.path.expanduser
        - 绝对路径 → 直接用
        - 相对路径 → os.path.join(repo_root, ...)
    """
    candidates = src.get("cache_paths") or []
    if not candidates:
        return ""

    def _expand(cand: str) -> str:
        if cand.startswith("~"):
            return os.path.expanduser(cand)
        if os.path.isabs(cand):
            return cand
        return os.path.join(repo_root, cand)

    # 先 try 找存在的目录
    for cand in candidates:
        full = _expand(cand)
        if os.path.isdir(full):
            return full
    # FAIL-OPEN: 全部不存在 → 返回首个候选 (展开后)
    return _expand(candidates[0])


def log(msg: str) -> None:
    """V37.8.6 MR-11: 写 stderr 防 `$(...)` 命令替换污染."""
    print(msg, file=sys.stderr)


def _fallback_extract_star_count(text: str) -> int:
    """Fallback ⭐ 提取 (project_alignment_scorer 缺失时使用).

    取文本中最长连续 ⭐ 段长度. project_alignment_scorer.extract_star_count
    同款逻辑. 1-5 范围 clamp.
    """
    if not isinstance(text, str) or not text:
        return 0
    matches = re.findall(r"⭐+", text)
    if not matches:
        return 0
    return max(min(len(m), 5) for m in matches)


def parse_alignment_from_content(content: str) -> dict[str, Any]:
    """从 6 字段 LLM 输出解析 cn_title / alignment_stars / alignment_reason.

    Returns:
        {"cn_title": str, "alignment_stars": int (0-5), "alignment_reason": str, "rating_stars": int}

    解析策略 (key-based + tolerant, 不依赖位置):
        - cn_title: 📌 标题字段
        - rating_stars: ⭐ 评级字段 (与 alignment 区分)
        - alignment: 🎚️ 项目对齐度字段, 抽出 ⭐ 数 + 后续原因

    FAIL-OPEN: content 为空 / 非 string / 缺字段 → 默认空值 0 stars 不抛异.
    """
    result = {
        "cn_title": "",
        "alignment_stars": 0,
        "alignment_reason": "",
        "rating_stars": 0,
    }
    if not isinstance(content, str) or not content.strip():
        return result

    # Lazy import scorer (FAIL-OPEN if missing)
    try:
        import project_alignment_scorer as _pas  # type: ignore[import-not-found]
        _extract_stars = _pas.extract_star_count
    except Exception:
        _extract_stars = _fallback_extract_star_count

    lines = content.split("\n")
    current_field: str | None = None
    field_buffer: dict[str, list[str]] = {
        "cn_title": [],
        "rating": [],
        "alignment": [],
    }

    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()

        # 📌 中文标题
        if stripped.startswith("📌"):
            current_field = "cn_title"
            m = re.match(r".*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)", line)
            if m and m.group(1).strip():
                field_buffer["cn_title"].append(m.group(1).strip())
            continue
        # 🔑 / 💡 / 🎯 (我们不解析这些, 但要 switch field)
        if stripped.startswith("🔑") or stripped.startswith("💡") or stripped.startswith("🎯"):
            current_field = None
            continue
        # 🎚️ 项目对齐度 (V37.9.45+ 新增字段, 必须在 ⭐ 检测之前避免干扰)
        if stripped.startswith("🎚️") or stripped.startswith("🎚"):
            current_field = "alignment"
            m = re.match(r".*🎚️?\s*(?:项目)?对齐度?\s*[:：]?\s*(.*)", line)
            if m and m.group(1).strip():
                field_buffer["alignment"].append(m.group(1).strip())
            continue
        # ⭐ 评级 (current_field != alignment 才进入, 否则被 alignment 段吸收)
        if stripped.startswith("⭐") and current_field != "alignment":
            current_field = "rating"
            field_buffer["rating"].append(stripped)
            continue
        # 普通行 append 到 current_field
        if current_field is not None and current_field in field_buffer:
            field_buffer[current_field].append(line)

    result["cn_title"] = "\n".join(field_buffer["cn_title"]).strip()
    alignment_text = "\n".join(field_buffer["alignment"]).strip()
    rating_text = "\n".join(field_buffer["rating"]).strip()

    # Alignment ⭐ 数 + reason 抽取
    # V37.9.56: clamp 到 [0, 5] 统一行为 (project_alignment_scorer.extract_star_count
    # 不 clamp, _fallback_extract_star_count 已 clamp, 这里统一兜底确保偶发 LLM
    # 输出 6+ ⭐ 不破坏下游 display layer 假设).
    if alignment_text:
        result["alignment_stars"] = min(max(_extract_stars(alignment_text), 0), 5)
        # Reason = alignment text 去掉 ⭐ 段后剩下的描述, 取第一行非空
        reason_candidates = re.split(r"⭐+\s*[/／]?\s*", alignment_text, maxsplit=1)
        if len(reason_candidates) > 1:
            reason = reason_candidates[1].strip()
        else:
            reason = re.sub(r"⭐+", "", alignment_text).strip()
        # 截断到 ≤60 字 (推送段紧凑)
        result["alignment_reason"] = reason[:60].strip(" /／.,。、")

    # Rating ⭐ 数 (评级字段, 与 alignment 是不同维度), 同样 clamp
    if rating_text:
        result["rating_stars"] = min(max(_extract_stars(rating_text), 0), 5)

    return result


def scan_source_results(cache_dir: str) -> list[dict[str, Any]]:
    """扫单个 source 的 llm_results.jsonl, 返回成功 LLM 输出列表.

    Args:
        cache_dir: 该 source 的 cache 目录绝对路径

    Returns:
        list of dict, each: {"idx": int, "content": str}
        含 failed=True 或 parse 错误的条目被跳过.

    FAIL-OPEN: 目录或文件缺失 → 空 list 不抛异.
    """
    results_path = os.path.join(cache_dir, RESULTS_FILENAME)
    if not os.path.isfile(results_path):
        return []

    items: list[dict[str, Any]] = []
    try:
        with open(results_path, encoding="utf-8", errors="replace") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    log(f"[top_picker] WARN: {results_path}:{line_no} JSON parse error, skip")
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("failed"):
                    continue
                content = entry.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                items.append({"idx": entry.get("idx", line_no - 1), "content": content})
    except OSError as exc:
        log(f"[top_picker] WARN: cannot read {results_path}: {exc}")
        return []
    return items


def collect_all_picks(repo_root: str | None = None,
                     min_stars: int = DEFAULT_MIN_STARS,
                     sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """扫所有 ALIGNED source, 解析 alignment ⭐, 过滤 ⭐≥min_stars.

    Args:
        repo_root: 仓库根目录 (None → 取脚本所在目录 / Mac Mini 是 $HOME)
        min_stars: 入选最低 ⭐ 数 (默认 4)
        sources: source registry override (默认全部 8 个)

    Returns:
        list of dict: {"source_id", "source_display", "source_priority", "cn_title",
                       "alignment_stars", "alignment_reason"}
    """
    if repo_root is None:
        repo_root = os.path.dirname(os.path.abspath(__file__))
    if sources is None:
        sources = ALIGNED_SOURCES

    picks: list[dict[str, Any]] = []
    for src in sources:
        # V37.9.57: 通过 _resolve_cache_dir 支持 dev + Mac Mini 两种 layout
        cache_dir = _resolve_cache_dir(repo_root, src)
        results = scan_source_results(cache_dir)
        for entry in results:
            parsed = parse_alignment_from_content(entry["content"])
            stars = parsed["alignment_stars"]
            if stars < min_stars:
                continue
            picks.append({
                "source_id": src["id"],
                "source_display": src["display"],
                "source_priority": src["priority"],
                "cn_title": parsed["cn_title"] or "(无中文标题)",
                "alignment_stars": stars,
                "alignment_reason": parsed["alignment_reason"],
            })
    return picks


def rank_picks(picks: list[dict[str, Any]], top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """按 ⭐ 数 desc + source priority asc + 标题长度 desc 排序取 Top N.

    排序 key 组合:
        - 主排: alignment_stars desc (5 > 4 > 3)
        - tie 1: source_priority asc (1=hf_papers > 7=hn)
        - tie 2: len(cn_title) desc (信息密度代理)

    FAIL-OPEN: 空输入 → 空输出. 缺字段 entry 用默认值参与排序.
    """
    if not picks:
        return []

    def _sort_key(p: dict[str, Any]) -> tuple:
        stars = p.get("alignment_stars", 0)
        priority = p.get("source_priority", 999)
        title_len = len(p.get("cn_title", ""))
        # negate stars + title_len for desc, priority asc
        return (-stars, priority, -title_len)

    ranked = sorted(picks, key=_sort_key)
    return ranked[:top_n]


def format_top_picks_block(picks: list[dict[str, Any]], header: str | None = None) -> str:
    """生成 markdown 紧凑段供 kb_dream / kb_evening prompt 注入.

    格式 (每条 1 行, 不超 80 字):
        - ⭐⭐⭐⭐⭐ [HF精选] 标题 / 一句话原因
        - ⭐⭐⭐⭐ [博客] 标题 / 一句话原因

    Args:
        picks: ranked picks list (已排序)
        header: 可选 header 字符串 (默认无)

    Returns:
        str: 多行 markdown 段, 末尾无换行. 空输入 → 空字符串.
    """
    if not picks:
        return ""

    lines: list[str] = []
    if header:
        lines.append(header)

    for p in picks:
        stars = int(p.get("alignment_stars", 0))
        stars = max(0, min(stars, 5))  # clamp [0, 5]
        star_str = "⭐" * stars
        source = p.get("source_display", "?")
        title = p.get("cn_title", "(无标题)")
        # 标题截断到 ≤40 字防止单行过长
        title_display = title[:40] + ("…" if len(title) > 40 else "")
        reason = p.get("alignment_reason", "")
        if reason:
            lines.append(f"- {star_str} [{source}] {title_display} / {reason}")
        else:
            lines.append(f"- {star_str} [{source}] {title_display}")

    return "\n".join(lines)


def pick_top_aligned(repo_root: str | None = None,
                    min_stars: int = DEFAULT_MIN_STARS,
                    top_n: int = DEFAULT_TOP_N) -> dict[str, Any]:
    """主入口 orchestrator: scan → parse → rank → emit block.

    Returns:
        {
          "status": "ok" | "no_picks" | "no_aligned_sources",
          "picks_total": int (符合 min_stars 阈值的总数),
          "picks_top": list[dict] (排序取 Top N 后),
          "block": str (markdown 推送段, no_picks 时为 ""),
        }

    FAIL-OPEN: 任何环节失败 → status="no_picks" + block="" 不抛异.
    """
    try:
        all_picks = collect_all_picks(repo_root=repo_root, min_stars=min_stars)
        ranked = rank_picks(all_picks, top_n=top_n)
        block = format_top_picks_block(ranked)
        status = "ok" if ranked else "no_picks"
        return {
            "status": status,
            "picks_total": len(all_picks),
            "picks_top": ranked,
            "block": block,
        }
    except Exception as exc:
        log(f"[top_picker] ERROR: pick_top_aligned failed: {exc}")
        return {
            "status": "no_picks",
            "picks_total": 0,
            "picks_top": [],
            "block": "",
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="V37.9.56 Sub-Stage 4c Top 5 高对齐推送 picker"
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="仓库根目录 (默认: 脚本所在目录)",
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        default=DEFAULT_MIN_STARS,
        help=f"最低 ⭐ 数阈值 (默认: {DEFAULT_MIN_STARS})",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"取 Top N 条 (默认: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式 (供 shell 解析)",
    )
    parser.add_argument(
        "--block-only",
        action="store_true",
        help="只输出 markdown block (适合 bash 注入 prompt)",
    )
    args = parser.parse_args()

    result = pick_top_aligned(
        repo_root=args.repo_root,
        min_stars=args.min_stars,
        top_n=args.top_n,
    )

    if args.block_only:
        # 只输出 block 字面量, 适合 BLOCK=$(python3 top_alignment_picker.py --block-only)
        print(result.get("block", ""))
        return 0

    if args.json:
        # 输出 JSON 不含原始 picks_top dict 序列化问题
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # Default human-readable output
    status = result.get("status", "no_picks")
    if status == "no_picks":
        print(f"[top_picker] no picks (min_stars={args.min_stars}, scanned {len(ALIGNED_SOURCES)} sources)")
        return 0

    print(f"[top_picker] {result['picks_total']} aligned picks total, Top {len(result['picks_top'])}:")
    print()
    print(result["block"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

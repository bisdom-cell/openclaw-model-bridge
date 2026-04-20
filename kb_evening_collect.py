#!/usr/bin/env python3
"""
kb_evening_collect.py — V37.6 KB 晚间整理数据采集器 + LLM 调用

V37.6 升级理由：原 kb_evening.sh 用"第一行正文"作为摘要，属于"假摘要"：
  - 零 LLM 智能，只是把今日文件名前 80 字拼接
  - 仅扫描 notes，完全忽略 sources（ArXiv/HN/freight 等 cron 抓取的全部价值）
  - 没有 registry 驱动，新增源看不到
  - 没有 fail-fast，"今日无新增知识记录"掩盖了采集失败
  - PA 无法说"今天用户知识库涨了什么"——因为 evening 从未产出过结构化回顾

V37.6 方案：复用 V37.5 kb_review_collect.py 已证明的 6 大架构（Python 化 /
registry-driven / H2 drill-down / LLM 深度分析 / fail-fast / 诚实 status），
只改三件事：
  1. DAYS 默认 1（今日窗口）— kb_review 是 7 天
  2. build_prompt 改成"今日要闻 + 今日行动建议 + 明日预期"结构
  3. 输出契约增加 dedup 报告透传字段（由 shell 层在推送时拼接）

其他一律沿用 kb_review_collect.py helpers（import 而非复制），保证两个 job 的
数据采集层只有一份代码、一份 bug 修复点。

CLI 用法：
  KB_DIR=~/.kb DAYS=1 REGISTRY=jobs_registry.yaml python3 kb_evening_collect.py
  输出：JSON 到 stdout

Exit codes:
  0 — JSON 已产出（status 字段指明 ok / llm_failed / collector_failed）
  1 — 致命错误（参数缺失/注册表不可读），stderr 有原因
"""
import glob
import json
import os
import sys
from datetime import datetime

# 复用 V37.5 kb_review 的所有采集 + LLM 调用原语
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_review_collect as rc


# ══════════════════════════════════════════════════════════════════════
# 0. Today-scoped helpers — V37.7 label semantics fix
# ══════════════════════════════════════════════════════════════════════

def count_today_notes(kb_dir, today=None):
    """Count notes whose filename prefix matches today's date (YYYYMMDD).

    V37.7: fixes V37.6 label bug where `今日笔记 {note_count} 篇` was
    mislabeled — `note_count` from rc.read_index_stats is the *total*
    note count on disk, not today's additions. Evening report's entire
    pitch is "今日 (today)" so this count must be today-scoped.

    Naming convention: files named YYYYMMDDHHMMSS.md (V27+).
    """
    if today is None:
        today = datetime.now()
    today_prefix = today.strftime("%Y%m%d")
    notes_dir = os.path.join(kb_dir, "notes")
    if not os.path.isdir(notes_dir):
        return 0
    return sum(
        1 for p in glob.glob(os.path.join(notes_dir, "*.md"))
        if os.path.basename(p).startswith(today_prefix)
    )


# ══════════════════════════════════════════════════════════════════════
# 1. Evening-specific prompt — 今日窗口 + 行动导向
# ══════════════════════════════════════════════════════════════════════

def build_evening_prompt(
    notes_text, sources_text, days, index_total, note_count,
    today_note_count, themes,
):
    """构造晚间整理 prompt。

    与 kb_review 周回顾的差异：
      - 窗口缩到 1 天，不需要"跨领域关联"长篇分析
      - 要求输出聚焦"今日要闻 + 明日关注"，行动导向
      - 总字数 450 内（WhatsApp 1 屏可见，不需要 review 文件那么长）

    V37.7: 区分 `note_count` (笔记总数，所有 .md 文件) 和 `today_note_count`
    (今日新增笔记)。V37.6 曾把 note_count 错误标签为"今日笔记"，导致 LLM
    prompt 里给出的统计与"今日"不一致。
    """
    return f"""你是一位知识管理助手，请基于用户知识库中今天（最近 {days} 天）新增的内容，
生成一份简洁的晚间整理。用中文回答，总字数 450 内，按以下四节输出：

1. **今日要闻**（2-4 条）：今天最值得记住的信息，每条标注来源（如 [ArXiv]、[HN]、[货代]），一句话说明价值
2. **一条行动**（1 条）：基于今日信息，用户明天最值得做的一件事（具体可执行）
3. **明日关注**（1-2 条）：今天看到的趋势/伏笔，明日值得继续追踪
4. **健康度**（1 句）：今天 KB 吸收质量评分（信号密度/信噪比），并说明依据

⚠️ 严格约束（违反则整份输出作废）：
- 只使用下方"今日笔记"和"今日来源归档"中**明确出现**的信息，禁��添加未出现的内容
- 每条要闻必须能在下方原文中找到对应段落，标注来源标签
- 严禁虚构任何发布公告、开源事件、产品发布、人物言论
- 如果某个领域今天无数据，直接跳过，不要编造

═══ 今日笔记 ═══
{notes_text or '（今日无新增笔记）'}

═══ 今日来源归档 ═══
{sources_text or '（今日无来源归档更新）'}

═══ 基础统计 ═══
知识库总条目: {index_total} 条
笔记总数: {note_count} 篇
今日新增: {today_note_count} 篇
活跃标签: {themes}"""


# ══════════════════════════════════════════════════════════════════════
# 2. Evening 输出构造
# ══════════════════════════════════════════════════════════════════════

def build_evening_markdown(
    date_str, days, llm_content, index_total, note_count, today_note_count,
    themes, sources_used, sources_skipped, sources_missing,
):
    """生成 evening_YYYYMMDD.md 文件内容，写入 ~/.kb/daily/。

    V37.7: 新增 `today_note_count` 参数，分列"笔记总数"（历史累计）和
    "今日新增"（今天实际生产），避免 V37.6 "今日笔记 298 篇"的虚标。
    """
    sources_block_lines = []
    if sources_used:
        sources_block_lines.append(f"**今日覆盖源** ({len(sources_used)}):")
        for label in sources_used:
            sources_block_lines.append(f"  - ✓ {label}")
    if sources_skipped:
        sources_block_lines.append(f"\n**今日无更新**:")
        for label in sources_skipped:
            sources_block_lines.append(f"  - ○ {label}")
    if sources_missing:
        sources_block_lines.append(f"\n**文件缺失**:")
        for label in sources_missing:
            sources_block_lines.append(f"  - ✗ {label}")
    sources_block = "\n".join(sources_block_lines) if sources_block_lines else "（无）"

    return f"""---
date: {date_str}
type: evening
period: {days}days
llm_analyzed: true
sources_used: {len(sources_used)}
sources_missing: {len(sources_missing)}
today_note_count: {today_note_count}
---

# 晚间整理 {date_str}

## 基础统计
- 知识库总条目：{index_total} 条
- 笔记总数：{note_count} 篇
- 今日新增：{today_note_count} 篇
- 活跃标签：{themes}

## 源覆盖
{sources_block}

## LLM 今日整理

{llm_content}
"""


def build_evening_wa_message(
    date_str, days, index_total, note_count, today_note_count,
    llm_content, sources_count,
):
    """生成 WhatsApp/Discord 晚间推送消息。

    V37.7: header 分列"KB 总条目"/"笔记总数"/"今日新增"，消除 V37.6 把
    历史累计笔记数标为"今日笔记"的误导。
    """
    header = (
        f"🌙 晚间整理 {date_str}"
        f"（KB 总条目 {index_total} | 笔记总数 {note_count} "
        f"| 今日新增 {today_note_count} 篇 | 覆盖 {sources_count} 源）"
    )
    body = llm_content[:1400] if len(llm_content) > 1400 else llm_content
    return f"{header}\n\n{body}"


# ══════════════════════════════════════════════════════════════════════
# 3. Main orchestrator — 复用 rc 的采集 + LLM，叠加 evening 特定输出
# ══════════════════════════════════════════════════════════════════════

# Evening 窗口更小，budget 可以更紧
MAX_NOTES_CHARS = 3500
MAX_SOURCE_CHARS = 2500  # per source file
PROMPT_TRUNCATE_NOTES = 3000
PROMPT_TRUNCATE_SOURCES = 4000


def run(kb_dir, days, registry_path, today=None, llm_caller=None):
    """Orchestrate evening collect → call → build pipeline.

    与 kb_review_collect.run 契约完全一致：
      - 返回 dict，status ∈ {ok, llm_failed, collector_failed}
      - llm_failed / collector_failed 路径**不**产出 evening_markdown / wa_message
      - 注入 llm_caller 可在单测中替换（同 kb_review 测试模式）
    """
    date_str = (today or datetime.now()).strftime("%Y%m%d")

    # Collect — 直接复用 kb_review_collect 的原语
    index_total, note_count, themes = rc.read_index_stats(kb_dir)
    # V37.7: today_note_count ≠ note_count（total），fix the V37.6 label bug
    today_note_count = count_today_notes(kb_dir, today=today)
    notes_text = rc.collect_notes(kb_dir, days, MAX_NOTES_CHARS, today=today)

    try:
        sources_info = rc.collect_sources(
            kb_dir, registry_path, days, MAX_SOURCE_CHARS, today=today
        )
    except FileNotFoundError as e:
        return {
            "status": "collector_failed",
            "reason": str(e),
            "date": date_str,
            "days": days,
        }

    # Build evening-specific prompt
    prompt_notes = notes_text[:PROMPT_TRUNCATE_NOTES]
    prompt_sources = sources_info["text"][:PROMPT_TRUNCATE_SOURCES]
    prompt = build_evening_prompt(
        prompt_notes, prompt_sources, days, index_total, note_count,
        today_note_count, themes,
    )

    # Call LLM — 复用 rc.call_llm（同一个 proxy URL/timeout/min-length 契约）
    caller = llm_caller if llm_caller is not None else rc.call_llm
    ok, llm_content, reason = caller(prompt)

    if not ok:
        return {
            "status": "llm_failed",
            "reason": reason,
            "date": date_str,
            "days": days,
            "index_total": index_total,
            "note_count": note_count,
            "today_note_count": today_note_count,
            "themes": themes,
            "sources_used": sources_info["used"],
            "sources_skipped": sources_info["skipped"],
            "sources_missing": sources_info["missing"],
        }

    # Build evening-specific output artifacts
    evening_md = build_evening_markdown(
        date_str, days, llm_content, index_total, note_count,
        today_note_count, themes,
        sources_info["used"], sources_info["skipped"], sources_info["missing"],
    )
    wa_message = build_evening_wa_message(
        date_str, days, index_total, note_count, today_note_count,
        llm_content, len(sources_info["used"]),
    )

    return {
        "status": "ok",
        "date": date_str,
        "days": days,
        "index_total": index_total,
        "note_count": note_count,
        "today_note_count": today_note_count,
        "themes": themes,
        "sources_used": sources_info["used"],
        "sources_skipped": sources_info["skipped"],
        "sources_missing": sources_info["missing"],
        "llm_content": llm_content,
        "evening_markdown": evening_md,
        "wa_message": wa_message,
    }


def main():
    kb_dir = os.environ.get("KB_DIR") or os.path.expanduser("~/.kb")
    days = int(os.environ.get("DAYS") or "1")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    registry = os.environ.get("REGISTRY") or os.path.join(
        script_dir, "jobs_registry.yaml"
    )

    try:
        result = run(kb_dir, days, registry)
    except Exception as e:
        err = {
            "status": "collector_failed",
            "reason": f"{type(e).__name__}: {e}",
            "days": days,
        }
        print(json.dumps(err, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    # Exit 0 even on llm_failed — bash wrapper decides how to handle
    sys.exit(0)


if __name__ == "__main__":
    main()

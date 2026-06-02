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

# V37.9.57: 公共反幻觉守卫模板 (MR-8 single-source-of-truth, 替代 V37.9.56-hotfix3 inline)
# kb_evening 需要 LEVEL_5_RADAR_AWARE: 含 V37.9.56-hotfix3 OpenClaw 项目动态编造禁令
# + Opportunity Radar 三件套信号源契约 (Top 5/cross_source/trend 跨多天非今日事件)
from hallucination_guards import get_guard

# V37.9.98: 来源可信度评级 (observer 5/28 proposal #2). 与 get_guard 互补 —
# guard 守内容真实性, credibility 守出处权威性 (非主流源如 chaspark 须标注可信度).
from source_credibility import format_credibility_block


# ══════════════════════════════════════════════════════════════════════
# 0a. Job failure scanner — V37.9.84 observer proposal #3/#5 (failure impact)
# ══════════════════════════════════════════════════════════════════════

_JOBS_CACHE_PATHS = [
    os.path.expanduser("~/.openclaw/jobs"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs"),
]

_FAILURE_STATUSES = {"llm_failed", "fetch_failed", "send_failed", "partial_degraded"}


def collect_job_failures(today=None):
    """Scan last_run.json from all job cache dirs, return failures.

    Returns:
        list of dict: [{job_id, status, reason}, ...] for non-ok jobs.
        Empty list if all ok or no cache found (FAIL-OPEN).
    """
    if today is None:
        today = datetime.now()
    failures = []
    seen = set()
    for base in _JOBS_CACHE_PATHS:
        if not os.path.isdir(base):
            continue
        try:
            subdirs = os.listdir(base)
        except OSError:
            continue
        for job_id in subdirs:
            if job_id in seen:
                continue
            lr = os.path.join(base, job_id, "cache", "last_run.json")
            if not os.path.isfile(lr):
                continue
            seen.add(job_id)
            try:
                with open(lr, "r", encoding="utf-8") as f:
                    data = json.load(f)
                status = data.get("status", "")
                if status in _FAILURE_STATUSES:
                    failures.append({
                        "job_id": job_id,
                        "status": status,
                        "reason": data.get("reason", ""),
                    })
            except (OSError, json.JSONDecodeError):
                continue
    return failures


def format_job_failures_block(failures):
    """Format job failures as a prompt injection block.

    Returns empty string if no failures (backward compatible).
    """
    if not failures:
        return ""
    lines = ["\n\n═══ 今日任务异常 (自动检测) ═══"]
    for f in failures:
        reason = f" ({f['reason'][:80]})" if f.get("reason") else ""
        lines.append(f"- {f['job_id']}: {f['status']}{reason}")
    lines.append(
        "\n注意: 如果上述异常影响了信源覆盖 (如某个论文源抓取失败), "
        "请在'今日要闻'或'健康度'段简要提及, 让用户了解今日信息可能不完整的领域."
    )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 0b. Today-scoped helpers — V37.7 label semantics fix
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

def collect_trend_signals_for_evening(radar_dir=None, max_strong=3, max_decel=2):
    """V37.9.49 Sub-Stage 4a: 读取最新 weekly_trends_*.json 转格式化 prompt 字符串.

    Args:
        radar_dir: ~/.kb/radar/ (default)
        max_strong: top N strong/mild signals
        max_decel: top N decel/obs signals

    Returns:
        str: 格式化好的 trend section (空字符串如无数据 / 模块缺失 / 失败)

    FAIL-OPEN: kb_trend_acceleration.py 缺失 / 无 weekly_trends 文件 → 返回空字符串
                不阻塞 evening 主流程.
    """
    if radar_dir is None:
        kb_dir = os.path.expanduser(os.environ.get("KB_BASE", "~/.kb"))
        radar_dir = os.path.join(kb_dir, "radar")

    if not os.path.isdir(radar_dir):
        return ""

    trend_files = sorted(glob.glob(os.path.join(radar_dir, "weekly_trends_*.json")),
                         reverse=True)
    if not trend_files:
        return ""

    try:
        with open(trend_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""

    signals = data.get("signals", [])
    if not signals:
        return ""

    strong_lines = []
    decel_lines = []
    for s in signals:
        cls = s.get("classification", "")
        kw = s.get("keyword", "")
        a1 = s.get("accel_1w", 0)
        if not kw:
            continue
        if "strong" in cls or "mild" in cls:
            if len(strong_lines) < max_strong:
                strong_lines.append(f"  {cls.split()[0]} {kw}: 本周占比 {a1}x")
        elif "decel" in cls or "obs" in cls:
            if len(decel_lines) < max_decel:
                decel_lines.append(f"  {cls.split()[0]} {kw}: 本周占比 {a1}x")

    parts = []
    if strong_lines:
        parts.append("加速主题 (本周关键词占比上升):\n" + "\n".join(strong_lines))
    if decel_lines:
        parts.append("减速主题 (本周关键词占比下降):\n" + "\n".join(decel_lines))
    return "\n\n".join(parts)


def collect_top_alignment_picks_for_evening(repo_root=None, min_stars=4, top_n=5):
    """V37.9.56 Sub-Stage 4c: 读今日 8 ALIGNED source 高对齐 Top N → 推送段.

    Args:
        repo_root: 仓库根目录 (None → 取本脚本所在目录, Mac Mini 是 $HOME)
        min_stars: 入选最低 ⭐ 数阈值 (默认 4, 与 top_alignment_picker 同款)
        top_n: 取 Top N (默认 5)

    Returns:
        str: 格式化好的 alignment section (空字符串如无 picks / 模块缺失)

    FAIL-OPEN: top_alignment_picker 缺失 / 无 picks / 异常 → 返回空字符串
                不阻塞 evening 主流程.
    """
    try:
        import top_alignment_picker as _tap
    except Exception:
        return ""

    try:
        result = _tap.pick_top_aligned(
            repo_root=repo_root, min_stars=min_stars, top_n=top_n,
        )
    except Exception:
        return ""

    if not isinstance(result, dict) or result.get("status") != "ok":
        return ""

    block = result.get("block", "")
    if not block:
        return ""
    return block


def build_evening_prompt(
    notes_text, sources_text, days, index_total, note_count,
    today_note_count, themes, trend_signals=None, top_alignment_picks=None,
    job_failures_block="",
):
    """构造晚间整理 prompt。

    与 kb_review 周回顾的差异：
      - 窗口缩到 1 天，不需要"跨领域关联"长篇分析
      - 要求输出聚焦"今日要闻 + 明日关注"，行动导向
      - 总字数 450 内（WhatsApp 1 屏可见，不需要 review 文件那么长）

    V37.7: 区分 `note_count` (笔记总数，所有 .md 文件) 和 `today_note_count`
    (今日新增笔记)。V37.6 曾把 note_count 错误标签为"今日笔记"，导致 LLM
    prompt 里给出的统计与"今日"不一致。

    V37.9.49 Sub-Stage 4a (Opportunity Radar): trend_signals 可选参数注入
    "本周加速主题/减速主题"上下文 (来自 kb_trend_acceleration.py 输出),
    辅助 LLM 在"明日关注"段给出更有信息密度的趋势判断. None / 空字符串
    → 不影响 prompt 行为 (向后兼容).

    V37.9.56 Sub-Stage 4c (Opportunity Radar #2): top_alignment_picks 可选参数
    注入"今日高对齐 Top 5"上下文 (来自 top_alignment_picker.py 输出, ⭐≥4 过滤
    后排序的 paper/repo/blog/tweet markdown 段). 辅助 LLM 在"今日要闻"段优先
    引用真正与项目方向直接相关的内容, 减少信息洪流稀释. None / 空字符串
    → 不影响 prompt 行为 (向后兼容).
    """
    trend_block = ""
    if trend_signals:
        trend_block = (
            "\n\n═══ 本周趋势加速度 (V37.9.48 4 周历史) ═══\n"
            + str(trend_signals)
            + "\n\n注意: 上述加速主题用于辅助'明日关注'段判断方向, "
            + "不能直接当今日要闻输出 (必须与今日笔记/来源中具体内容关联才可提及)"
        )

    alignment_block = ""
    # V37.9.56: strip() 守卫防止 whitespace-only 字符串 (如 picker 输出末尾换行)
    # 触发误注入空 block, 让 prompt 出现空"今日高对齐"段.
    # V37.9.56-hotfix3 (2026-05-12 血案修): 旧设计"在'今日要闻'段优先引用"指令在 LLM
    # 训练倾向下触发链式幻觉 — 9:41 evening 编造"OpenClaw 社区发布 v26" 推送给用户.
    # 真因: Top 5 提到"OpenClaw 项目对齐"+ 今日笔记少 → LLM 合理化"项目必然有版本更新"
    # → 编造来源标签 [openclaw] + 虚构事件. 修复: 注入软化 + 具体字面禁令.
    if top_alignment_picks and str(top_alignment_picks).strip():
        alignment_block = (
            "\n\n═══ 近期高对齐参考阅读 Top 5 (V37.9.56 #2, ⭐≥4 项目对齐度, 跨多天累积) ═══\n"
            + str(top_alignment_picks).strip()
            + "\n\n注意 (V37.9.56-hotfix3): 上述仅为'外部参考阅读列表', **不是今日发生的事件**. "
            + "8 个论文/repo/blog source 的**多日累积**高对齐内容 (非今日新闻). "
            + "你**绝不应**在'今日要闻'/'明日关注'段硬性引用上述条目, "
            + "除非今日笔记/来源中**实际出现**对应内容. Top 5 仅作为'背景知识参考', 不作为'新闻事件'."
        )

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
{get_guard("LEVEL_5_RADAR_AWARE")}{format_credibility_block()}

═══ 今日笔记 ═══
{notes_text or '（今日无新增笔记）'}

═══ 今日来源归档 ═══
{sources_text or '（今日无来源归档更新）'}

═══ 基础统计 ═══
知识库总条目: {index_total} 条
笔记总数: {note_count} 篇
今日新增: {today_note_count} 篇
活跃标签: {themes}{alignment_block}{trend_block}{job_failures_block}"""


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
    # V37.9.35: bump budget 1400→4000 — see kb_review_collect.py same change
    body = llm_content[:4000] if len(llm_content) > 4000 else llm_content
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
    # V37.9.49 Sub-Stage 4a: Opportunity Radar #3 trend acceleration injection
    trend_signals = collect_trend_signals_for_evening()
    # V37.9.56 Sub-Stage 4c: Opportunity Radar #2 high-alignment Top 5 injection
    top_alignment_picks = collect_top_alignment_picks_for_evening()
    # V37.9.84: job failure visibility (observer proposal #3/#5)
    job_failures = collect_job_failures(today=today)
    job_failures_block = format_job_failures_block(job_failures)
    prompt = build_evening_prompt(
        prompt_notes, prompt_sources, days, index_total, note_count,
        today_note_count, themes, trend_signals=trend_signals,
        top_alignment_picks=top_alignment_picks,
        job_failures_block=job_failures_block,
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

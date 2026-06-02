#!/usr/bin/env python3
"""V37.9.99 Opportunity Radar Stage 5 — 每日机会点雷达综合 collector.

═══════════════════════════════════════════════════════════════════════════
设计文档 docs/opportunity_radar_design.md 第六节 (6.1) 兑现.

职责: 消费 Opportunity Radar 三件套输出, 按"件套交集"分 红/黄/蓝 三档机会点,
早晨 06:00 推送综合雷达扫描. 三件套各自已做 LLM 分析 (#1 聚类 / #2 对齐评分 /
#3 趋势数学), 本 collector 的职责是 **交集 + 排序 + 呈现** — 这本质是规则化的,
不需新增 06:00 LLM 调用 (更鲁棒: 确定性 + 无 fail-fast 吵醒用户 + 零 token).
LLM prose 润色是 V+1 增强 (设计文档"重要性排序+联动检测"已用规则评分实现).

数据来源 (FAIL-OPEN: 任一缺失 → 该段空, 不阻塞其他段):
  #1 跨 source 共振 ← ~/.kb/radar/daily_signals_{YESTERDAY}.json
       (cross_source_signal_aggregator.py V37.9.46, signals[].suggested_topic + sources)
  #2 项目高对齐  ← top_alignment_picker.pick_top_aligned() (⭐≥4 picks[].cn_title)
  #3 趋势加速    ← ~/.kb/radar/weekly_trends_{latest}.json
       (kb_trend_acceleration.py V37.9.48, signals[].keyword + classification + accel_1w)

红/黄/蓝分档 (件套交集, 主题用 kb_dream_helpers.themes_overlap 跨源匹配):
  🚨 红色 = 候选主题命中全部 3 件套 (跨源共振 ∩ 高对齐⭐≥4 ∩ 趋势加速) = 最高优先机会点
  ⚠️ 黄色 = 命中其中 2 件套
  📈 蓝色 = 命中 1 件套 (主要是 #3 趋势观察: 加速主题 + 减速主题)

复用 (MR-8 单一真理源, 不 copy-paste): kb_dream_helpers.themes_overlap /
normalize_theme_keywords (V37.9.68 主题匹配) + top_alignment_picker (V37.9.56 #2).
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timedelta

_V37_9_99_MARKER = "V37.9.99 Opportunity Radar Stage 5"

DEFAULT_KB_DIR = os.path.expanduser("~/.kb")
RADAR_SUBDIR = "radar"
MIN_ALIGN_STARS = 4
# #3 classification 视为"加速"的档位 (kb_trend_acceleration 5 档)
_ACCEL_CLASSES = ("strong", "mild")
_DECEL_CLASSES = ("decel", "deceleration", "obsolescence", "obs")


def log(msg):
    """MR-11: 写 stderr 防 $(...) 命令替换污染."""
    print(msg, file=sys.stderr)


# ── 三件套读取 (各自 FAIL-OPEN 返回 []) ───────────────────────────────
def read_cross_source_signals(date_str, kb_dir=DEFAULT_KB_DIR):
    """#1: 读 daily_signals_{date}.json 的 signals (FAIL-OPEN 缺文件→[])."""
    path = os.path.join(kb_dir, RADAR_SUBDIR, f"daily_signals_{date_str}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        out = []
        for s in data.get("signals", []):
            topic = (s.get("suggested_topic") or "").strip()
            if topic:
                out.append({
                    "topic": topic,
                    "sources": s.get("sources", []),
                    "source_count": s.get("source_count", 0),
                    "score": s.get("score", 0.0),
                })
        return out
    except (OSError, ValueError, KeyError):
        return []


def read_trend_signals(kb_dir=DEFAULT_KB_DIR):
    """#3: 读最新 weekly_trends_*.json 的 signals (FAIL-OPEN 缺文件→[]).

    设计文档用 weekly_trends_current.json, 但 emit 写 weekly_trends_{week}.json —
    优先 current, 否则取最新 glob (mtime), 双兼容.
    """
    radar = os.path.join(kb_dir, RADAR_SUBDIR)
    candidates = []
    cur = os.path.join(radar, "weekly_trends_current.json")
    if os.path.isfile(cur):
        candidates.append(cur)
    globbed = sorted(glob.glob(os.path.join(radar, "weekly_trends_*.json")),
                     key=lambda p: os.path.getmtime(p) if os.path.isfile(p) else 0,
                     reverse=True)
    candidates += [g for g in globbed if g not in candidates]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            out = []
            for s in data.get("signals", []):
                kw = (s.get("keyword") or s.get("kw") or "").strip()
                if kw:
                    out.append({
                        "topic": kw,
                        "classification": (s.get("classification") or "").strip().lower(),
                        "accel_1w": s.get("accel_1w"),
                    })
            return out
        except (OSError, ValueError, KeyError):
            continue
    return []


def read_alignment_picks(repo_root=None, min_stars=MIN_ALIGN_STARS):
    """#2: 经 top_alignment_picker 取 ⭐≥min picks (lazy import + FAIL-OPEN → [])."""
    try:
        if repo_root is None:
            repo_root = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, repo_root)
        sys.path.insert(0, os.path.expanduser("~"))
        import top_alignment_picker as tap
        result = tap.pick_top_aligned(repo_root=repo_root, min_stars=min_stars)
        picks = result.get("picks_top") or result.get("picks") or []
        out = []
        for p in picks:
            title = (p.get("cn_title") or "").strip()
            if title:
                out.append({
                    "topic": title,
                    "stars": p.get("alignment_stars", 0),
                    "source_display": p.get("source_display", ""),
                    "reason": p.get("alignment_reason", ""),
                })
        return out
    except Exception as e:  # noqa: BLE001 — FAIL-OPEN, #2 缺失不阻塞雷达
        log(f"WARN: top_alignment_picker 不可用, #2 高对齐段空: {e}")
        return []


# ── 主题跨源匹配 (复用 kb_dream_helpers, MR-8) ────────────────────────
def _topics_match(a, b):
    """两主题是否指同一概念 (复用 kb_dream_helpers.themes_overlap, FAIL 退化精确串包含).

    themes_overlap 接受 normalize 后的关键词集合 (非原始串), 故先 normalize.
    跨语言匹配局限 (英文 #1/#3 keyword vs 中文 #2 cn_title): themes_overlap 基于 token
    共现, 同语言主题匹配可靠, 跨语言 (中↔英) 共享 token 少时退化 (RED 三件套交集
    在跨语言命名下较难触发, 是已知限制; V+1 可加 embedding/翻译匹配). 子串 fallback
    覆盖部分跨语言 (如英文术语嵌在中文标题中).
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.expanduser("~"))
        import kb_dream_helpers as kdh
        if kdh.themes_overlap(kdh.normalize_theme_keywords(a), kdh.normalize_theme_keywords(b)):
            return True
        # 补充: 英文术语子串 (中文标题内嵌英文术语时 themes_overlap 可能漏)
        al, bl = a.lower(), b.lower()
        return al in bl or bl in al
    except Exception:  # noqa: BLE001 — FAIL-OPEN 退化为子串包含
        al, bl = a.lower(), b.lower()
        return al in bl or bl in al


def _any_match(topic, candidates):
    """topic 是否匹配 candidates 中任一 (返回首个匹配项或 None)."""
    for c in candidates:
        if _topics_match(topic, c["topic"]):
            return c
    return None


# ── 红/黄/蓝 分档 (件套交集) ──────────────────────────────────────────
def classify_opportunities(cross, align, trend):
    """三件套交集分 红/黄/蓝.

    Returns:
        dict {red: [...], yellow: [...], blue_accel: [...], blue_decel: [...]}
        red/yellow 项含 topic / hits (命中件套数) / cross / align / trend 引用 + 建议行动.
        blue 项是未交集的趋势观察 (加速/减速主题).
    """
    # 候选主题 = #1 跨源 ∪ #2 高对齐 (这两个是"机会点"候选; #3 是趋势属性)
    accel_trends = [t for t in trend if t.get("classification") in _ACCEL_CLASSES]
    decel_trends = [t for t in trend if t.get("classification") in _DECEL_CLASSES]

    red, yellow = [], []
    seen_topics = []  # 已归类主题 (避免重复)

    # 以 #1 跨源信号为主轴 (跨源共振是机会点最强信号)
    for cs in cross:
        topic = cs["topic"]
        if any(_topics_match(topic, st) for st in seen_topics):
            continue
        align_hit = _any_match(topic, align)
        trend_hit = _any_match(topic, accel_trends)
        hits = 1 + (1 if align_hit else 0) + (1 if trend_hit else 0)
        item = {
            "topic": topic, "hits": hits, "cross": cs,
            "align": align_hit, "trend": trend_hit,
        }
        if hits == 3:
            item["action"] = "优先今日 22:30 deep_dive picker (三件套交集 = 高价值早期信号)"
            red.append(item)
            seen_topics.append(topic)
        elif hits == 2:
            item["action"] = "值得追踪 (二件套命中, 关注后续累积)"
            yellow.append(item)
            seen_topics.append(topic)
        # hits==1 (仅跨源, 无对齐无趋势) → 不单列红黄, 跨源单信号价值有限

    # #2 高对齐但未在 #1 跨源出现的 → 检查是否 + 趋势 = 黄, 否则不强列
    for al in align:
        topic = al["topic"]
        if any(_topics_match(topic, st) for st in seen_topics):
            continue
        trend_hit = _any_match(topic, accel_trends)
        if trend_hit:  # 高对齐 + 趋势加速 (但无跨源) = 黄色
            yellow.append({
                "topic": topic, "hits": 2, "cross": None,
                "align": al, "trend": trend_hit,
                "action": "值得追踪 (高对齐 + 趋势加速, 待跨源共振确认)",
            })
            seen_topics.append(topic)

    # 蓝色 = 趋势观察 (加速/减速主题, 未进红黄的)
    blue_accel = [t for t in accel_trends
                  if not any(_topics_match(t["topic"], st) for st in seen_topics)]
    blue_decel = decel_trends  # 减速主题始终列入蓝色观察

    # 红色内部按 (跨源 source_count + 对齐 stars) 排序 (重要性)
    red.sort(key=lambda x: (x["cross"]["source_count"] if x["cross"] else 0)
             + (x["align"]["stars"] if x["align"] else 0), reverse=True)

    return {"red": red, "yellow": yellow,
            "blue_accel": blue_accel, "blue_decel": blue_decel}


# ── 输出构造 ──────────────────────────────────────────────────────────
def _read_kb_stats(kb_dir=DEFAULT_KB_DIR):
    """数据复利状态 (FAIL-OPEN, 只读不算): chunks 数 (text_index meta) + notes 数."""
    stats = {"chunks": None, "notes": None}
    try:
        meta = os.path.join(kb_dir, "text_index", "meta.json")
        if os.path.isfile(meta):
            with open(meta, encoding="utf-8") as f:
                m = json.load(f)
            stats["chunks"] = len(m.get("chunks", [])) if isinstance(m.get("chunks"), list) else m.get("chunk_count")
    except (OSError, ValueError):
        pass
    try:
        notes_dir = os.path.join(kb_dir, "notes")
        if os.path.isdir(notes_dir):
            stats["notes"] = len([n for n in os.listdir(notes_dir) if n.endswith(".md")])
    except OSError:
        pass
    return stats


def build_radar_briefing(buckets, date_str, kb_stats=None):
    """生成 (markdown, wa_message, discord_message) 三态输出."""
    kb_stats = kb_stats or {}
    red, yellow = buckets["red"], buckets["yellow"]
    blue_accel, blue_decel = buckets["blue_accel"], buckets["blue_decel"]

    lines = [f"🛸 早晨机会点雷达 ({date_str})", ""]

    lines.append("═══ 🚨 红色机会点 (三件套交集) ═══")
    if red:
        for i, r in enumerate(red, 1):
            cs, al, tr = r["cross"], r["align"], r["trend"]
            lines.append(f"🚨 [机会点 {i}] {r['topic']}")
            if cs:
                lines.append(f"   📡 跨 source 共振: {' + '.join(cs['sources'])} ({cs['source_count']} 源)")
            if al:
                lines.append(f"   🎚️ 项目对齐度: {'⭐' * int(al['stars'])}")
            if tr:
                a1 = tr.get("accel_1w")
                lines.append(f"   📈 趋势加速: {tr['classification']}" + (f" ({a1}x)" if a1 else ""))
            lines.append(f"   💡 建议行动: {r['action']}")
    else:
        lines.append("（昨日无三件套交集信号 — 暂无红色机会点）")
    lines.append("")

    lines.append("═══ ⚠️ 黄色信号 (二件套命中) ═══")
    if yellow:
        for i, y in enumerate(yellow, 1):
            parts = []
            if y["cross"]:
                parts.append("跨源✓")
            if y["align"]:
                parts.append(f"对齐{'⭐' * int(y['align']['stars'])}")
            if y["trend"]:
                parts.append("趋势加速✓")
            lines.append(f"⚠️ [信号 {i}] {y['topic']}  ({' + '.join(parts)})")
    else:
        lines.append("（暂无二件套信号）")
    lines.append("")

    lines.append("═══ 📈 蓝色趋势观察 (单件套) ═══")
    if blue_accel:
        lines.append("📈 加速主题:")
        for t in blue_accel[:5]:
            a1 = t.get("accel_1w")
            lines.append(f"- {t['topic']}" + (f" (本周 {a1}x)" if a1 else ""))
    if blue_decel:
        lines.append("⚰️ 减速主题:")
        for t in blue_decel[:3]:
            lines.append(f"- {t['topic']}")
    if not blue_accel and not blue_decel:
        lines.append("（暂无趋势观察数据）")
    lines.append("")

    lines.append("═══ 📊 数据复利状态 ═══")
    if kb_stats.get("chunks") is not None:
        lines.append(f"📊 KB 累积: {kb_stats['chunks']} chunks")
    if kb_stats.get("notes") is not None:
        lines.append(f"📚 笔记: {kb_stats['notes']} 篇")
    lines.append(f"🚨 红 {len(red)} / ⚠️ 黄 {len(yellow)} / 📈 蓝加速 {len(blue_accel)} / ⚰️ 蓝减速 {len(blue_decel)}")

    markdown = "\n".join(lines)
    # WA/Discord 同款全文 (雷达报告通常 < 4000 字, V37.9.35 客户端折叠机制)
    return markdown, markdown, markdown


# ── orchestrator ──────────────────────────────────────────────────────
def run(today=None, kb_dir=DEFAULT_KB_DIR, repo_root=None):
    """端到端: 读三件套 → 分档 → 生成 briefing. FAIL-OPEN 整体不抛.

    Returns dict: status (ok/no_data/collector_failed) + briefing/wa/discord + counts.
    """
    try:
        if today is None:
            today = datetime.now()
        elif isinstance(today, str):
            today = datetime.strptime(today, "%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = (today - timedelta(days=1)).strftime("%Y%m%d")  # #1 用 YYYYMMDD

        cross = read_cross_source_signals(yesterday_str, kb_dir)
        # 兼容 #1 也可能用今天日期 (cron 在凌晨跑, 当日 signals 可能已生成)
        if not cross:
            cross = read_cross_source_signals(today.strftime("%Y%m%d"), kb_dir)
        trend = read_trend_signals(kb_dir)
        align = read_alignment_picks(repo_root, MIN_ALIGN_STARS)

        buckets = classify_opportunities(cross, align, trend)
        kb_stats = _read_kb_stats(kb_dir)
        markdown, wa, discord = build_radar_briefing(buckets, today_str, kb_stats)

        total_signals = (len(buckets["red"]) + len(buckets["yellow"])
                         + len(buckets["blue_accel"]) + len(buckets["blue_decel"]))
        status = "ok" if total_signals > 0 else "no_data"
        return {
            "status": status,
            "date": today_str,
            "red_count": len(buckets["red"]),
            "yellow_count": len(buckets["yellow"]),
            "blue_accel_count": len(buckets["blue_accel"]),
            "blue_decel_count": len(buckets["blue_decel"]),
            "briefing_markdown": markdown,
            "wa_message": wa,
            "discord_message": discord,
        }
    except Exception as e:  # noqa: BLE001 — 顶层 FAIL-OPEN, 绝不冒泡到 shell
        log(f"ERROR: kb_radar collector 失败: {e}")
        return {"status": "collector_failed", "error": str(e),
                "red_count": 0, "yellow_count": 0,
                "blue_accel_count": 0, "blue_decel_count": 0,
                "briefing_markdown": "", "wa_message": "", "discord_message": ""}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="V37.9.99 Opportunity Radar Stage 5 综合 collector")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD (默认今天)")
    parser.add_argument("--kb-dir", default=DEFAULT_KB_DIR)
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--briefing", action="store_true", help="只输出 briefing markdown")
    args = parser.parse_args()

    result = run(today=args.today, kb_dir=args.kb_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.briefing:
        print(result["briefing_markdown"])
    else:
        print(f"status={result['status']} red={result['red_count']} "
              f"yellow={result['yellow_count']} blue_accel={result['blue_accel_count']} "
              f"blue_decel={result['blue_decel_count']}")
        print()
        print(result["briefing_markdown"])
    # FAIL-OPEN: no_data 也 exit 0 (明日再试), 仅 collector_failed exit 1
    sys.exit(1 if result["status"] == "collector_failed" else 0)


if __name__ == "__main__":
    main()

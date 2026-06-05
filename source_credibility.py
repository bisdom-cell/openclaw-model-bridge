#!/usr/bin/env python3
"""V37.9.98 来源可信度评级 — Opportunity Radar observer proposal #2 兑现.

═══════════════════════════════════════════════════════════════════════════
背景 (observer 自我提议):
  V37.9.84 daily_observer 的 5/28 LLM critique 提议:
    "所有非主流学术源 (如 chaspark 华为茶思屋) 在首次引用时需附带来源可信度
     评级 — 是否同行评审 / 是否有 DOI / 是否可公开验证."

  这是 MR-7 治理自观察的延伸 — observer 不仅发现 dream 链式幻觉 (→ V37.9.89
  LEVEL_6), 还主动识别"来源可信度盲区": dream/evening LLM 把 chaspark 社区
  文章 (无同行评审/无 DOI/难公开验证) 与 ArXiv 论文等同引用, 用户无法区分
  "已发表学术结论"和"社区博客断言".

  V37.9.89 LEVEL_6 解决的是"跨域推论的可信度"(关联强度), 本模块解决的是
  "来源本身的可信度"(出处权威性) — 两个互补的可信度轴.

═══════════════════════════════════════════════════════════════════════════
5 档可信度 (按"引用断言无需独立验证的可信程度"递减):

  🥇 学术同行评审 (rank 1) — 已发表 / 有 DOI / 可公开验证
     可作学术结论引用. 源: Semantic Scholar / DBLP / ACL Anthology /
     Ontology 专属源 (W3C/JWS/DKE/KBS 期刊).

  🥈 会议预印本 (rank 2) — 公开可引但未经同行评审, 结论可能被后续推翻
     源: ArXiv / HuggingFace Daily Papers (本质是 arxiv preprint).

  🥉 工业实践 (rank 3) — 官方代码 / 发布, 可验证存在但宣称需独立验证
     源: GitHub Trending / OpenClaw Releases.

  📝 博客 (rank 4) — 个人 / 社区撰写, 无正式评审, 作者个人观点
     源: RSS 技术博客 / 华为茶思屋科技 (observer 点名的"非主流学术源").

  💬 社媒/资讯 (rank 5) — 推文 / 论坛 / 新闻聚合, 时效强但可靠性参差
     首次引用必须标注, 不可当结论陈述. 源: AI Leaders X / HackerNews /
     货代动态 / 全球财经政策.

设计取舍 (rank 顺序):
  设计 (unfinished #1) 枚举为 [学术同行评审/工业实践/会议预印本/博客/社媒],
  本模块按"引用断言可信度"重排为 学术同行评审(1) > 会议预印本(2) >
  工业实践(3) > 博客(4) > 社媒(5). 理由: 预印本虽未评审但是学术可引内容
  (有作者署名+方法论), 工业代码可验证存在但"宣称"常含营销成分. 这是
  judgment call, 5 个 tier 名严格遵循设计.

═══════════════════════════════════════════════════════════════════════════
使用方式 (镜像 hallucination_guards.py):

  # 1. prompt 注入 (dream / evening)
  from source_credibility import format_credibility_block
  prompt = base_prompt + format_credibility_block()

  # 2. 单源查询 (调试 / 未来 per-source 标注)
  from source_credibility import get_credibility
  info = get_credibility("chaspark")
  # → {"source_id":"chaspark","label":"🏠 华为茶思屋科技","tier":"博客",
  #    "rank":4,"emoji":"📝","verifiability":"...","trust_note":"..."}

  # 3. 列出 (debug / 测试 / 文档生成)
  list_tiers()    # ["学术同行评审", ..., "社媒"]  (rank 升序)
  list_sources()  # ["arxiv_monitor", ..., "finance_news"]

═══════════════════════════════════════════════════════════════════════════
契约:
  - SOURCE_CREDIBILITY 是 source→tier 的单一真理源 (MR-8). 必须与
    jobs_registry.yaml 的 15 个内容源 (含 kb_source_file 字段) 保持一致 —
    test_source_credibility.TestSourceCoverage 守卫 drift (id + label 双匹配).
  - format_credibility_block() 输出以 \\n\\n 开头便于 append 到 base prompt
  - 守卫块以 "⚠️" 开头让 LLM 注意力高位置识别 (与 hallucination_guards 一致)
  - 未知 source 默认按 _DEFAULT_TIER (社媒/最低可信度) 处理, 不抛异
  - 纯 stdlib 零外部依赖

MR-7 兑现: observer 自己提议 → Claude 实施 (V37.9.83 AI Partnership 闭环延续)
MR-8 兑现: 单一真理源, dream/evening 共享同款评级, 无 copy-paste 漂移
"""

from __future__ import annotations


# V37.9.98 marker (源码级守卫识别用)
_V37_9_98_MARKER = "V37.9.98 来源可信度评级"


# ════════════════════════════════════════════════════════════════════
# 5 档可信度 tier 语义 (rank 升序 = 可信度递减)
# rank: 1=最可信(引用无需独立验证) → 5=最需独立验证
# ════════════════════════════════════════════════════════════════════
_CREDIBILITY_TIERS: dict[str, dict] = {
    "学术同行评审": {
        "rank": 1, "emoji": "🥇",
        "verifiability": "已发表/有DOI/可公开验证",
        "trust_note": "可作学术结论引用",
    },
    "会议预印本": {
        "rank": 2, "emoji": "🥈",
        "verifiability": "公开可引但未经同行评审, 结论可能被推翻",
        "trust_note": "标注预印本, 结论非定论",
    },
    "工业实践": {
        "rank": 3, "emoji": "🥉",
        "verifiability": "官方代码/发布, 可验证存在但非学术结论",
        "trust_note": "代码可读但宣称需独立验证",
    },
    "博客": {
        "rank": 4, "emoji": "📝",
        "verifiability": "个人/社区撰写, 无正式评审",
        "trust_note": "作者个人观点, 需独立验证",
    },
    "社媒": {
        "rank": 5, "emoji": "💬",
        "verifiability": "推文/论坛/新闻聚合, 时效强但可靠性参差",
        "trust_note": "首次引用必须标注, 不可当结论陈述",
    },
}


# 未知 source 默认 tier (最保守 = 最低可信度, 要求标注+核实)
_DEFAULT_TIER = "社媒"


# ════════════════════════════════════════════════════════════════════
# source → tier 映射 (单一真理源, MR-8)
# 必须与 jobs_registry.yaml 16 个内容源 (含 kb_source_file) 一致. (V37.9.108: +ai_leaders_blogs; V37.9.110: +ai_leaders_bsky)
# key = job id (jobs_registry `- id:` 字段)
# label = kb_source_label (用户在 KB section 看到的标签, 用于 prompt 块显示)
# ════════════════════════════════════════════════════════════════════
SOURCE_CREDIBILITY: dict[str, dict] = {
    # 🥇 学术同行评审
    "semantic_scholar": {"label": "📈 Semantic Scholar", "tier": "学术同行评审"},
    "dblp": {"label": "📚 DBLP CS论文", "tier": "学术同行评审"},
    "acl_anthology": {"label": "📝 ACL Anthology NLP", "tier": "学术同行评审"},
    "ontology_sources": {"label": "🧠 Ontology 专属源", "tier": "学术同行评审"},
    # 🥈 会议预印本
    "arxiv_monitor": {"label": "📄 ArXiv AI论文", "tier": "会议预印本"},
    "hf_papers": {"label": "🤗 HuggingFace 论文", "tier": "会议预印本"},
    # 🥉 工业实践
    "github_trending": {"label": "🚀 GitHub Trending ML/AI", "tier": "工业实践"},
    "openclaw_run": {"label": "⚙️ OpenClaw Releases", "tier": "工业实践"},
    # 📝 博客 (chaspark = observer 5/28 点名的非主流学术源)
    "rss_blogs": {"label": "📖 RSS 技术博客", "tier": "博客"},
    "chaspark": {"label": "🏠 华为茶思屋科技", "tier": "博客"},
    # V37.9.108: AI 大神博客/Substack 长文观点 (ai_leaders 从 X 转向非 X 渠道;
    # 个人学者撰写无正式评审 → 博客 tier, 比 X 推文 ai_leaders_x 社媒档更可信)
    "ai_leaders_blogs": {"label": "🧠 AI 大神观点", "tier": "博客"},
    # 💬 社媒/资讯
    "ai_leaders_x": {"label": "🐦 AI Leaders X 洞察", "tier": "社媒"},
    # V37.9.110: AI 大神 Bluesky 实时短观点 (ai_leaders 加实时短观点维度;
    # 短帖时效强可靠性参差 → 社媒 tier, 同 X 推文; 长文版在 ai_leaders_blogs 博客档)
    "ai_leaders_bsky": {"label": "🦋 AI 大神实时观点", "tier": "社媒"},
    "run_hn_fixed": {"label": "🔥 HackerNews 热帖", "tier": "社媒"},
    "freight_watcher": {"label": "🚢 货代动态", "tier": "社媒"},
    "finance_news": {"label": "📊 全球财经/政策", "tier": "社媒"},
}


def list_tiers() -> list[str]:
    """列出 5 档 tier 名 (rank 升序 = 可信度递减)."""
    return sorted(_CREDIBILITY_TIERS, key=lambda t: _CREDIBILITY_TIERS[t]["rank"])


def list_sources() -> list[str]:
    """列出所有已分级 source id."""
    return list(SOURCE_CREDIBILITY.keys())


def get_credibility(source_id: str) -> dict:
    """查询单个 source 的可信度信息.

    Args:
        source_id: jobs_registry job id (如 "chaspark" / "arxiv_monitor").

    Returns:
        dict 含 source_id / label / tier / rank / emoji / verifiability /
        trust_note. 未知 source / 非 string → _DEFAULT_TIER (社媒, 最低可信度)
        + reason="未分类来源" + label=source_id 原值.

    FAIL-OPEN: 永不抛异, 未知源走最保守降级.
    """
    if not isinstance(source_id, str) or source_id not in SOURCE_CREDIBILITY:
        tier = _DEFAULT_TIER
        meta = _CREDIBILITY_TIERS[tier]
        return {
            "source_id": source_id if isinstance(source_id, str) else "",
            "label": source_id if isinstance(source_id, str) else "",
            "tier": tier,
            "rank": meta["rank"],
            "emoji": meta["emoji"],
            "verifiability": meta["verifiability"],
            "trust_note": meta["trust_note"],
            "reason": "未分类来源",
        }
    entry = SOURCE_CREDIBILITY[source_id]
    tier = entry["tier"]
    meta = _CREDIBILITY_TIERS[tier]
    return {
        "source_id": source_id,
        "label": entry["label"],
        "tier": tier,
        "rank": meta["rank"],
        "emoji": meta["emoji"],
        "verifiability": meta["verifiability"],
        "trust_note": meta["trust_note"],
        "reason": "",
    }


def _sources_by_tier() -> dict[str, list[str]]:
    """按 tier 分组 source label (rank 升序). 供 format_credibility_block."""
    grouped: dict[str, list[str]] = {t: [] for t in list_tiers()}
    for entry in SOURCE_CREDIBILITY.values():
        grouped[entry["tier"]].append(entry["label"])
    return grouped


def format_credibility_block() -> str:
    """构造可信度评级 prompt 注入块 (dream / evening 共用).

    Returns:
        守卫文本块 (以 \\n\\n 开头, 直接 append 到 base prompt 末尾即可).
        内容: 5 档 tier 定义 (可信度递减) + 各 tier 的 source 标签 +
        强制标注规则 + 非主流源 (chaspark) 首次引用特别提示.

    source 列表从 SOURCE_CREDIBILITY 动态生成 (单一真理源, MR-8) — 改映射
    无需改本函数.
    """
    grouped = _sources_by_tier()
    lines = [
        "",
        "",
        "⚠️ 来源可信度评级 (V37.9.98 observer proposal #2):",
        "引用任何来源条目时，在该条目后标注其可信度等级 [可信度: <等级>]，"
        "提醒读者按等级独立验证。",
        "等级定义（可信度递减，指引用断言无需独立验证的可信程度）:",
    ]
    for tier in list_tiers():
        meta = _CREDIBILITY_TIERS[tier]
        labels = " · ".join(grouped.get(tier, [])) or "（无）"
        lines.append(
            f"- {meta['emoji']} {tier} ({meta['verifiability']}): {labels}"
        )
    lines += [
        "强制规则:",
        "- 非主流学术源（尤其 🏠 华为茶思屋科技）首次引用必须明确标注可信度"
        "等级 + 提示\"需独立验证\"（observer 5/28 proposal #2 点名）",
        "- 严禁把 📝 博客 / 💬 社媒级来源的断言当作学术结论陈述"
        "（如\"研究表明 X\"/\"已证明 Y\"必须来自 🥇 学术同行评审 或 🥈 会议预印本级）",
        "- 未在上述列表的来源默认按 💬 社媒级（最低可信度）处理并标注"
        "\"[未分类来源, 需核实]\"",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="V37.9.98 来源可信度评级 (查询/调试 CLI)"
    )
    parser.add_argument("--list", action="store_true", help="列出 5 档 tier")
    parser.add_argument(
        "--sources", action="store_true", help="列出 source → tier 映射"
    )
    parser.add_argument(
        "--block", action="store_true", help="打印 prompt 注入块"
    )
    parser.add_argument(
        "--source", default=None, help="查询单个 source 的可信度信息"
    )
    args = parser.parse_args()

    if args.list:
        for t in list_tiers():
            meta = _CREDIBILITY_TIERS[t]
            print(f"{meta['rank']} {meta['emoji']} {t} — {meta['verifiability']}")
        sys.exit(0)

    if args.sources:
        for sid in list_sources():
            e = SOURCE_CREDIBILITY[sid]
            print(f"{sid:22s} {e['tier']:8s} {e['label']}")
        sys.exit(0)

    if args.block:
        print(format_credibility_block())
        sys.exit(0)

    if args.source:
        info = get_credibility(args.source)
        for k, v in info.items():
            print(f"{k}: {v}")
        sys.exit(0)

    # 默认: 概览
    print(f"=== {_V37_9_98_MARKER} ({len(_CREDIBILITY_TIERS)} 档 / "
          f"{len(SOURCE_CREDIBILITY)} 源) ===")
    print()
    for t in list_tiers():
        meta = _CREDIBILITY_TIERS[t]
        srcs = [e["label"] for e in SOURCE_CREDIBILITY.values()
                if e["tier"] == t]
        print(f"  {meta['emoji']} {t} (rank {meta['rank']}): {' · '.join(srcs)}")
    print()
    print("=== prompt 注入块预览 ===")
    print(format_credibility_block())

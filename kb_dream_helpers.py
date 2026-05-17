#!/usr/bin/env python3
"""
kb_dream_helpers.py — V37.9.68 Dream 三阶推送辅助纯函数模块

V37.9.68 重设计目的：把 Dream 从"每日单主题深挖"升级为"DEEP(1主题深挖) + WIDE(5主题鲜人知) + RADAR(5准期信号) + 总览"四段推送。

辅助这个升级的核心纯函数：
- normalize_theme_keywords(): 主题文本归一化为关键词集合（跨日比较用）
- extract_recent_themes(): 扫最近 N 天 dream 文件提取已用主题
- format_banned_themes_block(): 把 banned themes 渲染为 DEEP prompt 段
- extract_deep_theme_from_chunk(): 从 DEEP LLM 输出抓主题标题
- build_overview_block(): 总览段（规则提取，不调 LLM）

设计原则：
- 纯函数零 I/O at import（除 extract_recent_themes 需 dream_dir 路径），方便单测
- FAIL-OPEN：缺文件/解析失败/空输入都返回安全默认值不抛
- MR-8 single-source-of-truth：所有 dream theme 解析逻辑只在这里

历史背景（用户视角原则 #13 兑现）：
2026-05-14 用户反馈"连续几周 Qwen-BIM 重复推送"，根因 6 因子叠加：
1. 用户笔记反复 ⭐⭐⭐⭐⭐ Qwen-BIM
2. Phase 1b prompt 偏好"重复出现=持续关注"
3. content-stable cache 锁定 signals
4. mtime 倒序处理 + user notes 静态
5. NOTES_SIGNALS 按 signal-hash dedup 但不按主题
6. PREV_THEMES 只看 3 天 + 选题留后门

V37.9.68 修：扩 14 天 + 主题归一化 + 三阶推送 + WIDE+RADAR 多视野
"""
from __future__ import annotations

import glob
import os
import re
import sys
from datetime import datetime, timedelta


# ─────────────────────────────────────────────────────────────────────
# 主题归一化
# ─────────────────────────────────────────────────────────────────────

_THEME_PREFIX_STRIP_RE = re.compile(
    r"^(?:#+\s*|🌙\s*|今日深度发现[:：]?\s*|今日深度[:：]?\s*|今日主题[:：]?\s*)"
)
_ENGLISH_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
# 中文连续段（不限长度），后续用滑动窗口切 2-gram + 3-gram
_CHINESE_RUN_RE = re.compile(r"[一-鿿]+")

# 高频英文停用词（不计入主题关键词）
_EN_STOPWORDS = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "from",
        "this",
        "that",
        "are",
        "was",
        "were",
        "been",
        "has",
        "have",
        "had",
        "but",
        "not",
        "all",
        "can",
        "any",
        "into",
        "such",
        "more",
        "than",
        "their",
        "they",
        "them",
        "these",
        "those",
        "about",
        "agent",  # 太通用
    }
)


def normalize_theme_keywords(text: str) -> set[str]:
    """把主题文本归一化为关键词集合（用于跨日比较）。

    例：
      "## 🌙 今日深度发现：Qwen-BIM 14B 模型颠覆参数竞赛"
      → {"qwen-bim", "14b", "模型", "颠覆", "参数", "竞赛"}
    """
    if not isinstance(text, str) or not text.strip():
        return set()
    # 多次剥前缀直到稳定（处理多层 markdown header）
    cleaned = text.strip()
    for _ in range(4):
        new = _THEME_PREFIX_STRIP_RE.sub("", cleaned).strip()
        if new == cleaned:
            break
        cleaned = new
    # 提取英文 token（小写化 + 停用词过滤）
    keywords: set[str] = set()
    for m in _ENGLISH_TOKEN_RE.findall(cleaned.lower()):
        if m not in _EN_STOPWORDS:
            keywords.add(m)
    # 提取中文 2-gram + 3-gram 滑动窗口
    # 例："模型颠覆参数竞赛" → {模型, 型颠, 颠覆, 覆参, 参数, 数竞, 竞赛,
    #                            模型颠, 型颠覆, 颠覆参, 覆参数, 参数竞, 数竞赛}
    # 噪声 N-gram 多但 themes_overlap ≥2 阈值能过滤噪声组合，
    # 关键产品名/概念词（如"参数"/"颠覆"/"控制平面"）都能被识别为关键词
    for run in _CHINESE_RUN_RE.findall(cleaned):
        n = len(run)
        for i in range(n - 1):
            keywords.add(run[i : i + 2])  # 2-gram
        for i in range(n - 2):
            keywords.add(run[i : i + 3])  # 3-gram
    return keywords


_ANCHOR_EN_RE = re.compile(r"^[a-z0-9_-]+$")
_ANCHOR_CN_RE = re.compile(r"^[一-鿿]+$")


def _is_anchor_keyword(kw: str) -> bool:
    """判定是否为"高价值锚点"关键词。

    锚点 = 高独特性 token，单独命中就算主题重复。
    设计阈值（V37.9.68，避开通用术语 false positive）：
    - 中文 ≥3 字（e.g. 3-gram "控制平" / "控制平面" 拆出的子段）
    - 英文复合标识符 ≥4 字 + 含 hyphen 或 digit（e.g. "qwen-bim" / "gpt-4" / "v37" / "14b"）
    - 英文纯字母 ≥8 字（e.g. "ontology")

    设计权衡（实测 Memory Plane vs Control Plane 共享"plane"血案）：
    - "plane" (5 字纯字母) 不算 anchor → 避免 Memory Plane / Control Plane / Capability Plane
      三个不同子项被错误判定为重复
    - "qwen-bim" (8 字 + hyphen) 算 anchor → Qwen-BIM 类血案核心防御
    - "ontology" (8 字纯字母) 算 anchor → 但同时也意味着 Ontology Engine vs Ontology RDF
      会被判定为重复，由用户后续视角决定是否细化（V37.9.69+ 候选）
    """
    if not isinstance(kw, str) or not kw:
        return False
    # 中文 ≥3 字
    if _ANCHOR_CN_RE.match(kw) and len(kw) >= 3:
        return True
    # 英文
    if _ANCHOR_EN_RE.match(kw):
        has_compound = "-" in kw or any(c.isdigit() for c in kw)
        # 复合标识符 ≥4 字
        if has_compound and len(kw) >= 4:
            return True
        # 纯字母 ≥8 字
        if not has_compound and len(kw) >= 8:
            return True
    return False


def themes_overlap(theme_a_keywords: set[str], theme_b_keywords: set[str]) -> bool:
    """两个主题是否"实质重复"。

    V37.9.68 修：双层判定避免血案场景遗漏。
    - 层 1（高优先）：共同 anchor 关键词 ≥1 (长 token 单独命中 = 高概率同主题)
      → Qwen-BIM 类血案场景：仅"qwen-bim"单独重叠就触发，因 Qwen-BIM 是 8 字符高价值产品名
    - 层 2：共同关键词 ≥2 (含短 2-gram 噪声，但 ≥2 个降低误报)
    - 层 3：单关键词重叠但占主题 keyword ≥50% (短主题保护)
    任一层命中即视为重复。
    """
    if not theme_a_keywords or not theme_b_keywords:
        return False
    common = theme_a_keywords & theme_b_keywords
    if not common:
        return False
    # 层 1: anchor 单独命中
    if any(_is_anchor_keyword(k) for k in common):
        return True
    # 层 2: 2+ 共同关键词
    if len(common) >= 2:
        return True
    # 层 3: 短主题 ≥50% 重叠
    shorter = min(len(theme_a_keywords), len(theme_b_keywords))
    if shorter > 0 and len(common) / shorter >= 0.5:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────
# 最近 N 天主题提取
# ─────────────────────────────────────────────────────────────────────

# 匹配 dream 文件名 YYYY-MM-DD.md
_DREAM_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")

# 提取 dream 文件内的主题行（兼容 V37.4-V37.9.67 多种格式）
# 优先匹配新格式 (V37.9.68): "## 🌙 今日深度: <theme>"
# 兼容旧格式 (V37.4+): "## 🌙 今日深度发现：<theme>" / "### 今日深度发现"
_THEME_LINE_RE = re.compile(
    r"^#{1,4}\s*(?:🌙\s*)?今日(?:深度|主题)(?:发现)?\s*[:：]\s*(.+?)\s*$"
)


def extract_recent_themes(
    dream_dir: str, days: int = 14, today: datetime | None = None
) -> list[dict]:
    """扫 dream_dir 提取最近 days 天的梦境主题。

    返回：列表，每项 dict 含 date(str) / raw_title(str) / keywords(set[str])
    按日期降序（最新在前）。FAIL-OPEN：缺目录/无 dream 文件 → 返回 []
    """
    if not isinstance(dream_dir, str) or not os.path.isdir(dream_dir):
        return []
    if not isinstance(days, int) or days < 1:
        days = 1
    if today is None:
        today = datetime.now()
    cutoff = today - timedelta(days=days)

    themes: list[dict] = []
    try:
        for path in sorted(glob.glob(os.path.join(dream_dir, "*.md")), reverse=True):
            fname = os.path.basename(path)
            m = _DREAM_FILENAME_RE.match(fname)
            if not m:
                continue
            try:
                fdate = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if fdate < cutoff:
                continue
            raw_title = _extract_theme_from_file(path)
            if not raw_title:
                continue
            kw = normalize_theme_keywords(raw_title)
            if not kw:
                continue
            themes.append(
                {
                    "date": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
                    "raw_title": raw_title,
                    "keywords": kw,
                }
            )
    except OSError:
        return themes
    return themes


def _extract_theme_from_file(path: str) -> str:
    """从 dream md 文件抓"今日深度"主题行。FAIL-OPEN: 抓不到返回 "" """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue
                m = _THEME_LINE_RE.match(line)
                if m:
                    return m.group(1).strip()
                # V37.9.67 之前格式可能直接用 "## 🌙 今日深度发现：xxx" 一行
                # 上面 regex 已覆盖。其他变体不强求。
    except OSError:
        return ""
    return ""


# ─────────────────────────────────────────────────────────────────────
# Banned themes prompt 段渲染
# ─────────────────────────────────────────────────────────────────────


def format_banned_themes_block(themes: list[dict]) -> str:
    """把 themes 列表渲染为 DEEP prompt 中的禁选段。

    输入：extract_recent_themes() 返回的列表
    输出：markdown 文本，含日期 + 标题 + 关键词集合
    若 themes 为空，返回 "" 让 caller 决定是否插入空段。
    """
    if not themes:
        return ""
    lines = [
        "【过去 14 天已选主题（禁止重复，违反 = 整份输出作废）】",
        "本次 DEEP 主题必须**完全不同**于以下任一主题（即关键词重叠 ≥2 或重叠率 ≥50% 都视为重复）：",
        "",
    ]
    for t in themes:
        kw_preview = ", ".join(sorted(t["keywords"])[:6])
        lines.append(f"- [{t['date']}] {t['raw_title']} (关键词: {kw_preview})")
    lines.append("")
    lines.append(
        "如果今日数据中只有上述主题的延续，必须选**完全不同维度的新主题**而非'同主题新角度'。"
        "这是用户视角硬性要求：连续几周相同主题 = Dream 失去开拓视野价值。"
    )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# 从 LLM 输出抓 DEEP 主题
# ─────────────────────────────────────────────────────────────────────

_DEEP_THEME_PATTERNS = [
    re.compile(r"^#{1,4}\s*🌙\s*今日深度[:：]\s*(.+?)\s*$"),
    re.compile(r"^#{1,4}\s*🌙\s*今日深度发现[:：]\s*(.+?)\s*$"),
    re.compile(r"^#{1,4}\s*今日深度发现[:：]\s*(.+?)\s*$"),
    re.compile(r"^#{1,4}\s*今日主题[:：]\s*(.+?)\s*$"),
]


def extract_deep_theme_from_chunk(chunk: str) -> str:
    """从 DEEP LLM 输出中抓主题标题。多种格式兼容，抓不到返回 "(未识别主题)"."""
    if not isinstance(chunk, str) or not chunk.strip():
        return "(未识别主题)"
    for line in chunk.splitlines()[:10]:  # 只看前 10 行避免误抓
        line = line.strip()
        if not line:
            continue
        for pat in _DEEP_THEME_PATTERNS:
            m = pat.match(line)
            if m:
                title = m.group(1).strip().rstrip("。")
                if title:
                    return title
    return "(未识别主题)"


# ─────────────────────────────────────────────────────────────────────
# WIDE/RADAR 段拆分
# ─────────────────────────────────────────────────────────────────────

# 匹配 LLM 输出中 WIDE / RADAR 段的开始
# 接受多种 emoji + 文本变体
_WIDE_HEADER_RE = re.compile(r"^#{1,4}\s*(?:🌐|🔍|🌍)\s*(?:跨领域|跨域|跨主题)")
_RADAR_HEADER_RE = re.compile(r"^#{1,4}\s*(?:📡|🚨|⚡)\s*(?:准期|早期|蘆头|早期机会|准期信号)")


def split_wide_radar_output(content: str) -> tuple[str, str]:
    """把一次 LLM 调用产出的 WIDE+RADAR 内容拆为两段。

    返回 (wide_section, radar_section)。
    任一段未识别返回 "" 让 caller 决定降级处理。
    """
    if not isinstance(content, str) or not content.strip():
        return "", ""
    lines = content.splitlines()
    wide_start: int | None = None
    radar_start: int | None = None
    for i, line in enumerate(lines):
        if wide_start is None and _WIDE_HEADER_RE.match(line):
            wide_start = i
        elif radar_start is None and _RADAR_HEADER_RE.match(line):
            radar_start = i
    wide_section = ""
    radar_section = ""
    if wide_start is not None:
        end = radar_start if radar_start is not None else len(lines)
        wide_section = "\n".join(lines[wide_start:end]).strip()
    if radar_start is not None:
        radar_section = "\n".join(lines[radar_start:]).strip()
    return wide_section, radar_section


# ─────────────────────────────────────────────────────────────────────
# 总览段（规则提取，不调 LLM）
# ─────────────────────────────────────────────────────────────────────


def build_overview_block(
    deep_theme: str,
    wide_themes: list[str] | None,
    radar_themes: list[str] | None,
    kb_stats: dict | None = None,
) -> str:
    """构造"📋 今日连动 + 明日关注"段。

    不调 LLM，纯规则提取：DEEP 主题 + WIDE/RADAR 主题列表 + KB 数字。
    任何输入为 None/空 → 该行省略不抛。
    """
    lines = ["## 📋 今日连动 + 明日关注", ""]
    # DEEP 主题
    if deep_theme and deep_theme != "(未识别主题)":
        lines.append(f"- 🌙 **DEEP 主题**: {deep_theme}")
    # WIDE 主题列表
    wide_themes = wide_themes or []
    wide_themes = [t for t in wide_themes if t and t.strip()]
    if wide_themes:
        lines.append(f"- 🌐 **WIDE 主题** ({len(wide_themes)}): {', '.join(wide_themes)}")
    # RADAR 主题列表
    radar_themes = radar_themes or []
    radar_themes = [t for t in radar_themes if t and t.strip()]
    if radar_themes:
        lines.append(f"- 📡 **RADAR 主题** ({len(radar_themes)}): {', '.join(radar_themes)}")
    # KB 数字
    if kb_stats:
        src = kb_stats.get("sources_count", 0)
        notes = kb_stats.get("notes_count", 0)
        kb_kb = kb_stats.get("kb_kbytes", 0)
        reduce_chars = kb_stats.get("reduce_chars", 0)
        bits = []
        if src:
            bits.append(f"{src} sources")
        if notes:
            bits.append(f"{notes} notes")
        if kb_kb:
            bits.append(f"{kb_kb}KB KB")
        if reduce_chars:
            bits.append(f"{reduce_chars} chars 素材")
        if bits:
            lines.append(f"- 📊 **数据规模**: {' / '.join(bits)}")
    # 明日关注（规则模板）
    lines.append("")
    lines.append("**明日关注**:")
    if radar_themes:
        lines.append("- RADAR 准期信号若连续出现，明天升级为 WIDE 候选")
    if wide_themes:
        lines.append("- 今日 WIDE 主题任一在明日数据中累积证据 → 可能进入 DEEP 候选池")
    if not (radar_themes or wide_themes):
        lines.append("- 信号源稀薄，明日重点扩大 KB 输入面")
    return "\n".join(lines)


def extract_section_titles(section_md: str, max_n: int = 10) -> list[str]:
    """从 WIDE/RADAR 段 markdown 中提取每个子主题的标题。

    用于总览段生成。识别 `- **标题**:` / `- [标题]` / `### 标题` / `## 标题` 等格式。
    返回去重保序的标题列表 (最多 max_n 个)。
    """
    if not isinstance(section_md, str) or not section_md.strip():
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    patterns = [
        re.compile(r"^[-*]\s*\*\*([^*]+?)\*\*\s*[:：]"),  # - **title**:
        re.compile(r"^[-*]\s*\[([^\]]+)\]"),  # - [title]
        re.compile(r"^[-*]\s*\d+[.\s]\s*\*\*([^*]+?)\*\*"),  # - 1. **title**
        re.compile(r"^#{3,4}\s*\d*[.、\s]*(.+?)\s*$"),  # ### title
    ]
    for line in section_md.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in patterns:
            m = pat.match(line)
            if m:
                title = m.group(1).strip().rstrip("：:。").strip()
                if title and len(title) <= 60 and title not in seen:
                    seen.add(title)
                    ordered.append(title)
                    break
        if len(ordered) >= max_n:
            break
    return ordered


# ─────────────────────────────────────────────────────────────────────
# 多窗口分片（V37.9.68-hotfix）
# ─────────────────────────────────────────────────────────────────────


def split_dream_into_chunks(text: str, max_chunk: int = 4000) -> list[str]:
    """把 dream md 切分为 WhatsApp 推送 chunks。

    V37.9.68-hotfix 设计契约（修复 V37.9.68 设计假设错配）：
    - **每个 `## ` header 段独立成 chunk，不合并小段**
    - header 部分（首个 `## ` 之前的内容，含 `# 🌙 Agent Dream` + 元数据）
      合并到第一个 `## ` 段作 prefix（让用户在第一个推送窗口看到 Dream 标识）
    - 单段超 max_chunk 时按 `\\n` 内部切分
    - 空段静默跳过

    **修复历史**: V37.9.68 changelog 说"4 段 ## header 自然复用 V37.9.21 多窗口分片，
    推送 4 个独立 WhatsApp 窗口"，但 V37.9.21 实际是"≤ max_chunk 就合并"算法。
    V37.9.68 的 4 段大小不均匀（DEEP ~1500-5000 / WIDE ~1700 / RADAR ~900 / 总览 ~400），
    导致 WIDE+RADAR 等小段被合并成 1 chunk，最终只产 3 chunks 而非 4 chunks。
    Mac Mini 5/15 03:00 cron 实测确认：用户收到 [3/3] 而非 [4/4]。
    根因属 V37.9.66 案例库 类别 B "设计假设错配"。

    Returns:
        list[str]: 每个 chunk 是独立的 ≤ max_chunk 字符的 markdown 段。
                   空 text 返回 []。
    """
    if not isinstance(text, str) or not text.strip():
        return []
    if max_chunk < 100:
        # 防御: max_chunk 太小无意义, 但不抛 (caller 可能传 env var)
        max_chunk = 4000

    sections = text.split("\n## ")
    if not sections:
        return []

    # V37.9.73 修复（V37.9.68-hotfix 设计假设错配 — V37.9.66 类别 B 第 N+1 次演出）：
    # sections[0] 可能是 header 段（如 "# 🌙 Agent Dream\n> 元数据..."）或第一个 ## 段。
    # 判定规则：lstrip 后以 "## " 开头 → 是第一个 ## 段（生产 caller `<<< $DREAM_RESULT` 形态，
    # 见 kb_dream.sh:1557）；否则是 header 段（测试场景或 dream 文件写入场景）。
    # 2026-05-16/17 连续两天 Mac Mini cron 实测 [3/3] 血案 = 错把 DEEP 段当 header_part 与 WIDE 合并。
    first_is_h2 = sections[0].lstrip().startswith("## ")
    ordered_sections: list[str] = []
    if first_is_h2:
        # 生产场景：text 开头就是 "## DEEP"，sections[0] 已是第一个 ## 段，保留原 "## " 前缀
        for i, sec in enumerate(sections):
            if i == 0:
                ordered_sections.append(sec.strip())
            else:
                ordered_sections.append("## " + sec)
    else:
        # 测试/dream-file 场景：text 开头是 "# Agent Dream" header + 元数据
        header_part = sections[0].rstrip()
        for i, sec in enumerate(sections):
            if i == 0:
                continue
            ordered_sections.append("## " + sec)
        if ordered_sections and header_part:
            ordered_sections[0] = (header_part + "\n\n" + ordered_sections[0]).strip()
        elif header_part and not ordered_sections:
            # 退化场景: text 没有 ## header, header 自身作为唯一段
            ordered_sections.append(header_part)

    # 每段独立成 chunk; 仅当单段 > max_chunk 时内部按 \n 切分
    chunks: list[str] = []
    for piece in ordered_sections:
        piece = piece.strip()
        if not piece:
            continue
        while len(piece) > max_chunk:
            cut = piece[:max_chunk].rfind("\n")
            if cut < int(max_chunk * 0.5):
                cut = max_chunk  # hard cut 保底
            chunks.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            chunks.append(piece)
    return chunks


# ─────────────────────────────────────────────────────────────────────
# CLI（运维查询用）
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="kb_dream_helpers V37.9.68 CLI")
    parser.add_argument(
        "--show-recent-themes",
        type=str,
        metavar="DREAM_DIR",
        help="显示最近 14 天 dream 主题（用于运维 audit）",
    )
    parser.add_argument(
        "--days", type=int, default=14, help="主题回溯天数（默认 14）"
    )
    args = parser.parse_args()

    if args.show_recent_themes:
        themes = extract_recent_themes(args.show_recent_themes, days=args.days)
        if not themes:
            print(f"(no dream files in {args.show_recent_themes} within {args.days} days)")
            return 0
        for t in themes:
            kw = ", ".join(sorted(t["keywords"])[:8])
            print(f"[{t['date']}] {t['raw_title']}")
            print(f"  keywords: {kw}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

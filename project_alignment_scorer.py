#!/usr/bin/env python3
"""project_alignment_scorer.py — V37.9.47 Opportunity Radar Stage 2

#2 项目对齐度评分 — rule_check 验证层

设计文档: docs/opportunity_radar_design.md 第 4 节 (4.1 配置 / 4.2 算法 / 4.5 单测)
背景: V37.9.45 hf_papers PoC 加了 6 字段 (📌🔑💡🎯⭐🎚️) prompt + inline parse_6field_output
      但**没有 rule_check 验证层** — LLM 给的 🎚️ 评分完全主观, 无法检测 hallucination
Stage 2 (V37.9.47) 增加离线 keyword-based 验证:
  - 把 LLM 评分 vs project_concepts.yaml 关键词命中率比对
  - 不一致时显示 ⚠ 标记 (LLM 评 ⭐⭐⭐⭐⭐ 但仅命中 0 关键词)

核心 API:
  load_project_concepts(path=None) → dict (含 core_planes / active_research_directions /
                                            excluded_topics / scoring_guide)
  count_keyword_hits(content, concepts) → dict (positive=N, negative=M, total_score=...)
  compute_expected_range(score) → tuple (min_stars, max_stars)
  parse_6field_output(content) → dict (cn_title/highlights/insight/practice/rating/alignment)
  extract_star_count(text) → int (从 "⭐⭐⭐⭐ / 直接相关" 提取 4)
  validate_alignment_score(content, llm_score, concepts) → dict (validated/llm_score/
                                            rule_score/reason/keyword_hits)
  format_validation_marker(validation) → str ("" or "⚠️ LLM 评 5 但 rule 评 2 (命中 0 关键词)")

MR-8 兑现 (single source of truth):
  parse_6field_output 抽取自 hf_papers.sh inline 实现, 行为完全一致
  test_inline_parser_matches_module 单测守卫两侧不漂移

FAIL-OPEN 契约:
  load_project_concepts 缺文件 / 损坏 → 返回最小默认 dict, 不抛异
  validate_alignment_score 缺关键字段 → return validated=False, reason 解释原因
  parse_6field_output LLM 输出无 🎚️ 字段 → alignment='' (向后兼容)
"""

import os
import re
import sys
from datetime import datetime

# V37.9.47 marker (governance source-level guard 字面量)
_V37_9_47_MARKER = "V37.9.47 Opportunity Radar Stage 2"

# ── 默认路径 ──────────────────────────────────────────────────────────
_DEFAULT_CONCEPTS_PATH = os.environ.get(
    "PROJECT_CONCEPTS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "project_concepts.yaml"),
)

# ── 评分阈值 (设计文档 4.2 节锁定) ────────────────────────────────────
# 命中正向关键词数 → 预期评分区间 (min_stars, max_stars)
# 设计契约: 0 命中 → ⭐1-2 / 5+ 命中 → ⭐4-5
_SCORE_RANGES = [
    # (min_score_inclusive, max_score_inclusive, min_stars, max_stars)
    (-999, -1, 1, 2),   # 净分 < 0 (excluded 命中 > positive 命中) → 低评分
    (0, 0, 1, 2),       # 0 命中 → ⭐1-2
    (1, 2, 2, 3),       # 1-2 命中 → ⭐2-3
    (3, 4, 3, 4),       # 3-4 命中 → ⭐3-4
    (5, 999, 4, 5),     # 5+ 命中 → ⭐4-5
]


def log(msg):
    """V37.9.46 同款 stderr 输出 (MR-11 防 $(...) 命令替换污染)"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] project_alignment: {msg}", file=sys.stderr)


# ── 1. load_project_concepts ─────────────────────────────────────────
def load_project_concepts(path=None):
    """加载 project_concepts.yaml 解析为 dict.

    使用轻量 line-by-line YAML parse (kb_review_collect.py 同款 fallback 模式),
    避免新增 PyYAML 依赖 (Mac Mini 装 PyYAML 但 dev 可能没装).

    Returns:
        dict {
            'core_planes': {plane_name: {desc, keywords (list), weight (int)}},
            'active_research_directions': {dir_name: {desc, keywords, weight}},
            'excluded_topics': {topic_name: {desc, keywords, weight (negative)}},
            'scoring_guide': {star_N: str},
            'meta_rules': list[str],
        }

    FAIL-OPEN: 缺文件 / 损坏 → 返回最小默认 dict
    """
    if path is None:
        path = _DEFAULT_CONCEPTS_PATH

    default = {
        "core_planes": {},
        "active_research_directions": {},
        "excluded_topics": {},
        "scoring_guide": {},
        "meta_rules": [],
    }

    if not os.path.isfile(path):
        log(f"WARN: project_concepts.yaml not found: {path}")
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        log(f"WARN: project_concepts.yaml read failed: {e}")
        return default

    return _parse_concepts_yaml(text)


def _parse_concepts_yaml(text):
    """解析 project_concepts.yaml 文本 (轻量 YAML).

    支持:
      - 顶层 key (core_planes / active_research_directions / excluded_topics)
      - 嵌套子 key (plane_name → desc / keywords / weight)
      - keywords: [a, b, c] (list inline)
      - keywords: a, b, c (multi-line continuation 简化版本)
      - meta_rules: list ('- ' prefix)
      - scoring_guide: dict (star_N: "...")
    """
    result = {
        "core_planes": {},
        "active_research_directions": {},
        "excluded_topics": {},
        "scoring_guide": {},
        "meta_rules": [],
    }

    lines = text.splitlines()
    state = {
        "top_section": None,    # core_planes / active_research_directions / etc
        "current_item": None,   # 当前嵌套 plane/topic 名
        "current_field": None,  # desc / keywords / weight
        "buffer_kw": [],        # 多行 keywords 累积 buffer
    }

    def _commit_keywords():
        if state["current_item"] and state["buffer_kw"]:
            target = result.get(state["top_section"], {})
            entry = target.setdefault(state["current_item"], {})
            entry.setdefault("keywords", []).extend(state["buffer_kw"])
            state["buffer_kw"] = []

    for raw_line in lines:
        line = raw_line.rstrip()
        # Skip empty / comment / separator-style decoration
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # 检测 indent 级别
        leading_spaces = len(line) - len(line.lstrip(" "))

        # 顶层 section (no indent + 'key:')
        if leading_spaces == 0 and ":" in stripped:
            _commit_keywords()
            key = stripped.split(":", 1)[0].strip()
            if key in ("core_planes", "active_research_directions",
                       "excluded_topics", "scoring_guide", "meta_rules"):
                state["top_section"] = key
                state["current_item"] = None
                state["current_field"] = None
                continue
            elif key in ("project", "version", "last_updated"):
                # 忽略元信息字段 (project/version/last_updated 等)
                state["top_section"] = None
                state["current_item"] = None
                continue
            else:
                state["top_section"] = None
                state["current_item"] = None
                continue

        if state["top_section"] is None:
            continue

        # meta_rules section: '- "rule"' 列表
        if state["top_section"] == "meta_rules":
            m = re.match(r"^\s*-\s*[\"']?(.*?)[\"']?\s*$", line)
            if m and m.group(1):
                result["meta_rules"].append(m.group(1).strip())
            continue

        # scoring_guide section: '  star_N: "..."'
        if state["top_section"] == "scoring_guide":
            if leading_spaces >= 2 and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    result["scoring_guide"][key] = val
            continue

        # core_planes / active_research_directions / excluded_topics:
        # Level 1 (2 spaces indent): plane_name:
        # Level 2 (4 spaces indent): desc / keywords / weight
        if leading_spaces == 2 and stripped.endswith(":"):
            _commit_keywords()
            item_name = stripped[:-1].strip()
            if item_name:
                state["current_item"] = item_name
                state["current_field"] = None
                # Init item dict
                result[state["top_section"]].setdefault(item_name, {})
            continue

        if leading_spaces >= 4 and ":" in stripped and state["current_item"]:
            _commit_keywords()
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            entry = result[state["top_section"]][state["current_item"]]
            if key == "desc":
                entry["desc"] = val.strip('"').strip("'")
            elif key == "weight":
                # 剥离行尾注释 (V37.9.47: 'weight: 5  # 最高优先级' 形式)
                val_clean = val.split("#", 1)[0].strip()
                try:
                    entry["weight"] = int(val_clean)
                except ValueError:
                    entry["weight"] = 0
            elif key == "keywords":
                # keywords: [a, b, c] 单行 OR keywords: 多行模式
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1]
                    kws = [k.strip() for k in inner.split(",") if k.strip()]
                    entry.setdefault("keywords", []).extend(kws)
                elif val.startswith("[") and not val.endswith("]"):
                    # 多行 list (跨行) 简化处理
                    state["current_field"] = "keywords"
                    inner = val[1:]
                    kws = [k.strip() for k in inner.split(",") if k.strip()]
                    state["buffer_kw"].extend(kws)
                elif val:
                    # keywords: a, b, c (no brackets)
                    kws = [k.strip() for k in val.split(",") if k.strip()]
                    entry.setdefault("keywords", []).extend(kws)
            state["current_field"] = key
            continue

        # 多行 keywords continuation
        if state["current_field"] == "keywords" and state["current_item"]:
            content = stripped
            if content.endswith("]"):
                content = content[:-1]
                state["current_field"] = None
            kws = [k.strip() for k in content.split(",") if k.strip()]
            state["buffer_kw"].extend(kws)
            continue

    _commit_keywords()
    return result


# ── 2. count_keyword_hits ────────────────────────────────────────────
def count_keyword_hits(content, concepts):
    """统计 content 中的 project keywords 命中.

    Args:
        content: 待评分文本 (论文 abstract / repo description / etc)
        concepts: load_project_concepts() 返回的 dict

    Returns:
        dict {
            'positive_hits': int,        # core_planes + active_research 命中数
            'negative_hits': int,        # excluded_topics 命中数
            'total_score': int,          # positive - 2 * negative (excluded 强降权)
            'matched_keywords': list,    # 命中的关键词 (debug 可见)
            'matched_planes': set,       # 命中的 plane/dir 名
        }

    契约:
      - 大小写不敏感
      - 中英混合: 用 in 操作 (substring), 不分词
      - 同一关键词多次出现仅计 1 次 (set semantics)
      - excluded 命中权重 -2 (设计文档 4.1 weight=-2)
    """
    if not content or not isinstance(content, str):
        return {"positive_hits": 0, "negative_hits": 0, "total_score": 0,
                "matched_keywords": [], "matched_planes": set()}

    content_lower = content.lower()
    matched = set()
    matched_planes = set()
    positive = 0
    negative = 0

    # Positive: core_planes + active_research_directions
    for section_key in ("core_planes", "active_research_directions"):
        section = concepts.get(section_key, {}) or {}
        for plane_name, plane_dict in section.items():
            if not isinstance(plane_dict, dict):
                continue
            keywords = plane_dict.get("keywords", []) or []
            for kw in keywords:
                if not kw or not isinstance(kw, str):
                    continue
                kw_lower = kw.lower()
                if kw_lower in content_lower and kw_lower not in matched:
                    matched.add(kw_lower)
                    matched_planes.add(plane_name)
                    positive += 1

    # Negative: excluded_topics
    excluded_section = concepts.get("excluded_topics", {}) or {}
    for topic_name, topic_dict in excluded_section.items():
        if not isinstance(topic_dict, dict):
            continue
        keywords = topic_dict.get("keywords", []) or []
        for kw in keywords:
            if not kw or not isinstance(kw, str):
                continue
            kw_lower = kw.lower()
            if kw_lower in content_lower and kw_lower not in matched:
                matched.add(kw_lower)
                negative += 1

    # 总分: positive - 2 * negative (excluded 强降权)
    total_score = positive - 2 * negative

    return {
        "positive_hits": positive,
        "negative_hits": negative,
        "total_score": total_score,
        "matched_keywords": sorted(matched),
        "matched_planes": matched_planes,
    }


# ── 3. compute_expected_range ────────────────────────────────────────
def compute_expected_range(score):
    """根据 total_score 计算预期评分区间 (min_stars, max_stars).

    设计文档 4.2 节锁定档位:
      score < 0 (excluded > positive)        → ⭐1-2 (强降权)
      score 0 (无命中)                        → ⭐1-2
      score 1-2 (轻度相关)                    → ⭐2-3
      score 3-4 (中度相关)                    → ⭐3-4
      score 5+ (高度相关)                     → ⭐4-5
    """
    for min_score, max_score, min_stars, max_stars in _SCORE_RANGES:
        if min_score <= score <= max_score:
            return (min_stars, max_stars)
    # Fallback: 永不应达 (兜底)
    return (1, 5)


# ── 4. parse_6field_output (MR-8: 与 hf_papers.sh inline 实现一致) ────
def parse_6field_output(content):
    """从 LLM 输出解析 6 字段 (V37.9.45 hf_papers inline 实现的模块化版本).

    返回 dict: cn_title / highlights / insight / practice / rating / alignment

    MR-8 单一真理源契约: 行为必须与 hf_papers.sh inline parse_6field_output 一致.
    test_inline_parser_matches_module 单测守卫两侧不漂移.

    特点:
      - key-based 字段头识别 (不依赖位置), 容忍 LLM 输出字段顺序错乱
      - 单字段缺失 → 返回空字符串 (向后兼容)
      - prefix 变体 (📌 / 📌 中文标题: / 📌 标题:) 都识别
      - 🎚️ 字段在 ⭐ 之前检测 (避免 ⭐ 评分干扰 alignment 段)
    """
    fields = {
        "cn_title": "",
        "highlights": "",
        "insight": "",
        "practice": "",
        "rating": "",
        "alignment": "",
    }
    if not content or not isinstance(content, str):
        return fields

    current_field = [None]
    current_buffer = []

    def flush():
        if current_field[0] and current_buffer:
            fields[current_field[0]] = "\n".join(current_buffer).strip()

    for raw in content.split("\n"):
        line = raw.rstrip()
        # Skip horizontal separator lines (---/===/***)
        if re.match(r"^[-=*_]{3,}$", line.strip()):
            continue

        # 📌 中文标题
        if line.lstrip().startswith("📌"):
            flush()
            current_field[0] = "cn_title"
            current_buffer = []
            m = re.match(r".*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)", line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # 🔑 核心贡献
        if line.lstrip().startswith("🔑"):
            flush()
            current_field[0] = "highlights"
            current_buffer = []
            continue
        # 💡 关键方法
        if line.lstrip().startswith("💡"):
            flush()
            current_field[0] = "insight"
            current_buffer = []
            continue
        # 🎯 实践启发
        if line.lstrip().startswith("🎯"):
            flush()
            current_field[0] = "practice"
            current_buffer = []
            continue
        # 🎚️ 项目对齐度 (V37.9.45: 必须在 ⭐ 检测之前避免 ⭐ 评分干扰)
        if line.lstrip().startswith("🎚️") or line.lstrip().startswith("🎚"):
            flush()
            current_field[0] = "alignment"
            current_buffer = []
            m = re.match(r".*🎚️?\s*(?:项目)?对齐度?\s*[:：]?\s*(.*)", line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # ⭐ 评级 (current_field != rating/alignment 才进入)
        if line.lstrip().startswith("⭐") and current_field[0] not in ("rating", "alignment"):
            if "评级" in line or "推荐场景" in line or re.match(r"\s*⭐+\s*$", line):
                flush()
                current_field[0] = "rating"
                current_buffer = [line.lstrip()]
                continue
        # 普通行 → append
        if current_field[0] is not None:
            current_buffer.append(line)

    flush()
    return fields


# ── 5. extract_star_count ────────────────────────────────────────────
def extract_star_count(text):
    """从字符串提取连续 ⭐ 字符的数量 (LLM alignment 评分提取).

    例:
      "⭐⭐⭐⭐ / 直接相关"  → 4
      "评分: ⭐⭐⭐⭐⭐ - 强相关" → 5
      "⭐⭐ ⭐⭐⭐"         → 3 (取最大连续段)
      "无 ⭐"              → 1
      ""                  → 0

    契约:
      - 取最大的连续 ⭐ 段
      - 找不到 ⭐ → 返回 0
      - 同时 5 个 ⭐ 但分散两段 (3+2) → 返回 3 (max consecutive)
    """
    if not text or not isinstance(text, str):
        return 0
    # 找所有连续 ⭐ 段, 取最长
    matches = re.findall(r"⭐+", text)
    if not matches:
        return 0
    return max(len(m) for m in matches)


# ── 6. validate_alignment_score ──────────────────────────────────────
def validate_alignment_score(content, llm_score, concepts):
    """LLM 项目对齐度评分 vs rule-based keyword 评分一致性验证.

    Args:
        content: 待评分文本 (用 LLM 评分时给的同样输入)
        llm_score: LLM 给的 ⭐ 数 (1-5 整数)
        concepts: load_project_concepts() 返回的 dict

    Returns:
        dict {
            'validated': bool,           # True if LLM in expected range
            'llm_score': int,
            'rule_score': int,           # rule 推荐的最低 score (range 下界)
            'rule_range': tuple,         # (min_stars, max_stars)
            'positive_hits': int,
            'negative_hits': int,
            'matched_keywords': list,
            'reason': str,               # 不一致时的解释
        }

    设计契约 (设计文档 4.2 节):
      - LLM 评分在 expected_range 内 → validated=True
      - LLM 评分高于 max_range → 标 ⚠ (LLM 过度乐观, 关键词命中不足)
      - LLM 评分低于 min_range → 标 ⚠ (LLM 过度保守, 关键词显示高对齐)
    """
    # FAIL-OPEN: 输入不完整时 validated=False + reason 解释
    if not isinstance(llm_score, int) or llm_score < 1 or llm_score > 5:
        return {
            "validated": False,
            "llm_score": llm_score,
            "rule_score": 0,
            "rule_range": (1, 5),
            "positive_hits": 0,
            "negative_hits": 0,
            "matched_keywords": [],
            "reason": f"invalid llm_score: {llm_score!r} (expect int 1-5)",
        }

    hits = count_keyword_hits(content, concepts)
    expected_range = compute_expected_range(hits["total_score"])
    min_stars, max_stars = expected_range

    in_range = min_stars <= llm_score <= max_stars

    if in_range:
        reason = ""
    elif llm_score > max_stars:
        reason = (f"LLM 评 ⭐{llm_score} 偏高 (rule 预期 ⭐{min_stars}-{max_stars}, "
                  f"仅命中 {hits['positive_hits']} 关键词)")
    else:
        reason = (f"LLM 评 ⭐{llm_score} 偏低 (rule 预期 ⭐{min_stars}-{max_stars}, "
                  f"命中 {hits['positive_hits']} 关键词暗示更高对齐)")

    return {
        "validated": in_range,
        "llm_score": llm_score,
        "rule_score": min_stars,  # 取下界作为 rule 推荐
        "rule_range": expected_range,
        "positive_hits": hits["positive_hits"],
        "negative_hits": hits["negative_hits"],
        "matched_keywords": hits["matched_keywords"],
        "reason": reason,
    }


# ── 7. format_validation_marker ──────────────────────────────────────
def format_validation_marker(validation):
    """生成推送显示的 ⚠️ 标记字符串 (validated=False 时).

    Returns:
        str: "" if validated, otherwise "⚠️ <reason>"
    """
    if not isinstance(validation, dict):
        return ""
    if validation.get("validated", True):
        return ""
    reason = validation.get("reason", "")
    if not reason:
        return ""
    return f"⚠️ {reason}"


# ── CLI ──────────────────────────────────────────────────────────────
def main():
    """CLI: 给定 content + LLM 评分, 输出 validation result."""
    import argparse
    import json
    parser = argparse.ArgumentParser(
        description="Project alignment score validator (V37.9.47 Stage 2)")
    parser.add_argument("--content", required=True,
                        help="待评分文本 (论文 abstract / repo desc)")
    parser.add_argument("--llm-score", type=int, required=True,
                        help="LLM 给的 ⭐ 数 (1-5)")
    parser.add_argument("--concepts-path", default=None,
                        help="project_concepts.yaml 路径")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    concepts = load_project_concepts(args.concepts_path)
    result = validate_alignment_score(args.content, args.llm_score, concepts)

    if args.json:
        # set 不可 JSON 序列化, 转 list
        out = dict(result)
        if "matched_planes" in out:
            out["matched_planes"] = sorted(out["matched_planes"]) if out["matched_planes"] else []
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"Validated: {result['validated']}")
        print(f"LLM score: ⭐ × {result['llm_score']}")
        print(f"Rule range: ⭐{result['rule_range'][0]}-{result['rule_range'][1]}")
        print(f"Positive hits: {result['positive_hits']}")
        print(f"Negative hits: {result['negative_hits']}")
        if result["matched_keywords"]:
            print(f"Matched: {', '.join(result['matched_keywords'][:10])}")
        if result["reason"]:
            print(f"Marker: {format_validation_marker(result)}")

    return 0 if result["validated"] else 0  # exit 0 always (informational)


if __name__ == "__main__":
    sys.exit(main())

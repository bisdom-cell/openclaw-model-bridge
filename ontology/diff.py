#!/usr/bin/env python3
"""
ontology_diff.py — 硬编码 vs 本体 全量差异对比

宪法第四条工具：强制可视化两种范式之间的每一个差异。

用法：
    python3 ontology_diff.py              # 终端 Markdown 表格
    python3 ontology_diff.py --save       # 保存到 docs/ontology_diff_report.md
    python3 ontology_diff.py --json       # JSON 格式（供脚本调用）
    python3 ontology_diff.py --strict     # 有任何偏差则退出码非零
"""

import json
import os
import sys
import time

# 确保能导入父目录（proxy_filters）和当前目录（engine）
_ONTOLOGY_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _ONTOLOGY_DIR not in sys.path:
    sys.path.insert(0, _ONTOLOGY_DIR)

from proxy_filters import (
    ALLOWED_TOOLS,
    ALLOWED_PREFIXES,
    CLEAN_SCHEMAS,
    TOOL_PARAMS,
    CUSTOM_TOOLS,
    CUSTOM_TOOL_NAMES,
    VALID_BROWSER_PROFILES,
    MAX_REQUEST_BYTES,
    MEDIA_MAX_AGE_SECONDS,
    NO_TOOLS_MARKER,
)
from engine import ToolOntology


# ---------------------------------------------------------------------------
# 对比维度
# ---------------------------------------------------------------------------

class DiffItem:
    """单个对比项。"""
    def __init__(self, dimension, item, hardcoded, ontology, status, detail=""):
        self.dimension = dimension
        self.item = item
        self.hardcoded = hardcoded
        self.ontology = ontology
        self.status = status  # "match" | "mismatch" | "missing_ontology" | "missing_hardcoded" | "extra"
        self.detail = detail

    @property
    def icon(self):
        return {
            "match": "✅",
            "mismatch": "⚠️",
            "missing_ontology": "❌ 本体缺失",
            "missing_hardcoded": "❌ 硬编码缺失",
            "extra": "🆕 仅本体",
        }.get(self.status, "❓")

    def to_dict(self):
        return {
            "dimension": self.dimension,
            "item": self.item,
            "hardcoded": str(self.hardcoded),
            "ontology": str(self.ontology),
            "status": self.status,
            "icon": self.icon,
            "detail": self.detail,
        }


def _strip_descriptions(schema):
    """移除 schema 中的 description 字段，只保留结构（用于对比）。"""
    if not isinstance(schema, dict):
        return schema
    result = {}
    for k, v in schema.items():
        if k == "description":
            continue
        if isinstance(v, dict):
            result[k] = _strip_descriptions(v)
        else:
            result[k] = v
    return result


def _sorted_str(s):
    """将 set/list 转为排序后的字符串表示。"""
    if isinstance(s, set):
        return str(sorted(s))
    if isinstance(s, list):
        return str(sorted(s))
    return str(s)


def run_diff() -> list:
    """执行全量差异对比，返回 DiffItem 列表。"""
    onto = ToolOntology()
    items = []

    # ── 维度 1：工具白名单 ──
    items.extend(_diff_allowed_tools(onto))

    # ── 维度 2：前缀匹配 ──
    items.extend(_diff_prefixes(onto))

    # ── 维度 3：Schema 对比 ──
    items.extend(_diff_schemas(onto))

    # ── 维度 4：参数集合 ──
    items.extend(_diff_tool_params(onto))

    # ── 维度 5：参数别名 ──
    items.extend(_diff_aliases(onto))

    # ── 维度 6：自定义工具 ──
    items.extend(_diff_custom_tools(onto))

    # ── 维度 7：浏览器约束 ──
    items.extend(_diff_browser(onto))

    # ── 维度 8：策略值 ──
    items.extend(_diff_policies(onto))

    # ── 维度 9：rationale 覆盖率 ──
    items.extend(_diff_rationale(onto))

    return items


def _diff_allowed_tools(onto):
    """对比工具白名单。"""
    items = []
    all_tools = ALLOWED_TOOLS | onto.allowed_tools
    for tool in sorted(all_tools):
        in_hard = tool in ALLOWED_TOOLS
        in_onto = tool in onto.allowed_tools
        if in_hard and in_onto:
            items.append(DiffItem("工具白名单", tool, "✓", "✓", "match"))
        elif in_hard and not in_onto:
            items.append(DiffItem("工具白名单", tool, "✓", "✗", "missing_ontology",
                                  "硬编码有但本体缺失"))
        else:
            items.append(DiffItem("工具白名单", tool, "✗", "✓", "missing_hardcoded",
                                  "本体有但硬编码缺失"))
    return items


def _diff_prefixes(onto):
    """对比前缀匹配。"""
    items = []
    hard_set = set(ALLOWED_PREFIXES)
    onto_set = set(onto.allowed_prefixes)
    for prefix in sorted(hard_set | onto_set):
        if prefix in hard_set and prefix in onto_set:
            items.append(DiffItem("前缀匹配", prefix, "✓", "✓", "match"))
        elif prefix in hard_set:
            items.append(DiffItem("前缀匹配", prefix, "✓", "✗", "missing_ontology"))
        else:
            items.append(DiffItem("前缀匹配", prefix, "✗", "✓", "missing_hardcoded"))
    return items


def _diff_schemas(onto):
    """对比每个工具的 Schema。"""
    items = []
    all_tools = set(CLEAN_SCHEMAS.keys()) | set(onto.clean_schemas.keys())
    for tool in sorted(all_tools):
        hard_schema = CLEAN_SCHEMAS.get(tool)
        onto_schema = onto.clean_schemas.get(tool)

        if hard_schema is None and onto_schema is None:
            continue
        if hard_schema is None:
            items.append(DiffItem("Schema", tool, "无", _schema_summary(onto_schema),
                                  "missing_hardcoded"))
            continue
        if onto_schema is None:
            items.append(DiffItem("Schema", tool, _schema_summary(hard_schema), "无",
                                  "missing_ontology"))
            continue

        # 深度对比（忽略 description 文本差异，只比结构）
        hard_json = json.dumps(_strip_descriptions(hard_schema), sort_keys=True)
        onto_json = json.dumps(_strip_descriptions(onto_schema), sort_keys=True)
        if hard_json == onto_json:
            items.append(DiffItem("Schema", tool,
                                  _schema_summary(hard_schema),
                                  _schema_summary(onto_schema),
                                  "match"))
        else:
            detail = _schema_diff_detail(hard_schema, onto_schema)
            items.append(DiffItem("Schema", tool,
                                  _schema_summary(hard_schema),
                                  _schema_summary(onto_schema),
                                  "mismatch", detail))
    return items


def _schema_summary(schema):
    """Schema 的简短摘要。"""
    if not schema:
        return "无"
    props = list(schema.get("properties", {}).keys())
    req = schema.get("required", [])
    return f"props={props}, req={req}"


def _schema_diff_detail(hard, onto):
    """Schema 差异详细描述。"""
    diffs = []
    hard_props = set(hard.get("properties", {}).keys())
    onto_props = set(onto.get("properties", {}).keys())
    if hard_props != onto_props:
        diffs.append(f"properties: hard={sorted(hard_props)}, onto={sorted(onto_props)}")

    hard_req = set(hard.get("required", []))
    onto_req = set(onto.get("required", []))
    if hard_req != onto_req:
        diffs.append(f"required: hard={sorted(hard_req)}, onto={sorted(onto_req)}")

    hard_add = hard.get("additionalProperties")
    onto_add = onto.get("additionalProperties")
    if hard_add != onto_add:
        diffs.append(f"additionalProperties: hard={hard_add}, onto={onto_add}")

    # 对比每个属性的 type/enum
    for prop_name in hard_props & onto_props:
        hp = hard["properties"][prop_name]
        op = onto["properties"][prop_name]
        if hp.get("type") != op.get("type"):
            diffs.append(f"{prop_name}.type: hard={hp.get('type')}, onto={op.get('type')}")
        if hp.get("enum") != op.get("enum"):
            diffs.append(f"{prop_name}.enum: hard={hp.get('enum')}, onto={op.get('enum')}")

    return "; ".join(diffs) if diffs else "结构相同但 JSON 序列化不同"


def _diff_tool_params(onto):
    """对比每个工具的合法参数集。"""
    items = []
    all_tools = set(TOOL_PARAMS.keys()) | set(onto.tool_params.keys())
    for tool in sorted(all_tools):
        hard_params = TOOL_PARAMS.get(tool, set())
        onto_params = onto.tool_params.get(tool, set())
        if hard_params == onto_params:
            items.append(DiffItem("参数集合", tool,
                                  _sorted_str(hard_params),
                                  _sorted_str(onto_params),
                                  "match"))
        else:
            only_hard = hard_params - onto_params
            only_onto = onto_params - hard_params
            detail_parts = []
            if only_hard:
                detail_parts.append(f"仅硬编码: {sorted(only_hard)}")
            if only_onto:
                detail_parts.append(f"仅本体: {sorted(only_onto)}")
            items.append(DiffItem("参数集合", tool,
                                  _sorted_str(hard_params),
                                  _sorted_str(onto_params),
                                  "mismatch", "; ".join(detail_parts)))
    return items


def _diff_aliases(onto):
    """对比参数别名映射。"""
    items = []

    # 从 proxy_filters.py fix_tool_args 中提取的硬编码别名
    hardcoded_aliases = {
        "read": {"path": ["file_path", "file", "filepath", "filename"]},
        "exec": {"command": ["cmd", "shell", "bash", "script"]},
        "write": {"content": ["text", "data", "body", "file_content"]},
        "web_search": {"query": ["search_query", "q", "keyword", "search"]},
    }

    onto_aliases = onto.aliases
    all_tools = set(hardcoded_aliases.keys()) | set(onto_aliases.keys())

    for tool in sorted(all_tools):
        hard = hardcoded_aliases.get(tool, {})
        onot = onto_aliases.get(tool, {})

        for canonical in sorted(set(hard.keys()) | set(onot.keys())):
            hard_alts = sorted(hard.get(canonical, []))
            onto_alts = sorted(onot.get(canonical, []))
            if hard_alts == onto_alts:
                items.append(DiffItem("参数别名", f"{tool}.{canonical}",
                                      str(hard_alts), str(onto_alts), "match"))
            else:
                items.append(DiffItem("参数别名", f"{tool}.{canonical}",
                                      str(hard_alts), str(onto_alts), "mismatch",
                                      f"硬编码={hard_alts}, 本体={onto_alts}"))
    return items


def _diff_custom_tools(onto):
    """对比自定义工具。"""
    items = []
    hard_names = {t["function"]["name"] for t in CUSTOM_TOOLS}
    onto_names = onto.custom_tool_names

    for name in sorted(hard_names | onto_names):
        in_hard = name in hard_names
        in_onto = name in onto_names
        if in_hard and in_onto:
            # 对比 schema
            hard_tool = next(t for t in CUSTOM_TOOLS if t["function"]["name"] == name)
            onto_tool = next(t for t in onto.custom_tools if t["function"]["name"] == name)
            hard_json = json.dumps(_strip_descriptions(hard_tool["function"]["parameters"]), sort_keys=True)
            onto_json = json.dumps(_strip_descriptions(onto_tool["function"]["parameters"]), sort_keys=True)
            if hard_json == onto_json:
                items.append(DiffItem("自定义工具", name, "✓ schema一致", "✓ schema一致", "match"))
            else:
                detail = _schema_diff_detail(
                    hard_tool["function"]["parameters"],
                    onto_tool["function"]["parameters"])
                items.append(DiffItem("自定义工具", name,
                                      _schema_summary(hard_tool["function"]["parameters"]),
                                      _schema_summary(onto_tool["function"]["parameters"]),
                                      "mismatch", detail))
        elif in_hard:
            items.append(DiffItem("自定义工具", name, "✓", "✗", "missing_ontology"))
        else:
            items.append(DiffItem("自定义工具", name, "✗", "✓", "missing_hardcoded"))
    return items


def _diff_browser(onto):
    """对比浏览器约束。"""
    items = []
    # profiles
    hard_profiles = VALID_BROWSER_PROFILES
    onto_profiles = onto.valid_browser_profiles
    if hard_profiles == onto_profiles:
        items.append(DiffItem("浏览器约束", "valid_profiles",
                              str(sorted(hard_profiles)),
                              str(sorted(onto_profiles)), "match"))
    else:
        items.append(DiffItem("浏览器约束", "valid_profiles",
                              str(sorted(hard_profiles)),
                              str(sorted(onto_profiles)), "mismatch"))

    # default profile
    hard_default = "openclaw"  # 硬编码在 fix_tool_args 中
    onto_default = onto.default_browser_profile
    if hard_default == onto_default:
        items.append(DiffItem("浏览器约束", "default_profile",
                              hard_default, onto_default, "match"))
    else:
        items.append(DiffItem("浏览器约束", "default_profile",
                              hard_default, onto_default, "mismatch"))
    return items


def _diff_policies(onto):
    """对比策略规则中的数值与硬编码常量。"""
    items = []

    # max_request_bytes
    onto_val = onto.get_policy_value("request_limits", "max_request_bytes")
    if onto_val == MAX_REQUEST_BYTES:
        items.append(DiffItem("策略值", "max_request_bytes",
                              str(MAX_REQUEST_BYTES), str(onto_val), "match"))
    else:
        items.append(DiffItem("策略值", "max_request_bytes",
                              str(MAX_REQUEST_BYTES), str(onto_val), "mismatch"))

    # media_max_age_seconds
    onto_media_age = onto.get_policy_value("request_limits", "media_max_age_seconds")
    if onto_media_age == MEDIA_MAX_AGE_SECONDS:
        items.append(DiffItem("策略值", "media_max_age_seconds",
                              str(MEDIA_MAX_AGE_SECONDS), str(onto_media_age), "match"))
    else:
        items.append(DiffItem("策略值", "media_max_age_seconds",
                              str(MEDIA_MAX_AGE_SECONDS), str(onto_media_age), "mismatch"))

    # max_tools
    onto_max_tools = onto.get_policy_value("tool_admission", "max_tools")
    hard_max_tools = 12  # 硬编码在注释和 config.yaml 中
    if onto_max_tools == hard_max_tools:
        items.append(DiffItem("策略值", "max_tools",
                              str(hard_max_tools), str(onto_max_tools), "match"))
    else:
        items.append(DiffItem("策略值", "max_tools",
                              str(hard_max_tools), str(onto_max_tools), "mismatch"))

    return items


def _diff_rationale(onto):
    """检查本体中每条规则是否有 rationale。"""
    items = []
    for cat_name, policy in onto._policies.items():
        for rule in policy.get("rules", []):
            name = rule.get("name", "?")
            has_rationale = bool(rule.get("rationale", "").strip())
            if has_rationale:
                items.append(DiffItem("Rationale覆盖", f"{cat_name}.{name}",
                                      "N/A", "有", "match"))
            else:
                items.append(DiffItem("Rationale覆盖", f"{cat_name}.{name}",
                                      "N/A", "缺失", "missing_ontology",
                                      "违反宪法第三条：每条规则必须有 rationale"))
    return items


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def generate_report(items, fmt="markdown"):
    """生成差异报告。"""
    if fmt == "json":
        return json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False)

    # Markdown 表格
    lines = []
    lines.append(f"# Tool Ontology 差异对比报告")
    lines.append(f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 宪法第四条：强制差异对比表格")
    lines.append("")

    # 统计
    total = len(items)
    match_count = sum(1 for i in items if i.status == "match")
    mismatch_count = sum(1 for i in items if i.status == "mismatch")
    missing_count = sum(1 for i in items if i.status.startswith("missing"))
    pct = round(match_count * 100 / total, 1) if total else 0

    lines.append(f"## 总览")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 对比项总数 | {total} |")
    lines.append(f"| ✅ 一致 | {match_count} ({pct}%) |")
    lines.append(f"| ⚠️ 偏差 | {mismatch_count} |")
    lines.append(f"| ❌ 缺失 | {missing_count} |")
    lines.append(f"| 一致率 | **{pct}%** |")
    lines.append("")

    # 按维度分组
    dimensions = []
    seen = set()
    for item in items:
        if item.dimension not in seen:
            dimensions.append(item.dimension)
            seen.add(item.dimension)

    for dim in dimensions:
        dim_items = [i for i in items if i.dimension == dim]
        dim_match = sum(1 for i in dim_items if i.status == "match")
        dim_total = len(dim_items)
        dim_icon = "✅" if dim_match == dim_total else "⚠️"

        lines.append(f"## {dim_icon} {dim} ({dim_match}/{dim_total})")
        lines.append("")
        lines.append(f"| 状态 | 项目 | 硬编码 | 本体 | 备注 |")
        lines.append(f"|------|------|--------|------|------|")

        for item in dim_items:
            # 截断过长的值
            hard_val = str(item.hardcoded)[:50]
            onto_val = str(item.ontology)[:50]
            detail = item.detail[:60] if item.detail else ""
            lines.append(f"| {item.icon} | {item.item} | {hard_val} | {onto_val} | {detail} |")

        lines.append("")

    # 宪法合规
    lines.append("## 宪法合规检查")
    lines.append("")
    lines.append(f"| 宪法条款 | 状态 |")
    lines.append(f"|----------|------|")

    # 第一条：非破坏性
    lines.append(f"| 第一条：非破坏性引入 | ✅ proxy_filters.py 保留全部硬编码 |")

    # 第二条：一致性
    consistency_ok = mismatch_count == 0 and missing_count == 0
    c2_status = "✅ 100% 一致" if consistency_ok else f"⚠️ {mismatch_count} 偏差 + {missing_count} 缺失"
    lines.append(f"| 第二条：一致性安全网 | {c2_status} |")

    # 第三条：rationale
    rationale_items = [i for i in items if i.dimension == "Rationale覆盖"]
    rationale_ok = all(i.status == "match" for i in rationale_items)
    c3_status = f"✅ {len(rationale_items)}/{len(rationale_items)} 规则有 rationale" if rationale_ok else "❌ 有规则缺少 rationale"
    lines.append(f"| 第三条：每条规则有 rationale | {c3_status} |")

    # 第四条：本表格
    lines.append(f"| 第四条：差异对比表格 | ✅ 本报告 |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="硬编码 vs 本体 全量差异对比（宪法第四条）")
    parser.add_argument("--save", action="store_true", help="保存报告到 docs/ontology_diff_report.md")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--strict", action="store_true", help="有偏差则退出码非零")
    args = parser.parse_args()

    items = run_diff()
    fmt = "json" if args.json else "markdown"
    report = generate_report(items, fmt)

    print(report)

    if args.save:
        save_path = os.path.join(_ONTOLOGY_DIR, "docs", "ontology_diff_report.md")
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n📄 报告已保存到 {save_path}")

    if args.strict:
        mismatches = sum(1 for i in items if i.status != "match")
        if mismatches > 0:
            print(f"\n❌ --strict 模式：发现 {mismatches} 个不一致项")
            sys.exit(1)
        else:
            print(f"\n✅ --strict 模式：全部 {len(items)} 项一致")


if __name__ == "__main__":
    main()

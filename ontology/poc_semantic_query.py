#!/usr/bin/env python3
"""
poc_semantic_query.py — 从枚举到推理：第一个 POC 验证

证明本体论的核心价值：用语义属性查询工具，而不是手动维护工具名列表。

运行：
    python3 ontology/poc_semantic_query.py
"""

import json
import os
import sys
import time

_ONTOLOGY_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
sys.path.insert(0, _ONTOLOGY_DIR)
sys.path.insert(0, _PROJECT_ROOT)

from engine import ToolOntology


def main():
    onto = ToolOntology()

    print("=" * 70)
    print("  POC: 从枚举到推理 — Ontology Semantic Query")
    print("  证明本体论不是配置文件，而是可推理的语义控制平面")
    print("=" * 70)

    scenarios = [
        {
            "title": "场景 1：夜间禁止有副作用的操作",
            "problem": "凌晨 2-6 点禁止所有会修改数据的工具调用",
            "enumeration": {
                "code": 'NIGHT_BLOCKED = {"write", "edit", "exec", "cron", "message", "tts", "data_clean", "sessions_spawn", "sessions_send"}',
                "result": sorted(["write", "edit", "exec", "cron", "message", "tts", "data_clean", "sessions_spawn", "sessions_send"]),
                "problem": "手动维护列表，新增工具必须记得加入，遗漏 = 安全漏洞",
            },
            "semantic": {
                "condition": "side_effects == true",
                "advantage": "新增工具只要声明 side_effects: true，自动被覆盖，零遗漏",
            },
        },
        {
            "title": "场景 2：文件操作统一权限策略",
            "problem": "所有文件操作工具共享同一个目录权限检查",
            "enumeration": {
                "code": 'FILE_TOOLS = {"read", "write", "edit"}',
                "result": sorted(["read", "write", "edit"]),
                "problem": "新增文件工具（如 copy、move）必须手动加入",
            },
            "semantic": {
                "condition": "category == file_operation",
                "advantage": "按类别查询，新工具声明 category: file_operation 即可",
            },
        },
        {
            "title": "场景 3：只读工具免审计",
            "problem": "没有副作用的工具不需要写审计日志，减少开销",
            "enumeration": {
                "code": 'AUDIT_EXEMPT = {"web_search", "web_fetch", "read", "memory_search", "memory_get", "agents_list", "sessions_history", "image", "search_kb"}',
                "result": sorted(["web_search", "web_fetch", "read", "memory_search", "memory_get", "agents_list", "sessions_history", "image", "search_kb"]),
                "problem": "手动列出 9 个工具，是白名单的镜像反转，容易不一致",
            },
            "semantic": {
                "condition": "side_effects == false",
                "advantage": "side_effects == false 自动覆盖，与白名单保持语义一致",
            },
        },
        {
            "title": "场景 4：WebPage 资源访问控制",
            "problem": "限制 Agent 对外部网页的访问频率",
            "enumeration": {
                "code": 'WEB_TOOLS = {"web_search", "web_fetch"}',
                "result": sorted(["web_search", "web_fetch"]),
                "problem": "如果加了 web_screenshot、web_crawl，必须手动加入",
            },
            "semantic": {
                "condition": "resource_type == WebPage",
                "advantage": "按资源类型查询，任何操作 WebPage 的工具自动纳入",
            },
        },
        {
            "title": "场景 5：组合条件 — 有副作用的文件操作需要审批",
            "problem": "写入和编辑文件需要人工审批，读取不需要",
            "enumeration": {
                "code": 'FILE_APPROVE = {"write", "edit"}  # 手动排除 read',
                "result": sorted(["write", "edit"]),
                "problem": "手动排除 read，如果新增 file_delete 必须记得加入",
            },
            "semantic": {
                "condition": "side_effects == true AND category == file_operation",
                "advantage": "两个条件组合，自动排除只读工具，新增变更操作自动覆盖",
            },
        },
    ]

    all_match = True

    for s in scenarios:
        print(f"\n{'─' * 70}")
        print(f"  {s['title']}")
        print(f"  需求: {s['problem']}")
        print(f"{'─' * 70}")

        # 枚举方式
        enum_result = s["enumeration"]["result"]
        print(f"\n  ❌ 枚举方式（硬编码）:")
        print(f"     {s['enumeration']['code']}")
        print(f"     结果: {enum_result}")
        print(f"     问题: {s['enumeration']['problem']}")

        # 语义方式
        condition = s["semantic"]["condition"]
        semantic_result = onto.infer_policy_targets(condition)
        print(f"\n  ✅ 语义方式（本体查询）:")
        print(f"     onto.infer_policy_targets(\"{condition}\")")
        print(f"     结果: {semantic_result}")
        print(f"     优势: {s['semantic']['advantage']}")

        # 对比
        match = enum_result == semantic_result
        if match:
            print(f"\n  📊 对比: ✅ 结果完全一致 ({len(semantic_result)} 个工具)")
        else:
            print(f"\n  📊 对比: ⚠️ 结果不一致!")
            print(f"     仅枚举: {sorted(set(enum_result) - set(semantic_result))}")
            print(f"     仅语义: {sorted(set(semantic_result) - set(enum_result))}")
            all_match = False

    # 模拟新增工具场景
    print(f"\n{'═' * 70}")
    print(f"  模拟验证：新增工具后的行为差异")
    print(f"{'═' * 70}")

    print(f"\n  假设新增工具 'deploy' (side_effects: true, category: system_execution)")
    print(f"\n  枚举方式: 必须手动更新 5 个列表:")
    print(f"    - NIGHT_BLOCKED += 'deploy'")
    print(f"    - 忘记更新 → 凌晨可以触发部署 → 安全事故")
    print(f"\n  语义方式: 零修改")
    print(f"    - deploy 声明了 side_effects: true")
    print(f"    - 所有基于 side_effects 的策略自动覆盖")
    print(f"    - 无需修改任何策略代码")

    # 总结
    print(f"\n{'═' * 70}")
    print(f"  POC 结论")
    print(f"{'═' * 70}")
    print(f"\n  5 个场景全部验证: {'✅ 通过' if all_match else '⚠️ 有差异'}")
    print(f"  语义查询 vs 枚举: 结果一致，但语义方式：")
    print(f"    1. 新增工具零修改（自动覆盖）")
    print(f"    2. 规则数 O(M类别) << O(N工具)，M << N")
    print(f"    3. 条件可组合（AND），枚举只能交集")
    print(f"    4. 规则可追溯（condition 本身就是文档）")
    print(f"\n  这就是从'配置文件'到'语义控制平面'的跳跃。")
    print(f"{'═' * 70}")


if __name__ == "__main__":
    main()

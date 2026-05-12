#!/usr/bin/env python3
"""V37.9.57 公共反幻觉守卫模板 — MR-8 single-source-of-truth + MR-7 治理自观察.

═══════════════════════════════════════════════════════════════════════════
背景:
  V37.9.56-hotfix3 (2026-05-12) 修了 kb_evening 编造 "OpenClaw 社区发布 v26"
  幻觉血案 — LLM 看到 Top 5 paper 提"OpenClaw 三平面对齐" + 今日笔记少, 训练
  倾向下推断"项目必有版本更新"+ 自造 [openclaw] 来源标签. 修复路径是 prompt
  加具体字面禁令 (禁字面 OpenClaw 社区发布/v26/[openclaw] 等).

  审计发现同样风险存在于 8+ 其他 LLM-calling task (kb_dream/kb_review/
  kb_deep_dive/8 ALIGNED jobs/freight/finance/ontology). 单点修复 evening 等于
  把同一个反幻觉 bug fix 留给每个 task 各自维护 (MR-8 反例 copy-paste-is-
  a-bug-class).

  V37.9.57 立公共模板 — 5 档严格度 + 各 task 按场景引用, 未来加新 LLM task
  零反幻觉守卫维护成本 (引用即获得最新版本).

═══════════════════════════════════════════════════════════════════════════
5 档严格度 (按场景累积):

  LEVEL_1_MINIMAL — 仅基础"严禁虚构"通用守卫
    场景: 闭环数据提炼 (kb_harvest_chat 对话历史 — 输入 = 输出, 无外部推断)

  LEVEL_2_STANDARD — LEVEL_1 + 来源标签校验 (禁伪造未在 sources_used 出现的标签)
    场景: 单源新闻分析 (freight / finance / ontology — 数据源固定, 风险来自
    "推断未报道事件"而非跨域链式推论)

  LEVEL_3_STRICT — LEVEL_2 + 反链式推论 (禁"X 提到 Y → 推论 Z")
    场景: 跨域回顾性 LLM (kb_review 7 天跨 12 源 / kb_deep_dive 单 paper 全文
    分析 — 上下文丰富但回顾性, 需防 LLM 在跨域关联时编造)

  LEVEL_4_PROJECT_AWARE — LEVEL_3 + 反 OpenClaw 项目动态编造 (V37.9.56-hotfix3
    血案具体字面禁令: 禁 v26/v27/v37 版本更新 / 项目里程碑 / 开源 X 上线 /
    [openclaw] 来源标签等)
    场景: per-paper LLM 评分 (8 ALIGNED jobs hf_papers/s2/dblp/arxiv/gh/rss/
    ai_leaders/hn — prompt 含"OpenClaw 控制平面/记忆平面/ontology"项目方向
    评分指南, LLM 易过度推论"paper → 项目动态")

  LEVEL_5_RADAR_AWARE — LEVEL_4 + 反 Opportunity Radar 信号源混淆
    (Top 5 高对齐 / cross_source 共振 / trend 加速 仅作背景非事件, 跨多天
    累积非今日事件)
    场景: Reduce/Evening LLM 注入 #1+#2+#3 三件套信号 (kb_dream Reduce /
    kb_evening Evening — 注入 8 ALIGNED 跨多天 paper Top 5 + 今日 cross_source
    共振 + 本周趋势加速度, LLM 易混淆"参考阅读"与"今日事件")

═══════════════════════════════════════════════════════════════════════════
使用方式:

  from hallucination_guards import get_guard
  prompt = base_prompt + get_guard("LEVEL_5_RADAR_AWARE")

  # 或显式传入级别参数
  guard_text = get_guard(level="LEVEL_4_PROJECT_AWARE")

  # 列出所有可用级别 (debug/test)
  levels = list_levels()  # ["LEVEL_1_MINIMAL", ..., "LEVEL_5_RADAR_AWARE"]

═══════════════════════════════════════════════════════════════════════════
契约:
  - 所有 GUARD 文本块以 \\n\\n 开头便于直接 append 到 base_prompt 末尾
  - 每个守卫块以 "⚠️" 开头让 LLM 注意力高位置识别
  - 含具体字面禁令 (如 "OpenClaw 社区发布") 锁定血案精确字眼
  - 累积式: LEVEL_N 包含 LEVEL_(N-1) 全部内容
  - get_guard() 未知 level 默认 fallback LEVEL_3_STRICT (安全中位数)

MR-8 兑现: 单一真理源, 不同 task 共享同款守卫, 无 copy-paste 漂移
MR-7 兑现: 治理 INV 守卫每个 LLM task 必须 import get_guard, 反 inline 反模式
"""

from __future__ import annotations


# V37.9.57 marker (源码级守卫识别用)
_V37_9_57_MARKER = "V37.9.57 公共反幻觉守卫模板"


# ════════════════════════════════════════════════════════════════════
# LEVEL 1 — 最小守卫 (闭环数据)
# ════════════════════════════════════════════════════════════════════
_LEVEL_1_MINIMAL = """

⚠️ 反幻觉守卫 (V37.9.57 LEVEL_1):
- 严禁虚构任何未在输入数据中明确出现的内容
- 严禁推测/合理化任何不能从输入直接推导的"必然推论"
"""


# ════════════════════════════════════════════════════════════════════
# LEVEL 2 — 标准守卫 (单源新闻)
# ════════════════════════════════════════════════════════════════════
_LEVEL_2_STANDARD = """

⚠️ 反幻觉守卫 (V37.9.57 LEVEL_2):
- 严禁虚构任何未在输入数据中明确出现的内容
- 严禁推测/合理化任何不能从输入直接推导的"必然推论"
- 来源标签必须使用真实存在的源, 严禁创造未在 sources_used / 数据源列表中
  出现的标签 (如 [虚构源] / [推测来源] 等)
- 引用具体数据 (数字/日期/公司名/人名) 时必须能在原文中找到对应字面
"""


# ════════════════════════════════════════════════════════════════════
# LEVEL 3 — 严格守卫 (跨域回顾)
# ════════════════════════════════════════════════════════════════════
_LEVEL_3_STRICT = """

⚠️ 反幻觉守卫 (V37.9.57 LEVEL_3):
- 严禁虚构任何未在输入数据中明确出现的内容
- 严禁推测/合理化任何不能从输入直接推导的"必然推论"
- 来源标签必须真实存在, 严禁创造未在数据源列表中出现的标签
- 引用具体数据时必须能在原文中找到对应字面
- **反链式推论**: 严禁"X 段提到 Y → 推论 Z" 模式. 如果 Y 未在 Z 段输入数据
  中明确出现, 不得在 Z 段输出引用 Y. 跨段引用必须双向有原文支持.
- 跨域关联时只允许"事实 A 与事实 B 共现"陈述, 严禁"A → B 因果链推断" 除非
  原文显式说明因果
"""


# ════════════════════════════════════════════════════════════════════
# LEVEL 4 — 项目感知守卫 (per-paper alignment 评分)
# V37.9.56-hotfix3 血案具体字面禁令
# ════════════════════════════════════════════════════════════════════
_LEVEL_4_PROJECT_AWARE = """

⚠️ 反幻觉守卫 (V37.9.57 LEVEL_4 含 V37.9.56-hotfix3 血案规则):
- 严禁虚构任何未在输入数据中明确出现的内容
- 严禁推测/合理化任何不能从输入直接推导的"必然推论"
- 来源标签必须真实存在, 严禁创造未在数据源列表中出现的标签
- 引用具体数据时必须能在原文中找到对应字面
- 反链式推论: 严禁"X 段提到 Y → 推论 Z" 模式

【V37.9.56-hotfix3 反 OpenClaw 项目动态编造禁令】
(2026-05-12 evening 编造 "OpenClaw 社区发布 v26" 血案规则):
- 禁字面: "OpenClaw 社区发布"、"OpenClaw v26"、"v26/v27/v37 版本更新"、
  "项目里程碑"、"开源 X 上线"、"OpenClaw 新功能"、"项目重大更新"
- 禁来源标签: [openclaw] / [OpenClaw] / [社区] — 除非数据源中**真实存在**
  对应字面量 (如 sources_used 显式列出 OpenClaw 官方 release notes)
- 评分/分析必须基于输入 paper/repo/blog 中**明确出现**的技术内容,
  严禁"如果 OpenClaw 应用此技术..."等推测对齐
- 严禁在分析理由中引用 paper/repo 未出现的 OpenClaw 内部特性
- 严禁基于"项目方向词"(control plane/memory plane/ontology engine) 推断
  本项目存在某个未公开发布的功能
"""


# ════════════════════════════════════════════════════════════════════
# LEVEL 5 — Radar 感知守卫 (Reduce/Evening 注入 #1+#2+#3 信号源)
# V37.9.56-hotfix3 + Opportunity Radar 信号源重定位
# ════════════════════════════════════════════════════════════════════
_LEVEL_5_RADAR_AWARE = """

⚠️ 反幻觉守卫 (V37.9.57 LEVEL_5 含 V37.9.56-hotfix3 + Radar 信号源契约):
- 严禁虚构任何未在输入数据中明确出现的内容
- 严禁推测/合理化任何不能从输入直接推导的"必然推论"
- 来源标签必须真实存在, 严禁创造未在数据源列表中出现的标签
- 引用具体数据时必须能在原文中找到对应字面
- 反链式推论: 严禁"X 段提到 Y → 推论 Z" 模式

【V37.9.56-hotfix3 反 OpenClaw 项目动态编造禁令】
(2026-05-12 evening 编造 "OpenClaw 社区发布 v26" 血案规则):
- 禁字面: "OpenClaw 社区发布"、"OpenClaw v26"、"v26/v27/v37 版本更新"、
  "项目里程碑"、"开源 X 上线"、"OpenClaw 新功能"、"项目重大更新"
- 禁来源标签: [openclaw] / [OpenClaw] / [社区] — 除非数据源中**真实存在**
  对应字面量
- 严禁基于"项目方向词" (control plane/memory plane/ontology engine) 推断
  本项目存在某个未公开发布的功能

【V37.9.57 Opportunity Radar 信号源契约】
- "Opportunity Radar #1 跨 source 共振信号" / "Opportunity Radar #2 高对齐
  Top 5" / "Opportunity Radar #3 趋势加速度" 三类段是 **外部数据**:
  · #1 来自今日 KB notes 跨源聚类 (sentence-transformer + DBSCAN)
  · #2 来自 8 ALIGNED jobs 跨多天 cache 累积 (paper/repo/blog 高对齐 ⭐≥4)
  · #3 来自 4 周历史关键词加速度 (kb_trend_acceleration)
- 严禁把 Radar 信号当作"今日发生的事件"或"项目内部动态"
- Radar 仅作"背景知识参考", 在主分析段引用前必须确认今日笔记/今日来源
  归档中**实际出现**对应内容, 否则只能在"明日关注"段做"值得追踪"提示
- 严禁链式推论: "高对齐 paper 提到 OpenClaw → 本项目必有相关动态" /
  "趋势加速 X → 本项目必然在做 X" / "跨源共振 Y → 用户必然关心 Y"
- 严禁推论"用户/团队/项目"的内部状态, 除非输入数据中显式陈述
"""


# 5 档守卫文本字典 (公开字面常量)
GUARDS = {
    "LEVEL_1_MINIMAL": _LEVEL_1_MINIMAL,
    "LEVEL_2_STANDARD": _LEVEL_2_STANDARD,
    "LEVEL_3_STRICT": _LEVEL_3_STRICT,
    "LEVEL_4_PROJECT_AWARE": _LEVEL_4_PROJECT_AWARE,
    "LEVEL_5_RADAR_AWARE": _LEVEL_5_RADAR_AWARE,
}


# 默认 fallback 级别 (未知 level 时安全中位数, 不是最高也不是最低)
_DEFAULT_FALLBACK_LEVEL = "LEVEL_3_STRICT"


def get_guard(level: str = "LEVEL_3_STRICT") -> str:
    """获取指定严格度的反幻觉守卫文本.

    Args:
        level: 5 档之一: LEVEL_1_MINIMAL / LEVEL_2_STANDARD / LEVEL_3_STRICT /
               LEVEL_4_PROJECT_AWARE / LEVEL_5_RADAR_AWARE
               未知值 fallback LEVEL_3_STRICT (安全中位数).

    Returns:
        守卫文本块 (以 \\n\\n 开头, 直接 append 到 base prompt 末尾即可).

    使用例:
        from hallucination_guards import get_guard
        prompt = base_prompt + get_guard("LEVEL_5_RADAR_AWARE")
    """
    if not isinstance(level, str):
        return GUARDS[_DEFAULT_FALLBACK_LEVEL]
    return GUARDS.get(level, GUARDS[_DEFAULT_FALLBACK_LEVEL])


def list_levels() -> list[str]:
    """列出所有可用守卫级别 (供 debug / 测试 / 文档生成)."""
    return list(GUARDS.keys())


def get_blocked_phrases() -> list[str]:
    """V37.9.56-hotfix3 血案具体字面禁令清单 (LEVEL_4+ 含).

    返回必须在 LEVEL_4 / LEVEL_5 守卫中出现的精确字眼, 供测试守卫真有效性.
    """
    return [
        "OpenClaw 社区发布",
        "OpenClaw v26",
        "v26/v27/v37 版本更新",
        "项目里程碑",
        "开源 X 上线",
        "[openclaw]",
    ]


def get_radar_signal_types() -> list[str]:
    """V37.9.57 LEVEL_5 Opportunity Radar 三件套信号类型清单 (供测试).

    返回精确字面前缀, 必须作为连续 substring 在 LEVEL_5 守卫中出现.
    """
    return [
        "Opportunity Radar #1",
        "Opportunity Radar #2",
        "Opportunity Radar #3",
    ]


if __name__ == "__main__":
    # CLI: 列出所有级别 + 显示某级别完整守卫文本
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="V37.9.57 公共反幻觉守卫模板 (查询/调试 CLI)"
    )
    parser.add_argument(
        "--level", default=None,
        help="显示指定级别守卫文本 (LEVEL_1_MINIMAL / LEVEL_2_STANDARD / "
             "LEVEL_3_STRICT / LEVEL_4_PROJECT_AWARE / LEVEL_5_RADAR_AWARE)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="列出所有可用级别",
    )
    parser.add_argument(
        "--blocked-phrases", action="store_true",
        help="列出 V37.9.56-hotfix3 血案具体字面禁令",
    )
    args = parser.parse_args()

    if args.list:
        for lv in list_levels():
            print(lv)
        sys.exit(0)

    if args.blocked_phrases:
        for phrase in get_blocked_phrases():
            print(phrase)
        sys.exit(0)

    if args.level:
        print(get_guard(args.level))
        sys.exit(0)

    # 默认: 显示所有级别名 + LEVEL_3 文本作为预览
    print(f"=== V37.9.57 反幻觉守卫模板 ({len(GUARDS)} 档) ===")
    print()
    for lv in list_levels():
        print(f"  - {lv}")
    print()
    print(f"=== 预览 {_DEFAULT_FALLBACK_LEVEL} (默认 fallback) ===")
    print(get_guard(_DEFAULT_FALLBACK_LEVEL))

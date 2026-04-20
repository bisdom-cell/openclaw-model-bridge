#!/usr/bin/env python3
"""test_ontology_parser.py — V37.8.7 ontology_sources LLM 输出解析器单测

血案：2026-04-15 用户 WhatsApp 收到 ontology_sources 推送，格式严重错位：
  - 第 2 篇 cn_title 显示 *---*（分隔符被当成标题）
  - 第 2 篇缺失"要点"
  - 第 3 篇 cn_title 显示 *价值：⭐⭐⭐⭐*（前一篇的"价值"行错位上来）

根因：原解析器（V37.8.6 之前）用严格位置 lines[i], lines[i+1], lines[i+2] +
i += 3 步进。LLM 偶尔漏一行"要点"或多一行空白会导致**级联错位** —— 后续
所有条目的 (cn_title, highlight, stars) 全部错位。

V37.8.7 修复：抽到独立模块 ontology_parser.parse_llm_blocks()，按
分隔符切块 + 块内按前缀键查找。单块缺行不影响其他块。

本测试锁定 V37.8.7 解析器合约，覆盖：
  1. 正常 N 篇全字段
  2. 中间篇缺要点 / 缺价值（V37.8.6 血案）
  3. 多种分隔符变体（---、====、***）
  4. 文章N: 序号行被剥离
  5. 标题前缀变体（中文标题/标题/无前缀）
  6. 价值行变体（带"价值："前缀 / 仅 ⭐）
  7. 端到端：shell 脚本的 import 调用 + ONTOLOGY_JOBS_DIR env var
"""
import os
import re
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARSER_DIR = os.path.join(_HERE, "jobs", "ontology_sources")
sys.path.insert(0, _PARSER_DIR)

import ontology_parser as op


# ═══════════════════════════════════════════════════════════════════
# 1. 正常用例（baseline）
# ═══════════════════════════════════════════════════════════════════
class TestNormalCases(unittest.TestCase):
    def test_two_articles_full_fields(self):
        """正常 2 篇 3 字段"""
        content = (
            "中文标题：基于局部与全局信息的节点影响力识别\n"
            "要点：结合1到K阶邻居信息识别复杂网络中的关键节点\n"
            "价值：⭐⭐⭐\n"
            "---\n"
            "中文标题：面向烹饪替换的常识推理框架\n"
            "要点：用本体常识推理实现食材替换\n"
            "价值：⭐⭐⭐⭐"
        )
        got = op.parse_llm_blocks(content)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], (
            "基于局部与全局信息的节点影响力识别",
            "要点：结合1到K阶邻居信息识别复杂网络中的关键节点",
            "价值：⭐⭐⭐",
        ))
        self.assertEqual(got[1], (
            "面向烹饪替换的常识推理框架",
            "要点：用本体常识推理实现食材替换",
            "价值：⭐⭐⭐⭐",
        ))

    def test_single_article(self):
        content = "中文标题：A\n要点：B\n价值：⭐⭐⭐"
        got = op.parse_llm_blocks(content)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0], ("A", "要点：B", "价值：⭐⭐⭐"))

    def test_empty_input(self):
        self.assertEqual(op.parse_llm_blocks(""), [])
        self.assertEqual(op.parse_llm_blocks(None), [])

    def test_strips_chinese_title_prefix(self):
        """cn_title 应剥离 '中文标题：' 前缀，emit 时直接是标题文字"""
        content = "中文标题：纯净的标题\n要点：x\n价值：⭐"
        got = op.parse_llm_blocks(content)
        self.assertEqual(got[0][0], "纯净的标题")
        self.assertNotIn("中文标题", got[0][0])


# ═══════════════════════════════════════════════════════════════════
# 2. 血案回归：缺字段不级联（V37.8.7 核心修复）
# ═══════════════════════════════════════════════════════════════════
class TestBloodLessonRegression(unittest.TestCase):
    """V37.8.6 之前 i+=3 步进会让单块缺行级联污染所有后续条目。
    V37.8.7 按分隔符切块后，缺行只影响当前块。
    """

    def test_second_block_missing_highlight_does_not_cascade(self):
        """第 2 篇缺要点 — 第 3 篇仍正确解析（非级联）"""
        content = (
            "中文标题：A\n要点：B\n价值：⭐⭐⭐\n"
            "---\n"
            "中文标题：D\n价值：⭐⭐⭐⭐\n"  # 缺要点
            "---\n"
            "中文标题：E\n要点：F\n价值：⭐⭐⭐⭐⭐"
        )
        got = op.parse_llm_blocks(content)
        self.assertEqual(len(got), 3)
        # Block 1 正常
        self.assertEqual(got[0], ("A", "要点：B", "价值：⭐⭐⭐"))
        # Block 2 缺要点 → highlight 留空，cn_title 和 stars 仍正确
        self.assertEqual(got[1], ("D", "", "价值：⭐⭐⭐⭐"))
        # Block 3 完全不受 block 2 缺行影响
        self.assertEqual(got[2], ("E", "要点：F", "价值：⭐⭐⭐⭐⭐"))

    def test_block_missing_stars(self):
        """缺价值 → stars 留空，其他字段正常"""
        content = (
            "中文标题：A\n要点：B\n"  # 缺价值
            "---\n"
            "中文标题：D\n要点：E\n价值：⭐"
        )
        got = op.parse_llm_blocks(content)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], ("A", "要点：B", ""))
        self.assertEqual(got[1], ("D", "要点：E", "价值：⭐"))

    def test_block_missing_title(self):
        """缺标题 → cn_title 留空（fallback 不抢上一行）"""
        content = (
            "要点：仅有要点\n"
            "价值：⭐⭐"
        )
        got = op.parse_llm_blocks(content)
        # 注意：第一个无前缀行会作为 cn_title fallback；这里"要点：xxx"不是无前缀
        # 所以 cn_title 应为空
        self.assertEqual(got[0][0], "")
        self.assertEqual(got[0][1], "要点：仅有要点")

    def test_extra_blank_lines_within_block_ignored(self):
        content = (
            "中文标题：A\n\n\n要点：B\n\n价值：⭐⭐⭐\n"
            "---\n\n\n"
            "中文标题：D\n要点：E\n价值：⭐"
        )
        got = op.parse_llm_blocks(content)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], ("A", "要点：B", "价值：⭐⭐⭐"))

    def test_consecutive_separators_dropped(self):
        """连续多个 --- 中间产生空块，应被丢弃"""
        content = (
            "中文标题：A\n要点：B\n价值：⭐\n"
            "---\n"
            "---\n"
            "---\n"
            "中文标题：D\n要点：E\n价值：⭐⭐"
        )
        got = op.parse_llm_blocks(content)
        # 只有 2 个有效块，中间空块被丢弃
        self.assertEqual(len(got), 2)


# ═══════════════════════════════════════════════════════════════════
# 3. 分隔符变体兼容
# ═══════════════════════════════════════════════════════════════════
class TestSeparatorVariants(unittest.TestCase):
    def test_dashes_three(self):
        content = "中文标题：A\n要点：B\n价值：⭐\n---\n中文标题：D\n要点：E\n价值：⭐"
        self.assertEqual(len(op.parse_llm_blocks(content)), 2)

    def test_dashes_many(self):
        content = "中文标题：A\n要点：B\n价值：⭐\n--------\n中文标题：D\n要点：E\n价值：⭐"
        self.assertEqual(len(op.parse_llm_blocks(content)), 2)

    def test_equals_separator(self):
        content = "中文标题：A\n要点：B\n价值：⭐\n===\n中文标题：D\n要点：E\n价值：⭐"
        self.assertEqual(len(op.parse_llm_blocks(content)), 2)

    def test_asterisk_separator(self):
        content = "中文标题：A\n要点：B\n价值：⭐\n***\n中文标题：D\n要点：E\n价值：⭐"
        self.assertEqual(len(op.parse_llm_blocks(content)), 2)

    def test_separator_with_leading_trailing_spaces(self):
        content = "中文标题：A\n要点：B\n价值：⭐\n  ---  \n中文标题：D\n要点：E\n价值：⭐"
        self.assertEqual(len(op.parse_llm_blocks(content)), 2)


# ═══════════════════════════════════════════════════════════════════
# 4. LLM 输出变体兼容
# ═══════════════════════════════════════════════════════════════════
class TestLLMOutputVariants(unittest.TestCase):
    def test_article_prefix_stripped(self):
        """LLM 保留 '文章N:' 序号行 → 应被剥离"""
        content = (
            "文章1：原始英文标题\n"
            "中文标题：A\n要点：B\n价值：⭐\n"
            "---\n"
            "文章2：另一个英文标题\n"
            "中文标题：D\n要点：E\n价值：⭐⭐"
        )
        got = op.parse_llm_blocks(content)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][0], "A")
        self.assertEqual(got[1][0], "D")

    def test_title_without_chinese_prefix(self):
        """'标题：' 也被识别（不只是 '中文标题：'）"""
        content = "标题：A\n要点：B\n价值：⭐"
        got = op.parse_llm_blocks(content)
        self.assertEqual(got[0][0], "A")

    def test_stars_without_value_prefix(self):
        """单纯 ⭐ 行（无'价值：'前缀）应被识别为 stars 并补前缀"""
        content = "中文标题：A\n要点：B\n⭐⭐⭐⭐⭐"
        got = op.parse_llm_blocks(content)
        self.assertEqual(got[0][2], "价值：⭐⭐⭐⭐⭐")

    def test_no_prefix_line_becomes_title_fallback(self):
        """无前缀的第一行作为 cn_title fallback"""
        content = "纯净标题没有前缀\n要点：B\n价值：⭐"
        got = op.parse_llm_blocks(content)
        self.assertEqual(got[0][0], "纯净标题没有前缀")

    def test_residual_separator_in_block_filtered(self):
        """块内残留 --- 行（SEPARATOR_RE 边界 case）应被块内过滤"""
        # 极端构造：块内含一个不被 SEPARATOR_RE 切割的 --- 行
        # （比如开头/结尾，re.split 不切边界）
        content = "---\n中文标题：A\n要点：B\n价值：⭐"
        got = op.parse_llm_blocks(content)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "A")
        self.assertNotEqual(got[0][0], "---")  # 关键：--- 不能成为 cn_title


# ═══════════════════════════════════════════════════════════════════
# 5. Shell 脚本集成验证（防止 import 路径漂移）
# ═══════════════════════════════════════════════════════════════════
class TestShellScriptIntegration(unittest.TestCase):
    SHELL_PATH = os.path.join(_HERE, "jobs", "ontology_sources", "run_ontology_sources.sh")

    def test_shell_exports_ontology_jobs_dir(self):
        """shell 脚本必须 export ONTOLOGY_JOBS_DIR 让 heredoc 能找到 parser 模块"""
        with open(self.SHELL_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("export ONTOLOGY_JOBS_DIR=", content,
                     "shell 必须 export ONTOLOGY_JOBS_DIR")

    def test_shell_imports_ontology_parser(self):
        """heredoc 必须 from ontology_parser import parse_llm_blocks"""
        with open(self.SHELL_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("from ontology_parser import parse_llm_blocks", content)

    def test_shell_no_inline_positional_parser(self):
        """V37.8.7 修复后 shell 的可执行代码不应再含 i += 3 / lines[i+1] / lines[i+2]
        位置解析痕迹（注释里说明历史的字串允许保留）"""
        with open(self.SHELL_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 只扫描非注释行（# 开头或仅含 # 注释）
        # shell 注释 # 和 python heredoc 内 # 都跳过
        executable_lines = [
            ln for ln in lines
            if not ln.lstrip().startswith("#")
        ]
        executable = "\n".join(executable_lines)
        # 确认旧 bug 模式被彻底移除（仅在可执行代码层面）
        self.assertNotIn("lines[i+1]", executable,
                        "V37.8.6 位置解析残留 (lines[i+1]) 出现在可执行代码")
        self.assertNotIn("lines[i+2]", executable,
                        "V37.8.6 位置解析残留 (lines[i+2]) 出现在可执行代码")
        self.assertNotIn("i += 3", executable,
                        "V37.8.6 i += 3 步进残留出现在可执行代码")

    def test_shell_syntax_ok(self):
        """bash -n 语法检查通过"""
        result = subprocess.run(
            ["bash", "-n", self.SHELL_PATH],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0,
                        f"bash -n failed: {result.stderr}")


# ═══════════════════════════════════════════════════════════════════
# 6. 端到端血案重现（用户实际看到的污染输出）
# ═══════════════════════════════════════════════════════════════════
class TestActualBloodLessonScenario(unittest.TestCase):
    """重现用户 WhatsApp 收到的具体污染输出。

    用户 2026-04-15 收到的乱序：
      *中文标题：基于局部与全局信息的节点影响力识别*  ← 第 1 篇正确
      *---*  ← 第 2 篇 cn_title 变成了 ---
      中文标题：面向烹饪替换的常识推理框架  ← 这个应该是 cn_title 但跑到 highlight 槽
      *价值：⭐⭐⭐⭐*  ← 第 3 篇 cn_title 变成了价值行
    """

    def test_v37_8_6_actual_polluted_output_now_correct(self):
        """构造能让 V37.8.6 失败但 V37.8.7 通过的输入"""
        # 假设 LLM 输出第 2 篇缺"要点"，第 3 篇正常
        content = (
            "中文标题：基于局部与全局信息的节点影响力识别\n"
            "要点：结合1到K阶邻居信息识别复杂网络中的关键节点\n"
            "价值：⭐⭐⭐\n"
            "---\n"
            "中文标题：面向烹饪替换的常识推理框架\n"
            # 缺要点
            "价值：⭐⭐⭐⭐\n"
            "---\n"
            "中文标题：第三篇\n"
            "要点：第三篇要点\n"
            "价值：⭐⭐⭐⭐⭐"
        )
        got = op.parse_llm_blocks(content)

        # V37.8.7 行为：3 块都正确解析，无级联污染
        self.assertEqual(len(got), 3)

        # 第 1 篇正确
        self.assertEqual(got[0][0], "基于局部与全局信息的节点影响力识别")
        self.assertNotIn("---", got[0][0])

        # 第 2 篇 cn_title 正确（不是 ---、不是 价值、不是任何其他东西）
        self.assertEqual(got[1][0], "面向烹饪替换的常识推理框架")
        self.assertNotIn("---", got[1][0])
        self.assertNotIn("价值", got[1][0])

        # 第 3 篇完全不受第 2 篇缺要点的影响
        self.assertEqual(got[2][0], "第三篇")
        self.assertEqual(got[2][1], "要点：第三篇要点")
        self.assertEqual(got[2][2], "价值：⭐⭐⭐⭐⭐")


if __name__ == "__main__":
    unittest.main(verbosity=2)

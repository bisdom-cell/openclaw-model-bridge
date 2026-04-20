#!/usr/bin/env python3
"""ArXiv LLM输出解析器单测 — 验证按行模式匹配解析的鲁棒性"""
import unittest
import re


def parse_llm_output(llm_content):
    """从 run_arxiv.sh 提取的解析逻辑（保持一致）"""
    def clean_prefix(line, prefixes):
        for p in prefixes:
            if line.startswith(p):
                return line[len(p):].strip()
        return line

    TITLE_PREFIXES = ['第一行：', '第1行：', '标题：', '中文标题：']
    CONTRIB_PREFIXES = ['第二行：', '第2行：']
    STARS_PREFIXES = ['第三行：', '第3行：']

    parsed_blocks = []
    pending_title = None
    pending_contrib = None

    for raw_line in llm_content.split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r'^[-=*]{3,}$', line):
            continue
        if re.match(r'^(论文\d+[：:]?\s*$|\d+[.、)]\s*$|Paper\s+\d+[：:]?\s*$)', line):
            continue

        if '价值' in line and '⭐' in line:
            stars_line = clean_prefix(line, STARS_PREFIXES)
            if not stars_line.startswith('价值：') and not stars_line.startswith('价值:'):
                stars_line = '价值：' + stars_line.lstrip('价值：').lstrip('价值:')
            if not stars_line.startswith('价值：'):
                stars_line = '价值：' + stars_line
            parsed_blocks.append((
                pending_title or '',
                pending_contrib or '贡献：AI领域相关研究',
                stars_line
            ))
            pending_title = None
            pending_contrib = None
            continue

        if line.startswith('贡献：') or line.startswith('贡献:'):
            pending_contrib = clean_prefix(line, CONTRIB_PREFIXES)
            if not pending_contrib.startswith('贡献：'):
                pending_contrib = '贡献：' + pending_contrib
            continue
        stripped = clean_prefix(line, CONTRIB_PREFIXES)
        if stripped != line and ('贡献' in stripped[:3]):
            pending_contrib = stripped if stripped.startswith('贡献：') else '贡献：' + stripped
            continue

        if pending_title is None:
            title = clean_prefix(line, TITLE_PREFIXES)
            title = re.sub(r'^\d+[.、)\]]\s*', '', title)
            title = title.strip('*').strip()
            pending_title = title

    return parsed_blocks


class TestArxivParser(unittest.TestCase):
    """测试各种 LLM 输出格式的解析鲁棒性"""

    # ── 正常格式：带 --- 分隔符 ──────────────────────────────────────────
    def test_normal_with_separators(self):
        """LLM 按要求输出 --- 分隔符"""
        llm = """大模型强化学习信息锁定
贡献：提出信息自锁机制优化LLM代理推理
价值：⭐⭐⭐⭐
---
句子级心理语言学评测
贡献：探索LLM在记忆性和阅读时间的表现
价值：⭐⭐⭐
---
隐私审计框架
贡献：以人为中心的LLM隐私审计方法
价值：⭐⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')
        self.assertEqual(blocks[1][0], '句子级心理语言学评测')
        self.assertEqual(blocks[2][0], '隐私审计框架')
        self.assertIn('⭐⭐⭐⭐', blocks[0][2])
        self.assertIn('信息自锁', blocks[0][1])

    # ── 无分隔符（用户报告的实际故障场景）─────────────────────────────────
    def test_no_separators(self):
        """LLM 未输出 --- 分隔符，只用空行分隔"""
        llm = """大模型强化学习信息锁定
贡献：提出信息自锁机制优化LLM代理推理
价值：⭐⭐⭐⭐

句子级心理语言学评测
贡献：探索LLM在记忆性和阅读时间的表现
价值：⭐⭐⭐

隐私审计框架
贡献：以人为中心的LLM隐私审计方法
价值：⭐⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')
        self.assertEqual(blocks[1][0], '句子级心理语言学评测')
        self.assertEqual(blocks[2][0], '隐私审计框架')

    # ── LLM 添加序号前缀 ────────────────────────────────────────────────
    def test_numbered_output(self):
        """LLM 在每个 block 前加了序号"""
        llm = """1. 大模型强化学习信息锁定
贡献：提出信息自锁机制
价值：⭐⭐⭐⭐

2. 句子级心理语言学评测
贡献：探索LLM表现
价值：⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')
        self.assertEqual(blocks[1][0], '句子级心理语言学评测')

    # ── LLM 添加 "论文X：" 独立行 ──────────────────────────────────────
    def test_paper_label_lines(self):
        """LLM 输出 "论文1：" 之类的独立行"""
        llm = """论文1：
大模型强化学习信息锁定
贡献：提出信息自锁机制
价值：⭐⭐⭐⭐

论文2：
句子级心理语言学评测
贡献：探索LLM表现
价值：⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')
        self.assertEqual(blocks[1][0], '句子级心理语言学评测')

    # ── LLM 添加"第X行："前缀 ───────────────────────────────────────────
    def test_line_label_prefixes(self):
        """LLM 给每行加了 "第一行：" 等前缀"""
        llm = """第一行：大模型强化学习信息锁定
第二行：贡献：提出信息自锁机制
第三行：价值：⭐⭐⭐⭐
---
第一行：句子级心理语言学评测
第二行：贡献：探索LLM表现
第三行：价值：⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')
        self.assertIn('信息自锁', blocks[0][1])

    # ── 标题带星号包裹 ─────────────────────────────────────────────────
    def test_title_with_asterisks(self):
        """LLM 给标题加了 *xx* 星号"""
        llm = """*大模型强化学习信息锁定*
贡献：提出信息自锁机制
价值：⭐⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')

    # ── 贡献行不带前缀 ─────────────────────────────────────────────────
    def test_contrib_already_prefixed(self):
        """贡献行已经有 贡献： 前缀"""
        llm = """大模型强化学习信息锁定
贡献：提出信息自锁机制优化LLM代理推理
价值：⭐⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0][1].startswith('贡献：'))
        self.assertIn('信息自锁', blocks[0][1])

    # ── 10篇论文（用户实际场景）────────────────────────────────────────
    def test_ten_papers_no_separator(self):
        """10篇论文无分隔符（复现用户报告的 bug 场景）"""
        lines = []
        for i in range(10):
            lines.append(f"第{i+1}篇论文中文标题")
            lines.append(f"贡献：第{i+1}篇的核心贡献描述")
            lines.append(f"价值：{'⭐' * (3 + (i % 3))}")
            lines.append("")
        llm = "\n".join(lines)
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 10)
        for i, block in enumerate(blocks):
            self.assertEqual(block[0], f"第{i+1}篇论文中文标题")
            self.assertIn(f"第{i+1}篇的核心贡献", block[1])

    # ── 缺少贡献行（LLM 跳过）─────────────────────────────────────────
    def test_missing_contrib(self):
        """LLM 只输出标题和价值，漏掉贡献"""
        llm = """大模型强化学习信息锁定
价值：⭐⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')
        self.assertEqual(blocks[0][1], '贡献：AI领域相关研究')  # fallback

    # ── 空输出 ─────────────────────────────────────────────────────────
    def test_empty_output(self):
        """LLM 返回空内容"""
        blocks = parse_llm_output("")
        self.assertEqual(len(blocks), 0)

    # ── 只有分隔符 ────────────────────────────────────────────────────
    def test_only_separators(self):
        """LLM 只输出分隔符"""
        llm = "---\n===\n***\n---"
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 0)

    # ── 混合分隔符 ────────────────────────────────────────────────────
    def test_mixed_separators(self):
        """部分有分隔符，部分无"""
        llm = """标题一
贡献：贡献一
价值：⭐⭐⭐⭐
---
标题二
贡献：贡献二
价值：⭐⭐⭐

标题三
贡献：贡献三
价值：⭐⭐⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 3)

    # ── 中文标题带前缀 "中文标题：" ────────────────────────────────────
    def test_cn_title_prefix(self):
        """LLM 输出 "中文标题：xxx" """
        llm = """中文标题：大模型强化学习信息锁定
贡献：提出信息自锁机制
价值：⭐⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], '大模型强化学习信息锁定')

    # ── 价值行格式变体 ────────────────────────────────────────────────
    def test_stars_variants(self):
        """价值行的各种格式变体"""
        llm = """标题一
贡献：贡献一
价值：⭐⭐⭐⭐

标题二
贡献：贡献二
价值:⭐⭐⭐"""
        blocks = parse_llm_output(llm)
        self.assertEqual(len(blocks), 2)
        # 都应该统一为 价值：（中文冒号）
        self.assertTrue(blocks[0][2].startswith('价值：'))
        self.assertTrue(blocks[1][2].startswith('价值：'))


if __name__ == '__main__':
    unittest.main()

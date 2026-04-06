#!/usr/bin/env python3
"""
ontology_filter.py — 从论文 JSONL 中筛选 ontology 相关论文

供 ArXiv/S2/DBLP 等论文监控脚本调用，检测标题/摘要命中 ontology 关键词。
命中的论文输出中文格式化消息，用于推送到 Discord #ontology 频道。

用法：
    python3 ontology_filter.py <papers.jsonl> <day> <output.txt> [chinese_msg.txt]

    papers.jsonl    — 原始论文数据（英文，用于关键词匹配）
    day             — 日期字符串
    output.txt      — 输出文件
    chinese_msg.txt — 可选，主流程生成的中文消息文件（有则提取中文内容，无则回退英文）

如果无命中，输出文件为空。
"""

import json
import re
import sys

# Ontology 关键词（标题或摘要命中任一即可）
ONTO_KEYWORDS = [
    r'\bontolog',              # ontology, ontological, ontologies
    r'\bknowledge.graph',      # knowledge graph(s)
    r'\bneuro.symbolic',       # neuro-symbolic, neurosymbolic
    r'\bknowledge.represent',  # knowledge representation
    r'\bsymbolic.reason',      # symbolic reasoning
    r'\bsemantic.web',         # semantic web
    r'\bdescription.logic',    # description logic
    r'\bOWL\b',                # OWL (Web Ontology Language)
    r'\bRDF\b',                # RDF
    r'\bSPARQL\b',             # SPARQL
    r'\bformal.ontol',         # formal ontology
    r'\benterprise.ontol',     # enterprise ontology
    r'\btaxonomy',             # taxonomy
    r'\bconceptual.model',     # conceptual model/modeling
]
PATTERN = re.compile('|'.join(ONTO_KEYWORDS), re.IGNORECASE)


def _parse_chinese_blocks(chinese_msg_file):
    """解析中文消息文件，按论文分块。

    主流程消息格式（每篇论文 5 行 + 1 空行）：
        *中文标题*
        作者：XXX 等 | 日期：YYYY-MM-DD
        链接：https://arxiv.org/abs/XXXX
        贡献：...
        价值：⭐⭐⭐
        （空行）

    返回 list[str]，每个元素是一篇论文的完整中文文本块。
    """
    try:
        with open(chinese_msg_file, encoding="utf-8") as f:
            content = f.read()
    except (OSError, IOError):
        return []

    blocks = []
    current = []
    in_paper = False

    for line in content.split("\n"):
        # 论文标题行以 * 开头
        if line.startswith("*") and line.endswith("*"):
            if current and in_paper:
                blocks.append("\n".join(current))
            current = [line]
            in_paper = True
        elif in_paper:
            if line.strip() == "" and current:
                blocks.append("\n".join(current))
                current = []
                in_paper = False
            else:
                current.append(line)

    # 最后一个 block
    if current and in_paper:
        blocks.append("\n".join(current))

    return blocks


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <papers.jsonl> <day> <output.txt> [chinese_msg.txt]",
              file=sys.stderr)
        sys.exit(1)

    papers_file = sys.argv[1]
    day = sys.argv[2]
    output_file = sys.argv[3]
    chinese_msg_file = sys.argv[4] if len(sys.argv) > 4 else None

    # 加载原始论文数据（英文，用于关键词匹配）
    papers = []
    with open(papers_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("["):
                try:
                    papers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # 加载中文消息块（如有）
    cn_blocks = []
    if chinese_msg_file:
        cn_blocks = _parse_chinese_blocks(chinese_msg_file)

    # 检测命中（记录索引）
    matches = []  # (index, paper)
    for i, p in enumerate(papers):
        text = p.get("title", "") + " " + p.get("abstract", "")
        if PATTERN.search(text):
            matches.append((i, p))

    if not matches:
        with open(output_file, "w") as f:
            pass
        sys.exit(0)

    # 组装消息
    lines = [f"\U0001F9E0 Ontology 相关论文 ({day})", ""]

    for idx, p in matches:
        # 优先使用中文内容（按索引对应）
        if idx < len(cn_blocks):
            lines.append(cn_blocks[idx])
        else:
            # 回退英文
            lines.append(f"*{p.get('title', 'Untitled')}*")
            lines.append(f"作者：{p.get('first_author', '?')} 等 | 日期：{p.get('date', '?')}")
            if p.get("arxiv_id"):
                lines.append(f"链接：https://arxiv.org/abs/{p['arxiv_id']}")
            elif p.get("url"):
                lines.append(f"链接：{p['url']}")
        lines.append("")

    with open(output_file, "w") as f:
        f.write("\n".join(lines))

    print(f"[ontology_filter] 命中 {len(matches)}/{len(papers)} 篇"
          f"{'（中文）' if cn_blocks else '（英文回退）'}",
          file=sys.stderr)


if __name__ == "__main__":
    main()

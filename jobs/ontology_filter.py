#!/usr/bin/env python3
"""
ontology_filter.py — 从论文 JSONL 中筛选 ontology 相关论文

供 ArXiv/S2/DBLP 等论文监控脚本调用，检测标题/摘要命中 ontology 关键词。
命中的论文输出为格式化消息，用于推送到 Discord #ontology 频道。

用法：
    python3 ontology_filter.py <papers.jsonl> <day> <output.txt>

输入 JSONL 格式（每行一个 JSON）：
    {"title": "...", "abstract": "...", "first_author": "...", "date": "...", "arxiv_id": "..." }
    或
    {"title": "...", "abstract": "...", "first_author": "...", "date": "...", "url": "..." }

如果无命中，输出文件为空。
"""

import json
import re
import sys

# Ontology 关键词（标题或摘要命中任一即可）
# 宽泛匹配：覆盖 ontology 及其关联领域
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


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <papers.jsonl> <day> <output.txt>", file=sys.stderr)
        sys.exit(1)

    papers_file, day, output_file = sys.argv[1], sys.argv[2], sys.argv[3]

    papers = []
    with open(papers_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("["):
                try:
                    papers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # 检测命中
    onto_papers = []
    for p in papers:
        text = p.get("title", "") + " " + p.get("abstract", "")
        if PATTERN.search(text):
            onto_papers.append(p)

    if not onto_papers:
        with open(output_file, "w") as f:
            pass
        sys.exit(0)

    # 组装消息
    lines = [f"\U0001F9E0 Ontology 相关论文 ({day})", ""]
    for p in onto_papers:
        lines.append(f"*{p.get('title', 'Untitled')}*")
        lines.append(f"作者：{p.get('first_author', '?')} 等 | 日期：{p.get('date', '?')}")
        # 优先 arxiv_id，其次 url
        if p.get("arxiv_id"):
            lines.append(f"链接：https://arxiv.org/abs/{p['arxiv_id']}")
        elif p.get("url"):
            lines.append(f"链接：{p['url']}")
        abstract = p.get("abstract", "")
        if abstract:
            lines.append(f"摘要：{abstract[:200]}...")
        lines.append("")

    with open(output_file, "w") as f:
        f.write("\n".join(lines))

    print(f"[ontology_filter] 命中 {len(onto_papers)}/{len(papers)} 篇", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/bin/bash
# kb_review.sh — KB 跨笔记回顾（V29: LLM 深度分析版）
# 用法：bash kb_review.sh [天数，默认7]
# 功能：收集最近 N 天的 KB 内容 → LLM 跨领域深度分析 → 推送 WhatsApp + 写入 KB
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

DATE=$(date +%Y%m%d)
DAYS="${1:-7}"
KB_DIR="${KB_BASE:-/Users/bisdom/.kb}"
REVIEW_FILE="$KB_DIR/daily/review_${DATE}.md"
PHONE="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$KB_DIR/last_run_review.json"

mkdir -p "$KB_DIR/daily"

log() { echo "[$TS] kb_review: $1"; }

# ── 1. 统计基础信息 ──
NOTE_COUNT=$(ls "$KB_DIR/notes/"*.md 2>/dev/null | wc -l | tr -d ' ' || echo 0)
INDEX_TOTAL=$(python3 - "$KB_DIR/index.json" << 'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        print(len(json.load(f).get('entries', [])))
except (OSError, json.JSONDecodeError):
    print(0)
PYEOF
)

THEMES=$(python3 - "$KB_DIR/index.json" << 'PYEOF'
import json, sys
from collections import Counter
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    tags = Counter()
    for e in d.get('entries', []):
        tags.update(e.get('tags', []))
    print(' / '.join([t for t, _ in tags.most_common(5)]) or '技术/AI')
except (OSError, json.JSONDecodeError):
    print('技术/AI')
PYEOF
)

# ── 2. 收集最近 N 天的笔记内容 ──
NOTES_CONTENT=$(python3 - "$KB_DIR" "$DAYS" << 'PYEOF'
import os, sys, glob
from datetime import datetime, timedelta

kb_dir = sys.argv[1]
days = int(sys.argv[2])
cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
notes_dir = os.path.join(kb_dir, 'notes')
collected = []
total_chars = 0
MAX_CHARS = 8000  # LLM context budget for notes

for f in sorted(glob.glob(os.path.join(notes_dir, '*.md')), reverse=True):
    basename = os.path.basename(f)
    # 文件名格式: YYYYMMDDHHMMSS.md
    file_date = basename[:8]
    if file_date < cutoff:
        break
    try:
        with open(f) as fh:
            content = fh.read().strip()
        # 去掉 frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        # 截取前 300 字
        snippet = content[:300]
        if total_chars + len(snippet) > MAX_CHARS:
            break
        collected.append(f"[{file_date}] {snippet}")
        total_chars += len(snippet)
    except OSError:
        continue

print('\n---\n'.join(collected) if collected else '（本期无笔记）')
PYEOF
)

# ── 3. 收集来源归档的最近内容 ──
SOURCES_CONTENT=$(python3 - "$KB_DIR" "$DAYS" << 'PYEOF'
import os, sys, re
from datetime import datetime, timedelta

kb_dir = sys.argv[1]
days = int(sys.argv[2])
MAX_PER_SOURCE = 2000
sources = {
    'arxiv_daily.md': 'ArXiv论文',
    'hn_daily.md': 'HackerNews',
    'hf_papers_daily.md': 'HuggingFace论文',
    'semantic_scholar_daily.md': 'Semantic Scholar论文',
    'dblp_daily.md': 'DBLP CS论文',
    'acl_anthology.md': 'ACL Anthology NLP论文',
    'pwc_daily.md': 'Papers with Code 论文+代码',
    'github_trending.md': 'GitHub Trending ML/AI',
    'rss_blogs.md': 'RSS 博客订阅',
    'freight_daily.md': '货代动态',
    'openclaw_official.md': 'OpenClaw更新',
}
output = []
# 生成最近N天的日期字符串用于匹配
date_patterns = []
for i in range(days):
    d = (datetime.now() - timedelta(days=i))
    date_patterns.append(d.strftime('%Y-%m-%d'))
    date_patterns.append(d.strftime('%Y%m%d'))

for filename, label in sources.items():
    path = os.path.join(kb_dir, 'sources', filename)
    if not os.path.isfile(path):
        continue
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        continue
    # 提取包含最近日期的段落
    relevant = []
    for line in lines:
        if any(dp in line for dp in date_patterns):
            relevant.append(line.rstrip())
            # 也收集后续几行作为上下文
    if relevant:
        text = '\n'.join(relevant[:30])[:MAX_PER_SOURCE]
        output.append(f"### {label}\n{text}")

print('\n\n'.join(output) if output else '（本期无来源归档更新）')
PYEOF
)

# ── 4. 调用 LLM 进行跨领域深度分析 ──
PROMPT="你是一位知识管理专家和技术趋势分析师。以下是用户知识库中最近 ${DAYS} 天的内容。
请完成以下分析（用中文回答，总字数控制在 600 字以内）：

1. **本期亮点**（3-5个要点）：最值得关注的信息，说明为什么重要
2. **跨领域关联**（2-3条）：不同来源之间的联系（如 ArXiv 论文趋势 + HN 讨论热点 = 行业信号）
3. **行动建议**（2-3条）：基于这些信息，用户应该关注或尝试什么
4. **知识空白**（1-2条）：这些信息没有覆盖到但可能重要的领域

═══ 笔记内容 ═══
${NOTES_CONTENT}

═══ 来源归档 ═══
${SOURCES_CONTENT}

═══ 统计信息 ═══
知识库总条目: ${INDEX_TOTAL} 条
本期笔记: ${NOTE_COUNT} 篇
活跃标签: ${THEMES}"

log "开始 LLM 深度分析（${DAYS} 天回顾）..."

# 规则 #27: 纯推理直接 curl proxy:5002
# 用 Python 构造完整请求体（避免 heredoc/herestring 传递 prompt 时特殊字符问题）
LLM_RESULT=$(python3 - "$DAYS" "$INDEX_TOTAL" "$NOTE_COUNT" "$THEMES" << 'PYEOF'
import json, sys, urllib.request, os

days, index_total, note_count, themes = sys.argv[1:5]

# 读取已收集的内容
notes = os.environ.get('NOTES_CONTENT', '')[:3000]
sources = os.environ.get('SOURCES_CONTENT', '')[:3000]

prompt = f"""你是一位知识管理专家和技术趋势分析师。以下是用户知识库中最近 {days} 天的内容。
请完成以下分析（用中文回答，总字数控制在 600 字以内）：

1. **本期亮点**（3-5个要点）：最值得关注的信息，说明为什么重要
2. **跨领域关联**（2-3条）：不同来源之间的联系（如 ArXiv 论文趋势 + HN 讨论热点 = 行业信号）
3. **行动建议**（2-3条）：基于这些信息，用户应该关注或尝试什么
4. **知识空白**（1-2条）：这些信息没有覆盖到但可能重要的领域

═══ 笔记内容 ═══
{notes}

═══ 来源归档 ═══
{sources}

═══ 统计信息 ═══
知识库总条目: {index_total} 条
本期笔记: {note_count} 篇
活跃标签: {themes}"""

payload = json.dumps({
    'model': 'any',
    'messages': [{'role': 'user', 'content': prompt}],
    'max_tokens': 1000
}).encode()

try:
    req = urllib.request.Request(
        'http://127.0.0.1:5002/v1/chat/completions',
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
        print(data['choices'][0]['message']['content'])
except Exception as e:
    print('', file=sys.stderr)
    print(f'LLM error: {e}', file=sys.stderr)
PYEOF
)

# 传递内容给 Python
export NOTES_CONTENT
export SOURCES_CONTENT

# Fallback：LLM 失败时从实际内容中提取关键信息
if [ -z "${LLM_RESULT// }" ]; then
    log "WARN: LLM 分析失败，使用内容提取模式"
    LLM_RESULT=$(python3 - "$KB_DIR" "$DAYS" "$THEMES" << 'PYEOF'
import os, sys, glob, re
from datetime import datetime, timedelta
from collections import Counter

kb_dir, days_str, themes = sys.argv[1:4]
days = int(days_str)
cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

sections = []
sections.append("## 本期亮点")

# 提取近期笔记标题
notes_dir = os.path.join(kb_dir, 'notes')
note_titles = []
for f in sorted(glob.glob(os.path.join(notes_dir, '*.md')), reverse=True):
    basename = os.path.basename(f)
    if basename[:8] < cutoff:
        break
    try:
        with open(f) as fh:
            content = fh.read().strip()
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
        if lines:
            note_titles.append(lines[0][:100])
    except OSError:
        continue

# 提取各来源的关键内容
sources_map = {
    'arxiv_daily.md': ('📄 ArXiv', 3),
    'hn_daily.md': ('🔥 HN', 3),
    'hf_papers_daily.md': ('🤗 HF', 3),
    'semantic_scholar_daily.md': ('📈 S2', 3),
    'dblp_daily.md': ('📚 DBLP', 3),
    'acl_anthology.md': ('📝 ACL', 2),
    'pwc_daily.md': ('💻 PwC', 3),
    'github_trending.md': ('🚀 GH趋势', 3),
    'rss_blogs.md': ('📖 博客', 3),
    'freight_daily.md': ('🚢 货代', 2),
    'openclaw_official.md': ('⚙️ OpenClaw', 1),
}

date_patterns = []
for i in range(days):
    d = datetime.now() - timedelta(days=i)
    date_patterns.append(d.strftime('%Y-%m-%d'))
    date_patterns.append(d.strftime('%Y%m%d'))

for filename, (label, max_items) in sources_map.items():
    path = os.path.join(kb_dir, 'sources', filename)
    if not os.path.isfile(path):
        continue
    try:
        with open(path) as f:
            all_lines = f.readlines()
    except OSError:
        continue

    # 提取有意义的内容行（标题/要点，跳过纯日期和元数据）
    items = []
    for line in all_lines:
        line = line.strip()
        if not any(dp in line for dp in date_patterns):
            continue
        # 跳过纯日期行
        if re.match(r'^(20\d{2}[-/]\d{2}[-/]\d{2}\s*$|##\s*20|📊)', line):
            continue
        if len(line) > 15:
            # 清理 markdown
            clean = re.sub(r'\*\*?\[?|\]\([^)]*\)\*?\*?', '', line).strip('- *')
            if clean and clean not in items:
                items.append(clean[:100])
                if len(items) >= max_items:
                    break

    if items:
        sections.append(f"\n{label}:")
        for item in items:
            sections.append(f"  • {item}")

# 笔记亮点
if note_titles:
    sections.append(f"\n📝 近期笔记:")
    for t in note_titles[:5]:
        sections.append(f"  • {t}")

sections.append(f"\n## 行动建议")
sections.append(f"- 活跃领域：{themes}")
sections.append(f"- 建议关注以上各来源中标记的关键内容")

print('\n'.join(sections))
PYEOF
)
fi

# ── 5. 生成回顾文件 ──
NOTES_LIST=$(for f in $(ls -t "$KB_DIR/notes/"*.md 2>/dev/null | head -10); do
    echo "- $(basename "$f"): $(head -5 "$f" | { grep -v '^---' || true; } | { grep -v '^#' || true; } | head -1)"
done || true)

cat > "$REVIEW_FILE" << MDEOF
---
date: ${DATE}
type: review
period: ${DAYS}days
llm_analyzed: true
---

# 知识回顾 ${DATE}（最近 ${DAYS} 天）

## 基础统计
- 知识库共 ${INDEX_TOTAL} 条记录
- 活跃标签：${THEMES}

## LLM 深度分析

${LLM_RESULT}

## 本期笔记（最近 10 篇）
${NOTES_LIST}
MDEOF

log "回顾文件已生成: $REVIEW_FILE"

# ── 6. 推送 WhatsApp ──
# 截取分析结果（WhatsApp 消息不宜过长，保留实质内容）
LLM_SHORT=$(echo "$LLM_RESULT" | head -40)
# 限制总长度
LLM_SHORT="${LLM_SHORT:0:1200}"
WA_MSG="📚 知识回顾 ${DATE}（${DAYS}天 | ${INDEX_TOTAL}条 | ${NOTE_COUNT}篇）

${LLM_SHORT}

💡 回复任何话题可深入讨论"

SEND_ERR=$(mktemp)
if openclaw message send --target "$PHONE" --message "$WA_MSG" --json >/dev/null 2>"$SEND_ERR"; then
    log "回顾已推送 WhatsApp"
    printf '{"time":"%s","status":"ok","notes":%d,"llm":true}\n' "$TS" "$NOTE_COUNT" > "$STATUS_FILE"
else
    log "ERROR: WhatsApp 推送失败: $(head -3 "$SEND_ERR" 2>/dev/null)"
    printf '{"time":"%s","status":"send_failed","notes":%d,"llm":true}\n' "$TS" "$NOTE_COUNT" > "$STATUS_FILE"
fi
rm -f "$SEND_ERR"

# ── 7. rsync 备份 ──
rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

log "知识回顾 ${DATE} | 主题：${THEMES} | LLM分析：✓"
log "知识库共 ${INDEX_TOTAL} 条，最新 ${NOTE_COUNT} 篇"
log "回顾文件：${REVIEW_FILE}"

#!/bin/bash
# kb_inject.sh — 每日 KB 摘要生成（供 LLM 对话时查阅）
# 用法：bash kb_inject.sh [天数，默认3]
# 功能：从 notes + sources 提取最近 N 天精华 → 写入 ~/.kb/daily_digest.md
#        LLM 在对话中可通过 read 工具读取此文件，实现"知识库可查"
# 建议 cron：每天 07:00 运行，确保 LLM 拿到最新内容
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

DAYS="${1:-3}"
KB_DIR="${KB_BASE:-/Users/bisdom/.kb}"
DIGEST_FILE="$KB_DIR/daily_digest.md"
INDEX="$KB_DIR/index.json"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')"
DATE=$(date +%Y%m%d)

log() { echo "[$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')] kb_inject: $1"; }

mkdir -p "$KB_DIR"

# ── 生成摘要（Python 一次性处理，避免多次 shell 调用） ──
python3 - "$KB_DIR" "$DAYS" "$TS" "$DIGEST_FILE" "$INDEX" << 'PYEOF'
import os, sys, json, glob
from datetime import datetime, timedelta
from collections import Counter

kb_dir, days_str, ts, digest_file, index_path = sys.argv[1:6]
days = int(days_str)
cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
cutoff_dash = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

sections = []

# ── 统计 ──
try:
    with open(index_path) as f:
        entries = json.load(f).get('entries', [])
    total = len(entries)
    recent = [e for e in entries if e.get('date', '') >= cutoff]
    tags = Counter()
    for e in recent:
        tags.update(e.get('tags', []))
    top_tags = ', '.join(t for t, _ in tags.most_common(5)) or '无'
except (OSError, json.JSONDecodeError):
    total = 0
    recent = []
    top_tags = '无'

sections.append(f"""# KB 每日摘要
> 更新时间：{ts} | 范围：最近 {days} 天 | 总条目：{total}
> 热门标签：{top_tags}
> 本期新增：{len(recent)} 条""")

# ── Notes 精华 ──
notes_dir = os.path.join(kb_dir, 'notes')
note_items = []
total_chars = 0
MAX_NOTE_CHARS = 3000

for f in sorted(glob.glob(os.path.join(notes_dir, '*.md')), reverse=True):
    basename = os.path.basename(f)
    file_date = basename[:8]
    if file_date < cutoff:
        break
    try:
        with open(f) as fh:
            content = fh.read().strip()
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        # 提取第一个有意义的行作为标题
        lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
        title = lines[0][:120] if lines else basename
        snippet = content[:200].replace('\n', ' ')
        if total_chars + len(snippet) > MAX_NOTE_CHARS:
            break
        note_items.append(f"- [{file_date}] {title}")
        total_chars += len(snippet)
    except OSError:
        continue

if note_items:
    sections.append("## 近期笔记\n" + '\n'.join(note_items[:20]))

# ── Sources 归档精华（每个来源取最近内容） ──
sources_map = {
    'arxiv_daily.md': ('ArXiv 论文', 1500),
    'hn_daily.md': ('HackerNews 热帖', 1500),
    'freight_daily.md': ('货代动态', 800),
    'openclaw_official.md': ('OpenClaw 更新', 800),
}

# 生成日期匹配模式
date_patterns = []
for i in range(days):
    d = datetime.now() - timedelta(days=i)
    date_patterns.append(d.strftime('%Y-%m-%d'))
    date_patterns.append(d.strftime('%Y%m%d'))

for filename, (label, max_chars) in sources_map.items():
    path = os.path.join(kb_dir, 'sources', filename)
    if not os.path.isfile(path):
        continue
    try:
        with open(path) as f:
            all_lines = f.readlines()
    except OSError:
        continue

    # 提取含日期的行及其后续行
    relevant = []
    include_next = 0
    for line in all_lines:
        if any(dp in line for dp in date_patterns):
            relevant.append(line.rstrip())
            include_next = 4  # 包含后续4行作为上下文
        elif include_next > 0:
            relevant.append(line.rstrip())
            include_next -= 1

    if relevant:
        text = '\n'.join(relevant)[:max_chars]
        sections.append(f"## {label}\n{text}")

# ── 使用提示 ──
sections.append("""---
> 此文件由 kb_inject.sh 每日自动生成。
> 用户询问最近资讯/论文/新闻时，请参考以上内容回答。
> 需要更详细信息，可用 read 工具读取 ~/.kb/sources/ 下的完整归档。""")

# ── 写入 ──
output = '\n\n'.join(sections) + '\n'
with open(digest_file, 'w') as f:
    f.write(output)

print(f"OK: {digest_file} ({len(output)} chars, {len(note_items)} notes)")
PYEOF

log "摘要已生成: $DIGEST_FILE"

# ── 同步到 workspace CLAUDE.md（每个新 session 自动加载） ──
WORKSPACE_DIR="$HOME/.openclaw/workspace/.openclaw"
WORKSPACE_MD="$WORKSPACE_DIR/CLAUDE.md"
mkdir -p "$WORKSPACE_DIR"

# 静态 PA 指引 + 动态 KB 摘要
cat > "$WORKSPACE_MD" << MDEOF
# Wei — Personal AI Assistant

## 身份
你是 Wei，一个专业的个人 AI 助手。用中文回复，除非用户用其他语言。

## 知识库
以下是最近的知识库摘要，直接参考回答用户关于近期资讯、论文、新闻的问题：

$(cat "$DIGEST_FILE" 2>/dev/null || echo '（摘要暂未生成）')

## 查询更多
- 完整归档：\`~/.kb/sources/\` 目录下各来源文件
- 笔记详情：\`~/.kb/notes/\` 目录下按时间戳命名的 .md 文件
- KB 搜索：\`bash ~/kb_search.sh "关键词"\`
MDEOF

log "workspace CLAUDE.md 已同步 ($(wc -c < "$WORKSPACE_MD" | tr -d ' ') bytes)"

# ── rsync 备份 ──
rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

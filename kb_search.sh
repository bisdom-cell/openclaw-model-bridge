#!/bin/bash
# kb_search.sh — KB 按需查询工具
# 用法：
#   bash kb_search.sh "关键词"              # 全文搜索
#   bash kb_search.sh --tag "arxiv"         # 按 tag 过滤
#   bash kb_search.sh --days 7              # 最近 N 天
#   bash kb_search.sh --days 3 "RAG"        # 组合：最近3天 + 关键词
#   bash kb_search.sh --source arxiv        # 按来源文件搜索（arxiv/hn/freight/openclaw）
#   bash kb_search.sh --summary             # 当前 KB 统计概览

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

KB_DIR="${KB_BASE:-/Users/bisdom/.kb}"
INDEX="$KB_DIR/index.json"

# ── 颜色 ──
BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RESET="\033[0m"

usage() {
    cat << 'EOF'
用法: bash kb_search.sh [选项] [关键词]

选项:
  --tag TAG        按标签过滤（支持部分匹配）
  --days N         只看最近 N 天的条目
  --source NAME    搜索来源文件（arxiv/hn/freight/openclaw）
  --summary        显示 KB 统计概览
  --limit N        最多显示 N 条结果（默认 20）
  -h, --help       显示帮助

示例:
  bash kb_search.sh "RAG"
  bash kb_search.sh --tag arxiv --days 7
  bash kb_search.sh --source hn --days 3
  bash kb_search.sh --summary
EOF
    exit 0
}

# ── 参数解析 ──
MODE="search"
KEYWORD=""
TAG_FILTER=""
DAYS_FILTER=""
SOURCE_FILTER=""
LIMIT=20

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)     TAG_FILTER="$2"; shift 2 ;;
        --days)    DAYS_FILTER="$2"; shift 2 ;;
        --source)  SOURCE_FILTER="$2"; MODE="source"; shift 2 ;;
        --summary) MODE="summary"; shift ;;
        --limit)   LIMIT="$2"; shift 2 ;;
        -h|--help) usage ;;
        -*)        echo "未知选项: $1"; usage ;;
        *)         KEYWORD="$1"; shift ;;
    esac
done

# ── 统计概览 ──
if [ "$MODE" = "summary" ]; then
    echo -e "${BOLD}═══ KB 统计概览 ═══${RESET}"

    # 总条目数
    TOTAL=0
    if [ -f "$INDEX" ]; then
        TOTAL=$(python3 -c "
import json
try:
    with open('$INDEX') as f:
        print(len(json.load(f).get('entries', [])))
except: print(0)
")
    fi

    # notes 文件数
    NOTE_COUNT=$(ls "$KB_DIR/notes/"*.md 2>/dev/null | wc -l | tr -d ' ')

    # sources 文件列表和大小
    echo -e "\n${CYAN}索引条目:${RESET} $TOTAL 条"
    echo -e "${CYAN}笔记文件:${RESET} $NOTE_COUNT 篇"

    echo -e "\n${BOLD}来源归档:${RESET}"
    for src in "$KB_DIR/sources/"*.md; do
        [ -f "$src" ] || continue
        NAME=$(basename "$src" .md)
        LINES=$(wc -l < "$src" | tr -d ' ')
        SIZE=$(du -h "$src" | cut -f1 | tr -d ' ')
        echo -e "  ${GREEN}$NAME${RESET}: ${LINES} 行 (${SIZE})"
    done

    # 热门 tags
    if [ -f "$INDEX" ]; then
        echo -e "\n${BOLD}热门标签 (Top 10):${RESET}"
        python3 - "$INDEX" << 'PYEOF'
import json, sys
from collections import Counter
try:
    with open(sys.argv[1]) as f:
        entries = json.load(f).get('entries', [])
    tags = Counter()
    for e in entries:
        tags.update(e.get('tags', []))
    for tag, count in tags.most_common(10):
        print(f"  {tag}: {count} 条")
except: pass
PYEOF
    fi

    # 最近 7 天活跃度
    echo -e "\n${BOLD}最近 7 天:${RESET}"
    for i in $(seq 0 6); do
        D=$(date -v-${i}d +%Y%m%d 2>/dev/null || date -d "-${i} days" +%Y%m%d 2>/dev/null)
        COUNT=$(ls "$KB_DIR/notes/${D}"*.md 2>/dev/null | wc -l | tr -d ' ')
        BAR=$(printf '%*s' "$COUNT" '' | tr ' ' '█')
        echo -e "  ${DIM}$D${RESET}: ${COUNT} 条 ${GREEN}${BAR}${RESET}"
    done
    exit 0
fi

# ── 来源文件搜索 ──
if [ "$MODE" = "source" ]; then
    # 映射简写到文件名
    case "$SOURCE_FILTER" in
        arxiv)    SRC_FILE="$KB_DIR/sources/arxiv_daily.md" ;;
        hn)       SRC_FILE="$KB_DIR/sources/hn_daily.md" ;;
        freight)  SRC_FILE="$KB_DIR/sources/freight_daily.md" ;;
        openclaw) SRC_FILE="$KB_DIR/sources/openclaw_official.md" ;;
        *)        SRC_FILE="$KB_DIR/sources/${SOURCE_FILTER}.md" ;;
    esac

    if [ ! -f "$SRC_FILE" ]; then
        echo "来源文件不存在: $SRC_FILE"
        echo "可用来源: $(ls "$KB_DIR/sources/"*.md 2>/dev/null | xargs -I{} basename {} .md | tr '\n' ' ')"
        exit 1
    fi

    echo -e "${BOLD}═══ 来源: $(basename "$SRC_FILE" .md) ═══${RESET}\n"

    if [ -n "$DAYS_FILTER" ]; then
        # 按日期过滤：提取最近N天的日期段
        DATES=""
        for i in $(seq 0 $((DAYS_FILTER - 1))); do
            D=$(date -v-${i}d +%Y-%m-%d 2>/dev/null || date -d "-${i} days" +%Y-%m-%d 2>/dev/null)
            DATES="$DATES|$D"
        done
        DATES="${DATES:1}"  # 去掉开头的 |
        grep -E "$DATES" "$SRC_FILE" -A 5 | head -"$((LIMIT * 6))" || echo "（无匹配）"
    elif [ -n "$KEYWORD" ]; then
        grep -i "$KEYWORD" "$SRC_FILE" -B 1 -A 3 | head -"$((LIMIT * 5))" || echo "（无匹配）"
    else
        # 默认显示最后 N 段
        tail -"$((LIMIT * 5))" "$SRC_FILE"
    fi
    exit 0
fi

# ── 全文搜索（index.json + notes/） ──
echo -e "${BOLD}═══ KB 搜索 ═══${RESET}"
[ -n "$KEYWORD" ] && echo -e "关键词: ${CYAN}$KEYWORD${RESET}"
[ -n "$TAG_FILTER" ] && echo -e "标签: ${CYAN}$TAG_FILTER${RESET}"
[ -n "$DAYS_FILTER" ] && echo -e "时间: ${CYAN}最近 $DAYS_FILTER 天${RESET}"
echo ""

# 先从 index.json 搜索（快速）
RESULTS=$(python3 - "$INDEX" "$KEYWORD" "$TAG_FILTER" "$DAYS_FILTER" "$LIMIT" << 'PYEOF'
import json, sys
from datetime import datetime, timedelta

index_path, keyword, tag_filter, days_str, limit_str = sys.argv[1:6]
limit = int(limit_str)

try:
    with open(index_path) as f:
        entries = json.load(f).get('entries', [])
except (OSError, json.JSONDecodeError):
    entries = []

# 日期过滤
if days_str:
    cutoff = (datetime.now() - timedelta(days=int(days_str))).strftime('%Y%m%d')
    entries = [e for e in entries if e.get('date', '') >= cutoff]

# 标签过滤
if tag_filter:
    tag_lower = tag_filter.lower()
    entries = [e for e in entries
               if any(tag_lower in t.lower() for t in e.get('tags', []))]

# 关键词过滤（匹配 summary）
if keyword:
    kw_lower = keyword.lower()
    entries = [e for e in entries if kw_lower in e.get('summary', '').lower()]

# 输出
count = 0
for e in entries[:limit]:
    date = e.get('date', '?')
    tags = ', '.join(e.get('tags', []))
    summary = e.get('summary', '')[:80]
    fpath = e.get('file', '')
    print(f"DATE:{date}|TAGS:{tags}|SUMMARY:{summary}|FILE:{fpath}")
    count += 1

if count == 0 and not keyword:
    print("NO_RESULTS")
PYEOF
)

INDEX_COUNT=0
if [ -n "$RESULTS" ] && [ "$RESULTS" != "NO_RESULTS" ]; then
    while IFS= read -r line; do
        DATE=$(echo "$line" | sed 's/DATE:\([^|]*\).*/\1/')
        TAGS=$(echo "$line" | sed 's/.*TAGS:\([^|]*\).*/\1/')
        SUMMARY=$(echo "$line" | sed 's/.*SUMMARY:\([^|]*\).*/\1/')
        FILE=$(echo "$line" | sed 's/.*FILE:\(.*\)/\1/')
        echo -e "${DIM}$DATE${RESET} ${YELLOW}[$TAGS]${RESET} $SUMMARY"
        echo -e "  ${DIM}→ $FILE${RESET}"
        INDEX_COUNT=$((INDEX_COUNT + 1))
    done <<< "$RESULTS"
fi

# 如果有关键词，也搜索 notes 文件内容（深度搜索）
if [ -n "$KEYWORD" ]; then
    echo -e "\n${BOLD}── 笔记全文匹配 ──${RESET}"
    FILE_LIST=""
    if [ -n "$DAYS_FILTER" ]; then
        for i in $(seq 0 $((DAYS_FILTER - 1))); do
            D=$(date -v-${i}d +%Y%m%d 2>/dev/null || date -d "-${i} days" +%Y%m%d 2>/dev/null)
            FILE_LIST="$FILE_LIST $(ls "$KB_DIR/notes/${D}"*.md 2>/dev/null || true)"
        done
    else
        FILE_LIST=$(ls -t "$KB_DIR/notes/"*.md 2>/dev/null | head -200)
    fi

    GREP_COUNT=0
    if [ -n "$FILE_LIST" ]; then
        for f in $FILE_LIST; do
            if grep -qli "$KEYWORD" "$f" 2>/dev/null; then
                MATCH=$(grep -i "$KEYWORD" "$f" | head -1 | cut -c1-100)
                echo -e "${GREEN}$(basename "$f")${RESET}: $MATCH"
                GREP_COUNT=$((GREP_COUNT + 1))
                [ "$GREP_COUNT" -ge "$LIMIT" ] && break
            fi
        done
    fi

    # 搜索 sources 归档
    echo -e "\n${BOLD}── 来源归档匹配 ──${RESET}"
    for src in "$KB_DIR/sources/"*.md; do
        [ -f "$src" ] || continue
        MATCH_COUNT=$(grep -ci "$KEYWORD" "$src" 2>/dev/null || echo 0)
        if [ "$MATCH_COUNT" -gt 0 ]; then
            echo -e "${GREEN}$(basename "$src")${RESET}: ${MATCH_COUNT} 处匹配"
            grep -i "$KEYWORD" "$src" -m 3 | while IFS= read -r line; do
                echo -e "  ${DIM}${line:0:120}${RESET}"
            done
        fi
    done

    TOTAL=$((INDEX_COUNT + GREP_COUNT))
    echo -e "\n${BOLD}共找到 $TOTAL 条相关结果${RESET}"
else
    echo -e "\n${BOLD}共 $INDEX_COUNT 条结果${RESET}"
fi

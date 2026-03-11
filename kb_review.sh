#!/bin/bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail
DATE=$(date +%Y%m%d)
DAYS="${1:-7}"
KB_DIR="${KB_BASE:-/Users/bisdom/.kb}"
REVIEW_FILE="$KB_DIR/daily/review_${DATE}.md"
mkdir -p "$KB_DIR/daily"

NOTE_COUNT=$(ls "$KB_DIR/notes/"*.md 2>/dev/null | wc -l | tr -d ' ' || echo 0)
INDEX_TOTAL=$(python3 - "$KB_DIR/index.json" << 'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    print(len(d.get('entries', [])))
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
    print(' / '.join([t for t, _ in tags.most_common(3)]) or '技术/AI')
except (OSError, json.JSONDecodeError):
    print('技术/AI')
PYEOF
)

NOTES_TEXT=$(for f in $(ls -t "$KB_DIR/notes/"*.md 2>/dev/null | head -5); do
    echo "- $(basename "$f"): $(head -5 "$f" | { grep -v '^---' || true; } | { grep -v '^#' || true; } | head -1)"
done || true)

cat > "$REVIEW_FILE" << MDEOF
---
date: ${DATE}
type: review
period: ${DAYS}days
---

# 知识回顾 ${DATE}

## 本期主题
${THEMES}

## 知识连接
- 知识库共 ${INDEX_TOTAL} 条记录
- 最活跃标签：${THEMES}

## 本期笔记
${NOTES_TEXT}

## 综合洞见
- 基于最新 ${NOTE_COUNT} 条笔记的自动归档回顾
- 建议关注跨领域知识点的连接

## 知识空白
- 待人工补充深度洞见
MDEOF

# ── rsync备份 ────────────────────────────────────────────────────────
rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

echo "[kb_review] 知识回顾 ${DATE} | 主题：${THEMES}"
echo "[kb_review] 知识库共 ${INDEX_TOTAL} 条，最新 ${NOTE_COUNT} 篇已归档"
echo "[kb_review] 回顾文件：${REVIEW_FILE}"

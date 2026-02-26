#!/bin/bash
DATE=$(date +%Y%m%d)
DAYS="${1:-7}"
KB_DIR="/Users/bisdom/.kb"
REVIEW_FILE="$KB_DIR/daily/review_${DATE}.md"
mkdir -p "$KB_DIR/daily"

NOTE_COUNT=$(ls "$KB_DIR/notes/"*.md 2>/dev/null | wc -l | tr -d ' ')
INDEX_TOTAL=$(cat "$KB_DIR/index.json" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('entries',[])))" 2>/dev/null || echo "0")
THEMES=$(cat "$KB_DIR/index.json" 2>/dev/null | python3 -c "
import json,sys
from collections import Counter
d=json.load(sys.stdin)
tags=Counter()
[tags.update(e.get('tags',[])) for e in d.get('entries',[])]
print(' / '.join([t for t,_ in tags.most_common(3)]) or '技术/AI')
" 2>/dev/null || echo "技术/AI")

NOTES_TEXT=$(for f in $(ls -t "$KB_DIR/notes/"*.md 2>/dev/null | head -5); do
    echo "- $(basename $f): $(head -5 $f | grep -v '^---' | grep -v '^#' | head -1)"
done)

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

echo ""
echo "📚 知识回顾 ${DATE}"
echo "━━━━━━━━━━━━━━━"
echo "🗂️ 本期主题：${THEMES}"
echo "━━━━━━━━━━━━━━━"
echo "🔗 关键连接："
echo "  · 知识库共 ${INDEX_TOTAL} 条，持续积累中"
echo "  · 最新 ${NOTE_COUNT} 篇笔记已归档"
echo "━━━━━━━━━━━━━━━"
echo "💡 综合洞见："
echo "  · OpenClaw系统配置与知识管理双轨并行"
echo "  · 建议定期回顾交叉领域知识点"
echo "━━━━━━━━━━━━━━━"
echo "📝 笔记 ${NOTE_COUNT} 条 | 回顾文件：review_${DATE}.md"

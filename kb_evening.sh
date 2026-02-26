#!/bin/bash
DATE=$(date +%Y%m%d)
KB_DIR="/Users/bisdom/.kb"
PHONE="+85200000000"

TODAY_FILES=$(ls "$KB_DIR/notes/" 2>/dev/null | grep "^$DATE")

if [ -z "$TODAY_FILES" ]; then
    MSG="今日暂无新增知识记录 💪"
else
    TOTAL=$(echo "$TODAY_FILES" | wc -l | tr -d ' ')
    FIRST_FILE=$(echo "$TODAY_FILES" | head -1)
    CONTENT=$(head -20 "$KB_DIR/notes/$FIRST_FILE" | grep -v '^---' | grep -v '^#' | grep -v '^$' | head -3 | tr '\n' ' ')
    FILE_LIST=$(echo "$TODAY_FILES" | head -5 | while read f; do echo "  · $f"; done)
    MSG="📚 今日知识摘要 $DATE
━━━━━━━━━━━━━━━
📝 新增笔记：$TOTAL 条
💡 摘要：${CONTENT:0:100}
━━━━━━━━━━━━━━━
🗂️ 文件列表：
$FILE_LIST"
fi

openclaw message send --channel whatsapp -t "$PHONE" -m "$MSG"
echo "✅ 发送完成: $DATE"

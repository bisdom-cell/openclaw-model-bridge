#!/bin/bash
LOCKDIR="/Users/bisdom/.kb/.write.lock.d"
while ! mkdir "$LOCKDIR" 2>/dev/null; do sleep 0.1; done
trap "rmdir '$LOCKDIR'" EXIT

CONTENT="$1"
TAGS="${2:-技术/AI}"
TYPE="${3:-note}"
DATE=$(date +%Y%m%d)
TS=$(date +%Y%m%d%H%M%S)

FILEPATH="/Users/bisdom/.kb/notes/${TS}.md"
mkdir -p /Users/bisdom/.kb/notes /Users/bisdom/.kb/topics

cat > "$FILEPATH" << MDEOF
---
date: $DATE
tags: [$TAGS]
source: direct
type: $TYPE
---

# $CONTENT

## 核心要点
- $CONTENT

## 记录时间
$TS
MDEOF

[ ! -f "$FILEPATH" ] && echo "❌ 写入失败" && exit 1

INDEX="/Users/bisdom/.kb/index.json"
[ ! -f "$INDEX" ] && echo '{"entries":[]}' > "$INDEX"

python3 - "$CONTENT" "$DATE" "$TS" "$TAGS" "$TYPE" << 'PYEOF'
import json, sys
content, date, ts, tags, typ = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
index = f"/Users/bisdom/.kb/index.json"
with open(index, "r") as f:
    data = json.load(f)
data.setdefault("entries", []).insert(0, {
    "date": date,
    "file": f"notes/{ts}.md",
    "tags": [tags],
    "type": typ,
    "summary": content[:50]
})
tmpfile = index + ".tmp"
with open(tmpfile, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
import os; os.replace(tmpfile, index)
PYEOF

TOPIC=$(echo "$TAGS" | cut -d'/' -f1)
echo "- [$DATE] $CONTENT → [notes/${TS}.md]" >> "/Users/bisdom/.kb/topics/${TOPIC}.md"

echo "✅ 已记录到知识库"
echo "📁 文件：$FILEPATH"
echo "🏷️ 标签：$TAGS"
echo "📝 类型：$TYPE"
echo "💡 摘要：${CONTENT:0:50}"

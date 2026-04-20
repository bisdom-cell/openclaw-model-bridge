#!/bin/bash
# 使用 mkdir 原子锁（macOS 兼容），进程退出后 trap 自动释放
KB_BASE="${KB_BASE:-/Users/bisdom/.kb}"
LOCKDIR="$KB_BASE/.write.lockdir"
while ! mkdir "$LOCKDIR" 2>/dev/null; do sleep 0.1; done
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

CONTENT="$1"
TYPE="${3:-note}"

# 标签自动推断：有显式传入则用传入值，否则调用 kb_autotag.py
if [ -n "$2" ]; then
    TAGS="$2"
else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    TAGS=$(python3 "$SCRIPT_DIR/kb_autotag.py" "$CONTENT" 2>/dev/null || echo "技术/AI")
fi
DATE=$(date +%Y%m%d)
TS=$(date +%Y%m%d%H%M%S)

FILEPATH="$KB_BASE/notes/${TS}.md"
mkdir -p "$KB_BASE/notes" "$KB_BASE/topics"

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

[ ! -f "$FILEPATH" ] && echo "[kb_write] ERROR: 写入失败" && exit 1

INDEX="$KB_BASE/index.json"
[ ! -f "$INDEX" ] && echo '{"entries":[]}' > "$INDEX"

python3 - "$CONTENT" "$DATE" "$TS" "$TAGS" "$TYPE" "$INDEX" << 'PYEOF'
import json, sys
content, date, ts, tags, typ, index = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
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
echo "- [$DATE] $CONTENT → [notes/${TS}.md]" >> "$KB_BASE/topics/${TOPIC}.md"

echo "[kb_write] OK: 已记录到知识库"
echo "[kb_write] 文件：$FILEPATH"
echo "[kb_write] 标签：$TAGS | 类型：$TYPE | 摘要：${CONTENT:0:50}"

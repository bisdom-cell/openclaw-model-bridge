#!/bin/bash
# 使用 flock 文件锁替代目录忙等待锁，避免 CPU 空转且在进程异常退出后自动释放
KB_BASE="${KB_BASE:-/Users/bisdom/.kb}"
LOCKFILE="$KB_BASE/.write.lock"
exec 9>"$LOCKFILE"
flock -x 9

CONTENT="$1"
TAGS="${2:-技术/AI}"
TYPE="${3:-note}"
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

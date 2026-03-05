#!/bin/bash
# run_hn.sh - Hacker News AI/Tech精选 Watcher
# 触发时间：每3小时:45分（系统crontab）
# v23fix2：
#   - Bug修复：inbox写入提前到Python dedup阶段，防止LLM失败时重复处理同批URL
#   - Bug修复：正则宽容度提升，兼容LLM输出【】格式及多余空格
#   - 新增：LLM原始输出写入日志，方便排查格式问题
#   - 新增：HN_URL空值保护（防止shell loop中json解析失败导致空URL写inbox）

SCRIPT_DIR="$HOME/.openclaw/jobs/hn_watcher"
CACHE_DIR="$SCRIPT_DIR/cache"
INBOX="$HOME/.kb/inbox.md"
KB_SOURCE="$HOME/.kb/sources/hn_daily.md"
KB_DIR="$HOME/.kb"
SSD_BACKUP="/Volumes/MOVESPEED/KB/"
MSG_FILE="$CACHE_DIR/hn_message.txt"
NEW_FILE="$CACHE_DIR/hn_new.jsonl"
RSS_FILE="$CACHE_DIR/hn_frontpage.xml"
LLM_RAW_LOG="$CACHE_DIR/llm_raw_last.txt"
TO="+85256190187"

mkdir -p "$CACHE_DIR"
touch "$INBOX"
touch "$KB_SOURCE"

curl -s --max-time 30 "https://hnrss.org/frontpage" -o "$RSS_FILE" 2>/dev/null
if [ ! -s "$RSS_FILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: curl失败，跳过。"
    exit 0
fi

# ★ Fix1: Python dedup阶段同步写inbox，防止LLM失败时URL未记录导致重复处理
python3 - << 'PYEOF' > "$NEW_FILE"
import xml.etree.ElementTree as ET
import json, os

INBOX_PATH = os.path.expanduser("~/.kb/inbox.md")
RSS_PATH   = os.path.expanduser("~/.openclaw/jobs/hn_watcher/cache/hn_frontpage.xml")

KEYWORDS = [
    'ai', 'llm', 'gpt', 'claude', 'gemini', 'llama', 'mistral', 'qwen', 'deepseek',
    'neural', 'machine learning', 'deep learning', 'transformer', 'diffusion',
    'inference', 'fine-tun', 'rag', 'agent', 'model',
    'programming', 'software', 'developer', 'engineering', 'compiler',
    'algorithm', 'data structure', 'system design', 'architecture',
    'rust', 'golang', 'python', 'typescript', 'wasm', 'llvm',
    'database', 'postgres', 'sqlite', 'redis', 'kafka', 'performance',
    'linux', 'kernel', 'container', 'kubernetes', 'docker', 'cloud',
    'security', 'vulnerability', 'exploit', 'cryptography', 'privacy', 'cve',
    'open source', 'github', 'framework', 'library', 'api', 'cli', 'terminal',
    'cpu', 'gpu', 'hardware', 'chip', 'memory', 'benchmark',
    'startup', 'ycombinator', 'robotics', 'autonomous', 'automation',
]

try:
    inbox_content = open(INBOX_PATH).read()
except Exception:
    inbox_content = ""

try:
    tree = ET.parse(RSS_PATH)
    root = tree.getroot()
    items = root.findall('.//item')
except Exception:
    import sys; sys.exit(0)

results = []
new_inbox_lines = []

for item in items[:50]:
    title    = (item.findtext('title')    or '').strip()
    link     = (item.findtext('link')     or '').strip()
    comments = (item.findtext('comments') or link).strip()
    desc     = (item.findtext('description') or '').strip()[:400]
    pubdate  = (item.findtext('pubDate')  or '').strip()

    if not title or not link:
        continue
    if not any(kw in title.lower() for kw in KEYWORDS):
        continue

    hn_url = comments if comments.startswith('http') else link
    if hn_url in inbox_content:
        continue

    results.append(json.dumps({
        'title': title, 'hn_url': hn_url,
        'source_url': link, 'desc': desc, 'pubdate': pubdate,
    }, ensure_ascii=False))

    # ★ Fix1核心：立即记录到inbox，无论后续LLM是否成功
    new_inbox_lines.append(f"- {hn_url}")
    inbox_content += f"\n- {hn_url}"  # 更新内存中的inbox防止同批重复

    if len(results) >= 5:
        break

# 批量追加到inbox（原子性：一次write，而不是多次append）
if new_inbox_lines:
    with open(INBOX_PATH, 'a') as f:
        f.write('\n'.join(new_inbox_lines) + '\n')

print('\n'.join(results))
PYEOF

NEW_COUNT=$(wc -l < "$NEW_FILE" 2>/dev/null || echo 0)
if [ "$NEW_COUNT" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: 暂无新AI/Tech内容。"
    exit 0
fi

TODAY=$(date '+%Y-%m-%d')

# 用Python读取所有条目，构建批量Prompt，一次LLM调用处理全部
RESULT=$(python3 - << 'PYEOF'
import json, os, subprocess, re, sys

new_file = os.path.expanduser("~/.openclaw/jobs/hn_watcher/cache/hn_new.jsonl")
llm_raw_log = os.path.expanduser("~/.openclaw/jobs/hn_watcher/cache/llm_raw_last.txt")
items = []
with open(new_file) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except:
            continue

if not items:
    sys.exit(0)

# 构建批量Prompt
titles_text = "\n".join(f"{i}. {item['title']}" for i, item in enumerate(items))
prompt = f"""将以下{len(items)}条英文标题翻译成中文，每条给出简短要点和价值评级。

{titles_text}

严格按以下格式输出，不要任何其他内容：
0.中文标题：[翻译]
0.要点：[≤25字要点]
0.价值：[⭐到⭐⭐⭐⭐⭐]

1.中文标题：[翻译]
1.要点：[≤25字要点]
1.价值：[⭐到⭐⭐⭐⭐⭐]

以此类推，共{len(items)}条。"""

# 单次LLM调用
result = subprocess.run(
    ["openclaw", "agent", "--to", "+85256190187",
     "--message", prompt, "--session", "isolated", "--thinking", "minimal", "--timeout", "180"],
    capture_output=True, text=True, timeout=200
)
llm_out = result.stdout or ""

# ★ Fix2：LLM原始输出写入日志，方便排查格式匹配问题
try:
    with open(llm_raw_log, 'w') as f:
        f.write(f"=== {__import__('datetime').datetime.now()} ===\n")
        f.write(f"items: {len(items)}\n")
        f.write(f"stdout_len: {len(llm_out)}\n")
        f.write("--- stdout ---\n")
        f.write(llm_out[:3000])
        f.write("\n--- stderr ---\n")
        f.write((result.stderr or "")[:500])
except:
    pass

# 去除ANSI转义码
llm_out = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', llm_out)

# ★ Fix2核心：正则宽容度提升，兼容【】格式和多余空白
# 原：r'^(\d+)[\.。]?\s*(中文标题|要点|价值)\s*[：:]\s*(.+)$'
# 新：支持 【中文标题】、**中文标题**、多余空格等LLM输出变体
parsed = {}
for line in llm_out.splitlines():
    line = line.strip()
    # 兼容格式：0.中文标题：xxx / 0. 【中文标题】：xxx / 0.中文标题:xxx
    m = re.match(
        r'^(\d+)[\.。]?\s*[【\*]*(中文标题|要点|价值)[】\*]*\s*[：:]\s*(.+)$',
        line
    )
    if m:
        idx, key, val = m.group(1), m.group(2), m.group(3).strip()
        # 清理val中可能的markdown符号
        val = re.sub(r'^\*+|\*+$', '', val).strip()
        if idx not in parsed:
            parsed[idx] = {}
        parsed[idx][key] = val

# 统计解析成功率（写入日志）
success_count = sum(1 for i in range(len(items)) if len(parsed.get(str(i), {})) >= 2)
try:
    with open(llm_raw_log, 'a') as f:
        f.write(f"\n--- parsed ---\n")
        f.write(f"success: {success_count}/{len(items)}\n")
        f.write(json.dumps(parsed, ensure_ascii=False, indent=2)[:1000])
except:
    pass

# 输出结果供shell使用
for i, item in enumerate(items):
    zh_title = parsed.get(str(i), {}).get('中文标题', item['title'])
    point    = parsed.get(str(i), {}).get('要点', '技术内容，详见原文')
    stars    = parsed.get(str(i), {}).get('价值', '⭐⭐⭐')
    print(json.dumps({
        'zh_title': zh_title,
        'point': point,
        'stars': stars,
        'title': item['title'],
        'hn_url': item['hn_url'],
    }, ensure_ascii=False))
PYEOF
)

if [ -z "$RESULT" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: LLM调用失败（URL已记录inbox，不会重复处理）。"
    exit 1
fi

printf "💻 HN 头版精选 (%s)\n\n" "$TODAY" > "$MSG_FILE"
SENT_COUNT=0

while IFS= read -r LINE; do
    [ -z "$LINE" ] && continue

    ZH_TITLE=$(echo "$LINE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['zh_title'])" 2>/dev/null)
    POINT=$(echo "$LINE"    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['point'])"    2>/dev/null)
    STARS=$(echo "$LINE"    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['stars'])"    2>/dev/null)
    TITLE=$(echo "$LINE"    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['title'])"    2>/dev/null)
    HN_URL=$(echo "$LINE"   | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['hn_url'])"   2>/dev/null)

    # ★ Fix3：HN_URL空值保护，防止空URL写入KB和消息
    [ -z "$HN_URL" ] && continue

    [ -z "$ZH_TITLE" ] && ZH_TITLE="$TITLE"
    [ -z "$POINT"    ] && POINT="技术内容，详见原文"
    [ -z "$STARS"    ] && STARS="⭐⭐⭐"

    printf "%s\n链接：%s\n要点：%s\n价值：%s\n\n" "$ZH_TITLE" "$HN_URL" "$POINT" "$STARS" >> "$MSG_FILE"
    # ★ Fix1：inbox已在Python阶段写入，此处不重复写
    printf -- "- **[%s](%s)** | %s | 要点：%s | %s\n" "$TITLE" "$HN_URL" "$TODAY" "$POINT" "$STARS" >> "$KB_SOURCE"
    SENT_COUNT=$((SENT_COUNT + 1))
done <<< "$RESULT"

if [ "$SENT_COUNT" -gt 0 ]; then
    openclaw message send --target "$TO" --message "$(cat "$MSG_FILE")" --json >/dev/null 2>&1 || true
fi

rsync -a --quiet "$KB_DIR/" "$SSD_BACKUP" 2>/dev/null || true
echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: 已推送 ${SENT_COUNT} 条AI/Tech精选（单次批量LLM）。"

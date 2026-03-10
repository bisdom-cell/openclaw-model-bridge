#!/bin/bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
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
TO="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE_DIR/last_run.json"

log() { echo "[$TS] hn_watcher: $1"; }

mkdir -p "$CACHE_DIR"
touch "$INBOX"
touch "$KB_SOURCE"

curl -s --max-time 30 "https://hnrss.org/frontpage" -o "$RSS_FILE" 2>/dev/null
if [ ! -s "$RSS_FILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: curl失败，跳过。"
    printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
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
    log "暂无新AI/Tech内容。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi

TODAY=$(date '+%Y-%m-%d')

# 用Python读取所有条目，构建批量Prompt，一次LLM调用处理全部
RESULT=$(python3 - << 'PYEOF'
import json, os, re, sys

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
        except (json.JSONDecodeError, ValueError):
            continue

if not items:
    sys.exit(0)

# 构建批量Prompt — 严格纯文本格式，禁止Markdown以减少解析变体
titles_text = "\n".join(f"{i}. {item['title']}" for i, item in enumerate(items))
prompt = f"""将以下{len(items)}条英文标题翻译成中文，逐条输出。

{titles_text}

【输出规则】
- 严格按下方格式，每条恰好3行，条目间空一行
- 不要加任何多余文字、标点装饰、Markdown符号（*、#、[]等）
- 数字序号与字段名之间不加空格

格式（每条3行）：
0.中文标题：翻译文字
0.要点：不超过25字的核心要点
0.价值：⭐到⭐⭐⭐⭐⭐

1.中文标题：翻译文字
1.要点：不超过25字的核心要点
1.价值：⭐到⭐⭐⭐⭐⭐

共{len(items)}条，依次输出，序号从0开始。"""

# 规则 #27: 纯推理直接 curl proxy:5002，禁止用 openclaw agent（#94教训）
import urllib.request
payload = json.dumps({
    "model": "Qwen3-235B-A22B-Instruct-2507-W8A8",
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": 4096,
    "temperature": 0.3
}).encode()

llm_out = ""
llm_err = ""
try:
    req = urllib.request.Request(
        "http://127.0.0.1:5002/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        resp_data = json.loads(resp.read())
        llm_out = resp_data["choices"][0]["message"]["content"]
except Exception as e:
    llm_err = str(e)
    print(f"[hn_watcher] LLM调用失败: {e}", file=sys.stderr)

# LLM原始输出写入日志，方便排查格式匹配问题
try:
    with open(llm_raw_log, 'w') as f:
        f.write(f"=== {__import__('datetime').datetime.now()} ===\n")
        f.write(f"items: {len(items)}\n")
        f.write(f"stdout_len: {len(llm_out)}\n")
        f.write("--- stdout ---\n")
        f.write(llm_out[:3000])
        f.write("\n--- error ---\n")
        f.write(llm_err[:500])
except OSError as e:
    print(f"[hn_watcher] WARN: 无法写入 llm_raw_log: {e}", file=sys.stderr)

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
except OSError as e:
    print(f"[hn_watcher] WARN: 无法追加解析日志: {e}", file=sys.stderr)

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
    ERR_MSG="⚠️ HN Watcher LLM调用失败（$(date '+%Y-%m-%d %H:%M')），请检查 $LLM_RAW_LOG"
    echo "$ERR_MSG"
    openclaw message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

# 429限流检测：防止把错误文案推送到WhatsApp
if echo "$RESULT" | grep -q "429"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: ⚠️ 检测到429限流，跳过本轮推送。"
    exit 0
fi

printf "💻 HN 头版精选 (%s)\n\n" "$TODAY" > "$MSG_FILE"

# 单次 Python 调用处理全部结果，避免每条数据重复启动 5 个子进程
SENT_COUNT=$(python3 - "$TODAY" "$MSG_FILE" "$KB_SOURCE" << 'PYEOF'
import json, sys

today, msg_file, kb_source = sys.argv[1], sys.argv[2], sys.argv[3]
sent = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        continue
    hn_url   = d.get('hn_url', '').strip()
    if not hn_url:           # ★ Fix3：HN_URL空值保护
        continue
    zh_title = d.get('zh_title') or d.get('title', '')
    point    = d.get('point')    or '技术内容，详见原文'
    stars    = d.get('stars')    or '⭐⭐⭐'
    title    = d.get('title', zh_title)
    with open(msg_file, 'a') as f:
        f.write(f"{zh_title}\n链接：{hn_url}\n要点：{point}\n价值：{stars}\n\n")
    with open(kb_source, 'a') as f:
        f.write(f"- **[{title}]({hn_url})** | {today} | 要点：{point} | {stars}\n")
    sent += 1
print(sent)
PYEOF
<<< "$RESULT")

if [ "$SENT_COUNT" -gt 0 ]; then
    if openclaw message send --target "$TO" --message "$(cat "$MSG_FILE")" --json >/dev/null 2>&1; then
        log "已推送 ${SENT_COUNT} 条AI/Tech精选（单次批量LLM）。"
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$SENT_COUNT" > "$STATUS_FILE"
    else
        log "ERROR: 推送失败（${SENT_COUNT} 条待发），请检查 gateway。"
        printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$SENT_COUNT" > "$STATUS_FILE"
    fi
else
    log "LLM解析完成但无有效条目。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
fi

rsync -a --quiet "$KB_DIR/" "$SSD_BACKUP" 2>/dev/null || true

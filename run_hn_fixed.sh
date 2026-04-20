#!/bin/bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Homebrew python3 → 3.14 在 cron 环境 dlopen 失败，用系统 Python 3.9（只需标准库）
PYTHON3=/usr/bin/python3

# 防重叠执行（mkdir 原子锁，macOS 兼容）
LOCK="/tmp/hn_watcher.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[hn] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# run_hn.sh - Hacker News AI/Tech精选 Watcher
# 触发时间：每3小时:45分 HKT（系统crontab，与 ArXiv 错开45分钟）
# v23fix2：
#   - Bug修复：inbox写入提前到Python dedup阶段，防止LLM失败时重复处理同批URL
#   - Bug修复：正则宽容度提升，兼容LLM输出【】格式及多余空格
#   - 新增：LLM原始输出写入日志，方便排查格式问题
#   - 新增：HN_URL空值保护（防止shell loop中json解析失败导致空URL写inbox）

SCRIPT_DIR="$HOME/.openclaw/jobs/hn_watcher"
CACHE_DIR="$SCRIPT_DIR/cache"
INBOX="$HOME/.kb/inbox.md"
KB_SOURCE="$HOME/.kb/sources/hn_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
KB_DIR="$HOME/.kb"
SSD_BACKUP="/Volumes/MOVESPEED/KB/"
MSG_FILE="$CACHE_DIR/hn_message.txt"
NEW_FILE="$CACHE_DIR/hn_new.jsonl"
RSS_FILE="$CACHE_DIR/hn_frontpage.xml"
LLM_RAW_LOG="$CACHE_DIR/llm_raw_last.txt"
TO="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE_DIR/last_run.json"

log() { echo "[$TS] hn_watcher: $1" >&2; }

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
$PYTHON3 - << 'PYEOF' > "$NEW_FILE"
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
RESULT=$($PYTHON3 - << 'PYEOF'
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

# 构建批量Prompt — 包含 description 以生成有意义的要点，要求 JSON 输出避免正则解析
items_text = ""
for i, item in enumerate(items):
    desc = item.get('desc', '').strip()
    # 清理 HTML 标签
    desc = re.sub(r'<[^>]+>', '', desc).strip()
    if desc:
        items_text += f"\n{i}. {item['title']}\n   摘要：{desc[:300]}\n"
    else:
        items_text += f"\n{i}. {item['title']}\n"

prompt = f"""将以下{len(items)}条英文科技新闻翻译并提炼要点。

{items_text}

请以 JSON 数组格式输出，每个元素包含3个字段：
- zh_title: 中文标题
- point: 核心要点（15-25字，必须具体，禁止"详见原文"等空话）
- stars: 价值评级（⭐到⭐⭐⭐⭐⭐）

只输出 JSON 数组，不要其他文字。示例格式：
[{{"zh_title":"中文标题","point":"具体技术要点","stars":"⭐⭐⭐"}}]"""

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
llm_failed = False
for _attempt in range(3):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:5002/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = json.loads(resp.read())
            llm_out = resp_data["choices"][0]["message"]["content"]
            break  # 成功，退出重试
    except Exception as e:
        llm_err = str(e)
        print(f"[hn_watcher] LLM调用失败 (attempt {_attempt+1}/3): {e}", file=sys.stderr)
        if _attempt < 2:
            import time as _t
            _wait = 15 * (_attempt + 1)  # 15s, 30s
            print(f"[hn_watcher] 等待 {_wait}s 后重试...", file=sys.stderr)
            _t.sleep(_wait)

if not llm_out:
    llm_failed = True
    print(f"[hn_watcher] LLM_FAILED=true (3次重试全部失败)", file=sys.stderr)

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

# 去除ANSI转义码和 Qwen3 <think> 标签
llm_out = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', llm_out)
llm_out = re.sub(r'<think>.*?</think>', '', llm_out, flags=re.DOTALL).strip()

# ★ V38: JSON 优先解析，正则兜底
parsed_items = []

# 尝试1: 直接 JSON 解析
try:
    # 提取 JSON 数组（可能被 ```json 包裹）
    json_text = llm_out
    json_match = re.search(r'\[.*\]', json_text, re.DOTALL)
    if json_match:
        parsed_items = json.loads(json_match.group())
except (json.JSONDecodeError, ValueError):
    pass

# 尝试2: 如果 JSON 失败，回退到正则解析（向后兼容旧格式）
if not parsed_items:
    parsed = {}
    for line in llm_out.splitlines():
        line = line.strip()
        m = re.match(
            r'^(\d+)[\.。]?\s*[【\*]*(中文标题|要点|价值|zh_title|point|stars)[】\*]*\s*[：:]\s*(.+)$',
            line
        )
        if m:
            idx, key, val = m.group(1), m.group(2), m.group(3).strip()
            val = re.sub(r'^\*+|\*+$', '', val).strip()
            if idx not in parsed:
                parsed[idx] = {}
            # 统一 key 名
            key_map = {'中文标题': 'zh_title', '要点': 'point', '价值': 'stars'}
            parsed[idx][key_map.get(key, key)] = val
    # 转换为列表格式
    for i in range(len(items)):
        if str(i) in parsed:
            parsed_items.append(parsed[str(i)])
        else:
            parsed_items.append({})

# 统计解析成功率（写入日志）
success_count = sum(1 for p in parsed_items if p.get('zh_title') or p.get('point'))
try:
    with open(llm_raw_log, 'a') as f:
        f.write(f"\n--- parsed ---\n")
        f.write(f"method: {'json' if parsed_items and isinstance(parsed_items[0], dict) and 'zh_title' in parsed_items[0] else 'regex_fallback'}\n")
        f.write(f"success: {success_count}/{len(items)}\n")
        f.write(json.dumps(parsed_items, ensure_ascii=False, indent=2)[:1000])
except OSError as e:
    print(f"[hn_watcher] WARN: 无法追加解析日志: {e}", file=sys.stderr)

# LLM 完全失败时，输出特殊标记让 shell 层知道（而不是输出回退垃圾）
if llm_failed:
    print("__LLM_FAILED__")
    sys.exit(0)

# 输出结果供shell使用
for i, item in enumerate(items):
    p = parsed_items[i] if i < len(parsed_items) else {}
    zh_title = p.get('zh_title') or p.get('中文标题') or item['title']
    point    = p.get('point') or p.get('要点') or ''
    stars    = p.get('stars') or p.get('价值') or '⭐⭐⭐'
    # 过滤空话要点：如果要点是空的或含"详见原文"，用标题自动生成
    if not point or '详见原文' in point or len(point) < 4:
        # 从标题推断一个基本要点
        point = zh_title[:25] if zh_title != item['title'] else item['title'][:40]
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
    # V37.4.3: 告警消息加 [SYSTEM_ALERT] 隔离标记
    ERR_MSG="[SYSTEM_ALERT]
⚠️ HN Watcher LLM调用失败（$(date '+%Y-%m-%d %H:%M')），请检查 $LLM_RAW_LOG"
    echo "$ERR_MSG"
    openclaw message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    openclaw message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

# LLM 全部失败检测（3次重试后仍失败）— 不推送垃圾，而是告警
if echo "$RESULT" | grep -q "__LLM_FAILED__"; then
    log "⚠️ LLM 3次重试全部失败，跳过本轮推送（不发送回退文案）"
    printf '{"time":"%s","status":"llm_failed","new":%d}\n' "$TS" "$(echo "$RESULT" | grep -v __LLM_FAILED__ | wc -l)" > "$STATUS_FILE"
    exit 0
fi

# 429限流检测：防止把错误文案推送到WhatsApp
if echo "$RESULT" | grep -q "429"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: ⚠️ 检测到429限流，跳过本轮推送。"
    exit 0
fi

printf "💻 HN 头版精选 (%s)\n\n" "$TODAY" > "$MSG_FILE"

# 单次 Python 调用处理全部结果，避免每条数据重复启动 5 个子进程
# 注意：不能用 python3 - <<heredoc <<<data，heredoc 会耗尽 stdin 导致 data 丢失
SENT_COUNT=$(echo "$RESULT" | $PYTHON3 -c '
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
    hn_url   = d.get("hn_url", "").strip()
    if not hn_url:           # Fix3：HN_URL空值保护
        continue
    zh_title = d.get("zh_title") or d.get("title", "")
    point    = d.get("point")    or d.get("title", "")[:40]
    stars    = d.get("stars")    or "⭐⭐⭐"
    title    = d.get("title", zh_title)
    with open(msg_file, "a") as f:
        f.write(f"{zh_title}\n链接：{hn_url}\n要点：{point}\n价值：{stars}\n\n")
    with open(kb_source, "a") as f:
        f.write(f"- **[{title}]({hn_url})** | {today} | 要点：{point} | {stars}\n")
    sent += 1
print(sent)
' "$TODAY" "$MSG_FILE" "$KB_SOURCE")

if [ "$SENT_COUNT" -gt 0 ]; then
    SEND_ERR=$(mktemp)
    if openclaw message send --channel whatsapp --target "$TO" --message "$(cat "$MSG_FILE")" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${SENT_COUNT} 条AI/Tech精选（单次批量LLM）。"
        openclaw message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$(cat "$MSG_FILE")" --json >/dev/null 2>&1 || true
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$SENT_COUNT" > "$STATUS_FILE"
        # 写入 KB notes（与其他 10 个 job 对齐双写模式）
        bash "$KB_WRITE_SCRIPT" "# HN AI/Tech精选 $(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')

$(cat "$MSG_FILE")" "hn-tech" "note" 2>/dev/null || true
    else
        # 过滤已知无害警告（feishu 插件 duplicate id、plugins.allow empty）
        REAL_ERR=$(grep -v -E "feishu|plugin.*duplicate|plugins\.allow|Config warnings" "$SEND_ERR" 2>/dev/null || true)
        if [ -z "$REAL_ERR" ]; then
            # 只有无害警告，实际推送可能成功了
            log "已推送 ${SENT_COUNT} 条AI/Tech精选（单次批量LLM，忽略插件警告）。"
            openclaw message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$(cat "$MSG_FILE")" --json >/dev/null 2>&1 || true
            printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$SENT_COUNT" > "$STATUS_FILE"
            bash "$KB_WRITE_SCRIPT" "# HN AI/Tech精选 $(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')

$(cat "$MSG_FILE")" "hn-tech" "note" 2>/dev/null || true
        else
            log "ERROR: 推送失败（${SENT_COUNT} 条待发）: $(echo "$REAL_ERR" | head -3)"
            printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$SENT_COUNT" > "$STATUS_FILE"
        fi
    fi
else
    log "LLM解析完成但无有效条目。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
fi

rsync -a --quiet "$KB_DIR/" "$SSD_BACKUP" 2>/dev/null || true

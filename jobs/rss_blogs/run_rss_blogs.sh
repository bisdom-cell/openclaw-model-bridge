#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# RSS 博客订阅监控 v1
# 每天 2 次（08:00, 18:00 HKT）由系统 crontab 触发
# 支持多个 RSS 源，按需扩展
set -eo pipefail

# 防重叠执行
LOCK="/tmp/rss_blogs.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[rss] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/rss_blogs"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/rss_blogs.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
PYTHON3=/usr/bin/python3

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] rss_blogs: $1"; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# RSS 博客订阅" > "$KB_SRC"

# ── RSS 源配置（按需添加新博客）──────────────────────────────────────
# 格式：name|feed_url|label
RSS_FEEDS=(
    "科学空间|https://spaces.ac.cn/feed|苏剑林(NLP/深度学习)"
    "Lil'Log|https://lilianweng.github.io/index.xml|Lilian Weng/OpenAI(LLM/Agent综述)"
    "Simon Willison|https://simonwillison.net/atom/everything/|Simon Willison(LLM工具/实践)"
    "Latent Space|https://www.latent.space/feed|Swyx&Alessio(AI工程/Agent架构)"
    "LangChain|https://blog.langchain.dev/feed/|LangChain(Agent/RAG实战)"
)

SEEN_FILE="$CACHE/seen_urls.txt"
touch "$SEEN_FILE"
ALL_NEW_FILE="$CACHE/all_new.jsonl"
> "$ALL_NEW_FILE"

TOTAL_NEW=0

for feed_entry in "${RSS_FEEDS[@]}"; do
    IFS='|' read -r FEED_NAME FEED_URL FEED_LABEL <<< "$feed_entry"
    FEED_FILE="$CACHE/feed_$(echo "$FEED_NAME" | tr ' ' '_').xml"

    # 抓取 RSS
    FETCH_OK=false
    for attempt in 1 2 3; do
        HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
            -H "User-Agent: openclaw-rss-monitor/1.0" \
            -o "$FEED_FILE" \
            "$FEED_URL" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "200" ] && [ -s "$FEED_FILE" ]; then
            FETCH_OK=true
            break
        else
            log "WARN: ${FEED_NAME} RSS HTTP ${HTTP_CODE} (attempt ${attempt})"
        fi
        sleep "$((attempt * 5))"
    done

    if [ "$FETCH_OK" != "true" ]; then
        log "WARN: ${FEED_NAME} RSS 抓取失败，跳过"
        continue
    fi

    # 解析 RSS XML → 提取新文章
    $PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$FEED_NAME" "$FEED_LABEL" << 'PYEOF' >> "$ALL_NEW_FILE"
import sys, json
import xml.etree.ElementTree as ET

feed_file = sys.argv[1]
seen_file = sys.argv[2]
feed_name = sys.argv[3]
feed_label = sys.argv[4]

with open(seen_file) as f:
    seen_urls = set(line.strip() for line in f if line.strip())

try:
    tree = ET.parse(feed_file)
    root = tree.getroot()
except ET.ParseError:
    # 尝试清理常见的 XML 问题
    with open(feed_file, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        print(f"[rss] ERROR: {feed_name} XML解析失败", file=sys.stderr)
        sys.exit(0)

# 支持 RSS 2.0 和 Atom 格式
ns = {'atom': 'http://www.w3.org/2005/Atom',
      'content': 'http://purl.org/rss/1.0/modules/content/',
      'dc': 'http://purl.org/dc/elements/1.1/'}

items = root.findall('.//item')  # RSS 2.0
if not items:
    items = root.findall('.//atom:entry', ns)  # Atom

new_count = 0
for item in items[:20]:  # 最多检查20篇
    # RSS 2.0
    title_el = item.find('title')
    link_el = item.find('link')
    desc_el = item.find('description')
    date_el = item.find('pubDate')
    author_el = item.find('dc:creator', ns)
    content_el = item.find('content:encoded', ns)

    # Atom fallback
    if link_el is None:
        link_el = item.find('atom:link', ns)
        if link_el is not None:
            link_el = type('obj', (object,), {'text': link_el.get('href', '')})()
    if title_el is None:
        title_el = item.find('atom:title', ns)
    if date_el is None:
        date_el = item.find('atom:published', ns) or item.find('atom:updated', ns)

    title = (title_el.text or '').strip() if title_el is not None else ''
    link = (link_el.text or '').strip() if link_el is not None else ''
    description = ''
    if content_el is not None and content_el.text:
        # 去除 HTML 标签，取前500字
        import re
        description = re.sub(r'<[^>]+>', '', content_el.text)[:500]
    elif desc_el is not None and desc_el.text:
        import re
        description = re.sub(r'<[^>]+>', '', desc_el.text)[:500]
    pub_date = (date_el.text or '').strip()[:25] if date_el is not None else ''
    author = (author_el.text or '').strip() if author_el is not None else feed_name

    if not title or not link:
        continue
    if link in seen_urls:
        continue

    print(json.dumps({
        "title": title,
        "link": link,
        "description": description,
        "pub_date": pub_date,
        "author": author,
        "feed_name": feed_name,
        "feed_label": feed_label,
    }, ensure_ascii=False))
    new_count += 1

    if new_count >= 5:  # 每个源每次最多5篇新文章
        break

print(f"[rss] {feed_name}: {new_count} 篇新文章", file=sys.stderr)
PYEOF
done

TOTAL_NEW="$(wc -l < "$ALL_NEW_FILE" | tr -d ' ')"
if [ "$TOTAL_NEW" -eq 0 ]; then
    log "无新文章，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[rss] 共 ${TOTAL_NEW} 篇新文章"

# ── 构建LLM prompt ───────────────────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
$PYTHON3 - "$ALL_NEW_FILE" << 'PYEOF' > "$PROMPT_FILE"
import sys, json

articles = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line:
            articles.append(json.loads(line))

prompt = """你是技术博客编辑。对以下每篇博文严格输出两行（不要输出任何其他内容）：
第一行：要点：[1句话≤60字，说明核心内容]
第二行：价值：⭐（1到5个星，评估对AI从业者的参考价值）
每篇之间用一行 --- 分隔。不要输出序号。

"""
for i, a in enumerate(articles, 1):
    prompt += f"博文{i}：{a['title']}\n"
    if a.get('description'):
        prompt += f"摘要：{a['description'][:300]}\n"
    prompt += "\n"

print(prompt)
PYEOF

# ── 调用LLM ──────────────────────────────────────────────────────────
LLM_RAW="$CACHE/llm_raw_last.txt"
PAYLOAD_FILE="$CACHE/llm_payload.json"
$PYTHON3 -c "
import json
prompt = open('$CACHE/llm_prompt.txt').read()
with open('$CACHE/llm_payload.json', 'w') as f:
    json.dump({
        'model': 'Qwen3-235B-A22B-Instruct-2507-W8A8',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 4096,
        'temperature': 0.3
    }, f)
"

LLM_RESP=$(curl -s --max-time 120 \
    -H "Content-Type: application/json" \
    -d "@$PAYLOAD_FILE" \
    http://127.0.0.1:5002/v1/chat/completions 2>"$LLM_RAW.stderr" || true)

echo "$LLM_RESP" > "$LLM_RAW"

LLM_CONTENT=$(echo "$LLM_RESP" | $PYTHON3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception:
    pass
" 2>/dev/null || true)

if [ -z "${LLM_CONTENT// }" ]; then
    log "WARN: LLM调用失败，使用原始标题推送"
    LLM_CONTENT=""
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"

# ── 组装消息 ──────────────────────────────────────────────────────────
MSG_FILE="$CACHE/rss_message.txt"
$PYTHON3 - "$ALL_NEW_FILE" "$CACHE/llm_content.txt" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re

articles_file, llm_file, day, msg_file = sys.argv[1:5]

articles = []
with open(articles_file) as f:
    for line in f:
        line = line.strip()
        if line:
            articles.append(json.loads(line))

with open(llm_file) as f:
    llm_content = f.read()

# 解析 LLM 输出（要点/价值两行一组）
parsed_blocks = []
pending_highlight = None

for raw_line in llm_content.split('\n'):
    line = raw_line.strip()
    if not line:
        continue
    if re.match(r'^[-=*]{3,}$', line):
        continue
    if re.match(r'^(博文\d+[：:]?\s*$|\d+[.、)]\s*$)', line):
        continue

    if '价值' in line and '⭐' in line:
        for prefix in ['价值：', '价值:', '第二行：', '第2行：']:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        stars_line = '价值：' + line if not line.startswith('价值') else line
        parsed_blocks.append((
            pending_highlight or '',
            stars_line
        ))
        pending_highlight = None
        continue

    if line.startswith('要点：') or line.startswith('要点:'):
        pending_highlight = line
        continue
    for prefix in ['第一行：', '第1行：']:
        if line.startswith(prefix):
            rest = line[len(prefix):].strip()
            pending_highlight = rest if rest.startswith('要点') else '要点：' + rest
            break

msg_lines = [f"\U0001F4D6 博客精选 ({day})", ""]

for i, article in enumerate(articles):
    msg_lines.append(f"*{article['title']}*")
    msg_lines.append(f"来源：{article['feed_label']} | {article.get('pub_date', '')[:16]}")
    msg_lines.append(f"链接：{article['link']}")

    if i < len(parsed_blocks):
        highlight, stars = parsed_blocks[i]
        if highlight:
            msg_lines.append(highlight)
        msg_lines.append(stars)
    else:
        msg_lines.append("要点：技术深度文章")
        msg_lines.append("价值：⭐⭐⭐")

    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

print(f"[rss] 消息组装完成: {len(articles)} 篇", file=sys.stderr)
PYEOF

# ── 推送WhatsApp ─────────────────────────────────────────────────────
MSG_CONTENT="$(head -c 4000 "$MSG_FILE")"
SEND_ERR=$(mktemp)
if "$OPENCLAW" message send --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${TOTAL_NEW} 篇文章"
    # 标记为已发送
    $PYTHON3 -c "
import json, sys
with open('$ALL_NEW_FILE') as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            print(d.get('link', ''))
" >> "$SEEN_FILE"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
else
    log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi

# ── KB归档 ────────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# RSS 博客 ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "rss-blogs" "note" 2>/dev/null || true
    echo "[rss] KB写入完成"
fi

# ── 永久归档 ──────────────────────────────────────────────────────────
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} >> "$KB_SRC"

# ── 清理seen缓存 ─────────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── rsync备份 ─────────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

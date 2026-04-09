#!/usr/bin/env bash
# run_ontology_sources.sh — Ontology 专属信息源监控
# 监控本体论/语义网/知识表示领域的权威 RSS 源
# 推送到 Discord #ontology 频道 + KB 归档
#
# crontab: 0 10,20 * * * bash -lc 'bash ~/.openclaw/jobs/ontology_sources/run_ontology_sources.sh >> ~/.openclaw/logs/jobs/ontology_sources.log 2>&1'
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -eo pipefail

# 防重叠执行
LOCK="/tmp/ontology_sources.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[onto-src] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

JOB_DIR="${HOME}/.openclaw/jobs/ontology_sources"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/ontology_sources.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
PYTHON3=/usr/bin/python3

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] onto-src: $1"; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# Ontology Sources Watcher" > "$KB_SRC"

# ── 加载 notify.sh ────────────────────────────────────────────────────
NOTIFY_LOADED=false
for _np in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$_np" ]; then
        source "$_np"
        NOTIFY_LOADED=true
        break
    fi
done

# ── Ontology RSS 源配置 ───────────────────────────────────────────────
# 格式：name|feed_url|label
# 选择标准：有可用RSS、无Cloudflare反爬、ontology/语义网专属
RSS_FEEDS=(
    "W3C Semantic Web|https://www.w3.org/blog/feed/|W3C(OWL/RDF/SPARQL/SHACL标准动态)"
    "Journal of Web Semantics|https://rss.sciencedirect.com/publication/science/15708268|JWS(语义网研究，Elsevier)"
    "Data and Knowledge Engineering|https://rss.sciencedirect.com/publication/science/0169023X|DKE(Elsevier，数据与知识工程，本体建模/概念建模)"
    "Knowledge-Based Systems|https://rss.sciencedirect.com/publication/science/09507051|KBS(Elsevier，知识系统/知识图谱/推理)"
)

SEEN_FILE="$CACHE/seen_urls.txt"
touch "$SEEN_FILE"
ALL_NEW_FILE="$CACHE/all_new.jsonl"
> "$ALL_NEW_FILE"

TOTAL_NEW=0
FETCH_ERRORS=0

for feed_entry in "${RSS_FEEDS[@]}"; do
    IFS='|' read -r FEED_NAME FEED_URL FEED_LABEL <<< "$feed_entry"
    FEED_FILE="$CACHE/feed_$(echo "$FEED_NAME" | tr ' ' '_').xml"

    # 抓取 RSS
    FETCH_OK=false
    for attempt in 1 2 3; do
        HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
            -H "User-Agent: openclaw-ontology-monitor/1.0" \
            -o "$FEED_FILE" \
            "$FEED_URL" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "200" ] && [ -s "$FEED_FILE" ]; then
            FETCH_OK=true
            break
        else
            log "WARN: ${FEED_NAME} RSS HTTP ${HTTP_CODE} (attempt ${attempt})"
        fi
        sleep "$((attempt * 3))"
    done

    if [ "$FETCH_OK" != "true" ]; then
        log "WARN: ${FEED_NAME} RSS 抓取失败，跳过"
        FETCH_ERRORS=$((FETCH_ERRORS + 1))
        continue
    fi

    # 解析 RSS XML → 提取新文章（带 ontology 关键词过滤）
    $PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$FEED_NAME" "$FEED_LABEL" << 'PYEOF' >> "$ALL_NEW_FILE"
import sys, json, re
import xml.etree.ElementTree as ET

feed_file = sys.argv[1]
seen_file = sys.argv[2]
feed_name = sys.argv[3]
feed_label = sys.argv[4]

# Ontology 核心关键词（强信号，命中一个即通过）
STRONG_KEYWORDS = [
    "ontology", "ontologies", "ontological",
    "semantic web", "linked data",
    "OWL", "RDF", "SPARQL", "SHACL", "SKOS",
    "description logic", "formal ontology",
    "knowledge representation", "knowledge engineering",
    "upper ontology", "BFO", "UFO", "DOLCE",
    "neuro-symbolic", "neurosymbolic",
    "conceptual modeling", "conceptual model",
]
# 弱关键词（需要标题中出现才算，避免摘要中的泛匹配）
TITLE_KEYWORDS = [
    "knowledge graph", "knowledge base",
    "schema.org", "structured data",
    "reasoning", "taxonomy",
]

# JWS/DKE 是领域期刊，只需弱过滤；KBS 范围很广，需要强过滤
SKIP_FILTER = False  # 所有源都过滤

with open(seen_file) as f:
    seen_urls = set(line.strip() for line in f if line.strip())

try:
    tree = ET.parse(feed_file)
    root = tree.getroot()
except ET.ParseError:
    with open(feed_file, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        print(f"[onto-src] ERROR: {feed_name} XML解析失败", file=sys.stderr)
        sys.exit(0)

# 支持 RSS 2.0 和 Atom 格式
ns = {'atom': 'http://www.w3.org/2005/Atom',
      'content': 'http://purl.org/rss/1.0/modules/content/',
      'dc': 'http://purl.org/dc/elements/1.1/'}

items = root.findall('.//item')  # RSS 2.0
if not items:
    items = root.findall('.//atom:entry', ns)  # Atom

new_count = 0
for item in items[:30]:  # 检查前30篇
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
        description = re.sub(r'<[^>]+>', '', content_el.text)[:500]
    elif desc_el is not None and desc_el.text:
        description = re.sub(r'<[^>]+>', '', desc_el.text)[:500]
    pub_date = (date_el.text or '').strip()[:25] if date_el is not None else ''
    author = (author_el.text or '').strip() if author_el is not None else feed_name

    if not title or not link:
        continue
    if link in seen_urls:
        continue

    # 关键词过滤：强关键词查全文，弱关键词只查标题
    full_text = (title + " " + description).lower()
    title_lower = title.lower()
    has_strong = any(kw.lower() in full_text for kw in STRONG_KEYWORDS)
    has_title_kw = any(kw.lower() in title_lower for kw in TITLE_KEYWORDS)
    if not (has_strong or has_title_kw):
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

    if new_count >= 5:  # 每个源每次最多5篇（控制总量，避免LLM超时）
        break

print(f"[onto-src] {feed_name}: {new_count} 篇新文章", file=sys.stderr)
PYEOF
done

TOTAL_NEW="$(wc -l < "$ALL_NEW_FILE" | tr -d ' ')"
if [ "$TOTAL_NEW" -eq 0 ]; then
    log "无新文章，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
log "共 ${TOTAL_NEW} 篇新文章"

# ── 构建 LLM prompt ──────────────────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
$PYTHON3 - "$ALL_NEW_FILE" << 'PYEOF' > "$PROMPT_FILE"
import sys, json

articles = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line:
            articles.append(json.loads(line))

prompt = """你是本体论(Ontology)和语义网(Semantic Web)领域的学术编辑。对以下每篇文章严格输出三行（不要输出任何其他内容）：
第一行：中文标题（翻译或意译，≤25字）
第二行：要点：[1句话≤60字，说明核心贡献或价值]
第三行：价值：⭐（1到5个星，评估对本体论/知识工程从业者的参考价值）
每篇之间用一行 --- 分隔。

"""
for i, a in enumerate(articles, 1):
    prompt += f"文章{i}：{a['title']}\n"
    prompt += f"来源：{a['feed_label']}\n"
    if a.get('description'):
        prompt += f"摘要：{a['description'][:300]}\n"
    prompt += "\n"

print(prompt)
PYEOF

# ── 调用 LLM（直接调 adapter，不经 proxy）──────────────────────────────
LLM_RAW="$CACHE/llm_raw_last.txt"
$PYTHON3 -c "
import json
prompt = open('$CACHE/llm_prompt.txt').read()
with open('$CACHE/llm_payload.json', 'w') as f:
    json.dump({
        'model': 'default',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 4096,
        'temperature': 0.3
    }, f)
"

# 调 proxy（5002）而非 adapter（5001），proxy 有更好的超时和错误处理
LLM_RESP=$(curl -s --max-time 180 \
    -H "Content-Type: application/json" \
    -d "@$CACHE/llm_payload.json" \
    http://127.0.0.1:5002/v1/chat/completions 2>"$CACHE/llm.stderr" || true)

echo "$LLM_RESP" > "$LLM_RAW"

LLM_CONTENT=$($PYTHON3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception:
    pass
" <<< "$LLM_RESP" 2>/dev/null || true)

if [ -z "${LLM_CONTENT// }" ]; then
    log "WARN: LLM调用失败，使用原始标题推送"
    LLM_CONTENT=""
fi

# ── 组装消息 ──────────────────────────────────────────────────────────
MSG_FILE="$CACHE/onto_message.txt"
$PYTHON3 - "$ALL_NEW_FILE" "$LLM_RAW" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re

articles_file, llm_file, day, msg_file = sys.argv[1:5]

articles = []
with open(articles_file) as f:
    for line in f:
        line = line.strip()
        if line:
            articles.append(json.loads(line))

# 尝试解析 LLM 输出
try:
    with open(llm_file) as f:
        raw = json.load(f)
    llm_content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
except Exception:
    llm_content = ""

# 解析三行一组（中文标题/要点/价值）
parsed_blocks = []
lines = [l.strip() for l in llm_content.split('\n') if l.strip() and not re.match(r'^[-=*]{3,}$', l)]

i = 0
while i < len(lines):
    # 跳过"文章N："前缀行
    if re.match(r'^文章\d+[：:]', lines[i]):
        i += 1
        continue
    cn_title = lines[i] if i < len(lines) else ""
    highlight = lines[i+1] if i+1 < len(lines) else ""
    stars = lines[i+2] if i+2 < len(lines) else ""
    parsed_blocks.append((cn_title, highlight, stars))
    i += 3

msg_lines = [f"🔬 Ontology 学术动态 ({day})", ""]

for idx, article in enumerate(articles):
    if idx < len(parsed_blocks):
        cn_title, highlight, stars = parsed_blocks[idx]
        msg_lines.append(f"*{cn_title}*")
    else:
        msg_lines.append(f"*{article['title'][:60]}*")
        highlight = "要点：本体论/语义网领域论文"
        stars = "价值：⭐⭐⭐"

    msg_lines.append(f"来源：{article['feed_label']} | {article.get('pub_date', '')[:16]}")
    msg_lines.append(f"链接：{article['link']}")
    if highlight:
        msg_lines.append(highlight)
    if stars and '⭐' in stars:
        msg_lines.append(stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

print(f"[onto-src] 消息组装完成: {len(articles)} 篇", file=sys.stderr)
PYEOF

# ── 推送到 Discord #ontology（主推通道）+ WhatsApp ───────────────────
MSG_CONTENT="$(head -c 4000 "$MSG_FILE")"

if $NOTIFY_LOADED; then
    notify "$MSG_CONTENT" --topic ontology
    log "已推送 ${TOTAL_NEW} 篇到 #ontology"
else
    log "WARN: notify.sh not loaded, skipping push"
fi

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

# ── KB 归档 ──────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ] && [ -f "$KB_WRITE_SCRIPT" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# Ontology Sources ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "ontology" "note" 2>/dev/null || true
    log "KB写入完成"
fi

# ── 永久归档 ──────────────────────────────────────────────────────────
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} >> "$KB_SRC"

# ── 清理 seen 缓存 ──────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── rsync 备份 ──────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

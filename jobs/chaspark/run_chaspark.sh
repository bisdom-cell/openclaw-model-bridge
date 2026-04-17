#!/bin/bash
# run_chaspark.sh — 黄大年茶思屋(Chaspark)科技网站 公众号文章监控
# 通过搜狗微信搜索抓取最新文章，LLM 分析后推送 + KB 归档
# cron: 每天 11:00 执行一次（频率低避免搜狗验证码）
#
# 数据通路：搜狗微信搜索 → HTML 解析 → 去重 → LLM 分析 → KB + 推送
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true

JOB_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE="$JOB_DIR/cache"
KB_BASE="${KB_BASE:-$HOME/.kb}"
KB_SRC="$KB_BASE/sources/chaspark.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
KB_APPEND_SCRIPT="${KB_APPEND_SCRIPT:-$HOME/kb_append_source.sh}"
PYTHON3="${PYTHON3:-/usr/bin/python3}"
OPENCLAW="${OPENCLAW:-openclaw}"
TO="${OPENCLAW_PHONE:-}"
PROXY_URL="http://127.0.0.1:5002/v1/chat/completions"

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] chaspark: $1" >&2; }

mkdir -p "$CACHE/raw" "$(dirname "$KB_SRC")"
test -f "$KB_SRC" || echo "# 黄大年茶思屋(Chaspark)科技文章" > "$KB_SRC"

# ── 加载 notify.sh ────────────────────────────────────────────────────
NOTIFY_LOADED=false
for _np in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$_np" ]; then
        source "$_np"
        NOTIFY_LOADED=true
        break
    fi
done

# ── 去重文件 ──────────────────────────────────────────────────────────
SEEN_FILE="$CACHE/seen_urls_${DAY}.txt"
touch "$SEEN_FILE"

# ── 1. 搜狗微信搜索抓取文章 ──────────────────────────────────────────
SEARCH_QUERY="黄大年茶思屋科技网站"
ENCODED_QUERY=$(${PYTHON3} -c "import urllib.parse; print(urllib.parse.quote('${SEARCH_QUERY}'))")
RAW_HTML="$CACHE/raw/sogou_${DAY}.html"

log "抓取搜狗微信: $SEARCH_QUERY"
HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
    -o "$RAW_HTML" \
    "https://weixin.sogou.com/weixin?type=2&query=${ENCODED_QUERY}" \
    2>/dev/null) || HTTP_CODE="000"

if [ "$HTTP_CODE" != "200" ] || [ ! -s "$RAW_HTML" ]; then
    log "搜狗抓取失败 (HTTP $HTTP_CODE)"
    printf '{"time":"%s","status":"error","reason":"sogou_fetch_failed","http_code":"%s"}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
    exit 1
fi

# ── 2. 解析 HTML 提取文章 ─────────────────────────────────────────────
ALL_ARTICLES="$CACHE/articles_${DAY}.jsonl"
$PYTHON3 - "$RAW_HTML" "$SEEN_FILE" "$ALL_ARTICLES" << 'PYEOF'
import sys, re, json, html

raw_file, seen_file, out_file = sys.argv[1:4]

with open(raw_file, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()

with open(seen_file, "r") as f:
    seen = set(line.strip() for line in f if line.strip())

# 搜狗微信文章列表解析：提取标题、摘要、来源、链接
articles = []

# 方法1: 提取 <a> 标签中的文章（搜狗微信搜索结果格式）
# 标题在 <a> 里，摘要在 <p class="txt-info"> 或类似结构
title_pattern = re.compile(
    r'<a[^>]*href="([^"]*)"[^>]*target="_blank"[^>]*>\s*(.*?)\s*</a>',
    re.DOTALL
)
# 来源/公众号名
source_pattern = re.compile(r'<a[^>]*class="account"[^>]*>(.*?)</a>', re.DOTALL)
# 摘要
summary_pattern = re.compile(r'<p\s+class="txt-info"[^>]*>(.*?)</p>', re.DOTALL)

# 逐个搜索结果块提取
blocks = re.split(r'<li\s+id="sogou_vr_\d+', content)

for block in blocks[1:]:  # 跳过第一个非结果块
    title_match = title_pattern.search(block)
    if not title_match:
        continue

    url = title_match.group(1)
    title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
    title = html.unescape(title)

    if not title or len(title) < 4:
        continue
    if url in seen:
        continue

    summary = ""
    sm = summary_pattern.search(block)
    if sm:
        summary = re.sub(r'<[^>]+>', '', sm.group(1)).strip()
        summary = html.unescape(summary)

    source_name = ""
    src_m = source_pattern.search(block)
    if src_m:
        source_name = re.sub(r'<[^>]+>', '', src_m.group(1)).strip()

    articles.append({
        "title": title,
        "summary": summary[:300],
        "source": source_name or "茶思屋",
        "url": url
    })
    seen.add(url)

# 写入结果
with open(out_file, "w", encoding="utf-8") as f:
    for a in articles:
        f.write(json.dumps(a, ensure_ascii=False) + "\n")

# 更新 seen 文件
with open(seen_file, "w") as f:
    for u in seen:
        f.write(u + "\n")

print(f"[chaspark] 解析到 {len(articles)} 篇新文章", file=sys.stderr)
PYEOF

ARTICLE_COUNT=$(wc -l < "$ALL_ARTICLES" 2>/dev/null | tr -d ' ')
if [ "${ARTICLE_COUNT:-0}" -eq 0 ]; then
    log "无新文章（可能已全部推送过或搜狗返回验证码页面）"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi

log "解析到 $ARTICLE_COUNT 篇新文章"

# ── 3. LLM 分析 ──────────────────────────────────────────────────────
ARTICLE_LIST=$($PYTHON3 -c "
import json
with open('$ALL_ARTICLES') as f:
    arts = [json.loads(l) for l in f if l.strip()]
for i, a in enumerate(arts[:10], 1):
    print(f\"{i}. 【{a['title']}】\")
    if a.get('summary'):
        print(f\"   {a['summary'][:200]}\")
    print()
")

LLM_PROMPT="以下是华为黄大年茶思屋科技网站的最新文章列表：

${ARTICLE_LIST}

请用中文简要分析：
1. 这些文章涵盖哪些技术方向？
2. 有哪些值得特别关注的前沿话题？（用⭐标注）
3. 对 AI/本体论/智能体领域的从业者有什么启发？

控制在 300 字以内。"

LLM_BODY=$(cat <<JSONEOF
{"model":"auto","messages":[{"role":"user","content":$(echo "$LLM_PROMPT" | $PYTHON3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")}],"max_tokens":1000}
JSONEOF
)

LLM_RESPONSE=$(curl -s --max-time 120 -X POST "$PROXY_URL" \
    -H "Content-Type: application/json" \
    -d "$LLM_BODY" 2>/dev/null)

LLM_ANALYSIS=$($PYTHON3 -c "
import json, sys
try:
    r = json.loads('''$LLM_RESPONSE''')
    print(r['choices'][0]['message']['content'])
except:
    print('(LLM 分析未返回)')
" 2>/dev/null)

# ── 4. KB 归档 ────────────────────────────────────────────────────────
KB_CONTENT="# 茶思屋科技动态 $DAY

## 文章列表
${ARTICLE_LIST}

## AI 分析
${LLM_ANALYSIS}

---
来源: 搜狗微信搜索 → 黄大年茶思屋科技网站公众号
采集时间: ${TS}"

if [ -x "$KB_WRITE_SCRIPT" ] || [ -f "$KB_WRITE_SCRIPT" ]; then
    echo "$KB_CONTENT" | bash "$KB_WRITE_SCRIPT" --title "茶思屋科技动态 $DAY" --tags "chaspark,华为,科技前沿"
    log "KB 写入完成"
fi

# 写入 sources
if [ -f "$KB_APPEND_SCRIPT" ]; then
    SLOT_TAG="11:00"
    echo "$KB_CONTENT" | bash "$KB_APPEND_SCRIPT" "$KB_SRC" "$SLOT_TAG"
fi

# ── 5. 推送 ──────────────────────────────────────────────────────────
WA_MSG="🏠 茶思屋科技动态 ($DAY)

${ARTICLE_LIST}
📊 AI 分析：
${LLM_ANALYSIS}"

if [ "$NOTIFY_LOADED" = true ]; then
    notify "$WA_MSG" --topic daily
    log "推送完成 (WhatsApp + Discord)"
else
    log "notify.sh 未加载，跳过推送"
fi

# ── 6. 状态记录 ───────────────────────────────────────────────────────
printf '{"time":"%s","status":"ok","new":%d}\n' "$TS" "$ARTICLE_COUNT" > "$STATUS_FILE"
log "完成: $ARTICLE_COUNT 篇文章"

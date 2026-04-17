#!/bin/bash
# run_chaspark.sh — 黄大年茶思屋(Chaspark)科技网站内容监控
# 通过 chaspark.com 官方 API 直接抓取首页推荐内容，LLM 分析后推送 + KB 归档
# cron: 每天 11:00 执行
#
# 数据通路：Chaspark API → JSON 解析 → 去重 → LLM 分析 → KB + 推送
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true

JOB_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE="$JOB_DIR/cache"
KB_BASE="${KB_BASE:-$HOME/.kb}"
KB_SRC="$KB_BASE/sources/chaspark.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
KB_APPEND_SCRIPT="${KB_APPEND_SCRIPT:-$HOME/kb_append_source.sh}"
PYTHON3="${PYTHON3:-/usr/bin/python3}"
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

# ── 去重文件（按 contentId 去重，保留 30 天）─────────────────────────
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"

# ── 1. 调用 Chaspark 官方 API ─────────────────────────────────────────
CURL="/usr/bin/curl"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
API_BASE="https://www.chaspark.com/chasiwu/v1"

# 抓取多个 slot：头条 + 通用推荐 + 直播 + 活动
SLOTS="homeBanner1,homeGeneralBanner,homelive,homeActivity"
RAW_JSON="$CACHE/raw/api_${DAY}.json"

log "抓取 Chaspark API: $SLOTS"
HTTP_CODE=$($CURL -sS --max-time 30 -w '%{http_code}' \
    -H "User-Agent: $UA" \
    -o "$RAW_JSON" \
    "${API_BASE}/content/recommend/slot?slot=${SLOTS}&size=20&current=1&lang=zh&_t=$(date +%s)" \
    2>/dev/null) || HTTP_CODE="000"

if [ "$HTTP_CODE" != "200" ] || [ ! -s "$RAW_JSON" ]; then
    log "API 抓取失败 (HTTP $HTTP_CODE)"
    printf '{"time":"%s","status":"error","reason":"api_fetch_failed","http_code":"%s"}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
    exit 1
fi

# ── 2. 解析 JSON 提取文章 ─────────────────────────────────────────────
ALL_ARTICLES="$CACHE/articles_${DAY}.jsonl"
$PYTHON3 - "$RAW_JSON" "$SEEN_FILE" "$ALL_ARTICLES" << 'PYEOF'
import sys, json

raw_file, seen_file, out_file = sys.argv[1:4]

with open(raw_file, "r", encoding="utf-8") as f:
    data = json.load(f)

with open(seen_file, "r") as f:
    seen = set(line.strip() for line in f if line.strip())

if data.get("code") != "0" or not data.get("data"):
    print("[chaspark] API 返回异常或无数据", file=sys.stderr)
    with open(out_file, "w") as f:
        pass
    sys.exit(0)

articles = []
for slot in data["data"]:
    slot_name = slot.get("slot", "")
    slot_title = slot.get("slotTitle", {}).get("zh", slot_name)
    for item in slot.get("contents", []):
        cid = item.get("contentId", "")
        if not cid or cid in seen:
            continue

        # 标题：优先中文自定义标题
        custom = item.get("customTitle", {})
        title = custom.get("zh") or item.get("title", "")
        if not title or len(title) < 2:
            continue

        # 详情链接
        url = item.get("detailUrl") or item.get("customLink") or ""

        # 类型
        col_type = item.get("columnTypeName") or item.get("columnType") or ""

        # 领域标签
        domains = [d.get("domainName", "") for d in item.get("domains", []) if d.get("domainName")]

        articles.append({
            "id": cid,
            "title": title,
            "type": col_type,
            "slot": slot_title,
            "domains": domains,
            "url": url
        })
        seen.add(cid)

# 写入结果
with open(out_file, "w", encoding="utf-8") as f:
    for a in articles:
        f.write(json.dumps(a, ensure_ascii=False) + "\n")

# 更新 seen 文件
with open(seen_file, "w") as f:
    for u in seen:
        f.write(u + "\n")

print(f"[chaspark] 解析到 {len(articles)} 篇新内容", file=sys.stderr)
PYEOF

ARTICLE_COUNT=$(wc -l < "$ALL_ARTICLES" 2>/dev/null | tr -d ' ')
if [ "${ARTICLE_COUNT:-0}" -eq 0 ]; then
    log "无新内容（已全部推送过或 API 返回空）"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi

log "解析到 $ARTICLE_COUNT 篇新内容"

# ── 3. 构建文章列表文本 ──────────────────────────────────────────────
ARTICLE_LIST=$($PYTHON3 -c "
import json
with open('$ALL_ARTICLES') as f:
    arts = [json.loads(l) for l in f if l.strip()]
for i, a in enumerate(arts[:15], 1):
    domains = '，'.join(a.get('domains', [])) or '综合'
    print(f\"{i}. 【{a['title']}】[{a['type']}] ({domains})\")
")

# ── 4. LLM 分析 ──────────────────────────────────────────────────────
LLM_PROMPT="以下是华为黄大年茶思屋科技网站的最新推荐内容：

${ARTICLE_LIST}

请用中文简要分析（300 字以内）：
1. 涵盖哪些技术方向？
2. 值得特别关注的前沿话题？（用⭐标注）
3. 对 AI/本体论/智能体领域有什么启发？"

BODY_FILE="$CACHE/raw/llm_body_${DAY}.json"
$PYTHON3 -c "
import json
prompt = open('/dev/stdin').read()
body = {'model': 'auto', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 1000}
with open('$BODY_FILE', 'w') as f:
    json.dump(body, f, ensure_ascii=False)
" <<< "$LLM_PROMPT"

LLM_RAW="$CACHE/raw/llm_response_${DAY}.txt"
$CURL -s --max-time 120 -X POST "$PROXY_URL" \
    -H "Content-Type: application/json" \
    -d @"$BODY_FILE" > "$LLM_RAW" 2>/dev/null

LLM_ANALYSIS=$($PYTHON3 -c "
import json, sys, re
with open('$LLM_RAW') as f:
    raw = f.read()
# 处理 SSE 格式（proxy 可能返回 SSE 或纯 JSON）
content_parts = []
for line in raw.split('\n'):
    line = line.strip()
    if line.startswith('data: ') and line != 'data: [DONE]':
        try:
            chunk = json.loads(line[6:])
            delta = chunk.get('choices', [{}])[0].get('delta', {})
            if 'content' in delta:
                content_parts.append(delta['content'])
        except:
            pass
if content_parts:
    print(''.join(content_parts))
else:
    # 尝试纯 JSON 格式
    try:
        r = json.loads(raw)
        print(r['choices'][0]['message']['content'])
    except:
        print('(LLM 分析未返回)')
" 2>/dev/null)

# ── 5. KB 归档 ────────────────────────────────────────────────────────
KB_CONTENT="# 茶思屋科技动态 $DAY

## 内容列表
${ARTICLE_LIST}

## AI 分析
${LLM_ANALYSIS}

---
来源: Chaspark API (www.chaspark.com)
采集时间: ${TS}"

if [ -x "$KB_WRITE_SCRIPT" ] || [ -f "$KB_WRITE_SCRIPT" ]; then
    echo "$KB_CONTENT" | bash "$KB_WRITE_SCRIPT" --title "茶思屋科技动态 $DAY" --tags "chaspark,华为,科技前沿"
    log "KB 写入完成"
fi

if [ -f "$KB_APPEND_SCRIPT" ]; then
    SLOT_TAG="11:00"
    echo "$KB_CONTENT" | bash "$KB_APPEND_SCRIPT" "$KB_SRC" "$SLOT_TAG"
fi

# ── 6. 推送 ──────────────────────────────────────────────────────────
WA_MSG="🏠 茶思屋科技动态 ($DAY)

${ARTICLE_LIST}

📊 ${LLM_ANALYSIS}"

if [ "$NOTIFY_LOADED" = true ]; then
    notify "$WA_MSG" --topic daily
    log "推送完成 (WhatsApp + Discord)"
else
    log "notify.sh 未加载，跳过推送"
fi

# ── 7. 状态记录 ───────────────────────────────────────────────────────
printf '{"time":"%s","status":"ok","new":%d}\n' "$TS" "$ARTICLE_COUNT" > "$STATUS_FILE"
log "完成: $ARTICLE_COUNT 篇内容"

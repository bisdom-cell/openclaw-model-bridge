#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Andrej Karpathy X/Twitter 技术分享追踪 v1
# 每天 2 次（09:00, 21:00 HKT）由系统 crontab 触发
# 数据源：Twitter Syndication API（无需认证，用于 embed widget）
# 分析深度：LLM 深度技术分析 + 与我们系统演进的关联评估
set -eo pipefail

# 防重叠执行
LOCK="/tmp/karpathy_x.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[karpathy_x] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/karpathy_x"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/karpathy_x.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
PYTHON3=/usr/bin/python3

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] karpathy_x: $1" >&2; }

mkdir -p "$CACHE/raw" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# Andrej Karpathy X 技术分享" > "$KB_SRC"

SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"

# ── 1. 抓取 Karpathy 的推文（Twitter Syndication API）───────────────
RAW_HTML="$CACHE/raw/timeline.html"
FETCH_OK=false

for attempt in 1 2 3; do
    HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
        -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
        -o "$RAW_HTML" \
        "https://syndication.twitter.com/srv/timeline-profile/screen-name/karpathy" \
        2>"$CACHE/raw/curl.err") || HTTP_CODE="000"

    if [ "$HTTP_CODE" = "200" ] && [ -s "$RAW_HTML" ]; then
        FETCH_OK=true
        break
    else
        log "WARN: Syndication API HTTP ${HTTP_CODE} (attempt ${attempt})"
    fi
    sleep "$((attempt * 10))"
done

# Fallback: xcancel.com RSS
if [ "$FETCH_OK" != "true" ]; then
    log "INFO: Syndication failed, trying xcancel.com RSS fallback"
    RAW_RSS="$CACHE/raw/xcancel.xml"
    for attempt in 1 2; do
        HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
            -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
            -o "$RAW_RSS" \
            "https://xcancel.com/karpathy/rss" \
            2>"$CACHE/raw/curl_rss.err") || HTTP_CODE="000"
        if [ "$HTTP_CODE" = "200" ] && [ -s "$RAW_RSS" ]; then
            FETCH_OK=true
            break
        fi
        sleep "$((attempt * 10))"
    done
fi

if [ "$FETCH_OK" != "true" ]; then
    log "ERROR: 所有数据源抓取失败"
    printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 1
fi

# ── 2. 解析推文 → JSONL ─────────────────────────────────────────────
TWEETS_FILE="$CACHE/tweets.jsonl"
$PYTHON3 - "$CACHE/raw" "$SEEN_FILE" "$TWEETS_FILE" << 'PYEOF'
import sys, json, re, os, hashlib
from html import unescape

raw_dir, seen_file, output_file = sys.argv[1:4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

tweets = []

# ── 方式 A：解析 Syndication HTML（__NEXT_DATA__ JSON）──
html_file = os.path.join(raw_dir, "timeline.html")
if os.path.exists(html_file) and os.path.getsize(html_file) > 500:
    with open(html_file, encoding="utf-8", errors="replace") as f:
        html = f.read()

    # Twitter Syndication API 使用 Next.js，数据在 <script id="__NEXT_DATA__"> 中
    # 结构：props.pageProps.timeline.entries[].content.tweet
    next_data_match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
        html, re.DOTALL)
    if next_data_match:
        try:
            data = json.loads(next_data_match.group(1))
            entries = (data.get("props", {})
                          .get("pageProps", {})
                          .get("timeline", {})
                          .get("entries", []))
            for entry in entries:
                if entry.get("type") != "tweet":
                    continue
                tweet_data = entry.get("content", {}).get("tweet", {})
                if not tweet_data:
                    continue

                # 提取推文文本（支持 full_text 和 text 字段）
                text = tweet_data.get("full_text", tweet_data.get("text", ""))

                # 提取 tweet ID
                tweet_id = str(tweet_data.get("id_str",
                               entry.get("entry_id", "").replace("tweet-", "")))

                # 提取日期
                created_at = tweet_data.get("created_at", "")

                # 提取用户信息（确认是 Karpathy 本人而非转推引用）
                user = tweet_data.get("user", {})
                screen_name = user.get("screen_name", "").lower()

                # 构建推文链接
                link = ""
                if tweet_id and tweet_id.isdigit():
                    link = f"https://x.com/karpathy/status/{tweet_id}"

                if text and len(text) > 10:
                    tweets.append({
                        "id": tweet_id,
                        "text": unescape(text),
                        "date": created_at,
                        "link": link,
                        "is_retweet": screen_name not in ("karpathy", ""),
                        "source": "syndication_json"
                    })
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[karpathy_x] WARN: __NEXT_DATA__ 解析异常: {e}", file=sys.stderr)

    # Fallback: 正则提取推文文本（HTML 结构变化时的兜底）
    if not tweets:
        tweet_blocks = re.findall(
            r'data-tweet-id=["\'](\d+)["\'].*?<p[^>]*>(.*?)</p>',
            html, re.DOTALL | re.IGNORECASE)
        for tid, raw_text in tweet_blocks:
            text = re.sub(r'<[^>]+>', '', raw_text).strip()
            text = unescape(text)
            if text and len(text) > 20:
                tweets.append({"id": str(tid), "text": text,
                               "date": "", "link": f"https://x.com/karpathy/status/{tid}",
                               "is_retweet": False, "source": "syndication_html"})

# ── 方式 B：解析 xcancel RSS（fallback）──
rss_file = os.path.join(raw_dir, "xcancel.xml")
if not tweets and os.path.exists(rss_file) and os.path.getsize(rss_file) > 100:
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(rss_file)
        root = tree.getroot()
        for item in root.findall('.//item')[:30]:
            title_el = item.find('title')
            link_el = item.find('link')
            desc_el = item.find('description')
            pub_date_el = item.find('pubDate')

            text = ""
            if desc_el is not None and desc_el.text:
                text = re.sub(r'<[^>]+>', '', desc_el.text).strip()
            elif title_el is not None and title_el.text:
                text = title_el.text.strip()

            link = (link_el.text or "").strip() if link_el is not None else ""
            # 从链接提取 tweet ID
            tid_match = re.search(r'/status/(\d+)', link)
            tid = tid_match.group(1) if tid_match else hashlib.md5(text.encode()).hexdigest()[:16]
            pub_date = (pub_date_el.text or "").strip()[:25] if pub_date_el is not None else ""

            if text and len(text) > 20:
                tweets.append({"id": tid, "text": unescape(text),
                               "date": pub_date, "link": link,
                               "source": "xcancel_rss"})
    except ET.ParseError:
        print("[karpathy_x] ERROR: xcancel RSS XML 解析失败", file=sys.stderr)

# ── 去重 + 过滤 ──
new_tweets = []
for t in tweets:
    if t["id"] in seen_ids:
        continue
    # 过滤纯转发和过短内容
    if t.get("is_retweet") or t["text"].startswith("RT @") or len(t["text"]) < 30:
        continue
    new_tweets.append(t)
    if len(new_tweets) >= 15:  # 每次最多处理 15 条
        break

with open(output_file, 'w') as f:
    for t in new_tweets:
        f.write(json.dumps(t, ensure_ascii=False) + '\n')

print(f"[karpathy_x] 解析: {len(tweets)} 条推文, {len(new_tweets)} 条新推文", file=sys.stderr)
PYEOF

TOTAL_NEW="$(wc -l < "$TWEETS_FILE" | tr -d ' ')"
if [ "$TOTAL_NEW" -eq 0 ]; then
    log "无新推文，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
log "发现 ${TOTAL_NEW} 条新推文"

# ── 3. 构建 LLM 深度分析 Prompt ─────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
$PYTHON3 - "$TWEETS_FILE" << 'PYEOF' > "$PROMPT_FILE"
import sys, json

tweets = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line:
            tweets.append(json.loads(line))

prompt = """你是一位资深AI系统架构师。以下是 Andrej Karpathy（前OpenAI/Tesla AI负责人）最新发布的推文。
请对每条推文进行深度技术分析，严格按以下格式输出（不要输出任何其他内容）：

每条推文输出5行：
第1行：主题：[用≤15字概括核心主题]
第2行：深度分析：[100-200字技术深度解读，包含：核心观点是什么、技术背景、为什么重要]
第3行：系统启示：[50-100字，对Agent Runtime/Control Plane/Memory系统的具体启示]
第4行：行动建议：[1句话≤40字，我们可以做什么]
第5行：价值：⭐（1到5个星，评估对AI系统架构的参考价值）
每条之间用一行 --- 分隔。

"""
for i, t in enumerate(tweets, 1):
    prompt += f"推文{i}：\n{t['text']}\n\n"

print(prompt)
PYEOF

# ── 4. 调用 LLM ─────────────────────────────────────────────────────
LLM_RAW="$CACHE/llm_raw_last.txt"
$PYTHON3 -c "
import json
prompt = open('$CACHE/llm_prompt.txt').read()
with open('$CACHE/llm_payload.json', 'w') as f:
    json.dump({
        'model': 'Qwen3-235B-A22B-Instruct-2507-W8A8',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 8192,
        'temperature': 0.3
    }, f)
"

LLM_RESP=$(curl -s --max-time 180 \
    -H "Content-Type: application/json" \
    -d "@$CACHE/llm_payload.json" \
    http://127.0.0.1:5002/v1/chat/completions 2>"$LLM_RAW.stderr" || true)

echo "$LLM_RESP" > "$LLM_RAW"

LLM_CONTENT=$($PYTHON3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d['choices'][0]['message']['content'])
except Exception:
    pass
" <<< "$LLM_RESP" 2>/dev/null || true)

if [ -z "${LLM_CONTENT// }" ]; then
    log "WARN: LLM调用失败，使用原始推文推送"
    LLM_CONTENT=""
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"

# ── 5. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/karpathy_message.txt"
$PYTHON3 - "$TWEETS_FILE" "$CACHE/llm_content.txt" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re

tweets_file, llm_file, day, msg_file = sys.argv[1:5]

tweets = []
with open(tweets_file) as f:
    for line in f:
        line = line.strip()
        if line:
            tweets.append(json.loads(line))

with open(llm_file) as f:
    llm_content = f.read()

# 解析 LLM 输出（5行一组，--- 分隔）
analyses = []
current = {}
for raw_line in llm_content.split('\n'):
    line = raw_line.strip()
    if not line:
        continue
    if re.match(r'^[-=]{3,}$', line):
        if current:
            analyses.append(current)
            current = {}
        continue
    for key in ['主题', '深度分析', '系统启示', '行动建议', '价值']:
        prefix = key + '：'
        alt_prefix = key + ':'
        if line.startswith(prefix):
            current[key] = line[len(prefix):].strip()
            break
        elif line.startswith(alt_prefix):
            current[key] = line[len(alt_prefix):].strip()
            break
if current:
    analyses.append(current)

msg_lines = [f"\U0001F9E0 Karpathy 技术洞察 ({day})", ""]

for i, tweet in enumerate(tweets):
    # 推文原文（截取前200字）
    text_preview = tweet['text'][:200]
    if len(tweet['text']) > 200:
        text_preview += '...'
    msg_lines.append(f"━━━ 推文 {i+1} ━━━")
    msg_lines.append(f"_{text_preview}_")

    link = tweet.get('link', '')
    if not link and tweet.get('id', '').isdigit():
        link = f"https://x.com/karpathy/status/{tweet['id']}"
    if link:
        msg_lines.append(f"链接：{link}")
    msg_lines.append("")

    if i < len(analyses):
        a = analyses[i]
        if a.get('主题'):
            msg_lines.append(f"*{a['主题']}*")
        if a.get('深度分析'):
            msg_lines.append(f"分析：{a['深度分析']}")
        if a.get('系统启示'):
            msg_lines.append(f"启示：{a['系统启示']}")
        if a.get('行动建议'):
            msg_lines.append(f"行动：{a['行动建议']}")
        if a.get('价值'):
            msg_lines.append(f"价值：{a['价值']}")
    else:
        msg_lines.append("*技术分享*")
        msg_lines.append("价值：⭐⭐⭐")

    msg_lines.append("")

msg_lines.append("来源：@karpathy on X | 深度分析 by Qwen3-235B")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

print(f"[karpathy_x] 消息组装完成: {len(tweets)} 条推文, {len(analyses)} 条分析", file=sys.stderr)
PYEOF

# ── 6. 推送（WhatsApp + Discord #技术）──────────────────────────────
MSG_CONTENT="$(head -c 4000 "$MSG_FILE")"
SEND_ERR=$(mktemp)
if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${TOTAL_NEW} 条 Karpathy 推文分析"
    "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    # 标记为已发送（推送成功后才标记）
    $PYTHON3 -c "
import json, sys
with open('$TWEETS_FILE') as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            print(d.get('id', ''))
" >> "$SEEN_FILE"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
else
    log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi
rm -f "$SEND_ERR"

# ── 7. KB 深度归档 ──────────────────────────────────────────────────
FULL_ANALYSIS="$(cat "$MSG_FILE")"
if [ -n "$FULL_ANALYSIS" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# Karpathy X 技术分享 ${DATE_KB}

${FULL_ANALYSIS}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "karpathy-insights" "note" 2>/dev/null || true
    log "KB写入完成"
fi

# ── 8. 永久归档（源文件） ────────────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 9. 清理 seen 缓存 ───────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── 10. rsync 备份 ──────────────────────────────────────────────────
rsync -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>&1 || { _rc=$?; echo "[$(basename "$0")] WARN: SSD rsync failed (exit=$_rc)" >&2; "$HOME/movespeed_incident_capture.sh" "$_rc" "$0"; }  # V37.9.14 incident forensics + V37.9.4 MR-4 silent-failure 修复
log "完成"

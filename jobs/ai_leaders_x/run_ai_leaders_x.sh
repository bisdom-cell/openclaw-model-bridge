#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# AI Leaders X/Twitter 技术洞察追踪 v2
# 每天 2 次（09:00, 21:00 HKT）由系统 crontab 触发
# 追踪 9 位 AI 技术领袖的 X 动态，深度分析并归档到 KB
# 数据源：Twitter Syndication API（无需认证，用于 embed widget）
set -eo pipefail

# 防重叠执行
LOCK="/tmp/ai_leaders_x.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[ai_leaders] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/ai_leaders_x"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/ai_leaders_x.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
PYTHON3=/usr/bin/python3

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

JOB_TAG="ai_leaders"
log() { echo "[$TS] ${JOB_TAG}: $1"; }

mkdir -p "$CACHE/raw" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# AI Leaders X 技术洞察" > "$KB_SRC"

SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"

# ── 追踪列表（handle|显示名|身份标签）──────────────────────────────────
# 每个 handle 独立抓取，每人最多取 5 条新推文，总计上限 20 条送 LLM
LEADERS=(
    "karpathy|Andrej Karpathy|前OpenAI/Tesla AI负责人，AI教育家"
    "DrJimFan|Jim Fan|NVIDIA高级研究员，Foundation Agents/具身智能"
    "ylecun|Yann LeCun|Meta首席AI科学家，深度学习先驱"
    "fchollet|François Chollet|Keras创建者，ARC Benchmark，智能评测"
    "swyx|Swyx|Latent Space主播，AI Engineering实践"
    "lilianweng|Lilian Weng|OpenAI，LLM/Agent/RAG综述专家"
    "_jasonwei|Jason Wei|OpenAI，Chain-of-Thought推理研究"
    "hwchung27|Hyung Won Chung|OpenAI，Scaling Laws/训练洞察"
    "hwchase17|Harrison Chase|LangChain创建者，Agent编排/工具链"
    "GaryMarcus|Gary Marcus|Neuro-Symbolic AI倡导者，Rebooting AI作者"
    "juaborges|Jure Leskovec|Stanford，图神经网络/知识图谱推理"
    "Michael_Witbrock|Michael Witbrock|Cycorp/Cyc知识库，常识推理先驱"
)

MAX_PER_PERSON=5
MAX_TOTAL=25
ALL_TWEETS="$CACHE/all_tweets.jsonl"
> "$ALL_TWEETS"
FETCH_STATS=""

# ── 1. 逐账号抓取推文 ──────────────────────────────────────────────────
for leader_entry in "${LEADERS[@]}"; do
    IFS='|' read -r HANDLE DISPLAY_NAME LABEL <<< "$leader_entry"
    RAW_HTML="$CACHE/raw/${HANDLE}.html"

    # 抓取 Syndication API
    FETCH_OK=false
    for attempt in 1 2; do
        HTTP_CODE=$(curl -sSL --max-time 20 -w '%{http_code}' \
            -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
            -o "$RAW_HTML" \
            "https://syndication.twitter.com/srv/timeline-profile/screen-name/${HANDLE}" \
            2>"$CACHE/raw/curl_${HANDLE}.err") || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "200" ] && [ -s "$RAW_HTML" ]; then
            FETCH_OK=true
            break
        fi
        sleep "$((attempt * 5))"
    done

    if [ "$FETCH_OK" != "true" ]; then
        log "WARN: ${HANDLE} 抓取失败 (HTTP ${HTTP_CODE})"
        FETCH_STATS="${FETCH_STATS}${HANDLE}:fail "
        continue
    fi

    # 解析推文
    $PYTHON3 - "$RAW_HTML" "$SEEN_FILE" "$HANDLE" "$DISPLAY_NAME" "$LABEL" "$MAX_PER_PERSON" << 'PYEOF' >> "$ALL_TWEETS"
import sys, json, re
from html import unescape
from datetime import datetime, timedelta, timezone

html_file, seen_file, handle, display_name, label, max_per = sys.argv[1:7]
max_per = int(max_per)

# 只保留 14 天内的推文
CUTOFF = datetime.now(timezone.utc) - timedelta(days=14)

def parse_twitter_date(s):
    """解析 Twitter 的日期格式：'Wed Oct 10 20:19:24 +0000 2018'"""
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        return None

def is_link_only(text):
    """检测是否为纯链接推文（无实质内容）"""
    clean = re.sub(r'https?://\S+', '', text).strip()
    return len(clean) < 15

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

with open(html_file, encoding="utf-8", errors="replace") as f:
    html = f.read()

tweets = []

# 解析 __NEXT_DATA__ JSON
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

            text = tweet_data.get("full_text", tweet_data.get("text", ""))
            tweet_id = str(tweet_data.get("id_str",
                           entry.get("entry_id", "").replace("tweet-", "")))

            # 跳过转推
            user = tweet_data.get("user", {})
            screen_name = user.get("screen_name", "").lower()
            if screen_name and screen_name != handle.lower():
                continue

            link = f"https://x.com/{handle}/status/{tweet_id}" if tweet_id.isdigit() else ""
            created_at = tweet_data.get("created_at", "")

            if text and len(text) > 20 and tweet_id not in seen_ids:
                # 跳过纯 RT
                if text.startswith("RT @"):
                    continue
                # 跳过纯链接推文
                if is_link_only(text):
                    continue
                # 跳过超过 14 天的旧推文
                tweet_date = parse_twitter_date(created_at)
                if tweet_date and tweet_date < CUTOFF:
                    continue
                tweets.append({
                    "id": tweet_id,
                    "text": unescape(text),
                    "date": created_at,
                    "link": link,
                    "handle": handle,
                    "author": display_name,
                    "label": label,
                    "source": "syndication"
                })
                if len(tweets) >= max_per:
                    break
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"[ai_leaders] WARN: {handle} 解析异常: {e}", file=sys.stderr)

for t in tweets:
    print(json.dumps(t, ensure_ascii=False))

print(f"[ai_leaders] {handle}: {len(tweets)} 条新推文", file=sys.stderr)
PYEOF

    FETCH_STATS="${FETCH_STATS}${HANDLE}:ok "
    # 请求间隔，避免被限速
    sleep 3
done

TOTAL_NEW="$(wc -l < "$ALL_TWEETS" | tr -d ' ')"
log "抓取完成 (${FETCH_STATS}), 共 ${TOTAL_NEW} 条新推文"

if [ "$TOTAL_NEW" -eq 0 ]; then
    log "无新推文，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0,"stats":"%s"}\n' "$TS" "$FETCH_STATS" > "$STATUS_FILE"
    exit 0
fi

# 截取上限
if [ "$TOTAL_NEW" -gt "$MAX_TOTAL" ]; then
    head -"$MAX_TOTAL" "$ALL_TWEETS" > "$ALL_TWEETS.tmp" && mv "$ALL_TWEETS.tmp" "$ALL_TWEETS"
    TOTAL_NEW="$MAX_TOTAL"
    log "截取前 ${MAX_TOTAL} 条推文送 LLM 分析"
fi

# ── 2. 构建 LLM 深度分析 Prompt ─────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
$PYTHON3 - "$ALL_TWEETS" << 'PYEOF' > "$PROMPT_FILE"
import sys, json

tweets = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line:
            tweets.append(json.loads(line))

prompt = """你是一位资深AI系统架构师。以下是多位AI技术领袖在X平台的最新推文。
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
    prompt += f"推文{i}（{t['author']}，{t['label']}）：\n{t['text']}\n\n"

print(prompt)
PYEOF

# ── 3. 调用 LLM ─────────────────────────────────────────────────────
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

LLM_RESP=$(curl -s --max-time 300 \
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

# ── 4. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/ai_leaders_message.txt"
$PYTHON3 - "$ALL_TWEETS" "$CACHE/llm_content.txt" "$DAY" "$MSG_FILE" << 'PYEOF'
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

# 按作者分组统计
author_counts = {}
for t in tweets:
    author_counts[t['author']] = author_counts.get(t['author'], 0) + 1
authors_summary = ', '.join(f"{k}({v})" for k, v in author_counts.items())

msg_lines = [f"\U0001F9E0 AI Leaders 技术洞察 ({day})",
             f"来源：{authors_summary}", ""]

for i, tweet in enumerate(tweets):
    text_preview = tweet['text'][:200]
    if len(tweet['text']) > 200:
        text_preview += '...'
    msg_lines.append(f"━━━ [{tweet['author']}] ━━━")
    msg_lines.append(f"_{text_preview}_")

    link = tweet.get('link', '')
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

msg_lines.append("深度分析 by Qwen3-235B")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

print(f"[ai_leaders] 消息组装: {len(tweets)} 条推文, {len(analyses)} 条分析", file=sys.stderr)
PYEOF

# ── 5. 推送（WhatsApp + Discord #技术）──────────────────────────────
# WhatsApp 单消息限 4000 字，超长时分段发送
MSG_FULL="$(cat "$MSG_FILE")"
MSG_LEN=${#MSG_FULL}

send_msg() {
    local content="$1"
    "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$content" --json >/dev/null 2>&1
}

SEND_ERR=$(mktemp)
if [ "$MSG_LEN" -le 4000 ]; then
    # 单次发送
    if send_msg "$MSG_FULL" 2>"$SEND_ERR"; then
        SEND_OK=true
    else
        SEND_OK=false
    fi
else
    # 分段发送（按 3500 字切分，留 buffer）
    SEND_OK=true
    PART=1
    REMAINING="$MSG_FULL"
    while [ -n "$REMAINING" ]; do
        CHUNK="$(echo "$REMAINING" | head -c 3500)"
        REMAINING="$(echo "$REMAINING" | tail -c +3501)"
        if [ -n "$REMAINING" ]; then
            CHUNK="${CHUNK}

... (续 ${PART}) ..."
        fi
        if ! send_msg "$CHUNK" 2>>"$SEND_ERR"; then
            SEND_OK=false
            break
        fi
        PART=$((PART + 1))
        sleep 2
    done
fi

if [ "$SEND_OK" = true ]; then
    log "已推送 ${TOTAL_NEW} 条 AI Leaders 推文分析"
    # Discord 只发前 4000 字
    "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_TECH:-}" \
        --message "$(echo "$MSG_FULL" | head -c 4000)" --json >/dev/null 2>&1 || true
    # 标记已发送
    $PYTHON3 -c "
import json, sys
with open('$ALL_TWEETS') as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            print(d.get('id', ''))
" >> "$SEEN_FILE"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true,"stats":"%s"}\n' \
        "$TS" "$TOTAL_NEW" "$FETCH_STATS" > "$STATUS_FILE"
else
    log "ERROR: 推送失败: $(head -3 "$SEND_ERR")"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' \
        "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi
rm -f "$SEND_ERR"

# ── 6. KB 深度归档 ──────────────────────────────────────────────────
FULL_ANALYSIS="$(cat "$MSG_FILE")"
if [ -n "$FULL_ANALYSIS" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# AI Leaders X 技术洞察 ${DATE_KB}

${FULL_ANALYSIS}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "ai-leaders-insights" "note" 2>/dev/null || true
    log "KB写入完成"
fi

# ── 7. 永久归档 ─────────────────────────────────────────────────────
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} >> "$KB_SRC"

# ── 8. 清理 seen 缓存 ───────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 2000 ]; then
    tail -1000 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── 9. rsync 备份 ──────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

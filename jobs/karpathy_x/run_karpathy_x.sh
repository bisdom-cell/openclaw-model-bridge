#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Andrej Karpathy X/Twitter 技术分享追踪 v2 (V37.9.62 — 6 字段 + rule_check 升级,
# 机械迁移 V37.9.51 ai_leaders_x 同款模板 + V37.9.45 hf_papers / V37.9.50 semantic_scholar
# 同款 Opportunity Radar #2, Sub-Stage 4b 2/6)
# 每天 2 次（09:00, 21:00 HKT）由系统 crontab 触发
# 数据源：Twitter Syndication API (主) + xcancel RSS (fallback)
# 分析深度：6 字段 LLM 深度技术分析 + OpenClaw 项目对齐评分 + rule_check 验证
set -eo pipefail

# V37.9.62: 公共反幻觉守卫 LEVEL_4_PROJECT_AWARE (MR-8 single-source-of-truth)
# V37.9.57 LEVEL_4 含 V37.9.56-hotfix3 具体血案字眼 (禁"OpenClaw 社区发布"/"v26"/"[openclaw]")
# 防 alignment 评分输出"一句话原因"段编造项目动态. FAIL-OPEN: 模块缺失 → 空字符串
HG_LEVEL_4_TEXT=$(python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~'))
sys.path.insert(0, '$(cd "$(dirname "$0")" && pwd)')
try:
    import hallucination_guards as hg
    print(hg.get_guard('LEVEL_4_PROJECT_AWARE'))
except Exception:
    print('')
" 2>/dev/null)
export HG_LEVEL_4_TEXT

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

JOB_TAG="karpathy_x"
log() { echo "[$TS] ${JOB_TAG}: $1" >&2; }

# V37.9.62: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (机械迁移 V37.9.40 ai_leaders_x send_alert 模式)
NOTIFY_SH=""
for candidate in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$candidate" ]; then
        NOTIFY_SH="$candidate"
        break
    fi
done
if [ -n "$NOTIFY_SH" ]; then
    # shellcheck disable=SC1090
    source "$NOTIFY_SH" || true
fi

# V37.9.62: fail-fast alert helper
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] karpathy_x LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 Karpathy 技术洞察 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

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
                        "author": "Andrej Karpathy",
                        "label": "前OpenAI/Tesla AI负责人，AI教育家",
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
                               "author": "Andrej Karpathy",
                               "label": "前OpenAI/Tesla AI负责人，AI教育家",
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
                               "author": "Andrej Karpathy",
                               "label": "前OpenAI/Tesla AI负责人，AI教育家",
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

# ── V37.9.62: 每条推文独立调 LLM (6 字段深度分析 + retry 3 次 + Opportunity Radar #2) ──
# 老 V37.8: 单次调用全部 N 条 + 5 行格式 (主题/深度分析/系统启示/行动建议/价值) + 失败 silent fallback
# 新 V37.9.62: 每条独立调用 + 独立 retry (5s/10s/20s) + 6 字段 emoji 格式 (与 ai_leaders_x V37.9.51 统一)
#   📌 中文主题 / 🔑 核心观点 / 💡 技术深度解读 / 🎯 系统启示 / ⭐ 评级 / 🎚️ 项目对齐度
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败篇标 [LLM_DEGRADED] + 推文原文兜底

LLM_RAW="$CACHE/llm_raw_last.txt"
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单条 LLM 调用 + retry ───────────────────────────────────
call_llm_single_with_retry() {
    local prompt_file="$1"
    local idx="$2"
    LAST_LLM_FAIL_REASON=""
    local backoffs=(5 10 20)

    for attempt in 0 1 2; do
        local payload_file="$CACHE/llm_payload_${idx}_a${attempt}.json"
        $PYTHON3 -c "
import json
prompt = open('$prompt_file', encoding='utf-8').read()
with open('$payload_file', 'w', encoding='utf-8') as f:
    json.dump({
        'model': 'Qwen3-235B-A22B-Instruct-2507-W8A8',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 2500,
        'temperature': 0.3
    }, f)
"
        local llm_resp
        llm_resp=$(curl -s --max-time 90 \
            -H "Content-Type: application/json" \
            -d "@$payload_file" \
            http://127.0.0.1:5002/v1/chat/completions 2>/dev/null || true)

        echo "$llm_resp" > "$LLM_RAW"

        # V37.9.36 三层检测
        local parse_err_file="$CACHE/llm_parse_${idx}_a${attempt}.err"
        local parse_out
        parse_out=$(echo "$llm_resp" | $PYTHON3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'__LLM_PARSE_FAIL__:bad_json:{type(e).__name__}', file=sys.stderr)
    sys.exit(0)
if isinstance(d, dict) and 'error' in d:
    err_msg = str(d['error'])[:300].replace(chr(10), ' ')
    print(f'__LLM_HTTP_ERROR__:{err_msg}', file=sys.stderr)
    sys.exit(0)
try:
    content = d['choices'][0]['message']['content']
except (KeyError, IndexError, TypeError) as e:
    print(f'__LLM_PARSE_FAIL__:no_choices:{type(e).__name__}', file=sys.stderr)
    sys.exit(0)
print(content)
" 2>"$parse_err_file" || true)

        local parse_err
        parse_err="$(cat "$parse_err_file" 2>/dev/null || true)"

        if echo "$parse_err" | grep -q '__LLM_HTTP_ERROR__\|__LLM_PARSE_FAIL__'; then
            LAST_LLM_FAIL_REASON=$(echo "$parse_err" | head -c 200 | LC_ALL=C tr '\n' ' ')
            log "WARN: 条 $idx attempt $((attempt+1))/3: $LAST_LLM_FAIL_REASON"
            if [ $attempt -lt 2 ]; then sleep "${backoffs[$attempt]}"; fi
            continue
        fi

        if [ -z "${parse_out// }" ]; then
            LAST_LLM_FAIL_REASON="empty_content"
            log "WARN: 条 $idx attempt $((attempt+1))/3: empty content"
            if [ $attempt -lt 2 ]; then sleep "${backoffs[$attempt]}"; fi
            continue
        fi

        echo "$parse_out"
        return 0
    done
    return 1
}

# ── 主循环: 每条推文独立调 LLM (6 字段深度) ──────────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$TWEETS_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # V37.9.62: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT (V37.9.58-hotfix os import 同款)

tweets_file, idx = sys.argv[1], int(sys.argv[2])
tweets = []
with open(tweets_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            tweets.append(json.loads(line))
t = tweets[idx]
text = t['text']
author = t.get('author', 'Andrej Karpathy')
label = t.get('label', '前OpenAI/Tesla AI负责人，AI教育家')

prompt = """你是资深 AI 系统架构师 (兼 OpenClaw 项目对齐评估师)。对以下 Andrej Karpathy 的 X 推文做 6 字段深度技术分析:

📌 中文主题: 用 ≤15 字概括推文核心主题 (信达雅, 不直译)
🔑 核心观点: 3-5 条 bullet, 每条 1 句 ≤ 50 字, 列出 Karpathy 的关键论点/事实/技术声明
💡 技术深度解读: 揭示作者立场 / 技术背景 / 为什么重要 / 与已有讨论的关联 / 局限性
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字 (深度推文充分展开)
🎯 系统启示: 1-3 条对 Agent Runtime / Control Plane / Memory 系统的具体启示, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 用于什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.62 新增 (V37.9.51 ai_leaders_x 同款 Opportunity Radar #2 模板, 用于过滤 OpenClaw 高价值信号) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 任务)
   ⭐      = 完全无关 (噪声, 比如硬件细节 / GPU kernel / 单纯学术 paper)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的推文文本中的信息, 严禁虚构作者未提及的事实/数据/链接
- 推文短不足以判断深度时, 标⭐较低 + 写"基于推文片段的初步判断"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态除非原文提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文主题: <你的概括>

🔑 核心观点:
- 观点1
- 观点2

💡 技术深度解读:
<段落, 长度按评级规则>

🎯 系统启示:
- 启示1
- 启示2

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: <场景描述>

🎚️ 项目对齐度: ⭐⭐⭐ / <一句话原因, ≤ 30 字>

---

"""
prompt += f"作者: {author} ({label})\n"
prompt += f"推文原文:\n{text}\n"
# V37.9.62: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "调用 LLM 分析条 $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        $PYTHON3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: 条 $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        $PYTHON3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定整体 status (V37.9.36 fail-fast 契约保留) ──────────────────
if [ "$TOTAL_FAILED" -eq "$TOTAL_NEW" ]; then
    log "ERROR: 全部 $TOTAL_NEW 条 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 条 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 条失败 — 走 partial_degraded (失败条标 [LLM_DEGRADED])"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 条 LLM 部分失败 (其余正常推送, 失败条标 [LLM_DEGRADED])"
fi
echo "[karpathy_x] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── V37.9.62: 6 字段 emit (key-based parser + LLM_DEGRADED fallback + Opportunity Radar #2) ──
MSG_FILE="$CACHE/karpathy_message.txt"
$PYTHON3 - "$TWEETS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.62: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款)

tweets_file, results_file, day, msg_file = sys.argv[1:5]

tweets = []
with open(tweets_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            tweets.append(json.loads(line))
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.62 6 字段 key-based parser (V37.9.51 ai_leaders_x 同款 Opportunity Radar #2)
def parse_6field_output(content):
    fields = {
        'cn_title': '', 'highlights': '', 'insight': '', 'practice': '', 'rating': '',
        'alignment': '',  # V37.9.62 新增
    }
    current_field = None
    current_buffer = []

    def flush():
        if current_field and current_buffer:
            fields[current_field] = '\n'.join(current_buffer).strip()

    for raw in content.split('\n'):
        line = raw.rstrip()
        if re.match(r'^[-=*_]{3,}$', line.strip()):
            continue
        if line.lstrip().startswith('📌'):
            flush()
            current_field = 'cn_title'
            current_buffer = []
            m = re.match(r'.*📌\s*(?:中文)?主题\s*[:：]?\s*(.*)', line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        if line.lstrip().startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        if line.lstrip().startswith('💡'):
            flush()
            current_field = 'insight'
            current_buffer = []
            continue
        if line.lstrip().startswith('🎯'):
            flush()
            current_field = 'practice'
            current_buffer = []
            continue
        # 🎚️ 项目对齐度 (V37.9.62 新增, fallback 🎚 if no variation selector)
        stripped = line.lstrip()
        if stripped.startswith('🎚️') or stripped.startswith('🎚'):
            flush()
            current_field = 'alignment'
            current_buffer = []
            m = re.match(r'.*🎚️?\s*(?:项目)?对齐度?\s*[:：]?\s*(.*)', stripped)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        if line.lstrip().startswith('⭐') and current_field != 'rating':
            if '评级' in line or '推荐场景' in line or re.match(r'\s*⭐+\s*$', line):
                flush()
                current_field = 'rating'
                current_buffer = [line.lstrip()]
                continue
        if current_field is not None:
            current_buffer.append(line)
        elif line.strip():
            pass

    flush()
    return fields


msg_lines = [f"\U0001F9E0 Karpathy 技术洞察 ({day})", ""]

# V37.9.62: lazy import project_alignment_scorer + load concepts (V37.9.51 ai_leaders_x 同款 rule_check)
# FAIL-OPEN: 模块缺失 / yaml 缺失 → 跳过 rule_check 不阻塞 cron
_concepts = None
_validate_alignment_score = None
_extract_star_count = None
_format_validation_marker = None
try:
    sys.path.insert(0, os.environ.get('HOME', os.path.expanduser('~')))
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) if '__file__' in dir() else '.')
    from project_alignment_scorer import (
        load_project_concepts,
        validate_alignment_score,
        extract_star_count,
        format_validation_marker,
    )
    _concepts = load_project_concepts()
    _validate_alignment_score = validate_alignment_score
    _extract_star_count = extract_star_count
    _format_validation_marker = format_validation_marker
    print("[karpathy_x] V37.9.62 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[karpathy_x] V37.9.62 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.62: ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, tweet in enumerate(tweets):
    text_preview = tweet['text'][:200]
    if len(tweet['text']) > 200:
        text_preview += '...'
    author = tweet.get('author', 'Andrej Karpathy')
    link = tweet.get('link', '')
    if not link and tweet.get('id', '').isdigit():
        link = f"https://x.com/karpathy/status/{tweet['id']}"

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 推文原文兜底 (替代 V37.9.36 占位符反模式)
        degraded_count += 1
        msg_lines.append(f"━━━ 推文 {i+1} ━━━")
        msg_lines.append(f"_{text_preview}_")
        if link:
            msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 推文原文供参考 (见上)")
        msg_lines.append("")
    else:
        # V37.9.62: 解析 6 字段 (V37.9.51 ai_leaders_x 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or f"推文 {i+1}"
        msg_lines.append(f"━━━ {title_display} ━━━")
        msg_lines.append(f"_{text_preview}_")
        if link:
            msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        if fields['highlights']:
            msg_lines.append("🔑 核心观点:")
            msg_lines.append(fields['highlights'])
            msg_lines.append("")
        if fields['insight']:
            msg_lines.append("💡 技术深度解读:")
            msg_lines.append(fields['insight'])
            msg_lines.append("")
        if fields['practice']:
            msg_lines.append("🎯 系统启示:")
            msg_lines.append(fields['practice'])
            msg_lines.append("")
        if fields['rating']:
            msg_lines.append(fields['rating'])
            msg_lines.append("")
        # V37.9.62: 🎚️ 项目对齐度展示 + rule_check 验证 (V37.9.51 ai_leaders_x 同款)
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            # rule_check: LLM ⭐ 评分 vs keyword-based rule 一致性
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = author + text (V37.9.51 ai_leaders_x 同款模式)
                        rule_content = tweet.get('author', '') + ' ' + tweet.get('text', '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[karpathy_x] V37.9.62 rule_check 失败 tweet={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.62: 末尾追加高对齐统计 (Opportunity Radar #2)
total_tweets = len(tweets)
if total_tweets > 0:
    msg_lines.append(f"━━━ 本轮高对齐推文 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_tweets} 条 ━━━")
    msg_lines.append("")

msg_lines.append("来源：@karpathy on X | 深度分析 by Qwen3-235B")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[karpathy_x] 消息组装完成: {len(tweets)} 条 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 推送 WhatsApp + Discord (V37.9.21/V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    MSG_CONTENT="$(cat "$MSG_FILE")"
    # V37.9.171: 主推/分块走 notify.sh（微信→用户 + Discord #tech + 重试/队列），退役裸 whatsapp+discord 对
    if notify "$MSG_CONTENT" --topic tech 2>"$SEND_ERR"; then
        log "已推送 ${TOTAL_NEW} 条 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    WA_CHUNK_DIR=$(mktemp -d)
    trap 'rm -rf "$WA_CHUNK_DIR"; rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

    $PYTHON3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$DAY" << 'PYEOF'
import sys, os, re

msg_file, chunk_dir, day = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(msg_file, encoding='utf-8').read()
MAX_CHUNK = 4000

blocks = re.split(r'\n---\n', content)
header_block = blocks[0]
article_blocks = [b for b in blocks[1:] if b.strip()]

chunks = []
current = header_block
for block in article_blocks:
    candidate = current + "\n---\n" + block
    if len(candidate) < MAX_CHUNK:
        current = candidate
    else:
        chunks.append(current)
        current = block
if current.strip():
    chunks.append(current)

total_parts = len(chunks)
for i, chunk in enumerate(chunks):
    if total_parts > 1:
        if i == 0:
            chunk = chunk.replace(f"\U0001F9E0 Karpathy 技术洞察 ({day})",
                                  f"\U0001F9E0 Karpathy 技术洞察 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"\U0001F9E0 Karpathy 技术洞察 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
    with open(os.path.join(chunk_dir, f"{i:03d}.txt"), 'w', encoding='utf-8') as f:
        f.write(chunk)
PYEOF

    WA_PARTS_TOTAL=$(ls "$WA_CHUNK_DIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
    WA_SENT_OK=0
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        CHUNK_CONTENT="$(cat "$chunk_file")"
        if notify "$CHUNK_CONTENT" --topic tech 2>>"$SEND_ERR"; then
            WA_SENT_OK=$((WA_SENT_OK + 1))
        fi
        sleep 1  # 防 WhatsApp 消息乱序 (V37.9.21 契约)
    done
    log "已推送 ${TOTAL_NEW} 条 (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
    if [ "$WA_SENT_OK" -gt 0 ]; then
        WA_SENT=true
    fi
fi

if [ "$WA_SENT" = "true" ]; then
    # 标记已发送
    $PYTHON3 -c "
import json, sys
with open('$TWEETS_FILE') as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            print(d.get('id', ''))
" >> "$SEEN_FILE"
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi
rm -f "$SEND_ERR"

# ── KB 深度归档 ─────────────────────────────────────────────────────
FULL_ANALYSIS="$(cat "$MSG_FILE")"
if [ -n "$FULL_ANALYSIS" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# Karpathy X 技术分享 ${DATE_KB}

${FULL_ANALYSIS}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "karpathy-insights" "note" 2>/dev/null || true
    log "KB写入完成"
fi

# ── 永久归档（源文件） ───────────────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 清理 seen 缓存 ───────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── rsync 备份 ──────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

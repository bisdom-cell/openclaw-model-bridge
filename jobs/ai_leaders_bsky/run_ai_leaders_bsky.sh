#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# AI 大神 Bluesky 实时短观点监控 (V37.9.110 — ai_leaders 加实时短观点维度)
# 目的: 收集 AI 领域顶尖学者专家在 Bluesky 的实时短帖/观点 (尤其"不同意见"),
#   补足 ai_leaders_blogs (长文, V37.9.108) + ai_leaders_x (X Syndication 429+冻结
#   退化产 ~0, V37.9.101/102/103) 的实时短观点维度。
# 背景: 30 万学者 2023-2025 从 X 迁入 Bluesky (arXiv 2505.24801 + Science 报道),
#   2 万+ 有影响力科学家已在 Bluesky。Bluesky 公开 AppView API (getAuthorFeed)
#   无需认证 + 带缓存 (docs.bsky.app 确认), 是免费可靠的非 X 实时短观点渠道。
# 复用 ai_leaders_blogs/rss_blogs 已验证管道 (LLM 6 字段 + 🎚️ 项目对齐评分 +
#   反幻觉守卫 + 双通道 + KB), 仅抓取层从 RSS XML 换为 Bluesky getAuthorFeed JSON。
# ⚠️ handle 可达性必须 Mac Mini 首跑验证 (原则 #33 + 反馈 #1 chaspark 教训:
#   dev sandbox 网络 403 不代表 handle 死)。每账号 FAIL-OPEN, 死 handle log WARN +
#   跳过不杀 job。首跑后看 log WARN 剪枝 (V37.9.108-hotfix 同款 9/11 feed 活模式)。
# 每天 1 次 (17:00 HKT, 捕获前一整个美国白天的帖子) 由系统 crontab 触发。
set -eo pipefail

# V37.9.57: 公共反幻觉守卫 LEVEL_4_PROJECT_AWARE (MR-8 single-source-of-truth)
# LEVEL_4 含 V37.9.56-hotfix3 具体血案字眼 (禁"OpenClaw 社区发布"/"v26"/"[openclaw]")
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
LOCK="/tmp/ai_leaders_bsky.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[ai_leaders_bsky] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/ai_leaders_bsky"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/ai_leaders_bsky.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
PYTHON3=/usr/bin/python3

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] ai_leaders_bsky: $1" >&2; }

# V37.9.36: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
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

# V37.9.36: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] ai_leaders_bsky LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 AI 大神实时观点 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# AI 大神实时观点 (Bluesky)" > "$KB_SRC"

# ── AI 大神 Bluesky 账号 (V37.9.110) ────────────────────────────────
# 格式：handle|label
# 策展原则: (1) AI 领域顶尖个人学者/专家 (2) 不同意见/流派 (偏重 contrarian, 用户核心诉求)
#   (3) 实时短观点 (与 ai_leaders_blogs 长文互补)。
# ⚠️ handle 是 candidate (8 个 confirmed via WebSearch + 2 个高置信待验),
#   Mac Mini 首跑验证 — 死 handle 会 log "WARN: X Bluesky 抓取失败，跳过" (FAIL-OPEN),
#   之后剪枝 (V37.9.108-hotfix 同款模式)。Bluesky actor 参数接受 handle 或自定义域名。
BSKY_ACCOUNTS=(
    # ── 世界模型 / 反 LLM-AGI 路线 ──
    "ylecun.bsky.social|Yann LeCun(Meta,世界模型派/反LLM-AGI路线)"
    "fchollet.bsky.social|François Chollet(ARC-AGI/Keras,抽象推理/反scale-is-all)"
    # ── AI 理解 / 复杂系统怀疑派 ──
    "melaniemitchell.bsky.social|Melanie Mitchell(Santa Fe,AI理解怀疑/复杂系统)"
    # ── LLM 推理 / 规划怀疑派 ──
    "rao2z.bsky.social|Subbarao Kambhampati(ASU,LLM推理/规划怀疑派)"
    # ── 炒作祛魅 / 批判性 AI (实时短观点 vs AI Snake Oil 长文) ──
    "randomwalker.bsky.social|Arvind Narayanan(Princeton,AI炒作祛魅/实时短评)"
    # ── AI 伦理 / 治理 ──
    "timnitgebru.bsky.social|Timnit Gebru(DAIR,AI伦理/批判)"
    "mmitchell.bsky.social|Margaret Mitchell(HuggingFace,AI伦理/治理)"
    # ── 开源 / 实用 / 民主化派 ──
    "howard.fm|Jeremy Howard(fast.ai,开源/AI民主化/实用派)"
    # ── 高置信候选 (Mac Mini 验证, FAIL-OPEN 剪枝) ──
    "garymarcus.bsky.social|Gary Marcus(神经符号/反LLM-AGI,实时短评vs Substack长文)"
    "emilymbender.bsky.social|Emily Bender(UW,NLP/stochastic parrots/反炒作)"
)

SEEN_FILE="$CACHE/seen_urls.txt"
touch "$SEEN_FILE"
ALL_NEW_FILE="$CACHE/all_new.jsonl"
> "$ALL_NEW_FILE"

TOTAL_NEW=0

for account_entry in "${BSKY_ACCOUNTS[@]}"; do
    IFS='|' read -r BSKY_HANDLE BSKY_LABEL <<< "$account_entry"
    FEED_FILE="$CACHE/feed_$(echo "$BSKY_HANDLE" | tr './' '__').json"

    # 抓取 Bluesky getAuthorFeed JSON (公开 AppView, 无需认证)
    FETCH_OK=false
    for attempt in 1 2 3; do
        HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
            -H "User-Agent: openclaw-bsky-monitor/1.0" \
            -H "Accept: application/json" \
            -o "$FEED_FILE" \
            "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=${BSKY_HANDLE}&limit=20&filter=posts_no_replies" \
            2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "200" ] && [ -s "$FEED_FILE" ]; then
            FETCH_OK=true
            break
        else
            log "WARN: ${BSKY_HANDLE} getAuthorFeed HTTP ${HTTP_CODE} (attempt ${attempt})"
        fi
        sleep "$((attempt * 5))"
    done

    if [ "$FETCH_OK" != "true" ]; then
        log "WARN: ${BSKY_HANDLE} Bluesky 抓取失败，跳过"
        continue
    fi

    # 解析 getAuthorFeed JSON → 提取新帖子 (skip 转发 + 过短, 取最新 N 条实质帖)
    $PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$BSKY_HANDLE" "$BSKY_LABEL" << 'PYEOF' >> "$ALL_NEW_FILE"
import sys, json

feed_file = sys.argv[1]
seen_file = sys.argv[2]
handle = sys.argv[3]
label = sys.argv[4]

MIN_POST_CHARS = 50    # 跳过过短帖子 (单纯 emoji / ack)，避免对琐碎短帖做 6 字段分析
MAX_PER_ACCOUNT = 3    # 每账号每次最多 3 条实质新帖

with open(seen_file) as f:
    seen_urls = set(line.strip() for line in f if line.strip())

try:
    data = json.load(open(feed_file, encoding='utf-8'))
except Exception as e:
    print(f"[ai_leaders_bsky] ERROR: {handle} JSON 解析失败 {type(e).__name__}", file=sys.stderr)
    sys.exit(0)

feed = data.get('feed') or []
new_count = 0
for item in feed:
    if new_count >= MAX_PER_ACCOUNT:
        break
    post = item.get('post') or {}

    # skip 转发 (reasonRepost) — 只要大神自己的原创帖
    reason = item.get('reason') or {}
    if str(reason.get('$type', '')).endswith('reasonRepost'):
        continue

    record = post.get('record') or {}
    text = (record.get('text') or '').strip()
    if len(text) < MIN_POST_CHARS:
        continue

    uri = post.get('uri') or ''
    author = post.get('author') or {}
    author_handle = author.get('handle') or handle
    author_name = author.get('displayName') or author_handle
    # at://did:plc:xxx/app.bsky.feed.post/rkey → https://bsky.app/profile/{handle}/post/{rkey}
    rkey = uri.rsplit('/', 1)[-1] if uri else ''
    web_url = f"https://bsky.app/profile/{author_handle}/post/{rkey}" if rkey else ''
    created = (record.get('createdAt') or '')[:25]

    # 嵌入的外部链接 (大神分享论文/文章时, 链接标题给 LLM 更多上下文)
    embed = post.get('embed') or {}
    ext_note = ''
    if str(embed.get('$type', '')).startswith('app.bsky.embed.external'):
        ext = embed.get('external') or {}
        et = (ext.get('title') or '').strip()
        eu = (ext.get('uri') or '').strip()
        if et or eu:
            ext_note = f"[附带链接] {et} {eu}".strip()

    if not text or not web_url:
        continue
    if web_url in seen_urls:
        continue

    # 用 text + ext_note 作 description 给 LLM; title 取前 100 字作展示头
    desc = text
    if ext_note:
        desc = f"{text}\n{ext_note}"

    print(json.dumps({
        "title": text[:100],
        "description": desc,
        "link": web_url,
        "pub_date": created,
        "author": author_name,
        "handle": author_handle,
        "feed_name": handle,
        "feed_label": label,
    }, ensure_ascii=False))
    new_count += 1

print(f"[ai_leaders_bsky] {handle}: {new_count} 新帖", file=sys.stderr)
PYEOF

    sleep 2  # 账号间礼貌节流 (公开 API 缓存友好)
done

TOTAL_NEW="$(wc -l < "$ALL_NEW_FILE" | tr -d ' ')"
if [ "$TOTAL_NEW" -eq 0 ]; then
    log "无新帖，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[ai_leaders_bsky] 共 ${TOTAL_NEW} 条新帖"

# ── 每帖独立调 LLM (6 字段分析 + 短帖防过度膨胀 + retry 3 次) ─────────
# 复用 ai_leaders_blogs V37.9.37 模式: 每帖独立调用 + 独立 retry (5s/10s/20s),
# 部分失败走 partial_degraded, 全部失败 fail-fast (V37.9.36 契约)。

LLM_RAW="$CACHE/llm_raw_last.txt"
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单帖 LLM 调用 + retry ───────────────────────────────────
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

        # V37.9.36 三层检测 (HTTP error / parse fail / empty content)
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
            log "WARN: 帖 $idx attempt $((attempt+1))/3: $LAST_LLM_FAIL_REASON"
            if [ $attempt -lt 2 ]; then
                sleep "${backoffs[$attempt]}"
            fi
            continue
        fi

        if [ -z "${parse_out// }" ]; then
            LAST_LLM_FAIL_REASON="empty_content"
            log "WARN: 帖 $idx attempt $((attempt+1))/3: empty content"
            if [ $attempt -lt 2 ]; then
                sleep "${backoffs[$attempt]}"
            fi
            continue
        fi

        echo "$parse_out"
        return 0
    done

    return 1
}

# ── 主循环: 每帖独立调 LLM ────────────────────────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$ALL_NEW_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # os 用于注入 HG_LEVEL_4_TEXT (V37.9.50-hotfix NameError fix 同款)

articles_file, idx = sys.argv[1], int(sys.argv[2])
with open(articles_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
a = articles[idx]
author = a.get('author', '') or a.get('handle', '')
text = a.get('description', '')[:800]

prompt = """你是 AI 学者实时观点分析师 (兼 OpenClaw 项目对齐评估师)。下文是 AI 领域顶尖学者/专家在 Bluesky 发表的实时短帖/观点 (Bluesky 是学者从 X 迁入的新平台)。提炼其核心立场与独特视角, 输出 6 字段中文分析:

📌 中文标题: 用一句话概括这条观点的要旨, 不超过 25 字
🔑 核心要点: 1-3 条 bullet, 每条 1 句 ≤ 60 字, 列出帖子表达的核心主张/判断
💡 关键洞察: 重点揭示 ① 作者的独特立场/观点 (尤其与主流共识的分歧或不同意见) ② 观点背后的论证逻辑 ③ 与行业趋势/其他学者观点的关联或对立 ④ 局限性或可争议处
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→200-350字 / ⭐⭐⭐⭐⭐→400-600字
   ⚠️ Bluesky 帖子通常简短, 评级和洞察长度应与帖子的实际信息量匹配。单句观点给 ⭐⭐⭐ + 简短洞察, 仅链接转发给 ⭐⭐, 不要为短帖编造长篇分析。
🎯 实践启发: 1-2 条对 AI 工程师/创业者/架构师的具体行动建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ Opportunity Radar #2 模板 (V37.9.45 hf_papers / V37.9.50 semantic_scholar / V37.9.51 同款, 用于过滤 OpenClaw 高价值信号) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心)
   ⭐⭐     = 无明显关联 (但可能未来有用)
   ⭐      = 完全无关 (噪声)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的帖子原文信息, 严禁虚构作者未提及的事实/数据/链接
- 帖子简短不足以判断深度时, 标⭐较低 + 写"基于短帖的初步判断"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态除非原文提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文标题: <你的概括>

🔑 核心要点:
- 要点1

💡 关键洞察:
<段落, 长度按上述评级规则>

🎯 实践启发:
- 启发1

⭐ 评级: ⭐⭐⭐ / 推荐场景: <场景描述>

🎚️ 项目对齐度: ⭐⭐⭐ / <一句话原因, ≤ 30 字>

---

"""
prompt += f"作者: {author}\n"
prompt += f"Bluesky 帖子原文:\n{text}\n"
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "调用 LLM 分析帖 $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        $PYTHON3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: 帖 $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        $PYTHON3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定整体 status (V37.9.36 fail-fast 契约保留) ──────────────────
if [ "$TOTAL_FAILED" -eq "$TOTAL_NEW" ]; then
    log "ERROR: 全部 $TOTAL_NEW 帖 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 帖 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 帖失败 — 走 partial_degraded (失败帖标 [LLM_DEGRADED] + 原帖摘要 fallback)"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 帖 LLM 部分失败 (其余正常推送, 失败帖标 [LLM_DEGRADED])"
fi

# ── 组装消息 (6 字段解析 + LLM_DEGRADED fallback + 多窗口切片) ──────
MSG_FILE="$CACHE/bsky_message.txt"
$PYTHON3 - "$ALL_NEW_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # os 用于 lazy import project_alignment_scorer 路径解析

articles_file, results_file, day, msg_file = sys.argv[1:5]

with open(articles_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# 6 字段 key-based parser (V37.9.45 hf_papers / V37.9.50 / V37.9.51 同款 Opportunity Radar #2)
def parse_6field_output(content):
    """从 LLM 输出解析 6 字段, key-based + tolerant.

    返回 dict: cn_title / highlights / insight / practice / rating / alignment
    """
    fields = {
        'cn_title': '',
        'highlights': '',
        'insight': '',
        'practice': '',
        'rating': '',
        'alignment': '',
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
            m = re.match(r'.*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)', line)
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


msg_lines = [f"🦋 AI 大神实时观点 ({day})", ""]

# lazy import project_alignment_scorer + load concepts (V37.9.45/50/51 同款 rule_check)
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
    print("[ai_leaders_bsky] project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[ai_leaders_bsky] project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, article in enumerate(articles):
    msg_lines.append(f"*观点{i+1}: {article.get('author', '')}*")
    msg_lines.append(f"来源: {article['feed_label']} | {article.get('pub_date', '')[:16]}")
    msg_lines.append(f"链接: {article['link']}")
    msg_lines.append("")

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        degraded_count += 1
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 帖子原文供参考:")
        desc = (article.get('description') or '')[:300]
        if desc:
            msg_lines.append(desc)
        else:
            msg_lines.append("(无原文数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        fields = parse_6field_output(result.get('content', ''))
        if fields['cn_title']:
            msg_lines.append(f"📌 要旨: {fields['cn_title']}")
            msg_lines.append("")
        if fields['highlights']:
            msg_lines.append("🔑 核心要点:")
            msg_lines.append(fields['highlights'])
            msg_lines.append("")
        if fields['insight']:
            msg_lines.append("💡 关键洞察:")
            msg_lines.append(fields['insight'])
            msg_lines.append("")
        if fields['practice']:
            msg_lines.append("🎯 实践启发:")
            msg_lines.append(fields['practice'])
            msg_lines.append("")
        if fields['rating']:
            msg_lines.append(fields['rating'])
            msg_lines.append("")
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        rule_content = article.get('title', '') + ' ' + (article.get('description') or '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[ai_leaders_bsky] rule_check 失败 article={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

total_articles = len(articles)
if total_articles > 0:
    msg_lines.append(f"━━━ 本轮高对齐观点 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_articles} 条 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[ai_leaders_bsky] 消息组装完成: {len(articles)} 条 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 推送 WhatsApp + Discord (多窗口分片: >8000 字才切, ≤8000 单段发) ──
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    MSG_CONTENT="$(cat "$MSG_FILE")"
    if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${TOTAL_NEW} 条 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    WA_CHUNK_DIR=$(mktemp -d)
    # V37.9.86: 合并 lock cleanup 防 bash trap override (lockdir 残留血案)
    trap 'rmdir "$LOCK" 2>/dev/null; rm -rf "$WA_CHUNK_DIR"' EXIT INT TERM

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
            chunk = chunk.replace(f"🦋 AI 大神实时观点 ({day})",
                                  f"🦋 AI 大神实时观点 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"🦋 AI 大神实时观点 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
    with open(os.path.join(chunk_dir, f"{i:03d}.txt"), 'w', encoding='utf-8') as f:
        f.write(chunk)
PYEOF

    WA_PARTS_TOTAL=$(ls "$WA_CHUNK_DIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
    WA_SENT_OK=0
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        CHUNK_CONTENT="$(cat "$chunk_file")"
        if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$CHUNK_CONTENT" --json >/dev/null 2>>"$SEND_ERR"; then
            WA_SENT_OK=$((WA_SENT_OK + 1))
        fi
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$CHUNK_CONTENT" --json >/dev/null 2>&1 || true
        sleep 1  # 防 WhatsApp 消息乱序 (V37.9.21 契约)
    done
    log "已推送 ${TOTAL_NEW} 条 (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
    if [ "$WA_SENT_OK" -gt 0 ]; then
        WA_SENT=true
    fi
fi

if [ "$WA_SENT" = "true" ]; then
    $PYTHON3 -c "
import json
with open('$ALL_NEW_FILE') as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            print(d.get('link', ''))
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

# ── KB归档 ────────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# AI 大神实时观点 ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "ai-leaders-bsky" "note" 2>/dev/null || true
    echo "[ai_leaders_bsky] KB写入完成"
fi

# ── 永久归档 ──────────────────────────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 清理seen缓存 ─────────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── rsync备份 ─────────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture
log "完成"

#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# AI Leaders X/Twitter 技术洞察追踪 v2 (V37.9.51 — 6 字段 + rule_check 升级,
# V37.9.40 深度 5 字段基础 + V37.9.45 hf_papers / V37.9.50 semantic_scholar
# 同款 Opportunity Radar #2 模板横向迁移, Sub-Stage 4b 5/6)
# 每天 2 次（09:00, 21:00 HKT）由系统 crontab 触发
# 追踪 9 位 AI 技术领袖的 X 动态，深度分析并归档到 KB
# 数据源：Twitter Syndication API（无需认证，用于 embed widget）
set -eo pipefail

# V37.9.57: 公共反幻觉守卫 LEVEL_4_PROJECT_AWARE (MR-8 single-source-of-truth)
# 8 ALIGNED jobs 的 per-paper LLM prompt 已有 inline 反幻觉守卫, V37.9.57 追加
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
log() { echo "[$TS] ${JOB_TAG}: $1" >&2; }

# V37.9.40: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
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

# V37.9.40: fail-fast alert helper
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] ai_leaders_x LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 AI Leaders 技术洞察 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE/raw" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# AI Leaders X 技术洞察" > "$KB_SRC"

SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"

# ── 追踪列表（handle|显示名|身份标签）──────────────────────────────────
# V37.9.95: 32 accounts 跨 12 派别 — 用户视角原则 #32 周一观察反馈
# "加入更多 ai_leaders_x 的观点，尤其是更多 AI 大神的不同观点"
# 每人独立抓取，MAX_PER_PERSON=3（从 5 降，多元化优先），MAX_TOTAL=40
#
# 派别分布:
#   OpenAI 主流派 (5):    karpathy, lilianweng, _jasonwei, hwchung27, drfeifei (HAI)
#   Meta/开源派 (3):     ylecun, soumithchintala, ClementDelangue
#   NVIDIA/Hardware (1): DrJimFan
#   Anthropic/Safety 派 (3): DarioAmodei, jackclarkSF, ch402 (Chris Olah)
#   DeepMind/Alphabet (1): demishassabis
#   AI Safety 极致悲观派 (1): ESYudkowsky
#   元老派 (1):          geoffreyhinton (Nobel 2024)
#   学术应用派 (1):       AndrewYNg
#   Robotics/Embodied (1): pabbeel
#   评测/抽象派 (2):     fchollet (ARC), swyx
#   Agent 工具派 (1):    hwchase17 (LangChain)
#   Symbolic/Critical (1): GaryMarcus
#   架构创新派 (1):       YiTayML (Reka, ex-Google)
#   Graph/KG (2):       juaborges (Leskovec), Michael_Witbrock (Cyc)
#   企业 Ontology 派 (3): PalantirTech, AlexKarp, ShyamSankar
#   形式本体论奠基 (4):   BarrySmith46, gaborguizzardi, pascal_hitzler, IanHorrocks
#
# 健康验证: V37.8.4 INV-X-001 教训 — 新增账号若 newest_tweet > 7 天
# 视为僵尸, 真实生产时 fetch 0 条 WARN log 自动暴露, 之后从列表移除.
LEADERS=(
    # ── OpenAI 主流派 ──
    "karpathy|Andrej Karpathy|前OpenAI/Tesla AI负责人，AI教育家"
    "lilianweng|Lilian Weng|OpenAI，LLM/Agent/RAG综述专家"
    "_jasonwei|Jason Wei|OpenAI，Chain-of-Thought推理研究"
    "hwchung27|Hyung Won Chung|OpenAI，Scaling Laws/训练洞察"
    "drfeifei|Fei-Fei Li|Stanford HAI，Human-centered AI"
    # ── Meta / 开源派 ──
    "ylecun|Yann LeCun|Meta首席AI科学家，深度学习先驱"
    "soumithchintala|Soumith Chintala|Meta，PyTorch创建者，开源基础设施"
    "ClementDelangue|Clement Delangue|Hugging Face CEO，开源生态领导者"
    # ── NVIDIA / Hardware ──
    "DrJimFan|Jim Fan|NVIDIA高级研究员，Foundation Agents/具身智能"
    # ── Anthropic / Safety 派 ──
    "DarioAmodei|Dario Amodei|Anthropic CEO，Constitutional AI/Claude"
    "jackclarkSF|Jack Clark|Anthropic 联合创始人，AI Index/政策"
    "ch402|Chris Olah|Anthropic，可解释性/Mechanistic Interpretability"
    # ── DeepMind / Alphabet ──
    "demishassabis|Demis Hassabis|DeepMind CEO，AlphaFold/Gemini，谨慎科学派"
    # ── AI Safety 极致悲观派 ──
    "ESYudkowsky|Eliezer Yudkowsky|MIRI，AI doom 极致悲观派"
    # ── 元老派 ──
    "geoffreyhinton|Geoffrey Hinton|AI Godfather，2024 Nobel，Safety 转变派"
    # ── 学术应用派 ──
    "AndrewYNg|Andrew Ng|DeepLearning.ai/Coursera，教育与应用"
    # ── Robotics / Embodied ──
    "pabbeel|Pieter Abbeel|UC Berkeley + Covariant，机器人学习"
    # ── 评测 / 抽象派 ──
    "fchollet|François Chollet|Keras创建者，ARC Benchmark，智能评测"
    "swyx|Swyx|Latent Space主播，AI Engineering实践"
    # ── Agent 工具派 ──
    "hwchase17|Harrison Chase|LangChain创建者，Agent编排/工具链"
    # ── Symbolic / Critical ──
    "GaryMarcus|Gary Marcus|Neuro-Symbolic AI倡导者，Rebooting AI作者"
    # ── 架构创新派 (非美企背景) ──
    "YiTayML|Yi Tay|Reka AI联合创始人，前Google Brain 架构创新"
    # ── Graph / KG ──
    "juaborges|Jure Leskovec|Stanford，图神经网络/知识图谱推理"
    "Michael_Witbrock|Michael Witbrock|Cycorp/Cyc知识库，常识推理先驱"
    # ── 企业 Ontology 派 ──
    "PalantirTech|Palantir Technologies|企业AI平台/Ontology+AIP/数据治理"
    "AlexKarp|Alex Karp|Palantir CEO，企业AI+本体论落地实践"
    "ShyamSankar|Shyam Sankar|Palantir CTO，Foundry Ontology架构师"
    # ── 形式本体论奠基 ──
    "BarrySmith46|Barry Smith|BFO创建者，形式本体论奠基人"
    "gaborguizzardi|Giancarlo Guizzardi|UFO/OntoUML创建者，概念建模"
    "pascal_hitzler|Pascal Hitzler|Kansas State，Knowledge Graph/语义网"
    "IanHorrocks|Ian Horrocks|Oxford，OWL/Description Logic奠基人"
)

# V37.9.95: 32 accounts × 3 tweets/person = ~96 max raw, top 40 → LLM
MAX_PER_PERSON=3
MAX_TOTAL=40
ALL_TWEETS="$CACHE/all_tweets.jsonl"
> "$ALL_TWEETS"
FETCH_STATS=""

# ── V37.9.101: 轮换抓取防 429 ─────────────────────────────────────────
# 2026-06-03 复盘实测: 31 账号全 no_data (单条隔离请求都 HTTP:429 SIZE:20, IP 被标记),
# job 产 0 推文. V37.9.95 把账号 19→31 翻倍后每 run 31 请求超 X Syndication 限流阈值,
# V37.9.99 5s 节流对单 run 内有帮助但请求总量仍超. 修复: 每 run 只抓 batch 子集
# (默认 11), 按 rotation_idx 轮换+环绕, ceil(31/11)=3 run 全覆盖. 单 run 请求量 31→11
# 降到阈值下, 配 5s 节流 = 55s 摊开. 状态文件每 run 递增 (鲁棒于漏跑).
ROTATION_MOD="$JOB_DIR/ai_leaders_rotation.py"
ROTATION_FILE="$CACHE/rotation_idx"
ROTATION_IDX=$(cat "$ROTATION_FILE" 2>/dev/null || echo 0)
case "$ROTATION_IDX" in *[!0-9]*|"") ROTATION_IDX=0 ;; esac
AI_LEADERS_BATCH="${AI_LEADERS_BATCH:-11}"
SELECTED_IDX=""
if [ -f "$ROTATION_MOD" ]; then
    # || true: set -eo pipefail 下 python 非零退出不杀脚本 (FAIL-OPEN 下方 -z 兜底)
    SELECTED_IDX=$(python3 "$ROTATION_MOD" select "${#LEADERS[@]}" "$ROTATION_IDX" "$AI_LEADERS_BATCH" 2>/dev/null || true)
fi
# FAIL-OPEN: 模块缺失/异常 → 抓全部 (保持旧行为不阻塞, 但记 WARN 提示 429 风险)
if [ -z "$SELECTED_IDX" ]; then
    SELECTED_IDX=$(seq 0 $(( ${#LEADERS[@]} - 1 )))
    log "WARN: ai_leaders_rotation.py 缺失/异常, FAIL-OPEN 抓全部 ${#LEADERS[@]} 账号 (429 风险)"
else
    SEL_N=$(echo $SELECTED_IDX | wc -w | tr -d ' ')
    log "V37.9.101 轮换: idx=$ROTATION_IDX batch=$AI_LEADERS_BATCH → 本 run 抓 ${SEL_N}/${#LEADERS[@]} 账号 [索引 $SELECTED_IDX]"
fi

# ── 1. 逐账号抓取推文 (仅本 run 轮换选中的子集) ───────────────────────────
# 节流移到循环顶部对成功+失败账号都生效 (V37.9.99): 失败 continue 跳过 → 限流后
# rapid-fire. 默认每账号 5s, env AI_LEADERS_FETCH_DELAY 可调.
AI_LEADERS_FETCH_DELAY="${AI_LEADERS_FETCH_DELAY:-5}"
FETCH_IDX=0
for sel in $SELECTED_IDX; do
    leader_entry="${LEADERS[$sel]}"
    IFS='|' read -r HANDLE DISPLAY_NAME LABEL <<< "$leader_entry"
    RAW_HTML="$CACHE/raw/${HANDLE}.html"
    # 每账号 fetch 前节流 (第一个不 sleep), 成功+失败都生效防限流填满
    [ "$FETCH_IDX" -gt 0 ] && sleep "$AI_LEADERS_FETCH_DELAY"
    FETCH_IDX=$((FETCH_IDX + 1))

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
    # V37.9.99: inter-account 节流已移到循环顶部 (对成功+失败账号都生效防 429)
done

# V37.9.101: 递增轮换计数器 (鲁棒于漏跑 — 只在真跑时前进, 下 run 抓下一子集)
echo "$(( ROTATION_IDX + 1 ))" > "$ROTATION_FILE" 2>/dev/null || true

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

# ── V37.9.40: 每条推文独立调 LLM (5 字段深度分析 + 按评级动态调长度 + retry 3 次) ──
# 老 V37.8: 单次调用全部 N 条 + 5 行格式 (主题/深度分析/系统启示/行动建议/价值) + 失败 silent fallback
# 新 V37.9.40: 每条独立调用 + 独立 retry (5s/10s/20s) + 5 字段 emoji 格式 (与 S2/rss_blogs 统一)
#   📌 中文主题 / 🔑 核心观点 / 💡 技术深度解读 / 🎯 系统启示 / ⭐ 评级
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

# ── 主循环: 每条推文独立调 LLM (5 字段深度) ──────────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$ALL_TWEETS" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT (line ~411) NameError fix

tweets_file, idx = sys.argv[1], int(sys.argv[2])
tweets = []
with open(tweets_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            tweets.append(json.loads(line))
t = tweets[idx]
text = t['text']
author = t['author']
label = t['label']

prompt = """你是资深 AI 系统架构师 (兼 OpenClaw 项目对齐评估师)。对以下 X 推文做 6 字段深度技术分析:

📌 中文主题: 用 ≤15 字概括推文核心主题 (信达雅, 不直译)
🔑 核心观点: 3-5 条 bullet, 每条 1 句 ≤ 50 字, 列出推文作者的关键论点/事实/技术声明
💡 技术深度解读: 揭示作者立场 / 技术背景 / 为什么重要 / 与已有讨论的关联 / 局限性
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字 (深度推文充分展开)
🎯 系统启示: 1-3 条对 Agent Runtime / Control Plane / Memory 系统的具体启示, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 用于什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.51 新增 (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2 模板, 用于过滤 OpenClaw 高价值信号) ━

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
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
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
echo "[ai_leaders] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── V37.9.40: 5 字段 emit (5-field key-based parser + LLM_DEGRADED fallback + 多窗口切片) ──
MSG_FILE="$CACHE/ai_leaders_message.txt"
$PYTHON3 - "$ALL_TWEETS" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.51: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款)

tweets_file, results_file, day, msg_file = sys.argv[1:5]

tweets = []
with open(tweets_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            tweets.append(json.loads(line))
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.51 6 字段 key-based parser (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2)
def parse_6field_output(content):
    fields = {
        'cn_title': '', 'highlights': '', 'insight': '', 'practice': '', 'rating': '',
        'alignment': '',  # V37.9.51 新增
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
        # 🎚️ 项目对齐度 (V37.9.51 新增, fallback 🎚 if no variation selector)
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


# 按作者分组统计
author_counts = {}
for t in tweets:
    author_counts[t['author']] = author_counts.get(t['author'], 0) + 1
authors_summary = ', '.join(f"{k}({v})" for k, v in author_counts.items())

msg_lines = [f"🧠 AI Leaders 技术洞察 ({day})",
             f"来源: {authors_summary}", ""]

# V37.9.51: lazy import project_alignment_scorer + load concepts (V37.9.45 hf_papers / V37.9.50 同款 rule_check)
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
    print("[ai_leaders] V37.9.51 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[ai_leaders] V37.9.51 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.51: ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, tweet in enumerate(tweets):
    text_preview = tweet['text'][:200]
    if len(tweet['text']) > 200:
        text_preview += '...'
    author = tweet['author']
    link = tweet.get('link', '')

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 推文原文兜底 (替代 V37.9.36 占位符反模式)
        degraded_count += 1
        msg_lines.append(f"━━━ [{author}] ━━━")
        msg_lines.append(f"_{text_preview}_")
        if link:
            msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 推文原文供参考 (见上)")
        msg_lines.append("")
    else:
        # V37.9.51: 解析 6 字段 (V37.9.45 hf_papers / V37.9.50 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or f"{author} 技术分享"
        msg_lines.append(f"━━━ [{author}] {title_display} ━━━")
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
        # V37.9.51: 🎚️ 项目对齐度展示 + rule_check 验证 (V37.9.45 hf_papers / V37.9.50 同款)
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            # rule_check: LLM ⭐ 评分 vs keyword-based rule 一致性
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = author + text (V37.9.47 hf_papers 同款模式适配 tweet 场景)
                        rule_content = tweet.get('author', '') + ' ' + tweet.get('text', '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[ai_leaders] V37.9.51 rule_check 失败 tweet={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.51: 末尾追加高对齐统计 (Opportunity Radar #2)
total_tweets = len(tweets)
if total_tweets > 0:
    msg_lines.append(f"━━━ 本轮高对齐推文 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_tweets} 条 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[ai_leaders] 消息组装完成: {len(tweets)} 条 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 推送 WhatsApp + Discord (V37.9.21/V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | LC_ALL=C tr -d ' ')
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
            chunk = chunk.replace(f"🧠 AI Leaders 技术洞察 ({day})",
                                  f"🧠 AI Leaders 技术洞察 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"🧠 AI Leaders 技术洞察 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
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
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true,"stats":"%s"}\n' "$TS" "$TOTAL_NEW" "$TOTAL_FAILED" "$FETCH_STATS" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true,"stats":"%s"}\n' "$TS" "$TOTAL_NEW" "$FETCH_STATS" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
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
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 8. 清理 seen 缓存 ───────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 2000 ]; then
    tail -1000 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── 9. rsync 备份 ──────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

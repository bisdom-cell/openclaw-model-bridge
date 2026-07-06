#!/bin/bash
# run_chaspark.sh — 黄大年茶思屋(Chaspark)科技网站内容监控
# 通过 chaspark.com 官方 API 直接抓取首页推荐内容，LLM 分析后推送 + KB 归档
# cron: 每天 11:00 执行
#
# V37.9.62 混合设计 (per-article 6 字段 + 跨域分析 chaspark 特色):
#   Phase A: per-article 6 字段 (V37.9.51 rss_blogs / V37.9.45 hf_papers / V37.9.50 s2 同款
#            Opportunity Radar #2 模板, Sub-Stage 4b 续 batch)
#   Phase B: 跨文章关联洞察 (200-300 字, chaspark 特色, V37.9.33 freight 三层结构混合 LLM 调用参考)
#
# 数据通路：Chaspark API → JSON 解析 → 去重 → Phase A per-article 6 字段 LLM → Phase B 跨域分析 LLM → KB + 推送
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
OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
TO="${OPENCLAW_PHONE:-+85200000000}"

TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] chaspark: $1" >&2; }

mkdir -p "$CACHE/raw" "$(dirname "$KB_SRC")"
test -f "$KB_SRC" || echo "# 黄大年茶思屋(Chaspark)科技文章" > "$KB_SRC"

# V37.9.62: 公共反幻觉守卫 LEVEL_4_PROJECT_AWARE (V37.9.57 MR-8 single-source-of-truth)
# Phase A per-article LLM prompt 已含 inline 反幻觉, V37.9.62 追加 LEVEL_4
# 含 V37.9.56-hotfix3 具体血案字眼 (禁"OpenClaw 社区发布"/"v26"/"[openclaw]")
# 防 alignment 评分 "一句话原因" 段编造项目动态. FAIL-OPEN: 模块缺失 → 空字符串
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

# ── 加载 notify.sh ────────────────────────────────────────────────────
NOTIFY_LOADED=false
NOTIFY_SH=""
for _np in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$_np" ]; then
        source "$_np"
        NOTIFY_LOADED=true
        NOTIFY_SH="$_np"
        break
    fi
done

# V37.9.62: fail-fast alert helper (V37.9.51 rss_blogs / V37.9.36 同款模式)
# Phase A 全部失败时推 [SYSTEM_ALERT] 到 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] chaspark LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 chaspark 深度分析 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

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

# 取 Top 5 做 Phase A 深度分析 (其余在末尾全量列表中展示)
TOP_N=5
TOP_FILE="$CACHE/top_${DAY}.jsonl"
$PYTHON3 -c "
import json
with open('$ALL_ARTICLES') as f:
    arts = [json.loads(l) for l in f if l.strip()]
with open('$TOP_FILE', 'w', encoding='utf-8') as f:
    for a in arts[:$TOP_N]:
        f.write(json.dumps(a, ensure_ascii=False) + '\n')
"
TOP_COUNT=$(wc -l < "$TOP_FILE" 2>/dev/null | tr -d ' ')
log "Phase A 深度分析 Top $TOP_COUNT 篇"

# ── V37.9.62 Phase A: per-article 6 字段 (V37.9.51 rss_blogs 同款) ───
# 每篇独立调用 + 独立 retry (5s/10s/20s), 部分失败走 partial_degraded
# 全部失败 → fail-fast (V37.9.36 契约保留)

LLM_RAW="$CACHE/llm_raw_last.txt"   # forensic: 保留上一次失败响应
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单篇 LLM 调用 + retry (V37.9.51 rss_blogs 同款) ──────────
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
            "$PROXY_URL" 2>/dev/null || true)

        # 保存最后一次响应做 forensic
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
            log "WARN: 篇 $idx attempt $((attempt+1))/3: $LAST_LLM_FAIL_REASON"
            if [ $attempt -lt 2 ]; then
                sleep "${backoffs[$attempt]}"
            fi
            continue
        fi

        if [ -z "${parse_out// }" ]; then
            LAST_LLM_FAIL_REASON="empty_content"
            log "WARN: 篇 $idx attempt $((attempt+1))/3: empty content"
            if [ $attempt -lt 2 ]; then
                sleep "${backoffs[$attempt]}"
            fi
            continue
        fi

        # 成功 → 返回 content
        echo "$parse_out"
        return 0
    done

    # 所有 retry 都失败
    return 1
}

# ── Phase A 主循环: 每篇文章独立调 LLM (6 字段) ────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOP_COUNT; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$TOP_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # V37.9.62: os 用于 V37.9.57 LEVEL_4 注入 (HG_LEVEL_4_TEXT env)

top_file, idx = sys.argv[1], int(sys.argv[2])
with open(top_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
a = articles[idx]
title = a['title']
col_type = a.get('type', '')
slot = a.get('slot', '')
domains = '，'.join(a.get('domains', [])) or '综合'
url = a.get('url', '')

prompt = """你是华为黄大年茶思屋(Chaspark)产业 + 学术深度科技网站资深分析师 (兼 OpenClaw 项目对齐评估师)。

茶思屋是华为旗下专注于产业 + 学术深度科技内容的网站, 评分应考虑"是否对工程实践 / 产业战略 / 本体论 / Agent / LLM infra 有价值", 而不是泛泛 AI/科技话题。

对以下 Chaspark 推荐文章输出 6 字段中文分析:

📌 中文标题: 信达雅翻译 / 优化, 不超过 25 字 (原文已是中文则简化精炼提取核心)
🔑 核心要点: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出文章预期讨论的核心技术问题 / 论点 / 产业信号
💡 关键洞察: 揭示华为/茶思屋为何在此时推荐 / 背后的产业方向信号 / 与学术界趋势的关联 / 对从业者价值
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字 (旗舰级文章充分展开)
🎯 实践启发: 1-3 条对 AI 工程师 / 产业架构师 / 创业者的具体行动建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 配什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.62 chaspark 混合设计 (V37.9.51 rss_blogs 同款 Opportunity Radar #2 模板) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability / 工程实践)
   ⭐⭐⭐    = 一般 AI/产业趋势 (可借鉴但非核心, 如新模型架构 / 训练技巧 / benchmark / 通用产业新闻)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯材料学 / 单一硬件)
   ⭐      = 完全无关 (噪声, 比如纯娱乐 / 非科技活动)

⚠️ 严格约束 (违反则整份输出作废):
- 仅使用上方提供的标题 / 类型 / 来源板块 / 领域标签信息, 严禁虚构作者未提及的事实/数据/链接
- 如标题信息不足以判断深度, 标⭐较低 + 写"基于标题与板块推断"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine / Agent runtime 提供有价值的借鉴", 而非泛泛"AI 相关"
- 严禁推断华为 / 茶思屋 / 任何公司的具体内部状态除非原文标题/标签明确提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文标题: <你的翻译>

🔑 核心要点:
- 要点1
- 要点2

💡 关键洞察:
<段落, 长度按上述评级规则>

🎯 实践启发:
- 启发1
- 启发2

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: <场景描述>

🎚️ 项目对齐度: ⭐⭐⭐ / <一句话原因, ≤ 30 字>

---

"""
prompt += f"原标题: {title}\n"
if col_type:
    prompt += f"类型: {col_type}\n"
if slot:
    prompt += f"来源板块: {slot}\n"
if domains:
    prompt += f"领域标签: {domains}\n"
if url:
    prompt += f"链接: {url}\n"
# V37.9.62: append LEVEL_4 反幻觉守卫 (V37.9.57 MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "Phase A: 调用 LLM 分析篇 $((i+1))/$TOP_COUNT"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        # 成功
        $PYTHON3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        # 全 retry 失败 → 标 degraded, 不阻塞其他篇
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: 篇 $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        $PYTHON3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定 Phase A 整体 status (V37.9.36 fail-fast 契约保留) ──────────
if [ "$TOTAL_FAILED" -eq "$TOP_COUNT" ]; then
    # Phase A 全部失败 → fail-fast (V37.9.36 同款)
    log "ERROR: Phase A 全部 $TOP_COUNT 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "Phase A 全部 $TOP_COUNT 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$ARTICLE_COUNT" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: Phase A $TOTAL_FAILED/$TOP_COUNT 篇失败 — 走 partial_degraded (失败篇标 [LLM_DEGRADED] + KB title fallback)"
    send_alert "Phase A $TOTAL_FAILED/$TOP_COUNT 篇 LLM 部分失败 (其余正常推送, 失败篇标 [LLM_DEGRADED])"
fi

# ── V37.9.62 Phase B: 跨域分析 LLM 调用 (chaspark 特色保留) ─────────
# 输入: Phase A 5 篇 6 字段结果 + Top 5 标题列表
# 输出: 200-300 字"跨文章关联洞察 + 行动建议", 不结构化
# 失败不杀脚本 (Phase A 已成功 → 仍可推送)
log "Phase B: 跨文章关联洞察 LLM 调用"

PHASEB_PROMPT_FILE="$CACHE/phaseb_prompt.txt"
$PYTHON3 - "$TOP_FILE" "$RESULTS_FILE" << 'PYEOF' > "$PHASEB_PROMPT_FILE"
import sys, json, re

top_file, results_file = sys.argv[1:3]
with open(top_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# 从每篇 Phase A 输出提取 alignment 行 + cn_title (简概要)
def extract_alignment_line(content):
    for line in content.split('\n'):
        s = line.lstrip()
        if s.startswith('🎚️') or s.startswith('🎚'):
            return s.strip()
    return '(未提取)'

def extract_cn_title(content):
    for line in content.split('\n'):
        s = line.lstrip()
        if s.startswith('📌'):
            m = re.match(r'.*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)', s)
            if m:
                return m.group(1).strip()
    return ''

prompt = """你是华为黄大年茶思屋资深产业分析师。以下是 Chaspark 今日 Top 5 文章 + 每篇的项目对齐评分概要:

"""
for i, a in enumerate(articles):
    domains = '，'.join(a.get('domains', [])) or '综合'
    result = results[i] if i < len(results) else None
    cn_title = ''
    align_line = '(未生成 / Phase A 失败)'
    if result and not result.get('failed'):
        cn_title = extract_cn_title(result.get('content', ''))
        align_line = extract_alignment_line(result.get('content', ''))
    prompt += f"文章 {i+1}: 【{a['title']}】"
    if cn_title:
        prompt += f" (中文: {cn_title})"
    prompt += f"\n  类型: {a.get('type', '')} | 板块: {a.get('slot', '')} | 领域: {domains}\n"
    prompt += f"  {align_line}\n\n"

prompt += """请输出 **跨文章关联洞察** (200-300 字, 自由文本不结构化, 不重复逐篇分析):

聚焦讨论:
1. 这 Top 5 之间的隐藏关联 (技术栈交叉 / 产业链上下游 / 趋势共振)
2. 从中能看出华为 / 学术界本周押注哪个方向
3. 与 AI Agent / 本体论 / LLM 工程化 / control plane 大趋势的呼应
4. 1-2 条对技术从业者本周的具体行动建议

⚠️ 严格约束:
- 仅使用上方提供的标题 / 类型 / 板块 / 领域信息, 严禁虚构原文未提及的事实
- 严禁推断华为 / 茶思屋 / 任何公司的具体内部状态除非标签明确提及
- 严禁说 "Top 5 文章证明 OpenClaw 项目方向正确" 之类的迎合性句子
"""

# V37.9.62 LEVEL_4 反幻觉守卫
import os
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

# Phase B 1 次 LLM 调用 (max_tokens=1000, 不带 retry 简化, 失败 → log WARN 但不杀脚本)
PHASEB_PAYLOAD="$CACHE/phaseb_payload.json"
$PYTHON3 -c "
import json
prompt = open('$PHASEB_PROMPT_FILE', encoding='utf-8').read()
with open('$PHASEB_PAYLOAD', 'w', encoding='utf-8') as f:
    json.dump({
        'model': 'Qwen3-235B-A22B-Instruct-2507-W8A8',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 1000,
        'temperature': 0.3
    }, f)
"

PHASEB_RESP_FILE="$CACHE/phaseb_resp.txt"
curl -s --max-time 90 -H "Content-Type: application/json" \
    -d "@$PHASEB_PAYLOAD" "$PROXY_URL" > "$PHASEB_RESP_FILE" 2>/dev/null || true

PHASEB_CONTENT=$($PYTHON3 -c "
import json, sys
try:
    d = json.load(open('$PHASEB_RESP_FILE', encoding='utf-8'))
    if isinstance(d, dict) and 'error' in d:
        print('')
        sys.exit(0)
    print(d['choices'][0]['message']['content'])
except Exception:
    print('')
" 2>/dev/null)

if [ -z "${PHASEB_CONTENT// }" ]; then
    log "WARN: Phase B 跨域分析失败 (LLM 无响应或解析失败) — Phase A 仍推送, Phase B 段标记 degraded"
    PHASEB_CONTENT="⚠️ [PHASE_B_DEGRADED] 跨文章关联洞察生成失败 — 请直接查看上方 Top 5 各篇分析"
    PHASEB_FAILED=true
else
    log "Phase B 成功: $(echo "$PHASEB_CONTENT" | wc -c | tr -d ' ') 字"
    PHASEB_FAILED=false
fi

# ── 组装消息: Phase A per-article + Phase B 跨域分析 + 高对齐统计 ───
MSG_FILE="$CACHE/chaspark_message.txt"
PHASEB_CONTENT_EXPORTED="$PHASEB_CONTENT" $PYTHON3 - "$TOP_FILE" "$RESULTS_FILE" "$DAY" "$ARTICLE_COUNT" "$MSG_FILE" "$ALL_ARTICLES" << 'PYEOF'
import sys, json, re, os  # V37.9.62: os 用于 lazy import project_alignment_scorer (V37.9.51 同款)

top_file, results_file, day, article_count, msg_file, all_articles_file = sys.argv[1:7]
phaseb_content = os.environ.get('PHASEB_CONTENT_EXPORTED', '')

with open(top_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]
with open(all_articles_file, encoding='utf-8') as f:
    all_articles = [json.loads(l) for l in f if l.strip()]

# V37.9.62 6 字段 key-based parser (V37.9.51 rss_blogs 同款)
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
        # 跳过分隔符
        if re.match(r'^[-=*_]{3,}$', line.strip()):
            continue

        # 📌 中文标题
        if line.lstrip().startswith('📌'):
            flush()
            current_field = 'cn_title'
            current_buffer = []
            m = re.match(r'.*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)', line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # 🔑 核心要点
        if line.lstrip().startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        # 💡 关键洞察
        if line.lstrip().startswith('💡'):
            flush()
            current_field = 'insight'
            current_buffer = []
            continue
        # 🎯 实践启发
        if line.lstrip().startswith('🎯'):
            flush()
            current_field = 'practice'
            current_buffer = []
            continue
        # 🎚️ 项目对齐度 (V37.9.62 含 🎚 fallback)
        stripped = line.lstrip()
        if stripped.startswith('🎚️') or stripped.startswith('🎚'):
            flush()
            current_field = 'alignment'
            current_buffer = []
            m = re.match(r'.*🎚️?\s*(?:项目)?对齐度?\s*[:：]?\s*(.*)', stripped)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # ⭐ 评级
        if line.lstrip().startswith('⭐') and current_field != 'rating':
            if '评级' in line or '推荐场景' in line or re.match(r'\s*⭐+\s*$', line):
                flush()
                current_field = 'rating'
                current_buffer = [line.lstrip()]
                continue
        # 普通行 → append 到 current_field
        if current_field is not None:
            current_buffer.append(line)
        elif line.strip():
            pass  # 字段头之前的非空行 → 静默丢弃

    flush()
    return fields


msg_lines = [f"🏠 茶思屋深度分析 ({day}) | {article_count} 篇新内容", ""]

# V37.9.62: lazy import project_alignment_scorer + load concepts (V37.9.51 同款 rule_check)
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
    print("[chaspark] V37.9.62 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[chaspark] V37.9.62 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0

# ── Phase A emit: per-article × Top N ────────────────────────────
for i, article in enumerate(articles):
    domains = '，'.join(article.get('domains', [])) or '综合'
    msg_lines.append(f"*文章{i+1}: {article['title']}*")
    msg_lines.append(f"类型: {article.get('type', '')} | 板块: {article.get('slot', '')} | 领域: {domains}")
    if article.get('url'):
        msg_lines.append(f"链接: {article['url']}")
    msg_lines.append("")

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 用 KB title fallback (V37.9.51 同款最低保障)
        degraded_count += 1
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 仅提供标题与领域元数据供参考")
        msg_lines.append(f"原标题: {article.get('title', '')}")
        if article.get('domains'):
            msg_lines.append(f"领域标签: {domains} (可结合此判断重要性)")
        msg_lines.append("")
    else:
        # 解析 6 字段
        fields = parse_6field_output(result.get('content', ''))
        if fields['cn_title']:
            msg_lines.append(f"📌 中文标题: {fields['cn_title']}")
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
        # 🎚️ 项目对齐度展示 + rule_check 验证
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = title + domains (chaspark 无 abstract, 用 domains 元数据)
                        rule_content = article.get('title', '') + ' ' + ' '.join(article.get('domains', []))
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[chaspark] V37.9.62 rule_check 失败 article={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        # 至少保证有 cn_title 才算 LLM 解析成功
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("━━━━━━━━━━━━━━━━━━━━")
    msg_lines.append("")

# ── Phase B emit: 跨文章关联洞察 (chaspark 特色保留) ──────────────
msg_lines.append("📊 跨文章关联洞察")
msg_lines.append("")
msg_lines.append(phaseb_content)
msg_lines.append("")
msg_lines.append("━━━━━━━━━━━━━━━━━━━━")
msg_lines.append("")

# ── 末尾: 全量列表 + 高对齐统计 ────────────────────────────────
top_n = len(articles)
if top_n > 0:
    msg_lines.append(f"📈 高对齐统计: ⭐≥4 {high_alignment_count}/{top_n} 篇 (Top {top_n} 深度分析)")
    msg_lines.append("")

# 全量列表 (Top N 之外的其他文章)
if int(article_count) > top_n:
    msg_lines.append(f"📚 其余 {int(article_count) - top_n} 篇新内容 (未深度分析):")
    for i, a in enumerate(all_articles[top_n:15], top_n+1):
        domains = '，'.join(a.get('domains', [])) or '综合'
        msg_lines.append(f"  {i}. 【{a['title']}】[{a.get('type', '')}] ({domains})")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[chaspark] 消息组装完成: Phase A {top_n} 篇 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── KB 归档 ──────────────────────────────────────────────────────────
KB_CONTENT="# 茶思屋深度分析 $DAY

$(cat "$MSG_FILE")

---
来源: Chaspark API (www.chaspark.com)
采集时间: ${TS}
版本: V37.9.62 (per-article 6 字段 + Phase B 跨域分析混合设计)"

if [ -x "$KB_WRITE_SCRIPT" ] || [ -f "$KB_WRITE_SCRIPT" ]; then
    echo "$KB_CONTENT" | bash "$KB_WRITE_SCRIPT" --title "茶思屋深度分析 $DAY" --tags "chaspark,华为,科技前沿"
    log "KB 写入完成"
fi

if [ -f "$KB_APPEND_SCRIPT" ]; then
    SLOT_TAG="11:00"
    echo "$KB_CONTENT" | bash "$KB_APPEND_SCRIPT" "$KB_SRC" "$SLOT_TAG"
fi

# ── 推送 (WhatsApp + Discord 双通道) ─────────────────────────────────
WA_MSG="$(cat "$MSG_FILE")"

if [ "$NOTIFY_LOADED" = true ]; then
    notify "$WA_MSG" --topic daily
    log "推送完成 (WhatsApp + Discord)"
    WA_SENT=true
else
    log "WARN: notify.sh 未加载, fallback 直接 openclaw send"
    "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$WA_MSG" --json >/dev/null 2>&1 && WA_SENT=true || WA_SENT=false
fi

# ── 状态记录 ───────────────────────────────────────────────────────
if [ "$WA_SENT" = "true" ]; then
    if [ "$TOTAL_FAILED" -gt 0 ] || [ "${PHASEB_FAILED:-false}" = "true" ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"top_n":%d,"phase_a_failed":%d,"phase_b_failed":%s,"sent":true}\n' \
            "$TS" "$ARTICLE_COUNT" "$TOP_COUNT" "$TOTAL_FAILED" "${PHASEB_FAILED:-false}" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"top_n":%d,"sent":true}\n' \
            "$TS" "$ARTICLE_COUNT" "$TOP_COUNT" > "$STATUS_FILE"
    fi
else
    printf '{"time":"%s","status":"send_failed","new":%d,"top_n":%d,"sent":false}\n' \
        "$TS" "$ARTICLE_COUNT" "$TOP_COUNT" > "$STATUS_FILE"
fi

log "完成: Phase A $TOP_COUNT 篇 (失败 $TOTAL_FAILED) + Phase B 跨域分析 (${PHASEB_FAILED:-false}=failed)"

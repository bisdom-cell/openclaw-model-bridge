#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# ArXiv AI论文监控 v1 — 脚本控制格式，替代 --announce 模式
# 每3小时整点 HKT 由系统crontab触发（与 HN 错开45分钟）
# 合并原 monitor-arxiv-ai-models + kb-save-arxiv 两个 openclaw cron 任务
# 设计原则：结构化数据(作者/链接/日期)由XML提取，LLM只负责翻译+评价
#
# V37.9.43 fail-fast 升级 (V37.9.39 S2 / V37.9.40 DBLP+AI Leaders X / V37.9.41 HN 同款机械迁移):
#   - source notify.sh + send_alert() helper ([SYSTEM_ALERT] 前缀走 Discord #alerts)
#   - LLM 三层检测 (HTTP error / parse fail / empty content)
#   - per-paper 独立 LLM 调用 + retry 5/10/20s × 3 (替代单次 batch + 占位符 fallback)
#   - 5 字段深度 prompt (📌 标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 / ⭐ 评级)
#   - LLM_DEGRADED fallback 用 arxiv abstract 兜底 (替代 V37.9.36 占位符反模式)
#   - 多窗口切片 (>8000 字 + sleep 1s + [i/N] + 续段, V37.9.21 同款)
#
# V37.9.51 6 字段 + rule_check 升级 (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2 模板):
#   - 第 6 字段 🎚️ 项目对齐度 (⭐ × N + 一句话原因, OpenClaw 5 档评分指南)
#   - parse_5field_output → parse_6field_output (新增 alignment 字段)
#   - lazy import project_alignment_scorer (rule_check FAIL-OPEN)
#   - rule_check: LLM ⭐ vs keyword 一致性, 偏离时显示 ⚠️ <reason>
#   - 末尾追加"本轮高对齐论文 ⭐≥4: N/M 篇"统计 (Stage 2 PoC 简化版)
#   - 三档 status: ok / partial_degraded / llm_failed (全失败 → exit 1 fail-fast)
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

# 防重叠执行（mkdir 原子锁，macOS 兼容）
LOCK="/tmp/arxiv_monitor.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[arxiv] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/arxiv_monitor"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/arxiv_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
LLM_RAW="$CACHE/llm_raw_last.txt"
MAX_PAPERS=10
MAX_AGE_DAYS=14

ARXIV_URL="https://export.arxiv.org/api/query?search_query=ti:LLM+OR+ti:%22Large+Language+Model%22+OR+ti:%22AI+Agent%22+OR+ti:RAG+OR+ti:RLHF+OR+ti:Multimodal+OR+ti:DeepSeek+OR+ti:Gemini+OR+ti:ChatGPT+OR+ti:GPT-4+OR+ti:GPT-5+OR+ti:Claude+OR+ti:Llama+OR+ti:Mistral+OR+ti:Qwen+OR+ti:Ontology+OR+ti:%22Knowledge+Graph%22+OR+ti:%22Neuro-Symbolic%22+OR+ti:%22Knowledge+Representation%22+OR+ti:%22Symbolic+AI%22&sortBy=submittedDate&sortOrder=descending&max_results=50"

TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] arxiv: $1" >&2; }

# V37.9.43: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5 kb_review / V37.8.10 kb_evening / V37.9.16 kb_deep_dive /
#  V37.9.36-37 rss_blogs / V37.9.39 S2 / V37.9.40 DBLP+AI Leaders X /
#  V37.9.41 HN 同款模式)
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

# V37.9.43: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] arxiv_monitor LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 arxiv 论文精选 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# ArXiv AI论文监控" > "$KB_SRC"
DAY="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d')"

# ── 1. 抓取 ArXiv API XML（含重试 + 429退避 + 内容验证）─────────────────
FEED_FILE="$CACHE/arxiv_feed.xml"
FETCH_OK=false
HEADER_FILE="$CACHE/curl_headers.txt"
for attempt in 1 2 3; do
  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "User-Agent: openclaw-arxiv-monitor/1.0 (mailto:bisdom@example.com)" \
    -D "$HEADER_FILE" \
    "$ARXIV_URL" -o "$FEED_FILE" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

  # 检查 HTTP 状态码
  if [ "$HTTP_CODE" != "200" ]; then
    log "WARN: ArXiv API 返回 HTTP ${HTTP_CODE}（第${attempt}次）"
    # 429 专用退避：尊重 Retry-After 头，否则用指数退避（30s/90s/270s）
    if [ "$HTTP_CODE" = "429" ]; then
      RETRY_AFTER=$(grep -i '^Retry-After:' "$HEADER_FILE" 2>/dev/null | head -1 | tr -dc '0-9')
      if [ -n "$RETRY_AFTER" ] && [ "$RETRY_AFTER" -gt 0 ] 2>/dev/null; then
        WAIT="$RETRY_AFTER"
      else
        WAIT="$((30 * 3 ** (attempt - 1)))"  # 30s, 90s, 270s
      fi
      log "429 退避等待 ${WAIT}s（第${attempt}次）"
      sleep "$WAIT"
    else
      sleep "$((attempt * 10))"
    fi
    continue
  fi

  # 验证内容是 XML（ArXiv 限流时返回 HTML 错误页）
  if head -5 "$FEED_FILE" | grep -q '<feed\|<?xml'; then
    FETCH_OK=true
    break
  else
    log "WARN: ArXiv API 返回非XML内容（第${attempt}次）: $(head -1 "$FEED_FILE" | cut -c1-80)"
    sleep "$((attempt * 10))"
    continue
  fi
done

if [ "$FETCH_OK" != "true" ]; then
  log "ERROR: ArXiv API 3次重试均失败（最后HTTP=${HTTP_CODE}）"
  printf '{"time":"%s","status":"fetch_failed","new":0,"http_code":"%s"}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
  exit 1
fi
echo "[arxiv] XML抓取完成（HTTP ${HTTP_CODE}）"

# ── 2. 解析XML → 结构化JSONL（标题/作者/日期/ID/摘要）─────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"
if ! python3 - "$FEED_FILE" "$MAX_AGE_DAYS" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import os  # V37.9.57: read HG_LEVEL_4_TEXT env var
import sys, json, re, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

feed_file = sys.argv[1]
max_age = int(sys.argv[2])
max_papers = int(sys.argv[3])
seen_file = sys.argv[4]
new_ids_file = sys.argv[5]
cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)

# Load previously sent paper IDs
with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

NS = {"a": "http://www.w3.org/2005/Atom"}
tree = ET.parse(feed_file)
root = tree.getroot()

count = 0
new_ids = []
for entry in root.findall("a:entry", NS):
    published = entry.findtext("a:published", "", NS)
    if not published:
        continue
    try:
        pub_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        continue
    if pub_date < cutoff:
        continue

    title = " ".join((entry.findtext("a:title", "", NS) or "").split())
    if not title:
        continue

    # Extract arxiv ID (e.g., http://arxiv.org/abs/2503.12345v1 → 2503.12345)
    entry_id = entry.findtext("a:id", "", NS)
    arxiv_id = entry_id.split("/abs/")[-1] if "/abs/" in entry_id else ""
    arxiv_id = re.sub(r'v\d+$', '', arxiv_id)

    # Skip already sent papers
    if arxiv_id in seen_ids:
        continue

    # First author
    authors = entry.findall("a:author", NS)
    first_author = authors[0].findtext("a:name", "", NS) if authors else "Unknown"

    # Abstract (truncate for LLM prompt)
    abstract = " ".join((entry.findtext("a:summary", "", NS) or "").split())[:300]

    date_str = published[:10]

    print(json.dumps({
        "title": title,
        "arxiv_id": arxiv_id,
        "first_author": first_author,
        "date": date_str,
        "abstract": abstract
    }, ensure_ascii=False))

    new_ids.append(arxiv_id)
    count += 1
    if count >= max_papers:
        break

# Write new IDs to separate file (NOT seen_file — only mark seen after successful push)
with open(new_ids_file, 'w') as f:
    for aid in new_ids:
        f.write(aid + '\n')

print(f"[arxiv] 解析完成: {count} 篇新论文（跳过 {len(seen_ids)} 篇已发送）", file=sys.stderr)
PYEOF
then
  log "WARN: XML解析失败"
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

PAPER_COUNT="$(wc -l < "$PAPERS_FILE" | tr -d ' ')"
if [ "$PAPER_COUNT" -eq 0 ]; then
    log "无新论文（全部已发送或过去${MAX_AGE_DAYS}天无结果），跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[arxiv] 新论文: ${PAPER_COUNT} 篇"

# ── 3-4. V37.9.43: 每篇独立调 LLM (5 字段深度分析 + 按评级动态调长度 + retry 3 次) ─
# 老 V37.8: 单次调用全部 N 篇 + 3 字段输出 + 失败硬编码占位符 (V37.9.36 反模式)
# 新 V37.9.43: 每篇独立调用 + 独立 retry (5s/10s/20s) + 5 字段深度
#   📌 中文标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 / ⭐ 评级
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败篇标 [LLM_DEGRADED] + arxiv 摘要 fallback

LLM_RAW="$CACHE/llm_raw_last.txt"   # 兼容: 保留上一次失败响应做 forensic
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单篇 LLM 调用 + retry ───────────────────────────────────
# 输入: $1 = single_paper_prompt 文件路径, $2 = paper_idx
# 输出: stdout = 成功时 LLM content; 失败 → return 1, 全局 LAST_LLM_FAIL_REASON 含原因
call_llm_single_with_retry() {
    local prompt_file="$1"
    local idx="$2"
    LAST_LLM_FAIL_REASON=""
    local backoffs=(5 10 20)

    for attempt in 0 1 2; do
        local payload_file="$CACHE/llm_payload_${idx}_a${attempt}.json"
        python3 -c "
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

        # 保存最后一次响应做 forensic (覆盖式)
        echo "$llm_resp" > "$LLM_RAW"

        # V37.9.36 三层检测 (HTTP error / parse fail / empty content)
        local parse_err_file="$CACHE/llm_parse_${idx}_a${attempt}.err"
        local parse_out
        parse_out=$(echo "$llm_resp" | python3 -c "
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

    return 1
}

# ── 主循环: 每篇论文独立调 LLM (5 字段深度) ──────────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""
TOTAL_NEW="$PAPER_COUNT"

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    python3 - "$PAPERS_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT (line ~409) NameError fix

papers_file, idx = sys.argv[1], int(sys.argv[2])
papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))
p = papers[idx]
title = p['title']
date_str = p.get('date', '')
abstract = p.get('abstract', '')[:600]

prompt = """你是 AI 论文深度分析师 (兼 OpenClaw 项目对齐评估师)。对以下 ArXiv 论文输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (技术术语保持精确)
🔑 核心贡献: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出论文做了什么 / 解决了什么问题 / 关键创新
💡 关键方法: 揭示论文方法论 / 实验设计 / 与已有工作对比 / 结果对比 / 局限性
   长度按评级动态调整: ⭐⭐⭐ 写约 100-150 字 / ⭐⭐⭐⭐ 写约 250-400 字 / ⭐⭐⭐⭐⭐ 写约 500-800 字 (旗舰论文充分展开)
🎯 实践启发: 1-3 条对 AI 工程师 / 研究者 / 架构师的具体行动建议, 每条 ≤ 80 字
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
- 只使用上方提供的标题和摘要中的信息, 严禁虚构论文未提及的事实/数据/链接
- 如摘要不足以判断, 标⭐较低 + 写"基于摘要的初步判断"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态除非原文提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文标题: <你的翻译>

🔑 核心贡献:
- 贡献1
- 贡献2

💡 关键方法:
<段落, 长度按上述评级规则>

🎯 实践启发:
- 启发1
- 启发2

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: <场景描述>

🎚️ 项目对齐度: ⭐⭐⭐ / <一句话原因, ≤ 30 字>

---

"""
prompt += f"论文标题: {title}\n"
if date_str:
    prompt += f"发表日期: {date_str}\n"
if abstract:
    prompt += f"论文摘要:\n{abstract}\n"
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "调用 LLM 分析篇 $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        # 成功
        python3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        # 全 retry 失败 → 标 degraded, 不阻塞其他篇
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: 篇 $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        python3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定整体 status (V37.9.36 fail-fast 契约保留) ──────────────────
if [ "$TOTAL_FAILED" -eq "$TOTAL_NEW" ]; then
    # 全部失败 → fail-fast (V37.9.36 同款)
    log "ERROR: 全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 篇失败 — 走 partial_degraded (失败篇标 [LLM_DEGRADED] + arxiv 摘要 fallback)"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 篇 LLM 部分失败 (其余正常推送, 失败篇标 [LLM_DEGRADED])"
fi
echo "[arxiv] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── 5. V37.9.43: 5 字段 emit (5-field key-based parser + LLM_DEGRADED fallback + 多窗口切片) ──
MSG_FILE="$CACHE/arxiv_message.txt"
python3 - "$PAPERS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.51: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款)

papers_file, results_file, day, msg_file = sys.argv[1:5]

papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.51 6 字段 key-based parser (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2)
# 容忍 LLM 输出的字段顺序、单字段缺失、prefix 变体
def parse_6field_output(content):
    """从 LLM 输出解析 6 字段, key-based + tolerant.

    返回 dict: cn_title / highlights / insight / practice / rating / alignment
    V37.9.51: alignment 字段新增 (Opportunity Radar #2 PoC, V37.9.45/V37.9.50 同款模板)
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

        # 字段头识别 (key-based, 不依赖位置)
        # 📌 中文标题
        if line.lstrip().startswith('📌'):
            flush()
            current_field = 'cn_title'
            current_buffer = []
            m = re.match(r'.*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)', line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # 🔑 核心贡献 (论文场景)
        if line.lstrip().startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        # 💡 关键方法 (论文场景)
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
            pass

    flush()
    return fields


msg_lines = [f"📚 今日arXiv精选 ({day})", ""]

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
    print("[arxiv] V37.9.51 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[arxiv] V37.9.51 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.51: ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, paper in enumerate(papers):
    title = paper['title']
    arxiv_id = paper.get('arxiv_id', '')
    first_author = paper.get('first_author', 'Unknown')
    date_str = paper.get('date', '')
    link = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ''

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 用 arxiv abstract 给用户最低保障 (替代 V37.9.36 占位符反模式)
        degraded_count += 1
        msg_lines.append(f"*{title}*")
        author_meta = f"作者: {first_author} 等"
        if date_str:
            author_meta += f" | 日期: {date_str}"
        msg_lines.append(author_meta)
        if link:
            msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 论文摘要供参考:")
        fallback = paper.get('abstract', '')
        fallback = fallback[:300] if fallback else ''
        if fallback:
            msg_lines.append(fallback)
        else:
            msg_lines.append("(arxiv 无摘要数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        # V37.9.51: 解析 6 字段 (V37.9.45 hf_papers / V37.9.50 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or title
        msg_lines.append(f"*{title_display}*")
        author_meta = f"作者: {first_author} 等"
        if date_str:
            author_meta += f" | 日期: {date_str}"
        msg_lines.append(author_meta)
        if link:
            msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        if fields['highlights']:
            msg_lines.append("🔑 核心贡献:")
            msg_lines.append(fields['highlights'])
            msg_lines.append("")
        if fields['insight']:
            msg_lines.append("💡 关键方法:")
            msg_lines.append(fields['insight'])
            msg_lines.append("")
        if fields['practice']:
            msg_lines.append("🎯 实践启发:")
            msg_lines.append(fields['practice'])
            msg_lines.append("")
        if fields['rating']:
            # rating 行不重复加 ⭐ 前缀 (LLM 输出已含)
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
                        # rule_content = title + abstract (V37.9.43 fallback 用 abstract, 这里同款)
                        rule_content = paper.get('title', '') + ' ' + paper.get('abstract', '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[arxiv] V37.9.51 rule_check 失败 paper={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        # 至少保证有 cn_title 才算 LLM 解析成功
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.51: 末尾追加高对齐统计 (Opportunity Radar #2)
total_papers = len(papers)
if total_papers > 0:
    msg_lines.append(f"━━━ 本轮高对齐论文 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_papers} 篇 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[arxiv] 消息组装完成: {len(papers)} 篇 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 6. 推送 WhatsApp + Discord (V37.9.21/V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段发) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    # 单段直发 (≤4000 不折叠 / 4000-8000 客户端自动折叠 2 气泡, V37.9.35 已验证)
    MSG_CONTENT="$(cat "$MSG_FILE")"
    # V37.9.171 PathB-2: 主推/分块走 notify.sh（微信 + Discord #papers + 重试/队列）
    if notify "$MSG_CONTENT" --topic papers 2>"$SEND_ERR"; then
        log "已推送 ${PAPER_COUNT} 篇 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    # 总长 >8000 → 多窗口切片 (V37.9.21 同款 mktemp + sleep 1s 防乱序)
    WA_CHUNK_DIR=$(mktemp -d)
    # V37.9.86: 合并 lock cleanup 防 bash trap override (lockdir 残留血案)
    trap 'rmdir "$LOCK" 2>/dev/null; rm -rf "$WA_CHUNK_DIR"' EXIT INT TERM

    python3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$DAY" << 'PYEOF'
import sys, os, re

msg_file, chunk_dir, day = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(msg_file, encoding='utf-8').read()
MAX_CHUNK = 4000

# 按 "\n---\n" 切分文章块, 第一块是 header "📚 今日arXiv精选 (date)"
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
            chunk = chunk.replace(f"📚 今日arXiv精选 ({day})",
                                  f"📚 今日arXiv精选 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"📚 今日arXiv精选 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
    with open(os.path.join(chunk_dir, f"{i:03d}.txt"), 'w', encoding='utf-8') as f:
        f.write(chunk)
PYEOF

    WA_PARTS_TOTAL=$(ls "$WA_CHUNK_DIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
    WA_SENT_OK=0
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        CHUNK_CONTENT="$(cat "$chunk_file")"
        if notify "$CHUNK_CONTENT" --topic papers 2>>"$SEND_ERR"; then
            WA_SENT_OK=$((WA_SENT_OK + 1))
        fi
        sleep 1  # 防 WhatsApp 消息乱序 (V37.9.21 契约)
    done
    log "已推送 ${PAPER_COUNT} 篇 (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
    if [ "$WA_SENT_OK" -gt 0 ]; then
        WA_SENT=true
    fi
fi

if [ "$WA_SENT" = "true" ]; then
    if [ -f "$NEW_IDS_FILE" ]; then
        cat "$NEW_IDS_FILE" >> "$SEEN_FILE"
        log "已标记 ${PAPER_COUNT} 篇为已发送"
    fi
    # status 区分 ok / partial_degraded
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$PAPER_COUNT" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
fi

# ── 6.5 Ontology 论文单独推送到 Discord #ontology ─────────────────────────
# 检测标题/摘要命中 ontology 关键词的论文，额外推送到 #ontology 频道
ONTO_MSG_FILE="$CACHE/ontology_papers.txt"
ONTO_FILTER="$(dirname "$0")/../ontology_filter.py"
if [ -f "$ONTO_FILTER" ]; then
    python3 "$ONTO_FILTER" "$PAPERS_FILE" "$DAY" "$ONTO_MSG_FILE" "$MSG_FILE" 2>"$CACHE/onto_filter.err" || true
    if [ -s "$ONTO_MSG_FILE" ]; then
        ONTO_CONTENT="$(head -c 4000 "$ONTO_MSG_FILE")"
        ONTO_COUNT=$(grep -c '^\*' "$ONTO_MSG_FILE" || true)
        # 使用 notify.sh 统一推送（带重试+错误捕获+队列）
        if [ -f "${HOME}/notify.sh" ]; then
            source "${HOME}/notify.sh"
            notify "$ONTO_CONTENT" --channel discord --topic ontology
        else
            log "WARN: notify.sh not found, falling back to direct send"
            "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ONTOLOGY:-}" --message "$ONTO_CONTENT" --json 2>"$CACHE/onto_discord.err" || true
        fi
        log "Ontology论文推送到Discord #ontology: ${ONTO_COUNT} 篇"
    fi
fi

# ── 7. KB归档（合并原 kb-save-arxiv 功能）──────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M')
    CONTENT="# ArXiv AI论文监控 ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "arxiv-ai-models" "note" 2>/dev/null || true
    echo "[arxiv] KB写入完成"
fi

# ── 8. 永久归档到 sources ───────────────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 9. 清理seen缓存（保留最近500条，防无限增长）────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
    echo "[arxiv] seen缓存已裁剪至300条"
fi

# ── 10. rsync备份 ───────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

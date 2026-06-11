#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Semantic Scholar 热门论文监控 v1
# 每天 1 次（08:00 HKT）由系统 crontab 触发
# 与 ArXiv/HF 互补：S2 提供引用量数据，发现"爆款论文"
# 使用 Semantic Scholar Academic Graph API (免费，1 req/sec)
# 搜索多个 AI 关键词，按 citationCount 降序，去重取 top N
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
LOCK="/tmp/semantic_scholar.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[s2] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/semantic_scholar"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/semantic_scholar_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"
S2_API="https://api.semanticscholar.org/graph/v1/paper/search"
# V37.9.132: +openAccessPdf — 无 arxiv 版本但开放获取的论文 (期刊/跨学科常见)
# 取 OA PDF 直链, kb_deep_dive 的 .pdf 后缀路径可抓全文 (否则必然降级摘要级)
FIELDS="title,authors,abstract,url,citationCount,publicationDate,externalIds,tldr,openAccessPdf"
# 搜索最近 30 天的论文（引用量排序更有意义）
DATE_FROM="$(TZ=Asia/Hong_Kong date -v-30d '+%Y-%m-%d' 2>/dev/null || date -d '30 days ago' '+%Y-%m-%d')"
DATE_TO="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"

log() { echo "[$TS] s2: $1" >&2; }

# V37.9.39: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5 kb_review / V37.8.10 kb_evening / V37.9.16 kb_deep_dive /
#  V37.9.36-37 rss_blogs 同款模式)
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

# V37.9.39: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] semantic_scholar LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 S2 高引论文精选 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# Semantic Scholar AI论文" > "$KB_SRC"

# ── 1. 多关键词搜索 + 合并去重 ──────────────────────────────────────
# 搜索多个关键词，每个取 20 篇，合并后按引用量排序
# V37.8.13: 关键词 12→6 (S2 免费 API daily/session limit 收紧，04-16 全部 12 个 429)
# + 间隔 5s→30s 更温和规避限流。保留核心 AI 关键词 + 1 个 ontology 关键词。
# V37.9.135: 关键词 6→12 恢复 (unfinished #30 兑现) — S2_API_KEY 认证模式已稳定
# (2026-06-11 Mac Mini log 核实 6/8 起每天 11:00 认证 2s 间隔零 429), 补回
# V37.8.13 砍掉的 6 个 ontology/KR 方向关键词 (与 jobs/dblp/run_dblp.sh 同源
# 12 关键词集对齐, V30.5 同期上线 + V37.1 同时加 ontology 方向).
# 认证模式 12 关键词 × 2s = 24s; 无 key FAIL-OPEN 30s 间隔 × 12 = 6min (老版本同款).
KEYWORDS=("large language model" "LLM agent" "RAG retrieval augmented" "multimodal AI" "RLHF alignment" "ontology knowledge graph" "neuro-symbolic reasoning" "enterprise ontology" "formal ontology information systems" "description logic OWL" "semantic web linked data" "knowledge representation reasoning")
RAW_DIR="$CACHE/raw"
mkdir -p "$RAW_DIR"

FETCH_ERRORS=0
# V37.9.98: Semantic Scholar API key 集成 (unfinished #2 候选兑现).
# 有 S2_API_KEY → 认证模式 (独占 1 RPS 不抢匿名池, 规避 V37.8.13 起的 429 daily limit, 5/27-5/28
# 连续 6 关键词 429 全失败). FAIL-OPEN: 无 key → 无认证模式 (保守 30s 间隔, 当前行为不变).
# 申请免费 key: https://www.semanticscholar.org/product/api (即时发放).
# bash 3.2 兼容: 脚本 set -eo (无 -u), 空数组 "${arr[@]}" 安全展开.
S2_CURL_AUTH=()
if [ -n "${S2_API_KEY:-}" ]; then
  S2_CURL_AUTH=(-H "x-api-key: $S2_API_KEY")
  S2_KW_SLEEP=2   # V37.9.99: 2s 安全余量 (S2 邮件确认限额 1 RPS 且要求"设到阈值以下"; V37.9.135 12 关键词共 ~24s)
  log "S2 API key 已配置 (认证模式, 间隔 ${S2_KW_SLEEP}s)"
else
  S2_KW_SLEEP=30
  log "S2 API key 未配置 (无认证模式, 保守 ${S2_KW_SLEEP}s 间隔规避 429; 申请见 semanticscholar.org/product/api)"
fi
for i in "${!KEYWORDS[@]}"; do
  KW="${KEYWORDS[$i]}"
  OUTFILE="$RAW_DIR/search_${i}.json"
  ENCODED_KW=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$KW'))")

  # V37.8.13: 关键词间隔 30s（原 5s 触发 S2 429 daily limit）
  # V37.9.98: 有 S2_API_KEY 时 1s, 无 key 保持 30s 保守 (FAIL-OPEN)
  [ "$i" -gt 0 ] && sleep "$S2_KW_SLEEP"

  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' "${S2_CURL_AUTH[@]}" \
    -H "User-Agent: openclaw-s2-monitor/1.0" \
    "${S2_API}?query=${ENCODED_KW}&fields=${FIELDS}&limit=20&publicationDateOrYear=${DATE_FROM}:${DATE_TO}&fieldsOfStudy=Computer+Science" \
    -o "$OUTFILE" 2>"$CACHE/curl_s2.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    echo "[s2] 搜索 '$KW' 成功"
  elif [ "$HTTP_CODE" = "429" ]; then
    # 指数退避重试：60s → 120s
    for RETRY in 60 120; do
      log "WARN: S2 API 429 for '$KW'，等待 ${RETRY}s 重试"
      sleep "$RETRY"
      HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' "${S2_CURL_AUTH[@]}" \
        -H "User-Agent: openclaw-s2-monitor/1.0" \
        "${S2_API}?query=${ENCODED_KW}&fields=${FIELDS}&limit=20&publicationDateOrYear=${DATE_FROM}:${DATE_TO}&fieldsOfStudy=Computer+Science" \
        -o "$OUTFILE" 2>"$CACHE/curl_s2.err") || HTTP_CODE="000"
      [ "$HTTP_CODE" = "200" ] && break
    done
    if [ "$HTTP_CODE" != "200" ]; then
      log "WARN: S2 重试仍失败 ($HTTP_CODE) for '$KW'"
      FETCH_ERRORS=$((FETCH_ERRORS + 1))
    else
      echo "[s2] 搜索 '$KW' 成功（重试后）"
    fi
  else
    log "WARN: S2 API 返回 HTTP $HTTP_CODE for '$KW'"
    FETCH_ERRORS=$((FETCH_ERRORS + 1))
  fi
done

if [ "$FETCH_ERRORS" -ge "${#KEYWORDS[@]}" ]; then
  log "ERROR: 所有关键词搜索均失败"
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# ── 2. 合并 + 去重 + 按引用量排序 → JSONL ────────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! python3 - "$RAW_DIR" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import os  # V37.9.57: read HG_LEVEL_4_TEXT env var
import sys, json, os, glob

raw_dir = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

# 合并所有搜索结果
all_papers = {}
for fpath in sorted(glob.glob(os.path.join(raw_dir, "search_*.json"))):
    try:
        with open(fpath) as f:
            data = json.load(f)
        for paper in data.get("data", []):
            pid = paper.get("paperId", "")
            if not pid or pid in seen_ids or pid in all_papers:
                continue
            title = (paper.get("title") or "").strip()
            if not title:
                continue
            all_papers[pid] = paper
    except (json.JSONDecodeError, KeyError):
        continue

# 按引用量降序
sorted_papers = sorted(all_papers.values(),
                       key=lambda x: x.get("citationCount", 0) or 0,
                       reverse=True)[:max_papers]

new_ids = []
for paper in sorted_papers:
    pid = paper.get("paperId", "")
    authors = paper.get("authors", [])
    first_author = authors[0].get("name", "Unknown") if authors else "Unknown"
    abstract = ((paper.get("abstract") or "")[:300])
    tldr = ""
    if paper.get("tldr") and isinstance(paper["tldr"], dict):
        tldr = paper["tldr"].get("text", "")
    citations = paper.get("citationCount", 0) or 0
    pub_date = paper.get("publicationDate", "") or ""
    ext_ids = paper.get("externalIds", {}) or {}
    arxiv_id = ext_ids.get("ArXiv", "")
    url = paper.get("url", "")
    # V37.9.132: openAccessPdf.url — 出版商/仓储 OA PDF 直链 (可能为 None)
    oa_pdf = (paper.get("openAccessPdf") or {}).get("url", "") or ""

    out = {
        "paper_id": pid,
        "title": paper["title"],
        "first_author": first_author,
        "date": pub_date,
        "abstract": abstract,
        "tldr": tldr,
        "citations": citations,
        "arxiv_id": arxiv_id,
        "url": url,
        "oa_pdf": oa_pdf
    }
    print(json.dumps(out, ensure_ascii=False))
    new_ids.append(pid)

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

print(f"[s2] 合并去重完成: {len(sorted_papers)} 篇（总搜索 {len(all_papers)}，跳过 {len(seen_ids)} 已发送）", file=sys.stderr)
PYEOF
then
  log "WARN: 解析失败"
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

PAPER_COUNT="$(wc -l < "$PAPERS_FILE" | tr -d ' ')"
if [ "$PAPER_COUNT" -eq 0 ]; then
    log "无新论文（全部已发送），跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[s2] 新论文: ${PAPER_COUNT} 篇"

# ── 3-4. V37.9.39: 每篇独立调 LLM (5 字段深度分析 + 按评级动态调长度 + retry 3 次) ─
# 老 V37.8: 单次调用全部 N 篇 + 3 字段输出 + 失败硬编码占位符 (V37.9.36 反模式)
# 新 V37.9.39: 每篇独立调用 + 独立 retry (5s/10s/20s) + 5 字段深度
#   📌 中文标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 / ⭐ 评级
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败篇标 [LLM_DEGRADED] + S2 摘要 fallback

LLM_RAW="$CACHE/llm_raw_last.txt"   # 兼容: 保留上一次失败响应做 forensic
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单篇 LLM 调用 + retry ───────────────────────────────────
# 输入: $1 = single_paper_prompt 文件路径, $2 = paper_idx
# 输出: stdout = 成功时 LLM content; 失败 → exit 1, 全局 LAST_LLM_FAIL_REASON 含原因
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
import sys, json, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT (line ~398) NameError fix

papers_file, idx = sys.argv[1], int(sys.argv[2])
papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))
p = papers[idx]
title = p['title']
citations = p.get('citations', 0)
abstract = p.get('abstract', '')[:600]
tldr = p.get('tldr', '')
summary = tldr if tldr else abstract

prompt = """你是 AI 论文深度分析师 (兼 OpenClaw 项目对齐评估师)。
对以下论文输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (技术术语保持精确)
🔑 核心贡献: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出论文做了什么 / 解决了什么问题 / 关键创新
💡 关键方法: 揭示论文方法论 / 实验设计 / 与已有工作对比 / 结果对比 / 局限性
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字 (旗舰论文充分展开)
🎯 实践启发: 1-3 条对 AI 工程师 / 研究者 / 架构师的具体行动建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 用于什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.50 新增 (V37.9.45 hf_papers 同款 Opportunity Radar #2 模板, 用于过滤 OpenClaw 高价值信号) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 任务)
   ⭐      = 完全无关 (噪声, 比如硬件细节 / GPU kernel / 单纯学术 paper)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的标题和摘要中的信息, 严禁虚构论文未提及的事实/数据/链接
- 引用量已知 (作为热度参考但不影响学术价值评级)
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
prompt += f"引用次数: {citations}\n"
if summary:
    prompt += f"论文摘要:\n{summary}\n"
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
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 篇失败 — 走 partial_degraded (失败篇标 [LLM_DEGRADED] + S2 摘要 fallback)"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 篇 LLM 部分失败 (其余正常推送, 失败篇标 [LLM_DEGRADED])"
fi
echo "[s2] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── V37.9.39: 5 字段 emit (5-field key-based parser + LLM_DEGRADED fallback + 多窗口切片) ──
MSG_FILE="$CACHE/s2_message.txt"
python3 - "$PAPERS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.50-hotfix: os 用于 lazy import project_alignment_scorer 路径解析

papers_file, results_file, day, msg_file = sys.argv[1:5]

papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.50 6 字段 key-based parser (V37.9.45 hf_papers 同款 Opportunity Radar #2)
# 容忍 LLM 输出的字段顺序、单字段缺失、prefix 变体
def parse_6field_output(content):
    """从 LLM 输出解析 6 字段, key-based + tolerant.

    返回 dict: cn_title / highlights / insight / practice / rating / alignment
    V37.9.50: alignment 字段新增 (Opportunity Radar #2 PoC, V37.9.45 hf_papers 同款模板)
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
        # 🔑 核心贡献 (论文场景: 不是"核心要点")
        if line.lstrip().startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        # 💡 关键方法 (论文场景: 不是"关键洞察")
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
        # 🎚️ 项目对齐度 (V37.9.50 新增, fallback 🎚 if no variation selector)
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


msg_lines = [f"📈 S2 高引论文精选 ({day})", ""]

# V37.9.50: lazy import project_alignment_scorer + load concepts (V37.9.45 hf_papers 同款 rule_check)
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
    print("[s2] V37.9.50 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[s2] V37.9.50 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.50: ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, paper in enumerate(papers):
    citations = paper.get('citations', 0)
    arxiv_id = paper.get('arxiv_id', '')
    url = paper.get('url', '')
    oa_pdf = paper.get('oa_pdf', '')
    # V37.9.132 方案 A 链接链: arxiv > OA PDF 直链 (.pdf 结尾才用, 保证
    # kb_deep_dive endswith('.pdf') 路径可抓全文且用户点开即 PDF) > S2 页面.
    # 背景: 无 arxiv 版本的论文 (如期刊/医学 KG 类) 原 fallback S2 页面 URL
    # 无法派生 PDF → deep_dive 必然摘要级 (2026-06-11 用户视角发现 34/45 摘要级)
    if arxiv_id:
        link = f"https://arxiv.org/abs/{arxiv_id}"
    elif oa_pdf and oa_pdf.lower().endswith('.pdf'):
        link = oa_pdf
    else:
        link = url
    first_author = paper.get('first_author', 'Unknown')

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 用 S2 摘要给用户最低保障 (替代 V37.9.36 占位符反模式)
        degraded_count += 1
        msg_lines.append(f"*{paper['title']}*")
        msg_lines.append(f"作者: {first_author} 等 | 引用: {citations}")
        msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 论文摘要供参考:")
        # 优先 tldr (S2 native), 否则 abstract
        fallback = paper.get('tldr') or paper.get('abstract', '')
        fallback = fallback[:300] if fallback else ''
        if fallback:
            msg_lines.append(fallback)
        else:
            msg_lines.append("(S2 无摘要数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        # V37.9.50: 解析 6 字段 (V37.9.45 hf_papers 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or paper['title']
        msg_lines.append(f"*{title_display}*")
        msg_lines.append(f"作者: {first_author} 等 | 引用: {citations}")
        msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        if fields['cn_title'] and fields['cn_title'] != title_display:
            # cn_title 已经在标题行展示, 这里不重复
            pass
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
        # V37.9.50: 🎚️ 项目对齐度展示 + rule_check 验证 (V37.9.45 hf_papers 同款)
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            # rule_check: LLM ⭐ 评分 vs keyword-based rule 一致性
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # content = title + tldr/abstract (V37.9.47 hf_papers 同款)
                        rule_content = paper.get('title', '') + ' ' + (paper.get('tldr') or paper.get('abstract', ''))
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[s2] V37.9.50 rule_check 失败 paper={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        # 至少保证有 cn_title 才算 LLM 解析成功
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.50: 末尾追加高对齐统计 (Opportunity Radar #2)
total_papers = len(papers)
if total_papers > 0:
    msg_lines.append(f"━━━ 本轮高对齐论文 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_papers} 篇 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[s2] 消息组装完成: {len(papers)} 篇 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 推送 WhatsApp + Discord (V37.9.21/V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段发) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    # 单段直发 (≤4000 不折叠 / 4000-8000 客户端自动折叠 2 气泡, V37.9.35 已验证)
    MSG_CONTENT="$(cat "$MSG_FILE")"
    if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${PAPER_COUNT} 篇 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_PAPERS:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
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

# 按 "\n---\n" 切分文章块, 第一块是 header "📈 S2 高引论文精选 (date)"
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
            chunk = chunk.replace(f"📈 S2 高引论文精选 ({day})",
                                  f"📈 S2 高引论文精选 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"📈 S2 高引论文精选 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
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
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_PAPERS:-}" --message "$CHUNK_CONTENT" --json >/dev/null 2>&1 || true
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

# ── 7. KB归档 ────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# Semantic Scholar AI论文 ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "semantic-scholar-ai" "note" 2>/dev/null || true
    echo "[s2] KB写入完成"
fi

# ── 8. 永久归档 ──────────────────────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 9. 清理seen缓存 ─────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 1000 ]; then
    tail -500 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
    echo "[s2] seen缓存已裁剪至500条"
fi

# ── 10. rsync备份 ────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

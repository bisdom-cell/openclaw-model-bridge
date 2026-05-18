#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Hugging Face Daily Papers 监控 v1
# 每天 2 次（10:00, 20:00 HKT）由系统 crontab 触发
# 与 ArXiv 互补：ArXiv 全量撒网，HF 社区精选（高 upvotes = 高关注度）
# 设计：JSON API 提取结构化数据，LLM 只负责翻译+评价
#
# V37.9.45 fail-fast 升级 (V37.9.39 S2 / V37.9.40 DBLP+AI Leaders X /
#   V37.9.41 HN / V37.9.43 arxiv / V37.9.44 github_trending 同款机械迁移)
#   + Opportunity Radar #2 PoC (6 字段, 加 🎚️ 项目对齐度):
#   - source notify.sh + send_alert() helper ([SYSTEM_ALERT] 前缀走 Discord #alerts)
#   - LLM 三层检测 (HTTP error / parse fail / empty content)
#   - per-paper 独立 LLM 调用 + retry 5/10/20s × 3 (替代单次 batch + 占位符 fallback)
#   - 6 字段深度 prompt (📌 标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 /
#                       ⭐ 评级 / 🎚️ 项目对齐度 ← V37.9.45 新增)
#   - LLM_DEGRADED fallback 用 abstract 兜底 (替代 V37.9.36 占位符反模式)
#   - 多窗口切片 (>8000 字 + sleep 1s + [i/N] + 续段, V37.9.21 同款)
#   - 三档 status: ok / partial_degraded / llm_failed (全失败 → exit 1 fail-fast)
#   - HF-specific: 保留 Step 2.5 GitHub repo enrichment + emit 显示 github metadata
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
LOCK="/tmp/hf_papers.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[hf_papers] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/hf_papers"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/hf_papers_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] hf_papers: $1" >&2; }

# V37.9.45: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5 kb_review / V37.8.10 kb_evening / V37.9.16 kb_deep_dive /
#  V37.9.36-37 rss_blogs / V37.9.39 S2 / V37.9.40 DBLP+AI Leaders X /
#  V37.9.41 HN / V37.9.43 arxiv / V37.9.44 github_trending 同款模式)
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

# V37.9.45: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] hf_papers LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 HF 社区精选论文 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# Hugging Face Daily Papers" > "$KB_SRC"

# ── 1. 抓取 HF Daily Papers API ──────────────────────────────────────
FEED_FILE="$CACHE/hf_papers.json"
HEADER_FILE="$CACHE/curl_headers.txt"
FETCH_OK=false
for attempt in 1 2 3; do
  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "User-Agent: openclaw-hf-monitor/1.0" \
    -D "$HEADER_FILE" \
    "https://huggingface.co/api/daily_papers?limit=50&sort=trending" \
    -o "$FEED_FILE" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    # 验证 JSON
    if python3 -c "import json; json.load(open('$FEED_FILE'))" 2>/dev/null; then
      FETCH_OK=true
      break
    else
      log "WARN: HF API 返回非JSON内容（第${attempt}次）"
    fi
  else
    log "WARN: HF API 返回 HTTP $HTTP_CODE（第${attempt}次）"
  fi

  if [ "$HTTP_CODE" = "429" ]; then
    RETRY_AFTER=$(grep -i '^Retry-After:' "$HEADER_FILE" 2>/dev/null | head -1 | tr -dc '0-9')
    WAIT="${RETRY_AFTER:-$((30 * attempt))}"
    log "429 退避等待 ${WAIT}s"
    sleep "$WAIT"
  else
    sleep "$((attempt * 10))"
  fi
done

if [ "$FETCH_OK" != "true" ]; then
  log "ERROR: HF API 3次重试均失败（最后HTTP=$HTTP_CODE）"
  printf '{"time":"%s","status":"fetch_failed","new":0,"http_code":"%s"}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
  exit 1
fi
echo "[hf_papers] API抓取完成（HTTP 200）"

# ── 2. 解析JSON → 筛选高upvote论文 → 去重 ────────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! python3 - "$FEED_FILE" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import os  # V37.9.57: read HG_LEVEL_4_TEXT env var
import sys, json

feed_file = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

with open(feed_file) as f:
    data = json.load(f)

# HF Daily Papers API 返回列表，每项含 paper 对象
papers = []
for item in data:
    paper = item.get("paper", item)
    paper_id = paper.get("id", "")
    if not paper_id or paper_id in seen_ids:
        continue

    title = paper.get("title", "").strip()
    if not title:
        continue

    # 提取信息
    authors = paper.get("authors", [])
    first_author = ""
    if authors:
        if isinstance(authors[0], dict):
            first_author = authors[0].get("name", authors[0].get("user", {}).get("fullname", "Unknown"))
        else:
            first_author = str(authors[0])
    first_author = first_author or "Unknown"

    abstract = (paper.get("summary", "") or "")[:300]
    upvotes = item.get("paper", {}).get("upvotes", item.get("upvotes", 0))
    published = paper.get("publishedAt", paper.get("createdAt", ""))[:10]

    papers.append({
        "paper_id": paper_id,
        "title": title,
        "first_author": first_author,
        "date": published,
        "abstract": abstract,
        "upvotes": upvotes
    })

# 按 upvotes 降序排列，取 top N
papers.sort(key=lambda x: x.get("upvotes", 0), reverse=True)
papers = papers[:max_papers]

new_ids = []
for p in papers:
    print(json.dumps(p, ensure_ascii=False))
    new_ids.append(p["paper_id"])

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

skipped = len(seen_ids)
print(f"[hf_papers] 解析完成: {len(papers)} 篇新论文（跳过 {skipped} 篇已发送）", file=sys.stderr)
PYEOF
then
  log "WARN: JSON解析失败"
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

PAPER_COUNT="$(wc -l < "$PAPERS_FILE" | tr -d ' ')"
if [ "$PAPER_COUNT" -eq 0 ]; then
    log "无新论文（全部已发送），跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[hf_papers] 新论文: ${PAPER_COUNT} 篇"

# ── 2.5 通过 GitHub Search 查找论文关联的代码仓库 (HF-specific 保留) ──
ENRICHED_FILE="$CACHE/papers_enriched.jsonl"
python3 - "$PAPERS_FILE" "$ENRICHED_FILE" << 'PYEOF'
import sys, json, urllib.request, urllib.error, urllib.parse, time

papers_file = sys.argv[1]
enriched_file = sys.argv[2]

papers = []
with open(papers_file) as f:
    for line in f:
        line = line.strip()
        if line:
            papers.append(json.loads(line))

for p in papers:
    pid = p.get("paper_id", "")
    if not pid:
        continue
    # 用 ArXiv ID 搜索 GitHub 仓库（官方实现通常在 README 中引用论文链接）
    query = urllib.parse.quote(f"{pid} in:readme")
    url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=3"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "openclaw-hf-monitor/1.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("items", [])
        if items:
            best = items[0]
            p["github_url"] = best.get("html_url", "")
            p["github_stars"] = best.get("stargazers_count", 0)
            p["github_desc"] = (best.get("description", "") or "")[:80]
            p["github_lang"] = best.get("language", "")
            p["repo_count"] = data.get("total_count", len(items))
        time.sleep(3)  # GitHub 未认证限速 10次/分钟，间隔3s安全
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        print(f"[hf_papers] WARN: GitHub搜索 {pid} 失败: {e}", file=sys.stderr)

with open(enriched_file, 'w') as f:
    for p in papers:
        f.write(json.dumps(p, ensure_ascii=False) + '\n')

has_code = sum(1 for p in papers if p.get("github_url"))
print(f"[hf_papers] GitHub仓库查找完成: {has_code}/{len(papers)} 篇有代码", file=sys.stderr)
PYEOF
if [ -f "$ENRICHED_FILE" ]; then
    mv "$ENRICHED_FILE" "$PAPERS_FILE"
fi

# ── 3-4. V37.9.45: 每 paper 独立调 LLM (6 字段深度分析 + 项目对齐度评分 + retry 3 次) ─
# 老 V37.8: 单次调用全部 N 篇 + 3 字段输出 + 失败硬编码占位符 (V37.9.36 反模式)
# 新 V37.9.45: 每 paper 独立调用 + 独立 retry (5s/10s/20s) + 6 字段深度
#   📌 中文标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 / ⭐ 评级
#   🎚️ 项目对齐度 (V37.9.45 新增, Opportunity Radar #2 PoC)
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败篇标 [LLM_DEGRADED] + abstract 兜底

LLM_RAW="$CACHE/llm_raw_last.txt"   # 兼容: 保留上一次失败响应做 forensic
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单 paper LLM 调用 + retry ───────────────────────────────
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
            log "WARN: paper $idx attempt $((attempt+1))/3: $LAST_LLM_FAIL_REASON"
            if [ $attempt -lt 2 ]; then
                sleep "${backoffs[$attempt]}"
            fi
            continue
        fi

        if [ -z "${parse_out// }" ]; then
            LAST_LLM_FAIL_REASON="empty_content"
            log "WARN: paper $idx attempt $((attempt+1))/3: empty content"
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

# ── 主循环: 每 paper 独立调 LLM (6 字段深度 + 项目对齐度) ──────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""
TOTAL_NEW="$PAPER_COUNT"

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    python3 - "$PAPERS_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT (line ~445) NameError fix

papers_file, idx = sys.argv[1], int(sys.argv[2])
papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            papers.append(json.loads(line))
p = papers[idx]
title = p['title']
upvotes = p.get('upvotes', 0)
abstract = p.get('abstract', '')[:600]
date_str = p.get('date', '')

prompt = """你是 AI 论文深度分析师 (兼 OpenClaw 项目对齐评估师)。
对以下 HF 社区精选论文输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (技术术语保持精确)
🔑 核心贡献: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出论文做了什么 / 解决了什么问题 / 关键创新
💡 关键方法: 揭示论文方法论 / 实验设计 / 与已有工作对比 / 结果对比 / 局限性
   长度按评级动态调整: ⭐⭐⭐ 写约 100-150 字 / ⭐⭐⭐⭐ 写约 250-400 字 / ⭐⭐⭐⭐⭐ 写约 500-800 字 (旗舰论文充分展开)
🎯 实践启发: 1-3 条对 AI 工程师 / 研究者 / 架构师的具体行动建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 用于什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.45 新增: Opportunity Radar #2 PoC, 用于过滤 OpenClaw 项目高价值信号 ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 任务)
   ⭐      = 完全无关 (噪声, 比如硬件细节 / GPU kernel / 单纯学术 paper)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的标题和摘要中的信息, 严禁虚构论文未提及的事实/数据/链接
- HF upvotes 已知 (作为社区关注度参考但不影响学术价值评级)
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
prompt += f"HF Upvotes: {upvotes}"
if date_str:
    prompt += f" | 发表日期: {date_str}"
prompt += "\n"
if abstract:
    prompt += f"论文摘要:\n{abstract}\n"
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    # V37.9.76 Capability Router (shadow 模式): 每 paper LLM 调用前记录路由决策, 不改实际路由行为
    # 一周观察 ~/.kb/router_decisions.jsonl 数据反馈调 cap_score, V37.9.77+ 评估 enforcement
    # FAIL-OPEN: router_decide.py 缺失/异常不阻塞 caller LLM 调用
    if [ -x "$HOME/router_decide.py" ]; then
        ROUTER_CHOICE=$(python3 "$HOME/router_decide.py" --job-id hf_papers --task per_paper 2>/dev/null || true)
    elif [ -f "$(dirname "$0")/../../router_decide.py" ]; then
        ROUTER_CHOICE=$(python3 "$(dirname "$0")/../../router_decide.py" --job-id hf_papers --task per_paper 2>/dev/null || true)
    else
        ROUTER_CHOICE=""
    fi
    log "调用 LLM 分析 paper $((i+1))/$TOTAL_NEW (V37.9.76 router shadow: chosen=${ROUTER_CHOICE:-unknown})"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        # 成功
        python3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        # 全 retry 失败 → 标 degraded, 不阻塞其他 paper
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: paper $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
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
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 篇失败 — 走 partial_degraded (失败篇标 [LLM_DEGRADED] + abstract 兜底)"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 篇 LLM 部分失败 (其余正常推送, 失败篇标 [LLM_DEGRADED])"
fi
echo "[hf_papers] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── 5. V37.9.45: 6 字段 emit (6-field key-based parser + LLM_DEGRADED + 多窗口切片) ──
MSG_FILE="$CACHE/hf_message.txt"
python3 - "$PAPERS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os

papers_file, results_file, day, msg_file = sys.argv[1:5]

# V37.9.47 Stage 2: lazy import project_alignment_scorer for rule_check
# 部署在 $HOME (auto_deploy FILE_MAP) 或 dev 仓库根. FAIL-OPEN: 缺模块 → 跳过验证.
sys.path.insert(0, os.path.expanduser("~"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    import project_alignment_scorer as _pas
    _alignment_concepts = _pas.load_project_concepts()
    _alignment_available = bool(_alignment_concepts.get("core_planes"))
except Exception as _e:
    print(f"[hf_papers] WARN: project_alignment_scorer unavailable, skip rule_check: {_e}",
          file=sys.stderr)
    _alignment_available = False
    _alignment_concepts = None
    _pas = None

papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            papers.append(json.loads(line))
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.45: 6 字段 key-based parser (V37.9.39 5 字段扩展, 加 🎚️ 项目对齐度)
# V37.8.7 ontology_parser 同款模式: 容忍 LLM 输出字段顺序错乱 / 单字段缺失 / prefix 变体
def parse_6field_output(content):
    """从 LLM 输出解析 6 字段, key-based + tolerant.

    返回 dict: cn_title / highlights / insight / practice / rating / alignment
    新增字段 alignment (V37.9.45 PoC, 项目对齐度评分)
    """
    fields = {
        'cn_title': '',
        'highlights': '',
        'insight': '',
        'practice': '',
        'rating': '',
        'alignment': '',  # V37.9.45 新增字段
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
        # 🔑 核心贡献
        if line.lstrip().startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        # 💡 关键方法
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
        # 🎚️ 项目对齐度 (V37.9.45 新增, 必须在 ⭐ 检测之前避免 ⭐ 评分干扰)
        if line.lstrip().startswith('🎚️') or line.lstrip().startswith('🎚'):
            flush()
            current_field = 'alignment'
            current_buffer = []
            # 提取行内的剩余内容 (如 "🎚️ 项目对齐度: ⭐⭐⭐⭐ / 直接相关")
            m = re.match(r'.*🎚️?\s*(?:项目)?对齐度?\s*[:：]?\s*(.*)', line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # ⭐ 评级 (current_field != rating 才进入, 避免与 alignment 段冲突)
        if line.lstrip().startswith('⭐') and current_field not in ('rating', 'alignment'):
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


msg_lines = [f"\U0001F525 HF社区精选论文 ({day})", ""]

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.45 ⭐≥4 项目对齐高的统计

for i, paper in enumerate(papers):
    title = paper['title']
    paper_id = paper.get('paper_id', '')
    upvotes = paper.get('upvotes', 0)
    first_author = paper.get('first_author', 'Unknown')
    date_str = paper.get('date', '')
    paper_url = f"https://huggingface.co/papers/{paper_id}" if paper_id else ''

    # GitHub 代码仓库 (HF-specific 保留, V37.9.45 不动)
    github_url = paper.get('github_url', '')
    github_stars = paper.get('github_stars', 0)
    github_lang = paper.get('github_lang', '')

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 用 abstract 给用户最低保障 (替代 V37.9.36 占位符反模式)
        degraded_count += 1
        msg_lines.append(f"*{title}*")
        msg_lines.append(f"作者: {first_author} 等 | \U0001F44D {upvotes}")
        if paper_url:
            msg_lines.append(f"论文: {paper_url}")
        if github_url:
            badge_parts = [f"⭐ {github_stars}"]
            if github_lang:
                badge_parts.append(github_lang)
            msg_lines.append(f"代码: {github_url} ({' | '.join(badge_parts)})")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 论文摘要供参考:")
        fallback = paper.get('abstract', '')
        fallback = fallback[:300] if fallback else ''
        if fallback:
            msg_lines.append(fallback)
        else:
            msg_lines.append("(HF 无摘要数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        # 解析 6 字段
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or title
        msg_lines.append(f"*{title_display}*")
        msg_lines.append(f"作者: {first_author} 等 | \U0001F44D {upvotes}")
        if paper_url:
            msg_lines.append(f"论文: {paper_url}")
        if github_url:
            badge_parts = [f"⭐ {github_stars}"]
            if github_lang:
                badge_parts.append(github_lang)
            msg_lines.append(f"代码: {github_url} ({' | '.join(badge_parts)})")
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
            msg_lines.append(fields['rating'])
            msg_lines.append("")
        # V37.9.45 新增: 项目对齐度展示 / V37.9.47 Stage 2: 加 rule_check 验证
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            # V37.9.47 Stage 2: rule_check 验证 LLM 评分 vs keyword 命中
            if _alignment_available:
                _llm_stars = _pas.extract_star_count(fields['alignment'])
                if _llm_stars > 0:
                    # paper content = title + tldr + abstract (足够 keyword match)
                    _paper_content = (paper.get('title', '') + ' ' +
                                      paper.get('tldr', '') + ' ' +
                                      paper.get('abstract', ''))
                    _validation = _pas.validate_alignment_score(
                        _paper_content, _llm_stars, _alignment_concepts)
                    _marker = _pas.format_validation_marker(_validation)
                    if _marker:
                        msg_lines.append(_marker)
            msg_lines.append("")
            # 统计 ⭐≥4 (粗略匹配 4-5 颗星, V37.9.47 Stage 2 验证已加 rule_check 精确化)
            if '⭐⭐⭐⭐' in fields['alignment']:  # 4 或 5 颗星都含 ⭐⭐⭐⭐
                high_alignment_count += 1
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.45 PoC: 末尾加项目对齐度统计 (Stage 2 时加专门 Top 5 段)
if high_alignment_count > 0:
    msg_lines.append(f"🎯 本轮高对齐论文 (项目对齐度 ⭐≥4): {high_alignment_count}/{len(papers)} 篇")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[hf_papers] 消息组装完成: {len(papers)} 篇 (LLM 解析 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 6. 推送 WhatsApp + Discord (V37.9.21/V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段发) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    # 单段直发 (≤4000 不折叠 / 4000-8000 客户端自动折叠 2 气泡, V37.9.35 已验证)
    MSG_CONTENT="$(cat "$MSG_FILE")"
    if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${PAPER_COUNT} 篇论文 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_PAPERS:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    # 总长 >8000 → 多窗口切片 (V37.9.21 同款 mktemp + sleep 1s 防乱序)
    WA_CHUNK_DIR=$(mktemp -d)
    trap 'rm -rf "$WA_CHUNK_DIR"' EXIT INT TERM

    python3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$DAY" << 'PYEOF'
import sys, os, re

msg_file, chunk_dir, day = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(msg_file, encoding='utf-8').read()
MAX_CHUNK = 4000

# 按 "\n---\n" 切分论文块, 第一块是 header "🔥 HF社区精选论文 (date)"
blocks = re.split(r'\n---\n', content)
header_block = blocks[0]
paper_blocks = [b for b in blocks[1:] if b.strip()]

chunks = []
current = header_block
for block in paper_blocks:
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
            chunk = chunk.replace(f"\U0001F525 HF社区精选论文 ({day})",
                                  f"\U0001F525 HF社区精选论文 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"\U0001F525 HF社区精选论文 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
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
    log "已推送 ${PAPER_COUNT} 篇论文 (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
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

# ── 7. KB归档 ────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# HF Daily Papers ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "hf-daily-papers" "note" 2>/dev/null || true
    echo "[hf_papers] KB写入完成"
fi

# ── 8. 永久归档 ──────────────────────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 9. 清理seen缓存 ─────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
    echo "[hf_papers] seen缓存已裁剪至300条"
fi

# ── 10. rsync备份 ────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

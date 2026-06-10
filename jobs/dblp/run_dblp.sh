#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# 加载环境变量（cron 环境中 OPENCLAW_PHONE/DISCORD_CH_* 等必须从 profile 获取）
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true
# DBLP CS论文索引监控 v1 (V37.9.51 — 6 字段 + rule_check 升级,
# V37.9.40 深度 5 字段基础 + V37.9.45 hf_papers / V37.9.50 semantic_scholar
# 同款 Opportunity Radar #2 模板横向迁移, Sub-Stage 4b 2/6)
# 每天 12:00 HKT 由系统 crontab 触发
# 搜索 AI 相关关键词，按年份过滤当年论文，去重推送
# 使用 DBLP Search API (免费，CC0 开放数据，无需认证)
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
LOCK="/tmp/dblp.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[dblp] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/dblp"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/dblp_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"
YEAR="$(TZ=Asia/Hong_Kong date '+%Y')"

DBLP_API="https://dblp.org/search/publ/api"

log() { echo "[$TS] dblp: $1" >&2; }

# V37.9.40: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5/V37.8.10/V37.9.16/V37.9.36-37/V37.9.39 同款模式)
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

# V37.9.40: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT]
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] dblp LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 DBLP CS 论文精选 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# DBLP CS论文索引" > "$KB_SRC"

# ── 1. 多关键词搜索 ─────────────────────────────────────────────────
KEYWORDS=("large language model" "LLM agent" "multimodal foundation model" "retrieval augmented generation" "RLHF alignment" "ontology knowledge graph" "neuro-symbolic reasoning" "enterprise ontology" "formal ontology information systems" "description logic OWL" "semantic web linked data" "knowledge representation reasoning")
RAW_DIR="$CACHE/raw"
mkdir -p "$RAW_DIR"

FETCH_ERRORS=0
for i in "${!KEYWORDS[@]}"; do
  KW="${KEYWORDS[$i]}"
  OUTFILE="$RAW_DIR/search_${i}.json"
  ENCODED_KW=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$KW'))")

  # DBLP 建议请求间隔 >=1s
  sleep 2

  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "User-Agent: openclaw-dblp-monitor/1.0" \
    "${DBLP_API}?q=${ENCODED_KW}&format=json&h=50&f=0" \
    -o "$OUTFILE" 2>"$CACHE/curl_dblp.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    echo "[dblp] 搜索 '$KW' 成功"
  elif [ "$HTTP_CODE" = "429" ]; then
    log "WARN: DBLP 429 for '$KW'，等待 60s 重试"
    sleep 60
    HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
      -H "User-Agent: openclaw-dblp-monitor/1.0" \
      "${DBLP_API}?q=${ENCODED_KW}&format=json&h=50&f=0" \
      -o "$OUTFILE" 2>"$CACHE/curl_dblp.err") || HTTP_CODE="000"
    [ "$HTTP_CODE" != "200" ] && FETCH_ERRORS=$((FETCH_ERRORS + 1))
  else
    log "WARN: DBLP API 返回 HTTP $HTTP_CODE for '$KW'"
    FETCH_ERRORS=$((FETCH_ERRORS + 1))
  fi
done

if [ "$FETCH_ERRORS" -ge "${#KEYWORDS[@]}" ]; then
  log "ERROR: 所有关键词搜索均失败"
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# ── 2. 合并 + 去重 + 过滤当年论文 → JSONL ───────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! python3 - "$RAW_DIR" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" "$YEAR" << 'PYEOF' > "$PAPERS_FILE"
import os  # V37.9.57: read HG_LEVEL_4_TEXT env var
import sys, json, os, glob

raw_dir = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]
current_year = sys.argv[5]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

all_papers = {}
for fpath in sorted(glob.glob(os.path.join(raw_dir, "search_*.json"))):
    try:
        with open(fpath) as f:
            data = json.load(f)
        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        if not isinstance(hits, list):
            continue
        for hit in hits:
            info = hit.get("info", {})
            if not info:
                continue

            # 用 DBLP key 作为唯一 ID（比 DOI 更稳定）
            pid = hit.get("@id", "") or info.get("key", "") or info.get("doi", "")
            if not pid or pid in seen_ids or pid in all_papers:
                continue

            title = (info.get("title") or "").strip().rstrip(".")
            if not title:
                continue

            year = str(info.get("year", ""))
            # 只保留当年和上一年的论文
            if year and year not in (current_year, str(int(current_year) - 1)):
                continue

            venue = info.get("venue", "")
            if isinstance(venue, list):
                venue = venue[0] if venue else ""

            pub_type = info.get("type", "")
            # 优先 Conference/Journal，跳过 Informal/Editorship
            if pub_type in ("Editorship", "Reference Works", "Parts in Books or Collections"):
                continue

            authors_raw = info.get("authors", {})
            author_list = []
            if isinstance(authors_raw, dict):
                auth = authors_raw.get("author", [])
                if isinstance(auth, dict):
                    auth = [auth]
                if isinstance(auth, list):
                    for a in auth:
                        if isinstance(a, dict):
                            author_list.append(a.get("text", a.get("@pid", "Unknown")))
                        else:
                            author_list.append(str(a))
            first_author = author_list[0] if author_list else "Unknown"

            doi = info.get("doi", "")
            url = info.get("ee", info.get("url", ""))
            if isinstance(url, list):
                url = url[0] if url else ""

            all_papers[pid] = {
                "paper_id": pid,
                "title": title,
                "first_author": first_author,
                "year": year,
                "venue": venue,
                "type": pub_type,
                "doi": doi,
                "url": url
            }
    except (json.JSONDecodeError, KeyError, TypeError):
        continue

# 按年份降序（新论文优先），同年按 venue 知名度粗排
sorted_papers = sorted(all_papers.values(),
                       key=lambda x: (x.get("year", ""), x.get("venue", "")),
                       reverse=True)[:max_papers]

new_ids = []
for p in sorted_papers:
    print(json.dumps(p, ensure_ascii=False))
    new_ids.append(p["paper_id"])

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

print(f"[dblp] 合并去重完成: {len(sorted_papers)} 篇（总搜索 {len(all_papers)}，跳过 {len(seen_ids)} 已发送）", file=sys.stderr)
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
echo "[dblp] 新论文: ${PAPER_COUNT} 篇"

# ── 3-4. V37.9.40: 每篇独立调 LLM (5 字段深度分析 + 按评级动态调长度 + retry 3 次) ─
# 老 V37.8: 单次调用全部 N 篇 + 3 字段输出 + 失败硬编码占位符 (V37.9.36 反模式)
# 新 V37.9.40: 每篇独立调用 + 独立 retry (5s/10s/20s) + 5 字段深度
#   📌 中文标题 / 🔑 核心贡献 / 💡 关键方法 (基于标题/venue 推断, 数据有限时显式标注) / 🎯 实践启发 / ⭐ 评级
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败篇标 [LLM_DEGRADED] + 标题/venue 兜底

LLM_RAW="$CACHE/llm_raw_last.txt"
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单篇 LLM 调用 + retry ───────────────────────────────────
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

        echo "$llm_resp" > "$LLM_RAW"

        # V37.9.36 三层检测
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
            if [ $attempt -lt 2 ]; then sleep "${backoffs[$attempt]}"; fi
            continue
        fi

        if [ -z "${parse_out// }" ]; then
            LAST_LLM_FAIL_REASON="empty_content"
            log "WARN: 篇 $idx attempt $((attempt+1))/3: empty content"
            if [ $attempt -lt 2 ]; then sleep "${backoffs[$attempt]}"; fi
            continue
        fi

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
import sys, json, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT (line ~402) NameError fix

papers_file, idx = sys.argv[1], int(sys.argv[2])
papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))
p = papers[idx]
title = p['title']
venue = p.get('venue', '')
year = p.get('year', '')
first_author = p.get('first_author', 'Unknown')

prompt = """你是 AI/CS 论文深度分析师 (兼 OpenClaw 项目对齐评估师)。对以下论文输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (技术术语保持精确)
🔑 核心贡献: 3-5 条 bullet, 每条 1 句 ≤ 60 字 (基于标题与会议推断, 不虚构)
💡 关键方法: 基于标题与发表会议的初步技术分析
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字
   注意: DBLP 不提供摘要, 此处仅基于标题/会议做合理技术推断, 必须显式标注 "(基于标题推断)"
🎯 实践启发: 1-3 条对 AI 工程师 / 研究者的具体建议, 每条 ≤ 80 字
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
- DBLP 数据仅含标题与会议, 严禁虚构论文中的具体数据 / 实验结果 / 方法细节
- 推断时必须显式标注 "基于标题推断" 或 "推测" 让用户知道置信度
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文标题: <你的翻译>

🔑 核心贡献:
- 贡献1
- 贡献2

💡 关键方法:
<段落, 长度按评级规则, 含 (基于标题推断) 标注>

🎯 实践启发:
- 启发1

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: <场景描述>

🎚️ 项目对齐度: ⭐⭐⭐ / <一句话原因, ≤ 30 字>

---

"""
prompt += f"论文标题: {title}\n"
if venue:
    prompt += f"发表会议: {venue} {year}\n"
prompt += f"第一作者: {first_author}\n"
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "调用 LLM 分析篇 $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        python3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
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
    log "ERROR: 全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 篇失败 — 走 partial_degraded (失败篇标 [LLM_DEGRADED])"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 篇 LLM 部分失败 (其余正常推送, 失败篇标 [LLM_DEGRADED])"
fi
echo "[dblp] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── V37.9.40: 5 字段 emit (5-field key-based parser + LLM_DEGRADED fallback + 多窗口切片) ──
MSG_FILE="$CACHE/dblp_message.txt"
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


msg_lines = [f"📚 DBLP CS 论文精选 ({day})", ""]

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
    print("[dblp] V37.9.51 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[dblp] V37.9.51 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.51: ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, paper in enumerate(papers):
    venue = paper.get('venue', '')
    year = paper.get('year', '')
    url = paper.get('url', '')
    doi = paper.get('doi', '')
    first_author = paper.get('first_author', 'Unknown')
    # V37.9.132 方案 A: arxiv 直链优先 > doi > 其他 — kb_deep_dive 全文 PDF 抓取
    # 只能从 arxiv/acl/.pdf URL 派生, 原 doi 优先把 ee 字段里的 arxiv 链接压掉,
    # 导致 dblp 被选中的论文必然降级到摘要级 (2026-06-11 用户视角发现, 34/45 摘要级)
    if url and "arxiv.org" in url:
        link = url
    elif doi:
        link = f"https://doi.org/{doi}"
    else:
        link = url

    meta_parts = [first_author + " 等"]
    if venue:
        meta_parts.append(venue)
    if year:
        meta_parts.append(year)

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 标题 + venue 兜底 (DBLP 无 abstract, 这是最低保障)
        degraded_count += 1
        msg_lines.append(f"*{paper['title']}*")
        msg_lines.append("  | ".join(meta_parts))
        if link:
            msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 仅提供标题+会议元数据")
        msg_lines.append(f"(DBLP 数据库不含摘要, 请直接点链接阅读全文)")
        msg_lines.append("")
    else:
        # V37.9.51: 解析 6 字段 (V37.9.45 hf_papers / V37.9.50 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or paper['title']
        msg_lines.append(f"*{title_display}*")
        msg_lines.append("  | ".join(meta_parts))
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
                        # rule_content = title + venue (DBLP 无 abstract, 用 venue 元数据作 fallback)
                        rule_content = paper.get('title', '') + ' ' + paper.get('venue', '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[dblp] V37.9.51 rule_check 失败 paper={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
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

print(f"[dblp] 消息组装完成: {len(papers)} 篇 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count})", file=sys.stderr)
PYEOF

# ── 推送 WhatsApp + Discord (V37.9.21/V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    MSG_CONTENT="$(cat "$MSG_FILE")"
    if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${PAPER_COUNT} 篇 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_PAPERS:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    WA_CHUNK_DIR=$(mktemp -d)
    trap 'rm -rf "$WA_CHUNK_DIR"; rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

    python3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$DAY" << 'PYEOF'
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
            chunk = chunk.replace(f"📚 DBLP CS 论文精选 ({day})",
                                  f"📚 DBLP CS 论文精选 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"📚 DBLP CS 论文精选 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
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
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$PAPER_COUNT" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
fi
rm -f "$SEND_ERR"

# ── 6.5 Ontology 论文单独推送到 Discord #ontology ─────────────────────────
ONTO_MSG_FILE="$CACHE/ontology_papers.txt"
ONTO_FILTER="$(dirname "$0")/../ontology_filter.py"
if [ -f "$ONTO_FILTER" ]; then
    python3 "$ONTO_FILTER" "$PAPERS_FILE" "$DAY" "$ONTO_MSG_FILE" "$MSG_FILE" 2>"$CACHE/onto_filter.err" || true
    if [ -s "$ONTO_MSG_FILE" ]; then
        ONTO_CONTENT="$(head -c 4000 "$ONTO_MSG_FILE")"
        ONTO_COUNT=$(grep -c '^\*' "$ONTO_MSG_FILE" || true)
        # 使用 notify.sh 统一推送（带重试+错误捕获+队列）
        notify "$ONTO_CONTENT" --channel discord --topic ontology
        log "Ontology论文推送到Discord #ontology: ${ONTO_COUNT} 篇"
    fi
fi

# ── 7. KB归档 ────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    bash "$KB_WRITE_SCRIPT" "# DBLP CS论文 ${DATE_KB}

${SUMMARY}" "dblp-cs" "note" 2>/dev/null || true
fi

# ── 8. 永久归档 + 清理 + rsync ──────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{ echo ""; echo "## ${DAY}"; cat "$MSG_FILE"; } | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 1000 ]; then
    tail -500 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
    echo "[dblp] seen缓存已裁剪至500条"
fi

bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

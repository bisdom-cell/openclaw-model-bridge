#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# 加载环境变量（cron 环境中 OPENCLAW_PHONE/DISCORD_CH_* 等必须从 profile 获取）
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true
# ACL Anthology NLP 顶会论文监控 (V37.9.62 — 6 字段 + rule_check 升级,
# V37.9.45 hf_papers / V37.9.50 semantic_scholar / V37.9.51 dblp 同款 Opportunity Radar #2 模板横向迁移)
# Sub-Stage 4b 续 batch 1/6 (ACL Anthology audit P3 升级 — 用户视角原则 #13 第 N 次正向兑现)
# ACL-specific: 有 abstract (与 DBLP 不同), rule_content 用 title + abstract + venue 拼接
# 每周三 09:30 HKT 由系统 crontab 触发（顶会论文按会议周期更新, ACL 学术源更新频率较低）
# 监控 ACL/EMNLP/NAACL/EACL/COLING 等 NLP 顶会
# 使用 ACL Anthology XML (https://raw.githubusercontent.com/acl-org/acl-anthology)
# MR-19 不在 scope: ACL 是 LLM-task 但 V37.9.61 framework (set -eE + ERR trap) 已覆盖所有 LLM cron
set -eo pipefail

# V37.9.62: 公共反幻觉守卫 LEVEL_4_PROJECT_AWARE (MR-8 single-source-of-truth)
# V37.9.51 dblp / V37.9.57 横向应用同款模式 — LEVEL_4 含 V37.9.56-hotfix3 具体血案字眼
# (禁"OpenClaw 社区发布"/"v26"/"[openclaw]") 防 alignment 评分"一句话原因"段编造项目动态
# FAIL-OPEN: 模块缺失 → 空字符串
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
LOCK="/tmp/acl_anthology.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[acl] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/acl_anthology"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/acl_anthology.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"
YEAR="$(TZ=Asia/Hong_Kong date '+%Y')"
PREV_YEAR="$((YEAR - 1))"

log() { echo "[$TS] acl: $1" >&2; }

# V37.9.62: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5/V37.8.10/V37.9.16/V37.9.36-37/V37.9.39/V37.9.51 dblp 同款模式)
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

# V37.9.62: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT]
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] acl_anthology LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 ACL Anthology NLP 顶会论文精选 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# ACL Anthology NLP论文" > "$KB_SRC"

# ── 1. 抓取多个 NLP 顶会的最近 volume ────────────────────────────────
# ACL Anthology volume ID 格式：{year}.{venue}-{type}
# 主要会议 XML 文件名（GitHub acl-org/acl-anthology 仓库）
# 每个文件包含该会议所有论文的结构化 XML
XML_FILES=(
  "${YEAR}.acl"
  "${YEAR}.emnlp"
  "${YEAR}.naacl"
  "${YEAR}.eacl"
  "${PREV_YEAR}.acl"
  "${PREV_YEAR}.emnlp"
  "${PREV_YEAR}.naacl"
)

RAW_DIR="$CACHE/raw"
mkdir -p "$RAW_DIR"

FETCH_OK=0
for i in "${!XML_FILES[@]}"; do
  XMLF="${XML_FILES[$i]}"
  OUTFILE="$RAW_DIR/vol_${i}.xml"

  sleep 1  # GitHub 限速友好

  # 从 GitHub 获取结构化 XML（比 HTML 抓取更可靠）
  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "User-Agent: openclaw-acl-monitor/1.0" \
    "https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/${XMLF}.xml" \
    -o "$OUTFILE" 2>"$CACHE/curl_acl.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    # 验证是否是有效 XML
    if head -5 "$OUTFILE" | grep -q '<collection\|<volume\|<?xml'; then
      echo "[acl] XML '$XMLF' 获取成功"
      FETCH_OK=$((FETCH_OK + 1))
    fi
  else
    # 很多 volume 可能还不存在（会议尚未举行），静默跳过
    :
  fi
done

if [ "$FETCH_OK" -eq 0 ]; then
  log "无可用的 ACL volume（可能会议尚未举行）"
  printf '{"time":"%s","status":"no_volumes","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 0
fi

# ── 2. 从 XML 提取论文信息 → JSONL ──────────────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! python3 - "$RAW_DIR" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import sys, os, glob, json
import xml.etree.ElementTree as ET

raw_dir = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

all_papers = {}

for fpath in sorted(glob.glob(os.path.join(raw_dir, "vol_*.xml"))):
    try:
        tree = ET.parse(fpath)
        root = tree.getroot()

        # ACL Anthology XML: <collection> → <volume id="2024.acl-long"> → <paper id="1">
        for volume in root.iter("volume"):
            vol_id = volume.get("id", "")
            # 只取 long/main/findings，跳过 short/tutorial/demo
            if vol_id and not any(t in vol_id for t in ["long", "main", "findings"]):
                continue

            for paper in volume.iter("paper"):
                paper_num = paper.get("id", "")
                paper_id = f"{vol_id}.{paper_num}" if vol_id and paper_num else ""
                if not paper_id or paper_id in seen_ids or paper_id in all_papers:
                    continue

                title_el = paper.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""
                # title 可能包含子元素（如 <fixed-case>）
                if not title and title_el is not None:
                    title = ET.tostring(title_el, encoding='unicode', method='text').strip()
                if not title:
                    continue

                # 提取第一作者
                authors = paper.findall("author")
                first_author = "Unknown"
                if authors:
                    first = authors[0].find("first")
                    last = authors[0].find("last")
                    first_name = (first.text or "") if first is not None else ""
                    last_name = (last.text or "") if last is not None else ""
                    first_author = f"{first_name} {last_name}".strip() or "Unknown"

                abstract_el = paper.find("abstract")
                abstract = ""
                if abstract_el is not None:
                    abstract = ET.tostring(abstract_el, encoding='unicode', method='text').strip()[:600]

                all_papers[paper_id] = {
                    "paper_id": paper_id,
                    "title": title,
                    "first_author": first_author,
                    "abstract": abstract,
                    "venue": vol_id
                }
    except Exception:
        continue

# 取最新的 N 篇（按 ID 倒序 = 最新的编号最大）
sorted_papers = sorted(all_papers.values(),
                       key=lambda x: x.get("paper_id", ""),
                       reverse=True)[:max_papers]

new_ids = []
for p in sorted_papers:
    print(json.dumps(p, ensure_ascii=False))
    new_ids.append(p["paper_id"])

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

print(f"[acl] 提取完成: {len(sorted_papers)} 篇（总 {len(all_papers)}，跳过 {len(seen_ids)} 已发送）", file=sys.stderr)
PYEOF
then
  log "WARN: 解析失败"
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

PAPER_COUNT="$(wc -l < "$PAPERS_FILE" | tr -d ' ')"
if [ "$PAPER_COUNT" -eq 0 ]; then
    log "无新论文，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[acl] 新论文: ${PAPER_COUNT} 篇"

# ── 3-4. V37.9.62: 每篇独立调 LLM (6 字段深度分析 + rule_check + retry 3 次) ─────
# 老 V1: 单次调用全部 N 篇 + 3 字段输出 + 失败硬编码占位符 "价值：⭐⭐⭐" (V37.9.36 反模式)
# 新 V37.9.62: 每篇独立调用 + 独立 retry (5s/10s/20s) + 6 字段深度 + 项目对齐度 + rule_check
#   📌 中文标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 / ⭐ 评级 / 🎚️ 项目对齐度
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败篇标 [LLM_DEGRADED] + abstract/title 兜底
# ACL-specific: 有 abstract (与 DBLP 不同, rule_content 拼接更强), prompt 仍标注 "(基于标题+摘要推断)"

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
            LAST_LLM_FAIL_REASON=$(echo "$parse_err" | head -c 200 | tr '\n' ' ')
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

# ── 主循环: 每篇论文独立调 LLM (6 字段深度 + 项目对齐度) ───────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""
TOTAL_NEW="$PAPER_COUNT"

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    python3 - "$PAPERS_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # V37.9.58-hotfix lineage: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT NameError fix

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
abstract = p.get('abstract', '')
first_author = p.get('first_author', 'Unknown')

prompt = """你是 NLP/AI 论文深度分析师 (兼 OpenClaw 项目对齐评估师)。对以下 ACL Anthology 顶会论文输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (NLP 术语保持精确)
🔑 核心贡献: 3-5 条 bullet, 每条 1 句 ≤ 60 字 (基于标题与摘要推断, 不虚构)
💡 关键方法: 基于标题/摘要/发表会议的技术分析
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字
   注意: ACL Anthology 提供摘要, 但实验/数据细节仅在论文 PDF 中 — 若摘要未明确, 必须显式标注 "(基于标题与摘要推断)"
🎯 实践启发: 1-3 条对 NLP 工程师 / AI 研究者的具体建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 用于什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.62 新增 (V37.9.45 hf_papers / V37.9.50 semantic_scholar / V37.9.51 dblp 同款 Opportunity Radar #2 模板, 用于过滤 OpenClaw 高价值信号) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/NLP 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 子任务)
   ⭐      = 完全无关 (噪声, 比如硬件细节 / GPU kernel / 单纯学术 paper)

⚠️ 严格约束 (违反则整份输出作废):
- ACL 数据含 title + abstract + venue, 严禁虚构摘要未提及的具体数据 / 实验结果 / 作者言论
- 推断时必须显式标注 "基于标题与摘要推断" 或 "推测" 让用户知道置信度
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI/NLP 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文标题: <你的翻译>

🔑 核心贡献:
- 贡献1
- 贡献2

💡 关键方法:
<段落, 长度按评级规则, 含 (基于标题与摘要推断) 标注>

🎯 实践启发:
- 启发1

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: <场景描述>

🎚️ 项目对齐度: ⭐⭐⭐ / <一句话原因, ≤ 30 字>

---

"""
prompt += f"论文标题: {title}\n"
if venue:
    prompt += f"发表会议: {venue}\n"
prompt += f"第一作者: {first_author}\n"
if abstract:
    prompt += f"摘要: {abstract}\n"
# V37.9.62: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var, V37.9.57 横向)
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
echo "[acl] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── V37.9.62: 6 字段 emit (6-field key-based parser + LLM_DEGRADED fallback + 多窗口切片) ──
MSG_FILE="$CACHE/acl_message.txt"
python3 - "$PAPERS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.62: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款防 NameError)

papers_file, results_file, day, msg_file = sys.argv[1:5]

papers = []
with open(papers_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.62 6 字段 key-based parser (V37.9.45 hf_papers / V37.9.50 semantic_scholar / V37.9.51 dblp 同款 Opportunity Radar #2)
def parse_6field_output(content):
    fields = {
        'cn_title': '', 'highlights': '', 'insight': '', 'practice': '', 'rating': '',
        'alignment': '',  # V37.9.62 新增 (V37.9.51 dblp 同款)
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


msg_lines = [f"📝 ACL Anthology 顶会 NLP 论文精选 ({day})", ""]

# V37.9.62: lazy import project_alignment_scorer + load concepts (V37.9.45 hf_papers / V37.9.50 / V37.9.51 dblp 同款 rule_check)
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
    print("[acl] V37.9.62 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[acl] V37.9.62 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.62: ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, paper in enumerate(papers):
    venue = paper.get('venue', '')
    first_author = paper.get('first_author', 'Unknown')
    abstract = paper.get('abstract', '')
    link = f"https://aclanthology.org/{paper.get('paper_id', '')}/"

    meta_parts = [first_author + " 等"]
    if venue:
        meta_parts.append(f"会议: {venue}")

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: abstract / title 兜底 (ACL 有 abstract, 比 DBLP 更强的最低保障)
        degraded_count += 1
        msg_lines.append(f"*{paper['title']}*")
        msg_lines.append("  | ".join(meta_parts))
        msg_lines.append(f"链接: {link}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 论文摘要供参考:")
        if abstract:
            msg_lines.append(abstract[:400])
        else:
            msg_lines.append("(本篇 ACL XML 未提供摘要, 请直接点链接阅读全文)")
        msg_lines.append("")
    else:
        # V37.9.62: 解析 6 字段 (V37.9.45 hf_papers / V37.9.50 / V37.9.51 dblp 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or paper['title']
        msg_lines.append(f"*{title_display}*")
        msg_lines.append("  | ".join(meta_parts))
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
        # V37.9.62: 🎚️ 项目对齐度展示 + rule_check 验证 (V37.9.45 hf_papers / V37.9.50 / V37.9.51 dblp 同款)
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            # rule_check: LLM ⭐ 评分 vs keyword-based rule 一致性
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = title + abstract + venue (ACL 有 abstract, 比 DBLP 更强)
                        rule_content = paper.get('title', '') + ' ' + paper.get('abstract', '') + ' ' + paper.get('venue', '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[acl] V37.9.62 rule_check 失败 paper={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.62: 末尾追加高对齐统计 (Opportunity Radar #2)
total_papers = len(papers)
if total_papers > 0:
    msg_lines.append(f"━━━ 本轮高对齐论文 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_papers} 篇 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[acl] 消息组装完成: {len(papers)} 篇 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count})", file=sys.stderr)
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
            chunk = chunk.replace(f"📝 ACL Anthology 顶会 NLP 论文精选 ({day})",
                                  f"📝 ACL Anthology 顶会 NLP 论文精选 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"📝 ACL Anthology 顶会 NLP 论文精选 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
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

# ── 7. KB归档 ────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    bash "$KB_WRITE_SCRIPT" "# ACL Anthology ${DATE_KB}

${SUMMARY}" "acl-anthology-nlp" "note" 2>/dev/null || true
fi

# ── 8. 永久归档 + 清理 + rsync ──────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{ echo ""; echo "## ${DAY}"; cat "$MSG_FILE"; } | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

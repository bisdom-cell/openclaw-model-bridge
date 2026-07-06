#!/bin/bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Homebrew python3 → 3.14 在 cron 环境 dlopen 失败，用系统 Python 3.9（只需标准库）
PYTHON3=/usr/bin/python3

# 防重叠执行（mkdir 原子锁，macOS 兼容）
LOCK="/tmp/hn_watcher.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[hn] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# run_hn.sh - Hacker News AI/Tech精选 Watcher
# 触发时间：每3小时:45分 HKT（系统crontab，与 ArXiv 错开45分钟）
# v23fix2：
#   - Bug修复：inbox写入提前到Python dedup阶段，防止LLM失败时重复处理同批URL
#   - Bug修复：正则宽容度提升，兼容LLM输出【】格式及多余空格
#   - 新增：LLM原始输出写入日志，方便排查格式问题
#   - 新增：HN_URL空值保护（防止shell loop中json解析失败导致空URL写inbox）

SCRIPT_DIR="$HOME/.openclaw/jobs/hn_watcher"
CACHE_DIR="$SCRIPT_DIR/cache"
INBOX="$HOME/.kb/inbox.md"
KB_SOURCE="$HOME/.kb/sources/hn_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
KB_DIR="$HOME/.kb"
SSD_BACKUP="/Volumes/MOVESPEED/KB/"
MSG_FILE="$CACHE_DIR/hn_message.txt"
NEW_FILE="$CACHE_DIR/hn_new.jsonl"
RSS_FILE="$CACHE_DIR/hn_frontpage.xml"
LLM_RAW_LOG="$CACHE_DIR/llm_raw_last.txt"
TO="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE_DIR/last_run.json"

log() { echo "[$TS] hn_watcher: $1" >&2; }

# V37.9.41: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5/V37.8.10/V37.9.16/V37.9.36-37/V37.9.39/V37.9.40 同款模式)
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

# V37.9.57: 公共反幻觉守卫 LEVEL_4_PROJECT_AWARE (MR-8 single-source-of-truth)
# 8 ALIGNED jobs 的 per-paper LLM prompt 已有 inline 反幻觉守卫, V37.9.57 追加
# LEVEL_4 含 V37.9.56-hotfix3 具体血案字眼 (禁"OpenClaw 社区发布"/"v26"/"[openclaw]")
# 防 alignment 评分输出"一句话原因"段编造项目动态. FAIL-OPEN: 模块缺失 → 空字符串
HG_LEVEL_4_TEXT=$(python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~'))
sys.path.insert(0, '$(cd \"$(dirname \"$0\")\" && pwd)')
try:
    import hallucination_guards as hg
    print(hg.get_guard('LEVEL_4_PROJECT_AWARE'))
except Exception:
    print('')
" 2>/dev/null)
export HG_LEVEL_4_TEXT

# V37.9.41: fail-fast alert helper
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] hn_watcher LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 HN 头版精选 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW_LOG:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        openclaw message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE_DIR"
touch "$INBOX"
touch "$KB_SOURCE"

curl -s --max-time 30 "https://hnrss.org/frontpage" -o "$RSS_FILE" 2>/dev/null
if [ ! -s "$RSS_FILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') hn_watcher: curl失败，跳过。"
    printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi

# ★ Fix1: Python dedup阶段同步写inbox，防止LLM失败时URL未记录导致重复处理
$PYTHON3 - << 'PYEOF' > "$NEW_FILE"
import os  # V37.9.57: read HG_LEVEL_4_TEXT env var
import xml.etree.ElementTree as ET
import json, os

INBOX_PATH = os.path.expanduser("~/.kb/inbox.md")
RSS_PATH   = os.path.expanduser("~/.openclaw/jobs/hn_watcher/cache/hn_frontpage.xml")

KEYWORDS = [
    'ai', 'llm', 'gpt', 'claude', 'gemini', 'llama', 'mistral', 'qwen', 'deepseek',
    'neural', 'machine learning', 'deep learning', 'transformer', 'diffusion',
    'inference', 'fine-tun', 'rag', 'agent', 'model',
    'programming', 'software', 'developer', 'engineering', 'compiler',
    'algorithm', 'data structure', 'system design', 'architecture',
    'rust', 'golang', 'python', 'typescript', 'wasm', 'llvm',
    'database', 'postgres', 'sqlite', 'redis', 'kafka', 'performance',
    'linux', 'kernel', 'container', 'kubernetes', 'docker', 'cloud',
    'security', 'vulnerability', 'exploit', 'cryptography', 'privacy', 'cve',
    'open source', 'github', 'framework', 'library', 'api', 'cli', 'terminal',
    'cpu', 'gpu', 'hardware', 'chip', 'memory', 'benchmark',
    'startup', 'ycombinator', 'robotics', 'autonomous', 'automation',
]

try:
    inbox_content = open(INBOX_PATH).read()
except Exception:
    inbox_content = ""

try:
    tree = ET.parse(RSS_PATH)
    root = tree.getroot()
    items = root.findall('.//item')
except Exception:
    import sys; sys.exit(0)

results = []
new_inbox_lines = []

for item in items[:50]:
    title    = (item.findtext('title')    or '').strip()
    link     = (item.findtext('link')     or '').strip()
    comments = (item.findtext('comments') or link).strip()
    desc_raw = (item.findtext('description') or '').strip()
    # V37.9.85: strip HN comment metadata that leaks into LLM prompt as noise
    # hnrss.org descriptions contain: "username | Hacker News•昨天6:22" comment attribution
    import re as _re
    desc_raw = _re.sub(r'<[^>]+>', ' ', desc_raw)              # strip HTML tags
    desc_raw = _re.sub(r'Hacker News\s*[•·]\s*\S+', '', desc_raw)  # "Hacker News•昨天6:22"
    desc_raw = _re.sub(r'\b\d+\s*(?:points?|comments?)\b', '', desc_raw, flags=_re.IGNORECASE)  # "123 points", "45 comments"
    desc_raw = _re.sub(r'\s{2,}', ' ', desc_raw)               # collapse whitespace
    desc     = desc_raw.strip()[:400]
    pubdate  = (item.findtext('pubDate')  or '').strip()

    if not title or not link:
        continue
    if not any(kw in title.lower() for kw in KEYWORDS):
        continue

    hn_url = comments if comments.startswith('http') else link
    if hn_url in inbox_content:
        continue

    results.append(json.dumps({
        'title': title, 'hn_url': hn_url,
        'source_url': link, 'desc': desc, 'pubdate': pubdate,
    }, ensure_ascii=False))

    # ★ Fix1核心：立即记录到inbox，无论后续LLM是否成功
    new_inbox_lines.append(f"- {hn_url}")
    inbox_content += f"\n- {hn_url}"  # 更新内存中的inbox防止同批重复

    if len(results) >= 5:
        break

# 批量追加到inbox（原子性：一次write，而不是多次append）
if new_inbox_lines:
    with open(INBOX_PATH, 'a') as f:
        f.write('\n'.join(new_inbox_lines) + '\n')

print('\n'.join(results))
PYEOF

NEW_COUNT=$(wc -l < "$NEW_FILE" 2>/dev/null | tr -d '[:space:]' || echo 0)
NEW_COUNT="${NEW_COUNT:-0}"
# V37.9.42 hotfix: dedup Python 在 results=[] 时 print('\n'.join([])) 输出 '\n' (空行),
# wc -l 数到 1 但 grep -c '^{' 为 0 (无 JSON). 用 JSON 起始符精确计数避开此空行陷阱.
NEW_JSON_COUNT=$(grep -c '^{' "$NEW_FILE" 2>/dev/null | tr -d '[:space:]' || echo 0)
NEW_JSON_COUNT="${NEW_JSON_COUNT:-0}"
if [ "$NEW_JSON_COUNT" -lt 1 ]; then
    log "无新AI/Tech内容 (NEW_FILE 无有效 JSON 条目)。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
NEW_COUNT="$NEW_JSON_COUNT"
if [ "$NEW_COUNT" -eq 0 ]; then
    log "暂无新AI/Tech内容。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi

TODAY=$(date '+%Y-%m-%d')

# ── V37.9.41: 每条独立调 LLM (5 字段深度分析 + 按评级动态调长度 + retry 3 次) ─
# 老 V38: 单次批量调用全部 N 条 + 4 字段 (zh_title/point/stars) + stars='⭐⭐⭐' silent fallback
# 新 V37.9.41: 每条独立调用 + 独立 retry (5s/10s/20s) + 5 字段深度
#   📌 中文标题 / 🔑 核心要点 / 💡 技术深度解读 / 🎯 实践启发 / ⭐ 评级
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败条标 [LLM_DEGRADED] + HN description 兜底

LLM_RAW="$LLM_RAW_LOG"
> "$LLM_RAW"
RESULTS_FILE="$CACHE_DIR/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单条 LLM 调用 + retry (V37.9.39/V37.9.40 同款) ───────────
call_llm_single_with_retry() {
    local prompt_file="$1"
    local idx="$2"
    LAST_LLM_FAIL_REASON=""
    local backoffs=(5 10 20)

    for attempt in 0 1 2; do
        local payload_file="$CACHE_DIR/llm_payload_${idx}_a${attempt}.json"
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

        # V37.9.36 三层检测 + Qwen3 <think>/ANSI 清理
        local parse_err_file="$CACHE_DIR/llm_parse_${idx}_a${attempt}.err"
        local parse_out
        parse_out=$(echo "$llm_resp" | $PYTHON3 -c "
import json, sys, re
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
content = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', content)  # ANSI
content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()  # Qwen3
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

# ── 主循环: 每条 HN post 独立调 LLM (5 字段深度) ──────────────────────
TOTAL_NEW="$NEW_COUNT"
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE_DIR/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$NEW_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, re, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 prompt += os.environ.get('HG_LEVEL_4_TEXT', '') (line ~352), V37.9.50-hotfix 同款 NameError fix 但 V37.9.57 注入到不同 heredoc 未同步补齐

new_file, idx = sys.argv[1], int(sys.argv[2])
items = []
with open(new_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
p = items[idx]
title = p['title']
desc = p.get('desc', '').strip()
desc = re.sub(r'<[^>]+>', '', desc).strip()  # HTML 清理
desc = desc[:600]

prompt = """你是技术新闻深度分析师 (兼 OpenClaw 项目对齐评估师)。对以下 Hacker News 帖子输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (技术术语保持精确)
🔑 核心要点: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出帖子最重要的发现/技术声明/新闻事实
💡 技术深度解读: 揭示作者立场 / 技术背景 / 为什么 HN 社区关注 / 与已有讨论关联 / 局限性
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字
   注意: HN 摘要常较短或缺失, 数据不足时显式标注 "(基于标题与摘要推断)"
🎯 实践启发: 1-3 条对开发者 / 工程师 / 架构师的具体建议, 每条 ≤ 80 字
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
- 只使用上方提供的标题和摘要中的信息, 严禁虚构帖子未提及的事实/数据/链接
- HN 摘要不足以判断时显式标注 "(基于标题与摘要推断)"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态除非原文提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文标题: <你的翻译>

🔑 核心要点:
- 要点1
- 要点2

💡 技术深度解读:
<段落, 长度按评级规则>

🎯 实践启发:
- 启发1

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: <场景描述>

🎚️ 项目对齐度: ⭐⭐⭐ / <一句话原因, ≤ 30 字>

---

"""
prompt += f"原文标题: {title}\n"
if desc:
    prompt += f"摘要: {desc}\n"
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
echo "[hn] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── V37.9.41: 5 字段 emit (key-based parser + LLM_DEGRADED + 多窗口切片) ──
$PYTHON3 - "$NEW_FILE" "$RESULTS_FILE" "$TODAY" "$MSG_FILE" "$KB_SOURCE" << 'PYEOF'
import sys, json, re, os  # V37.9.51: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款)

new_file, results_file, today, msg_file, kb_source = sys.argv[1:6]

items = []
with open(new_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
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


msg_lines = [f"💻 HN 头版精选 ({today})", ""]

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
    print("[hn] V37.9.51 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[hn] V37.9.51 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.51: ⭐≥4 alignment 计数 (Opportunity Radar #2)
sent = 0
for i, item in enumerate(items):
    title = item['title']
    hn_url = item.get('hn_url', '').strip()
    if not hn_url:
        continue

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: HN description 兜底 (替代 V37.9.36 占位符 stars='⭐⭐⭐')
        degraded_count += 1
        msg_lines.append(f"*{title}*")
        msg_lines.append(f"链接: {hn_url}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 原文摘要供参考:")
        desc = item.get('desc', '')
        desc = re.sub(r'<[^>]+>', '', desc).strip()[:300]
        if desc:
            msg_lines.append(desc)
        else:
            msg_lines.append("(HN 无摘要数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        # V37.9.51: 解析 6 字段 (V37.9.45 hf_papers / V37.9.50 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or title
        msg_lines.append(f"*{title_display}*")
        msg_lines.append(f"链接: {hn_url}")
        msg_lines.append("")
        if fields['highlights']:
            msg_lines.append("🔑 核心要点:")
            msg_lines.append(fields['highlights'])
            msg_lines.append("")
        if fields['insight']:
            msg_lines.append("💡 技术深度解读:")
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
                        # rule_content = title + desc (HN 场景, V37.9.47 hf_papers 同款模式适配)
                        rule_content = item.get('title', '') + ' ' + (re.sub(r'<[^>]+>', '', item.get('desc', '') or '').strip())
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[hn] V37.9.51 rule_check 失败 item={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1
        # KB source line
        rating_short = (fields['rating'][:30] if fields['rating'] else '') or '—'
        with open(kb_source, "a") as f:
            f.write(f"- **[{title}]({hn_url})** | {today} | {rating_short}\n")

    msg_lines.append("---")
    msg_lines.append("")
    sent += 1

# V37.9.51: 末尾追加高对齐统计 (Opportunity Radar #2)
total_items = sent  # sent = 实际推送的条目数 (跳过 hn_url 空的)
if total_items > 0:
    msg_lines.append(f"━━━ 本轮高对齐帖子 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_items} 条 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[hn] 消息组装: {len(items)} 条 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count}, sent {sent})", file=sys.stderr)
PYEOF

SENT_COUNT=$(grep -c "^---$" "$MSG_FILE" 2>/dev/null) || SENT_COUNT=0
SENT_COUNT="${SENT_COUNT:-0}"
SENT_COUNT="$(echo "$SENT_COUNT" | tr -d '[:space:]')"
SENT_COUNT="${SENT_COUNT:-0}"

# ── 推送 WhatsApp + Discord (V37.9.21/V37.9.40 多窗口分片: >8000 字才切, ≤8000 单段) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$SENT_COUNT" -eq 0 ]; then
    log "无有效条目, 跳过推送"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
elif [ "$TOTAL_LEN" -le 8000 ]; then
    MSG_CONTENT="$(cat "$MSG_FILE")"
    # V37.9.171: 走 notify.sh（微信→用户 + Discord #tech + 重试/队列）。
    # 退役 whatsapp-stderr 插件警告过滤 hack（notify 返回码权威，发出≥1 即成功）。
    if notify "$MSG_CONTENT" --topic tech >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${SENT_COUNT} 条 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    # 总长 >8000 → 多窗口切片 (V37.9.21 同款 mktemp + sleep 1s 防乱序)
    WA_CHUNK_DIR=$(mktemp -d)
    trap 'rm -rf "$WA_CHUNK_DIR"; rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

    $PYTHON3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$TODAY" << 'PYEOF'
import sys, os, re

msg_file, chunk_dir, today = sys.argv[1], sys.argv[2], sys.argv[3]
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
            chunk = chunk.replace(f"💻 HN 头版精选 ({today})",
                                  f"💻 HN 头版精选 [1/{total_parts}] ({today})", 1)
        else:
            chunk = f"💻 HN 头版精选 [{i+1}/{total_parts}] ({today}) (续)\n\n" + chunk
    with open(os.path.join(chunk_dir, f"{i:03d}.txt"), 'w', encoding='utf-8') as f:
        f.write(chunk)
PYEOF

    WA_PARTS_TOTAL=$(ls "$WA_CHUNK_DIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
    WA_SENT_OK=0
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        CHUNK_CONTENT="$(cat "$chunk_file")"
        # V37.9.171: 走 notify.sh（微信 + Discord #tech + 重试/队列）
        if notify "$CHUNK_CONTENT" --topic tech >/dev/null 2>>"$SEND_ERR"; then
            WA_SENT_OK=$((WA_SENT_OK + 1))
        fi
        sleep 1  # 防消息乱序 (V37.9.21 契约)
    done
    log "已推送 ${SENT_COUNT} 条 (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
    if [ "$WA_SENT_OK" -gt 0 ]; then
        WA_SENT=true
    fi
fi

if [ "$WA_SENT" = "true" ]; then
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
    fi
    bash "$KB_WRITE_SCRIPT" "# HN AI/Tech精选 $(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M')

$(cat "$MSG_FILE")" "hn-tech" "note" 2>/dev/null || true
elif [ "$SENT_COUNT" -gt 0 ]; then
    log "ERROR: 推送全失败"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi
rm -f "$SEND_ERR"

bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$KB_DIR/" "$SSD_BACKUP"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
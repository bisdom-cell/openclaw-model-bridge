#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# OpenClaw 官方 Releases 监控 (V37.9.62 — 6 字段 + rule_check 升级,
# V37.9.51 rss_blogs 同款 Opportunity Radar #2 模板横向迁移, Sub-Stage 4b 3/6)
# 数据源: GitHub Atom feed (fetch_github_releases.sh + format_github_releases.py
# 由 Mac Mini 部署在 $JOB_DIR 下), 每个 release JSONL 含 id/title/url/ts/fingerprint
set -euo pipefail

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

# 防重叠执行（mkdir 原子锁，macOS 兼容）
LOCK="/tmp/openclaw_releases.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[releases] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
TO="${OPENCLAW_PHONE:-+85200000000}"
PYTHON3=/usr/bin/python3

day="$(TZ=Asia/Hong_Kong date "+%Y-%m-%d")"
DAY="$day"

ROOT="${ROOT:-$HOME/.openclaw}"
JOB_DIR="$ROOT/jobs/openclaw_official"

FETCH="$JOB_DIR/fetch_github_releases.sh"
FORMAT_PY="$JOB_DIR/format_github_releases.py"
STATE="$JOB_DIR/state.json"
CACHE_DIR="$JOB_DIR/cache"
CACHE="$CACHE_DIR"  # alias for V37.9.51 同款命名
MSG="$CACHE_DIR/system_message.txt"
MSG_FILE="$CACHE_DIR/system_message.txt"

KB_SRC="${KB_BASE:-$HOME/.kb}/sources/openclaw_official.md"
KB_INBOX="${KB_BASE:-$HOME/.kb}/inbox.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE_DIR/last_run.json"

log() { echo "[$TS] openclaw_releases: $1" >&2; }

# V37.9.62: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5 kb_review / V37.8.10 kb_evening / V37.9.16 kb_deep_dive / V37.9.51 rss_blogs 同款模式)
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

# V37.9.62: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] openclaw_official LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 OpenClaw 版本更新 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE_DIR" "$HOME/.kb/sources" "$ROOT/logs/jobs"

# init files if absent
if [ ! -f "$STATE" ]; then
  printf "%s\n" "{\"github_releases\":{\"last_updated\":null,\"seen_ids\":[]}}" > "$STATE"
fi
if [ ! -f "$KB_SRC" ]; then
  echo "# OpenClaw Official Watcher" > "$KB_SRC"
fi
if [ ! -f "$KB_INBOX" ]; then
  echo "# INBOX" > "$KB_INBOX"
fi

if ! ATOM_PATH="$("$FETCH" 2>"$CACHE_DIR/fetch_releases.err")"; then
  # V37.4.3: 告警消息加 [SYSTEM_ALERT] 隔离标记
  ERR_MSG="[SYSTEM_ALERT]
⚠️ OpenClaw Releases 抓取失败（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE_DIR/fetch_releases.err" 2>/dev/null)"
  log "ERROR: $ERR_MSG"
  "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# JSONL stream (string) -> write to temp file to avoid pipe subshell issues
JSONL_FILE="$(mktemp)"
if [ -n "$ATOM_PATH" ] && [ -f "$ATOM_PATH" ]; then
  "$FORMAT_PY" "$ATOM_PATH" > "$JSONL_FILE" 2>/dev/null || true
fi

last_updated="$(jq -r ".github_releases.last_updated" "$STATE")"
seen_ids="$(jq -c ".github_releases.seen_ids" "$STATE")"

new_count=0
new_last_updated=""
new_ids_file="$(mktemp)"
new_events_file="$(mktemp)"
ALL_NEW_FILE="$new_events_file"  # alias for V37.9.51 同款命名

# Read JSONL from file (no subshell)
while IFS= read -r line; do
  [ -z "${line// }" ] && continue

  eid="$(printf "%s\n" "$line" | jq -r ".id")"
  ts="$(printf "%s\n" "$line" | jq -r ".ts")"

  # already seen?
  if printf "%s\n" "$seen_ids" | jq -e --arg id "$eid" "index(\$id) != null" >/dev/null 2>&1; then
    continue
  fi

  # only strictly newer than last_updated
  if [ "$last_updated" != "null" ] && [ -n "$last_updated" ]; then
    if [[ "$ts" < "$last_updated" || "$ts" == "$last_updated" ]]; then
      continue
    fi
  fi

  printf "%s\n" "$line" >> "$new_events_file"
  printf "%s\n" "$eid" >> "$new_ids_file"
  new_count=$((new_count+1))
  if [ -z "$new_last_updated" ]; then
    new_last_updated="$ts"
  fi
done < "$JSONL_FILE"

rm -f "$JSONL_FILE"

TOTAL_NEW="$new_count"
log "releases_new=${TOTAL_NEW}"

if [ "$TOTAL_NEW" -eq 0 ]; then
  log "no new updates."
  printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
  rm -f "$new_ids_file" "$new_events_file"
  exit 0
fi

# ── V37.9.62: 每篇独立调 LLM (6 字段深度分析 + 按评级调长度 + retry 3 次) ─
# V37.9.51 rss_blogs 同款机械迁移 (V37.9.45 hf_papers / V37.9.50 semantic_scholar 系列)
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
            LAST_LLM_FAIL_REASON=$(echo "$parse_err" | head -c 200 | tr '\n' ' ')
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

        echo "$parse_out"
        return 0
    done

    return 1
}

# ── 主循环: 每个 release 独立调 LLM ────────────────────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$ALL_NEW_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, re, os  # V37.9.50-hotfix: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT NameError fix

events_file, idx = sys.argv[1], int(sys.argv[2])
with open(events_file, encoding='utf-8') as f:
    events = [json.loads(l) for l in f if l.strip()]
e = events[idx]
title = e.get('title', '')
url = e.get('url', '')
ts = e.get('ts', '')[:10]
# release notes 通常在 fingerprint / body 字段, 我们尝试多字段兼容
body = e.get('body', '') or e.get('description', '') or e.get('notes', '')
body = body[:600] if body else ''
# 从 title 提取版本号 (如 v2026.5.3)
version_match = re.search(r'v?\d{4}\.\d+(?:\.\d+)?(?:-\w+)?', title)
version_tag = version_match.group(0) if version_match else ''

prompt = """你是 OpenClaw 项目的技术编辑 (兼项目对齐评估师)。请对以下 GitHub Release 输出 6 字段中文分析:

📌 中文标题: 信达雅翻译版本标题, 不超过 25 字 (保留版本号, 如"OpenClaw v2026.5.3 — Tool Plugin 增强")
🔑 核心要点: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出本次 release 的核心变更/新增/修复
💡 关键洞察: 揭示这个版本的设计意图 / 架构演进方向 / 与上一版本的对比 / 升级紧迫度依据
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字 (重大版本充分展开)
🎯 实践启发: 1-3 条对集成方/使用者的具体行动建议 (是否立即升级 / 配置变更 / 兼容性影响), 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该升级 / 何时升级 / 配什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.62 新增 (V37.9.51 rss_blogs 同款 Opportunity Radar #2 模板, 用于过滤本项目高价值上游变更) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 任务)
   ⭐      = 完全无关 (噪声, 比如硬件细节 / GPU kernel / 单纯学术 paper)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的标题和 release notes 中的信息, 严禁虚构未提及的变更/数据/链接
- 如 notes 不足以判断深度, 标⭐较低 + 写"基于标题与简要 notes 的初步判断"
- 项目对齐度评分必须基于"是否能为本项目控制平面 / 记忆平面 / ontology engine 提供有价值借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态除非原文提及
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
prompt += f"Release 标题: {title}\n"
if version_tag:
    prompt += f"版本号: {version_tag}\n"
prompt += f"链接: {url}\n"
prompt += f"发布日期: {ts}\n"
if body:
    prompt += f"\nRelease notes 摘要:\n{body}\n"
else:
    prompt += "\n(无 release notes, 请基于标题与版本号推断, 必须明确标注'基于标题的初步判断')\n"
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "调用 LLM 分析 release $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        $PYTHON3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: release $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        $PYTHON3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定整体 status (V37.9.36 fail-fast 契约保留) ──────────────────
if [ "$TOTAL_FAILED" -eq "$TOTAL_NEW" ]; then
    log "ERROR: 全部 $TOTAL_NEW 个 release LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 个 release LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    rm -f "$new_ids_file" "$new_events_file"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 个 release 失败 — 走 partial_degraded (失败 release 标 [LLM_DEGRADED])"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 个 release LLM 部分失败 (其余正常推送, 失败标 [LLM_DEGRADED])"
fi

# ── V37.9.62: 组装消息 (6 字段解析 + LLM_DEGRADED fallback + rule_check) ──
now_hkt="$(TZ=Asia/Hong_Kong date "+%Y-%m-%d %H:%M HKT")"
$PYTHON3 - "$ALL_NEW_FILE" "$RESULTS_FILE" "$DAY" "$now_hkt" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.51: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款)

events_file, results_file, day, now_hkt, msg_file = sys.argv[1:6]

with open(events_file, encoding='utf-8') as f:
    events = [json.loads(l) for l in f if l.strip()]
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.51 6 字段 key-based parser (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款)
def parse_6field_output(content):
    """从 LLM 输出解析 6 字段, key-based + tolerant."""
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


msg_lines = [f"🦞 OpenClaw 版本更新 ({now_hkt})", ""]

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
    print("[releases] V37.9.62 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[releases] V37.9.62 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.51: ⭐≥4 alignment 计数 (Opportunity Radar #2)

for i, event in enumerate(events):
    title = event.get('title', '')
    url = event.get('url', '')
    ts = event.get('ts', '')[:10]
    msg_lines.append(f"*Release {i+1}: {title}*")
    msg_lines.append(f"发布: {ts}")
    msg_lines.append(f"链接: {url}")
    msg_lines.append("")

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 用 release title + 简要 fallback 给用户最低保障
        degraded_count += 1
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 请直接查看上方链接:")
        body = (event.get('body') or event.get('description') or '')[:300]
        if body:
            msg_lines.append(body)
        else:
            msg_lines.append("(release notes 数据缺失, 请直接点链接阅读 GitHub release page)")
        msg_lines.append("")
    else:
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
        # V37.9.51: 🎚️ 项目对齐度展示 + rule_check 验证
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = title + body (release 数据特定: 用 release notes body 而非 description)
                        rule_content = title + ' ' + (event.get('body') or event.get('description') or '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[releases] V37.9.62 rule_check 失败 release={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.51: 末尾追加高对齐统计 (Opportunity Radar #2)
total_releases = len(events)
if total_releases > 0:
    msg_lines.append(f"━━━ 本轮高对齐 release (项目对齐度 ⭐≥4): {high_alignment_count}/{total_releases} 个 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[releases] 消息组装完成: {len(events)} 个 release (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── KB 永久归档 (V37.6: idempotent H2-dedup append) ─────────────────
{
  echo ""
  echo "## ${day}"
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    ts="$(printf "%s\n" "$ev" | jq -r ".ts")"
    id="$(printf "%s\n" "$ev" | jq -r ".id")"
    fp="$(printf "%s\n" "$ev" | jq -r ".fingerprint")"
    echo "- **${title}**"
    echo "  - 时间: ${ts}"
    echo "  - 链接: ${url}"
    echo "  - ID: ${id}"
    echo "  - Fingerprint: ${fp}"
  done < "$new_events_file"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${day}"

# INBOX append with de-dup by URL
while IFS= read -r ev; do
  title="$(printf "%s\n" "$ev" | jq -r ".title")"
  url="$(printf "%s\n" "$ev" | jq -r ".url")"
  line="- [ ] (${day}) openclaw release | ${title} | ${url}"
  if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
    printf "\n%s\n" "$line" >> "$KB_INBOX"
  fi
done < "$new_events_file"

# ── 更新 state.json (seen_ids + last_updated) ─────────────────────
add_json="$(jq -R . < "$new_ids_file" | jq -s .)"
updated_seen="$(jq -c --argjson add "$add_json" --argjson seen "$seen_ids" "(\$add + \$seen)[:200]" <<< "{}")"

tmp="$(mktemp)"
jq --arg last "$new_last_updated" --argjson seen "$updated_seen" \
  ".github_releases.last_updated=\$last | .github_releases.seen_ids=\$seen" "$STATE" > "$tmp"
mv "$tmp" "$STATE"

rm -f "$new_ids_file" "$new_events_file"

log "openclaw_official/github_releases: new=${TOTAL_NEW}, last_updated=${new_last_updated}"

# ── rsync 备份 (V37.9.27 jitter+retry+fail-loud+capture) ───────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"

# ── 推送 WhatsApp + Discord (V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段发) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    MSG_CONTENT="$(cat "$MSG_FILE")"
    if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${TOTAL_NEW} 个 release (单段, $TOTAL_LEN 字)"
        WA_SENT=true
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    WA_CHUNK_DIR=$(mktemp -d)
    trap 'rmdir "$LOCK" 2>/dev/null; rm -rf "$WA_CHUNK_DIR"' EXIT INT TERM

    $PYTHON3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$DAY" << 'PYEOF'
import sys, os, re

msg_file, chunk_dir, day = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(msg_file, encoding='utf-8').read()
MAX_CHUNK = 4000

# 按 "\n---\n" 切分 release 块, 第一块是 header "🦞 OpenClaw 版本更新 (...)"
blocks = re.split(r'\n---\n', content)
header_block = blocks[0]
release_blocks = [b for b in blocks[1:] if b.strip()]

chunks = []
current = header_block
for block in release_blocks:
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
            # 保留 header 的 🦞 + 加 [1/N]
            chunk = re.sub(r'^(🦞 OpenClaw 版本更新)', f'🦞 OpenClaw 版本更新 [1/{total_parts}]', chunk, count=1)
        else:
            chunk = f"🦞 OpenClaw 版本更新 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
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
    log "已推送 ${TOTAL_NEW} 个 release (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
    if [ "$WA_SENT_OK" -gt 0 ]; then
        WA_SENT=true
    fi
fi

# ── 写 status.json (区分 ok / partial_degraded / send_failed) ─────
if [ "$WA_SENT" = "true" ]; then
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi

echo "openclaw_official/github_releases V37.9.62 完成"
echo "system_message_saved=${MSG}"
echo "kb_source_saved=${KB_SRC}"
echo "kb_inbox_saved=${KB_INBOX}"
echo "---- SYSTEM MESSAGE ----"
cat "$MSG"

#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

# V37.9.62: 6 字段深度分析 + rule_check (V37.9.51 run_hn_fixed.sh 同款机械迁移)
# - 老 V37.4.3: 3 字段 (CN_TITLE/CONTRIB/STARS) + 占位符 fallback "贡献：社区 issue，建议关注。 / 价值：⭐⭐⭐" (V37.9.36 反模式血案 L155 风险)
# - 新 V37.9.62: 6 字段 (📌中文标题/🔑核心讨论点/💡技术深度解读/🎯实践启发/⭐评级/🎚️项目对齐度)
#   + 每条独立 LLM 调用 + retry 3 次 (5s/10s/20s) + V37.9.36 三层检测 (HTTP_ERROR/PARSE_FAIL/empty)
#   + LLM_DEGRADED 用 issue body 兜底 (替代占位符)
#   + project_alignment_scorer rule_check + ⚠️ marker + 高对齐 ⭐≥4 统计 (Opportunity Radar #2)
#   + 多窗口分片 (>8000 字, V37.9.21 同款)
# Lineage: V37.9.45 hf_papers / V37.9.50 semantic_scholar / V37.9.51 run_hn_fixed.sh (帖子讨论类同款)

# 防重叠执行（mkdir 原子锁，macOS 兼容）
LOCK="/tmp/openclaw_discussions.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[discussions] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Homebrew python3 → 3.14 在 cron 环境 dlopen 失败，用系统 Python 3.9（V37.9.51 同款）
PYTHON3=/usr/bin/python3

ROOT="${ROOT:-$HOME/.openclaw}"
JOB="$ROOT/jobs/openclaw_official"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/openclaw_official.md"
KB_INBOX="${KB_BASE:-$HOME/.kb}/inbox.md"
CACHE="$JOB/cache"
# V28.1: Discussions 已禁用(404)，改用 GitHub REST API 监控 Issues
# V28.3: 加 GITHUB_TOKEN 认证(5000 req/hr) + ETag 缓存避免限流
API_URL="https://api.github.com/repos/openclaw/openclaw/issues?state=open&sort=created&direction=desc&per_page=20"
TO="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE/last_run_discussions.json"
ETAG_FILE="$CACHE/issues_etag.txt"
LLM_RAW_LOG="$CACHE/llm_raw_last.txt"
RESULTS_FILE="$CACHE/llm_results.jsonl"

log() { echo "[$TS] openclaw_issues: $1" >&2; }

# V37.9.62: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.9.41 hn / V37.9.51 同款模式)
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
# 防 alignment 评分输出"一句话原因"段编造 OpenClaw 项目动态. FAIL-OPEN: 模块缺失 → 空字符串
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

# V37.9.62: fail-fast alert helper (V37.9.41 hn 同款)
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] openclaw_discussions LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 OpenClaw 社区新动态 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW_LOG:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        openclaw message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "$HOME/.kb/sources"
test -f "$KB_SRC"   || echo "# OpenClaw Official Watcher" > "$KB_SRC"
test -f "$KB_INBOX" || echo "# INBOX" > "$KB_INBOX"

# V28.3: 构建认证 + ETag 请求头
AUTH_HEADERS=(-H "Accept: application/vnd.github+json" -H "User-Agent: openclaw-watcher/1.0")
if [ -n "${GITHUB_TOKEN:-}" ]; then
  AUTH_HEADERS+=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi
if [ -f "$ETAG_FILE" ]; then
  AUTH_HEADERS+=(-H "If-None-Match: $(cat "$ETAG_FILE")")
fi

API_JSON="$CACHE/issues_api.json"
HTTP_CODE="$(curl -sSL --max-time 30 -w '%{http_code}' \
    -D "$CACHE/issues_headers.txt" \
    "${AUTH_HEADERS[@]}" \
    "$API_URL" -o "$CACHE/issues_api_new.json" 2>"$CACHE/curl_issues_api.err")"

# 304 Not Modified → 无新数据，直接复用缓存（不消耗限额）
if [ "$HTTP_CODE" -eq 304 ]; then
  log "304 Not Modified, 无新 issue。"
  printf '{"time":"%s","status":"ok","new":0,"cached":true}\n' "$TS" > "$STATUS_FILE"
  exit 0
fi

if [ "$HTTP_CODE" -lt 200 ] || [ "$HTTP_CODE" -ge 300 ]; then
  # V37.4.3: 告警消息加 [SYSTEM_ALERT] 隔离标记
  ERR_MSG="[SYSTEM_ALERT]
⚠️ Issues Watcher API 请求失败 HTTP ${HTTP_CODE}（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE/curl_issues_api.err" 2>/dev/null)"
  log "ERROR: $ERR_MSG"
  openclaw message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  openclaw message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"fetch_failed","http":%s,"new":0}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
  exit 1
fi

# 成功：保存 ETag + 更新缓存
mv "$CACHE/issues_api_new.json" "$API_JSON"
grep -i '^etag:' "$CACHE/issues_headers.txt" 2>/dev/null | sed 's/^[Ee][Tt][Aa][Gg]: *//' | tr -d '\r' > "$ETAG_FILE" || true

# V37.9.62: 解析 JSON → JSONL with title/url/date/body/comments/state (过滤掉 pull_request 条目)
ISSUES_JSONL="$CACHE/issues_parsed.jsonl"
if ! $PYTHON3 - "$API_JSON" "$ISSUES_JSONL" << 'PYEOF' 2>"$CACHE/parse_issues.err"
import json, sys
api_file, out_file = sys.argv[1], sys.argv[2]
with open(api_file) as f:
    data = json.load(f)
if not isinstance(data, list):
    msg = data.get("message", "unknown") if isinstance(data, dict) else "unknown"
    print(f'API error: {msg}', file=sys.stderr)
    sys.exit(1)
count = 0
with open(out_file, 'w', encoding='utf-8') as out:
    for item in data:
        # GitHub REST API 的 /issues 端点也返回 PR，用 pull_request 字段区分
        if 'pull_request' in item:
            continue
        rec = {
            'title': item.get('title', ''),
            'url': item.get('html_url', ''),
            'date': (item.get('created_at') or '')[:10],
            'body': (item.get('body') or '')[:500],  # V37.9.62: body 摘要 (rule_check + LLM_DEGRADED 兜底)
            'comments': item.get('comments', 0),
            'state': item.get('state', 'open'),
            'author': (item.get('user') or {}).get('login', ''),
        }
        if rec['title'] and rec['url']:
            out.write(json.dumps(rec, ensure_ascii=False) + '\n')
            count += 1
print(f'[issues] 解析完成: {count} 条 issues', file=sys.stderr)
PYEOF
then
  # V37.4.3: 告警消息加 [SYSTEM_ALERT] 隔离标记
  ERR_MSG="[SYSTEM_ALERT]
⚠️ Issues Watcher 解析失败（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE/parse_issues.err" 2>/dev/null)"
  log "ERROR: $ERR_MSG"
  openclaw message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  openclaw message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# V37.9.62: dedup by inbox + 构建 NEW_FILE (jsonl, 同 HN 模式)
NEW_FILE="$CACHE/discussions_new.jsonl"
day="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"

$PYTHON3 - "$ISSUES_JSONL" "$NEW_FILE" "$KB_INBOX" "$day" << 'PYEOF'
import sys, json
parsed_file, new_file, inbox_path, day = sys.argv[1:5]
try:
    inbox_content = open(inbox_path).read()
except Exception:
    inbox_content = ""

new_inbox_lines = []
with open(parsed_file, encoding='utf-8') as fin, open(new_file, 'w', encoding='utf-8') as fout:
    for line in fin:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        url = item.get('url', '')
        if not url or url in inbox_content:
            continue
        fout.write(json.dumps(item, ensure_ascii=False) + '\n')
        new_inbox_lines.append(f"- [ ] ({day}) openclaw issues | {item.get('title','')} | {url}")
        inbox_content += f"\n{url}"

if new_inbox_lines:
    with open(inbox_path, 'a') as f:
        f.write('\n'.join(new_inbox_lines) + '\n')
PYEOF

NEW_COUNT=$(grep -c '^{' "$NEW_FILE" 2>/dev/null | tr -d '[:space:]' || echo 0)
NEW_COUNT="${NEW_COUNT:-0}"
if [ "$NEW_COUNT" -lt 1 ]; then
    log "暂无新 issue。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi

# V37.7: idempotent H2-dedup append to sources (was direct >> loop, bug class
# MR-4/MR-9). Cron 4x/day (08:15/12:15/16:15/20:15) → SLOT_TAG distinguishes
# same-day runs so second run isn't silently dropped as "duplicate section".
SLOT_TAG="$(TZ=Asia/Hong_Kong date '+%H:%M')"
SECTION_MARKER="## ${day} ${SLOT_TAG}"
{
    echo ""
    echo "${SECTION_MARKER}"
    $PYTHON3 -c "
import json, sys
with open('$NEW_FILE', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                item = json.loads(line)
                print(f\"- **[{item.get('title','')}]({item.get('url','')})** | {item.get('date','')}\")
            except Exception:
                continue
"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "${SECTION_MARKER}"

# ── V37.9.62: 每条独立调 LLM (6 字段深度 + 按评级动态调长度 + retry 3 次) ─
# 老 V37.4.3: 3 字段 (CN_TITLE/贡献/价值⭐) + silent fallback "贡献：社区 issue / 价值：⭐⭐⭐"
# 新 V37.9.62: 6 字段 + retry + LLM_DEGRADED (V37.9.51 hn 同款)

LLM_RAW="$LLM_RAW_LOG"
> "$LLM_RAW"
> "$RESULTS_FILE"

MSG="$CACHE/system_message_discussions.txt"
: > "$MSG"

# ── helper: 单条 LLM 调用 + retry (V37.9.51 hn 同款) ───────────
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

        # V37.9.36 三层检测 + Qwen3 <think>/ANSI 清理
        local parse_err_file="$CACHE/llm_parse_${idx}_a${attempt}.err"
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
            LAST_LLM_FAIL_REASON=$(echo "$parse_err" | head -c 200 | tr '\n' ' ')
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

# ── 主循环: 每条 issue 独立调 LLM (6 字段深度) ──────────────────────
TOTAL_NEW="$NEW_COUNT"
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$NEW_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, re, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 prompt += os.environ.get('HG_LEVEL_4_TEXT', '')

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
title = p.get('title', '')
body = p.get('body', '').strip()
body = re.sub(r'<[^>]+>', '', body).strip()
body = body[:500]
comments = p.get('comments', 0)
state = p.get('state', 'open')
author = p.get('author', '')

prompt = """你是 OpenClaw 社区技术编辑 (兼 OpenClaw 项目对齐评估师)。对以下 OpenClaw GitHub Issue/Discussion 输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (技术术语保持精确)
🔑 核心讨论点: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出 issue 的关键问题/痛点/技术诉求
💡 技术深度解读: 揭示作者立场 / 技术背景 / 这个 issue 反映出什么 OpenClaw 架构问题 / 与已有讨论关联 / 局限性
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字
   注意: Issue body 可能短或缺失, 数据不足时显式标注 "(基于标题与摘要推断)"
🎯 实践启发: 1-3 条对 OpenClaw 用户 / 贡献者 / 架构师的具体建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该关注 / 何时跟进 / 用于什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.51 新增 (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2 模板, 用于过滤 OpenClaw 高价值信号) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 任务)
   ⭐      = 完全无关 (噪声, 比如纯硬件细节 / 单纯学术讨论)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的标题和 body 中的信息, 严禁虚构 issue 未提及的事实/数据/链接
- Issue body 不足以判断时显式标注 "(基于标题与摘要推断)"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 OpenClaw 的具体内部状态或未发布功能除非原文提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

📌 中文标题: <你的翻译>

🔑 核心讨论点:
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
prompt += f"作者: {author} | 状态: {state} | 评论数: {comments}\n"
if body:
    prompt += f"摘要: {body}\n"
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
echo "[discussions] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── V37.9.62: 6 字段 emit (key-based parser + LLM_DEGRADED + 多窗口切片) ──
$PYTHON3 - "$NEW_FILE" "$RESULTS_FILE" "$day" "$MSG" "$KB_SRC" << 'PYEOF'
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

# V37.9.51 6 字段 key-based parser (V37.9.45 hf_papers / V37.9.50 semantic_scholar / V37.9.51 hn 同款 Opportunity Radar #2)
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


msg_lines = [f"🦞 OpenClaw 社区新动态 ({today})", ""]

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
    print("[discussions] V37.9.62 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[discussions] V37.9.62 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.51: ⭐≥4 alignment 计数 (Opportunity Radar #2)
sent = 0
for i, item in enumerate(items):
    title = item.get('title', '')
    url = item.get('url', '').strip()
    if not url:
        continue
    date_str = item.get('date', '')

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: issue body 兜底 (替代 V37.9.36 占位符 "贡献：社区 issue / 价值：⭐⭐⭐")
        degraded_count += 1
        msg_lines.append(f"*{title}*")
        msg_lines.append(f"链接: {url}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, issue 摘要供参考:")
        body = item.get('body', '')
        body = re.sub(r'<[^>]+>', '', body).strip()[:300]
        if body:
            msg_lines.append(body)
        else:
            msg_lines.append("(Issue 无摘要数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        # V37.9.51: 解析 6 字段 (V37.9.45 hf_papers / V37.9.50 / V37.9.51 hn 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
        title_display = fields['cn_title'] or title
        msg_lines.append(f"*{title_display}* | {date_str}")
        msg_lines.append(f"链接: {url}")
        msg_lines.append("")
        if fields['highlights']:
            msg_lines.append("🔑 核心讨论点:")
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
        # V37.9.51: 🎚️ 项目对齐度展示 + rule_check 验证 (V37.9.45 hf_papers / V37.9.50 / V37.9.51 hn 同款)
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            # rule_check: LLM ⭐ 评分 vs keyword-based rule 一致性
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = title + body (issue 场景, V37.9.51 hn 同款模式适配)
                        rule_content = item.get('title', '') + ' ' + (re.sub(r'<[^>]+>', '', item.get('body', '') or '').strip())
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[discussions] V37.9.62 rule_check 失败 item={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1
        # KB source line
        rating_short = (fields['rating'][:30] if fields['rating'] else '') or '—'
        with open(kb_source, "a") as f:
            f.write(f"- **[{title}]({url})** | {today} | {rating_short}\n")

    msg_lines.append("---")
    msg_lines.append("")
    sent += 1

# V37.9.51: 末尾追加高对齐统计 (Opportunity Radar #2)
total_items = sent  # sent = 实际推送的条目数
if total_items > 0:
    msg_lines.append(f"━━━ 本轮高对齐 issue (项目对齐度 ⭐≥4): {high_alignment_count}/{total_items} 条 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[discussions] 消息组装: {len(items)} 条 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count}, sent {sent})", file=sys.stderr)
PYEOF

SENT_COUNT=$(grep -c "^---$" "$MSG" 2>/dev/null) || SENT_COUNT=0
SENT_COUNT="${SENT_COUNT:-0}"
SENT_COUNT="$(echo "$SENT_COUNT" | tr -d '[:space:]')"
SENT_COUNT="${SENT_COUNT:-0}"

# ── 推送 WhatsApp + Discord (V37.9.21/V37.9.40 多窗口分片: >8000 字才切, ≤8000 单段) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG" | tr -d ' ')
WA_SENT=false

if [ "$SENT_COUNT" -eq 0 ]; then
    log "无有效条目, 跳过推送"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
elif [ "$TOTAL_LEN" -le 8000 ]; then
    MSG_CONTENT="$(cat "$MSG")"
    if openclaw message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${SENT_COUNT} 条 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
        openclaw message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    else
        # 过滤已知无害警告（feishu 插件 duplicate id、plugins.allow empty）
        REAL_ERR=$(grep -v -E "feishu|plugin.*duplicate|plugins\.allow|Config warnings" "$SEND_ERR" 2>/dev/null || true)
        if [ -z "$REAL_ERR" ]; then
            log "已推送 ${SENT_COUNT} 条 (单段, 忽略插件警告)"
            WA_SENT=true
            openclaw message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
        else
            log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
        fi
    fi
else
    # 总长 >8000 → 多窗口切片 (V37.9.21 同款 mktemp + sleep 1s 防乱序)
    WA_CHUNK_DIR=$(mktemp -d)
    trap 'rm -rf "$WA_CHUNK_DIR"; rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

    $PYTHON3 - "$MSG" "$WA_CHUNK_DIR" "$day" << 'PYEOF'
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
            chunk = chunk.replace(f"🦞 OpenClaw 社区新动态 ({today})",
                                  f"🦞 OpenClaw 社区新动态 [1/{total_parts}] ({today})", 1)
        else:
            chunk = f"🦞 OpenClaw 社区新动态 [{i+1}/{total_parts}] ({today}) (续)\n\n" + chunk
    with open(os.path.join(chunk_dir, f"{i:03d}.txt"), 'w', encoding='utf-8') as f:
        f.write(chunk)
PYEOF

    WA_PARTS_TOTAL=$(ls "$WA_CHUNK_DIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
    WA_SENT_OK=0
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        CHUNK_CONTENT="$(cat "$chunk_file")"
        if openclaw message send --channel whatsapp --target "$TO" --message "$CHUNK_CONTENT" --json >/dev/null 2>>"$SEND_ERR"; then
            WA_SENT_OK=$((WA_SENT_OK + 1))
        fi
        openclaw message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$CHUNK_CONTENT" --json >/dev/null 2>&1 || true
        sleep 1  # 防 WhatsApp 消息乱序 (V37.9.21 契约)
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
elif [ "$SENT_COUNT" -gt 0 ]; then
    log "ERROR: 推送全失败"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi
rm -f "$SEND_ERR"

bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)

#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# GitHub Trending ML/AI 仓库监控 v1
# 每天 1 次（14:00 HKT）由系统 crontab 触发
# 核心价值：从代码端发现趋势，与论文监控互补
# 设计：GitHub Search API（免费，无需认证），LLM 翻译+评价
#
# V37.9.44 fail-fast 升级 (V37.9.39 S2 / V37.9.40 DBLP+AI Leaders X /
#   V37.9.41 HN / V37.9.43 arxiv 同款机械迁移):
#   - source notify.sh + send_alert() helper ([SYSTEM_ALERT] 前缀走 Discord #alerts)
#   - LLM 三层检测 (HTTP error / parse fail / empty content)
#   - per-repo 独立 LLM 调用 + retry 5/10/20s × 3 (替代单次 batch + 占位符 fallback)
#   - 5 字段深度 prompt (📌 项目名 / 🔑 核心功能 / 💡 技术亮点 / 🎯 实践启发 / ⭐ 评级)
#   - LLM_DEGRADED fallback 用 GitHub repo description 兜底 (替代 V37.9.36 占位符反模式)
#   - 多窗口切片 (>8000 字 + sleep 1s + [i/N] + 续段, V37.9.21 同款)
#   - 三档 status: ok / partial_degraded / llm_failed (全失败 → exit 1 fail-fast)
set -eo pipefail

# 防重叠执行
LOCK="/tmp/github_trending.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[gh_trending] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/github_trending"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/github_trending.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_REPOS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"
PYTHON3=/usr/bin/python3

log() { echo "[$TS] gh_trending: $1" >&2; }

# V37.9.44: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5 kb_review / V37.8.10 kb_evening / V37.9.16 kb_deep_dive /
#  V37.9.36-37 rss_blogs / V37.9.39 S2 / V37.9.40 DBLP+AI Leaders X /
#  V37.9.41 HN / V37.9.43 arxiv 同款模式)
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

# V37.9.44: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] github_trending LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 GitHub 热门仓库精选 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# GitHub Trending ML/AI" > "$KB_SRC"

# ── 1. 搜索最近7天的热门 ML/AI 仓库 ─────────────────────────────────
WEEK_AGO=$($PYTHON3 -c "from datetime import datetime,timedelta; print((datetime.now()-timedelta(days=7)).strftime('%Y-%m-%d'))")
FEED_FILE="$CACHE/gh_repos.json"
HEADER_FILE="$CACHE/curl_headers.txt"

# 搜索策略：多个 topic 关键词，按 stars 排序
# GitHub Search API：免费未认证 10次/分钟，认证 30次/分钟
# GitHub Search API 限制最多 5 个 AND/OR/NOT 操作符
TOPICS="machine-learning+OR+deep-learning+OR+llm+OR+ai-agent+OR+diffusion-model"
SEARCH_URL="https://api.github.com/search/repositories?q=${TOPICS}+created:%3E${WEEK_AGO}&sort=stars&order=desc&per_page=50"

FETCH_OK=false
for attempt in 1 2 3; do
  HTTP_CODE=$(curl -sS --max-time 30 -w '%{http_code}' \
    -H "Accept: application/vnd.github.v3+json" \
    -H "User-Agent: openclaw-gh-trending/1.0" \
    -o "$FEED_FILE" \
    "$SEARCH_URL" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    if $PYTHON3 -c "import json; json.load(open('$FEED_FILE'))" 2>/dev/null; then
      FETCH_OK=true
      break
    else
      log "WARN: GitHub API 返回非JSON内容（attempt ${attempt}）"
    fi
  else
    log "WARN: GitHub API HTTP ${HTTP_CODE}（attempt ${attempt}）"
  fi

  if [ "$HTTP_CODE" = "403" ] || [ "$HTTP_CODE" = "429" ]; then
    WAIT=$((60 * attempt))
    log "限速退避等待 ${WAIT}s"
    sleep "$WAIT"
  else
    sleep "$((attempt * 10))"
  fi
done

if [ "$FETCH_OK" != "true" ]; then
  log "ERROR: GitHub API 3次重试均失败（最后HTTP=$HTTP_CODE）"
  printf '{"time":"%s","status":"fetch_failed","new":0,"http_code":"%s"}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
  exit 1
fi
echo "[gh_trending] API抓取完成（HTTP 200）"

# ── 2. 解析JSON → 筛选去重 ───────────────────────────────────────────
REPOS_FILE="$CACHE/repos.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! $PYTHON3 - "$FEED_FILE" "$MAX_REPOS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$REPOS_FILE"
import sys, json

feed_file = sys.argv[1]
max_repos = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

with open(feed_file) as f:
    data = json.load(f)

items = data.get("items", [])

repos = []
for item in items:
    repo_id = str(item.get("id", ""))
    full_name = item.get("full_name", "")

    if not repo_id or repo_id in seen_ids or full_name in seen_ids:
        continue

    name = item.get("name", "")
    description = (item.get("description", "") or "")[:200]
    stars = item.get("stargazers_count", 0)
    language = item.get("language", "") or ""
    html_url = item.get("html_url", "")
    created = (item.get("created_at", "") or "")[:10]
    topics = item.get("topics", [])

    # 过滤：至少 50 stars 才有意义
    if stars < 50:
        continue

    repos.append({
        "repo_id": repo_id,
        "full_name": full_name,
        "name": name,
        "description": description,
        "stars": stars,
        "language": language,
        "html_url": html_url,
        "created": created,
        "topics": topics[:5],
    })

# 按 stars 降序
repos.sort(key=lambda x: x.get("stars", 0), reverse=True)
repos = repos[:max_repos]

new_ids = []
for r in repos:
    print(json.dumps(r, ensure_ascii=False))
    new_ids.append(r["repo_id"])

with open(new_ids_file, 'w') as f:
    for rid in new_ids:
        f.write(rid + '\n')

print(f"[gh_trending] 解析完成: {len(repos)} 个新仓库（跳过 {len(seen_ids)} 个已发送）", file=sys.stderr)
PYEOF
then
  log "WARN: JSON解析失败"
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

REPO_COUNT="$(wc -l < "$REPOS_FILE" | tr -d ' ')"
if [ "$REPO_COUNT" -eq 0 ]; then
    log "无新仓库（全部已发送），跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[gh_trending] 新仓库: ${REPO_COUNT} 个"

# ── 3-4. V37.9.44: 每 repo 独立调 LLM (5 字段深度分析 + 按评级动态调长度 + retry 3 次) ─
# 老 V37.8: 单次调用全部 N 个 repo + 3 字段输出 + 失败硬编码占位符 (V37.9.36 反模式)
# 新 V37.9.44: 每 repo 独立调用 + 独立 retry (5s/10s/20s) + 5 字段深度
#   📌 中文项目名 / 🔑 核心功能 / 💡 技术亮点 / 🎯 实践启发 / ⭐ 评级
# 全部失败 → fail-fast (V37.9.36 契约保留)
# 部分失败 → partial_degraded + 失败 repo 标 [LLM_DEGRADED] + GitHub description 兜底

LLM_RAW="$CACHE/llm_raw_last.txt"   # 兼容: 保留上一次失败响应做 forensic
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单 repo LLM 调用 + retry ────────────────────────────────
# 输入: $1 = single_repo_prompt 文件路径, $2 = repo_idx
# 输出: stdout = 成功时 LLM content; 失败 → return 1, 全局 LAST_LLM_FAIL_REASON 含原因
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

        # 保存最后一次响应做 forensic (覆盖式)
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
            log "WARN: repo $idx attempt $((attempt+1))/3: $LAST_LLM_FAIL_REASON"
            if [ $attempt -lt 2 ]; then
                sleep "${backoffs[$attempt]}"
            fi
            continue
        fi

        if [ -z "${parse_out// }" ]; then
            LAST_LLM_FAIL_REASON="empty_content"
            log "WARN: repo $idx attempt $((attempt+1))/3: empty content"
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

# ── 主循环: 每 repo 独立调 LLM (5 字段深度) ─────────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""
TOTAL_NEW="$REPO_COUNT"

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$REPOS_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json

repos_file, idx = sys.argv[1], int(sys.argv[2])
repos = []
with open(repos_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            repos.append(json.loads(line))
r = repos[idx]
full_name = r['full_name']
description = r.get('description', '')[:300]
stars = r.get('stars', 0)
language = r.get('language', '') or 'Unknown'
created = r.get('created', '')
topics = r.get('topics', [])

prompt = """你是 AI 技术深度分析师。对以下 GitHub 热门仓库输出 5 字段中文分析:

📌 中文项目名: 信达雅翻译, 不超过 25 字 (技术术语保持精确)
🔑 核心功能: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出仓库做了什么 / 解决了什么问题 / 关键创新
💡 技术亮点: 揭示项目技术栈 / 架构设计 / 与已有项目对比 / 局限性
   长度按评级动态调整: ⭐⭐⭐ 写约 100-150 字 / ⭐⭐⭐⭐ 写约 250-400 字 / ⭐⭐⭐⭐⭐ 写约 500-800 字 (旗舰项目充分展开)
🎯 实践启发: 1-3 条对 AI 工程师 / 研究者 / 开发者的具体行动建议 (是否值得 fork / 集成 / 学习架构), 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该看 / 何时用 / 适配什么场景)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的仓库描述、标签、元数据中的信息, 严禁虚构未提及的事实/数据
- stars 数 + 创建日期作为热度参考但不影响技术价值评级
- 如描述太短 (<50 字) 不足以判断, 标 ⭐ 较低 + 写"基于描述的初步判断"
- 严禁推断仓库的具体内部实现细节除非描述提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 5 字段, 字段间用空行分隔):

📌 中文项目名: <你的翻译>

🔑 核心功能:
- 功能1
- 功能2

💡 技术亮点:
<段落, 长度按上述评级规则>

🎯 实践启发:
- 启发1
- 启发2

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: <场景描述>

---

"""
prompt += f"仓库: {full_name}\n"
prompt += f"⭐ Stars: {stars} | 主语言: {language}"
if created:
    prompt += f" | 创建于: {created}"
prompt += "\n"
if topics:
    prompt += f"标签: {', '.join(topics[:5])}\n"
if description:
    prompt += f"项目描述:\n{description}\n"
print(prompt)
PYEOF

    log "调用 LLM 分析 repo $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        # 成功
        $PYTHON3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        # 全 retry 失败 → 标 degraded, 不阻塞其他 repo
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: repo $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        $PYTHON3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定整体 status (V37.9.36 fail-fast 契约保留) ──────────────────
if [ "$TOTAL_FAILED" -eq "$TOTAL_NEW" ]; then
    # 全部失败 → fail-fast (V37.9.36 同款)
    log "ERROR: 全部 $TOTAL_NEW 个 repo LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 个 repo LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 个 repo 失败 — 走 partial_degraded (失败 repo 标 [LLM_DEGRADED] + GitHub description 兜底)"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 个 repo LLM 部分失败 (其余正常推送, 失败 repo 标 [LLM_DEGRADED])"
fi
echo "[gh_trending] LLM 调用完成: 成功 $((TOTAL_NEW - TOTAL_FAILED))/$TOTAL_NEW"

# ── 5. V37.9.44: 5 字段 emit (5-field key-based parser + LLM_DEGRADED + 多窗口切片) ──
MSG_FILE="$CACHE/gh_message.txt"
$PYTHON3 - "$REPOS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re

repos_file, results_file, day, msg_file = sys.argv[1:5]

repos = []
with open(repos_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            repos.append(json.loads(line))
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.37/V37.9.39 5 字段 key-based parser (V37.8.7 ontology_parser 同款模式)
def parse_5field_output(content):
    """从 LLM 输出解析 5 字段, key-based + tolerant.

    返回 dict: cn_name / highlights / insight / practice / rating
    """
    fields = {
        'cn_name': '',
        'highlights': '',
        'insight': '',
        'practice': '',
        'rating': '',
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
        # 📌 中文项目名
        if line.lstrip().startswith('📌'):
            flush()
            current_field = 'cn_name'
            current_buffer = []
            m = re.match(r'.*📌\s*(?:中文)?项目名\s*[:：]?\s*(.*)', line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # 🔑 核心功能 (项目场景)
        if line.lstrip().startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        # 💡 技术亮点 (项目场景)
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


msg_lines = [f"\U0001F680 GitHub 热门 AI/ML 仓库 ({day})", ""]

degraded_count = 0
llm_ok_count = 0
for i, repo in enumerate(repos):
    full_name = repo['full_name']
    stars = repo.get('stars', 0)
    lang = repo.get('language', '')
    topics = repo.get('topics', [])
    html_url = repo['html_url']
    created = repo.get('created', '')

    badge_parts = [f"⭐ {stars}"]
    if lang:
        badge_parts.append(lang)
    if created:
        badge_parts.append(f"创建于{created}")
    badge = ' | '.join(badge_parts)

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 用 GitHub repo description 给用户最低保障 (替代 V37.9.36 占位符反模式)
        degraded_count += 1
        msg_lines.append(f"*{repo['name']}*")
        msg_lines.append(f"{html_url} ({badge})")
        if topics:
            msg_lines.append(f"标签：{', '.join(topics[:3])}")
        msg_lines.append("")
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 仓库描述供参考:")
        fallback = repo.get('description', '')
        fallback = fallback[:300] if fallback else ''
        if fallback:
            msg_lines.append(fallback)
        else:
            msg_lines.append("(GitHub 无描述数据, 请直接点链接阅读 README)")
        msg_lines.append("")
    else:
        # 解析 5 字段
        fields = parse_5field_output(result.get('content', ''))
        name_display = fields['cn_name'] or repo['name']
        msg_lines.append(f"*{name_display}*")
        msg_lines.append(f"{html_url} ({badge})")
        if topics:
            msg_lines.append(f"标签：{', '.join(topics[:3])}")
        msg_lines.append("")
        if fields['highlights']:
            msg_lines.append("🔑 核心功能:")
            msg_lines.append(fields['highlights'])
            msg_lines.append("")
        if fields['insight']:
            msg_lines.append("💡 技术亮点:")
            msg_lines.append(fields['insight'])
            msg_lines.append("")
        if fields['practice']:
            msg_lines.append("🎯 实践启发:")
            msg_lines.append(fields['practice'])
            msg_lines.append("")
        if fields['rating']:
            msg_lines.append(fields['rating'])
            msg_lines.append("")
        if fields['cn_name'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[gh_trending] 消息组装完成: {len(repos)} 个 repo (LLM 解析成功 {llm_ok_count}, degraded {degraded_count})", file=sys.stderr)
PYEOF

# ── 6. 推送 WhatsApp + Discord (V37.9.21/V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段发) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    # 单段直发 (≤4000 不折叠 / 4000-8000 客户端自动折叠 2 气泡, V37.9.35 已验证)
    MSG_CONTENT="$(cat "$MSG_FILE")"
    if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
        log "已推送 ${REPO_COUNT} 个 repo (单段, $TOTAL_LEN 字)"
        WA_SENT=true
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_TECH:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    # 总长 >8000 → 多窗口切片 (V37.9.21 同款 mktemp + sleep 1s 防乱序)
    WA_CHUNK_DIR=$(mktemp -d)
    trap 'rm -rf "$WA_CHUNK_DIR"' EXIT INT TERM

    $PYTHON3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$DAY" << 'PYEOF'
import sys, os, re

msg_file, chunk_dir, day = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(msg_file, encoding='utf-8').read()
MAX_CHUNK = 4000

# 按 "\n---\n" 切分 repo 块, 第一块是 header "🚀 GitHub 热门 AI/ML 仓库 (date)"
blocks = re.split(r'\n---\n', content)
header_block = blocks[0]
repo_blocks = [b for b in blocks[1:] if b.strip()]

chunks = []
current = header_block
for block in repo_blocks:
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
            chunk = chunk.replace(f"\U0001F680 GitHub 热门 AI/ML 仓库 ({day})",
                                  f"\U0001F680 GitHub 热门 AI/ML 仓库 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"\U0001F680 GitHub 热门 AI/ML 仓库 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
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
    log "已推送 ${REPO_COUNT} 个 repo (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
    if [ "$WA_SENT_OK" -gt 0 ]; then
        WA_SENT=true
    fi
fi

if [ "$WA_SENT" = "true" ]; then
    if [ -f "$NEW_IDS_FILE" ]; then
        cat "$NEW_IDS_FILE" >> "$SEEN_FILE"
        log "已标记 ${REPO_COUNT} 个为已发送"
    fi
    # status 区分 ok / partial_degraded
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$REPO_COUNT" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$REPO_COUNT" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$REPO_COUNT" > "$STATUS_FILE"
fi

# ── 7. KB归档 ────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# GitHub Trending ML/AI ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "github-trending-ml" "note" 2>/dev/null || true
    echo "[gh_trending] KB写入完成"
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
    echo "[gh_trending] seen缓存已裁剪至300条"
fi

# ── 10. rsync备份 ────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

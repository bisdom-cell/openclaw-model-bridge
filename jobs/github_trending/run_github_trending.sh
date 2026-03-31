#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# GitHub Trending ML/AI 仓库监控 v1
# 每天 1 次（14:00 HKT）由系统 crontab 触发
# 核心价值：从代码端发现趋势，与论文监控互补
# 设计：GitHub Search API（免费，无需认证），LLM 翻译+评价
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

log() { echo "[$TS] gh_trending: $1"; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# GitHub Trending ML/AI" > "$KB_SRC"

# ── 1. 搜索最近7天的热门 ML/AI 仓库 ─────────────────────────────────
WEEK_AGO=$($PYTHON3 -c "from datetime import datetime,timedelta; print((datetime.now()-timedelta(days=7)).strftime('%Y-%m-%d'))")
FEED_FILE="$CACHE/gh_repos.json"
HEADER_FILE="$CACHE/curl_headers.txt"

# 搜索策略：多个 topic 关键词，按 stars 排序
# GitHub Search API：免费未认证 10次/分钟，认证 30次/分钟
TOPICS="machine-learning+OR+deep-learning+OR+llm+OR+large-language-model+OR+transformer+OR+diffusion+OR+rag+OR+ai-agent"
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

# ── 3. 构建LLM prompt ────────────────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
$PYTHON3 - "$REPOS_FILE" << 'PYEOF' > "$PROMPT_FILE"
import sys, json

repos = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line:
            repos.append(json.loads(line))

prompt = """你是AI技术编辑。对以下每个GitHub仓库严格输出三行（不要输出任何其他内容）：
第一行：中文项目名（≤20字，翻译或意译，不加任何前缀标签）
第二行：亮点：[1句话≤50字，说明核心功能或创新点]
第三行：推荐：⭐（1到5个星，评估对AI从业者的实用价值）
每个仓库之间用一行 --- 分隔。不要输出序号。

"""
for i, r in enumerate(repos, 1):
    topics_str = ", ".join(r.get("topics", []))
    prompt += f"仓库{i}：{r['full_name']}（⭐{r['stars']}，{r['language']}）\n"
    prompt += f"描述：{r['description']}\n"
    if topics_str:
        prompt += f"标签：{topics_str}\n"
    prompt += "\n"

print(prompt)
PYEOF

# ── 4. 调用LLM ──────────────────────────────────────────────────────
LLM_RAW="$CACHE/llm_raw_last.txt"
PAYLOAD_FILE="$CACHE/llm_payload.json"
$PYTHON3 -c "
import json
prompt = open('$CACHE/llm_prompt.txt').read()
with open('$CACHE/llm_payload.json', 'w') as f:
    json.dump({
        'model': 'Qwen3-235B-A22B-Instruct-2507-W8A8',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 4096,
        'temperature': 0.3
    }, f)
"

LLM_RESP=$(curl -s --max-time 120 \
    -H "Content-Type: application/json" \
    -d "@$PAYLOAD_FILE" \
    http://127.0.0.1:5002/v1/chat/completions 2>"$LLM_RAW.stderr" || true)

echo "$LLM_RESP" > "$LLM_RAW"

LLM_CONTENT=$(echo "$LLM_RESP" | $PYTHON3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception:
    pass
" 2>/dev/null || true)

if [ -z "${LLM_CONTENT// }" ]; then
    ERR_MSG="⚠️ GitHub Trending LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"
echo "[gh_trending] LLM调用成功"

# ── 5. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/gh_message.txt"
$PYTHON3 - "$REPOS_FILE" "$CACHE/llm_content.txt" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re

repos_file, llm_file, day, msg_file = sys.argv[1:5]

repos = []
with open(repos_file) as f:
    for line in f:
        line = line.strip()
        if line:
            repos.append(json.loads(line))

with open(llm_file) as f:
    llm_content = f.read()

# 解析 LLM 输出（项目名/亮点/推荐三行一组）
NAME_PREFIXES = ['第一行：', '第1行：', '标题：', '中文项目名：', '项目名：']
HIGHLIGHT_PREFIXES = ['第二行：', '第2行：', '亮点：']
STARS_PREFIXES = ['第三行：', '第3行：', '推荐：']

def clean_prefix(line, prefixes):
    for p in prefixes:
        if line.startswith(p):
            return line[len(p):].strip()
    return line

parsed_blocks = []
pending_name = None
pending_highlight = None

for raw_line in llm_content.split('\n'):
    line = raw_line.strip()
    if not line:
        continue
    if re.match(r'^[-=*]{3,}$', line):
        continue
    if re.match(r'^(仓库\d+[：:]?\s*$|\d+[.、)]\s*$)', line):
        continue

    if '推荐' in line and '⭐' in line:
        stars_line = clean_prefix(line, STARS_PREFIXES)
        if not stars_line.startswith('推荐：'):
            stars_line = '推荐：' + stars_line.lstrip('推荐：').lstrip('推荐:')
        parsed_blocks.append((
            pending_name or '',
            pending_highlight or '亮点：AI/ML相关项目',
            stars_line
        ))
        pending_name = None
        pending_highlight = None
        continue

    if line.startswith('亮点：') or line.startswith('亮点:'):
        pending_highlight = clean_prefix(line, HIGHLIGHT_PREFIXES)
        if not pending_highlight.startswith('亮点：'):
            pending_highlight = '亮点：' + pending_highlight
        continue

    if pending_name is None:
        name = clean_prefix(line, NAME_PREFIXES)
        name = re.sub(r'^\d+[.、)\]]\s*', '', name)
        name = name.strip('*').strip()
        pending_name = name

llm_ok = 0
msg_lines = [f"\U0001F680 GitHub 热门 AI/ML 仓库 ({day})", ""]

for i, repo in enumerate(repos):
    stars = repo.get('stars', 0)
    lang = repo.get('language', '')
    topics = repo.get('topics', [])

    if i < len(parsed_blocks):
        cn_name, highlight, rec_stars = parsed_blocks[i]
        if cn_name:
            llm_ok += 1
        else:
            cn_name = repo['name']
    else:
        cn_name = repo['name']
        highlight = '亮点：AI/ML相关项目'
        rec_stars = '推荐：⭐⭐⭐'

    badge_parts = [f"\u2B50 {stars}"]
    if lang:
        badge_parts.append(lang)
    if repo.get('created', ''):
        badge_parts.append(f"创建于{repo['created']}")

    msg_lines.append(f"*{cn_name}*")
    msg_lines.append(f"{repo['html_url']} ({' | '.join(badge_parts)})")
    if topics:
        msg_lines.append(f"标签：{', '.join(topics[:3])}")
    msg_lines.append(highlight)
    msg_lines.append(rec_stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

total = len(repos)
print(f"[gh_trending] 消息组装完成: {total} 个，LLM解析成功 {llm_ok}/{total}", file=sys.stderr)
PYEOF

# ── 6. 推送WhatsApp ──────────────────────────────────────────────────
MSG_CONTENT="$(head -c 4000 "$MSG_FILE")"
SEND_ERR=$(mktemp)
if "$OPENCLAW" message send --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${REPO_COUNT} 个仓库"
    if [ -f "$NEW_IDS_FILE" ]; then
        cat "$NEW_IDS_FILE" >> "$SEEN_FILE"
        log "已标记 ${REPO_COUNT} 个为已发送"
    fi
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$REPO_COUNT" > "$STATUS_FILE"
else
    log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
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
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} >> "$KB_SRC"

# ── 9. 清理seen缓存 ─────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
    echo "[gh_trending] seen缓存已裁剪至300条"
fi

# ── 10. rsync备份 ────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

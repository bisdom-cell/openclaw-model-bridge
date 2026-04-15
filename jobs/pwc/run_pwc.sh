#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Papers with Code 论文+代码监控 v1
# 每天 1 次（13:00 HKT）由系统 crontab 触发
# 核心价值：论文+代码实现关联，实用性最强
# 设计：PwC REST API（免费，无需认证），LLM 翻译+评价
set -eo pipefail

# 防重叠执行
LOCK="/tmp/pwc_monitor.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[pwc] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/pwc"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/pwc_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] pwc: $1" >&2; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# Papers with Code" > "$KB_SRC"

# ── 1. 抓取 PwC API（最近发布的论文）──────────────────────────────────
FEED_FILE="$CACHE/pwc_papers.json"
HEADER_FILE="$CACHE/curl_headers.txt"
FETCH_OK=false
for attempt in 1 2 3; do
  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "Accept: application/json" \
    -H "User-Agent: openclaw-pwc-monitor/1.0" \
    -D "$HEADER_FILE" \
    "https://paperswithcode.com/api/v1/papers/?ordering=-published&items_per_page=50" \
    -o "$FEED_FILE" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    if /usr/bin/python3 -c "import json; json.load(open('$FEED_FILE'))" 2>/dev/null; then
      FETCH_OK=true
      break
    else
      log "WARN: PwC API 返回非JSON内容（第${attempt}次）"
    fi
  else
    log "WARN: PwC API 返回 HTTP $HTTP_CODE（第${attempt}次）"
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
  log "ERROR: PwC API 3次重试均失败（最后HTTP=$HTTP_CODE）"
  printf '{"time":"%s","status":"fetch_failed","new":0,"http_code":"%s"}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
  exit 1
fi
echo "[pwc] API抓取完成（HTTP 200）"

# ── 2. 解析JSON → 筛选有代码的论文 → 去重 ────────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! /usr/bin/python3 - "$FEED_FILE" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import sys, json

feed_file = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

with open(feed_file) as f:
    data = json.load(f)

# PwC API 返回 {"count":N, "next":url, "results":[...]}
results = data.get("results", data) if isinstance(data, dict) else data
if not isinstance(results, list):
    results = []

papers = []
for item in results:
    paper_id = item.get("id", item.get("paper_id", ""))
    if not paper_id:
        paper_id = (item.get("url_abs", "") or "").split("/")[-1]
    paper_id = str(paper_id)

    if not paper_id or paper_id in seen_ids:
        continue

    title = (item.get("title", "") or "").strip()
    if not title:
        continue

    # 提取信息
    authors_raw = item.get("authors", [])
    if isinstance(authors_raw, list) and authors_raw:
        if isinstance(authors_raw[0], dict):
            first_author = authors_raw[0].get("name", "Unknown")
        else:
            first_author = str(authors_raw[0])
    elif isinstance(authors_raw, str):
        first_author = authors_raw.split(",")[0].strip()
    else:
        first_author = "Unknown"

    abstract = (item.get("abstract", "") or "")[:300]
    published = (item.get("published", "") or "")[:10]
    url_abs = item.get("url_abs", "") or ""
    url_pdf = item.get("url_pdf", "") or ""
    pwc_url = "https://paperswithcode.com" + item.get("proceeding", item.get("url", "")) if item.get("url") else ""

    # 代码仓库数量（API 直接提供或需要额外请求）
    repo_count = item.get("repository_count", item.get("repos_count", 0)) or 0

    papers.append({
        "paper_id": paper_id,
        "title": title,
        "first_author": first_author,
        "date": published,
        "abstract": abstract,
        "url_abs": url_abs,
        "url_pdf": url_pdf,
        "repo_count": repo_count,
    })

# 优先有代码的论文，然后按日期排序
papers.sort(key=lambda x: (-x.get("repo_count", 0), x.get("date", "")), reverse=False)
papers.sort(key=lambda x: x.get("repo_count", 0), reverse=True)
papers = papers[:max_papers]

new_ids = []
for p in papers:
    print(json.dumps(p, ensure_ascii=False))
    new_ids.append(p["paper_id"])

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

print(f"[pwc] 解析完成: {len(papers)} 篇新论文（跳过 {len(seen_ids)} 篇已发送）", file=sys.stderr)
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
echo "[pwc] 新论文: ${PAPER_COUNT} 篇"

# ── 2.5 获取每篇论文的 GitHub 仓库详情 ────────────────────────────────
ENRICHED_FILE="$CACHE/papers_enriched.jsonl"
/usr/bin/python3 - "$PAPERS_FILE" "$ENRICHED_FILE" << 'PYEOF'
import sys, json, urllib.request, urllib.error, time

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
    # 请求 /papers/{id}/repositories/ 获取代码仓库
    url = f"https://paperswithcode.com/api/v1/papers/{pid}/repositories/"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "openclaw-pwc-monitor/1.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            repo_data = json.loads(resp.read().decode())
        results = repo_data.get("results", repo_data) if isinstance(repo_data, dict) else repo_data
        if isinstance(results, list) and results:
            # 按 stars 降序取最佳仓库
            results.sort(key=lambda r: r.get("stars", 0), reverse=True)
            best = results[0]
            p["github_url"] = best.get("url", "")
            p["github_stars"] = best.get("stars", 0)
            p["framework"] = best.get("framework", "")
            p["is_official"] = best.get("is_official", False)
            p["repo_count"] = len(results)
        time.sleep(0.5)  # 保守限速，每篇间隔 0.5s
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        print(f"[pwc] WARN: 获取 {pid} 仓库失败: {e}", file=sys.stderr)

with open(enriched_file, 'w') as f:
    for p in papers:
        f.write(json.dumps(p, ensure_ascii=False) + '\n')

has_code = sum(1 for p in papers if p.get("github_url"))
print(f"[pwc] 仓库详情获取完成: {has_code}/{len(papers)} 篇有代码", file=sys.stderr)
PYEOF
# 用 enriched 版本替换原始文件
if [ -f "$ENRICHED_FILE" ]; then
    mv "$ENRICHED_FILE" "$PAPERS_FILE"
fi

# ── 3. 构建LLM prompt ────────────────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
/usr/bin/python3 - "$PAPERS_FILE" << 'PYEOF' > "$PROMPT_FILE"
import sys, json

papers = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))

prompt = """你是AI论文编辑。对以下每篇论文严格输出三行（不要输出任何其他内容）：
第一行：中文标题（≤25字，翻译或意译，不加任何前缀标签）
第二行：贡献：[1句话≤50字，说明核心贡献]
第三行：价值：⭐（1到5个星，评估对AI从业者的参考价值）
每篇之间用一行 --- 分隔。不要输出序号。

"""
for i, p in enumerate(papers, 1):
    repos = f"（{p['repo_count']}个代码仓库）" if p.get("repo_count", 0) > 0 else "（无代码）"
    prompt += f"论文{i}：{p['title']} {repos}\n摘要：{p['abstract']}\n\n"

print(prompt)
PYEOF

# ── 4. 调用LLM ──────────────────────────────────────────────────────
LLM_RAW="$CACHE/llm_raw_last.txt"
PAYLOAD_FILE="$CACHE/llm_payload.json"
/usr/bin/python3 -c "
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

LLM_CONTENT=$(echo "$LLM_RESP" | /usr/bin/python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception:
    pass
" 2>/dev/null || true)

if [ -z "${LLM_CONTENT// }" ]; then
    ERR_MSG="⚠️ PwC Monitor LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"
echo "[pwc] LLM调用成功"

# ── 5. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/pwc_message.txt"
/usr/bin/python3 - "$PAPERS_FILE" "$CACHE/llm_content.txt" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re

papers_file, llm_file, day, msg_file = sys.argv[1:5]

papers = []
with open(papers_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("["):
            papers.append(json.loads(line))

with open(llm_file) as f:
    llm_content = f.read()

# 复用标准 LLM 输出解析逻辑
TITLE_PREFIXES = ['第一行：', '第1行：', '标题：', '中文标题：']
CONTRIB_PREFIXES = ['第二行：', '第2行：']
STARS_PREFIXES = ['第三行：', '第3行：']

def clean_prefix(line, prefixes):
    for p in prefixes:
        if line.startswith(p):
            return line[len(p):].strip()
    return line

parsed_blocks = []
pending_title = None
pending_contrib = None

for raw_line in llm_content.split('\n'):
    line = raw_line.strip()
    if not line:
        continue
    if re.match(r'^[-=*]{3,}$', line):
        continue
    if re.match(r'^(论文\d+[：:]?\s*$|\d+[.、)]\s*$|Paper\s+\d+[：:]?\s*$)', line):
        continue

    if '价值' in line and '⭐' in line:
        stars_line = clean_prefix(line, STARS_PREFIXES)
        if not stars_line.startswith('价值：'):
            stars_line = '价值：' + stars_line.lstrip('价值：').lstrip('价值:')
        if not stars_line.startswith('价值：'):
            stars_line = '价值：' + stars_line
        parsed_blocks.append((
            pending_title or '',
            pending_contrib or '贡献：AI领域相关研究',
            stars_line
        ))
        pending_title = None
        pending_contrib = None
        continue

    if line.startswith('贡献：') or line.startswith('贡献:'):
        pending_contrib = clean_prefix(line, CONTRIB_PREFIXES)
        if not pending_contrib.startswith('贡献：'):
            pending_contrib = '贡献：' + pending_contrib
        continue
    stripped = clean_prefix(line, CONTRIB_PREFIXES)
    if stripped != line and ('贡献' in stripped[:3]):
        pending_contrib = stripped if stripped.startswith('贡献：') else '贡献：' + stripped
        continue

    if pending_title is None:
        title = clean_prefix(line, TITLE_PREFIXES)
        title = re.sub(r'^\d+[.、)\]]\s*', '', title)
        title = title.strip('*').strip()
        pending_title = title

llm_ok = 0
msg_lines = [f"\U0001F4BB Papers with Code 精选 ({day})", ""]

for i, paper in enumerate(papers):
    github_url = paper.get('github_url', '')
    github_stars = paper.get('github_stars', 0)
    framework = paper.get('framework', '')
    is_official = paper.get('is_official', False)
    repo_count = paper.get('repo_count', 0)

    if i < len(parsed_blocks):
        cn_title, contrib, stars = parsed_blocks[i]
        if cn_title:
            llm_ok += 1
        else:
            cn_title = paper['title']
    else:
        cn_title = paper['title']
        contrib = "贡献：AI领域相关研究"
        stars = "价值：⭐⭐⭐"

    msg_lines.append(f"*{cn_title}*")
    msg_lines.append(f"作者：{paper['first_author']} 等")

    # 论文链接
    link = paper.get('url_abs', '') or f"https://paperswithcode.com/paper/{paper.get('paper_id', '')}"
    msg_lines.append(f"论文：{link}")

    # GitHub 仓库详情（核心价值）
    if github_url:
        badge_parts = [f"\u2B50 {github_stars}"]
        if framework:
            badge_parts.append(framework)
        if is_official:
            badge_parts.append("官方")
        if repo_count > 1:
            badge_parts.append(f"共{repo_count}个仓库")
        msg_lines.append(f"代码：{github_url} ({' | '.join(badge_parts)})")
    else:
        msg_lines.append("代码：暂无")

    msg_lines.append(contrib)
    msg_lines.append(stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

total = len(papers)
print(f"[pwc] 消息组装完成: {total} 篇，LLM解析成功 {llm_ok}/{total}", file=sys.stderr)
PYEOF

# ── 6. 推送WhatsApp ──────────────────────────────────────────────────
MSG_CONTENT="$(head -c 4000 "$MSG_FILE")"
SEND_ERR=$(mktemp)
if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${PAPER_COUNT} 篇论文"
    "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_PAPERS:-}" --message "$MSG_CONTENT" --json >/dev/null 2>&1 || true
    if [ -f "$NEW_IDS_FILE" ]; then
        cat "$NEW_IDS_FILE" >> "$SEEN_FILE"
        log "已标记 ${PAPER_COUNT} 篇为已发送"
    fi
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
else
    log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
fi

# ── 7. KB归档 ────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# Papers with Code ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "pwc-papers" "note" 2>/dev/null || true
    echo "[pwc] KB写入完成"
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
    echo "[pwc] seen缓存已裁剪至300条"
fi

# ── 10. rsync备份 ────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

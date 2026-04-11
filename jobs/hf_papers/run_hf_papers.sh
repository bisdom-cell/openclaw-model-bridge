#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Hugging Face Daily Papers 监控 v1
# 每天 2 次（10:00, 20:00 HKT）由系统 crontab 触发
# 与 ArXiv 互补：ArXiv 全量撒网，HF 社区精选（高 upvotes = 高关注度）
# 设计：JSON API 提取结构化数据，LLM 只负责翻译+评价
set -eo pipefail

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

log() { echo "[$TS] hf_papers: $1"; }

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

# ── 2.5 通过 GitHub Search 查找论文关联的代码仓库 ─────────────────────
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

# ── 3. 构建LLM prompt ────────────────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
python3 - "$PAPERS_FILE" << 'PYEOF' > "$PROMPT_FILE"
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
    prompt += f"论文{i}：{p['title']}\n摘要：{p['abstract']}\n\n"

print(prompt)
PYEOF

# ── 4. 调用LLM ──────────────────────────────────────────────────────
LLM_RAW="$CACHE/llm_raw_last.txt"
PAYLOAD_FILE="$CACHE/llm_payload.json"
python3 -c "
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

LLM_CONTENT=$(echo "$LLM_RESP" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception:
    pass
" 2>/dev/null || true)

if [ -z "${LLM_CONTENT// }" ]; then
    ERR_MSG="⚠️ HF Papers LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"
echo "[hf_papers] LLM调用成功"

# ── 5. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/hf_message.txt"
python3 - "$PAPERS_FILE" "$CACHE/llm_content.txt" "$DAY" "$MSG_FILE" << 'PYEOF'
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

# 复用 ArXiv 的 LLM 输出解析逻辑
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
msg_lines = [f"\U0001F525 HF社区精选论文 ({day})", ""]

for i, paper in enumerate(papers):
    upvotes = paper.get('upvotes', 0)
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
    msg_lines.append(f"作者：{paper['first_author']} 等 | \U0001F44D {upvotes}")
    msg_lines.append(f"论文：https://huggingface.co/papers/{paper.get('paper_id', '')}")

    # GitHub 代码仓库（通过 ArXiv ID 搜索关联）
    github_url = paper.get('github_url', '')
    if github_url:
        github_stars = paper.get('github_stars', 0)
        github_lang = paper.get('github_lang', '')
        badge_parts = [f"\u2B50 {github_stars}"]
        if github_lang:
            badge_parts.append(github_lang)
        msg_lines.append(f"代码：{github_url} ({' | '.join(badge_parts)})")

    msg_lines.append(contrib)
    msg_lines.append(stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

total = len(papers)
rate = llm_ok / total if total else 0
print(f"[hf_papers] 消息组装完成: {total} 篇，LLM解析成功 {llm_ok}/{total}", file=sys.stderr)
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
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

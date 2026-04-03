#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# Semantic Scholar 热门论文监控 v1
# 每天 1 次（08:00 HKT）由系统 crontab 触发
# 与 ArXiv/HF 互补：S2 提供引用量数据，发现"爆款论文"
# 使用 Semantic Scholar Academic Graph API (免费，1 req/sec)
# 搜索多个 AI 关键词，按 citationCount 降序，去重取 top N
set -eo pipefail

# 防重叠执行
LOCK="/tmp/semantic_scholar.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[s2] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/semantic_scholar"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/semantic_scholar_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"
S2_API="https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS="title,authors,abstract,url,citationCount,publicationDate,externalIds,tldr"
# 搜索最近 30 天的论文（引用量排序更有意义）
DATE_FROM="$(TZ=Asia/Hong_Kong date -v-30d '+%Y-%m-%d' 2>/dev/null || date -d '30 days ago' '+%Y-%m-%d')"
DATE_TO="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"

log() { echo "[$TS] s2: $1"; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# Semantic Scholar AI论文" > "$KB_SRC"

# ── 1. 多关键词搜索 + 合并去重 ──────────────────────────────────────
# 搜索多个关键词，每个取 20 篇，合并后按引用量排序
KEYWORDS=("large language model" "LLM agent" "RAG retrieval augmented" "multimodal AI" "RLHF alignment")
RAW_DIR="$CACHE/raw"
mkdir -p "$RAW_DIR"

FETCH_ERRORS=0
for i in "${!KEYWORDS[@]}"; do
  KW="${KEYWORDS[$i]}"
  OUTFILE="$RAW_DIR/search_${i}.json"
  ENCODED_KW=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$KW'))")

  # Semantic Scholar 免费 API: 严格限流，关键词间隔 5s
  [ "$i" -gt 0 ] && sleep 5

  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "User-Agent: openclaw-s2-monitor/1.0" \
    "${S2_API}?query=${ENCODED_KW}&fields=${FIELDS}&limit=20&publicationDateOrYear=${DATE_FROM}:${DATE_TO}&fieldsOfStudy=Computer+Science" \
    -o "$OUTFILE" 2>"$CACHE/curl_s2.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    echo "[s2] 搜索 '$KW' 成功"
  elif [ "$HTTP_CODE" = "429" ]; then
    # 指数退避重试：60s → 120s
    for RETRY in 60 120; do
      log "WARN: S2 API 429 for '$KW'，等待 ${RETRY}s 重试"
      sleep "$RETRY"
      HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
        -H "User-Agent: openclaw-s2-monitor/1.0" \
        "${S2_API}?query=${ENCODED_KW}&fields=${FIELDS}&limit=20&publicationDateOrYear=${DATE_FROM}:${DATE_TO}&fieldsOfStudy=Computer+Science" \
        -o "$OUTFILE" 2>"$CACHE/curl_s2.err") || HTTP_CODE="000"
      [ "$HTTP_CODE" = "200" ] && break
    done
    if [ "$HTTP_CODE" != "200" ]; then
      log "WARN: S2 重试仍失败 ($HTTP_CODE) for '$KW'"
      FETCH_ERRORS=$((FETCH_ERRORS + 1))
    else
      echo "[s2] 搜索 '$KW' 成功（重试后）"
    fi
  else
    log "WARN: S2 API 返回 HTTP $HTTP_CODE for '$KW'"
    FETCH_ERRORS=$((FETCH_ERRORS + 1))
  fi
done

if [ "$FETCH_ERRORS" -ge "${#KEYWORDS[@]}" ]; then
  log "ERROR: 所有关键词搜索均失败"
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# ── 2. 合并 + 去重 + 按引用量排序 → JSONL ────────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! python3 - "$RAW_DIR" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import sys, json, os, glob

raw_dir = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

# 合并所有搜索结果
all_papers = {}
for fpath in sorted(glob.glob(os.path.join(raw_dir, "search_*.json"))):
    try:
        with open(fpath) as f:
            data = json.load(f)
        for paper in data.get("data", []):
            pid = paper.get("paperId", "")
            if not pid or pid in seen_ids or pid in all_papers:
                continue
            title = (paper.get("title") or "").strip()
            if not title:
                continue
            all_papers[pid] = paper
    except (json.JSONDecodeError, KeyError):
        continue

# 按引用量降序
sorted_papers = sorted(all_papers.values(),
                       key=lambda x: x.get("citationCount", 0) or 0,
                       reverse=True)[:max_papers]

new_ids = []
for paper in sorted_papers:
    pid = paper.get("paperId", "")
    authors = paper.get("authors", [])
    first_author = authors[0].get("name", "Unknown") if authors else "Unknown"
    abstract = ((paper.get("abstract") or "")[:300])
    tldr = ""
    if paper.get("tldr") and isinstance(paper["tldr"], dict):
        tldr = paper["tldr"].get("text", "")
    citations = paper.get("citationCount", 0) or 0
    pub_date = paper.get("publicationDate", "") or ""
    ext_ids = paper.get("externalIds", {}) or {}
    arxiv_id = ext_ids.get("ArXiv", "")
    url = paper.get("url", "")

    out = {
        "paper_id": pid,
        "title": paper["title"],
        "first_author": first_author,
        "date": pub_date,
        "abstract": abstract,
        "tldr": tldr,
        "citations": citations,
        "arxiv_id": arxiv_id,
        "url": url
    }
    print(json.dumps(out, ensure_ascii=False))
    new_ids.append(pid)

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

print(f"[s2] 合并去重完成: {len(sorted_papers)} 篇（总搜索 {len(all_papers)}，跳过 {len(seen_ids)} 已发送）", file=sys.stderr)
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
echo "[s2] 新论文: ${PAPER_COUNT} 篇"

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
    citations = p.get('citations', 0)
    tldr = p.get('tldr', '')
    abstract = p['abstract']
    # 优先用 S2 的 TLDR（更简洁），否则用摘要
    summary = tldr if tldr else abstract
    prompt += f"论文{i}（引用{citations}次）：{p['title']}\n摘要：{summary}\n\n"

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
    ERR_MSG="⚠️ S2论文监控 LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"
echo "[s2] LLM调用成功"

# ── 5. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/s2_message.txt"
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
msg_lines = [f"\U0001F4C8 S2高引论文精选 ({day})", ""]

for i, paper in enumerate(papers):
    citations = paper.get('citations', 0)
    arxiv_id = paper.get('arxiv_id', '')
    url = paper.get('url', '')

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

    # 优先用 arxiv 链接（更通用），否则用 S2 链接
    link = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else url

    msg_lines.append(f"*{cn_title}*")
    msg_lines.append(f"作者：{paper['first_author']} 等 | 引用：{citations}")
    msg_lines.append(f"链接：{link}")
    msg_lines.append(contrib)
    msg_lines.append(stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

total = len(papers)
print(f"[s2] 消息组装完成: {total} 篇，LLM解析成功 {llm_ok}/{total}", file=sys.stderr)
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
    CONTENT="# Semantic Scholar AI论文 ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "semantic-scholar-ai" "note" 2>/dev/null || true
    echo "[s2] KB写入完成"
fi

# ── 8. 永久归档 ──────────────────────────────────────────────────────
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} >> "$KB_SRC"

# ── 9. 清理seen缓存 ─────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 1000 ]; then
    tail -500 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
    echo "[s2] seen缓存已裁剪至500条"
fi

# ── 10. rsync备份 ────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

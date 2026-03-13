#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# ArXiv AI论文监控 v1 — 脚本控制格式，替代 --announce 模式
# 每3小时整点 HKT 由系统crontab触发（与 HN 错开45分钟）
# 合并原 monitor-arxiv-ai-models + kb-save-arxiv 两个 openclaw cron 任务
# 设计原则：结构化数据(作者/链接/日期)由XML提取，LLM只负责翻译+评价
set -eo pipefail

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/arxiv_monitor"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/arxiv_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
LLM_RAW="$CACHE/llm_raw_last.txt"
MAX_PAPERS=10
MAX_AGE_DAYS=14

ARXIV_URL="https://export.arxiv.org/api/query?search_query=ti:LLM+OR+ti:%22Large+Language+Model%22+OR+ti:%22AI+Agent%22+OR+ti:RAG+OR+ti:RLHF+OR+ti:Multimodal+OR+ti:DeepSeek+OR+ti:Gemini+OR+ti:ChatGPT+OR+ti:GPT-4+OR+ti:GPT-5+OR+ti:Claude+OR+ti:Llama+OR+ti:Mistral+OR+ti:Qwen&sortBy=submittedDate&sortOrder=descending&max_results=50"

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] arxiv: $1"; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# ArXiv AI论文监控" > "$KB_SRC"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"

# ── 1. 抓取 ArXiv API XML ────────────────────────────────────────────────
FEED_FILE="$CACHE/arxiv_feed.xml"
# 去掉 -f：HTTP 错误不再触发 set -e 静默退出，改为手动检测
if ! curl -sSL --max-time 30 -H "User-Agent: openclaw-arxiv-monitor/1.0 (mailto:bisdom@example.com)" "$ARXIV_URL" -o "$FEED_FILE" 2>"$CACHE/curl_feed.err"; then
  log "ERROR: ArXiv API 抓取失败: $(head -1 "$CACHE/curl_feed.err" 2>/dev/null)"
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi
echo "[arxiv] XML抓取完成"

# ── 2. 解析XML → 结构化JSONL（标题/作者/日期/ID/摘要）─────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
if ! python3 - "$FEED_FILE" "$MAX_AGE_DAYS" "$MAX_PAPERS" "$SEEN_FILE" << 'PYEOF' > "$PAPERS_FILE"
import sys, json, re, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

feed_file = sys.argv[1]
max_age = int(sys.argv[2])
max_papers = int(sys.argv[3])
seen_file = sys.argv[4]
cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)

# Load previously sent paper IDs
with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

NS = {"a": "http://www.w3.org/2005/Atom"}
tree = ET.parse(feed_file)
root = tree.getroot()

count = 0
new_ids = []
for entry in root.findall("a:entry", NS):
    published = entry.findtext("a:published", "", NS)
    if not published:
        continue
    try:
        pub_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        continue
    if pub_date < cutoff:
        continue

    title = " ".join((entry.findtext("a:title", "", NS) or "").split())
    if not title:
        continue

    # Extract arxiv ID (e.g., http://arxiv.org/abs/2503.12345v1 → 2503.12345)
    entry_id = entry.findtext("a:id", "", NS)
    arxiv_id = entry_id.split("/abs/")[-1] if "/abs/" in entry_id else ""
    arxiv_id = re.sub(r'v\d+$', '', arxiv_id)

    # Skip already sent papers
    if arxiv_id in seen_ids:
        continue

    # First author
    authors = entry.findall("a:author", NS)
    first_author = authors[0].findtext("a:name", "", NS) if authors else "Unknown"

    # Abstract (truncate for LLM prompt)
    abstract = " ".join((entry.findtext("a:summary", "", NS) or "").split())[:300]

    date_str = published[:10]

    print(json.dumps({
        "title": title,
        "arxiv_id": arxiv_id,
        "first_author": first_author,
        "date": date_str,
        "abstract": abstract
    }, ensure_ascii=False))

    new_ids.append(arxiv_id)
    count += 1
    if count >= max_papers:
        break

# Append new IDs to seen file
if new_ids:
    with open(seen_file, 'a') as f:
        for aid in new_ids:
            f.write(aid + '\n')

print(f"[arxiv] 解析完成: {count} 篇新论文（跳过 {len(seen_ids)} 篇已发送）", file=sys.stderr)
PYEOF
then
  log "WARN: XML解析失败"
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

PAPER_COUNT="$(wc -l < "$PAPERS_FILE" | tr -d ' ')"
if [ "$PAPER_COUNT" -eq 0 ]; then
    log "无新论文（全部已发送或过去${MAX_AGE_DAYS}天无结果），跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[arxiv] 新论文: ${PAPER_COUNT} 篇"

# ── 3. 构建LLM prompt（只要求翻译+贡献+评级，结构化数据由脚本填充）───
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

# ── 4. 调用LLM（纯推理，直接curl proxy:5002，原则#27）─────────────────
PAYLOAD=$(python3 -c "
import json, sys
prompt = open('$CACHE/llm_prompt.txt').read()
print(json.dumps({
    'model': 'Qwen3-235B-A22B-Instruct-2507-W8A8',
    'messages': [{'role': 'user', 'content': prompt}],
    'max_tokens': 4096,
    'temperature': 0.3
}))
")

LLM_RESP=$(curl -s --max-time 120 \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
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

# L1检查：LLM输出为空
if [ -z "${LLM_CONTENT// }" ]; then
    ERR_MSG="⚠️ ArXiv监控 LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"
echo "[arxiv] LLM调用成功"

# ── 5. 组装消息（脚本控制格式，结构化数据从XML，翻译从LLM）──────────
MSG_FILE="$CACHE/arxiv_message.txt"
ASSEMBLE_ERR="$CACHE/assemble.stderr"
python3 - "$PAPERS_FILE" "$CACHE/llm_content.txt" "$DAY" "$MSG_FILE" 2>"$ASSEMBLE_ERR" << 'PYEOF'
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

# ── 按行模式匹配解析 LLM 输出（不依赖 --- 分隔符）──────────────────
# 策略：逐行扫描，用"贡献："和"价值：⭐"前缀识别行类型，
#   三行为一组（标题→贡献→价值），自动忽略分隔符/空行/序号行。
def clean_prefix(line, prefixes):
    """去除 LLM 可能添加的格式前缀"""
    for p in prefixes:
        if line.startswith(p):
            return line[len(p):].strip()
    return line

TITLE_PREFIXES = ['第一行：', '第1行：', '标题：', '中文标题：']
CONTRIB_PREFIXES = ['第二行：', '第2行：']
STARS_PREFIXES = ['第三行：', '第3行：']

# 分类所有非空行
parsed_blocks = []  # list of (cn_title, contrib, stars)
pending_title = None
pending_contrib = None

for raw_line in llm_content.split('\n'):
    line = raw_line.strip()
    if not line:
        continue
    # 跳过分隔符行（---、===、***等）
    if re.match(r'^[-=*]{3,}$', line):
        continue
    # 跳过纯序号行（如 "论文1："、"1."、"Paper 1:"）
    if re.match(r'^(论文\d+[：:]?\s*$|\d+[.、)]\s*$|Paper\s+\d+[：:]?\s*$)', line):
        continue

    # 检测"价值："行 → 结束一个 block
    if '价值' in line and '⭐' in line:
        stars_line = clean_prefix(line, STARS_PREFIXES)
        if not stars_line.startswith('价值：') and not stars_line.startswith('价值:'):
            stars_line = '价值：' + stars_line.lstrip('价值：').lstrip('价值:')
        # 确保格式统一
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

    # 检测"贡献："行
    if line.startswith('贡献：') or line.startswith('贡献:'):
        pending_contrib = clean_prefix(line, CONTRIB_PREFIXES)
        if not pending_contrib.startswith('贡献：'):
            pending_contrib = '贡献：' + pending_contrib
        continue
    # 带前缀的贡献行
    stripped = clean_prefix(line, CONTRIB_PREFIXES)
    if stripped != line and ('贡献' in stripped[:3]):
        pending_contrib = stripped if stripped.startswith('贡献：') else '贡献：' + stripped
        continue

    # 其余非空行视为标题（取每个 block 的第一个标题行）
    if pending_title is None:
        title = clean_prefix(line, TITLE_PREFIXES)
        # 去除序号前缀（如 "1. xxx"、"1、xxx"）
        title = re.sub(r'^\d+[.、)\]]\s*', '', title)
        title = title.strip('*').strip()
        pending_title = title

# 匹配 LLM 解析结果到论文
llm_ok = 0
msg_lines = [f"\U0001F4DA 今日arXiv精选 ({day})", ""]

for i, paper in enumerate(papers):
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

    # 脚本控制的严格 5 行格式
    msg_lines.append(f"*{cn_title}*")
    msg_lines.append(f"作者：{paper['first_author']} 等 | 日期：{paper['date']}")
    msg_lines.append(f"链接：https://arxiv.org/abs/{paper['arxiv_id']}")
    msg_lines.append(contrib)
    msg_lines.append(stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

# L2检查：解析成功率
total = len(papers)
rate = llm_ok / total if total else 0
if total > 0 and rate < 0.5:
    print(f"[arxiv] WARNING: LLM解析成功率过低 {llm_ok}/{total} ({rate:.0%})，可能格式异常", file=sys.stderr)
else:
    print(f"[arxiv] 消息组装完成: {total} 篇，LLM解析成功 {llm_ok}/{total}", file=sys.stderr)
PYEOF
ASSEMBLE_EXIT=$?

# L2检查：Python脚本检测到解析成功率过低时输出WARNING到stderr
if [ -f "$ASSEMBLE_ERR" ] && grep -q "WARNING.*解析成功率过低" "$ASSEMBLE_ERR" 2>/dev/null; then
    L2_MSG="⚠️ ArXiv监控 LLM解析异常（${DAY}）: $(grep 'WARNING' "$ASSEMBLE_ERR" | head -1)，请检查 $CACHE/llm_content.txt"
    log "$L2_MSG"
    "$OPENCLAW" message send --target "$TO" --message "$L2_MSG" --json >/dev/null 2>&1 || true
    printf '{"time":"%s","status":"parse_quality_low","new":%d}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
    exit 2
fi

# ── 6. 推送WhatsApp ─────────────────────────────────────────────────────
# 消息过长时截断（WhatsApp 单条上限约 65000 字符，留 buffer 取 4000）
MSG_CONTENT="$(head -c 4000 "$MSG_FILE")"
SEND_ERR=$(mktemp)
if "$OPENCLAW" message send --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${PAPER_COUNT} 篇论文"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
else
    log "ERROR: 推送失败（${PAPER_COUNT} 篇待发）: $(cat "$SEND_ERR" | head -3)"
    log "MSG_FILE size: $(wc -c < "$MSG_FILE") bytes"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$PAPER_COUNT" > "$STATUS_FILE"
fi

# ── 7. KB归档（合并原 kb-save-arxiv 功能）──────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# ArXiv AI论文监控 ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "arxiv-ai-models" "note" 2>/dev/null || true
    echo "[arxiv] KB写入完成"
fi

# ── 8. 永久归档到 sources ───────────────────────────────────────────────
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} >> "$KB_SRC"

# ── 9. 清理seen缓存（保留最近500条，防无限增长）────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
    echo "[arxiv] seen缓存已裁剪至300条"
fi

# ── 10. rsync备份 ───────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

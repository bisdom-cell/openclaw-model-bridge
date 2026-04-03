#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# OpenReview 顶会高分论文监控 v2
# 每周一 09:30 HKT 由系统 crontab 触发（顶会论文更新按 deadline 周期，非每日）
# 监控 ICLR/NeurIPS/ICML 等顶会的高分投稿
# 使用 openreview-py 客户端 guest 模式（REST API 需 Bearer token，但 Python 客户端支持 guest 访问）
# 依赖：pip3 install openreview-py
set -eo pipefail

# 防重叠执行
LOCK="/tmp/openreview.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[openreview] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/openreview"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/openreview_top.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"
YEAR="$(TZ=Asia/Hong_Kong date '+%Y')"

log() { echo "[$TS] openreview: $1"; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# OpenReview 顶会论文" > "$KB_SRC"

# ── 1. 使用 openreview-py 客户端抓取顶会论文 ────────────────────────
# OpenReview REST API 需要 Bearer token（403），但 Python 客户端支持 guest 访问
# 使用 V1 guest client 按 invitation 抓取各顶会最新 submission
# 注意：openreview-py 装在系统 Python 3.9 下，homebrew Python 3.14 无此包
PYTHON_OR="/usr/bin/python3"
command -v "$PYTHON_OR" >/dev/null || PYTHON_OR="python3"
RAW_FILE="$CACHE/raw_papers.json"

if ! "$PYTHON_OR" << 'PYEOF' > "$RAW_FILE" 2>"$CACHE/fetch.err"
import json, sys, time

try:
    import openreview
except ImportError:
    print("ERROR: openreview-py not installed. Run: pip3 install openreview-py", file=sys.stderr)
    sys.exit(1)

# Guest client — 无需认证，可访问公开数据
try:
    client_v1 = openreview.Client(baseurl='https://api.openreview.net')
except Exception as e:
    print(f"ERROR: V1 guest client init failed: {e}", file=sys.stderr)
    sys.exit(1)

try:
    client_v2 = openreview.api.OpenReviewClient(baseurl='https://api2.openreview.net')
except Exception:
    client_v2 = None

# 顶会 invitation 列表（优先用 V2，回退 V1）
# 格式：(invitation_v2, invitation_v1_fallback, venue_label)
from datetime import datetime
year = datetime.now().year

VENUES = [
    (f"ICLR.cc/{year}/Conference/-/Submission",
     f"ICLR.cc/{year}/Conference/-/Blind_Submission",
     f"ICLR {year}"),
    (f"NeurIPS.cc/{year}/Conference/-/Submission",
     f"NeurIPS.cc/{year}/Conference/-/Blind_Submission",
     f"NeurIPS {year}"),
    (f"ICML.cc/{year}/Conference/-/Submission",
     f"ICML.cc/{year}/Conference/-/Blind_Submission",
     f"ICML {year}"),
    # 上一年也查（论文公开有延迟）
    (f"ICLR.cc/{year-1}/Conference/-/Submission",
     f"ICLR.cc/{year-1}/Conference/-/Blind_Submission",
     f"ICLR {year-1}"),
    (f"NeurIPS.cc/{year-1}/Conference/-/Submission",
     f"NeurIPS.cc/{year-1}/Conference/-/Blind_Submission",
     f"NeurIPS {year-1}"),
]

all_papers = []
fetch_ok = 0

for inv_v2, inv_v1, label in VENUES:
    notes = []
    # 尝试 V2 API
    if client_v2:
        try:
            notes = client_v2.get_notes(invitation=inv_v2, limit=50, sort='cdate:desc')
            if notes:
                print(f"[openreview] {label}: {len(notes)} papers (V2)", file=sys.stderr)
                fetch_ok += 1
        except Exception as e:
            print(f"[openreview] V2 {label} failed: {e}", file=sys.stderr)

    # V2 失败则回退 V1
    if not notes:
        try:
            notes = client_v1.get_notes(invitation=inv_v1, limit=50, sort='cdate:desc')
            if notes:
                print(f"[openreview] {label}: {len(notes)} papers (V1)", file=sys.stderr)
                fetch_ok += 1
        except Exception as e:
            print(f"[openreview] V1 {label} also failed: {e}", file=sys.stderr)

    for note in notes:
        content = note.content or {}

        def get_val(field):
            v = content.get(field, "")
            if isinstance(v, dict):
                return v.get("value", "")
            return v or ""

        title = get_val("title").strip()
        if not title:
            continue

        abstract = get_val("abstract")[:300]
        authors = get_val("authors")
        if isinstance(authors, list):
            first_author = authors[0] if authors else "Unknown"
        else:
            first_author = str(authors).split(",")[0].strip() or "Unknown"

        venue = label
        pid = note.id or note.forum or ""

        all_papers.append({
            "paper_id": pid,
            "title": title,
            "first_author": first_author,
            "abstract": abstract,
            "venue": venue,
            "rating": 0
        })

    time.sleep(1)  # 礼貌间隔

print(f"[openreview] 总计: {len(all_papers)} papers from {fetch_ok}/{len(VENUES)} venues", file=sys.stderr)
print(json.dumps(all_papers, ensure_ascii=False))
PYEOF
then
  log "ERROR: Python 抓取失败"
  cat "$CACHE/fetch.err" 2>/dev/null || true
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# 检查是否有数据
TOTAL_FETCHED=$(python3 -c "import json; print(len(json.load(open('$RAW_FILE'))))" 2>/dev/null || echo "0")
if [ "$TOTAL_FETCHED" -eq 0 ]; then
  log "ERROR: 所有 venue 抓取均失败或无数据"
  cat "$CACHE/fetch.err" 2>/dev/null || true
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi
log "抓取完成: $TOTAL_FETCHED 篇论文"

# ── 2. 去重 + 选取 top N → JSONL ────────────────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! python3 - "$RAW_FILE" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import sys, json

raw_file = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

with open(raw_file) as f:
    all_raw = json.load(f)

# 去重（seen_ids + 同批 title 去重）
unique = {}
for p in all_raw:
    pid = p.get("paper_id", "")
    if not pid or pid in seen_ids or pid in unique:
        continue
    unique[pid] = p

# 按 venue 分散选取（每个 venue 至少2篇），其余按时间（列表顺序=cdate desc）
sorted_papers = list(unique.values())[:max_papers]

new_ids = []
for p in sorted_papers:
    print(json.dumps(p, ensure_ascii=False))
    new_ids.append(p["paper_id"])

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

print(f"[openreview] 去重完成: {len(sorted_papers)} 篇（总 {len(all_raw)}，跳过 {len(seen_ids)} 已发送）", file=sys.stderr)
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
echo "[openreview] 新论文: ${PAPER_COUNT} 篇"

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
    rating = p.get('rating', 0)
    venue = p.get('venue', '')
    rating_str = f"评分{rating}" if rating else "未评分"
    prompt += f"论文{i}（{venue}，{rating_str}）：{p['title']}\n摘要：{p['abstract']}\n\n"

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
    ERR_MSG="⚠️ OpenReview LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"
echo "[openreview] LLM调用成功"

# ── 5. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/openreview_message.txt"
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
msg_lines = [f"\U0001F3C6 顶会论文精选 ({day})", ""]

for i, paper in enumerate(papers):
    venue = paper.get('venue', '')
    rating = paper.get('rating', 0)
    rating_str = f"评分{rating:.1f}" if rating else ""

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

    meta_parts = [paper['first_author'] + " 等"]
    if venue:
        meta_parts.append(venue)
    if rating_str:
        meta_parts.append(rating_str)

    msg_lines.append(f"*{cn_title}*")
    msg_lines.append(f"{'  | '.join(meta_parts)}")
    msg_lines.append(f"链接：https://openreview.net/forum?id={paper.get('paper_id', '')}")
    msg_lines.append(contrib)
    msg_lines.append(stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

total = len(papers)
print(f"[openreview] 消息组装完成: {total} 篇，LLM解析成功 {llm_ok}/{total}", file=sys.stderr)
PYEOF

# ── 6. 推送WhatsApp ──────────────────────────────────────────────────
MSG_CONTENT="$(head -c 4000 "$MSG_FILE")"
SEND_ERR=$(mktemp)
if "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$MSG_CONTENT" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${PAPER_COUNT} 篇论文"
    if [ -f "$NEW_IDS_FILE" ]; then
        cat "$NEW_IDS_FILE" >> "$SEEN_FILE"
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
    bash "$KB_WRITE_SCRIPT" "# OpenReview 顶会论文 ${DATE_KB}

${SUMMARY}" "openreview-top" "note" 2>/dev/null || true
fi

# ── 8. 永久归档 + 清理 + rsync ──────────────────────────────────────
{ echo ""; echo "## ${DAY}"; cat "$MSG_FILE"; } >> "$KB_SRC"

if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

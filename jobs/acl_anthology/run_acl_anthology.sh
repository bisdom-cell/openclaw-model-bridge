#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# ACL Anthology NLP 顶会论文监控 v1
# 每周三 09:30 HKT 由系统 crontab 触发（顶会论文按会议周期更新）
# 监控 ACL/EMNLP/NAACL/EACL/COLING 等 NLP 顶会
# 使用 ACL Anthology API (https://aclanthology.org)
set -eo pipefail

# 防重叠执行
LOCK="/tmp/acl_anthology.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[acl] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/acl_anthology"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/acl_anthology.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
MAX_PAPERS=10

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"
YEAR="$(TZ=Asia/Hong_Kong date '+%Y')"
PREV_YEAR="$((YEAR - 1))"

log() { echo "[$TS] acl: $1"; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# ACL Anthology NLP论文" > "$KB_SRC"

# ── 1. 抓取多个 NLP 顶会的最近 volume ────────────────────────────────
# ACL Anthology volume ID 格式：{year}.{venue}-{type}
# 主要会议 XML 文件名（GitHub acl-org/acl-anthology 仓库）
# 每个文件包含该会议所有论文的结构化 XML
XML_FILES=(
  "${YEAR}.acl"
  "${YEAR}.emnlp"
  "${YEAR}.naacl"
  "${YEAR}.eacl"
  "${PREV_YEAR}.acl"
  "${PREV_YEAR}.emnlp"
  "${PREV_YEAR}.naacl"
)

RAW_DIR="$CACHE/raw"
mkdir -p "$RAW_DIR"

FETCH_OK=0
for i in "${!XML_FILES[@]}"; do
  XMLF="${XML_FILES[$i]}"
  OUTFILE="$RAW_DIR/vol_${i}.xml"

  sleep 1  # GitHub 限速友好

  # 从 GitHub 获取结构化 XML（比 HTML 抓取更可靠）
  HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
    -H "User-Agent: openclaw-acl-monitor/1.0" \
    "https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/${XMLF}.xml" \
    -o "$OUTFILE" 2>"$CACHE/curl_acl.err") || HTTP_CODE="000"

  if [ "$HTTP_CODE" = "200" ]; then
    # 验证是否是有效 XML
    if head -5 "$OUTFILE" | grep -q '<collection\|<volume\|<?xml'; then
      echo "[acl] XML '$XMLF' 获取成功"
      FETCH_OK=$((FETCH_OK + 1))
    fi
  else
    # 很多 volume 可能还不存在（会议尚未举行），静默跳过
    :
  fi
done

if [ "$FETCH_OK" -eq 0 ]; then
  log "无可用的 ACL volume（可能会议尚未举行）"
  printf '{"time":"%s","status":"no_volumes","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 0
fi

# ── 2. 从 HTML 提取论文信息 → JSONL ─────────────────────────────────
PAPERS_FILE="$CACHE/papers.jsonl"
SEEN_FILE="$CACHE/seen_ids.txt"
touch "$SEEN_FILE"
NEW_IDS_FILE="$CACHE/new_ids.txt"

if ! python3 - "$RAW_DIR" "$MAX_PAPERS" "$SEEN_FILE" "$NEW_IDS_FILE" << 'PYEOF' > "$PAPERS_FILE"
import sys, os, glob, json
import xml.etree.ElementTree as ET

raw_dir = sys.argv[1]
max_papers = int(sys.argv[2])
seen_file = sys.argv[3]
new_ids_file = sys.argv[4]

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

all_papers = {}

for fpath in sorted(glob.glob(os.path.join(raw_dir, "vol_*.xml"))):
    try:
        tree = ET.parse(fpath)
        root = tree.getroot()

        # ACL Anthology XML: <collection> → <volume id="2024.acl-long"> → <paper id="1">
        for volume in root.iter("volume"):
            vol_id = volume.get("id", "")
            # 只取 long/main/findings，跳过 short/tutorial/demo
            if vol_id and not any(t in vol_id for t in ["long", "main", "findings"]):
                continue

            for paper in volume.iter("paper"):
                paper_num = paper.get("id", "")
                paper_id = f"{vol_id}.{paper_num}" if vol_id and paper_num else ""
                if not paper_id or paper_id in seen_ids or paper_id in all_papers:
                    continue

                title_el = paper.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""
                # title 可能包含子元素（如 <fixed-case>）
                if not title and title_el is not None:
                    title = ET.tostring(title_el, encoding='unicode', method='text').strip()
                if not title:
                    continue

                # 提取第一作者
                authors = paper.findall("author")
                first_author = "Unknown"
                if authors:
                    first = authors[0].find("first")
                    last = authors[0].find("last")
                    first_name = (first.text or "") if first is not None else ""
                    last_name = (last.text or "") if last is not None else ""
                    first_author = f"{first_name} {last_name}".strip() or "Unknown"

                abstract_el = paper.find("abstract")
                abstract = ""
                if abstract_el is not None:
                    abstract = ET.tostring(abstract_el, encoding='unicode', method='text').strip()[:300]

                all_papers[paper_id] = {
                    "paper_id": paper_id,
                    "title": title,
                    "first_author": first_author,
                    "abstract": abstract,
                    "venue": vol_id
                }
    except Exception:
        continue

# 取最新的 N 篇（按 ID 倒序 = 最新的编号最大）
sorted_papers = sorted(all_papers.values(),
                       key=lambda x: x.get("paper_id", ""),
                       reverse=True)[:max_papers]

new_ids = []
for p in sorted_papers:
    print(json.dumps(p, ensure_ascii=False))
    new_ids.append(p["paper_id"])

with open(new_ids_file, 'w') as f:
    for pid in new_ids:
        f.write(pid + '\n')

print(f"[acl] 提取完成: {len(sorted_papers)} 篇（总 {len(all_papers)}，跳过 {len(seen_ids)} 已发送）", file=sys.stderr)
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
echo "[acl] 新论文: ${PAPER_COUNT} 篇"

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

prompt = """你是NLP论文编辑。对以下每篇论文严格输出三行（不要输出任何其他内容）：
第一行：中文标题（≤25字，翻译或意译，不加任何前缀标签）
第二行：贡献：[1句话≤50字，说明核心贡献]
第三行：价值：⭐（1到5个星，评估对NLP/AI从业者的参考价值）
每篇之间用一行 --- 分隔。不要输出序号。

"""
for i, p in enumerate(papers, 1):
    venue = p.get('venue', '')
    abstract = p.get('abstract', '')
    if abstract:
        prompt += f"论文{i}（{venue}）：{p['title']}\n摘要：{abstract}\n\n"
    else:
        prompt += f"论文{i}（{venue}）：{p['title']}\n\n"

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
    ERR_MSG="⚠️ ACL Anthology LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

echo "$LLM_CONTENT" > "$CACHE/llm_content.txt"
echo "[acl] LLM调用成功"

# ── 5. 组装消息 ──────────────────────────────────────────────────────
MSG_FILE="$CACHE/acl_message.txt"
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
            pending_contrib or '贡献：NLP领域相关研究',
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
msg_lines = [f"\U0001F4DD ACL顶会NLP论文 ({day})", ""]

for i, paper in enumerate(papers):
    venue = paper.get('venue', '')

    if i < len(parsed_blocks):
        cn_title, contrib, stars = parsed_blocks[i]
        if cn_title:
            llm_ok += 1
        else:
            cn_title = paper['title']
    else:
        cn_title = paper['title']
        contrib = "贡献：NLP领域相关研究"
        stars = "价值：⭐⭐⭐"

    msg_lines.append(f"*{cn_title}*")
    first_author = paper.get('first_author', '')
    msg_lines.append(f"作者：{first_author} 等 | 会议：{venue}")
    msg_lines.append(f"链接：https://aclanthology.org/{paper.get('paper_id', '')}/")
    msg_lines.append(contrib)
    msg_lines.append(stars)
    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\n'.join(msg_lines))

total = len(papers)
print(f"[acl] 消息组装完成: {total} 篇，LLM解析成功 {llm_ok}/{total}", file=sys.stderr)
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
    bash "$KB_WRITE_SCRIPT" "# ACL Anthology ${DATE_KB}

${SUMMARY}" "acl-anthology-nlp" "note" 2>/dev/null || true
fi

# ── 8. 永久归档 + 清理 + rsync ──────────────────────────────────────
{ echo ""; echo "## ${DAY}"; cat "$MSG_FILE"; } >> "$KB_SRC"

if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
log "完成"

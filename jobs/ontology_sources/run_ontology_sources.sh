#!/usr/bin/env bash
# run_ontology_sources.sh — Ontology 专属信息源监控
# 监控本体论/语义网/知识表示领域的权威 RSS 源
# 推送到 Discord #ontology 频道 + KB 归档
#
# V37.9.62 升级 (V37.9.51 rss_blogs / V37.9.50 semantic_scholar / V37.9.45 hf_papers
# 同款 Opportunity Radar #2 模板, Sub-Stage 4b 6/6 ontology_sources 迁移):
#   - 3 字段 (中文标题/要点/价值⭐) → 6 字段 (📌/🔑/💡/🎯/⭐/🎚️ 项目对齐度)
#   - 单次调用全部 N 篇 → per-article 独立 LLM 调用 + 5/10/20s retry
#   - 加入 rule_check (LLM ⭐ 评分 vs project_alignment_scorer keyword 一致性)
#   - 高对齐 ⭐≥4 统计 + ontology-specific reminder (ontology engine 强相关 ≥⭐⭐⭐⭐)
#   - V37.8.7 ontology_parser separator 切块设计**完整保留**, 仅扩展支持 6 字段
#   - V37.1 设计完整保留: 4 RSS source / 两层关键词过滤 / Discord #ontology / KB 归档
#
# crontab: 0 10,20 * * * bash -lc 'bash ~/.openclaw/jobs/ontology_sources/run_ontology_sources.sh >> ~/.openclaw/logs/jobs/ontology_sources.log 2>&1'
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -eo pipefail

# 防重叠执行
LOCK="/tmp/ontology_sources.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[onto-src] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/ontology_sources"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/ontology_sources.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
PYTHON3=/usr/bin/python3

# V37.8.7: 让 heredoc Python 能 import 同目录的 ontology_parser 模块
# (heredoc 通过 `python3 -` 读 stdin，sys.path 不含脚本目录，需主动注入)
export ONTOLOGY_JOBS_DIR="$JOB_DIR"

TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] onto-src: $1" >&2; }

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# Ontology Sources Watcher" > "$KB_SRC"

# ── 加载 notify.sh ────────────────────────────────────────────────────
NOTIFY_LOADED=false

# V37.9.62: 公共反幻觉守卫升级到 LEVEL_4_PROJECT_AWARE
# (V37.9.57 起 LEVEL_2 是单 source 新闻类, ontology_sources 是 ALIGNED job 含 per-article
# 项目对齐评分, 升级到 LEVEL_4 含 V37.9.56-hotfix3 血案字眼防"OpenClaw 社区发布"等编造)
# FAIL-OPEN: hallucination_guards 模块缺失 → 空字符串, 不阻塞 prompt 主流程
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

# 兼容老 V37.9.57 HG_GUARD_TEXT (LEVEL_2, 留空让 prompt 不强行注入)
HG_GUARD_TEXT=""
export HG_GUARD_TEXT
for _np in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$_np" ]; then
        source "$_np"
        NOTIFY_LOADED=true
        break
    fi
done

# V37.9.62: fail-fast alert helper (V37.9.51 rss_blogs / V37.9.36 同款模式)
# — LLM 全部失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] ontology_sources LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送 ontology 学术动态 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

# ── Ontology RSS 源配置 ───────────────────────────────────────────────
# 格式：name|feed_url|label
# 选择标准：有可用RSS、无Cloudflare反爬、ontology/语义网专属
RSS_FEEDS=(
    "W3C Semantic Web|https://www.w3.org/blog/feed/|W3C(OWL/RDF/SPARQL/SHACL标准动态)"
    "Journal of Web Semantics|https://rss.sciencedirect.com/publication/science/15708268|JWS(语义网研究，Elsevier)"
    "Data and Knowledge Engineering|https://rss.sciencedirect.com/publication/science/0169023X|DKE(Elsevier，数据与知识工程，本体建模/概念建模)"
    "Knowledge-Based Systems|https://rss.sciencedirect.com/publication/science/09507051|KBS(Elsevier，知识系统/知识图谱/推理)"
)

SEEN_FILE="$CACHE/seen_urls.txt"
touch "$SEEN_FILE"
ALL_NEW_FILE="$CACHE/all_new.jsonl"
> "$ALL_NEW_FILE"

TOTAL_NEW=0
FETCH_ERRORS=0

for feed_entry in "${RSS_FEEDS[@]}"; do
    IFS='|' read -r FEED_NAME FEED_URL FEED_LABEL <<< "$feed_entry"
    FEED_FILE="$CACHE/feed_$(echo "$FEED_NAME" | tr ' ' '_').xml"

    # 抓取 RSS
    FETCH_OK=false
    for attempt in 1 2 3; do
        HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
            -H "User-Agent: openclaw-ontology-monitor/1.0" \
            -o "$FEED_FILE" \
            "$FEED_URL" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "200" ] && [ -s "$FEED_FILE" ]; then
            FETCH_OK=true
            break
        else
            log "WARN: ${FEED_NAME} RSS HTTP ${HTTP_CODE} (attempt ${attempt})"
        fi
        sleep "$((attempt * 3))"
    done

    if [ "$FETCH_OK" != "true" ]; then
        log "WARN: ${FEED_NAME} RSS 抓取失败，跳过"
        FETCH_ERRORS=$((FETCH_ERRORS + 1))
        continue
    fi

    # 解析 RSS XML → 提取新文章（带 ontology 关键词过滤）
    $PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$FEED_NAME" "$FEED_LABEL" << 'PYEOF' >> "$ALL_NEW_FILE"
import sys, json, re
import xml.etree.ElementTree as ET

feed_file = sys.argv[1]
seen_file = sys.argv[2]
feed_name = sys.argv[3]
feed_label = sys.argv[4]

# Ontology 核心关键词（强信号，命中一个即通过）
STRONG_KEYWORDS = [
    "ontology", "ontologies", "ontological",
    "semantic web", "linked data",
    "OWL", "RDF", "SPARQL", "SHACL", "SKOS",
    "description logic", "formal ontology",
    "knowledge representation", "knowledge engineering",
    "upper ontology", "BFO", "UFO", "DOLCE",
    "neuro-symbolic", "neurosymbolic",
    "conceptual modeling", "conceptual model",
]
# 弱关键词（需要标题中出现才算，避免摘要中的泛匹配）
TITLE_KEYWORDS = [
    "knowledge graph", "knowledge base",
    "schema.org", "structured data",
    "reasoning", "taxonomy",
]

# KBS 范围极广，只接受强关键词（弱关键词如 reasoning 会命中航空/医学等无关论文）
STRICT_SOURCES = ["Knowledge-Based Systems"]

with open(seen_file) as f:
    seen_urls = set(line.strip() for line in f if line.strip())

try:
    tree = ET.parse(feed_file)
    root = tree.getroot()
except ET.ParseError:
    with open(feed_file, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        print(f"[onto-src] ERROR: {feed_name} XML解析失败", file=sys.stderr)
        sys.exit(0)

# 支持 RSS 2.0 和 Atom 格式
ns = {'atom': 'http://www.w3.org/2005/Atom',
      'content': 'http://purl.org/rss/1.0/modules/content/',
      'dc': 'http://purl.org/dc/elements/1.1/',
      'prism': 'http://prismstandard.org/namespaces/basic/2.0/'}


# V37.9.183: 提取 DOI（Elsevier/ScienceDirect 用 prism:doi 标准字段）供 deep_dive
# DOI→S2 OA 全文解析（镜像 V37.9.132 dblp/hf/s2 link 改进，根治 ontology_sources
# 付费墙期刊 deep_dive 摘要级 gap）。仅取结构化字段（prism:doi/dc:identifier/guid），
# 不碰 description 防把摘要里引用的别人 DOI 误当本文（fail-plausible 防线）。
# FAIL-OPEN：无 DOI → 返回 ''，调用方保持原 PII link 不变。
def _extract_item_doi(item, ns):
    for path in ('prism:doi', 'dc:identifier', 'guid'):
        el = item.find(path, ns) if ':' in path else item.find(path)
        if el is not None and (el.text or '').strip():
            m = re.search(r'10\.\d{4,9}/[^\s<>"\']+', el.text)
            if m:
                return m.group(0).rstrip('.')
    return ''

items = root.findall('.//item')  # RSS 2.0
if not items:
    items = root.findall('.//atom:entry', ns)  # Atom

new_count = 0
for item in items[:30]:  # 检查前30篇
    title_el = item.find('title')
    link_el = item.find('link')
    desc_el = item.find('description')
    date_el = item.find('pubDate')
    author_el = item.find('dc:creator', ns)
    content_el = item.find('content:encoded', ns)

    # Atom fallback
    if link_el is None:
        link_el = item.find('atom:link', ns)
        if link_el is not None:
            link_el = type('obj', (object,), {'text': link_el.get('href', '')})()
    if title_el is None:
        title_el = item.find('atom:title', ns)
    if date_el is None:
        date_el = item.find('atom:published', ns) or item.find('atom:updated', ns)

    title = (title_el.text or '').strip() if title_el is not None else ''
    link = (link_el.text or '').strip() if link_el is not None else ''
    # V37.9.183: 有 DOI 且 link 非 arxiv/doi.org（付费墙 PII URL）→ 改写为 doi.org link，
    # 让 deep_dive 的 DOI→S2 OA 全文解析可用（FAIL-OPEN：无 DOI 不改）。
    doi = _extract_item_doi(item, ns)
    if doi and 'arxiv.org' not in link and 'doi.org' not in link:
        link = 'https://doi.org/' + doi
    description = ''
    if content_el is not None and content_el.text:
        description = re.sub(r'<[^>]+>', '', content_el.text)[:500]
    elif desc_el is not None and desc_el.text:
        description = re.sub(r'<[^>]+>', '', desc_el.text)[:500]
    pub_date = (date_el.text or '').strip()[:25] if date_el is not None else ''
    author = (author_el.text or '').strip() if author_el is not None else feed_name

    if not title or not link:
        continue
    if link in seen_urls:
        continue

    # 关键词过滤（三层严格度）
    # KBS 等泛源：强关键词必须出现在标题中（摘要中偶然出现不算）
    # JWS/DKE 等领域期刊：强关键词查全文 OR 弱关键词查标题
    full_text = (title + " " + description).lower()
    title_lower = title.lower()
    has_strong_title = any(kw.lower() in title_lower for kw in STRONG_KEYWORDS)
    if feed_name in STRICT_SOURCES:
        if not has_strong_title:
            continue
    else:
        has_strong_full = any(kw.lower() in full_text for kw in STRONG_KEYWORDS)
        has_title_kw = any(kw.lower() in title_lower for kw in TITLE_KEYWORDS)
        if not (has_strong_full or has_title_kw):
            continue

    print(json.dumps({
        "title": title,
        "link": link,
        "description": description,
        "pub_date": pub_date,
        "author": author,
        "feed_name": feed_name,
        "feed_label": feed_label,
    }, ensure_ascii=False))
    new_count += 1

    if new_count >= 3:  # 每个源每次最多3篇（控制总量≤12，避免截断）
        break

print(f"[onto-src] {feed_name}: {new_count} 篇新文章", file=sys.stderr)
PYEOF
done

TOTAL_NEW="$(wc -l < "$ALL_NEW_FILE" | tr -d ' ')"
if [ "$TOTAL_NEW" -eq 0 ]; then
    # V37.9.227 (audit F): 全源抓取失败 ≠ 平静无新文章。原写 status:ok → watchdog 静默
    # (dead FETCH_ERRORS 计数器 incr 后从不 consult)。全部 RSS 源失败 → fetch_failed 告警。
    if [ "$FETCH_ERRORS" -ge "${#RSS_FEEDS[@]}" ]; then
        log "ERROR: 全部 ${#RSS_FEEDS[@]} 源抓取失败 (fetch_failed)，无内容"
        printf '{"time":"%s","status":"fetch_failed","new":0,"errors":%d}\n' "$TS" "$FETCH_ERRORS" > "$STATUS_FILE"
        exit 1
    fi
    log "无新文章，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0,"errors":%d}\n' "$TS" "$FETCH_ERRORS" > "$STATUS_FILE"
    exit 0
fi
log "共 ${TOTAL_NEW} 篇新文章"

# ── V37.9.62: 每篇独立调 LLM (6 字段深度分析 + retry 3 次 + rule_check) ─
# 老 V37.8.7: 单次调用全部 N 篇, LLM 漏一行级联错位 (V37.8.7 separator 切块已修, 但仍单 LLM 调用)
# 新 V37.9.62: 每篇独立调用 + 独立 retry (5s/10s/20s), 部分失败走 partial_degraded
# 全部失败 → fail-fast (V37.9.51 rss_blogs 同款模式)

LLM_RAW="$CACHE/llm_raw_last.txt"   # 兼容: 保留上一次失败响应做 forensic
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单篇 LLM 调用 + retry (V37.9.51 rss_blogs 同款) ─────────
# 输入: $1 = single_article_prompt 文件路径, $2 = article_idx
# 输出: stdout = 成功时 LLM content; 失败 → exit 1, 全局 LAST_LLM_FAIL_REASON 含原因
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

        # 保存最后一次响应做 forensic (覆盖式, 仅最后一次)
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
            LAST_LLM_FAIL_REASON=$(echo "$parse_err" | head -c 200 | LC_ALL=C tr '\n' ' ')
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

        # 成功 → 返回 content
        echo "$parse_out"
        return 0
    done

    # 所有 retry 都失败
    return 1
}

# ── 主循环: 每篇文章独立调 LLM (V37.9.51 rss_blogs 同款) ───────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$ALL_NEW_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, re, os  # V37.9.62: os 用于 V37.9.57 HG_LEVEL_4_TEXT 注入

articles_file, idx = sys.argv[1], int(sys.argv[2])
with open(articles_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
a = articles[idx]
title = a['title']
desc_raw = a.get('description', '')

def clean_desc(desc):
    if not desc:
        return ""
    desc = re.sub(r'<[^>]+>', ' ', desc)
    # Strip ScienceDirect metadata
    desc = re.sub(r'^.*?Abstract\s*:\s*', '', desc, flags=re.DOTALL | re.IGNORECASE)
    desc = re.sub(r'Publication date:[^\n]*', '', desc)
    desc = re.sub(r'Author\(s\):[^\n]*', '', desc)
    return re.sub(r'\s+', ' ', desc).strip()

desc = clean_desc(desc_raw)[:600]
feed_label = a.get('feed_label', '')

prompt = """你是本体论(Ontology)和语义网(Semantic Web)领域的学术编辑 (兼 OpenClaw 项目对齐评估师)。对以下文章输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (如原文已是中文则简化精炼)
🔑 核心要点: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出核心贡献/方法/价值
💡 关键洞察: 揭示作者立场 / 方法论 / 与本体论领域趋势的关联 / 与已有工作的对比 / 局限性
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字 (旗舰级文章充分展开)
🎯 实践启发: 1-3 条对本体论/知识工程从业者的具体行动建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 应用领域)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.51 新增 (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2 模板) ━

OpenClaw 项目方向 (参考评分, ontology-specific):
   ⭐⭐⭐⭐⭐ = 直接相关 (ontology engine / 本体论 / Semantic Web / Knowledge Graph / RDF / OWL / SHACL /
              control plane / agent runtime / governance / convergence framework / fail-fast / memory plane)
   ⭐⭐⭐⭐  = 间接相关 (knowledge representation / reasoning / taxonomy / conceptual modeling /
              tool plugin / KB RAG / semantic search / drift detection / declarative policy)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 任务)
   ⭐      = 完全无关 (噪声, 比如硬件细节 / GPU kernel / 不涉及知识表示的工程论文)

   特别提醒: 本体论 / Semantic Web / Knowledge Graph / RDF / OWL / SHACL 等议题对 OpenClaw ontology engine 直接相关,
            评分应高一档. 但严禁仅因来源是 W3C/JWS/DKE/KBS 就自动给高分, 必须看具体内容是否真涉及上述方向.

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的标题和摘要中的信息, 严禁虚构作者未提及的事实/数据/链接
- 如摘要不足以判断深度, 标⭐较低 + 写"基于摘要的初步判断"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态除非原文提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔, 末尾用 --- 分隔多篇):

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
prompt += f"文章标题: {title}\n"
prompt += f"来源: {feed_label}\n"
if desc:
    prompt += f"原文摘要:\n{desc}\n"
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "调用 LLM 分析篇 $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        $PYTHON3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: 篇 $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        $PYTHON3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定整体 status (V37.9.51 rss_blogs / V37.9.36 fail-fast 契约保留) ──
if [ "$TOTAL_FAILED" -eq "$TOTAL_NEW" ]; then
    log "ERROR: 全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 篇失败 — 走 partial_degraded (失败篇标 [LLM_DEGRADED] + RSS 摘要 fallback)"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 篇 LLM 部分失败 (其余正常推送, 失败篇标 [LLM_DEGRADED])"
fi

# ── V37.9.62: 组装消息 (6 字段解析 + LLM_DEGRADED fallback + rule_check + 高对齐统计) ──
MSG_FILE="$CACHE/onto_message.txt"
$PYTHON3 - "$ALL_NEW_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.62: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款)

articles_file, results_file, day, msg_file = sys.argv[1:5]

with open(articles_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.62: lazy import project_alignment_scorer + load concepts (V37.9.51 rss_blogs / V37.9.50 同款 rule_check)
# FAIL-OPEN: 模块缺失 / yaml 缺失 → 跳过 rule_check 不阻塞 cron
_concepts = None
_validate_alignment_score = None
_extract_star_count = None
_format_validation_marker = None
try:
    sys.path.insert(0, os.environ.get('HOME', os.path.expanduser('~')))
    # ontology_sources 在 jobs/ontology_sources/, project_alignment_scorer 在 repo 根
    # 沿 __file__ 向上 3 级 (jobs/ontology_sources/run_ontology_sources.sh → ../../..) 找根
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
    print("[onto-src] V37.9.62 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[onto-src] V37.9.62 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

# V37.9.62: 使用 ontology_parser.parse_6field_output (V37.8.7 separator 切块设计保留, 扩展支持 6 字段)
# 关键: ontology_parser 保持 separator-based + key-based, 防 ontology 血案 cn_title 错位
_jobs_dir = os.environ.get("ONTOLOGY_JOBS_DIR", "")
if _jobs_dir:
    sys.path.insert(0, _jobs_dir)
from ontology_parser import parse_6field_output

msg_lines = [f"🔬 Ontology 学术动态 ({day})", ""]

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.62: ⭐≥4 alignment 计数 (Opportunity Radar #2)

for i, article in enumerate(articles):
    msg_lines.append(f"*{article['title']}*")
    msg_lines.append(f"来源: {article['feed_label']} | {article.get('pub_date', '')[:16]}")
    msg_lines.append(f"链接: {article['link']}")
    msg_lines.append("")

    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        # LLM_DEGRADED: 用 RSS description 给用户最低保障
        degraded_count += 1
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 原文摘要供参考:")
        desc = (article.get('description') or '')[:300]
        if desc:
            msg_lines.append(desc)
        else:
            msg_lines.append("(原文无摘要数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        # V37.9.62: parse_6field_output 返回 list (每个 result.content 应只含 1 篇), 取第一块
        parsed = parse_6field_output(result.get('content', ''))
        fields = parsed[0] if parsed else {
            'cn_title': '', 'highlights': '', 'insight': '',
            'practice': '', 'rating': '', 'alignment': '',
        }
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
        # V37.9.62: 🎚️ 项目对齐度展示 + rule_check 验证 (V37.9.51 rss_blogs / V37.9.50 同款)
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = title + description (V37.9.51 rss_blogs 同款 blog/paper 通用模式)
                        rule_content = article.get('title', '') + ' ' + (article.get('description') or '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[onto-src] V37.9.62 rule_check 失败 article={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        # 至少保证有 cn_title 才算 LLM 解析成功
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.62: 末尾追加高对齐统计 (Opportunity Radar #2)
total_articles = len(articles)
if total_articles > 0:
    msg_lines.append(f"━━━ 本轮高对齐 ontology 论文 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_articles} 篇 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[onto-src] 消息组装完成: {len(articles)} 篇 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 推送到 Discord #ontology（主推通道）+ WhatsApp ───────────────────
# V37.9.62: 复用 V37.1 设计 — 通过 notify.sh --topic ontology 路由到 #ontology 频道
# 按文章分段推送，避免单条消息超长截断 (>3500 字符自动切分)
SEND_ERR=$(mktemp)
WA_SENT=false

if $NOTIFY_LOADED; then
    PART_FILES=$($PYTHON3 - "$MSG_FILE" << 'SPLIT_EOF'
import sys

msg_file = sys.argv[1]
with open(msg_file) as f:
    content = f.read()

# 按空行分割为文章块，每段≤3500字符 (V37.1 同款切片)
blocks = content.split('\n\n')
chunks = []
current = ""
for block in blocks:
    candidate = (current + "\n\n" + block).strip() if current else block.strip()
    if len(candidate) > 3500 and current:
        chunks.append(current.strip())
        current = block.strip()
    else:
        current = candidate
if current.strip():
    chunks.append(current.strip())

for i, chunk in enumerate(chunks):
    path = f"/tmp/onto_msg_part_{i}.txt"
    with open(path, 'w') as f:
        f.write(chunk)
    print(path)
SPLIT_EOF
    )

    PART_COUNT=0
    while IFS= read -r part_file; do
        [ -f "$part_file" ] || continue
        PART_CONTENT="$(cat "$part_file")"
        if notify "$PART_CONTENT" --topic ontology 2>"$SEND_ERR"; then
            WA_SENT=true
        fi
        PART_COUNT=$((PART_COUNT + 1))
        rm -f "$part_file"
        sleep 1  # 防 WhatsApp/Discord 消息乱序
    done <<< "$PART_FILES"

    log "已推送 ${TOTAL_NEW} 篇到 #ontology（${PART_COUNT} 段）"
else
    log "WARN: notify.sh not loaded, skipping push"
fi

if [ "$WA_SENT" = "true" ]; then
    # 标记为已发送
    $PYTHON3 -c "
import json, sys
with open('$ALL_NEW_FILE') as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            print(d.get('link', ''))
" >> "$SEEN_FILE"
    # V37.9.62: status 区分 ok / partial_degraded (V37.9.51 rss_blogs 同款)
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi

# ── KB 归档 ──────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ] && [ -f "$KB_WRITE_SCRIPT" ]; then
    DATE_KB=$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M')
    CONTENT="# Ontology Sources ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "ontology" "note" 2>/dev/null || true
    log "KB写入完成"
fi

# ── 永久归档 ──────────────────────────────────────────────────────────
# V37.6: idempotent H2-dedup append。cron 10:00/20:00 2x/day，用 HH:MM 区分
# slot，避免第二次运行被当作"同一天重复"静默丢弃；同一 slot 内同一天再次
# 触发（watchdog/手动）才会触发幂等跳过。
SLOT_TAG="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%H:%M')"
SECTION_MARKER="## ${DAY} ${SLOT_TAG}"
{
    echo ""
    echo "${SECTION_MARKER}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "${SECTION_MARKER}"

# ── 清理 seen 缓存 ──────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── rsync 备份 ──────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

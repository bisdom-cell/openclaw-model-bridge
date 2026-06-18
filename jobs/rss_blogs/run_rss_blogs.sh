#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# RSS 博客订阅监控 v2 (V37.9.51 — 6 字段 + rule_check 升级,
# V37.9.37 深度 5 字段基础 + V37.9.45 hf_papers / V37.9.50 semantic_scholar
# 同款 Opportunity Radar #2 模板横向迁移, Sub-Stage 4b 1/6)
# 每天 2 次（08:00, 18:00 HKT）由系统 crontab 触发
# 支持多个 RSS 源，按需扩展
set -eo pipefail

# V37.9.57: 公共反幻觉守卫 LEVEL_4_PROJECT_AWARE (MR-8 single-source-of-truth)
# 8 ALIGNED jobs 的 per-paper LLM prompt 已有 inline 反幻觉守卫, V37.9.57 追加
# LEVEL_4 含 V37.9.56-hotfix3 具体血案字眼 (禁"OpenClaw 社区发布"/"v26"/"[openclaw]")
# 防 alignment 评分输出"一句话原因"段编造项目动态. FAIL-OPEN: 模块缺失 → 空字符串
HG_LEVEL_4_TEXT=$(python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~'))
sys.path.insert(0, '$(cd "$(dirname "$0")" && pwd)')
try:
    import hallucination_guards as hg
    print(hg.get_guard('LEVEL_4_PROJECT_AWARE'))
except Exception:
    print('')
" 2>/dev/null)
export HG_LEVEL_4_TEXT

# 防重叠执行
LOCK="/tmp/rss_blogs.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[rss] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
JOB_DIR="${HOME}/.openclaw/jobs/rss_blogs"
CACHE="$JOB_DIR/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/rss_blogs.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
TO="${OPENCLAW_PHONE:-+85200000000}"
PYTHON3=/usr/bin/python3

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] rss_blogs: $1" >&2; }

# V37.9.36: source notify.sh 让 fail-fast alert 走统一 [SYSTEM_ALERT] 通道
# (与 V37.5 kb_review / V37.8.10 kb_evening / V37.9.16 kb_deep_dive 同款模式)
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

# V37.9.36: fail-fast alert helper — LLM 失败时推 [SYSTEM_ALERT] 给 Discord #alerts
send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] rss_blogs LLM 失败
时间: $TS
原因: $reason
降级处理: 今日未推送博客精选 (避免占位符污染)
建议: 查 Adapter/Proxy 状态 + ${LLM_RAW:-llm_raw_last.txt}"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        "$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# RSS 博客订阅" > "$KB_SRC"

# ── RSS 源配置（按需添加新博客）──────────────────────────────────────
# 格式：name|feed_url|label
RSS_FEEDS=(
    "科学空间|https://spaces.ac.cn/feed|苏剑林(NLP/深度学习)"
    "Lil'Log|https://lilianweng.github.io/index.xml|Lilian Weng/OpenAI(LLM/Agent综述)"
    "Simon Willison|https://simonwillison.net/atom/everything/|Simon Willison(LLM工具/实践)"
    "Latent Space|https://www.latent.space/feed|Swyx&Alessio(AI工程/Agent架构)"
    # V37.9.6 移除: LangChain 博客 RSS 持续 9 次 HTTP 404 (4/20 18:00 watchdog 仍报),
    # 上游 feed 已死链。移除止噪音, 如未来恢复或迁移路径再加回。
    # "LangChain|https://blog.langchain.dev/feed/|LangChain(Agent/RAG实战)"
)

SEEN_FILE="$CACHE/seen_urls.txt"
touch "$SEEN_FILE"
ALL_NEW_FILE="$CACHE/all_new.jsonl"
> "$ALL_NEW_FILE"

TOTAL_NEW=0

for feed_entry in "${RSS_FEEDS[@]}"; do
    IFS='|' read -r FEED_NAME FEED_URL FEED_LABEL <<< "$feed_entry"
    FEED_FILE="$CACHE/feed_$(echo "$FEED_NAME" | tr ' ' '_').xml"

    # 抓取 RSS
    FETCH_OK=false
    for attempt in 1 2 3; do
        HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
            -H "User-Agent: openclaw-rss-monitor/1.0" \
            -o "$FEED_FILE" \
            "$FEED_URL" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "200" ] && [ -s "$FEED_FILE" ]; then
            FETCH_OK=true
            break
        else
            log "WARN: ${FEED_NAME} RSS HTTP ${HTTP_CODE} (attempt ${attempt})"
        fi
        sleep "$((attempt * 5))"
    done

    if [ "$FETCH_OK" != "true" ]; then
        log "WARN: ${FEED_NAME} RSS 抓取失败，跳过"
        continue
    fi

    # 解析 RSS XML → 提取新文章
    $PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$FEED_NAME" "$FEED_LABEL" << 'PYEOF' >> "$ALL_NEW_FILE"
import sys, json
import xml.etree.ElementTree as ET

feed_file = sys.argv[1]
seen_file = sys.argv[2]
feed_name = sys.argv[3]
feed_label = sys.argv[4]

with open(seen_file) as f:
    seen_urls = set(line.strip() for line in f if line.strip())

try:
    tree = ET.parse(feed_file)
    root = tree.getroot()
except ET.ParseError:
    # 尝试清理常见的 XML 问题
    with open(feed_file, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        print(f"[rss] ERROR: {feed_name} XML解析失败", file=sys.stderr)
        sys.exit(0)

# 支持 RSS 2.0 和 Atom 格式
ns = {'atom': 'http://www.w3.org/2005/Atom',
      'content': 'http://purl.org/rss/1.0/modules/content/',
      'dc': 'http://purl.org/dc/elements/1.1/'}

items = root.findall('.//item')  # RSS 2.0
if not items:
    items = root.findall('.//atom:entry', ns)  # Atom

new_count = 0
for item in items[:20]:  # 最多检查20篇
    # RSS 2.0
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
    description = ''
    if content_el is not None and content_el.text:
        # 去除 HTML 标签，取前500字
        import re
        description = re.sub(r'<[^>]+>', '', content_el.text)[:500]
    elif desc_el is not None and desc_el.text:
        import re
        description = re.sub(r'<[^>]+>', '', desc_el.text)[:500]
    pub_date = (date_el.text or '').strip()[:25] if date_el is not None else ''
    author = (author_el.text or '').strip() if author_el is not None else feed_name

    if not title or not link:
        continue
    if link in seen_urls:
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

    if new_count >= 5:  # 每个源每次最多5篇新文章
        break

print(f"[rss] {feed_name}: {new_count} 篇新文章", file=sys.stderr)
PYEOF
done

TOTAL_NEW="$(wc -l < "$ALL_NEW_FILE" | tr -d ' ')"
if [ "$TOTAL_NEW" -eq 0 ]; then
    log "无新文章，跳过推送。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi
echo "[rss] 共 ${TOTAL_NEW} 篇新文章"

# ── V37.9.37: 每篇独立调 LLM (5 字段深度分析 + 按评级调长度 + retry 3 次) ─
# 老 V37.9.36: 单次调用全部 N 篇, 一次失败 = 全篇失败
# 新 V37.9.37: 每篇独立调用 + 独立 retry (5s/10s/20s), 部分失败走 partial_degraded
# 全部失败 → 仍 fail-fast (V37.9.36 契约保留)

LLM_RAW="$CACHE/llm_raw_last.txt"   # 兼容: 保留上一次失败响应做 forensic
> "$LLM_RAW"
RESULTS_FILE="$CACHE/llm_results.jsonl"
> "$RESULTS_FILE"

# ── helper: 单篇 LLM 调用 + retry ───────────────────────────────────
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

# ── 主循环: 每篇文章独立调 LLM ────────────────────────────────────
TOTAL_FAILED=0
LAST_FAIL_REASON=""

for ((i=0; i<TOTAL_NEW; i++)); do
    SINGLE_PROMPT="$CACHE/llm_single_prompt_${i}.txt"
    $PYTHON3 - "$ALL_NEW_FILE" "$i" << 'PYEOF' > "$SINGLE_PROMPT"
import sys, json, os  # V37.9.58-hotfix: os 用于 V37.9.57 注入 HG_LEVEL_4_TEXT (line ~382) NameError fix

articles_file, idx = sys.argv[1], int(sys.argv[2])
with open(articles_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
a = articles[idx]
title = a['title']
desc = a.get('description', '')[:600]

prompt = """你是技术博客深度分析师 (兼 OpenClaw 项目对齐评估师)。对以下博文输出 6 字段中文分析:

📌 中文标题: 信达雅翻译, 不超过 25 字 (如原文已是中文则简化精炼)
🔑 核心要点: 3-5 条 bullet, 每条 1 句 ≤ 60 字, 列出文章最重要的发现/论点/事实
💡 关键洞察: 揭示作者立场 / 方法论 / 与行业趋势的关联 / 与已有工作的对比 / 局限性
   长度按评级动态调整: ⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字 (旗舰级文章充分展开)
🎯 实践启发: 1-3 条对 AI 工程师/创业者/架构师的具体行动建议, 每条 ≤ 80 字
⭐ 评级: ⭐ × N (1-5 个) + 推荐场景 (谁应该读 / 何时读 / 配什么场景)
🎚️ 项目对齐度: ⭐ × N (1-5 个) + 一句话原因 (≤ 30 字)
   ━ V37.9.51 新增 (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2 模板, 用于过滤 OpenClaw 高价值信号) ━

OpenClaw 项目方向 (参考评分):
   ⭐⭐⭐⭐⭐ = 直接相关 (control plane / agent runtime / ontology / governance / convergence framework / fail-fast / memory plane / multimodal routing / opportunity radar)
   ⭐⭐⭐⭐  = 间接相关 (tool plugin / KB RAG / semantic search / drift detection / declarative policy / agent reliability)
   ⭐⭐⭐    = 一般 AI/ML 趋势 (可借鉴但非核心, 如新模型架构 / training tricks / benchmark)
   ⭐⭐     = 无明显关联 (但可能未来有用, 比如纯 NLP 任务)
   ⭐      = 完全无关 (噪声, 比如硬件细节 / GPU kernel / 单纯学术 paper)

⚠️ 严格约束 (违反则整份输出作废):
- 只使用上方提供的标题和摘要中的信息, 严禁虚构作者未提及的事实/数据/链接
- 如摘要不足以判断深度, 标⭐较低 + 写"基于摘要的初步判断"
- 项目对齐度评分必须基于"是否能为 OpenClaw 控制平面 / 记忆平面 / ontology engine 提供有价值的借鉴", 而非泛泛 AI 相关
- 严禁推断 Hugging Face / OpenAI / GitHub 等平台的具体内部状态除非原文提及
- 严禁把 HTTP 错误码 / Python 异常 / 错误日志当外部信号

输出格式 (严格按此 6 字段, 字段间用空行分隔):

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
prompt += f"博文标题: {title}\n"
if desc:
    prompt += f"原文摘要:\n{desc}\n"
# V37.9.57: append LEVEL_4 反幻觉守卫 (MR-8 single-source-of-truth via env var)
prompt += os.environ.get('HG_LEVEL_4_TEXT', '')
print(prompt)
PYEOF

    log "调用 LLM 分析篇 $((i+1))/$TOTAL_NEW"
    if RESULT=$(call_llm_single_with_retry "$SINGLE_PROMPT" "$i"); then
        # 成功
        $PYTHON3 -c "
import json, sys
result = sys.stdin.read()
print(json.dumps({'idx': $i, 'content': result, 'failed': False, 'fail_reason': ''}, ensure_ascii=False))
" <<< "$RESULT" >> "$RESULTS_FILE"
    else
        # 全 retry 失败 → 标 degraded, 不阻塞其他篇
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        LAST_FAIL_REASON="$LAST_LLM_FAIL_REASON"
        log "FAIL: 篇 $((i+1)) 全 retry 失败 — $LAST_LLM_FAIL_REASON"
        $PYTHON3 -c "
import json, sys
print(json.dumps({'idx': $i, 'content': '', 'failed': True, 'fail_reason': '''$LAST_LLM_FAIL_REASON'''}, ensure_ascii=False))
" >> "$RESULTS_FILE"
    fi
done

# ── 决定整体 status (V37.9.36 fail-fast 契约保留) ──────────────────
if [ "$TOTAL_FAILED" -eq "$TOTAL_NEW" ]; then
    # 全部失败 → fail-fast (V37.9.36 同款)
    log "ERROR: 全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    send_alert "全部 $TOTAL_NEW 篇 LLM 分析失败 (last reason: $LAST_FAIL_REASON)"
    REASON_ESCAPED=$(echo "$LAST_FAIL_REASON" | tr '"' "'" | tr '\n' ' ' | head -c 200)
    printf '{"time":"%s","status":"llm_failed","new":%d,"sent":false,"reason":"all_failed_%s"}\n' "$TS" "$TOTAL_NEW" "$REASON_ESCAPED" > "$STATUS_FILE"
    exit 1
elif [ "$TOTAL_FAILED" -gt 0 ]; then
    log "WARN: $TOTAL_FAILED/$TOTAL_NEW 篇失败 — 走 partial_degraded (失败篇标 [LLM_DEGRADED] + RSS 摘要 fallback)"
    send_alert "$TOTAL_FAILED/$TOTAL_NEW 篇 LLM 部分失败 (其余正常推送, 失败篇标 [LLM_DEGRADED])"
fi

# ── V37.9.37: 组装消息 (5 字段解析 + LLM_DEGRADED fallback + 多窗口切片) ──
MSG_FILE="$CACHE/rss_message.txt"
$PYTHON3 - "$ALL_NEW_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << 'PYEOF'
import sys, json, re, os  # V37.9.51: os 用于 lazy import project_alignment_scorer 路径解析 (V37.9.50-hotfix 同款)

articles_file, results_file, day, msg_file = sys.argv[1:5]

with open(articles_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

# V37.9.51 6 字段 key-based parser (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 Opportunity Radar #2)
# 容忍 LLM 输出的字段顺序、单字段缺失、prefix 变体
def parse_6field_output(content):
    """从 LLM 输出解析 6 字段, key-based + tolerant.

    返回 dict: cn_title / highlights / insight / practice / rating / alignment
    V37.9.51: alignment 字段新增 (Opportunity Radar #2 PoC, V37.9.45/V37.9.50 同款模板)
    """
    fields = {
        'cn_title': '',
        'highlights': '',
        'insight': '',
        'practice': '',
        'rating': '',
        'alignment': '',
    }
    current_field = None
    current_buffer = []

    def flush():
        if current_field and current_buffer:
            fields[current_field] = '\n'.join(current_buffer).strip()

    for raw in content.split('\n'):
        line = raw.rstrip()
        # 跳过分隔符
        if re.match(r'^[-=*_]{3,}$', line.strip()):
            continue

        # 字段头识别 (key-based, 不依赖位置)
        # 📌 中文标题
        if line.lstrip().startswith('📌'):
            flush()
            current_field = 'cn_title'
            current_buffer = []
            # 提取冒号后的内容作为单行 title 值
            m = re.match(r'.*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)', line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # 🔑 核心要点
        if line.lstrip().startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        # 💡 关键洞察
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
        # ⭐ 评级
        if line.lstrip().startswith('⭐') and current_field != 'rating':
            # 仅当遇到独立的"⭐ 评级"行时切换 (而不是 highlights/insight 中可能含 ⭐)
            # 启发式: 行首字符是 ⭐ + 含"评级"或"推荐场景"
            if '评级' in line or '推荐场景' in line or re.match(r'\s*⭐+\s*$', line):
                flush()
                current_field = 'rating'
                current_buffer = [line.lstrip()]
                continue
        # 普通行 → append 到 current_field
        if current_field is not None:
            current_buffer.append(line)
        elif line.strip():
            # 字段头之前的非空行 (LLM 偶尔多嘴) → 静默丢弃
            pass

    flush()
    return fields


msg_lines = [f"📖 博客精选 ({day})", ""]

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
    print("[rss] V37.9.51 project_alignment_scorer 加载成功 (rule_check 启用)", file=sys.stderr)
except Exception as _e:
    print(f"[rss] V37.9.51 project_alignment_scorer 缺失或失败: {_e} (rule_check 跳过, FAIL-OPEN)", file=sys.stderr)

degraded_count = 0
llm_ok_count = 0
high_alignment_count = 0  # V37.9.51: ⭐≥4 alignment 计数 (Opportunity Radar #2)
for i, article in enumerate(articles):
    msg_lines.append(f"*博文{i+1}: {article['title']}*")
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
        # V37.9.51: 解析 6 字段 (V37.9.45 hf_papers / V37.9.50 同款 Opportunity Radar #2)
        fields = parse_6field_output(result.get('content', ''))
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
            # rating 行不重复加 ⭐ 前缀 (LLM 输出已含)
            msg_lines.append(fields['rating'])
            msg_lines.append("")
        # V37.9.51: 🎚️ 项目对齐度展示 + rule_check 验证 (V37.9.45 hf_papers / V37.9.50 同款)
        if fields['alignment']:
            msg_lines.append(f"🎚️ 项目对齐度: {fields['alignment']}")
            # rule_check: LLM ⭐ 评分 vs keyword-based rule 一致性
            if _validate_alignment_score and _concepts and _extract_star_count and _format_validation_marker:
                try:
                    llm_stars = _extract_star_count(fields['alignment'])
                    if llm_stars > 0:
                        # rule_content = title + description (V37.9.47 hf_papers 同款模式, blog 无 abstract 用 description)
                        rule_content = article.get('title', '') + ' ' + (article.get('description') or '')
                        validation = _validate_alignment_score(rule_content, llm_stars, _concepts)
                        marker = _format_validation_marker(validation)
                        if marker:  # validated=False 时返回 ⚠️ <reason>
                            msg_lines.append(marker)
                        if llm_stars >= 4:
                            high_alignment_count += 1
                except Exception as _e:
                    print(f"[rss] V37.9.51 rule_check 失败 article={i}: {_e} (FAIL-OPEN)", file=sys.stderr)
            msg_lines.append("")
        # 至少保证有 cn_title 才算 LLM 解析成功
        if fields['cn_title'] or fields['highlights'] or fields['insight']:
            llm_ok_count += 1

    msg_lines.append("---")
    msg_lines.append("")

# V37.9.51: 末尾追加高对齐统计 (Opportunity Radar #2)
total_articles = len(articles)
if total_articles > 0:
    msg_lines.append(f"━━━ 本轮高对齐博文 (项目对齐度 ⭐≥4): {high_alignment_count}/{total_articles} 篇 ━━━")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(msg_lines))

print(f"[rss] 消息组装完成: {len(articles)} 篇 (LLM 解析成功 {llm_ok_count}, degraded {degraded_count}, 高对齐 {high_alignment_count})", file=sys.stderr)
PYEOF

# ── 推送 WhatsApp + Discord (V37.9.37 多窗口分片: >8000 字才切, ≤8000 单段发) ─
SEND_ERR=$(mktemp)
TOTAL_LEN=$(wc -c < "$MSG_FILE" | tr -d ' ')
WA_SENT=false

if [ "$TOTAL_LEN" -le 8000 ]; then
    # 单段直发 (≤4000 不折叠 / 4000-8000 客户端自动折叠 2 气泡, V37.9.35 已验证)
    MSG_CONTENT="$(cat "$MSG_FILE")"
    # V37.9.171: 主推/分块走 notify.sh（微信→用户 + Discord #tech + 重试/队列），退役裸 whatsapp+discord 对
    if notify "$MSG_CONTENT" --topic tech 2>"$SEND_ERR"; then
        log "已推送 ${TOTAL_NEW} 篇 (单段, $TOTAL_LEN 字)"
        WA_SENT=true
    else
        log "ERROR: 推送失败: $(cat "$SEND_ERR" | head -3)"
    fi
else
    # 总长 >8000 → 多窗口切片 (V37.9.21 同款 mktemp + sleep 1s 防乱序)
    WA_CHUNK_DIR=$(mktemp -d)
    # V37.9.86: 合并 lock cleanup 防 bash trap override (lockdir 残留血案)
    trap 'rmdir "$LOCK" 2>/dev/null; rm -rf "$WA_CHUNK_DIR"' EXIT INT TERM

    $PYTHON3 - "$MSG_FILE" "$WA_CHUNK_DIR" "$DAY" << 'PYEOF'
import sys, os, re

msg_file, chunk_dir, day = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(msg_file, encoding='utf-8').read()
MAX_CHUNK = 4000

# 按 "\n---\n" 切分文章块, 第一块是 header "📖 博客精选 (date)"
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
        # 给每段加 [i/N] 标识让用户知道是连续的
        if i == 0:
            chunk = chunk.replace(f"📖 博客精选 ({day})",
                                  f"📖 博客精选 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"📖 博客精选 [{i+1}/{total_parts}] ({day}) (续)\n\n" + chunk
    with open(os.path.join(chunk_dir, f"{i:03d}.txt"), 'w', encoding='utf-8') as f:
        f.write(chunk)
PYEOF

    WA_PARTS_TOTAL=$(ls "$WA_CHUNK_DIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
    WA_SENT_OK=0
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        CHUNK_CONTENT="$(cat "$chunk_file")"
        if notify "$CHUNK_CONTENT" --topic tech 2>>"$SEND_ERR"; then
            WA_SENT_OK=$((WA_SENT_OK + 1))
        fi
        sleep 1  # 防 WhatsApp 消息乱序 (V37.9.21 契约)
    done
    log "已推送 ${TOTAL_NEW} 篇 (多窗口 ${WA_SENT_OK}/${WA_PARTS_TOTAL} 段, 共 $TOTAL_LEN 字)"
    if [ "$WA_SENT_OK" -gt 0 ]; then
        WA_SENT=true
    fi
fi

if [ "$WA_SENT" = "true" ]; then
    # 标记为已发送
    $PYTHON3 -c "
import json
with open('$ALL_NEW_FILE') as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            print(d.get('link', ''))
" >> "$SEEN_FILE"
    # status 区分 ok / partial_degraded
    if [ "$TOTAL_FAILED" -gt 0 ]; then
        printf '{"time":"%s","status":"partial_degraded","new":%d,"failed":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" "$TOTAL_FAILED" > "$STATUS_FILE"
    else
        printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
    fi
else
    log "ERROR: 推送全失败: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$TOTAL_NEW" > "$STATUS_FILE"
fi

# ── KB归档 ────────────────────────────────────────────────────────────
SUMMARY="$(cat "$MSG_FILE")"
if [ -n "$SUMMARY" ]; then
    DATE_KB=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')
    CONTENT="# RSS 博客 ${DATE_KB}

${SUMMARY}"
    bash "$KB_WRITE_SCRIPT" "$CONTENT" "rss-blogs" "note" 2>/dev/null || true
    echo "[rss] KB写入完成"
fi

# ── 永久归档 ──────────────────────────────────────────────────────────
# V37.6: idempotent H2-dedup append — 同一天多次运行不会产生重复 section
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"

# ── 清理seen缓存 ─────────────────────────────────────────────────────
if [ "$(wc -l < "$SEEN_FILE" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$SEEN_FILE" > "$SEEN_FILE.tmp" && mv "$SEEN_FILE.tmp" "$SEEN_FILE"
fi

# ── rsync备份 ─────────────────────────────────────────────────────────
bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$HOME/.kb/" "/Volumes/MOVESPEED/KB/"  # V37.9.27 jitter+retry+fail-loud+capture (replaces V37.9.4/V37.9.14 inline pattern)
log "完成"

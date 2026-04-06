#!/usr/bin/env bash
# kb_dream.sh — Agent "做梦"引擎 v2：MapReduce 全量 KB 探索
#
# 核心理念：不是总结，而是探索。在数据宇宙中寻找跨领域关联、反直觉趋势、被忽视的信号。
# 每天凌晨系统空闲时触发，对 KB 全量数据进行两阶段"计算性想象"。
#
# 架构（MapReduce）：
#   Phase 1 (Map)   — 每个 source 文件独立发送给 LLM，提取关键信号和异常点
#   Phase 2 (Reduce) — 汇总所有信号 + notes + 状态，进行跨领域关联发现
#
# 输出：~/.kb/dreams/YYYY-MM-DD.md + WhatsApp+Discord 推送精华洞察
#
# 用法：bash kb_dream.sh              # 正常运行（MapReduce 全量）
#       bash kb_dream.sh --dry-run    # 只展示输入数据统计，不调用 LLM
#       bash kb_dream.sh --fast       # 跳过 Map 阶段，直接采样做梦（旧模式）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -o pipefail
# 注意：不用 set -e，因为 find/wc/grep 在空目录下返回非零会中断脚本

# 防重叠执行
LOCK="/tmp/kb_dream.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[dream] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Dream LLM 配置：直接调 Adapter(:5001)，绕过 Proxy
# 资源竞争缓解策略：cron 调度到凌晨 4:00（GPU 低负载时段）+ 短响应自动重试
# Gemini 2.5 Flash 已验证不适合（中文质量差、免费 tier 限速、输出极短）
LLM_URL="http://localhost:5001/v1/chat/completions"
LLM_AUTH=""
LLM_MODEL="any"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DRY_RUN=false
FAST_MODE=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true
[ "${1:-}" = "--fast" ] && FAST_MODE=true

KB_BASE="${KB_BASE:-$HOME/.kb}"
DREAM_DIR="$KB_BASE/dreams"
DREAM_FILE="$DREAM_DIR/$DAY.md"
STATUS_FILE="$DREAM_DIR/.last_run.json"
MAP_DIR="$DREAM_DIR/.map_cache"
mkdir -p "$DREAM_DIR" "$MAP_DIR"

log() { echo "[$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')] dream: $1"; }

# UTF-8 安全截断函数
utf8_truncate() {
    local max_chars="${1:-20000}"
    python3 -c "
import sys
text = sys.stdin.read()
if len(text) > $max_chars:
    text = text[:$max_chars]
    last_nl = text.rfind('\n')
    if last_nl > int($max_chars * 0.9):
        text = text[:last_nl]
print(text)
"
}

# LLM 调用封装（含重试和错误诊断）
llm_call() {
    local prompt="$1"
    local max_tokens="${2:-1500}"
    local temp="${3:-0.8}"
    local timeout="${4:-120}"
    local result=""
    local raw=""
    local attempt=0
    local err_file=$(mktemp)

    while [ $attempt -lt 2 ]; do
        raw=$(curl -sS --max-time "$timeout" "$LLM_URL" \
            -H 'Content-Type: application/json' \
            -d "$(jq -nc --arg p "$prompt" --argjson mt "$max_tokens" --argjson t "$temp" \
                '{model:"any",messages:[{role:"user",content:$p}],max_tokens:$mt,temperature:$t,stream:false}')" \
            2>"$err_file" || true)

        # 尝试从标准 JSON 提取
        result=$(echo "$raw" | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)

        # 如果标准 JSON 失败，尝试解析 SSE 格式（data: {...}\n\n）
        if [ -z "${result// }" ] && echo "$raw" | grep -q '^data: ' 2>/dev/null; then
            log "  检测到 SSE 响应，解析中..."
            result=$(echo "$raw" | grep '^data: ' | grep -v '\[DONE\]' | sed 's/^data: //' \
                | jq -rs '[.[].choices[0].delta.content // empty] | join("")' 2>/dev/null || true)
        fi

        if [ -n "${result// }" ]; then
            rm -f "$err_file"
            echo "$result"
            return 0
        fi

        # 诊断失败原因
        local curl_err=$(cat "$err_file" 2>/dev/null)
        local error_msg=$(echo "$raw" | jq -r '.error.message // .error // empty' 2>/dev/null || true)
        [ -n "$curl_err" ] && log "  LLM curl error: $curl_err"
        [ -n "$error_msg" ] && log "  LLM API error: $error_msg"
        [ -z "$raw" ] && log "  LLM returned empty response"

        attempt=$((attempt + 1))
        [ $attempt -lt 2 ] && sleep 3
    done
    rm -f "$err_file"
    return 1
}

# ═══════════════════════════════════════════════════════════════════
# 1. 收集 KB 全量文件列表
# ═══════════════════════════════════════════════════════════════════

ALL_SOURCES=""
SRC_COUNT=0
if [ -d "$KB_BASE/sources" ]; then
    ALL_SOURCES=$(find "$KB_BASE/sources" -name "*.md" -size +0c 2>/dev/null | sort || true)
    [ -n "$ALL_SOURCES" ] && SRC_COUNT=$(echo "$ALL_SOURCES" | wc -l | tr -d ' ')
fi

ALL_NOTES=""
NOTE_COUNT=0
if [ -d "$KB_BASE/notes" ]; then
    ALL_NOTES=$(find "$KB_BASE/notes" -name "*.md" -size +0c 2>/dev/null | sort || true)
    [ -n "$ALL_NOTES" ] && NOTE_COUNT=$(echo "$ALL_NOTES" | wc -l | tr -d ' ')
fi

TOTAL_KB_BYTES=0
if [ -d "$KB_BASE/sources" ]; then
    TOTAL_KB_BYTES=$(find "$KB_BASE/sources" "$KB_BASE/notes" -name "*.md" -exec cat {} + 2>/dev/null | wc -c | tr -d ' ')
fi

log "KB 全量: sources=$SRC_COUNT files, notes=$NOTE_COUNT files, total=${TOTAL_KB_BYTES} bytes"

if $DRY_RUN; then
    echo "=== DRY RUN ==="
    echo "Sources: $SRC_COUNT files"
    echo "Notes: $NOTE_COUNT files"
    echo "Total KB size: $TOTAL_KB_BYTES bytes (~$((TOTAL_KB_BYTES / 1024))KB)"
    echo "Dream file: $DREAM_FILE"
    echo "Mode: $([ "$FAST_MODE" = true ] && echo 'FAST (single-pass)' || echo 'MAPREDUCE (two-phase)')"
    echo "=== Sources ==="
    echo "$ALL_SOURCES" 2>/dev/null
    echo "=== Notes ==="
    echo "$ALL_NOTES" 2>/dev/null
    exit 0
fi

# 素材太少则跳过
if [ "$SRC_COUNT" -eq 0 ] && [ "$NOTE_COUNT" -eq 0 ]; then
    log "KB 为空，跳过做梦"
    printf '{"time":"%s","status":"skip_no_data","sources":0,"notes":0}\n' "$TS" > "$STATUS_FILE"
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════
# 2. 历史梦境 + 项目状态（两个阶段都需要）
# ═══════════════════════════════════════════════════════════════════

# 最近 3 次梦境的主题（用于去重）
PREV_THEMES=""
if [ -d "$DREAM_DIR" ]; then
    PREV_FILES=$(ls -t "$DREAM_DIR"/*.md 2>/dev/null | head -3 || true)
    if [ -n "$PREV_FILES" ]; then
        while IFS= read -r pf; do
            [ -z "$pf" ] || [ ! -f "$pf" ] && continue
            pdate=$(basename "$pf" .md)
            themes=$(grep -E '^(##|###|\*\*|[*] )' "$pf" 2>/dev/null | head -12 | head -c 400)
            PREV_THEMES+="[$pdate] $themes
"
        done <<< "$PREV_FILES"
    fi
fi

# 项目状态
STATUS_CONTEXT=""
if [ -f "$KB_BASE/status.json" ]; then
    STATUS_CONTEXT=$(python3 -c "
import json
with open('$KB_BASE/status.json') as f:
    s = json.load(f)
priorities = s.get('priorities', [])
active = [p for p in priorities if p.get('status') == 'active']
print('当前活跃任务:')
for p in active[:5]:
    print(f'- {p.get(\"task\", \"\")}')
focus = s.get('focus', '')
if focus:
    print(f'本周焦点: {focus}')
" 2>/dev/null || echo "(status.json 解析失败)")
fi

# KB 趋势
TREND_CONTEXT=""
TREND_FILE="$KB_BASE/weekly_trend.md"
if [ -f "$TREND_FILE" ]; then
    TREND_CONTEXT=$(tail -40 "$TREND_FILE" 2>/dev/null | head -c 1500)
fi

# ═══════════════════════════════════════════════════════════════════
# 3. Phase 1 (Map)：每个 source 独立提取信号
#    每个文件 → LLM 提取 5-8 个关键信号（事实+日期+异常点）
#    并行度受 LLM 限制，串行处理但每次调用很轻量
# ═══════════════════════════════════════════════════════════════════

MAP_SIGNALS=""
MAP_COUNT=0

if [ "$FAST_MODE" = false ] && [ "$SRC_COUNT" -gt 0 ]; then
    log "Phase 1 (Map): 开始逐源提取信号..."

    MAP_PROMPT_TPL='你是一个数据矿工。从以下数据源中挖掘值得注意的信号。

数据源名称: %s
数据量: %d 行

完整内容:
---
%s
---

请提取 10-15 个值得注意的信号，每个信号一行，格式：
- [日期或时间段] 信号描述（具体事实，含关键数字/人名/技术名）

提取维度（每个维度至少 2 个信号）：
1. 反常数据点（数字突变、趋势逆转、异常沉默、与预期相反的结果）
2. 具体实体（人名/公司/技术/产品/论文标题——越具体越好，不要泛化为"某AI公司"）
3. 时间维度变化（加速、减速、消失、首次出现、周期性波动）
4. 容易被忽略的细节（脚注里的数字、附带提及的事实、数据中的空白区域）
5. 量化事实（具体数字、百分比、金额、排名变化——这些是最有价值的信号）

不要试图关联其他领域，只忠实提取本数据源中的事实。
只输出信号列表，不要前言或总结。控制在 500 字以内。'

    while IFS= read -r src; do
        [ -z "$src" ] && continue
        [ -f "$src" ] || continue
        name=$(basename "$src" .md)
        total_lines=$(wc -l < "$src" 2>/dev/null | tr -d ' ')
        [ -z "$total_lines" ] && total_lines=0
        [ "$total_lines" -eq 0 ] && continue

        # 检查 map 缓存（同一天同一文件大小不重复提取）
        file_size=$(wc -c < "$src" 2>/dev/null | tr -d ' ')
        # 缓存 key 含 prompt 版本哈希，prompt 变化时自动重新提取
        prompt_hash=$(echo "$MAP_PROMPT_TPL" | md5sum 2>/dev/null | cut -c1-8 || echo "v2")
        cache_key="${name}_${file_size}_${prompt_hash}"
        cache_file="$MAP_DIR/${DAY}_${cache_key}.txt"

        if [ -f "$cache_file" ]; then
            log "  Map [$name]: 使用缓存"
            signals=$(cat "$cache_file")
        else
            # 读取文件全文，用 UTF-8 安全截断到 15000 字符
            # 15K chars ≈ 4-5K tokens，Qwen3 262K context 轻松容纳
            full_content=$(cat "$src" 2>/dev/null | utf8_truncate 15000)

            prompt=$(printf "$MAP_PROMPT_TPL" "$name" "$total_lines" "$full_content")

            log "  Map [$name]: ${total_lines}行, ${file_size}B → 提取信号..."
            signals=$(llm_call "$prompt" 1200 0.5 90 || true)

            if [ -n "${signals// }" ]; then
                echo "$signals" > "$cache_file"
            else
                log "  Map [$name]: LLM 返回空，跳过"
                continue
            fi
        fi

        MAP_SIGNALS+="
## $name
$signals
"
        MAP_COUNT=$((MAP_COUNT + 1))
    done <<< "$ALL_SOURCES"

    log "Phase 1 完成: $MAP_COUNT/$SRC_COUNT sources 提取了信号"
fi

# ═══════════════════════════════════════════════════════════════════
# 4. 收集 Notes 素材
#    MapReduce 模式：Map 已覆盖 sources 全量，notes 只取最近 + 随机采样
#    Fast 模式：notes 全量读取（受 Reduce 截断保护）
# ═══════════════════════════════════════════════════════════════════

NOTES_MATERIAL=""
if [ -n "$ALL_NOTES" ]; then
    # 按修改时间倒序（最新在前）
    SORTED_NOTES=$(echo "$ALL_NOTES" | while read f; do
        [ -f "$f" ] && echo "$(stat -f '%m' "$f" 2>/dev/null || stat -c '%Y' "$f" 2>/dev/null || echo 0) $f"
    done | sort -rn | awk '{print $2}')

    NOTE_BUDGET=30   # MapReduce 模式下取最近 20 + 随机 10
    [ "$FAST_MODE" = true ] && NOTE_BUDGET=80
    NOTE_IDX=0
    NOTE_RECENT=20
    [ "$FAST_MODE" = true ] && NOTE_RECENT=60

    # 收集剩余 notes 路径供随机采样
    REMAINING_NOTES=""

    while IFS= read -r note; do
        [ -z "$note" ] && continue
        [ -f "$note" ] || continue
        NOTE_IDX=$((NOTE_IDX + 1))

        if [ "$NOTE_IDX" -le "$NOTE_RECENT" ]; then
            # 最近的 notes 直接取
            name=$(basename "$note" .md)
            content=$(cat "$note" 2>/dev/null | utf8_truncate 2000)
            [ -z "${content// }" ] && continue
            NOTES_MATERIAL+="
### $name
$content
"
        else
            REMAINING_NOTES+="$note
"
        fi
    done <<< "$SORTED_NOTES"

    # 从剩余 notes 随机采样
    RANDOM_BUDGET=$((NOTE_BUDGET - NOTE_RECENT))
    if [ "$RANDOM_BUDGET" -gt 0 ] && [ -n "$REMAINING_NOTES" ]; then
        RANDOM_PICKS=$(echo "$REMAINING_NOTES" | grep -v '^$' | sort -R 2>/dev/null | head -"$RANDOM_BUDGET" || \
                       echo "$REMAINING_NOTES" | grep -v '^$' | awk 'BEGIN{srand()} {print rand(), $0}' | sort -n | head -"$RANDOM_BUDGET" | awk '{print $2}')
        while IFS= read -r note; do
            [ -z "$note" ] && continue
            [ -f "$note" ] || continue
            name=$(basename "$note" .md)
            content=$(cat "$note" 2>/dev/null | utf8_truncate 2000)
            [ -z "${content// }" ] && continue
            NOTES_MATERIAL+="
### $name (历史)
$content
"
        done <<< "$RANDOM_PICKS"
    fi

    log "notes 采样完成: $NOTE_IDX total, 取 $NOTE_RECENT recent + $RANDOM_BUDGET random = $NOTE_BUDGET"
fi

# ═══════════════════════════════════════════════════════════════════
# 5. Phase 2 (Reduce)：跨领域关联发现
#    输入：Map 阶段提取的所有信号 + Notes + 状态 + 趋势
#    输出：梦境报告
# ═══════════════════════════════════════════════════════════════════

log "Phase 2 (Reduce): 开始跨领域关联..."

# 组装 Reduce 素材
if [ "$FAST_MODE" = true ] || [ -z "${MAP_SIGNALS// }" ]; then
    # Fast 模式或 Map 失败：回退到直接采样
    log "使用直接采样模式"
    REDUCE_INTRO="以下是系统知识库的全量采样数据（涵盖论文、技术博客、HackerNews、航运动态、项目笔记等多个领域）："
    REDUCE_DATA=""

    # Sources: 加大采样量
    if [ -n "$ALL_SOURCES" ]; then
        while IFS= read -r src; do
            [ -z "$src" ] && continue
            [ -f "$src" ] || continue
            name=$(basename "$src" .md)
            total_lines=$(wc -l < "$src" 2>/dev/null | tr -d ' ')
            [ -z "$total_lines" ] && total_lines=0
            [ "$total_lines" -eq 0 ] && continue

            # 头 10 行 + 尾 100 行 + 随机 20 行
            head_content=$(head -10 "$src" 2>/dev/null | head -c 500)
            tail_content=$(tail -100 "$src" 2>/dev/null | utf8_truncate 3000)
            mid_content=""
            if [ "$total_lines" -gt 120 ]; then
                rand_offset=$(( RANDOM % (total_lines / 2) + total_lines / 4 ))
                mid_content=$(tail -n +${rand_offset} "$src" 2>/dev/null | head -20 | utf8_truncate 1000)
            fi

            REDUCE_DATA+="
### $name (${total_lines}行)
[起源] $head_content
[历史] $mid_content
[最新] $tail_content
"
        done <<< "$ALL_SOURCES"
    fi

    # Notes 全量
    REDUCE_DATA+="$NOTES_MATERIAL"
else
    # MapReduce 模式：用 Map 阶段的精炼信号
    REDUCE_INTRO="以下是系统知识库的 **全量深度分析结果**。Phase 1 已对 $MAP_COUNT 个数据源逐一进行了信号提取（覆盖全部 ${TOTAL_KB_BYTES} 字节数据），以下是每个源的关键信号："
    REDUCE_DATA="
# Phase 1 提取的信号（全量 KB 覆盖）
$MAP_SIGNALS

# 笔记全文
$NOTES_MATERIAL
"
fi

# 加上状态、趋势、历史梦境
REDUCE_DATA+="
# 项目状态
$STATUS_CONTEXT

# 本周趋势
$TREND_CONTEXT
"

# 截断 Reduce 素材到 80K chars（直接调 Adapter，无 Proxy 200KB 限制）
# Qwen3-235B 262K context，80K chars ≈ 25-30K tokens，留足空间给 prompt + 8K output
REDUCE_MATERIAL=$(echo "$REDUCE_DATA" | utf8_truncate 80000)
REDUCE_CHARS=$(echo "$REDUCE_MATERIAL" | wc -c | tr -d ' ')
log "Reduce 素材: ${REDUCE_CHARS} bytes (截断前 $(echo "$REDUCE_DATA" | wc -c | tr -d ' ') bytes)"

REDUCE_PROMPT="你是一个在海量数据中寻找蛛丝马迹的探索者。你的目标是发现真正有价值的隐藏信号，而不是把不相关的领域硬凑在一起。

$REDUCE_INTRO

---
$REDUCE_MATERIAL
---

这些数据是花费大量算力（14 个数据源逐一深度分析）的结果。不要浪费在浅尝辄止的多主题分析上。

**核心要求：每天只深挖一个主题，但用全部分析维度去钻透它。**

第一步：从所有信号中选出今天最有价值的一个发现。选题标准：
1. 有扎实的多源证据链（至少 3 个不同数据源互相印证）
2. 对我们的项目或技术方向有直接可操作的启示
3. 反直觉、容易被忽视、但有数据支撑的信号
4. 如果与最近梦境同一主题，必须有新角度或新证据，不要简单重复

$([ -n "$PREV_THEMES" ] && echo "### 最近梦境主题（仅供参考，如果同一热点有新角度可以继续深挖）
$PREV_THEMES")

第二步：围绕这一个主题，严格按以下结构深度展开：

## 🌙 今日深度发现：[一句话主题]

### 发现过程
像侦探一样描述：哪些数据源的哪些条目最先引起注意？信号是如何从不同数据源中逐步浮现并互相印证的？

### 🔗 隐藏关联
围绕这个主题，列出 3-5 个隐藏的关联：
- 每个关联需标注证据链：A事实([数据源, 日期]) → B事实([数据源, 日期]) → 因此C
- 关联可以是同一领域内的深层联系，也可以是跨领域的意外连接
- 如果有矛盾的证据，也要列出并分析为什么矛盾

### 🔮 趋势推演
基于这个主题的证据，推演 2-3 个未来走向：
- **趋势名**
- **数据证据**（具体引用源、日期、数字）
- **推演逻辑**（为什么这些数据暗示了这个方向）
- **时间窗口**（萌芽期/加速期/拐点？6 个月/1 年/3 年后会怎样？）
- **如果成真的影响**

### 💎 被忽视的信号
围绕这个主题，找出 2-3 个藏在数据中容易被忽略的信号：
- **是什么**（具体数字、事件、异常）
- **在哪发现的**（数据源、日期）
- **为什么被忽视**（人们通常怎么忽略它）
- **为什么值得关注**（它暗示了什么更深层的变化）

### 🎯 行动建议（按优先级排列）
基于以上全部分析，给出 3-5 个具体可执行的建议：
- **做什么**（具体到这周可以直接执行的操作）
- **为什么现在做**（时间窗口/机会成本）
- **怎么验证**（怎么知道做对了）
- **预期产出**（做完后能得到什么具体的东西）

### 📊 数据质量备注
哪些数据源为这个主题贡献了关键证据？哪些数据源信息密度低或更新滞后？本次分析存在哪些信息盲区，需要补充什么新的数据源？

---

写作要求：
- 像写给技术决策者的专业分析备忘录，不是写给 AI 看的
- 所有维度（关联、推演、信号、建议）都必须紧扣同一个主题，形成完整的分析闭环
- 每个论点都要有出处（数据源+日期+具体内容），不允许空泛断言
- 不要客套话和铺垫，直接进入核心发现
- 行动建议必须具体到可以立即执行，拒绝「关注某某趋势」这种空话
- 目标 2000-3000 字，Markdown 格式"

PROMPT_BYTES=$(echo "$REDUCE_PROMPT" | wc -c | tr -d ' ')
log "Reduce prompt: ${PROMPT_BYTES} bytes → 发送 LLM..."

# 安全检查：prompt 超过 180KB 则截断（Proxy 限制 200KB，留 20KB 给 JSON 包装）
if [ "$PROMPT_BYTES" -gt 500000 ]; then
    log "WARN: Reduce prompt 过大 (${PROMPT_BYTES}B > 500KB)，回退到 40K 素材"
    REDUCE_MATERIAL=$(echo "$REDUCE_DATA" | utf8_truncate 40000)
    # 重新构建 prompt（用简化版，避免递归展开）
    REDUCE_PROMPT="你是一个在海量数据中寻找蛛丝马迹的探索者。

$REDUCE_INTRO

---
$REDUCE_MATERIAL
---

从所有信号中选出最有价值的一个发现，深度分析：证据全景（多源互证）→ 深层分析（本质+阶段+推演）→ 对我们的启示 → 2-3 个这周可执行的行动步骤。
每个论点必须引用具体数据源名称和日期。目标 1500 字。"
    PROMPT_BYTES=$(echo "$REDUCE_PROMPT" | wc -c | tr -d ' ')
    log "回退后 prompt: ${PROMPT_BYTES} bytes"
fi

# Reduce 调用 + 短响应自动重试
# LLM 响应不稳定：同样的 prompt 有时产出 17KB，有时只有 2KB
# 如果响应 < 4000 字符（约 1000 字），大概率是被截断，值得重试
MIN_DREAM_CHARS=4000
MAX_RETRIES=2
DREAM_RESULT=""

for retry in $(seq 1 $MAX_RETRIES); do
    DREAM_RESULT=$(llm_call "$REDUCE_PROMPT" 8000 0.85 300 || true)
    DREAM_CHARS=$(echo "$DREAM_RESULT" | wc -c | tr -d ' ')

    if [ -z "${DREAM_RESULT// }" ]; then
        log "Reduce 尝试 $retry/$MAX_RETRIES: 空响应"
    elif [ "$DREAM_CHARS" -lt "$MIN_DREAM_CHARS" ]; then
        log "Reduce 尝试 $retry/$MAX_RETRIES: 响应过短 (${DREAM_CHARS} chars < ${MIN_DREAM_CHARS})，重试..."
    else
        log "Reduce 尝试 $retry/$MAX_RETRIES: 成功 (${DREAM_CHARS} chars)"
        break
    fi

    [ "$retry" -lt "$MAX_RETRIES" ] && sleep 5
done

if [ -z "${DREAM_RESULT// }" ]; then
    log "ERROR: Phase 2 所有重试均失败 (prompt was ${PROMPT_BYTES} bytes)"
    printf '{"time":"%s","status":"llm_failed","phase":"reduce","map_count":%d,"reduce_chars":%d,"prompt_bytes":%d}\n' \
        "$TS" "$MAP_COUNT" "$REDUCE_CHARS" "$PROMPT_BYTES" > "$STATUS_FILE"
    exit 1
fi

DREAM_CHARS=$(echo "$DREAM_RESULT" | wc -c | tr -d ' ')
log "最终梦境: ${DREAM_CHARS} chars"

# ═══════════════════════════════════════════════════════════════════
# 6. 输出"梦境"
# ═══════════════════════════════════════════════════════════════════

MODE_DESC="MapReduce 全量（$MAP_COUNT 源 × 独立信号提取 → 跨域关联）"
[ "$FAST_MODE" = true ] || [ "$MAP_COUNT" -eq 0 ] && MODE_DESC="直接采样（80K chars）"

{
    echo "# 🌙 Agent Dream — $DAY"
    echo ""
    echo "> 模式: $MODE_DESC"
    echo "> 覆盖: $SRC_COUNT sources ($((TOTAL_KB_BYTES / 1024))KB) + $NOTE_COUNT notes"
    echo "> Reduce 素材: ${REDUCE_CHARS} chars"
    echo "> 生成时间: $(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
    echo ""
    echo "$DREAM_RESULT"
    echo ""
    echo "---"
    echo "*Generated by kb_dream.sh v2 (MapReduce) — ${TOTAL_KB_BYTES} bytes of knowledge, ${MAP_COUNT} sources deep-analyzed, every signal counts.*"
} > "$DREAM_FILE"

log "梦境已写入: $DREAM_FILE ($(wc -c < "$DREAM_FILE" | tr -d ' ') bytes)"

# ═══════════════════════════════════════════════════════════════════
# 7. 推送 + 状态记录
# ═══════════════════════════════════════════════════════════════════

# 推送完整梦境（分段发送，每段 ≤ 4000 字符，确保 WhatsApp 可读性）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NOTIFY_PATH=""
for np in "$SCRIPT_DIR/notify.sh" "$HOME/notify.sh"; do
    [ -f "$np" ] && NOTIFY_PATH="$np" && break
done

SENT=false
if [ -n "$NOTIFY_PATH" ]; then
    source "$NOTIFY_PATH"

    # 用 Python 按章节智能分段，写入临时文件
    CHUNK_DIR=$(mktemp -d)
    TOTAL_PARTS=$(python3 -c "
import sys, os

text = sys.stdin.read()
chunk_dir = '$CHUNK_DIR'
max_chunk = 4000
sections = text.split('\n## ')
chunks = []
current = ''

for i, sec in enumerate(sections):
    piece = sec if i == 0 else '## ' + sec
    if len(current) + len(piece) + 1 <= max_chunk:
        current = current + '\n' + piece if current else piece
    else:
        if current:
            chunks.append(current.strip())
        while len(piece) > max_chunk:
            cut = piece[:max_chunk].rfind('\n')
            if cut < int(max_chunk * 0.5):
                cut = max_chunk
            chunks.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        current = piece
if current.strip():
    chunks.append(current.strip())

for idx, chunk in enumerate(chunks):
    with open(os.path.join(chunk_dir, f'{idx:03d}.txt'), 'w') as f:
        f.write(chunk)

print(len(chunks))
" <<< "$DREAM_RESULT")

    PART_IDX=0
    SEND_OK=0

    for chunk_file in "$CHUNK_DIR"/*.txt; do
        [ -f "$chunk_file" ] || continue
        PART_IDX=$((PART_IDX + 1))
        segment=$(cat "$chunk_file")

        if [ "$TOTAL_PARTS" -gt 1 ]; then
            PUSH_MSG="🌙 Agent Dream ($DAY) [$PART_IDX/$TOTAL_PARTS]

$segment"
        else
            PUSH_MSG="🌙 Agent Dream ($DAY)

$segment"
        fi

        if notify "$PUSH_MSG" --topic daily; then
            SEND_OK=$((SEND_OK + 1))
        else
            log "WARN: 第 $PART_IDX/$TOTAL_PARTS 段推送失败"
        fi

        # 段间间隔 1 秒，避免消息乱序
        [ "$PART_IDX" -lt "$TOTAL_PARTS" ] && sleep 1
    done

    rm -rf "$CHUNK_DIR"

    if [ "$SEND_OK" -gt 0 ]; then
        log "梦境已推送 $SEND_OK/$TOTAL_PARTS 段到 WhatsApp + Discord"
        SENT=true
    else
        log "WARN: 所有段推送失败"
    fi
else
    log "WARN: notify.sh 未找到，跳过推送"
fi

# 状态记录
printf '{"time":"%s","status":"ok","mode":"%s","map_count":%d,"sources":%d,"notes":%d,"kb_bytes":%d,"reduce_chars":%d,"dream_bytes":%d,"sent":%s}\n' \
    "$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')" \
    "$([ "$FAST_MODE" = true ] && echo 'fast' || echo 'mapreduce')" \
    "$MAP_COUNT" "$SRC_COUNT" "$NOTE_COUNT" "$TOTAL_KB_BYTES" "$REDUCE_CHARS" \
    "$(wc -c < "$DREAM_FILE" | tr -d ' ')" "$SENT" > "$STATUS_FILE"

# 清理过期 map 缓存（保留 3 天）
find "$MAP_DIR" -name "*.txt" -mtime +3 -delete 2>/dev/null || true

# rsync 备份
rsync -a --quiet "$KB_BASE/dreams/" "/Volumes/MOVESPEED/KB/dreams/" 2>/dev/null || true

log "完成。模式=$MODE_DESC"

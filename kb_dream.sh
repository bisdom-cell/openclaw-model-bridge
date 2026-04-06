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

PROXY_URL="http://localhost:5002/v1/chat/completions"
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
        raw=$(curl -sS --max-time "$timeout" "$PROXY_URL" \
            -H 'Content-Type: application/json' \
            -d "$(jq -nc --arg p "$prompt" --argjson mt "$max_tokens" --argjson t "$temp" \
                '{model:"any",messages:[{role:"user",content:$p}],max_tokens:$mt,temperature:$t}')" \
            2>"$err_file" || true)

        # 尝试提取内容
        result=$(echo "$raw" | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)

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
        cache_key="${name}_${file_size}"
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

# 截断 Reduce 素材到 50K chars（≈ 100-150KB UTF-8，低于 Proxy 200KB 限制）
REDUCE_MATERIAL=$(echo "$REDUCE_DATA" | utf8_truncate 50000)
REDUCE_CHARS=$(echo "$REDUCE_MATERIAL" | wc -c | tr -d ' ')
log "Reduce 素材: ${REDUCE_CHARS} bytes (截断前 $(echo "$REDUCE_DATA" | wc -c | tr -d ' ') bytes)"

REDUCE_PROMPT="你是一个在海量数据中寻找蛛丝马迹的探索者。你的目标是发现真正有价值的隐藏信号，而不是把不相关的领域硬凑在一起。

$REDUCE_INTRO

---
$REDUCE_MATERIAL
---

$([ -n "$PREV_THEMES" ] && echo "### 最近梦境主题（必须避免重复这些发现）
$PREV_THEMES")

这些数据是花费大量算力逐源深度分析的结果，请充分利用每一条信号，产出尽可能丰富详尽的分析。严格按以下格式输出：

## 🌙 隐藏关联（3-5 个）
找出数据中**真实存在**的关联。每个关联必须包含：
- **标题**（一句话概括）
- **证据链**（A事实 → B事实 → 因此C，引用具体数据源名称和日期）
- **为什么重要**（这个关联意味着什么，对我们有什么启示）

不要为了跨领域而跨领域——同一领域内的深层关联同样有价值。但如果确实发现了跨域联系，要详细解释逻辑链条。

## 🔮 趋势推演（3-5 个）
基于数据中的**具体数字或事件序列**推演走向。每个推演必须包含：
- **趋势名**
- **数据证据**（具体引用哪个源、什么日期、什么数字）
- **推演逻辑**（为什么这个数据点暗示了某个方向）
- **时间窗口**（这个趋势大概在什么时间范围内会显现）
- **如果成真的影响**（对技术/行业/我们的项目意味着什么）

## 💎 被忽视的信号（2-4 个）
藏在数据中但容易被忽略的重要信息。每个信号必须包含：
- **信号是什么**（一个具体的数字、事件、异常）
- **在哪发现的**（数据源名称、日期）
- **为什么被忽视**（通常人们会怎么看待/忽略它）
- **为什么值得关注**（它暗示了什么更大的变化）

## 🎯 行动建议（3-5 个，按优先级排列）
基于以上所有发现，给出具体可执行的建议。每个建议必须包含：
- **做什么**（具体到可以立即执行的步骤）
- **为什么现在做**（时间窗口/机会成本）
- **预期收益**（做了之后能得到什么）
- **验证方法**（怎么知道做对了）

## 📊 数据质量观察
对本次分析的数据源质量做简短评价：哪些源信息密度最高、哪些源最近更新滞后、哪些源之间存在信息冗余、是否有明显的信息盲区。

核心原则：
- 这次分析覆盖了全量 KB 数据，代价不小——请充分产出，不要吝啬篇幅
- 质量仍然最重要：一个有扎实证据链的发现，胜过三个牵强附会的关联
- 宁可说「没发现跨域关联」也不要编造
- 每个论点必须引用具体的数据源名称、日期或数字
- 总输出 1500-2500 字，Markdown 格式，尽可能详尽"

PROMPT_BYTES=$(echo "$REDUCE_PROMPT" | wc -c | tr -d ' ')
log "Reduce prompt: ${PROMPT_BYTES} bytes → 发送 LLM..."

# 安全检查：prompt 超过 180KB 则截断（Proxy 限制 200KB，留 20KB 给 JSON 包装）
if [ "$PROMPT_BYTES" -gt 180000 ]; then
    log "WARN: Reduce prompt 过大 (${PROMPT_BYTES}B > 180KB)，回退到 30K 素材"
    REDUCE_MATERIAL=$(echo "$REDUCE_DATA" | utf8_truncate 30000)
    # 重新构建 prompt（用简化版，避免递归展开）
    REDUCE_PROMPT="你是一个在海量数据中寻找蛛丝马迹的探索者。

$REDUCE_INTRO

---
$REDUCE_MATERIAL
---

请找出 2-3 个隐藏关联 + 2-3 个趋势推演 + 1-2 个被忽视的信号 + 1-2 个行动建议。
每个论点必须引用具体的数据源名称和日期。质量优先，控制在 800 字以内。"
    PROMPT_BYTES=$(echo "$REDUCE_PROMPT" | wc -c | tr -d ' ')
    log "回退后 prompt: ${PROMPT_BYTES} bytes"
fi

DREAM_RESULT=$(llm_call "$REDUCE_PROMPT" 4000 0.85 240 || true)

if [ -z "${DREAM_RESULT// }" ]; then
    log "ERROR: Phase 2 LLM 返回空结果 (prompt was ${PROMPT_BYTES} bytes)"
    printf '{"time":"%s","status":"llm_failed","phase":"reduce","map_count":%d,"reduce_chars":%d,"prompt_bytes":%d}\n' \
        "$TS" "$MAP_COUNT" "$REDUCE_CHARS" "$PROMPT_BYTES" > "$STATUS_FILE"
    exit 1
fi

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

PUSH_BODY=$(echo "$DREAM_RESULT" | utf8_truncate 1500)
PUSH_MSG="🌙 Agent Dream ($DAY)

$PUSH_BODY"

# 使用 notify.sh 统一双通道推送（WhatsApp + Discord，含自动重试+失败队列）
SENT=false
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for notify_path in "$SCRIPT_DIR/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$notify_path" ]; then
        source "$notify_path"
        if notify "$PUSH_MSG" --topic daily; then
            log "梦境已推送到 WhatsApp + Discord"
            SENT=true
        else
            log "WARN: notify.sh 推送失败"
        fi
        break
    fi
done
[ "$SENT" = false ] && log "WARN: notify.sh 未找到，跳过推送"

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

log "完成。今夜的梦境已记录（$MODE_DESC）。"

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

# LLM 调用封装（含重试）
llm_call() {
    local prompt="$1"
    local max_tokens="${2:-1500}"
    local temp="${3:-0.8}"
    local timeout="${4:-120}"
    local result=""
    local attempt=0

    while [ $attempt -lt 2 ]; do
        result=$(curl -sS --max-time "$timeout" "$PROXY_URL" \
            -H 'Content-Type: application/json' \
            -d "$(jq -nc --arg p "$prompt" --argjson mt "$max_tokens" --argjson t "$temp" \
                '{model:"any",messages:[{role:"user",content:$p}],max_tokens:$mt,temperature:$t}')" \
            2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)

        if [ -n "${result// }" ]; then
            echo "$result"
            return 0
        fi
        attempt=$((attempt + 1))
        [ $attempt -lt 2 ] && sleep 3
    done
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

请提取 5-8 个最值得注意的信号，每个信号一行，格式：
- [日期或时间段] 信号描述（具体事实，非观点）

重点关注：
1. 反常数据点（数字突变、趋势逆转、异常沉默）
2. 具体的人名/公司/技术/数字（越具体越好，不要泛化）
3. 时间维度上的变化（加速、减速、消失、首次出现）
4. 容易被忽略的细节（脚注里的数字、附带提及的事实）

不要试图关联其他领域，只忠实提取本数据源中的事实。
只输出信号列表，不要前言或总结。控制在 300 字以内。'

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
            signals=$(llm_call "$prompt" 800 0.5 60 || true)

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
# 4. 收集 Notes 素材（全量读取，notes 通常较短）
# ═══════════════════════════════════════════════════════════════════

NOTES_MATERIAL=""
if [ -n "$ALL_NOTES" ]; then
    while IFS= read -r note; do
        [ -z "$note" ] && continue
        [ -f "$note" ] || continue
        name=$(basename "$note" .md)
        content=$(cat "$note" 2>/dev/null | utf8_truncate 3000)
        [ -z "${content// }" ] && continue
        NOTES_MATERIAL+="
### $name
$content
"
    done <<< "$ALL_NOTES"
fi

# ═══════════════════════════════════════════════════════════════════
# 5. Phase 2 (Reduce)：跨领域关联发现
#    输入：Map 阶段提取的所有信号 + Notes + 状态 + 趋势
#    输出：梦境报告
# ═══════════════════════════════════════════════════════════════════

log "Phase 2 (Reduce): 开始跨领域关联..."

# 组装 Reduce 素材
if [ "$FAST_MODE" = true ] || [ -z "${MAP_SIGNALS// }" ]; then
    # Fast 模式或 Map 失败：回退到直接采样（加大到 80K）
    log "使用直接采样模式 (80K chars)"
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

# 截断 Reduce 素材到 80K（Qwen3 262K context 的 ~30%，留足空间给 prompt + 输出）
REDUCE_MATERIAL=$(echo "$REDUCE_DATA" | utf8_truncate 80000)
REDUCE_CHARS=$(echo "$REDUCE_MATERIAL" | wc -c | tr -d ' ')

REDUCE_PROMPT="你是一个在海量数据中寻找蛛丝马迹的探索者。你的目标是发现真正有价值的隐藏信号，而不是把不相关的领域硬凑在一起。

$REDUCE_INTRO

---
$REDUCE_MATERIAL
---

$([ -n "$PREV_THEMES" ] && echo "### 最近梦境主题（必须避免重复这些发现）
$PREV_THEMES")

请在这些数据中寻找蛛丝马迹。严格按以下格式输出：

## 🌙 隐藏关联
找出 2-3 个数据中**真实存在**的关联。关联必须有具体证据链（A事实 → B事实 → 因此C），不要为了跨领域而跨领域——如果两个领域确实没关系，就不要硬凑。同一领域内的深层关联同样有价值。

## 🔮 趋势推演
基于数据中的**具体数字或事件序列**推演 2-3 个走向。每个推演必须标注：数据点是什么 → 趋势方向 → 如果持续会怎样。拒绝没有数据支撑的泛泛预测。

## 💎 被忽视的信号
找出 1-2 个藏在数据中但容易被忽略的重要信息。越具体越好：一个具体的数字、一个反常的事件、一个突然消失的趋势。说清楚在哪个数据源、什么日期发现的。

## 🎯 行动建议
基于以上发现，给出 1-2 个这周可以立即执行的建议。必须具体（做什么、查什么、验证什么），不要「关注某某趋势」这种空话。

核心原则：
- 质量 > 数量：一个有扎实证据链的发现，胜过三个牵强附会的关联
- 宁可说「没发现跨域关联」也不要编造——同领域内的深层洞察同样珍贵
- 每个论点必须引用具体的数据源名称、日期或数字
- 总输出控制在 800 字以内，Markdown 格式"

DREAM_RESULT=$(llm_call "$REDUCE_PROMPT" 2000 0.9 180 || true)

if [ -z "${DREAM_RESULT// }" ]; then
    log "ERROR: Phase 2 LLM 返回空结果"
    printf '{"time":"%s","status":"llm_failed","phase":"reduce","map_count":%d,"chars":%d}\n' \
        "$TS" "$MAP_COUNT" "$REDUCE_CHARS" > "$STATUS_FILE"
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
    echo "*This dream was generated by kb_dream.sh v2 (MapReduce) — not a summary, but an exploration of ${TOTAL_KB_BYTES} bytes of knowledge.*"
} > "$DREAM_FILE"

log "梦境已写入: $DREAM_FILE ($(wc -c < "$DREAM_FILE" | tr -d ' ') bytes)"

# ═══════════════════════════════════════════════════════════════════
# 7. 推送 + 状态记录
# ═══════════════════════════════════════════════════════════════════

PUSH_BODY=$(echo "$DREAM_RESULT" | utf8_truncate 800)
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

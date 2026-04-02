#!/usr/bin/env bash
# kb_dream.sh — Agent "做梦"引擎：跨领域关联 + 趋势推演 + 洞察发现
#
# 核心理念：不是总结，而是探索。在数据宇宙中寻找跨领域关联、反直觉趋势、被忽视的信号。
# 每天凌晨系统空闲时触发，读取 KB 全量数据，让 LLM 进行"计算性想象"。
#
# 输出：~/.kb/dreams/YYYY-MM-DD.md + WhatsApp 推送精华洞察
#
# 用法：bash kb_dream.sh              # 正常运行
#       bash kb_dream.sh --dry-run    # 只展示输入数据统计，不调用 LLM
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -o pipefail
# 注意：不用 set -e，因为 find/wc/grep 在空目录下返回非零会中断脚本

# 防重叠执行
LOCK="/tmp/kb_dream.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[dream] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

PROXY_URL="http://localhost:5002/v1/chat/completions"
TO="${OPENCLAW_PHONE:-+85200000000}"
OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

KB_BASE="${KB_BASE:-$HOME/.kb}"
DREAM_DIR="$KB_BASE/dreams"
DREAM_FILE="$DREAM_DIR/$DAY.md"
STATUS_FILE="$DREAM_DIR/.last_run.json"
mkdir -p "$DREAM_DIR"

log() { echo "[$TS] dream: $1"; }

# ═══════════════════════════════════════════════════════════════════
# 1. 收集"梦的素材"：KB 全量数据（智能采样，避免 token 爆炸）
#    策略：全量文件 × 分层采样（标题概览 + 最新条目 + 历史随机段）
# ═══════════════════════════════════════════════════════════════════

# Sources: 全量 source 文件，每个文件采样三层
SOURCES_SUMMARY=""
ALL_SOURCES=""
SRC_COUNT=0
if [ -d "$KB_BASE/sources" ]; then
    ALL_SOURCES=$(find "$KB_BASE/sources" -name "*.md" 2>/dev/null | sort || true)
    if [ -n "$ALL_SOURCES" ]; then
        SRC_COUNT=$(echo "$ALL_SOURCES" | wc -l | tr -d ' ')
    fi

    while IFS= read -r src; do
        [ -z "$src" ] && continue
        [ -f "$src" ] || continue
        name=$(basename "$src" .md)
        total_lines=$(wc -l < "$src" 2>/dev/null | tr -d ' ')
        [ -z "$total_lines" ] && total_lines=0
        [ "$total_lines" -eq 0 ] && continue

        # 层1: 文件头部（标题 + 最早的几条，理解数据来源）
        head_content=$(head -10 "$src" 2>/dev/null | head -c 500)
        # 层2: 文件尾部（最新条目，了解最近动态）
        tail_content=$(tail -20 "$src" 2>/dev/null | head -c 1000)
        # 层3: 中间随机采样（挖掘历史中被忽视的信号）
        mid_content=""
        if [ "$total_lines" -gt 50 ]; then
            # 从中间 1/3 位置取 10 行
            mid_start=$(( total_lines / 3 ))
            mid_content=$(tail -n +${mid_start} "$src" 2>/dev/null | head -10 | head -c 600)
        fi

        SOURCES_SUMMARY+="
### $name (${total_lines}行, 全量采样)
[最早] $head_content
[历史] $mid_content
[最新] $tail_content
"
    done <<< "$ALL_SOURCES"
fi
log "sources 采样完成: $SRC_COUNT files"

# Notes: 全量笔记文件
NOTES_SUMMARY=""
NOTE_COUNT=0
if [ -d "$KB_BASE/notes" ]; then
    ALL_NOTES=$(find "$KB_BASE/notes" -name "*.md" 2>/dev/null | sort || true)
    while IFS= read -r note; do
        [ -z "$note" ] && continue
        [ -f "$note" ] || continue
        name=$(basename "$note" .md)
        total_lines=$(wc -l < "$note" 2>/dev/null | tr -d ' ')
        [ -z "$total_lines" ] && total_lines=0
        [ "$total_lines" -eq 0 ] && continue
        NOTE_COUNT=$((NOTE_COUNT + 1))

        # 笔记通常较短，取更多内容
        content=$(head -80 "$note" 2>/dev/null | head -c 2000)
        NOTES_SUMMARY+="
### $name (${total_lines}行)
$content
"
    done <<< "$ALL_NOTES"
fi
log "notes 采样完成: $NOTE_COUNT files"

# 历史梦境回顾（如果有前次梦境，避免重复同样的发现）
PREV_DREAMS=""
if [ -d "$DREAM_DIR" ]; then
    PREV_DREAM=$(ls -t "$DREAM_DIR"/*.md 2>/dev/null | head -1 || true)
    if [ -n "$PREV_DREAM" ] && [ -f "$PREV_DREAM" ]; then
        PREV_DREAMS="
### 上次梦境摘要
$(head -30 "$PREV_DREAM" 2>/dev/null | head -c 800)
"
    fi
fi

# Status: 项目当前状态
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
    print(f'- {p.get(\"title\", \"\")}')
focus = s.get('focus', '')
if focus:
    print(f'本周焦点: {focus}')
" 2>/dev/null || echo "(status.json 解析失败)")
fi

# KB 趋势（如果有最近的趋势报告）
TREND_CONTEXT=""
TREND_FILE="$KB_BASE/weekly_trend.md"
if [ -f "$TREND_FILE" ]; then
    TREND_CONTEXT=$(tail -40 "$TREND_FILE" 2>/dev/null | head -c 1500)
fi

# 统计
TOTAL_CHARS=$(printf "%s%s%s%s%s" "$SOURCES_SUMMARY" "$NOTES_SUMMARY" "$STATUS_CONTEXT" "$TREND_CONTEXT" "$PREV_DREAMS" | wc -c | tr -d ' ')
log "素材收集完成: sources=$SRC_COUNT files, total=${TOTAL_CHARS} chars (全量采样)"

if $DRY_RUN; then
    echo "=== DRY RUN ==="
    echo "Sources: $SRC_COUNT files (全量)"
    echo "Total chars: $TOTAL_CHARS"
    echo "Dream file: $DREAM_FILE"
    echo "=== Sources list ==="
    echo "$ALL_SOURCES" 2>/dev/null
    exit 0
fi

# 素材太少则跳过
if [ "$TOTAL_CHARS" -lt 500 ]; then
    log "素材不足 ($TOTAL_CHARS chars < 500)，跳过做梦"
    printf '{"time":"%s","status":"skip_no_data","chars":%d}\n' "$TS" "$TOTAL_CHARS" > "$STATUS_FILE"
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════
# 2. "做梦"：让 LLM 进行跨领域探索
# ═══════════════════════════════════════════════════════════════════

# 截断素材到 20000 chars（Qwen3-235B 262K context，留足空间给 prompt + 输出）
# 用变量截断避免 printf | head 的 broken pipe 问题
_RAW_MATERIAL=$(printf "%s\n\n%s\n\n%s\n\n%s\n\n%s" "$SOURCES_SUMMARY" "$NOTES_SUMMARY" "$STATUS_CONTEXT" "$TREND_CONTEXT" "$PREV_DREAMS")
MATERIAL="${_RAW_MATERIAL:0:20000}"

DREAM_PROMPT="你是一个「数据梦境分析师」。你的任务不是总结信息，而是在数据中发现隐藏的关联、趋势和可能性。

以下是系统全量知识库的采样数据（每个数据源包含最早/历史/最新三层采样，涵盖论文、技术博客、HackerNews、航运动态、项目笔记等多个领域的完整时间跨度）：

---
$MATERIAL
---

请进行「做梦」——在这些数据中进行跨领域的自由联想和推演。严格按以下格式输出：

## 🌙 跨领域关联（发现 2-3 个不同领域之间的意外联系）
每个关联：标题 + 2 句话解释为什么这两个看似不相关的领域有联系

## 🔮 趋势推演（基于数据推演 2-3 个可能的未来走向）
每个推演：趋势名 + 当前信号 + 如果继续发展会怎样

## 💎 被忽视的信号（找出 1-2 个数据中存在但可能被忽略的重要信息）
每个信号：是什么 + 为什么值得关注

## 🎯 行动建议（基于以上发现，给出 1-2 个具体可执行的建议）
每个建议：做什么 + 为什么现在做

要求：
- 大胆联想，但每个论点必须有数据支撑（引用具体的来源名称和内容）
- 特别关注跨越时间的关联（早期数据中的线索 + 最新数据中的印证）
- 宁可有创意但可能错误，也不要平庸但正确
- 如果有上次梦境摘要，避免重复相同发现，寻找新的角度
- 总输出控制在 600 字以内"

# 调用 LLM
DREAM_RESULT=$(curl -sS --max-time 120 "$PROXY_URL" \
    -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg p "$DREAM_PROMPT" '{model:"any",messages:[{role:"user",content:$p}],max_tokens:1500,temperature:0.9}')" \
    2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)

if [ -z "${DREAM_RESULT// }" ]; then
    log "ERROR: LLM 返回空结果"
    printf '{"time":"%s","status":"llm_failed","chars":%d}\n' "$TS" "$TOTAL_CHARS" > "$STATUS_FILE"
    exit 1
fi

# ═══════════════════════════════════════════════════════════════════
# 3. 输出"梦境"
# ═══════════════════════════════════════════════════════════════════

# 写入 dream 文件
{
    echo "# 🌙 Agent Dream — $DAY"
    echo ""
    echo "> 基于 $SRC_COUNT 个数据源、${TOTAL_CHARS} 字符素材的跨领域探索"
    echo "> 生成时间: $TS"
    echo ""
    echo "$DREAM_RESULT"
    echo ""
    echo "---"
    echo "*This dream was generated by kb_dream.sh — not a summary, but an exploration.*"
} > "$DREAM_FILE"

log "梦境已写入: $DREAM_FILE ($(wc -c < "$DREAM_FILE" | tr -d ' ') bytes)"

# WhatsApp 推送精华（截取前 800 字符）
WA_MSG="🌙 Agent Dream ($DAY)

$(echo "$DREAM_RESULT" | head -c 800)"

SEND_ERR=$(mktemp)
if "$OPENCLAW" message send --target "$TO" --message "$WA_MSG" --json >/dev/null 2>"$SEND_ERR"; then
    log "梦境已推送到 WhatsApp"
    printf '{"time":"%s","status":"ok","chars":%d,"sources":%d,"dream_bytes":%d,"sent":true}\n' \
        "$TS" "$TOTAL_CHARS" "$SRC_COUNT" "$(wc -c < "$DREAM_FILE" | tr -d ' ')" > "$STATUS_FILE"
else
    log "WARN: WhatsApp 推送失败: $(head -1 "$SEND_ERR")"
    printf '{"time":"%s","status":"ok","chars":%d,"sources":%d,"dream_bytes":%d,"sent":false}\n' \
        "$TS" "$TOTAL_CHARS" "$SRC_COUNT" "$(wc -c < "$DREAM_FILE" | tr -d ' ')" > "$STATUS_FILE"
fi

rm -f "$SEND_ERR"

# rsync 备份
rsync -a --quiet "$KB_BASE/dreams/" "/Volumes/MOVESPEED/KB/dreams/" 2>/dev/null || true

log "完成。今夜的梦境已记录。"

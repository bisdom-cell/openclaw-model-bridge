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

# 加载环境变量（cron 环境极简，OPENCLAW_PHONE/DISCORD_CH_* 等必须从 profile 获取）
# V37.1: 这是 Dream 推送长期失败的根因之一 — cron 中 env 为空导致 notify.sh 用占位号发送
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true

# 诊断日志（cron 环境调试）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] dream: START pid=$$ args=$* PATH=$PATH"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] dream: jq=$(which jq 2>/dev/null || echo MISSING) python3=$(which python3 2>/dev/null || echo MISSING) curl=$(which curl 2>/dev/null || echo MISSING)"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] dream: OPENCLAW_PHONE=${OPENCLAW_PHONE:-(unset)} DISCORD_CH_DAILY=${DISCORD_CH_DAILY:-(unset)}"

# 防重叠执行（含残留锁检测：超过 60 分钟视为残留，自动清理）
LOCK="/tmp/kb_dream.lockdir"
if [ -d "$LOCK" ]; then
    lock_age=$(( $(date +%s) - $(stat -f '%m' "$LOCK" 2>/dev/null || stat -c '%Y' "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -gt 3600 ]; then
        echo "[dream] Stale lock detected (${lock_age}s old), removing"
        rmdir "$LOCK" 2>/dev/null || rm -rf "$LOCK"
    else
        echo "[dream] Already running (${lock_age}s), skip"
        exit 0
    fi
fi
mkdir "$LOCK" 2>/dev/null || { echo "[dream] Lock contention, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# 全局超时保护：整个 Dream 不超过 25 分钟（V37.2 fix: 防止卡住影响后续 job）
# 04:30 启动 → 最迟 04:55 结束，留 5 分钟余量给 05:00 的其他 job
DREAM_START_EPOCH=$(date +%s)
DREAM_TIMEOUT_SEC=3600  # 60 minutes（凌晨整段时间预留给 Dream，全量无损运行）
check_deadline() {
    local elapsed=$(( $(date +%s) - DREAM_START_EPOCH ))
    if [ "$elapsed" -ge "$DREAM_TIMEOUT_SEC" ]; then
        log "⚠️ 全局超时 (${elapsed}s >= ${DREAM_TIMEOUT_SEC}s)，中止当前阶段"
        return 1
    fi
    return 0
}

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

# 提前加载 notify.sh（用于失败告警，不仅限于成功推送）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_DREAM_NOTIFY_LOADED=false
for _np in "$SCRIPT_DIR/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$_np" ]; then
        source "$_np"
        _DREAM_NOTIFY_LOADED=true
        log "notify.sh loaded from $_np"
        break
    fi
done
$_DREAM_NOTIFY_LOADED || log "WARN: notify.sh not found, push/alerts will be skipped"

# 失败告警：无论哪个阶段失败都通知用户（V37.1 核心修复：静默失败→显式告警）
dream_fail_alert() {
    local reason="$1"
    local msg="⚠️ Agent Dream 失败 ($DAY): $reason — 检查 ~/kb_dream.log"
    log "FAIL ALERT: $reason"
    if $_DREAM_NOTIFY_LOADED; then
        notify "$msg" --topic alerts 2>/dev/null || true
    fi
}

# 前置依赖检查：python3 + jq 是核心依赖，缺失则整个脚本无法工作（V37.2 fix: fail-fast）
for _dep in python3 jq curl; do
    if ! command -v "$_dep" >/dev/null 2>&1; then
        dream_fail_alert "$_dep 未找到，Dream 无法运行"
        exit 1
    fi
done

# LLM 健康检查：在开始 30 分钟的 MapReduce 前确认 Adapter 可达
# V37.1: 避免 LLM 不可用时白白浪费时间
llm_health=$(curl -s --max-time 10 "http://localhost:5001/health" 2>/dev/null || echo "")
if [ -z "$llm_health" ]; then
    dream_fail_alert "Adapter(:5001) 不可达，LLM 服务可能未启动"
    printf '{"time":"%s","status":"llm_unreachable"}\n' "$TS" > "$STATUS_FILE"
    exit 1
fi
log "LLM health OK: $(echo "$llm_health" | head -c 100)"

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

# LLM 调用封装（含智能退避重试和错误诊断）
# V37.1: 用 Python 构造 JSON + 临时文件传递，彻底避免 shell/jq 对大型 KB 数据的编码问题
# 旧方式 `jq --arg p "$prompt"` 在 KB 含特殊字符时会损坏请求体导致 LLM 空响应
# V38: 智能退避 — 429(rate limit)等60s, 524(timeout)等20s, 指数退避最多4次
LLM_CALL_MAX_RETRIES=4
llm_call() {
    local prompt="$1"
    local max_tokens="${2:-1500}"
    local temp="${3:-0.8}"
    local timeout="${4:-120}"
    local result=""
    local raw=""
    local attempt=0
    local err_file=$(mktemp)
    local body_file=$(mktemp)

    while [ $attempt -lt $LLM_CALL_MAX_RETRIES ]; do
        # 用 Python 安全构造 JSON（处理所有 Unicode/转义/控制字符）
        python3 -c "
import json, sys
prompt = sys.stdin.read()
body = {
    'model': 'any',
    'messages': [{'role': 'user', 'content': prompt}],
    'max_tokens': $max_tokens,
    'temperature': $temp,
    'stream': False
}
with open('$body_file', 'w') as f:
    json.dump(body, f, ensure_ascii=False)
" <<< "$prompt"

        raw=$(curl -sS --max-time "$timeout" "$LLM_URL" \
            -H 'Content-Type: application/json' \
            -d @"$body_file" \
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
            rm -f "$err_file" "$body_file"
            echo "$result"
            return 0
        fi

        # 诊断失败原因（V37.1: 记录原始响应前 500 字符）
        local curl_err=$(cat "$err_file" 2>/dev/null)
        local error_msg=$(echo "$raw" | jq -r '.error.message // .error // empty' 2>/dev/null || true)
        local raw_len=${#raw}
        local body_len=$(wc -c < "$body_file" 2>/dev/null | tr -d ' ')
        [ -n "$curl_err" ] && log "  LLM curl error: $curl_err"
        [ -n "$error_msg" ] && log "  LLM API error: $error_msg"
        if [ -z "$raw" ]; then
            log "  LLM returned completely empty response (0 bytes, request body was ${body_len} bytes)"
        else
            log "  LLM raw response: ${raw_len} bytes, first 500 chars: ${raw:0:500}"
        fi

        attempt=$((attempt + 1))
        if [ $attempt -lt $LLM_CALL_MAX_RETRIES ]; then
            # 智能退避：根据错误类型决定等待时间
            local wait_sec=5
            if echo "$raw" "$error_msg" | grep -qi "429\|rate.limit\|too many" 2>/dev/null; then
                # 429 Rate Limit: 等较长时间让配额恢复
                wait_sec=$((30 + attempt * 30))  # 60s, 90s, 120s
                log "  Rate limited (429), waiting ${wait_sec}s before retry $((attempt+1))/$LLM_CALL_MAX_RETRIES..."
            elif echo "$raw" "$error_msg" "$curl_err" | grep -qi "524\|timeout\|timed.out" 2>/dev/null; then
                # 524 Timeout: 服务端过载，适度等待
                wait_sec=$((10 + attempt * 10))  # 20s, 30s, 40s
                log "  Timeout (524), waiting ${wait_sec}s before retry $((attempt+1))/$LLM_CALL_MAX_RETRIES..."
            else
                # 其他错误: 指数退避
                wait_sec=$((3 * attempt * attempt))  # 3s, 12s, 27s
                log "  Waiting ${wait_sec}s before retry $((attempt+1))/$LLM_CALL_MAX_RETRIES..."
            fi
            sleep "$wait_sec"
        fi
    done
    rm -f "$err_file" "$body_file"
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
MAP_CONSECUTIVE_FAILS=0  # 连续失败计数：超过 3 次停止 Map，保护 fallback 配额

if [ "$FAST_MODE" = false ] && [ "$SRC_COUNT" -gt 0 ]; then
    log "Phase 1 (Map): 开始逐源提取信号..."

    while IFS= read -r src; do
        # 全局超时检查
        if ! check_deadline; then
            log "  ⚠️ Map 阶段超时，跳过剩余 sources"
            break
        fi
        # 连续失败熔断：3 次连续 LLM 失败 → 停止 Map，保留配额给 Reduce
        if [ "$MAP_CONSECUTIVE_FAILS" -ge 3 ]; then
            log "  ⚠️ 连续 ${MAP_CONSECUTIVE_FAILS} 次 LLM 失败，停止 Map 阶段（保护 fallback 配额）"
            break
        fi
        [ -z "$src" ] && continue
        [ -f "$src" ] || continue
        name=$(basename "$src" .md)
        total_lines=$(wc -l < "$src" 2>/dev/null | tr -d ' ')
        [ -z "$total_lines" ] && total_lines=0
        [ "$total_lines" -eq 0 ] && continue

        # 检查 map 缓存（同一天同一文件大小不重复提取）
        file_size=$(wc -c < "$src" 2>/dev/null | tr -d ' ')
        # 缓存 key 含 prompt 版本哈希，prompt 变化时自动重新提取
        prompt_hash="v3"  # bump when prompt template changes to invalidate cache
        cache_key="${name}_${file_size}_${prompt_hash}"
        cache_file="$MAP_DIR/${DAY}_${cache_key}.txt"

        if [ -f "$cache_file" ]; then
            log "  Map [$name]: 使用缓存"
            signals=$(cat "$cache_file")
            MAP_CONSECUTIVE_FAILS=0  # 缓存命中视为成功，重置计数（V37.2 fix: 防止假熔断）
        else
            # 读取文件全文，用 UTF-8 安全截断到 15000 字符
            # 15K chars ≈ 4-5K tokens，Qwen3 262K context 轻松容纳
            full_content=$(cat "$src" 2>/dev/null | utf8_truncate 15000)

            # 用 Python 安全拼接 prompt（避免 printf 对 KB 内容中 % 字符的格式化注入）
            prompt=$(python3 -c "
import sys
tpl = sys.stdin.read()
print(tpl.replace('TPL_NAME', sys.argv[1]).replace('TPL_LINES', sys.argv[2]).replace('TPL_CONTENT', sys.argv[3]))
" "$name" "$total_lines" "$full_content" <<'PROMPT_EOF'
你是一个数据矿工。从以下数据源中挖掘值得注意的信号。

数据源名称: TPL_NAME
数据量: TPL_LINES 行

完整内容:
---
TPL_CONTENT
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
只输出信号列表，不要前言或总结。控制在 500 字以内。
PROMPT_EOF
)

            log "  Map [$name]: ${total_lines}行, ${file_size}B → 提取信号..."
            signals=$(llm_call "$prompt" 1200 0.5 90 || true)

            if [ -n "${signals// }" ]; then
                echo "$signals" > "$cache_file"
                MAP_CONSECUTIVE_FAILS=0  # 成功，重置计数
                # 节流：Map 调用之间等待 5 秒，避免密集调用耗尽 fallback 配额
                sleep 5
            else
                MAP_CONSECUTIVE_FAILS=$((MAP_CONSECUTIVE_FAILS + 1))
                log "  Map [$name]: LLM 返回空，跳过 (连续失败: $MAP_CONSECUTIVE_FAILS)"
                sleep 10  # 失败后等更久再继续
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
# 4. Notes 也走 Map 阶段（与 Sources 同等待遇）
#    Notes 含用户与 PA 的重要交互信息，不能被 Sources 信号淹没
#    策略：按时间分批打包（每批 ~15KB），每批独立提取信号
# ═══════════════════════════════════════════════════════════════════

NOTES_SIGNALS=""
NOTES_MAP_COUNT=0

if [ "$FAST_MODE" = false ] && [ -n "$ALL_NOTES" ] && [ "$MAP_CONSECUTIVE_FAILS" -lt 3 ]; then
    log "Phase 1b (Map Notes): 开始从笔记中提取信号..."

    # 按修改时间倒序（最新在前）
    SORTED_NOTES=$(echo "$ALL_NOTES" | while read f; do
        [ -f "$f" ] && echo "$(stat -f '%m' "$f" 2>/dev/null || stat -c '%Y' "$f" 2>/dev/null || echo 0) $f"
    done | sort -rn | awk '{print $2}')

    # 将 notes 分批打包，每批 ~15KB
    BATCH=""
    BATCH_COUNT=0
    BATCH_NUM=0

    while IFS= read -r note; do
        # 全局超时检查
        if ! check_deadline; then
            log "  ⚠️ Notes Map 阶段超时，跳过剩余 notes"
            break
        fi
        [ -z "$note" ] && continue
        [ -f "$note" ] || continue
        name=$(basename "$note" .md)
        content=$(cat "$note" 2>/dev/null | utf8_truncate 2000)
        [ -z "${content// }" ] && continue

        BATCH+="
### $name
$content
"
        BATCH_COUNT=$((BATCH_COUNT + 1))

        # 每 15 条或累计 > 12000 字符，提交一批
        BATCH_SIZE=${#BATCH}
        if [ "$BATCH_COUNT" -ge 15 ] || [ "$BATCH_SIZE" -gt 12000 ]; then
            BATCH_NUM=$((BATCH_NUM + 1))

            # 检查缓存
            batch_hash=$(echo "$BATCH" | md5sum 2>/dev/null | cut -c1-12 || echo "b${BATCH_NUM}")
            cache_file="$MAP_DIR/${DAY}_notes_${batch_hash}.txt"

            if [ -f "$cache_file" ]; then
                log "  Map Notes [批次$BATCH_NUM]: 使用缓存 ($BATCH_COUNT 条)"
                signals=$(cat "$cache_file")
                MAP_CONSECUTIVE_FAILS=0  # 缓存命中视为成功，重置计数（V37.2 fix）
            else
                prompt=$(python3 -c "
import sys
tpl = sys.stdin.read()
print(tpl.replace('TPL_COUNT', sys.argv[1]).replace('TPL_CONTENT', sys.argv[2]))
" "$BATCH_COUNT" "$BATCH" <<'PROMPT_EOF'
你是一个数据矿工。以下是用户的个人笔记和与AI助手的交互记录。这些笔记包含用户认为重要的信息、决策、发现和想法。

数据类型: 用户笔记/交互记录
笔记数量: TPL_COUNT 条

完整内容:
---
TPL_CONTENT
---

请提取 8-12 个最值得注意的信号，每个信号一行，格式：
- [日期或笔记名] 信号描述（具体事实，含关键内容）

重点关注：
1. 用户明确标记为重要的信息（主动保存 = 用户认为有价值）
2. 用户的决策、判断、观点（反映用户的思考方向）
3. 跨多条笔记出现的主题（重复出现 = 持续关注）
4. 与外部数据源（论文、新闻、技术趋势）可能关联的线索
5. 反常或意外的记录（与日常模式不同的条目）

只输出信号列表，不要前言或总结。控制在 500 字以内。
PROMPT_EOF
)
                log "  Map Notes [批次$BATCH_NUM]: ${BATCH_COUNT}条, ${BATCH_SIZE}B → 提取信号..."
                signals=$(llm_call "$prompt" 1200 0.5 90 || true)

                if [ -n "${signals// }" ]; then
                    echo "$signals" > "$cache_file"
                    MAP_CONSECUTIVE_FAILS=0
                    # 节流：批次之间等待 5 秒
                    sleep 5
                else
                    MAP_CONSECUTIVE_FAILS=$((MAP_CONSECUTIVE_FAILS + 1))
                    log "  Map Notes [批次$BATCH_NUM]: LLM 返回空 (连续失败: $MAP_CONSECUTIVE_FAILS)"
                    if [ "$MAP_CONSECUTIVE_FAILS" -ge 3 ]; then
                        log "  ⚠️ 连续失败熔断，停止 Notes Map"
                        break
                    fi
                    sleep 10
                    BATCH=""
                    BATCH_COUNT=0
                    continue
                fi
            fi

            NOTES_SIGNALS+="
## 用户笔记（批次$BATCH_NUM, ${BATCH_COUNT}条）
$signals
"
            NOTES_MAP_COUNT=$((NOTES_MAP_COUNT + BATCH_COUNT))
            BATCH=""
            BATCH_COUNT=0
        fi
    done <<< "$SORTED_NOTES"

    # 处理最后不足一批的剩余
    if [ -n "${BATCH// }" ] && [ "$BATCH_COUNT" -gt 0 ]; then
        BATCH_NUM=$((BATCH_NUM + 1))
        BATCH_SIZE=${#BATCH}
        batch_hash=$(echo "$BATCH" | md5sum 2>/dev/null | cut -c1-12 || echo "b${BATCH_NUM}")
        cache_file="$MAP_DIR/${DAY}_notes_${batch_hash}.txt"

        if [ -f "$cache_file" ]; then
            log "  Map Notes [批次$BATCH_NUM]: 使用缓存 ($BATCH_COUNT 条)"
            signals=$(cat "$cache_file")
            MAP_CONSECUTIVE_FAILS=0  # 缓存命中视为成功（V37.2 fix）
        else
            prompt=$(python3 -c "
import sys
tpl = sys.stdin.read()
print(tpl.replace('TPL_COUNT', sys.argv[1]).replace('TPL_CONTENT', sys.argv[2]))
" "$BATCH_COUNT" "$BATCH" <<'PROMPT_EOF'
你是一个数据矿工。以下是用户的个人笔记和与AI助手的交互记录。这些笔记包含用户认为重要的信息、决策、发现和想法。

数据类型: 用户笔记/交互记录
笔记数量: TPL_COUNT 条

完整内容:
---
TPL_CONTENT
---

请提取 8-12 个最值得注意的信号，每个信号一行，格式：
- [日期或笔记名] 信号描述（具体事实，含关键内容）

重点关注：
1. 用户明确标记为重要的信息（主动保存 = 用户认为有价值）
2. 用户的决策、判断、观点（反映用户的思考方向）
3. 跨多条笔记出现的主题（重复出现 = 持续关注）
4. 与外部数据源（论文、新闻、技术趋势）可能关联的线索
5. 反常或意外的记录（与日常模式不同的条目）

只输出信号列表，不要前言或总结。控制在 500 字以内。
PROMPT_EOF
)
            log "  Map Notes [批次$BATCH_NUM]: ${BATCH_COUNT}条, ${BATCH_SIZE}B → 提取信号..."
            signals=$(llm_call "$prompt" 1200 0.5 90 || true)
            [ -n "${signals// }" ] && echo "$signals" > "$cache_file"
        fi

        if [ -n "${signals// }" ]; then
            NOTES_SIGNALS+="
## 用户笔记（批次$BATCH_NUM, ${BATCH_COUNT}条）
$signals
"
            NOTES_MAP_COUNT=$((NOTES_MAP_COUNT + BATCH_COUNT))
        fi
    fi

    log "Phase 1b 完成: $NOTES_MAP_COUNT/$NOTE_COUNT notes 提取了信号 ($BATCH_NUM 批)"

elif [ -n "$ALL_NOTES" ]; then
    # Fast 模式：直接采样 notes 原文
    SORTED_NOTES=$(echo "$ALL_NOTES" | while read f; do
        [ -f "$f" ] && echo "$(stat -f '%m' "$f" 2>/dev/null || stat -c '%Y' "$f" 2>/dev/null || echo 0) $f"
    done | sort -rn | awk '{print $2}')

    NOTES_MATERIAL=""
    NOTE_IDX=0
    while IFS= read -r note; do
        [ -z "$note" ] && continue
        [ -f "$note" ] || continue
        NOTE_IDX=$((NOTE_IDX + 1))
        [ "$NOTE_IDX" -gt 80 ] && break
        name=$(basename "$note" .md)
        content=$(cat "$note" 2>/dev/null | utf8_truncate 2000)
        [ -z "${content// }" ] && continue
        NOTES_MATERIAL+="
### $name
$content
"
    done <<< "$SORTED_NOTES"
fi

# 全局超时检查：Map 阶段如果用了太长时间，跳过 Reduce 直接用已有信号生成简报
if ! check_deadline; then
    dream_fail_alert "全局超时 — Map 阶段耗时过长，跳过 Reduce"
    # 写入已收集的 Map 信号作为简化报告
    {
        echo "# Agent Dream $DAY (超时简报)"
        echo ""
        echo "> Map 阶段超时，以下为已收集的原始信号（未经 Reduce 分析）"
        echo ""
        [ -n "${MAP_SIGNALS// }" ] && echo "$MAP_SIGNALS"
        [ -n "${NOTES_SIGNALS// }" ] && echo "$NOTES_SIGNALS"
    } > "$DREAM_FILE"
    printf '{"time":"%s","status":"timeout","map_count":%d,"sources":%d,"notes":%d}\n' \
        "$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')" "$MAP_COUNT" "$SRC_COUNT" "$NOTE_COUNT" > "$STATUS_FILE"
    log "超时退出，已保存简化报告到 $DREAM_FILE"
    exit 0
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
    # MapReduce 模式：Sources 信号 + Notes 信号并列
    REDUCE_INTRO="以下是系统知识库的 **全量深度分析结果**。Phase 1 已对 $MAP_COUNT 个数据源和 $NOTES_MAP_COUNT 条用户笔记逐一进行了信号提取（覆盖全部 ${TOTAL_KB_BYTES} 字节数据）。

**重要：用户笔记中的信号同样重要。** 这些笔记是用户主动保存的重要信息、决策和发现，反映了用户的关注方向和判断。分析时必须同时考虑外部数据源信号和用户笔记信号。"
    REDUCE_DATA="
# Phase 1a: 外部数据源信号
$MAP_SIGNALS

# Phase 1b: 用户笔记/交互记录信号（用户主动保存的重要信息）
$NOTES_SIGNALS
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

# 安全检查：prompt 超过 500KB 则截断（Qwen3 262K context 可处理 130KB prompt）
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
# V38: 增加重试次数 2→3，退避时间升级为指数退避
MIN_DREAM_CHARS=4000
MAX_RETRIES=3
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

    if [ "$retry" -lt "$MAX_RETRIES" ]; then
        local_wait=$((15 * retry))  # 15s, 30s
        log "Reduce: 等待 ${local_wait}s 后重试..."
        sleep "$local_wait"
    fi
done

if [ -z "${DREAM_RESULT// }" ]; then
    log "ERROR: Phase 2 所有重试均失败 (prompt was ${PROMPT_BYTES} bytes)"
    printf '{"time":"%s","status":"llm_failed","phase":"reduce","map_count":%d,"reduce_chars":%d,"prompt_bytes":%d}\n' \
        "$TS" "$MAP_COUNT" "$REDUCE_CHARS" "$PROMPT_BYTES" > "$STATUS_FILE"
    dream_fail_alert "Phase 2 Reduce LLM ${MAX_RETRIES}次全部失败 (prompt=${PROMPT_BYTES}B, map=$MAP_COUNT sources)"
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
# notify.sh 已在脚本开头加载（用于失败告警），这里直接使用

SENT=false
if $_DREAM_NOTIFY_LOADED; then
    log "推送开始: OPENCLAW_PHONE=${OPENCLAW_PHONE:-(unset)} DISCORD_CH_DAILY=${DISCORD_CH_DAILY:-(unset)}"

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

    if [ "$SEND_OK" -eq "$TOTAL_PARTS" ]; then
        log "梦境已推送 $SEND_OK/$TOTAL_PARTS 段到 WhatsApp + Discord"
        SENT=true
    elif [ "$SEND_OK" -gt 0 ]; then
        log "WARN: 梦境部分推送成功 $SEND_OK/$TOTAL_PARTS 段"
        SENT="\"partial:${SEND_OK}/${TOTAL_PARTS}\""
        dream_fail_alert "梦境推送不完整: $SEND_OK/$TOTAL_PARTS 段成功"
    else
        log "ERROR: 所有 $TOTAL_PARTS 段推送均失败"
        SENT=false
        dream_fail_alert "梦境已生成(${DREAM_CHARS}字)但推送全部失败(${TOTAL_PARTS}段) — 检查 OPENCLAW_PHONE/DISCORD_CH_DAILY 环境变量"
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

#!/usr/bin/env bash
# notify.sh — 统一消息推送（多通道 + 分频道 + 自动重试 + 失败队列）
# 用法：source ~/openclaw-model-bridge/notify.sh
#       notify "消息内容"                             # WhatsApp + Discord DM
#       notify "消息内容" --topic papers               # WhatsApp + Discord #论文
#       notify "消息内容" --topic alerts               # WhatsApp + Discord #告警
#       notify "消息内容" --channel discord --topic papers  # 只发 Discord #论文
#       notify "消息内容" --channel whatsapp            # 只发 WhatsApp
#       notify_queue_status                            # 查看失败队列
#       notify_queue_flush                             # 手动重放队列
#
# 自动重试机制：
#   - 每次发送失败自动重试 3 次（指数退避：2s/4s/8s）
#   - 3 次全部失败 → 消息写入 ~/.kb/notify_queue/ 队列
#   - 下次 notify() 调用时自动尝试重放队列
#   - notify_queue_flush 可手动触发重放
#
# Topic 频道映射（Discord Server 频道）：
#   papers  → #论文（arxiv/hf/s2/dblp/acl）
#   freight → #货代（freight_watcher）
#   alerts  → #告警（auto_deploy/watchdog/preflight）
#   daily    → #日报（kb_dream/kb_review/health_check）
#   ontology → #ontology（本体论讨论与推送）
#   tech    → #技术（hn/github_trending/rss_blogs/openclaw）
#   (空)    → DM（私信，默认）
#
# 环境变量：
#   OPENCLAW_PHONE      — WhatsApp 目标号码（默认 +85200000000）
#   DISCORD_TARGET      — Discord 目标用户ID（DM 用）
#   DISCORD_CH_PAPERS   — Discord #论文 频道ID
#   DISCORD_CH_FREIGHT  — Discord #货代 频道ID
#   DISCORD_CH_ALERTS   — Discord #告警 频道ID
#   DISCORD_CH_DAILY    — Discord #日报 频道ID
#   DISCORD_CH_TECH     — Discord #技术 频道ID
#   DISCORD_CH_ONTOLOGY — Discord #ontology 频道ID
#   NOTIFY_CHANNELS     — 启用的通道，逗号分隔（默认 "whatsapp,discord"）
#   NOTIFY_MAX_RETRIES  — 最大重试次数（默认 3）
#   NOTIFY_QUEUE_DIR    — 失败队列目录（默认 ~/.kb/notify_queue）
#   OPENCLAW            — openclaw 二进制路径

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
_NOTIFY_WA_TARGET="${OPENCLAW_PHONE:-+85200000000}"
_NOTIFY_DISCORD_TARGET="${DISCORD_TARGET:-}"
_NOTIFY_CHANNELS="${NOTIFY_CHANNELS:-whatsapp,discord}"
_NOTIFY_MAX_RETRIES="${NOTIFY_MAX_RETRIES:-3}"
_NOTIFY_QUEUE_DIR="${NOTIFY_QUEUE_DIR:-$HOME/.kb/notify_queue}"

# topic → Discord channel ID 映射
_notify_discord_target_for_topic() {
    case "$1" in
        papers)  echo "${DISCORD_CH_PAPERS:-}" ;;
        freight) echo "${DISCORD_CH_FREIGHT:-}" ;;
        alerts)  echo "${DISCORD_CH_ALERTS:-}" ;;
        daily)   echo "${DISCORD_CH_DAILY:-}" ;;
        tech)    echo "${DISCORD_CH_TECH:-}" ;;
        ontology) echo "${DISCORD_CH_ONTOLOGY:-}" ;;
        *)       echo "" ;;
    esac
}

# _notify_send_with_retry — 带指数退避重试的发送
# 用法：_notify_send_with_retry <channel> <target> <message>
# 返回：0=成功，1=全部重试失败
_notify_send_with_retry() {
    local channel="$1" target="$2" msg="$3"
    local attempt=0 delay=2
    local send_err="" last_err=""

    while [ "$attempt" -lt "$_NOTIFY_MAX_RETRIES" ]; do
        send_err=$("$OPENCLAW" message send --channel "$channel" --target "$target" --message "$msg" --json 2>&1 >/dev/null) && {
            [ "$attempt" -gt 0 ] && echo "[notify] OK: $channel 第$((attempt+1))次重试成功" >&2
            return 0
        }
        last_err="$send_err"
        attempt=$((attempt + 1))
        if [ "$attempt" -lt "$_NOTIFY_MAX_RETRIES" ]; then
            echo "[notify] WARN: $channel 发送失败(attempt $attempt): ${send_err:-(no stderr)}，${delay}s 后重试..." >&2
            sleep "$delay"
            delay=$((delay * 2))
        fi
    done
    echo "[notify] ERROR: $channel ${_NOTIFY_MAX_RETRIES}次均失败，最后错误: ${last_err:-(no stderr)}" >&2
    return 1
}

# _notify_queue_failed — 将失败消息写入队列
# 用法：_notify_queue_failed <channel> <target> <topic> <message>
_notify_queue_failed() {
    local channel="$1" target="$2" topic="$3" msg="$4"
    mkdir -p "$_NOTIFY_QUEUE_DIR"
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    local qfile="$_NOTIFY_QUEUE_DIR/${ts}_${channel}_$$.json"
    cat > "$qfile" <<QEOF
{"ts":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","channel":"$channel","target":"$target","topic":"$topic","msg":$(python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" <<< "$msg")}
QEOF
    echo "[notify] QUEUED: $channel 消息已入队 → $qfile" >&2
}

# _notify_drain_queue — 重放队列中的失败消息
# 在每次 notify() 开头调用，成功发送则删除队列文件
_notify_drain_queue() {
    [ -d "$_NOTIFY_QUEUE_DIR" ] || return 0
    local qfiles
    qfiles=$(find "$_NOTIFY_QUEUE_DIR" -name "*.json" -type f 2>/dev/null | sort)
    [ -z "$qfiles" ] && return 0

    local count
    count=$(echo "$qfiles" | wc -l | tr -d ' ')
    echo "[notify] 发现 $count 条排队消息，尝试重放..." >&2

    local f channel target msg
    while IFS= read -r f; do
        [ -f "$f" ] || continue
        channel=$(python3 -c "import json,sys; print(json.load(sys.stdin)['channel'])" < "$f" 2>/dev/null) || continue
        target=$(python3 -c "import json,sys; print(json.load(sys.stdin)['target'])" < "$f" 2>/dev/null) || continue
        msg=$(python3 -c "import json,sys; print(json.load(sys.stdin)['msg'])" < "$f" 2>/dev/null) || continue

        local replay_err=""
        replay_err=$("$OPENCLAW" message send --channel "$channel" --target "$target" --message "$msg" --json 2>&1 >/dev/null) && {
            echo "[notify] REPLAY OK: $(basename "$f")" >&2
            rm -f "$f"
            continue
        }
        echo "[notify] REPLAY FAIL: $(basename "$f") — ${replay_err:-(no stderr)}，保留队列" >&2
        break  # 如果还是失败，停止重放（避免雪崩）
    done <<< "$qfiles"
}

# notify "message" [--channel whatsapp|discord] [--topic papers|freight|alerts|daily|tech]
notify() {
    local msg="$1"
    shift
    local channels="$_NOTIFY_CHANNELS"
    local topic=""

    # 解析可选参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --channel) channels="$2"; shift 2 ;;
            --topic)   topic="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    [ -z "$msg" ] && return 1

    # V37.4.3: 告警消息隔离标记
    # --topic alerts 的消息在开头加 [SYSTEM_ALERT] 前缀，
    # tool_proxy.py 构建 LLM 请求时会 filter_system_alerts() 剥离这类消息，
    # 防止告警污染 PA 对话上下文 → Qwen3 幻觉替换用户问题（2026-04-11 13:06 案例）。
    # 标记对用户可见且自说明（用户看到 [SYSTEM_ALERT] 就知道是自动告警非 PA 对话）。
    if [ "$topic" = "alerts" ]; then
        case "$msg" in
            "[SYSTEM_ALERT]"*) ;;   # 已有标记（调用方自行加过），不重复
            *) msg="[SYSTEM_ALERT]
$msg" ;;
        esac
    fi

    # 先尝试重放队列中的失败消息
    _notify_drain_queue

    local rc=0
    local sent=0

    # WhatsApp（所有 topic 都发到同一个号码）
    if echo "$channels" | grep -q "whatsapp" && [ -n "$_NOTIFY_WA_TARGET" ]; then
        if _notify_send_with_retry whatsapp "$_NOTIFY_WA_TARGET" "$msg"; then
            sent=$((sent + 1))
        else
            echo "[notify] FAIL: WhatsApp 3次重试均失败，入队" >&2
            _notify_queue_failed whatsapp "$_NOTIFY_WA_TARGET" "$topic" "$msg"
            rc=1
        fi
    fi

    # Discord（根据 topic 选择频道，无 topic 则 DM）
    if echo "$channels" | grep -q "discord"; then
        local discord_target=""
        if [ -n "$topic" ]; then
            local ch_id
            ch_id=$(_notify_discord_target_for_topic "$topic")
            if [ -n "$ch_id" ]; then
                discord_target="$ch_id"
            else
                # V36.2: 空 channel ID 显式报错（不再静默跳过）
                local var_name="DISCORD_CH_$(echo "$topic" | tr '[:lower:]' '[:upper:]')"
                echo "[notify] ERROR: topic='$topic' 但 $var_name 为空 — Discord 推送被跳过！请设置环境变量" >&2
            fi
        fi
        # fallback 到 DM
        [ -z "$discord_target" ] && discord_target="user:$_NOTIFY_DISCORD_TARGET"

        if [ -n "$discord_target" ] && [ "$discord_target" != "user:" ]; then
            if _notify_send_with_retry discord "$discord_target" "$msg"; then
                sent=$((sent + 1))
            else
                echo "[notify] FAIL: Discord 3次重试均失败，入队" >&2
                _notify_queue_failed discord "$discord_target" "$topic" "$msg"
                rc=1
            fi
        fi
    fi

    [ "$sent" -eq 0 ] && return 1
    return $rc
}

# notify_file "filepath" [--channel whatsapp|discord] [--topic ...]
notify_file() {
    local file="$1"
    shift
    [ -f "$file" ] || return 1
    notify "$(cat "$file")" "$@"
}

# notify_queue_status — 显示队列状态
notify_queue_status() {
    if [ ! -d "$_NOTIFY_QUEUE_DIR" ]; then
        echo "队列为空（目录不存在）"
        return 0
    fi
    local count
    count=$(find "$_NOTIFY_QUEUE_DIR" -name "*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
    if [ "$count" -eq 0 ]; then
        echo "队列为空"
    else
        echo "队列中有 $count 条待发送消息："
        find "$_NOTIFY_QUEUE_DIR" -name "*.json" -type f -exec basename {} \; | sort
    fi
}

# notify_queue_flush — 强制重放全部队列
notify_queue_flush() {
    _notify_drain_queue
    notify_queue_status
}

#!/usr/bin/env bash
# notify.sh — 统一消息推送（多通道 + 分频道支持）
# 用法：source ~/openclaw-model-bridge/notify.sh
#       notify "消息内容"                             # WhatsApp + Discord DM
#       notify "消息内容" --topic papers               # WhatsApp + Discord #论文
#       notify "消息内容" --topic alerts               # WhatsApp + Discord #告警
#       notify "消息内容" --channel discord --topic papers  # 只发 Discord #论文
#       notify "消息内容" --channel whatsapp            # 只发 WhatsApp
#
# Topic 频道映射（Discord Server 频道）：
#   papers  → #论文（arxiv/hf/s2/dblp/acl）
#   freight → #货代（freight_watcher）
#   alerts  → #告警（auto_deploy/watchdog/preflight）
#   daily   → #日报（kb_dream/kb_review/health_check）
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
#   NOTIFY_CHANNELS     — 启用的通道，逗号分隔（默认 "whatsapp,discord"）
#   OPENCLAW            — openclaw 二进制路径

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
_NOTIFY_WA_TARGET="${OPENCLAW_PHONE:-+85200000000}"
_NOTIFY_DISCORD_TARGET="${DISCORD_TARGET:-}"
_NOTIFY_CHANNELS="${NOTIFY_CHANNELS:-whatsapp,discord}"

# topic → Discord channel ID 映射
_notify_discord_target_for_topic() {
    case "$1" in
        papers)  echo "${DISCORD_CH_PAPERS:-}" ;;
        freight) echo "${DISCORD_CH_FREIGHT:-}" ;;
        alerts)  echo "${DISCORD_CH_ALERTS:-}" ;;
        daily)   echo "${DISCORD_CH_DAILY:-}" ;;
        tech)    echo "${DISCORD_CH_TECH:-}" ;;
        *)       echo "" ;;
    esac
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

    local rc=0
    local sent=0

    # WhatsApp（所有 topic 都发到同一个号码）
    if echo "$channels" | grep -q "whatsapp" && [ -n "$_NOTIFY_WA_TARGET" ]; then
        if "$OPENCLAW" message send --channel whatsapp --target "$_NOTIFY_WA_TARGET" --message "$msg" --json >/dev/null 2>&1; then
            sent=$((sent + 1))
        else
            echo "[notify] WARN: WhatsApp 发送失败" >&2
            rc=1
        fi
    fi

    # Discord（根据 topic 选择频道，无 topic 则 DM）
    if echo "$channels" | grep -q "discord"; then
        local discord_target=""
        if [ -n "$topic" ]; then
            local ch_id
            ch_id=$(_notify_discord_target_for_topic "$topic")
            [ -n "$ch_id" ] && discord_target="$ch_id"
        fi
        # fallback 到 DM
        [ -z "$discord_target" ] && discord_target="user:$_NOTIFY_DISCORD_TARGET"

        if [ -n "$discord_target" ] && [ "$discord_target" != "user:" ]; then
            if "$OPENCLAW" message send --channel discord --target "$discord_target" --message "$msg" --json >/dev/null 2>&1; then
                sent=$((sent + 1))
            else
                echo "[notify] WARN: Discord 发送失败 (target=$discord_target)" >&2
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

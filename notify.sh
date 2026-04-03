#!/usr/bin/env bash
# notify.sh — 统一消息推送（多通道支持）
# 用法：source ~/openclaw-model-bridge/notify.sh
#       notify "消息内容"                    # 发送到所有启用通道
#       notify "消息内容" --channel discord   # 只发 Discord
#       notify "消息内容" --channel whatsapp  # 只发 WhatsApp
#
# 环境变量：
#   OPENCLAW_PHONE    — WhatsApp 目标号码（默认 +85200000000）
#   DISCORD_TARGET    — Discord 目标用户ID（数字字符串）
#   NOTIFY_CHANNELS   — 启用的通道，逗号分隔（默认 "whatsapp,discord"）
#   OPENCLAW          — openclaw 二进制路径
#
# 兼容性：所有现有脚本的 TO 变量保持不变，notify.sh 只是附加层。
# 迁移路径：旧脚本继续用 openclaw message send --target "$TO"，
#          新脚本/渐进迁移的脚本 source notify.sh && notify "$MSG"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
_NOTIFY_WA_TARGET="${OPENCLAW_PHONE:-+85200000000}"
_NOTIFY_DISCORD_TARGET="${DISCORD_TARGET:-}"
_NOTIFY_CHANNELS="${NOTIFY_CHANNELS:-whatsapp,discord}"

# notify "message" [--channel whatsapp|discord]
notify() {
    local msg="$1"
    shift
    local channels="$_NOTIFY_CHANNELS"

    # 解析可选参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --channel) channels="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    [ -z "$msg" ] && return 1

    local rc=0
    local sent=0

    # WhatsApp（多通道环境必须指定 --channel）
    if echo "$channels" | grep -q "whatsapp" && [ -n "$_NOTIFY_WA_TARGET" ]; then
        if "$OPENCLAW" message send --channel whatsapp --target "$_NOTIFY_WA_TARGET" --message "$msg" --json >/dev/null 2>&1; then
            sent=$((sent + 1))
        else
            echo "[notify] WARN: WhatsApp 发送失败" >&2
            rc=1
        fi
    fi

    # Discord（target 格式: user:<ID>）
    if echo "$channels" | grep -q "discord" && [ -n "$_NOTIFY_DISCORD_TARGET" ]; then
        if "$OPENCLAW" message send --channel discord --target "user:$_NOTIFY_DISCORD_TARGET" --message "$msg" --json >/dev/null 2>&1; then
            sent=$((sent + 1))
        else
            echo "[notify] WARN: Discord 发送失败" >&2
            rc=1
        fi
    fi

    [ "$sent" -eq 0 ] && return 1
    return $rc
}

# notify_file "filepath" [--channel whatsapp|discord]
# 从文件读取消息内容发送（避免命令行参数过长）
notify_file() {
    local file="$1"
    shift
    [ -f "$file" ] || return 1
    notify "$(cat "$file")" "$@"
}

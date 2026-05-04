#!/usr/bin/env bash
# install_openclaw_macmini.sh
# 全新 Mac Mini 一键部署 vanilla OpenClaw (latest) + Qwen3
#
# 用途: 在一台干净的 Mac Mini 上从零安装 OpenClaw 最新版,
#       并接入指定的 Qwen3 OpenAI-compatible endpoint, 准备 WhatsApp 接入。
# 不含: 本仓库的 adapter/proxy/KB/cron/Dream 等定制层 — 完全 vanilla 安装。
#
# === 用法 ===
# 1. 把本脚本复制到目标 Mac Mini (scp / cp / 直接粘贴):
#       scp deploy/install_openclaw_macmini.sh user@new-mac:~/install_openclaw.sh
# 2. 在 Mac Mini 上以普通用户身份运行 (禁止 root):
#       bash ~/install_openclaw.sh
# 3. 按提示输入 WhatsApp 手机号 (E.164, e.g. +85212345678).
# 4. 安装结束后按"下一步"提示完成 WhatsApp 登录.
#
# === 可覆盖的环境变量 (在 bash 命令前加) ===
#   QWEN_API_KEY="sk-..."             默认嵌入下方 (用户已豁免明文)
#   QWEN_BASE_URL="https://..."       默认 https://hkagentx.hkopenlab.com/v1
#   QWEN_MODEL="Qwen3-..."            主模型
#   QWEN_VL_MODEL="Qwen2.5-VL-..."    多模态 (图片) 模型
#   PHONE_NUMBER="+85212345678"       手机号 (设了就跳过交互输入)
#   OPENCLAW_VERSION="latest"         npm 标签 / 具体版本号
#   SKIP_CONFIG_WRITE=1               跳过 openclaw.json 自动写入 (手动配置场景)
#   DRY_RUN=1                         仅打印将要执行的步骤, 不真改系统
#
# === 安全 ===
# 用户已豁免明文 API Key 限制。脚本会:
#   - 把 API Key 写入 ~/.zshrc / ~/.bash_profile (chmod 600)
#   - 把 API Key 写入 ~/.openclaw/openclaw.json (chmod 600)
#   - 把 API Key 写入 ~/Library/LaunchAgents/ai.openclaw.gateway.plist (chmod 600)
# 如后续需要更强安全 (Keychain), 见脚本末尾注释。

set -euo pipefail

# =============================================================================
# Configuration block — edit before running if needed
# =============================================================================
QWEN_API_KEY="${QWEN_API_KEY:-sk-REDACTED-OLD-LEAKED-KEY}"
QWEN_BASE_URL="${QWEN_BASE_URL:-https://hkagentx.hkopenlab.com/v1}"
QWEN_MODEL="${QWEN_MODEL:-Qwen3-235B-A22B-Instruct-2507-W8A8}"
QWEN_VL_MODEL="${QWEN_VL_MODEL:-Qwen2.5-VL-72B-Instruct}"
PHONE_NUMBER="${PHONE_NUMBER:-+85200000000}"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-latest}"
SKIP_CONFIG_WRITE="${SKIP_CONFIG_WRITE:-0}"
DRY_RUN="${DRY_RUN:-0}"

OPENCLAW_HOME="$HOME/.openclaw"
OPENCLAW_CONFIG="$OPENCLAW_HOME/openclaw.json"
GATEWAY_LABEL="ai.openclaw.gateway"
GATEWAY_PLIST="$HOME/Library/LaunchAgents/$GATEWAY_LABEL.plist"
GATEWAY_PORT=18789

# =============================================================================
# Helpers
# =============================================================================
log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
ok()   { printf '\033[1;32m✅\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m⚠️ \033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m❌\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ "$DRY_RUN" == "1" ]]; then echo "[dry-run] $*"; else eval "$@"; fi; }

trap 'echo; fail "脚本中断 (line $LINENO)"' ERR INT TERM

banner() {
  cat <<'EOF'

  ╔═══════════════════════════════════════════════════════╗
  ║   OpenClaw + Qwen3 一键部署 (vanilla, 全新 Mac Mini)   ║
  ║   target: macOS 14+, Node ≥18, npm latest             ║
  ╚═══════════════════════════════════════════════════════╝

EOF
}

# =============================================================================
# Phase 1: 前置检查
# =============================================================================
phase_1_preflight() {
  log "==== Phase 1/6: 前置检查 ===="

  [[ "$(uname)" == "Darwin" ]] || fail "本脚本仅支持 macOS (检测到 $(uname))"
  [[ "$EUID" -ne 0 ]] || fail "禁止以 root 运行 — OpenClaw 必须以普通用户身份执行"

  # macOS version
  local osver
  osver=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
  ok "macOS: $osver  user: $(whoami)  home: $HOME"

  # Homebrew
  if ! command -v brew >/dev/null 2>&1; then
    warn "Homebrew 未安装, 现在安装 (大约 3-5 分钟)..."
    if [[ "$DRY_RUN" != "1" ]]; then
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    # Apple Silicon 默认装到 /opt/homebrew
    if [[ -x /opt/homebrew/bin/brew ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
  fi
  ok "Homebrew: $(brew --version 2>/dev/null | head -1 || echo 'unknown')"

  # Node.js (require ≥18)
  if ! command -v node >/dev/null 2>&1; then
    log "Node.js 未安装, 通过 brew 安装..."
    run "brew install node"
  fi
  local node_major
  node_major=$(node --version 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')
  if [[ -z "$node_major" ]] || [[ "$node_major" -lt 18 ]]; then
    warn "Node.js 版本太旧 ($(node --version 2>/dev/null)), 升级到 LTS..."
    run "brew upgrade node || brew install node"
  fi
  ok "Node.js: $(node --version)  npm: $(npm --version)"

  # Python3 (用于辅助验证)
  command -v python3 >/dev/null 2>&1 || run "brew install python3"
  ok "Python3: $(python3 --version 2>&1 | head -1)"

  # 手机号 (E.164 格式)
  if [[ -z "$PHONE_NUMBER" ]]; then
    echo
    warn "需要 WhatsApp 手机号 (E.164 格式, 必须含国家代码, 如 +85212345678)"
    read -r -p "请输入手机号: " PHONE_NUMBER
  fi
  [[ "$PHONE_NUMBER" =~ ^\+[1-9][0-9]{7,14}$ ]] \
    || fail "手机号格式无效: '$PHONE_NUMBER' — 必须以 + 开头, 8-15 位数字"
  ok "手机号: $PHONE_NUMBER"
}

# =============================================================================
# Phase 2: 安装 OpenClaw (npm global)
# =============================================================================
phase_2_install_openclaw() {
  log "==== Phase 2/6: 安装 OpenClaw '$OPENCLAW_VERSION' ===="

  if command -v openclaw >/dev/null 2>&1; then
    local cur_ver
    cur_ver=$(openclaw --version 2>/dev/null | head -1 || echo unknown)
    warn "已存在 OpenClaw ($cur_ver), 将通过 npm 安装/升级到 $OPENCLAW_VERSION"
  fi

  run "npm install -g 'openclaw@$OPENCLAW_VERSION'"

  if [[ "$DRY_RUN" == "1" ]]; then
    warn "[dry-run] 跳过 openclaw 命令存在性检查"
    return 0
  fi

  if ! command -v openclaw >/dev/null 2>&1; then
    fail "openclaw 命令未找到 — 检查 npm global bin path: $(npm config get prefix)"
  fi
  ok "OpenClaw 已安装: $(openclaw --version 2>/dev/null | head -1 || echo unknown)"
  ok "可执行路径: $(which openclaw)"
}

# =============================================================================
# Phase 3: 配置环境变量 (shell rc)
# =============================================================================
phase_3_env_vars() {
  log "==== Phase 3/6: 配置环境变量 ===="

  mkdir -p "$OPENCLAW_HOME"
  chmod 700 "$OPENCLAW_HOME"

  local marker="# OpenClaw + Qwen3 (added by install_openclaw_macmini.sh)"
  for SHELL_RC in "$HOME/.zshrc" "$HOME/.bash_profile"; do
    [[ -f "$SHELL_RC" ]] || touch "$SHELL_RC"
    if grep -q "QWEN_API_KEY" "$SHELL_RC" 2>/dev/null; then
      warn "$(basename "$SHELL_RC") 已含 QWEN_API_KEY, 跳过 (避免重复)"
      continue
    fi
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "[dry-run] would append QWEN_API_KEY/OPENCLAW_PHONE/REMOTE_API_KEY to $SHELL_RC"
      continue
    fi
    cat >> "$SHELL_RC" <<EOF

$marker
export QWEN_API_KEY='$QWEN_API_KEY'
export QWEN_BASE_URL='$QWEN_BASE_URL'
export OPENCLAW_PHONE='$PHONE_NUMBER'
# 兼容性别名 (部分脚本/调用方读 REMOTE_API_KEY)
export REMOTE_API_KEY="\${REMOTE_API_KEY:-\$QWEN_API_KEY}"
EOF
    chmod 600 "$SHELL_RC"
    ok "已写入 $SHELL_RC (chmod 600)"
  done

  # Export to current shell so后续 phase 可以用
  export QWEN_API_KEY QWEN_BASE_URL OPENCLAW_PHONE="$PHONE_NUMBER"
  export REMOTE_API_KEY="${REMOTE_API_KEY:-$QWEN_API_KEY}"
}

# =============================================================================
# Phase 4: 生成 openclaw.json
# =============================================================================
phase_4_config() {
  log "==== Phase 4/6: 生成 openclaw.json ===="

  if [[ "$SKIP_CONFIG_WRITE" == "1" ]]; then
    warn "SKIP_CONFIG_WRITE=1 — 跳过 openclaw.json 自动写入"
    warn "请手动配置: openclaw config --help (或参考 https://docs.openclaw.ai)"
    return 0
  fi

  if [[ -f "$OPENCLAW_CONFIG" ]]; then
    local bak="$OPENCLAW_CONFIG.backup.$(date +%Y%m%d_%H%M%S)"
    run "cp '$OPENCLAW_CONFIG' '$bak'"
    warn "已备份原 config 到 $bak"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] would write $OPENCLAW_CONFIG"
    return 0
  fi

  cat > "$OPENCLAW_CONFIG" <<EOF
{
  "_comment": "Generated by install_openclaw_macmini.sh on $(date -u +%FT%TZ). Schema based on OpenClaw v2026.3.x — adjust if 'openclaw start' reports schema errors in newer versions.",
  "version": "1.0",
  "phone": "$PHONE_NUMBER",
  "providers": {
    "qwen": {
      "type": "openai-compatible",
      "base_url": "$QWEN_BASE_URL",
      "api_key_env": "QWEN_API_KEY",
      "models": [
        { "id": "$QWEN_MODEL",    "alias": "primary" },
        { "id": "$QWEN_VL_MODEL", "alias": "vision"  }
      ]
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "qwen/$QWEN_MODEL",
        "vision":  "qwen/$QWEN_VL_MODEL"
      },
      "thinking": "medium",
      "max_tokens": 4096,
      "max_tool_calls_per_task": 2,
      "temperature": 0.7
    }
  },
  "channels": {
    "whatsapp": {
      "enabled": true
    }
  }
}
EOF
  chmod 600 "$OPENCLAW_CONFIG"
  ok "已写入 $OPENCLAW_CONFIG (chmod 600)"

  # 验证 JSON 格式
  if ! python3 -c "import json; json.load(open('$OPENCLAW_CONFIG'))" 2>/dev/null; then
    fail "$OPENCLAW_CONFIG 不是合法 JSON, 请人工检查"
  fi
  ok "openclaw.json 格式合法"

  warn "⚠️  Schema 兼容性提示:"
  warn "    上述字段基于 OpenClaw v2026.3.x 已验证模式; 最新版字段可能略有不同。"
  warn "    若 'openclaw start' 报 schema 错误, 检查方法:"
  warn "      openclaw config --help            # 查看正确字段"
  warn "      openclaw config validate          # 校验当前 config"
  warn "      openclaw provider list / add      # 通过 CLI 配置 provider"
}

# =============================================================================
# Phase 5: 验证 Qwen3 endpoint 可达性 + Key 有效性
# =============================================================================
phase_5_verify_qwen() {
  log "==== Phase 5/6: 验证 Qwen3 endpoint ===="

  if [[ "$DRY_RUN" == "1" ]]; then
    warn "[dry-run] 跳过 endpoint 验证 (curl $QWEN_BASE_URL/models)"
    return 0
  fi

  local tmp http_code
  tmp=$(mktemp /tmp/qwen_test.XXXXXX) || fail "无法创建临时文件"
  http_code=$(curl -s -o "$tmp" -w "%{http_code}" --max-time 10 \
    -H "Authorization: Bearer $QWEN_API_KEY" \
    -H "Accept: application/json" \
    "$QWEN_BASE_URL/models" 2>/dev/null || echo "000")

  case "$http_code" in
    200)
      ok "Qwen3 endpoint 可达 + API Key 有效 (HTTP 200)"
      if grep -q "$QWEN_MODEL" "$tmp" 2>/dev/null; then
        ok "目标模型 '$QWEN_MODEL' 在服务端 /models 列表中"
      else
        warn "目标模型 '$QWEN_MODEL' 未在 /models 响应中找到"
        warn "实际可用模型 (前 10 个):"
        python3 -c "
import json, sys
try:
    data = json.load(open('$tmp'))
    models = [m.get('id','?') for m in data.get('data',[])][:10]
    for m in models: print(f'    - {m}')
except Exception as e:
    print(f'    (无法解析响应: {e})')
" 2>/dev/null || cat "$tmp" | head -20
      fi
      ;;
    401)
      rm -f "$tmp"
      fail "API Key 无效 (HTTP 401) — 请检查 QWEN_API_KEY 值"
      ;;
    403)
      rm -f "$tmp"
      fail "权限拒绝 (HTTP 403) — Key 可能被禁用或目标 endpoint 限制 IP"
      ;;
    404)
      warn "endpoint /models 路径返回 404 — base_url 可能拼写错误或缺 /v1"
      warn "当前: $QWEN_BASE_URL/models"
      ;;
    000)
      warn "网络不通或 endpoint 无响应 — 检查 QWEN_BASE_URL=$QWEN_BASE_URL"
      ;;
    *)
      warn "Qwen3 endpoint 返回 HTTP $http_code"
      warn "响应内容 (前 500 字节):"
      head -c 500 "$tmp"
      echo
      ;;
  esac
  rm -f "$tmp"
}

# =============================================================================
# Phase 6: launchd 自启 (开机/崩溃自动重启)
# =============================================================================
phase_6_launchd() {
  log "==== Phase 6/6: 配置 launchd 自启 ===="

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] would write $GATEWAY_PLIST with Label=$GATEWAY_LABEL"
    echo "[dry-run] would launchctl bootstrap gui/\$(id -u) $GATEWAY_PLIST"
    echo "[dry-run] would wait for Gateway on :$GATEWAY_PORT"
    return 0
  fi

  mkdir -p "$(dirname "$GATEWAY_PLIST")"

  local node_path openclaw_path
  node_path=$(command -v node || echo /opt/homebrew/bin/node)
  openclaw_path=$(command -v openclaw || echo "")
  [[ -n "$openclaw_path" ]] || fail "找不到 openclaw 命令路径 — Phase 2 是否成功?"

  cat > "$GATEWAY_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$GATEWAY_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$node_path</string>
        <string>$openclaw_path</string>
        <string>start</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
        <key>QWEN_API_KEY</key>
        <string>$QWEN_API_KEY</string>
        <key>QWEN_BASE_URL</key>
        <string>$QWEN_BASE_URL</string>
        <key>REMOTE_API_KEY</key>
        <string>$QWEN_API_KEY</string>
        <key>OPENCLAW_PHONE</key>
        <string>$PHONE_NUMBER</string>
    </dict>

    <key>StandardOutPath</key>
    <string>$HOME/openclaw_gateway.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/openclaw_gateway.err.log</string>

    <key>WorkingDirectory</key>
    <string>$HOME</string>

    <key>ProcessType</key>
    <string>Background</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF
  chmod 600 "$GATEWAY_PLIST"
  ok "已写入 $GATEWAY_PLIST"

  # 卸载旧的 (如有), 然后加载新的
  local uid_num
  uid_num=$(id -u)
  launchctl bootout "gui/$uid_num/$GATEWAY_LABEL" 2>/dev/null || true
  sleep 1

  if launchctl bootstrap "gui/$uid_num" "$GATEWAY_PLIST" 2>&1 | tee /tmp/launchctl_out; then
    ok "launchd bootstrap 成功"
  else
    warn "launchctl bootstrap 报错, 尝试 fallback: launchctl load"
    launchctl load -w "$GATEWAY_PLIST" 2>&1 || warn "load 也失败 — 可手动调试"
  fi
  rm -f /tmp/launchctl_out

  # 等待 Gateway 启动 (5 次 × 2 秒)
  log "等待 Gateway 启动..."
  local started=0
  for attempt in 1 2 3 4 5; do
    sleep 2
    if curl -sS -o /dev/null -w "%{http_code}" --max-time 2 \
       "http://localhost:$GATEWAY_PORT/" 2>/dev/null | grep -qE "^[2-4][0-9][0-9]$"; then
      started=1
      ok "Gateway 监听 :$GATEWAY_PORT (attempt $attempt)"
      break
    fi
  done
  if [[ "$started" == "0" ]]; then
    warn "10 秒内未检测到 Gateway 监听 :$GATEWAY_PORT"
    warn "查看日志: tail -100 $HOME/openclaw_gateway.err.log"
  fi
}

# =============================================================================
# 完成 + 下一步指引
# =============================================================================
print_next_steps() {
  local uid_num
  uid_num=$(id -u)

  echo
  echo "════════════════════════════════════════════════════════"
  ok "🎉 全新 OpenClaw + Qwen3 部署完成"
  echo "════════════════════════════════════════════════════════"
  echo
  log "下一步 (按顺序执行):"
  echo
  echo "1️⃣  WhatsApp 登录 (扫描 QR 码绑定手机):"
  echo "       openclaw login"
  echo
  echo "2️⃣  发条测试消息给自己 (绑定后):"
  echo "       openclaw message send --to '$PHONE_NUMBER' '👋 OpenClaw 上线测试'"
  echo
  echo "3️⃣  在 WhatsApp 里给自己发 'hello', 等 AI 用 Qwen3 回复"
  echo
  log "运维命令:"
  echo "  • 查看 Gateway 日志:  tail -f $HOME/openclaw_gateway.log"
  echo "  • 查看错误日志:      tail -f $HOME/openclaw_gateway.err.log"
  echo "  • 重启 Gateway:      launchctl kickstart -k gui/$uid_num/$GATEWAY_LABEL"
  echo "  • 停止 Gateway:      launchctl bootout gui/$uid_num/$GATEWAY_LABEL"
  echo "  • 查看 Gateway 状态: launchctl list | grep openclaw"
  echo "  • 验证 Qwen API:     curl -H 'Authorization: Bearer \$QWEN_API_KEY' \\"
  echo "                            $QWEN_BASE_URL/models | head -50"
  echo
  log "卸载 (如需完全清理):"
  echo "       launchctl bootout gui/$uid_num/$GATEWAY_LABEL"
  echo "       rm -f $GATEWAY_PLIST"
  echo "       rm -rf $OPENCLAW_HOME"
  echo "       npm uninstall -g openclaw"
  echo "       # 然后手动从 ~/.zshrc / ~/.bash_profile 删除 QWEN_API_KEY 那段"
  echo
  warn "🔐 安全提醒: API Key 已写入以下位置 (chmod 600):"
  echo "       - ~/.zshrc / ~/.bash_profile  (env vars)"
  echo "       - $OPENCLAW_CONFIG"
  echo "       - $GATEWAY_PLIST"
  echo
  echo "如需更强安全 (Keychain 替代明文), 可执行:"
  echo "       security add-generic-password -a \"\$USER\" -s 'qwen-api-key' -w '\$QWEN_API_KEY'"
  echo "       # 然后改 plist 用 launchctl setenv 或 wrapper 脚本读 Keychain 注入"
  echo
}

# =============================================================================
# Main
# =============================================================================
main() {
  banner
  log "DRY_RUN=$DRY_RUN  OPENCLAW_VERSION=$OPENCLAW_VERSION  SKIP_CONFIG_WRITE=$SKIP_CONFIG_WRITE"
  echo

  phase_1_preflight
  echo
  phase_2_install_openclaw
  echo
  phase_3_env_vars
  echo
  phase_4_config
  echo
  phase_5_verify_qwen
  echo
  phase_6_launchd
  echo
  print_next_steps
}

main "$@"

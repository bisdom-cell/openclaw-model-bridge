#!/bin/bash
# auto_deploy.sh — 自动部署脚本（V28 重写）
# 轮询 GitHub，有新 commit 就 git pull + 同步文件 + 按需 restart
# 使用方式：加入系统 crontab，每 2 分钟执行一次
# crontab: */2 * * * * bash ~/openclaw-model-bridge/auto_deploy.sh

set -euo pipefail

REPO_DIR="$HOME/openclaw-model-bridge"
LOG="$HOME/.openclaw/logs/auto_deploy.log"
LOCK="/tmp/auto_deploy.lock"

mkdir -p "$(dirname "$LOG")"

# 防止并发执行
if [ -f "$LOCK" ]; then
    LOCK_PID=$(cat "$LOCK" 2>/dev/null || true)
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        exit 0
    fi
    rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

cd "$REPO_DIR" || { echo "$(date) ERROR: cannot cd to $REPO_DIR" >> "$LOG"; exit 1; }

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null)
if [ -z "$BRANCH" ]; then
    echo "$(date) ERROR: cannot determine branch" >> "$LOG"
    exit 1
fi

# ── 1. 拉取远端最新状态 ──────────────────────────────────────────────
git fetch origin "$BRANCH" --quiet 2>&1 || {
    echo "$(date) WARN: git fetch failed" >> "$LOG"
    exit 1
}

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0  # 无更新，静默退出
fi

echo "$(date) 检测到新 commit: ${LOCAL:0:8} -> ${REMOTE:0:8}" >> "$LOG"

# 检查是否有未提交的本地改动
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "$(date) WARN: 本地有未提交改动，stash 后再 pull" >> "$LOG"
    git stash push -m "auto_deploy_$(date +%s)" >> "$LOG" 2>&1
fi

git pull origin "$BRANCH" --ff-only >> "$LOG" 2>&1 || {
    echo "$(date) ERROR: git pull 失败（可能有冲突）" >> "$LOG"
    exit 1
}

# 获取变更的文件列表
CHANGED_FILES=$(git diff --name-only "$LOCAL" "$REMOTE")
echo "$(date) 变更文件: $(echo "$CHANGED_FILES" | tr '\n' ' ')" >> "$LOG"

# ── 2. 运行测试（仅当 proxy_filters.py 变更时）─────────────────────
if echo "$CHANGED_FILES" | grep -q "proxy_filters.py\|test_tool_proxy.py"; then
    echo "$(date) 运行单测..." >> "$LOG"
    if ! python3 "$REPO_DIR/test_tool_proxy.py" >> "$LOG" 2>&1; then
        echo "$(date) ❌ 测试失败，跳过部署！请检查。" >> "$LOG"
        exit 1
    fi
    echo "$(date) ✅ 测试通过" >> "$LOG"
fi

# ── 3. 文件同步映射表 ────────────────────────────────────────────────
# 格式：仓库相对路径 → 运行绝对路径
declare -a FILE_MAP=(
    # 核心服务（Proxy + Adapter）
    "proxy_filters.py|$HOME/proxy_filters.py"
    "tool_proxy.py|$HOME/tool_proxy.py"
    "adapter.py|$HOME/adapter.py"

    # 运维脚本
    "restart.sh|$HOME/restart.sh"
    "health_check.sh|$HOME/health_check.sh"
    "kb_write.sh|$HOME/kb_write.sh"
    "kb_review.sh|$HOME/kb_review.sh"
    "kb_evening.sh|$HOME/kb_evening.sh"
    "kb_save_arxiv.sh|$HOME/kb_save_arxiv.sh"

    # 独立 Watcher 脚本
    "run_hn_fixed.sh|$HOME/.openclaw/jobs/hn_watcher/run_hn_fixed.sh"
    "run_discussions.sh|$HOME/run_discussions.sh"

    # OpenClaw 官方 Watcher
    "jobs/openclaw_official/run.sh|$HOME/.openclaw/jobs/openclaw_official/run.sh"
    "jobs/openclaw_official/run_blog.sh|$HOME/.openclaw/jobs/openclaw_official/run_blog.sh"
    "jobs/openclaw_official/run_discussions.sh|$HOME/.openclaw/jobs/openclaw_official/run_discussions.sh"

    # 货代 Watcher
    "jobs/freight_watcher/run_freight.sh|$HOME/.openclaw/jobs/freight_watcher/run_freight.sh"
)

SYNCED=0
NEED_RESTART=false

for mapping in "${FILE_MAP[@]}"; do
    SRC="${mapping%%|*}"
    DST="${mapping##*|}"

    # 只同步本次变更的文件
    if echo "$CHANGED_FILES" | grep -q "^${SRC}$"; then
        DST_DIR="$(dirname "$DST")"
        mkdir -p "$DST_DIR"
        cp "$REPO_DIR/$SRC" "$DST"
        echo "$(date)   同步: $SRC -> $DST" >> "$LOG"
        SYNCED=$((SYNCED + 1))

        # 核心服务文件变更 → 需要 restart
        case "$SRC" in
            proxy_filters.py|tool_proxy.py|adapter.py)
                NEED_RESTART=true
                ;;
        esac
    fi
done

echo "$(date) 同步完成: ${SYNCED} 个文件" >> "$LOG"

# ── 4. 按需重启服务 ──────────────────────────────────────────────────
if $NEED_RESTART; then
    echo "$(date) 核心服务文件变更，执行 restart..." >> "$LOG"
    bash "$HOME/restart.sh" >> "$LOG" 2>&1 || {
        echo "$(date) ❌ restart.sh 失败" >> "$LOG"
        exit 1
    }
    echo "$(date) ✅ 服务重启完成" >> "$LOG"
fi

NEW_COMMIT=$(git rev-parse --short HEAD)
echo "$(date) ✅ 部署完成 (branch: $BRANCH, commit: $NEW_COMMIT, synced: $SYNCED files, restart: $NEED_RESTART)" >> "$LOG"

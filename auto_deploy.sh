#!/bin/bash
# auto_deploy.sh — 自动部署脚本（V28 重写）
# 轮询 GitHub，有新 commit 就 git pull + 同步文件 + 按需 restart
# 使用方式：加入系统 crontab，每 2 分钟执行一次
# crontab: */2 * * * * bash ~/openclaw-model-bridge/auto_deploy.sh

set -euo pipefail

# crontab 环境 PATH 不含 homebrew，手动补充
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

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

HAS_NEW_COMMITS=true
if [ "$LOCAL" = "$REMOTE" ]; then
    HAS_NEW_COMMITS=false
fi

CHANGED_FILES=""

if $HAS_NEW_COMMITS; then
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
    "job_watchdog.sh|$HOME/job_watchdog.sh"
    "wa_keepalive.sh|$HOME/wa_keepalive.sh"

    # 独立 Watcher 脚本
    "run_hn_fixed.sh|$HOME/.openclaw/jobs/hn_watcher/run_hn_fixed.sh"

    # OpenClaw 官方 Watcher
    "jobs/openclaw_official/run.sh|$HOME/.openclaw/jobs/openclaw_official/run.sh"
    "jobs/openclaw_official/run_discussions.sh|$HOME/.openclaw/jobs/openclaw_official/run_discussions.sh"

    # 货代 Watcher
    "jobs/freight_watcher/run_freight.sh|$HOME/.openclaw/jobs/freight_watcher/run_freight.sh"
    "jobs/freight_watcher/importyeti_scraper.py|$HOME/.openclaw/jobs/freight_watcher/importyeti_scraper.py"

    # ArXiv 监控
    "jobs/arxiv_monitor/run_arxiv.sh|$HOME/.openclaw/jobs/arxiv_monitor/run_arxiv.sh"

    # 自部署（bootstrapping）
    "auto_deploy.sh|$HOME/openclaw-model-bridge/auto_deploy.sh"
)

SYNCED=0
NEED_RESTART=false

if $HAS_NEW_COMMITS; then
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
fi

# ── 3b. 漂移检测（每小时整点执行一次全量比对）────────────────────────
# 解决盲区：新加入 FILE_MAP 的文件、初始手动部署的旧版不会被增量同步覆盖
MINUTE=$(date +%M)
if [ "$MINUTE" -lt 2 ]; then
    DRIFT=0
    for mapping in "${FILE_MAP[@]}"; do
        SRC="${mapping%%|*}"
        DST="${mapping##*|}"

        [ ! -f "$REPO_DIR/$SRC" ] && continue
        [ ! -f "$DST" ] && {
            # 目标不存在，直接部署
            mkdir -p "$(dirname "$DST")"
            cp "$REPO_DIR/$SRC" "$DST"
            echo "$(date)   漂移修复(缺失): $SRC -> $DST" >> "$LOG"
            DRIFT=$((DRIFT + 1))
            continue
        }

        # 比对 md5 哈希
        HASH_SRC=$(md5 -q "$REPO_DIR/$SRC" 2>/dev/null || md5sum "$REPO_DIR/$SRC" | cut -d' ' -f1)
        HASH_DST=$(md5 -q "$DST" 2>/dev/null || md5sum "$DST" | cut -d' ' -f1)

        if [ "$HASH_SRC" != "$HASH_DST" ]; then
            cp "$REPO_DIR/$SRC" "$DST"
            echo "$(date)   漂移修复(不一致): $SRC -> $DST" >> "$LOG"
            DRIFT=$((DRIFT + 1))

            case "$SRC" in
                proxy_filters.py|tool_proxy.py|adapter.py)
                    NEED_RESTART=true
                    ;;
            esac
        fi
    done

    if [ "$DRIFT" -gt 0 ]; then
        echo "$(date) ⚠️ 漂移检测: 修复 ${DRIFT} 个文件" >> "$LOG"
        # 漂移发现时推送 WhatsApp 告警
        DRIFT_MSG="⚠️ 漂移检测: 修复 ${DRIFT} 个部署文件不一致，已自动覆盖。详见 auto_deploy.log"
        openclaw message send --target "${OPENCLAW_PHONE:-+85200000000}" --message "$DRIFT_MSG" --json >/dev/null 2>&1 || true
    fi

    # ── 3c. Crontab 引号完整性检查（每小时整点，与漂移检测同步）──────────
    CRONTAB_ISSUES=""
    while IFS= read -r cline; do
        [ -z "$cline" ] && continue
        echo "$cline" | grep -q '^#' && continue
        if echo "$cline" | grep -q "bash -lc"; then
            QUOTE_COUNT=$(echo "$cline" | tr -cd "'" | wc -c | tr -d ' ')
            if [ $((QUOTE_COUNT % 2)) -ne 0 ]; then
                CRONTAB_ISSUES="${CRONTAB_ISSUES}• 引号未闭合: ${cline:0:60}...\n"
            fi
        fi
    done < <(crontab -l 2>/dev/null)

    if [ -n "$CRONTAB_ISSUES" ]; then
        echo "$(date) 🔴 crontab 语法异常:" >> "$LOG"
        echo -e "$CRONTAB_ISSUES" >> "$LOG"
        CRON_MSG="🔴 Crontab 语法异常（自动检测）：
${CRONTAB_ISSUES}
请立即检查: crontab -l"
        openclaw message send --target "${OPENCLAW_PHONE:-+85200000000}" --message "$CRON_MSG" --json >/dev/null 2>&1 || true
    fi
fi

# ── 4. 按需重启服务 ──────────────────────────────────────────────────
if $NEED_RESTART; then
    echo "$(date) 核心服务文件变更，执行 restart..." >> "$LOG"
    bash "$HOME/restart.sh" >> "$LOG" 2>&1 || {
        echo "$(date) ❌ restart.sh 失败" >> "$LOG"
        exit 1
    }
    echo "$(date) ✅ 服务重启完成" >> "$LOG"
fi

if $HAS_NEW_COMMITS; then
    NEW_COMMIT=$(git rev-parse --short HEAD)
    echo "$(date) ✅ 部署完成 (branch: $BRANCH, commit: $NEW_COMMIT, synced: $SYNCED files, restart: $NEED_RESTART)" >> "$LOG"

    # ── 5. 部署后自动体检 ──────────────────────────────────────────────
    PREFLIGHT="$REPO_DIR/preflight_check.sh"
    if [ -f "$PREFLIGHT" ]; then
        echo "$(date) 运行 preflight_check..." >> "$LOG"
        PREFLIGHT_OUT=$(bash "$PREFLIGHT" --full 2>&1) && PREFLIGHT_RC=0 || PREFLIGHT_RC=$?
        if [ $PREFLIGHT_RC -ne 0 ]; then
            # 提取失败项（只保留 ❌ 行）
            FAIL_LINES=$(echo "$PREFLIGHT_OUT" | grep "❌" | head -10)
            ALERT_MSG="🔴 部署后体检失败 (commit: $NEW_COMMIT)

$FAIL_LINES

详情：auto_deploy.log"
            echo "$(date) ❌ preflight_check 失败:" >> "$LOG"
            echo "$PREFLIGHT_OUT" >> "$LOG"
            openclaw message send --target "${OPENCLAW_PHONE:-+85200000000}" --message "$ALERT_MSG" --json >/dev/null 2>&1 || true
        else
            echo "$(date) ✅ preflight_check 通过" >> "$LOG"
        fi
    fi
fi

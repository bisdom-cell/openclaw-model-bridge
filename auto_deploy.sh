#!/bin/bash
# auto_deploy.sh — 自动部署脚本（V28 重写）
# 轮询 GitHub，有新 commit 就 git pull + 同步文件 + 按需 restart
# 使用方式：加入系统 crontab，每 2 分钟执行一次
# crontab: */2 * * * * bash ~/openclaw-model-bridge/auto_deploy.sh

set -euo pipefail

# 防重叠执行（mkdir 原子锁，macOS 兼容）
LOCK="/tmp/auto_deploy.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[auto_deploy] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# crontab 环境 PATH 不含 homebrew，手动补充
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

REPO_DIR="$HOME/openclaw-model-bridge"
LOG="$HOME/.openclaw/logs/auto_deploy.log"
mkdir -p "$(dirname "$LOG")"

# 凌晨静默期：00:00-07:00 不推送告警（deploy/sync 照常，只是不发通知）
is_quiet_hours() {
    local hour=$(TZ=Asia/Hong_Kong date '+%H')
    [ "$hour" -ge 0 ] && [ "$hour" -lt 7 ]
}

# 静默感知的推送封装：静默期跳过 WhatsApp，Discord 始终推送
# V37.4.3: 自动加 [SYSTEM_ALERT] 前缀，防止告警污染 PA 上下文
# V37.8.13: 静默期仍推 Discord #alerts（2026-04-16 血案：Gateway 宕 9h 因凌晨静默期
#   同时跳过 WhatsApp+Discord，3 次 CRITICAL preflight 失败全被吞没）
quiet_alert() {
    local msg="$1"
    case "$msg" in
        "[SYSTEM_ALERT]"*) ;;
        *) msg="[SYSTEM_ALERT]
$msg" ;;
    esac
    if is_quiet_hours; then
        echo "$(date) [QUIET] 静默期跳过WhatsApp，Discord仍推: ${msg:0:80}..." >> "$LOG"
        openclaw message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$msg" --json >/dev/null 2>&1 || true
        return 0
    fi
    openclaw message send --channel whatsapp --target "${OPENCLAW_PHONE:-+85200000000}" --message "$msg" --json >/dev/null 2>&1 || true
    openclaw message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$msg" --json >/dev/null 2>&1 || true
}

cd "$REPO_DIR" || { echo "$(date) ERROR: cannot cd to $REPO_DIR" >> "$LOG"; exit 1; }

# ── 0. 确保 JSON merge driver 已配置（status.json 冲突自动合并）──
if ! git config --get merge.json-status.driver >/dev/null 2>&1; then
    git config merge.json-status.driver "python3 $REPO_DIR/merge_status_json.py %O %A %B"
    git config merge.json-status.name "JSON-aware merge for status.json"
    echo "$(date) 配置 JSON merge driver for status.json" >> "$LOG"
fi

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

# ── 检测手动 git reset 导致的 HEAD 变化（auto_deploy 的 fetch 看不到新 commit）──
# 场景：用户执行 git fetch + git reset --hard origin/main，repo 已是最新，
# 但 auto_deploy 的 fetch 发现 LOCAL==REMOTE，跳过文件同步 → 部署文件滞后
# 修复：记录上次部署的 HEAD，HEAD 变化时强制全量文件同步
LAST_DEPLOY_HEAD_FILE="$REPO_DIR/.last_deploy_head"
LAST_DEPLOY_HEAD=""
[ -f "$LAST_DEPLOY_HEAD_FILE" ] && LAST_DEPLOY_HEAD=$(cat "$LAST_DEPLOY_HEAD_FILE" 2>/dev/null)
HEAD_CHANGED=false
if [ "$LOCAL" != "$LAST_DEPLOY_HEAD" ]; then
    HEAD_CHANGED=true
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
#
# V36.2: 显式声明不需要部署的文件类别（解决 INV-DEPLOY-001 gap）
# 以下文件在仓库中但有意不在 FILE_MAP 中：
#   - test_*.py          → 开发环境单测，Mac Mini 不运行
#   - full_regression.sh → 开发环境全量回归，Mac Mini 不运行
#   - smoke_test.sh      → 开发环境 smoke test
#   - quickstart.sh      → 一次性 demo 脚本
#   - *_benchmark.py     → 开发环境评测工具（slo_benchmark/reliability_bench）
#   - adversarial_audit.py → 已移除（V37.1: 合并入 ontology/governance_checker.py）
#   - ontology/           → 独立子项目（宪法规定：删除后原系统正常）
#   - docs/               → 文档（GitHub 在线阅读）
#   - gameday.sh          → 故障演练（手动执行）
#   - merge_status_json.py → git merge driver（仓库内使用）
#
declare -a FILE_MAP=(
    # 核心服务（Proxy + Adapter）
    "proxy_filters.py|$HOME/proxy_filters.py"
    "tool_proxy.py|$HOME/tool_proxy.py"
    "adapter.py|$HOME/adapter.py"
    "providers.py|$HOME/providers.py"
    "memory_plane.py|$HOME/memory_plane.py"
    "VERSION|$HOME/VERSION"

    # 运维脚本
    "restart.sh|$HOME/restart.sh"
    "health_check.sh|$HOME/health_check.sh"
    "kb_write.sh|$HOME/kb_write.sh"
    "kb_append_source.sh|$HOME/kb_append_source.sh"
    "kb_review.sh|$HOME/kb_review.sh"
    "kb_evening.sh|$HOME/kb_evening.sh"
    "kb_save_arxiv.sh|$HOME/kb_save_arxiv.sh"
    "kb_search.sh|$HOME/kb_search.sh"
    "kb_inject.sh|$HOME/kb_inject.sh"
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
    "jobs/hf_papers/run_hf_papers.sh|$HOME/.openclaw/jobs/hf_papers/run_hf_papers.sh"
    "jobs/semantic_scholar/run_semantic_scholar.sh|$HOME/.openclaw/jobs/semantic_scholar/run_semantic_scholar.sh"
    "jobs/dblp/run_dblp.sh|$HOME/.openclaw/jobs/dblp/run_dblp.sh"
    "jobs/acl_anthology/run_acl_anthology.sh|$HOME/.openclaw/jobs/acl_anthology/run_acl_anthology.sh"
    "jobs/github_trending/run_github_trending.sh|$HOME/.openclaw/jobs/github_trending/run_github_trending.sh"
    "jobs/rss_blogs/run_rss_blogs.sh|$HOME/.openclaw/jobs/rss_blogs/run_rss_blogs.sh"
    "jobs/ontology_sources/run_ontology_sources.sh|$HOME/.openclaw/jobs/ontology_sources/run_ontology_sources.sh"
    "jobs/ontology_sources/ontology_parser.py|$HOME/.openclaw/jobs/ontology_sources/ontology_parser.py"

    # 财经/政策新闻（V37.8.2） + V37.8.5 僵尸检测模块
    "jobs/finance_news/run_finance_news.sh|$HOME/.openclaw/jobs/finance_news/run_finance_news.sh"
    "jobs/finance_news/finance_news_zombie.py|$HOME/.openclaw/jobs/finance_news/finance_news_zombie.py"

    # 黄大年茶思屋科技网站(V37.8.14)
    "jobs/chaspark/run_chaspark.sh|$HOME/.openclaw/jobs/chaspark/run_chaspark.sh"

    # AI Leaders X 技术洞察追踪（V34: 替代 karpathy_x，9位 AI 大牛）
    "jobs/karpathy_x/run_karpathy_x.sh|$HOME/.openclaw/jobs/karpathy_x/run_karpathy_x.sh"
    "jobs/ai_leaders_x/run_ai_leaders_x.sh|$HOME/.openclaw/jobs/ai_leaders_x/run_ai_leaders_x.sh"

    # 备份脚本
    "openclaw_backup.sh|$HOME/openclaw_backup.sh"

    # 体检 & 验证脚本（V37.8.3: 确保 Mac Mini ~/preflight 是最新版）
    "preflight_check.sh|$HOME/preflight_check.sh"
    "job_smoke_test.sh|$HOME/job_smoke_test.sh"

    # Multimodal Memory
    "mm_index.py|$HOME/openclaw-model-bridge/mm_index.py"
    "mm_search.py|$HOME/openclaw-model-bridge/mm_search.py"
    "mm_index_cron.sh|$HOME/openclaw-model-bridge/mm_index_cron.sh"

    # 监控 & 维护（conv_quality+token_report 合并为 daily_ops_report）
    "daily_ops_report.sh|$HOME/daily_ops_report.sh"
    "conv_quality.py|$HOME/conv_quality.py"
    "token_report.py|$HOME/token_report.py"
    "kb_dedup.py|$HOME/kb_dedup.py"
    "kb_review_collect.py|$HOME/kb_review_collect.py"
    "kb_evening_collect.py|$HOME/kb_evening_collect.py"
    "kb_autotag.py|$HOME/kb_autotag.py"

    # KB 趋势报告 + 状态共享 + 安全
    "kb_trend.py|$HOME/kb_trend.py"
    "status_update.py|$HOME/status_update.py"
    "kb_status_refresh.sh|$HOME/kb_status_refresh.sh"
    "kb_integrity.py|$HOME/kb_integrity.py"
    "audit_log.py|$HOME/audit_log.py"
    "security_score.py|$HOME/security_score.py"

    # KB Embedding + RAG（search_kb 依赖）
    "local_embed.py|$HOME/local_embed.py"
    "kb_embed.py|$HOME/kb_embed.py"
    "kb_rag.py|$HOME/kb_rag.py"

    # Cron 健康工具
    "cron_doctor.sh|$HOME/cron_doctor.sh"
    "cron_canary.sh|$HOME/cron_canary.sh"
    "crontab_safe.sh|$HOME/crontab_safe.sh"

    # 数据清洗工具
    "data_clean.py|$HOME/data_clean.py"

    # 用户偏好自动学习
    "preference_learner.py|$HOME/preference_learner.py"

    # SLO + 配置中心化 + 故障快照（V32）
    "config.yaml|$HOME/config.yaml"
    "config_loader.py|$HOME/config_loader.py"
    "slo_checker.py|$HOME/slo_checker.py"
    "incident_snapshot.py|$HOME/incident_snapshot.py"
    "gameday.sh|$HOME/gameday.sh"

    # Agent 做梦引擎（V32）
    "kb_dream.sh|$HOME/kb_dream.sh"

    # 对话精华提炼（V37）
    "kb_harvest_chat.py|$HOME/kb_harvest_chat.py"

    # 治理审计定时任务（V37.1）
    "governance_audit_cron.sh|$HOME/governance_audit_cron.sh"

    # 统一推送（V33 Discord 双通道）
    "notify.sh|$HOME/notify.sh"

    # 三方宪法 SOUL.md
    "SOUL.md|$HOME/.openclaw/workspace/.openclaw/SOUL.md"

    # 三方宪法状态锚点（repo → ~/.kb/，Claude Code 收工写入的变更同步到 PA）
    "status.json|$HOME/.kb/status.json"

    # 自部署（bootstrapping）— 部署到 HOME 目录
    # V37.8.12: 移除冗余 $HOME/openclaw-model-bridge/auto_deploy.sh 映射——
    #   当 REPO_DIR==$HOME/openclaw-model-bridge 时是自复制，macOS cp 返回非零 + set -e
    #   杀脚本，导致 new-commit 同步中途死亡。仓库目录的 auto_deploy.sh 已经由 git pull 更新。
    "auto_deploy.sh|$HOME/auto_deploy.sh"

    # PA 灵魂文件（最高优先级上下文）
    "SOUL.md|$HOME/.openclaw/workspace/SOUL.md"

    # Ops Agent SOUL.md + 健康检查脚本
    "ops_soul.md|$HOME/.openclaw/SOUL.md"
    "ops_health.sh|$HOME/ops_health.sh"

    # 文档同步到 KB（供 WhatsApp PA 按需查阅）
    "docs/GUIDE.md|$HOME/.kb/docs/GUIDE.md"
    "docs/config.md|$HOME/.kb/docs/config.md"
    "CLAUDE.md|$HOME/.kb/docs/CLAUDE.md"
)

SYNCED=0
NEED_RESTART=false

if $HAS_NEW_COMMITS; then
    for mapping in "${FILE_MAP[@]}"; do
        SRC="${mapping%%|*}"
        DST="${mapping##*|}"

        # 只同步本次变更的文件
        if echo "$CHANGED_FILES" | grep -q "^${SRC}$"; then
            # V37.8.12: 自复制守卫（REPO_DIR/SRC == DST 时 macOS cp 返回非零 → set -e 杀脚本）
            if [ "$REPO_DIR/$SRC" = "$DST" ]; then
                continue
            fi
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

# ── 3b. 漂移检测 ──────────────────────────────────────────────────────
# 触发条件（满足任一即执行）：
#   1. 每小时整点 (minute < 2) — 定期兜底
#   2. HEAD 变化但无新 commit — 手动 git reset 后立即同步（V37.1 修复）
# 解决盲区：用户执行 git reset --hard origin/main 后，auto_deploy 看不到新 commit，
# 但 HEAD 已变化，立即触发全量比对确保部署文件同步
MINUTE=$(date +%M)
DRIFT_REASON=""
if [ "$MINUTE" -lt 2 ]; then
    DRIFT_REASON="hourly"
elif $HEAD_CHANGED && ! $HAS_NEW_COMMITS; then
    DRIFT_REASON="head_changed_no_new_commits"
    echo "$(date) HEAD 变化(${LAST_DEPLOY_HEAD:0:8}->${LOCAL:0:8})但无新commit(手动reset?)，触发全量同步" >> "$LOG"
fi

if [ -n "$DRIFT_REASON" ]; then
    DRIFT=0
    for mapping in "${FILE_MAP[@]}"; do
        SRC="${mapping%%|*}"
        DST="${mapping##*|}"

        [ ! -f "$REPO_DIR/$SRC" ] && continue

        # V37.8.11: status.json 合法分叉豁免（mirror V37.8.1 preflight 豁免）
        # repo 是 Claude Code 快照，runtime 由 kb_status_refresh cron 每小时
        # 重写 health/quality 字段 → 两侧设计上永远不一致 → 整文件 md5 比对
        # 永远 mismatch → 每小时"修复"+ 每小时告警 + 每次覆盖会清空运行时数据。
        # 修复方法：drift loop 跳过 status.json；Claude Code 的 intent 变更（priorities/
        # unfinished/recent_changes）通过 new-commit 路径（上方 CHANGED_FILES 循环）
        # 单向下传，确保 intent 仍能到达运行时。
        # Blood lesson: 2026-04-15 用户反馈"每小时收到漂移告警"—预期噪声被 [SYSTEM_ALERT]
        # 前缀显性化后成为干扰。详见 ontology/docs/cases/kb_evening_fallback_quota_chain_case.md
        # V37.8.11 扩展章节。
        if [[ "$SRC" == "status.json" ]]; then
            continue
        fi

        # V37.8.12: 自复制守卫（REPO_DIR/SRC == DST 时跳过，避免 cp 自身报错）
        if [ "$REPO_DIR/$SRC" = "$DST" ]; then
            continue
        fi

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
        quiet_alert "$DRIFT_MSG"
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
        quiet_alert "$CRON_MSG"
    fi

    # ── 3d. Crontab 条目数监控（V30新增：防止意外清空）─────────────────
    CRON_COUNT=$(crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' | wc -l | tr -d ' ')
    CRON_COUNT_FILE="$HOME/.crontab_entry_count"
    CRON_MIN_ENTRIES=10  # 正常应有 ~20 条，低于 10 条说明出了问题

    if [ "$CRON_COUNT" -lt "$CRON_MIN_ENTRIES" ]; then
        # 检查是否已经告警过（避免每小时重复推送）
        LAST_ALERT=$(cat "$CRON_COUNT_FILE.alert" 2>/dev/null || echo "0")
        ALERT_AGE=$(( $(date +%s) - LAST_ALERT ))
        if [ "$ALERT_AGE" -gt 3600 ]; then
            echo "$(date) 🚨 crontab 条目异常减少: ${CRON_COUNT} 条 (预期 >= ${CRON_MIN_ENTRIES})" >> "$LOG"
            CRON_ALERT="🚨 Crontab 条目异常减少！

当前只有 $CRON_COUNT 条（正常应 >= $CRON_MIN_ENTRIES 条）
可能被意外清空！

修复：
1. bash ~/crontab_safe.sh restore
2. 或: bash ~/cron_doctor.sh"
            quiet_alert "$CRON_ALERT"
            date +%s > "$CRON_COUNT_FILE.alert"
        fi
    else
        # 条目数正常时，记录当前数量（供后续对比）
        echo "$CRON_COUNT" > "$CRON_COUNT_FILE"
        rm -f "$CRON_COUNT_FILE.alert" 2>/dev/null
    fi

    # 每日自动备份 crontab（每天首次运行时）
    CRON_BACKUP_FLAG="$HOME/.crontab_backups/.today_$(date +%Y%m%d)"
    if [ ! -f "$CRON_BACKUP_FLAG" ]; then
        bash "$HOME/crontab_safe.sh" backup 2>/dev/null || bash "$REPO_DIR/crontab_safe.sh" backup 2>/dev/null || true
        touch "$CRON_BACKUP_FLAG"
        # 清理旧标记
        find "$HOME/.crontab_backups" -name ".today_*" -mtime +7 -delete 2>/dev/null || true
    fi
fi

# 记录当前已部署的 HEAD（用于下次检测手动 reset）
echo "$LOCAL" > "$LAST_DEPLOY_HEAD_FILE"

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
        # V37.8.15: SKIP_PUSH_TEST=1 跳过 WhatsApp/Discord push test 实际发送
        # auto_deploy 有自己的告警通道(quiet_alert)，不需要 preflight 额外发送测试消息
        PREFLIGHT_OUT=$(SKIP_PUSH_TEST=1 bash "$PREFLIGHT" --full 2>&1) && PREFLIGHT_RC=0 || PREFLIGHT_RC=$?
        if [ $PREFLIGHT_RC -ne 0 ]; then
            # 提取失败项（只保留 ❌ 行）
            FAIL_LINES=$(echo "$PREFLIGHT_OUT" | grep "❌" | head -10)
            ALERT_MSG="🔴 部署后体检失败 (commit: $NEW_COMMIT)

$FAIL_LINES

详情：auto_deploy.log"
            echo "$(date) ❌ preflight_check 失败:" >> "$LOG"
            echo "$PREFLIGHT_OUT" >> "$LOG"
            quiet_alert "$ALERT_MSG"
        else
            echo "$(date) ✅ preflight_check 通过" >> "$LOG"
            # 更新三方共享状态
            python3 "$HOME/status_update.py" --set health.last_preflight pass --by cron 2>/dev/null || true
            python3 "$HOME/status_update.py" --set health.last_preflight_time "$(date '+%Y-%m-%d %H:%M')" --by cron 2>/dev/null || true
        fi
    fi

    # 更新三方共享状态 — 部署信息
    python3 "$HOME/status_update.py" --set health.last_deploy "$NEW_COMMIT" --by cron 2>/dev/null || true
    python3 "$HOME/status_update.py" --set health.last_deploy_time "$(date '+%Y-%m-%d %H:%M')" --by cron 2>/dev/null || true
fi

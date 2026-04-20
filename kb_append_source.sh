#!/bin/bash
# kb_append_source.sh — idempotent H2-dedup append to ~/.kb/sources/*.md
#
# V37.6: 修复 cron job 每次运行都 append 已存在 ## YYYY-MM-DD section
# 导致 sources 文件重复行爆炸（kb_dedup 2026-04-10 报告：438 行重复，
# ontology_sources.md 183 行 / dblp_daily 77 行 / arxiv 42 行 / rss 40 行 /
# freight 36 行）。所有 14 个 cron job 都是同一 bug class。
#
# 用法：
#   {
#     echo ""
#     echo "## ${DAY}"
#     cat "$MSG_FILE"
#   } | bash "$HOME/kb_append_source.sh" "$KB_SRC" "## ${DAY}"
#
# 参数：
#   $1 = KB_SRC — 目标 sources 文件路径（~/.kb/sources/*.md）
#   $2 = H2 marker — 待追加内容的唯一 H2 标记行（整行精确匹配）
#   stdin = 待追加的完整内容（包含 H2 marker 本身）
#
# 契约：
#   - 如果 KB_SRC 中已存在一行 exactly 等于 $2 → 跳过追加，exit 0（静默幂等）
#   - 否则 → 把 stdin 原样 append 到 KB_SRC
#   - 并发安全：用 flock 保护 append 阶段，避免两个 cron 同时写
#   - 不存在文件 → 自动创建 parent dir + touch
#
# 为什么用 `grep -Fxq` 而不是 `grep -q`：
#   - -F: fixed string（H2 marker 可能含 `.`/`*`/`[`/`📊` 等 regex meta）
#   - -x: 整行匹配（避免 `## 2026-04-11` 误伤 `## 2026-04-11-patch`）
#   - -q: quiet
#
# V37.6 血案归属：MR-4 (silent failure is a bug) + MR-6 (declaration
# ≠ runtime verification)。之前的 kb_dedup.py 是事后清理，本脚本
# 是源头幂等——从根本上阻断重复产生。

set -euo pipefail

KB_SRC="${1:?usage: kb_append_source.sh <kb_src_file> <h2_marker>}"
H2_MARKER="${2:?usage: kb_append_source.sh <kb_src_file> <h2_marker>}"

# 允许测试覆盖 (set KB_APPEND_SOURCE_QUIET=1 to silence stderr skip messages)
_log() {
    if [ "${KB_APPEND_SOURCE_QUIET:-0}" != "1" ]; then
        echo "$@" >&2
    fi
}

# 确保父目录存在 + 文件存在（幂等）
mkdir -p "$(dirname "$KB_SRC")"
touch "$KB_SRC"

# 已存在 → 静默跳过（exit 0，这是幂等契约的一部分）
if grep -Fxq "$H2_MARKER" "$KB_SRC" 2>/dev/null; then
    _log "[kb_append_source] skip: '$H2_MARKER' already in $(basename "$KB_SRC")"
    # Still drain stdin so upstream pipe doesn't SIGPIPE
    cat >/dev/null
    exit 0
fi

# flock 保护 append（避免两个 cron 同时写同一个 sources 文件导致行撕裂）
# 使用 subshell + exec 绑定 lock fd
(
    # macOS bash 3.2 不支持 flock 内建，用 /opt/homebrew/bin/flock 或
    # fallback 到无锁（append 模式对文件本身已经是原子的，小概率仅影响顺序）
    if command -v flock >/dev/null 2>&1; then
        flock -w 30 200
    fi
    cat >> "$KB_SRC"
) 200>"${KB_SRC}.lock"

# 不清理 .lock 文件（cron 下次复用），避免并发竞争
exit 0

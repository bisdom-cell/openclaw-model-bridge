#!/bin/bash
# mm_index_cron.sh — Multimodal Memory 定时索引
# cron: 0 */2 * * *  （每2小时增量索引）
# 扫描 Gateway 媒体目录 → Gemini Embedding 2 → 本地向量索引
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/.openclaw/logs/jobs/mm_index.log"
mkdir -p "$(dirname "$LOG")"

TS=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TS] === mm_index start ===" >> "$LOG"

# google-genai 安装在 /usr/bin/python3 (3.9) 下，Homebrew python3 (3.14) 无法 pip install
MM_PYTHON="/usr/bin/python3"
if ! $MM_PYTHON -c "from google import genai" 2>/dev/null; then
    echo "[$TS] ERROR: google-genai not installed for $MM_PYTHON, run: $MM_PYTHON -m pip install google-genai" >> "$LOG"
    exit 1
fi

# 检查 API Key（从 bash_profile 加载）
source "$HOME/.bash_profile" 2>/dev/null || true
if [ -z "${GEMINI_API_KEY:-}" ]; then
    echo "[$TS] ERROR: GEMINI_API_KEY not set" >> "$LOG"
    exit 1
fi
export GEMINI_API_KEY

# 运行索引（使用与 google-genai 匹配的 Python）
$MM_PYTHON "$SCRIPT_DIR/mm_index.py" >> "$LOG" 2>&1
RC=$?

TS2=$(date '+%Y-%m-%d %H:%M:%S')
if [ $RC -eq 0 ]; then
    echo "[$TS2] === mm_index done ===" >> "$LOG"
else
    echo "[$TS2] === mm_index FAILED (rc=$RC) ===" >> "$LOG"
fi

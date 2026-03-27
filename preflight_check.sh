#!/bin/bash
# preflight_check.sh — 收工前全面体检（V28新增）
# 在"结束今天的工作"前运行，系统性验证所有 job、配置、环境变量、部署一致性
# 用法：bash preflight_check.sh           （本地 dev 环境，跳过网络检查）
#       bash preflight_check.sh --full     （Mac Mini 上运行，含连通性+WhatsApp验证）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

FULL_MODE=false
[ "${1:-}" = "--full" ] && FULL_MODE=true

PASS=0
FAIL=0
WARN=0
SKIP=0

pass() { echo "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN + 1)); }
skip() { echo "  ⏭  $1 (skipped, use --full)"; SKIP=$((SKIP + 1)); }

echo "=== Preflight Check $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Mode: $([ "$FULL_MODE" = true ] && echo 'FULL (Mac Mini)' || echo 'DEV (repo only)')"
echo ""

# ── 1. 单元测试 ──────────────────────────────────────────────────────
echo "📋 1/16 单元测试"

if python3 test_tool_proxy.py > /dev/null 2>&1; then
    pass "proxy_filters 单测 (test_tool_proxy.py)"
else
    fail "proxy_filters 单测失败"
fi

if python3 test_check_registry.py > /dev/null 2>&1; then
    pass "registry 校验器单测 (test_check_registry.py)"
else
    fail "registry 校验器单测失败"
fi

# ── 2. 注册表校验 ─────────────────────────────────────────────────────
echo ""
echo "📋 2/16 注册表校验"

if python3 check_registry.py > /dev/null 2>&1; then
    pass "jobs_registry.yaml 校验通过"
else
    fail "jobs_registry.yaml 校验失败（运行 python3 check_registry.py 查看详情）"
fi

# ── 3. 文档漂移检测 ───────────────────────────────────────────────────
echo ""
echo "📋 3/16 文档漂移检测"

if python3 gen_jobs_doc.py --check > /dev/null 2>&1; then
    pass "docs/config.md 与 registry 一致"
else
    warn "docs/config.md 与 registry 不一致（运行 python3 gen_jobs_doc.py --check 查看）"
fi

# ── 4. 脚本语法检查 + 权限检查 ────────────────────────────────────────
echo ""
echo "📋 4/16 脚本语法 & 权限"

# 从 registry 提取所有 enabled 的 entry 文件（兼容无 PyYAML 环境）
SCRIPT_FILES=$(python3 -c "
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath('$SCRIPT_DIR/check_registry.py')))
try:
    import yaml
    with open('jobs_registry.yaml') as f:
        data = yaml.safe_load(f)
except ImportError:
    # 回退：用 check_registry 的 load_yaml
    from check_registry import load_yaml
    data = load_yaml('jobs_registry.yaml')
for j in data.get('jobs', []):
    if j.get('enabled', False) or j.get('enabled') == 'true':
        print(j['entry'])
" 2>/dev/null || echo "")

if [ -z "$SCRIPT_FILES" ]; then
    warn "无法解析 jobs_registry.yaml（缺少 PyYAML？）"
else
    while IFS= read -r script; do
        [ -z "$script" ] && continue
        script_path="$SCRIPT_DIR/$script"

        if [ ! -f "$script_path" ]; then
            fail "$script: 文件不存在"
            continue
        fi

        # 语法检查：.py 用 Python ast，.sh 用 bash -n
        if [[ "$script" == *.py ]]; then
            if python3 -c "import ast; ast.parse(open('$script_path').read())" 2>/dev/null; then
                pass "$script: 语法正确"
            else
                fail "$script: Python 语法错误"
            fi
        else
            if bash -n "$script_path" 2>/dev/null; then
                pass "$script: 语法正确"
            else
                fail "$script: bash 语法错误"
            fi
        fi

        # 可执行权限（仅在 --full 模式下 warn，因为 cron 通常用 bash xxx.sh 调用）
        if [ ! -x "$script_path" ]; then
            warn "$script: 缺少可执行权限（chmod +x 修复）"
        fi
    done <<< "$SCRIPT_FILES"
fi

# 额外检查非 registry 管理但重要的脚本（排除已在 registry 中检查过的）
for extra in restart.sh smoke_test.sh preflight_check.sh; do
    if [ -f "$SCRIPT_DIR/$extra" ]; then
        if bash -n "$SCRIPT_DIR/$extra" 2>/dev/null; then
            pass "$extra: 语法正确"
        else
            fail "$extra: bash 语法错误"
        fi
    fi
done

# ── 5. Python 文件语法检查 ────────────────────────────────────────────
echo ""
echo "📋 5/16 Python 语法检查"

for pyfile in adapter.py tool_proxy.py proxy_filters.py check_registry.py gen_jobs_doc.py; do
    if [ -f "$SCRIPT_DIR/$pyfile" ]; then
        if python3 -c "import ast; ast.parse(open('$pyfile').read())" 2>/dev/null; then
            pass "$pyfile: 语法正确"
        else
            fail "$pyfile: Python 语法错误"
        fi
    fi
done

# ── 6. 部署文件一致性检查（仓库 vs 运行时副本）────────────────────────
echo ""
echo "📋 6/16 部署文件一致性"

if $FULL_MODE; then
    # FILE_MAP from auto_deploy.sh
    declare -a FILE_MAP=(
        "proxy_filters.py|$HOME/proxy_filters.py"
        "tool_proxy.py|$HOME/tool_proxy.py"
        "adapter.py|$HOME/adapter.py"
        "restart.sh|$HOME/restart.sh"
        "health_check.sh|$HOME/health_check.sh"
        "kb_write.sh|$HOME/kb_write.sh"
        "kb_review.sh|$HOME/kb_review.sh"
        "kb_evening.sh|$HOME/kb_evening.sh"
        "kb_save_arxiv.sh|$HOME/kb_save_arxiv.sh"
        "job_watchdog.sh|$HOME/job_watchdog.sh"
        "wa_keepalive.sh|$HOME/wa_keepalive.sh"
        "run_hn_fixed.sh|$HOME/.openclaw/jobs/hn_watcher/run_hn_fixed.sh"
        "jobs/openclaw_official/run.sh|$HOME/.openclaw/jobs/openclaw_official/run.sh"
        "jobs/openclaw_official/run_discussions.sh|$HOME/.openclaw/jobs/openclaw_official/run_discussions.sh"
        "jobs/freight_watcher/run_freight.sh|$HOME/.openclaw/jobs/freight_watcher/run_freight.sh"
        "jobs/arxiv_monitor/run_arxiv.sh|$HOME/.openclaw/jobs/arxiv_monitor/run_arxiv.sh"
    )

    DRIFT_COUNT=0
    for mapping in "${FILE_MAP[@]}"; do
        SRC="${mapping%%|*}"
        DST="${mapping##*|}"

        if [ ! -f "$SCRIPT_DIR/$SRC" ]; then
            continue
        fi

        if [ ! -f "$DST" ]; then
            fail "$SRC: 运行时副本不存在 ($DST)"
            DRIFT_COUNT=$((DRIFT_COUNT + 1))
            continue
        fi

        # md5 比对（兼容 macOS 和 Linux）
        HASH_SRC=$(md5 -q "$SCRIPT_DIR/$SRC" 2>/dev/null || md5sum "$SCRIPT_DIR/$SRC" | cut -d' ' -f1)
        HASH_DST=$(md5 -q "$DST" 2>/dev/null || md5sum "$DST" | cut -d' ' -f1)

        if [ "$HASH_SRC" != "$HASH_DST" ]; then
            fail "$SRC: 仓库与运行时不一致（需运行 auto_deploy 或手动 cp）"
            DRIFT_COUNT=$((DRIFT_COUNT + 1))
        fi
    done

    if [ "$DRIFT_COUNT" -eq 0 ]; then
        pass "所有部署文件与仓库一致"
    fi
else
    skip "部署文件一致性（需在 Mac Mini 上验证）"
fi

# ── 7. 环境变量检查（bash -lc 模拟 cron 环境）────────────────────────
echo ""
echo "📋 7/16 环境变量检查（cron 环境模拟）"

if $FULL_MODE; then
    # 模拟 cron 调用方式：bash -lc 读取 ~/.bash_profile
    REQUIRED_VARS=(
        "REMOTE_API_KEY|adapter.py 远程 GPU 认证"
        "OPENCLAW_PHONE|WhatsApp 推送目标号码"
    )

    for entry in "${REQUIRED_VARS[@]}"; do
        VAR_NAME="${entry%%|*}"
        VAR_DESC="${entry##*|}"

        VAL=$(bash -lc "echo \${$VAR_NAME:-}" 2>/dev/null)
        if [ -n "$VAL" ]; then
            # 只显示前4位和后4位，保护敏感信息
            if [ ${#VAL} -gt 8 ]; then
                MASKED="${VAL:0:4}...${VAL: -4}"
            else
                MASKED="***"
            fi
            pass "$VAR_NAME ($VAR_DESC) = $MASKED"
        else
            fail "$VAR_NAME ($VAR_DESC) 在 bash -lc 环境中未设置（检查 ~/.bash_profile）"
        fi
    done

    # 检查 PATH 包含 homebrew
    HAS_BREW=$(bash -lc 'command -v brew >/dev/null 2>&1 && echo yes || echo no' 2>/dev/null)
    if [ "$HAS_BREW" = "yes" ]; then
        pass "bash -lc PATH 包含 Homebrew"
    else
        warn "bash -lc PATH 不包含 Homebrew（某些工具可能找不到）"
    fi
else
    skip "环境变量检查（需在 Mac Mini 上验证）"
fi

# ── 8. 服务连通性检查 ─────────────────────────────────────────────────
echo ""
echo "📋 8/16 服务连通性"

if $FULL_MODE; then
    # Adapter :5001
    ADAPTER_RESP=$(curl -s --max-time 5 http://localhost:5001/health 2>/dev/null) && RC=0 || RC=$?
    if [ $RC -eq 0 ] && echo "$ADAPTER_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
        pass "Adapter :5001 /health OK"
    else
        fail "Adapter :5001 /health 异常 (response: ${ADAPTER_RESP:-timeout})"
    fi

    # Tool Proxy :5002
    PROXY_RESP=$(curl -s --max-time 5 http://localhost:5002/health 2>/dev/null) && RC=0 || RC=$?
    if [ $RC -eq 0 ] && echo "$PROXY_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
        pass "Tool Proxy :5002 /health OK"
    else
        fail "Tool Proxy :5002 /health 异常 (response: ${PROXY_RESP:-timeout})"
    fi

    # Gateway :18789
    GW_CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://localhost:18789 2>/dev/null) || true
    if [ "$GW_CODE" -ge 200 ] 2>/dev/null && [ "$GW_CODE" -lt 400 ] 2>/dev/null; then
        pass "Gateway :18789 可达 (HTTP $GW_CODE)"
    else
        fail "Gateway :18789 不可达 (HTTP ${GW_CODE:-000})"
    fi
else
    skip "服务连通性（需在 Mac Mini 上验证）"
fi

# ── 9. 安全扫描（push 前必扫）────────────────────────────────────────
echo ""
echo "📋 9/16 安全扫描"

# API Key 泄漏检查（只扫描 git 跟踪的文件，忽略 .gitignore 排除的本地配置）
LEAK_SK=$(git grep -n "sk-[A-Za-z0-9]\{15,\}" -- "*.py" "*.sh" "*.md" 2>/dev/null | grep -v "sk-REPLACE-ME" | grep -v "sk-xxxx" || true)
LEAK_BSA=$(git grep -n "BSA[A-Za-z0-9]\{15,\}" -- "*.py" "*.sh" "*.md" 2>/dev/null | grep -v "BSAxxx" || true)

if [ -z "$LEAK_SK" ] && [ -z "$LEAK_BSA" ]; then
    pass "无 API Key 泄漏"
else
    fail "检测到可能的 API Key 泄漏！"
    [ -n "$LEAK_SK" ] && echo "      sk-* 匹配: $LEAK_SK"
    [ -n "$LEAK_BSA" ] && echo "      BSA* 匹配: $LEAK_BSA"
fi

# 真实手机号检查（排除占位号，只扫描 git 跟踪的文件）
LEAK_PHONE=$(git grep -n "+852[0-9]\{8\}" -- "*.py" "*.sh" "*.md" 2>/dev/null | grep -v "+85200000000" || true)
if [ -z "$LEAK_PHONE" ]; then
    pass "无真实手机号泄漏"
else
    fail "检测到真实手机号！"
    echo "      $LEAK_PHONE"
fi

# ── 10. Job 数据流 smoke test ──────────────────────────────────────────
# 用合成数据验证 job 脚本的 shell→Python 数据传递，零接触生产状态
echo ""
echo "📋 10/16 Job 数据流 smoke test"

# 10a. 反模式扫描：heredoc 结束符后紧跟 <<< 会导致 stdin 被 heredoc 耗尽
#      python3 - <<'PYEOF' ... PYEOF; <<< "$DATA" → DATA 永远读不到
ANTIPATTERN_FOUND=false
for script in run_hn_fixed.sh jobs/*/run*.sh jobs/*/*.sh; do
    [ -f "$SCRIPT_DIR/$script" ] || continue
    # 检测：heredoc 结束标记（独占一行）的下一行含 <<<
    if grep -A1 -n "^PYEOF$\|^PYEOF2$\|^EOF$\|^ENDPY$" "$SCRIPT_DIR/$script" 2>/dev/null | grep -q "<<<"; then
        fail "$script: heredoc+herestring 反模式（stdin 冲突，SENT_COUNT 将永远为 0）"
        ANTIPATTERN_FOUND=true
    fi
done
if ! $ANTIPATTERN_FOUND; then
    pass "所有 job 脚本无 heredoc+herestring 反模式"
fi

# 10b. HN Watcher 数据流验证：合成 JSONL → pipe → python3 -c → 输出文件
SMOKE_DIR=$(mktemp -d)
MOCK_RESULT='{"zh_title":"smoke测试标题","point":"smoke要点","stars":"⭐⭐⭐","title":"Smoke Test","hn_url":"https://example.com/smoke-test"}'
SMOKE_MSG="$SMOKE_DIR/msg.txt"
SMOKE_KB="$SMOKE_DIR/kb.txt"
> "$SMOKE_MSG"
> "$SMOKE_KB"

SMOKE_COUNT=$(echo "$MOCK_RESULT" | python3 -c '
import json, sys
today, msg_file, kb_source = sys.argv[1], sys.argv[2], sys.argv[3]
sent = 0
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: d = json.loads(line)
    except: continue
    hn_url = d.get("hn_url", "").strip()
    if not hn_url: continue
    zh_title = d.get("zh_title") or d.get("title", "")
    with open(msg_file, "a") as f:
        f.write(f"{zh_title}\n链接：{hn_url}\n\n")
    with open(kb_source, "a") as f:
        f.write(f"- [{zh_title}]({hn_url}) | {today}\n")
    sent += 1
print(sent)
' "2026-01-01" "$SMOKE_MSG" "$SMOKE_KB" 2>/dev/null || echo "0")

if [ "$SMOKE_COUNT" = "1" ] && [ -s "$SMOKE_MSG" ] && [ -s "$SMOKE_KB" ]; then
    pass "run_hn_fixed.sh: 数据流 ok (stdin→python→file, count=$SMOKE_COUNT)"
else
    fail "run_hn_fixed.sh: 数据流异常 (expected count=1, got ${SMOKE_COUNT:-empty}; msg=$(wc -c < "$SMOKE_MSG" 2>/dev/null)B, kb=$(wc -c < "$SMOKE_KB" 2>/dev/null)B)"
fi
rm -rf "$SMOKE_DIR"

# ── 11. 货代 deep_dive 静默失败检测 ─────────────────────────────────────
echo ""
echo "📋 11/16 货代 deep_dive 静默失败检测"

if $FULL_MODE; then
    # 检查 last_run.json 中 deep_dive 字段
    FREIGHT_STATUS="$HOME/.openclaw/jobs/freight_watcher/cache/last_run.json"
    if [ -f "$FREIGHT_STATUS" ]; then
        DEEP_DIVE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$FREIGHT_STATUS'))
    print(d.get('deep_dive', 'missing'))
except Exception:
    print('error')
" 2>/dev/null || echo "error")

        case "$DEEP_DIVE" in
            ok)
                pass "货代 deep_dive 状态正常"
                ;;
            no_data)
                warn "货代 deep_dive: ImportYeti 无数据返回（可能被 Cloudflare 拦截）"
                ;;
            skipped)
                warn "货代 deep_dive: 被跳过（检查 run_freight.sh 日志）"
                ;;
            missing)
                warn "货代 deep_dive: last_run.json 无 deep_dive 字段（旧版本脚本？）"
                ;;
            *)
                fail "货代 deep_dive: 状态异常 ($DEEP_DIVE)"
                ;;
        esac
    else
        warn "货代 last_run.json 不存在（任务未运行过？）"
    fi

    # 检查 scraper.log 最近是否有错误
    SCRAPER_LOG="$HOME/.openclaw/jobs/freight_watcher/cache/scraper.log"
    if [ -f "$SCRAPER_LOG" ]; then
        SCRAPER_ERRORS=$(grep -ciE "error|traceback|exception" "$SCRAPER_LOG" 2>/dev/null || true)
        SCRAPER_ERRORS=${SCRAPER_ERRORS:-0}
        if [ "$SCRAPER_ERRORS" -gt 0 ]; then
            warn "ImportYeti scraper.log 有 $SCRAPER_ERRORS 处错误记录"
        else
            pass "ImportYeti scraper.log 无错误"
        fi
    fi

    # 检查 playwright 在 /usr/bin/python3 下可用
    if /usr/bin/python3 -c "import playwright" 2>/dev/null; then
        pass "playwright 在 /usr/bin/python3 下可用"
    else
        fail "playwright 在 /usr/bin/python3 下不可用（pip3 install playwright）"
    fi
else
    skip "货代 deep_dive 检测（需在 Mac Mini 上验证）"
fi

# ── 12. #48703 WhatsApp listeners Map 补丁检测 ────────────────────────
echo "📋 12/16 #48703 WhatsApp listeners Map 补丁"

if $FULL_MODE; then
    OPENCLAW_DIST="/opt/homebrew/lib/node_modules/openclaw/dist"
    if [ -d "$OPENCLAW_DIST" ]; then
        UNPATCHED=$(grep -rl 'const listeners = /\* @__PURE__ \*/ new Map()' \
            "$OPENCLAW_DIST" --include="*.js" 2>/dev/null | grep -v ".bak" | wc -l | tr -d ' ')
        if [ "$UNPATCHED" -gt 0 ]; then
            fail "#48703 未修复: $UNPATCHED 个文件有 listeners Map 副本（运行 bash ~/patch_48703.sh 或 bash restart.sh 自动修复）"
        else
            pass "#48703 已修复（listeners Map 使用 globalThis singleton）"
        fi
    else
        warn "OpenClaw dist 目录不存在（未安装？）"
    fi
else
    skip "#48703 补丁检测（需在 Mac Mini 上验证）"
fi

# ── 13. 陈旧锁文件检测（V30新增）─────────────────────────────────────
echo ""
echo "📋 13/16 陈旧锁文件检测"

if $FULL_MODE; then
    STALE_LOCK_DIRS=(
        "/tmp/arxiv_monitor.lockdir|ArXiv监控"
        "/tmp/hn_watcher.lockdir|HN抓取"
        "/tmp/freight_watcher.lockdir|货代Watcher"
        "/tmp/job_watchdog.lockdir|元监控"
        "/tmp/auto_deploy.lockdir|自动部署"
        "/tmp/openclaw_run.lockdir|OpenClaw版本"
        "/tmp/run_discussions.lockdir|Issues监控"
        "/tmp/kb_review.lockdir|KB回顾"
        "/tmp/kb_evening.lockdir|KB晚间"
    )

    STALE_COUNT=0
    CHECK_EPOCH=$(date +%s)
    for entry in "${STALE_LOCK_DIRS[@]}"; do
        IFS='|' read -r lock_path name <<< "$entry"
        if [ -d "$lock_path" ]; then
            if [ "$(uname)" = "Darwin" ]; then
                LOCK_EPOCH=$(stat -f %m "$lock_path" 2>/dev/null || echo "0")
            else
                LOCK_EPOCH=$(stat -c %Y "$lock_path" 2>/dev/null || echo "0")
            fi
            LOCK_AGE=$(( CHECK_EPOCH - LOCK_EPOCH ))
            if [ "$LOCK_AGE" -gt 3600 ]; then
                LOCK_HOURS=$(( LOCK_AGE / 3600 ))
                fail "$name: 陈旧锁 $lock_path （${LOCK_HOURS}h）— 该 job 无法执行！"
                STALE_COUNT=$((STALE_COUNT + 1))
            fi
        fi
    done

    if [ "$STALE_COUNT" -eq 0 ]; then
        pass "无陈旧锁文件"
    else
        echo "      修复：rmdir /tmp/*.lockdir"
    fi
else
    skip "陈旧锁文件检测（需在 Mac Mini 上验证）"
fi

# ── 14. Cron 心跳检测（V30新增）──────────────────────────────────────
echo ""
echo "📋 14/16 Cron 心跳检测"

if $FULL_MODE; then
    CANARY_FILE="$HOME/.cron_canary"
    if [ -f "$CANARY_FILE" ]; then
        CANARY_EPOCH=$(head -1 "$CANARY_FILE" 2>/dev/null | tr -d '[:space:]')
        CHECK_NOW=$(date +%s)
        if [[ "$CANARY_EPOCH" =~ ^[0-9]+$ ]]; then
            CANARY_AGE=$(( CHECK_NOW - CANARY_EPOCH ))
            CANARY_MINS=$(( CANARY_AGE / 60 ))
            if [ "$CANARY_AGE" -gt 1800 ]; then
                fail "Cron 心跳已 ${CANARY_MINS}m 未更新（cron daemon 可能已停止）"
            else
                pass "Cron 心跳正常（${CANARY_MINS}m 前更新）"
            fi
        else
            warn "Cron 心跳文件格式异常"
        fi
    else
        warn "Cron 心跳文件不存在（cron_canary.sh 未注册到 crontab？）"
    fi
else
    skip "Cron 心跳检测（需在 Mac Mini 上验证）"
fi

# ── 15. Crontab 路径 vs FILE_MAP 一致性（V30.3 系统联调）──────────────
echo ""
echo "📋 15/16 Crontab 路径 vs FILE_MAP 一致性"

if $FULL_MODE; then
    CRON_PATH_ERRORS=0

    # 解析 Python 输出
    while IFS= read -r result_line; do
        case "$result_line" in
            PASS\|*) pass "${result_line#PASS|}" ;;
            FAIL\|*) fail "${result_line#FAIL|}"; CRON_PATH_ERRORS=$((CRON_PATH_ERRORS + 1)) ;;
            WARN\|*) warn "${result_line#WARN|}" ;;
        esac
    done < <(python3 - "$SCRIPT_DIR/auto_deploy.sh" << 'PYEOF'
import sys, os, re, subprocess

deploy_script = sys.argv[1]

file_map = {}
with open(deploy_script) as f:
    in_map = False
    for line in f:
        if 'declare -a FILE_MAP' in line:
            in_map = True
            continue
        if in_map and line.strip() == ')':
            break
        if in_map and '|' in line:
            m = re.search(r'"([^"]+)"', line)
            if m:
                parts = m.group(1).split('|', 1)
                if len(parts) == 2:
                    src = os.path.basename(parts[0])
                    dst = parts[1].replace('$HOME', os.path.expanduser('~'))
                    file_map[src] = dst

try:
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
    crontab_lines = result.stdout.strip().split('\n')
except Exception:
    print("WARN|无法读取 crontab")
    sys.exit(0)

errors = []
checked = 0
for line in crontab_lines:
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    m = re.search(r'bash\s+([^\s>|]+\.sh)', line)
    if not m:
        continue
    cron_path = m.group(1).replace('~/', os.path.expanduser('~/'))
    script_name = os.path.basename(cron_path)
    if script_name in file_map:
        expected = file_map[script_name]
        if os.path.normpath(cron_path) != os.path.normpath(expected):
            errors.append(f"FAIL|{script_name}: crontab 路径 {cron_path} ≠ FILE_MAP 目标 {expected}")
        checked += 1

for e in errors:
    print(e)

if not errors and checked > 0:
    print(f"PASS|{checked} 个 crontab 脚本路径均与 FILE_MAP 一致")
elif checked == 0:
    print("WARN|未找到可验证的 crontab 条目")
PYEOF
)
else
    skip "Crontab 路径一致性（需在 Mac Mini 上验证）"
fi

# ── 16. 推送通道 smoke test（V30.3 E2E 功能测试）──────────────────────
echo ""
echo "📋 16/16 推送通道 smoke test"

if $FULL_MODE; then
    # 验证 openclaw message send 命令可用且无配置错误
    PUSH_ERR=$(mktemp)
    PUSH_TEST=$(openclaw message send --target "${OPENCLAW_PHONE:-+85200000000}" --message "🔧 preflight push test $(date '+%H:%M')" --json 2>"$PUSH_ERR") && PUSH_RC=0 || PUSH_RC=$?

    PUSH_STDERR=$(cat "$PUSH_ERR" 2>/dev/null)
    rm -f "$PUSH_ERR"

    if [ $PUSH_RC -eq 0 ]; then
        # 检查 stderr 是否有插件警告（即使退出码为 0）
        if echo "$PUSH_STDERR" | grep -qi "duplicate plugin\|plugin.*error" 2>/dev/null; then
            warn "推送成功但有插件警告: $(echo "$PUSH_STDERR" | head -1)"
        else
            pass "WhatsApp 推送通道正常（openclaw message send 退出码 0）"
        fi
    else
        fail "WhatsApp 推送失败（退出码 $PUSH_RC）: $(echo "$PUSH_STDERR" | head -2)"
    fi
else
    skip "推送通道 smoke test（需在 Mac Mini 上验证）"
fi

# ── 汇总 ──────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  通过: $PASS | 失败: $FAIL | 警告: $WARN | 跳过: $SKIP"
echo "═══════════════════════════════════════"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "❌ PREFLIGHT FAILED: $FAIL 项检查未通过，请修复后再提交"
    exit 1
elif [ $WARN -gt 0 ]; then
    echo ""
    echo "⚠️  PASSED WITH WARNINGS: 建议处理 $WARN 条警告"
    exit 0
else
    echo ""
    echo "✅ ALL CLEAR — 可以安全提交"
    exit 0
fi

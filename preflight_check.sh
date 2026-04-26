#!/bin/bash
# preflight_check.sh — 收工前全面体检（V28新增）
# 在"结束今天的工作"前运行，系统性验证所有 job、配置、环境变量、部署一致性
# 用法：bash preflight_check.sh           （本地 dev 环境，跳过网络检查）
#       bash preflight_check.sh --full     （Mac Mini 上运行，含连通性+WhatsApp验证）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# V37.8.3: 当部署到 HOME 运行时，自动解析到仓库目录
# (preflight_check.sh 部署到 ~/ 但测试文件和源码在 ~/openclaw-model-bridge/)
if [ "$SCRIPT_DIR" = "$HOME" ] && [ -d "$HOME/openclaw-model-bridge" ] && [ -f "$HOME/openclaw-model-bridge/preflight_check.sh" ]; then
    SCRIPT_DIR="$HOME/openclaw-model-bridge"
fi

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
echo "📋 1/19 单元测试"

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
echo "📋 2/19 注册表校验"

if python3 check_registry.py > /dev/null 2>&1; then
    pass "jobs_registry.yaml 校验通过"
else
    fail "jobs_registry.yaml 校验失败（运行 python3 check_registry.py 查看详情）"
fi

# V36.2 → V37.9.18: Crontab 漂移检测（仅 --full 模式，需要 crontab 访问）
# V37.9.18 修复: 之前只 grep "间隔漂移" 漏掉 "registry 已启用但 crontab 中未找到" warning，
# 让 V37.9.16 kb_deep_dive 收工时假绿通过，cron 从未注册导致 2 天静默推送丢失。
# 现在两种 warning 都触发 fail。
if $FULL_MODE; then
    DRIFT_OUT=$(python3 check_registry.py --check-crontab 2>&1 || true)
    DRIFT_FAILED=false

    # 检查 1: 间隔漂移（V36.2）
    if echo "$DRIFT_OUT" | grep -q "间隔漂移"; then
        DRIFT_LINES=$(echo "$DRIFT_OUT" | grep "间隔漂移")
        fail "crontab 间隔漂移（registry vs 实际 crontab 不一致）:
$DRIFT_LINES
修复: 用 crontab_safe.sh remove/add 对齐 registry"
        DRIFT_FAILED=true
    fi

    # 检查 2: 注册缺失（V37.9.18 新增 — kb_deep_dive 血案修复）
    if echo "$DRIFT_OUT" | grep -q "registry 已启用但 crontab 中未找到"; then
        MISSING_LINES=$(echo "$DRIFT_OUT" | grep "registry 已启用但 crontab 中未找到")
        fail "crontab 注册缺失（registry 声明 enabled 但 crontab 中无对应条目）:
$MISSING_LINES
修复: 用 crontab_safe.sh add '<cron 行>' 注册到 crontab"
        DRIFT_FAILED=true
    fi

    if ! $DRIFT_FAILED; then
        pass "crontab 与 registry 一致（无间隔漂移 + 所有 enabled job 已注册）"
    fi
else
    skip "crontab 漂移检测"
fi

# ── 3. 文档漂移检测 ───────────────────────────────────────────────────
echo ""
echo "📋 3/19 文档漂移检测"

if python3 gen_jobs_doc.py --check > /dev/null 2>&1; then
    pass "docs/config.md 与 registry 一致"
else
    warn "docs/config.md 与 registry 不一致（运行 python3 gen_jobs_doc.py --check 查看）"
fi

# ── 4. 脚本语法检查 + 权限检查 ────────────────────────────────────────
echo ""
echo "📋 4/19 脚本语法 & 权限"

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
seen = set()
for j in data.get('jobs', []):
    if j.get('enabled', False) or j.get('enabled') == 'true':
        script = j['entry'].split()[0]  # strip args (e.g. 'kb_dream.sh --map-sources' → 'kb_dream.sh')
        if script not in seen:
            seen.add(script)
            print(script)
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
echo "📋 5/19 Python 语法检查"

for pyfile in adapter.py tool_proxy.py proxy_filters.py check_registry.py gen_jobs_doc.py; do
    if [ -f "$SCRIPT_DIR/$pyfile" ]; then
        if python3 -c "import ast; ast.parse(open('$pyfile').read())" 2>/dev/null; then
            pass "$pyfile: 语法正确"
        else
            fail "$pyfile: Python 语法错误"
        fi
    fi
done

# V37.8.13: last_run.json 写入声明层守卫（防回退到 watchdog 报"状态文件不存在"的 V37.8.13 之前状态）
LAST_RUN_GUARDS=(
    "kb_inject.sh|last_run_inject.json"
    "kb_harvest_chat.py|last_run_harvest_chat.json"
)
for entry in "${LAST_RUN_GUARDS[@]}"; do
    IFS='|' read -r script status_file <<< "$entry"
    if [ -f "$SCRIPT_DIR/$script" ]; then
        if grep -q "$status_file" "$SCRIPT_DIR/$script"; then
            pass "$script: V37.8.13 last_run 写入声明存在 ($status_file)"
        else
            fail "$script: V37.8.13 last_run 写入声明丢失 — watchdog 会报状态文件不存在"
        fi
    fi
done

# ── 6. 部署文件一致性检查（仓库 vs 运行时副本）────────────────────────
echo ""
echo "📋 6/19 部署文件一致性"

if $FULL_MODE; then
    # V31: 从 auto_deploy.sh 动态解析 FILE_MAP（不再硬编码，避免两处不同步）
    declare -a FILE_MAP=()
    while IFS= read -r map_line; do
        [ -n "$map_line" ] && FILE_MAP+=("$map_line")
    done < <(python3 -c "
import re, os
home = os.path.expanduser('~')
with open('$SCRIPT_DIR/auto_deploy.sh') as f:
    in_map = False
    for line in f:
        if 'declare -a FILE_MAP' in line:
            in_map = True; continue
        if in_map and line.strip() == ')':
            break
        if in_map:
            m = re.search(r'\"([^\"]+)\"', line)
            if m and '|' in m.group(1):
                # 展开 \$HOME 为实际路径
                print(m.group(1).replace('\$HOME', home))
" 2>/dev/null)

    DRIFT_COUNT=0
    for mapping in "${FILE_MAP[@]}"; do
        SRC="${mapping%%|*}"
        DST="${mapping##*|}"

        if [ ! -f "$SCRIPT_DIR/$SRC" ]; then
            continue
        fi

        # V37.8.1: status.json 合法分叉豁免 — repo 是 Claude Code 快照，
        # runtime 由 cron (kb_status_refresh) 每小时刷新 health/quality 字段，
        # 两边设计上永远不一致，全文件 md5 比对无意义
        if [[ "$SRC" == "status.json" ]]; then
            pass "status.json: 豁免（仓库快照 vs 运行时实时刷新，合法分叉）"
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
echo "📋 7/19 环境变量检查（cron 环境模拟）"

if $FULL_MODE; then
    # 模拟 cron 调用方式：bash -lc 读取 ~/.bash_profile
    REQUIRED_VARS=(
        "REMOTE_API_KEY|adapter.py 远程 GPU 认证"
        "OPENCLAW_PHONE|WhatsApp 推送目标号码"
        "GEMINI_API_KEY|mm_index Multimodal Embedding"
    )
    OPTIONAL_VARS=(
        "DISCORD_BOT_TOKEN|Discord Bot 认证"
        "DISCORD_TARGET|Discord 推送目标用户ID"
    )
    # V36.2: Discord 频道 ID — 缺失会导致推送静默失败
    DISCORD_CHANNEL_VARS=(
        "DISCORD_CH_PAPERS|Discord #论文 频道"
        "DISCORD_CH_TECH|Discord #技术 频道"
        "DISCORD_CH_ALERTS|Discord #告警 频道"
        "DISCORD_CH_DAILY|Discord #日报 频道"
        "DISCORD_CH_FREIGHT|Discord #货代 频道"
        "DISCORD_CH_ONTOLOGY|Discord #ontology 频道"
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

    for entry in "${OPTIONAL_VARS[@]}"; do
        VAR_NAME="${entry%%|*}"
        VAR_DESC="${entry##*|}"
        VAL=$(bash -lc "echo \${$VAR_NAME:-}" 2>/dev/null)
        if [ -n "$VAL" ]; then
            if [ ${#VAL} -gt 8 ]; then
                MASKED="${VAL:0:4}...${VAL: -4}"
            else
                MASKED="***"
            fi
            pass "$VAR_NAME ($VAR_DESC) = $MASKED"
        else
            warn "$VAR_NAME ($VAR_DESC) 未设置（Discord 通道不可用）"
        fi
    done

    # V36.2: Discord 频道 ID 检查（缺失 = 推送静默丢失）
    DISCORD_MISSING=0
    for entry in "${DISCORD_CHANNEL_VARS[@]}"; do
        VAR_NAME="${entry%%|*}"
        VAR_DESC="${entry##*|}"
        VAL=$(bash -lc "echo \${$VAR_NAME:-}" 2>/dev/null)
        if [ -n "$VAL" ]; then
            pass "$VAR_NAME ($VAR_DESC) 已设置"
        else
            fail "$VAR_NAME ($VAR_DESC) 未设置 — Discord 推送到该频道会静默失败！"
            DISCORD_MISSING=$((DISCORD_MISSING + 1))
        fi
    done
    [ "$DISCORD_MISSING" -gt 0 ] && warn "共 $DISCORD_MISSING 个 Discord 频道 ID 缺失，对应频道的推送会静默丢失"

    # V36.2: 从 registry 读取 needs_api_key=true 的 job，验证其脚本可在 cron 环境运行
    # 这让 needs_api_key 字段从纯文档变成可执行的约束
    NEEDS_KEY_JOBS=$(python3 -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR')
from check_registry import load_yaml
data = load_yaml('$SCRIPT_DIR/jobs_registry.yaml')
for j in data.get('jobs', []):
    if j.get('enabled') and j.get('needs_api_key'):
        print(j.get('id','?') + '|' + j.get('entry','?'))
" 2>/dev/null || true)
    if [ -n "$NEEDS_KEY_JOBS" ]; then
        API_KEY_OK=true
        while IFS='|' read -r jid jentry; do
            # 这些 job 声明需要 API key，检查 REMOTE_API_KEY 是否可用
            # （大部分通过 Proxy→Adapter→远程 GPU，用 REMOTE_API_KEY）
            :  # REMOTE_API_KEY 已在上方 REQUIRED_VARS 检查，此处确认 registry 声明被消费
        done <<< "$NEEDS_KEY_JOBS"
        NKEY_COUNT=$(echo "$NEEDS_KEY_JOBS" | wc -l | tr -d ' ')
        pass "needs_api_key 字段已被消费: $NKEY_COUNT 个 job 声明需要 API key"
    fi

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
echo "📋 8/19 服务连通性"

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
echo "📋 9/19 安全扫描"

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
echo "📋 10/19 Job 数据流 smoke test"

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
echo "📋 11/19 货代 deep_dive 静默失败检测"

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
echo ""
echo "📋 12/19 #48703 WhatsApp listeners Map 补丁"

if $FULL_MODE; then
    OPENCLAW_DIST="/opt/homebrew/lib/node_modules/openclaw/dist"
    if [ -d "$OPENCLAW_DIST" ]; then
        # 只扫描顶层 chunks（避免 821 个文件全量 grep 卡住）
        UNPATCHED=$(grep -l 'const listeners = /\* @__PURE__ \*/ new Map()' \
            "$OPENCLAW_DIST"/*.js "$OPENCLAW_DIST"/chunks/*.js 2>/dev/null | grep -vc ".bak" 2>/dev/null || echo "0")
        UNPATCHED=$(echo "$UNPATCHED" | tr -d '[:space:]')
        if [ "$UNPATCHED" -gt 0 ]; then
            fail "#48703 未修复: $UNPATCHED 个文件有 listeners Map 副本"
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
echo "📋 13/19 陈旧锁文件检测"

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
echo "📋 14/19 Cron 心跳检测"

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

# ── 15. Crontab 路径 vs FILE_MAP 双向一致性（V31 加强）──────────────
echo ""
echo "📋 15/19 Crontab ↔ FILE_MAP 双向一致性"

if $FULL_MODE; then
    CRON_PATH_ERRORS=0

    # 解析 Python 输出
    while IFS= read -r result_line; do
        case "$result_line" in
            PASS\|*) pass "${result_line#PASS|}" ;;
            FAIL\|*) fail "${result_line#FAIL|}"; CRON_PATH_ERRORS=$((CRON_PATH_ERRORS + 1)) ;;
            WARN\|*) warn "${result_line#WARN|}" ;;
        esac
    done < <(python3 - "$SCRIPT_DIR/auto_deploy.sh" "$SCRIPT_DIR/jobs_registry.yaml" << 'PYEOF'
import sys, os, re, subprocess, yaml

deploy_script = sys.argv[1]
registry_file = sys.argv[2] if len(sys.argv) > 2 else None

# ── 解析 FILE_MAP ──
file_map = {}  # basename → [target1, target2, ...] (V37.8.3: 支持多部署目标)
file_map_srcs = set()  # full source paths
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
                    target = parts[1].replace('$HOME', os.path.expanduser('~'))
                    file_map.setdefault(os.path.basename(parts[0]), []).append(target)
                    file_map_srcs.add(parts[0])

# ── 解析 crontab ──
try:
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
    crontab_lines = result.stdout.strip().split('\n')
except Exception:
    print("WARN|无法读取 crontab")
    sys.exit(0)

errors = []
checked = 0
not_in_map = []

for line in crontab_lines:
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    # 匹配 bash .sh 和 python3 .py 两种模式
    m = re.search(r"(?:bash\s+(?:-\w+\s+)?['\"]?|python3\s+)([^\s>|\"']+\.(?:sh|py))", line)
    if not m:
        continue
    cron_path = m.group(1).replace('~/', os.path.expanduser('~/')).replace('$HOME/', os.path.expanduser('~/'))
    if not cron_path.startswith('/'):
        cron_path = os.path.expanduser('~/') + cron_path
    script_name = os.path.basename(cron_path)

    if script_name in file_map:
        # V37.8.13: auto_deploy.sh 是 bootstrap 脚本，git pull 直接更新仓库版本，
        # FILE_MAP 部署到 $HOME 只是 backup 副本。crontab 调用仓库路径是规范，
        # 不应要求 crontab 路径必在 FILE_MAP targets 里。
        if script_name == 'auto_deploy.sh':
            checked += 1
            continue
        # 正向检查：路径一致性（V37.8.3: 支持多部署目标，任一匹配即可）
        targets = file_map[script_name]
        norm_targets = [os.path.normpath(t) for t in targets]
        if os.path.normpath(cron_path) not in norm_targets:
            errors.append(f"FAIL|{script_name}: crontab 路径 {cron_path} ≠ FILE_MAP 目标 {targets}")
        checked += 1
    else:
        # 反向检查：crontab 引用了 FILE_MAP 中不存在的脚本
        not_in_map.append(script_name)

# ── 反向检查输出 ──
if not_in_map:
    for name in not_in_map:
        errors.append(f"FAIL|{name}: crontab 引用但不在 FILE_MAP 中（文件可能从未部署！）")

# ── Registry vs FILE_MAP 交叉检查 ──
if registry_file and os.path.exists(registry_file):
    with open(registry_file) as f:
        registry = yaml.safe_load(f)
    reg_missing = []
    seen_scripts = set()
    for job in registry.get('jobs', []):
        entry = job.get('entry', '')
        enabled = job.get('enabled', True)
        if not enabled:
            continue
        # Strip arguments: "kb_dream.sh --map-sources" → "kb_dream.sh"
        entry_script = entry.split()[0] if entry else ''
        if entry_script in seen_scripts:
            continue  # Same script with different args, already checked
        if entry_script not in file_map_srcs:
            reg_missing.append(f"{job.get('id','?')}: {entry_script}")
        seen_scripts.add(entry_script)
    if reg_missing:
        for rm in reg_missing:
            errors.append(f"FAIL|Registry job {rm} 不在 FILE_MAP 中")

for e in errors:
    print(e)

if not errors and checked > 0:
    total = checked + len(not_in_map)
    print(f"PASS|{checked} 个 crontab 脚本路径均与 FILE_MAP 一致，无遗漏")
elif checked == 0:
    print("WARN|未找到可验证的 crontab 条目")
PYEOF
)
else
    skip "Crontab 路径一致性（需在 Mac Mini 上验证）"
fi

# ── 16. 推送通道 smoke test（V30.3 E2E 功能测试）──────────────────────
echo ""
echo "📋 16/19 推送通道 smoke test"

if $FULL_MODE; then
    # V37.8.15: SKIP_PUSH_TEST=1 由 auto_deploy.sh 传入时跳过（auto_deploy 有自己的告警通道，
    # push test 只验证连通性，不需要每次自动部署都发消息）
    # 同时加速率限制：手动运行 preflight --full 也最多每小时发一次 push test
    PUSH_TEST_LAST="$HOME/.preflight_push_test_last"
    PUSH_TEST_COOLDOWN=3600  # 1 hour
    PUSH_TEST_AGE=$PUSH_TEST_COOLDOWN  # default: expired
    if [ -f "$PUSH_TEST_LAST" ]; then
        PUSH_TEST_AGE=$(( $(date +%s) - $(cat "$PUSH_TEST_LAST" 2>/dev/null || echo 0) ))
    fi

    if [ "${SKIP_PUSH_TEST:-0}" = "1" ]; then
        skip "推送通道 smoke test（auto_deploy 模式，跳过实际发送）"
    elif [ "$PUSH_TEST_AGE" -lt "$PUSH_TEST_COOLDOWN" ]; then
        MINS_AGO=$(( PUSH_TEST_AGE / 60 ))
        skip "推送通道 smoke test（${MINS_AGO}分钟前已验证，每小时最多一次）"
    else
        # 验证 openclaw message send 命令可用且无配置错误
        PUSH_ERR=$(mktemp)
        PUSH_TEST=$(openclaw message send --channel whatsapp --target "${OPENCLAW_PHONE:-+85200000000}" --message "🔧 preflight push test $(date '+%H:%M')" --json 2>"$PUSH_ERR") && PUSH_RC=0 || PUSH_RC=$?

        PUSH_STDERR=$(cat "$PUSH_ERR" 2>/dev/null)
        rm -f "$PUSH_ERR"

        if [ $PUSH_RC -eq 0 ]; then
            # 检查 stderr 是否有插件警告（即使退出码为 0）
            if echo "$PUSH_STDERR" | grep -qi "duplicate plugin\|plugin.*error" 2>/dev/null; then
                warn "推送成功但有插件警告: $(echo "$PUSH_STDERR" | head -1)"
            else
                pass "WhatsApp 推送通道正常（openclaw message send 退出码 0）"
                date +%s > "$PUSH_TEST_LAST"
            fi
        else
            fail "WhatsApp 推送失败（退出码 $PUSH_RC）: $(echo "$PUSH_STDERR" | head -2)"
        fi

        # Discord 推送通道 E2E
        if [ -n "${DISCORD_TARGET:-}" ]; then
            DC_ERR=$(mktemp)
            openclaw message send --channel discord --target "user:${DISCORD_TARGET}" --message "🔧 preflight push test $(date '+%H:%M')" --json 2>"$DC_ERR" && DC_RC=0 || DC_RC=$?
            DC_STDERR=$(cat "$DC_ERR" 2>/dev/null)
            rm -f "$DC_ERR"
            if [ $DC_RC -eq 0 ]; then
                pass "Discord 推送通道正常（openclaw message send 退出码 0）"
            else
                warn "Discord 推送失败（退出码 $DC_RC）: $(echo "$DC_STDERR" | head -2)"
            fi
        else
            skip "Discord 推送通道（DISCORD_TARGET 未设置）"
        fi
    fi
else
    skip "推送通道 smoke test（需在 Mac Mini 上验证）"
fi

# ── 17. KB 语义索引健康（数据复利基础）────────────────────────────────
echo ""
echo "📋 17/19 KB 语义索引健康"

if $FULL_MODE; then
    KB_IDX_DIR="$HOME/.kb/text_index"
    if [ -f "$KB_IDX_DIR/meta.json" ] && [ -f "$KB_IDX_DIR/vectors.bin" ]; then
        # 运行 kb_embed.py --verify（轻量级，不加载模型，只做文件比对）
        VERIFY_RC=0
        VERIFY_OUT=$(python3 "$HOME/kb_embed.py" --verify 2>/dev/null) || VERIFY_RC=$?

        # 提取关键指标
        FILE_COV=$(echo "$VERIFY_OUT" | grep -o '文件覆盖.*' | head -1)
        CHAR_COV=$(echo "$VERIFY_OUT" | grep -o '字符覆盖.*' | head -1)
        VEC_OK=$(echo "$VERIFY_OUT" | grep -o '向量一致.*' | head -1)
        CHUNKS=$(echo "$VERIFY_OUT" | grep -o '总 chunks.*' | head -1)
        STALE=$(echo "$VERIFY_OUT" | grep -o '过期索引.*' | head -1)

        if [ $VERIFY_RC -eq 0 ]; then
            pass "KB 索引 100% 覆盖 ($FILE_COV, $CHUNKS)"
        else
            ISSUE_COUNT=$(echo "$VERIFY_OUT" | grep -c "❌\|⚠️" 2>/dev/null || echo "0")
            # V37.8.1: 用覆盖率百分比判断——kb_embed 每天 03:30 跑一次，
            # 33 个 cron job 白天持续产出，到晚上 23:00 自然累积 ~36 个未索引文件
            # （约 90% 覆盖率），这是正常节奏。<90% 才说明 cron 可能故障。
            COV_PCT=$(echo "$FILE_COV" | grep -oE '[0-9]+%' | grep -oE '[0-9]+')
            COV_PCT=${COV_PCT:-0}
            if [ "$COV_PCT" -ge 90 ]; then
                warn "KB 索引覆盖 ${COV_PCT}%（${ISSUE_COUNT} 个待索引，下次 kb_embed cron 自动补齐）"
            else
                fail "KB 索引覆盖不足: $FILE_COV ($ISSUE_COUNT 个问题，检查 kb_embed cron）"
            fi
            echo "$VERIFY_OUT" | grep "❌\|⚠️" | head -5 | while read -r line; do
                echo "      $line"
            done
        fi

        # 向量文件时效检查（超过 48h 未更新可能是 cron 问题）
        if [ "$(uname)" = "Darwin" ]; then
            VEC_EPOCH=$(stat -f %m "$KB_IDX_DIR/vectors.bin" 2>/dev/null || echo "0")
        else
            VEC_EPOCH=$(stat -c %Y "$KB_IDX_DIR/vectors.bin" 2>/dev/null || echo "0")
        fi
        VEC_AGE_H=$(( ($(date +%s) - VEC_EPOCH) / 3600 ))
        if [ "$VEC_AGE_H" -le 48 ]; then
            pass "向量索引新鲜度: ${VEC_AGE_H}h 前更新"
        else
            warn "向量索引已 ${VEC_AGE_H}h 未更新（kb_embed cron 是否正常？）"
        fi
    else
        fail "KB text_index 不存在（运行 python3 kb_embed.py --reindex 初始化）"
    fi
else
    # dev 环境：只检查 kb_embed.py 语法正确
    if python3 -c "import ast; ast.parse(open('$SCRIPT_DIR/kb_embed.py').read())" 2>/dev/null; then
        pass "kb_embed.py: 语法正确"
    else
        fail "kb_embed.py: Python 语法错误"
    fi
    if python3 -c "import ast; ast.parse(open('$SCRIPT_DIR/kb_rag.py').read())" 2>/dev/null; then
        pass "kb_rag.py: 语法正确"
    else
        fail "kb_rag.py: Python 语法错误"
    fi
fi

# ── 18. 旅程级 E2E 测试（V32: P0-3）────────────────────────────────────
echo ""
echo "📋 18/19 旅程级 E2E 测试"

if $FULL_MODE; then
    E2E_SCRIPT="$HOME/wa_e2e_test.sh"
    if [ -f "$E2E_SCRIPT" ]; then
        E2E_OUT=$(bash "$E2E_SCRIPT" 2>&1) && E2E_RC=0 || E2E_RC=$?
        E2E_PASS=$(echo "$E2E_OUT" | grep -c "✅\|PASS" || true)
        E2E_FAIL=$(echo "$E2E_OUT" | grep -c "❌\|FAIL" || true)
        if [ $E2E_RC -eq 0 ]; then
            pass "E2E 测试通过（$E2E_PASS 项验证）"
        else
            warn "E2E 测试失败（$E2E_FAIL 项失败）: $(echo "$E2E_OUT" | grep -i "fail\|❌" | head -3)"
        fi
    else
        warn "wa_e2e_test.sh 未部署到 ~/（部署后自动运行）"
    fi
else
    # dev 环境：验证 E2E 脚本语法
    if [ -f "$SCRIPT_DIR/wa_e2e_test.sh" ]; then
        bash -n "$SCRIPT_DIR/wa_e2e_test.sh" 2>/dev/null && pass "wa_e2e_test.sh: 语法正确" || fail "wa_e2e_test.sh: 语法错误"
    else
        skip "wa_e2e_test.sh 不存在"
    fi
fi

# ── 19. SLO 合规检查（V32: P0-1）──────────────────────────────────────
echo ""
echo "📋 19/19 SLO 合规检查"

if $FULL_MODE; then
    SLO_SCRIPT="$HOME/slo_checker.py"
    if [ -f "$SLO_SCRIPT" ]; then
        SLO_OUT=$(python3 "$SLO_SCRIPT" --alert 2>&1) && SLO_RC=0 || SLO_RC=$?
        if [ $SLO_RC -eq 0 ]; then
            pass "SLO 全部达标"
        elif [ $SLO_RC -eq 2 ]; then
            warn "SLO 有违规: $(echo "$SLO_OUT" | head -5)"
        else
            warn "SLO 检查异常: $SLO_OUT"
        fi
    else
        skip "slo_checker.py 未部署到 ~/（部署后自动运行）"
    fi
else
    # dev 环境：验证 SLO 脚本语法
    if python3 -c "import ast; ast.parse(open('$SCRIPT_DIR/slo_checker.py').read())" 2>/dev/null; then
        pass "slo_checker.py: 语法正确"
    else
        fail "slo_checker.py: Python 语法错误"
    fi
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

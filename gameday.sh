#!/bin/bash
# gameday.sh — 故障演练脚本（V33: GameDay）
#
# 模拟常见故障场景，验证系统降级、告警、快照是否正确触发。
# ⚠️ 仅在 Mac Mini 上运行，会短暂影响服务！
#
# 用法：
#   bash gameday.sh --dry-run     # 只检查前置条件
#   bash gameday.sh --scenario 1  # 运行单个场景
#   bash gameday.sh --all         # 运行全部场景（约 5 分钟）
#
# 场景：
#   1. GPU 不可用（模拟远程 API 超时）
#   2. Proxy 连续错误→断路器→自动恢复
#   3. 故障快照自动触发
#   4. SLO 检查器违规检测
#   5. Watchdog 告警流水线
set -uo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

PROXY_URL="http://localhost:5002"
ADAPTER_URL="http://localhost:5001"

PASS=0
FAIL=0
SKIP=0
DRY_RUN=false
SCENARIO=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --all) SCENARIO="all" ;;
        --scenario) : ;;
        [1-5]) SCENARIO="$arg" ;;
    esac
done

TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')"
echo "=== GameDay 故障演练 $TS ==="
echo ""

pass() { echo "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }
skip() { echo "  ⏭  $1"; SKIP=$((SKIP + 1)); }

# ── 前置条件 ──────────────────────────────────────────────────────
echo "📋 前置条件检查"

PROXY_OK=false
ADAPTER_OK=false

if curl -s --max-time 5 "$PROXY_URL/health" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') else 1)" 2>/dev/null; then
    pass "Proxy :5002 健康"
    PROXY_OK=true
else
    fail "Proxy :5002 不可达"
fi

if curl -s --max-time 5 "$ADAPTER_URL/health" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') else 1)" 2>/dev/null; then
    pass "Adapter :5001 健康"
    ADAPTER_OK=true
else
    fail "Adapter :5001 不可达"
fi

if [ -f "$HOME/incident_snapshot.py" ]; then
    pass "incident_snapshot.py 已部署"
else
    fail "incident_snapshot.py 未部署"
fi

if [ -f "$HOME/slo_checker.py" ]; then
    pass "slo_checker.py 已部署"
else
    fail "slo_checker.py 未部署"
fi

if $DRY_RUN; then
    echo ""
    echo "⏭  --dry-run 模式，跳过实际演练"
    echo "═══════════════════════════════════════"
    echo "  通过: $PASS | 失败: $FAIL | 跳过: $SKIP"
    echo "═══════════════════════════════════════"
    exit 0
fi

if [ -z "$SCENARIO" ]; then
    echo ""
    echo "用法: bash gameday.sh --scenario <1-5> | --all | --dry-run"
    echo ""
    echo "场景:"
    echo "  1  GPU 不可用（发送请求到不存在的后端）"
    echo "  2  Proxy 连续错误→断路器→自动恢复验证"
    echo "  3  故障快照手动触发 + 验证"
    echo "  4  SLO 检查器验证"
    echo "  5  Watchdog 告警流水线验证"
    exit 0
fi

run_scenario() {
    local num="$1"
    case "$num" in
    1)
        # ── 场景 1：GPU 不可用（模拟超时）──
        echo ""
        echo "🔥 场景 1/5: GPU 不可用（模拟请求超时）"
        echo "  发送请求到 Proxy，超短超时模拟 GPU 无响应..."

        # 发送一个请求，用 2 秒超时（正常需 10-30 秒）
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
            "$PROXY_URL/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d '{"model":"qwen","messages":[{"role":"user","content":"gameday test"}],"max_tokens":5}' \
            2>/dev/null) || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "000" ] || [ "$HTTP_CODE" -ge 500 ] 2>/dev/null; then
            pass "超时/错误正确返回 (HTTP $HTTP_CODE)"
        elif [ "$HTTP_CODE" = "200" ]; then
            # 模型太快响应了，这也说明链路正常
            pass "模型正常响应（GPU 可用，降级路径未触发）"
        else
            fail "意外响应 (HTTP $HTTP_CODE)"
        fi

        # V37.9.331: 只声称验证过的事 — 这里能验证的是 stats 文件可读与当前计数,
        # 不是「本次请求已被记录」(记录发生在 backend 响应返回后, 可能晚于此刻数秒
        # 到数十秒)。旧文案「记录正常」是无对账的声称 (原则 #36-4); 且 TOTAL 为空
        # (文件损坏/半截写) 时旧代码照样 pass = 该报警时打绿 (V37.9.322 家族)。
        if [ -f "$HOME/proxy_stats.json" ]; then
            TOTAL=$(python3 -c "import json; print(json.load(open('$HOME/proxy_stats.json')).get('total_requests',0))" 2>/dev/null)
            if [ -n "$TOTAL" ]; then
                pass "proxy_stats 可读（total_requests=${TOTAL}）"
            else
                fail "proxy_stats.json 存在但不可解析（损坏/半截写）"
            fi
        else
            fail "proxy_stats.json 不存在"
        fi
        ;;

    2)
        # ── 场景 2：断路器验证 ──
        echo ""
        echo "🔥 场景 2/5: 断路器状态验证"

        if ! $ADAPTER_OK; then
            skip "Adapter 不可达，跳过断路器检查"
            return
        fi

        # 读取 /health 中的断路器状态
        # V37.9.331: adapter /health 的 circuit_breaker 是**裸字符串** (CircuitBreaker
        # .state() 返回 closed/open/half-open), 且仅在配置了 fallback 链时存在。
        # 修复前这里按 dict 解析 (对字符串调 .get → AttributeError → 输出恒空) →
        # 永远走 skip 分支还把锅甩给「adapter 可能版本较旧」= 声称的断路器验证
        # 在现行 adapter 上从未发生过 (本场景自 V33 起结构性空转)。
        CB_STATE=$(curl -s --max-time 5 "$ADAPTER_URL/health" | python3 -c "
import sys, json
d = json.load(sys.stdin)
cb = d.get('circuit_breaker')
if isinstance(cb, dict):
    print(cb.get('state', 'unknown'))
elif isinstance(cb, str) and cb:
    print(cb)
else:
    print('absent')
" 2>/dev/null)

        if [ "$CB_STATE" = "closed" ]; then
            pass "断路器正常闭合 (state=closed)"
        elif [ "$CB_STATE" = "half-open" ]; then
            pass "断路器半开放（正在尝试恢复）"
        elif [ "$CB_STATE" = "open" ]; then
            fail "断路器已打开！主路径被跳过，所有请求走 fallback"
        elif [ "$CB_STATE" = "absent" ]; then
            skip "未配置 fallback 链（/health 无断路器字段）"
        else
            skip "无法读取断路器状态 (got=${CB_STATE:-empty})"
        fi
        ;;

    3)
        # ── 场景 3：故障快照验证 ──
        echo ""
        echo "🔥 场景 3/5: 故障快照手动触发"

        SNAPSHOT_PATH=$(python3 "$HOME/incident_snapshot.py" --manual "GameDay 演练测试" 2>&1)
        if echo "$SNAPSHOT_PATH" | grep -q "Snapshot saved"; then
            FILE=$(echo "$SNAPSHOT_PATH" | grep -o '/.*\.json')
            pass "快照创建成功: $(basename "$FILE")"

            # 验证快照内容
            # V37.9.331: 改读 V37.9.325 的 logs_meta.resolved (生产端自己的账),
            # 退役按缺失文案子串计数的旧法 — V37.9.325 把缺失日志文案改成
            # 「[no log file found; tried: ...]」后, 旧子串判据不再命中 → 缺失
            # 日志被数成「有内容」(dev 实测: 4/4 全缺失仍报 4)。快照由本场景
            # 现场用当前 incident_snapshot.py 生成, logs_meta 必然在场。
            HAS_LOGS=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
meta = d.get('logs_meta') or {}
has = sum(1 for m in meta.values() if isinstance(m, dict) and m.get('resolved'))
print(has)
" "$FILE" 2>/dev/null)
            if [ -n "$HAS_LOGS" ]; then
                pass "快照日志段: $HAS_LOGS 个真实日志文件被捕获 (logs_meta 对账)"
            else
                fail "快照日志段不可解析（无 logs_meta — incident_snapshot 版本过旧？）"
            fi

            HAS_SERVICES=$(python3 -c "
import json
d = json.load(open('$FILE'))
svcs = d.get('services', {})
ok = sum(1 for v in svcs.values() if v.get('http_code') == '200')
print(f'{ok}/{len(svcs)}')
" 2>/dev/null)
            pass "服务状态快照: $HAS_SERVICES 健康"

            # 清理测试快照
            rm -f "$FILE"
            pass "测试快照已清理"
        else
            fail "快照创建失败: $SNAPSHOT_PATH"
        fi

        # 验证 --list 功能
        LIST_OUT=$(python3 "$HOME/incident_snapshot.py" --list 2>&1)
        if echo "$LIST_OUT" | grep -qE "Time|No snapshots"; then
            pass "快照列表功能正常"
        else
            fail "快照列表异常"
        fi
        ;;

    4)
        # ── 场景 4：SLO 检查器验证 ──
        echo ""
        echo "🔥 场景 4/5: SLO 合规检查"

        if [ ! -f "$HOME/proxy_stats.json" ]; then
            skip "proxy_stats.json 不存在，跳过 SLO 检查"
            return
        fi

        # 检查 proxy_stats.json 是否包含 slo 字段
        HAS_SLO=$(python3 -c "
import json
d = json.load(open('$HOME/proxy_stats.json'))
print('yes' if 'slo' in d else 'no')
" 2>/dev/null)

        if [ "$HAS_SLO" != "yes" ]; then
            skip "proxy_stats.json 无 SLO 数据（proxy 可能需要重启）"
            return
        fi

        # V37.9.67: cmd_and_or_chain → if-then-else (INV-CROSS-OS-001)
        if SLO_OUT=$(python3 "$HOME/slo_checker.py" 2>&1); then SLO_RC=0; else SLO_RC=$?; fi

        if [ $SLO_RC -eq 0 ]; then
            pass "SLO 全部达标"
            # 打印各指标
            echo "$SLO_OUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    for r in d.get('results', []):
        icon = '✅' if r['ok'] else '🔴'
        print(f'      {icon} {r[\"name\"]}: {r[\"value\"]}{r[\"unit\"]} (目标: {r[\"target\"]}{r[\"unit\"]})')
except: pass
" 2>/dev/null
        elif [ $SLO_RC -eq 3 ]; then
            # V37.9.331: rc=3 = V37.9.322 新增「无数据可判」(全部指标样本域为空,
            # 如 proxy 刚重启/当日零流量) — 是 checker 的**诚实结果**非异常。
            # 修复前落 else 分支报 ❌「SLO 检查异常 (rc=3)」= 把设计行为误报成
            # checker 损坏; preflight 已在 V37.9.322 同步 (exit 3 → warn),
            # gameday 这个消费方漏网 = 原则 #31 跨消费者同步缺口。
            skip "SLO 无数据可判（零流量/刚重启, rc=3 为诚实结果非 checker 异常）"
        elif [ $SLO_RC -eq 2 ]; then
            fail "SLO 有违规项"
        else
            fail "SLO 检查异常 (rc=$SLO_RC)"
        fi

        # 验证 --alert 模式
        # V37.9.67: cmd_and_or_chain → if-then-else (INV-CROSS-OS-001)
        # V37.9.331: rc=3 (无数据可判) 与 0/2 同为合法退出码
        if ALERT_OUT=$(python3 "$HOME/slo_checker.py" --alert 2>&1); then ALERT_RC=0; else ALERT_RC=$?; fi
        if [ $ALERT_RC -eq 0 ] || [ $ALERT_RC -eq 2 ] || [ $ALERT_RC -eq 3 ]; then
            pass "SLO --alert 模式正常 (rc=$ALERT_RC)"
        else
            fail "SLO --alert 模式异常"
        fi
        ;;

    5)
        # ── 场景 5：Watchdog 告警流水线验证 ──
        echo ""
        echo "🔥 场景 5/5: Watchdog 告警流水线"

        # 验证 watchdog 脚本语法
        if bash -n "$HOME/job_watchdog.sh" 2>/dev/null; then
            pass "job_watchdog.sh 语法正确"
        else
            fail "job_watchdog.sh 语法错误"
        fi

        # 验证告警文件可写
        ALERT_LOG="$HOME/.openclaw_alerts.log"
        echo "[gameday] $TS test entry" >> "$ALERT_LOG" && {
            pass "告警日志可写 ($ALERT_LOG)"
            # 清理测试行
            sed -i '' '/\[gameday\].*test entry/d' "$ALERT_LOG" 2>/dev/null || true
        } || {
            fail "告警日志不可写"
        }

        # 验证 incidents 目录
        INCIDENTS_DIR="$HOME/.kb/incidents"
        if [ -d "$INCIDENTS_DIR" ] || mkdir -p "$INCIDENTS_DIR" 2>/dev/null; then
            pass "incidents 目录可用 ($INCIDENTS_DIR)"
        else
            fail "incidents 目录不可创建"
        fi

        # 验证通知通道
        OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
        if command -v "$OPENCLAW" >/dev/null 2>&1; then
            pass "openclaw CLI 可用"
        else
            fail "openclaw CLI 不可用"
        fi

        # 验证 cron 心跳
        CANARY="$HOME/.cron_canary"
        if [ -f "$CANARY" ]; then
            AGE=$(( $(date +%s) - $(head -1 "$CANARY" | tr -d '[:space:]') ))
            AGE_MIN=$((AGE / 60))
            if [ "$AGE_MIN" -lt 30 ]; then
                pass "Cron 心跳正常（${AGE_MIN}m 前）"
            else
                fail "Cron 心跳过期（${AGE_MIN}m 前）"
            fi
        else
            fail "Cron 心跳文件不存在"
        fi
        ;;

    *)
        echo "未知场景: $num"
        ;;
    esac
}

# ── 执行场景 ──────────────────────────────────────────────────────
echo ""
if [ "$SCENARIO" = "all" ]; then
    for i in 1 2 3 4 5; do
        run_scenario "$i"
    done
else
    run_scenario "$SCENARIO"
fi

# ── 汇总 ──────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  通过: $PASS | 失败: $FAIL | 跳过: $SKIP"
echo "═══════════════════════════════════════"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "⚠️  部分演练未通过——检查上述失败项"
    exit 1
else
    echo ""
    echo "✅ GameDay 演练全部通过"
    exit 0
fi

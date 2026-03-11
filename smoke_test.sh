#!/bin/bash
# smoke_test.sh — 端到端连通性 smoke test（不走完整 LLM 推理）
# 用法：bash smoke_test.sh
# 检查 5002→5001→远程GPU 链路通畅，以及本地单测
# 遵循原则：健康检查只检目标组件，不走完整 LLM 链路
set -euo pipefail

PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    local result="$2"
    local expected="${3:-0}"
    if [ "$result" -eq "$expected" ]; then
        echo "  ✅ $name"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $name (exit=$result)"
        FAIL=$((FAIL + 1))
    fi
}

warn() {
    local name="$1"
    local msg="$2"
    echo "  ⚠️  $name: $msg"
    WARN=$((WARN + 1))
}

echo "=== OpenClaw Smoke Test ==="
echo ""

# --- 1. 本地单测 ---
echo "📋 1/5 单测"
python3 test_tool_proxy.py > /dev/null 2>&1 && RC=0 || RC=$?
check "proxy_filters 单测 (test_tool_proxy.py)" $RC

python3 test_check_registry.py > /dev/null 2>&1 && RC=0 || RC=$?
check "registry 校验器单测 (test_check_registry.py)" $RC

# --- 2. 注册表校验 ---
echo ""
echo "📋 2/5 注册表校验"
python3 check_registry.py > /dev/null 2>&1 && RC=0 || RC=$?
check "jobs_registry.yaml 校验" $RC

# --- 3. 文档漂移检测 ---
echo ""
echo "📋 3/5 文档漂移检测"
python3 gen_jobs_doc.py --check > /dev/null 2>&1 && RC=0 || RC=$?
if [ $RC -ne 0 ]; then
    warn "文档漂移检测" "docs/config.md 与 registry 存在不一致（运行 python3 gen_jobs_doc.py --check 查看详情）"
else
    check "文档漂移检测" $RC
fi

# --- 4. Tool Proxy 连通性 ---
echo ""
echo "📋 4/5 Tool Proxy 健康检查 (localhost:5002)"
HEALTH_RESP=$(curl -s --max-time 5 http://localhost:5002/health 2>/dev/null) && RC=0 || RC=$?
if [ $RC -ne 0 ]; then
    warn "Proxy 5002" "无法连接（服务未启动或不在本机运行）"
else
    check "Proxy 5002 /health 端点" $RC
    # 检查响应中是否包含 OK 或 status
    if echo "$HEALTH_RESP" | grep -qi "ok\|status\|healthy" > /dev/null 2>&1; then
        check "Proxy 5002 健康响应" 0
    else
        warn "Proxy 5002" "响应内容异常: ${HEALTH_RESP:0:100}"
    fi
fi

# --- 5. Adapter 连通性 ---
echo ""
echo "📋 5/5 Adapter 健康检查 (localhost:5001)"
curl -s --max-time 5 http://localhost:5001/v1/models > /dev/null 2>&1 && RC=0 || RC=$?
if [ $RC -ne 0 ]; then
    warn "Adapter 5001" "无法连接（服务未启动或不在本机运行）"
else
    check "Adapter 5001 连通性" $RC
fi

# --- 汇总 ---
echo ""
echo "=== 结果汇总 ==="
echo "  通过: $PASS | 失败: $FAIL | 警告: $WARN"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "❌ FAILED: $FAIL 项检查未通过"
    exit 1
elif [ $WARN -gt 0 ]; then
    echo ""
    echo "⚠️  WARN: 全部通过但有 $WARN 条警告（网络检查需在 Mac Mini 上运行）"
    exit 0
else
    echo ""
    echo "✅ ALL PASSED"
    exit 0
fi

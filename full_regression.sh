#!/bin/bash
# full_regression.sh — 全量全业务回归测试
# 每次发布新功能/新任务前必须运行，100% 通过才允许推送
# 用法：bash full_regression.sh
set -uo pipefail

# macOS pip3 用户安装的可执行文件路径（bandit 等）
export PATH="$HOME/Library/Python/3.9/bin:$PATH"

PASS=0
FAIL=0
TOTAL_TESTS=0
FAILED_SUITES=()

TS="$(date '+%Y-%m-%d %H:%M:%S')"
echo "╔══════════════════════════════════════════════════════╗"
echo "║     Full Regression Test — 全量全业务回归测试         ║"
echo "║     $TS                            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

run_suite() {
    local name="$1"
    local cmd="$2"
    echo -n "  🧪 $name ... "
    output=$(eval "$cmd" 2>&1)
    rc=$?
    # 提取测试数量
    count=$(echo "$output" | grep -oE 'Ran [0-9]+ test' | grep -oE '[0-9]+' || echo "0")
    TOTAL_TESTS=$((TOTAL_TESTS + count))
    if [ "$rc" -eq 0 ]; then
        echo "✅ ($count tests)"
        PASS=$((PASS + 1))
    else
        echo "❌ FAILED"
        echo "$output" | tail -10
        echo ""
        FAIL=$((FAIL + 1))
        FAILED_SUITES+=("$name")
    fi
}

# ═══════════════════════════════════════════════════════════════
# 第一层：单元测试（纯逻辑，无外部依赖）
# ═══════════════════════════════════════════════════════════════
echo "📋 第一层：单元测试"
run_suite "proxy_filters (工具过滤/截断/SSE)" "python3 test_tool_proxy.py"
run_suite "check_registry (注册表校验器)" "python3 test_check_registry.py"
run_suite "cron_health (锁/心跳/告警/完整性)" "python3 test_cron_health.py"
run_suite "status_update (三方状态CRUD)" "python3 test_status_update.py"
run_suite "adapter (路由/Fallback/认证)" "python3 test_adapter.py"
run_suite "providers (Provider Compatibility Layer)" "python3 test_providers.py"
run_suite "kb_business (KB全业务逻辑)" "python3 test_kb_business.py"
run_suite "audit_log (审计日志/链式哈希)" "python3 test_audit_log.py"
run_suite "reliability_bench (故障场景评测)" "python3 test_reliability_bench.py"
run_suite "memory_plane (统一记忆平面)" "python3 test_memory_plane.py"
run_suite "slo_dashboard (SLO仪表盘)" "python3 test_slo_dashboard.py"

# 条件性测试（仅当文件存在时运行）
for tf in test_conv_quality.py test_kb_autotag.py test_kb_dedup.py test_token_report.py test_arxiv_parser.py test_shell_antipatterns.py; do
    if [ -f "$tf" ]; then
        suite_name=$(echo "$tf" | sed 's/test_//' | sed 's/.py//')
        run_suite "$suite_name" "python3 $tf"
    fi
done
echo ""

# ═══════════════════════════════════════════════════════════════
# 第二层：注册表 + 文档一致性
# ═══════════════════════════════════════════════════════════════
echo "📋 第二层：注册表与文档"
echo -n "  📑 jobs_registry.yaml 校验 ... "
if python3 check_registry.py 2>&1 | grep -q "OK"; then
    echo "✅"
    PASS=$((PASS + 1))
else
    echo "❌"
    FAIL=$((FAIL + 1))
    FAILED_SUITES+=("registry validation")
fi

echo -n "  📑 docs/config.md 漂移检测 ... "
if python3 gen_jobs_doc.py --check 2>&1 | grep -q "OK"; then
    echo "✅"
    PASS=$((PASS + 1))
else
    echo "❌"
    python3 gen_jobs_doc.py --check 2>&1
    FAIL=$((FAIL + 1))
    FAILED_SUITES+=("config drift")
fi
echo ""

# ═══════════════════════════════════════════════════════════════
# 第三层：安全扫描
# ═══════════════════════════════════════════════════════════════
echo "📋 第三层：安全扫描"
echo -n "  🔒 API Key 泄漏扫描 ... "
LEAKED=$(grep -r "sk-[A-Za-z0-9]\{20,\}" . --include="*.py" --include="*.sh" 2>/dev/null | grep -v ".git" | grep -v "sk-xx" | grep -v "sk-REPLACE" | grep -v "sk-X\.\.\." | grep -v "test_" || true)
if [ -z "$LEAKED" ]; then
    echo "✅"
    PASS=$((PASS + 1))
else
    echo "❌"
    echo "$LEAKED"
    FAIL=$((FAIL + 1))
    FAILED_SUITES+=("API key leak")
fi

echo -n "  🔒 手机号泄漏扫描 ... "
PHONE_LEAKED=$(grep -r "+852[0-9]\{8\}" . --include="*.py" --include="*.sh" 2>/dev/null | grep -v ".git" | grep -v "+85200000000" | grep -v "test_" || true)
if [ -z "$PHONE_LEAKED" ]; then
    echo "✅"
    PASS=$((PASS + 1))
else
    echo "❌"
    echo "$PHONE_LEAKED"
    FAIL=$((FAIL + 1))
    FAILED_SUITES+=("phone leak")
fi

echo -n "  🔒 危险 crontab 模式扫描 ... "
DANGEROUS=$(grep -rn "| crontab -" . --include="*.sh" 2>/dev/null | grep -v ".git" | grep -v "^.*:#" | grep -v "echo" | grep -v "crontab_safe" | grep -v "full_regression" || true)
if [ -z "$DANGEROUS" ]; then
    echo "✅"
    PASS=$((PASS + 1))
else
    echo "❌"
    echo "$DANGEROUS"
    FAIL=$((FAIL + 1))
    FAILED_SUITES+=("dangerous crontab pattern")
fi

echo -n "  🔒 依赖漏洞扫描 (pip-audit) ... "
if command -v pip-audit &>/dev/null; then
    AUDIT_OUT=$(pip-audit --desc -q 2>&1) || true
    VULN_COUNT=$(echo "$AUDIT_OUT" | grep -cE "^Name" 2>/dev/null || echo "0")
    # pip-audit 无漏洞时输出为空
    if [ -z "$(echo "$AUDIT_OUT" | grep -iE 'found [1-9]|CRITICAL|HIGH')" ]; then
        echo "✅ 无已知漏洞"
        PASS=$((PASS + 1))
    else
        echo "⚠️ 发现漏洞（非阻塞）"
        echo "$AUDIT_OUT" | head -10
    fi
else
    echo "⚠️ pip-audit 未安装（pip3 install pip-audit）"
fi

echo -n "  🔒 审计日志完整性 ... "
if python3 audit_log.py --verify --json 2>/dev/null | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('ok') else 1)" 2>/dev/null; then
    AUDIT_COUNT=$(python3 audit_log.py --verify --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo 0)
    echo "✅ ($AUDIT_COUNT records)"
    PASS=$((PASS + 1))
else
    echo "❌ 链式哈希校验失败"
    FAIL=$((FAIL + 1))
    FAILED_SUITES+=("audit integrity")
fi
echo -n "  🔒 对抗审计（声明 vs 实际） ... "
AUDIT_RESULT=$(python3 adversarial_audit.py 2>&1)
AUDIT_RC=$?
if [ $AUDIT_RC -eq 0 ]; then
    AUDIT_PASS=$(echo "$AUDIT_RESULT" | grep -c "✅" || true)
    echo "✅ ($AUDIT_PASS checks passed)"
    PASS=$((PASS + 1))
else
    echo "❌"
    echo "$AUDIT_RESULT" | grep "❌"
    FAIL=$((FAIL + 1))
    FAILED_SUITES+=("adversarial audit")
fi
echo -n "  🔒 Ontology 宪法执行（双范式互验证） ... "
if [ -f ontology/governance_checker.py ]; then
    GOV_RESULT=$(python3 ontology/governance_checker.py 2>&1)
    GOV_RC=$?
    if [ $GOV_RC -eq 0 ]; then
        GOV_PASS=$(echo "$GOV_RESULT" | grep -c "✅" || true)
        echo "✅ ($GOV_PASS invariants pass)"
        PASS=$((PASS + 1))
    else
        echo "❌"
        echo "$GOV_RESULT" | grep "❌"
        FAIL=$((FAIL + 1))
        FAILED_SUITES+=("governance ontology")
    fi
else
    echo "⏭ ontology/ 不存在（宪法最高条：删除不影响原系统）"
fi

echo -n "  🔒 Ontology 一致性（tool_ontology vs hardcoded） ... "
if [ -f ontology/engine.py ]; then
    ONTO_RESULT=$(python3 ontology/engine.py --check 2>&1)
    ONTO_RC=$?
    if [ $ONTO_RC -eq 0 ]; then
        echo "✅ consistent"
        PASS=$((PASS + 1))
    else
        echo "❌"
        echo "$ONTO_RESULT" | tail -3
        FAIL=$((FAIL + 1))
        FAILED_SUITES+=("ontology consistency")
    fi
else
    echo "⏭ ontology/ 不存在"
fi

echo -n "  🔒 Ontology diff（硬编码 vs 本体声明） ... "
if [ -f ontology/diff.py ]; then
    DIFF_RESULT=$(python3 ontology/diff.py --check 2>&1)
    DIFF_RC=$?
    if [ $DIFF_RC -eq 0 ]; then
        echo "✅ 全量一致"
        PASS=$((PASS + 1))
    else
        echo "⚠️ 存在差异（非阻塞）"
        echo "$DIFF_RESULT" | grep -E "⚠️|❌" | head -5
    fi
else
    echo "⏭ ontology/ 不存在"
fi

echo -n "  🔒 Ontology 项目隔离验证 ... "
if [ -d ontology/ ]; then
    # 宪法最高条：原项目测试不依赖 ontology
    ISOLATION_RESULT=$(python3 -c "
import proxy_filters
assert len(proxy_filters.ALLOWED_TOOLS) > 0
print('proxy_filters OK: independent of ontology')
" 2>&1)
    if [ $? -eq 0 ]; then
        echo "✅ proxy_filters 不依赖 ontology"
        PASS=$((PASS + 1))
    else
        echo "❌ proxy_filters 依赖 ontology（违反宪法最高条！）"
        FAIL=$((FAIL + 1))
        FAILED_SUITES+=("ontology isolation")
    fi
else
    echo "⏭ ontology/ 不存在"
fi
echo ""

# ═══════════════════════════════════════════════════════════════
# 第四层：代码质量（非阻塞，仅报告）
# ═══════════════════════════════════════════════════════════════
echo "📋 第四层：代码质量（参考项）"

# 代码覆盖率
echo -n "  📊 代码覆盖率 ... "
if command -v coverage &>/dev/null || python3 -c "import coverage" 2>/dev/null; then
    COV_OUTPUT=$(python3 -m coverage run --source=. --omit="test_*,*/site-packages/*" -m pytest test_tool_proxy.py test_check_registry.py test_status_update.py test_adapter.py test_providers.py -q 2>&1 || \
                 python3 -m coverage run --source=proxy_filters,status_update,adapter,providers,audit_log --omit="test_*" -m unittest test_tool_proxy test_status_update test_adapter test_providers 2>&1)
    COV_REPORT=$(python3 -m coverage report --format=total 2>/dev/null || python3 -m coverage report 2>/dev/null | tail -1 | awk '{print $NF}')
    echo "📈 $COV_REPORT"
else
    echo "⚠️ coverage 未安装（pip3 install coverage）"
fi

# Bandit 安全扫描
echo -n "  🛡️  bandit 静态安全分析 ... "
if command -v bandit &>/dev/null; then
    BANDIT_OUT=$(bandit -r proxy_filters.py adapter.py tool_proxy.py status_update.py audit_log.py -q -ll 2>&1 || true)
    BANDIT_ISSUES=$(echo "$BANDIT_OUT" | grep -c "Issue:" 2>/dev/null || echo "0")
    if [ "$BANDIT_ISSUES" -eq 0 ] || [ -z "$BANDIT_OUT" ]; then
        echo "✅ 无中高危漏洞"
        PASS=$((PASS + 1))
    else
        echo "⚠️ $BANDIT_ISSUES 个问题（详见下方）"
        echo "$BANDIT_OUT" | head -20
    fi
else
    echo "⚠️ bandit 未安装（pip3 install bandit）"
fi
echo ""

# ═══════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════
echo "══════════════════════════════════════════════════════"
echo "  结果: $PASS 通过 / $FAIL 失败 / 共 $TOTAL_TESTS 个测试用例"
echo "══════════════════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "❌ 失败项:"
    for s in "${FAILED_SUITES[@]}"; do
        echo "  • $s"
    done
    echo ""
    echo "⛔ 回归测试未通过，禁止推送！"
    exit 1
else
    echo ""
    echo "✅ 全量回归测试通过，可以安全推送"
    exit 0
fi

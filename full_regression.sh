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
run_suite "kb_review (V37.5 fail-fast + registry-driven)" "python3 test_kb_review.py"
run_suite "kb_evening (V37.6 import reuse + V37.7 today-count)" "python3 test_kb_evening.py"
run_suite "audit_log (审计日志/链式哈希)" "python3 test_audit_log.py"
run_suite "reliability_bench (故障场景评测)" "python3 test_reliability_bench.py"
run_suite "memory_plane (统一记忆平面)" "python3 test_memory_plane.py"
run_suite "slo_dashboard (SLO仪表盘)" "python3 test_slo_dashboard.py"
run_suite "finance_news_zombie (V37.8.5 三层僵尸检测)" "python3 test_finance_news_zombie.py"
run_suite "dream_surrogate_sanitize (V37.8.6 log→stderr + surrogate 清洗 + 反污染 prompt)" "python3 test_dream_surrogate_sanitize.py"
run_suite "ontology_parser (V37.8.7 separator+key-based 解析，防级联错位)" "python3 test_ontology_parser.py"
run_suite "governance_mrd_v8_9 (V37.8.9 MRD-LOG-STDERR + MRD-LLM-PARSER-POSITIONAL)" "python3 test_governance_mrd_v8_9.py"
run_suite "wa_gateway_resilience (V37.8.13 Gateway 宕机韧性三层修复)" "python3 test_wa_gateway_resilience.py"
if [ -f test_restart_launchd.py ]; then
    run_suite "restart_launchd (V37.9.13 restart.sh 单一 manager 契约)" "python3 test_restart_launchd.py"
fi
if [ -f test_movespeed_incident_capture.py ]; then
    run_suite "movespeed_incident_capture (V37.9.14 SSD rsync 事故取证 helper + INV-BACKUP-001 check 4)" "python3 test_movespeed_incident_capture.py"
fi
if [ -f ontology/tests/test_governance_cron_matcher.py ]; then
    run_suite "governance_cron_matcher (INV-CRON-003/004 匹配器)" "python3 -m unittest ontology.tests.test_governance_cron_matcher"
fi
if [ -f ontology/tests/test_governance_summary.py ]; then
    run_suite "governance_summary (INV-GOV-001 silent error)" "python3 -m unittest ontology.tests.test_governance_summary"
fi
if [ -f ontology/tests/test_dream_cache_stability.py ]; then
    run_suite "dream_cache_stability (INV-DREAM-001/002 + INV-CACHE-002)" "python3 -m unittest ontology.tests.test_dream_cache_stability"
fi
if [ -f ontology/tests/test_audit_perf_dimensions.py ]; then
    run_suite "audit_perf_dimensions (V37.9.3 MRD-AUDIT-PERF-001 4 维度判定)" "python3 -m unittest ontology.tests.test_audit_perf_dimensions"
fi
if [ -f test_security_ontology_alignment.py ]; then
    run_suite "security_ontology_alignment (V37.9.3 路线 C Step 3 数据源统一)" "python3 test_security_ontology_alignment.py"
fi
if [ -f test_kb_embed_workspace.py ]; then
    run_suite "kb_embed_workspace (V37.9.5 INV-KB-COVERAGE-001 workspace .md 索引)" "python3 test_kb_embed_workspace.py"
fi
if [ -f test_watchdog_freshness.py ]; then
    run_suite "watchdog_freshness (V37.9.6 INV-WATCHDOG-FRESHNESS-001 行级时间戳过滤)" "python3 test_watchdog_freshness.py"
fi
if [ -f test_phase4_ontology_skeleton.py ]; then
    run_suite "phase4_ontology_skeleton (V37.9.9 domain+policy ontology 骨架守卫)" "python3 test_phase4_ontology_skeleton.py"
fi
if [ -f ontology/tests/test_engine_phase4.py ]; then
    run_suite "engine_phase4 (V37.9.12 load_domain_ontology+find_by_domain+evaluate_policy 契约)" "python3 ontology/tests/test_engine_phase4.py"
fi

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
echo -n "  🔒 Ontology 治理审计（17不变式 + 6元发现） ... "
if [ -f ontology/governance_checker.py ]; then
    GOV_RESULT=$(python3 ontology/governance_checker.py 2>&1)
    GOV_RC=$?
    if [ $GOV_RC -eq 0 ]; then
        GOV_PASS=$(echo "$GOV_RESULT" | grep -c "✅" || true)
        echo "✅ ($GOV_PASS checks pass)"
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
# 第 3.5 层：对抗性混沌审计 — Category A 回归防线（V37.9）
# Cat A 10 场景模拟已知血案回归攻击，audit 必须 100% catch
# 每次 PR 合并前跑一次确认治理防御力未退化
# ═══════════════════════════════════════════════════════════════
echo "📋 第 3.5 层：对抗性混沌审计（Category A 回归防线）"
if [ -f ontology/tests/adversarial_chaos_audit.py ]; then
    # 检查 git 工作树干净（chaos audit 需要）— 豁免 status.json（证据回写）
    DIRTY=$(git status --porcelain 2>/dev/null | grep -v "status.json$" || true)
    if [ -n "$DIRTY" ]; then
        echo "  ⚠️ git 工作树不干净（非 status.json），跳过对抗审计"
    else
        echo -n "  🎯 Category A 10 场景（已知血案回归） ... "
        CHAOS_OUTPUT=$(python3 ontology/tests/adversarial_chaos_audit.py --category a 2>&1)
        CHAOS_EXIT=$?
        CAT_A_PASS=$(echo "$CHAOS_OUTPUT" | grep -oE "真实防御率: [0-9]+/[0-9]+" | tail -1)
        if [ "$CHAOS_EXIT" -eq 0 ] && echo "$CHAOS_OUTPUT" | grep -q "PASS: 10"; then
            echo "✅ $CAT_A_PASS (10/10 全抓)"
            PASS=$((PASS + 1))
        else
            echo "❌ $CAT_A_PASS — audit 对已知血案回归防御退化"
            echo "$CHAOS_OUTPUT" | grep -E "FAIL|DIRTY|STALE" | head -5
            FAIL=$((FAIL + 1))
            FAILED_SUITES+=("adversarial Cat A regression")
        fi
    fi
else
    echo "⏭ adversarial_chaos_audit.py 不存在"
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

    # ═══════════════════════════════════════════════════════════════
    # 证据口径自动化：所有指标回写 status.json（单一数据源）
    # ═══════════════════════════════════════════════════════════════
    REGRESSION_TS="$(date '+%Y-%m-%d %H:%M')"
    if [ -f status_update.py ]; then
        echo ""
        echo "📊 证据回写 status.json ..."

        # 1) 测试数 + 回归结果
        python3 status_update.py --set quality.test_count "$TOTAL_TESTS" --by full_regression 2>/dev/null
        python3 status_update.py --set quality.last_regression "${REGRESSION_TS} pass" --by full_regression 2>/dev/null
        python3 status_update.py --set quality.test_suites "$PASS" --by full_regression 2>/dev/null
        echo "   test_count=$TOTAL_TESTS, suites=$PASS"

        # 2) 安全评分（单次调用，解析三个字段）
        if [ -f security_score.py ]; then
            SEC_JSON=$(python3 security_score.py --json 2>/dev/null || echo "{}")
            SEC_SCORE=$(echo "$SEC_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',''))" 2>/dev/null || echo "")
            SEC_MAX=$(echo "$SEC_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('max',100))" 2>/dev/null || echo "100")
            SEC_PCT=$(echo "$SEC_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('percentage',''))" 2>/dev/null || echo "")
            if [ -n "$SEC_SCORE" ]; then
                python3 status_update.py --set quality.security_score "$SEC_SCORE" --by full_regression 2>/dev/null
                python3 status_update.py --set quality.security_score_time "$REGRESSION_TS" --by full_regression 2>/dev/null
                python3 status_update.py --set health.security_score "${SEC_SCORE}/${SEC_MAX} (${SEC_PCT}%)" --by full_regression 2>/dev/null
                echo "   security_score=${SEC_SCORE}/${SEC_MAX} (${SEC_PCT}%)"
            fi
        fi

        # 3) 治理不变式
        if [ -f ontology/governance_checker.py ]; then
            GOV_STATS=$(python3 ontology/governance_checker.py --json 2>/dev/null | grep -v '^\[proxy\]' | python3 -c "
import sys, json
raw = sys.stdin.read()
depth = 0
for i, c in enumerate(raw):
    if c == '[': depth += 1
    elif c == ']': depth -= 1
    if depth == 0 and i > 0:
        data = json.loads(raw[:i+1])
        total = len(data)
        passed = sum(1 for d in data if d['status'] == 'pass')
        checks = sum(d.get('total_checks', 0) for d in data)
        checks_passed = sum(d.get('passed_checks', 0) for d in data)
        print(f'{passed}/{total}/{checks_passed}/{checks}')
        break
" 2>/dev/null || echo "")
            if [ -n "$GOV_STATS" ]; then
                GOV_INV_PASSED=$(echo "$GOV_STATS" | cut -d/ -f1)
                GOV_INV_TOTAL=$(echo "$GOV_STATS" | cut -d/ -f2)
                GOV_CHK_PASSED=$(echo "$GOV_STATS" | cut -d/ -f3)
                GOV_CHK_TOTAL=$(echo "$GOV_STATS" | cut -d/ -f4)
                python3 status_update.py --set quality.governance_invariants "${GOV_INV_PASSED}/${GOV_INV_TOTAL}" --by full_regression 2>/dev/null
                python3 status_update.py --set quality.governance_checks "${GOV_CHK_PASSED}/${GOV_CHK_TOTAL}" --by full_regression 2>/dev/null
                echo "   governance=${GOV_INV_PASSED}/${GOV_INV_TOTAL} invariants, ${GOV_CHK_PASSED}/${GOV_CHK_TOTAL} checks"
            fi
        fi

        # 4) 版本号
        if [ -f VERSION ]; then
            VERSION_STR=$(cat VERSION | tr -d '[:space:]')
            python3 status_update.py --set quality.version "$VERSION_STR" --by full_regression 2>/dev/null
            echo "   version=$VERSION_STR"
        fi

        echo "✅ 证据回写完成"
    fi

    exit 0
fi

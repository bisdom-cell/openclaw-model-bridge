#!/bin/bash
set -euo pipefail
# quickstart.sh — 10-minute Quick Start for OpenClaw Model Bridge (V35)
#
# One script that checks prerequisites, starts services, verifies health,
# and runs a demo request to produce a golden test trace.
#
# Usage:
#   bash quickstart.sh              # Full setup + demo
#   bash quickstart.sh --check      # Prerequisites check only
#   bash quickstart.sh --demo       # Demo only (services already running)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

# Colors (if terminal supports them)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
WARN=0

ok()   { echo -e "  ${GREEN}✅ $1${NC}"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}❌ $1${NC}"; FAIL=$((FAIL + 1)); }
warn() { echo -e "  ${YELLOW}⚠️  $1${NC}"; WARN=$((WARN + 1)); }
info() { echo -e "  ${BLUE}ℹ️  $1${NC}"; }

# ============================================================================
# Phase 1: Prerequisites Check
# ============================================================================
check_prerequisites() {
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  Phase 1: Prerequisites Check"
    echo "═══════════════════════════════════════════════════════"
    echo ""

    # Python 3
    if command -v python3 &>/dev/null; then
        PYVER=$(python3 --version 2>&1)
        ok "Python 3 installed ($PYVER)"
    else
        fail "Python 3 not found — install Python 3.9+"
    fi

    # No third-party deps needed for core
    ok "Core services: zero third-party dependencies (stdlib only)"

    # REMOTE_API_KEY
    if [ -n "${REMOTE_API_KEY:-}" ] && [ "$REMOTE_API_KEY" != "sk-REPLACE-ME" ]; then
        ok "REMOTE_API_KEY is set"
    else
        fail "REMOTE_API_KEY not set — export REMOTE_API_KEY='your-key-here'"
    fi

    # config.yaml
    if [ -f "$SCRIPT_DIR/config.yaml" ]; then
        ok "config.yaml found"
    else
        fail "config.yaml missing"
    fi

    # Key Python files exist
    for f in adapter.py tool_proxy.py proxy_filters.py config_loader.py providers.py; do
        if [ -f "$SCRIPT_DIR/$f" ]; then
            ok "$f exists"
        else
            fail "$f missing"
        fi
    done

    # Python syntax check on core files
    for f in adapter.py tool_proxy.py proxy_filters.py; do
        python3 -c "import py_compile; py_compile.compile('$SCRIPT_DIR/$f', doraise=True)" 2>/dev/null && \
            ok "$f syntax OK" || fail "$f has syntax errors"
    done

    echo ""
    echo "  Prerequisites: $PASS passed, $FAIL failed, $WARN warnings"
    if [ $FAIL -gt 0 ]; then
        echo ""
        echo -e "  ${RED}Fix the above issues before proceeding.${NC}"
        return 1
    fi
    return 0
}

# ============================================================================
# Phase 2: Start Services
# ============================================================================
start_services() {
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  Phase 2: Start Services"
    echo "═══════════════════════════════════════════════════════"
    echo ""

    # Check if already running
    if curl -s --max-time 2 http://localhost:5002/health &>/dev/null; then
        ok "Tool Proxy (:5002) already running"
        PROXY_RUNNING=1
    else
        PROXY_RUNNING=0
    fi

    if curl -s --max-time 2 http://localhost:5001/v1/models &>/dev/null; then
        ok "Adapter (:5001) already running"
        ADAPTER_RUNNING=1
    else
        ADAPTER_RUNNING=0
    fi

    if [ $PROXY_RUNNING -eq 1 ] && [ $ADAPTER_RUNNING -eq 1 ]; then
        ok "All services already running — skipping start"
        return 0
    fi

    info "Starting services..."

    if [ $ADAPTER_RUNNING -eq 0 ]; then
        lsof -ti :5001 2>/dev/null | xargs kill 2>/dev/null || true
        nohup python3 "$SCRIPT_DIR/adapter.py" > ~/adapter.log 2>&1 &
        info "Adapter starting on :5001..."
        sleep 2
    fi

    if [ $PROXY_RUNNING -eq 0 ]; then
        lsof -ti :5002 2>/dev/null | xargs kill 2>/dev/null || true
        nohup python3 "$SCRIPT_DIR/tool_proxy.py" > ~/tool_proxy.log 2>&1 &
        info "Tool Proxy starting on :5002..."
        sleep 2
    fi

    # Verify
    if curl -s --max-time 5 http://localhost:5001/v1/models &>/dev/null; then
        ok "Adapter (:5001) healthy"
    else
        fail "Adapter (:5001) failed to start — check ~/adapter.log"
    fi

    if curl -s --max-time 5 http://localhost:5002/health &>/dev/null; then
        ok "Tool Proxy (:5002) healthy"
    else
        fail "Tool Proxy (:5002) failed to start — check ~/tool_proxy.log"
    fi
}

# ============================================================================
# Phase 3: Health Verification
# ============================================================================
verify_health() {
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  Phase 3: Health Verification"
    echo "═══════════════════════════════════════════════════════"
    echo ""

    # Proxy health (cascade: proxy → adapter → remote GPU)
    HEALTH=$(curl -s --max-time 5 http://localhost:5002/health 2>/dev/null) || HEALTH=""
    if [ -n "$HEALTH" ]; then
        ok "Proxy /health responds"
        echo ""
        echo "  Response:"
        echo "  $HEALTH" | python3 -m json.tool 2>/dev/null || echo "  $HEALTH"
        echo ""
    else
        fail "Proxy /health not responding"
    fi

    # Adapter models endpoint
    MODELS=$(curl -s --max-time 5 http://localhost:5001/v1/models 2>/dev/null) || MODELS=""
    if [ -n "$MODELS" ]; then
        ok "Adapter /v1/models responds"
        MODEL_ID=$(echo "$MODELS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['data'][0]['id'] if d.get('data') else 'unknown')" 2>/dev/null || echo "parse error")
        info "Active model: $MODEL_ID"
    else
        warn "Adapter /v1/models not responding (remote GPU may be down)"
    fi

    # Unit tests
    echo ""
    info "Running unit tests..."
    TEST_OUTPUT=$(python3 -m unittest discover -s "$SCRIPT_DIR" -p "test_*.py" -q 2>&1) || true
    TEST_LAST=$(echo "$TEST_OUTPUT" | tail -1)
    if echo "$TEST_LAST" | grep -q "^OK"; then
        TEST_COUNT=$(echo "$TEST_OUTPUT" | grep -oP 'Ran \K[0-9]+' || echo "?")
        ok "Unit tests passed ($TEST_COUNT tests)"
    else
        warn "Some tests failed: $TEST_LAST"
    fi

    # Provider compatibility matrix
    if [ -f "$SCRIPT_DIR/providers.py" ]; then
        PROVIDER_COUNT=$(python3 "$SCRIPT_DIR/providers.py" --json 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
        ok "Provider registry: $PROVIDER_COUNT providers registered"
    fi
}

# ============================================================================
# Phase 4: Golden Test Trace (Demo)
# ============================================================================
run_demo() {
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  Phase 4: Golden Test Trace"
    echo "═══════════════════════════════════════════════════════"
    echo ""
    info "Sending a test request through the full stack..."
    info "(Proxy :5002 → Adapter :5001 → Remote GPU → Response)"
    echo ""

    # Build a minimal chat completion request
    DEMO_REQUEST='{
  "model": "qwen-local/auto",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant. Reply in exactly one sentence."},
    {"role": "user", "content": "What is 2+2? Reply in one word."}
  ],
  "max_tokens": 50,
  "stream": false
}'

    echo "  ── Request ──"
    echo "  POST http://localhost:5002/v1/chat/completions"
    echo "  Model: qwen-local/auto"
    echo "  Prompt: 'What is 2+2? Reply in one word.'"
    echo ""

    START_MS=$(python3 -c "import time; print(int(time.time()*1000))")
    RESPONSE=$(curl -s --max-time 60 \
        -X POST http://localhost:5002/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d "$DEMO_REQUEST" 2>/dev/null) || RESPONSE=""
    END_MS=$(python3 -c "import time; print(int(time.time()*1000))")
    ELAPSED=$((END_MS - START_MS))

    if [ -z "$RESPONSE" ]; then
        fail "No response received (timeout or connection refused)"
        echo ""
        echo "  Possible causes:"
        echo "    - Services not running (run: bash restart.sh)"
        echo "    - Remote GPU unreachable"
        echo "    - REMOTE_API_KEY invalid"
        return 1
    fi

    # Parse response
    CONTENT=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    r = json.load(sys.stdin)
    if 'choices' in r:
        print(r['choices'][0]['message']['content'])
    elif 'error' in r:
        print('ERROR: ' + str(r['error']))
    else:
        print(json.dumps(r)[:200])
except Exception as e:
    print('PARSE ERROR: ' + str(e))
" 2>/dev/null || echo "parse error")

    TOKENS=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    r = json.load(sys.stdin)
    u = r.get('usage', {})
    print(f\"prompt={u.get('prompt_tokens', '?')}, completion={u.get('completion_tokens', '?')}, total={u.get('total_tokens', '?')}\")
except:
    print('unknown')
" 2>/dev/null || echo "unknown")

    echo "  ── Response ──"
    echo "  Content: $CONTENT"
    echo "  Tokens: $TOKENS"
    echo "  Latency: ${ELAPSED}ms"
    echo ""

    if echo "$CONTENT" | grep -qi "error\|parse error"; then
        fail "Demo request returned an error"
    else
        ok "Golden test trace completed (${ELAPSED}ms)"
    fi

    # Save golden trace
    TRACE_FILE="$SCRIPT_DIR/docs/golden_trace.json"
    echo "$RESPONSE" | python3 -c "
import json, sys, time
try:
    r = json.load(sys.stdin)
    trace = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'latency_ms': $ELAPSED,
        'request': {
            'model': 'qwen-local/auto',
            'prompt': 'What is 2+2? Reply in one word.',
            'max_tokens': 50,
        },
        'response': {
            'content': r.get('choices', [{}])[0].get('message', {}).get('content', ''),
            'tokens': r.get('usage', {}),
            'model': r.get('model', ''),
        },
        'path': 'Proxy(:5002) → Adapter(:5001) → Remote GPU → Response',
    }
    with open('$TRACE_FILE', 'w') as f:
        json.dump(trace, f, indent=2, ensure_ascii=False)
    print('  Trace saved to docs/golden_trace.json')
except Exception as e:
    print(f'  Could not save trace: {e}')
" 2>/dev/null || warn "Could not save golden trace"
}

# ============================================================================
# Phase 5: Summary
# ============================================================================
print_summary() {
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  Summary"
    echo "═══════════════════════════════════════════════════════"
    echo ""
    echo "  Passed: $PASS | Failed: $FAIL | Warnings: $WARN"
    echo ""

    if [ $FAIL -eq 0 ]; then
        echo -e "  ${GREEN}🎉 Quick Start complete! System is operational.${NC}"
    else
        echo -e "  ${RED}⚠️  $FAIL checks failed. Review the output above.${NC}"
    fi

    echo ""
    echo "  Next steps:"
    echo "    • Point OpenClaw Gateway to http://localhost:5002 (proxy)"
    echo "    • Run 'bash smoke_test.sh' for full connectivity check"
    echo "    • Run 'python3 slo_benchmark.py' for SLO metrics report"
    echo "    • Run 'python3 providers.py' for provider compatibility matrix"
    echo "    • See docs/GUIDE.md for full integration guide"
    echo ""
}

# ============================================================================
# Main
# ============================================================================
echo ""
echo "╔═══════════════════════════════════════════════════════╗"
echo "║  OpenClaw Model Bridge — Quick Start                 ║"
echo "║  Runtime Control Plane for Tool-Calling Agents       ║"
echo "╚═══════════════════════════════════════════════════════╝"

case "${1:-full}" in
    --check)
        check_prerequisites
        ;;
    --demo)
        run_demo
        print_summary
        ;;
    full|*)
        check_prerequisites || exit 1
        start_services
        verify_health
        run_demo
        print_summary
        ;;
esac

exit $FAIL

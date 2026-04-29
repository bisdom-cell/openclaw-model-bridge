#!/bin/bash
# check_upgrade.sh — OpenClaw 升级 tripwire 监控 + 就绪检查
# V37.9.22: 6 条 tripwire 替代"看到新版本就评估"模式 — 见 docs/gateway_upgrade_eval_v2026.4.md 第十二节
# 用法：bash check_upgrade.sh
set -euo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

# ── 配置（升级后须更新 LAST_EVAL_DATE）──
LAST_EVAL_DATE="${OPENCLAW_LAST_EVAL_DATE:-2026-04-29}"
TIME_TRIPWIRE_DAYS="${OPENCLAW_TIME_TRIPWIRE_DAYS:-180}"
VERSION_GAP_TRIPWIRE="${OPENCLAW_VERSION_GAP_TRIPWIRE:-50}"
CVE_FILE="${OPENCLAW_CVE_ALERT_FILE:-$HOME/.openclaw_cve_alert}"
PAIN_FILE="${OPENCLAW_PAIN_POINT_FILE:-$HOME/.openclaw_pain_point}"

# ── 读当前版本（dev 环境无 openclaw 命令时降级）──
DEV_MODE=false
CURRENT=$(openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "")
if [ -z "$CURRENT" ]; then
    DEV_MODE=true
    CURRENT="2026.3.13"  # 已知部署版本，dev 环境占位
fi

echo "=== OpenClaw 升级 tripwire 检查 $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "当前部署版本: v$CURRENT"
echo "最后正式评估: $LAST_EVAL_DATE"
[ "$DEV_MODE" = true ] && echo "⚠️  dev 环境模式 (无 openclaw 命令，仅跑 tripwire 检查)"
echo ""

TRIPWIRE_TRIPPED=0
TRIPWIRE_REPORT=()

# ── Tripwire 1: 时间上限 ≥ TIME_TRIPWIRE_DAYS 天 ──
DAYS_SINCE=$(python3 -c "
from datetime import date
try:
    last = date(*[int(x) for x in '$LAST_EVAL_DATE'.split('-')])
    print((date.today() - last).days)
except Exception:
    print(0)
" 2>/dev/null || echo "0")

if [ "$DAYS_SINCE" -ge "$TIME_TRIPWIRE_DAYS" ]; then
    TRIPWIRE_REPORT+=("🚨 [1/6] 时间上限: ${DAYS_SINCE} 天 ≥ ${TIME_TRIPWIRE_DAYS} 天 (TRIPPED)")
    TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
else
    REMAIN=$((TIME_TRIPWIRE_DAYS - DAYS_SINCE))
    TRIPWIRE_REPORT+=("✅ [1/6] 时间上限: ${DAYS_SINCE}/${TIME_TRIPWIRE_DAYS} 天 (剩 ${REMAIN} 天)")
fi

# ── Tripwire 2: 版本差距 ≥ VERSION_GAP_TRIPWIRE 个 stable ──
STABLE_AFTER=$(curl -s --max-time 10 https://registry.npmjs.org/openclaw 2>/dev/null | \
  CURRENT_V="$CURRENT" python3 -c "
import json, os, sys
try:
    d = json.load(sys.stdin)
    versions = d.get('versions', {})
    current = os.environ.get('CURRENT_V','')
    # 仅计 v2026.x 的 stable（无 beta/alpha/rc/dev 后缀）
    stable = []
    for v in versions.keys():
        if not v.startswith('2026.'): continue
        if any(x in v for x in ['beta','alpha','rc','dev']): continue
        stable.append(v)
    # 数 current 字典序之后的 stable 数（粗略代表'之后发布的版本数'）
    after = [v for v in stable if v > current]
    print(len(after))
except Exception:
    print(0)
" 2>/dev/null || echo "0")

if [ "$STABLE_AFTER" -ge "$VERSION_GAP_TRIPWIRE" ]; then
    TRIPWIRE_REPORT+=("🚨 [2/6] 版本差距: ${STABLE_AFTER} ≥ ${VERSION_GAP_TRIPWIRE} stable (TRIPPED)")
    TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
else
    REMAIN=$((VERSION_GAP_TRIPWIRE - STABLE_AFTER))
    TRIPWIRE_REPORT+=("✅ [2/6] 版本差距: ${STABLE_AFTER}/${VERSION_GAP_TRIPWIRE} stable (剩 ${REMAIN})")
fi

# ── Tripwire 3: EOL 信号（grep latest release body）──
LATEST_JSON=$(curl -s --max-time 10 "https://api.github.com/repos/openclaw/openclaw/releases/latest" 2>/dev/null || echo "{}")
EOL_HIT=$(echo "$LATEST_JSON" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    body = (d.get('body','') or '').lower()
    hits = [kw for kw in ['v2026.3', 'eol', 'end of life', 'end-of-life',
                          'no longer supported', 'deprecated v2026']
            if kw in body]
    print(','.join(hits))
except Exception:
    print('')
" 2>/dev/null || echo "")

if [ -n "$EOL_HIT" ]; then
    TRIPWIRE_REPORT+=("🚨 [3/6] EOL 信号: latest release 含 [$EOL_HIT] (TRIPPED — 须人工确认影响 v2026.3.x)")
    TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
else
    TRIPWIRE_REPORT+=("✅ [3/6] EOL 信号: latest release 未检出")
fi

# ── Tripwire 4: WhatsApp plugin 破坏性变更（仅扫 "Breaking" section 内的 whatsapp） ──
WA_BREAKING=$(echo "$LATEST_JSON" | python3 -c "
import json, re, sys
try:
    d = json.load(sys.stdin)
    body = (d.get('body','') or '')
    # 找 markdown 'Breaking Changes' / 'Breaking changes' section（## 或 ### header）
    pattern = re.compile(r'^#{2,6}\s+breaking', re.IGNORECASE | re.MULTILINE)
    sections = []
    matches = list(pattern.finditer(body))
    for i, m in enumerate(matches):
        start = m.end()
        # 下一个同级或更高级 header（## 或 ### 开头）
        next_match = re.search(r'^#{2,6}\s+', body[start:], re.MULTILINE)
        end = start + next_match.start() if next_match else len(body)
        sections.append(body[start:end])
    # 在 breaking section 内找 whatsapp
    for sec in sections:
        for line in sec.split('\n'):
            if 'whatsapp' in line.lower():
                print(line.strip()[:180])
                sys.exit()
    # 兜底：精确短语扫全文（remove/drop/discontinue/deprecate WhatsApp support/plugin）
    exact_patterns = [
        r'remove\s+whatsapp\s+(support|plugin|integration)',
        r'drop\s+whatsapp\s+(support|plugin|integration)',
        r'discontinu\w+\s+whatsapp',
        r'deprecat\w+\s+whatsapp\s+(support|plugin|integration)',
    ]
    for pat in exact_patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            ln_start = body.rfind('\n', 0, m.start()) + 1
            ln_end = body.find('\n', m.end())
            if ln_end == -1: ln_end = len(body)
            print(body[ln_start:ln_end].strip()[:180])
            sys.exit()
except Exception:
    pass
" 2>/dev/null || echo "")

if [ -n "$WA_BREAKING" ]; then
    TRIPWIRE_REPORT+=("🚨 [4/6] WhatsApp 破坏性: $WA_BREAKING (TRIPPED)")
    TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
else
    TRIPWIRE_REPORT+=("✅ [4/6] WhatsApp 破坏性: latest release 未检出")
fi

# ── Tripwire 5: CVE（人工触发：echo "..." > $CVE_FILE）──
if [ -f "$CVE_FILE" ]; then
    CVE_DESC=$(head -c 180 "$CVE_FILE" 2>/dev/null || echo "(无内容)")
    TRIPWIRE_REPORT+=("🚨 [5/6] CVE 人工标记: $CVE_DESC (TRIPPED)")
    TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
else
    TRIPWIRE_REPORT+=("✅ [5/6] CVE: 无人工标记 ($CVE_FILE 不存在)")
fi

# ── Tripwire 6: 业务痛点（人工触发：echo "..." > $PAIN_FILE）──
if [ -f "$PAIN_FILE" ]; then
    PAIN_DESC=$(head -c 180 "$PAIN_FILE" 2>/dev/null || echo "(无内容)")
    TRIPWIRE_REPORT+=("🚨 [6/6] 业务痛点人工标记: $PAIN_DESC (TRIPPED)")
    TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
else
    TRIPWIRE_REPORT+=("✅ [6/6] 业务痛点: 无人工标记 ($PAIN_FILE 不存在)")
fi

# ── 输出 tripwire 报告（全部状态可见，不静默吞 — V37.3 INV-GOV-001 同款） ──
echo "── Tripwire 状态 (${TRIPWIRE_TRIPPED}/6 触发) ──"
for line in "${TRIPWIRE_REPORT[@]}"; do
    echo "  $line"
done
echo ""

# ── 决策 ──
if [ "$TRIPWIRE_TRIPPED" -eq 0 ]; then
    echo "═══════════════════════════════════════"
    echo "结论: ✅ 继续 hold (0/6 tripwire 触发)"
    echo "  当前 v$CURRENT 稳定运行，无升级触发条件"
    echo "  下次检查: 每周一 cron 自动 + 任一 tripwire 触发推送告警"
    echo "═══════════════════════════════════════"
    exit 0
fi

echo "═══════════════════════════════════════"
echo "🚨 ${TRIPWIRE_TRIPPED}/6 tripwire 触发 — 启动正式升级评估流程"
echo "═══════════════════════════════════════"
echo ""

if [ "$DEV_MODE" = true ]; then
    echo "⚠️  dev 环境跳过 runtime 就绪检查"
    echo "   请在 Mac Mini 上重跑此脚本以完成 npm + WhatsApp plugin 验证"
    echo ""
    echo "下一步: read docs/gateway_upgrade_eval_v2026.4.md 第十二节决策矩阵"
    exit 1
fi

# ── Mac Mini 上：跑传统的 npm + WhatsApp 就绪检查 ──
LATEST_TAG=$(echo "$LATEST_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tag_name','unknown'))" 2>/dev/null || echo "unknown")
LATEST_VER=$(echo "$LATEST_TAG" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "$LATEST_TAG")

echo "── 升级就绪检查 (target: v$LATEST_VER) ──"
NPM_CHECK=$(npm view "openclaw@$LATEST_VER" version 2>&1 || echo "unavailable")
if echo "$NPM_CHECK" | grep -q "$LATEST_VER"; then
    echo "  ✅ npm registry 可用"
else
    echo "  ❌ npm registry 不可用或限流: $(echo "$NPM_CHECK" | head -1)"
fi

WA_STATUS=$(openclaw plugins install whatsapp 2>&1 || true)
if echo "$WA_STATUS" | grep -q "Installed plugin"; then
    echo "  ✅ WhatsApp plugin 可安装"
elif echo "$WA_STATUS" | grep -q "prerelease"; then
    WA_VER=$(echo "$WA_STATUS" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+-[A-Za-z]+' | head -1 || echo "unknown")
    echo "  ⚠️  WhatsApp plugin 仍预发布版 ($WA_VER)"
elif echo "$WA_STATUS" | grep -q "429"; then
    echo "  ❌ ClawHub 限流中 (429)"
else
    echo "  ❓ WhatsApp 状态未知: $(echo "$WA_STATUS" | head -1)"
fi

echo ""
echo "═══════════════════════════════════════"
echo "下一步:"
echo "  1. read docs/gateway_upgrade_eval_v2026.4.md 第十二节看完整决策矩阵"
echo "  2. 选定目标版本（不一定是 latest — 当前 latest=v$LATEST_VER 有 #73358 dealbreaker）"
echo "  3. 在维护窗口执行 npm install -g openclaw@TARGET + 完整 SOP（备份/升级/验证/回滚预案）"
echo "  4. 升级成功后更新 LAST_EVAL_DATE 至升级日期"
echo "═══════════════════════════════════════"

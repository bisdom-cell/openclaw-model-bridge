#!/bin/bash
# check_upgrade.sh — OpenClaw 升级 tripwire 监控 + 就绪检查
# V37.9.22: 6 条 tripwire 替代"看到新版本就评估"模式 — 见 docs/gateway_upgrade_eval_v2026.4.md 第十二节
# V37.9.334: 对抗审计四修（本脚本此前零登记/零告警接线/零测试覆盖 = "验证者自身无人验证"家族）:
#   CU-F1 告警接线 — "任一触发推送告警"此前只是文档声称: 脚本无任何推送机制, 且不在
#         jobs_registry / auto_deploy FILE_MAP / job_watchdog 任何名单里（tripwire 响了
#         没有线连到铃上, V37.9.328 死告警家族）。现: 触发 → notify --topic alerts
#         （重试+队列）; 并注册 registry + FILE_MAP + watchdog LOG_FRESHNESS（周任务 14d 阈值）。
#   CU-F2 版本差距改数值元组比较 — 旧字典序字符串比较在 2026.10.x 起把新版本排在
#         2026.4.27 之前 = 系统性漏计（'1'<'4'）; 旧 '2026.' 前缀过滤在 2027.1.x 起同款漏计。
#   CU-F3 网络失败显式不可判 — 旧行为: curl 失败/响应异常 → tripwire 2 报 "0/50 ✅"、
#         3/4 报 "未检出 ✅"（跑不动 ≠ 没新版, V37.9.322 F3 家族）。现: ⚠️ NA 不计触发但可见。
#   CU-F4 EOL 关键词跟随部署版本 — 旧硬编码 'v2026.3'（部署 2026.3.x 时代遗留, 部署已
#         4.27 十一周）。现: 从 CURRENT 派生 v{year}.{minor} + 'deprecated v{year}'。
# 用法：bash check_upgrade.sh   （每周一 09:10 cron, 登记见 jobs_registry.yaml: check_upgrade）
set -euo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

# ── V37.9.334 (CU-F1): notify 装载（优先运行时副本, fallback 仓库副本; 装载失败不阻塞检查）──
if [ -f "$HOME/notify.sh" ]; then
    source "$HOME/notify.sh" 2>/dev/null || true
else
    _cu_dir="$(cd "$(dirname "$0")" && pwd)"
    if [ -f "$_cu_dir/notify.sh" ]; then
        source "$_cu_dir/notify.sh" 2>/dev/null || true
    fi
fi

# ── 配置（升级后须更新 LAST_EVAL_DATE）──
# 2026-07-20 第七次评估 (2026.7.1 stable 发布触发判据跟踪): 继续 hold, 判据全未满足——
# ① SQLite/session 弧线 ❌ 未收敛 (7.1 仍 4+ session-accessor refactor) ② 节奏 🟡 部分改善
# ③ Node 门槛 🔴 升为区间黑名单 (SQLite WAL 安全 #106065). 详见 eval doc 第十八节.
# 背景 (第六次 2026-07-04): 4.27→6.11 三结构性迁移 M1 插件外部化/M2 SQLite 迁移/M3 Proxyline + 回滚单向门.
LAST_EVAL_DATE="${OPENCLAW_LAST_EVAL_DATE:-2026-07-20}"  # V37.9.267: 第七次评估 (eval doc 第十八节)
TIME_TRIPWIRE_DAYS="${OPENCLAW_TIME_TRIPWIRE_DAYS:-180}"
VERSION_GAP_TRIPWIRE="${OPENCLAW_VERSION_GAP_TRIPWIRE:-50}"
CVE_FILE="${OPENCLAW_CVE_ALERT_FILE:-$HOME/.openclaw_cve_alert}"
PAIN_FILE="${OPENCLAW_PAIN_POINT_FILE:-$HOME/.openclaw_pain_point}"

# ── 读当前版本（dev 环境无 openclaw 命令时降级）──
DEV_MODE=false
CURRENT=$(openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "")
if [ -z "$CURRENT" ]; then
    DEV_MODE=true
    CURRENT="2026.4.27"  # 已知部署版本 (V37.9.138: 2026-06-11 升级完成)，dev 环境占位
fi

echo "=== OpenClaw 升级 tripwire 检查 $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "当前部署版本: v$CURRENT"
echo "最后正式评估: $LAST_EVAL_DATE"
[ "$DEV_MODE" = true ] && echo "⚠️  dev 环境模式 (无 openclaw 命令，仅跑 tripwire 检查)"
echo ""

TRIPWIRE_TRIPPED=0
TRIPWIRE_NA=0
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
# V37.9.334 (CU-F2): 数值元组比较（'2026.7.1-2' → (2026,7,1,2)）替代字典序 —— 字典序在
# 2026.10.x 起漏计（'1'<'4'）; 去掉 '2026.' 前缀过滤 —— 2027.1.x 起同款漏计, 旧年份
# 版本数值上天然 < current 不需要前缀挡。
# V37.9.334 (CU-F3): curl 失败/空响应/versions 为空 → NA 显式不可判, 不再伪装成 0。
NPM_TMP=$(mktemp "${TMPDIR:-/tmp}/check_upgrade_npm.XXXXXX")
if curl -s --max-time 10 https://registry.npmjs.org/openclaw -o "$NPM_TMP" 2>/dev/null && [ -s "$NPM_TMP" ]; then
    STABLE_AFTER=$(CURRENT_V="$CURRENT" python3 -c "
import json, os, re, sys
def vkey(v):
    return tuple(int(p) for p in re.split(r'[.-]', v))
try:
    d = json.load(sys.stdin)
    versions = d.get('versions', {})
    if not versions:
        print('NA')
        sys.exit()
    cur_key = vkey(os.environ.get('CURRENT_V', ''))
    after = 0
    for v in versions.keys():
        if any(x in v for x in ['beta', 'alpha', 'rc', 'dev']):
            continue
        try:
            k = vkey(v)
        except Exception:
            continue
        if k > cur_key:
            after += 1
    print(after)
except Exception:
    print('NA')
" < "$NPM_TMP" 2>/dev/null || echo "NA")
else
    STABLE_AFTER="NA"
fi
rm -f "$NPM_TMP"

if [ "$STABLE_AFTER" = "NA" ]; then
    TRIPWIRE_REPORT+=("⚠️ [2/6] 版本差距: 不可判（npm registry 不可达或响应异常 — 跑不动≠没新版）")
    TRIPWIRE_NA=$((TRIPWIRE_NA + 1))
elif [ "$STABLE_AFTER" -ge "$VERSION_GAP_TRIPWIRE" ]; then
    TRIPWIRE_REPORT+=("🚨 [2/6] 版本差距: ${STABLE_AFTER} ≥ ${VERSION_GAP_TRIPWIRE} stable (TRIPPED)")
    TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
else
    REMAIN=$((VERSION_GAP_TRIPWIRE - STABLE_AFTER))
    TRIPWIRE_REPORT+=("✅ [2/6] 版本差距: ${STABLE_AFTER}/${VERSION_GAP_TRIPWIRE} stable (剩 ${REMAIN})")
fi

# ── Tripwire 3/4 共享: latest release 获取 + 可判性检查 ──
# V37.9.334 (CU-F3): 响应不可解析 / 非 dict / 无 tag_name+body（网络失败/限流/404）
# → tripwire 3/4 双双 NA, 不再把"看不到 release"读作"未检出 ✅"。
LATEST_JSON=$(curl -s --max-time 10 "https://api.github.com/repos/openclaw/openclaw/releases/latest" 2>/dev/null || true)
GH_OK=true
if [ -z "$LATEST_JSON" ]; then
    GH_OK=false
elif ! printf '%s' "$LATEST_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert isinstance(d, dict)
assert ('tag_name' in d) or ('body' in d)
" 2>/dev/null; then
    GH_OK=false
fi

# ── Tripwire 3: EOL 信号（grep latest release body）──
# V37.9.334 (CU-F4): EOL 关键词从 CURRENT 派生（现 v2026.4）, 退役硬编码 'v2026.3'
# （那是部署 2026.3.13 时代的关键词, 4.27 时代对 v2026.3 的 EOL 不再受影响, 而对
# v2026.4 的 EOL 反而抓不到）。'deprecated v{year}' 同款去硬编码。
if [ "$GH_OK" = false ]; then
    TRIPWIRE_REPORT+=("⚠️ [3/6] EOL 信号: 不可判（GitHub releases API 不可达或响应异常）")
    TRIPWIRE_NA=$((TRIPWIRE_NA + 1))
else
    EOL_HIT=$(printf '%s' "$LATEST_JSON" | CURRENT_V="$CURRENT" python3 -c "
import json, os, sys
try:
    d = json.load(sys.stdin)
    body = (d.get('body','') or '').lower()
    cur = os.environ.get('CURRENT_V', '')
    parts = [p for p in cur.split('.') if p]
    kws = ['eol', 'end of life', 'end-of-life', 'no longer supported']
    if len(parts) >= 2:
        kws.insert(0, 'v' + parts[0] + '.' + parts[1])
    if parts:
        kws.append('deprecated v' + parts[0])
    hits = [kw for kw in kws if kw in body]
    print(','.join(hits))
except Exception:
    print('')
" 2>/dev/null || echo "")

    if [ -n "$EOL_HIT" ]; then
        TRIPWIRE_REPORT+=("🚨 [3/6] EOL 信号: latest release 含 [$EOL_HIT] (TRIPPED — 须人工确认影响 v$CURRENT)")
        TRIPWIRE_TRIPPED=$((TRIPWIRE_TRIPPED + 1))
    else
        TRIPWIRE_REPORT+=("✅ [3/6] EOL 信号: latest release 未检出")
    fi
fi

# ── Tripwire 4: WhatsApp plugin 破坏性变更（仅扫 "Breaking" section 内的 whatsapp） ──
if [ "$GH_OK" = false ]; then
    TRIPWIRE_REPORT+=("⚠️ [4/6] WhatsApp 破坏性: 不可判（GitHub releases API 不可达或响应异常）")
    TRIPWIRE_NA=$((TRIPWIRE_NA + 1))
else
    WA_BREAKING=$(printf '%s' "$LATEST_JSON" | python3 -c "
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
if [ "$TRIPWIRE_NA" -gt 0 ]; then
    echo "  ⚠️ ${TRIPWIRE_NA} 项网络不可判（连续多周不可判 = 网络/证书问题, 需人工核查）"
fi
echo ""

# ── V37.9.334 (CU-F1): tripwire 触发 → 推送告警 ──
# 此前"任一触发推送告警"承诺无任何机制支撑（脚本零 notify 调用, 日志无人扫描,
# exit code 无消费者）。周任务节奏 = 触发状态持续期间每周提醒一次, 无告警风暴风险。
if [ "$TRIPWIRE_TRIPPED" -gt 0 ]; then
    TRIP_LINES=$(printf '%s\n' "${TRIPWIRE_REPORT[@]}" | grep "🚨" || true)
    ALERT_MSG="[SYSTEM_ALERT] OpenClaw 升级 tripwire ${TRIPWIRE_TRIPPED}/6 触发 (当前 v${CURRENT})
${TRIP_LINES}
处理: read docs/gateway_upgrade_eval_v2026.4.md 第十二节决策矩阵"
    if type notify >/dev/null 2>&1; then
        if notify "$ALERT_MSG" --topic alerts 2>/dev/null; then
            echo "📣 tripwire 告警已推送 (notify --topic alerts)"
        else
            echo "WARN: tripwire 告警推送失败（notify 队列将自动重放; 本日志已留痕）"
        fi
    else
        echo "WARN: notify.sh 不可用, tripwire 告警仅日志可见"
    fi
    echo ""
fi

# ── 决策 ──
if [ "$TRIPWIRE_TRIPPED" -eq 0 ]; then
    echo "═══════════════════════════════════════"
    echo "结论: ✅ 继续 hold (0/6 tripwire 触发)"
    if [ "$TRIPWIRE_NA" -gt 0 ]; then
        echo "  ⚠️ 其中 ${TRIPWIRE_NA} 项网络不可判未计入 — 本结论仅覆盖可判项"
    fi
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

# 就绪检查副作用登记（CU-F5, V37.9.334 登记不改）: `plugins install` 是有副作用的
# "检查"（MR-23 观察者不得改被观察者的张力点）。4.27 上 whatsapp 为 bundled plugin,
# 重复 install 实测 no-op; dev 无法验证替代 CLI 面（原则 #33）, 触发路径罕见（仅 tripped
# 且 Mac Mini）, 登记待未来升级评估时一并处理, 不盲改。
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
echo "  2. 选定目标版本（不一定是 latest — 见 eval doc 第十七/十八节 hold 判据）"
echo "  3. 在维护窗口执行 npm install -g openclaw@TARGET + 完整 SOP（备份/升级/验证/回滚预案）"
echo "  4. 升级成功后更新 LAST_EVAL_DATE 至升级日期"
echo "═══════════════════════════════════════"
# V37.9.334: tripped 路径显式非零退出（此前隐式 exit 0 = cron/调用方视角与 hold 无差别）
exit 1

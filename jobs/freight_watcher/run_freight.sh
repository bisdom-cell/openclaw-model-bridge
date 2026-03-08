#!/usr/bin/env bash
# 货代商机 Watcher v3 (v23新增, v25修复, v27: ImportYeti, v3: 客户画像)
# 每天 08:00/14:00/20:00 HKT 由系统crontab触发
# 调试记录：#84(信号源切换), #91(--session-id修复), 第31章(脚本设计宪法)
# V2变更：⭐⭐⭐⭐+ 条目自动附加 ImportYeti 企业查询链接
# V3变更：三层情报漏斗 — 信号捕获→ImportYeti深挖→客户画像生成
set -eo pipefail

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
ROOT="${HOME}/.openclaw"
JOB="$ROOT/jobs/freight_watcher"
CACHE="$JOB/cache"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/freight_daily.md"
KB_INBOX="${KB_BASE:-$HOME/.kb}/inbox.md"
TO="${OPENCLAW_PHONE:-+85200000000}"
LLM_RAW="$CACHE/llm_raw_last.txt"

mkdir -p "$CACHE" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC"   || echo "# 货代商机 Watcher" > "$KB_SRC"
test -f "$KB_INBOX" || echo "# INBOX" > "$KB_INBOX"

DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')"
NEW_FILE="$CACHE/freight_new.jsonl"
: > "$NEW_FILE"

# ── 1. 抓取多源RSS + Google News ────────────────────────────────────────
python3 - "$KB_INBOX" "$NEW_FILE" << 'PYEOF'
import sys, json, re, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

INBOX_FILE = sys.argv[1]
OUT_FILE   = sys.argv[2]

try:
    with open(INBOX_FILE) as f:
        inbox = f.read()
except OSError:
    inbox = ""

# 关键词过滤
FREIGHT_KW   = ["freight","forwarder","logistics","shipping","cargo","import","export",
                 "supply chain","ocean freight","air freight","customs","tariff"]
EXPANSION_KW = ["expand","acquisition","new facility","warehouse","distribution center",
                 "manufacturing","production","sourcing china","china supplier"]

SOURCES = [
    ("https://www.freightwaves.com/news/feed",   FREIGHT_KW),
    ("https://theloadstar.com/feed/",             FREIGHT_KW),
    ("https://www.aircargonews.net/feed/",        FREIGHT_KW),
    ("https://www.dcvelocity.com/rss/articles",   FREIGHT_KW),
    ("https://www.chinadaily.com.cn/rss/bizchina_rss.xml", EXPANSION_KW),
    ("https://www.scmp.com/rss/91/feed",          EXPANSION_KW),
    ("https://www.prnewswire.com/rss/news-releases-list.rss", EXPANSION_KW),
    # Google News — 原有
    ("https://news.google.com/rss/search?q=freight+forwarder+china&hl=en-US&gl=US&ceid=US:en", FREIGHT_KW),
    ("https://news.google.com/rss/search?q=importing+from+china+logistics&hl=en-US&gl=US&ceid=US:en", FREIGHT_KW),
    ("https://news.google.com/rss/search?q=supply+chain+china+expansion&hl=en-US&gl=US&ceid=US:en", EXPANSION_KW),
    # V3新增 — 聚焦真实货运需求信号
    ("https://news.google.com/rss/search?q=%22looking+for+freight+forwarder%22+OR+%22logistics+partner%22+OR+%22shipping+partner%22&hl=en-US&gl=US&ceid=US:en", FREIGHT_KW),
    ("https://news.google.com/rss/search?q=%22new+warehouse%22+OR+%22fulfillment+center%22+china&hl=en-US&gl=US&ceid=US:en", EXPANSION_KW),
    ("https://news.google.com/rss/search?q=%22FBA%22+%22freight+forwarder%22+OR+%22ocean+freight%22+rate&hl=en-US&gl=US&ceid=US:en", FREIGHT_KW),
]

results = []
NS = {"a": "http://www.w3.org/2005/Atom"}

for url, kws in SOURCES:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue

        items = root.findall(".//item") or root.findall(".//a:entry", NS)
        for item in items[:20]:
            title = (item.findtext("title") or item.findtext("a:title", "", NS) or "").strip()
            link  = (item.findtext("link")  or item.findtext("a:link",  "", NS) or "").strip()
            if not link:
                for lk in item.findall("a:link", NS):
                    if lk.get("type") == "text/html":
                        link = lk.get("href", "")
            title_low = title.lower()
            if any(k in title_low for k in kws) and link and link not in inbox:
                results.append({"title": title, "url": link, "source": url.split("/")[2]})
    except Exception as e:
        print(f"[freight] WARN: {url[:60]} -> {e}", file=sys.stderr)

# 去重 + 限制10条
seen = set()
with open(OUT_FILE, "w") as f:
    count = 0
    for r in results:
        if r["url"] not in seen and count < 10:
            seen.add(r["url"])
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            count += 1
print(f"[freight] 抓取完成，新条目: {count}", file=sys.stderr)
PYEOF

# ── 2. 计算新条目数 ──────────────────────────────────────────────────────
NEW_COUNT="$(wc -l < "$NEW_FILE" | tr -d ' ')"
if [ "$NEW_COUNT" -eq 0 ]; then
    echo "[freight] 暂无新商机，本轮跳过。"
    exit 0
fi

# 提前写入INBOX（与LLM结果解耦，#85原则）
while IFS= read -r line; do
    url="$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || true)"
    [ -n "$url" ] && echo "- $url" >> "$KB_INBOX"
done < "$NEW_FILE"

# ── 3. 单次批量LLM调用（L1告警：失败时WhatsApp⚠️）────────────────────
PROMPT="你是货代行业分析师。以下是${NEW_COUNT}条行业新闻，请逐条分析：

$(python3 -c "
import json, sys
lines = open('$NEW_FILE').readlines()
for i, l in enumerate(lines, 1):
    d = json.loads(l)
    print(f'{i}. 标题：{d[\"title\"]}')
    print(f'   来源：{d[\"source\"]}')
" 2>/dev/null)

请严格按以下格式输出，每条之间空一行：
序号. 企业信号：[企业名] — [≤25字需求描述]（无明确企业时写'行业信号 — 描述'）
行动：[≤30字可执行行动建议]
评级：⭐（1-5个星）"

LLM_OUT="$(
    "$OPENCLAW" agent \
        --to "$TO" \
        --session-id "freight-$(date +%s)" \
        --message "$PROMPT" \
        --thinking off \
        2>"$LLM_RAW.stderr" || true
)"
echo "returncode=$?" > "$LLM_RAW"
echo "--- stderr ---" >> "$LLM_RAW"
cat "$LLM_RAW.stderr" >> "$LLM_RAW" 2>/dev/null || true
echo "--- stdout ---" >> "$LLM_RAW"
echo "$LLM_OUT" >> "$LLM_RAW"

# L1检查：LLM输出为空
if [ -z "${LLM_OUT// }" ]; then
    ERR_MSG="⚠️ 货代Watcher LLM调用失败（${DAY}），请检查 $LLM_RAW"
    echo "$ERR_MSG"
    "$OPENCLAW" message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
    exit 1
fi

# L2检查：解析成功率 < 50%
PARSE_OK="$(echo "$LLM_OUT" | grep -c '评级：' || true)"
if [ "$PARSE_OK" -lt $(( NEW_COUNT / 2 )) ] && [ "$NEW_COUNT" -gt 2 ]; then
    WARN_MSG="⚠️ 货代Watcher解析成功率低 ${PARSE_OK}/${NEW_COUNT}（${DAY}），请查 $LLM_RAW"
    echo "$WARN_MSG"
    "$OPENCLAW" message send --target "$TO" --message "$WARN_MSG" --json >/dev/null 2>&1 || true
    exit 2
fi

# ── 4. 组装WhatsApp消息 ─────────────────────────────────────────────────
MSG_FILE="$CACHE/system_message_freight.txt"
{
    echo "🚢 货代商机速报 (${DAY})"
    echo ""
    # 将LLM输出与原始条目对应，追加链接
    python3 - "$NEW_FILE" "$LLM_RAW" << 'PYEOF2'
import sys, json, re, urllib.parse

lines_file = open(sys.argv[1]).readlines()
urls = []
titles = []
for l in lines_file:
    try:
        d = json.loads(l)
        urls.append(d.get("url",""))
        titles.append(d.get("title",""))
    except Exception:
        urls.append("")
        titles.append("")

def extract_company(block):
    """从'企业信号：XX — 描述'提取企业名"""
    m = re.search(r'企业信号：(.+)', block)
    if not m:
        return None
    signal = m.group(1).strip()
    if signal.startswith("行业信号"):
        return None
    # 按 — 或 - 分隔，取企业名部分
    parts = re.split(r'\s*[—–-]\s*', signal, maxsplit=1)
    company = parts[0].strip()
    if len(company) < 2 or len(company) > 30:
        return None
    return company

def count_stars(block):
    """统计评级星数"""
    m = re.search(r'评级：(⭐+)', block)
    return len(m.group(1)) if m else 0

def importyeti_url(company):
    """生成 ImportYeti 查询链接"""
    q = urllib.parse.quote(company)
    return f"https://www.importyeti.com/search?q={q}"

# 读LLM原始输出（从stdout部分）
raw = open(sys.argv[2]).read()
stdout_part = raw.split("--- stdout ---")[-1] if "--- stdout ---" in raw else raw

blocks = re.split(r'\n(?=\d+\.)', stdout_part.strip())
for i, block in enumerate(blocks):
    if not block.strip():
        continue
    url = urls[i] if i < len(urls) else ""
    print(block.strip())
    if url:
        print(f"链接：{url}")
    # V2: 4星+条目自动附加 ImportYeti 查询链接
    stars = count_stars(block)
    if stars >= 4:
        company = extract_company(block)
        if company:
            print(f"📦 ImportYeti：{importyeti_url(company)}")
    print("")
PYEOF2
} > "$MSG_FILE"

# ── 5. 推送WhatsApp ─────────────────────────────────────────────────────
"$OPENCLAW" message send --target "$TO" --message "$(cat "$MSG_FILE")" --json >/dev/null 2>&1 || true
echo "[freight] 已推送 ${NEW_COUNT} 条商机（${DAY}）"

# ── 6. KB归档 ───────────────────────────────────────────────────────────
{
    echo ""
    echo "## ${DAY}"
    cat "$MSG_FILE"
} >> "$KB_SRC"

# ── 7. rsync备份 ────────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

# ══════════════════════════════════════════════════════════════════════════
# V3: 三层情报漏斗 — 第2层 ImportYeti 深挖 + 第3层 客户画像
# ══════════════════════════════════════════════════════════════════════════

# ── 8. 提取高星企业名（≥4星 + 非行业信号）─────────────────────────────
HIGH_STARS="$CACHE/high_star_companies.txt"
: > "$HIGH_STARS"

python3 -c "
import re
raw = open('$LLM_RAW').read()
stdout = raw.split('--- stdout ---')[-1] if '--- stdout ---' in raw else raw
seen = set()
for block in re.split(r'\n(?=\d+\.)', stdout.strip()):
    if not block.strip():
        continue
    if len(re.findall(r'⭐', block)) >= 4:
        m = re.search(r'企业信号：(.+?)[\s]*[—–\-]', block)
        if m:
            name = m.group(1).strip()
            if name != '行业信号' and 2 <= len(name) <= 30 and name not in seen:
                seen.add(name)
                print(name)
" > "$HIGH_STARS" 2>/dev/null || true

COMPANY_COUNT=$(wc -l < "$HIGH_STARS" | tr -d ' ')
echo "[freight] 发现 ${COMPANY_COUNT} 个高星企业待深挖"

# 限制最多深挖5个，取前5条（LLM输出越靠前通常评级越高）
if [ "$COMPANY_COUNT" -gt 5 ]; then
    head -5 "$HIGH_STARS" > "$HIGH_STARS.tmp" && mv "$HIGH_STARS.tmp" "$HIGH_STARS"
    COMPANY_COUNT=5
    echo "[freight] 截取前5个企业进行深挖"
fi

# ── 9. ImportYeti 自动化深挖（browser绕过Cloudflare）─────────────────
if [ "$COMPANY_COUNT" -gt 0 ]; then
    ENRICHED_FILE="$CACHE/enriched_data.txt"
    : > "$ENRICHED_FILE"

    while IFS= read -r company; do
        echo "[freight] 深挖: $company"
        SLUG=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$company'))")

        ENRICH_OUT="$("$OPENCLAW" agent \
            --to "$TO" \
            --session-id "enrich-$(date +%s)" \
            --message "请执行以下步骤查询 ${company} 的海关数据：

1. 用 browser_navigate 访问 https://www.importyeti.com/search?q=${SLUG}
2. 等待页面加载完成后，用 browser_snapshot 截取页面内容
3. 从页面中提取以下信息并严格按格式输出：

公司：${company}
总发货次数：[数字，如无数据写 N/A]
月均发货量：[数字，如无数据写 N/A]
前3大供应商：[供应商名(国家), ...]
主要航线：[起运港→目的港, ...]
最近发货日期：[YYYY-MM-DD，如无数据写 N/A]
趋势：[增长/平稳/下降/无数据]

注意：如果页面显示无结果或被阻止，所有字段写 N/A。不要编造数据。" \
            --thinking off 2>/dev/null || true)"

        {
            echo "--- ${company} ---"
            echo "$ENRICH_OUT"
            echo ""
        } >> "$ENRICHED_FILE"

        # 浏览器操作较慢，间隔8秒
        sleep 8
    done < "$HIGH_STARS"

    # ── 10. 客户画像生成（LLM综合推理）──────────────────────────────
    ENRICHED_DATA="$(cat "$ENRICHED_FILE" 2>/dev/null || true)"

    # 只在有有效深挖数据时才生成画像（至少一个公司有非N/A数据）
    HAS_DATA=$(echo "$ENRICHED_DATA" | grep -c "总发货次数" || true)
    ALL_NA=$(echo "$ENRICHED_DATA" | grep "总发货次数：N/A" | wc -l | tr -d ' ')

    if [ "$HAS_DATA" -gt 0 ] && [ "$HAS_DATA" -gt "$ALL_NA" ]; then
        echo "[freight] 生成客户画像..."

        PROFILE_OUT="$("$OPENCLAW" agent \
            --to "$TO" \
            --session-id "profile-$(date +%s)" \
            --message "你是资深货代销售顾问。基于以下企业新闻信号和 ImportYeti 美国海关提单数据，为每个企业生成完整客户画像。

== 今日新闻信号 ==
$(cat "$MSG_FILE")

== ImportYeti 海关数据 ==
${ENRICHED_DATA}

== 当前市场运价参考 ==
中国→美西整柜: USD 2,500-4,000/TEU
中国→美东整柜: USD 3,500-5,500/TEU
中国→欧洲整柜: USD 1,800-3,000/TEU
中国→东南亚整柜: USD 800-1,500/TEU
空运中国→美国: USD 4-6/kg

请为每个有数据的企业输出客户画像卡片，严格使用以下格式：

📋 客户：[企业名]
├ 📦 需求量：[月均TEU或重量，标注数据来源]
├ 🕐 运输周期：[基于航线的海运/空运天数]
├ 💰 月物流预算：[货量×运价推算，给出USD范围]
├ 🚢 主要路径：[起运港→目的港，可多条]
├ 📈 趋势：[增长/平稳/下降 + 依据]
├ 🏭 主要供应商：[前3个，含国家]
└ 🎯 开发建议：[≤60字，联系哪个部门、什么切入点]

规则：
1. ImportYeti数据全部N/A的公司→跳过不输出
2. 部分N/A→基于新闻信号保守估算，标注'⚠估算'
3. 预算 = 月均TEU × 对应航线运价，给出USD范围
4. 开发建议要具体可执行" \
            --thinking off 2>/dev/null || true)"

        # 推送客户画像
        if [ -n "${PROFILE_OUT// }" ]; then
            "$OPENCLAW" message send --target "$TO" \
                --message "📊 货代客户画像 (${DAY})

${PROFILE_OUT}

💡 数据来源：ImportYeti美国海关提单 + 行业新闻
⚠ 预算为运价推算值，仅供参考" --json >/dev/null 2>&1 || true

            echo "[freight] 已推送客户画像"

            # KB归档
            {
                echo ""
                echo "## 📊 客户画像 ${DAY}"
                echo "$PROFILE_OUT"
            } >> "$KB_SRC"

            # 再次备份
            rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
        else
            echo "[freight] 客户画像LLM调用无输出，跳过"
        fi
    else
        echo "[freight] 深挖数据不足（${HAS_DATA}条中${ALL_NA}条全N/A），跳过画像生成"
    fi
fi

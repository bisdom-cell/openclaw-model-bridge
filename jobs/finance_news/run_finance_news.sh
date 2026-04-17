#!/usr/bin/env bash
# run_finance_news.sh — 全球财经/政策新闻每日汇总
# 每天早上 07:30 HKT 由系统 crontab 触发（算力空闲窗口）
# 双通道抓取：8 个直连 RSS + 14 个财经 X 账号 → LLM 分析 → 一份结构化简报推送
#
# 输出格式：
#   1. 原始新闻列表（出处/时间/价值/关键点评）
#   2. 国内 vs 海外对比总结
#   3. 一句话投资建议 + 风险提示
#
# crontab: 30 7 * * * bash -lc 'bash ~/.openclaw/jobs/finance_news/run_finance_news.sh >> ~/.openclaw/logs/jobs/finance_news.log 2>&1'
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -eo pipefail

# 防重叠执行
LOCK="/tmp/finance_news.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[finance] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

JOB_DIR="${HOME}/.openclaw/jobs/finance_news"
CACHE="$JOB_DIR/cache"
# V37.8.5 暴露 JOB_DIR 给 heredoc Python（import finance_news_zombie）
export FINANCE_NEWS_JOBS_DIR="$JOB_DIR"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/finance_daily.md"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$HOME/kb_write.sh}"
PYTHON3=/usr/bin/python3

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DAY="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
STATUS_FILE="$CACHE/last_run.json"

log() { echo "[$TS] finance: $1" >&2; }

mkdir -p "$CACHE/raw" "${KB_BASE:-$HOME/.kb}/sources"
test -f "$KB_SRC" || echo "# 全球财经/政策每日汇总" > "$KB_SRC"

# ── 加载 notify.sh ────────────────────────────────────────────────────
NOTIFY_LOADED=false
for _np in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$_np" ]; then
        source "$_np"
        NOTIFY_LOADED=true
        break
    fi
done

# ── RSS 源配置（仅直连可达，不依赖 RSSHub）────────────────────────────
# 格式：name|feed_url|label|region
# region: intl=国际, cn=中国/亚太
RSS_FEEDS=(
    # ── 国际权威（Mac Mini 2026-04-13 验证）──
    "Fed Press|https://www.federalreserve.gov/feeds/press_all.xml|美联储官方声明|intl"
    "NBER|https://www.nber.org/rss/new.xml|美国国家经济研究局(工作论文)|intl"
    "FT|https://www.ft.com/rss/home|金融时报(摘要)|intl"
    "ECB|https://www.ecb.europa.eu/rss/press.xml|欧洲央行声明|intl"
    "BIS Speeches|https://www.bis.org/doclist/cbspeeches.rss|国际清算银行(央行演讲)|intl"
    "Yahoo Finance|https://finance.yahoo.com/news/rssindex|雅虎财经|intl"
    # ── 中国/亚太（Mac Mini 2026-04-13 验证）──
    "SCMP Economy|https://www.scmp.com/rss/5/feed|南华早报经济频道|cn"
    "36氪|https://36kr.com/feed|36氪科技财经|cn"
)

# ── 财经 X/Twitter 账号（Syndication API，无需认证）─────────────────────
# 格式：handle|显示名|标签|region
# 补充 RSS 无法覆盖的中国/亚太 + 国际权威声音
FINANCE_X_ACCOUNTS=(
    # ── 国际权威 ──
    # V37.8.4 已移除僵尸账号：
    #   Reuters         — 最新推文 2025-08-03（253 天无更新）
    #   WorldBank       — Syndication 返回 2KB stub（embed disabled）
    #   BrookingsInst   — 最新推文 2024-09-06（585 天无更新）
    "IMFNews|IMF官方(X)|intl"
    "business|Bloomberg商业(X)|intl"
    "WSJ|华尔街日报(X)|intl"
    "ReutersBiz|路透社财经(X)|intl"
    "TheEconomist|经济学人(X)|intl"
    # ── 中国官媒 ──
    # V37.8.4 已移除僵尸账号：
    #   caixin          — 最新推文 2019-10-15（2227 天无更新）
    #   yicaichina      — 最新推文 2016-12-09（3364 天无更新）
    "XHNews|新华社英文(X)|cn"
    "PDChina|人民日报英文(X)|cn"
    "CGTNOfficial|CGTN央视国际(X)|cn"
    "ChinaDaily|中国日报(X)|cn"
    "globaltimesnews|环球时报(X)|cn"
    "CNS1952|中新社(X)|cn"
    # ── 港台/亚太 ──
    # V37.8.4 已移除僵尸账号：
    #   ChannelNewsAsia — 最新推文 2018-01（2955 天无更新）
    #   straits_times   — 最新推文 2024-12-20（420 天无更新）
    # V37.8.13 已移除僵尸账号：
    #   SCMPNews        — 3 天连续 zombie 嫌疑（2026-04-14/15/16，95/95 超窗口）
    "NikkeiAsia|日经亚洲(X)|cn"
    "SingTaoDaily|星岛日报(X)|cn"
    "asahi|朝日新闻(X)|cn"
)

# 每日 job：按天去重，每天自动清空旧缓存
SEEN_FILE="$CACHE/seen_urls_${DAY}.txt"
: > "$SEEN_FILE"
ALL_NEW_FILE="$CACHE/all_new.jsonl"
> "$ALL_NEW_FILE"
# 清理前几天的 seen 文件
find "$CACHE" -name 'seen_urls_*.txt' -not -name "seen_urls_${DAY}.txt" -delete 2>/dev/null || true
find "$CACHE" -name 'seen_x_ids_*.txt' -not -name "seen_x_ids_${DAY}.txt" -delete 2>/dev/null || true

TOTAL_NEW=0
FETCH_ERRORS=0
INTL_COUNT=0
CN_COUNT=0

for feed_entry in "${RSS_FEEDS[@]}"; do
    IFS='|' read -r FEED_NAME FEED_URL FEED_LABEL FEED_REGION <<< "$feed_entry"
    FEED_FILE="$CACHE/feed_$(echo "$FEED_NAME" | tr ' /' '_').xml"

    # 抓取 RSS（3 次重试，指数退避）
    FETCH_OK=false
    for attempt in 1 2 3; do
        HTTP_CODE=$(curl -sSL --max-time 30 -w '%{http_code}' \
            -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120.0.0.0" \
            -o "$FEED_FILE" \
            "$FEED_URL" 2>"$CACHE/curl_feed.err") || HTTP_CODE="000"

        if [ "$HTTP_CODE" = "200" ] && [ -s "$FEED_FILE" ]; then
            FETCH_OK=true
            break
        fi
        sleep "$((attempt * 3))"
    done

    if [ "$FETCH_OK" != "true" ]; then
        log "WARN: ${FEED_NAME} 抓取失败 (HTTP ${HTTP_CODE})，跳过"
        FETCH_ERRORS=$((FETCH_ERRORS + 1))
        continue
    fi

    # 解析 RSS/JSON → 提取近 24h 文章
    $PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$FEED_NAME" "$FEED_LABEL" "$FEED_REGION" << 'PYEOF' >> "$ALL_NEW_FILE"
import sys, json, re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

feed_file, seen_file, feed_name, feed_label, region = sys.argv[1:6]

with open(seen_file) as f:
    seen_urls = set(line.strip() for line in f if line.strip())

# 72h 窗口（覆盖周末）
cutoff = datetime.now(timezone.utc) - timedelta(hours=72)

def parse_date(s):
    """尝试解析多种日期格式"""
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z"]:
        try:
            dt = datetime.strptime(s[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None

articles = []

# 检测 JSON vs XML（读前几字节判断）
with open(feed_file, 'r', encoding='utf-8', errors='replace') as f:
    first_bytes = f.read(20).lstrip()
is_json = first_bytes.startswith('{') or first_bytes.startswith('[')

if is_json:
    try:
        with open(feed_file) as f:
            raw = f.read()
        data = json.loads(raw)
        # 兼容新浪 API 格式
        items_list = data.get("result", {}).get("data", []) if "result" in data else data if isinstance(data, list) else []
        for item in items_list[:8]:
            title = item.get("title", "").strip()
            url = item.get("url", item.get("link", ""))
            ctime = item.get("ctime", item.get("pub_date", item.get("pubDate", "")))
            intro = item.get("intro", item.get("summary", item.get("description", "")))[:300]
            if not title or url in seen_urls:
                continue
            articles.append({
                "title": title,
                "link": url,
                "description": intro,
                "pub_date": ctime,
                "feed_name": feed_name,
                "feed_label": feed_label,
                "region": region,
            })
    except Exception as e:
        print(f"[finance] {feed_name} JSON解析失败: {e}", file=sys.stderr)
else:
    # 标准 RSS/Atom XML 解析
    try:
        tree = ET.parse(feed_file)
        root = tree.getroot()
    except ET.ParseError:
        try:
            with open(feed_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            root = ET.fromstring(content)
        except ET.ParseError:
            print(f"[finance] {feed_name} XML解析失败", file=sys.stderr)
            sys.exit(0)

    ns = {'atom': 'http://www.w3.org/2005/Atom',
          'content': 'http://purl.org/rss/1.0/modules/content/',
          'dc': 'http://purl.org/dc/elements/1.1/'}

    items = root.findall('.//item')
    if not items:
        items = root.findall('.//atom:entry', ns)

    for item in items[:15]:
        title_el = item.find('title')
        link_el = item.find('link')
        desc_el = item.find('description')
        date_el = item.find('pubDate')
        content_el = item.find('content:encoded', ns)

        # Atom fallback
        if link_el is None:
            link_el = item.find('atom:link', ns)
            if link_el is not None:
                link_el = type('o', (object,), {'text': link_el.get('href', '')})()
        if title_el is None:
            title_el = item.find('atom:title', ns)
        if date_el is None:
            date_el = item.find('atom:published', ns) or item.find('atom:updated', ns)

        title = (title_el.text or '').strip() if title_el is not None else ''
        link = (link_el.text or '').strip() if link_el is not None else ''
        description = ''
        if content_el is not None and content_el.text:
            description = re.sub(r'<[^>]+>', '', content_el.text)[:400]
        elif desc_el is not None and desc_el.text:
            description = re.sub(r'<[^>]+>', '', desc_el.text)[:400]
        pub_date = (date_el.text or '').strip() if date_el is not None else ''

        if not title or not link:
            continue
        if link in seen_urls:
            continue

        # 时间过滤（允许无日期的通过，避免漏掉）
        dt = parse_date(pub_date)
        if dt and dt < cutoff:
            continue

        articles.append({
            "title": title,
            "link": link,
            "description": re.sub(r'\s+', ' ', description).strip(),
            "pub_date": pub_date[:25],
            "feed_name": feed_name,
            "feed_label": feed_label,
            "region": region,
        })

# 每源最多 5 篇
for a in articles[:5]:
    print(json.dumps(a, ensure_ascii=False))
    with open(seen_file, 'a') as f:
        f.write(a["link"] + "\n")

count = min(len(articles), 5)
print(f"[finance] {feed_name}: {count} 篇", file=sys.stderr)
PYEOF

done

# ── 2. X/Twitter 财经账号抓取（Syndication API）───────────────────────
SEEN_X_FILE="$CACHE/seen_x_ids_${DAY}.txt"
: > "$SEEN_X_FILE"
# V37.8.4 新增：静默失败检测 — 抓到 HTML 但所有推文都超 72h 的账号 = 僵尸嫌疑
ZOMBIE_FILE="$CACHE/zombies_${DAY}.txt"
: > "$ZOMBIE_FILE"
X_FETCH_OK=0
X_FETCH_FAIL=0

for x_entry in "${FINANCE_X_ACCOUNTS[@]}"; do
    IFS='|' read -r X_HANDLE X_LABEL X_REGION <<< "$x_entry"
    RAW_HTML="$CACHE/raw/${X_HANDLE}.html"

    # 抓取 Twitter Syndication API
    XFETCH_OK=false
    for attempt in 1 2; do
        HTTP_CODE=$(curl -sSL --max-time 20 -w '%{http_code}' \
            -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
            -o "$RAW_HTML" \
            "https://syndication.twitter.com/srv/timeline-profile/screen-name/${X_HANDLE}" \
            2>/dev/null) || HTTP_CODE="000"
        if [ "$HTTP_CODE" = "200" ] && [ -s "$RAW_HTML" ]; then
            XFETCH_OK=true
            break
        fi
        sleep "$((attempt * 3))"
    done

    if [ "$XFETCH_OK" != "true" ]; then
        X_FETCH_FAIL=$((X_FETCH_FAIL + 1))
        continue
    fi

    # 解析推文 → JSONL
    $PYTHON3 - "$RAW_HTML" "$SEEN_X_FILE" "$X_HANDLE" "$X_LABEL" "$X_REGION" "$ZOMBIE_FILE" << 'XPYEOF' >> "$ALL_NEW_FILE"
import sys, json, re
from html import unescape
from datetime import datetime, timedelta, timezone

html_file, seen_file, handle, label, region, zombie_file = sys.argv[1:7]
# X 账号用 72h 窗口（覆盖周末，官媒周末发帖少）
CUTOFF = datetime.now(timezone.utc) - timedelta(hours=72)

with open(seen_file) as f:
    seen_ids = set(line.strip() for line in f if line.strip())

with open(html_file, encoding="utf-8", errors="replace") as f:
    html = f.read()

tweets = []
# 诊断计数
diag = {"total": 0, "rt_pure": 0, "short": 0, "seen": 0, "old": 0, "no_data": 0}

next_data = re.search(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    html, re.DOTALL)
if next_data:
    try:
        data = json.loads(next_data.group(1))
        entries = (data.get("props", {})
                      .get("pageProps", {})
                      .get("timeline", {})
                      .get("entries", []))
        for entry in entries:
            if entry.get("type") != "tweet":
                continue
            td = entry.get("content", {}).get("tweet", {})
            if not td:
                continue
            text = td.get("full_text", td.get("text", ""))
            tweet_id = str(td.get("id_str", ""))
            created_at = td.get("created_at", "")
            diag["total"] += 1

            # 跳过纯转推（保留带评论的引用推文）
            if text.startswith("RT @"):
                # 提取 RT 后的原文，如果整条都是 "RT @xxx: 原文" 且无额外内容则跳过
                diag["rt_pure"] += 1
                continue

            # 跳过过短推文（中文 10 字已有信息量）
            clean_for_len = re.sub(r'https?://\S+', '', text).strip()
            if len(clean_for_len) < 10:
                diag["short"] += 1
                continue

            if tweet_id in seen_ids:
                diag["seen"] += 1
                continue

            # 时间过滤
            try:
                dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                if dt < CUTOFF:
                    diag["old"] += 1
                    continue
                pub_date = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pub_date = ""

            # 清理文本
            clean_text = unescape(re.sub(r'https?://t\.co/\S+', '', text)).strip()
            link = f"https://x.com/{handle}/status/{tweet_id}" if tweet_id.isdigit() else ""

            tweets.append({
                "title": clean_text[:120],
                "link": link,
                "description": clean_text[:300],
                "pub_date": pub_date,
                "feed_name": f"X @{handle}",
                "feed_label": label,
                "region": region,
                "tweet_id": tweet_id,
            })
    except (json.JSONDecodeError, KeyError):
        pass
else:
    diag["no_data"] = 1

# 每账号最多 3 条
for t in tweets[:3]:
    print(json.dumps(t, ensure_ascii=False))
    with open(seen_file, 'a') as f:
        f.write(t.get("tweet_id", t["link"]) + "\n")

count = min(len(tweets), 3)
# V37.8.5 三层僵尸检测（闭合 V37.8.4 两个边缘盲区：CNS1952 99% + SingTaoDaily 0-tweet stub）
# 导入纯函数模块以获得可单测的 classify_zombie()；失败时硬退出（不提供 inline fallback，避免血案重演）
import os as _os, sys as _sys
_jobs_dir = _os.environ.get("FINANCE_NEWS_JOBS_DIR")
if _jobs_dir and _jobs_dir not in _sys.path:
    _sys.path.insert(0, _jobs_dir)
from finance_news_zombie import classify_zombie  # noqa: E402
is_zombie_suspect, zombie_tier = classify_zombie(diag, count)
if is_zombie_suspect:
    with open(zombie_file, 'a') as f:
        f.write(f"{handle}\n")

# 始终打印诊断（含 0 条时的过滤原因）
if count > 0:
    print(f"[finance] X @{handle}: {count} 条", file=sys.stderr)
else:
    reasons = []
    if diag["no_data"]:
        reasons.append("无时间线数据")
    if diag["total"] == 0:
        reasons.append("API返回0推文")
    if diag["rt_pure"]:
        reasons.append(f"{diag['rt_pure']}条纯RT")
    if diag["short"]:
        reasons.append(f"{diag['short']}条过短")
    if diag["seen"]:
        reasons.append(f"{diag['seen']}条已见")
    if diag["old"]:
        reasons.append(f"{diag['old']}条超72h")
    reason_str = ", ".join(reasons) if reasons else "未知"
    # V37.8.5 tier 标记：stub（空骨架）/ stale（≥90% 老化），便于日志定位
    prefix = f"⚠️ ZOMBIE嫌疑[{zombie_tier}] " if is_zombie_suspect else ""
    print(f"[finance] X @{handle}: {prefix}0 条（原始{diag['total']}条, 过滤: {reason_str}）", file=sys.stderr)
XPYEOF

    X_FETCH_OK=$((X_FETCH_OK + 1))
done

if [ "$X_FETCH_OK" -gt 0 ] || [ "$X_FETCH_FAIL" -gt 0 ]; then
    log "X/Twitter: ${X_FETCH_OK} 账号成功, ${X_FETCH_FAIL} 失败"
fi
FETCH_ERRORS=$((FETCH_ERRORS + X_FETCH_FAIL))

# V37.8.4 连续 3 天僵尸检测：本次 + 前两天都命中同一个 handle → 告警建议人工复核
if [ -s "$ZOMBIE_FILE" ]; then
    TODAY_COUNT=$(wc -l < "$ZOMBIE_FILE" | tr -d ' ')
    log "X 僵尸嫌疑今日 ${TODAY_COUNT} 个（详见 $ZOMBIE_FILE）"

    # 计算昨天、前天日期（macOS 和 Linux 语法不同，容错处理）
    YESTERDAY=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d 'yesterday' +%Y-%m-%d 2>/dev/null || echo "")
    DAY_BEFORE=$(date -v-2d +%Y-%m-%d 2>/dev/null || date -d '2 days ago' +%Y-%m-%d 2>/dev/null || echo "")
    Y_FILE="$CACHE/zombies_${YESTERDAY}.txt"
    DB_FILE="$CACHE/zombies_${DAY_BEFORE}.txt"

    if [ -n "$YESTERDAY" ] && [ -n "$DAY_BEFORE" ] && [ -s "$Y_FILE" ] && [ -s "$DB_FILE" ]; then
        PERSISTENT=$(sort -u "$ZOMBIE_FILE" | comm -12 - <(sort -u "$Y_FILE") | comm -12 - <(sort -u "$DB_FILE") || true)
        if [ -n "$PERSISTENT" ]; then
            COUNT=$(echo "$PERSISTENT" | wc -l | tr -d ' ')
            log "⚠️ X 账号连续 3 天无新推文（疑似僵尸 ${COUNT} 个）："
            ZOMBIE_LIST=""
            echo "$PERSISTENT" | while IFS= read -r h; do
                [ -z "$h" ] && continue
                log "  - @${h}"
            done
            ZOMBIE_LIST=$(echo "$PERSISTENT" | tr '\n' ',' | sed 's/,$//')
            # V37.8.14: 3 天连续检测必须推送告警（MR-4: 检测无通知 = silent failure）
            if [ "$NOTIFY_LOADED" = true ]; then
                notify "[SYSTEM_ALERT] X 僵尸账号连续 3 天检测命中 ${COUNT} 个: ${ZOMBIE_LIST}。建议人工复核后从 FINANCE_X_ACCOUNTS 移除。" --topic alerts
            fi
        fi
    fi
fi

TOTAL_NEW="$(wc -l < "$ALL_NEW_FILE" | tr -d ' ')"
if [ "$TOTAL_NEW" -eq 0 ]; then
    log "无新文章（${FETCH_ERRORS} 源抓取失败），跳过推送。"
    printf '{"time":"%s","status":"ok","new":0,"errors":%d}\n' "$TS" "$FETCH_ERRORS" > "$STATUS_FILE"
    exit 0
fi

# 统计国内/国际
INTL_COUNT=$($PYTHON3 -c "
import json
with open('$ALL_NEW_FILE') as f:
    print(sum(1 for l in f if json.loads(l).get('region')=='intl'))
")
CN_COUNT=$($PYTHON3 -c "
import json
with open('$ALL_NEW_FILE') as f:
    print(sum(1 for l in f if json.loads(l).get('region')=='cn'))
")
log "共 ${TOTAL_NEW} 篇新文章（国际 ${INTL_COUNT} / 国内 ${CN_COUNT}），${FETCH_ERRORS} 源失败"

# ── 构建 LLM 分析 prompt ──────────────────────────────────────────────
PROMPT_FILE="$CACHE/llm_prompt.txt"
$PYTHON3 - "$ALL_NEW_FILE" << 'PYEOF' > "$PROMPT_FILE"
import sys, json, re

articles = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line:
            articles.append(json.loads(line))

intl = [a for a in articles if a.get("region") == "intl"]
cn = [a for a in articles if a.get("region") == "cn"]

def format_articles(arts, max_n=25):
    lines = []
    for i, a in enumerate(arts[:max_n], 1):
        desc = a.get("description", "")[:150]
        lines.append(f"{i}. [{a['feed_label']}] {a['title']}")
        if a.get("pub_date"):
            lines.append(f"   时间: {a['pub_date']}")
        if desc:
            lines.append(f"   摘要: {desc}")
        lines.append("")
    return "\n".join(lines)

prompt = f"""你是一位资深财经分析师。以下是过去24小时内来自全球权威信源的财经/政策新闻。
请严格基于以下提供的新闻内容，输出结构化分析报告。

⚠️ 严格约束：
- 只使用下方新闻中明确出现的信息，严禁虚构任何事件/政策/数据
- 每条新闻必须标注原始出处（如 [美联储]、[路透社]、[新华财经]）
- 如果某个领域无数据，直接标注"今日无相关信源"

═══ 第一部分：海外新闻（{len(intl)} 条）═══
{format_articles(intl)}

═══ 第二部分：国内/亚太新闻（{len(cn)} 条）═══
{format_articles(cn)}

═══ 请输出以下结构（总字数 800-1200 字）═══

## 📰 今日要闻（按价值排序，最多 8 条）

对每条新闻输出：
- **[来源] 标题**（发布时间）
  💡 价值：⭐~⭐⭐⭐⭐⭐ | 关键点评：一句话分析其影响

## 🌏 海外 vs 国内 对比总结

| 维度 | 海外动向 | 国内动向 |
|------|---------|---------|
| 货币政策 | ... | ... |
| 经济数据 | ... | ... |
| 产业趋势 | ... | ... |
| 风险信号 | ... | ... |

（无数据的维度写"今日无相关信源"，不要编造）

## 💰 一句话投资建议

（基于今日信息，≤50 字，必须有依据）

## ⚠️ 风险提示

（≤30 字，指出最大不确定性）
"""

print(prompt)
PYEOF

# ── 调用 LLM ──────────────────────────────────────────────────────────
LLM_RAW="$CACHE/llm_raw_last.json"
$PYTHON3 -c "
import json
prompt = open('$CACHE/llm_prompt.txt').read()
with open('$CACHE/llm_payload.json', 'w') as f:
    json.dump({
        'model': 'default',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 3000,
        'temperature': 0.3
    }, f, ensure_ascii=False)
"

LLM_OK=false
for attempt in 1 2 3; do
    LLM_RESP=$(curl -s --max-time 180 \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $(echo $REMOTE_API_KEY)" \
        -d "@$CACHE/llm_payload.json" \
        http://127.0.0.1:5001/v1/chat/completions 2>"$CACHE/llm.stderr" || true)

    echo "$LLM_RESP" > "$LLM_RAW"

    LLM_CONTENT=$($PYTHON3 -c "
import json, sys
try:
    r = json.loads(open('$LLM_RAW').read())
    c = r.get('choices',[{}])[0].get('message',{}).get('content','')
    if len(c) > 200:
        print(c)
    else:
        sys.exit(1)
except:
    sys.exit(1)
" 2>/dev/null) && LLM_OK=true && break

    log "WARN: LLM 调用失败 (attempt ${attempt})"
    sleep "$((attempt * 10))"
done

if [ "$LLM_OK" != "true" ]; then
    log "ERROR: LLM 3次调用全部失败，推送原始标题"
    # Fallback: 只推送标题列表
    LLM_CONTENT=$($PYTHON3 -c "
import json
arts = []
with open('$ALL_NEW_FILE') as f:
    for l in f:
        if l.strip(): arts.append(json.loads(l))
lines = ['⚠️ LLM分析失败，以下为原始标题：\n']
for a in arts[:15]:
    lines.append(f'• [{a[\"feed_label\"]}] {a[\"title\"]}')
print('\n'.join(lines))
")
fi

# ── 组装消息 + 推送 ──────────────────────────────────────────────────
MSG_HEADER="📊 每日财经简报 ${DAY}（国际 ${INTL_COUNT} + 国内 ${CN_COUNT} 条 | ${FETCH_ERRORS} 源不可用）"
FULL_MSG="${MSG_HEADER}

${LLM_CONTENT}"

# 截断 WhatsApp 长度限制
WA_MSG="${FULL_MSG:0:3800}"

# ── KB 归档 ──────────────────────────────────────────────────────────
KB_APPEND_SCRIPT="${HOME}/openclaw-model-bridge/kb_append_source.sh"
if [ -f "$KB_APPEND_SCRIPT" ]; then
    echo "$LLM_CONTENT" | bash "$KB_APPEND_SCRIPT" "$KB_SRC" "$DAY" "finance_news"
else
    # fallback: 直接 append
    printf '\n## %s\n\n%s\n' "$DAY" "$LLM_CONTENT" >> "$KB_SRC"
fi

# ── KB notes 写入 ────────────────────────────────────────────────────
if [ -f "$KB_WRITE_SCRIPT" ]; then
    echo "$LLM_CONTENT" | bash "$KB_WRITE_SCRIPT" --title "财经简报 ${DAY}" --tags "finance,policy,daily" --source "finance_news"
fi

# ── 推送（WhatsApp + Discord）────────────────────────────────────────
if [ "$NOTIFY_LOADED" = "true" ]; then
    notify "$WA_MSG" --topic daily
else
    # fallback: 直接推送
    OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
    TO="${OPENCLAW_PHONE:-+85200000000}"
    "$OPENCLAW" message send --to "$TO" --content "$WA_MSG" --channel whatsapp 2>/dev/null || true
    if [ -n "${DISCORD_CH_DAILY:-}" ]; then
        "$OPENCLAW" message send --channel-id "$DISCORD_CH_DAILY" --content "$FULL_MSG" --channel discord 2>/dev/null || true
    fi
fi

# ── 状态记录 ──────────────────────────────────────────────────────────
printf '{"time":"%s","status":"ok","new":%d,"intl":%d,"cn":%d,"errors":%d}\n' \
    "$TS" "$TOTAL_NEW" "$INTL_COUNT" "$CN_COUNT" "$FETCH_ERRORS" > "$STATUS_FILE"

log "完成: ${TOTAL_NEW} 篇 → LLM 分析 → 推送成功"

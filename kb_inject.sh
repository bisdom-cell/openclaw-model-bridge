#!/bin/bash
# kb_inject.sh — 每日 KB 摘要生成（供 LLM 对话时查阅）
# 用法：bash kb_inject.sh [天数，默认3]
# 功能：从 notes + sources 提取最近 N 天精华 → 写入 ~/.kb/daily_digest.md
#        LLM 在对话中可通过 read 工具读取此文件，实现"知识库可查"
# 建议 cron：每天 07:00 运行，确保 LLM 拿到最新内容
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

DAYS="${1:-3}"
KB_DIR="${KB_BASE:-/Users/bisdom/.kb}"
DIGEST_FILE="$KB_DIR/daily_digest.md"
INDEX="$KB_DIR/index.json"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')"
DATE=$(date +%Y%m%d)

log() { echo "[$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')] kb_inject: $1"; }

mkdir -p "$KB_DIR"

# ── 生成摘要（Python 一次性处理，避免多次 shell 调用） ──
python3 - "$KB_DIR" "$DAYS" "$TS" "$DIGEST_FILE" "$INDEX" << 'PYEOF'
import os, sys, json, glob
from datetime import datetime, timedelta
from collections import Counter

kb_dir, days_str, ts, digest_file, index_path = sys.argv[1:6]
days = int(days_str)
cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
cutoff_dash = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

sections = []

# ── 统计 ──
try:
    with open(index_path) as f:
        entries = json.load(f).get('entries', [])
    total = len(entries)
    recent = [e for e in entries if e.get('date', '') >= cutoff]
    tags = Counter()
    for e in recent:
        tags.update(e.get('tags', []))
    top_tags = ', '.join(t for t, _ in tags.most_common(5)) or '无'
except (OSError, json.JSONDecodeError):
    total = 0
    recent = []
    top_tags = '无'

sections.append(f"""# KB 每日摘要
> 更新时间：{ts} | 范围：最近 {days} 天 | 总条目：{total}
> 热门标签：{top_tags}
> 本期新增：{len(recent)} 条""")

# ── Notes 精华 ──
notes_dir = os.path.join(kb_dir, 'notes')
note_items = []
total_chars = 0
MAX_NOTE_CHARS = 3000

for f in sorted(glob.glob(os.path.join(notes_dir, '*.md')), reverse=True):
    basename = os.path.basename(f)
    file_date = basename[:8]
    if file_date < cutoff:
        break
    try:
        with open(f) as fh:
            content = fh.read().strip()
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        # 提取第一个有意义的行作为标题
        lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
        title = lines[0][:120] if lines else basename
        snippet = content[:200].replace('\n', ' ')
        if total_chars + len(snippet) > MAX_NOTE_CHARS:
            break
        note_items.append(f"- [{file_date}] {title}")
        total_chars += len(snippet)
    except OSError:
        continue

if note_items:
    sections.append("## 近期笔记\n" + '\n'.join(note_items[:20]))

# ── Sources 归档精华（每个来源取最近内容） ──
sources_map = {
    'arxiv_daily.md': ('ArXiv 论文', 1500),
    'hn_daily.md': ('HackerNews 热帖', 1500),
    'hf_papers_daily.md': ('HuggingFace 论文', 1500),
    'semantic_scholar_daily.md': ('Semantic Scholar 论文', 1500),
    'dblp_daily.md': ('DBLP CS论文', 1500),
    'acl_anthology.md': ('ACL Anthology NLP论文', 1000),
    'pwc_daily.md': ('Papers with Code 论文+代码', 1500),
    'freight_daily.md': ('货代动态', 800),
    'openclaw_official.md': ('OpenClaw 更新', 800),
}

# 生成日期匹配模式
date_patterns = []
for i in range(days):
    d = datetime.now() - timedelta(days=i)
    date_patterns.append(d.strftime('%Y-%m-%d'))
    date_patterns.append(d.strftime('%Y%m%d'))

for filename, (label, max_chars) in sources_map.items():
    path = os.path.join(kb_dir, 'sources', filename)
    if not os.path.isfile(path):
        continue
    try:
        with open(path) as f:
            all_lines = f.readlines()
    except OSError:
        continue

    # 提取含日期的行及其后续行
    relevant = []
    include_next = 0
    for line in all_lines:
        if any(dp in line for dp in date_patterns):
            relevant.append(line.rstrip())
            include_next = 4  # 包含后续4行作为上下文
        elif include_next > 0:
            relevant.append(line.rstrip())
            include_next -= 1

    if relevant:
        text = '\n'.join(relevant)[:max_chars]
        sections.append(f"## {label}\n{text}")

# ── 用户反馈（闭环进化的关键数据） ──
feedback_items = []
for f in sorted(glob.glob(os.path.join(notes_dir, '*.md')), reverse=True):
    basename = os.path.basename(f)
    file_date = basename[:8]
    if file_date < cutoff:
        break
    try:
        with open(f) as fh:
            content = fh.read()
        # 识别 feedback 类型的笔记
        if 'type: feedback' in content[:200] or 'tags: [feedback]' in content[:200]:
            # 提取正文
            body = content
            if body.startswith('---'):
                parts = body.split('---', 2)
                if len(parts) >= 3:
                    body = parts[2].strip()
            lines = [l.strip() for l in body.split('\n') if l.strip() and not l.startswith('#') and not l.startswith('20')]
            if lines:
                feedback_items.append(f"- [{file_date}] {lines[0][:150]}")
    except OSError:
        continue

if feedback_items:
    sections.append("## 用户反馈\n以下是用户最近的反馈，Claude Code 开工时请参考并采取行动：\n" + '\n'.join(feedback_items[:10]))

# ── 使用提示 ──
sections.append("""---
> 此文件由 kb_inject.sh 每日自动生成。
> 用户询问最近资讯/论文/新闻时，请参考以上内容回答。
> 需要更详细信息，可用 read 工具读取 ~/.kb/sources/ 下的完整归档。""")

# ── 原子写入（tmp + replace，防 crash 损坏）──
output = '\n\n'.join(sections) + '\n'
tmp = digest_file + '.tmp'
with open(tmp, 'w') as f:
    f.write(output)
import os; os.replace(tmp, digest_file)

print(f"OK: {digest_file} ({len(output)} chars, {len(note_items)} notes)")
PYEOF

log "摘要已生成: $DIGEST_FILE"

# ── 推送 WhatsApp 每日摘要 ──
PHONE="${OPENCLAW_PHONE:-+85200000000}"

# 提取摘要关键信息作为推送内容（WhatsApp 消息不宜过长）
WA_MSG=$(python3 - "$DIGEST_FILE" << 'PYEOF'
import sys, re
from datetime import datetime

try:
    with open(sys.argv[1]) as f:
        content = f.read()
except OSError:
    print("（摘要文件读取失败）")
    sys.exit(0)

lines = content.split('\n')
today = datetime.now().strftime('%Y-%m-%d')

# 提取头部统计
stats_parts = []
for line in lines[:6]:
    if line.startswith('>'):
        stats_parts.append(line.lstrip('> ').strip())
stats_line = ' | '.join(stats_parts[:2]) if stats_parts else ''

# 按 ## 标题分割各来源章节
known_sources = ['ArXiv 论文', 'HackerNews 热帖', 'HuggingFace 论文', 'Semantic Scholar 论文', 'DBLP CS论文', 'ACL Anthology NLP论文', '货代动态', 'OpenClaw 更新']
source_content = {}
current_source = None
for line in lines:
    if line.startswith('## '):
        title = line[3:].strip()
        if title in known_sources:
            current_source = title
        continue
    if current_source and line.strip():
        if current_source not in source_content:
            source_content[current_source] = []
        source_content[current_source].append(line.strip())

# ── 提取结构化条目（中英文 + URL） ──

def extract_arxiv(lines):
    """ArXiv 格式: *中文标题* / 作者 / 链接：url / 贡献 / 价值"""
    items = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('*') and line.endswith('*'):
            cn_title = line.strip('*').strip()
            url = ''
            author = ''
            # 扫描后续行找链接和作者
            for j in range(i+1, min(i+5, len(lines))):
                if lines[j].startswith('链接：'):
                    url = lines[j].replace('链接：', '').strip()
                elif lines[j].startswith('作者：'):
                    author = lines[j]
            if cn_title:
                items.append({'cn': cn_title, 'url': url, 'meta': author})
        i += 1
    return items

def extract_hn(lines):
    """HN 格式: - **[Title](url)** | date | 要点：中文 | stars"""
    items = []
    for line in lines:
        m = re.match(r'-\s*\*\*\[([^\]]+)\]\(([^)]+)\)\*\*\s*\|[^|]*\|\s*要点：([^|]+)', line)
        if m:
            en_title, url, point = m.group(1), m.group(2), m.group(3).strip()
            items.append({'en': en_title, 'cn': point, 'url': url})
    return items

def extract_freight(lines):
    """货代格式: 自由文本，提取有意义的行"""
    items = []
    for line in lines:
        if re.match(r'^(20\d{2}[-/]\d{2}|📊|🚢\s*货代商机)', line):
            continue
        if len(line) > 10 and not line.startswith('行动：'):
            items.append({'cn': line})
    return items

def extract_openclaw(lines):
    """OpenClaw 格式: 版本号 + 链接"""
    version = ''
    url = ''
    for line in lines:
        if line.startswith('- *v'):
            version = line.strip('- *').strip()
        if '链接:' in line or '链接：' in line:
            url = re.sub(r'^.*链接[：:]\s*', '', line).strip()
    if version:
        return [{'cn': f'新版本 {version}', 'url': url}]
    return []

# 提取各来源的结构化数据（HF/S2/DBLP/ACL 与 ArXiv 同格式：*标题* / 作者 / 链接 / 贡献 / 价值）
arxiv_items = extract_arxiv(source_content.get('ArXiv 论文', []))
hf_items = extract_arxiv(source_content.get('HuggingFace 论文', []))
s2_items = extract_arxiv(source_content.get('Semantic Scholar 论文', []))
dblp_items = extract_arxiv(source_content.get('DBLP CS论文', []))
acl_items = extract_arxiv(source_content.get('ACL Anthology NLP论文', []))
hn_items = extract_hn(source_content.get('HackerNews 热帖', []))
freight_items = extract_freight(source_content.get('货代动态', []))
oc_items = extract_openclaw(source_content.get('OpenClaw 更新', []))

# ── 选取今日重点 ──
top_item = None
all_paper_items = arxiv_items + hf_items + s2_items + dblp_items + acl_items
if hn_items:
    top_item = f"{hn_items[0]['en']} — {hn_items[0]['cn']}"
elif all_paper_items:
    top_item = all_paper_items[0]['cn']

# ── 组装消息 ──
msg = f"📰 KB 每日摘要 ({today})\n"
if stats_line:
    msg += f"{stats_line}\n"

if top_item:
    msg += f"\n🔑 今日重点: {top_item}\n"

# 论文来源统一格式输出（每个来源最多2篇，控制总长度）
paper_sources = [
    ('📄 ArXiv', arxiv_items),
    ('🤗 HF精选', hf_items),
    ('📈 S2高引', s2_items),
    ('📚 DBLP', dblp_items),
    ('📝 ACL', acl_items),
]
for emoji_label, items in paper_sources:
    if items:
        msg += f"\n{emoji_label}:\n"
        for item in items[:2]:
            msg += f"  {item['cn']}\n"
            if item.get('url'):
                msg += f"  {item['url']}\n"

# HN（英文标题 + 中文要点 + URL）
if hn_items:
    msg += "\n🔥 HackerNews:\n"
    for item in hn_items[:3]:
        msg += f"  {item['en']}\n"
        msg += f"  → {item['cn']}\n"
        if item.get('url'):
            msg += f"  {item['url']}\n"

# 货代
if freight_items:
    msg += "\n🚢 货代动态:\n"
    for item in freight_items[:2]:
        msg += f"  {item['cn']}\n"

# OpenClaw
if oc_items:
    msg += "\n⚙️ OpenClaw:\n"
    for item in oc_items[:1]:
        msg += f"  {item['cn']}\n"
        if item.get('url'):
            msg += f"  {item['url']}\n"

msg += "\n💡 回复任何话题可深入讨论"
print(msg[:1500])
PYEOF
)

SEND_ERR=$(mktemp)
if openclaw message send --target "$PHONE" --message "$WA_MSG" --json >/dev/null 2>"$SEND_ERR"; then
    log "每日摘要已推送 WhatsApp"
else
    log "WARNING: WhatsApp 推送失败: $(head -3 "$SEND_ERR" 2>/dev/null)"
    # 本地告警回退（V30: 监控不依赖被监控对象）
    echo "[$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')] kb_inject: WhatsApp 推送失败" >> ~/.openclaw_alerts.log 2>/dev/null || true
fi
rm -f "$SEND_ERR"

# ── 同步到 workspace CLAUDE.md（每个新 session 自动加载） ──
WORKSPACE_DIR="$HOME/.openclaw/workspace/.openclaw"
WORKSPACE_MD="$WORKSPACE_DIR/CLAUDE.md"
mkdir -p "$WORKSPACE_DIR"

# 静态 PA 指引 + 运维知识精华 + 动态 KB 摘要（原子写入）
WORKSPACE_TMP="$WORKSPACE_MD.tmp"
cat > "$WORKSPACE_TMP" << 'MDEOF'
# Wei — 操作手册

> 身份、宪法、行为准则见 SOUL.md（已自动加载）。本文件提供操作细节。

## 系统架构
- **Gateway** (:18789) — WhatsApp 接入、媒体存储、工具执行
- **Tool Proxy** (:5002) — 工具过滤、图片 base64 注入、SSE 转换、token 监控
- **Adapter** (:5001) — 认证、多模态路由（文本→Qwen3-235B，图片→Qwen2.5-VL-72B）、Fallback 降级
- **远程 GPU** — Qwen3-235B（文本，262K context）+ Qwen2.5-VL-72B（视觉理解）

## 运维命令
```
bash ~/restart.sh                                      # 一键重启
curl http://localhost:5002/health                      # 健康检查
python3 ~/status_update.py --read --human              # 查看三方状态
bash ~/openclaw-model-bridge/preflight_check.sh --full # 全面体检
```

## 运维原则
1. **故障先查自身代码** — 从自己的代码找 bug，不轻易归因上游
2. **故障先回滚** — `git checkout v26-snapshot` 恢复服务，再排查
3. **工具数量 ≤ 12** / 每任务调用 ≤ 2 次 / 请求体 ≤ 200KB
4. **纯推理绕过 Gateway** — 直接 curl proxy:5002

## 反馈捕获（重要！）
当用户对推送内容（趋势报告、ArXiv、HN、周报等）给出反馈时，**必须**用 exec 工具保存到 KB：
```
bash ~/kb_write.sh "用户的反馈内容" "feedback" "feedback"
```
反馈示例：
- "趋势报告里 npm 是噪音" → `bash ~/kb_write.sh "趋势报告噪音词：npm，应加入停用词" "feedback" "feedback"`
- "这篇论文很有用" → `bash ~/kb_write.sh "用户认为有价值：[论文标题]" "feedback" "feedback"`
- "以后少推货代的" → `bash ~/kb_write.sh "用户偏好：减少货代动态推送频率" "feedback" "feedback"`

判断规则：用户回复中包含评价（好/不好/没用/太多/太少/噪音/有用/关注/不关注）时触发保存。
不确定时宁可多存，反馈数据是系统进化的燃料。

## 深度文档（按需查阅）
遇到架构/配置/故障排查问题时，用 read 工具查阅：
- `~/.kb/docs/config.md` — 完整系统配置、环境变量、cron 任务、历史变更
- `~/.kb/docs/GUIDE.md` — 集成指南 + 26 条生产踩坑经验（中英双语）
- `~/.kb/docs/CLAUDE.md` — 项目全貌、版本历史、工作原则、待办清单

MDEOF

# 追加三方共享意识快照（status.json 直接注入，PA 无需调用工具即可感知）
STATUS_SNAPSHOT=$(python3 "$HOME/status_update.py" --read --human 2>/dev/null || echo "（状态暂不可用）")
cat >> "$WORKSPACE_TMP" << MDEOF
## 三方共享意识（实时快照）
以下是当前系统状态，每次 kb_inject 运行时自动刷新。
回答用户关于项目进展、系统状态、优先级等问题时，直接参考此快照。
如需最新数据，用 exec 工具执行：\`python3 ~/status_update.py --read --human\`

$STATUS_SNAPSHOT

MDEOF

# 追加动态 KB 摘要
cat >> "$WORKSPACE_TMP" << MDEOF
## 知识库
以下是最近的知识库摘要，直接参考回答用户关于近期资讯、论文、新闻的问题：

$(cat "$DIGEST_FILE" 2>/dev/null || echo '（摘要暂未生成）')

## 查询更多
- 完整归档：\`~/.kb/sources/\` 目录下各来源文件
- 笔记详情：\`~/.kb/notes/\` 目录下按时间戳命名的 .md 文件
- KB 搜索：\`bash ~/kb_search.sh "关键词"\`

## RAG 语义搜索
当用户询问知识库中的内容时，优先使用 RAG 语义搜索获取精准上下文：
\`python3 ~/kb_rag.py --context "用户的问题"\`
返回与问题最相关的 KB 片段，可直接参考回答。
示例：
- "Qwen3 有什么特点" → \`python3 ~/kb_rag.py --context "Qwen3 模型特点"\`
- "最近有什么AI论文" → \`python3 ~/kb_rag.py --context "recent AI papers"\`
- "货代运费趋势" → \`python3 ~/kb_rag.py --context "shipping freight rates"\`

## 多媒体搜索
当用户询问照片、图片、录音、视频等媒体文件时，使用 exec 工具执行：
\`python3 ~/mm_search.py "用户描述的内容"\`
支持自然语言查询，例如：
- "找一下猫的照片" → \`python3 ~/mm_search.py "猫的照片"\`
- "最近的会议录音" → \`python3 ~/mm_search.py "会议录音"\`
- "上周的PDF文档" → \`python3 ~/mm_search.py "PDF文档"\`
返回结果包含文件路径，可用 read 工具查看或告知用户路径。
查看索引统计：\`python3 ~/mm_search.py --stats\`

## 数据清洗
当用户发送 CSV/Excel/JSON/TSV 文件并要求清洗数据时，使用 data_clean 工具。
用户上传的文件存储在 \`~/.openclaw/media/inbound/\` 目录下。

**第1步：诊断** — 调用 data_clean 工具：action=profile, file=~/.openclaw/media/inbound/<文件名>
→ 返回 JSON 质量报告，向用户解释发现的问题

**第2步：确认清洗方案** — 根据 profile 结果中的 issues，向用户建议操作并等待确认。

**第3步：执行清洗** — 调用 data_clean 工具：action=execute, file=同上, ops=trim,dedup,fix_dates
可用操作（action=list_ops 查看）: trim(去空格) dedup(去重) dedup_near(近似去重) fix_dates(统一日期) fix_case(统一大小写) fill_missing(标记缺失) remove_test(去测试数据)

**第4步：展示报告** — 用 read 工具读取 \`~/.data_clean/workspace/report.md\`

**推荐操作顺序**: trim → dedup → fix_dates → fix_case → fill_missing → remove_test
MDEOF

mv "$WORKSPACE_TMP" "$WORKSPACE_MD"
log "workspace CLAUDE.md 已同步 ($(wc -c < "$WORKSPACE_MD" | tr -d ' ') bytes)"

# ── 同步 SOUL.md（PA 灵魂文件，OpenClaw 最高优先级加载）──
SOUL_SRC="$HOME/openclaw-model-bridge/SOUL.md"
SOUL_DST="$HOME/.openclaw/workspace/SOUL.md"
if [ -f "$SOUL_SRC" ]; then
    cp "$SOUL_SRC" "$SOUL_DST"
    log "SOUL.md 已同步 ($(wc -c < "$SOUL_DST" | tr -d ' ') bytes)"
fi

# ── 权限收紧（防止 other 用户读取 KB 数据）──
chmod 750 "$KB_DIR" 2>/dev/null || true
chmod 640 "$KB_DIR/status.json" 2>/dev/null || true
chmod 640 "$KB_DIR/index.json" 2>/dev/null || true

# ── rsync 备份 ──
rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

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

try:
    with open(sys.argv[1]) as f:
        content = f.read()
except OSError:
    print("（摘要文件读取失败）")
    sys.exit(0)

lines = content.split('\n')

# 提取头部统计（> 开头的行）
stats_lines = []
for line in lines[:6]:
    if line.startswith('>'):
        stats_lines.append(line.lstrip('> ').strip())

# 提取近期笔记（- [日期] 格式，只取8位数字日期开头的）
note_lines = []
in_notes = False
for line in lines:
    if line.startswith('## 近期笔记'):
        in_notes = True
        continue
    if in_notes:
        if line.startswith('## '):
            break
        stripped = line.strip()
        if stripped.startswith('- [') and re.match(r'- \[20\d{6}\]', stripped):
            note_lines.append(stripped)
            if len(note_lines) >= 5:
                break

# 来源：只取四大来源的 ## 标题（固定白名单，避免抓到内容行）
known_sources = {'ArXiv 论文', 'HackerNews 热帖', '货代动态', 'OpenClaw 更新'}
active_sources = []
for line in lines:
    if line.startswith('## '):
        title = line[3:].strip()
        if title in known_sources:
            active_sources.append(title)

# 组装消息
msg = "📰 KB 每日摘要\n"
for s in stats_lines:
    msg += f"  {s}\n"
if active_sources:
    msg += f"📁 今日来源: {' / '.join(active_sources)}\n"
if note_lines:
    msg += "\n📝 近期笔记:\n" + '\n'.join(note_lines[:5]) + "\n"

msg += "\n💡 详细内容可在对话中直接提问"
print(msg[:800])
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
# Wei — Personal AI Assistant

## 身份
你是 Wei，一个专业的个人 AI 助手。用中文回复，除非用户用其他语言。

## 系统架构（简版）
你运行在一个四层中间件系统上：
- **Gateway** (:18789) — WhatsApp 接入、媒体存储、工具执行
- **Tool Proxy** (:5002) — 工具过滤(24→12)、图片 base64 注入、SSE 转换、token 监控
- **Adapter** (:5001) — 认证、多模态路由（文本→Qwen3-235B，图片→Qwen2.5-VL-72B）、Fallback 降级到 Gemini
- **远程 GPU** — Qwen3-235B（文本，262K context）+ Qwen2.5-VL-72B（视觉理解）

## 核心运维原则
1. **故障先查自身代码** — 排查问题从自己的代码和架构找 bug（shell 数据传递、cron 环境、进程管理），不轻易归因于上游服务不稳定
2. **故障先回滚** — 线上故障先 `git checkout v26-snapshot` 恢复服务，再排查根因
3. **一键重启**：`bash ~/restart.sh`（Proxy + Adapter + Gateway）
4. **健康检查**：`curl http://localhost:5002/health` → `{"ok":true,"proxy":true,"adapter":true}`
5. **工具数量 ≤ 12** — 超出导致模型混乱；每任务工具调用 ≤ 2 次
6. **请求体 ≤ 200KB** — 超出 280KB 硬限制后端无有效报错
7. **纯推理任务绕过 Gateway** — 直接 curl 调 proxy:5002，不注入 tools，避免模型失控循环调用
8. **cron 脚本必须 `export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"`**
9. **进程管理单一主控** — Gateway 由 launchd 管理，不加额外 watchdog

## 常用运维命令
```
bash ~/restart.sh                          # 一键重启
curl http://localhost:5002/health          # 健康检查
python3 ~/openclaw-model-bridge/test_tool_proxy.py   # 运行单测
python3 ~/openclaw-model-bridge/check_registry.py    # 校验任务注册表
bash ~/openclaw-model-bridge/preflight_check.sh      # 全面体检（dev）
bash ~/openclaw-model-bridge/preflight_check.sh --full  # 全面体检（含连通性）
```

## 项目状态（三方共享）
用 exec 工具查看当前项目状态：
\`python3 ~/status_update.py --read --human\`

当用户提到优先级变更、新任务、完成任务时，更新状态：
- 新增任务：\`python3 ~/status_update.py --add priorities '{"task":"任务名","status":"active","note":"说明"}' --by pa\`
- 完成任务：\`python3 ~/status_update.py --update-priority "任务名" status done --by pa\`
- 记录反馈：\`python3 ~/status_update.py --add feedback "反馈内容" --by pa\`
- 设置焦点：\`python3 ~/status_update.py --focus "本周重点" --by pa\`

当用户问"最近在做什么"、"项目状态"、"进展如何"时，先读取 status.json 再回答。

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
MDEOF

mv "$WORKSPACE_TMP" "$WORKSPACE_MD"
log "workspace CLAUDE.md 已同步 ($(wc -c < "$WORKSPACE_MD" | tr -d ' ') bytes)"

# ── 权限收紧（防止 other 用户读取 KB 数据）──
chmod 750 "$KB_DIR" 2>/dev/null || true
chmod 640 "$KB_DIR/status.json" 2>/dev/null || true
chmod 640 "$KB_DIR/index.json" 2>/dev/null || true

# ── rsync 备份 ──
rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

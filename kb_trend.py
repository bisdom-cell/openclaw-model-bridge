#!/usr/bin/env python3
"""
kb_trend.py — KB 周趋势报告
从 ArXiv + HN 等来源中提取本周 vs 上周的关键词频率变化，
识别上升趋势、新出现热词、消退话题，调用 LLM 生成趋势分析报告。

用法：python3 kb_trend.py [--weeks 2] [--no-llm] [--json]
cron：每周六 09:00 运行
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
KB_DIR = os.environ.get("KB_BASE", os.path.expanduser("~/.kb"))
PROXY_URL = "http://localhost:5002/v1/chat/completions"
REPORT_DIR = os.path.join(KB_DIR, "trends")
PHONE = os.environ.get("OPENCLAW_PHONE", "+85200000000")

# 停用词（中英文混合，过滤无意义高频词）
STOP_WORDS = {
    # English
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need", "dare",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "and", "but", "or", "if", "because", "while", "until", "about",
    "this", "that", "these", "those", "it", "its", "we", "our", "they",
    "their", "you", "your", "he", "she", "his", "her", "i", "me", "my",
    "what", "which", "who", "whom", "up", "also", "just", "new", "one",
    "two", "using", "based", "via", "show", "use", "used", "paper",
    "results", "approach", "method", "propose", "proposed", "model",
    "data", "performance", "task", "tasks", "learning", "training",
    # GitHub Issues / 运维噪音词（高频但无趋势分析价值）
    "bug", "fix", "fixed", "fail", "failed", "error", "issue", "issues",
    "missing", "wrong", "broken", "crash", "hang", "stuck",
    "message", "send", "sent", "receive", "reply", "replies",
    "run", "running", "start", "stop", "restart", "kill",
    "file", "path", "dir", "log", "config", "setting", "settings",
    "add", "added", "remove", "removed", "update", "updated", "change",
    "check", "test", "tests", "debug", "version", "release",
    "work", "working", "doesn", "didn", "isn", "won", "don",
    "want", "like", "get", "got", "set", "try", "make", "made",
    "still", "seem", "seems", "since", "already", "even",
    "would", "think", "know", "see", "look", "way", "well",
    "windows", "linux", "macos", "mac", "ubuntu",
    "http", "https", "www", "com", "org", "html", "json", "yaml",
    "github.com", "news.ycombinator.com", "arxiv.org",
    "outbound", "inbound", "webhook", "endpoint", "request", "response",
    "item", "abs", "id",
    # 中文通用词（无AI领域特殊含义）
    "的", "了", "在", "是", "和", "与", "对", "为", "从", "到",
    "可以", "通过", "进行", "使用", "一个", "我们", "提出", "方法",
    "基于", "实现", "研究", "问题", "系统", "模型", "数据",
    "链接", "作者", "日期", "贡献", "价值",
}

# 有意义的 AI/Tech 领域关键词模式（优先匹配）
DOMAIN_PATTERNS = [
    # 模型架构
    r"transformer", r"attention", r"moe", r"mixture.of.experts",
    r"diffusion", r"autoregressive", r"encoder", r"decoder",
    # 训练方法
    r"rlhf", r"dpo", r"sft", r"fine.?tun", r"pre.?train", r"distill",
    r"quantiz", r"pruning", r"lora", r"qlora", r"adapter",
    # 能力
    r"reasoning", r"chain.of.thought", r"cot", r"tool.?use", r"function.?call",
    r"multimodal", r"vision", r"speech", r"audio", r"video",
    r"retrieval", r"rag", r"embedding", r"vector",
    r"agent", r"agentic", r"planning", r"code.?gen",
    # 模型名
    r"gpt", r"claude", r"gemini", r"llama", r"qwen", r"mistral",
    r"deepseek", r"phi", r"gemma", r"command.r",
    # 安全/对齐
    r"alignment", r"safety", r"jailbreak", r"red.?team", r"guardrail",
    r"hallucination", r"grounding", r"factual",
    # 基础设施
    r"inference", r"serving", r"vllm", r"trt.?llm", r"onnx",
    r"edge", r"on.?device", r"mobile",
    # 应用
    r"chatbot", r"copilot", r"assistant", r"autonomous",
    r"benchmark", r"eval", r"leaderboard",
]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] kb_trend: {msg}", flush=True)


# ---------------------------------------------------------------------------
# 文本提取
# ---------------------------------------------------------------------------

def extract_period_text(kb_dir, start_date, end_date):
    """提取指定日期范围内的所有 KB 文本（notes + sources）。"""
    texts = []

    # Notes（文件名格式 YYYYMMDDHHMMSS.md）
    notes_dir = os.path.join(kb_dir, "notes")
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")
    for f in glob.glob(os.path.join(notes_dir, "*.md")):
        basename = os.path.basename(f)
        file_date = basename[:8]
        if start_str <= file_date <= end_str:
            try:
                with open(f) as fh:
                    content = fh.read()
                # 去掉 frontmatter
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        content = parts[2]
                texts.append(content.strip())
            except OSError:
                continue

    # Sources（按日期行匹配）
    sources_dir = os.path.join(kb_dir, "sources")
    date_patterns = set()
    d = start_date
    while d <= end_date:
        date_patterns.add(d.strftime("%Y-%m-%d"))
        date_patterns.add(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    for src_file in glob.glob(os.path.join(sources_dir, "*.md")):
        try:
            with open(src_file) as f:
                lines = f.readlines()
        except OSError:
            continue
        relevant = []
        include_next = 0
        for line in lines:
            if any(dp in line for dp in date_patterns):
                relevant.append(line.rstrip())
                include_next = 6
            elif include_next > 0:
                relevant.append(line.rstrip())
                include_next -= 1
        if relevant:
            texts.append("\n".join(relevant))

    return "\n\n".join(texts)


# ---------------------------------------------------------------------------
# 关键词提取
# ---------------------------------------------------------------------------

def tokenize(text):
    """简单分词：英文按空格 + 中文按字符组。"""
    # 先移除 URL（避免 URL 碎片污染关键词）
    text_clean = re.sub(r"https?://\S+", " ", text)
    # 提取英文词（2字符以上，排除纯数字和短碎片）
    en_words = re.findall(r"[a-zA-Z][a-zA-Z0-9]{1,30}", text_clean.lower())
    # 提取中文词组（2-4字）
    zh_words = re.findall(r"[\u4e00-\u9fff]{2,4}", text_clean)
    return en_words + zh_words


def extract_keywords(text, top_n=100):
    """提取文本中的关键词频率。"""
    words = tokenize(text)
    # 过滤停用词
    filtered = [w for w in words if w not in STOP_WORDS and len(w) > 1]
    counter = Counter(filtered)

    # 领域关键词加权
    domain_boost = Counter()
    for word, count in counter.items():
        for pattern in DOMAIN_PATTERNS:
            if re.search(pattern, word, re.IGNORECASE):
                domain_boost[word] += count  # 双倍权重
                break

    boosted = counter + domain_boost
    return boosted.most_common(top_n)


def compute_trends(this_week_kw, last_week_kw):
    """比较两周关键词，返回上升/新出现/消退趋势。"""
    this_dict = dict(this_week_kw)
    last_dict = dict(last_week_kw)
    all_words = set(this_dict.keys()) | set(last_dict.keys())

    rising = []    # 频率显著上升
    emerging = []  # 本周新出现（上周没有）
    fading = []    # 本周消退（上周有但本周没有或大幅下降）

    for word in all_words:
        this_count = this_dict.get(word, 0)
        last_count = last_dict.get(word, 0)

        if last_count == 0 and this_count >= 3:
            emerging.append((word, this_count))
        elif this_count == 0 and last_count >= 3:
            fading.append((word, last_count))
        elif last_count > 0 and this_count > 0:
            ratio = this_count / last_count
            if ratio >= 1.5 and this_count >= 3:
                rising.append((word, this_count, last_count, ratio))
            elif ratio <= 0.5 and last_count >= 3:
                fading.append((word, last_count))

    rising.sort(key=lambda x: x[3], reverse=True)
    emerging.sort(key=lambda x: x[1], reverse=True)
    fading.sort(key=lambda x: x[1], reverse=True)

    return rising[:20], emerging[:15], fading[:10]


# ---------------------------------------------------------------------------
# LLM 分析
# ---------------------------------------------------------------------------

def llm_analyze(rising, emerging, fading, this_week_text_sample):
    """调用 LLM 分析趋势数据，返回分析文本。"""
    rising_str = "\n".join(
        f"  - {w}: {tc}次(本周) vs {lc}次(上周), +{r:.0%}"
        for w, tc, lc, r in rising[:15]
    ) or "  （无显著上升）"

    emerging_str = "\n".join(
        f"  - {w}: {c}次（上周未出现）"
        for w, c in emerging[:10]
    ) or "  （无新出现词）"

    fading_str = "\n".join(
        f"  - {w}: 上周{c}次，本周消失或大幅下降"
        for w, c in fading[:10]
    ) or "  （无消退词）"

    # 截取样本文本
    sample = this_week_text_sample[:4000]

    prompt = f"""你是一位 AI 技术趋势分析师。以下是从知识库（ArXiv 论文摘要 + HackerNews 热帖 + 技术笔记）中提取的本周 vs 上周关键词变化数据。

## 上升趋势（频率显著增加）
{rising_str}

## 新出现热词（上周未出现，本周频繁出现）
{emerging_str}

## 消退话题（上周热门，本周减少或消失）
{fading_str}

## 本周内容样本（供参考）
{sample}

请完成以下分析（中文，总字数 500 字以内）：

1. **趋势解读**（3-5条）：这些关键词变化反映了 AI 领域的哪些趋势？为什么会出现这些变化？
2. **值得关注**（2-3条）：哪些新出现的话题可能成为下一个热点？给出判断依据
3. **行动建议**（2-3条）：基于这些趋势，建议关注或学习什么？
4. **下周预测**（1-2条）：基于当前趋势，预测下周可能出现的新热点"""

    payload = json.dumps({
        "model": "any",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200,
    })

    try:
        req = Request(PROXY_URL, data=payload.encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=90) as resp:
            data = json.load(resp)
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"LLM 分析失败: {e}")
        return ""


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def generate_report(rising, emerging, fading, llm_result, this_kw, last_kw,
                    this_text_len, last_text_len, weeks):
    """生成 Markdown 格式的趋势报告。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    date_str = datetime.now().strftime("%Y%m%d")

    sections = []
    sections.append(f"""---
date: {date_str}
type: trend_report
period: {weeks}w
---

# AI 周趋势报告 {date_str}
> 生成时间：{ts} | 对比周期：{weeks} 周
> 本周文本量：{this_text_len:,} 字 | 上周文本量：{last_text_len:,} 字""")

    # 上升趋势
    if rising:
        lines = [f"| {w} | {tc} | {lc} | +{r:.0%} |" for w, tc, lc, r in rising[:15]]
        sections.append("## 📈 上升趋势\n| 关键词 | 本周 | 上周 | 变化 |\n|--------|------|------|------|\n" + "\n".join(lines))

    # 新出现
    if emerging:
        lines = [f"- **{w}** ({c}次)" for w, c in emerging[:10]]
        sections.append("## 🆕 新出现热词\n" + "\n".join(lines))

    # 消退
    if fading:
        lines = [f"- ~~{w}~~ (上周{c}次)" for w, c in fading[:10]]
        sections.append("## 📉 消退话题\n" + "\n".join(lines))

    # 本周 Top 20
    if this_kw:
        lines = [f"| {w} | {c} |" for w, c in this_kw[:20]]
        sections.append("## 🔑 本周高频词 Top 20\n| 关键词 | 频次 |\n|--------|------|\n" + "\n".join(lines))

    # LLM 分析
    if llm_result:
        sections.append(f"## 🤖 LLM 趋势分析\n\n{llm_result}")
    else:
        sections.append("## 🤖 LLM 趋势分析\n\n（LLM 分析不可用，请参考上方数据自行判断）")

    return "\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# WhatsApp 推送
# ---------------------------------------------------------------------------

def push_whatsapp(report_text, rising, emerging, fading):
    """截取报告精华推送到 WhatsApp — 内容优先于数字。"""
    date_str = datetime.now().strftime('%Y-%m-%d')

    parts = [f"📊 AI 周趋势报告 {date_str}\n"]

    # 上升趋势：显示关键词 + 具体数据
    if rising:
        parts.append("📈 上升趋势:")
        for w, tc, lc, r in rising[:5]:
            parts.append(f"  • {w}: {lc}→{tc}次 (+{r:.0%})")

    # 新出现热词
    if emerging:
        parts.append("\n🆕 新出现热词:")
        for w, c in emerging[:5]:
            parts.append(f"  • {w} ({c}次)")

    # 消退话题
    if fading:
        parts.append(f"\n📉 消退话题:")
        for w, c in fading[:3]:
            parts.append(f"  • {w} (上周{c}次)")

    # 截取 LLM 分析的实质内容（跳过标题行和空行）
    lines = report_text.split("\n")
    llm_start = None
    for i, line in enumerate(lines):
        if "LLM 趋势分析" in line:
            llm_start = i + 1
            break
    if llm_start:
        llm_lines = []
        for line in lines[llm_start:llm_start + 25]:
            stripped = line.strip()
            if stripped and not stripped.startswith("（LLM"):
                llm_lines.append(stripped)
        if llm_lines:
            parts.append("\n🤖 分析:")
            parts.append("\n".join(llm_lines[:15]))

    # 回测结果
    for line in lines:
        if "命中率" in line:
            parts.append(f"\n{line.strip()}")
            break

    parts.append("\n💡 回复任何话题可深入讨论")

    msg = "\n".join(parts)
    msg = msg[:1500]

    try:
        os.system(
            f'openclaw message send --target "{PHONE}" '
            f'--message "{msg}" --json >/dev/null 2>&1'
        )
        log("趋势报告已推送 WhatsApp")
    except Exception as e:
        log(f"WhatsApp 推送失败: {e}")


# ---------------------------------------------------------------------------
# 预测回测：上周预测 vs 本周实际
# ---------------------------------------------------------------------------

def backtest(report_dir, this_kw, emerging):
    """从上周报告中提取预测关键词，与本周实际数据对比。"""
    # 找到最近的历史报告（不含今天）
    today_str = datetime.now().strftime("%Y%m%d")
    reports = sorted(glob.glob(os.path.join(report_dir, "trend_*.md")), reverse=True)

    prev_report = None
    for r in reports:
        rdate = os.path.basename(r).replace("trend_", "").replace(".md", "")
        if rdate < today_str:
            prev_report = r
            break

    if not prev_report:
        return None

    try:
        with open(prev_report) as f:
            prev_content = f.read()
    except OSError:
        return None

    prev_date = os.path.basename(prev_report).replace("trend_", "").replace(".md", "")

    # 提取上周的"新出现热词"和"上升趋势"
    prev_rising = set()
    prev_emerging = set()
    section = None
    for line in prev_content.split("\n"):
        if "上升趋势" in line:
            section = "rising"
        elif "新出现热词" in line:
            section = "emerging"
        elif "消退话题" in line or "高频词" in line or "LLM" in line:
            section = None
        elif section == "rising" and "|" in line and "关键词" not in line and "---" not in line:
            parts = line.split("|")
            if len(parts) >= 2:
                word = parts[1].strip()
                if word:
                    prev_rising.add(word)
        elif section == "emerging" and line.strip().startswith("- **"):
            word = line.split("**")[1] if "**" in line else ""
            if word:
                prev_emerging.add(word)

    if not prev_rising and not prev_emerging:
        return None

    # 本周实际数据
    this_dict = dict(this_kw)
    this_emerging_set = {w for w, _ in emerging}

    # 对比
    results = {
        "prev_date": prev_date,
        "continued_rising": [],    # 上周上升，本周仍在 top 100
        "faded_rising": [],        # 上周上升，本周消失
        "confirmed_emerging": [],  # 上周新出现，本周仍活跃
        "flash_emerging": [],      # 上周新出现，本周已消退
    }

    for w in prev_rising:
        if w in this_dict:
            results["continued_rising"].append((w, this_dict[w]))
        else:
            results["faded_rising"].append(w)

    for w in prev_emerging:
        if w in this_dict:
            results["confirmed_emerging"].append((w, this_dict[w]))
        else:
            results["flash_emerging"].append(w)

    total_predictions = len(prev_rising) + len(prev_emerging)
    hits = len(results["continued_rising"]) + len(results["confirmed_emerging"])
    accuracy = hits / total_predictions * 100 if total_predictions > 0 else 0
    results["accuracy"] = round(accuracy, 1)
    results["total"] = total_predictions
    results["hits"] = hits

    return results


def format_backtest(bt):
    """格式化回测结果为 Markdown。"""
    lines = [
        f"## 🔄 预测回测（上期 {bt['prev_date']} → 本期）",
        f"> 命中率：**{bt['accuracy']}%**（{bt['hits']}/{bt['total']}）",
    ]
    if bt["continued_rising"]:
        items = ", ".join(f"{w}({c}次)" for w, c in bt["continued_rising"][:8])
        lines.append(f"\n**持续上升** ✅：{items}")
    if bt["confirmed_emerging"]:
        items = ", ".join(f"{w}({c}次)" for w, c in bt["confirmed_emerging"][:8])
        lines.append(f"**确认热词** ✅：{items}")
    if bt["faded_rising"]:
        items = ", ".join(bt["faded_rising"][:8])
        lines.append(f"**昙花一现** ❌：{items}")
    if bt["flash_emerging"]:
        items = ", ".join(bt["flash_emerging"][:8])
        lines.append(f"**一周即退** ❌：{items}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 用户反馈集成
# ---------------------------------------------------------------------------

def load_feedback(kb_dir, days=14):
    """从 KB 中读取用户反馈，提取对趋势报告的优化建议。"""
    notes_dir = os.path.join(kb_dir, "notes")
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    feedback = []
    for f in sorted(glob.glob(os.path.join(notes_dir, "*.md")), reverse=True):
        basename = os.path.basename(f)
        file_date = basename[:8]
        if file_date < cutoff:
            break
        try:
            with open(f) as fh:
                content = fh.read()
            if "type: feedback" in content[:200] or "tags: [feedback]" in content[:200]:
                body = content
                if body.startswith("---"):
                    parts = body.split("---", 2)
                    if len(parts) >= 3:
                        body = parts[2].strip()
                lines = [l.strip() for l in body.split("\n")
                         if l.strip() and not l.startswith("#") and not l.startswith("20")]
                if lines:
                    feedback.append(lines[0][:200])
        except OSError:
            continue
    return feedback


def apply_feedback_stopwords(feedback_items):
    """从反馈中提取应该加入停用词的词。返回额外停用词集合。"""
    extra_stops = set()
    # 匹配"噪音词：X, Y, Z"格式，捕获词列表部分
    noise_patterns = re.compile(
        r"噪音[词]?[：:]\s*(.+)|noise\s*(?:word)?s?[：:]\s*(.+)|停用词[：:]\s*(.+)",
        re.I
    )
    for fb in feedback_items:
        m = noise_patterns.search(fb)
        if m:
            words_str = m.group(1) or m.group(2) or m.group(3)
            # 分割：支持中英文逗号、顿号、空格；剥离中文标点和描述性后缀
            for w in re.split(r"[,，、；;\s]+", words_str):
                # 去掉前后标点和中文字符（只保留英文词）
                w = re.sub(r"[^\w-]", "", w).strip().lower()
                # 只保留英文词（2字符以上）或中文词（2字符以上）
                if not w or len(w) <= 1:
                    continue
                # 过滤掉明显是描述语而非关键词的部分
                if re.search(r"[\u4e00-\u9fff]{3,}", w):
                    continue  # 3个以上中文字 = 描述句，不是关键词
                extra_stops.add(w)
    return extra_stops


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="KB 周趋势报告")
    parser.add_argument("--weeks", type=int, default=2,
                        help="对比周数（默认2，即本周 vs 上周）")
    parser.add_argument("--no-llm", action="store_true",
                        help="跳过 LLM 分析（仅统计）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 格式（供脚本调用）")
    parser.add_argument("--no-push", action="store_true",
                        help="不推送 WhatsApp")
    args = parser.parse_args()

    now = datetime.now()

    # 本周：最近7天
    this_end = now
    this_start = now - timedelta(days=7)

    # 上周：7-14天前
    last_end = this_start
    last_start = this_start - timedelta(days=7)

    log(f"本周: {this_start.strftime('%m/%d')}-{this_end.strftime('%m/%d')} | "
        f"上周: {last_start.strftime('%m/%d')}-{last_end.strftime('%m/%d')}")

    # 读取用户反馈，动态扩充停用词
    feedback = load_feedback(KB_DIR)
    if feedback:
        extra_stops = apply_feedback_stopwords(feedback)
        if extra_stops:
            STOP_WORDS.update(extra_stops)
            log(f"从反馈中加载 {len(extra_stops)} 个额外停用词: {extra_stops}")
        log(f"读取到 {len(feedback)} 条用户反馈")

    # 提取文本
    this_text = extract_period_text(KB_DIR, this_start, this_end)
    last_text = extract_period_text(KB_DIR, last_start, last_end)

    if not this_text and not last_text:
        log("两周均无 KB 内容，跳过")
        return

    log(f"本周文本: {len(this_text):,} 字 | 上周文本: {len(last_text):,} 字")

    # 提取关键词
    this_kw = extract_keywords(this_text)
    last_kw = extract_keywords(last_text)

    log(f"本周关键词: {len(this_kw)} | 上周关键词: {len(last_kw)}")

    # 计算趋势
    rising, emerging, fading = compute_trends(this_kw, last_kw)

    log(f"上升: {len(rising)} | 新出现: {len(emerging)} | 消退: {len(fading)}")

    # 预测回测（所有模式共用）
    bt = backtest(REPORT_DIR, this_kw, emerging)
    if bt:
        log(f"预测回测: 命中率 {bt['accuracy']}% ({bt['hits']}/{bt['total']})")

    # JSON 输出模式
    if args.json:
        result = {
            "date": now.strftime("%Y-%m-%d"),
            "this_week_chars": len(this_text),
            "last_week_chars": len(last_text),
            "rising": [{"word": w, "this": tc, "last": lc, "ratio": round(r, 2)}
                       for w, tc, lc, r in rising],
            "emerging": [{"word": w, "count": c} for w, c in emerging],
            "fading": [{"word": w, "last_count": c} for w, c in fading],
            "top_keywords": [{"word": w, "count": c} for w, c in this_kw[:30]],
        }
        if bt:
            result["backtest"] = bt
        if feedback:
            result["feedback_count"] = len(feedback)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # LLM 分析
    llm_result = ""
    if not args.no_llm:
        log("调用 LLM 进行趋势分析...")
        llm_result = llm_analyze(rising, emerging, fading, this_text)
        if llm_result:
            log(f"LLM 分析完成 ({len(llm_result)} 字)")

    # 生成报告
    report = generate_report(
        rising, emerging, fading, llm_result,
        this_kw, last_kw, len(this_text), len(last_text), args.weeks
    )
    # 插入回测结果（在 LLM 分析之前）
    if bt:
        bt_section = format_backtest(bt)
        report = report.replace("## 🤖 LLM 趋势分析", f"{bt_section}\n\n## 🤖 LLM 趋势分析")

    # 写入文件
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f"trend_{now.strftime('%Y%m%d')}.md")
    with open(report_file, "w") as f:
        f.write(report)
    log(f"报告已写入: {report_file}")

    # 更新三方共享状态
    try:
        import subprocess
        subprocess.run([
            sys.executable, os.path.expanduser("~/status_update.py"),
            "--set", "health.last_trend_report", now.strftime("%Y-%m-%d"),
            "--by", "cron"
        ], capture_output=True, timeout=5)
    except Exception:
        pass

    # 推送 WhatsApp
    if not args.no_push:
        push_whatsapp(report, rising, emerging, fading)

    # 打印摘要
    if not args.json:
        print(f"\n{'='*60}")
        print(f"📊 AI 周趋势报告 {now.strftime('%Y-%m-%d')}")
        print(f"{'='*60}")
        if rising:
            print(f"\n📈 上升趋势 Top 5:")
            for w, tc, lc, r in rising[:5]:
                print(f"   {w}: {lc}→{tc} (+{r:.0%})")
        if emerging:
            print(f"\n🆕 新出现:")
            for w, c in emerging[:5]:
                print(f"   {w} ({c}次)")
        if fading:
            print(f"\n📉 消退:")
            for w, c in fading[:5]:
                print(f"   {w} (上周{c}次)")
        if bt:
            print(f"\n🔄 预测回测 (上期 {bt['prev_date']}):")
            print(f"   命中率: {bt['accuracy']}% ({bt['hits']}/{bt['total']})")
        if feedback:
            print(f"\n💬 用户反馈: {len(feedback)} 条已应用")
        print(f"\n📄 完整报告: {report_file}")


if __name__ == "__main__":
    main()

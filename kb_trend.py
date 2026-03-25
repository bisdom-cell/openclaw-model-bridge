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
    # 中文
    "的", "了", "在", "是", "和", "与", "对", "为", "从", "到",
    "可以", "通过", "进行", "使用", "一个", "我们", "提出", "方法",
    "基于", "实现", "研究", "问题", "系统", "模型", "数据",
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
    # 提取英文词（2字符以上）
    en_words = re.findall(r"[a-zA-Z][a-zA-Z0-9._-]{1,30}", text.lower())
    # 提取中文词组（2-4字）
    zh_words = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
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

def push_whatsapp(report_text, rising, emerging):
    """截取报告精华推送到 WhatsApp。"""
    rising_short = ", ".join(w for w, *_ in rising[:5]) or "无"
    emerging_short = ", ".join(w for w, _ in emerging[:5]) or "无"

    msg = f"""📊 AI 周趋势报告 {datetime.now().strftime('%Y-%m-%d')}

📈 上升: {rising_short}
🆕 新词: {emerging_short}
📉 消退: {len([1 for _ in []])}个话题"""

    # 截取 LLM 分析的前几行
    lines = report_text.split("\n")
    llm_start = None
    for i, line in enumerate(lines):
        if "LLM 趋势分析" in line:
            llm_start = i + 1
            break
    if llm_start:
        llm_snippet = "\n".join(lines[llm_start:llm_start + 15])[:400]
        msg += f"\n\n{llm_snippet}"

    try:
        os.system(
            f'openclaw message send --target "{PHONE}" '
            f'--message "{msg}" --json >/dev/null 2>&1'
        )
        log("趋势报告已推送 WhatsApp")
    except Exception as e:
        log(f"WhatsApp 推送失败: {e}")


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

    # 写入文件
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f"trend_{now.strftime('%Y%m%d')}.md")
    with open(report_file, "w") as f:
        f.write(report)
    log(f"报告已写入: {report_file}")

    # 推送 WhatsApp
    if not args.no_push:
        push_whatsapp(report, rising, emerging)

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
        print(f"\n📄 完整报告: {report_file}")


if __name__ == "__main__":
    main()

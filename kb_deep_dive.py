#!/usr/bin/env python3
"""kb_deep_dive.py — V37.9.16 每日深度分析

每日 22:30 HKT 从今日 sources 中挑选 1 篇最有价值的论文/文章，
抓取原文（一档 PDF / 二档 HTML / 三档摘要降级），调 LLM 做深度分析
（论证链 + 实验对比 + 局限性 / 摘要级 grounding 约束），
推送 WhatsApp + Discord #daily + 归档到 ~/.kb/deep_dives/。

设计契约（见 CLAUDE.md V37.9.16 方案 12 项决策）：
  - 复用 kb_review_collect: load_sources_from_registry / extract_recent_sections / call_llm
  - picker: 星级优先 (⭐≥4) + 主题加权 (ontology/agent runtime/LLM infra) + 摘要长度 tie-breaker
  - tier1 PDF (arxiv/hf/acl/pwc) → tier2 HTML (rss_blogs/ontology_sources/github_trending)
    → tier3 摘要降级 (HN/X/finance/freight 等)
  - fetch 失败 → degrade 到摘要 + 标注"摘要级分析"，不 block
  - pdfplumber + bs4 lazy import (dev 环境 degrade 不依赖)
  - prompt 分支：full_text 模式 vs abstract-only 模式，都强 grounding
  - 无 ⭐≥4 候选 → status=no_candidates 推告警
  - LLM 失败 → status=llm_failed fail-fast（镜像 kb_review V37.5 合约）

CLI 用法：
  KB_DIR=~/.kb REGISTRY=jobs_registry.yaml python3 kb_deep_dive.py
  输出：JSON 到 stdout

Exit codes:
  0 — JSON 产出（status 字段指明 ok / no_candidates / llm_failed / collector_failed）
  1 — 致命错误（参数缺失/注册表不可读），stderr 有原因
"""
import glob
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

# Reuse kb_review_collect 采集 + LLM 原语（MR-8 兑现：不 copy-paste）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_review_collect as rc


# ══════════════════════════════════════════════════════════════════════
# 1. 源分层（决定抓取策略）
# ══════════════════════════════════════════════════════════════════════

# 一档：PDF 可抓（论文原文）
TIER_1_SOURCES = {
    "arxiv_monitor",
    "hf_papers",
    "acl_anthology",
    "pwc",
    "semantic_scholar",   # S2 常链 arxiv，走 PDF 路径
    "dblp",               # DBLP 条目多指向 arxiv/acl
}

# 二档：HTML 可抓（博客/技术文）
TIER_2_SOURCES = {
    "rss_blogs",
    "ontology_sources",
    "github_trending",
}

# 三档：降级到摘要（HN/X posts/finance/freight 等）
# 不在 TIER_1/2 的都走三档


# ══════════════════════════════════════════════════════════════════════
# 2. Picker — 从今日 H2 section 抽出 entries + 排序 + 选 top 1
# ══════════════════════════════════════════════════════════════════════

# Entry block: 以 `*title*` 开头，包含 链接/贡献/价值/要点 等行，
# 以下一个 `*...*` 或 section 结束分隔
_ENTRY_OPEN_RE = re.compile(r"^\*([^*\n]+)\*\s*$")
_STARS_RE = re.compile(r"(⭐+)")
_LINK_RE = re.compile(r"链接[：:]\s*(\S+)")
_URL_RE = re.compile(r"https?://\S+")

# 主题加权关键字（命中加分，case-insensitive 子串匹配）
TOPIC_WEIGHTS = {
    # ontology / knowledge graph 领域
    "ontology": 10, "本体": 10, "knowledge graph": 10, "知识图谱": 10,
    # agent runtime / infra
    "agent runtime": 10, "agent system": 8, "agent framework": 8,
    "multi-agent": 6, "tool use": 8, "tool calling": 8, "tool learning": 6,
    # LLM infra / serving
    "llm infra": 10, "llm serving": 10, "inference": 5, "kv cache": 6,
    "mixture of experts": 5, "moe": 4, "speculative decoding": 6,
    # RAG / memory
    "rag": 5, "retrieval augmented": 6, "long context": 5, "memory": 4,
    # governance / eval / reliability
    "governance": 8, "evaluation": 5, "benchmark": 4, "reliability": 6,
    "safety": 4, "alignment": 4,
    # control plane / orchestration（本项目核心叙事）
    "control plane": 10, "orchestration": 5,
}

MIN_STARS = 4     # 门槛：⭐≥4 才算合格候选
MAX_ABSTRACT_BONUS = 10


def parse_entries_from_section(section_text, source_id, source_label):
    """从某 source 的今日 H2 section body 解析出 entry list。

    每个 entry = 一个 `*title*` 开头的连续块，直到下一个 `*...*` 或空双换行。
    Returns:
      list of dict [{title, link, stars, abstract, source_id, source_label}, ...]
    """
    entries = []
    if not section_text:
        return entries

    lines = section_text.split("\n")
    current = None
    for raw in lines:
        line = raw.rstrip()
        m = _ENTRY_OPEN_RE.match(line)
        if m:
            # flush 前一个
            if current is not None and current.get("title"):
                entries.append(current)
            current = {
                "title": m.group(1).strip(),
                "link": "",
                "stars": 0,
                "abstract": "",
                "source_id": source_id,
                "source_label": source_label,
            }
            continue
        if current is None:
            continue
        # 提取链接
        link_m = _LINK_RE.search(line)
        if link_m and not current["link"]:
            current["link"] = link_m.group(1).rstrip("/")
        else:
            # fallback 扫一般 URL
            url_m = _URL_RE.search(line)
            if url_m and not current["link"]:
                current["link"] = url_m.group(0).rstrip(".,;)")
        # 提取星级（取最长连续 ⭐）
        star_m = _STARS_RE.search(line)
        if star_m:
            stars_count = len(star_m.group(1))
            if stars_count > current["stars"]:
                current["stars"] = stars_count
        # 累积摘要（非链接/星级行）
        if line.strip() and not link_m and not star_m:
            if current["abstract"]:
                current["abstract"] += " "
            current["abstract"] += line.strip()[:200]
    if current is not None and current.get("title"):
        entries.append(current)
    return entries


def score_entry(entry):
    """候选评分：星级×10 + 主题加权 + 摘要长度 tie-breaker。

    不合格（⭐<MIN_STARS）返回 -1 使其被排除（保留可见以便 top-N 时识别）。
    """
    stars = entry.get("stars", 0) or 0
    if stars < MIN_STARS:
        return -1
    score = stars * 10
    text = (entry.get("title", "") + " " + entry.get("abstract", "")).lower()
    for kw, boost in TOPIC_WEIGHTS.items():
        if kw in text:
            score += boost
    # tie-breaker: 摘要长度（封顶 MAX_ABSTRACT_BONUS）
    score += min(len(entry.get("abstract", "")) // 100, MAX_ABSTRACT_BONUS)
    return score


def collect_today_candidates(kb_dir, registry_path, today=None):
    """从所有 registry 声明的 source 中收集今日 entries。

    Returns:
      list of dict (ranked by score desc; only stars>=MIN_STARS kept)
    """
    if today is None:
        today = datetime.now()
    sources = rc.load_sources_from_registry(registry_path)
    sources_dir = os.path.join(kb_dir, "sources")

    candidates = []
    for job in sources:
        filename = job["kb_source_file"]
        label = job.get("kb_source_label") or job["id"]
        path = os.path.join(sources_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue

        # 只看今日 section（days=1，只今日 H2）
        section_text = rc.extract_recent_sections(
            content, days=1, max_chars=30000, today=today
        )
        if not section_text.strip():
            continue
        entries = parse_entries_from_section(section_text, job["id"], label)
        candidates.extend(entries)

    # 过滤 + 打分 + 排序
    scored = []
    for e in candidates:
        s = score_entry(e)
        if s >= 0:
            scored.append((s, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored]


def pick_top(candidates):
    """返回 top 1 候选，或 None（当 candidates 为空）。

    V37.9.17 方案 C — tier-aware fallback：
      优先选 TIER 1/2 (PDF/HTML 可抓 → full_text 模式)；
      只有当 TIER 1+2 全部为空才回退 TIER 3 (degrade abstract_only)。

    背景：V37.9.16 首跑 tier-blind picker 选中 X tweet ⭐5 走 abstract_only，
    用户感知质量低于预期 (摘要分析 vs 期望论证链拆解)。方案 C 保证：
      - 当今日有 ⭐≥4 论文/博客时，绝不被 X tweet/HN 帖子挤掉
      - 当今日纯无论文 (周末等) 才回退 X tweet 的摘要级分析
    每个桶内仍按 score 排序 (V37.9.16 公式不变)。
    """
    if not candidates:
        return None
    tier_12 = [c for c in candidates if classify_tier(c["source_id"]) in (1, 2)]
    if tier_12:
        return tier_12[0]
    return candidates[0]


# ══════════════════════════════════════════════════════════════════════
# 3. Fetcher — tier1 PDF / tier2 HTML / tier3 degrade
# ══════════════════════════════════════════════════════════════════════

FETCH_TIMEOUT = 60
MAX_FULL_TEXT_CHARS = 30000
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 kb_deep_dive/1.0"


def classify_tier(source_id):
    """根据 source_id 分档。"""
    if source_id in TIER_1_SOURCES:
        return 1
    if source_id in TIER_2_SOURCES:
        return 2
    return 3


def arxiv_url_to_pdf(url):
    """arxiv abs URL → pdf URL。支持常见变体。"""
    if "arxiv.org/abs/" in url:
        return url.replace("/abs/", "/pdf/").rstrip("/") + ".pdf"
    if "arxiv.org/pdf/" in url:
        return url if url.endswith(".pdf") else url + ".pdf"
    return None


def acl_url_to_pdf(url):
    """aclanthology URL → pdf URL。"""
    # https://aclanthology.org/2024.acl-long.123/ → 加 .pdf
    if "aclanthology.org" in url:
        base = url.rstrip("/")
        if base.endswith(".pdf"):
            return base
        return base + ".pdf"
    return None


def _urlopen(url, timeout=FETCH_TIMEOUT):
    """Wrapped urlopen with UA header（学术站点拒默认 Python UA）。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_pdf_text(url, max_chars=MAX_FULL_TEXT_CHARS):
    """抓 arxiv/acl PDF → pdfplumber 提取文本。

    Returns:
      (ok, text, reason)
      成功：(True, text, "")
      失败：(False, "", reason)

    lazy import pdfplumber — dev 环境无此库时 degrade 到摘要。
    """
    # 定位真实 PDF URL
    pdf_url = None
    if "arxiv.org" in url:
        pdf_url = arxiv_url_to_pdf(url)
    elif "aclanthology.org" in url:
        pdf_url = acl_url_to_pdf(url)
    elif url.lower().endswith(".pdf"):
        pdf_url = url
    if not pdf_url:
        return False, "", f"no PDF URL derivable from {url}"

    try:
        import pdfplumber  # lazy — dev 无此库走 degrade
    except ImportError:
        return False, "", "pdfplumber not installed (dev env?)"

    try:
        with _urlopen(pdf_url) as resp:
            pdf_bytes = resp.read()
    except urllib.error.HTTPError as e:
        return False, "", f"HTTP {e.code} fetching {pdf_url}"
    except urllib.error.URLError as e:
        return False, "", f"URLError: {e.reason} for {pdf_url}"
    except (TimeoutError, OSError) as e:
        return False, "", f"{type(e).__name__}: {e}"

    if not pdf_bytes:
        return False, "", "empty PDF body"

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                try:
                    t = page.extract_text()
                except Exception:
                    t = ""
                if t:
                    pages_text.append(t)
            raw = "\n".join(pages_text)
    except Exception as e:
        return False, "", f"pdfplumber parse error: {type(e).__name__}: {e}"

    if not raw.strip():
        return False, "", "PDF has no extractable text (scanned?)"

    cleaned = preprocess_pdf_text(raw, max_chars=max_chars)
    return True, cleaned, ""


def preprocess_pdf_text(raw, max_chars=MAX_FULL_TEXT_CHARS):
    """切 References/Acknowledgments 之后 + 去图表噪声 + 截断 max_chars。"""
    text = raw

    # 切 References/Acknowledgments 之后（大小写不敏感，行首匹配）
    CUT_HEADS = [
        r"\n\s*References\s*\n",
        r"\n\s*REFERENCES\s*\n",
        r"\n\s*Acknowledgments?\s*\n",
        r"\n\s*ACKNOWLEDGMENTS?\s*\n",
        r"\n\s*参考文献\s*\n",
        r"\n\s*致谢\s*\n",
    ]
    for pat in CUT_HEADS:
        m = re.search(pat, text)
        if m:
            text = text[: m.start()]
            break

    # 去图片/表格 caption 噪声（行首 Figure X: / Table X: 后续到空行）
    text = re.sub(
        r"\n\s*(Figure|Fig\.|Table|图|表)\s*\d+[:\.\s][^\n]{0,300}\n",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    # 合并连续空白行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text


def fetch_html_text(url, max_chars=MAX_FULL_TEXT_CHARS):
    """抓博客/新闻 HTML → BeautifulSoup 提取正文。

    lazy import bs4 — dev 环境无此库时 degrade 到摘要。
    """
    try:
        from bs4 import BeautifulSoup  # lazy
    except ImportError:
        return False, "", "beautifulsoup4 not installed (dev env?)"

    try:
        with _urlopen(url) as resp:
            html_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return False, "", f"HTTP {e.code} fetching {url}"
    except urllib.error.URLError as e:
        return False, "", f"URLError: {e.reason} for {url}"
    except (TimeoutError, OSError) as e:
        return False, "", f"{type(e).__name__}: {e}"

    if not html_bytes:
        return False, "", "empty HTML body"

    # charset 探测
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if m:
        charset = m.group(1)
    try:
        html_text = html_bytes.decode(charset, errors="replace")
    except LookupError:
        html_text = html_bytes.decode("utf-8", errors="replace")

    try:
        soup = BeautifulSoup(html_text, "html.parser")
    except Exception as e:
        return False, "", f"bs4 parse error: {type(e).__name__}: {e}"

    # 去 script/style/nav/footer/header/aside
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    # 优先 article / main
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text(separator="\n", strip=True)

    # 压缩空白
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if not text:
        return False, "", "HTML has no extractable text"

    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return True, text, ""


def fetch_full_text(entry):
    """根据 entry.source_id 分档选抓取策略。

    Returns:
      (mode, text, reason)
        mode: "full_text" | "abstract_only"
        text: 真正用于 LLM 的文本（full PDF/HTML / 摘要降级）
        reason: degrade 原因（如 "PDF fetch failed: HTTP 404" / "tier3 source"）
    """
    tier = classify_tier(entry["source_id"])
    url = entry.get("link", "")

    if tier == 3 or not url:
        return "abstract_only", entry.get("abstract", ""), f"tier{tier} source (no fetch attempted)"

    if tier == 1:
        ok, text, reason = fetch_pdf_text(url)
        if ok and text.strip():
            return "full_text", text, ""
        # degrade 到摘要
        return "abstract_only", entry.get("abstract", ""), f"PDF fetch failed: {reason}"

    # tier 2 HTML
    ok, text, reason = fetch_html_text(url)
    if ok and text.strip():
        return "full_text", text, ""
    return "abstract_only", entry.get("abstract", ""), f"HTML fetch failed: {reason}"


# ══════════════════════════════════════════════════════════════════════
# 4. Prompt 构造（两分支：full_text / abstract_only）
# ══════════════════════════════════════════════════════════════════════

GROUNDING_CONSTRAINT = """⚠️ 严格约束（违反则整份输出作废）：
- 只使用下方原文明确出现的信息，禁止添加未出现的内容
- 每个判断/论点必须能在原文中找到对应段落，可以引用原文短句
- 严禁虚构作者言论、未出现的实验数据、未出现的结论
- 如果某个维度在原文中没有充分信息，直接跳过，不要编造"""


def build_full_text_prompt(entry, full_text):
    """full_text 模式：要求论证链+实验对比+局限性分析。"""
    return f"""你是一位深度技术阅读专家。以下是一篇重要论文/文章的完整原文，
请完成结构化深度分析（用中文回答，总字数控制在 800-1200 字）：

## 1. 核心论点（3 句内）
论文/文章最核心的 claim 是什么？作者试图证明什么？

## 2. 论证链（3-5 条）
作者如何一步步论证核心论点？关键的推理跳跃在哪里？

## 3. 实验/证据（如有）
关键实验设计 + 数据对比。对比的 baseline 是什么？结果有多显著？

## 4. 局限性与开放问题（2-3 条）
作者明确承认的局限 + 你读出的潜在问题（如假设不成立/泛化性/成本）

## 5. 对本项目（Agent Runtime Control Plane）的启发（1-2 条）
如果要在 openclaw-model-bridge 这类 agent runtime 系统中应用，能借鉴什么？

{GROUNDING_CONSTRAINT}

═══ 基本信息 ═══
标题: {entry["title"]}
来源: {entry["source_label"]}
链接: {entry.get("link", "(无)")}
星级: {"⭐" * entry["stars"]} ({entry["stars"]}/5)

═══ 原文（完整） ═══
{full_text}"""


def build_abstract_only_prompt(entry, abstract):
    """abstract-only 模式：禁止推测方法细节 + 明确标注"摘要级"。"""
    return f"""你是一位技术分析师。以下仅有一篇论文/文章的**摘要**（抓取原文失败或不可用），
请基于有限信息做**摘要级分析**（用中文回答，总字数控制在 400-600 字）：

## 1. 核心议题（1-2 句）
这篇内容在谈什么问题？

## 2. 可识别的 claim（摘要中明确提到的）
作者声称了什么？（只列摘要中有的）

## 3. 值得进一步阅读的理由（1-2 条）
为什么值得花时间去读完整版？（基于摘要线索）

## 4. 分析局限性声明
⚠️ 明确告诉读者：这是基于摘要的分析，方法细节/实验/局限性无法从摘要中推出。

⚠️ 严格约束（违反则整份输出作废）：
- **严禁推测方法细节、实验设置、未在摘要中明确出现的数字**
- 每条判断必须来自摘要原文
- 每篇分析都必须在开头或结尾明确标注"基于摘要的分析"
- 不要假设你知道论文全文

═══ 基本信息 ═══
标题: {entry["title"]}
来源: {entry["source_label"]}
链接: {entry.get("link", "(无)")}
星级: {"⭐" * entry["stars"]} ({entry["stars"]}/5)

═══ 摘要 ═══
{abstract or "(摘要为空)"}"""


def build_prompt_for_entry(entry, mode, text):
    """按 mode 分派 prompt builder。"""
    if mode == "full_text":
        return build_full_text_prompt(entry, text)
    return build_abstract_only_prompt(entry, text)


# ══════════════════════════════════════════════════════════════════════
# 5. 输出构造（markdown / WA / Discord）
# ══════════════════════════════════════════════════════════════════════

def build_deep_dive_markdown(entry, mode, llm_content, degrade_reason, date_str):
    """生成 ~/.kb/deep_dives/YYYY-MM-DD.md 内容。"""
    mode_label = "完整原文" if mode == "full_text" else "摘要级"
    mode_note = ""
    if mode == "abstract_only" and degrade_reason:
        mode_note = f"\n\n> ⚠️ 抓取降级原因：{degrade_reason}"
    return f"""---
date: {date_str}
type: deep_dive
mode: {mode}
source_id: {entry["source_id"]}
source_label: {entry["source_label"]}
stars: {entry["stars"]}
link: {entry.get("link", "")}
---

# 🔬 每日深度分析 {date_str}

## {entry["title"]}

**来源**: {entry["source_label"]} | **星级**: {"⭐" * entry["stars"]} | **模式**: {mode_label}
**链接**: {entry.get("link", "(无)")}{mode_note}

---

{llm_content}
"""


def build_deep_dive_wa(entry, mode, llm_content, date_str):
    """WhatsApp 简版推送（<=1400 字，保留完整 LLM 内容）。"""
    mode_tag = "" if mode == "full_text" else "（摘要级）"
    header = (
        f"🔬 每日深度 {date_str}{mode_tag}\n"
        f"{entry['title']}\n"
        f"⭐{entry['stars']} | {entry['source_label']}\n"
    )
    if entry.get("link"):
        header += f"{entry['link']}\n"
    header += "\n"
    budget = 1400 - len(header) - 10
    body = llm_content if len(llm_content) <= budget else llm_content[:budget] + "..."
    return header + body


def build_deep_dive_discord(entry, mode, llm_content, date_str):
    """Discord #daily 完整版（Discord 单消息 2000 字，留 buffer）。"""
    mode_tag = "" if mode == "full_text" else "（摘要级分析）"
    header = (
        f"🔬 **每日深度分析 {date_str}**{mode_tag}\n"
        f"**{entry['title']}**\n"
        f"{'⭐' * entry['stars']} | {entry['source_label']}\n"
    )
    if entry.get("link"):
        header += f"🔗 {entry.get('link')}\n"
    header += "\n---\n\n"
    budget = 1900 - len(header)
    body = llm_content if len(llm_content) <= budget else llm_content[:budget] + "\n...[完整版见 KB 归档]"
    return header + body


# ══════════════════════════════════════════════════════════════════════
# 6. Run 主控流程 — orchestrate pick → fetch → LLM → build
# ══════════════════════════════════════════════════════════════════════

def run(kb_dir, registry_path, today=None, llm_caller=None, fetcher=None):
    """Orchestrate full pipeline.

    Args:
      llm_caller: optional callable(prompt) -> (ok, content, reason) for tests.
      fetcher:    optional callable(entry) -> (mode, text, reason) for tests.

    Returns:
      dict (JSON-serializable) with status + artifacts.
    """
    if today is None:
        today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")

    # 1. 收集候选
    try:
        candidates = collect_today_candidates(kb_dir, registry_path, today=today)
    except FileNotFoundError as e:
        return {
            "status": "collector_failed",
            "reason": str(e),
            "date": date_str,
        }
    except Exception as e:
        return {
            "status": "collector_failed",
            "reason": f"{type(e).__name__}: {e}",
            "date": date_str,
        }

    pick = pick_top(candidates)
    if pick is None:
        return {
            "status": "no_candidates",
            "reason": f"no ⭐≥{MIN_STARS} candidates in today's sources",
            "date": date_str,
            "candidates_count": 0,
        }

    # 2. 抓取原文
    fetch_fn = fetcher if fetcher is not None else fetch_full_text
    mode, text, degrade_reason = fetch_fn(pick)

    # 3. 构造 prompt + 调 LLM
    prompt = build_prompt_for_entry(pick, mode, text)
    caller = llm_caller if llm_caller is not None else rc.call_llm
    ok, llm_content, reason = caller(prompt)
    if not ok:
        return {
            "status": "llm_failed",
            "reason": reason,
            "date": date_str,
            "pick_title": pick["title"],
            "pick_stars": pick["stars"],
            "pick_source": pick["source_label"],
            "mode": mode,
            "degrade_reason": degrade_reason,
        }

    # 4. 构建输出
    md = build_deep_dive_markdown(pick, mode, llm_content, degrade_reason, date_str)
    wa = build_deep_dive_wa(pick, mode, llm_content, date_str)
    discord_msg = build_deep_dive_discord(pick, mode, llm_content, date_str)

    return {
        "status": "ok",
        "date": date_str,
        "mode": mode,
        "degrade_reason": degrade_reason,
        "pick": {
            "title": pick["title"],
            "link": pick.get("link", ""),
            "stars": pick["stars"],
            "source_id": pick["source_id"],
            "source_label": pick["source_label"],
        },
        "candidates_count": len(candidates),
        "llm_content": llm_content,
        "markdown": md,
        "wa_message": wa,
        "discord_message": discord_msg,
    }


def main():
    kb_dir = os.environ.get("KB_DIR") or os.path.expanduser("~/.kb")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    registry = os.environ.get("REGISTRY") or os.path.join(
        script_dir, "jobs_registry.yaml"
    )

    try:
        result = run(kb_dir, registry)
    except Exception as e:
        err = {
            "status": "collector_failed",
            "reason": f"{type(e).__name__}: {e}",
        }
        print(json.dumps(err, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()

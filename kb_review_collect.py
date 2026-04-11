#!/usr/bin/env python3
"""
kb_review_collect.py — V37.5 KB 周度回顾数据采集器 + LLM 调用

从 jobs_registry.yaml 动态发现源文件（消除硬编码漂移），按 H2 章节精确提取
最近 N 天的内容（替代行级日期匹配，避免把 digest 容器标题当论文亮点），
调用 LLM 做深度分析。纯 Python 模块，无 shell scope bug，可单测。

设计契约：
  - 失败必须可见（fail-fast）：LLM 失败 → status="llm_failed" + 具体原因
  - 状态诚实：never claim llm_status="ok" when LLM actually failed
  - 数据源单一真理：sources 从 registry 读取，不硬编码
  - 结构化解析：按 `## YYYY-MM-DD` H2 章节 drill-down 到具体内容

CLI 用法：
  KB_DIR=~/.kb DAYS=7 REGISTRY=jobs_registry.yaml python3 kb_review_collect.py
  输出：JSON 到 stdout

Exit codes:
  0 — JSON 已产出（status 字段指明 ok / llm_failed / collector_failed）
  1 — 致命错误（参数缺失/注册表不可读），stderr 有原因
"""
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta


# ══════════════════════════════════════════════════════════════════════
# 1. Registry 加载 — 单一真理源
# ══════════════════════════════════════════════════════════════════════

def load_sources_from_registry(registry_path):
    """从 jobs_registry.yaml 读取所有声明 kb_source_file 的 enabled job。

    只解析扁平字段（flat YAML），和 check_registry.py 的 fallback parser
    同构，避免新增 PyYAML 依赖。

    Returns:
        list of dict: [{id, kb_source_file, kb_source_label, enabled, tier}, ...]
        按 registry 定义顺序返回，只包含 enabled=true 且有 kb_source_file 的 job。
    """
    if not os.path.isfile(registry_path):
        raise FileNotFoundError(f"registry not found: {registry_path}")

    with open(registry_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    sources = []
    current = None
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # New job record
        if stripped.startswith("- id:"):
            if current is not None and current.get("enabled") and current.get("kb_source_file"):
                sources.append(current)
            current = {"id": stripped.split(":", 1)[1].strip()}
            continue
        if current is None:
            continue
        # Key: value
        if ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()
            # Strip inline comment
            if "#" in val:
                val = val[: val.index("#")].strip()
            val = val.strip('"').strip("'")
            if val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            current[key] = val

    # Final record
    if current is not None and current.get("enabled") and current.get("kb_source_file"):
        sources.append(current)

    return sources


# ══════════════════════════════════════════════════════════════════════
# 2. H2 章节解析器 — 替代行级日期匹配
# ══════════════════════════════════════════════════════════════════════

_DATE_RE = re.compile(r"(20\d{2}[-/.]?\d{2}[-/.]?\d{2})")


def _date_patterns_for_window(days, today=None):
    """生成最近 N 天的日期字符串集合（YYYY-MM-DD + YYYYMMDD 两种格式）。"""
    if today is None:
        today = datetime.now()
    patterns = set()
    for i in range(days):
        d = today - timedelta(days=i)
        patterns.add(d.strftime("%Y-%m-%d"))
        patterns.add(d.strftime("%Y%m%d"))
        patterns.add(d.strftime("%Y/%m/%d"))
    return patterns


def extract_recent_sections(content, days, max_chars, today=None):
    """按 H2 (`^## `) 章节拆分 markdown，返回最近 N 天的章节合并文本。

    契约：
      - 每个 `## ` header 开启一个章节，body 持续到下一个 `## ` 或 EOF
      - 章节被保留当 header 或 body 前 5 行包含窗口内日期模式
      - 超过 max_chars 则在章节边界截断（不切断章节中间）
      - 无 H2 结构时 fallback 到最后 50 行（避免完全空白）

    Args:
        content: 源文件完整文本
        days: 时间窗口天数
        max_chars: 输出 budget
        today: 测试注入参数（datetime）

    Returns:
        str — 合并后的章节文本（可能为空字符串）
    """
    if not content:
        return ""

    patterns = _date_patterns_for_window(days, today=today)
    lines = content.split("\n")

    # Split into sections
    sections = []  # list of (header_line, [body_lines])
    current_header = None
    current_body = []
    for line in lines:
        if line.startswith("## "):
            if current_header is not None:
                sections.append((current_header, current_body))
            current_header = line
            current_body = []
        else:
            if current_header is not None:
                current_body.append(line)
    if current_header is not None:
        sections.append((current_header, current_body))

    # No H2 structure — fallback to last 50 non-empty lines
    if not sections:
        fallback_lines = [l for l in lines if l.strip()][-50:]
        text = "\n".join(fallback_lines)
        return text[:max_chars] if len(text) > max_chars else text

    # Filter sections whose header or first 5 body lines mention a window date
    recent = []
    for header, body in sections:
        probe = header + "\n" + "\n".join(body[:5])
        if any(p in probe for p in patterns):
            recent.append((header, body))

    # Budget-aware concatenation (section boundaries only)
    if not recent:
        return ""

    out_chunks = []
    total = 0
    for header, body in recent[:10]:  # at most 10 sections per source
        block = header + "\n" + "\n".join(body).rstrip()
        block_len = len(block) + 2  # for separator
        if total + block_len > max_chars:
            remaining = max_chars - total
            if remaining > 400:  # only truncate if meaningfully large
                # Truncate at next newline to avoid mid-line cut
                cut = block[:remaining].rfind("\n")
                if cut > 0:
                    out_chunks.append(block[:cut] + "\n...[truncated]")
            break
        out_chunks.append(block)
        total += block_len

    return "\n\n".join(out_chunks)


# ══════════════════════════════════════════════════════════════════════
# 3. 采集：notes + sources
# ══════════════════════════════════════════════════════════════════════

def collect_notes(kb_dir, days, max_chars, today=None):
    """读取 ~/.kb/notes/*.md 中最近 N 天的笔记内容（去 frontmatter）。

    文件名约定：YYYYMMDDHHMMSS.md（V27+）
    """
    if today is None:
        today = datetime.now()
    cutoff = (today - timedelta(days=days)).strftime("%Y%m%d")

    notes_dir = os.path.join(kb_dir, "notes")
    if not os.path.isdir(notes_dir):
        return ""

    collected = []
    total_chars = 0
    for path in sorted(glob.glob(os.path.join(notes_dir, "*.md")), reverse=True):
        basename = os.path.basename(path)
        file_date = basename[:8]
        if file_date < cutoff:
            break
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
        except OSError:
            continue
        # Strip YAML frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        snippet = content[:400]  # richer per-note content than V29's 300
        if total_chars + len(snippet) > max_chars:
            break
        collected.append(f"[{file_date}] {snippet}")
        total_chars += len(snippet)

    return "\n---\n".join(collected)


def collect_sources(kb_dir, registry_path, days, max_chars_per_source, today=None):
    """从 registry 发现 source 文件，按 H2 章节提取最近 N 天内容。

    Returns:
        dict: {
            "text": str,            # 合并后完整文本（含标签分隔）
            "used": [labels],       # 成功提取到内容的源标签列表
            "skipped": [labels],    # 声明存在但本期无内容的源
            "missing": [labels],    # 文件不存在的源
        }
    """
    sources = load_sources_from_registry(registry_path)
    sources_dir = os.path.join(kb_dir, "sources")

    output_blocks = []
    used_labels = []
    skipped_labels = []
    missing_labels = []

    for job in sources:
        filename = job["kb_source_file"]
        label = job.get("kb_source_label") or job["id"]
        path = os.path.join(sources_dir, filename)

        if not os.path.isfile(path):
            missing_labels.append(label)
            continue

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            missing_labels.append(label)
            continue

        section_text = extract_recent_sections(
            content, days, max_chars_per_source, today=today
        )
        if section_text.strip():
            output_blocks.append(f"### {label}\n{section_text}")
            used_labels.append(label)
        else:
            skipped_labels.append(label)

    return {
        "text": "\n\n".join(output_blocks),
        "used": used_labels,
        "skipped": skipped_labels,
        "missing": missing_labels,
    }


# ══════════════════════════════════════════════════════════════════════
# 4. KB 统计
# ══════════════════════════════════════════════════════════════════════

def read_index_stats(kb_dir):
    """读取 index.json，返回 (index_total, note_count, top_themes_str)。"""
    index_path = os.path.join(kb_dir, "index.json")
    notes_dir = os.path.join(kb_dir, "notes")

    index_total = 0
    themes_str = "技术/AI"
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("entries", [])
        index_total = len(entries)
        tag_counter = Counter()
        for e in entries:
            tag_counter.update(e.get("tags", []))
        top = [t for t, _ in tag_counter.most_common(5)]
        if top:
            themes_str = " / ".join(top)
    except (OSError, json.JSONDecodeError):
        pass

    note_count = 0
    if os.path.isdir(notes_dir):
        note_count = len(glob.glob(os.path.join(notes_dir, "*.md")))

    return index_total, note_count, themes_str


# ══════════════════════════════════════════════════════════════════════
# 5. LLM 调用
# ══════════════════════════════════════════════════════════════════════

PROXY_URL = "http://127.0.0.1:5002/v1/chat/completions"
LLM_MODEL = "any"
LLM_TIMEOUT = 120  # V37.5: 从 60s 上调到 120s，LLM 推理预算足够
MAX_LLM_TOKENS = 1200


def build_prompt(notes_text, sources_text, days, index_total, note_count, themes):
    """构造 LLM prompt。注意 notes/sources 现在是真实内容，非空壳。"""
    return f"""你是一位知识管理专家和技术趋势分析师。以下是用户知识库中最近 {days} 天的内容。
请完成以下分析（用中文回答，总字数控制在 600 字以内）：

1. **本期亮点**（3-5个要点）：最值得关注的信息，说明为什么重要
2. **跨领域关联**（2-3条）：不同来源之间的联系（如 ArXiv 论文趋势 + HN 讨论热点 = 行业信号）
3. **行动建议**（2-3条）：基于这些信息，用户应该关注或尝试什么
4. **知识空白**（1-2条）：这些信息没有覆盖到但可能重要的领域

═══ 笔记内容 ═══
{notes_text or '（本期无笔记）'}

═══ 来源归档 ═══
{sources_text or '（本期无来源归档更新）'}

═══ 统计信息 ═══
知识库总条目: {index_total} 条
本期笔记: {note_count} 篇
活跃标签: {themes}"""


def call_llm(prompt, timeout=LLM_TIMEOUT, url=PROXY_URL, model=LLM_MODEL):
    """调用本地 Proxy:5002 做 LLM 推理。

    Returns:
        (ok: bool, content: str, reason: str)
        成功：(True, analysis_text, "")
        失败：(False, "", error_reason)

    失败路径**不降级**——调用方必须 fail-fast，不能伪装成成功。
    """
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_LLM_TOKENS,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, "", f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, "", f"URLError: {e.reason}"
    except (TimeoutError, json.JSONDecodeError) as e:
        return False, "", f"{type(e).__name__}: {e}"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"

    # Validate response structure
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return False, "", f"invalid response structure: {e}"

    if not content or not content.strip():
        return False, "", "LLM returned empty content"

    # V37.5 最小内容阈值：防止空壳响应被误判为成功
    if len(content.strip()) < 80:
        return False, "", f"LLM content too short ({len(content.strip())} chars, min 80)"

    return True, content.strip(), ""


# ══════════════════════════════════════════════════════════════════════
# 6. Review 输出构造
# ══════════════════════════════════════════════════════════════════════

def build_review_markdown(
    date_str, days, llm_content, index_total, note_count, themes,
    sources_used, sources_skipped, sources_missing,
):
    """生成 review_YYYYMMDD.md 文件内容。

    V37.5: llm_analyzed 字段只在 LLM 真的成功时才写入 true。
    """
    sources_block_lines = []
    if sources_used:
        sources_block_lines.append(f"**本期覆盖源** ({len(sources_used)}):")
        for label in sources_used:
            sources_block_lines.append(f"  - ✓ {label}")
    if sources_skipped:
        sources_block_lines.append(f"\n**本期无更新**:")
        for label in sources_skipped:
            sources_block_lines.append(f"  - ○ {label}")
    if sources_missing:
        sources_block_lines.append(f"\n**文件缺失**:")
        for label in sources_missing:
            sources_block_lines.append(f"  - ✗ {label}")
    sources_block = "\n".join(sources_block_lines) if sources_block_lines else "（无）"

    return f"""---
date: {date_str}
type: review
period: {days}days
llm_analyzed: true
sources_used: {len(sources_used)}
sources_missing: {len(sources_missing)}
---

# 知识回顾 {date_str}（最近 {days} 天）

## 基础统计
- 知识库总条目：{index_total} 条（含 notes + sources 索引）
- 本期笔记：{note_count} 篇（notes/*.md 累计）
- 活跃标签：{themes}

## 源覆盖
{sources_block}

## LLM 深度分析

{llm_content}
"""


def build_wa_message(
    date_str, days, index_total, note_count, llm_content, sources_count
):
    """生成 WhatsApp/Discord 推送消息。

    V37.5: 不再追加无实现的 follow-up 悬空承诺字符串。
    """
    header = (
        f"📚 知识回顾 {date_str}"
        f"（{days}天 | KB总条目 {index_total} | 本期笔记 {note_count}篇 "
        f"| 覆盖 {sources_count} 源）"
    )
    # Truncate LLM content for WhatsApp
    body = llm_content[:1400] if len(llm_content) > 1400 else llm_content
    return f"{header}\n\n{body}"


# ══════════════════════════════════════════════════════════════════════
# 7. Main entry — orchestrate collect + call + emit JSON
# ══════════════════════════════════════════════════════════════════════

# Per-source budget constants
MAX_NOTES_CHARS = 4000
MAX_SOURCE_CHARS = 3000  # per source file
PROMPT_TRUNCATE_NOTES = 3500
PROMPT_TRUNCATE_SOURCES = 4500  # merged across all sources


def run(kb_dir, days, registry_path, today=None, llm_caller=None):
    """Orchestrate the full collect → call → build pipeline.

    Returns a dict (JSON-serializable) with the result envelope.

    Args:
        llm_caller: optional callable(prompt) -> (ok, content, reason) for tests.
    """
    date_str = (today or datetime.now()).strftime("%Y%m%d")

    # Collect
    index_total, note_count, themes = read_index_stats(kb_dir)
    notes_text = collect_notes(kb_dir, days, MAX_NOTES_CHARS, today=today)

    try:
        sources_info = collect_sources(
            kb_dir, registry_path, days, MAX_SOURCE_CHARS, today=today
        )
    except FileNotFoundError as e:
        return {
            "status": "collector_failed",
            "reason": str(e),
            "date": date_str,
            "days": days,
        }

    # Build prompt — truncate from collected text for LLM budget
    prompt_notes = notes_text[:PROMPT_TRUNCATE_NOTES]
    prompt_sources = sources_info["text"][:PROMPT_TRUNCATE_SOURCES]
    prompt = build_prompt(
        prompt_notes, prompt_sources, days, index_total, note_count, themes
    )

    # Call LLM
    caller = llm_caller if llm_caller is not None else call_llm
    ok, llm_content, reason = caller(prompt)

    if not ok:
        return {
            "status": "llm_failed",
            "reason": reason,
            "date": date_str,
            "days": days,
            "index_total": index_total,
            "note_count": note_count,
            "themes": themes,
            "sources_used": sources_info["used"],
            "sources_skipped": sources_info["skipped"],
            "sources_missing": sources_info["missing"],
        }

    # Build output artifacts
    review_md = build_review_markdown(
        date_str, days, llm_content, index_total, note_count, themes,
        sources_info["used"], sources_info["skipped"], sources_info["missing"],
    )
    wa_message = build_wa_message(
        date_str, days, index_total, note_count, llm_content,
        len(sources_info["used"]),
    )

    return {
        "status": "ok",
        "date": date_str,
        "days": days,
        "index_total": index_total,
        "note_count": note_count,
        "themes": themes,
        "sources_used": sources_info["used"],
        "sources_skipped": sources_info["skipped"],
        "sources_missing": sources_info["missing"],
        "llm_content": llm_content,
        "review_markdown": review_md,
        "wa_message": wa_message,
    }


def main():
    kb_dir = os.environ.get("KB_DIR") or os.path.expanduser("~/.kb")
    days = int(os.environ.get("DAYS") or "7")
    # REGISTRY defaults to script's own directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    registry = os.environ.get("REGISTRY") or os.path.join(script_dir, "jobs_registry.yaml")

    try:
        result = run(kb_dir, days, registry)
    except Exception as e:
        err = {
            "status": "collector_failed",
            "reason": f"{type(e).__name__}: {e}",
            "days": days,
        }
        print(json.dumps(err, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    # Exit 0 even on llm_failed — bash wrapper decides how to handle
    sys.exit(0)


if __name__ == "__main__":
    main()

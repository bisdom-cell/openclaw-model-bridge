"""ontology_parser — V37.8.7 LLM 摘要输出的鲁棒解析器（纯函数，可单测）

V37.8.6 之前用严格位置解析 (lines[i], lines[i+1], lines[i+2]) + i+=3 步进，
LLM 漏一行"要点"或多一行空白就会让所有后续条目级联错位 —— 用户看到
*---*、*价值：⭐⭐⭐⭐* 当作 cn_title、中文标题串到 highlight 槽等乱序症状。

V37.8.7 重构：
  Step 1: 按 ---/===/*** 等分隔符切块 (separator-based split)
  Step 2: 块内按前缀键查找 cn_title/highlight/stars (key-based extraction)
         单块缺行不影响其他块，每块独立解析

签约：输入 LLM 原始文本，返回 list[tuple[str, str, str]] = [(cn_title, highlight, stars), ...]
"""
import re

# 分隔符匹配：行首/行尾 + 至少 3 个 [-=*_] 字符（兼容 LLM 各种变体）
# 注意：保留 (?:^|\n) ... (?:\n|$) 边界让 re.split 不吞内容
_SEPARATOR_RE = re.compile(r'(?:^|\n)\s*[-=*_]{3,}\s*(?:\n|$)')

# 文章序号行（LLM 有时把 prompt 里的 "文章N:" 保留到输出中）
_ARTICLE_PREFIX_RE = re.compile(r'(?m)^\s*文章\d+[：:].*$')

# 标题前缀正则：兼容"中文标题：xxx"、"标题: xxx"、"中文标题 xxx" 等变体
_TITLE_PREFIX_RE = re.compile(r'^(?:中文)?标题\s*[：:]?\s*')

# 纯分隔符行（块内残留防御）
_PURE_SEPARATOR_RE = re.compile(r'^[-=*_]{3,}$')


def parse_llm_blocks(llm_content: str) -> list:
    """把 LLM 原始输出解析成 [(cn_title, highlight, stars), ...]。

    - 容忍单块缺行（任何字段可为空字符串）
    - 容忍单块多行空白
    - 容忍 LLM 用 "标题"/"中文标题" 不同变体
    - 容忍 LLM 把"价值"行写成不带"价值："前缀但含 ⭐
    - 容忍 LLM 在输出中保留 "文章N:" 序号行
    - 兼容 ---/====/*** 多种分隔符
    """
    if not llm_content:
        return []

    # 先剥掉"文章N:"序号行（LLM 偶尔保留 prompt 结构）
    cleaned = _ARTICLE_PREFIX_RE.sub('', llm_content)

    # 按分隔符切块（re.split 会自动丢弃匹配的分隔符段）
    raw_blocks = _SEPARATOR_RE.split(cleaned)

    parsed = []
    for block in raw_blocks:
        cn_title, highlight, stars = _parse_single_block(block)
        # 任一字段非空就算有效块
        if cn_title or highlight or stars:
            parsed.append((cn_title, highlight, stars))

    return parsed


def _parse_single_block(block: str) -> tuple:
    """块内按前缀键查找三元组（不依赖位置）。"""
    cn_title = ""
    highlight = ""
    stars = ""

    for line in block.split('\n'):
        line = line.strip()
        if not line:
            continue
        # 防御：分隔符残留（_SEPARATOR_RE 边界 case 漏掉时）
        if _PURE_SEPARATOR_RE.match(line):
            continue
        # 按前缀/特征识别字段
        if line.startswith('中文标题') or line.startswith('标题'):
            # 剥离 "中文标题：" / "标题:" 前缀，保留实际标题
            cn_title = _TITLE_PREFIX_RE.sub('', line).strip()
        elif line.startswith('要点'):
            # 保留"要点："前缀便于 WhatsApp/Discord 显示
            highlight = line
        elif '⭐' in line:
            # 价值行特征：含星号；不依赖"价值："前缀（LLM 有时用"评分："等变体）
            if line.startswith('价值'):
                stars = line
            else:
                # 添加"价值："前缀让显示一致
                stars = f"价值：{line}"
        else:
            # 无前缀无 ⭐ 的普通行：作为 cn_title fallback（如果还没设置）
            # 这覆盖 LLM 直接输出标题不带"中文标题："前缀的情况
            if not cn_title:
                cn_title = line

    return (cn_title, highlight, stars)


if __name__ == "__main__":
    # 命令行 smoke test
    test_cases = [
        (
            "中文标题：A\n要点：B\n价值：⭐⭐⭐\n---\n中文标题：D\n要点：E\n价值：⭐⭐⭐⭐",
            2, ("A", "要点：B", "价值：⭐⭐⭐"),
            "正常 2 篇 3 字段",
        ),
        (
            "中文标题：A\n要点：B\n价值：⭐⭐⭐\n---\n中文标题：D\n价值：⭐⭐⭐⭐",
            2, ("A", "要点：B", "价值：⭐⭐⭐"),
            "第 2 篇缺要点 — 原 V37.8.6 的 i+=3 会级联，新解析器隔离",
        ),
        (
            "标题：A\n要点：B\n⭐⭐⭐⭐⭐",
            1, ("A", "要点：B", "价值：⭐⭐⭐⭐⭐"),
            "标题（无中文前缀）+ 单纯星号行（无价值前缀）",
        ),
    ]
    all_pass = True
    for content, exp_count, exp_first, desc in test_cases:
        got = parse_llm_blocks(content)
        ok = (len(got) == exp_count and got[0] == exp_first)
        marker = "✓" if ok else "✗"
        print(f"{marker} {desc}: got {len(got)} blocks")
        if not ok:
            all_pass = False
            print(f"   expected first: {exp_first}")
            print(f"   got first: {got[0] if got else None}")
    exit(0 if all_pass else 1)

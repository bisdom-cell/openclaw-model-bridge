"""ontology_parser — V37.8.7 LLM 摘要输出的鲁棒解析器（纯函数，可单测）

V37.8.6 之前用严格位置解析 (lines[i], lines[i+1], lines[i+2]) + i+=3 步进，
LLM 漏一行"要点"或多一行空白就会让所有后续条目级联错位 —— 用户看到
*---*、*价值：⭐⭐⭐⭐* 当作 cn_title、中文标题串到 highlight 槽等乱序症状。

V37.8.7 重构：
  Step 1: 按 ---/===/*** 等分隔符切块 (separator-based split)
  Step 2: 块内按前缀键查找 cn_title/highlight/stars (key-based extraction)
         单块缺行不影响其他块，每块独立解析

V37.9.62 升级 (V37.9.51 rss_blogs / V37.9.50 semantic_scholar / V37.9.45 hf_papers
同款 Opportunity Radar #2 模板, Sub-Stage 4b 6/6 ontology_sources 迁移)：
  - 新增 parse_6field_output(content) 函数支持 6 字段格式
    (📌 中文标题 / 🔑 核心要点 / 💡 关键洞察 / 🎯 实践启发 / ⭐ 评级 / 🎚️ 项目对齐度)
  - **保留 V37.8.7 separator 切块设计**: 6 字段也按 --- 切块, 块内 key-based 解析,
    单块缺行不级联污染 (ontology 血案防线必须保留)
  - 老 parse_llm_blocks(content) 仍可用 (3 字段 fallback / 向后兼容老 cron 输出)

签约：
  - parse_llm_blocks(content) → list[tuple[str, str, str]]  # 3 字段 (V37.8.7)
  - parse_6field_output(content) → list[dict]                # 6 字段 (V37.9.62)
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


# ─── V37.9.62: 6 字段 parser (V37.9.51 rss_blogs / V37.9.50 semantic_scholar / V37.9.45 hf_papers
#               同款 Opportunity Radar #2 模板, Sub-Stage 4b 6/6 ontology_sources 迁移) ───
#
# **保留 V37.8.7 ontology 血案防线**: 仍按 --- 分隔符切块, 块内 key-based 解析,
# 单块缺字段不影响其他块. 6 字段也是 separator-based + key-based, 不是位置索引!


def parse_6field_output(llm_content: str) -> list:
    """把 LLM 6 字段输出解析成 list[dict].

    V37.9.62 新增 (V37.9.51 rss_blogs / V37.9.50 semantic_scholar / V37.9.45 hf_papers
    同款 Opportunity Radar #2 模板, Sub-Stage 4b 6/6 ontology_sources 迁移).

    每个 dict 含 6 个字段:
      - cn_title: 中文标题 (📌)
      - highlights: 核心要点 (🔑)
      - insight: 关键洞察 (💡)
      - practice: 实践启发 (🎯)
      - rating: 评级 + 推荐场景 (⭐)
      - alignment: 项目对齐度 + 一句话原因 (🎚️ V37.9.51 新增)

    设计契约 (V37.8.7 ontology 血案防线保留):
      - 按 --- 分隔符切块 → 单块缺字段不级联污染下一块
      - 块内 key-based field 识别 → 不依赖位置, 容忍 LLM 输出顺序变化
      - 任一字段非空就算有效块 (允许 LLM 偶尔缺字段)
      - 兼容 🎚 fallback (无 variation selector U+FE0F)
    """
    if not llm_content:
        return []

    # 先剥掉"文章N:"序号行 (LLM 偶尔保留 prompt 结构)
    cleaned = _ARTICLE_PREFIX_RE.sub('', llm_content)

    # 按分隔符切块 (V37.8.7 同款, ontology 血案防线)
    raw_blocks = _SEPARATOR_RE.split(cleaned)

    parsed = []
    for block in raw_blocks:
        fields = _parse_single_6field_block(block)
        # 任一字段非空就算有效块
        if any(fields.values()):
            parsed.append(fields)

    return parsed


def _parse_single_6field_block(block: str) -> dict:
    """块内 key-based 解析 6 字段 (state machine 累积模式).

    V37.9.62 镜像 V37.9.51 rss_blogs parse_6field_output 块内逻辑,
    但保留 V37.8.7 separator-based 切块设计 (调用方 parse_6field_output 负责切块).
    """
    fields = {
        'cn_title': '',
        'highlights': '',
        'insight': '',
        'practice': '',
        'rating': '',
        'alignment': '',
    }
    current_field = None
    current_buffer = []

    def flush():
        if current_field and current_buffer:
            fields[current_field] = '\n'.join(current_buffer).strip()

    for raw in block.split('\n'):
        line = raw.rstrip()
        # 跳过分隔符残留 (V37.8.7 防御)
        if _PURE_SEPARATOR_RE.match(line.strip()):
            continue

        stripped = line.lstrip()

        # 📌 中文标题
        if stripped.startswith('📌'):
            flush()
            current_field = 'cn_title'
            current_buffer = []
            # 提取冒号后内容作为单行 title 值
            m = re.match(r'.*📌\s*(?:中文)?标题\s*[:：]?\s*(.*)', stripped)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # 🔑 核心要点
        if stripped.startswith('🔑'):
            flush()
            current_field = 'highlights'
            current_buffer = []
            continue
        # 💡 关键洞察
        if stripped.startswith('💡'):
            flush()
            current_field = 'insight'
            current_buffer = []
            continue
        # 🎯 实践启发
        if stripped.startswith('🎯'):
            flush()
            current_field = 'practice'
            current_buffer = []
            continue
        # 🎚️ 项目对齐度 (V37.9.51 新增, fallback 🎚 if no variation selector)
        if stripped.startswith('🎚️') or stripped.startswith('🎚'):
            flush()
            current_field = 'alignment'
            current_buffer = []
            m = re.match(r'.*🎚️?\s*(?:项目)?对齐度?\s*[:：]?\s*(.*)', stripped)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        # ⭐ 评级 (启发式: 行首 ⭐ + 含"评级"或"推荐场景"或纯星号行)
        if stripped.startswith('⭐') and current_field != 'rating':
            if '评级' in line or '推荐场景' in line or re.match(r'\s*⭐+\s*$', line):
                flush()
                current_field = 'rating'
                current_buffer = [stripped]
                continue

        # 普通行 → append 到 current_field
        if current_field is not None:
            current_buffer.append(line)
        elif line.strip():
            # 字段头之前的非空行 → 静默丢弃
            pass

    flush()
    return fields


if __name__ == "__main__":
    # 命令行 smoke test (V37.8.7 3-字段 + V37.9.62 6-字段)
    test_cases_3field = [
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
    print("=== V37.8.7 parse_llm_blocks (3 字段, 向后兼容) ===")
    for content, exp_count, exp_first, desc in test_cases_3field:
        got = parse_llm_blocks(content)
        ok = (len(got) == exp_count and got[0] == exp_first)
        marker = "✓" if ok else "✗"
        print(f"{marker} {desc}: got {len(got)} blocks")
        if not ok:
            all_pass = False
            print(f"   expected first: {exp_first}")
            print(f"   got first: {got[0] if got else None}")

    # V37.9.62: 6 字段测试
    print("\n=== V37.9.62 parse_6field_output (6 字段, Opportunity Radar #2) ===")
    test_6field = """📌 中文标题: 知识图谱推理新范式
🔑 核心要点:
- 要点1
- 要点2

💡 关键洞察:
深度分析段落

🎯 实践启发:
- 启发1

⭐ 评级: ⭐⭐⭐⭐ / 推荐场景: 知识工程从业者

🎚️ 项目对齐度: ⭐⭐⭐⭐ / 直接相关 ontology engine

---

📌 中文标题: 语义网十年回顾
🔑 核心要点:
- 单独要点

💡 关键洞察:
洞察段落

⭐ 评级: ⭐⭐⭐ / 推荐场景: 历史背景

🎚️ 项目对齐度: ⭐⭐⭐ / 历史参考
"""
    got_6 = parse_6field_output(test_6field)
    ok_6 = (
        len(got_6) == 2
        and got_6[0]['cn_title'] == '知识图谱推理新范式'
        and '要点1' in got_6[0]['highlights']
        and '⭐⭐⭐⭐' in got_6[0]['alignment']
        and got_6[1]['cn_title'] == '语义网十年回顾'
        and '历史参考' in got_6[1]['alignment']
    )
    marker_6 = "✓" if ok_6 else "✗"
    print(f"{marker_6} 2 篇 6 字段完整解析: got {len(got_6)} blocks")
    if not ok_6:
        all_pass = False
        for i, b in enumerate(got_6):
            print(f"   block {i}: {b}")

    # V37.9.62: 第 2 篇缺 practice 不级联污染 (用真实 LLM 多行格式)
    test_6field_partial = """📌 中文标题: A
🔑 核心要点:
- 要点A
💡 关键洞察:
洞察A
🎯 实践启发:
- 启发A
⭐ 评级: ⭐⭐⭐
🎚️ 项目对齐度: ⭐⭐⭐

---

📌 中文标题: B
🔑 核心要点:
- 要点B
💡 关键洞察:
洞察B
⭐ 评级: ⭐⭐⭐⭐
🎚️ 项目对齐度: ⭐⭐⭐⭐ / 强相关
"""
    got_partial = parse_6field_output(test_6field_partial)
    ok_partial = (
        len(got_partial) == 2
        and got_partial[0]['practice'] != ''  # 第1篇 practice 完整
        and got_partial[1]['cn_title'] == 'B'  # 第2篇 cn_title 不被第1篇污染
        and got_partial[1]['practice'] == ''   # 第2篇缺 practice 但不影响其他字段
        and got_partial[1]['alignment'] != ''  # 第2篇 alignment 正常
    )
    marker_partial = "✓" if ok_partial else "✗"
    print(f"{marker_partial} 第 2 篇缺 practice 不级联污染 (V37.8.7 血案防线): got {len(got_partial)} blocks")
    if not ok_partial:
        all_pass = False
        for i, b in enumerate(got_partial):
            print(f"   block {i}: {b}")

    exit(0 if all_pass else 1)

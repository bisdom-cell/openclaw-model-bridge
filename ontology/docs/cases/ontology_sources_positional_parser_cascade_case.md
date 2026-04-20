# ontology_sources 推送格式错位血案（V37.8.7）

> **日期**：2026-04-15（与 V37.8.6 Dream 自引用幻觉同日）
> **血案类**：MR-4 (silent-failure-is-a-bug) 第 8 次演出
> **严重度**：high — 用户 WhatsApp 收到推送结构性错位
> **核心教训**：LLM 输出永远不能用严格位置解析；缺一行就级联污染所有后续条目

---

## TL;DR

用户在 WhatsApp 看到 ontology_sources 推送的 ontology 学术动态出现严重错位：

```
*中文标题：基于局部与全局信息的节点影响力识别*    ← 第 1 篇正确
来源：DKE...
要点：...
价值：⭐⭐⭐

*---*                                               ← 第 2 篇 cn_title = "---"!
来源：DKE...
中文标题：面向烹饪替换的常识推理框架                ← 应该在 cn_title 槽，跑到 highlight 槽
*价值：⭐⭐⭐⭐*                                     ← 第 3 篇 cn_title = "价值：⭐⭐⭐⭐"!
来源：DKE...
```

LLM 偶尔漏一行"要点"，原解析器 `i += 3` 步进让所有后续条目右移一格级联污染。

V37.8.7 重写为 separator 切块 + 块内 key-based prefix 解析，单块缺行不影响其他块。

## 完整因果链

```
ontology_sources 10:00 cron 触发
│
├─ Phase 1 抓取 4 个 RSS 源（W3C/JWS/DKE/KBS）
│
├─ Phase 2 关键词过滤 → N 篇候选文章
│
├─ Phase 3 LLM 摘要（adapter:5001 + Qwen3）
│  └─ 输出格式预期：每篇 3 行 + --- 分隔
│       ┌──────────────────────────────┐
│       │ 中文标题：A                   │
│       │ 要点：B                       │
│       │ 价值：⭐⭐⭐                  │
│       │ ---                           │
│       │ 中文标题：D                   │
│       │ ⚠️ 漏了"要点：" 行            │  ← LLM 偶尔不严格遵守
│       │ 价值：⭐⭐⭐⭐                │
│       │ ---                           │
│       │ ...                           │
│       └──────────────────────────────┘
│
├─ Phase 4 解析（V37.8.6 之前的位置解析器）
│  │
│  │  lines = [strip + filter empty/separator 行]
│  │  ├─ "中文标题：A"
│  │  ├─ "要点：B"
│  │  ├─ "价值：⭐⭐⭐"
│  │  ├─ "中文标题：D"     ← LLM 漏了下面这行
│  │  ├─ "价值：⭐⭐⭐⭐"  ← (本应是"要点：E"在这里)
│  │  ├─ "中文标题：F"
│  │  ├─ "要点：G"
│  │  └─ "价值：⭐⭐⭐⭐⭐"
│  │
│  │  while 循环 i += 3：
│  │  ├─ i=0: cn=lines[0]="中文标题：A", hl=lines[1]="要点：B", st=lines[2]="价值：⭐⭐⭐" ✓
│  │  ├─ i=3: cn=lines[3]="中文标题：D", hl=lines[4]="价值：⭐⭐⭐⭐"❌, st=lines[5]="中文标题：F"❌
│  │  └─ i=6: cn=lines[6]="要点：G"❌, hl=lines[7]="价值：⭐⭐⭐⭐⭐"❌, st=<out of range>
│  │
│  │  ⚠️ 整个解析全部错位，且 emit 端不做语义校验
│
├─ Phase 5 emit 消息文本
│  ├─ 文章 1: *中文标题：基于...*  ← 正确
│  ├─ 文章 2: *中文标题：D*        ← 看似 cn_title，实际是错位的 highlight
│  │           highlight=价值⭐⭐⭐⭐ → 显示成第二行
│  │           stars=中文标题：F  → 不含⭐ 被 if 判断过滤掉
│  └─ 文章 3: *要点：G*           ← cn_title 槽里是"要点："开头的字符串
│              highlight=价值⭐⭐⭐⭐⭐ → 当成 highlight 显示
│
└─ Phase 6 推送 WhatsApp + Discord
   └─ 用户看到的污染输出：cn_title 槽里出现 ---、价值⭐⭐⭐⭐ 等错位字段
```

实际用户截图比上面分析更乱（显示了 `*---*` 作为 cn_title），说明 LLM 还有别的格式偏差（如把 `---` 重复输出在内容行间）+ 解析器的 `^[-=*]{3,}$` 过滤可能漏掉某些边界 case（行尾空白、混合分隔符等）。

V37.8.7 的修复用 `re.split` **块切分** + 块内 **prefix-based extraction**，不再依赖位置 — 只要分隔符正确切块，块内任意字段缺失都能容忍。

## 三层根因

| 层级 | 问题 | 证据 |
|------|------|------|
| **触发器** | LLM 不严格遵守"3 行 + ---"格式（漏行/多空行/把 --- 放错位置） | Qwen3 指令遵循训练倾向"输出有用内容"而非"严格遵循三行" |
| **放大器** | `run_ontology_sources.sh:300-313` 用严格位置 `lines[i], lines[i+1], lines[i+2]` + `i += 3` 步进；任何单块缺行让所有后续条目右移一格级联污染 | 同样的代码模式之前在 V37.5 kb_review、V37.6 kb_evening 也出过事，是 silent failure 的同源 bug class |
| **掩护者** | emit 端 `*{cn_title}*` 直接输出 parse 结果，不做"cn_title 不应是分隔符 / 不应以已知前缀开头"等最小语义校验；用户不察觉就永远发现不了 | `run_ontology_sources.sh:320` `msg_lines.append(f"*{cn_title}*")` 无防御 |

## 时间线

| 时间 | 事件 | 影响 |
|------|------|------|
| 历史 | run_ontology_sources.sh 初版用 `i += 3` 位置解析 | 潜在 bug 一直存在 |
| 2026-04-15 ~14:00 | ontology_sources 10:00 cron 推送给用户 | 产生错位输出 |
| 2026-04-15 14:25 | 用户 WhatsApp 截图上报"格式好像有些不对" | 触发闭环 |
| 2026-04-15 V37.8.7 闭合 | 4 项结构修复 + INV-ONTOLOGY-001 + 24 单测 | |

## 为什么以前没发生（条件组合）

| 条件 | 以前 | 现在 |
|------|------|------|
| LLM 输出某条漏字段 | 偶发（每次 cron 都不一样） | 今天偶然触发 |
| 位置解析器 `i += 3` | 一直如此 | 一直如此 |
| emit 端无语义校验 | 一直如此 | 一直如此 |
| **用户察觉违和并上报** | 没注意 / 当作单条 LLM 错误 | 今天注意到结构性问题 |

类似 V37.8.6 Dream 血案：底层 bug 一直存在，只是用户察觉的偶然性让它今天才浮现。

## 与 V37.8.6 Dream 血案的对照

同一天发生两个 MR-4 演出，高度相关：

| 血案 | V37.8.6 (Dream) | V37.8.7 (ontology_sources) |
|------|----------------|----------------------------|
| 触发器 | scraped 内容含 surrogate UTF-8 | LLM 输出漏一行 |
| 放大器 | log() 写 stdout 污染 cache | 严格位置 i+=3 级联错位 |
| 掩护者 | LLM 编造"Hugging Face 危机" | emit 端无语义校验 |
| 修复 | 4 层防御 (log→stderr / sanitize / errors=replace / 反污染 prompt) | 解析器从位置→key-based |
| 共同教训 | **对 LLM 输出 / 系统数据流的任何"格式假设"都是脆弱的** | 同上 |

## V37.8.7 修复策略

### Step 1: 抽到独立模块（避开 V37.5 heredoc-only 不可测血案）

```python
# jobs/ontology_sources/ontology_parser.py
def parse_llm_blocks(llm_content: str) -> list[tuple[str, str, str]]:
    """[(cn_title, highlight, stars), ...]"""
```

### Step 2: separator 切块

```python
_SEPARATOR_RE = re.compile(r'(?:^|\n)\s*[-=*_]{3,}\s*(?:\n|$)')
raw_blocks = _SEPARATOR_RE.split(cleaned)
```

### Step 3: 块内 key-based 解析

```python
for line in block.split('\n'):
    if line.startswith('中文标题') or line.startswith('标题'):
        cn_title = re.sub(...).strip()
    elif line.startswith('要点'):
        highlight = line
    elif '⭐' in line:
        stars = line if line.startswith('价值') else f'价值：{line}'
    else:
        if not cn_title:
            cn_title = line  # fallback
```

### Step 4: shell 集成

```bash
export ONTOLOGY_JOBS_DIR="$JOB_DIR"  # heredoc 找模块路径
# heredoc:
sys.path.insert(0, os.environ.get("ONTOLOGY_JOBS_DIR", ""))
from ontology_parser import parse_llm_blocks
```

## 喂养本体

1. **本案例文档** `ontology/docs/cases/ontology_sources_positional_parser_cascade_case.md`
2. **INV-ONTOLOGY-001** 新增 7 checks（含 runtime python_assert 真跑用户实际看到的污染场景）
3. **test_ontology_parser.py** 24 单测覆盖正常 / 缺要点 / 缺价值 / 多种分隔符 / 端到端血案重现
4. **CLAUDE.md V37.8.7 changelog** + 文件表 + VERSION 0.37.8.7
5. **MR-4 silent-failure 案例库**：第 8 次演出，与 V37.8.6 Dream 血案同日发生强化"silent failure 持续以不同形态出现"

## 下一步迭代（V37.8.8 候选）

- [ ] **emit 端字段语义校验**：`*{cn_title}*` emit 前断言 `cn_title not in {'---', '===', '***'}` 且不以"价值："/"要点："开头
- [ ] **同模式审查**：扫其他 LLM 输出解析器（finance_news / dblp / hf_papers / semantic_scholar / freight_watcher）是否有类似位置解析
- [ ] **MR-12 元规则候选** `llm-output-parser-must-be-key-based-not-positional`：LLM 输出永远不能假设格式严格遵守，所有解析必须容忍单条缺/多/重排，用前缀/语义键定位

---

**状态**：2026-04-15 闭合 / V37.8.7 发布 / 24 单测 / 38 invariants / 1018 tests

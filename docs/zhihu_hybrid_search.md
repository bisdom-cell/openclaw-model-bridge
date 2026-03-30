# 让 AI 助手真正"记住"你的知识：从零构建混合检索系统的实战经验

> 我们在 WhatsApp AI 助手中实现了一套完整的混合检索（Hybrid Search）系统，让 AI 不再只会搜网络，而是能真正搜索和理解用户积累的本地知识库。本文分享完整的架构设计、踩坑经验和核心代码。

## 一、问题：AI 助手的"失忆症"

我们运营着一个基于 WhatsApp 的 AI 助手系统，后端接入 Qwen3-235B 大模型。系统每天自动从 5 个学术来源（ArXiv、HuggingFace、Semantic Scholar、DBLP、ACL Anthology）抓取论文，加上 HackerNews 热帖、行业动态等，积累了上百篇笔记和数千条来源记录。

**但有一个尴尬的问题**：当用户问"从我的文档中找到关于 DeepSeek 的文章"时，AI 助手不去搜本地知识库，而是直接调用 `web_search` 去互联网上搜索，然后用训练数据编造一个看似合理的回答。

用户积累的数据成了"沉睡的资产"，AI 完全不知道它们的存在。

## 二、为什么光靠 Prompt 解决不了？

我们最初的方案是在系统提示词（System Prompt）中写明指令：

```
当用户提到"文档"、"论文"、"文章"时，必须先用 read 工具读取
~/.kb/daily_digest.md，绝不凭训练数据编造。
```

**结果**：Qwen3 有明显的工具选择偏好，遇到"找文章"类请求，它倾向于调用 `web_search`（因为训练数据中搜索是最常见的模式），而非 `read`。即使在 Prompt 中反复强调，效果也不稳定。

**教训**：对于关键行为，Prompt 指令不如工具级别的保障可靠。与其告诉模型"你应该用 read 工具读某个路径"，不如直接给它一个语义明确的 `search_kb` 工具——工具名本身就是最强的行为引导。

## 三、架构设计：自定义工具注入 + 混合检索 + LLM 解读

### 3.1 整体流程

```
用户在 WhatsApp 问"找关于模型对齐的研究"
  ↓
Gateway 转发请求 → Tool Proxy 注入 search_kb 到 LLM 工具列表
  ↓
LLM (Qwen3) 看到 search_kb 工具 → 调用 search_kb(query="模型对齐")
  ↓
Proxy 拦截，不转发给 Gateway，本地执行混合检索：
  ① 语义搜索：embedding → cosine similarity → top 8 结果
  ② 关键词搜索：精确匹配 → 补充语义搜索遗漏的内容
  ③ 合并去重
  ↓
搜索结果注入对话上下文 → 第二次 LLM 调用（无工具，纯推理）
  ↓
LLM 基于搜索结果生成自然语言回答 → 返回 WhatsApp
```

### 3.2 自定义工具注入机制

关键创新：**Gateway 完全无感知**。我们没有修改 Gateway 代码，而是在中间件（Tool Proxy）层实现了工具注入和拦截。

```python
# proxy_filters.py — 自定义工具定义
CUSTOM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "搜索用户的知识库。当用户提到论文、文档、文章时必须调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    },
                    "source": {
                        "type": "string",
                        "enum": ["all", "arxiv", "hf", "dblp", "acl", "hn", "notes"],
                        "description": "搜索范围，默认 all"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

# filter_tools() 中自动注入
def filter_tools(tools):
    # ... 过滤 Gateway 原始工具到 ≤12 个 ...
    for custom_tool in CUSTOM_TOOLS:
        new_tools.append(custom_tool)  # 注入自定义工具
    return new_tools
```

LLM 响应中如果调用了 `search_kb`，Proxy 拦截执行，不转发给 Gateway：

```python
# tool_proxy.py — 拦截执行
def _handle_custom_tool_calls(self, response_json, original_body, rid):
    for choice in response_json["choices"]:
        tool_calls = choice["message"].get("tool_calls", [])
        custom_calls = [tc for tc in tool_calls
                       if tc["function"]["name"] in CUSTOM_TOOL_NAMES]
        if custom_calls:
            # 本地执行，不经过 Gateway
            result = self._execute_custom_tool(name, arguments)
            # search_kb 特殊处理：结果注入对话，再调一次 LLM
            return self._followup_llm_call(original_body, result, rid)
```

**这个模式的优势**：
- Gateway 无感知（不需要修改上游框架）
- LLM 无抵触（`search_kb` 和 `read`/`write` 是同级的正规工具）
- 可扩展（CUSTOM_TOOLS 列表可以添加更多自定义工具）

### 3.3 混合检索：语义搜索 + 关键词补充

这是整个系统的核心。单一检索方式都有盲区：

| | 语义搜索 | 关键词搜索 |
|---|---|---|
| **原理** | 文本 → 384 维向量 → cosine similarity | 字符串精确匹配（grep） |
| **擅长** | "模型对齐" → 匹配 "RLHF alignment" | "DeepSeek" → 精确命中含 "DeepSeek" 的行 |
| **弱点** | 可能漏掉生僻专有名词 | "深度求索" 搜不到 "DeepSeek" |

**混合就能互补**：

```python
def _search_kb(self, query, source="all"):
    results = []
    seen_files = set()

    # ── 1. 语义搜索（优先）──
    semantic_results = self._semantic_search(query, top_k=8)
    if semantic_results:
        for r in semantic_results:
            seen_files.add(r["filename"])
            results.append(format_result(r))

    # ── 2. 关键词补充（去重）──
    keyword_results = self._keyword_search(query, exclude_files=seen_files)
    if keyword_results:
        results.append(keyword_results)

    return combine(results)
```

### 3.4 Followup LLM 调用：让 AI 解读搜索结果

早期版本直接把搜索结果返回给用户，效果很差——用户看到的是原始文件片段和相关度分数。

改进方案：搜索结果注入对话上下文，再发起一次 LLM 调用，让模型用自然语言组织回答。

```python
def _followup_llm_call(self, original_body, kb_results, rid):
    followup_body = dict(original_body)
    msgs = list(followup_body["messages"])

    # 注入搜索结果
    msgs.append({
        "role": "system",
        "content": f"以下是知识库搜索结果，请基于这些结果回答用户问题。"
                   f"如果没有相关内容，如实告知。不要编造。\n\n{kb_results}"
    })
    followup_body["messages"] = msgs
    followup_body.pop("tools", None)  # 第二次调用不需要工具

    # 发起纯推理调用
    response = call_llm(followup_body)
    return response
```

**效果对比**：

直接返回（V1）：
```
📝 笔记:
  [openclaw_tasks_snapshot.md] ...search_query=all:deepseek+OR+all...
```

Followup LLM 解读（V2）：
```
根据您知识库中的最新资料，近期关于模型对齐的研究主要集中在：
1. 联邦个性化偏好优化（FedPDPO）：在保护隐私的前提下实现个性化对齐...
2. 多模态模型对齐（SeGroS）：通过语义锚定监督增强多模态一致性...
3. 对齐多样性研究（RLVR）：探讨对齐是否需要多样性...
```

## 四、本地 Embedding：零 API 调用的语义搜索

语义搜索的基础是 embedding 向量索引。我们选择完全本地化：

```
模型：paraphrase-multilingual-MiniLM-L12-v2
维度：384
语言：50+ 语言（中英双语优秀）
性能：Mac Mini M 系列 — 单条 ~10ms，批量 100 条 ~500ms
成本：零（本地运行，不调任何 API）
```

**两套数据并存**：

```
~/.kb/
├── sources/*.md          # 原始文本（人可读 + 关键词搜索）
├── notes/*.md            # 笔记（人可读 + 关键词搜索）
├── text_index/
│   ├── meta.json         # 索引元数据（文件名、分块信息）
│   └── vectors.bin       # 384 维向量（机器可读 + 语义搜索）
└── daily_digest.md       # 每日摘要
```

**索引策略**：
- 分块：400 字/块，80 字重叠（保证跨块内容不丢失）
- 增量：按文件 MD5 去重，只索引新增/变更的文件
- 定时：每 4 小时自动运行 `kb_embed.py` 增量索引

## 五、实测效果

### 验证 1：精确匹配（关键词搜索生效）

问："找关于 DeepSeek 的文章"
- 关键词搜索命中 3 个文件中的 "DeepSeek" 字符串 ✓
- 结果准确，来自本地知识库而非网络 ✓

### 验证 2：语义匹配（embedding 搜索生效）

问："最近有什么关于模型对齐的研究"
- 用户说"模型对齐"（中文），知识库中的论文是英文的 "RLHF alignment"
- 语义搜索正确匹配到 FedPDPO、SeGroS、RLVR 三篇论文 ✓
- **纯关键词搜索不可能做到这一点** ✓

### 验证 3：自然语言回答（Followup LLM 生效）

- 搜索结果不是原始文件片段，而是经过 LLM 解读的结构化回答 ✓
- 包含论文名称、核心贡献、发布日期 ✓

## 六、踩过的坑

### 坑 1：Prompt 指令 vs 工具设计

❌ 在 System Prompt 中写"你必须用 read 工具读 ~/.kb/daily_digest.md"
✅ 给 LLM 一个名叫 `search_kb` 的专用工具

**原理**：LLM 的工具选择更多是基于工具名和描述的语义匹配，而非 Prompt 中的指令。一个叫 `search_kb` 的工具，在用户问"搜索文档"时被调用的概率，远高于让 LLM 记住"应该用 read 工具读某个路径"。

### 坑 2：直接返回搜索结果 vs LLM 解读

❌ 把搜索到的文件片段直接发给用户
✅ 搜索结果注入对话，让 LLM 用自然语言组织回答

**代价**：多一次 LLM 调用（约 10 秒延迟）。但用户体验的提升是值得的。

### 坑 3：Gateway 不提供某些工具

我们尝试通过配置让 Gateway 提供更多工具给 LLM，折腾了多种配置方式都无效。最终发现：与其改 Gateway 配置，不如在 Proxy 层注入——**中间件的灵活性远超上游框架的配置**。

### 坑 4：Embedding 模型选择

选择 `paraphrase-multilingual-MiniLM-L12-v2` 的原因：
- 中英双语效果好（我们的知识库中英混合）
- 384 维（比 768/1024 维更省空间和计算）
- Mac Mini 上推理极快（10ms/条）
- 完全本地，不依赖任何 API

## 七、可复用的架构模式

这套系统的核心模式可以推广到任何 LLM 应用：

```
┌──────────────────────────────────────────────────┐
│  自定义工具注入模式（Custom Tool Injection）       │
│                                                    │
│  LLM Framework ─→ Middleware ─→ LLM Backend       │
│                      │                             │
│                 注入自定义工具                       │
│                 拦截工具调用                         │
│                 本地执行                            │
│                 结果回注/直返                        │
│                                                    │
│  优势：不改框架代码、LLM无感知、可扩展              │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│  混合检索模式（Hybrid Search）                     │
│                                                    │
│  查询 ─→ ① 语义搜索（embedding + cosine）         │
│       ─→ ② 关键词搜索（精确匹配，去重补充）        │
│       ─→ 合并去重 ─→ LLM 解读 ─→ 自然语言回答     │
│                                                    │
│  两套数据：原始文本(人可读) + 向量索引(机器可读)    │
│  优势：语义理解 + 精确匹配互补，覆盖面最大          │
└──────────────────────────────────────────────────┘
```

## 八、总结

| 维度 | 方案 |
|------|------|
| **检索策略** | 混合检索 = 语义搜索(embedding) + 关键词补充(grep) |
| **工具注入** | 中间件层注入自定义工具，Gateway/LLM 均无感知 |
| **Embedding** | 本地 sentence-transformers（384维，零 API 调用） |
| **结果处理** | Followup LLM 调用，自然语言解读而非原始片段 |
| **数据架构** | 双轨制：md 原文 + vectors.bin 向量，cron 定时同步 |

这套系统上线后，AI 助手终于能"记住"用户积累的知识了。用户问"最近有什么关于模型对齐的研究"，AI 搜索本地论文库返回 3 篇匹配论文——而不是去网上搜然后编造答案。

**数据越积越多，AI 越来越懂你。** 这才是"数据复利"的真正含义。

---

*本文基于 [openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge) 项目的实战经验。系统运行于 Mac Mini，接入 Qwen3-235B 大模型，通过 WhatsApp 提供 AI 助手服务。*

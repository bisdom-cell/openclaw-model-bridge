# Ontology + LLM + Agent：企业智能系统的语义控制平面

> **核心命题（v2，经外部专家评审升级）**：Ontology is not the whole solution; it is the semantic control plane of enterprise AI. — 本体论不是企业 AI 的全部，但它应该成为企业 AI 的语义控制平面。

> 术语严格定义见 `ontology_scope.md`。本文中"ontology"按四层使用，不混用。

## 核心论点

**当前企业 AI 的困境**：不是模型不够聪明，而是**四种漂移**在失控。

| 漂移类型 | 含义 | 后果 |
|----------|------|------|
| **词义漂移** | customer/case/approval 在不同系统含义不一致 | LLM 回答"正确但错误"——用对了词但含义不对 |
| **关系漂移** | 跨对象关系链容易错（客户→订单→产品→供应商） | LLM 能说对局部，但全链条不一致 |
| **权限漂移** | Agent 会调用工具，但不知道组织边界 | 越权操作，无法审计 |
| **状态漂移** | 动作做了，但系统状态不一定合法 | 数据不一致，流程断裂 |

**Ontology 的作用不是"让模型更聪明"，而是"让系统更有边界感"。**

**三者形成互补闭环**（传播语：骨架+大脑+四肢；专业语如下）：

```
没有 Ontology 的 LLM+Agent = 强大但失控的智能
没有 LLM 的 Ontology+Agent = 精确但僵化的自动化
没有 Agent 的 Ontology+LLM = 深刻但无法行动的理解
三者互补 = 有边界感的、可审计的、能行动的企业智能系统
```

| 组件 | 传播语 | 专业语 |
|------|--------|--------|
| Ontology | 骨架 | Semantic Control Plane（语义控制平面） |
| LLM | 大脑 | Probabilistic Reasoning + Language Interface |
| Agent | 四肢 | Execution and Orchestration Layer |

## 三角关系详解

### Ontology → LLM：语义锚定

LLM 最大的问题是**不确定性**——同一个问题问两次，可能得到不同答案。本体论通过以下方式锚定 LLM：

1. **领域词表绑定**：不是让 LLM 猜"客户"是什么意思，而是明确定义：客户 = 有过至少一次交易的法人实体，包含属性 {名称, 信用等级, 账期, ...}
2. **关系约束**：客户→订单→产品→供应商 的关系链是确定的，LLM 的回答必须在这个关系网内
3. **推理规则**：如果客户信用等级 < B 且订单金额 > 100K，则需要审批。这不是 LLM "觉得"应该审批，而是规则规定必须审批
4. **事实锚定**：LLM 生成的内容可以用本体进行事实检查——"这个回答中的实体关系是否符合本体定义？"

### Ontology → Agent：ontology-aware policy enforcement + state transition validation

Agent 的工具调用当前是**无约束的**——模型决定调什么工具、传什么参数。本体提供的不只是"能不能调"，而是完整的治理框架：

1. **权限本体**：定义哪些 Agent 角色可以调用哪些工具类别（→ Governance 域）
2. **前置条件**：调用某工具前，必须满足的约束（如：删除操作需要管理员角色 + 数据未被锁定）
3. **状态转移验证**：系统状态是否允许这个操作（状态漂移防护）
4. **后置验证**：工具执行结果是否符合预期状态
5. **副作用追踪**：操作产生了什么副作用，是否越权
6. **失败回滚**：失败后如何恢复到合法状态
7. **证据落盘**：决策 + 规则命中 + 结果写入审计链（→ Layer 4）

**Neuro-Symbolic 耦合点分类**（基于 arXiv 2604.00555）：

| 耦合点 | 时机 | OpenClaw 实例 |
|--------|------|-------------|
| Input-side | 检索/提示前注入本体上下文 | search_kb 注入领域知识 |
| Reasoning-side | 推理中调用约束检查器 | LLM 选择工具时参考 schema 约束 |
| Action-side | 执行前后做前提验证 | proxy_filters 验证参数合法性 |
| Audit-side | 决策后写入审计链 | audit_log 记录工具调用 |

### LLM ↔ Agent：智能执行

这是当前技术最成熟的部分（也是 OpenClaw 已经在做的）：
- LLM 理解自然语言意图 → 转化为工具调用
- Agent 执行工具 → 返回结果 → LLM 解释给用户
- **本体论的加入使这个循环从"尽力而为"升级为"有据可依"**

## 为什么现在是关键时间窗口

### 1. LLM 能力已经"过剩"

2026 年的 LLM 已经能处理大部分文本理解和生成任务。瓶颈不再是"模型够不够智能"，而是"如何让智能在企业中可控地落地"。这正是本体论的价值。

### 2. Agent 生态正在爆发

OpenClaw、LangChain、CrewAI 等框架让 Agent 开发变得容易。但"容易开发"也意味着"容易失控"。没有本体约束的 Agent 是危险的。

### 3. 企业 AI 治理需求的真实驱动力

合规（AI Act、NIST AI RMF）提供外部正当性，但真正驱动预算的是三件事：
1. **降低错误执行成本** — 有审计记录才能定位和复盘
2. **提高跨系统语义一致性** — 有概念本体才能统一"客户"在 CRM、ERP、LLM 中的含义
3. **让智能系统接入真实业务流程** — 有治理边界才敢把 Agent 放进生产环节

### 4. 知识图谱技术成熟

OWL/RDF/SPARQL 生态已经成熟，Protégé 等工具降低了本体工程门槛。缺的不是技术，而是将本体论与 LLM/Agent 结合的实践方法论。

## 与 OpenClaw 的映射（六域参考架构）

| OpenClaw 组件 | 六域映射 | ontology_scope 层级 |
|------|------|------|
| proxy_filters.py 工具白名单 | Capability 域 | Layer 1 (概念) + Layer 2 (约束) |
| tool_ontology.yaml policies | Governance 域 | Layer 2 (策略) |
| adapter.py Provider 路由 | Capability.FallbackPath | Layer 3 (执行语义) |
| audit_log.py 审计日志 | Memory.Evidence | Layer 4 (审计) |
| memory_plane.py 统一接口 | Memory 域 | Layer 1 (概念) + Layer 3 (执行) |
| SOUL.md PA 身份定义 | Actor.PrimaryAgent | Layer 1 (概念) |
| status.json 三方共享状态 | Memory.Fact + Governance | Layer 1 + Layer 4 |

**OpenClaw 本质上已经在做语义控制平面的事情，只是还没有用本体的语言来描述它。** 将隐式的设计决策显式化为分层本体，是从"系统构建者"到"方法论作者"的关键一步。

> 详细六域定义见 `architecture/enterprise_agent_ontology_v0.1.md`

## 行动路径

### 短期（1-2 周）
- 建立本体论知识库（本文档 + 基础理论 + 关键文献）
- 用本体论语言重新描述 OpenClaw 的架构（`cases/openclaw_as_ontology.md`）

### 中期（1-3 个月）
- 追踪 Neuro-Symbolic AI 前沿论文（KB 自动收集）
- 写第一篇立场文章：**"Why Enterprise AI Needs Ontology Before It Needs More Models"**
- 在 OpenClaw 中实验性引入本体约束（如工具调用的前置条件检查）

### 长期（3-12 个月）
- 构建"企业智能体本体"（Enterprise Agent Ontology）参考模型
- 将本体论集成到 Control Plane 的核心设计中
- 发布方法论：如何用 Ontology + LLM + Agent 构建企业智能系统

## 关键参考

- Dietz, J.L.G. — *Enterprise Ontology: Theory and Methodology* (DEMO 方法论)
- Guarino, N. — *Formal Ontology in Information Systems* (形式本体论奠基)
- Marcus, G. & Davis, E. — *Rebooting AI* (Neuro-Symbolic 立场)
- Horrocks, I. — OWL/Description Logic 体系创始人
- Bengio, Y. — 近期 System 2 Deep Learning + 结构化推理方向

---
*初始版本: 2026-04-06 | v2 升级: 2026-04-06（吸收外部专家评审：四层分离+六域参考+四漂移+耦合分类+命题升级）*
*这是一个活文档，随认知深化持续更新*

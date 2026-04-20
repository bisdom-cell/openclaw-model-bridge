# Ontology Scope — 术语宪法

> **在本项目中，"ontology"不是一个词，而是四个层次。混用即失控。**

## 为什么需要这份文档

"Ontology"在 AI/企业架构中被至少四种不同含义混用：

1. 学术界说 ontology = OWL/Description Logic 形式本体
2. 企业架构师说 ontology = 业务概念模型
3. 知识图谱工程师说 ontology = KG 的 schema 层
4. Agent 开发者说 ontology = 运行时策略和约束

**如果这四层不拆开，论述会显得强，但实现时会变得模糊。**

本文档为本项目中出现的每一个"ontology"相关术语划定严格边界。

## 核心命题（升级版）

~~本体论是企业 AI 的核心骨架。~~

**Ontology is not the whole solution; it is the semantic control plane of enterprise AI.**

**本体论不是企业 AI 的全部，但它应该成为企业 AI 的语义控制平面。**

为什么这个表述更好：

1. **更接近系统架构语言** — 我们已经有 Control Plane / Memory Plane / Tool Policy，"semantic control plane"让本体论从"知识资产"提升为"运行时控制结构"
2. **避免被各圈子攻击** — 说"核心骨架"会被问：那 workflow engine 呢？policy engine 呢？vector retrieval 呢？"语义控制平面"允许本体占据精准位置：**它不代替执行系统，但它给执行系统提供语义约束、概念一致性与治理坐标**
3. **更容易落地成产品** — "骨架"是哲学立场，"控制平面"是工程能力

## 四层定义

```
┌─────────────────────────────────────────────────────────────────┐
│                    在本项目中，"ontology"指：                      │
│                                                                 │
│  Layer 4: Evidence & Audit Model（证据与审计模型）                 │
│           谁做了什么决策、基于什么规则、产生什么证据、如何追溯         │
│           ← 企业买单的核心理由不是"更聪明"而是"能审计"               │
│                                                                 │
│  Layer 3: Execution Semantics（执行语义层）                       │
│           Agent 如何把"概念+约束"映射为工具调用、状态迁移、          │
│           异常回滚、审计记录                                       │
│           ← 这是 ontology-aware runtime，不是 ontology 本身       │
│                                                                 │
│  Layer 2: Constraint & Policy Model（约束与策略层）               │
│           什么关系允许、什么前提必须满足、什么状态转移合法             │
│           ← 部分可用 OWL，但很多业务规则超出 OWL 表达舒适区         │
│           ← 需要规则引擎、策略语言、应用层检查协同                   │
│                                                                 │
│  Layer 1: Concept Ontology（概念本体）                            │
│           企业里"有什么"：实体、角色、资源、关系、属性               │
│           ← 这里解决语义锚定，是唯一严格意义的 ontology              │
│                                                                 │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ 以下不属于 ontology ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│                                                                 │
│  Knowledge Graph：实例数据层（存储"实际是什么"），不等于 ontology    │
│  Prompt / Context：消费 ontology 的界面，不是 ontology 本身        │
│  LLM Parameters：统计知识，不可审计，不是 ontology                  │
└─────────────────────────────────────────────────────────────────┘
```

## 各层详解

### Layer 1: Concept Ontology（概念本体）

**定义**：对领域中概念、属性、关系的形式化描述。

**解决的问题**：语义锚定 — "客户"在系统 A、系统 B、LLM 对话中含义一致。

**技术基础**：OWL/RDF（W3C 标准），Description Logic，UFO/BFO 顶层本体。

**边界**：
- ✅ 定义"有什么东西"和"它们之间是什么关系"
- ✅ 支持逻辑一致性检查和隐式知识推导
- ❌ 不定义时序规则（"如果连续 3 次失败则..."）
- ❌ 不定义过程性规则（"先做A再做B"）
- ❌ 不定义计数/聚合逻辑

**在 OpenClaw 中的实例**：
```
Tool 是一个 Concept
  - web_search 是 Tool 的实例
  - web_search 属于 InformationRetrieval 类别
  - web_search 操作 WebPage 资源
  - web_search 没有副作用
```

### Layer 2: Constraint & Policy Model（约束与策略层）

**定义**：基于概念本体的规则系统，定义"什么允许、什么禁止、什么条件下触发什么"。

**解决的问题**：行为边界 — 不是"能不能做"而是"在什么条件下允许做"。

**技术基础**：部分 OWL 公理 + SHACL 约束 + 规则引擎 + 策略语言（YAML/Rego/自定义）。

**关键认知（来自专家评审）**：
> **不要把所有治理都压成 OWL。** OWL 擅长静态逻辑约束（"每个 Booking 必须有且仅有一个 Customer"），但时序性、计数性、过程性、上下文依赖的规则需要规则引擎、策略语言或应用层代码。

**边界**：
- ✅ 静态约束："有副作用的工具需要审计"
- ✅ 前置条件："删除操作需要管理员角色"
- ✅ 状态约束："信用等级 < B 且金额 > 100K → 需要审批"
- ⚠️ 时序规则需要规则引擎辅助
- ⚠️ 计数规则需要应用层辅助

**在 OpenClaw 中的实例**：
```
策略：max_tools = 12
  - 概念层已定义 Tool
  - 策略层定义约束：|Tool instances in request| ≤ 12
  - rationale：Qwen3 >12 工具时选择准确率从 95% 降到 73%
  - 来源：V26 实测证据
```

### Layer 3: Execution Semantics（执行语义层）

**定义**：Agent 如何在运行时消费 Layer 1 + Layer 2，执行工具调用、状态迁移、异常处理。

**解决的问题**：把"概念+约束"变成"可执行的行为"。

**技术基础**：ontology-aware runtime、状态机、工作流引擎。

**关键认知**：
> 这是"ontology-aware runtime"，不是 ontology 本身。本体提供语义，运行时消费语义。

**Neuro-Symbolic 耦合点分类**（来自 arXiv 2604.00555）：

| 耦合点 | 时机 | 示例 |
|--------|------|------|
| **Input-side** | 检索/提示前 | 从概念本体注入领域词表到 prompt |
| **Reasoning-side** | 推理中 | 调用约束检查器验证推理步骤 |
| **Action-side** | 执行前后 | 前置条件验证 + 状态校验 |
| **Audit-side** | 决策后 | 规则命中 + 证据 + 审计链写入 |

**在 OpenClaw 中的实例**：
```
用户说"帮我清洗这个 Excel"
  → Input-side: search_kb 注入领域知识
  → Reasoning-side: LLM 选择 data_clean 工具
  → Action-side: 验证 action ∈ {profile, execute, list_ops}
  → Audit-side: 记录工具调用 + 参数 + 结果到 audit_log
```

### Layer 4: Evidence & Audit Model（证据与审计模型）

**定义**：决策的可追溯性系统 — 谁、何时、基于什么规则、做了什么决定、产生什么结果。

**解决的问题**：可解释性、可审计性、可追责性。

**技术基础**：链式哈希审计日志、NIST AI RMF、EU AI Act 合规框架。

**关键认知（来自专家评审）**：
> 企业真正买单的，往往不是"更聪明"，而是"能解释、能追责、能审计、能复盘"。这一层必须成为独立支柱，不是附在 Agent 后面。

**驱动预算的三件事**（不仅是合规）：
1. **降低错误执行成本** — 有审计记录才能定位和复盘
2. **提高跨系统语义一致性** — 有概念本体才能统一语言
3. **让智能系统接入真实业务流程** — 有治理边界才敢放进去

**在 OpenClaw 中的实例**：
```
audit_log.py — 链式哈希审计日志（V30.2）
  - 每条记录：时间 + 操作者 + 动作 + 参数 + 结果 + SHA256 链
  - 篡改/删除可检测
  - 但目前只记"做了什么"，不记"基于什么规则"
  → 升级方向：每条审计记录关联到 Layer 2 的具体策略规则
```

## 企业 AI 四大失控源（框架）

不是"模型不够聪明"，而是四种漂移：

| 漂移类型 | 含义 | 本体如何解决 | 解决层级 |
|----------|------|-------------|---------|
| **词义漂移** | customer/case/approval 在不同系统含义不一致 | 概念本体提供唯一定义 | Layer 1 |
| **关系漂移** | LLM 能说对局部，但跨对象关系链容易错 | 关系约束限定合法关系 | Layer 1 + 2 |
| **权限漂移** | Agent 会调用工具，但不知道组织边界 | 策略层定义角色-权限映射 | Layer 2 |
| **状态漂移** | 动作做了，但系统状态不一定合法 | 执行语义层做状态转移验证 | Layer 3 |

**本体的作用不是"让模型更聪明"，而是"让系统更有边界感"。**

## 与 tool_ontology.yaml 的映射

当前 `tool_ontology.yaml` 的内容需要按四层重新归类：

| 当前内容 | 正确归属 | 备注 |
|----------|---------|------|
| concepts（Tool/Party/Resource） | Layer 1: Concept Ontology | ✅ 正确 |
| tools.builtin（白名单+schema） | Layer 1 + Layer 2 混合 | ⚠️ 需要分离 |
| tools.custom（自定义工具） | Layer 1（定义）+ Layer 3（执行） | ⚠️ 需要分离 |
| policies.tool_admission | Layer 2: Constraint & Policy | ✅ 正确 |
| policies.parameter_healing | Layer 3: Execution Semantics | ⚠️ 这是运行时行为，不是约束 |
| policies.routing | Layer 3: Execution Semantics | ⚠️ 这是运行时行为，不是约束 |
| policies.context_management | Layer 3: Execution Semantics | ⚠️ 这是运行时行为，不是约束 |
| policies.request_limits | Layer 2: Constraint & Policy | ✅ 正确 |
| aliases | Layer 3: Execution Semantics | 这是运行时修复，不是概念或约束 |
| （缺失） | Layer 4: Evidence & Audit | ❌ 完全缺失 |

**结论**：当前 YAML 把四层混在一起了。下一步重构需要按层分文件或分 section。

## 术语规范

在本项目所有文档中：

| 术语 | 含义 | 不要混用为 |
|------|------|-----------|
| **概念本体** (Concept Ontology) | Layer 1：实体-关系-属性的形式定义 | 不等于"所有规则" |
| **策略模型** (Policy Model) | Layer 2：约束、前后置条件、权限规则 | 不等于"OWL 公理"（部分超出 OWL 表达力） |
| **执行语义** (Execution Semantics) | Layer 3：运行时消费本体的行为逻辑 | 不等于"ontology"（它是 ontology-aware runtime） |
| **审计模型** (Audit Model) | Layer 4：决策+证据+追溯的完整链条 | 不等于"日志"（日志记事实，审计记规则命中） |
| **知识图谱** (Knowledge Graph) | 概念本体的实例数据层 | 不等于 ontology（KG 是实例，ontology 是 schema） |
| **语义控制平面** (Semantic Control Plane) | Layer 1-4 的整体 | 不等于"本体论"（它比本体论更大，包含运行时和审计） |

## 措辞规范

| 场景 | 推荐用 | 不要用 |
|------|--------|--------|
| 对外传播 | "LLM 是没有骨架的大脑" | （保留，传播力强） |
| 专业文档 | Ontology = semantic control plane | Ontology = 核心骨架 |
| 专业文档 | "三者形成互补闭环" | "缺一不可""唯一" |
| 专业文档 | LLM = probabilistic reasoning + language interface | LLM = 大脑 |
| 专业文档 | Agent = execution and orchestration layer | Agent = 四肢 |
| 论证时 | "降低错误执行成本 + 提高语义一致性 + 接入真实流程" | "合规需求激增" |

---
*创建: 2026-04-06 | 本文档在项目中具有宪法级效力，与 CONSTITUTION.md 并列*
*来源: 外部专家评审反馈 + W3C OWL 标准 + NIST AI RMF + EU AI Act + arXiv 2604.00555*

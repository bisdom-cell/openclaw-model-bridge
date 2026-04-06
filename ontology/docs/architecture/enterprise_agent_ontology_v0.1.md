# Enterprise Agent Ontology — 参考模型 v0.1

> 不是比喻，而是可实现的六域参考架构。

## 设计原则

1. **分域不分层** — 六个对象域平行存在，通过关系连接
2. **从 OpenClaw 实践中来** — 每个概念都有已运行系统的实例映射
3. **可渐进实现** — 先做 1-2 个域的 POC，不需要一次全建
4. **OWL-compatible 但不 OWL-only** — 概念定义可用 OWL，策略规则用最合适的工具

## 六个对象域

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  ┌─────────────┐    canInvoke    ┌─────────────────┐                 │
│  │   Actor      │───────────────→│   Capability    │                 │
│  │   施事者域    │                │   能力域         │                 │
│  └──────┬──────┘                └────────┬────────┘                 │
│         │                                │                           │
│         │ initiates                      │ implements                │
│         ▼                                ▼                           │
│  ┌─────────────┐    requires     ┌─────────────────┐                │
│  │   Task       │───────────────→│   Governance    │                 │
│  │   任务域      │                │   治理域         │                 │
│  └──────┬──────┘                └────────┬────────┘                 │
│         │                                │                           │
│         │ produces                       │ enforces                  │
│         ▼                                ▼                           │
│  ┌─────────────┐    records      ┌─────────────────┐                │
│  │   Memory     │←──────────────│   Execution     │                 │
│  │   记忆域      │                │   执行域         │                 │
│  └─────────────┘                └─────────────────┘                 │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

## Domain 1: Actor（施事者域）

**定义**：系统中能发起行为的实体。

```
Actor（施事者）
├── HumanActor（人类施事者）
│   ├── User（终端用户）— WhatsApp/Discord 用户
│   ├── Admin（管理员）— 系统管理者
│   └── Reviewer（审核者）— 审批流中的审核人
│
├── AgentActor（智能体施事者）
│   ├── PrimaryAgent（主 Agent）— OpenClaw PA
│   ├── SubAgent（子 Agent）— sessions_spawn 创建的
│   └── CronAgent（定时 Agent）— cron 触发的任务执行者
│
└── SystemActor（系统施事者）
    ├── Proxy（工具代理）— Tool Proxy
    ├── Gateway（网关）— OpenClaw Gateway
    └── Provider（模型提供者）— Qwen/GPT-4o/Gemini/...
```

**关键属性**：
- `role`: 角色（决定权限）
- `trustLevel`: 信任等级（决定可操作范围）
- `sessionContext`: 当前会话上下文

**OpenClaw 映射**：
| 概念 | 实例 |
|------|------|
| User | WhatsApp 用户 bisdom |
| PrimaryAgent | PA (Wei) |
| SubAgent | ops agent |
| Proxy | tool_proxy.py |
| Provider | Qwen3-235B, Gemini 2.5 Flash |

## Domain 2: Capability（能力域）

**定义**：系统能做什么 — 工具、技能、模型能力、降级路径。

```
Capability（能力）
├── Tool（工具）
│   ├── BuiltinTool — Gateway 原生工具
│   │   ├── InformationTool（信息获取）— web_search, web_fetch, read
│   │   ├── MutationTool（变更操作）— write, edit, exec
│   │   ├── CommunicationTool（通信）— message, tts
│   │   └── ManagementTool（管理）— cron, sessions_spawn
│   ├── CustomTool — Proxy 自定义工具
│   │   ├── data_clean
│   │   └── search_kb
│   └── BrowserTool — browser_* 系列
│
├── ModelCapability（模型能力）
│   ├── TextGeneration — 文本生成
│   ├── ToolCalling — 工具调用
│   ├── VisionUnderstanding — 图片理解（VL 模型）
│   └── Embedding — 向量化
│
├── FallbackPath（降级路径）
│   ├── primaryProvider → fallbackProvider
│   └── circuitBreaker 配置
│
└── PolicyBinding（策略绑定）
    └── 每个 Capability 绑定一组 Governance 规则
```

**关键属性**：
- `sideEffects: bool` — 是否有副作用（核心语义属性）
- `resourceType`: 操作的资源类型
- `category`: 能力类别
- `executor`: 执行方式（Gateway / Proxy / 外部）

**关键关系**：
- `Tool operatesOn Resource`
- `Tool belongsTo Category`
- `FallbackPath substitutes Capability`
- `ModelCapability supports Tool`（如 VisionUnderstanding supports image）

## Domain 3: Task（任务域）

**定义**：用户意图到系统行为的结构化分解。

```
Task（任务）
├── Goal（目标）— 用户想达成什么
│   └── "帮我清洗这个 Excel 文件"
│
├── Plan（计划）— 达成目标的步骤序列
│   ├── Step 1: profile（数据画像）
│   ├── Step 2: execute（执行清洗）
│   └── Step 3: validate（验证结果）
│
├── Precondition（前置条件）— 执行前必须满足
│   ├── "文件存在且格式支持"
│   ├── "用户有文件读写权限"
│   └── "请求体 ≤ 200KB"
│
├── Postcondition（后置条件）— 执行后应达到的状态
│   ├── "清洗后文件已生成"
│   └── "审计记录已写入"
│
└── Status（状态）
    ├── pending → executing → completed
    ├── pending → executing → failed → retrying
    └── pending → blocked（前置条件不满足）
```

**关键关系**：
- `Task decomposesInto Step`
- `Step requires Precondition`
- `Step produces Postcondition`
- `Task transitionsTo Status`
- `Goal motivates Task`

## Domain 4: Memory（记忆域）

**定义**：系统中持久化的知识、事实、偏好、证据。

```
Memory（记忆）
├── Fact（事实）— 客观知识
│   ├── KBNote — ~/.kb/notes/ 中的笔记
│   ├── SourceArchive — ~/.kb/sources/ 中的归档
│   └── StructuredData — status.json 中的结构化数据
│
├── Episode（经历）— 事件记录
│   ├── ConversationHistory — 对话历史
│   ├── IncidentSnapshot — 故障快照
│   └── DreamInsight — Dream 引擎发现的洞察
│
├── Preference（偏好）— 用户特征
│   ├── CommunicationStyle — 简洁/详细
│   ├── ActiveHours — 活跃时段
│   └── InterestDomains — 关注领域
│
├── Artifact（制品）— 媒体/文件
│   ├── Image — 图片（MM 索引）
│   ├── Document — 文档
│   └── AudioVideo — 音视频
│
└── Evidence（证据）— 决策依据 ← Layer 4 的核心
    ├── AuditRecord — 审计日志条目
    ├── PolicyHit — 规则命中记录
    └── BenchmarkResult — 评测结果
```

**关键关系**：
- `Memory stores Fact`
- `Evidence justifies Decision`
- `Preference shapes AgentBehavior`
- `Episode contextualizesIn Session`

**OpenClaw 映射**：
| 概念 | 现有实现 | ontology_scope 层级 |
|------|---------|-------------------|
| KBNote | ~/.kb/notes/ | Layer 1 实例 |
| Preference | status.json.preferences | Layer 1 实例 |
| AuditRecord | audit_log.py | Layer 4 |
| IncidentSnapshot | incident_snapshot.py | Layer 4 |

## Domain 5: Governance（治理域）

**定义**：控制谁能做什么、在什么条件下、需要什么审批。

```
Governance（治理）
├── Role（角色）
│   ├── userRole — 终端用户权限
│   ├── agentRole — Agent 权限（工具调用范围）
│   ├── adminRole — 管理员权限
│   └── cronRole — 定时任务权限（受限）
│
├── Permission（权限）
│   ├── canInvoke(Tool) — 可以调用哪些工具
│   ├── canAccess(Resource) — 可以访问哪些资源
│   └── canModify(State) — 可以修改哪些状态
│
├── Constraint（约束）— 来自 ontology_scope Layer 2
│   ├── StaticConstraint — OWL 可表达的
│   │   ├── "每个 Booking 必须有一个 Customer"
│   │   └── "TEU 容量 > 0"
│   ├── PolicyConstraint — 需要策略引擎的
│   │   ├── "max_tools ≤ 12"
│   │   ├── "max_request_bytes ≤ 200KB"
│   │   └── "side_effects=true 的工具需要审计"
│   └── TemporalConstraint — 需要规则引擎的
│       ├── "连续 3 次错误触发告警"
│       └── "凌晨 2-6 点禁止有副作用的操作"
│
├── ApprovalFlow（审批流）
│   ├── autoApprove — 自动批准（信任等级足够）
│   ├── humanApprove — 需要人工审批
│   └── denyWithReason — 拒绝并说明原因
│
└── Exception（异常处理）
    ├── fallback — 降级到替代能力
    ├── retry — 重试（含退避策略）
    ├── escalate — 升级到人工
    └── abort — 终止并记录
```

**关键关系**：
- `Role authorizes Action`
- `Constraint governs Capability`
- `ApprovalFlow appliesTo Task`
- `Exception handlesFailureOf Execution`

**关键区分**（来自专家评审）：

| 约束类型 | OWL 能表达？ | 实现方式 |
|----------|------------|---------|
| 类型约束（X 是 Y 的子类） | ✅ 完全 | OWL class hierarchy |
| 基数约束（最多 12 个工具） | ✅ 部分 | OWL cardinality + 应用层 |
| 时序约束（连续 N 次失败） | ❌ 不能 | 规则引擎 / 应用代码 |
| 过程约束（先 A 再 B） | ❌ 不能 | 状态机 / 工作流引擎 |
| 聚合约束（总量 > 阈值） | ❌ 不能 | SQL / 应用代码 |

## Domain 6: Execution（执行域）

**定义**：工具调用的全生命周期 — 从发起到结果到审计。

```
Execution（执行）
├── Invocation（调用）
│   ├── tool: Tool — 调用的工具
│   ├── actor: Actor — 发起者
│   ├── arguments: Map — 参数
│   ├── timestamp: DateTime — 时间
│   └── preconditionsMet: bool — 前置条件是否满足
│
├── Result（结果）
│   ├── success: bool
│   ├── output: Any — 返回值
│   ├── sideEffects: List — 产生的副作用
│   ├── latencyMs: int — 耗时
│   └── tokensUsed: int — token 消耗
│
├── SideEffect（副作用）
│   ├── FileCreated / FileModified / FileDeleted
│   ├── MessageSent
│   ├── StateChanged
│   └── ExternalAPICall
│
├── Retry（重试）
│   ├── attempt: int — 第几次
│   ├── backoffMs: int — 退避时间
│   └── reason: String — 重试原因
│
└── Rollback（回滚）
    ├── trigger: Exception — 触发回滚的异常
    ├── undoActions: List — 回滚操作
    └── finalState: Status — 回滚后状态
```

**关键关系**：
- `Execution produces Evidence`（→ Domain 4 Memory）
- `Execution governed by Constraint`（← Domain 5 Governance）
- `Execution implements Step`（← Domain 3 Task）
- `Execution invokes Capability`（← Domain 2 Capability）

## 跨域核心关系总表

```
Actor ──canInvoke──→ Capability
Actor ──initiates──→ Task
Actor ──hasRole────→ Role

Capability ──operatesOn──→ Resource
Capability ──hasSideEffect──→ bool
Capability ──belongsTo──→ Category
Capability ──boundBy──→ Constraint

Task ──decomposesInto──→ Step
Task ──requires──→ Precondition
Task ──produces──→ Postcondition
Task ──transitionsTo──→ Status

Memory ──stores──→ Fact | Episode | Preference | Artifact | Evidence
Evidence ──justifies──→ Decision
Evidence ──references──→ Constraint

Governance ──authorizes──→ Action
Governance ──governs──→ Capability
Governance ──defines──→ Constraint | Role | Permission

Execution ──invokes──→ Capability
Execution ──produces──→ Evidence
Execution ──governedBy──→ Constraint
Execution ──implements──→ Step
```

## 与 OpenClaw 现有系统的映射

| 六域概念 | OpenClaw 现有实现 | 成熟度 |
|----------|-----------------|--------|
| Actor.User | WhatsApp/Discord 用户 | ✅ 生产运行 |
| Actor.PrimaryAgent | PA (SOUL.md 定义) | ✅ 生产运行 |
| Actor.Provider | providers.py (7 个) | ✅ 生产运行 |
| Capability.Tool | proxy_filters.py (16+2) | ✅ 生产运行 |
| Capability.FallbackPath | config.yaml fallback matrix | ✅ 生产运行 |
| Task.Plan | LLM 隐式推理 | ⚠️ 隐式，未显式化 |
| Task.Precondition | proxy_filters 中的检查 | ⚠️ 散落在代码中 |
| Memory.Fact | ~/.kb/ + search_kb | ✅ 生产运行 |
| Memory.Preference | preference_learner.py | ✅ 生产运行 |
| Memory.Evidence | audit_log.py | ✅ 基础版 |
| Governance.Constraint | tool_ontology.yaml policies | ✅ 声明式 |
| Governance.Role | 隐式（所有用户同权限） | ❌ 缺失 |
| Execution.Invocation | tool_proxy.py 拦截执行 | ✅ 生产运行 |
| Execution.Rollback | 基本无 | ❌ 缺失 |

## 实施优先级

**不需要一次全建。** 按"高约束、高风险、强流程"优先：

| 优先级 | 域 | 理由 |
|--------|------|------|
| P0 | Capability + Governance | 已有 tool_ontology.yaml 基础，最容易从声明升级为语义查询 |
| P1 | Execution + Memory.Evidence | 审计升级：从"记事实"到"记规则命中"，直接提升治理价值 |
| P2 | Actor + Task | 角色权限分离 + 任务状态机，需要更多设计 |

---
*版本: v0.1 | 创建: 2026-04-06*
*来源: 外部专家评审（六域建议）+ OpenClaw 生产系统映射*
*下一步: 选 Capability + Governance 做语义查询 POC*

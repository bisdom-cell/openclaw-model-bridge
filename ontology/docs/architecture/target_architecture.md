# Ontology 子项目终态架构：从 Shadow 到 Semantic Control Plane

> 版本：v1.0 | 2026-04-09
> 状态：目标架构文档（指导未来 3-6 个月实现方向）
> 前置阅读：`ontology_scope.md`（术语宪法）、`CONSTITUTION.md`（工作原则）

---

## 一、现状定位

当前处于**方法论验证阶段**（Phase 2 Shadow Mode）：

- Tool Ontology Engine：81 条声明式规则，与硬编码 100% 等价（Phase 1 证明完成）
- Governance Ontology：17 不变式 + 6 元规则 + 35 可执行检查，每日 07:00 自动审计
- Shadow 模式：引擎加载并比对，硬编码做决策，引擎只观察
- `classify_tool_call()`：运行时语义分类（risk_level + policy_tags），仅打日志

```
当前数据流：
  hardcoded proxy_filters ──决策──→ 工具过滤 / 参数修复 / 策略执行
  ontology engine ──────────观察──→ 比对 + 记录 + 分类（不参与决策）
```

**核心差距**：ontology 的推理能力已验证，但未接入决策路径。所有策略仍以 if/else 枚举形式硬编码在 Python 中。

---

## 二、终态全景

终态中 ontology 不再是旁路观察者，而是系统的**语义决策中枢**——所有策略决策从声明式规则推理而来，硬编码归零。

```
┌─────────────────────────────────────────────────────────────────┐
│              Ontology Semantic Control Plane                      │
│                                                                  │
│  Layer 1: Concept Ontology（概念本体）                            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐              │
│  │  Actor   │ │  Tool   │ │Resource │ │  Task    │              │
│  │  施事者  │ │  工具   │ │  资源   │ │  任务    │              │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘              │
│       │ canInvoke │ operatesOn│  requires │                     │
│       └──────────┴───────────┴───────────┘                     │
│  属性驱动：category, side_effects, risk_level, resource_type     │
│  关系推理：Tool operatesOn Resource → 自动继承 Resource 约束     │
│                              ↓ 推理                              │
│  Layer 2: Policy Engine（策略推理引擎）                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │ 静态策略      │ │ 时序策略      │ │ 路由策略      │            │
│  │ OWL 可表达   │ │ 状态机       │ │ 条件推理      │            │
│  │ tool≤12      │ │ 断路器       │ │ 多模态路由    │            │
│  │ 夜间阻止     │ │ 静默期       │ │ 降级链       │            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│  核心 API: evaluate_policy() / infer_targets() / explain()      │
│                              ↓ 执行                              │
│  Layer 3: Execution Semantics（执行语义层）                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐        │
│  │ Pre-check   │→ │ Runtime Gate │→ │ Post-verify     │        │
│  │ 前置条件    │  │ 过滤/修复/路由│  │ 后置验证        │        │
│  └─────────────┘  └──────────────┘  └─────────────────┘        │
│                              ↓ 记录                              │
│  Layer 4: Evidence & Audit（证据与审计层）                        │
│  每个决策 = { 时间, 角色, 工具, 策略链, 推理路径, 结果, hash }    │
│  chain-hash 审计 + 治理不变式 + 效果层验证                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、各层技术细节

### Layer 1: 概念本体 — 从工具列表到领域模型

**当前**：`tool_ontology.yaml` 只定义 Tool 实体和少量属性（category, side_effects）。

**终态**：完整六域概念模型，每个概念有属性、关系、约束。

```yaml
# domain_ontology.yaml — 统一领域本体（终态）
concepts:
  Actor:
    subtypes: [HumanActor, AgentActor, SystemActor, ProviderActor]
    properties:
      - name: role
        type: enum[user, admin, pa, ops_agent, sub_agent]
      - name: permissions
        type: Set[Permission]
    relations:
      - canInvoke: Tool
      - owns: Resource
      - delegates_to: AgentActor

  Tool:
    subtypes: [BuiltinTool, CustomTool, BrowserTool, PluginTool]
    properties:
      - name: category
        type: enum[file_operation, web_access, memory, communication, ...]
      - name: side_effects
        type: boolean
      - name: risk_level
        derived_from: "side_effects ∧ category → risk_level"  # 推理，非硬编码
      - name: resource_type
        type: Resource
      - name: requires_api_key
        type: boolean
    relations:
      - operatesOn: Resource
      - requires: Precondition
      - produces: Postcondition

  Resource:
    subtypes: [WebPage, File, MemoryEntry, KBNote, MediaAsset]
    properties:
      - name: sensitivity
        type: enum[public, internal, confidential]
      - name: max_size_bytes
        type: integer
    # 关键：Tool 通过 operatesOn 继承 Resource 约束
    # write operatesOn File, File.sensitivity=confidential → 自动触发 approval_required

  Provider:
    subtypes: [QwenProvider, GPT4oProvider, GeminiProvider, ...]
    properties:
      - name: capabilities
        type: Set[Capability]  # text, vision, tool_calling, streaming
      - name: status
        type: enum[active, degraded, down]
      - name: fallback_chain
        derived_from: "capabilities overlap → fallback ordering"
```

**关键进步**：从单实体属性推理（当前 `classify_tool_call`）到**概念间关系推理**——`write` operatesOn `File`，`File.sensitivity=confidential`，因此 `write` 自动继承 `approval_required`，无需硬编码。

### Layer 2: 策略引擎 — 从枚举到推理

**当前**：`proxy_filters.py` 硬编码（夜间阻止列表、工具白名单），`infer_policy_targets()` POC 未接入。

**终态**：所有策略声明式定义，引擎运行时推理执行。

```yaml
# policy_ontology.yaml — 策略声明（终态）
policies:
  # === 静态策略（OWL 可表达）===
  tool_admission:
    rule: "∀ tool ∈ request.tools: tool ∈ AllowedTools"
    enforcement: filter_tools()
    rationale: "V26 实验：Qwen3 超过 12 工具准确率 95%→73%"

  night_blocking:
    rule: "time ∈ [00:00, 07:00) ∧ tool.side_effects = true → BLOCK"
    targets: infer("side_effects == true")  # 语义推理，非枚举
    enforcement: evaluate_policy()
    exception: "kb_dream.sh 豁免（03:00，00:00-06:00 算力专属窗口）"

  # === 时序策略（状态机）===
  circuit_breaker:
    trigger: "consecutive_failures(provider) >= 3"
    action: "status(provider) ← degraded; route_to(fallback_chain)"
    recovery: "after 5min success → status ← active"
    state_machine:
      states: [active, degraded, down]
      transitions:
        - {from: active, to: degraded, on: "3_consecutive_failures"}
        - {from: degraded, to: active, on: "1_success_after_5min"}
        - {from: degraded, to: down, on: "10_consecutive_failures"}

  # === 路由策略（条件推理）===
  multimodal_routing:
    rule: "request.has_image = true → route(provider WHERE vision ∈ capabilities)"
    current: "hardcoded → Qwen2.5-VL"
    target: "capability-based → find_by_capability('vision')"

  capability_fallback:
    rule: "provider.status = down → build_fallback_chain(required_capabilities)"
    current: "配置文件定义降级链"
    target: "从 Provider.capabilities 自动推导"
```

**核心 API**：

```python
def evaluate_policy(tool_call, context):
    """终态：每个工具调用经策略引擎评估"""
    tool = ontology.get_concept(tool_call.name)
    policies = ontology.find_applicable_policies(tool, context)

    decisions = []
    for policy in policies:
        result = policy.evaluate(tool, context)
        decisions.append(Decision(
            policy=policy.id,
            result=result,              # Allow | Block | Route
            rule_chain=policy.trace(),  # 完整推理路径
            rationale=policy.rationale
        ))

    final = resolve_conflicts(decisions)  # 最严格策略胜出
    audit_log.record(tool_call, decisions, final)
    return final
```

### Layer 3: 执行语义 — 前置/运行/后置三阶段门控

**当前**：`proxy_filters.py` 做过滤和修复，无前置条件和后置验证。

**终态**：每个工具调用经过完整三阶段门控。

```
请求进入
  │
  ├─ Stage 1: Pre-check（前置条件）
  │   ontology.get_preconditions(tool) → 权限/配额/上下文检查
  │
  ├─ Stage 2: Runtime Gate（运行时策略）
  │   ├─ 工具过滤: ontology.query_tools(actor, context)  # 非 ALLOWED_TOOLS 枚举
  │   ├─ 参数修复: ontology.heal_args(tool_call)          # 非硬编码 alias
  │   ├─ 模型路由: ontology.route(request)                # 非硬编码 provider
  │   └─ 策略评估: policy_engine.evaluate(request)        # 统一评估
  │
  ├─ 执行（LLM / Provider）
  │
  └─ Stage 3: Post-verify（后置验证）
      ontology.get_postconditions(tool) → 结果合规性检查
      audit_log.record(request, response, decision)  # 带规则链
```

**四个 Neuro-Symbolic 耦合点**（参考 `neuro_symbolic.md`）：

| 耦合点 | 位置 | 作用 |
|--------|------|------|
| Input-side | Pre-check | 注入领域词汇到 prompt/RAG |
| Reasoning-side | Runtime Gate | 推理中调用约束检查器 |
| Action-side | Runtime Gate | 执行前验证前置条件 |
| Audit-side | Post-verify | 记录规则命中 + 证据 |

### Layer 4: 证据审计 — 从"发生了什么"到"基于什么规则"

**当前**：`audit_log.py` 记录操作（who/when/what），不记录规则依据。

**终态**：每条审计记录包含完整决策推理链。

```python
# 当前审计记录
{"time": "...", "actor": "pa", "action": "tool_call", "tool": "write", "result": "blocked"}

# 终态审计记录
{
    "time": "2026-04-09T03:15:00",
    "actor": {"type": "AgentActor", "id": "pa", "role": "primary_agent"},
    "action": "tool_call",
    "tool": {"name": "write", "category": "file_operation", "side_effects": true},
    "context": {"time_slot": "quiet_hours", "token_usage": 0.72},
    "policy_evaluation": [{
        "policy": "night_blocking",
        "rule": "time ∈ [00:00,07:00) ∧ side_effects=true → BLOCK",
        "rule_chain": [
            "write.side_effects = true  (tool_ontology.yaml)",
            "current_time = 03:15       (runtime)",
            "03:15 ∈ [00:00, 07:00)     (temporal match)",
            "→ BLOCK                    (policy match)"
        ],
        "rationale": "凌晨静默期：防止 cron 意外触发文件写入"
    }],
    "final_decision": "Block",
    "evidence_hash": "sha256:a3f2..."
}
```

---

## 四、治理体系终态

```
Governance Ontology v_final
├── 不变式 30+（从当前 17 扩展）
│   ├── Tool 域: 工具数 / Schema / 权限 / 前置条件
│   ├── Cron 域: 调度 / bash-lc / 重复 / 静默期
│   ├── Notify 域: 双通道 / 重试 / 错误捕获
│   ├── Env 域: 环境变量 / API Key / 占位号
│   ├── Health 域: 服务状态 / 刷新 / 内容验证
│   ├── Deploy 域: 文件映射 / crontab 隔离 / 漂移检测
│   ├── [新] Policy 域: 策略一致性 / 推理正确性
│   ├── [新] Memory 域: KB 完整性 / 索引覆盖 / 去重
│   └── [新] Provider 域: 能力声明 / 降级链 / SLO
│
├── 元规则 10+（从当前 6 扩展）
│   ├── MR-1~6: 现有（声明=执行, 传播, 无静默失败, ...）
│   ├── [新] MR-7: 新策略必须有 shadow 观察期
│   ├── [新] MR-8: 概念变更必须触发影响分析
│   └── [新] MR-9: 效果层覆盖率 ≥ 60%
│
└── 验证深度三层全覆盖
    ├── L1 Declaration: 所有 critical 不变式 ✓
    ├── L2 Runtime: 所有 critical 不变式 ✓
    └── L3 Effect: ≥ 60% 不变式 ✓
```

---

## 五、关键架构转变对照

| 维度 | 当前（Shadow） | 终态（Semantic Control Plane） |
|------|---------------|-------------------------------|
| 数据源 | 硬编码 `proxy_filters.py` + YAML 旁路 | YAML 唯一事实源，Python 只做引擎 |
| 策略执行 | if/else 枚举 | `evaluate_policy()` 推理 |
| 工具发现 | `ALLOWED_TOOLS` 固定集合 | `query_tools(capabilities=...)` 语义查询 |
| 夜间阻止 | 手动维护阻止列表 | `infer("side_effects==true")` 自动覆盖新工具 |
| 降级路由 | 配置文件定义链 | Provider capabilities 自动推导 |
| 参数修复 | 硬编码 alias 映射 | ontology alias 属性推理 |
| 新工具接入 | 改 3 个 Python 文件 | 加 1 段 YAML + 合约验证 |
| 审计内容 | "做了什么" | "做了什么 + 基于什么规则 + 推理路径" |
| 影响分析 | 人工评估 | `ontology.impact_analysis("修改 max_tools")` |
| 冲突检测 | 无 | 策略间矛盾自动发现 |

---

## 六、迁移路径

```
Phase 2 (当前)        Phase 3              Phase 4              Phase 5
Shadow 观察          渐进替换             完全推理              对外输出
─────────────────→──────────────────→──────────────────→──────────────
shadow mode          ONTOLOGY_MODE=on     策略引擎接管          Plugin SDK
classify 观察        filter_tools 用引擎  evaluate_policy()    tool_policy.yaml
drift 日志           alias 用引擎         pre/post check       memory_policy.yaml
治理审计每日          night block 用推理   审计带规则链          Extension Guide
                    新工具只加 YAML       影响分析              pip install
```

### Phase 3: 渐进替换（近期目标）

1. **ONTOLOGY_MODE=on** — 引擎数据替代硬编码（等价已证明，风险为零）
2. **filter_tools()** 内部改用 `ontology.query_tools()`
3. **fix_tool_args()** 改用 `ontology.resolve_alias()`
4. **夜间阻止** 从枚举改为 `infer("side_effects==true")`
5. **新增 Tool** 只需 YAML 声明（推广 V37 Provider Plugin 模式到 Tool）

### Phase 4: 完全推理（中期目标）

1. **策略引擎** 完整实现（静态 + 时序 + 路由三类策略统一评估）
2. **前置/后置条件** 门控接入请求管线
3. **审计记录** 带完整规则推理链
4. **策略冲突** 自动检测
5. **影响分析** 工具：概念变更 → 受影响策略/工具列表

### Phase 5: 对外输出（长期目标）

1. **Tool Policy Plugin**：`tool_policy.yaml` 声明式工具策略扩展
2. **Memory Policy Plugin**：`memory_policy.yaml` 记忆平面策略扩展
3. **Extension Guide**：第三方基于 ontology 框架扩展的指南
4. **可发布的 ontology 引擎**：可独立 pip install 的治理组件

---

## 七、核心洞察

终态的本质不是"用 YAML 替代 Python"——那只是表面。真正的转变是：

> **从"开发者知道规则并写成代码"变成"系统知道规则并自主推理"。**

当前系统的每条策略都活在开发者脑中，以 if/else 固化在代码中。新开发者不知道"write 为什么夜间被阻止"，他得读代码、注释、CLAUDE.md，在脑中重建规则链。

终态：系统自己知道"write 有 side_effects，side_effects 在夜间被策略阻止，rationale 是防止 cron 意外写入"。开发者问系统而非猜代码。审计问系统而非查日志。新工具接入时系统自动推理应受哪些策略约束，而非等人更新 5 个硬编码列表。

**把隐性知识变成可查询、可推理、可审计的显性结构** — 这就是 ontology 的终极价值。

---

## 附录：与现有文档关系

| 文档 | 关系 |
|------|------|
| `ontology_scope.md` | 术语宪法，本文严格遵守四层定义 |
| `CONSTITUTION.md` | 工作原则，迁移过程中持续适用 |
| `ontology_llm_agent.md` | 三角架构理论基础 |
| `enterprise_agent_ontology_v0.1.md` | 六域参考模型，本文 Layer 1 具体化 |
| `industrial_ai_paradigm.md` | 三平面方法论，本文是其 ontology 层的技术实现 |
| `neuro_symbolic.md` | 六种集成范式，Layer 3 的理论依据 |
| `openclaw_as_ontology.md` | 差距分析，本文是其目标答案 |

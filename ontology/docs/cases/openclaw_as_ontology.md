# 用本体论视角重新审视 OpenClaw

> OpenClaw 已经在做本体论的事情，只是还没有用本体论的语言来描述。

## OpenClaw 的隐式本体

### Control Plane = 运行时本体引擎

```
OpenClaw 概念                    本体论对应
─────────────────────────────   ──────────────────────
Provider（提供者）               Class: LLMProvider
  ├── capabilities              Capability Ontology（能力声明）
  ├── auth_style                Authentication Axiom（认证约束）
  └── model_list                Instance Enumeration（实例枚举）

Tool（工具）                     Class: AgentTool
  ├── allowed / blocked         Access Control Axiom（权限公理）
  ├── schema                    Interface Ontology（接口本体）
  └── max_calls_per_task        Behavioral Constraint（行为约束）

Fallback Chain（降级链）         Alternative Path Axiom（替代路径公理）
  qwen3 → gemini → gpt4o       if Provider.status = unavailable
                                then use Provider.fallback

Memory Plane（记忆平面）         Knowledge Base Instance Store
  ├── KB 语义层                  Domain Knowledge Instances
  ├── 多媒体层                   Multimedia Object Instances
  ├── 偏好层                     User Preference Instances
  └── 状态层                     System State Instances
```

### 已有的本体约束（隐式）

OpenClaw 中已经存在但没有被形式化为本体的规则：

| 现有规则 | 本体化表述 |
|---------|----------|
| 工具数量 ≤ 12 | `∀ TaskSession: count(assignedTools) ≤ 12` |
| 每任务工具调用 ≤ 2 | `∀ ToolCall in Task: sequence_number ≤ 2` |
| 请求体 ≤ 200KB | `∀ Request: body.size ≤ 200KB` |
| 图片请求 → VL 模型 | `∀ Request: hasMultimodal = true → routeTo(VLProvider)` |
| 信用等级 < B → 需审批 | `∀ Booking: customer.creditGrade < B ∧ value > 100K → requiresApproval = true` |

### 缺失的本体化

当前系统中**隐式存在但没有形式化**的知识：

1. **工具语义**：`memory_search` 和 `search_kb` 有什么区别？它们的输入/输出语义是什么？什么情况下用哪个？→ 需要 Tool Semantic Ontology
2. **用户意图分类**：用户发的消息是查询？指令？反馈？闲聊？→ 需要 Intent Ontology
3. **领域知识**：货代中"订舱"、"提单"、"清关"之间的关系是什么？→ 需要 Domain Ontology
4. **质量标准**：什么算"好的回答"？→ 需要 Quality Ontology

## 从隐式到显式：价值在哪里

### Before（当前）：规则散落在代码中
```python
# proxy_filters.py
if len(tools) > 12:
    tools = tools[:12]  # 为什么是 12？谁定的？什么时候改？

# adapter.py
if has_multimodal:
    model = VL_MODEL_ID  # 为什么图片必须走 VL？有例外吗？
```

### After（本体化）：规则有定义、有理由、可查询
```owl
ToolCapacityConstraint:
  description: "Qwen3-235B 工具调用超 12 个后准确率骤降（2026-02-26 实测）"
  applies_to: TaskSession
  constraint: count(assignedTools) ≤ 12
  rationale: "MoE 架构上下文管理瓶颈"
  evidence: notes/20260226_Qwen3工具调.md
  last_validated: 2026-02-26
  may_change_when: "下一代模型发布"
```

## 下一步实验

1. **选一个小切面**：把 `proxy_filters.py` 的工具白名单本体化——定义每个工具的语义、权限、前置条件
2. **验证价值**：本体化后，能否自动检测工具配置冲突？能否自动生成工具使用文档？
3. **评估成本**：形式化一个模块需要多少工作量？维护成本是否可接受？

---
*初始版本: 2026-04-06*

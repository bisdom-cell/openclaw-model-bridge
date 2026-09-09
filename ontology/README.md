# Ontology KB — 企业 AI 的语义控制平面

> **核心命题**：Ontology is not the whole solution; it is the **semantic control plane** of enterprise AI.
> 本体论不是企业 AI 的全部，但它应该成为企业 AI 的语义控制平面。
>
> 术语严格定义见 `ontology_scope.md`（术语宪法）。工作原则见 `CONSTITUTION.md`（七条宪法）。

## 为什么需要本体论

| 单靠 LLM 的问题 | 本体论补什么 |
|---------|---------|
| 幻觉、不同 session 说法矛盾 | 唯一事实源（Single Source of Truth） |
| 决策是黑盒，无法审计 | 推理路径可追溯（从概念到规则到结论） |
| 不理解组织规则和权限边界 | 业务规则编码（Policy as Ontology） |
| 工具调用无约束 | 工具治理（Ontology-Constrained Tool Calling） |
| 知识随 context window 消失 | 持久化语义结构（跨 session 存活） |

## 三角架构

```
         ┌──────────────────┐
         │   Ontology        │
         │   企业语义骨架     │
         │   概念·关系·规则   │
         └────────┬─────────┘
                  │ 约束 & 锚定
         ┌────────┴─────────┐
         │                  │
    ┌────▼─────┐     ┌─────▼────┐
    │   LLM     │     │  Agent    │
    │   大脑     │◄───►│  执行     │
    │ 理解·推理  │     │ 工具调度   │
    │ 生成      │     │ 动作编排   │
    └──────────┘     └──────────┘
```

- **Ontology → LLM**：事实锚定（减少幻觉）、领域词表（精准理解）、推理规则（约束生成）
- **Ontology → Agent**：工具权限（谁能调什么）、流程规则（什么条件下执行）、合规边界（不能做什么）
- **LLM ↔ Agent**：自然语言理解 → 工具调用 → 结果解释

## 知识库结构

```
ontology/docs/                # 2026-09-09 注：下方树为 2026-04 建库时的示意；实际目录另含 cases/（28 篇血案档案）、reflections/、failure_modes_catalog.md 等，以 ls 为准
├── ontology_scope.md    # ✅ 🔴 术语宪法（四层定义+术语规范+措辞规范，宪法级）
├── CONSTITUTION.md      # ✅ 🔴 工作宪法（五条不可违反原则）
│
├── foundations/          # 基础理论
│   ├── core_concepts.md           # ✅ 本体论核心概念（What/Why/How）
│   ├── schools_comparison.md      # ✅ 三大流派深度对比：BFO / DOLCE / UFO（选型建议+决策树）
│   └── knowledge_representation.md # 🔜 知识表示技术：OWL/RDF/SHACL/KG
│
├── architecture/         # 架构模式
│   ├── ontology_llm_agent.md      # ✅ 语义控制平面论述 v2（四漂移+耦合分类+六域映射）
│   ├── enterprise_agent_ontology_v0.1.md # ✅ 六域参考模型（Actor/Capability/Task/Memory/Governance/Execution）
│   ├── neuro_symbolic.md          # ✅ Neuro-Symbolic AI 深度分析（6范式+5缺陷+实现路径）
│   └── semantic_layer.md          # 🔜 企业语义层设计
│
├── enterprise/           # 企业应用
│   ├── governance.md              # ✅ AI 治理的本体论基础
│   ├── supply_chain_ontology.md   # ✅ 供应链本体（DCSA对齐+货代Watcher升级路径+Phase1-3）
│   └── digital_transformation.md  # 🔜 数字化转型的本体论视角
│
├── technical/            # 技术实现（待创建）
│   ├── ontology_engineering.md    # 🔜 本体工程方法论
│   ├── llm_alignment.md           # 🔜 LLM 与本体对齐技术
│   └── agent_binding.md           # 🔜 Agent 工具调用的本体约束
│
├── cases/                # 案例与证据
│   └── openclaw_as_ontology.md    # ✅ OpenClaw 的本体论重新审视
│
└── readings/             # 文献追踪
    ├── papers.md                  # ✅ 关键论文时间线（7主题40+文献）
    └── thought_leaders.md         # ✅ 关键人物与思想流派
```

## 与现有系统的关系

- **现有 KB**（`~/.kb/`）= 信息流（what's happening）
- **Ontology KB**（`ontology/docs/`）= 知识结构（what does it mean）
- **Dream 引擎** = 用本体论视角分析信息流，发现深层关联
- **OpenClaw Control Plane** = Ontology 的运行时实例化

## 运行时引擎（ontology/）

从知识库到生产系统 — 本体论不只是文档，更是运行时引擎。

### Tool Ontology Engine

```bash
python3 ontology/engine.py --check    # 一致性校验（引擎 vs proxy_filters 一致）
python3 ontology/engine.py --tools    # 工具列表 + 副作用标记
python3 ontology/engine.py --validate write '{"path":"/tmp/x","content":"hi"}'  # 参数验证
```

| 能力 | 说明 |
|------|------|
| 声明式规则 | tool_ontology.yaml，16 builtin + 1 prefix + 3 custom（data_clean/search_kb/expert_escalate）+ 11 policies |
| 语义查询 | `query_tools(side_effects=True)` — 按属性查，非按名称查 |
| 策略推理 | `infer_policy_targets("side_effects == true AND category == file_operation")` |
| **语义分类** | `classify_tool_call("write")` → `{risk: high, tags: [night_blockable, audit_required]}` |
| 等价证明 | Phase 1：引擎输出 = 硬编码输出（ontology/tests 全套验证；V37.8.14 起 ONTOLOGY_MODE=on 生产） |

### Governance Checker v3

```bash
python3 ontology/governance_checker.py          # dev 模式
python3 ontology/governance_checker.py --full    # Mac Mini（含 env/crontab 运行时检查）
```

| 维度 | 数量 |
|------|------|
| 不变式 | **91**（governance v3.56，2026-09-09；每条绑血案/事故，100% job 覆盖） |
| 可执行检查 | **839**（python_assert / file_contains / env_var_exists / command_succeeds / http_endpoint） |
| 元规则 | **23**（MR-1~23，含 MR-6 多层深度、MR-7 治理自观察、MR-8 copy-paste、MR-22 日落法、MR-23 审计只读） |
| 元规则发现 | **14 MRD 扫描器**（CRON / ENV / NOTIFY / LAYER / RESERVED-FILES 等） |

### 验证深度三层模型（V36.3 核心创新）

```
Layer 3: Effect（效果层）  — "X 达到了预期目的吗？"          → 待建设
Layer 2: Runtime（运行时层）— "X 在执行环境中真的发生了吗？"  → ✅ 已覆盖（MRD-LAYER-002：57 个 high 不变式均 ≥2 层）
Layer 1: Declaration（声明层）— "代码/配置说了 X 吗？"       → ✅ 全部 91 不变式
```

MRD-LAYER-001 自动发现单层覆盖的 critical 不变式 — **governance 检查自己的检查能力**。

### Feature Flag 三档切换

```python
# proxy_filters.py
ONTOLOGY_MODE = "off"     # 纯硬编码（回滚档）
ONTOLOGY_MODE = "shadow"  # 双跑比对：引擎观察，硬编码决策（V36.3-V37.8.13 观察期）
ONTOLOGY_MODE = "on"      # 引擎替换硬编码（已验证等价）← 默认值 + Mac Mini 生产运行中（V37.8.14 起）
```

## 起步路径

1. ✅ 目录结构和 README（V36.1）
2. ✅ 核心文档：三角架构论述（V36.1）
3. ✅ 基础理论：本体论核心概念 + **三大流派深度对比**（V36.1）
4. ✅ 实践验证：用本体论视角审视 OpenClaw（V36.1）
5. ✅ 架构分析：**Neuro-Symbolic AI 6 范式 + 实现路径**（V36.1）
6. ✅ 行业应用：**供应链本体 + DCSA 对齐 + 货代 Watcher 升级路径**（V36.1）
7. ✅ Tool Ontology Engine：81 条声明式规则 + 推理引擎（V36.2）
8. ✅ Governance v3：15 不变式 + 验证深度三层模型（V36.3）
9. ✅ Phase 2 Shadow：Mac Mini 生产双跑比对（V36.3）
10. ✅ 话语权输出：**"Why Enterprise AI Needs Ontology"**（EN dev.to + ZH 知乎）（V36.2）
11. ✅ Phase 3：shadow → on 切换（V37.8.14）；Phase 4 P1-P3 部分兑现（domain/policy ontology + three_gate shadow）；Phase 5 引擎已发 PyPI（V37.9.137）
12. 🔜 语义策略替代枚举：infer_policy_targets 接入 proxy_filters 决策点

## 进度统计

| 维度 | 已完成 | 计划 |
|------|--------|------|
| 引擎代码 | **2,691 行** Python + **922 行** YAML | 持续演进 |
| 测试 | ontology/tests 全套（engine 89 + three_gate 57 + governance 等；全仓 6504） | 持续 |
| 不变式 | **91** 不变式 + **839** 可执行检查 + 23 元规则 + 14 MRD | 按 incident 驱动增长 |
| 生产模式 | **on**（Mac Mini 运行中，V37.8.14 起） | Phase 4 enforcement 待需求驱动 |
| KB 文档 | ontology/docs/ 下含 28 篇血案档案 + 架构/基础/企业篇 | 按血案增长 |
| 文献追踪 | 40+ 文献 | 持续 |
| 标准对齐 | DCSA / UN/CEFACT | + GS1 / ISO 28000 |

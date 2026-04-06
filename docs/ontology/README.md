# Ontology KB — 本体论驱动的企业智能架构

> 核心论点：**Ontology（语义骨架）+ LLM（大脑）+ Agent（执行）= 未来企业的核心业务形态**

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
docs/ontology/
├── foundations/          # 基础理论
│   ├── core_concepts.md           # ✅ 本体论核心概念（What/Why/How）
│   ├── schools_comparison.md      # ✅ 三大流派深度对比：BFO / DOLCE / UFO（选型建议+决策树）
│   └── knowledge_representation.md # 🔜 知识表示技术：OWL/RDF/SHACL/KG
│
├── architecture/         # 架构模式
│   ├── ontology_llm_agent.md      # ✅ 三角架构深度论述（核心文档）
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
- **Ontology KB**（`docs/ontology/`）= 知识结构（what does it mean）
- **Dream 引擎** = 用本体论视角分析信息流，发现深层关联
- **OpenClaw Control Plane** = Ontology 的运行时实例化

## 起步路径

1. ✅ 目录结构和 README（V36.1）
2. ✅ 核心文档：三角架构论述（V36.1）
3. ✅ 基础理论：本体论核心概念 + **三大流派深度对比**（V36.1）
4. ✅ 实践验证：用本体论视角审视 OpenClaw（V36.1）
5. ✅ 架构分析：**Neuro-Symbolic AI 6 范式 + 实现路径**（V36.1）
6. ✅ 行业应用：**供应链本体 + DCSA 对齐 + 货代 Watcher 升级路径**（V36.1）
7. 🔜 知识表示技术：OWL/RDF/SHACL 实操指南
8. 🔜 话语权输出：**"Why Enterprise AI Needs Ontology Before It Needs More Models"**
9. 🔜 实验：用 OntoUML 重新建模 proxy_filters.py 的工具策略

## 进度统计

| 维度 | 已完成 | 计划 |
|------|--------|------|
| 文件数 | **10** | ~15 |
| 总行数 | ~1500 | ~2500 |
| 覆盖子目录 | 5/6 | 6/6（technical 待创建） |
| 文献追踪 | 40+ 文献 | 持续 |
| 标准对齐 | DCSA / UN/CEFACT | + GS1 / ISO 28000 |

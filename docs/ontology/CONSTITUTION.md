# Ontology 工作宪法

> 本体论实验的五条不可违反原则。每次修改 ontology 相关代码前必须对照检查。

## 宪法五条

### 第零条：术语纪律（2026-04-06 新增，来自外部专家评审）

> **"Ontology"在本项目中不是一个词，而是四个层次。混用即失控。**

- Layer 1: Concept Ontology（概念本体）— 实体-关系-属性的形式定义
- Layer 2: Constraint & Policy（约束与策略）— 部分可用 OWL，部分需要规则引擎
- Layer 3: Execution Semantics（执行语义）— ontology-aware runtime，不是 ontology 本身
- Layer 4: Evidence & Audit（证据与审计）— 决策追溯，独立支柱

任何文档、代码注释、PR 描述中出现"ontology"时，必须明确指的是哪一层。

**核心命题**：Ontology is not the whole solution; it is the semantic control plane of enterprise AI.

**检查方法**：review 时搜索"ontology"/"本体"，确认每处使用都能对应到 L1-L4 之一。详见 `ontology_scope.md`。

### 第一条：非破坏性引入

> **现有系统必须继续用原有方式运行，本体层并行存在。**

- 新增本体文件（YAML/引擎/测试）不修改现有运行逻辑
- proxy_filters.py 保留全部硬编码规则，ontology_engine 不替代、只验证
- 切换到本体驱动需要经过：并行运行 → 差异对比 → 逐项替换 → 全量回归
- **禁止**：一次性将硬编码规则删除换成本体加载

**检查方法**：`python3 -m unittest test_tool_proxy` 必须不依赖 ontology_engine 就能通过。

### 第二条：一致性安全网

> **本体声明必须与硬编码规则 100% 一致，任何偏差立即可见。**

- `ontology_engine.py --check` 必须返回 `✅ consistent`
- 每次修改 proxy_filters.py 或 tool_ontology.yaml，必须同步修改对方
- CI/preflight 包含一致性检查，不一致则阻断
- 修改任一侧后立即运行 `python3 ontology_engine.py --check`

**检查方法**：`python3 ontology_engine.py --check` 退出码为 0。

### 第三条：每条规则有 rationale

> **不只记录"是什么"，还必须记录"为什么"。这是本体论与配置文件的核心区别。**

- tool_ontology.yaml 中每条 policy rule 必须有 `rationale` 字段
- rationale 说明：为什么需要这条规则 + 什么证据/教训驱动了它
- 空 rationale 或 "TODO" 不允许合入
- 测试强制：`test_policy_has_rationale` 扫描全部规则

**检查方法**：`python3 -m unittest test_ontology_engine.TestPolicies.test_policy_has_rationale`

### 第四条：强制差异对比表格

> **每次变更必须生成两种范式（硬编码 vs 本体）的全量差异对比表格。**

- `python3 ontology_diff.py` 生成 Markdown 表格，逐项对比
- 对比维度：工具白名单、Schema、参数集、别名、策略规则、浏览器约束
- 每个维度标注状态：✅ 一致 / ⚠️ 偏差 / ❌ 缺失
- 表格输出到终端 + 可选保存到 `docs/ontology_diff_report.md`
- **变更 PR 描述中必须附带 diff 表格截图或内容**

**检查方法**：`python3 ontology_diff.py` 输出全绿。

## 执行流程

```
修改 proxy_filters.py 或 tool_ontology.yaml
    │
    ├── 1. python3 ontology_engine.py --check     ← 宪法第二条
    ├── 2. python3 ontology_diff.py               ← 宪法第四条
    ├── 3. python3 -m unittest test_tool_proxy     ← 宪法第一条（不依赖本体）
    ├── 4. python3 -m unittest test_ontology_engine ← 宪法第三条（rationale 检查）
    │
    └── 全部通过 → 允许提交
```

## 宪法的意义

这四条不是流程开销，而是**实验安全网**：

1. **非破坏性**保证实验失败不影响生产
2. **一致性安全网**保证两套系统不会悄悄漂移
3. **rationale**让每条规则有生命——可以被质疑、被修改、被追溯
4. **差异对比**让"本体化进度"可视化——哪些规则已迁移、哪些还在硬编码

当差异对比表格显示 100% 一致时，就是硬编码可以安全删除的时刻。

---
*创建: 2026-04-06 | 这是不可修改的宪法，除非四条原则本身需要演化*

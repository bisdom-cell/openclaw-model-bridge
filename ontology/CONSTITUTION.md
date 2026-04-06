# Ontology 工作宪法

> 本体论实验的七条不可违反原则。每次修改 ontology 相关代码前必须对照检查。

## 宪法七条

### 最高条：项目隔离（优先级最高，不可覆盖）

> **Ontology 是 openclaw-model-bridge 的独立子项目。Ontology 的任何操作都不得对原项目产生任何影响。**

这是所有其他条款的前提。违反此条 = 违反全部宪法。

**具体要求**：

1. **目录隔离**：所有 ontology 文件集中在 `ontology/` 目录下（代码、测试、数据、文档），不在项目根目录散落任何文件。后续成熟后可直接 `git subtree split` 拆为独立仓库
2. **代码隔离**：`ontology/engine.py`、`ontology/diff.py`、`ontology/tests/test_engine.py`、`ontology/tool_ontology.yaml` 是**只读观察者**，不得修改原项目的运行时行为
3. **导入方向单向**：ontology 代码可以 `import proxy_filters`（读取硬编码数据做对比），但 proxy_filters / tool_proxy / adapter **永远不得 import ontology 模块**（当前 proxy_filters.py 启动时的一致性检查是唯一例外，使用 `importlib.util` 动态加载且 try/except 包裹，`ontology/` 不存在时静默跳过）
4. **删除安全**：`rm -rf ontology/` 后原项目必须正常运行，所有原有测试必须通过
5. **文档隔离**：ontology 文档全部在 `ontology/docs/` 下，不修改 `docs/config.md`、`README.md` 等原项目文档
6. **测试隔离**：ontology 测试在 `ontology/tests/` 下，不修改 `test_tool_proxy.py` 等原有测试；`full_regression.sh` 可选择性包含 ontology 测试，但 ontology 测试失败**不得阻断**原项目的回归

**检查方法**：
```bash
# 模拟删除 ontology 后原项目是否正常
python3 -c "
import proxy_filters
print('ALLOWED_TOOLS:', len(proxy_filters.ALLOWED_TOOLS))
print('CLEAN_SCHEMAS:', len(proxy_filters.CLEAN_SCHEMAS))
print('proxy_filters OK: independent of ontology')
"
python3 -m unittest test_tool_proxy  # 必须不依赖 ontology 就能通过
```

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

- `python3 ontology/engine.py --check` 必须返回 `✅ consistent`
- 每次修改 proxy_filters.py 或 ontology/tool_ontology.yaml，必须同步修改对方
- CI/preflight 包含一致性检查，不一致则阻断
- 修改任一侧后立即运行 `python3 ontology/engine.py --check`

**检查方法**：`python3 ontology/engine.py --check` 退出码为 0。

### 第三条：每条规则有 rationale

> **不只记录"是什么"，还必须记录"为什么"。这是本体论与配置文件的核心区别。**

- tool_ontology.yaml 中每条 policy rule 必须有 `rationale` 字段
- rationale 说明：为什么需要这条规则 + 什么证据/教训驱动了它
- 空 rationale 或 "TODO" 不允许合入
- 测试强制：`test_policy_has_rationale` 扫描全部规则

**检查方法**：`python3 -m unittest ontology.tests.test_engine.TestPolicies.test_policy_has_rationale`

### 第四条：强制差异对比表格

> **每次变更必须生成两种范式（硬编码 vs 本体）的全量差异对比表格。**

- `python3 ontology/diff.py` 生成 Markdown 表格，逐项对比
- 对比维度：工具白名单、Schema、参数集、别名、策略规则、浏览器约束
- 每个维度标注状态：✅ 一致 / ⚠️ 偏差 / ❌ 缺失
- 表格输出到终端 + 可选保存到 `ontology/docs/ontology_diff_report.md`
- **变更 PR 描述中必须附带 diff 表格截图或内容**

**检查方法**：`python3 ontology/diff.py` 输出全绿。

### 第五条：每次变更必须输出优势分析表格（无例外）

> **当前处于项目开发前期。每一次变更都必须输出表格，详细列举并分析本体论 vs 硬编码/配置文件的差异及优势。没有例外。项目可以慢一点，但不能跳跃任何模糊的难点。**

这条宪法的存在理由：本体论的价值不是"我们说它好"，而是**每一步都能用对比证据说明好在哪里**。如果某次变更无法清晰说明比硬编码优在何处，说明这次变更要么不该做，要么还没想清楚。

**具体要求**：

1. **每次 commit 前**必须输出一张对比表格（在对话中展示给用户），至少包含：

   | 维度 | 硬编码/配置文件方式 | 本体论方式 | 优势分析 |
   |------|-------------------|-----------|---------|
   | （变更涉及的每个具体点） | （现有方式怎么做） | （本体方式怎么做） | （好在哪里，量化证据） |

2. **表格必须具体**，禁止出现以下模糊表述：
   - ❌ "更灵活" → 必须说明哪种场景下灵活在哪里
   - ❌ "更好维护" → 必须量化：改 N 处 vs 改 1 处
   - ❌ "更可扩展" → 必须给出新增工具/规则时的具体操作步骤对比

3. **如果某个变更点无法说清优势**，必须在表格中标注 `⚠️ 待验证` 并暂不实现，而不是模糊跳过

4. **模糊难点必须显式记录**：如果在实现中遇到"硬编码其实也能做"或"本体方式反而更复杂"的情况，必须诚实记录在表格中，标注为 `⚠️ 本体无明显优势` 或 `❌ 本体更复杂`。这不是失败，而是精确划定本体的价值边界

**检查方法**：每次 PR / commit message 中必须包含对比分析表格的引用或摘要。reviewer（用户）确认表格覆盖了所有变更点。

**为什么这条宪法重要**：

```
没有这条宪法：
  → 开发者凭直觉推进 → 某些功能做完才发现"其实配置文件也行"
  → 优势模糊 → 无法对外解释 → 项目失去说服力

有这条宪法：
  → 每一步都有对比证据 → 优势清晰可量化 → 劣势也被诚实标记
  → 积累 20 次变更后 → 自动形成一份"本体论价值白皮书"
  → 这份白皮书就是项目对外的核心说服力
```

## 执行流程

```
修改 proxy_filters.py 或 ontology/tool_ontology.yaml
    │
    ├── 1. 输出"本体 vs 硬编码"对比分析表格          ← 宪法第五条（最先做）
    ├── 2. python3 ontology/engine.py --check            ← 宪法第二条
    ├── 3. python3 ontology/diff.py                      ← 宪法第四条
    ├── 4. python3 -m unittest test_tool_proxy            ← 宪法第一条（不依赖本体）
    ├── 5. python3 -m unittest ontology.tests.test_engine ← 宪法第三条（rationale 检查）
    │
    └── 全部通过 + 表格已展示 → 允许提交
```

## 宪法的意义

这七条不是流程开销，而是**实验安全网**：

0. **项目隔离**保证 ontology 是独立子项目，随时可拆走、删除不影响原系统
1. **术语纪律**保证"ontology"四层不混用
2. **非破坏性**保证实验失败不影响生产
3. **一致性安全网**保证两套系统不会悄悄漂移
4. **rationale**让每条规则有生命——可以被质疑、被修改、被追溯
5. **差异对比**让"本体化进度"可视化——哪些规则已迁移、哪些还在硬编码
6. **优势分析表格**让每一步的价值可量化、可追溯、可累积——20 次变更后自动形成价值白皮书

当差异对比表格显示 100% 一致时，就是硬编码可以安全删除的时刻。
当 `ontology/` 目录足够成熟时，`git subtree split` 即可拆为独立仓库。

---
*创建: 2026-04-06 | 这是不可修改的宪法，除非原则本身需要演化*

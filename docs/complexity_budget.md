# Complexity Budget — a convention, deliberately *not* a tool

> 外部评审 #2（2026-06-11）点了"复杂度组合面大"；评审 #1（2026-04-17）独立地点了
> "读起来像一个超强脚本系统，模块边界偏重作者的心智模型"。两个陌生人指向同一个风险：
> **复杂度在不被注意时增长——因为每一次新增，局部看都合理。**
>
> 这份文档是答案。而答案是一条**约定**，不是一个追踪器。
>
> 立项：外部评审2 P2(d)（V37.9.150 / 2026-06-14）。北极星：[原则 #34 日落法](../CLAUDE.md) + MR-22。

---

## 一、为什么要有预算

把一个 agent runtime 推上生产的代价，从来不在单个部件——单个 provider、单个 cron、单个不变式，局部看都对、都有理由。代价在**接缝**：部件的交互面积超线性增长，超过测试能覆盖的范围（"复杂关乎部件，意外关乎接缝"）。

复杂度预算的工作不是"测量复杂度有多大"。它的工作是：

1. **让"净新增"无处可藏。** 每个版本都报出账本增量，新增一个机制就在 changelog 里留痕，没法假装它是免费的。
2. **强制一个问题：你退役了什么？** 对每一个净新增的*机制*（不是证据/文档），必须回答："我退役了一个等价的旧机制吗？还是这是一个真正加价值的新能力，而不是又一道接缝？"

预算不阻止增长。它让增长**有意识**。

---

## 二、为什么*不是*一个工具（日落法张力）

最诱人的实现是写一个 `complexity_budget.py`：从 git 数文件、从 governance 数 check、对比上次的数字、漂移就告警。**坚决不做。**

因为那个工具本身就是它要防的东西：

- 一个**新脚本** = 一个新的故障面（防御本身是事故面）。
- 一个**新状态源**（"上一个版本的数字"存哪里？）= 又一个要同步、会漂移的真理源。
- 一个**新 check**（实际 vs 预算的漂移）= 又一条要维护的治理规则。

**用增加复杂度的方式来测量复杂度——这正是评审点中的那个自相矛盾。** 药参与了病。

所以这份预算的执行机制是**可见性**，零新机器：

- **账本 = 你已经有的徽章。** `gen_readme_badges.py` 已经从权威源算出所有增长数字。
- **报告 = changelog 已有的"验证"字段。** 它已经在报 suites/tests/checks/VERSION 增量。
- **强制 = 已有的收工清单（[原则 #9](../CLAUDE.md) 新增 J 项）+ 人工复核。**

**这份预算刻意没有守卫测试。** 这不是疏漏，是 MR-22（sunset-over-accretion）的示范：一个被新 check 守卫的复杂度约定，会把"治理复杂度"本身变成新的复杂度。约定靠"留痕在 changelog + 收工自问"执行——净新增藏不住，就够了。

---

## 三、账本 = 你已经有的徽章

`gen_readme_badges.py` 已是单一真理源，从权威源（status.json / audit_metadata / providers.py）算出：

| 维度 | 来源 | 含义 |
|---|---|---|
| **suites / tests** | full_regression / status.json | 测试规模（证据，*增长是好事*） |
| **invariants / meta-rules / checks** | governance audit_metadata | 治理规模（*每个都须说明防哪类事故*） |
| **MRD scanners** | meta_rule_discovery | 主动扫描器数 |
| **case docs** | `ontology/docs/cases/*.md` | 血案案例数（证据） |
| **providers** | providers.py | 接入的 provider 数 |
| **version / semver** | CLAUDE.md / VERSION | 文档版本 / 业务语义版本 |

这八个就是账本。**不另起炉灶。** 还有四个维度徽章不覆盖——**只在它们变化时**手动在 changelog 里记一行：

- **files (+/-)**：新增/删除文件数（`git diff --stat` 一眼可见，不存状态）。
- **env vars (+/-)**：新增/删除的环境变量依赖。
- **cron / jobs (+/-)**：新增/删除的定时任务。
- **runtime state sources (+/-)**：新增/删除的运行时状态源（status.json 字段 / 新 cache 文件 / 新 JSONL 等）。

后四个是最危险的复杂度——它们是真正的"新接缝"。它们变化时必须显式留痕。

---

## 四、约定（收工清单 J 项）

收工时，changelog 的"验证"字段照常报账本增量（suites/tests/inv/MR/checks/VERSION）。**额外**：

1. **后四维度若有变化，显式记一行**（files/env/jobs/state-sources 的 +/-）。
2. **对每个净新增的*机制*，回答一个问题**："我退役了一个等价的旧机制吗？还是这是真正加价值的新能力（不是又一道接缝）？"

什么算"机制"（须自问），什么算"免费"：

| 类别 | 算什么 | 预算待遇 |
|---|---|---|
| **证据 / 文档** | 新测试、新 case doc、新 example、新文章、徽章同步 | **免费**——增长是健康的，VERSION 不变 |
| **机制** | 新 runtime path / 新 invariant / 新 MR / 新 cron / 新 env / 新状态源 / 新框架 | **须自问**——退役了什么？或为何是真能力非新接缝？ |

经验法则：**如果一个版本 `VERSION 0.37.9.70 不变`，它大概率是证据/文档刷新（免费）。如果 VERSION bump 了，账本必须解释新增的*机制*退役了什么。**

---

## 五、三条硬规则（已是惯例，此处明文）

1. **新增 runtime path 须退役一个等价旧 path。**（日落法规则 1 + 一物一形。）一段复制了既有能力的新代码路径，必须*替换*而非*共存*。真的退不掉，在 changelog 里说明为什么（如 V37.9.119 的 7064 realpath：保留是因风险不对称，已登记）。
2. **新 invariant / check 须命名它防的历史事故。**（已是惯例：`meta_rule` + `blood_lessons`。）一个背后没有历史事故的 check，是投机性复杂度——它守的是想象中的 bug，却是真实的维护成本。
3. **新 cron / job 须定义 silence-timeout + degrade 策略。**（已是惯例：jobs_registry interval + job_watchdog 阈值 + fail-fast/降级。）一个没有 silence-timeout 的 job，是一个等着发生的静默故障（MR-4 家族）。

---

## 六、健康预算长什么样（实例：V37.9.146 → V37.9.149）

| 版本 | 账本增量 | 后四维度 | 净*机制*？ | 判定 |
|---|---|---|---|---|
| **V37.9.146** | +30 tests, +1 suite | env/jobs/state 0 | `verification_tier` 字段进 providers.py | ✅ **净 -1 接缝**：新字段*退役了*手写档位表（一物一形），VERSION 不变 |
| **V37.9.147** | +23 tests | 0 | Reliability Bench +10 场景 | ✅ **免费**：新场景是证据（测真函数），VERSION 不变 |
| **V37.9.148** | +1 suite, +17 tests | files +12（example+test）, env/jobs/state 0 | 0（example + test 是证据） | ✅ **免费**：仓库外 dogfood 是可迁移性*证据*，零 runtime 机制，VERSION 不变 |
| **V37.9.149** | +0 tests | files +2（articles） | 0 | ✅ **免费**：话语权文档，VERSION 不变 |

读法：**最近四版全是证据/文档刷新，VERSION 全程不变，净 runtime 机制 ≈ 0（V37.9.146 甚至是净退役）。** 这正是预算要让人看见的"日落法健康态"——系统在变重的是*证据密度*，不是*接缝面积*。

---

## 七、预算什么时候喊"停"

以下是红旗。**没有一个是硬阻断**——它们只是被*看见*，让操作者（你 + 未来的 Claude）有意识地决定：

- VERSION bump 了，但 changelog 没说退役了哪条等价 path。
- 新增了一个运行时状态源，却没退役一个旧的。
- 新增了一个 check，却命名不出它防的历史事故。
- 连续数版文件净增长，却没有任何对应退役（接缝在悄悄累积）。

红旗出现时，问日落法的第一个问题：**"在加这个之前，能退役一个等价的现有机制吗？"** 如果能，先退役。如果不能，在 changelog 里说清楚为什么这是真能力而非新接缝。

---

*相关：[原则 #34 日落法](../CLAUDE.md)（北极星）· MR-22 sunset-over-accretion · MR-23 audit-observes-never-mutates · [为什么这么多意外的深度反思](../ontology/docs/cases/why_so_many_incidents_2026_06_05_reflection.md) · 立场文章 [control plane, not wrapper](articles/why_runtime_not_wrapper.md)（第六条原则"先退役，再添加"）。*

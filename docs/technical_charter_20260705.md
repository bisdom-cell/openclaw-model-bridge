# 技术纲领：openclaw-model-bridge 中长期架构与业务规划（2026H2 → 2028）

> **文档性质**：长期保存的技术纲领（Technical Charter）。上承 `docs/strategic_review_20260403.md`（导师 12 个月 V1/V2/V3 路线，V1/V2 已完成、V3 收口中），向下覆盖 2026 下半年至 2028 年的架构与业务方向。
> **确立版本**：V37.9.246（2026-07-05） | **确立时项目状态**：VERSION 0.37.9.101 / 5440 tests / 153 suites / 91 invariants / 839 checks / 11 providers / ~40 jobs / arXiv:2606.14589 已发表 / PyPI openclaw-ontology-engine 0.1.0 已发布 / IEEE Software + ISSRE 双投 in-review / LLM-Observer shadow 周观察中。
> **维护契约**（见 §11）：季度复核、判据驱动增量修订、修订只追加不改写历史判断。

---

## 〇、纲领的三条元约束（先于一切内容）

任何后续规划条目若与以下三条冲突，以三条为准：

1. **日落法北极星（原则 #34）适用于纲领本身**：本纲领的每个新增方向都必须回答"退役了什么"。纲领不是功能愿望清单，是"做什么/不做什么/什么条件下做"的判据集。规划失败的常见形态是"计划本身成为复杂度来源"——本纲领用判据门控（§8）替代日历承诺来防止这一点。
2. **一物一形**：本纲领只管"方向与判据"。当前执行态活在 `status.json`，操作手册活在 `CLAUDE.md`，研究细节活在各 design doc（如 `docs/llm_observer_design.md`）。本纲领引用它们，绝不复制细节——否则纲领会像一切多重表示一样必然漂移。
3. **诚实（原则 #23）**：§2 的趋势判断全部标注置信度，是基于 2026-07 可得信息的判断而非事实；§6 的业务路径不编造市场数字；§8 的判据允许"条件不满足则不做"作为体面结局。负面结果（如 Observer held-out recall 非平凡失败）本身可发表，不掩盖。

---

## 一、现状盘点（2026-07-05 快照）

### 1.1 阶段判定

| 阶段 | 定义 | 状态 | 证据 |
|------|------|------|------|
| Stage 1 系统构建者 | 系统能跑、有工程纪律 | ✅ 完成（2026-04 导师确认） | 全栈生产运行 8+ 月 |
| Stage 2 被社区认可的系统作者 | 有可引用的公开资产 | ✅ 大部分达成 | arXiv:2606.14589 发表 / PyPI 包 / 8 篇文章 / 两次外部评审 7.8/10 |
| Stage 3 同行评审认可 + 真实采用 | 论文接收 + 外部消费方 | 🟡 进行中 | IEEE/ISSRE in-review；bench/引擎已公开但外部采用 ≈ 0 |
| Stage 4 领域参考点 | 概念/方法被他人引用复用 | ⬜ 本纲领的目标区间 | — |

### 1.2 五平面资产（工程轨）

| 平面 | 核心资产 | 成熟度 |
|------|----------|--------|
| 控制平面 | Tool Proxy（工具治理/告警隔离/截断）+ Adapter（认证/fallback 链/断路器/`_deliver` 投递契约）+ workload-aware 路由（A2 分流 + B1 reasoning-off 注入） | 生产验证，本项目最强 |
| 能力平面 | 11 provider（7 built-in + 4 插件）+ 能力声明/验证四档 + capability-aware vision fallback + FALLBACK_ORDER | 生产验证；doubao_21 primary flip 完成 |
| 记忆平面 | Memory Plane v2（4 层：KB 语义/多媒体/偏好/状态 + 去重/加权/冲突消解）+ KB RAG + 对话精华 MapReduce | 生产运行；plugin 化未启动 |
| 作业平面 | ~40 cron jobs + jobs_registry 单一注册表 + watchdog 状态契约 + fail-loud 纪律 + convergence 声明↔运行时机器同步 | 生产运行 |
| 治理平面 | ontology-engine（PyPI 已发布，config-injectable）+ 91 不变式/839 检查/23 元规则/14 MRD 扫描器 + 三阶段门控（shadow）+ 混沌审计 16/16 | 生产验证 + 已包化 |

### 1.3 研究与话语权资产（研究轨）——本项目真正的护城河

**关键判断：本项目的护城河不是代码，是纵向证据语料库与方法论。** 代码可以被复制，但"8+ 月连续记录、28+ 个完整因果链 postmortem、每条不变式绑定血案、防御经 sabotage 验证"的纵向语料，全球没有第二份。随着 agent 可靠性成为行业痛点，这份语料的价值随时间**增值**而非贬值。

| 资产 | 状态 |
|------|------|
| fail-plausible 概念 + 5 类静默故障 taxonomy | arXiv:2606.14589 已发表（潜在品类定义词） |
| 28+ postmortem 语料库（带机器标签 ground-truth，`llm_observer_ground_truth.yaml`） | 持续增长中 |
| LLM-Observer（机械化人眼，两层管道 S1-S5 + LLM-judge） | Stage 0-6 chunk 1 完成，shadow 周中，宪法级 #1 |
| fail_plausible_bench + reliability_bench（17 场景） | 已开源，manifest 可复现，外部贡献协议就绪 |
| 方法论体系：日落法 / 复杂度预算 / 异常分析宪法 / audit-as-regression / 判据门控决策法 | 已文档化并自我应用 |
| 8 篇文章（EN/ZH 双轨）+ 2 篇在审论文 | 影响力漏斗上游 |

### 1.4 已被外部交叉验证的短板（两次独立评审相隔八周撞出同一对）

1. **个人 PA 耦合深、可迁移性不足**——引擎已抽出（PyPI），但完整 runtime 从未在 Mac Mini 之外的机器上跑通过；
2. **复杂度组合面大**——已有复杂度预算/日落法对冲，但接缝总量仍在高位；
3. 文档认知过载（V37.9.167/245 两轮归档已在收敛）；
4. n=1 证据——纵向深度是优势，横向可迁移性是待证命题。

**纲领结论：短板 1、2 就是 H1 地平线（§5）的主攻方向。**

---

## 二、未来 12–36 个月技术趋势判断（置信度标注）

> 判断基于 2026-07 可得信息 + 本项目一线实证。置信度：高 = 本项目已实证或多方独立信号；中 = 方向性判断，形态不确定。

| # | 趋势 | 置信度 | 对本项目的含义 |
|---|------|--------|----------------|
| T1 | **Agent 基础设施分层成型**：framework（百花齐放红海）之下，runtime / gateway / control plane 成为独立品类；"谁都能搭 agent，没人能治理 agent"的缺口显性化 | 高 | 本项目 2026-04 就押注的定位被趋势追认；坚持"治理与可靠性层"不做"又一个 framework" |
| T2 | **AI 可靠性工程（SRE-for-AI）成为落地瓶颈**：静默故障/语义级失败从学术词变成工程预算项；evals 从离线走向 runtime monitor | 高 | fail-plausible taxonomy + LLM-Observer 正在窗口期内；~1-2 年内是个人成为该细分领域"the person"的窗口，之后大厂产品会占领心智 |
| T3 | **Reasoning 模型普及 + per-request 推理控制成为路由维度**：thinking on/off、reasoning budget 是请求属性而非模型属性 | 高（已实证） | V37.9.220 事故→V37.9.221/222 A2+B1 的"批量 thinking-off / PA thinking-on"实践领先行业；这类运维知识本身可发表 |
| T4 | **多模型异构常态化**：无单一最优模型；capability-aware + workload-aware + cost-aware 路由成 gateway 标配 | 高（已实证） | 能力平面继续纵深（成本-质量-时延联动调度在 backlog）；provider 阵亡/价格剧变风险由多 provider 架构天然对冲 |
| T5 | **协议标准化收敛**：MCP 类工具协议 + OpenAI-compatible API 长尾并存 | 中高 | 工具治理引擎评估 MCP 兼容形态（判据门控 G7，不投机先做） |
| T6 | **Agent memory 独立品类化**：记忆系统从 framework 附属变独立层 | 中 | Memory Plane 保持"统一接口 + 优雅降级"叙事，plugin 化按需求驱动（原则 #18） |
| T7 | **AI 治理合规压力增长**（欧盟 AI Act 分阶段执行、企业内部 AI 审计需求）：可解释的 audit trail / policy / invariants 出现真实买单方 | 中高 | ontology-engine 的"声明式不变式 + 链式审计日志 + 血案 lineage"正对合规叙事；但个人项目不直接吃合规市场，通过开源影响力间接受益 |
| T8 | **LLM-as-judge 从离线 eval 走向 runtime observer**：质量监控层成为生产标配 | 高 | LLM-Observer 的两层管道（确定性 pre-filter + judge + 反幻觉 grounding）是该品类的先行参考实现 |
| T9 | **本地/混合推理增长**：自建端点 + 云 API 混合是中小团队常态 | 中 | 本项目 Qwen 自建 + 云 provider 混合的运维经验（端点刷新/量化质量差异/geo-block）是真实素材 |
| T10 | **AI 协作开发改变单人系统规模上限**：一人 + AI 可运营企业级复杂度系统，新瓶颈从实现带宽转移到判断纪律 | 高（本项目即实证） | 论文已论述；日落法/复杂度预算/判据门控就是"判断纪律"的方法论产品，可继续输出 |

**趋势综合结论**：T1+T2+T8 构成本项目未来 24 个月的顺风主航道（治理 × 可靠性 × runtime observer）；T3+T4 是能力平面的持续纵深方向；T5-T7、T9 是机会性配合项，全部判据门控。

---

## 三、市场潜在需求与本项目的可服务位置

> 诚实框定：这是**个人 + AI 协作**项目，非公司。"市场"在此指影响力市场（谁会引用/采用/复用）与选项性商业市场（若未来出现真实付费信号）。不编造市场规模数字。

| 需求段 | 需求形态 | 本项目可服务的资产 | 服务方式 |
|--------|----------|--------------------|----------|
| M1 企业/团队 agent 治理 | 工具白名单、策略声明、审计链、不变式回归 | ontology-engine + governance 框架 + convergence | 开源采用（pip install）+ Extension Guide |
| M2 AI 可靠性工程 | 静默故障检测、incident taxonomy、混沌演练、可复现 bench | fail-plausible taxonomy + Observer + 双 bench + 28 postmortems | 论文引用 + bench 社区贡献 + 参考实现 |
| M3 多 provider 韧性路由 | fallback/断路器/能力路由/reasoning 控制 | control plane 全套实践 + 事故档案 | 证据型文章 + 参考架构 |
| M4 个人/SMB 智能中枢 | 个人 PA、数据复利、作业编排 | 完整活体系统（本项目自身） | 不作为产品卖点，作为**活体实验室证据**（见 §4.3） |
| M5 研究共同体 | 纵向生产数据、可复现验证集 | ground-truth 语料 + bench manifest | 论文 #2/#3 + artifact evaluation |

**定位一句话**：不做 agent 的"第 N 个框架"，做 agent 的**治理与可靠性层**——用一个真实生产系统的纵向证据，把"静默故障可被机械化检测与预防"做成一门可迁移的学科。

---

## 四、战略定位（在 2026-04 定位基础上的精炼，非推翻）

### 4.1 北极星不变

- **第 0 号宪法**（最强模型不降配）——判断深度是核心竞争力；
- **三合一宪法**（用户专业深度 × Claude 高效执行 × OpenClaw 数据复利）；
- **日落法**（降复杂度优先于加功能）；
- **宪法级研究攻关 #1**（机械化人眼 LLM-Observer）凌驾一切功能添加。

### 4.2 双轨战略（本纲领的主结构）

```
研究轨（Reliability Science for Agent Runtimes）
  taxonomy(已发表) → 预测性检测(Observer, 进行中) → 预防性框架(论文#2/#3) → 社区标准(bench 被外部跑)
                                    ×
工程轨（Portable Governance Plane）
  引擎抽取(ontology-engine 已发布) → PA 解耦/第二实例(H1) → 引擎家族+真实外部消费方(H2) → 参考架构(H3)
```

两轨互为燃料：工程轨的生产系统持续产出研究轨的语料与实验场；研究轨的概念与 bench 反过来给工程轨的开源资产提供"为什么该用它"的叙事。**任何只喂单轨的大型投入需要额外理由。**

### 4.3 PA 的角色重定义（回应"个人耦合"批评的战略答案）

不是杀掉 PA 去追求"纯净的框架"，而是明确双身份：

- **PA = 长寿活体实验室（longitudinal testbed）**：它每天产生真实流量、真实事故、真实用户视角观察——这是 28+ postmortem 语料的来源，是别人没有的资产。杀掉它 = 杀掉数据复利。
- **引擎 = 可抽取的产品**：凡是被证明有普适价值的机制，走"config-injection 抽包"路径离开 PA（ontology-engine 已走通，observer-engine 是下一个候选）。

一句话：**一个系统，两种产出——实验室与引擎。** PA 耦合从"待清除的债"重定义为"须管理的边界"：边界内（个人配置/中文 prompt/Mac Mini 特定）明确标注并 config 化；边界外（引擎/bench/方法论）保证零 PA 依赖。

### 4.4 反目标（明确不做什么，与做什么同等重要）

1. **不做通用 agent framework**（红海 + 违反纵向做深原则 #19）；
2. **不追新模型/新协议的每一班车**——provider 接入由真实需求驱动（Doubao/DeepSeek 先例），协议兼容走判据门控；
3. **不做 UI/前端/托管服务**（超出个人 + AI 运营半径；若未来有 G4/G5 信号再议）；
4. **不做投机性 enforcement**——three-gate Phase D（删改用户可见内容）永久保持 human-approval；
5. **不假设团队与资金**——所有规划以"一人 + AI + 一台主机（+未来第二实例）"为资源约束；
6. **不让纲领本身膨胀**——季度复核只做增量修订，任何"加一节"须先问"退役哪一节"。

---

## 五、架构中长期规划：三地平线

> 每个地平线都有**主攻方向**与**退役清单**（日落法强制配对）。时间是预期区间非承诺，推进由 §8 判据门控。

### H1（2026H2，0–6 个月）：收敛与解耦——"从一台 Mac Mini 到一个可复制的形态"

**主题：把两次评审共同点名的短板（PA 耦合 / 可迁移性）从声明式回应升级为实证式回应；同时完成宪法级 #1 的研究闭环。**

| # | 方向 | 内容 | 完成判据 |
|---|------|------|----------|
| H1-A | **LLM-Observer 收官**（宪法级 #1，最高优先） | Stage 5.1 flip 决策（按 design doc §9.1 预注册判据）→ flip 后精度观察 → Stage 6 论文 #2 数据积累（detection latency：Observer 抢在用户之前多少小时；held-out recall 诚实结果）→ 新 TP/FP case 持续回灌 ground-truth 与 bench | 论文 #2 的核心数据表可填（含 negative result 也算完成） |
| H1-B | **PA 解耦第一批（config 化，非目录重排）** | 机器化盘点 PA-specific 硬编码面（个人路径/号码占位/中文 prompt/Mac Mini 假设）→ 产出解耦 backlog → 低 blast radius 的 config 化分批落地。**明确不做**大目录重排与 3-engine-merge（两次 DEFER 决议维持） | 盘点清单入库 + 第一批 config 化合并 + 新增 PA-specific 硬编码有扫描器拦截 |
| H1-C | **第二实例 PoC（可迁移性从声明到实证的关键一步）** | 在一台 Linux 容器/VPS 上跑通最小闭环：minimal_runtime + governance audit + 1-2 个内容 job + notify（通道可为 Discord-only）。产出 portability report（证据型文章）。cross_os_quirk_scanner 已铺路 | 非 Mac Mini 机器上 E2E 绿 + 报告发表。**这是对两次评审可迁移性批评的终局回答** |
| H1-D | **证据刷新持续**（评审2 指令） | Mac Mini 侧 SLO 报告重生成、bench manifest 保持 current、compat matrix 机器化守卫（已完成）持续运转 | 季度复核时无 stale 证据 |
| H1-E | **复杂度预算年度复盘** | 2026 全年账本汇总：files/env/jobs/runtime-state-sources 四维度净变化 + 净退役清单 | 复盘入 changelog；2027 年度目标：runtime-state-sources 净零增 |

**H1 退役清单**：PA 硬编码假设（→config）；stale 证据面；头注/文档计数漂移源（V37.9.245 已开始）；Observer shadow 期结束后退役"人工逐条扫已知 fail-plausible 模式"的人工动作（design doc 日落法第一问的兑现）。

### H2（2027，6–18 个月）：产品化与生态——"别人真的在用"

**主题：从"可被使用"（published）到"正在被使用"（adopted）。核心度量从内部测试数转向外部采用信号。**

| # | 方向 | 内容 | 判据/门控 |
|---|------|------|----------|
| H2-A | **ontology-engine 0.x → 1.0** | semver 稳定承诺、公开 CI、good-first-issue、外部贡献者文档；目标 ≥3 个非本人真实消费方（从 dogfood toy 到 real adopter） | 1.0 发布判据：外部 issue/PR 出现且 API 连续 2 个 minor 无 breaking |
| H2-B | **bench 社区化运营** | fail_plausible_bench + reliability_bench 接受外部 case 贡献；提交 artifact evaluation track / awesome list；发布跨系统对比报告（若有第二个系统贡献数据） | 首个外部贡献 case 合并 = 里程碑 |
| H2-C | **observer-engine 抽包（候选）** | 把 daily_observer + llm_observer 按 ontology-engine 同款 config-injection 路径抽为可安装引擎（"给任何 agent 系统装一只机械化人眼"） | **G3 门控：出现 ≥1 个真实外部需求信号才做**（镜像 Provider Plugin 由 Doubao 驱动的先例）；无信号则只维持 bench 形态 |
| H2-D | **论文 #2 投稿 + 可能的论文 #3** | #2 = predictive taxonomy（Observer 生产数据）；#3 候选 = Observer 系统论文或"单人 + AI 运营企业级系统的判断纪律"方法论论文 | #2 完成投稿；#3 视 #2 反响 |
| H2-E | **能力平面纵深** | 成本-质量-时延联动调度（V2 backlog 项）：把 A2/B1 的 workload 路由扩展为显式成本/延迟预算路由；Memory Plane plugin / Job SDK 按需求信号启动 | 均需求驱动（原则 #18），不投机 |

**H2 退役清单**：bridge-specific 逻辑持续进 config；未被任何消费方使用的引擎接口（发布 1.0 前清理）；PA 内已被引擎替代的旧路径。

### H3（2028，18–36 个月）：标准与影响力——"领域的参考点"

| # | 方向 | 内容 | 判据/门控 |
|---|------|------|----------|
| H3-A | **Agent Reliability 参考架构白皮书** | 四件套成体系：taxonomy（是什么）+ defense framework（怎么防）+ bench（怎么测）+ observer（怎么在线抓），配三年纵向数据 | H2 的采用信号 ≥ 基线才值得写；否则降级为长文 |
| H3-B | **教学/布道形态**（可选） | 课程化内容或书（中文技术社区对"单人 + AI 运营生产系统"题材有真实缺口） | 判据：白皮书/论文的自然流量证明需求 |
| H3-C | **商业化评估**（选项，非承诺） | 若 M1/M2 出现 ≥3 个独立组织主动 inbound（咨询/定制/托管询问），评估轻形态（咨询/赞助/托管 observer） | **G4 门控**；不满足则明确维持开源影响力路线，这同样是体面结局 |
| H3-D | **纲领 2.0 修订** | 以三年真实数据重写趋势判断与地平线 | 2028 年中 |

**H3 退役清单**：届时已被标准/上游吸收的自建机制（例如 OpenClaw ≥6.11 升级后退役冷调用 workaround 家族——判据已在 eval doc §17）。

---

## 六、业务规划（四条业务线，B0 为根）

> "业务"在本项目语境 = B0 个人生产力复利（系统存在的第一性理由）+ B1/B2 影响力资产运营 + B3 商业化选项。

### B0：个人智能中枢（用户的真实日常价值——一切的根）

- **现状**：论文/资讯/财经/货代监控 + KB 数据复利 + 每日深度/Dream/机会点雷达 + PA 对话，~40 jobs 全天候运转。
- **规划**：该线的原则是**质量收敛而非数量扩张**（原则 #32 用户视角观察制度持续运转）。候选增强始终从用户真实反馈驱动（如 2026-06-01 "更多 AI 大神不同观点" → ai_leaders 双渠道落地的先例）。货代/财经等领域数据积累到足够密度后，评估领域知识图谱（V3 backlog，6-12 个月数据门槛）。
- **与其他线的关系**：B0 是活体实验室（§4.3），它的每次事故喂研究轨、每次修复喂工程轨。**B0 的健康度（告警噪声/推送延迟/信息密度/用户感知）是全项目的第一 KPI**——一个自己都不好用的系统没资格谈治理别人。

### B1：研究与话语权（Stage 3→4 的主引擎）

- 影响力漏斗：**论文被引 → bench 被跑 → 引擎被装 → 合作/咨询 inbound**。每一级都有免费的公开渠道（arXiv/GitHub/PyPI/知乎/dev.to），无需资金。
- 内容节奏：维持"每季度 ≥1 篇有据文章"惯例（证据型 > 立场型 > 架构型的优先序，因为证据是本项目差异化）。
- 双语双轨：英文（arXiv/dev.to，学术与国际工程圈）+ 中文（知乎/公众号，中文技术社区的"单人 AI 系统"题材缺口）。
- 度量（每季度记录一次，进 status.json recent_changes，不建新工具）：论文引用数 / PyPI 下载 / GitHub 外部 star·issue·PR / bench 外部贡献 / 文章阅读与 inbound 询问。**诚实复盘：连续两季度全零增长 = 触发叙事/渠道策略反思，不自欺。**

### B2：开源生态（引擎与 bench 的采用）

- 从"发布"到"运营"的最小动作集：README 双语门面（已具备）/ 快速上手 10 分钟路径（minimal_runtime 已具备）/ 外部 issue 48h 内响应承诺（个人可承受）/ 每个引擎版本有 changelog 与迁移说明。
- **刻意的小**：不建 Discord 社区、不做 roadmap 投票——个人项目的生态运营半径就是"回 issue + 收 PR + 写文档"，超出即违反资源约束。

### B3：商业化选项（保持选项性，不做承诺）

- 可能形态（按启动成本升序）：技术咨询（按次）→ 内容变现（课程/书）→ 赞助（GitHub Sponsors）→ 托管 observer 服务（需 G4+G5 双门控）。
- **明确纪律**：任何商业化动作不得损害 B1 的公开性（论文/bench/引擎保持开源），不得引入违反 §4.4 反目标的承诺。

---

## 七、贯穿性技术决策（架构层的长期押注）

这些是跨地平线的持久决策，改变它们需要纲领级修订：

1. **零依赖核心**：Core Runtime 保持 stdlib-only（PyYAML 仅治理层），这是对抗 framework 膨胀趋势的差异化，也是可迁移性的底座。
2. **config-injection 是唯一的抽包路径**：任何"PA 机制 → 通用引擎"的迁移都走 ONTOLOGY_CONFIG_DIR 同款模式（已被 ontology-engine/WeatherBot/LibraryBot 三级验证），不发明第二种插件机制。
3. **声明式治理 + 机器同步**：governance YAML（声明）↔ runtime（观察）↔ convergence（对账）三角保持——这是"GitOps for agent runtime"的雏形，是 T7 合规趋势的技术接口。
4. **判据门控决策法**：所有"要不要做 X"的战略决策写成可机器观察的收敛判据（OpenClaw 升级三判据、Observer flip §9.1 是范式），拒绝日历驱动与情绪驱动。
5. **测试三层 + sabotage 验证**：任何防御必须被反向验证"真的在防"（test the test），这是本项目工程可信度的根基，规模再大不放弃。
6. **每次事故 → 不变式 → 扫描器的三步跃迁**：point fix → meta-rule → scanner 的机器化路径继续作为事故处理的标准出口（bug 类 ≥2 次演出达机器化门槛的惯例保持）。
7. **上游依赖的 tripwire 化**：对 OpenClaw 及未来任何重依赖，维持"版本差距/时间/判据"三维 tripwire + 定期评估文档，不裸奔也不盲升。

---

## 八、判据门控汇总表（本纲领的决策中枢）

| 门 | 决策 | 判据（全满足才触发） | 状态 |
|----|------|----------------------|------|
| G1 | Observer shadow→on flip | design doc §9.1 预注册三判据（C1 零系统性 FP / C2 ≥1 TP 或干净周 / C3 成本可持续） | shadow 周中，~7/7 决策 |
| G2 | 第二实例 PoC 启动 | H1-B 首批 config 化合并 + cross_os_quirk_scanner 持续 0 violations + minimal_runtime golden 跨机 MATCH | ✅ 前置满足·Linux 实证（V37.9.253，2026-07-07：三前置 + external_dogfood 在 Linux x86_64 全绿，C1 选型记录已出，C2 真跑待 Q4 host） |
| G3 | observer-engine 抽包 | ≥1 真实外部需求信号（issue/邮件/合作询问明确要"装到我的系统"） | 未触发 |
| G4 | 商业化评估启动 | ≥3 独立组织主动 inbound | 未触发 |
| G5 | 超出个人半径的形态（团队/资金/托管） | G4 满足 + 连续两季度 inbound 不衰减 + 用户明确决策 | 未触发 |
| G6 | OpenClaw 升级 | eval doc §17 三收敛判据（连续 2 stable 无 SQLite 迁移 PR / 周稳定 ≤1 / node ≥22.19） | 跟踪中 |
| G7 | MCP/协议兼容层 | 工具治理引擎出现 ≥1 个 MCP 生态真实消费需求 | 未触发 |
| G8 | engine 1.0 semver 承诺 | 外部 issue/PR 存在 + API 连续 2 minor 无 breaking | 未触发 |

---

## 九、风险登记（诚实）

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | 单点人力（bus factor = 1） | 项目停摆 | 全量文档化 + AI 协作可复现（已是论文主题）；第二实例降低单机风险 |
| R2 | OpenClaw 上游结构性迁移（6.x SQLite/插件外部化） | 升级单向门 | G6 判据 + tripwire + 每周监控（已建制） |
| R3 | Observer 研究失败（held-out recall 非平凡失败） | 论文 #2 弱化 | 负面结果本身可发表（预注册方法学使 negative result 有效）；bench 与 taxonomy 价值独立于 Observer 成败 |
| R4 | 复杂度失控 | 意外率回升 | 日落法 + 复杂度预算 + 年度净退役目标（H1-E） |
| R5 | 生态冷启动失败（无人采用） | Stage 3 采用半边落空 | B1 季度指标诚实复盘；两季度零增长触发策略反思；论文轨独立于采用轨 |
| R6 | 模型市场剧变（provider 消亡/价格/质量） | 能力平面波动 | 多 provider + FALLBACK_ORDER + 验证四档已是对冲机制；主力切换有 A2/B1+回滚 runbook 先例 |
| R7 | 个人时间/精力波动 | 节奏中断 | 判据门控天然容忍暂停（条件不满足就不做）；B0 自动化保底运转 |
| R8 | 窗口期错失（T2 领域被大厂占领心智） | 话语权空间收窄 | H1 前置研究收官 + H2 前置论文 #2——研究输出在 2027 年内完成是硬节奏要求 |

---

## 十、落实建议（季度粒度的第一步行动）

> 只写"下一步可执行动作"，完成后由季度复核滚动追加。已完成项不回填此表（活在 changelog）。

### 2026 Q3（立即开始）

1. **Observer flip 决策**（~7/7，G1 判据，需 Mac Mini score_history 数据）→ flip 后两周精度观察 → 新 TP/FP case 回灌 ground-truth/bench。
2. **PA 耦合机器化盘点**：扫描个人配置面（路径/占位号码/中文 prompt/macOS 假设）→ 产出 `docs/pa_coupling_inventory.md` + config 化 backlog 分级（低/中/高 blast radius）→ 低风险批次落地。
3. **Mac Mini 证据刷新**：`slo_benchmark.py --save` 真实数据重生成入库（评审2 P0 收尾）。
4. 论文 #2 数据表设计（detection latency / live precision / held-out recall 的采集口径预注册——镜像 §9.1 方法学）。
5. 季度复核 #1（9 月底）：影响力指标基线首次记录 + 本纲领增量修订。

### 2026 Q4

1. **第二实例 PoC**（G2 判据满足后）：Linux 容器最小闭环 + portability report 文章。
2. **论文 #2 草稿**（Observer 生产数据 + 诚实 negative result 处理）。
3. ontology-engine 0.2.0（config-injection 使用反馈吸收 + project-scoped PyPI token 发版流程）。
4. 2026 年度复杂度预算复盘 + 净退役清单。

### 2027 H1

1. 论文 #2 投稿（arXiv 直发 + venue 按 #1 经验选择）。
2. bench 社区推广（artifact track / awesome lists / 邀请外部 case）。
3. G3/G8 判据首次正式评估（observer-engine / engine 1.0）。

### 2027 H2 – 2028

按 §5 H2-D/H2-E → H3 推进，全部判据门控；2028 年中纲领 2.0 修订。

### 每季度固定动作（复核清单）

- [ ] B1 影响力指标记录（引用/下载/star/外部 PR/inbound）→ status.json recent_changes
- [ ] 复杂度预算账本季度小结（四维度净变化）
- [ ] 判据门控表（§8）逐门核对状态
- [ ] 本纲领增量修订（只追加"修订记录"，不改写历史判断）
- [ ] 反目标自查（§4.4）：本季度是否有违反项

---

## 十一、维护契约与文档关系

### 11.1 与既有文档的分工（一物一形）

| 文档 | 角色 | 与本纲领关系 |
|------|------|--------------|
| 本纲领 | 中长期方向 + 判据 | 单一真理源：地平线/门控/反目标 |
| `docs/charter_execution_plan_20260705.md` | 任务分解 + 季度复核运行手册 | 本纲领的"怎么执行"（H1 任务分解 + 五项固定复核协议）；随本纲领季度滚动 |
| `docs/strategic_review_20260403.md` | 历史文献（导师 V1/V2/V3） | 本纲领的前身；V1/V2 已完成，V3 收口进 H1/H2，不再单独滚动 |
| `status.json` | 当前执行态 | 周/日粒度，本纲领不复制 |
| `CLAUDE.md` | 操作手册 + 近期 changelog | 战略定位区放本纲领指针 |
| `docs/llm_observer_design.md` 等 design doc | 单项研究/设计细节 | 本纲领只引用 |
| `docs/complexity_budget.md` | 复杂度预算约定 | H1-E/季度复核的执行依据 |

### 11.2 修订规则

- **节奏**：季度复核（随季度固定动作）；重大外部信号（论文接收/拒稿、评审、首个外部采用、上游剧变）触发增量修订。
- **方式**：只在下方"修订记录"追加条目 + 正文最小增量修改；历史判断（尤其 §2 趋势置信度）不回改——判断错了就在修订记录里承认，这本身是方法论的一部分。
- **废止条件**：若项目方向发生纲领级转向（如 G5 触发团队化），本纲领由 2.0 版整体接替，1.x 冻结存档。

### 修订记录

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-07-05 | 初版确立（V37.9.246）。上承 strategic_review_20260403（V1/V2 完成收口），确立双轨战略 + 三地平线 + 8 判据门 + 4 业务线 + 8 风险 + 季度复核制。 |

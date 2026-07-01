# LLM-Observer 设计文档 — 机械化人眼（研究攻关 #1）

> 状态：**design doc · Stage 0**（方向已确立，本文档是第一份产物，零代码）
> 启动：2026-06-29（V37.9.192 确立宪法级研究攻关 #1 后第一个工作日）
> 锚点：论文 *When Errors Become Narratives* [arXiv:2606.14589](https://arxiv.org/abs/2606.14589) §4.4 / §5.2 / §5.6 / §6 / §7
> 北极星约束：日落法（CLAUDE.md 原则 #34）— Observer 是又一道接缝，设计第一问是"它能退役什么人工观察"
> 复杂度预算：本文档 files +1（设计文档=研究产物非 runtime 接缝），其余维度全不变，VERSION 0.37.9.71 不变

---

## 一、研究愿景与定位

### 1.1 靶心问题（论文 §5.2，也是论文最大的未解问题）

论文给出三个反直觉发现，其中第一个是本研究的靶心：

> **发现渠道分布：~70% 的静默故障最终是被"人以用户视角看产品"发现的，不是被单测、健康检查、治理审计发现的——后三者在大多数事故中全程绿灯。**

具体到数字（论文 §5.2 + study cutoff）：系统跑 **4,286 单测 / 827 治理检查 / 19 项 preflight**，全绿穿过大多数事故；而**最高产的静默故障探测器是"一个人像用户一样看产品"**。论文把这件事制度化为每周 30 分钟用户视角观察仪式（CLAUDE.md 原则 #32），它持续跑赢自动化栈。

论文在 §5.2 把"机械化人眼"明确登记为**给研究者的开放问题**：

> "for researchers, the open problem is **mechanizing even part of what the human eye does here**."

并立即给出可行性证据 + 一条铁律：

> "our own step in that direction, a daily LLM 'observer' that critiques yesterday's outputs against quality heuristics, found real regressions (including a fabrication, and including two bugs in *itself*). The qualifier our experience adds: an LLM judging a *system's output for silent failure* is itself an LLM component of that system, inheriting every class in this taxonomy ... **so the judge needs the same governance, provenance hygiene, and sabotage validation as the components it judges.**"

**本研究 = 把这个开放问题往前推一步**：把现有的"每日 LLM observer"（quality-critique 小工具）升级为**有度量、可证伪、被 sabotage-validate 的 fail-plausible 检测器**，并把它打包成别人能跑的 bench。

### 1.2 三概念锚点各司其职（不是口号，是工程职责划分）

| 锚点 | 在本设计中的职责 | 操作含义 |
|------|------------------|----------|
| **fail-plausible** | **打击目标** | Observer 必须读**内容语义**（一段输出读起来可信但实际错误/编造），不是读状态码/时间戳/文件存在性。这是当前 `daily_observer.detect_anomalies()` 做不到的事（见 §2.2）。 |
| **audit-as-regression** | **理论授权** | 论文 §5.6 实证：治理审计对**新型**故障 0% 事前预防、87% 事后回归阻断——审计是回归引擎不是预测引擎。**推论：新型故障的预防必须来自"别处"。Observer 就是要成为那个"别处"**（缺失的预测/早探测引擎），它的存在理由正是审计在定义上做不到的。 |
| **seams-not-components** | **看哪里 + 警惕自己** | 论文 §7：最长潜伏故障住在**接缝**（部署拓扑/跨脚本契约/观察者-被观察者耦合），不在部件。Observer 该看**接缝 + 面向用户的输出**（部件已被 4,286 测试覆盖）。**同时**：Observer 自己是又一道接缝（§7 警告"加 guard 本身就加部件因而加接缝"），必须 read-only + 接受同等治理（§7.3）。 |

### 1.3 双产出：论文 #2 + 社区可跑 bench（深度 + 广度，一箭双雕）

研究攻关 #1 的产出目标同时回应两次外部评审点中的**同一对短板**（PA 耦合深 / 复杂度组合面大 / 可迁移性不足）：

- **深度（攻自己提出的开放问题）= 论文 #2**：把 taxonomy 从**描述性**（论文 #1 描述 5 类静默故障）升级为**预测性/预防性**（Observer 在用户之前捕获 fail-plausible）。这是"解掉自己论文的头条未解问题"——顶级专家的跃迁不在 LOC。
- **广度（真实迁移采用）= 社区可跑 bench**：一个别人能跑的 *silent-failure / fail-plausible 检测 bench*。种子是 `reliability_bench.py`（17 场景，已是"行业可引用测试集"方向）。bench 让 Observer 的能力变成"别人能验证、能复现、能贡献新案例"的东西——直接缩"可迁移性/个人耦合"短板。

### 1.4 关键定位：不是从零造，是升级既有 daily_observer（一物一形）

**项目已经跑着一个每日 LLM observer**（`daily_observer.py`，V37.9.84 起，06:30 cron）。它已经：
- 调 LLM-as-judge 评 yesterday 的 evening/dream/deep_dive 输出（5 维度：信息密度/准确性风险/主题多样性/可行动性/格式规范，`CRITIQUE_SYSTEM` prompt）
- 发现过真 fabrication（2026-05-28 "心理导航→光子学"强行跨域联想，催生 hallucination_guards LEVEL_6）
- 发现过它**自己的**两个 bug（V37.9.92 path fallback + V37.9.93 sampling 截断幻觉）—— 这正是论文 §5.2 "two bugs in itself" 的出处

所以本研究**绝不另起一个并行 observer**（那违反日落法"一物一形"+ 制造观察者-观察者接缝，原则 #34）。它是对 `daily_observer.py` 的**定向升级**，把它从"人看的 prose 质量批评" → "机器可读、有 ground-truth 度量、被 sabotage 验证的 fail-plausible 检测器"。差距矩阵见 §2.2。

---

## 二、问题精确化

### 2.1 fail-plausible 的工程定义（论文 §4.4）

> **fail-plausible**：系统把一个内部错误**转化**为连贯、上下文恰当、但错误的输出。它是 gray failure 的 LLM 时代升级——gray failure 让故障探测器**收不到信号**；fail-plausible 给人**喂一个伪造的信号**。观察者不只是失明，而是被失败本身有说服力地欺骗。

论文 §4.4 的四个 D 类事故（语料库里能找到对应 case doc）：
- **D1 编造平台危机**（`dream_self_referential_hallucination_case.md`）：UTF-16 surrogate → json.dump 截断 → 400 错误页 → log 写 stdout 被命令替换当"信号"捕获 → reduce LLM 把错误码当主题，编造"Hugging Face 平台危机"分析推给用户。**一个 `>&2` 切断整条链**。
- **D2 编造修复指令**（`pa_alert_contamination_case.md`）：watchdog 告警被存进 chat history 当 assistant 消息 → 36 分钟后用户问无关架构问题 → 模型跨污染上下文编造"我收到系统告警后续任务"+ 让用户去 macOS 给 cron 二进制开 Full Disk Access。
- **D3 编造成功**（`kb_review_silent_degradation_case.md` 类）：LLM 调用失败 → fallback 机械行过滤器把容器标题当 review 内容吐出 + 无条件写 `"llm": true`。**fabrication 不需要模型——一个制造 plausible 形状输出的 fallback 路径就是用 shell 实现的幻觉。**
- **D4 编造发布**（dream/evening 类）：把"当日高对齐论文"清单当上下文 → 推断项目"一定发布了" → 宣布一个只存在于 changelog 的内部版本号的社区发布。**provenance-free 的真实但无标注上下文 = fabrication 燃料。**

**共同结构 = 污染链**：一个 A/B/C 类失败把非信号内容存进下游 LLM 期待信号的地方 → LLM 履行其功能（流畅连贯补全）→ 输出**继承了健康的形式 + 失败的内容**。

### 2.2 当前 daily_observer 的能力边界（结构 vs 语义 gap）

| 能力 | daily_observer 现状 | fail-plausible 检测需要 |
|------|---------------------|-------------------------|
| 文件存在/缺失 | ✅ `detect_anomalies()` (line 596) | — |
| 输出长度过薄 | ✅ thin_output < 200 chars | — |
| job 状态码/时效 | ✅ `scan_job_statuses()` stale > 7d | — |
| deep_dive 降级率 | ✅ `scan_deep_dive_modes()` (V37.9.168) | — |
| **读内容语义判断"可信但错误"** | ❌ **完全没有** | ✅ **本研究的核心 gap** |
| LLM 质量批评 | 🟡 `call_llm_critique()` (line 808) 5 维度——但**输出是给人看的 markdown prose，不是机器可读 verdict；不针对 fail-plausible；未被 ground-truth 验证** | ✅ machine-actionable verdict + 度量 |

**关键洞察**：daily_observer 的 LLM critique **已经在读内容**（它看 evening/dream/deep_dive 的实际内容），但它产出的是"给人读的散文评分"，不是"这段输出是 fail-plausible，证据是 X，置信度 Y"的机器信号；它也从没在 22 个历史事故上被验证过能不能抓到那些真实的 fail-plausible。**gap 不在"接没接 LLM"，在"判断是否被结构化、是否针对 fail-plausible、是否被度量验证"。**

### 2.3 Observer 该看哪里（seams-not-components）

Observer 的输入面 = **面向用户的输出 + 接缝**，不是部件：
- ✅ 看：推送给用户的 evening/dream/deep_dive/radar/各 job 内容（D 类全在这里发生）
- ✅ 看：跨脚本契约的产物（如 LLM 输出 → parser → 推送，positional parse 漂移在这里）
- ✅ 看：声明态 vs 运行态接缝的可观测产物（已部分由 convergence engine + daily_observer 结构检查覆盖）
- ❌ 不看：部件内部逻辑（4,286 单测的职责，Observer 重复 = 加接缝违日落法）

---

## 三、子目标 1：评分函数（如何判定一段 LLM 输出是 fail-plausible/静默故障）

### 3.1 核心设计原则：分层、可解释、可证伪

评分函数**不是单个 LLM 黑箱打分**。它是**两层管道**——确定性 pre-filter（便宜、可解释、零 LLM）→ LLM-judge（语义、贵、只在 pre-filter 命中或高风险时触发）。这呼应论文的防御哲学（hygiene → provenance → prompt-guard，prompt 是最后一层不是第一层 §4.4）。

```
一段面向用户的输出 (evening/dream/deep_dive/job content)
        │
        ▼
┌─ Layer 1: 确定性信号 (zero-LLM, 可解释, 复用 seeds) ─────────────┐
│  S1 pollution-signal   错误码/HTTP状态文本/工具名/"Bad JSON"/    │
│                        "Waiting Ns before retry" 出现在用户内容里 │
│                        (D1 的指纹: 错误码当"信号")               │
│  S2 credibility-mismatch  低档来源(📝博客/💬社媒)被高档措辞包装   │
│                        ("研究表明"/"已证明"/"[强证据]") —         │
│                        复用 source_credibility.get_credibility()  │
│  S3 fabrication-phrase  hallucination_guards.get_blocked_phrases()│
│                        的血案精确字眼 (如 "OpenClaw v26"/编造版本号)│
│  S4 provenance-gap     跨域因果断言无 [强证据]/[弱关联] 标注 —     │
│                        复用 hallucination_guards LEVEL_6 契约      │
│  S5 coherence-structural  signal 段与 action 段主题不匹配         │
│                        (D1 用户正是靠这个发现的: "信号"和"行动项不符")│
└──────────────────────────────────────────────────────────────────┘
        │  任一命中 OR 高风险 job  → 升级 Layer 2; 全不命中 → verdict=clean (便宜路径)
        ▼
┌─ Layer 2: LLM-judge (语义, 复用 call_llm_critique infra) ─────────┐
│  专门的 FAIL_PLAUSIBLE_SYSTEM prompt (区别于现有 CRITIQUE_SYSTEM): │
│   - grounding: 每个事实断言能否在源材料找到支撑?                  │
│   - intent-alignment: 系统被要求做 X, 用户没问 X? (D2 指纹)        │
│   - pollution-evidence: 内容里有没有系统 artifact (错误码/HTTP/    │
│     工具名) 被当成外部信号? (D1 指纹)                              │
│   - fabricated-success: 是不是 fallback 制造的 plausible 形状空壳? │
│     (D3 指纹: 容器标题当内容 / boilerplate 重复)                   │
│   注入 hallucination_guards LEVEL_6 + source_credibility 块作约束  │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
  Verdict (machine-actionable, 见 §3.4)
```

### 3.2 为什么 Layer 1 在前（成本 + 可证伪 + 防 Observer 自身幻觉）

- **成本**：大多数日子的输出是干净的；Layer 1 全不命中就走便宜路径不调 LLM（每天数十 job × LLM-judge 不可持续）。
- **可解释**：S1-S5 是确定性规则，能精确说"为什么 flag"（"第 14 行出现 `Error code: 400` 在 signal 段"），不是 LLM 不可审计的直觉。
- **防 Observer 幻觉**（§5.2 铁律）：V37.9.93 血案——Observer 自己的 LLM 因为 sampling 截断**幻觉了一个 truncation**。Layer 1 确定性信号是 Layer 2 LLM 判断的**锚**，verdict 必须引用具体证据（line/phrase），不接受无证据的 LLM "我觉得这段可疑"。

### 3.3 复用 seeds（不重新造，一物一形）

| seed | 复用方式 |
|------|----------|
| `source_credibility.py` (5 档) | S2 credibility-mismatch + Layer 2 prompt 注入 `format_credibility_block()` |
| `hallucination_guards.py` (6 档, LEVEL_6 [强证据]/[弱关联]) | S3 blocked-phrases + S4 provenance-gap + Layer 2 prompt 注入 `get_guard("LEVEL_6_...")` |
| `daily_observer.call_llm_critique()` (line 808) | Layer 2 直接复用 HTTP-to-proxy infra (127.0.0.1:5002, 90s timeout, JSON payload) |
| `daily_observer.scan_push_outputs()` + `_read_file_sample()` (V37.9.93 smart head+tail) | Observer 的输入采集（注意 sampling 标注必须传给 Layer 2 防 V37.9.93 重演） |

### 3.4 输出契约（machine-actionable，接入 daily_observer anomalies 列表）

verdict 是**结构化对象**，不是 prose。复用 `daily_observer.detect_anomalies()` 已有的多态 anomaly 结构 `{severity, category, message}`，新增 category：

```python
# 示例 verdict (设计草案, 非最终 schema)
{
  "severity": "HIGH",                  # 沿用 HIGH/MED/LOW
  "category": "fail_plausible",        # 新 category (also: pollution_signal / unsupported_claim / fabricated_success)
  "artifact": "dream/2026-06-29.md",   # 哪个面向用户的输出
  "evidence": [                        # 必须有证据 (可证伪, 防 Observer 幻觉)
    {"layer": 1, "signal": "pollution-signal", "locus": "L14", "snippet": "Error code: 400 Bad JSON"},
    {"layer": 2, "judge": "pollution-evidence", "rationale": "..."}
  ],
  "confidence": 0.0_to_1.0,            # 校准用 (§5.5 度量)
  "message": "..."                     # 人读摘要 (向后兼容现有 report)
}
```

接入点：`detect_anomalies()` 之后、`build_report_markdown()` 之前 merge 进 anomalies 列表（§六）。fail_plausible verdict 与"job 失败"/"输出过薄"享受同等 severity rollup → 进每日 score + status.json + 推送告警。

### 3.5 评分函数本身必须可证伪（不是 tautology）

论文 §6 pillar 2（sabotage validation）+ 项目铁律："在静默故障 regime 里，一个未验证的探测器和一个空检测器无法区分"。所以评分函数的每条 S1-S5 + Layer 2 都必须：
- 对**已知 fail-plausible 案例**（22 corpus 的 D 类）命中（regression 集，§四 + §五）
- 对**已知干净输出**不命中（false-positive 控制）
- sabotage：故意改坏一条 S 规则 → 它守护的 case 立即漏检（证明它真有效）

---

## 四、子目标 2：ground-truth 标注协议（22 postmortem 语料库作带标注验证集）

### 4.1 独有资产：22 corpus 已经半结构化

语料库（`ontology/docs/cases/`，27 个 .md，含 22 个论文计入的 postmortem）**已经**带强结构（postmortem 协议强制，论文 §3.2）：
- TL;DR / 事件摘要（~100% present）
- **trigger / amplifier / concealer** 三层根因（~100% present）
- 因果链 ASCII 图（时间×层级×逻辑×架构）
- 条件组合分析（why now not before）
- 发现渠道（部分隐含在叙事里）

这是**全世界唯一**针对一个生产 LLM agent runtime 的纵向、带因果链标注的静默故障语料库——是 Observer 验证集的天然 ground truth。

### 4.2 新增标注 schema（在既有 case doc 上加机器可读 front-matter / 旁路标注表）

为每个 case 补一组**机器可读标签**（候选：放进 `data_inventory.md` 的扩展表，或每 case 的 YAML front-matter，Stage 1 决定，倾向单表避免 27 处漂移=一物一形）：

| 标签字段 | 取值 | 用途 |
|----------|------|------|
| `taxonomy_class` | A / B / C / D / E | 对齐论文 5 类 |
| `fail_plausible` | yes / no / partial | Observer 主打击目标筛选 |
| `llm_fabrication` | yes / no | 区分"LLM 编造" vs "结构失败"（D3 是 shell fabrication 也算 yes） |
| `observable_artifact` | 文件/消息路径 + 是否面向用户 | Observer **能不能看到**这个失败（看不到的不在 Observer 职责内，诚实划界） |
| `expected_signal` | S1..S5 中哪些应命中 | regression 测试断言（Observer 跑这个 case 的 artifact 必须 flag） |
| `discovery_channel` | user-view / check / log-forensics / self-observation | 验证论文 §5.2 的 ~70% + 标出"哪些本可被 Observer 抢先抓到" |
| `observer_in_scope` | yes / no | **诚实边界**：60-day TCC sandbox（E 类，无面向用户 artifact）Observer 看不到 → out of scope，不假装能抓 |

### 4.3 标注协议（诚实登记 κ 限制，呼应论文 §3.3 / §8）

论文 §3.3 + §8 诚实承认：分类由两个系统操作者（人 + AI）做，**无独立标注者，不报 κ**。Observer 的 ground-truth 标注继承同一诚实约束 + 一条缓解：
- **缓解（论文 §8 已用的论据）**：标签是**load-bearing**（每条标签驱动一个真 sabotage 测试，其结果是客观的——"Observer 跑这个 artifact 有没有 flag"是二值事实），不是纯描述性。这把"主观分类"风险降到"分类驱动的客观测试是否通过"。
- **双标候选**：fail_plausible 与 observer_in_scope 两个最关键字段，可由"人标 + LLM 独立标 + 不一致人工裁决"产生**轻量一致性数字**（非正式 κ，诚实标注为"single-system, two-annotator-of-which-one-is-AI"）。

### 4.4 验证集划分（镜像 adversarial_chaos_audit Category A/B）

- **Regression set（Category A，已知，~10 case）**：所有 `fail_plausible=yes` + `observer_in_scope=yes` 的 case（种子：dream HF-crisis / PA FDA-contamination / dream quota-stubs / kb_review header-fabrication / echo-chamber）。Observer 跑这些 case 的 artifact **必须 100% flag**（defense rate）。
- **Held-out/Exploratory set（Category B，探索，~6 case）**：用户研究/未来事故里新发现的 fail-plausible 模式，Observer 没专门为它们设计过 → 度量 **false-negative rate**（盲区）。每个盲区 → 反哺评分函数新 S 规则或新 guard level。

### 4.5 黄金种子案例（Stage 1 先标这 5 个）

| case | 类 | Observer 应检出 | 对应 S 信号 |
|------|----|----|----|
| `dream_self_referential_hallucination_case.md` | D1 | 错误码当信号 + signal/action 主题不符 | S1 pollution + S5 coherence |
| `pa_alert_contamination_case.md` | D2 | 系统 artifact(FDA/cron) 出现在无关回答 + intent 不对齐 | S1 + Layer2 intent-alignment |
| `dream_quota_blast_radius_case.md` | D3 | boilerplate "详见原文" ×5 + 无信号内容 | S5 + Layer2 fabricated-success |
| `kb_review_silent_degradation_case.md` | D3 | 容器标题当 review 内容 + `"llm":true` 不一致 | Layer2 fabricated-success + 结构交叉 |
| `pa_echo_chamber_case.md` | D(弱) | 过度等价("异曲同工") + 声称已存 KB 实未存 | S4 provenance-gap + Layer2 grounding |

---

## 五、子目标 3：Observer 自验证回路（sabotage-validate，§5.2 铁律）

### 5.1 §5.2 铁律：Observer 自己是 LLM 组件，继承全 taxonomy

论文 §5.2 + V37.9.92/93 血案铁证：现有 daily_observer 自己就**带了一个 Class B path bug（5 天）+ 一个 sampling 幻觉（V37.9.93）**。所以 Observer 必须接受**和它评判的组件同等**的治理 / provenance hygiene / sabotage validation。这是设计的硬约束，不是 nice-to-have。

### 5.2 复用 adversarial_chaos_audit 框架（一物一形，不新造 harness）

`ontology/tests/adversarial_chaos_audit.py` 已是成熟的 sabotage harness（16 场景，Category A 回归 10 + Category B 探索 6，`file_mutation` 上下文管理器 + baseline→mutate→audit→restore→evaluate + git-clean 防护）。Observer 的自验证**复用这个框架**，新增 Observer-flavored 场景：

```
Observer self-validation (复用 adversarial_chaos_audit 模式):
  Category A (regression, expected_catch=True):
    OBS-A1..A5: 把 §4.5 黄金 case 的 artifact 喂给 Observer → 必须 flag fail_plausible
    OBS-A6: sabotage S1 pollution-signal 检测 → dream HF-crisis case 立即漏检 (证明 S1 真有效)
    OBS-A7: sabotage source_credibility 复用 → credibility-mismatch 漏检
    ...
  Category B (exploratory, expected_catch 度量盲区):
    OBS-B1..B6: 新 fail-plausible 变体 (伪造会议名/听起来对的作者名/...) → 量 false-negative
```

### 5.3 Observer 自己的治理（防 V37.9.92/93 重演）

- **provenance hygiene**：Observer 读输入用 `_read_file_sample()` 的 smart head+tail，**sampling 标注必须显式传给 Layer 2 LLM**（V37.9.93 的修复：CRITIQUE_SYSTEM 已加 sampling 警告，FAIL_PLAUSIBLE_SYSTEM 必须继承）。
- **path 健壮性**：Observer 任何文件/registry 解析走既有 `_resolve_*` 多候选 + FAIL-OPEN（V37.9.92 修复 + INV-CROSS-ENV-PATH-001 scanner 已覆盖类）。
- **read-only 铁律**（§7.3 + MR-23 audit-observes-never-mutates）：Observer **绝不修改它观察的输出**，只产 verdict + 告警。convergence engine 血案（observer mutates observed → 三次 crontab 重复）是反面教材。

### 5.4 度量（Observer 的 scorecard，本身是论文 #2 的核心数据）

| 指标 | 定义 | 目标 |
|------|------|------|
| **defense rate** | Category A regression case 被 flag 的比例 | →100%（sabotage 守护） |
| **false-negative rate** | Category B exploratory 盲区比例 | 诚实报告 + 每个盲区反哺新规则 |
| **false-positive rate** | 干净输出被误 flag 的比例 | 低到不制造告警噪声（原则 #32 用户视角：噪声本身是问题） |
| **confidence calibration** | verdict.confidence vs 实际正确率 | 校准曲线（避免 overconfident 幻觉） |
| **detection latency** | Observer 抢在用户之前几小时/天发现 | 论文 #2 的 killer 数字：把 §5.1 的 latency 从"人发现"提前 |

---

## 六、子目标 4：与现有每日 observer job 的关系（扩展非另起）

### 6.1 干净接缝（已由 Explore 验证，零 daily_observer 重写）

`daily_observer.run()` (line 1208) 编排已经是：`scan → detect_anomalies → LLM critique → build_report → score → status`。fail-plausible 检测插在**两个已有步骤之间**，不改编排：

```python
# daily_observer.run() 内, detect_anomalies() 之后, build_report_markdown() 之前:
anomalies = detect_anomalies(job_statuses, push_outputs, source_sections, deep_dive_modes)
# ── 新增 (本研究) ──
fp_verdicts = detect_fail_plausible(push_outputs, source_sections, target_date, llm_caller)  # §三 两层管道
anomalies.extend(fp_verdicts)   # 同 {severity, category, ...} 结构, 无 schema break
# ── 下游零改动 ──
report = build_report_markdown(..., anomalies, ...)
```

### 6.2 向后兼容扩展点

- `anomalies` 列表多态：新 category（`fail_plausible` / `pollution_signal` / `unsupported_claim` / `fabricated_success`）自然渲染进 `build_report_markdown()` 的 anomalies 段，零格式改动。
- `score_history.jsonl`：新增字段 `fp_high` / `fp_med`（JSONL append，旧 reader 不破）。
- `status.json` `quality.observer`：`_write_observer_to_status()` (V37.9.92) 加 fail_plausible 计数字段（PA/health_check 可见）。

### 6.3 为什么不另起新 job（日落法 + 论文 §7）

另起一个并行 "fail-plausible observer" cron = 新脚本 + 新 last_run + 新 cron + 新 watchdog 契约 + **观察者-观察者接缝**。日落法（原则 #34）+ 论文 §7（"加 guard 本身加部件因而加接缝"）直接否决。扩展既有 = 一物一形 + 复用 daily_observer 已有的采集/采样/推送/status/trend 全套。

---

## 七、子目标 5：日落法第一问 — 它能退役什么人工观察？

### 7.1 退役目标（诚实、具体、有限）

Observer **能机械化**周一 30min 用户视角观察仪式（原则 #32）的**部分维度**——具体是"准确性风险 / 信息密度"两维里**可结构化的 fail-plausible 子集**：
- 退役：人工逐条读 evening/dream/deep_dive 找"这段读起来像但其实是错的/编造的"（D 类）
- 退役：人工核对"signal 段和 action 段主题对不对得上"（D1 用户当年正是手工做的）

具体收益（论文 #2 的论点）：把 §5.1 的 detection latency（fail-plausible 子集）从"等到周一人看"提前到"次日 06:30 Observer 自动 flag"。

### 7.2 诚实边界：Observer 退役不了全部人工观察（audit-as-regression 的自我应用）

论文 §5.6 的逻辑**对 Observer 自己也成立**：Observer 是为已知 fail-plausible 模式训练/设计的检测器 → 它对**新型** fail-plausible 模式同样会 0% 事前预防（这就是 Category B 盲区）。所以：
- **不退役**：周一用户视角观察的**其余维度**（告警噪声 / 推送延迟 / 整体感知）+ **novel 模式发现**（论文 §5.6：novel 类预防来自人眼/对抗审计/目标环境暴露，不来自任何回归引擎，Observer 也是回归引擎）。
- Observer 的诚实定位 = "把人眼从**已知** fail-plausible 模式的重复扫描里解放出来，让人眼专注于**新型**模式"——这正是 audit-as-regression 锚点的操作含义。

### 7.3 Observer 是又一道接缝（§7 自我警告）

每加一个 Observer 检测维度 = 加一个部件 = 加接缝（§7）。设计纪律：
- **read-only 铁律**（§5.3 + MR-23）：绝不 mutate 观察对象。
- **复用不新造**（§3.3/§5.2/§6.3）：seeds + adversarial harness + daily_observer 全复用，净新增 runtime 机制趋近"一个 detect_fail_plausible 函数 + 一组 sabotage 场景"。
- **复杂度预算**（原则 #34 / V37.9.150）：每加一条 S 规则先问"它退役了哪个人工核对动作 / 它守护的历史 case 是哪个"——earned complexity（有血案 lineage）而非投机。

### 7.4 日落法第一问的答案（一句话）

> Observer 退役"人工逐条扫已知 fail-plausible 模式"，**不**退役"人发现新型模式"；它把人眼从回归性扫描解放出来，专注于预测引擎定义上做不到的新型故障——这正是它存在的理由（audit-as-regression）。

---

## 八、架构总览与数据流

```
                        每日 06:30 cron (daily_observer.sh, 复用)
                                  │
                                  ▼
  ┌────────────────────── daily_observer.run() (line 1208, 复用编排) ──────────────────────┐
  │                                                                                          │
  │  scan_job_statuses / scan_push_outputs / scan_source_sections / scan_deep_dive_modes     │
  │         │ (结构信号, 复用)                                                                │
  │         ▼                                                                                 │
  │  detect_anomalies()  ──►  anomalies[] {severity,category,message}  (结构, 复用)           │
  │         │                                                                                 │
  │         ▼  ◄────────────────────── 本研究新增 ──────────────────────────────────────┐    │
  │  detect_fail_plausible(push_outputs, source_sections, ...) :                         │    │
  │     Layer 1 (zero-LLM): S1 pollution · S2 credibility-mismatch · S3 fab-phrase ·     │    │
  │                          S4 provenance-gap · S5 coherence  (复用 seeds)              │    │
  │              │ 命中/高风险 → Layer 2                                                 │    │
  │     Layer 2 (LLM-judge): FAIL_PLAUSIBLE_SYSTEM (grounding/intent/pollution/fab)      │    │
  │                          复用 call_llm_critique infra + LEVEL_6 + credibility 注入    │    │
  │              │                                                                       │    │
  │              ▼  fp_verdicts[] {severity,category=fail_plausible,artifact,evidence[],  │    │
  │                                confidence,message}  (machine-actionable, 可证伪)      │    │
  │  anomalies.extend(fp_verdicts) ─────────────────────────────────────────────────────┘    │
  │         │                                                                                 │
  │         ▼                                                                                 │
  │  build_report_markdown / append_score_history(+fp_high/fp_med) /                          │
  │  _write_observer_to_status(+fail_plausible 计数)  (复用, 向后兼容扩展)                     │
  └───────────────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼  fail_plausible HIGH → notify.sh 告警 (复用推送)
                                  ▼  verdict + 证据 → 用户/PA 可见

  ┌──────────── 离线: Observer 自验证回路 (复用 adversarial_chaos_audit 框架) ────────────┐
  │  ground-truth: 22 corpus 标注集 (§四) — Category A regression + Category B exploratory │
  │  sabotage-validate: 注入已知 fail-plausible → 必抓; 改坏 S 规则 → 守护的 case 漏检      │
  │  scorecard: defense rate / FN rate / FP rate / calibration / detection latency (§5.4)  │
  │           └──► 论文 #2 核心数据 + bench (reliability_bench 扩展, 社区可跑)             │
  └──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 九、实施路径（分阶段 · 进度见状态列，Stage 0-5 + 6 chunk 1 已完成）

| Stage | 内容 | 成功定义 | 状态 |
|-------|------|----------|------------|
| **Stage 0** ✅ | 本设计文档（5 子目标拆解 + 锚点 + 边界） | design doc 入库，方向可执行 | ✅ V37.9.193 |
| **Stage 1** ✅ | ground-truth 标注集：22 corpus 打 §4.2 schema 标签（单表，一物一形）+ 5 黄金种子 | 标注表入库，Category A/B 划分清晰，observer_in_scope 诚实划界 | ✅ V37.9.194 |
| **Stage 2** ✅ | 确定性 pre-filter（S1-S5，零 LLM，复用 seeds）+ 单测 + sabotage 守卫 | S1-S5 对 5 黄金 case 命中、干净输出不命中、sabotage 改坏即漏检 | ✅ V37.9.195 |
| **Stage 3** ✅ | LLM-judge Layer 2（FAIL_PLAUSIBLE_SYSTEM，复用 call_llm_critique）+ 注入 LEVEL_6/credibility | verdict 结构化 + 引用证据 + 防 V37.9.93 sampling 幻觉 | ✅ V37.9.196 |
| **Stage 4** ✅ | self-validation harness（复用 adversarial_chaos_audit 模式，OBS-A/B 场景）+ scorecard | defense rate / FN / FP / calibration 可度量；Category A →100% | ✅ V37.9.197 |
| **Stage 5** ✅ | 接入 `daily_observer.run()`（detect_fail_plausible，§6.1）+ score/status 向后兼容扩展，**shadow-first**（`OBSERVER_FP_MODE=shadow` 默认，观察性不影响评分/告警） | daily_observer 次日报告含 fail_plausible 段，零下游破坏，Mac Mini E2E | ✅ V37.9.198（+199 shadow 抓真 FP 修 S5） |
| **Stage 5.1** 🟡 | flip `OBSERVER_FP_MODE` shadow→on（fp 进 anomalies → 影响评分/告警）+ force_judge 评估 | 决策框架见 §9.1（预注册 criteria），gated 于 shadow 周真实数据 | 🟡 shadow 周观察中（2026-06-30 起，~7/7） |
| **Stage 6** 🟡 | bench 化（silent-failure/fail-plausible 检测 bench，社区可跑）+ 论文 #2 草案 | 别人能 `pip`/clone 跑 bench；论文 #2 描述→预测的 scorecard | 🟡 chunk 1 ✅ V37.9.200（bench 社区化）；论文 #2 草案 gated 于 detection latency 生产数据 |

**Stage 0 交付 = 本文档**。后续 Stage 由独立 session 推进（每 Stage 走完整收工清单 + Mac Mini E2E，原则 #6/#9/#29）。

### 9.1 Stage 5.1 决策框架（shadow → on flip · 数据到达前预注册决策规则）

> **为什么现在写（数据未到就定规则）**：flip-on 是**有真实代价的决策**（`OBSERVER_FP_MODE=on` → fp verdict `.extend(anomalies)` → 影响 `overall_score` + 触发 Discord #observer 告警）。在看到 shadow 数据前**预注册决策规则**（pre-registration，正是论文 #2 自身的方法学纪律）防止 motivated reasoning（"想上线就把噪声说成可接受"）。镜像 ONTOLOGY_MODE / three_gate / INV-WA-001 / INV-DEEP-001 的 shadow→on 纪律——每次 flip 都该有 criteria。V37.9.199 已证明 shadow 会抓到真 FP（S5 评分字段误报），故 flip 是有噪声风险的真决策。**本节是 Stage 5.1 的"设计半"**（可在数据到达前做，gated 的只是数据本身，非决策结构，镜像 Stage 0 design-before-code）。

**观察对象（shadow 周真实产出的信号，非 corpus）**：
1. `score_history.jsonl` 的 7 天 `fp_high` / `fp_med` 计数序列（V37.9.198 `append_score_history` wiring）。
2. 每日报告的 `fail_plausible` 段（带 L1+L2 证据）—— 逐条人工判 **TP**（真 fail-plausible，如 dream_quota 类）vs **FP**（噪声，如 V37.9.199 的 S5 评分字段重复误报）。
3. `status.quality.observer.fail_plausible_high/med`。

**核心度量 = live precision**：把 §5.4 scorecard 的 `fp_rate` 从 corpus 应用到**生产真实数据**——shadow 周每条 fired verdict 分类 TP/FP。

**flip-on criteria（预注册，全部满足才 flip）**：
- **C1 · 零未修复系统性 FP**：无未修复的**系统性**误报模式（每天/多源复现，如 V37.9.199 的评分字段重复）。若 shadow 抓到新系统性 FP → **先在 shadow 修 detector**（+ sabotage 守卫 + `llm_observer_selfcheck` scorecard 复跑），**不带已知噪声 flip**。one-off 边缘 FP 可接受。
- **C2 · 信号有用性**：shadow 周 ≥1 条 TP **或** 干净周（0 fired）。**干净周也可 flip**——flip 一个干净探测器不增噪声（安全），只是价值暂未兑现；有 TP 则证明 on 模式会在用户之前告警（价值兑现）。**混合（有 TP + 有未修系统性 FP）→ 先走 C1 修 FP 再复评**。
- **C3 · 成本可持续**：Layer 2 LLM 调用量在 shadow 周可持续（cheap-path 生效：干净日 ≈0 调用，事故日与事故成正比，§3.2）。

**flip vs extend 逻辑**：
- C1+C2+C3 全满足 → flip `OBSERVER_FP_MODE=on`（Mac Mini env）→ Mac Mini E2E 观察首日 anomalies / `overall_score` / 告警。
- 发现系统性 FP → shadow 内修 detector（sabotage 守卫 + scorecard 复跑 defense 仍 100% / FP 仍 0%）→ extend shadow 一周 → 复评（V37.9.199 正是此路径）。
- FP-only 周（有 fired 全 FP，0 TP）→ 修 FP → extend（不 flip 一个只产噪声的探测器）。

**flip 后变化 + rollback**：
- on 模式：fp verdict 进 anomalies → 影响 `overall_score` + 触发告警 → **FP bar 高**。
- **rollback 即时可逆**：`OBSERVER_FP_MODE=shadow`（env flip，无需重启，next 06:30 cron 生效）。flip 后若 FP 风暴 → 立即回 shadow。flip 是**可逆决策**（非 irreversible）→ C1-C3 满足时可 lean toward flip，保留回退能力（同 three_gate shadow→on 的可逆纪律）。

**诚实边界（small-N）**：shadow 周样本小（多数日干净），live precision 估计噪声大 → 决策**不追求统计显著**，追求"无已知系统性噪声 + 探测器行为可解释"。同 §5.4 的 `detection_latency`/`calibration` 标 N/A 的同源诚实——生产小样本给不了置信区间，但给得了"有没有系统性 FP"这个可操作判据。

**force_judge 评估（flip 时一并决策，非本 Stage 前置）**：当前 shadow 用 cheap-path（Layer 2 仅 Layer 1 命中触发）。flip 时评估是否对高风险 synthesis（dream/deep_dive 大 LLM 输出）开 `force_judge`（catch novel D2/grounding，§五 scorecard 证 Category B 仅 Layer 2 能抓）——但 force_judge 增 LLM 成本须权衡 C3。**默认 flip 后维持 cheap-path**，force_judge 作独立后续评估（不与 flip 捆绑，避免一次改两个变量）。

---

## 十、测试与守卫策略

- **反向验证（sabotage）贯穿**（论文 §6 pillar 2 + 项目铁律）：每条 S 规则 + 每个 OBS 场景都必须证明"改坏即漏检，还原即全过"——否则探测器与空检测器无法区分。
- **Observer 自身治理**：倾向**用单测守 Observer 契约**（read-only / sampling-aware / path-robust / 证据非空），**不立新治理 INV** 除非原则 #21"什么坏了没人发现"判据成立（V37.9.166/186/188 三类判据 category A：test 已覆盖 → 不加 INV = 避 governance bloat，外部评审2"降组合复杂度"）。
- **三层测试**（原则 #15）：单测（S 规则纯函数）+ full_regression + Mac Mini E2E（用户视角看次日报告 fail_plausible 段是否真有用、有没有噪声）。

---

## 十一、风险与开放问题（诚实登记）

| 风险 | 评估 | 缓解 |
|------|------|------|
| ground-truth 主观性（无 κ） | 论文 §3.3/§8 已诚实承认 | 标签 load-bearing（驱动客观 sabotage 测试）+ 轻量双标候选（§4.3） |
| LLM-judge 成本/延迟 | 每天数十 artifact × LLM 不可持续 | 两层管道，Layer 1 便宜路径过滤，Layer 2 只在命中/高风险触发（§3.2） |
| Observer 自身幻觉（V37.9.93 重演） | 真实风险，已演出过 | verdict 必须引用确定性证据（§3.4）+ sampling-aware（§5.3）+ sabotage 验证 |
| **输入空间开放（vs Red Team #11 deferred 的同一困境）** | Red Team（unfinished #11）因"输入空间开放无 ground-truth 评分函数"deferred；Observer **部分缓解**——22 corpus 是封闭的带标注 ground truth，给了 regression 集一个可证伪的评分锚 | 但 novel 模式（Category B）仍无 ground truth → 诚实定位为"回归引擎+人眼补充"（§7.2），不假装解决开放输入空间 |
| Observer 是又一道接缝（§7） | 加部件=加接缝 | read-only + 复用不新造 + 复杂度预算每维度问"退役什么"（§7.3） |
| false-positive 噪声 | 误报本身是用户视角问题（原则 #32） | FP rate 作一等度量（§5.4）+ 两层管道降误报 |

**最大开放问题（留给后续 Stage + 论文 #2）**：评分函数能否在**held-out（Category B）** 上有非平凡的 recall——即 Observer 能不能抓到**没专门为它设计过**的 fail-plausible 模式？这是"描述→预测"跃迁的真正考验，也是论文 #2 的核心 claim 必须诚实回答的（如果只能抓 regression，那它只是回归引擎不是预测引擎——但即便如此，把 §5.2 人眼回归性扫描机械化本身也有真实价值）。

---

## 附录 A：与 unfinished item 0 的 5 子目标对应

| unfinished #0 子目标 | 本文档章节 |
|----------------------|------------|
| (1) 评分函数（判定 fail-plausible/静默故障） | §三 |
| (2) ground-truth 标注协议（22 postmortem 验证集） | §四 |
| (3) Observer 自验证回路（sabotage-validate，§5.2） | §五 |
| (4) 与现有每日 observer job 的关系（扩展非另起） | §六 |
| (5) 日落法第一问：退役什么人工观察 | §七 |

## 附录 B：引用锚点

- 论文 *When Errors Become Narratives* [arXiv:2606.14589](https://arxiv.org/abs/2606.14589)：§4.4（fail-plausible / D1-D4）、§5.2（discovery ~70% 人眼 + 开放问题 + judge 铁律）、§5.6（audit-as-regression 0%/87%）、§6 pillar 2/5（sabotage validation / LLM observer）、§7（seams-not-components / Sunset Law）、§8（threats / 无 κ）
- 代码：`daily_observer.py`（`detect_anomalies` L596 / `call_llm_critique` L808 / `_write_observer_to_status` V37.9.92 / `run` L1208）、`source_credibility.py`（5 档）、`hallucination_guards.py`（6 档，LEVEL_6 [强证据]/[弱关联]）、`reliability_bench.py`（17 场景，bench 种子）、`ontology/tests/adversarial_chaos_audit.py`（16 场景 Category A/B，self-validation 模板）
- 血案：`v37_9_92_observer_path_blood_case.md`（Observer 自身 path bug + sampling 幻觉）、§4.5 五黄金 fail-plausible case（§4.5 表）
- 原则：CLAUDE.md #34（日落法）/#32（周一用户视角观察）/#21（什么坏了没人发现）/#28（理解再动手）/#15（三层测试）；MR-23（audit-observes-never-mutates）

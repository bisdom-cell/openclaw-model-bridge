# Your Audit System Is Not a Prevention Tool — It's a Regression Engine

> 2026-04-20 | Stage 2 实证交付 | V37.9 audit 防御率 16/16

## TL;DR

我用 45 天在一个生产级 Agent Runtime 系统上运营了 53 个治理不变式 + 15 条元规则 + 13 个运行时扫描器。然后我做了件罕见的事：**对自己的 audit 系统做对抗性测试**，量化它的真实防御力。

结果让我重新理解了"审计"这个词：

- **事前预防率 = 0%**（15 个真实血案中没有一个被 audit 预先拦截）
- **事后回归率 = 87%**（13/15 血案的修复被 audit 固化，不会重复发生）
- **已知维度覆盖率 = 100%**（对抗测试 16/16 全部按预期工作）

这不是"审计失败"，是**审计工作方式的真相**。如果你在构建复杂系统的治理，这篇文章会省下你几个月的误期望。

---

## 第一个错觉：audit 能预防故障

大多数人谈"加审计"时，心里想的是"这样就不会出事了"。

**数据说不是。**

我逐一回填了过去 45 天的 15 个真实血案（`ontology/docs/audit_coverage_retrospective.md`）：

```
Q1 爆发前 audit 能否发现？
  ✅ 能:     0/15  (0%)
  ⚠️ 部分:   2/15  (13%)
  ❌ 不能:  13/15  (87%)
```

**13/15 是"audit 当时完全没有相关不变式"**。不是检测逻辑写差了，是**维度根本没被想到**。

每次真实故障都暴露一个此前审计从未考虑的维度：
- `HEARTBEAT.md 是 OpenClaw runtime 保留文件` — 从未被 audit 建模
- `shell log 写 stdout 被 $(cmd) 捕获污染 cache` — 从未被建模
- `LLM 输出严格位置解析级联错位` — 从未被建模
- `Gateway 宕机用 WhatsApp 告警 = 死循环` — 从未被建模

审计的盲区不是"具体检查粒度不够"，是**未知维度的发现速度落后于故障发生速度**。

## 第二个错觉：审计写得越多越安全

如果 audit 不能预防，那它的价值在哪？

```
Q3 补的不变式能防下次同类吗？
  🛡️ 完全: 5/15 (33%)
  🛡️ 一半: 8/15 (53%)
  ⚠️ 表层: 2/15 (13%)
```

**87% 的血案修复 = 至少一半的回归防御率**。每次故障变成一条锁定同类故障的不变式。audit 是 regression engineering 工具，不是 prevention engineering 工具。

类比最清晰：软件测试。测试不能保证无 bug，但能保证**修过的 bug 不再回归**。audit 和测试是同构的治理机制。

## 第三个发现：维度扩展靠血案喂养

审计的维度从 ~10 扩张到 53 不变式 + 15 元规则，45 天内的扩张轨迹：

```
V37.2   LLM 配额消耗 + 静默推送
V37.3   Governance 自观察（观察者盲区，audit 审计 audit 自己）
V37.4   预算弹性 + cache key 稳定 + 分离调度契约
V37.4.3 LLM context 污染（系统告警 vs PA 回复）
V37.5   推送内容质量 + registry-driven
V37.6   类型歧义 + 跨 job copy-paste
V37.8.4 外部账号时效性（HTTP 200 ≠ 内容健康）
V37.8.6 LLM 输入污染链（log→cache→幻觉）
V37.8.7 LLM 输出解析鲁棒性
V37.8.10 错误链透明度
V37.8.13 告警路径独立性
V37.8.16 runtime 保留文件语义
V37.9    audit 自身性能（MR-7 观察者盲区补齐）
```

每一项都是一次真实血案 + 一份案例文档 + 若干不变式 + 一个元规则。**没有一项是理论推演**。

元规则（meta-rule）是关键机制——它把"这次具体 bug"抽象为"所有同类 bug"的预防规则，并通过运行时扫描器（MRD）自动覆盖跨文件。比如 MR-11 `shell-function-output-must-go-to-stderr-if-not-returned-value` 从一次 Dream 幻觉血案升级为 38 个 shell 文件的自动扫描。

## 第四个实证：对抗性测试才是 audit 的真实体检

我写了 16 个"故意破坏"场景：10 个回归攻击（已知血案）+ 6 个探测攻击（未知盲区）。对真实仓库文件做 try/finally 可逆破坏，跑 audit 看能抓到几个。

```
Category A (回归攻击): 10/10 (100%)  ← audit 对已知维度的真实防御力
Category B (探测攻击):  6/6  (100%)  ← 最初 0/6，完成 4 轮修复后全闭
```

关键不在数字，在**修复路径**：

```
V37.8.17   Route B 测量发现 6 个量化盲区
V37.8.18 P1 修复 3 盲区（C14/C11/C15） → 3/6
V37.8.18 P2 修复 2 盲区（C12/C13）     → 5/6
V37.9      修复 1 盲区（C16 audit-of-audit）→ 6/6
```

每个盲区都对应具体修复工作——新增不变式、扩展现有 check、建立 MRD 扫描器。**从"不知道自己不知道"到"知道并闭合"大约需要 5 天**（P1 2 天 + P2 2 天 + C16 1 天）。

## 第五个发现：audit 必须审计自己

C16 是最有意思的一个盲区：**audit 对自己的性能退化完全无感**。

我注入 `time.sleep(1.5)` 到 `governance_checker.py`，audit 正常通过所有 check。因为 **audit 从不审计自己的执行**。

这是 V37.3 "governance_silent_error" 血案的延续——那次是 summary 逻辑的 bug，这次是性能回归。共同特征：**观察者看不见自己的观察机制**。

MR-7 元规则 "governance-execution-is-self-observable" 说：**审计系统自身是一等被观察对象**。V37.9 补了 wall_time 维度（MRD-AUDIT-PERF-001 基于历史中位数的双阈值检测），但 memory / skip_rate / cold start 等维度仍是空白。

这个元规则的本质：**任何自动化治理机制，如果不对自己应用同款规则，就会在 recursive 层爆发问题**。

## 第六个反思：空白类别占 80%

15 血案的盲区按类别分布：

```
空白类别（audit 从未想过这个维度）:  12/15 = 80%
观察者盲区（audit 看不见自己）:         2/15 = 13%
粒度不够（有 check 但 pattern 粗糙）:   1/15 =  7%
```

**"空白类别"占压倒多数**。这意味着：

1. 要想提升 audit 覆盖率，**继续加严 existing check 的回报递减**
2. 真正的杠杆在**寻找从未建模的维度**
3. 两个可行手段：
   - **对抗性测试** — 主动构造"故意破坏"探测未覆盖维度（V37.9 Route B）
   - **血案回填** — 每次故障后系统性问"为什么这个维度之前没被想到"（Route A）

## 实际落地的 6 条原则

### 1. 不要承诺 audit 能预防

承诺 0% 预防、87% 回归。承诺 audit 是回归工程。承诺维度扩展依赖故障喂养。

### 2. 投资元规则（meta-rule）多于单个不变式

单个不变式防一个 case；元规则防一个 bug class；MRD 扫描器跨所有文件自动覆盖。**三步跃迁**比堆不变式价值高一个数量级。

### 3. 审计自己的审计

audit-of-audit 不是 overkill，是必需。V37.3 的 governance_silent_error 和 V37.9 的 C16 证明：观察者盲区**必然存在**，只能持续补。

### 4. 对抗性测试作为 CI 一部分

Cat A 场景（已知血案回归）应在每次 PR 合并前跑。防止未来某个"纯重构"提交意外削弱已有 invariant。

### 5. 最小修复 + 立即测试

血案爆发时容易连锁修复（V37.8.3 典型案例：1 条 cp 命令的问题演化成 5 轮修复）。
元规则 MR-10 "understand-before-fix" 三问：**之前存在吗 / 哪个改动引入的 / 最小修复是什么**。

### 6. 诚实承认技术债

V37.9 MRD-LAYER-002 暴露 12 个 severity=high 不变式只有单层验证。**不遮**，持续 warn 直到补齐。audit 价值的一半来自诚实，另一半来自工具。

## 结语

Audit 不是你想象的"预防系统"。它是一台持续吸收事故教训的 **regression engine**：每次真实故障被修复后，下一次不会以同样的方式复发。

真正的价值在时间维度：**今天的 audit 是过去 N 个故障的总结**，明天的 audit 是今天新故障被消化后的进化。它像代码测试一样，没有它你会活，但有它你的系统会**累积地变强**。

如果你在构建复杂系统的治理，停止期待 audit 预防未知。开始设计 audit 吸收已知。

---

## 证据链附录

- **路线 A**: `ontology/docs/audit_coverage_retrospective.md` (618 行，15 血案逐项回填)
- **路线 B**: `ontology/docs/adversarial_audit_report.md` (177 行，16 对抗场景报告)
- **对抗工具**: `ontology/tests/adversarial_chaos_audit.py` (572 行，可 CI 集成)
- **审计引擎**: `ontology/governance_checker.py` (1500+ 行，13 MRD scanners)
- **本体声明**: `ontology/governance_ontology.yaml` (3800+ 行，55 invariants + 15 meta-rules)

## 本项目相关文章

- [Why Agent Systems Need a Control Plane](why_control_plane.md) — 控制平面先行的架构论点
- [Seven Failure Scenarios](seven_failure_scenarios.md) — 7 场景韧性报告
- [Provider Compatibility Layer](zhihu_provider_compatibility.md) — 导师建议的三个高价值模块之一

---

_本文是 Stage 2 "从系统构建者到系统作者" 的第四份公开资产（前三份：Control Plane / Seven Failures / Provider Compat）。项目仓库：[openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge)_

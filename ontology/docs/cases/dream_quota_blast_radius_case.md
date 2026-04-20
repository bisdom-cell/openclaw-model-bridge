# 案例分析：Dream MapReduce 配额耗尽的跨 Job 爆炸链

> 日期：2026-04-10 | 触发事件：Qwen3 524 超时 + Dream 30+ 次 Gemini fallback → HN 推送全部垃圾

---

## 事件

用户收到 HN 头版精选推送，发现所有 5 条新闻的"要点"全部是 `技术内容，详见原文`（硬编码回退文案），标题未翻译成中文。系统看似正常运行（推送成功、格式正确），但内容是垃圾。

## 表面现象 vs 真实根因

| 层级 | 表面判断 | 深挖后的真实根因 |
|------|----------|-----------------|
| 第一眼 | "HN 脚本的正则解析 bug" | LLM 调用本身返回 502，不是解析问题 |
| 第二层 | "LLM 服务不可用" | Gemini 日配额被 Dream 在凌晨耗尽 |
| 第三层 | "Dream 调用太多" | V36.1 MapReduce 将 1-2 次调用变成 30+，叠加 Qwen3 宕机 = 配额溃坝 |

## 完整爆炸链

```
[触发器] Qwen3 GPU 超时 (524)
    │
    ▼
[放大器 1] Dream MapReduce V2 (V36.1)
    │  单次 Dream: 14 sources + ~15 notes 批次 ≈ 30 次 LLM 调用
    │  每次 Qwen3 524 → 触发 Gemini fallback
    │  30 次 fallback → Gemini 日配额耗尽 (429)
    │
    ▼
[放大器 2] 跨 Job 无资源隔离
    │  Dream(:5001) 和 HN(:5002→:5001) 共享 Adapter 进程
    │  Dream 耗尽的配额 = 所有 Job 的配额
    │  无 per-job quota 感知，无跨 Job 熔断
    │
    ▼
[放大器 3] Dream 无止损机制
    │  30 次调用全部失败仍继续尝试
    │  每次失败 = 又烧一次 Gemini 配额
    │  没有"连续失败 → 提前停止"的熔断
    │
    ▼
[掩护者] HN 脚本无法区分"LLM 失败"和"LLM 返回异常格式"
    │  Python except 块捕获 502，llm_out = ""
    │  所有条目填入硬编码回退值
    │  Shell 层 RESULT 非空（包含回退 JSON）
    │  if [ -z "$RESULT" ] → FALSE → 当作成功
    │  if grep "429" → FALSE → 429 在 exception 中不在 RESULT 中
    │  推送垃圾到 WhatsApp，用户才发现
    │
    ▼
[用户发现] "要点：技术内容，详见原文" × 5 条
```

## 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| 05:30 | Dream MapReduce 启动，14 sources Map 开始 | Qwen3 开始 524 |
| 05:30-06:20 | Dream 30+ 次 LLM 调用，全部 Qwen3 524 → Gemini fallback | Gemini 日配额持续消耗 |
| 06:21 | Dream 日志首次记录双失败 | Gemini 429 = 日配额耗尽 |
| 06:23 | Dream 再次双失败，Phase 2 Reduce 失败 | Dream 本身也产出空结果 |
| 08:45 | HN cron 触发 | Qwen3 仍 524，Gemini 仍 429 |
| 08:45 | HN LLM 调用 502 → 静默回退 → 推送垃圾 | 用户收到无意义推送 |

> **注**：此事故发生时 Dream 调度为 05:30。V37.2 已将 Dream 提前至 03:00，并划定 00:00-06:00 为 Qwen3 算力专属窗口，避免与其他 job 资源竞争。

## 为什么以前没发生

| 条件 | V36.1 之前 | V36.1 之后 | 今天 |
|------|-----------|-----------|------|
| Dream LLM 调用数 | 1-2 次 | 30+ 次 | 30+ 次 |
| Qwen3 可用 | ✅ 通常正常 | ✅ 通常正常 | ❌ 524 超时 |
| Gemini 配额 | 1-2 次不会耗尽 | Qwen3 正常时不触发 | 30 次 fallback → 耗尽 |
| HN 受影响 | ❌ | ❌ | ✅ |

**两个条件的组合**才触发问题：Dream 30+ 调用 × Qwen3 宕机。单独出现任一条件都不会造成 HN 故障。

## 修复（三层对应三因）

| 层级 | 修复 | 原理 |
|------|------|------|
| **止损** | Dream Map 阶段连续 3 次 LLM 失败 → 熔断停止 | 最多消耗 3 次配额而非 30 次 |
| **退避** | `llm_call` 429→60-120s 退避，524→20-40s 退避 | 给 rate limit 恢复时间 |
| **检测** | HN 3 次重试 + `__LLM_FAILED__` 信号传播 + 失败不推送 | 宁可不推送也不推垃圾 |

## 从本体论视角的启示

### 1. 共享资源的隐式耦合

Dream 和 HN 表面上是独立 Job，但通过 Adapter 进程共享 LLM 配额。这种**隐式耦合**比显式依赖更危险——它不出现在任何依赖图中，只在故障时才暴露。

**本体论映射**：
- 概念：`SharedResource`（Gemini 日配额）
- 关系：`consumes(Dream, GeminiQuota)`, `consumes(HN, GeminiQuota)`
- 不变式候选：**"任何共享 LLM 配额的 Job 集合，其总最坏调用数不应超过配额上限的 80%"**

### 2. 功能增强的 Blast Radius 盲区

V36.1 的 Dream MapReduce 是一个"纯功能增强"——让 Dream 看更多数据、产出更深洞察。没有人评估它对共享资源的消耗增量。这是 **功能增强的隐含代价**：看似只改了自己，实际改变了整个系统的资源消耗模型。

**新增功能的评估清单应包含**：
- [ ] 该功能的最坏 LLM 调用数是多少？
- [ ] 当主力 provider 全部 fallback 时，调用数 × fallback 配额影响？
- [ ] 哪些其他 Job 共享这个 fallback？

### 3. 静默失败是最危险的失败模式

HN 脚本的设计意图是"LLM 不可用时发告警"（line 283-287），但实际上 **告警逻辑被回退逻辑绕过了**。Python 层吞掉了错误，输出了看起来合法的 JSON，Shell 层无法区分"成功"和"带回退值的失败"。

**不变式候选**：**"任何面向用户的推送，必须包含内容质量断言（如：翻译后的标题不应等于原始英文标题）"**

### 4. 原则 #8 的活教材

> "做减法不做加法。新增防护/监控前先问'谁已经在管这件事'；每加一层保险 = 多一个故障源"

MapReduce 是"做加法"的典型：加了 30 次 Map 调用 = 加了 30 个可能的 fallback 触发点 = 加了对共享配额的 30 倍压力。功能增强不是免费的，它改变了系统的故障面。

## Governance 不变式提案

```yaml
# 新增不变式 — 从本案例提炼
- id: INV-QUOTA-001
  name: "LLM Quota Blast Radius"
  description: "批量 LLM 调用的 Job 必须有连续失败熔断机制"
  check: "grep -c 'consecutive.*fail\|MAP_CONSECUTIVE_FAILS' kb_dream.sh | test $(cat) -gt 0"
  layer: runtime
  source_case: "dream_quota_blast_radius_case.md"

- id: INV-PUSH-001
  name: "No Silent Garbage Push"
  description: "面向用户的推送脚本必须能检测 LLM 失败并跳过推送"
  check: "grep -c 'LLM_FAILED\|llm_failed' run_hn_fixed.sh | test $(cat) -gt 0"
  layer: runtime
  source_case: "dream_quota_blast_radius_case.md"
```

---

*本案例是原则 #26（异常必须深挖爆炸链，喂养本体工程）的首个实践。每次事故不是修完就结束，而是系统防线织密的原材料。*

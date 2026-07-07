# 第二实例 PoC — 选型记录（H1-C / C1）

> **任务**：`docs/charter_execution_plan_20260705.md` §1 **H1-C / C1**（第二实例最小闭环选型 + 前置门控核对，P1 · 原排 Q4）。
> **交付物类型**：选型记录（决策文档 + G2 门控核对结果），非运行时代码。
> **确立**：2026-07-07（V37.9.253），**VERSION 0.37.9.102 不变**（纯选型/证据文档，零 runtime 机制，镜像 V37.9.246 纲领 / V37.9.248 执行计划惯例）。
> **上游文档**：技术纲领 §8 门控 G2 / 执行计划 §1 H1-C / PA 耦合盘点（V37.9.249）。一物一形：纲领管方向、执行计划管任务分解、本文档管**第二实例的具体选型与门控实证**。
> **为什么现在做（原排 Q4 → 拉到 Q3 启动 C1）**：Q3 任务（Observer flip / PA 解耦 / SLO 重生成 / 论文数据）均已完成或 gated；门控收敛日无非-gated dev 项。而 C1 的门控 G2 前置**恰好可在当前 Linux 开发容器上 grounded 实证**（这台机器 = `uname -s -m` → `Linux x86_64`，非 Mac），是"可迁移性从声明到实证"最廉价的一步，直击两次外部评审共同点名的 #1 短板（可迁移性 6.5/10）。C1=选型记录可单 session 完成；C2（真跑最小闭环）仍需持久 Linux host + LLM 端点，保持 Q4。

---

## §0 执行摘要

**一句话结论**：第二实例最小闭环选定为 **PUSH-only 纯推送形态**（内容 job → adapter/proxy → LLM → Discord），**刻意不含 OpenClaw Gateway / WhatsApp 入站**——这一决策绕开了系统最重的三处 PA 耦合（WhatsApp auth 链 / Gateway launchd / sessions.json 会话状态），把第二实例的迁移表面从"整个 bridge"收窄到"控制平面核心 + 治理 + 1–2 内容 job + notify"。**G2 门控三前置已在本 Linux 容器全部 grounded 实证通过**（§1），门控经实证可开。

**门控状态更新**：纲领 §8 G2 由「前置进行中」→ **「前置满足·2026-07-07 Linux 实证」**（三前置 + 加强探针全绿，见 §1 证据表）。

**C2（Q4）真门槛**（§3）：🔴 launchd 进程管理（最小闭环用 cron+nohup 可绕，不需 launchd 抽象）+ 可达 LLM 端点 + Discord token/channel + Linux 安装脚本。**均可枚举、无未知**。

---

## §1 G2 门控核对（三前置 · Linux x86_64 实证）

纲领 §8 G2「第二实例 PoC 启动」前置 = H1-B 首批 config 化合并 + `cross_os_quirk_scanner` 持续 0 violations + `minimal_runtime` golden 跨机 MATCH。三条 2026-07-07 在本开发容器（`Linux x86_64`，非 Mac Mini）逐一实证：

| G2 前置 | 判据 | 2026-07-07 Linux 实证结果 | 证据源（可复现） |
|---------|------|--------------------------|------------------|
| ① H1-B 首批 config 化合并 | KB 路径 / TZ / diagnose 路径 config 化进 main | ✅ 已合并 | V37.9.249（A1）/ V37.9.250（D3 TZ + A2）/ V37.9.251（B3 scanner），PR 已入 origin/main |
| ② `cross_os_quirk_scanner` 持续 0 violations | 全 repo 扫描 0 跨-OS quirk | ✅ **0 violations** | `python3 cross_os_quirk_scanner.py` → `INV-CROSS-OS-001 全 repo scan: 0 violations`；CI 守卫 `test_cross_os_quirk_scanner.py`（在 full_regression） |
| ③ `minimal_runtime` golden 跨机 MATCH | 确定性控制平面决策与提交 golden 逐字一致 | ✅ **MATCH**（跨机=Linux 非 Mac） | `python3 examples/minimal_runtime/minimal_runtime.py` → `golden trace: MATCH`；CI 守卫 `test_minimal_runtime.py`（在 full_regression） |
| （加强）`external_dogfood` 仓库外 wheel 消费 | build wheel → 隔离 venv 装 → 仓库外第三方消费方审计通过 | ✅ **PASS** | `bash examples/external_dogfood/run_dogfood.sh` → `openclaw-ontology-engine is consumable out-of-repo from a wheel`；CI 守卫 `test_external_dogfood.py`（在 full_regression） |

**门控结论**：G2 三前置全部满足，加强探针（仓库外 wheel 消费）在 Linux 亦 PASS。**可抽取产品（ontology-engine + 三个 demo）在非-Mac Linux 上实证可跑**，可迁移性底座就位。门控经实证可开 → C1 启动合规（纲领 §7-4 判据门控决策法：条件全满足才推进，此处已全满足）。

**诚实边界**：G2 验证的是**可抽取产品层 + 控制平面决策核心**在 Linux 的可跑性，**不是**整个 bridge runtime（adapter/proxy 真转发 + 内容 job 真抓取 + notify 真投递）已在 Linux 端到端跑通——后者是 C2（§4/§5，需持久 host + 端点）。

---

## §2 第二实例最小闭环选型

### 2.1 选定形态：PUSH-only 纯推送（核心决策）

纲领 C2 验收 = 「非 Mac Mini 机器上 E2E 绿：`minimal_runtime` + governance audit + 1–2 内容 job + notify（Discord-only 可）」。据此 + PA 盘点的迁移门槛分级，最小闭环选定为**四件套 PUSH-only 形态**：

```
[cron/systemd timer]                                    ┌── (无 Gateway / 无 WhatsApp 入站 = 绕开最重 PA 耦合)
     │                                                  │
     ▼                                                  │
[内容 job (fetch)] ──→ [adapter:5001 + proxy:5002] ──→ [LLM 端点] ──→ [job 组装] ──→ [notify.sh → Discord]
   arxiv / dblp          控制平面核心 (nohup)          REMOTE_BASE_URL          NOTIFY_CHANNELS=discord
                              │
                              └──→ [governance_checker.py] (每日治理审计, 独立 cron)
```

**为什么 PUSH-only（而非搬整个 bridge）**：系统最重的三处 PA 耦合都在**入站/交互链**上——

| 绕开的耦合 | 属于 | PA 盘点 blast-radius | PUSH-only 是否触碰 |
|-----------|------|---------------------|--------------------|
| WhatsApp auth / Baileys 重连 | Gateway 入站 | 🔴（408 限流史 / 设备链接限制） | ❌ 不需要（无 WhatsApp） |
| Gateway launchd (KeepAlive) | 进程管理 | 🔴（第二实例真门槛之一） | ❌ 不需要（无 Gateway 进程） |
| sessions.json 会话状态 | 交互上下文 | 🟡（PA 对话状态） | ❌ 不需要（无交互，纯推送） |
| SOUL.md PA 身份 prompt | 交互人格 | 🟢（已 config 部署） | ❌ 不需要（内容 job 不加载 PA 人格） |

PUSH-only 把第二实例从"复刻 Wei 这个 PA"降级为"在 Linux 上证明控制平面 + 治理 + 内容管道 + 推送可跑"——**这正是可迁移性要证明的东西**（引擎/管道可迁移），而非把个人 PA 也搬过去（纲领 §4.3：杀 PA = 杀数据复利，第二实例不复制 PA，只证可抽取部分）。

### 2.2 四件套逐项选型

| # | 组件 | 选定 | 理由 / 已验证态 |
|---|------|------|-----------------|
| 1 | 控制平面核心 | `adapter.py` + `tool_proxy.py`（nohup 启动，非 launchd） | 决策核心已 Linux 实证（§1 minimal_runtime MATCH）；真转发需 §3 的 LLM 端点。`restart.sh` 已有 nohup fallback 分支（PA 盘点 C1）。 |
| 2 | 治理审计 | `ontology/governance_checker.py`（独立 cron） | 已在 Linux 跑（full_regression 每次执行 governance，91/91 invariants 绿）；零 Mac 依赖。 |
| 3 | 内容 job（选 1–2） | **arxiv 论文监控** + **dblp CS 论文监控** | 选型准则=最少 PA 耦合的纯-fetch job：① 纯 RSS/API 抓取（无 PA-specific KB 读依赖）② 走 adapter/proxy → LLM 摘要（练全链路）③ Discord 推送。中文 prompt 对中文用户实例是特性非 bug（PA 盘点 D2）。**排除**：freight/finance（含地域/业务耦合）、dream/observer（读全量个人 KB）、KB 回顾类（依赖个人 notes 积累）。 |
| 4 | notify | `notify.sh`（`NOTIFY_CHANNELS=discord`，默认值） | 默认即 discord（V37.9.179），已可移植；需 §3 的 `DISCORD_CH_*` env + bot token。 |

**进程管理选型**：内容 job 用 **Linux crontab**（跨平台，非 macOS system crontab 特有）；adapter/proxy 用 **nohup**（`restart.sh` fallback 分支）。**刻意不实现完整 launchd→systemd 抽象**（🔴 高 blast，最小闭环用 cron+nohup 即可跑通，抽象层留给未来若第二实例要长稳运维时再评估——日落法：不为最小闭环造进程管理框架）。

---

## §3 真实迁移门槛（C2 前置清单，grounded 自 PA 盘点 V37.9.249）

C2（Q4 真跑）启动前需就位，**均可枚举、无未知**：

| 门槛 | PA 盘点分级 | C2 前需要 | 现状 |
|------|-----------|-----------|------|
| 🔴 launchd 进程管理 | C1 高 | 最小闭环用 cron+nohup **绕过**（不需 launchd 抽象） | `restart.sh` 已有 nohup fallback；内容 job 走 crontab |
| LLM 端点可达 | ✅ 已 env 化（`REMOTE_BASE_URL`，V37.9.211） | 第二实例 host 能访问一个 OpenAI 兼容端点（用户自建 / 或 stub） | env 驱动就绪，填自己端点即可 |
| Discord 投递 | ✅ 已 config（notify.sh 默认 discord） | `DISCORD_CH_*` channel ID + bot token 进 `.env_shared` | 参数化就绪 |
| Linux 安装/引导脚本 | C4（`install_openclaw_macmini.sh` 是 Mac 特定） | 并行的 Linux bootstrap（装 python 依赖 + 起 adapter/proxy nohup + 写 crontab） | **需新建**（H1-C 子任务，Q4） |
| 中文 prompt locale | D2 🟡 | 第二实例若中文用户则原样可用；英文用户才需 locale 层 | 边界内，不阻塞（假定第二实例同为中文用户） |
| `/opt/homebrew` PATH / `md5 -q` / TZ | 🟢 / ✅ | 无害（Linux 回落系统路径）/ 已有 `md5sum` fallback / 已 `${SYSTEM_TZ:-…}` config | 已可移植 |

**唯一真需新建的工件 = Linux bootstrap 脚本**（其余是 env 填值或 cron+nohup 绕过）。

---

## §4 已验证 vs C2 待做（诚实边界）

| 层 | 2026-07-07 已在 Linux 实证 | C2（Q4）待做 |
|----|---------------------------|--------------|
| 可抽取产品（engine + demos） | ✅ minimal_runtime MATCH / external_dogfood PASS / scanner 0 | — |
| 治理审计 | ✅ governance 91/91 在 Linux 跑（full_regression） | 独立 cron 化 + 每日审计推 Discord |
| 控制平面决策核心 | ✅ minimal_runtime 路由/治理决策 MATCH | adapter/proxy **真转发**到活 LLM 端点（需端点） |
| 内容管道 | ❌ 未验证真跑 | arxiv/dblp job 在 Linux **真抓取→LLM→Discord** E2E |
| notify Discord | ❌ 未验证真投递 | Discord 真收到推送（需 token/channel） |
| 进程存活 | ❌ 未验证 | cron+nohup 在持久 host 上跨重启存活 |

**不夸大**：今天证明的是"可抽取部分 + 控制平面决策核心在 Linux 可跑" + "G2 门控经实证可开"，**不是**第二实例已跑通。C2 是真正的端到端实证。

---

## §5 C2 验收标准（Q4）

第二实例 PoC 在非-Mac Linux host（VPS/容器）达成即 C2 done：

1. `minimal_runtime` + `governance_checker.py` 在该 host 绿（已知可跑，§1）。
2. adapter + proxy 经 nohup 起，`/health` 三层绿，真转发一个请求到活 LLM 端点得到回复。
3. 至少 1 个内容 job（arxiv 或 dblp）经 crontab 触发，真抓取 → LLM 摘要 → **Discord 真收到推送**。
4. 该 host 重启后 cron+nohup 自动恢复（进程存活）。
5. 运行日志 + Discord 截图归档 → 喂 C3 portability report（EN/ZH 证据型文章 = 对两次评审可迁移性批评的终局回答）。

**C2 前置**：持久 Linux host + 可达 LLM 端点 + Discord token/channel + Linux bootstrap 脚本（§3）。

---

## §6 复杂度预算（日落法 #34）

| 维度 | 变化 |
|------|------|
| files | +1（本选型记录 = 决策文档，非 runtime 接缝） |
| env / jobs / runtime-state-sources | 全不变 |
| suites / tests / checks / VERSION | 全不变（VERSION 0.37.9.102 不变） |
| 净 runtime 机制 | **0** |

**零新增机器**：G2 门控核对**复用既有 CI 守卫**（`test_cross_os_quirk_scanner` / `test_minimal_runtime` / `test_external_dogfood` 均已在 full_regression），不造新扫描器/新校验（镜像执行计划 Part 2 五项复核"刻意无守卫测试"）。选型决策把第二实例表面**收窄**（PUSH-only 绕开 Gateway/WhatsApp/sessions）= 净负复杂度倾向。**刻意无守卫测试**（选型记录不在机器跟踪范围，靠 changelog 留痕，对齐 design doc 惯例）。

---

## §7 诚实边界

- 本文档是**选型（C1）**，非实证（C2）——真跑门槛登记在 §3/§5，需 Q4 的 host + 端点。
- PUSH-only 选型意味着第二实例**不复刻 PA（Wei）**——这是有意的（纲领 §4.3：第二实例证"可抽取部分可迁移"，非"个人 PA 可复制"）。若未来要证交互链（WhatsApp/Gateway）也可迁移，是独立更大工件（H2+），不在本 PoC 范围。
- "中文用户实例原样可用"假定第二实例同为中文用户；英文用户实例的 locale 层（D2）是独立中等工程量，不在最小闭环。
- G2 的 `cross_os_quirk_scanner 持续 0` 中"持续"由 CI（每次 full_regression）保证，非一次性——今日实证是当前快照，回归由 `test_cross_os_quirk_scanner` 守。

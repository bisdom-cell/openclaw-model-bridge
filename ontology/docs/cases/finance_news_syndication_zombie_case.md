# finance_news X/Twitter 僵尸账号血案

> **日期**: 2026-04-14
> **版本**: V37.8.3 → V37.8.4
> **元规则归属**: MR-10 understand-before-fix（V37.8.3 就已引入，但第二天就被违反）
> **新增不变式**: INV-X-001 syndication-account-must-have-fresh-tweets
> **血案类**: 静默失败 × HTTP 协议层绿灯 × 内容层全黑 × 跨版本连锁违规

---

## TL;DR

V37.8.3 声称"修复 finance_news X 账号错误 handle"，把 `CaixinGlobal → caixin`、`YicaiGlobal → yicaichina`、`STcom → straits_times` 三处改名。**上线第二天（V37.8.4 开工验证）发现三个改名后的 handle 全都是僵尸账号**，Syndication API 返回的最新推文分别是 2227 天 / 3364 天 / 420 天前。继续审计又发现 `Reuters` 253 天停更、`BrookingsInst` 585 天停更、`WorldBank` 直接返回 2KB 空 stub、`ChannelNewsAsia` 2955 天停更——**6 个本应为系统核心信源的国际权威 X 账号实际已死，总 22 个账号中 7 个僵尸，~32% 污染率**。

修复本身没错（原 handle 确实不存在），错在**没有验证新 handle 的真实健康状态**。HTTP 200 + 可解析 HTML + 推文数据 **三层都绿**，但最新推文是几年前——Syndication API 对已停更账号会返回 stale 快照，**把僵尸包装成 200 OK**。

---

## 完整因果链架构图

```
2026-04-12  [V37.8.2] finance_news 上线
            │  声称"Mac Mini 验证：32 篇（16 国际 + 16 国内），0 源失败"
            │  但 32 篇具体从哪些 handle 来，未做账号级拆分
            │  — 僵尸账号已在其中（Reuters/WorldBank 等），被整体成功率掩盖
            │
2026-04-13  [V37.8.3] MR-10 元规则引入 + 声称修复 3 个错误 handle
            │
            ├─ [session] 看到 finance_news 日志中有 CaixinGlobal/YicaiGlobal/STcom 标注"账号不存在"
            │  → 猜测是 handle 写错 → 改为 caixin/yicaichina/straits_times
            │
            ├─ [MR-10 三问] 理论上应该问：
            │   (1) 之前存在吗？→ 日志显示不存在
            │   (2) 哪个改动引入？→ V37.8.2 原始定义
            │   (3) 最小修复？→ 确认"正确 handle"是什么
            │  但第三问被简化为"搜到同名账号就是对的"，没有验证**活跃度**
            │
            ├─ [commit 7e819c0] 改名后 push + 部署 + 推送走绿，没人发现
            │  因为 Syndication API 对 caixin 返回 HTTP 200 + 可解析 JSON + 有推文
            │  过滤器 CUTOFF=72h 把所有老推文过滤成 0 条 → 诊断行显示
            │  "[finance] X @caixin: 0 条（原始10条, 过滤: 10条超72h）"
            │  — 这行日志本应是 ZOMBIE 信号，但当时没有告警机制
            │
2026-04-14  [V37.8.4] 开工第一个 session 即触发血案发现
            │
            ├─ [06:38 Dream 输出] 用户注意到推送里"今日新鲜推文稀少"
            ├─ [session 诊断] 手动跑 Syndication API 测试三个 CN handle
            │  → 三层全绿：HTTP 200 ✓, HTML 485KB+ ✓, 推文数据 ✓
            │  → 但最新推文：caixin 2019-10-15, yicaichina 2016-12-09, straits_times 2024-12-20
            │  → "账号活着但内容死了"
            │
            ├─ [更大审计 22 handle] 运行分页 Syndication 测试（遇 429 限流）
            │  结果拼凑出：
            │    Reuters         ZOMBIE  最新 2025-08-03  (253 天)
            │    WorldBank       ZOMBIE  2KB stub（embed disabled）
            │    BrookingsInst   ZOMBIE  最新 2024-09-06  (585 天)
            │    ChannelNewsAsia ZOMBIE  最新 2018-01     (2955 天)
            │    caixin          ZOMBIE  2227 天
            │    yicaichina      ZOMBIE  3364 天
            │    straits_times   ZOMBIE  420 天
            │    IMFNews         FRESH   当天推文
            │    其他 14 个      429 限流未能确认
            │
            └─ [触发 MR-10 反思] V37.8.3 引入的"修复前必答三问"
               在**引入后第二天就被违反**——第三问"最小修复方案是什么"
               被理解为"改到字面上正确的 handle"而不是"改到一个可用的信源"
               → 元规则本身没问题，问题是执行时的"浅层化倾向"
```

---

## 三层根因

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | V37.8.3 修改 3 个 X handle，改后的 handle 全是僵尸 | `CaixinGlobal→caixin` 等 3 处改名，改后 handle 停更 1~9 年 |
| **放大器** | Syndication API 的"协议层/内容层不一致" | HTTP 200 + 有推文数据 ≠ 账号活跃；embed-disabled 账号会返回静态快照 |
| **掩护者** | 1. 没有时间戳健康检查 2. 72h 窗口过滤把僵尸伪装成"今日无热点" 3. V37.8.2 的"32 篇 16 国际 16 国内"整体成功率掩盖账号级异常 4. preflight/治理层没有 X 账号健康不变式 | 所有监控和单测都在"HTTP+parse"层面，没有"推文时效性"层面 |

---

## 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| 2026-04-12 | V37.8.2 finance_news 上线，22 个 X 账号（含僵尸） | 成功率 32 篇掩盖真实健康 |
| 2026-04-13 | V37.8.3 改名 3 个 handle，改后全僵尸 | 0 新鲜内容 + 日志"0 条（10条超72h）"静默 |
| 2026-04-14 06:38 | 用户在 Dream 输出中感知新鲜度不足 | 主动触发诊断 |
| 2026-04-14 上午 | 手动 Syndication 验证，三个 CN handle 全僵尸 | 揭开 V37.8.3 问题 |
| 2026-04-14 中午 | 22 handle 全量审计，发现 6+ 个僵尸（含 Reuters/WorldBank/BrookingsInst） | 问题远超 V37.8.3 范围 |
| 2026-04-14 下午 | V37.8.4：删 7 僵尸 + 加静默失败检测 + INV-X-001 + CLAUDE.md #27 更新 | 闭环 |

---

## 为什么以前没发生（条件组合分析）

| 条件 | V37.8.1 以前 | V37.8.2~V37.8.3 | V37.8.4 |
|------|------|------|------|
| 有 X 账号 | 无（纯 RSS） | 22 个（含僵尸） | 15 个（清洗后） |
| 健康检查 | N/A | 仅 HTTP 200 + HTML size | HTTP + 时间戳 + 3 天连续命中告警 |
| 诊断可见性 | N/A | 有 `0 条（超72h）` 但无 ZOMBIE 标记 | 有 `⚠️ ZOMBIE嫌疑` + 独立 zombie_${DAY}.txt |
| 治理层不变式 | 无 | 无 | INV-X-001 |
| MR-10 执行 | 未定义 | 定义但浅层执行 | 纳入案例库 + 正向兑现 |

**组合爆炸条件**：
1. X 账号是 V37.8.2 才引入的**新信源**（触发面积增大）
2. Syndication API 具有"协议层绿灯+内容层全黑"的可能（结构性放大器）
3. 初期成功率 16+16=32 篇掩盖了账号级异常（观察盲区）
4. 改 handle 时没有要求"验证最新推文时间"（MR-10 浅层执行）
5. 诊断行里的"超72h"被当作"今日热点少"而非"账号已死"（语义误读）

五个条件同时出现才触发。但类似组合会在**任何新外部信源引入时**再次出现——这正是血案喂给本体工程的价值。

---

## 修复（V37.8.4）

### 1. 删除 7 个确认僵尸

```diff
- "Reuters|路透社官方(X)|intl"           # 253 天
- "WorldBank|世界银行官方(X)|intl"       # 2KB stub
- "BrookingsInst|布鲁金斯学会(X)|intl"   # 585 天
- "caixin|财新英文(X)|cn"                # 2227 天
- "yicaichina|第一财经(X)|cn"            # 3364 天
- "ChannelNewsAsia|CNA新加坡(X)|cn"      # 2955 天
- "straits_times|海峡时报(X)|cn"         # 420 天
```

剩余 15 个账号中，IMFNews 已验证 FRESH，其他 14 个被 429 限流，依赖**生产 cron 每天自然暴露**。

### 2. 静默失败检测（`jobs/finance_news/run_finance_news.sh`）

```python
# V37.8.4 僵尸嫌疑检测：API 返回推文，但 100% 超 72h = 账号停更/embed disabled
is_zombie_suspect = (diag["total"] > 0 and diag["old"] == diag["total"])
if is_zombie_suspect:
    with open(zombie_file, 'a') as f:
        f.write(f"{handle}\n")
    # 诊断行打 ⚠️ 前缀
    prefix = "⚠️ ZOMBIE嫌疑 " if is_zombie_suspect else ""
```

每日写入 `cache/zombies_${DAY}.txt`，bash 后处理对比今日 + 昨天 + 前天三个文件，**3 天连续命中同一 handle → log warning**，供人工复核。

### 3. CLAUDE.md 原则 #27 升级

从"X 是高质量实时数据第一选择"升级为"**X 是高质量实时数据第一选择（但账号健康 ≠ HTTP 200）**"，新增强制条款：
- 账号健康必须通过最新推文时间戳验证
- 新增账号前必须先抽查最新推文时间
- 生产脚本必须对"抓到内容但 0 条过时间窗"的账号打 ZOMBIE 嫌疑标记

### 4. 新增不变式 INV-X-001

```yaml
- id: INV-X-001
  name: syndication-account-must-have-fresh-tweets
  meta_rule: MR-10
  severity: high
  verification_layer: [declaration]
  description: >
    finance_news 等使用 Syndication API 的 job 必须具备僵尸账号检测能力：
    抓到 HTML 但 100% 推文超时间窗 = ZOMBIE 嫌疑，必须打标记+独立文件记录+
    3 天连续命中告警。
```

---

## 喂养本体工程

### MR-10 正向兑现

V37.8.3 引入 MR-10 "understand-before-fix"，**引入后第二天就被违反**——证明元规则的**引入 ≠ 强制**。V37.8.4 把这个违规事件本身固化为 case doc + INV-X-001，让下次修改 X handle（或任何外部信源 handle）时：
1. 治理审计会扫到 INV-X-001 缺失
2. CLAUDE.md #27 硬要求"先验证最新推文时间"
3. 修改前必须在本地跑 Syndication 抽查脚本

### 元洞察：协议层绿灯 ≠ 内容层健康

这是本项目第 3 次遇到"表面 OK 实际死"的静默失败：
- V37.4 Dream reduce：cache 命中看起来快，但内容是 bland 重复
- V37.5 kb_review：registry 读取看起来全，但效果是空 prompt
- V37.8.4 X Syndication：HTTP 看起来 200，但推文是僵尸

**模式**：任何"效果层"的监控缺失 × "协议层"的监控完备 = 绿灯下的死亡。→ 纳入 MR-6 "critical-invariants-need-depth" 的应用范围。

---

## 参考

- 代码改动: `jobs/finance_news/run_finance_news.sh` (V37.8.4)
- 关联不变式: `ontology/governance_ontology.yaml` INV-X-001
- 前置元规则: MR-10 (V37.8.3 引入), MR-6 (verification depth)
- 关联原则: CLAUDE.md #27 (X/Twitter 使用), #28 (理解再动手)
- 审计脚本: 本文档末尾"完整 22 账号审计 Python 片段"可复用为定期运维脚本

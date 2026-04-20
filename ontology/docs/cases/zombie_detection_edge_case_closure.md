# Zombie 检测边缘盲区闭合案例（V37.8.5）

> **日期**：2026-04-15
> **血案类**：MR-10 (understand-before-fix) 二次演出 + MR-6 (critical-invariants-need-depth) 正向兑现
> **核心教训**：每次"修复"都可能埋下下一个 bug 的种子；修复本身必须被单测和运行时治理锁定

---

## TL;DR

V37.8.4 凌晨在 Mac Mini E2E 验证时，用户手动运行 `run_finance_news.sh` 发现
V37.8.4 刚引入的僵尸检测逻辑漏掉两个边缘案例：

- **CNS1952**：99 条推文中 98 条超过 72h 窗口 + 1 条过短 → 老化率 99%，但 V37.8.4 严格相等 `old == total` 放过
- **SingTaoDaily**：返回 2KB 空 HTML stub，`total=0` 被 `total > 0` 前置门槛排除，完全不被标记

这两个案例被登记为"已知盲区"留到 V37.8.5 次日处理。本案例是修复闭合的记录。

## 完整因果链架构图

```
2026-04-14 V37.8.3
    │
    ├─ [用户] 改 3 个 X handle：CaixinGlobal/YicaiGlobal/STcom → caixin/yicaichina/straits_times
    │         （编译器层验证：HTTP 200 + __NEXT_DATA__ 可解析 + 有推文数据）
    │
    ├─ [Mac Mini] 07:30 cron 次日运行：三个 handle 全部僵尸（停更 2227/3364/420 天）
    │
2026-04-14 V37.8.4 引入僵尸检测
    │
    ├─ parser heredoc 加入一行：
    │   is_zombie_suspect = (diag["total"] > 0 and diag["old"] == diag["total"])
    │
    ├─ 13 个 governance check 全部声明层 (file_contains)
    ├─ 单测：无（逻辑嵌在 shell heredoc 内不可 import）
    ├─ 单层 verification_layer: [declaration]
    │
    ├─ [Mac Mini E2E] 用户手动运行 → 系统发现 SCMPNews 95/95 超窗口标记成功 ✓
    ├─ [Mac Mini E2E] 但同一 run 中：
    │   ├─ CNS1952 98/99 → 严格相等不触发 → 无标记 ✗
    │   └─ SingTaoDaily 0 → total>0 门槛排除 → 无标记 ✗
    │
    ├─ [用户登记] V37.8.4 收工：status.json 写入 2 个遗留边缘 case
    │            "老/总=99%但未=100%不触发"
    │            "total=0 不触发 total>0 门槛"
    │
    │
2026-04-15 V37.8.5 闭合修复
    │
    ├─ 开工三问（原则 #28）：
    │   (1) 之前存在吗？ → YES, V37.8.4 引入的检测器本身埋的盲区
    │   (2) 是哪个改动引入的？ → V37.8.4 严格 old==total + total>0 双重前置过滤
    │   (3) 最小修复？ → 扩展到三层 tier，保守守卫防止误报
    │
    ├─ [结构修复] jobs/finance_news/finance_news_zombie.py 纯函数独立模块
    │   ├─ classify_zombie(diag, count) -> (bool, tier)
    │   ├─ Tier 1 "stub": no_data=0 + total=0 (闭合 SingTaoDaily)
    │   └─ Tier 2+3 "stale": count=0 + old*10 >= total*9 (闭合 CNS1952)
    │
    ├─ [可测性] 24 个独立单测：3 层 tier + 5 守卫场景 + 7 shell 集成 + 1 部署 + 1 常量
    │            V37.8.4 heredoc 嵌入式逻辑 0 单测 → V37.8.5 独立模块 24 单测
    │
    ├─ [治理升级] INV-X-001 verification_layer: [declaration] → [declaration, runtime]
    │   ├─ 10 声明层 check 扩展为 12（+import + +call-with-count + +export-env + +module-exists + +tier-1-pattern + +tier-2-pattern + +auto-deploy-mapping - 旧 strict-predicate）
    │   └─ 1 新增 python_assert：真跑 classify_zombie 5 个场景
    │
    └─ [MR-8 兑现] 禁止 shell 内嵌 inline fallback
                   （否则模块缺失时静默退回 V37.8.4 行为 = copy-paste-is-a-bug-class 反面）
```

## 三层根因

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | V37.8.4 手快：上线新检测时用字面量照搬"100% stale = 僵尸"的直觉语义 | 严格相等是所有 `old/total >= X` 的特例，语义上天然比"≥90%"更窄；0-tweet stub 完全不在 `diag["total"] > 0` 的论域内 |
| **放大器** | V37.8.4 检测逻辑嵌入 shell heredoc，0 单测。 V37.8.4 INV-X-001 只有声明层（grep file_contains），无运行时 python_assert | "有治理"≠"治理正确"——声明层 check 只能证明"pattern 字面存在"，无法证明"逻辑覆盖所有应有情形"。MR-6 正是此问题，但 INV-X-001 在 V37.8.4 登记时被漏掉 |
| **掩护者** | V37.8.4 首次 E2E 就暴露了两个盲区，被用户手动观察发现（非自动化），且以"登记 unfinished"方式延后 | 登记 ≠ 治理——`status.json.session_context.unfinished` 是人类 TODO，CI/regression 无感知。若非用户次日（今天）主动拿出处理，可能继续漏数月 |

## 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| 2026-04-14 下午 | V37.8.3 上线改 handle | 3 个僵尸 handle 被部署 |
| 2026-04-14 深夜 | V37.8.4 引入僵尸检测 + 删除 7 个已知僵尸 | 检测器有漏洞但能抓到大部分 |
| 2026-04-14 深夜 | V37.8.4 Mac Mini E2E：手动运行 finance_news | 系统独立发现 SCMPNews（检测器基本工作）+ 用户肉眼发现 CNS1952 + SingTaoDaily 漏标（检测器边缘失效）|
| 2026-04-14 深夜 | V37.8.4 收工登记 2 个边缘 case 到 status.json unfinished | 治理降级为纯人类 TODO |
| 2026-04-15 开工 | 原则 #28 三问锁定：修复本身埋坑 | V37.8.5 结构化闭合 |
| 2026-04-15 闭合 | 纯函数模块 + 24 单测 + INV-X-001 runtime 层 + tier 可观测 | 从"检测器有漏洞"升级为"检测器被单测和运行时治理锁定" |

## 为什么以前没发生（条件组合分析）

V37.8.4 引入检测器 + V37.8.5 立即闭合，这是"修复的修复"——条件组合：

| 条件 | V37.8.4 当下 | V37.8.5 闭合前 |
|------|-------------|-----------------|
| 项目已有"血案喂养本体"文化 | 是（V37.3 MR-7 起） | 是（延续） |
| Mac Mini E2E 是 V37.8.4 收工必选项 | 是 | 是 |
| 用户实际在 Mac Mini 手动触发 finance_news（非纯 cron 观察） | 是 — 用户主动验证 | 是 |
| 用户肉眼对比原始诊断 + 严格相等谓词 | 是 — 发现 99% 和 0 两个边缘 | 是 |
| 次日开工"遗留清单"有刚性执行 | 否 — 靠人记 | 是 — 原则 #28/#29 强制 |

**五个条件同时出现才能让 V37.8.4 → V37.8.5 闭合**；如果任一条件缺失：
- 如果不做 Mac Mini E2E，V37.8.4 检测器会继续工作但漏检 CNS1952/SingTaoDaily 类账号
- 如果不原子观察 E2E（用户只看最终 count），不会发现严格相等的盲区
- 如果未登记 unfinished，次日开工会遗忘
- 如果没有原则 #29"收工零遗漏"+ #28"理解再动手"，次日可能被更急的任务插队

这是项目"三方宪法闭环"起作用的典型范例——血案 → 登记 → 下次开工主动处理 → 结构化修复 → 喂回本体。

## 元规则兑现

### MR-10 understand-before-fix（V37.8.3 引入 → 第 2 次演出）

V37.8.4 时机不对：引入僵尸检测器时没有回答"这个检测器真的覆盖所有僵尸情形吗？"三问中的第三问（最小修复是什么）被浅化为"严格相等 + total>0 就够了"。

V37.8.5 正向兑现：开工 3 问被严格执行，发现修复者本身是 bug 源。

### MR-6 critical-invariants-need-depth（V37.8 引入 → 正向兑现）

V37.8 把 "critical invariants 必须有 ≥2 层验证" 从建议（MRD-LAYER-001 warn）升级为硬强制（INV-LAYER-001 fail-stopping）。

INV-X-001 原来只有声明层（`verification_layer: [declaration]`），不在 critical 强制范围内（severity: high）。V37.8.5 主动升级到 `[declaration, runtime]`，运行时 python_assert 真跑 5 个 tier 场景。

**MR-6 不仅约束 critical，模式应推广到所有需要"防止漂移"的检测器。**

### MR-8 copy-paste-is-a-bug-class（V37.7 引入 → 阻断反向兑现）

shell 脚本禁止内嵌 `classify_zombie` inline fallback——若模块缺失时静默退回 V37.8.4 行为 = 复制相同逻辑到两处，这正是 MR-8 禁止的反面。

`test_script_has_no_inline_zombie_fallback` 单测直接 grep 脚本源码拒绝 `def classify_zombie` 出现。

### MR-4 silent-failure-is-a-bug（V37.7 之前 → 第 6 次演出）

SingTaoDaily 0-tweet stub 是经典 silent failure：HTTP 200 → HTML 可读 → parser 运行 → 0 推文 → 无诊断、无告警、无标记。V37.8.4 的 `total > 0` 门槛直接排除了这类"静默成功"的误判。

## 被喂养的本体

1. **本案例文档** `ontology/docs/cases/zombie_detection_edge_case_closure.md`
2. **INV-X-001 升级** 从 `[declaration]` → `[declaration, runtime]`，13 checks → 20 checks
3. **test_finance_news_zombie.py** 24 单测入 full_regression.sh 第一层
4. **finance_news_zombie.py** 独立 pure function 模块（未来扩展到其他 X 监控 job）
5. **CLAUDE.md V37.8.5 版本行 + 文件表 + changelog**（待写）

## 下一步（迭代本体不闭合）

- [ ] 把 `finance_news_zombie.py` 迁移成通用 `x_syndication_zombie_detector`，被 `ai_leaders_x` / `karpathy_x` 复用（MR-8 正向）
- [ ] 考虑"短推文 + 老推文"组合阈值（CNS1952 的 "1 条过短" 其实可能也是老推文，只是被 short 优先过滤）
- [ ] MRD-MOCK-001 新元规则候选：`任何 heredoc 内嵌的 Python 逻辑必须能通过 import 单测`（阻断 V37.5 heredoc 血案类）

---

**状态**：2026-04-15 闭合 / V37.8.5 发布 / 24 单测 + governance runtime 层全绿

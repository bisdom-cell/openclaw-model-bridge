# Preflight 连锁修复案例 — "不理解就动手"的系统性破坏

> 日期：2026-04-13 | 版本：V37.8.3 | 元规则：MR-10 understand-before-fix

## 事件摘要

一个正常运行的系统，因为 Claude Code 对报错的"条件反射式修复"，
从 0 失败变为 20 失败，经过 5 轮修复、4 层新复杂度、用户 5 次 Mac Mini 手动测试，
才恢复到 0 失败。原始问题只需一条 `cp` 命令。

## 完整因果链架构图

```
Session 开始: 正常的 finance_news X 账号修复 + Dream 阈值调整
       │
       ├─ ✅ 合法改动: auto_deploy.sh FILE_MAP += finance_news 条目
       │
       ╔══════════════════════════════════════════════════════════╗
       ║  转折点: Mac Mini preflight --full 报 20 失败             ║
       ║                                                          ║
       ║  真实原因: ~/auto_deploy.sh (HOME副本) 是旧版本           ║
       ║  → 没有 finance_news 条目 → 与 crontab 不一致            ║
       ║  正确修复: cp ~/openclaw-model-bridge/auto_deploy.sh ~/   ║
       ╚══════════════════════════════════════════════════════════╝
       │
       ├─ ❌ 误诊: "FILE_MAP 缺少 preflight 和 job_smoke_test 条目"
       │   └─ 实际: 这两个脚本从来不在 FILE_MAP 里，以前也能通过
       │
Round 1 ├─ "修复": 加 preflight/job_smoke_test 到 FILE_MAP
       │   ├─ 引入: 体检脚本首次纳入部署链（循环依赖）
       │   ├─ 引入: 部署目标是 $HOME/（不是仓库目录）
       │   └─ 埋下: SCRIPT_DIR=~ 的定时炸弹
       │
Round 2 ├─ "修复": auto_deploy.sh 双目标部署（仓库 + HOME）
       │   ├─ 引入: 从未有过的一源两目标模式
       │   └─ 埋下: dict 解析器覆盖 bug
       │
Round 3 ├─ 爆炸: sections 1/2/6 全崩（SCRIPT_DIR=~ → cd ~ → 找不到文件）
       │   ├─ 根因: Round 1 部署到 $HOME/ 导致
       │   └─ "修复": SCRIPT_DIR HOME 自动检测逻辑
       │
Round 4 ├─ 爆炸: section 15 路径不匹配（dict 只保留最后一个目标）
       │   ├─ 根因: Round 2 双目标部署导致
       │   └─ "修复": dict → dict-of-lists
       │
Round 5 ├─ 爆炸: job_smoke_test 同样的 SCRIPT_DIR 问题
       │   └─ "修复": 加同样的 HOME 检测
       │
       └─ ✅ 终于 0 失败（但系统多了 4 层本不需要的复杂度）
```

## 三层根因

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | ~/auto_deploy.sh 是旧版本，与新 crontab 条目不一致 | 部署同步问题，不是配置缺失 |
| **放大器** | 不理解部署拓扑就开始改配置 | 每层修复引入新 bug → 连锁修复 |
| **掩护者** | 951 个 dev 环境测试无一模拟 HOME 部署拓扑 | 测试全过给出虚假安全感 |

## 条件组合分析

| 条件 | 单独出现 | 组合效果 |
|------|----------|----------|
| ~/auto_deploy.sh 是旧版本 | 旧版本一直在用，只是没有新条目需要校验 | ① + ② = section 15 报"缺失" |
| FILE_MAP 新增了 finance_news | 新条目在仓库版有，HOME 副本没有 | ① + ② = 不一致 |
| Claude Code 把"不一致"误诊为"缺少配置" | 错误的诊断导致错误的修复方向 | ① + ② + ③ = 5 轮连锁修复 |

## 核心教训

### 1. 连锁修复的数学

每次修复增加系统复杂度 C，每层新复杂度创造 k 个新故障面。
5 轮修复 = C₁ + C₂ + C₃ + C₄ + C₅ 的总复杂度。
而最小修复方案 = 1 条 cp 命令 = 零新复杂度。

### 2. "之前为什么能通过"是最强大的诊断问题

如果 Round 1 时问了这个问题，就会发现：
- preflight 和 job_smoke_test 从来不在 FILE_MAP 里
- 它们以前也能通过
- 所以问题不在这里

### 3. Dev 测试 ≠ 部署验证

| 维度 | Dev (Linux/root) | Production (macOS/bisdom) |
|------|-------------------|---------------------------|
| SCRIPT_DIR | 永远是仓库目录 | 可能是 HOME |
| 文件位置 | 仓库里 | HOME 副本 + 仓库 |
| crontab | 不存在 | 34 条 |
| section 15 | 跳过 | 真实执行 |

951 个测试验证了组件内部逻辑，零个测试验证了部署拓扑。

## 元规则 MR-10 提炼

> **understand-before-fix**: 看到报错时，先回答三个问题再动手：
> (1) 之前存在吗？(2) 哪个改动引入的？(3) 最小修复是什么？
> 答不上来 = 还没理解问题，禁止动手。

MR-10 是所有元规则的上位原则——在写代码之前截断"不理解就动手"的冲动。
MR-4 (silent failure) 说"失败要有信号"，MR-6 (depth) 说"验证要有深度"，
MR-10 说"修复之前先确认你真的理解了问题"。

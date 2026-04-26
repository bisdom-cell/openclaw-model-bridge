# kb_deep_dive Cron 未注册血案 — preflight 假绿 + crontab_safe 谎报双层掩护

> 日期：2026-04-26 | 版本：V37.9.17 → V37.9.18 候选 | 元规则：MR-4 silent-failure-is-a-bug

## 事件摘要

V37.9.16 (2026-04-24) 上线 `kb_deep_dive` 每日 22:30 HKT 深度分析任务。
仓库侧改动完整正确（jobs_registry.yaml + auto_deploy FILE_MAP + notify.sh + 单测 + 文档），
Mac Mini auto_deploy 2min 轮询完成文件部署，手动验证 `bash kb_deep_dive.sh` 跑通。

但**实际从未在 cron 触发过**——4/24 22:30 第一次预期触发静默不跑，
4/25 22:30 同样静默，2026-04-26 用户察觉"昨天没收到"才浮现。

调查发现：仓库声明完整、Mac Mini 文件就位、Python 依赖装好、
notify 路由配好——**唯独 crontab 从未被人手动 `crontab_safe.sh add`**，
而本应拦住此漏的两道防线（preflight 检查 + crontab_safe 自身的安全机制）
都因为各自的 silent bug 静默失效。

## 完整因果链架构图

```
2026-04-24 V37.9.16 上线（Claude Code session）
       │
       ├─ ✅ 仓库改动完整: kb_deep_dive.{sh,py} + jobs_registry.yaml
       │   + auto_deploy FILE_MAP + notify.sh routing + 55 单测
       │
       ├─ ✅ auto_deploy 2min 轮询 → cp 部署到 $HOME (15:28)
       │
       ├─ ✅ 手动验证: bash $HOME/kb_deep_dive.sh
       │   → last_run_deep_dive.json: status=ok mode=abstract_only
       │   → deep_dives/2026-04-24.md 产出
       │   → WhatsApp + Discord 推送成功
       │
       │  ╔════════════════════════════════════════════════════╗
       │  ║  收工流程缺口: 没人手动 crontab_safe.sh add        ║
       │  ║                                                    ║
       │  ║  jobs_registry.yaml 是声明，不会自动同步到 crontab ║
       │  ║  V37.9.16 实施清单只覆盖了"代码 + 单测 + 文档"     ║
       │  ║  漏掉了"crontab 手动注册"这一步运维步骤            ║
       │  ╚════════════════════════════════════════════════════╝
       │
       ├─ V37.9.17 收工: bash preflight_check.sh --full
       │   → 81 通过 / 0 失败 / 3 警告
       │   ↑↑↑ 这里应该 FAIL 但没有
       │
       │  ┌──────────────────────────────────────────────────┐
       │  │  防线 1 失效: preflight 假绿 bug                 │
       │  │                                                  │
       │  │  check_registry.py:188 正确输出 warning:         │
       │  │  "[kb_deep_dive] registry 已启用但 crontab 中    │
       │  │   未找到 'kb_deep_dive.sh'"                      │
       │  │                                                  │
       │  │  但 preflight_check.sh:63 只 grep "间隔漂移":    │
       │  │  if echo "$DRIFT_OUT" | grep -q "间隔漂移"       │
       │  │  → 这种 warning 被静默吞掉                       │
       │  │  → 走到 else 分支报告 ✅ "零漂移"                │
       │  │  → 假绿                                          │
       │  └──────────────────────────────────────────────────┘
       │
2026-04-24 22:30 第一次预期触发
       └─ cron 未注册 → 不触发 → 不写日志 → 不写 last_run
          → 无 Watchdog 告警（因为 watchdog 监控的是 job 跑了没成功，
            不是 job 应该跑但没跑）
       │
2026-04-25 22:30 第二次预期触发
       └─ 同上 silent
       │
2026-04-26 09:00 用户察觉"昨天没收到深度分析"
       │
       ├─ Claude Code 调查（按原则 #28 理解再动手 + #26 异常分析宪法）
       │
       ├─ Mac Mini 诊断输出:
       │   crontab -l | grep deep_dive   → 空（cron 真的没注册）
       │   ls ~/.kb/deep_dives/           → 只有 2026-04-24.md
       │   last_run.json: 2026-04-24 15:28（手动验证那次）
       │
       ├─ Step 1 用户复制 LINE 变量 → echo 输出验证
       ├─ Step 2 bash $HOME/crontab_safe.sh add "$LINE"
       │   → 35 → 36 条 ✅ 真实增加
       │
       │  ┌──────────────────────────────────────────────────┐
       │  │  附带发现: crontab_safe.sh 谎报 bug              │
       │  │  (上一次用户照搬字面占位符时暴露)                │
       │  │                                                  │
       │  │  cmd_add() line 80: crontab "$tmp_file"          │
       │  │   → "bad minute" 错误，crontab 拒绝安装          │
       │  │   → 但退出码没检查（无 set -e、无 if check）     │
       │  │                                                  │
       │  │  line 91: if count_after -lt count_before        │
       │  │   → 35 < 35 = false (用 < 不是 <=)               │
       │  │   → 跳过回滚分支                                 │
       │  │                                                  │
       │  │  line 97: echo "✅ 已添加 $count_before → $after" │
       │  │   → 35 → 35 仍打 ✅                              │
       │  │   → 谎报成功                                     │
       │  └──────────────────────────────────────────────────┘
       │
       ├─ Step 3 验证: crontab -l | grep deep_dive → 真有输出 ✓
       │   今晚 22:30 会自动触发
       │
       └─ Step 4 立即手动补今日: bash $HOME/kb_deep_dive.sh
           → 2026-04-26.md 产出（"潜在去噪增强大模型视觉对齐"）
           → WhatsApp + Discord 推送成功
```

## 三层根因

| 层级 | 问题 | 性质 |
|------|------|------|
| **触发器** | V37.9.16 收工漏 `crontab_safe.sh add` 步骤 | 运维流程缺失 |
| **放大器 1** | `preflight_check.sh:63` 只 grep `间隔漂移`，吞掉"未找到"warning，假报 ✅ "零漂移" | 代码 bug — 选择性吞 warning |
| **放大器 2** | `crontab_safe.sh:cmd_add` 不检查 `crontab` 退出码，count 比较用 `<` 不是 `<=`，35→35 还打 ✅ | 代码 bug — 谎报成功 |
| **掩护者** | cron 不触发 = 完全静默：无日志、无 last_run、无 watchdog 告警 | 监控盲区 — 缺"应该跑但没跑"检测 |

## 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| 2026-04-24 13:52 | V37.9.16 ceb937e 合并到 main | 仓库声明完整 |
| 2026-04-24 15:28 | Mac Mini auto_deploy 部署 + 手动 `bash kb_deep_dive.sh` 验证 | 文件就位 + 1 次手动跑通 |
| 2026-04-24 收工 | preflight `--full` 81 通过/0 失败/3 警告 | **假绿掩盖** crontab 缺失 |
| 2026-04-24 22:30 | 第一次预期 cron 触发 | **不触发，无日志，无告警** |
| 2026-04-25 22:30 | 第二次预期 cron 触发 | **同样静默** |
| 2026-04-26 09:00 | 用户察觉"昨天没收到" | 浮现 |
| 2026-04-26 09:13 | 用户跑 `bash kb_deep_dive.sh` 手动补今日 | 推送恢复 |
| 2026-04-26 09:16 | 用户用 `crontab_safe.sh add` 注册 cron 成功 | 今晚 22:30 起自动跑 |

## 条件组合分析

| 条件 | 单独出现 | 组合效果 |
|------|----------|----------|
| ① V37.9.16 漏注册 crontab | 任何新加 system cron 都潜在风险 | 单独触发本案 |
| ② preflight `--full` 只 grep 间隔漂移 | 在间隔漂移场景仍正常工作 | ① + ② = 收工假绿，没人发现 |
| ③ crontab_safe `<` 比较 + 不检查退出码 | 正常添加场景能用 | 仅在用户复盘时再次踩坑 |
| ④ cron job 无"应该跑但没跑"主动监控 | watchdog 监控失败 job，不监控失踪 job | ① + ② + ④ = silent 2 天 |
| ⑤ 用户手机视角验证频率不稳定 | 用户每天看 PA 推送 | 2 天后才察觉 |

**所有 5 个条件同时存在才爆炸**：① 单独是运维流程问题（人会忘事），但 ② ③ ④ 三层防线本应拦住，
全部因各自的 silent bug 失效，最后只能靠 ⑤ 用户手动察觉——这是最差的兜底。

## MR-4 silent-failure 演出史

本案是 MR-4 在 V37.x 系列的又一次演出。每次演出都是新形态：

| 演出 | 形态 |
|------|------|
| V37.3 | governance summary 吞 error |
| V37.4 | Dream Map budget 溢出无告警 |
| V37.4.3 | PA 告警污染对话上下文 |
| V37.5 | kb_review 空 prompt 机械 fallback |
| V37.6 | KB content blocks repr bug + sources H2 重复 |
| V37.7 | kb_dedup 误删活兄弟 |
| V37.8.6 | Dream 自引用幻觉（错误日志进 cache） |
| V37.8.7 | ontology_sources 位置解析级联错位 |
| V37.8.10 | kb_evening 错误链三层稀释 |
| V37.8.11 | auto_deploy 漂移噪声 |
| V37.8.13 | Gateway 宕 9h 静默 |
| V37.8.16 | PA 自残 HEARTBEAT.md 13h |
| V37.9.4 | MOVESPEED rsync 静默 6 天 |
| V37.9.5 | KB 索引 workspace 接缝盲区 |
| V37.9.6 | watchdog 告警过度噪声 |
| **V37.9.18 (本案)** | **cron 注册遗漏 + preflight 选择性吞 warning + crontab_safe 谎报，三重 silent 掩护** |

新形态特征：**不是单点 silent，是三层独立 silent bug 的协同**。
每一层单独都能拦住故障，但三层都因各自的 bug 同时失效——
体现 MR-8 (copy-paste-is-a-bug-class) 的反向变种：**"分散维护是 bug 类"**，
当一个流程的多道防线由不同代码分别实现时，每道防线的局部 bug 累积会
让整体防御失效。

## 核心教训

### 1. 声明 ≠ 运行时

`jobs_registry.yaml` 中 `enabled: true` 是**声明意图**，
不是**运行时事实**。任何"声明 → 运行时"的同步必须有：
(a) 自动化路径（理想）或 (b) 收工清单 + 强制 preflight 检查（最小要求）。

V37.9.16 走的是 (b) 但 preflight 检查不完整，等于回到没有保障。

### 2. 选择性 grep = 选择性 silent

`preflight_check.sh:63 grep -q "间隔漂移"` 是经典的选择性吞 warning：
"我只关心这一种"。`check_registry.py` 实际报告 N 种 warning，
preflight 只消化 1 种 — 其他 N-1 种全静默丢弃。

正确做法：消费方应处理**所有非空 warning**，而不是 grep 特定关键词。

### 3. 状态比较的 `<` vs `<=` 边界

`crontab_safe.sh:91 count_after -lt count_before` 假设"成功添加 = 数量增加"。
但**安装失败 = 数量不变** — 用 `<` 看不出。
正确做法：(a) 检查 `crontab "$tmp_file"` 退出码（这是因果链上游），
(b) 数量比较改用 `count_after -ne (count_before + 1)` 严格相等。

### 4. "应该跑但没跑"的监控盲区

`job_watchdog.sh` 监控的是"job 跑了但失败/超时"。
"job 该跑但 cron 没触发"完全在视野外。

更上游的检测：把 jobs_registry 的 `interval` 转换成"最大允许间隔"，
和 `last_run_*.json` 的 mtime 对比，超过阈值即告警。

### 5. 用户视角频率是最弱兜底

5 个条件全失效后，唯一的兜底是用户察觉。
原则 #13 "定期像用户用" + 原则 #32 "每周用户视角观察" 是结构性补强，
但不能替代上游硬性检查。

## 喂养 ontology — 立案的 V37.9.18 候选

### INV-CRON-005（待立案）`enabled-job-must-be-in-crontab-not-just-registry`

- meta_rule: MR-4
- severity: high
- verification_layer: [declaration, runtime]
- declaration check: preflight_check.sh:63 必须 grep `crontab 中未找到` 不只是 `间隔漂移`
- runtime check: subprocess 跑 `python3 check_registry.py --check-crontab` 解析所有 warning，
  断言 enabled+system 类 job 都在 crontab 中存在
- 单测覆盖：插入 fake registry job 但不 add cron，断言 preflight 报 fail

### INV-CRON-006（待立案）`crontab-safe-add-must-verify-real-success`

- meta_rule: MR-4
- severity: high
- verification_layer: [declaration, runtime]
- 修复点：
  - `crontab_safe.sh:cmd_add` 加 `if ! crontab "$tmp_file"; then echo "❌ crontab 安装失败"; exit 1; fi`
  - count 比较改用 `count_after -ne $((count_before + 1))` 严格相等
- 回归测试：构造 `bad minute` 输入，断言脚本 exit 1 + 不打 ✅

### MR-X 候选（待评估）`runtime-monitoring-must-include-missing-not-just-failing`

- 监控维度从"跑了但失败"扩展到"应该跑但没跑"
- 实现路径：watchdog 比对 jobs_registry interval vs last_run_*.json mtime
- 严格度：可观察性强化，不是 hard invariant

## 不在本次修复内的工作（按原则 #28 最小修复）

| 项 | 决策 |
|---|------|
| INV-CRON-005 实现 + preflight grep 修复 | 待用户决策今天/V37.9.18 |
| INV-CRON-006 实现 + crontab_safe 退出码检查 | V37.9.18 |
| "应该跑但没跑" watchdog 扩展 | V37.9.18 或更晚（设计先行） |
| 给所有现存 system cron job 加 `last_run_*.json` 一致命名 | 长期治理 |

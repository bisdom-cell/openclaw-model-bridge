# PA 自残血案：HEARTBEAT.md 保留文件污染 + WhatsApp 插件路由偏移（V37.8.16）

> 2026-04-20 闭环 | MR-4 silent-failure 第 12 次演出 | 催生 MR-15 元规则候选

## TL;DR

PA (Wei) 自己用 write 工具往 `~/.openclaw/workspace/HEARTBEAT.md` 写"任务完成总结"，
无意中触发 OpenClaw 的 HEARTBEAT 机制：所有后续 WhatsApp 用户消息被 LLM 按
"If nothing needs attention, reply HEARTBEAT_OK" 指令覆盖，Gateway `stripTokenAtEdges`
剥离 12 字符 HEARTBEAT_OK → outbound 空 → 用户以为 PA 死了。

**从 PA 自己写入 HEARTBEAT.md 到 13 小时后用户发现，中间没有任何告警。**

## 最小修复

清空 `~/.openclaw/workspace/HEARTBEAT.md` 为只含注释，`isHeartbeatContentEffectivelyEmpty()`
返回 true，heartbeat 不再激活。WhatsApp E2E 实测 2026-04-20 09:16 通过（5 秒延迟正常回复）。

## 结构修复（V37.8.16）

| 层 | 修复 | 文件 |
|---|---|---|
| LLM 行为层 | SOUL.md 规则 11「禁止写 HEARTBEAT.md」 | `SOUL.md` |
| Proxy 拦截层 | `RESERVED_FILES` 常量 + `detect_reserved_file_write()` 预检 + `fix_tool_args` 集成 | `proxy_filters.py` |
| 治理层 | INV-HB-001 不变式 + MR-15 元规则候选 | `ontology/governance_ontology.yaml` |
| 测试层 | `TestReservedFileWriteBlock` 单测 | `test_tool_proxy.py` |

---

## 完整因果链（原则 #26 异常分析宪法四维度）

```
【时间线 × 层级 × 逻辑 × 架构】

04/10 14:53  OpenClaw 自动安装 WhatsApp 插件
             /opt/homebrew/lib/node_modules/openclaw/extensions/whatsapp/
             │   ↓ openclaw.json 新增字段:
             │     plugins.entries.whatsapp.enabled = True
             │     plugins.installs.whatsapp.installedAt = 2026-04-10T06:53:15Z
             │     plugins.load.paths[0] = .../whatsapp
             │
             │   ↓ 副作用（未被注意到）:
             │     WhatsApp inbound 从 "core-builtin" 处理改为 "plugin" 处理
             │     路由从 main agent 偏移到 research agent（插件 fallback 逻辑）
             │     main/sessions.json mtime 停留在 Mar 30 23:34（永不再更新）
             │
             │   ✅ 但因为 HEARTBEAT.md 此时为空 → research 正常响应 → 用户没察觉
             │
04/19 09:09  PA (Wei) 处理昨晚 HN alert 对话
             │   ↓ PA 调用 write 工具:
             │     path = "~/.openclaw/workspace/HEARTBEAT.md"
             │     content =
             │       - [SYSTEM_ALERT] HN热帖抓取已恢复，cron_doctor.sh 运行正常。告警解除。
             │       - 任务完成：已解决因macOS磁盘访问权限导致的HN热帖抓取超时问题。
             │       - 下一步：持续监控系统健康，确保其他服务稳定运行。
             │   ↓ PA 的认知:
             │     "HEARTBEAT.md 看起来像是 TODO 笔记文件名，
             │      写个总结记录一下工作成果吧"
             │   ↓ PA 不知道的:
             │     HEARTBEAT.md 是 OpenClaw 保留文件，有特殊语义
             │     isHeartbeatContentEffectivelyEmpty() 对"非 #/非空bullet"内容返回 false
             │     文件非空 → heartbeat 机制被激活
             │
             ↓ 13 小时潜伏期 ↓
             （PA 偶尔收到 cron 消息仍正常推送；无人注意 HEARTBEAT.md 异常）
             │
04/19 22:09  用户开始给 PA 发 WhatsApp 消息
             │
             ├─ [Gateway] WhatsApp plugin inbound handler 接管
             ├─ [Gateway] isHeartbeatContentEffectivelyEmpty(HEARTBEAT.md) → FALSE
             │               （文件非空非仅注释，"actionable content"）
             ├─ [Gateway] 进入 runKind=heartbeat 模式
             │               path: dist/auth-profiles-DRjqKE3G.js:48796
             │               filter: params.files.filter(f => f.name === "HEARTBEAT.md")
             │               其他 workspace 文件全部被排除
             ├─ [Adapter] system prompt = resolveHeartbeatPrompt(undefined)
             │               openclaw.json 无 agents.defaults.heartbeat.prompt
             │               → fallback 默认:
             │                 "Read HEARTBEAT.md if it exists (workspace context).
             │                  Follow it strictly.
             │                  Do not infer or repeat old tasks from prior chats.
             │                  If nothing needs attention, reply HEARTBEAT_OK."
             ├─ [Qwen3] 严格执行指令:
             │            读 HEARTBEAT.md → 看到 "任务完成 / 下一步监控"
             │            自行判定 "nothing needs attention"
             │            "Do not infer or repeat old tasks" 禁止它推理用户真实问题
             │            → 输出 "HEARTBEAT_OK"（12 字符）
             │            proxy 日志印证: TEXT=12 稳定（整段 22:09~23:04 所有请求）
             │
             ↓
        [Proxy → Gateway] LLM 响应 = "HEARTBEAT_OK"
             │
             ├─ [Gateway] stripTokenAtEdges() 识别 HEARTBEAT_TOKEN 剥离 12 字符
             │            path: dist/auth-profiles-DRjqKE3G.js:stripTokenAtEdges
             │            剥离后 text = ""
             │
             ↓
        [outbound = empty] Gateway 不发 WhatsApp 消息给用户
        [Gateway 日志] 无 "Sending message" 对应 outbound（印证 step 4 观察）
             │
04/19 22:09~23:04 用户发一堆问题 → 每条都被吞 → 用户以为 PA 死了
             │
04/19 深夜 (2h) 我（Claude Code）被卷入错误归因:
             ├─ proxy /stats last_error_code=502+p99=126s → 初判 primary/fallback
             ├─ 看到 auth "Bad MAC" 日志 → 误判 auth corruption
             ├─ 让用户做 rm -rf auth reset + QR 扫码路径  ❌ 原则 #28 违规
             └─ 用户回滚，无损失但浪费 2h
             │
04/20 早上 （本次开工）完整 E2E 调查:
             ├─ 第 1 轮: 查 SOUL.md FILE_MAP → 推断 research agent 缺 SOUL.md（错）
             ├─ 第 2 轮: 查 openclaw.json heartbeat 配置 → 推断 prompt 缺失（错）
             ├─ 第 3 轮: 查 agents.list 顺序 → 推断 routing fallback 到 research[0]（半对）
             ├─ 第 4 轮: diff 4/3 bak vs 4/10 cur → 发现 WhatsApp 插件安装（路由偏移证据）
             └─ 第 5 轮: 读 HEARTBEAT.md 内容 → 看到 PA 自己写的"任务完成" (09:09 mtime)
                         → 真相水落石出
             │
             ↓ 立即最小修复 ↓
04/20 09:00+  cat > HEARTBEAT.md 置为只含注释
04/20 09:16   用户发 "早上好 你在吗" → PA 5 秒后回复 ✅
```

---

## 三层根因

| 层 | 问题 | 发现 |
|---|---|---|
| **触发器** | PA 不知道 HEARTBEAT.md 是 OpenClaw 保留文件（无语义隔离），当普通 TODO 写入 | LLM 的 write 工具对 workspace 无约束 + OpenClaw 文件语义未传给 LLM |
| **放大器** | OpenClaw heartbeat 机制读到非空 HEARTBEAT.md → runKind=heartbeat → 只读 HEARTBEAT.md + 默认 prompt 含 "reply HEARTBEAT_OK" 硬性指令 → LLM 严格遵守 → 输出被 Gateway 剥离 → 完全静默 | 单个文件的状态（空 vs 非空）决定了整个 PA 对话链路的行为，但没有任何观察点 |
| **掩护者** | 无任何告警、无日志、无监控。Gateway 日志里连 HEARTBEAT 字样都没有（因为 `stripTokenAtEdges` 在写日志之前发生）。用户发现依赖人肉感知（"PA 不回消息了"） | MR-4 silent-failure 的典型形态 — 错误没被吞，是**正确的 LLM 输出被正确的 Gateway 剥离**，但结果对用户不可见 |

## 时间线还原表

| 时间 | 事件 | 影响 |
|---|---|---|
| 04/10 14:53 | WhatsApp 插件被安装 | 路由 main → research，但无察觉 |
| 04/19 09:09:50 | PA 写入 HEARTBEAT.md "任务完成/下一步监控" | heartbeat 激活阈值达成 |
| 04/19 09:09~22:09 | 13 小时无用户提问 | 潜伏期 |
| 04/19 22:09 | 用户开始发问题 | 每条被 HEARTBEAT_OK 吞 |
| 04/19 23:04 | 用户意识异常并向 Claude 反馈 | 错诊开始 |
| 04/19 深夜 | 我误判 Bad MAC，让用户 auth reset | 浪费 2h，幸无永久损失 |
| 04/20 早上 | 根因锁定 + 最小修复 | 5 秒验证通过 |

## 为什么以前没发生（六条件组合爆炸）

| 条件 | 以前 | 这次 |
|---|---|---|
| WhatsApp 插件已安装 | 4/10 之前未安装（core 处理 WhatsApp） | ✅ 4/10 起已安装 |
| PA 有 write 工具 + workspace 访问 | 一直有 | ✅ 一直有 |
| HEARTBEAT.md 是 OpenClaw 保留文件 | 一直是（OpenClaw 约定） | ✅ 一直是 |
| PA 不知道它有特殊语义 | 一直不知道（SOUL.md/CLAUDE.md 从未提过） | ✅ 一直不知道 |
| `agents.defaults.heartbeat.prompt` 已配置 | 一直未配置 → fallback 默认 prompt | ✅ 一直未配置 |
| HEARTBEAT.md 内容非空非注释 | **一直空** → heartbeat 不激活 | ❌ 4/19 09:09 被 PA 污染 |

**六项缺一不爆炸**。前五项长期存在，第六项单项触发就全链失效。

## MR-15 元规则候选：`reserved-files-must-not-be-writable-by-llm`

**提案**：任何被底层 runtime（OpenClaw / Gateway / Proxy / Adapter）赋予特殊语义的文件路径，
不能暴露给 LLM 的 write/edit 工具作为无约束写入目标。必须：

1. **声明层**：系统维护一份 `RESERVED_FILES` 明细表（路径 + 语义 + 违规后果）
2. **行为层**：SOUL.md 等 LLM system prompt 明确列出"禁止写"
3. **Proxy 拦截层**：检测到 LLM 试图写保留文件时 block/rewrite + 推 [SYSTEM_ALERT]
4. **测试层**：单测覆盖每个保留文件的检测路径
5. **治理层**：不变式锁定四层声明齐全

**与 MR-4 (silent-failure) 的关系**：MR-4 是"问题分类"，MR-15 是"上游预防层"——
MR-4 说"静默故障是 bug"，MR-15 说"别让 LLM 碰能制造静默故障的 runtime 文件"。

**与 MR-8 (copy-paste-is-a-bug-class) 的关系**：MR-8 阻止源码层重复制造 bug，
MR-15 阻止 runtime 边界层错误交互。两者都是架构硬规则，不是纪律问题。

**当前已知的保留文件**（V37.8.16 起维护在 `RESERVED_FILES`）：
- `~/.openclaw/workspace/HEARTBEAT.md` — OpenClaw heartbeat 激活控制
- `~/HEARTBEAT.md` — 同上（不同 workspace）
- 未来候选：`~/.openclaw/workspace/AGENTS.md` / `~/.openclaw/workspace/CLAUDE.md`（如有类似语义）

## 本体喂养

- 案例文档: `ontology/docs/cases/heartbeat_md_pa_self_silencing_case.md`（本文件）
- 不变式: `ontology/governance_ontology.yaml` INV-HB-001 `heartbeat-md-reserved-file-not-llm-writable`
- 元规则: MR-15 `reserved-files-must-not-be-writable-by-llm`
- SOUL.md 规则 11 `禁止写 HEARTBEAT.md`
- proxy_filters.py `RESERVED_FILES` + `detect_reserved_file_write()` 纯函数
- test_tool_proxy.py `TestReservedFileWriteBlock` 单测

## 元教训（MR-4 silent-failure 第 12 次演出）

演出史：V37.3 governance summary 吞 error → V37.4 Dream Map budget → V37.4.3 PA 告警污染 →
V37.5 kb_review 空 prompt → V37.6 KB dedup → V37.7 双跑审计 →
V37.8.6 Dream 自引用幻觉 → V37.8.7 ontology_sources 位置解析 →
V37.8.10 LLM 错误链稀释 → V37.8.11 auto_deploy 漂移噪声 →
V37.8.13 Gateway 宕 9h 静默 → **V37.8.16 PA 自残 HEARTBEAT.md（本案）**

**本次新形态**：不是错误被吞，不是错误被稀释，不是告警路径失效 ——
是 **LLM 正确响应系统 prompt，Gateway 正确剥离 ack token，但 LLM 误读了 system prompt 的适用范围**。
整条链路的每一环都"按设计工作"，但设计本身有语义 gap：
HEARTBEAT.md 作为 runtime 控制文件 vs LLM 视为普通 TODO 文件 —— 这个 gap 没被任何一层填补。

**MR-15 正是填补这种 gap**：把 runtime 边界的保留语义显式声明给所有可能触碰的层。

## 反面教训：2026-04-19 深夜我的原则 #28 违规

故障发生时我的错误路径：
1. 看到 proxy 502 + p99 超时 → **先猜 "primary/fallback 问题"**（违反"理解再动手"）
2. 看到 Gateway auth 日志 "Bad MAC" → **立刻联想到 auth corruption**（违反"先问这个问题之前存在吗"）
3. 让用户 `rm -rf auth` + QR 扫码重新登录 → **不可逆操作，幸好用户回滚**

正确路径（今天走的）：
1. 读 status.json focus + 昨晚诊断笔记（理解现状）
2. 查 git log 最近改动（排除自己改动引入 — Q2）
3. 读 OpenClaw 源码 resolveHeartbeatPrompt 调用点（锁定 heartbeat 配置来源）
4. diff 4/3 vs 4/10 openclaw.json（锁定唯一 config 改动 = 插件安装）
5. 读 HEARTBEAT.md 实际内容（锁定真正触发点 = PA 自写）

**六轮假说全错五轮才到达根因**。每次假说被证伪都应该重置，不要累积成执念。
原则 #28 不是口号，是硬纪律。

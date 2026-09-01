# OpenClaw Gateway 升级评估：v2026.3.13-1 → v2026.4.x

> 初次评估：2026-04-04
> 二次评估：2026-04-10（上游已到 v2026.4.9，#59265 仍 OPEN）
> 三次评估：2026-04-29（实证查明：#59265 已 closed but no fix evidence + 发现 v2026.4.26 新硬阻塞 #73358 + 引入 tripwire 决策框架）
> 四次评估：2026-05-05（#73358 已 v2026.4.27 修复，战略路径已开，推荐方案 C 等 5.x 沉淀，第十三节）
> 五次评估：2026-06-08（上游 v2026.6.1，6.x 加 SQLite/plugin breaking，#59265 仍无 fix，用户决策 hold 到 6/20 时间表，目标 v2026.4.27，第十四节）
> **升级执行：2026-06-11 完成 3.13-1 → v2026.4.27**（前置验证当日 GO，实录见第十五节）
> 六次评估：2026-07-04（深评「4.27 能否无损继续升级」→ 继续 hold；三结构性迁移 M1/M2/M3 + 回滚退化为有损单向门；设三条收敛判据，第十七节）
> 七次评估：2026-07-20（2026.7.1 stable 触发判据跟踪 → 继续 hold，判据全未满足；Node 门槛升为区间黑名单，第十八节）
> 八次评估：2026-09-01（2026.8.1 stable 触发判据跟踪 → 继续 hold；判据 ② 首次满足、① 仍未满足、③ 待我方确认；新增「默认自主行为扩张」风险与可量化持有成本，第十九节）
> 评估者：Claude Code
>
> 🔴 **当前态（本行为单一真理源，其余章节均为各自时点快照）**
> 部署版本 **v2026.4.27**（2026-06-11 起）| 上游 latest **2026.8.1**（2026-08-31）|
> 决策 **继续 hold**（第八次评估，2026-09-01）| 判据与下次跟踪点见 **第十九节 19.7**

---

## 一、版本概览

> ⚠️ **2026-04 初次评估时点快照**（勿作当前判断依据）。当前部署版本与上游 latest 见文档头部「当前态」行。

| 项目 | 值（初次评估时） |
|------|------|
| 当时部署版本 | v2026.3.13-1（已于 2026-06-11 升级到 v2026.4.27） |
| 原 hold 条件 | 等 @openclaw/whatsapp 正式发布 + ClawHub 429 修复 |
| 最新稳定版 | **v2026.4.9**（2026-04-10 确认，npm 可用） |
| 上次评估最新版 | v2026.4.2（2026-04-03） |
| 中间版本 | v2026.3.23 → 3.23-2 → 3.24 → 3.28 → 3.31 → 4.1 → 4.2 → 4.5 → 4.7 → 4.8 → 4.9 |

## 二、原 Hold 条件评估

### 条件 1：@openclaw/whatsapp 正式发布 → **已满足**

- WhatsApp sidecar 在 v2026.3.23 已重新打包为 bundled plugin（`dist/extensions/whatsapp/light-runtime-api.js` 随 npm tarball 分发）
- v2026.4.1 进一步改进：WhatsApp inbound message timestamps 注入 model context
- 相关 issue #52838（WhatsApp silently broken）已关闭
- 相关 issue #53247（missing light-runtime-api crash）已关闭

### 条件 2：ClawHub 429 #54446 → **仍未修复，但已不阻塞**

- ClawHub 429 是 marketplace 服务端限流问题，影响 `openclaw plugins install` 从 ClawHub 安装
- WhatsApp 已改为 bundled 分发（不再需要从 ClawHub 下载），因此 429 不影响 WhatsApp 功能
- 结论：此条件降级为**非阻塞**

**Hold 条件综合判定：已满足，可以评估升级。**

## 三、v2026.4.1 新功能（与我们相关的）

| 功能 | 影响 | 价值 |
|------|------|------|
| **WhatsApp timestamp 注入** | 消息时间戳传入 model context | 中：PA 可感知消息发送时间 |
| **`/tasks` 任务面板** | 会话内查看后台任务状态 | 低：我们用 system crontab |
| **Per-job tool allowlists** (`openclaw cron --tools`) | cron 任务可指定工具子集 | 低：我们的 cron 多数是 system crontab |
| **Bundled SearXNG provider** | 自托管搜索引擎 | 低：我们用 Brave Search |
| **Amazon Bedrock/Guardrails** | 新 provider 支持 | 无：我们用自定义 qwen-local |
| **Plugin allowlist 兼容** | bundled channel plugins 在限制性 allowlist 下仍可加载 | 中：确保 WhatsApp 不被意外屏蔽 |

## 四、Breaking Changes（关键风险）

### 4.1 配置迁移策略变更（⚠️ 中风险）

**变更**：超过 2 个月的 legacy config key 不再自动迁移，改为 validation 失败。

**影响评估**：
- 我们的 `openclaw.json` 在 v2026.3.13-1 时代创建
- 需要在升级前运行 `openclaw doctor --fix` 检查和修复 legacy key
- 如果有 2 个月前的旧格式 key，升级后 Gateway 可能无法启动

**缓解**：升级前先备份 `~/.openclaw/openclaw.json`，运行 `openclaw doctor --fix`

### 4.2 Plugin SDK 废弃旧接口（⚠️ 低风险）

**变更**：Plugin SDK 废弃 legacy provider compat subpaths + 旧 bundled provider 设置。

**影响评估**：
- 我们不使用自定义 plugin，风险低
- 但 WhatsApp/Discord bundled plugins 的内部加载路径可能变化
- 升级后需验证 `openclaw channels status --probe`

### 4.3 qwen-portal-auth 移除（✅ 无影响）

**变更**：移除 portal.qwen.ai OAuth，需迁移到 Model Studio。

**影响评估**：我们通过自建 Adapter(:5001) 对接远程 GPU，不使用 qwen-portal-auth。**零影响**。

### 4.4 x_search 配置路径变更（✅ 无影响）

**变更**：x_search 从 `core tools.web.x_search.*` 移到 `plugins.entries.xai.config.*`。

**影响评估**：我们不使用 x_search（用 Brave Search）。**零影响**。

## 五、已知 Bug 与新增风险

### 5.1 #59265: Agents working in secret — no actions visible in chat（⚠️⚠️ 高风险）

**描述**：Agent 在后台执行操作，但 chat 中不显示任何 action。
**状态**：OPEN，未修复，无 assignee。**v2026.4.2 macOS 上也已确认复现**。
**症状**：Chat history 消失、agent 输出不可见、WebSocket 断连重连 (code 1001)。
**关联**：可能与 auto-failover 功能有关。

**影响评估**：
- 如果影响 WhatsApp 通道，用户将看不到 PA 的工具调用过程
- **v2026.4.2 未修复此问题**
- **建议**：此 bug 是当前最大升级阻塞，等修复后再考虑

### 5.2 `trusted-proxy` auth 变更（⚠️⚠️ 高风险，v2026.3.31）

**变更**：拒绝混合 shared-token 配置，local-direct fallback 需要配置 token，不再隐式信任同主机调用。

**影响评估**：
- 我们的 Tool Proxy(:5002) 转发请求到 Gateway(:18789)，都在 localhost
- 如果 Gateway 之前隐式信任 localhost 调用，此变更可能**中断 Proxy→Gateway 链路**
- **必须在升级前确认** `openclaw.json` 中的 auth 配置是否充分

### 5.3 #58701: v2026.3.31 bundled plugin runtime deps（✅ 已修复）

**描述**：v2026.3.31 npm tarball 缺少 grammy、@aws-sdk 等依赖。
**状态**：CLOSED，v2026.4.1 已修复。

### 5.4 Exec 环境安全加固（⚠️ 中风险，v2026.3.31）

**变更**：exec 环境屏蔽 proxy/TLS/Docker/Python 包索引/编译器路径等环境变量。
**影响评估**：我们的 cron 脚本通过 `bash -lc` 加载环境。如果 Gateway exec 工具屏蔽了某些 env，可能影响 openclaw cron 内的 agent 任务。System crontab 不受影响。

## 六、我们的集成点风险矩阵

### 6.1 高影响集成点

| 集成点 | 调用量 | 升级风险 | 验证方法 |
|--------|--------|----------|----------|
| `openclaw message send` (WhatsApp) | 35+ 处 | 🟡 中 | `openclaw message send --channel whatsapp -t "$PHONE" -m "test"` |
| `openclaw message send` (Discord) | 35+ 处 | 🟡 中 | `openclaw message send --channel discord -t "$DISCORD_TARGET" -m "test"` |
| Gateway :18789 /health | 8+ 脚本 | 🟢 低 | `curl -s http://localhost:18789/health` |
| `openclaw.json` 配置 | 核心 | 🟡 中 | `openclaw doctor --fix` + 启动验证 |
| Session 管理 | 每 6h 清理 | 🟢 低 | 清理脚本用 rm，不依赖 Gateway API |
| launchd KeepAlive | 进程管理 | 🟢 低 | plist 不随 npm 升级变化 |
| 媒体存储路径 | 图片理解 | 🟡 中 | 发送图片 → 检查 `~/.openclaw/media/inbound/` |

### 6.2 Tool Proxy 兼容性

| 关注点 | 风险 | 说明 |
|--------|------|------|
| OpenAI-compatible API 格式 | 🟢 低 | Gateway → Proxy(:5002) 的请求格式是 OpenAI 标准，不太可能变 |
| 工具 schema 格式 | 🟡 中 | 如果 Gateway 改变工具 schema 传递方式，proxy_filters 可能需要调整 |
| SSE 响应格式 | 🟢 低 | 标准 SSE 格式，变化可能性低 |
| `sessions_spawn`/`sessions_send` | 🟡 中 | 多 Agent 功能可能有行为变化 |

## 七、升级 SOP（如决定升级）

### 7.0 前置条件

> **目标版本不在本节硬编码**——由**最近一次评估的结论**决定（评估节按时间倒序：第十九节 = 最新）。
> 截至第八次评估（2026-09-01）结论为**继续 hold，无目标版本**；本 SOP 仅在用户明确决定升级后启用。

- [ ] **目标版本**：读最新评估节的结论确定（当前第十九节 19.7）。**禁止沿用本文档任何历史节里的版本号**——它们是当时的时点判断。
- [ ] **时间窗口**：工作日白天，确保能快速处置（注意：回滚不再是无损的，见 7.5）
- [ ] **在 Mac Mini 上 SSH 直连执行**（禁止通过 WhatsApp 触发）
- [ ] **前置 A · Node 版本区间**（第七次评估 18.4 起）：确认 `node -v` 落在目标版本 `engines.node` 声明的区间内。7.1/8.1 的区间是 `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`（**区间黑名单**，排除 node 23.x 全部与 24.0–24.14），根因是 SQLite WAL 数据损坏安全。**不能只确认「够新」**。
- [ ] **前置 B · 外部插件锁步**（第八次评估 19.5）：M1 插件外部化后，`@openclaw/whatsapp` / `@openclaw/discord` 与 core **同版本发布**且声明 `peerDependencies: { openclaw: '>=<同版本>' }`。确认目标版本对应的 channel 插件存在；同时确认第三方插件（如 `@tencent-weixin/openclaw-weixin`）的 `peerDependencies.openclaw` 与目标版本相容。
- [ ] **前置 C · 默认行为审计**（第八次评估 19.4，**须在首次启动前完成**）：目标 ≥8.1 时，以下 **7 项默认变更**（6 项自主行为 + 1 项并发假设）默认为开/生效，其中 3 项踩我们已立案的血案机制，逐项决定关闭或显式接受：
  - [ ] Grounded dreaming（后台 LLM 记忆整合，#114819）→ 对应 `dream_quota_blast_radius_case`
  - [ ] Owner-directed ambient heartbeat（#121988）→ 对应 `heartbeat_md_pa_self_silencing_case`
  - [ ] Session reset default 变更（无 reset policy 时跨闲置/跨天保留会话，#111140）→ 对应 `pa_alert_contamination_case`
  - [ ] Personal conversation recall（Active Memory 开启时默认召回同 agent 私聊上下文，#110597）→ 同上下文污染家族
  - [ ] Automatic self-learning 自动应用技能（#115576）
  - [ ] Skill Workshop 无额外批准提示（#107690，`skills.workshop.approvalPolicy: "pending"` 可找回审批门）
  - [ ] CPU-scaled foreground concurrency 8–16（#114047）→ 与我方 12 工具 / 200KB / 单 adapter 链路假设的负载核对

### 7.1 升级前备份（5 分钟）

先记录当前版本（**7.5 回滚依赖这个文件，不要跳过**），再做配置与全量状态备份。目标 ≥6.x 时
SQLite 迁移含 cleanup，**必须有全量 `~/.openclaw` 快照**，仅备份 json 不足以回滚。

```bash
openclaw --version > ~/upgrade_before_version.txt
cat ~/upgrade_before_version.txt
```

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak-$(date +%Y%m%d)
cp ~/.openclaw/cron/jobs.json ~/.openclaw/cron/jobs.json.bak-$(date +%Y%m%d)
cp -r ~/.openclaw/workspace/.openclaw/ ~/openclaw_workspace_backup_$(date +%Y%m%d)/
```

```bash
tar -czf ~/openclaw_full_snapshot_$(date +%Y%m%d).tar.gz -C ~ .openclaw
ls -lh ~/openclaw_full_snapshot_$(date +%Y%m%d).tar.gz
```

目标 ≥8.1 时另有官方 SQLite 快照（`openclaw backup sqlite`，#105718，create/list/verify/restore）——
它属于**新版能力**，升级前的旧版没有，因此上面的 tar 全量快照仍是回滚的唯一依据。

### 7.2 升级前检查（3 分钟）

```bash
bash ~/openclaw-model-bridge/check_upgrade.sh
bash ~/openclaw-model-bridge/preflight_check.sh --full
openclaw doctor
```

```bash
node -v
npm view openclaw@<目标版本> engines.node
```

`node -v` 必须落在 `engines.node` 输出的区间内（前置 A）。

### 7.3 执行升级（5 分钟）

```bash
openclaw gateway stop 2>/dev/null || true
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
```

```bash
npm install -g openclaw@<目标版本>
openclaw --version
```

```bash
openclaw doctor --fix
```

`doctor --fix` 会做 legacy config 迁移 + 外部 channel 插件安装（M1 后 WhatsApp/Discord 不在核心包内，
需网络安装，ClawHub 限流史见第二节条件 2）+ 目标 ≥8.1 时的 OpenProse 移除与 OpenAI route 迁移。

```bash
bash ~/restart.sh
```

### 7.4 升级后验证（10 分钟）

```bash
openclaw --version
curl -s http://localhost:18789/health
curl -s http://localhost:5002/health
curl -s http://localhost:5001/v1/models
```

```bash
openclaw message send --channel discord -t "user:$DISCORD_TARGET" -m "升级验证 $(openclaw --version)"
openclaw message send --channel whatsapp -t "$OPENCLAW_PHONE" -m "升级验证 $(openclaw --version)"
```

```bash
bash ~/openclaw-model-bridge/preflight_check.sh --full
bash ~/openclaw-model-bridge/job_smoke_test.sh
openclaw channels status --probe
```

手动业务验证（原则 #13，单测不能替代）：WhatsApp 发消息确认 PA 正常回复 → 发图片确认多模态路由 →
触发 search_kb 确认混合检索。目标 ≥8.1 时另需复核前置 C 的 6 项默认开关是否按决定生效。

### 7.5 回滚（视目标版本，**可能有损**）

> 🔴 **「30 秒无损回滚」自 6.x 起不再成立**（第六次评估 17.4）。SQLite 状态迁移含 cleanup：
> 回滚到旧版后，旧版读不回已迁移的 cron/auth 状态，凭据丢失会让 WhatsApp 重新链接撞设备限流（408）。
> 目标 ≥6.x 时回滚 = **恢复 7.1 的全量快照 + 丢弃升级窗口内产生的新状态**，不是无损操作。
> 8.1 侧的 newer-database-state fence（#132916/#133081）保护方向是**向前**（新版遇到更新 schema 不 restart-loop），
> 回滚方向不受它保护。

回滚版本从 7.1 记录的文件读回，**不硬编码**：

```bash
openclaw gateway stop 2>/dev/null || true
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
```

```bash
PREV=$(tr -d ' \t\n' < ~/upgrade_before_version.txt)
echo "$PREV"
npm install -g "openclaw@$PREV"
```

若 `~/upgrade_before_version.txt` 不存在或内容不是版本号，**停下人工确认**，不要猜测版本。

```bash
cp ~/.openclaw/openclaw.json.bak-$(date +%Y%m%d) ~/.openclaw/openclaw.json
```

目标 ≥6.x 时改为恢复全量快照（先把当前目录挪走保留取证）：

```bash
mv ~/.openclaw ~/.openclaw.failed-$(date +%Y%m%d%H%M)
tar -xzf ~/openclaw_full_snapshot_$(date +%Y%m%d).tar.gz -C ~
```

```bash
bash ~/restart.sh
curl -s http://localhost:5002/health
openclaw message send --channel discord -t "user:$DISCORD_TARGET" -m "回滚完成 $(openclaw --version)"
```

## 八、综合评估

> ⚠️ **2026-04 时点快照，勿作当前判断依据**。本节的收益/风险/建议（含选项 A/B/C = hold / 升 4.2 / 升 4.1）
> 是首次评估时的判断；此后 4.27 已于 2026-06-11 升级完成，且第六/七/八次评估的方案 A/B/C 是**另一套语义**
> （A=hold / B=中间版本 / C=现升最新）。**当前判断以最新评估节（第十九节）为准**，本节仅作历史留档。

### 升级收益
1. **WhatsApp 稳定性提升**：bundled sidecar + crash fix + timestamp
2. **Plugin 兼容性改善**：restrictive allowlist 下仍可加载
3. **跟进上游**：缩小版本差距（3.13 → 4.x），减少未来升级跨度

### 升级风险
1. **🟡 config 兼容性**：legacy key validation 变严格，需 `openclaw doctor --fix`
2. **🟡 #59265 bug**：Agent actions 不可见（需确认是否已修复）
3. **🟡 Plugin SDK 变更**：旧接口废弃，可能影响 channel 加载
4. **🟢 API 兼容性**：OpenAI-compatible API 格式不太可能变

### 建议

| 选项 | 描述 | 推荐度 |
|------|------|--------|
| **A. 继续 hold（更新阻塞原因）** | 等 #59265 修复 + trusted-proxy 确认 | ⭐⭐⭐⭐⭐ 推荐 |
| **B. 升级到 v2026.4.2** | 最新版，但 #59265 在 macOS 已确认复现 | ⭐⭐ |
| **C. 升级到 v2026.4.1** | 有 #59265 + 未修的 deps 问题 | ⭐ |

**推荐方案 A**：继续 hold，但更新阻塞原因。理由：
- **#59265（agent actions 不可见）在 v2026.4.2 macOS 上已确认复现**，无修复，无 workaround
- `trusted-proxy` auth 变更可能中断 Proxy→Gateway 链路，需先研究确认
- 原 hold 条件（WhatsApp sidecar）已满足，但出现了新的阻塞
- 版本差距确实在增大，但功能稳定性优先于版本跟进

**新 hold 条件**：
1. #59265 关闭或确认不影响 WhatsApp + macOS + 自定义 provider
2. `trusted-proxy` auth 变更对 localhost proxy 链路的影响确认
3. 目标版本至少 v2026.4.10+（#59265 修复版本）

**下次检查时机**：每周一 `check_upgrade.sh` + 关注 #59265 进展

---

## 十、二次评估记录（2026-04-10）

### 背景

上游从 v2026.4.2 推进到 **v2026.4.9**（7 个新版本），重新评估阻塞条件。

### 阻塞条件复查

| 阻塞项 | v2026.4.2 时 | v2026.4.9 时 | 结论 |
|--------|-------------|-------------|------|
| **#59265: Agent actions 不可见** | OPEN | **仍 OPEN**（最后更新 2026-04-03，一周无动静） | 硬阻塞未解除 |
| **trusted-proxy auth 变更** | 未验证 | v2026.4.8 有 proxy 相关变更（Slack outbound），但非 localhost trust 问题 | 未解除 |
| **新增：v2026.4.5 config alias 移除** | — | legacy config aliases 移除（有 `doctor --fix` 迁移路径） | 新增中风险 |

### v2026.4.3~4.9 关键变更（与我们相关）

| 版本 | 变更 | 影响 |
|------|------|------|
| v2026.4.5 | **Legacy config aliases 移除**（breaking） | 中：升级前需 `openclaw doctor --fix` |
| v2026.4.8 | HTTP(S) proxy 支持 Socket Mode WebSocket；trusted env-proxy 模式 | 低：Slack 相关，不影响我们 |
| v2026.4.9 | `providerAuthAliases`（provider 声明 auth 别名共享）；Memory/Dreaming 改进 | 低：长期有价值但非紧急 |
| v2026.4.3~4.9 | **#59265 未出现在任何版本 fix 列表中** | 确认未修复 |

### 二次评估结论

**继续 hold，理由不变且更充分**：
1. #59265 经过 7 个版本仍未修复，说明是深层 bug，短期不会解决
2. 版本跨度从 6 个增加到 11 个中间版本，升级风险反而更大
3. v2026.4.5 新增 config breaking change，增加一个迁移步骤
4. 当前 v2026.3.13-1 运行稳定（718 tests pass，三层服务 ok）
5. 无功能缺失或 bug 驱动升级

**下次检查**：关注 #59265 状态变化（`curl -s https://api.github.com/repos/openclaw/openclaw/issues/59265 | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'#{d[\"number\"]}: {d[\"state\"]}')"）

---

## 十一、三次评估记录（2026-04-29，实证版）

### 背景

上游从 v2026.4.9 (4/10) 推进到 **v2026.4.26**（2026-04-28 发布，最新稳定版），共 19 个 stable 版本（4/1 ~ 4/28），加上 beta 链 30+ 个中间版本。距上次评估 19 天，距 V37.8.15 (4/16 changelog) 评估 13 天。

**本次评估方法升级**：从"按节奏推断"升级到"实证调查"——通过 WebFetch 直接拉 GitHub issue 页 / release notes / open bugs list，得到事实数据后再评估。

### 上游版本演进（v2026.4.9 → v2026.4.26）

| 版本 | 日期 | 备注 |
|------|------|------|
| v2026.4.5 | 2026-04-06 | （legacy config alias 移除，已在二次评估覆盖） |
| v2026.4.7 / 4.7-1 | 2026-04-08 | minor releases |
| v2026.4.8 | 2026-04-08 | （HTTP proxy 改进，已在二次评估覆盖） |
| v2026.4.9 / 4.9-beta.1 | 2026-04-09 | （二次评估末点） |
| v2026.4.25-beta.1~9 | 2026-04-26 | beta 链 |
| v2026.4.26-beta.1 | 2026-04-27 | beta |
| **v2026.4.26** | **2026-04-28** | **最新稳定版** |

> 注：v2026.4.10~24 区间 npm registry 未列出 stable 版本（仅 4.25-beta 系列），从 4.9 直接跳到 4.25/4.26。

### 实证发现（用户授权 WebFetch 后）

#### 发现 1：#59265 已 closed at 4/25，但 **NO FIX EVIDENCE**

| 数据点 | 来源 | 结论 |
|---|---|---|
| state | issue page + search results 双确认 | closed as "completed" at 2026-04-25 |
| Development sidebar | issue page 直接拉取 | **"No branches or pull requests"** |
| Relationships | issue page 直接拉取 | **"None yet"** |
| v2026.4.26 changelog | 完整拉取 ~150 个 fix item | **无一处提及 #59265** |
| v2026.4.25 changelog | release notes（部分截断） | 可见部分**无 #59265 引用** |

**判断**：closed 可能是 reporter 自助/maintainer 标 stale/相关 PR 间接修复但未明确归功——**不是有据可查的 verified fix**。按"理解再动手"原则 #28，**没有 PR 证据不能假设修复有效**。

#### 发现 2：v2026.4.26 引入新硬阻塞 #73358（直接 dealbreaker）

> 标题：*v2026.4.26 ships `coding-agent` skill + `codex` provider with `openai/gpt-5.5` as silent default — breaks stacks without OpenAI configured*

| 维度 | 详情 |
|---|---|
| 状态 | issue 已 closed (报告 + workaround 完成) |
| 行为 | v2026.4.26 silently 启用 `coding-agent` skill + `codex` provider，silent default 调 `openai/gpt-5.5` |
| 症状 | Gateway 启动正常，但 "every chat lane fails before reply" 报 `No API key found for provider 'openai'` |
| **直接命中我们** | qwen-local + gemini fallback，**无 OpenAI key** → 升级即 WhatsApp PA 全断 |
| workaround | 手动 (1) 删 `models.json` 中 `codex` provider (2) strip OpenAI entries from catalog (3) 显式 `coding-agent` disable (4) 重启 Gateway |
| 严重度 | feature-blocking（Gateway 起来但所有对话失败） |

#### 发现 3：v2026.4.26 其他可能影响我们的变更（≥150 fix item 中筛出）

- **#40024 Local models**：custom providers with only `baseUrl` defaulted to Chat Completions adapter — 我们的 qwen-local 路由策略可能改变
- **#59681 Agents/sessions_spawn**：解析 bare model alias 改用 target agent runtime default provider — 可能影响 Multi-Agent
- **plugin manifests 重构**：pre-runtime model-id normalization 移到 plugin manifests — 可能影响 qwen-local 注册方式
- **trusted-proxy auth**：本次 release notes **未提及修改**，所以二次评估提出的 v2026.3.31 影响**仍未做 localhost 兼容性验证**

#### 发现 4：上游 open bugs 中有 5+ critical/regression 级

`#46531 gateway crash-loop` / `#46733 opus 4.6 broken` / `#46637 reasoning_content JSON parse` / `#46786 elevated.enabled breaks exec` / `#47487 tool restrictions not enforced` —— 上游本身在持续产生 regression bugs，"升级到 latest" ≠ "升级到 stable"。

### 阻塞条件复查（实证后）

| 阻塞项 | 二次评估时（v2026.4.9） | 三次评估时（v2026.4.26） | 结论 |
|--------|------------------------|--------------------------|------|
| **#59265: Agent actions 不可见** | OPEN（v2026.4.2 macOS 复现） | **closed at 4/25 but no PR / no release notes mention** | **状态变了实质未变**——不能基于 GitHub status label 升级 |
| **trusted-proxy auth 变更** | 未做 localhost 链路验证 | **仍未做验证**（v2026.4.26 无相关变更） | 未解除 |
| **v2026.4.5 legacy config alias 移除** | 新增中风险 | 仍生效（升级时仍需 `openclaw doctor --fix`） | 未解除 |
| **新增：#73358 OpenAI silent default** | — | **v2026.4.26 引入，直接命中我们的 qwen-local + 无 OpenAI key 配置** | **新硬阻塞，直接 dealbreaker** |

### 三次评估结论：**继续 hold，但理由完全不同了**

实证后 hold 理由比早上的推断版本**更强**：
1. **#59265 closed 但无 verified fix** — V37.8.15 教训反向适用："上游 status 变化 ≠ 实质修复"
2. **#73358 是新硬阻塞** — 升级 v2026.4.26 即业务中断，未来即使决定升级也必须先在 dev/shadow 环境验证 workaround
3. **跨度未减小** — 30+ 中间版本 + ~150 fix 累积破坏面巨大
4. **上游 regression 风险** — "latest" 不等于 "stable"，5+ 个 critical open bugs 证明持续动荡

---

## 十二、Tripwire 决策框架（V37.9.22 引入）

### 12.1 战略矛盾

- **不升级风险**：版本债务持续累积（30+ → 50+ → 无限），未来某天必须升级时跨度太大失败概率指数上升
- **升级风险**：每个时点都有当时具体的 dealbreaker（如今天的 #73358）
- **以前的方法**："看到新版本就评估" → 容易陷入"是否升级"的二元决策疲劳

### 12.2 新方法：Tripwire-Based Upgrade Trigger

不再"是否升级"二元决策，**预先声明 6 条触发条件**，0/6 触发时自动 hold，任一触发时启动正式评估流程（不是立即升级，是"正式评估 → 选定目标版本 → dev 验证 → 维护窗口切换"）。

| # | Tripwire | 自动化 | 阈值 | 触发后行为 |
|---|---|---|---|---|
| 1 | **时间上限** | ✅ | 距上次正式评估 ≥ 180 天 | 启动正式评估 |
| 2 | **版本差距** | ✅ | 上游 stable 版本差 ≥ 50 个 | 启动正式评估 |
| 3 | **EOL 信号** | ✅ | latest release notes 含 "v2026.3 / EOL / deprecated v2026 / no longer supported" | 立即启动正式评估 |
| 4 | **WhatsApp plugin 破坏性变更** | ✅ | latest release notes 的 "Breaking" section 含 whatsapp 提及 | 立即启动正式评估 |
| 5 | **CVE 命中** | ⚠️ 半自动 | `~/.openclaw_cve_alert` 文件存在（人工写入） | 立即启动正式评估 |
| 6 | **业务痛点** | ⚠️ 半自动 | `~/.openclaw_pain_point` 文件存在（人工写入） | 启动正式评估 |

**实现**：`check_upgrade.sh` V37.9.22 重写，每周一 cron 运行，6 条全部状态可见（不静默吞 — V37.3 INV-GOV-001 同款），任一触发推送告警但不自动升级。

### 12.3 升级路径选项对比（如未来某天 tripwire 触发）

| 方案 | 跨度 | 风险 | 工程成本 | 适用场景 |
|---|---|---|---|---|
| **A. 完全 hold** | 0 | 0 | 0 | 已被 tripwire 否决（仅初始默认状态） |
| **B. 直跳 latest + workaround** | 大 | 高（多 dealbreaker 累积 + workaround 在 dev 难验证） | 中 | 不推荐 |
| **C. 阶梯到中间稳定版** | 中 | 中（避主 dealbreaker 但仍多 breaking change） | 中 | 时间不紧迫且能找到"刚好避开" dealbreaker 的版本 |
| **D. 先建 shadow 演练机制再决定** | — | 0 | 高（需 docker / Mac Mini 副本 + 流量复制） | 跨度极大或多 dealbreaker 时 |
| **F. 等下一稳定窗口（推荐 default）** | 中 | 低 | 低 | 等上游修当前 dealbreaker（如 v2026.4.27+ 修 #73358） |

### 12.4 选定的下次升级路径模板（条件式）

**当 tripwire 触发，按以下顺序判断**：

1. **检查当前 latest 是否有 dealbreaker**（如今天的 #73358）
   - 有 → 选 **方案 C**（阶梯到 dealbreaker 引入前的最近稳定版，如 v2026.4.23）或 **方案 F**（等修复）
   - 无 → 进入第 2 步
2. **检查跨度**
   - ≥ 30 中间版本 → **方案 D**（shadow 演练）
   - < 30 → **方案 C** 直接升级
3. **检查 #59265 是否有 verified fix**
   - 有 → 减一个风险点
   - 无 → 升级前必须备好回滚预案 + WhatsApp 立即可用性验证

### 12.5 触发后的标准流程

1. `check_upgrade.sh` 输出 tripwire 状态 + 启动正式评估提示
2. 阅读本文档第十二节决策矩阵选定方案
3. 在非生产环境（dev 或 Mac Mini 临时副本）dry-run
4. 跑 `preflight_check.sh --full` + `job_smoke_test.sh` + WhatsApp E2E
5. 通过后选维护窗口（深夜 + 用户在线）切换 + 30 秒回滚预案
6. 升级成功后更新 `LAST_EVAL_DATE` 至升级日期（重置时间 tripwire）

### 12.6 下次定期检查

- **每周一 cron**：`check_upgrade.sh` 自动跑，0/6 触发时静默通过
- **任一 tripwire 触发**：脚本退出码 1，通过 cron 失败邮件 / WhatsApp 推送告警
- **180 天硬性上限**（~ 2026-10-26）：即使 0/6 触发，时间 tripwire 自动触发启动正式评估

---

## 十三、第四次评估记录（2026-05-05，Tripwire 框架首次复评）

### 13.1 背景

上游从 v2026.4.26（4/28）推进到 **v2026.5.3-1**（2026-05-04，最新稳定版），7 天内推出 5 个新 stable + 主版本号从 4.x 跳到 **5.x**。距三次评估 6 天。

**重要澄清**：5.x **不是 semver major bump**，是**日历版本号**（年.月.patch）—— 5 月到了自然跳 5.x，**不暗示架构性破坏变更**。

**评估方法**：本次首次跑 V37.9.22 Tripwire 框架自动判定 + 实证 WebFetch 关键 release notes，对比三次评估时的阻塞条件矩阵。

### 13.2 上游版本演进（v2026.4.26 → v2026.5.3-1）

| 版本 | 日期 | 关键内容 |
|------|------|---------|
| v2026.4.27 | 2026-04-29 | **🔥 修复 #73358** — release notes 显式写："require explicit `skills.entries.coding-agent.enabled` before exposing the bundled coding-agent skill, so installs with Codex on PATH but no OpenAI auth do not silently offer Codex delegation" |
| v2026.4.29 | 2026-04-30 | **⚠️ 新破坏性变更**："Security/tools: configured tool sections (`tools.exec`, `tools.fs`) no longer implicitly widen restrictive profiles" — restrictive profile 用户必须显式 `alsoAllow` |
| v2026.5.2 | 2026-05-03 | 大量改进：plugin manifest `contracts.tools` 强制为工具注册的 ownership 契约；thread-binding toggle 迁移（`threadBindings.spawnSessions` 替代 split toggles，有 `doctor --fix` 自动迁移）；Codex native runtime 标准化（无 silent default） |
| v2026.5.3 | 2026-05-04 | 性能优化（lazy-loading / defer timers / startup path trimming）+ 插件加固 + macOS LaunchAgent upgrade recovery + ~70 fixes |
| v2026.5.3-1 | 2026-05-04 | hotfix：plugin install scanner 误判官方 bundled 修复 |

### 13.3 Tripwire 状态（dev 环境跑 `bash check_upgrade.sh`）

```
✅ [1/6] 时间上限: 6/180 天 (剩 174 天)
✅ [2/6] 版本差距: 34/50 stable (剩 16)
✅ [3/6] EOL 信号: latest release 未检出
✅ [4/6] WhatsApp 破坏性: latest release 未检出
✅ [5/6] CVE: 无人工标记
✅ [6/6] 业务痛点: 无人工标记

结论: ✅ 继续 hold (0/6 tripwire 触发)
```

**自动化判定：继续 hold**。但人工实证仍要做（验证自动化是否漏报）。

### 13.4 阻塞条件复查（实证 vs 三次评估）

| 阻塞项 | 三次评估时（4/29） | 四次评估时（5/5） | 变化 |
|--------|----|----|----|
| **#73358 codex/gpt-5.5 silent default** | 硬阻塞 dealbreaker | **✅ v2026.4.27 release notes 显式修复** — 这是 verified fix evidence（不像 #59265 只 closed but no PR） | 🟢 **解除** |
| **#59265 Agent actions 不可见** | closed but no PR / no fix evidence | **仍无 PR / 无版本 fix mention**（本次 WebFetch 再确认 issue page "No branches or pull requests" + 后续版本 release notes 无引用） | ⚪ 无变化 |
| **trusted-proxy auth**（v2026.3.31） | 未做 localhost 验证 | v2026.4.29 进一步收紧（IPv6 ULA opt-in）；仍未验证我们 localhost 链路 | ⚪ 无变化 |
| **v2026.4.5 legacy config alias 移除** | 中风险 | 仍生效；v2026.5.2 新增 thread-binding toggle 迁移（有 `doctor --fix` 自动） | ⚪ 不变 |
| **新增：v2026.4.29 `tools.exec`/`tools.fs` 不再隐式扩展 restrictive profile** | — | 我们的 proxy_filters 工具白名单可能依赖 OpenClaw 的 tool-section 暴露机制，需在 dev/shadow 验证升级后 12 工具集是否仍可见 | 🟡 **新中风险** |
| **新增：v2026.5.2 plugin manifest `contracts.tools`** | — | 强制 manifest ownership 契约 — 可能影响 qwen-local provider 的注册路径（adapter.py 启动时如何向 Gateway 注册） | 🟡 **新中风险** |
| **跨度** | 19 stable | **34 stable**（+15） | 🔴 增大 |

### 13.5 战略局面变化

**4/29 三次评估时**：hold "indefinitely"——`#73358` 不修就不能升，没有时间表。

**5/5 四次评估时**：hold "tactically"——核心 dealbreaker 已修，**升级路径变得明朗**：
1. 等 5.x 沉淀 4-8 周（~2026-06-15）让社区验证 v2026.5.x 的 70+ 修复
2. 届时阶梯升至 **v2026.4.27 或 v2026.4.29**（已修 #73358，避开 5.x 早期 churn）
3. 升级前 dev 验证两个新中风险点（tools.exec 白名单 + plugin manifest contracts）
4. 同步重新评估 #59265 是否有 verified fix（如仍无 PR + 无版本 mention，准备 WhatsApp 立即可用性回滚预案）

### 13.6 升级路径选项对比（实证后更新）

| 方案 | 跨度 | 风险 | 工程成本 | 推荐度 |
|------|------|------|--------|--------|
| **A. 立即升 v2026.5.3-1 latest** | 34 stable | 高（5.x 仅 4 天稳定期 + 两个新 breaking 未验证 + 累积 churn） | 中 | ⭐ |
| **B. 阶梯升 v2026.4.27**（含 #73358 fix 的最早稳定） | 19 stable | 中（避 4.29/5.x 累积变更，但 4.27 也仅 6 天稳定期） | 中 | ⭐⭐⭐ |
| **C. 继续 hold 等 5.x 沉淀**（推荐） | 0 | 0 | 0 | ⭐⭐⭐⭐⭐ |
| **D. shadow 演练** | — | 0 | 高 | ⭐⭐ |

### 13.7 第四次评估结论：**继续 hold（推荐方案 C），但战略路径已开**

**Hold 理由（与四次评估前不同）**：
1. **不是因为 dealbreaker 不修**（已修），而是**因为社区验证不充分**（5.x 仅 4 天 stable）
2. **两个新中风险点**（tools.exec + plugin manifest）需要先在 dev 验证
3. **跨度 34 stable** 意味着升级时累积 breaking 面巨大，不应在缺验证证据时仓促升
4. **Tripwire 0/6 触发** 表示无外部强制因素，可以从容选时机

**下次评估时机**：
- **硬性触发**：任一 tripwire 跳红（每周一 cron 自动）
- **软性触发**：~2026-06-15（4-8 周观察期到达；届时 5.x 已 6 周稳定，社区验证累积充分）
- **本次 LAST_EVAL_DATE 更新到 2026-05-05**（重置时间 tripwire 计数）

### 13.8 元价值

本次评估是 V37.9.22 Tripwire 框架首次"复评"实践，验证了 framework 的核心承诺：
- ✅ **不再陷入"看到新版本就评估"的二元决策疲劳**（自动 hold + 人工实证补充）
- ✅ **方法论从"按节奏推断"升级到"实证调查"**（WebFetch 直接拉 release notes）
- ✅ **决策矩阵让 hold 理由透明可追溯**（不是模糊的"先等等"，而是具体到哪个 dealbreaker、哪个 breaking change、跨度多少）
- 🟡 **未来 framework 可优化**：tripwire 当前未自动检测"上游已修复阻塞 bug"信号（如 #73358 修复检测），需 v37.9.x 后续迭代加 tripwire #7（关键 bug fix 检测）作为正向触发

### 13.9 推荐升级时间表（2026-05-05 制定）

#### 13.9.1 时间轴

```
今日 5/5  ─┬── 今天    Tripwire 0/6  → 自动 hold
          │
          │  软性观察期 (4-8 周让 5.x 沉淀，社区验证累积)
          │
~5/26-6/2 ┼── 预计 tripwire #2 (版本差距 50) 自动触发
          │   → 强制启动第五次评估，但不一定升级
          │
6/15 周一 ┼── 软目标：第五次评估检查点
          │   bash check_upgrade.sh + 实证 WebFetch v2026.5.x release notes
          │
6/15-6/19 ─── 评估通过则做升级前 dev 验证（2-4 小时工作量）
          │   • 验证 tools.exec/tools.fs 不破坏 12 工具集
          │   • 验证 plugin manifest contracts.tools 不破坏 qwen-local 注册
          │   • 备份 openclaw.json + 准备 30 秒 rollback 预案
          │
6/20 周六 ◀── 升级窗口 10:00-12:00 HKT
          │
6/30      ─── 8 周保守上限（如 6/20 评估不通过顺延）
```

#### 13.9.2 推荐升级日期：**2026-06-20（周六）10:00-12:00 HKT**

**为什么是 6/20 周六上午 10-12 点**：

| 维度 | 选择理由 |
|------|---------|
| **6/20 而非 6/15** | 6/15 是评估检查点不是升级日；评估通过后还需 4-5 天 dev 验证两个新中风险点 + 准备回滚预案 |
| **周六而非周五** | 周五晚需熬夜 + 周六补救时间充足；如出严重问题，整个周末都可用于排查/回滚，不影响工作日 |
| **10:00 而非凌晨** | 早上 cron 批次（07:30 finance_news / 09:30 ACL / 10:00 ontology_sources 第一波）已基本跑完；用户清醒可监控 |
| **避开 12 点后** | 12-22 点是用户日常 WhatsApp 使用窗口，应让升级在用户活跃前完成 + 验证稳定 |
| **距 22:00 kb_evening 有 10h 缓冲** | 出问题有充裕时间回滚到原状态，不影响晚间核心 job |

#### 13.9.3 触发条件（必须四个 gate 全过才执行）

升级前最后清单（按 7.4/7.5 节 SOP 执行）：

| Gate | 检查项 | 不通过的处理 |
|------|--------|-------------|
| **G1** | 5.x 收敛证据：v2026.5.x 周稳定版次数 ≤ 1（不再每天 hotfix） | 不通过 → 顺延 1 周到 6/27 重评 |
| **G2** | #59265 验证修复：issue 有 PR + 某版本 release notes 显式 mention（双证据） | 不通过 → 升级目标降级为 v2026.4.27（避开 #59265 风险）；备好 WhatsApp 立即可用性 E2E |
| **G3** | tools.exec/tools.fs：dev 跑 `bash ~/preflight_check.sh --full` 升级模拟环境后 12 工具集仍可见 | 不通过 → 在 openclaw.json 显式加 `alsoAllow` 配置后重测 |
| **G4** | plugin manifest contracts.tools：dev 验证 qwen-local 注册路径不被新 ownership 契约破坏 | 不通过 → 修改 adapter.py 注册逻辑后 dev 重测 |

#### 13.9.4 升级目标版本选择（6/15 当时决策）

| 候选版本 | 选择条件 |
|---------|---------|
| **v2026.4.27**（最早含 #73358 fix） | **首选** — 届时已 7 周稳定期 + 跨度仅 19 stable + 避开 4.29/5.x 累积变更 |
| **v2026.5.3 / 5.4+**（届时最新 stable） | 备选 — 仅当 G1+G2+G3+G4 全过且社区 6 周内无 5.x dealbreaker 报告时 |
| **当时其他稳定版** | 兜底 — 如 v2026.4.27 反而出现晚发现 bug |

#### 13.9.5 元规则

- **6/15 之前**：每周一 cron 自动跑 `check_upgrade.sh`，任一 tripwire 跳红立即评估
- **6/15 当天**：人工运行第五次评估流程，更新此推荐时间表
- **6/15-6/19 之间**：dev 完成两个新中风险点的兼容性验证
- **6/20 升级窗口**：执行升级 + 立即更新 `LAST_EVAL_DATE` → `2026-06-20`（重置 180 天硬性上限到 2026-12-17）

#### 13.9.6 置信度声明

- 🟢 **6/20 是合理时间点**（中等置信度 70%）— 假设 5.x 在 6 周内稳定且无新 dealbreaker
- 🟡 **可能延后到 6/27 或 7/4**（30% 概率）— 如果 5.x 持续高频迭代或出现新硬阻塞
- 🔴 **不太可能提前**（< 5%）— 5.x 仅 4 天稳定期就升级风险太高，不建议

#### 13.9.7 顺延决策树

如 6/20 升级窗口被推迟，按以下决策树选下一窗口：

```
6/20 评估不通过
  ├─ 单 Gate 失败 (G1/G2/G3/G4 任一)
  │   └─ 顺延 1 周 → 6/27 周六 10:00 HKT 重评
  │
  ├─ 多 Gate 失败 + 5.x 仍高频迭代
  │   └─ 顺延 2 周 → 7/4 周六 10:00 HKT 重评 (届时 9 周观察期)
  │
  ├─ 5.x 出现新 dealbreaker
  │   └─ 降级目标版本到 v2026.4.27 或更早稳定，6/27 重评
  │
  └─ 任何 tripwire 触发 → 立即评估不等待
```

**硬上限**：2026-10-26（180 天 tripwire 自动触发），无论如何届时必须正式评估并选定升级或更新理由继续 hold。

---

## 九、升级后文档更新清单

升级成功后需同步更新：
- [ ] `docs/config.md` 第 5 行：版本号 + hold 状态
- [ ] `CLAUDE.md`：版本引用
- [ ] `SOUL.md`：Gateway 版本字段
- [ ] `status.json`：constraints 中的 Gateway hold 条件
- [ ] `upgrade_openclaw.sh`：确认脚本与新版本兼容
- [ ] `check_upgrade.sh`：更新 hold 逻辑（如不再需要）

---

## 十四、第五次评估记录（2026-06-08，当前数据实证 + 用户决策继续 hold）

> 触发：用户主动要求"充分评估升级风险（已推迟 3 个月）"。本次用 WebFetch 实证当前上游状态
> （上次评估 2026-05-05 已一个月，数据需刷新）。结论：**继续 hold 到文档 6/20 时间表（用户决策）**。

### 14.1 "3 个月推迟"裁决：有理有据，非拖延

核心阻塞是 **#73358**（无 OpenAI key 时 bundled Codex skill silent default → PA 业务静默中断），
直到 **v2026.4.27（2026-04-29）才修复**。在此之前任何升级都导致业务中断 → 3 个月 hold 是
evidence-driven 非 procrastination。4/29 后战略路径才打开。

### 14.2 当前上游状态（WebFetch 实证 github.com/openclaw/openclaw/releases）

| 项 | 5/5 第四次评估 | 6/8 本次第五次 | 变化 |
|----|----------------|----------------|------|
| 最新稳定版 | v2026.5.3-1 | **v2026.6.1**（6/3 发布） | ↑ +N |
| 预发布 | — | v2026.6.5-beta.2 / 6.2-beta.1 等 | — |
| 版本差距 tripwire（50 stable） | 未触发 | **likely 已触发**（文档预测 ~5/26-6/2，3.13→6.1 跨度更大） | 🚨 |
| #73358 dealbreaker | ✅ v4.27 修复 | ✅ 无变化 | — |
| #59265 Agent 不可见 | ⚪ closed 无 PR / 无 fix | **WebFetch 再确认仍 closed 无 PR / 无 fix evidence**（last update 4/1） | — |
| **6.x SQLite migration**（file-based → SQLite state） | N/A | 🟡 **新 breaking**（可能影响 Gateway state 备份 / session 存储 / openclaw_backup.sh） | 新增 |
| **6.x plugin 安装策略大改**（dangerous-code scanner → operator install policy） | N/A | 🟡 **新 breaking**（plugin 验证模型变更） | 新增 |

### 14.3 关键洞察：保守目标 v2026.4.27 更有理

瞄准 **v2026.4.27**（最早含 #73358 fix）可**避开全部** 4.29/5.x/6.x breaking：
tools.exec/fs restrictive profile（4.29）+ plugin manifest contracts.tools（5.2）+
**SQLite migration（6.x）+ plugin policy overhaul（6.x）**。6.x 又加 2 个 breaking →
保守目标 v2026.4.27 比 5/5 时更有理（升级到最新 6.1 风险面更大）。

### 14.4 v2026.4.27 升级风险矩阵（本次刷新）

- 🟢 **已解除**：WhatsApp plugin（条件 1 满足）/ #73358（4.27 修复）/ API 兼容（OpenAI 格式不变）
- 🟡 **中风险（须 Mac Mini 验证，3.13→4.27 跨度 19 stable）**：(1) config 迁移 `openclaw doctor --fix`（legacy key 验证变严）(2) trusted-proxy auth 变更（v2026.3.31，影响 Gateway→Proxy:5002）(3) Exec 安全加固（v2026.3.31，我们 12 工具白名单）
- ⚪ **最大残留风险**：**#59265 Agent actions 不可见**（G2 门不通过 — closed 无 verified fix）。若 4.27 含此 bug，PA(Wei) WhatsApp 回复可能不可见。**自动检查抓不到，只能 WhatsApp E2E 观察 + 30 秒回滚**。这是 13.9.1 G2 失败的文档化路径（目标降级 v4.27 + WhatsApp 立即可用性 E2E 就绪）。

### 14.5 第五次评估结论：**继续 hold 到 6/20 时间表（用户决策）**

本次评估（当前数据）**全面确认文档化方案 C + 时间表**：
1. 目标 **v2026.4.27** 确认（G2 门 #59265 失败 → 降级目标，避开全部 4.29/5.x/6.x breaking）。
2. v2026.4.27 已 ~40 天稳定期（4/29 发布），远超 6/20 的"soak"假设 → 技术上已成熟。
3. 用户看完整风险画面后选**继续 hold 到文档 6/20 时间表**（保守纪律，weekend 回滚窗口 + 6/15 最终前置验证）。
4. 风险特性**可验证 + 可恢复**（备份 + doctor --fix + WhatsApp/Discord E2E + 30 秒回滚），但执行是 Mac Mini SSH 操作（禁 AI 执行，自杀悖论）。

**已知 framework gap（V37.9.22 登记）**：版本差距 tripwire likely 触发后每周一 cron 会持续告警，
但我们已评估 + 决策 hold → 6/20 前的 Monday 告警是预期噪声（acknowledged）。未来可加 tripwire #7
（关键 bug fix 检测）或"已评估决策 hold 期间静默告警"逻辑。

### 14.6 待办（6/15-6/20，用户 Mac Mini）

- **6/15 最终前置验证**：跑 `bash check_upgrade.sh`（看 tripwire 状态）+ WebFetch 确认 #59265 / 6.x 无新 dealbreaker + dev 侧审查 proxy_filters 工具白名单 against v3.31 exec 加固。
- **~6/20 升级**：按第七节 SOP（备份 → 升 v2026.4.27 → `doctor --fix` → **强制 WhatsApp E2E（PA 回复可见性，#59265 验证）** → 双通道推送验证 → 不可见立即回滚）。
- 升级成功后按第九节清单更新 docs/config.md + CLAUDE.md + SOUL.md + status.json + LAST_EVAL_DATE。

> 本次 LAST_EVAL_DATE 更新到 **2026-06-08**（第五次评估已做，重置时间 tripwire 计数）。

---

## 第十五节：6/15 最终前置验证（2026-06-11 提前执行，V37.9.136）

> 用户指示提前执行 6/15 前置验证以为 ArXiv 论文 session 清场。按 14.6 三步执行 + 实证数据。

### 15.1 验证结果：✅ GO（v2026.4.27，6/20 周六 10:00-12:00 HKT 窗口维持）

| 验证项 | 数据（2026-06-11 实证） | 结论 |
|--------|------------------------|------|
| Tripwire | `check_upgrade.sh` 0/6 触发（版本差距 48/50 接近但未触发） | ✅ |
| #59265 Agent 不可见 | GitHub API 实证：仍 closed (4/25) **无 PR / 无 fix commit / 无 milestone**，最后活动 4/28 | G2 不通过 → 按 13.9.3 决策树**目标维持 v2026.4.27** + 升级后强制 WhatsApp E2E |
| v2026.4.27 健康度 | npm registry：published 2026-04-29，**未 deprecated**，43 天稳定期，邻近版本 (4.25/4.26/4.29) 均未 deprecated | ✅ 无 post-release 负面信号 |
| v2026.4.27 notes 复核 | WebFetch 实证：**确认含 #73358 fix**（"require explicit skills.entries.coding-agent.enabled … do not silently offer Codex delegation"） | ✅ dealbreaker fix 在内 |
| 6.x 动态 | 最新 stable v2026.6.5 (6/9)；SQLite auth/session migration 在 6.6-beta train 反复 deferred；6 月仅 2 stable vs 5 月 15（频率收敛但大迁移在路上） | ✅ 避开 6.x 决策仍正确 |
| G3 (tools.exec/tools.fs) | v2026.4.29 引入，4.27 路径**不适用** | N/A |
| G4 (plugin manifest contracts.tools) | v2026.5.2 引入，4.27 路径**不适用** | N/A |
| rotateBytes deprecated (4.27 行为变更) | grep 全配置文档无 `session.maintenance.rotateBytes` 使用 | ✅ 无影响 |

### 15.2 v2026.4.27 notes 新发现的 2 个升级日验证项（追加到第七节 SOP 执行清单）

1. **WhatsApp plugin 自动加载**：4.27 含 plugin manifest-first 重构（"Plugin startup now requires explicit
   `activation.onStartup` declarations; implicit sidecar loading deprecated"）。我们的 WhatsApp plugin
   是 4/10 自动安装的 sidecar 形态——升级后必须验证 plugin 仍自动加载（`openclaw doctor` + 既有
   WhatsApp E2E 强制步骤已覆盖，此处显式登记防漏）。**若 plugin 不加载 → 给 plugin manifest 加
   activation.onStartup 声明后重启，仍不行立即回滚。**
2. **Discord 回复默认 private（4.27 行为变更）**：我们走 `openclaw message send --channel-id` 显式发送
   不受影响（该变更针对 agent 隐式回复），升级后观察第一次 cron 双通道推送确认 Discord 到达。

### 15.4 升级实录（2026-06-11 12:30-12:50 HKT — 当日完成，提前 9 天）

前置验证 GO 后用户决策当日升级（原第七节 SOP 前置条件即"工作日白天，确保能快速回滚"；
6/20 周六是后来加的保守层，其周末补救价值在 30 秒回滚面前有限；当日升级另有 Claude 在线
实时协助的优势）。完整时间线：

| 时间 | 步骤 | 结果 |
|------|------|------|
| 12:27 | 备份 openclaw.json + 版本记录 (2026.3.13) | ✅ |
| 12:33 | `npm install -g openclaw@2026.4.27` | ✅ 12s, 2026.4.27 (cbc2ba0) |
| 12:35 | `openclaw doctor --fix` | ✅ legacy config 迁移 (web.search→brave plugin / discord.streaming→mode) + 12 bundled plugin deps 安装 (baileys/carbon/...) + sessions canonicalize + 10 orphan transcripts 归档 (可逆 rename) + plugin registry 71/116, **0 errors** |
| 12:40 | `bash ~/restart.sh` | ✅ adapter/proxy kickstart + gateway launchd + 健康验证 2×3s |
| 12:41 | `channels status --probe` | ✅ **WhatsApp linked+connected (15.2 验证项 1: manifest-first 自动加载通过, auth 保留)** + Discord connected |
| 12:42 | WhatsApp send #1 | ⚠️ gateway timeout 10s — plugin 首次调用按需 staging baileys+jimp (4.27 机制) |
| 12:44 | WhatsApp send #2 | ✅ Message ID 3EB0...（staging 完成后即通）+ Discord send ✅ (15.2 验证项 2) |
| 12:47 | **真人 E2E**: 用户 WhatsApp 问 PA | ✅ **PA 回复完整可见 — #59265 未复现, 最大残留风险解除** |
| 12:49 | `preflight_check.sh --full` | ✅ **85 通过 / 0 失败 / 1 警告 (KB 索引 lag) / SLO 全部达标** (p95 警告随窗口冲刷自愈) |

回滚未触发。操作教训：(a) zsh 不吃 `#` 注释行（粘贴命令需去注释）(b) `doctor | tail` 管道
吞交互提示导致看似挂起 — doctor 必须不接管道直跑 (c) upgrade_openclaw.sh 原用 `@latest`
是隐患（会拉到 2026.6.x），已改为强制显式版本参数 (V37.9.138)。

观察项（非阻塞）：Discord groupPolicy=allowlist + groupAllowFrom 空的新警告（我们不收
群组入站，无影响）；#48703 hotfix 在 4.27 上游已含修复，restart.sh sed 补丁冗余但幂等
无害，移除登记 follow-up；openclaw_config_to_runtime convergence spec 下次 --full 跑时
declared version 字段随 doctor 重写的 openclaw.json 变化，如报 drift 属预期（alert_only_permanent）。

### 15.3 LAST_EVAL_DATE 更新

本次前置验证（人工 + 实证）将 LAST_EVAL_DATE 更新到 **2026-06-11**。6/20 升级窗口前无需再评估
（除非 tripwire 跳红 / 上游出新 dealbreaker）。

---

## 第十六节：2026-07-02 上游情报收集（unfinished [5](c) 兑现 + [9] Option A 复核，V37.9.225）

> 方法注记：dev 沙箱 api.github.com 对 session 外仓库被代理拦截，本节改用 **npm registry tarball 实证**
> （registry.npmjs.org 可达）：提取 CHANGELOG.md + dist zod-schema + whatsapp connection-controller
> 源码直接验证 —— 代码即事实，比 release notes 转述更权威。无升级动作（tripwire 不自动升级）。

### 16.1 上游版本状态

- dist-tags：latest = **2026.6.11**（2026-06-30 发布）/ beta = 2026.7.1-beta.1（2026-07-02）。
- 自第五次评估（6/8，当时上游 2026.6.1）以来 stable 节奏恢复：6.6 → 6.8 → 6.9 → 6.10 → 6.11。
- SQLite 迁移机制**仍在活跃改动**（6.11 含 #95857 "Fix SQLite user version guardrail allowlist" +
  #95916 node:sqlite guidance）→ hold 决策登记的 6.x migration 风险面仍真实，维持 hold。

### 16.2 CLI 冷调用/plugin staging 修复定位（V37.9.145 回归的上游收敛点）

2026.6.11 changelog（覆盖 v2026.6.10..HEAD，308 PRs，即全部为 6/24-6/30 窗口合并）含三个直接命中：

| PR | 内容 | 对应本地症状 |
|----|------|-------------|
| #93356 | fix(plugins): cache plugin setup registry to kill the **/models CPU storm** | 冷调用 ~40s user CPU（V37.9.145 定性） |
| #93919 | perf(plugins): cache existence probes within **bundle manifest scan** | plugin staging 反复扫描（V37.9.156 (b)） |
| #89628 | Speed up precomputed command **help startup** | CLI 冷启动面 |

**这些 PR 在 6.10→6.11 窗口合并 → 构造上不可能在 4.27（4 月底 cut）里** → 冷调用缓解在 4.27 上
仍靠 notify.sh 重试+队列（V37.9.145 已闭合丢失面，现状可接受）；根治随未来升级到 ≥2026.6.11 获得。
这是未来升级评估时 6.x 收益侧的新增筹码（与 6.x SQLite/plugin breaking 成本侧对冲）。

### 16.3 #56365 已 closed（PR #73580）+ 4.27/6.11 schema 对比实证

- **#56365（makeWASocket config passthrough）已 closed，经 PR #73580 修复**：暴露 Baileys socket
  timing（`defaultQueryTimeoutMs` / `keepAliveIntervalMs` / `connectTimeoutMs`）与 proxy `agent`。
- tarball schema 实证：`web.whatsapp.{keepAliveIntervalMs, connectTimeoutMs, defaultQueryTimeoutMs}`
  在 **2026.6.11 schema 存在、2026.4.27 schema 不存在** → socket timing 透传需升级 6.x 才可用。

### 16.4 🔴 V37.9.180 Option A「证伪」修正：4.27 本就有 web.reconnect（路径错误误判）

- 4.27 `zod-schema-CxqiRMUZ.js` 实证：**顶层** `web.reconnect.{initialMs, maxMs, factor, jitter,
  maxAttempts}`（strict）存在。
- 消费方实证：`extensions/whatsapp/connection-controller` 的 `resolveReconnectPolicy(cfg)` 读
  `cfg.web?.reconnect`，merge 进 `DEFAULT_RECONNECT_POLICY {initialMs:2000, maxMs:30000,
  factor:1.8, jitter:0.25, maxAttempts:12}` —— 默认 12 与 V37.9.162 血案日志「Retry 1/12」吻合。
- **V37.9.180 的结论「4.27 strict schema 拒绝该键」是路径错误**：当时试的
  `channels.whatsapp.web.reconnect.maxAttempts`（channels.whatsapp 下无 web 键 → 被 strict 拒绝），
  正确路径是**顶层** `web.reconnect.*`。Option A（重连风暴限制）在已安装 4.27 上今天即可配置。
- 对症分析：血案是 6h 风暴 = 多轮 12-attempt 循环，`maxAttempts` 默认已 12 → 真正杠杆是**拉长退避**
  （`maxMs` 30s → 300s+ 降低风暴请求密度一个量级）；风暴中断后频道退出由 V37.9.162 wa_channel_status
  检测 + Discord escalation 兜底（MR-14）。
- 🔴 Mac Mini 验证项（用户执行）：`openclaw config set web.reconnect.maxMs 300000` → 确认 config set
  接受（schema 验证即证明）→ 重启后 `openclaw config get web.reconnect` 确认 canonicalize 保留
  （V37.9.180 教训：手改 openclaw.json 会被剥，必须走 config set）→ 后续重连日志观察退避拉长。

---

## 第十七节：第六次评估（2026-07-04，用户主动要求「深入评估 4.27 能否无损稳定继续升级」——仅评估，零升级动作）

> 方法：npm packument 全量分析 + **5 个版本 tarball 实证**（4.29 / 5.2 / 5.28 / 6.1 / 6.11 的
> CHANGELOG + dist 源码 grep，V37.9.225 同款「代码即事实」）+ WebFetch 补齐 6.5-6.10 窗口
> release notes + **全仓 Gateway 依赖面审计**（CLI 解析点 / 状态文件 / schema 假设 / dist grep）。
> Tripwire 机械状态 0/6（时间 23/180 天，版本差距 23/50 stable）——本次为用户主动评估。

### 17.1 一句话结论

**4.27 → 6.11 不是「无损」升级——是一次含三个结构性迁移的升级，且其中一个（状态 SQLite 化）
在 6.11 仍处进行中；最关键的质变是「30 秒无损回滚」安全网退化为「有损回滚」（SQLite 单向门）。
推荐继续 hold，设收敛判据（17.6），待上游迁移弧线完成后再开升级窗口。**

### 17.2 上游现状（2026-07-04 实证）

- dist-tags：latest = **2026.6.11**（6/30）/ beta = 2026.7.1-beta.1（7/2）。4.27 → 6.11 跨度 **23 个 stable**（2 个月）。
- **发版节奏未收敛**：6 月 7 个 stable（6.1/6.5/6.6/6.8/6.9/6.10/6.11）≈ 周更 —— 原 G1 收敛 gate（周稳定版 ≤1）**不通过**。
- **Node 引擎门槛**：4.27 要求 ≥22.14 → 5.12 提升 22.16+（node:sqlite statement metadata API 依赖）→ 6.x 要求 **≥22.19**（packument engines 实证）。Mac Mini 当前 node 版本未知 = 新增前置条件。
- 4.27 未被 deprecate，仍可安全停留。

### 17.3 三个结构性迁移（4.27 → 6.11 必然经过）

| # | 迁移 | 实证 | 对我们的含义 |
|---|------|------|-------------|
| **M1** | **WhatsApp/Discord 插件外部化**（5.2 起核心包移除，5.12 正式外部化 + Baileys rc9→rc11） | tarball 目录实证：4.29 whatsapp=77 文件 → 5.2 起 = 0；6.11 dist 引用外部包 `@openclaw/whatsapp`；6.11 仍在继续外部化更多插件（#95683） | 升级后频道插件须经 doctor/update 修复机制从 npm/ClawHub 下载安装 = **网络依赖 + ClawHub 429 历史（#54446）**。修复机制本身有 bug 史：#82533/#82813「升级后 Discord 频道消失」真实发生过，5.17 才修（"repair configured externalized plugin installs during legacy 2026.4.x upgrades"——好消息是 4.x→6.x 跳变路径被显式支持并硬化过） |
| **M2** | **状态 SQLite 渐进迁移（6.1-6.11 进行中，未完成）** | 6.1: inbound queues/plugin ledger/iMessage state；6.5: **cron store（doctor preflight 自动迁 legacy cron JSON→SQLite）+ auth profiles(#89102) + session metadata(#91322)**；6.6: auth 迁移 verify-before-**cleanup**(#91740)；6.8: cron.status 改报 SQLite 路径**退役 jobs.json**；6.9: default-agent auth 补迁(#93156)；6.11: guardrail 仍在修（#95857/#95916）+ **session accessors 正在 refactor（#96182/#96204）**。**sessions.json 在 6.11 仍是会话存储**（dist 实证 `agents/<agent>/sessions/sessions.json`）= 迁移弧线走到中段 | (a) 我们的 openclaw-scheduled cron job 要穿越 jobs.json→SQLite 迁移 (b) **auth（含频道凭据）迁 SQLite 且迁移含 cleanup 步骤** = 回滚单向门（17.4）(c) `openclaw_backup.sh` 的 backup create 对 SQLite 文件的覆盖语义未实证 |
| **M3** | **Gateway HTTP/WebSocket 内部栈整体替换**（5.12 正式 Breaking：managed interception → Proxyline） | 5.28 累积 changelog「### Breaking」段原文（唯一正式 Breaking，另一条 BlueBubbles 移除与我们无关） | Gateway→Tool Proxy(:5002) 的通路底层实现整体更换（上游声明保留 loopback routing policy）——对我们最重要的一条数据通路，只能 E2E 验证无法预先排除 |

### 17.4 🔴 质变：回滚不再无损（与 3.13→4.27 那次的最大区别）

上次升级的安全网是「30 秒回滚」（npm 降级 + 恢复 config）。本次 6.x 的 doctor 会把 cron store、
auth profiles 迁入 SQLite 且**迁移含 cleanup**（#91740 "verify SQLite auth migration before cleanup"
措辞实证）。回滚到 4.27 后旧版本读 file-based 状态 → **cron jobs / auth（含 WhatsApp 凭据）可能
回不去**。若凭据丢失 → WhatsApp 重新扫码链接 → 撞 2026-06-16 设备链接限流（408）血案类风险 →
最坏情况 WhatsApp 瘫数天。**缓解**：升级前对整个 `~/.openclaw/` 做完整 tar 快照（不只 backup create），
回滚 = npm 降级 + 恢复快照（升级窗口期间产生的新状态丢弃）——可控，但已不是「无损」。

### 17.5 风险矩阵（依赖面审计 × 上游变更交叉）

**🔴 高（可能业务中断，只能 E2E 确认）**
- **R1 WhatsApp 频道穿越升级**：M1 安装步（网络/429）× Baileys rc9→rc11+ 跳变（creds 若失效须重链 → 408 风险）× M2 auth SQLite 迁移，三重叠加。收益侧同样在这里（17.6 B2）。
- **R2 weixin 第三方插件**：好消息 = `@tencent-weixin/openclaw-weixin@2.4.3` **在官方外部 catalog**（5.12 era changelog 实证），6.x 认识它；坏消息 = 4.27 时代安装的版本在 6.11 plugin SDK/manifest contract 下能否直接加载未知 + 6.8 有 untrusted-external-plugin 警告机制 + 若被迫升插件版本，其 contextToken/48h 窗口投递语义可能变化 → notify.sh weixin 分支假设失效。
- **R3 notify.sh 冷调用超时签名判定**（notify.sh:89-91，全系统推送最高频解析点）：4.27 quirk 在 6.11 被根治（#93356/#93919/#89628）→ 签名可能消失（良性）或语义反转（漏投/重复投）。升级日必须 E2E。

**🟡 中（可验证可恢复）**
- **R4 CLI 输出/退出码解析面**：`wa_channel_status.py` 解析 channels status 自由文本（FAIL-OPEN——格式变 → 静默失明回到 7h 盲区）；6.8 CLI usage-error 退出码重分类；6.9 cron list 输出改版；check_upgrade.sh plugins install 输出 grep。
- **R5 openclaw.json 多跳 doctor 迁移**：一次跳 23 stable 的迁移链（含 6.5 cron preflight 迁移）；已知内部漂移（install 写 `providers.qwen` vs health_check 读 `models.providers.qwen-local`）会在 doctor 下暴露。
- **R6 preflight #48703 dist grep**（preflight_check.sh:592-608）：dist chunk 结构必变 → 检查失效，升级日须同步改。
- **R7 Node ≥22.19 前置**：Mac Mini node 版本待确认；不足则先升 Node（又一变动源，launchd plist 路径）。
- **R8 工具注入契约**（proxy_filters ALLOWED_TOOLS/CLEAN_SCHEMAS）：4.29 tools.exec/fs restrictive profile + 6.10 trusted tool policy enforcement——工具名/schema 若变，白名单**静默丢工具** → PA 无声失能（fail-plausible 类，测试抓不到，只能 WhatsApp E2E）。

**🟢 收益侧（升级动机，诚实登记）**
- **B1** 根治 4.27 CLI 冷调用回归（当前每条推送靠 notify.sh 重试兜底 + preflight warn hack）。
- **B2** WhatsApp 可靠性大幅增强：socket-timing 透传（#73580 keys 仅 ≥6.x）+ 6.9 preserve auth on terminal disconnects + 6.11 Baileys group reliability/durable reply targets——正对我们的 Baileys 重连封禁风险面。
- **B3** cron 可靠性（transient rate-limit retry / malformed job 容忍）+ doctor 增强（**#94148 非交互 --fix 不再自动重启 gateway**——对我们的升级 SOP 是利好）。
- **B4** 版本差距的复利成本：每晚一个月，未来跳变风险 +N。

### 17.6 结论与建议：**继续 hold，设三条收敛判据（数据驱动开窗，非日历驱动）**

- **方案 A（推荐）**：hold 4.27。同时满足以下三条时开升级窗口：
  1. **SQLite 迁移弧线收敛**：连续 2 个 stable 的 changelog 无 session-store/SQLite 迁移类 PR（当前 6.11 还在 refactor session accessors = 明确未收敛；beta 2026.7.1 已在跑，弧线可能数周内完成）；
  2. **发版节奏收敛**：周稳定版 ≤1（原 G1，当前 ≈ 周更不通过）；
  3. **Mac Mini node ≥22.19 预先就位**（可提前独立完成，与升级解耦）。
- **方案 B（不推荐）**：升到中间版本（5.x/6.1-6.10）——任何 ≥5.2 版本都吃下 M1+M3 两个最大 breaking，却拿不到 6.11 的冷调用修复与 SQLite guardrail 修复 = 成本全担收益不全拿。**若升，4.27→当时最新 stable 一步到位是唯一合理跳法**。
- **方案 C（用户若决定现在升）**：第七节 SOP 基础上增补前置——`node -v` ≥22.19 / **全量 `~/.openclaw/` tar 快照**（回滚唯一可靠恢复源，17.4）/ 升级前试 `openclaw plugins install whatsapp` 确认 ClawHub 通畅 / doctor --fix 后 channels status 确认 whatsapp+discord+openclaw-weixin 三频道 / `wa_channel_status.py` 对新版 channels status 输出真跑一次解析验证 / notify.sh 三通道 E2E / preflight #48703 检查预期失效登记 / **WhatsApp E2E 含图片+search_kb**（R8 静默丢工具验证）。回滚 = npm 降级 + 恢复 tar 快照（有损：窗口期新状态丢弃）。

**LAST_EVAL_DATE 更新至 2026-07-04**（第六次评估完成，重置时间 tripwire）。下次触发 = 任一 tripwire
跳红，或 17.6 判据 1（SQLite 弧线收敛信号）出现——每周一 check_upgrade.sh cron 照常监控，发现
2026.7.x stable 发布时顺手核对其 changelog 是否仍含 session/SQLite 迁移 PR 即可完成判据 1 的跟踪。

---

## 第十八节：第七次评估（2026-07-20，2026.7.1 stable 发布触发判据跟踪）

> 触发：第六次评估（17.6）结尾预设的跟踪点——「发现 2026.7.x stable 发布时顺手核对其 changelog
> 是否仍含 session/SQLite 迁移 PR」。2026.7.1 stable 于 2026-07-13 发布（自 07-04 第六次评估以来
> 唯一新 minor stable），到期核对三条收敛判据。方法：npm tarball 实证（`npm pack openclaw@2026.7.1`
> → 全量 CHANGELOG grep，V37.9.225/244 同款「代码即事实」）+ engines 门槛跨版本对比。Tripwire
> 机械状态仍 0/6（时间 16/180 天，版本差距 31/50 stable——check_upgrade.sh 字典序粗略计数含 patch，
> 脚本自注「粗略」；minor stable 精确计数见 18.2）——本次为判据到期跟踪。

### 18.1 一句话结论

**继续 hold，且理由较第六次更强：三条收敛判据无一满足——判据 1（SQLite/session 弧线收敛）在 7.1
明确未收敛反而深化（4+ 新 session-accessor refactor PR）；判据 3（Node 门槛）因 SQLite WAL 数据损坏
安全被再度收紧成「版本区间黑名单」（M2 单向门风险加深的直接证据）。仅判据 2（发版节奏）出现部分改善。**

### 18.2 上游现状（2026-07-20 实证）

- dist-tags：latest = **2026.7.1-2**（07-18，patch）/ minor stable = **2026.7.1**（07-13）/ beta = 2026.7.2-beta.3（07-18）。4.27 → 7.1 **minor stable 跨度 24**（第六次 23 + 7.1；check_upgrade.sh 字典序粗略计数报 31/50 含 patch 与字典序误差，两口径均远低于 50 tripwire）。
- 自 07-04 以来发版：7.1（07-13 minor）→ 7.1-1（07-18 patch）→ 7.1-2（07-18 patch）；beta 侧 7.2-beta.1/2/3（07-15/17/18）= 7.2 在 beta 阶段。
- 4.27 仍未被 deprecate，可安全停留。

### 18.3 三条收敛判据逐一核对

| 判据 | 第六次（07-04）状态 | 第七次（07-20）实证 | 判定 |
|------|-------------------|-------------------|------|
| **① SQLite/session 弧线收敛**（连续 2 stable 无 session-store/SQLite 迁移 PR） | 6.11 仍在 refactor session accessors（#96182/#96204）= 未收敛 | **7.1 仍密集 refactor**：#101178（session-accessor boundary guard 加 debt ratchet）/ #101179（route new session store bypasses through accessor）/ #101180（move inbound meta+goals+delivery reads behind accessor）/ #101688（route chat transcript injection through accessor）+ SQLite state 处理 #100827/#101375/#89597 + legacy-state 迁移收尾 #104529/#102780/#103157/#103281 | ❌ **未满足**，弧线深化非收尾 |
| **② 发版节奏 ≤1/周** | 6 月 7 stable ≈ 周更，不通过 | 6.11（06-30）→ 7.1（07-13）间隔 **13 天**（明显放缓）；但 7.1 后同日两 patch（7.1-1/7.1-2）= 混合信号；4 周窗口 minor 3 个（6.10/6.11/7.1）≈ 1/周边界 | 🟡 **部分改善**，边界未稳定 |
| **③ Mac Mini node ≥22.19 就位** | 6.11 要求 ≥22.19（新前置） | **7.1 门槛再升**：`>=22.22.3 <23 \|\| >=24.15.0 <25 \|\| >=25.9.0`（**区间黑名单**，排除 node 23.x 全部 + 24.0–24.14 + 25.0–25.8）。根因 = **PR #106065「SQLite WAL safety: reject runtimes vulnerable to WAL corruption」**——7.1 直接拒绝有 WAL 数据损坏漏洞的 node 运行时 | 🔴 **门槛升级 + 语义变化**，Mac Mini node 必须落在 7.1 接受区间内 |

engines.node 演进实证：4.27=`>=22.14.0` / 6.11=`>=22.19.0` / **7.1=`>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`**。

### 18.4 🔴 判据 3 语义质变：Node 门槛从「最低版本」变为「SQLite 安全区间」

第六次评估的 R7（Node 前置）此前是「≥22.19 单调抬高」。7.1 把它变成**区间黑名单**且根因是 SQLite WAL
数据损坏安全（#106065 + #1739 "reject runtimes vulnerable to WAL corruption"）。含义两条：(a) Mac Mini
升级前不能只确认「node 够新」，必须确认 node 版本**落在 7.1 接受的具体区间内**（跑在 23.x 或 24.0–24.14
会被直接拒绝安装）；(b) 这个约束会**持续演进**——未来版本会继续 reject 新发现的 WAL 漏洞运行时，node 升级与
Gateway 升级从此耦合。这是第六次评估 M2「SQLite 单向门」风险的直接加深：连底层 node 运行时都被 SQLite 状态层
的数据完整性要求绑定了。

### 18.5 收益侧新增（诚实登记，但不改变结论）

7.1 有若干条对我们直接有利（登记，为未来升级窗口的收益侧累积）：
- **launchd 友好**：Gateway crash-loop recovery + `EX_CONFIG` fatal-config 退出码 → systemd/launchd 停止 restart flapping（对我们 launchd 管理 adapter/proxy/Gateway 有利）。
- **SSE 解析健壮化**（#96503）：识别被误标为 JSON 的 event stream 不再重复加 `data:` 前缀 → 与我们 tool_proxy 的 SSE 转换路径同源关切。
- **WhatsApp 重连 rate-limit 缓解**：delivery recovery pacing（#101118/#101058，outage backlog 不再突发撞 channel rate limit）+ outbound pre-connect recovery（#101024/#100979，connect/DNS 失败原子清除 stale send evidence）→ 正对 #9 Baileys 重连封禁 + notify.sh 重复投递关切。
- **诚实边界**：这些收益仍**不抵**判据 1 未收敛 + 判据 3 门槛硬化的结构性风险（升到 ≥5.2 吃全部 M1+M3 breaking + SQLite 单向门 + node 区间约束），方案 B（中间版本）依旧不推荐。

### 18.6 结论与建议：**继续 hold，判据全未满足**

- **方案 A（推荐，不变）**：hold 4.27。三条收敛判据核对结果 ❌🟡🔴——判据 1 未收敛（session accessor
  refactor 弧线在 7.1 深化）+ 判据 3 门槛因 SQLite WAL 安全再升级。开升级窗口的条件未到。
- **下次跟踪点**：2026.7.2 stable 发布时核对——若 7.2 **无** session-accessor/SQLite-migration PR，则判据 1
  的「连续 2 stable」计数从 7.2 起步（需 7.2 + 7.3 两个都干净才满足）；同时核对 7.2 的 engines.node 区间是否
  稳定。判据 3 的 Mac Mini 侧动作（`node -v` 确认落在接受区间）可独立提前完成，与升级解耦。
- **方案 B/C 不变**（见 17.6）：中间版本不推荐；若用户决定现升，方案 C 前置清单基础上**增补 node 区间确认**
  （不只 `node -v ≥22.19`，须确认落在 `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`）。

**LAST_EVAL_DATE 更新至 2026-07-20**（第七次评估完成，重置时间 tripwire）。下次触发 = 任一 tripwire
跳红，或 2026.7.2 stable 发布时的判据 1 跟踪。

---

*本文档为评估报告，不执行任何升级操作。升级决策由用户做出。*

## 第十九节：第八次评估（2026-09-01，2026.8.1 stable 发布触发判据跟踪）

> 触发：第七次评估（18.6）预设的跟踪点是「2026.7.2 stable 发布时核对判据 1」。**2026.7.2 从未 stable**
> ——它走完 beta.1→beta.7（07-15 → 08-02）后被放弃，上游直接跳到 **2026.8.1**（2026-08-31 stable，
> 自 07-13 的 7.1 以来唯一新 minor stable）。按原则 #1「有新版本对比决策条件是否变化」，到期核对三条
> 收敛判据。方法沿用 V37.9.225/244/267「代码即事实」：`npm pack openclaw@2026.8.1` → 全量 CHANGELOG
> grep + tarball 目录实证 + engines 跨版本对比 + **npm 元数据实证外部插件 peer floor**（本次新增维度）。
> Tripwire 机械状态 0/6（时间 43/180 天，版本差距 29/50——字典序粗略计数；精确 minor stable 见 19.2）。

### 19.1 一句话结论

**继续 hold，但卡点首次收敛到单一判据。② 发版节奏首次满足；③ Node 区间**已于 2026-07-24 满足**
（用户 `brew upgrade node` 25.6.1 → **26.5.0**，落在 8.1 接受区间 `>=25.9.0`（无上界），且 4.27-on-node26
兼容已受控验证）；**唯一未满足的是判据 ①**——session-accessor refactor 子弧线确已在 8.1 收尾，但 SQLite
状态迁移弧线不仅未收敛反而**扩面**（8.1 把团队凭据这类新状态搬进 SQLite）。同时本次新增两项此前评估没有的判断依据：🔴 **第四类风险「默认自主
行为扩张」**（8.1 把 6 项自主行为设为默认开，其中 3 项直接踩我们已立案的血案机制）+ 🔴 **持有成本首次可量化**
（外部插件生态已把 4.27 钉在旧版本上，weixin 插件落后 4 版 / 71 天）。**

### 19.2 上游现状（2026-09-01 实证）

- dist-tags：latest = **2026.8.1**（08-31）/ beta = 2026.9.1-beta.1（08-28）/ extended-stable = 2026.6.34（08-04）/ alpha = 2026.5.19-alpha.1。
- **2026.7.2 从未发布 stable**：beta.1→beta.7 跑了 18 天（07-15 → 08-02）后被放弃，变更并入 8.1。第七次评估把它设为跟踪点，实际由 8.1 承接。
- 4.27 → 8.1 **精确 26 个 minor stable**（其中 6.33/6.34 属 extended-stable 维护线，主线 minor = 24）；check_upgrade.sh 字典序粗略计数报 29/50，两口径均远低于 50 tripwire。
- **4.27 仍未被 deprecate**（`npm view openclaw@2026.4.27 deprecated` 空），可安全停留；发布于 2026-04-29，至今 125 天。
- engines.node：**8.1 与 7.1 逐字相同** = `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`（未再收紧）。

### 19.3 三条收敛判据逐一核对

| 判据 | 第七次（07-20）状态 | 第八次（09-01）实证 | 判定 |
|------|-------------------|-------------------|------|
| **① SQLite/session 弧线收敛**（连续 2 stable 无 session-store/SQLite 迁移 PR） | 7.1 密集 session-accessor refactor（#101178/#101179/#101180/#101688）+ SQLite state + legacy-state 迁移 = 未收敛 | **局部改善**：accessor refactor 家族在 8.1 changelog **完全消失**（该子弧线看似收尾）。**但主弧线扩面**：① 新状态入 SQLite——shared credential store 把团队 secret/env 搬进 SQLite（#121559/#121724/#126088）② session 历史迁移 #127241/#131527/#131276（"migrate long histories without exhausting the heap"）③ schema 迁移校验 #105583（"accepting supported additive-migration layouts"）④ WAL 数据损坏修复两条 #132844（split-brain cleanup 损坏 DB）/ #120597（virtiofs·9p 上 WAL 损坏）⑤ 启动期 doctor 迁移 #132135 ⑥ SQLite terminal session recovery（transcript mtime 入 agent DB + doctor import 保留 legacy mtime）⑦ restart sentinel 报 SQLite 读写与 legacy-file cleanup 失败 #106385。另有**两条新 breaking 迁移**：OpenProse 插件移除 #128494 + OpenAI route migration（codex/* → openai/*，**明文 touches stored sessions**） | ❌ **未满足**，「连续 2 stable 干净」计数仍为 0 |
| **② 发版节奏 ≤1/周** | 6.11→7.1 间隔 13 天放缓，但同日两 patch = 混合信号，边界未稳定 | **首次明确满足**：7.1（07-13）→ 8.1（08-31）间隔 **49 天**，期间主线仅 2 个 patch（7.1-1/7.1-2，07-18）+ 维护线 2 个（6.33/6.34）。4 周窗口（08-04→08-31）= 2 stable ≈ **0.5/周**；7 周窗口（07-13→08-31）= 5 stable ≈ **0.71/周**。对比 6 月 7 stable ≈ 周更。且 7.2 攒进 8.1 而非小步 stable = 节奏收敛的结构性证据 | ✅ **满足**（首次） |
| **③ Mac Mini node 落在接受区间** | 7.1 收紧为区间黑名单（根因 SQLite WAL 安全 #106065） | 两侧都已到位：**上游侧** 8.1 门槛未再收紧（engines 与 7.1 逐字相同）；**我方侧已于 2026-07-24 满足**——用户 `brew upgrade node` 25.6.1 → **26.5.0**，落在第三区间 `>=25.9.0`（无上界），同日 restart.sh 二次验证 4.27-on-node26 三服务健康 + gateway 200 | ✅ **满足**（升级窗口开时仍须按当时 stable 的 engines 重核一次，见 7.0 前置 A——上游有加区间黑名单先例） |

### 19.4 🔴 新增第四类风险：默认自主行为扩张（前三次评估均不存在的维度）

第六/七次评估聚焦「状态迁移 / breaking / node 门槛」三类。8.1 引入一个**性质不同的新风险**：它不只改变状态
存储，还**改变 agent 的默认自主程度**——而其中 3 项恰好踩在我们已立案的血案机制上：

| 8.1 默认变更 | 机制 | 对应我方血案 / 约束 |
|---|---|---|
| **Grounded dreaming 默认开**（#114819，model-backed 后台记忆整合 + Dream Diary） | 后台自发 LLM 调用，经 proxy→adapter→provider 链 | 🔴 `dream_quota_blast_radius_case.md`（V37.2：30+ LLM 调用 × 后端宕机 → Gemini 配额耗尽 → 跨 job 垃圾推送）。Gateway 一起来就开始后台消费，配额/成本非零 |
| **Owner-directed ambient heartbeat 默认开**（#121988，ambient heartbeat 告警发 owner DM） | heartbeat 机制默认投递 | 🔴 `heartbeat_md_pa_self_silencing_case.md`（V37.8.16：HEARTBEAT.md 触发 heartbeat 机制 → PA 对所有消息回 HEARTBEAT_OK → 13h 完全静默） |
| **Session reset default 变更**（#111140，无 reset policy 时跨闲置/跨天保留会话） | 会话生命周期假设变化 | 🔴 `pa_alert_contamination_case.md`（V37.4.3 告警经 sessions.json 污染对话上下文）+ 我方硬纪律「PA 行为变更后必须清空 session + 重启 Gateway + WhatsApp 实测」 |
| **Automatic self-learning 默认开**（#115576，自动应用 scanner-approved 技能） | 自主变更自身能力面 | 与纲领反目标「enforcement 永久 human-approval」相悖，需显式关闭 |
| **Skill Workshop approvals 默认无额外批准提示**（#107690，agent 自发 apply/reject/quarantine） | 同上 | 同上（`skills.workshop.approvalPolicy: "pending"` 可 opt-in 找回审批门） |
| **Personal conversation recall 默认开**（#110597，Active Memory 开启时默认召回同 agent 私聊上下文） | 上下文注入面扩大 | 同 `pa_alert_contamination_case` 家族（上下文污染） |
| **CPU-scaled foreground concurrency 8–16**（#114047） | 默认并发按 CPU 核数放大 | 我方链路假设：12 工具上限 / 200KB 请求体 / 单 adapter 单 proxy；Mac Mini 上默认 8–16 并发是新负载假设 |

**性质与缓解**：每项都有显式 disable/opt-out，因此不是「不可升」，而是**升级 SOP 必须新增一个环节**——
「默认行为审计与显式关闭清单」（7 项，见 7.0 前置 C），且必须在**首次启动前**完成（否则 Gateway 起来即开始后台 LLM 消费 +
自动应用技能）。这条对我们的意义大于对普通用户：我们三个血案案例正好覆盖其中三项机制，等于上游把我们
花了三次事故才关掉的东西默认打开了。

### 19.5 🔴 持有成本首次可量化：外部插件生态版本锁

前七次评估的持有成本一直是定性的（"版本差距复利"）。本次首次拿到**机器可验证的量化证据**：

- **weixin 插件已把我们钉死**：`@tencent-weixin/openclaw-weixin` 的 `peerDependencies.openclaw` 在
  **2.4.5（2026-06-22）** 从 `>=2026.3.22` 抬到 **`>=2026.5.12`**。我们的 4.27 < 5.12 → 可用上限停在
  **2.4.4（2026-05-22）**。而 **2.4.8 于 2026-09-01（今天）发布** = 插件在活跃维护，我们**落后 4 个版本 / 71 天**，
  且此后所有 weixin 修复都拿不到。诚实边界：peerDependencies 是声明不是硬阻断（npm 可 `--force`），
  但维护者已声明与 <5.12 不兼容，强装属无支持路径。
- **官方 channel 插件与 core 锁步**：`@openclaw/whatsapp@2026.8.1` 与 `@openclaw/discord@2026.8.1` 均声明
  `peerDependencies: { openclaw: '>=2026.8.1' }`，且版本序列自 `2026.5.1-beta.1` 起**逐 core 版本发布**
  （112 个版本）。含义两条：(a) M1 外部化之后 core 与 channel 插件必须**同版本移动**，不能单独 pin
  → **方案 B（升到中间版本）比第六次评估时更不可取**；(b) 升级时 channel 插件版本由 core 版本决定，
  升级失败回滚也要把插件一起回退。
- tarball 目录实证 M1 仍成立：8.1 包内 whatsapp 相关文件仅剩 `dist/config-doctor/whatsapp.js`、
  `control-ui/plugin-art/whatsapp.webp`、`doctor-whatsapp-responsiveness-*.js` 与 2 份 docs——**无插件实现**
  （对比 4.27 的 77 个 whatsapp 文件），R1「升级须经 doctor/update 网络安装外部插件」的风险不变。

### 19.6 收益侧新增（诚实登记，不改变结论）

8.1 有几条对我们**直接**有利，登记以累积未来开窗时的收益侧：

- 🔴 **`OPENCLAW_SUPERVISOR_MODE=external`**（#109162/#119846/#121069）：让外部 supervisor 拥有 Gateway
  重启、服务生命周期与更新，**不与原生服务管理竞争**。这正对我们 launchd 单一管理者不变式
  （V37.9.12.1 双管理血案 → V37.9.13 restart.sh 收编 `launchctl kickstart`）——上游首次提供官方的
  「我不管进程，你管」开关。这是本次收益侧最有价值的一条。
- **`openclaw backup sqlite`**（#105718）：create / list / verify / restore 紧凑的全局与 per-agent 数据库快照。
  第六次评估登记过「`backup create` 对 SQLite 的覆盖语义未实证」——8.1 给了专门的快照+校验+恢复 CLI，
  **升级前快照这一缓解手段实质增强**（但见下条的方向性限制）。
- **Newer database state fence**（#132916/#133081）：安装版 Gateway 遇到**更新的 schema** 时停止 restart loop、
  拒绝竞争性 shared-state schema 变更、用 unhealthy readiness 围栏隔离不兼容缓存状态。**部分缓解 M2**，
  但**保护方向向前不向后**——回滚到 4.27 时，4.27 里没有这套 fence，所以「升级后回滚」的单向门性质不变。
- **HTTP API failures**（#133275）：agent 失败与整体超时在 Chat Completions/Responses 里报 error，**包括已发出
  部分内容的流**。与我们 fail-plausible 关切同源（部分流之后的失败不再被吞成成功）。
- **Session settings 并发安全**（#124471）：不同进程并发写 settings 不再互相覆盖；读缺失 settings 无文件系统副作用。
- WhatsApp：未匹配附件保留（#131672）、QR 登录与改账号动作限制在 owning operator（#129381）。
- **诚实边界**：这些收益仍**不抵**判据 ① 未满足 + 19.4 新增的默认自主行为风险 + 19.5 的 M1 锁步约束。

### 19.7 结论与建议：**继续 hold（判据 ② 首次满足，① 未满足，③ 待我方确认）**

- **方案 A（推荐，不变）**：hold 4.27。判据核对 **❌✅✅** —— ②③ 均已满足，**卡点首次收敛到判据 ① 一条**。
  但 ① 恰是三条里权重最高的（它直接决定回滚单向门的严重程度），且 8.1 **新增状态入 SQLite** 说明迁移弧线
  在扩面而非收尾。**含义**：一旦 ① 满足（连续 2 个 stable 干净），升级窗口即可开——届时不再有其他判据阻挡，
  只需走 7.0 的三项前置（A node 区间重核 / B 插件锁步 / C 默认行为审计）。
- **下次跟踪点（更新）**：
  1. **2026.9.1 stable 发布时核对判据 ①**（beta.1 已于 08-28 发布）。**核对必须走 19.8 的协议**（自带防空转
     门槛），不要裸 grep——beta.1 的提前读数已证明裸 grep 会给出假绿。若 9.1 的 changelog **无** SQLite/session
     迁移类 PR，则「连续 2 stable 干净」计数从 9.1 起步为 1，**仍需再一个干净 stable** 才满足。
  2. **判据 ③ 已绿，无需再跟踪**（2026-07-24 node 26.5.0 落在 `>=25.9.0`）。仅在真正开升级窗口时，
     按**当时** stable 的 `engines.node` 重核一次（7.0 前置 A）——上游有加区间黑名单的先例。
  3. **新增跟踪项**：19.5 的持有成本是**单调递增**的——每次评估重新测一次 weixin 插件落后版本数/天数，
     作为「继续 hold 的代价」的量化输入（本次基线：落后 4 版 / 71 天）。
- **方案 B（中间版本）：本次证据下更不可取**——19.5 证明 core 与 channel 插件锁步发布，停在中间版本等于
  同时锁死 core 与两个 channel 插件的版本，收益不全拿而成本全担。
- **方案 C（若用户决定现升）**：在第六次评估 17.6 前置清单 + 第七次 node 区间确认之上，**新增第三项前置**——
  **19.4 的默认行为审计清单**（7 项默认变更须在首次启动前显式关闭或确认可接受，落地为 7.0 前置 C），否则 Gateway 起来即
  开始后台 LLM 消费与自动技能应用。

**LAST_EVAL_DATE 更新至 2026-09-01**（第八次评估完成，重置时间 tripwire）。下次触发 = 任一 tripwire 跳红，
或 2026.9.1 stable 发布时的判据 ① 跟踪（走 19.8 协议）。

### 19.8 判据 ① 提前读数（2026.9.1-beta.1）与核对协议

> 同日追加。既然判据 ① 是唯一卡点，先对已发布的 `2026.9.1-beta.1`（08-28）做一次提前读数——
> **结论是「不可判」，不是「干净」**，而得出这个结论的过程本身暴露了核对方法的一个假绿陷阱，
> 故一并把方法固化成带防空转门槛的协议。

**读数结果：`VERDICT=N/A_NO_CONTENT`（判据 ① 在 beta.1 上不可判）**

`sqlite` / `migrat` / `session store` 在 9.1-beta.1 的 changelog 里计数**全为 0**——但这不是干净，
是**内容还没写**：该 changelog 自述「2 in-range PRs + 1,518 retained seed-only PRs」，
其 `### Complete contribution record` 段的 1,520 条目**全是无描述的裸 PR 行**，叙述段只有 17 条。
三个版本对照：

| 版本 | 有语义条目 | sqlite | migrat | 判定 |
|------|-----------|--------|--------|------|
| 2026.7.1 stable | **1719**（叙述 183 + 带描述 PR 1536） | 12 | 15 | 可判 → ❌ 不干净 |
| 2026.8.1 stable | **506**（叙述 506 + 带描述 PR 0） | 13 | 19 | 可判 → ❌ 不干净 |
| **2026.9.1-beta.1** | **17**（叙述 17 + 带描述 PR **0**，裸 PR 行 1520） | 0 | 0 | ⚠️ **不可判** |

**这是一次典型的假绿**：一个返回 0 的 grep 读起来像「上游终于收敛了」，实际含义是「还没有数据」。
与 V37.9.288「搜索坏了 ≠ 没搜到」/ V37.9.310「缺工具伪装成否定结果」/ V37.9.322「跑不动 ≠ 没问题」
同族；而这里的误判方向是**最贵的那一个**——假绿会让判据 ① 计数错误 +1，进而提前开升级窗口。

**方法学修正（设计协议时第二次自我证伪）**：最初想用「只数叙述段（Highlights/Changes/Fixes）」作内容
门槛与扫描范围，实测被 7.1 否决——7.1 的 session-accessor 证据（#101178 "add debt ratchet to the
session-accessor boundary guard" / #101179 "route new session store bypasses through the accessor"）
恰恰住在 **Complete contribution record 段且带描述**（叙述段只有 sqlite=2/migrat=3，全文 12/15）。
故协议固定为两条：**扫描范围 = 整份 changelog**；**内容门槛 = 叙述条目 + 带描述 PR 条目**
（裸 PR 行与仅 Thanks/Related/Fixes/Closes 的条目不计入）。

**判据 ① 核对协议（下次 stable 发布时照此执行，替换目标版本号）**

```bash
WORK=$(mktemp -d) && cd "$WORK" && npm pack openclaw@2026.9.1 >/dev/null 2>&1 && python3 - <<'PYCHK'
import re, sys, tarfile, glob
cl = tarfile.open(sorted(glob.glob("openclaw-*.tgz"))[-1]).extractfile("package/CHANGELOG.md").read().decode("utf-8", "replace")
i = cl.find("### Complete contribution record")
narrative = len([l for l in (cl[:i] if i > 0 else cl).splitlines() if l.startswith("- **")])
pr = [l for l in cl.splitlines() if l.startswith("- **PR #")]
described = len([l for l in pr if re.match(r"^- \*\*PR #\d+\*\*\s+(?!Thanks\b|Related\b|Fixes\b|Closes\b)\S", l)])
semantic = narrative + described
print(f"semantic_entries={semantic} narrative={narrative} described_pr={described} bare_pr={len(pr)-described}")
if semantic < 100:
    print("VERDICT=N/A_NO_CONTENT  judgement-1 not decidable: changelog semantic content too thin")
    sys.exit(3)
hits = re.findall(r"(?im)^.*(?:sqlite|migrat|session store|session accessor|legacy state).*$", cl)
print(f"migration_hits={len(hits)}")
for h in hits[:25]:
    print("  " + h.strip()[:160])
print("VERDICT=" + ("CLEAN" if not hits else "DIRTY"))
sys.exit(0 if not hits else 2)
PYCHK
```

退出码：`0` = CLEAN（判据①连续计数 +1）/ `2` = DIRTY（计数归零）/ `3` = 不可判
（**不得计入任何一侧**，等内容填充后重跑）。

**门槛 `100` 的依据与诚实边界**：三个实测点——真 stable 的语义条目是 **506**（8.1）与 **1719**（7.1），
beta 骨架是 **17**；100 在两者之间有 5× 以上余量。只有 3 个数据点，故取**保守方向**：宁可多判一次
「不可判」再等 stable，也绝不接受假绿开窗。若未来某个真 stable 的语义条目低于 100 而内容确实完整，
按实测数据下调门槛并在此登记，不要为了让它通过而绕过门槛。

**刻意不把这段做成仓库脚本**（日落法）：核对每 6–8 周一次、由 session 手动执行，一个自带门槛的可复制
命令块已足够；做成常驻脚本要付出新文件 + FILE_MAP + 部署 + 自身守卫的代价，而它退役不了任何东西。
守卫改为钉住本协议的两个要害（整份扫描 + 防空转门槛），防止未来有人把它简化回裸 grep。

---

# OpenClaw Gateway 升级评估：v2026.3.13-1 → v2026.4.x

> 初次评估：2026-04-04
> 二次评估：2026-04-10（上游已到 v2026.4.9，#59265 仍 OPEN）
> 三次评估：2026-04-29（实证查明：#59265 已 closed but no fix evidence + 发现 v2026.4.26 新硬阻塞 #73358 + 引入 tripwire 决策框架）
> 四次评估：2026-05-05（#73358 已 v2026.4.27 修复，战略路径已开，推荐方案 C 等 5.x 沉淀，第十三节）
> 五次评估：2026-06-08（当前数据实证：上游 v2026.6.1，6.x 加 SQLite/plugin breaking，#59265 仍无 fix，用户决策继续 hold 到 6/20 时间表，目标 v2026.4.27，第十四节）
> 评估者：Claude Code
> 状态：**继续 hold — 用户决策守 6/20 时间表 | 目标 v2026.4.27（避开全部 4.29/5.x/6.x breaking）| 版本差距 tripwire likely 触发**

---

## 一、版本概览

| 项目 | 值 |
|------|------|
| 当前部署版本 | v2026.3.13-1 |
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
- [ ] 确认目标版本：建议 **v2026.4.2**（修复 #59265 需确认）
- [ ] 时间窗口：工作日白天，确保能快速回滚
- [ ] 在 Mac Mini 上 SSH 直连执行（**禁止通过 WhatsApp 触发**）

### 7.1 升级前备份（5 分钟）
```bash
# 1. 备份配置
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak-$(date +%Y%m%d)
cp ~/.openclaw/cron/jobs.json ~/.openclaw/cron/jobs.json.bak-$(date +%Y%m%d)

# 2. 备份 workspace state
cp -r ~/.openclaw/workspace/.openclaw/ ~/openclaw_workspace_backup_$(date +%Y%m%d)/

# 3. 记录当前版本
openclaw --version > ~/upgrade_before_version.txt
```

### 7.2 升级前检查（3 分钟）
```bash
# 4. 运行升级就绪检查
bash ~/openclaw-model-bridge/check_upgrade.sh

# 5. 确认所有服务正常
bash ~/openclaw-model-bridge/preflight_check.sh --full

# 6. 检查 legacy config
openclaw doctor  # 查看有无 warning/error
```

### 7.3 执行升级（5 分钟）
```bash
# 7. 停止 Gateway
openclaw gateway stop 2>/dev/null || true
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
sleep 2

# 8. npm 升级（建议锁定版本）
npm install -g openclaw@2026.4.2

# 9. 修复配置（如有 legacy key）
openclaw doctor --fix

# 10. 重启 Gateway
bash ~/restart.sh
sleep 5
```

### 7.4 升级后验证（10 分钟）
```bash
# 11. 基础健康检查
openclaw --version  # 确认新版本
curl -s http://localhost:18789/health
curl -s http://localhost:5002/health
curl -s http://localhost:5001/v1/models

# 12. 消息通道验证（双通道）
openclaw message send --channel whatsapp -t "$OPENCLAW_PHONE" -m "升级验证 $(openclaw --version)"
openclaw message send --channel discord -t "user:$DISCORD_TARGET" -m "升级验证 $(openclaw --version)"

# 13. 全面体检
bash ~/openclaw-model-bridge/preflight_check.sh --full
bash ~/openclaw-model-bridge/job_smoke_test.sh

# 14. WhatsApp 业务验证
# → 手动在 WhatsApp 发消息，确认 PA 正常回复
# → 发送一张图片，确认多模态路由正常
# → 触发 search_kb，确认混合检索正常

# 15. 通道状态
openclaw channels status --probe
```

### 7.5 回滚方案（如升级失败，30 秒）
```bash
# 停止 Gateway
openclaw gateway stop 2>/dev/null || true
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true

# 降级回原版本
npm install -g openclaw@2026.3.13-1

# 恢复配置
cp ~/.openclaw/openclaw.json.bak-$(date +%Y%m%d) ~/.openclaw/openclaw.json

# 重启
bash ~/restart.sh

# 验证
curl -s http://localhost:5002/health
openclaw message send --channel whatsapp -t "$OPENCLAW_PHONE" -m "回滚完成"
```

## 八、综合评估

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

*本文档为评估报告，不执行任何升级操作。升级决策由用户做出。*

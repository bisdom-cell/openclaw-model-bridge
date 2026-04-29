# OpenClaw Gateway 升级评估：v2026.3.13-1 → v2026.4.x

> 初次评估：2026-04-04
> 二次评估：2026-04-10（上游已到 v2026.4.9，#59265 仍 OPEN）
> 三次评估：2026-04-29（上游已到 v2026.4.26，#59265 状态因 sandbox 环境受限无法直查，按节奏推断仍 OPEN）
> 评估者：Claude Code
> 状态：**继续 hold — 三个 hold 条件全部未变化 + 无功能驱动**

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

## 十一、三次评估记录（2026-04-29）

### 背景

上游从 v2026.4.9 (4/10) 推进到 **v2026.4.26**（2026-04-28 发布，最新稳定版），中间还出了 v2026.4.25-beta.1~9 + v2026.4.26-beta.1。距上次评估 19 天，距 V37.8.15 (4/16 changelog) 评估 13 天。

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

### 阻塞条件复查

| 阻塞项 | 二次评估时（v2026.4.9） | 三次评估时（v2026.4.26） | 结论 |
|--------|------------------------|--------------------------|------|
| **#59265: Agent actions 不可见** | OPEN（v2026.4.2 macOS 复现） | **sandbox 环境无法直查 GitHub API**。但 V37.8.15 (4/16) 已确认"经 14 个版本仍 OPEN"。从 4/16 到 4/29 又过 13 天 + 10+ 版本，按上游 4/3 提交后**连续三次评估均 OPEN** 的深层 bug 节奏，**仍 OPEN 概率极高** | 硬阻塞按推断未解除 |
| **trusted-proxy auth 变更** | 未做 localhost 链路验证 | **仍未做验证**（无新版本会自愈这个问题） | 未解除 |
| **v2026.4.5 legacy config alias 移除** | 新增中风险 | 仍生效（升级时仍需 `openclaw doctor --fix`） | 未解除 |

### 三次评估结论

**继续 hold，理由不变且更强**：

1. **#59265 验证盲区**：当前环境无法直查 GitHub API 确认状态。按"理解再动手"原则 #28，**未验证不动手**。这一项任何变化前不应推动升级。
2. **跨度从 11 个增加到 30+ 个中间版本**：breaking changes 累积（trusted-proxy + legacy config + Plugin SDK 废弃），一次性吞下变更的回滚验证成本高。
3. **无功能驱动**：当前 v2026.3.13-1 + V37.9.21 控制平面 1600 tests / 0 fail / 安全 95/100，Mac Mini E2E 完美通过（用户 4/27 实测 WhatsApp Part [1/2]+[2/2] + Discord 推送 + 35 cron 全部稳定）。**升级不带来用户可见收益**。
4. **原则 #8（做减法）+ V37.8.15 教训**："上游有新版 ≠ 我们必须升级"。没人催着升级 = 现在不升级。

### 触发重新评估的条件（任一满足即可）

- #59265 confirmed closed（需在能直查 GitHub API 的环境验证）
- 上游出现明确的安全 CVE 影响 v2026.3.13-1
- 用户业务出现 v2026.3.13-1 无法解决的痛点
- WhatsApp plugin 重大破坏性变更（v2026.3.13-1 不再可用）

### 下次检查

每周一 `check_upgrade.sh`（已 cron）+ 在能访问 GitHub API 的环境主动 poll #59265 状态。

## 九、升级后文档更新清单

升级成功后需同步更新：
- [ ] `docs/config.md` 第 5 行：版本号 + hold 状态
- [ ] `CLAUDE.md`：版本引用
- [ ] `SOUL.md`：Gateway 版本字段
- [ ] `status.json`：constraints 中的 Gateway hold 条件
- [ ] `upgrade_openclaw.sh`：确认脚本与新版本兼容
- [ ] `check_upgrade.sh`：更新 hold 逻辑（如不再需要）

---

*本文档为评估报告，不执行任何升级操作。升级决策由用户做出。*

# OpenClaw Gateway 升级评估：v2026.3.13-1 → v2026.4.x

> 评估日期：2026-04-04
> 评估者：Claude Code
> 状态：**评估完成，待用户决策**

---

## 一、版本概览

| 项目 | 值 |
|------|------|
| 当前部署版本 | v2026.3.13-1 |
| 原 hold 条件 | 等 @openclaw/whatsapp 正式发布 + ClawHub 429 修复 |
| 最新稳定版 | **v2026.4.1**（npm 已发布） |
| 最新版 | **v2026.4.2**（2026-04-03 发布） |
| 中间稳定版本 | 6个：v2026.3.23 → 3.23-2 → 3.24 → 3.28 → 3.31 → 4.1 |

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
3. 目标版本至少 v2026.4.3+

**下次检查时机**：每周一 `check_upgrade.sh` + 关注 #59265 进展

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

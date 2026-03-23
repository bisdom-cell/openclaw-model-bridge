# OpenClaw 架构参考文档

> 基于 OpenClaw 开源仓库（github.com/openclaw/openclaw）整理
> 最后更新：2026-03-23 | 适用版本：v2026.3.22

---

## 一、项目概述

OpenClaw 是一个**自托管、多通道网关**，将消息平台（WhatsApp、Telegram、Discord、iMessage 等 20+ 平台）与 AI Agent 连接起来。

- **GitHub**: github.com/openclaw/openclaw（250K+ stars，MIT 协议）
- **前身**: Clawdbot → Moltbot → OpenClaw（2026-01-30 更名）
- **运行环境**: Node.js 22+，单进程 Gateway
- **默认端口**: 18789

## 二、系统架构

```
┌──────────────────────────────────────────────────────────┐
│                   用户消息平台                             │
│  WhatsApp / Telegram / Discord / iMessage / Slack / ...   │
└────────────────────────┬─────────────────────────────────┘
                         │ (各 Channel Adapter)
┌────────────────────────▼─────────────────────────────────┐
│              Gateway 进程 (:18789)                         │
│                                                           │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ Channel  │  │  Queue   │  │  Session  │  │   Tool   │ │
│  │ Adapters │→ │  System  │→ │  Manager  │→ │  Engine  │ │
│  └─────────┘  └──────────┘  └───────────┘  └──────────┘ │
│       │            │              │              │         │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │  Cron   │  │  Memory  │  │  Config   │  │ Control  │ │
│  │ Scheduler│  │  Store   │  │ Hot-Reload│  │    UI    │ │
│  └─────────┘  └──────────┘  └───────────┘  └──────────┘ │
└────────────────────────┬─────────────────────────────────┘
                         │ (OpenAI-compatible API)
┌────────────────────────▼─────────────────────────────────┐
│              LLM Provider                                 │
│  Anthropic / OpenAI / 本地 Proxy / 自定义 Provider         │
└──────────────────────────────────────────────────────────┘
```

## 三、Queue 系统（消息队列）

### 3.1 Lane-aware FIFO Queue

Gateway 使用 **lane-aware FIFO 队列** 管理所有消息处理。每个 lane 独立排队，互不阻塞。

| Lane | 默认并发数 | 用途 |
|------|-----------|------|
| **main** | **4** | 用户消息（DM + 群组） |
| **subagent** | **8** | sessions_spawn 创建的子 agent |
| **cron** | 1 | 定时任务 |
| **hook** | 共享 main（待拆分） | Hook session（Issue #24749 提议独立 lane） |

### 3.2 Queue Mode

| 模式 | 行为 |
|------|------|
| **collect**（默认） | 新消息排队等待，当前 run 完成后才处理下一条 |
| **steer** | 新消息注入当前运行的 agent run（`/queue steer` 或 `/steer`） |
| **interrupt** | 新消息中断当前 run，立即处理 |

### 3.3 并发配置

```json
{
  "agents": {
    "defaults": {
      "maxConcurrent": 4,
      "subagents": {
        "maxConcurrent": 8
      }
    }
  }
}
```

> **注意**: `maxConcurrent` 仅支持在 `agents.defaults` 级别设置，不支持 per-agent 配置。
> **已知问题**: 多 agent 共享 main lane 的全局 cap（Issue #16055）。Workaround：为每个 agent 指定自定义 lane。

### 3.4 已知 Gotchas

- Gateway restart 后 `drainLane()` 可能卡在 `draining=true`，导致子 agent 任务延迟最多 44 分钟（Issue #27407）
- Hook session 与用户消息共享 main lane，高频 hook 可能饿死用户消息（Issue #24749）

## 四、Session 模型

### 4.1 Session 类型

| 类型 | 隔离级别 | 用途 |
|------|---------|------|
| **main** | 每个 sender 一个 session | 直接聊天 |
| **group** | 每个群组一个 session | 群组对话 |
| **isolated** | 完全独立 | cron 任务、子 agent |
| **shared** | 跨 channel 共享 | 多通道同一 agent |

### 4.2 Session 生命周期

1. 消息到达 → Channel Adapter 解析
2. 路由到目标 agent（基于 channel/sender 规则）
3. 查找或创建 session
4. 入队 → 等待 lane 并发位
5. 执行 agent run（LLM 调用 + 工具执行）
6. 返回结果 → Channel Adapter 发送回复

### 4.3 Compaction（压缩）

当 session token 达到阈值（默认 ~40K），Gateway 触发 compaction：
- 将历史对话蒸馏为 memory 摘要
- 写入 daily memory 文件
- 如无重要内容，写入 `NO_FLUSH`

### 4.4 Context Pruning

```json
{
  "agents": {
    "defaults": {
      "contextPruning": {
        "mode": "cache-ttl",
        "ttlHours": 6,
        "keepLastAssistant": 3
      }
    }
  }
}
```

## 五、Tool 系统

### 5.1 内置工具分类

| 组 | 工具 | 说明 |
|----|------|------|
| **group:fs** | `read`, `write`, `edit`, `apply_patch` | 文件系统操作 |
| **group:runtime** | `exec`, `bash`, `process` | Shell 执行 + 进程管理 |
| **group:sessions** | `sessions_list`, `sessions_history`, `sessions_send`, `sessions_spawn`, `session_status` | 会话管理 |
| **group:memory** | `memory_search`, `memory_get` | 记忆检索 |
| **group:web** | `web_search`, `web_fetch` | 网页搜索 + 抓取 |
| **group:ui** | `browser`, `canvas` | 浏览器控制 + Canvas |
| **group:automation** | `cron`, `gateway` | 定时任务 + 网关管理 |
| **group:messaging** | `message` | 跨平台消息发送 |
| **group:nodes** | `nodes` | 设备节点（相机/屏幕） |
| 其他 | `image`, `pdf`, `loop-detection`, `agents_list`, `diffs` | 图片分析、PDF、循环检测 |

### 5.2 工具权限配置

```json
{
  "tools": {
    "allow": ["web_search", "web_fetch", "exec", "read", "write"],
    "deny": ["browser", "canvas"],
    "profile": "coding",
    "byProvider": {
      "anthropic/*": {
        "deny": ["browser"]
      }
    }
  }
}
```

- `deny` 优先于 `allow`（deny wins）
- 支持通配符匹配
- `profile` 预设：`minimal`, `coding`, `messaging`, `full`

### 5.3 工具数量限制（与本项目相关）

我们的 Tool Proxy (`tool_proxy.py`) 将 Gateway 发来的工具从 ~24 个过滤到 **≤12 个**，因为 Qwen3 对大量工具定义容易混乱。这是中间件层面的限制，与 Gateway 的 `tools.allow/deny` 配合使用。

## 六、Agent 配置

### 6.1 openclaw.json 完整结构

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/projects",
      "repoRoot": "~/projects",
      "model": {
        "primary": "provider/model-id",
        "fallback": "provider/fallback-model"
      },
      "maxConcurrent": 4,
      "skipBootstrap": false,
      "bootstrapMaxChars": 4000,
      "userTimezone": "Asia/Hong_Kong",
      "timeFormat": "24h",
      "heartbeat": { "model": "cheap-model" },
      "compaction": {
        "memoryFlush": { "tokenThreshold": 40000 }
      },
      "contextPruning": {
        "mode": "cache-ttl",
        "ttlHours": 6,
        "keepLastAssistant": 3
      },
      "sandbox": {
        "mode": "off",
        "workspaceAccess": "full"
      },
      "subagents": {
        "maxConcurrent": 8
      }
    },
    "list": [
      {
        "name": "agent-name",
        "model": { "primary": "provider/model" },
        "workspace": "/path/to/workspace",
        "channels": ["whatsapp"],
        "tools": {
          "allow": ["exec", "web_fetch"],
          "deny": []
        }
      }
    ]
  },
  "channels": {
    "whatsapp": {
      "dmPolicy": "allowlist",
      "allowFrom": ["+85200000000"],
      "groupPolicy": "disabled"
    }
  },
  "gateway": {
    "port": 18789,
    "host": "127.0.0.1"
  },
  "tools": {
    "allow": [],
    "deny": [],
    "profile": "coding",
    "exec": { "host": "gateway", "security": "full" },
    "loopDetection": { "enabled": true }
  },
  "messages": {
    "responsePrefix": "",
    "ackReaction": "👍",
    "inboundDebounce": { "ms": 1000 }
  },
  "models": {},
  "auth": {},
  "logging": {
    "level": "info",
    "redactSensitive": "tools"
  },
  "env": {}
}
```

### 6.2 Model 配置（与本项目的关系）

本项目中，模型 ID 配置规则：

| 位置 | 格式 | 示例 |
|------|------|------|
| `openclaw.json` agents.defaults.model.primary | **必须带 provider 前缀** | `qwen-local/Qwen3-235B-...` |
| `adapter.py` / `tool_proxy.py` | 裸 ID（无前缀） | `Qwen3-235B-...` |
| `jobs.json` payload.model | 不指定（继承默认值） | — |

### 6.3 Hot-Reload

Gateway 监控 `~/.openclaw/openclaw.json` 文件变更，大部分配置修改**自动热加载**，无需重启。

## 七、Channel 集成

### 7.1 支持的平台（20+）

WhatsApp, Telegram, Discord, Slack, Google Chat, Signal, iMessage (BlueBubbles), IRC, Microsoft Teams, Matrix, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, WebChat, macOS, iOS/Android

### 7.2 WhatsApp 配置

```json
{
  "channels": {
    "whatsapp": {
      "dmPolicy": "allowlist",
      "allowFrom": ["+85200000000"],
      "groupPolicy": "disabled",
      "groups": {}
    }
  }
}
```

DM Policy 选项：
- `pairing`: 配对码验证
- `allowlist`: 仅白名单号码
- `open`: 所有人可用
- `disabled`: 关闭

### 7.3 登录流程

```bash
openclaw channels login        # 扫码登录 WhatsApp
openclaw gateway --port 18789  # 启动网关
```

## 八、Cron 系统

### 8.1 cron 任务添加

```bash
openclaw cron add \
  --name "task-name" \
  --cron "0 */3 * * *" \
  --tz "Asia/Hong_Kong" \
  --session isolated \
  --announce \
  --to "+85200000000" \
  --timeout-seconds 300 \
  --message "执行指令..."
```

### 8.2 关键参数

| 参数 | 说明 |
|------|------|
| `--session isolated` | 独立 session，不与用户对话混合 |
| `--announce` | 将结果推送到指定目标 |
| `--to` | 推送目标（手机号） |
| `--timeout-seconds` | 超时时间 |
| `--thinking` | 思考模式：`off, minimal, low, medium, high, adaptive`（**禁止 `none`**） |

### 8.3 与系统 crontab 的区别

| 特性 | openclaw cron | 系统 crontab |
|------|-------------|-------------|
| 经过 LLM | 是 | 否 |
| 工具访问 | 完整工具链 | 仅 shell |
| Session | Gateway 管理 | 独立进程 |
| 适用场景 | 需要 LLM 理解/生成的任务 | 确定性脚本（清理、备份、抓取） |

### 8.4 已知问题

- Isolated cron session 不会从 workspace 加载 skills（Issue #10804）
- Model override 在 isolated session 中可能被忽略（Issue #13159）

## 九、监控 & 健康检查

### 9.1 内置端点

| 端点 | 说明 |
|------|------|
| `http://localhost:18789/` | Control UI（Web 界面） |
| `http://localhost:18789/health` | 健康检查（HTTP 状态码） |

### 9.2 CLI 诊断

```bash
openclaw gateway status     # 网关状态
openclaw sessions list      # 活跃 session 列表
openclaw cron list          # cron 任务列表
```

### 9.3 日志

Gateway 日志输出到 stdout，可通过 launchd/systemd 重定向到文件。

## 十、与本项目的集成架构

```
WhatsApp → Gateway (:18789) → Tool Proxy (:5002) → Adapter (:5001) → 远程GPU
           [OpenClaw开源]      [本项目]              [本项目]          [Qwen3-235B]
           maxConcurrent=4     ThreadingMixIn        ThreadingMixIn    vLLM batch
           lane-aware FIFO     工具过滤12个           多Provider转发
           session管理          截断200KB              认证+/health
           cron调度             SSE转换
           工具执行             token监控
```

### 10.1 并发能力总结

| 层 | 并发能力 | 瓶颈说明 |
|----|---------|---------|
| Gateway (main lane) | 4 并发 | 可配置 `maxConcurrent` |
| Tool Proxy | 无限制（per-thread） | Python ThreadingMixIn |
| Adapter | 无限制（per-thread） | Python ThreadingMixIn |
| 远程 GPU | 1-4 并发 | 取决于 vLLM batch 配置 |
| **端到端实际并发** | **~4** | 受限于 GPU 推理速度 |

### 10.2 关键配置文件位置（Mac Mini）

| 文件 | 路径 | 说明 |
|------|------|------|
| openclaw.json | `~/.openclaw/openclaw.json` | Gateway 主配置 |
| jobs.json | `~/.openclaw/cron/jobs.json` | Cron 任务定义 |
| Session 存储 | `~/.openclaw/agents/main/sessions/` | Session JSONL 文件 |
| Memory | `~/.openclaw/agents/main/memory/` | 记忆文件 |
| 日志 | launchd 管理 | Gateway 运行日志 |

## 十一、近期重要更新

### 2026.3.x 版本亮点

**v2026.3.22（最新稳定版，2026-03-23 发布）**:

⚠️ Breaking Changes（升级前必读）:
- **Plugin SDK 路径变更**: `openclaw/extension-api` 移除，改用 `openclaw/plugin-sdk/*` 子路径
- **Legacy 环境变量移除**: `CLAWDBOT_*` / `MOLTBOT_*` 废弃，必须用 `OPENCLAW_*`
- **Legacy 目录移除**: `.moltbot` 自动检测/迁移移除，必须已迁移到 `~/.openclaw`
- **Chrome 扩展 relay 移除**: `driver: "extension"` 废弃，需运行 `openclaw doctor --fix` 迁移到 `existing-session`/`user` 模式
- **Plugin 安装优先级**: `openclaw plugins install <pkg>` 优先从 ClawHub 查找，再 fallback npm
- **Image generation**: `nano-banana-pro` skill 移除，改用 `agents.defaults.imageGenerationModel`
- **Sandbox**: JVM/glibc/dotnet 注入环境变量被阻断

Provider 架构重构:
- OpenRouter / GitHub Copilot / OpenAI Codex 逻辑重构为 bundled plugins（动态 fallback + runtime auth + cache-TTL）
- 新增 bundled providers: Anthropic Vertex (GCP auth)、Exa / Tavily / Firecrawl (web-search)
- 新增模型: OpenAI `gpt-5.4` 为默认，前向兼容 `gpt-5.4-mini` / `gpt-5.4-nano`
- Provider 兼容修复: 去重重复 tool-call ID、非 OpenAI 后端自动 strip `prompt_cache_key` 和 `strict` 字段
- Per-agent model overrides with fallback auto-revert

WhatsApp 修复:
- **#48703 已修复**: Active listener registry 改为 `globalThis` singleton，修复 bundler code-splitting 导致的 outbound send 失败
- Append recency filter 恢复 + protobuf `Long` timestamp 处理
- Pre-reconnect credential writes（Baileys pairing restart 前先写凭证）

Multi-Agent & Context:
- Per-agent thinking/reasoning/fast defaults（不支持的 override 自动 revert）
- Context engine transcript maintenance 保留 active-branch metadata
- Post-compaction session JSONL truncation（opt-in）
- `/btw` side questions — 工具无关的快速回答，不修改 session context

Health & 监控:
- 可配置 stale-event 阈值和 restart 上限
- Per-channel/account `healthMonitor.enabled` 开关

性能优化:
- Gateway 懒加载 channel plugins + 从 compiled `dist/extensions` 加载 bundled plugins（冷启动大幅加速）
- 配置的 primary model 在 channel startup 前 prewarm
- Token usage 可见性恢复（不再强制 `supportsUsageInStreaming: false`）

安全加固:
- Exec sandbox 阻断 JVM injection + glibc exploitable tunables
- 阻断远程 `file://` media URLs 和 Windows UNC paths
- Telegram pinned-IP SSRF 防护
- Nostr inbound DM policy 在 decrypt 前强制执行

**v2026.3.13-1（前一版本）**:
- **安全**: 配对码改为短效 bootstrap tokens、禁用 workspace 插件自动加载、防止 Docker token 泄露
- **模型**: Kimi Coding 恢复原生 Anthropic tool calls、Replay 丢弃 thinking blocks、Vertex model-ID 标准化
- **Session**: compaction token count 校验修复、reset/compaction 保留 persona/language
- **Cron**: isolated cron nested lane 死锁修复
- **基建**: Docker 时区支持、plugin-SDK chunks 去重（修复 2x 内存回归）、macOS 最低 Node.js 22.16.0

**v2026.3.12**:
- **Breaking**: Cron job delivery 收紧（不再通过 ad hoc agent sends 发送通知）
- Browser origin validation 强制（GHSA-5wcw-8jjv-m286）
- Ollama first-class local/cloud+local setup
- Memory: optional multimodal image/audio indexing via Gemini embedding

**早期 2026.3.x**:
- Queue 可靠性: lane draining flag 重置、restart 拒绝入队、stale message 跳过
- 工具升级: browser/canvas/nodes/cron 从 skills 升级为 first-class agent tools
- ACP runtime: sessions_spawn 支持 `resumeSessionId`
- Loop detection 内置防护

### 值得关注的 Open Issues

| Issue | 状态 | 影响 |
|-------|------|------|
| ~~#48703~~ | **✅ Fixed (v2026.3.22)** | ~~WhatsApp listener Map 被 bundler code-splitting 拆成多实例~~ → `globalThis` singleton 修复 |
| #24749 | Open | Hook session 独立 lane（防止饿死用户消息） |
| #16055 | Open | 多 agent 共享 main lane cap |
| #27407 | Open | restart 后 drainLane 卡住 |
| #13159 | Open | isolated session model override 被忽略 |
| #24832 | Open | 跨 session 共享上下文 |

---

## 参考链接

- GitHub: https://github.com/openclaw/openclaw
- Releases: https://github.com/openclaw/openclaw/releases
- Tools 文档: https://github.com/openclaw/openclaw/blob/main/docs/tools/index.md
- 配置参考: https://github.com/openclaw/openclaw/blob/main/docs/gateway/configuration-reference.md

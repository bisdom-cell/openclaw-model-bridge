# OpenClaw 架构参考文档

> 基于 OpenClaw 开源仓库（github.com/openclaw/openclaw）整理
> 最后更新：2026-03-13 | 适用版本：2026.3.x

---

## 一、项目概述

OpenClaw 是一个**自托管、多通道网关**，将消息平台（WhatsApp、Telegram、Discord、iMessage 等 20+ 平台）与 AI Agent 连接起来。

- **GitHub**: github.com/openclaw/openclaw（191K+ stars，MIT 协议）
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

**v2026.3.13-1（最新，我们当前使用版本 2026.3.13）**:
- **安全**: `/pair` 和 `openclaw qr` 配对码改为短效 bootstrap tokens（不再在聊天/QR 中暴露 gateway credentials）
- **安全**: 禁用 workspace 插件自动加载（克隆仓库不能在未显式信任的情况下执行插件代码）
- **安全**: 防止 Docker 构建上下文中泄露 gateway token
- **模型**: Kimi Coding 恢复原生 Anthropic 格式 tool calls（修复 XML/纯文本退化）
- **模型**: Replay 时丢弃 Anthropic thinking blocks
- **模型**: google-vertex provider 应用 Gemini model-ID 标准化
- **Session**: 修复 compaction 后的 full-session token count 校验
- **Session**: session reset/compaction 时保留 `lastAccountId`、`lastThreadId`、persona、language
- **Cron**: 修复 isolated cron nested lane 死锁
- **Telegram**: threaded media transport policy, IPv4 download retry
- **Discord**: 处理 gateway metadata fetch 失败
- **Slack**: opt-in interactive reply directives
- **基建**: Docker 时区支持 (`OPENCLAW_TZ`)
- **基建**: 去重 plugin-SDK chunks（修复约 2x 内存回归）
- **基建**: macOS 最低 Node.js 版本对齐至 22.16.0

**v2026.3.12（我们当前使用版本）**:
- **安全**: Browser origin validation 强制应用于所有 WebSocket 连接（GHSA-5wcw-8jjv-m286）
- **Breaking Change**: Cron job delivery 收紧 — 不再通过 ad hoc agent sends 或 fallback summaries 发送通知
- 剥离 leaked model control tokens from assistant text
- Kimi Coding tool call 格式修正为 native Anthropic format
- iOS: bundled welcome screen, docked toolbar
- macOS: chat model picker, persistent thinking-level selections
- Ollama: first-class local/cloud+local setup
- Memory: optional multimodal image/audio indexing via Gemini embedding

**v2026.3.8**:
- ACP provenance metadata, CLI version hash
- xAI web_search collision guard (v2026.3.7)

**早期 2026.3.x**:
- Queue 可靠性增强: lane draining 保证 flag 重置、restart 期间拒绝新入队、stale message 跳过
- diffs 插件工具: 只读 diff 渲染
- ACP runtime: sessions_spawn 支持 `resumeSessionId` 恢复会话
- 工具升级: browser/canvas/nodes/cron 从旧 skills 升级为 first-class agent tools
- Loop detection: 内置循环检测防护

### 值得关注的 Open Issues

| Issue | 状态 | 影响 |
|-------|------|------|
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

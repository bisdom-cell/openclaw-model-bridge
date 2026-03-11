# 一台 Mac Mini 如何跑起完整的 AI 助手基础设施：四层自愈架构实战

> 用一台 Mac Mini + 一个远程 GPU，搭建从 WhatsApp 对话到自动部署的全链路 AI 系统。没有 Kubernetes，没有云厂商账单，只有 crontab、launchd 和几百行 Python/Bash。

---

## 背景：为什么不用现成的？

市面上的 AI 助手方案要么绑定特定平台（ChatGPT、Gemini），要么需要复杂的云基础设施。我想要的很简单：

- **WhatsApp 作为唯一入口**（手机上随时用，不需要开电脑）
- **模型可换**（今天用 Qwen3-235B，明天可以换 GPT-4o 或 Claude）
- **自动化信息流**（论文、技术新闻、行业动态定时推送到手机）
- **零运维**（推代码就部署，出问题自动告警，不需要半夜爬起来修）

最终方案：一台放在桌上的 Mac Mini，跑着一套四层自愈架构。

---

## 整体架构：四层洋葱模型

```
          ┌─────────────────────────────┐
          │    WhatsApp (用户入口)        │
          └──────────┬──────────────────┘
                     │
    ━━━━━━━━━━━━━━━━━▼━━━━━━━━━━━━━━━━━━━━━━━━━
    ① 核心数据通路    Gateway → Proxy → Adapter → GPU
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ② 定时任务层      8 个自动化 Job（论文/新闻/行业/KB维护）
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ③ 监控层          4 级自动告警（保活/看门狗/统计/健康检查）
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ④ DevOps 层       自动部署 + 漂移检测 + 9 项体检
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

每一层都可以独立故障而不影响其他层。这不是设计出来的，而是踩了足够多的坑之后演化出来的。

---

## 第一层：核心数据通路

一条消息从 WhatsApp 到 AI 回复，经过四个节点：

```
WhatsApp → Gateway(:18789) → Tool Proxy(:5002) → Adapter(:5001) → 远程GPU
```

### Gateway（端口 18789）
OpenClaw 框架的核心，负责 WhatsApp 协议接入和工具执行。由 macOS launchd 管理（KeepAlive = true），崩溃后自动重启，不需要额外的看门狗。

这里有一个血泪教训：**进程管理必须单一主控**。我曾经同时用 launchd 和 crontab 两套机制守护 Gateway，结果两者互相踩踏——crontab 检测到进程不在就启动一个新的，launchd 也在重启，最后跑了两个实例抢端口。

### Tool Proxy（端口 5002）
这是整个系统最精巧的部分。远程 GPU 上的 Qwen3-235B 虽然推理能力强，但有几个实际问题：

- **工具过多会混乱**：OpenClaw 提供 24 个工具，但大模型同时面对太多工具时容易"选择困难"。Proxy 用白名单过滤到 12 个。
- **参数幻觉**：模型有时会给工具传入 schema 中不存在的参数。Proxy 会清理掉这些幻觉参数。
- **请求体过大**：长对话的 context 可能超过 280KB 硬限制。Proxy 自动截断旧消息。
- **SSE 转换**：远程 GPU 返回的是普通 JSON，Gateway 期望 SSE 流。Proxy 做格式转换。

架构上，Proxy 被拆成两个文件：

| 文件 | 职责 | 特点 |
|------|------|------|
| `tool_proxy.py` | HTTP 收发 + 日志 | 有网络依赖 |
| `proxy_filters.py` | 过滤/修复/截断/SSE 转换 | **纯函数，零网络依赖** |

为什么要拆？因为纯函数可以写单测。目前有 43 个测试用例覆盖策略层，每次部署自动跑。改一个过滤规则，5 秒内就知道有没有破坏其他逻辑。

### Adapter（端口 5001）
最薄的一层，只做两件事：认证和转发。但它的 Provider 注册表设计值得一提：

```python
PROVIDERS = {
    "qwen":   { "base_url": "...", "api_key_env": "REMOTE_API_KEY",   "model_id": "Qwen3-235B-..." },
    "openai": { "base_url": "...", "api_key_env": "OPENAI_API_KEY",   "model_id": "gpt-4o" },
    "gemini": { "base_url": "...", "api_key_env": "GEMINI_API_KEY",   "model_id": "gemini-2.0-flash" },
    "claude": { "base_url": "...", "api_key_env": "ANTHROPIC_API_KEY", "model_id": "claude-sonnet-4-6" },
}
```

切换模型只需改一个环境变量 `PROVIDER=openai`，不需要改任何代码。这在远程 GPU 维护时非常有用——一行命令切到 OpenAI 备用，等 GPU 恢复再切回来。

### 健康检查级联

三个组件各自暴露 `/health` 端点，形成级联检查：

```
Proxy /health → 检查自身 + 主动探测 Adapter /health
Adapter /health → 本地拦截，不转发到远程 GPU（避免 GPU 维护时误报 502）
```

`curl localhost:5002/health` 一次调用就能知道整条链路是否正常。

---

## 第二层：定时任务——你的个人信息流

这是 Mac Mini 最像"个人助手"的部分。8 个定时任务自动运行，把信息推送到 WhatsApp：

| 任务 | 频率 | 做什么 |
|------|------|--------|
| ArXiv 论文监控 | 每 3 小时 | 抓取 AI 领域新论文，写入知识库 + 推送摘要 |
| HackerNews 热帖 | 每 3 小时 | 抓取 HN 前 30 热帖，过滤中国相关话题 |
| 货代行业动态 | 每天 3 次 | 抓取航运新闻，用 LLM 分析商机，推送要点 |
| OpenClaw 版本 | 每天 | 检查框架新 Release，LLM 生成中文富摘要 |
| GitHub Issues | 每小时 | 监控相关仓库 Issues，推送重要变更 |
| KB 晚间整理 | 每天 22:00 | 整理当天写入的知识库笔记 |
| KB 跨笔记回顾 | 每周五 21:00 | 用 LLM 做跨笔记关联分析 |
| 健康周报 | 每周一 09:00 | 系统全面体检，生成周报推送 |

### 双 Cron 架构

这里有一个设计决策：**确定性脚本和 LLM 脚本分开调度**。

- **System crontab**：纯 bash 脚本，可靠性高，如抓取、写入、备份
- **OpenClaw cron**：经过 LLM 链路的任务，如 KB 回顾需要"理解"笔记内容

为什么分开？因为 LLM 链路有不可控因素（远程 GPU 重启、模型 ID 变更、超时），确定性任务不应该被这些因素影响。

所有任务统一登记在 `jobs_registry.yaml` 注册表中，新增任务必须先登记、通过校验脚本检查，才能注册 cron。这避免了"我在 crontab 里加了一行但忘了在哪"的运维噩梦。

---

## 第三层：监控——四级自动告警

出过一次事故后我学到：**监控必须端到端验证，不能只检查中间状态**。

Gateway 进程在运行 ≠ WhatsApp 能发消息。进程活着但 session 断了，从 `ps aux` 看一切正常，实际上用户已经收不到任何消息了。

四级监控体系：

### 1. Gateway 保活（每 30 分钟）
`wa_keepalive.sh` 向 Gateway 发 HTTP 请求验证服务存活。不发真实消息——我曾经试过发送零宽字符做端到端验证，结果 WhatsApp 还是会显示一个空消息气泡，每 30 分钟收一条空消息，很烦人。

### 2. 任务看门狗（每小时）
`job_watchdog.sh` 检查每个定时任务的状态文件时间戳。如果 ArXiv 监控 7 小时没更新（正常间隔 3 小时的 2 倍 + 缓冲），就推送告警。

它还会扫描最近 1 小时的日志，检测推送失败。这覆盖了一个监控盲区：任务本身执行成功了，但 WhatsApp 推送失败了——状态文件显示正常，用户却没收到消息。

### 3. Proxy 实时统计
Tool Proxy 内置 token 用量计数和连续错误追踪。连续 5 次请求失败会触发告警。这能在远程 GPU 出问题时第一时间发现。

### 4. 三层 /health 端点
`Gateway(:18789) → Proxy(:5002) → Adapter(:5001)` 级联健康检查，一个 curl 就能看到全链路状态。

---

## 第四层：DevOps——推代码就部署

这是我最满意的部分。整个开发部署流程完全自动化：

```
Claude Code (开发) → claude/ 分支 → GitHub PR → main → auto_deploy → Mac Mini
```

### auto_deploy.sh：每 2 分钟轮询

这个 246 行的 bash 脚本是整个 DevOps 层的核心：

1. **git fetch + pull**：检测 main 分支是否有新 commit
2. **条件测试**：如果 `proxy_filters.py` 变更了，先跑 43 个单测，失败则中止部署
3. **文件同步**：17 个文件的映射关系（仓库路径 → 运行时路径），只同步本次变更的文件
4. **按需重启**：只有核心服务文件（proxy、adapter）变更时才重启
5. **部署后体检**：自动运行 9 项全面检查

### 漂移检测：每小时 md5 全量比对

增量同步有一个盲区：如果有人直接在 Mac Mini 上改了运行时文件（比如紧急修 bug），仓库和运行时就会"漂移"。

每小时整点，`auto_deploy.sh` 对 17 个文件做 md5 全量比对。发现不一致就自动覆盖并推送 WhatsApp 告警。

还会检查 crontab 的引号完整性——我曾经因为 `bash -lc '...'` 少了一个引号，导致 cron 任务静默失败了一整天。

### preflight_check.sh：9 项自动体检

每次部署后自动触发，检查：

| # | 检查项 | 内容 |
|---|--------|------|
| 1 | 单元测试 | proxy_filters + registry 校验器 |
| 2 | 注册表校验 | ID 唯一、路径存在、字段完整 |
| 3 | 文档漂移 | registry 与文档的一致性 |
| 4 | 脚本语法 | 所有 .sh 文件的 `bash -n` 检查 |
| 5 | Python 语法 | 所有 .py 文件的 `ast.parse` 检查 |
| 6 | 部署一致性 | 仓库 vs 运行时 md5 比对 |
| 7 | 环境变量 | 用 `bash -lc` 模拟 cron 环境验证 |
| 8 | 服务连通性 | 5001/5002/18789 三个端口 |
| 9 | 安全扫描 | API Key + 真实手机号泄漏检测 |

任何一项失败，WhatsApp 立即收到告警。

### 自更新的 Bootstrapping

`auto_deploy.sh` 的文件映射表里包含它自己：

```bash
"auto_deploy.sh|$HOME/openclaw-model-bridge/auto_deploy.sh"
```

这意味着部署脚本本身也能通过 GitHub push 来更新。第一次改动会被旧版本同步过去，第二次执行时就是新版本了。

---

## 踩过的坑（精选）

### 1. 进程管理双主控
同时用 launchd 和 cron 守护同一个进程 → 两个实例抢端口。**教训：单一主控原则。**

### 2. 零宽字符不零宽
U+200B 在 WhatsApp 中仍然显示为空消息气泡。**教训：所有方案必须实际验证，不能假设。**

### 3. cron 环境没有 PATH
cron 执行环境几乎是空的，`/opt/homebrew/bin` 不在 PATH 里。所有 cron 脚本第一行必须 `export PATH=...`。

### 4. BSD sed 不支持 `\|`
macOS 的 sed 是 BSD 版本，正则语法和 GNU sed 不同。复杂文本处理统一用 Python。

### 5. `--thinking none` 不等于 `--thinking off`
Qwen3 的 thinking 参数，`none` 是非法值会导致 500 错误，`off` 才是正确的。线上排查了半天。

### 6. 模型 ID 有前缀规则
在 adapter.py 里用裸 ID `Qwen3-235B-...`，在 openclaw.json 里必须带 `qwen-local/` 前缀。搞反了就是 404。

---

## 为什么用 Mac Mini 而不是云服务器？

- **成本**：Mac Mini 一次性购买，没有月费。远程 GPU 按量付费。
- **延迟**：本地服务之间通信是 localhost，延迟为零。
- **可控性**：所有数据在本地，知识库文件直接在硬盘上。
- **简单**：不需要 Docker、K8s、Terraform。launchd + crontab 就够了。

当然也有代价：需要稳定的网络（连接远程 GPU 和 WhatsApp），需要不断电。但对于个人使用，这些不是问题。

---

## 代码量

整个系统的核心代码量：

| 组件 | 语言 | 行数 |
|------|------|------|
| adapter.py | Python | ~100 |
| tool_proxy.py | Python | ~150 |
| proxy_filters.py | Python | ~300 |
| auto_deploy.sh | Bash | ~250 |
| preflight_check.sh | Bash | ~310 |
| job_watchdog.sh | Bash | ~200 |
| 8 个定时任务脚本 | Bash | ~800 |
| 2 个测试文件 | Python | ~500 |
| **合计** | | **~2600 行** |

2600 行代码跑起一个完整的 AI 助手 + 信息流 + 自动部署 + 多级监控体系。没有框架，没有依赖地狱，每一行都知道它在干什么。

---

## 总结

这个项目的核心理念：

1. **做减法不做加法**——每加一层"保险"就是多一个故障源。在加监控之前先问"谁已经在管这件事"。
2. **纯函数可测试**——把策略逻辑和 IO 分离，43 个测试用例跑在毫秒级。
3. **漂移必须被检测**——仓库和运行时、注册表和文档、crontab 和配置，任何两个"应该一致"的东西都要有自动检测。
4. **告警推送到手机**——日志文件没人看。告警必须推到你每天都在用的渠道。

Mac Mini 不是玩具。给它一个好的架构，它就是你的私人 AI 基础设施。

---

*项目开源在 GitHub：[openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge)*

*如果你也在用 OpenClaw 或类似框架接入自己的模型，欢迎交流。*

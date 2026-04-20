# 我用一台 Mac Mini 搭了一个 7 Provider 通用 Agent Runtime，任何人 10 分钟跑通

> 从"能用"到"别人也能用"——一个 Agent 中间件的可用性工程实践

---

## TL;DR

我们开源了一个 **Agent Runtime Control Plane**：用一台 Mac Mini、零第三方依赖（纯 Python 标准库），把任意大模型（Qwen / OpenAI / Gemini / Claude / Kimi / MiniMax / GLM）接入 WhatsApp，支持多模态、工具调用、自动降级。

关键数据：**7 个 Provider、605 个单测、5 项 SLO 全部 PASS、一键 10 分钟跑通**。

本文重点讲的不是"我做了什么功能"，而是 **"别人怎么验证你的系统是可行的"**——这是从工程项目到开源产品的最大鸿沟。

---

## 1. 问题：Agent 系统的"最后一公里"

做过 LLM Agent 的人都知道，让系统"自己能跑"不难，难的是：

1. **换个模型就挂了** —— 参数格式不同、认证方式不同、能力边界不同
2. **别人跑不起来** —— 依赖 20 个包、需要 3 个配置文件、文档和代码不同步
3. **出了问题不知道哪层坏了** —— Gateway、代理、适配器、远端 GPU，四层排查
4. **没有证据证明系统可靠** —— 单测通过≠生产可用，缺少延迟、成功率、降级的硬数据

我们用 6 个月解决了这四个问题。下面逐一展开。

---

## 2. 架构：三层分离，各司其职

```
用户 (WhatsApp)
    │
    ▼
Gateway (:18789)          ← 消息接入、媒体存储、会话管理
    │
    ▼
Tool Proxy (:5002)        ← 策略过滤、工具注入、图片 base64、SLO 采集
    │
    ▼
Adapter (:5001)           ← Provider 路由、认证、多模态分发、Fallback 降级
    │
    ▼
Remote LLM                ← Qwen3-235B / GPT-4o / Gemini / Claude / Kimi / MiniMax / GLM
```

**设计哲学：每一层只做一件事，且可以被独立替换。**

- **Tool Proxy** 是策略层：决定哪些工具暴露给模型（硬限制 ≤12 个）、Schema 如何简化、请求如何截断。它不关心下游是什么模型。
- **Adapter** 是能力层：处理认证差异（Bearer vs x-api-key）、模型路由（文本→Qwen3，图片→VL-72B）、Fallback 降级（Qwen 挂了→Gemini 接管）。它不关心上游是什么应用。
- 两层之间通过标准的 **OpenAI-compatible API** 通信，任何一层都可以单独测试。

---

## 3. Provider Compatibility Layer：7 个模型，一个接口

这是本次架构升级的核心。我们把硬编码的 Provider 字典升级为 **可插拔的 Provider 抽象层**：

```python
class BaseProvider:
    name: str                    # "qwen" | "openai" | "kimi" | ...
    base_url: str                # API 端点
    api_key_env: str             # 环境变量名
    auth_style: str              # "bearer" | "x-api-key"
    models: List[ModelInfo]      # 模型列表（含上下文窗口、模态）
    capabilities: ProviderCapabilities  # 能力声明
```

每个 Provider **显式声明自己的能力**，而不是运行时试探：

```python
capabilities = ProviderCapabilities(
    text=True,
    vision=True,         # 是否支持图片理解
    tool_calling=True,   # 是否支持工具调用
    streaming=True,      # 是否支持流式输出
    json_mode=True,      # 是否支持 JSON 模式
    context_window=131072,
    # 验证状态（实际测试过的才标 True）
    verified_text=True,
    verified_fallback=True,
)
```

### 当前支持的 7 个 Provider

| Provider | 默认模型 | 上下文 | 特色 |
|----------|---------|--------|------|
| **Qwen (Remote GPU)** | Qwen3-235B | 262K | 主力，含 VL-72B 视觉模型 |
| **OpenAI** | GPT-4o | 128K | 多模态（文本+视觉+音频） |
| **Google Gemini** | Gemini 2.5 Flash | 1M | 百万级上下文，已验证 Fallback |
| **Anthropic Claude** | Claude Sonnet 4.6 | 200K | x-api-key 认证 |
| **Kimi (Moonshot AI)** | Kimi K2.5 (1T MoE, 32B active) | 256K | 视觉理解 + 32K 输出 |
| **MiniMax** | MiniMax M2.7 | 200K | 视觉理解 + 131K 超长输出 |
| **GLM (Zhipu AI)** | GLM-5 (744B MoE) + GLM-5V-Turbo | 200K | 视觉模型 + 128K 输出 |

**添加新 Provider 只需 3 步**：

```python
# 1. 继承 BaseProvider，声明能力
class MyProvider(BaseProvider):
    name = "my_provider"
    base_url = "https://api.example.com/v1"
    api_key_env = "MY_API_KEY"
    models = [ModelInfo(model_id="my-model", is_default=True)]
    capabilities = ProviderCapabilities(text=True, tool_calling=True)

# 2. 注册
_default_registry.register(MyProvider())

# 3. 使用
export PROVIDER=my_provider && export MY_API_KEY=... && bash restart.sh
```

**向后兼容**：`providers.py` 导出标准的 `PROVIDERS` dict，`adapter.py` 零改动即可切换。已有的 605 个单测确保任何变更不会破坏现有功能。

---

## 4. 一键跑通：Quick Start 的设计哲学

> "如果别人不能在 10 分钟内跑通你的系统，那你的系统就不存在。"

这句话驱动了我们的 Quick Start 设计。

### 4.1 零依赖

核心服务**只用 Python 标准库**，不需要 `pip install` 任何东西。`http.server`、`json`、`urllib` 就够了。

这不是偷懒，是刻意的架构决策：

- 部署目标是 Mac Mini，不想维护虚拟环境
- 第三方依赖是最大的"跑不起来"原因
- 标准库够用的场景，不引入 FastAPI/aiohttp

### 4.2 自动检测 Provider

```bash
# 你有什么 API key，就用什么模型。零配置。
export OPENAI_API_KEY='sk-...'    # 有 OpenAI key？用 GPT-4o
export GEMINI_API_KEY='...'       # 有 Gemini key？用 Gemini
export MOONSHOT_API_KEY='...'     # 有 Kimi key？用 Kimi K2.5
export GLM_API_KEY='...'          # 有 GLM key？用 GLM-4
# 或者显式指定：
export PROVIDER=minimax

bash quickstart.sh   # 自动检测，4 阶段跑通
```

Quick Start 的 4 个阶段：

```
Phase 1: Prerequisites Check
  ✅ Python 3 installed (Python 3.11.5)
  ✅ Core services: zero third-party dependencies
  ✅ Provider: openai (via $OPENAI_API_KEY)
  ✅ config.yaml found
  ✅ All 5 core files exist
  ✅ All 3 core files syntax OK

Phase 2: Start Services
  ✅ Adapter (:5001) healthy
  ✅ Tool Proxy (:5002) healthy

Phase 3: Health Verification
  ✅ Proxy /health responds (cascade check)
  ✅ Adapter /v1/models responds
  ✅ Unit tests passed (605 tests)
  ✅ Provider registry: 7 providers registered

Phase 4: Golden Test Trace
  POST http://localhost:5002/v1/chat/completions
  Content: Four
  Latency: 521ms
  ✅ Golden test trace completed
  Trace saved to docs/golden_trace.json
```

### 4.3 Golden Test Trace：可复现的证据

Quick Start 不只是"能跑"，它会生成一个 **Golden Test Trace**——一个真实请求穿越全栈的完整记录：

```json
{
  "timestamp": "2026-04-05 10:03:58",
  "latency_ms": 521,
  "request": {
    "model": "qwen-local/auto",
    "prompt": "What is 2+2? Reply in one word.",
    "max_tokens": 50
  },
  "response": {
    "content": "Four",
    "tokens": {"prompt_tokens": 37, "completion_tokens": 2, "total_tokens": 39},
    "model": "Qwen3-235B-A22B-Instruct-2507-W8A8"
  },
  "path": "Proxy(:5002) → Adapter(:5001) → Remote GPU → Response"
}
```

这个 trace 文件入库，任何人都可以复现和对比。**证据，不是承诺。**

---

## 5. SLO：不是"感觉快"，是"量化快"

我们定义了 5 项 SLO 指标，用真实生产数据验证：

| 指标 | 目标 | 实测 | 状态 |
|------|------|------|------|
| 延迟 p95 | ≤ 30s | 459ms | **PASS** |
| 工具调用成功率 | ≥ 95% | 100% | **PASS** |
| 降级率 | ≤ 5% | 0% | **PASS** |
| 超时率 | ≤ 3% | 0% | **PASS** |
| 自动恢复率 | ≥ 90% | 100% | **PASS** |

这些数据来自 `proxy_stats.json`，由 Tool Proxy 实时采集，`slo_benchmark.py` 自动生成报告。不是手动填的表格，是**可重复生成的实验结果**。

```bash
python3 slo_benchmark.py          # Markdown 报告
python3 slo_benchmark.py --json   # JSON 格式（供 CI 消费）
python3 slo_benchmark.py --save   # 保存到 docs/
```

---

## 6. Fallback 降级：不是"能恢复"，是"自动恢复"

```
Qwen3-235B (Primary, 5min timeout)
    ↓ 失败 / 超时 / 电路断路
Gemini 2.5 Flash (Fallback, 1min timeout)
    ↓ 也失败
502 Error (两个错误信息一起返回)
```

**电路断路器**：连续 5 次失败后自动短路 Primary，直接走 Fallback。300 秒后半开尝试恢复。

这不是理论设计，我们有 **GameDay 故障演练脚本**，可以模拟 5 种故障场景：

```bash
bash gameday.sh --all   # GPU 超时 / 断路器 / 快照 / SLO / Watchdog
```

---

## 7. 工具治理：为什么限制工具数量

很多 Agent 系统恨不得给模型塞 50 个工具。我们的经验是：**工具越多，模型越混乱**。

硬性限制：
- **工具数量 ≤ 12**（超出导致 Qwen3 参数幻觉）
- **每任务工具调用 ≤ 2 次**（超出超时风险指数级上升）
- **请求体 ≤ 200KB**（硬限制 280KB，留 buffer）

Tool Proxy 的 `proxy_filters.py` 做了几件关键的事：

1. **工具过滤**：Gateway 注入 24 个工具，Proxy 只放行 12 个
2. **Schema 简化**：去掉冗余的 `description`、`enum`，减少 token 消耗
3. **参数修复**：模型生成了错误的参数名？自动别名映射修复
4. **自定义工具注入**：`data_clean`（数据清洗）和 `search_kb`（知识检索）在 Proxy 层拦截执行，不需要 Gateway 支持

这些都是**纯函数，无网络依赖**，有 67 个单测覆盖。

---

## 8. 对其他开发者：如何验证这个系统

### 最快路径（10 分钟）

```bash
git clone https://github.com/bisdom-cell/openclaw-model-bridge.git
cd openclaw-model-bridge

# 设置任意一个 API key
export OPENAI_API_KEY='sk-...'    # 或 GEMINI_API_KEY / MOONSHOT_API_KEY / GLM_API_KEY / ...

# 一键跑通
bash quickstart.sh
```

你会看到：
- 前置检查（Python 版本、文件完整性、Provider 自动检测）
- 服务启动（两个 Python 进程，无需 Docker）
- 健康验证（605 个单测 + Provider 注册表）
- Golden Test Trace（一个真实请求穿越全栈）

### 深度验证路径

```bash
# 1. 全量回归测试（605 个用例）
python3 -m unittest discover -p "test_*.py" -q

# 2. SLO Benchmark（真实生产数据报告）
python3 slo_benchmark.py

# 3. Provider 兼容性矩阵
python3 providers.py

# 4. 故障演练
bash gameday.sh --all

# 5. 安全评分（7 维度 100 分）
python3 security_score.py
```

### 接入你自己的模型

```python
# providers.py 中添加
class MyProvider(BaseProvider):
    name = "my_llm"
    base_url = "https://my-api.com/v1"
    api_key_env = "MY_API_KEY"
    models = [ModelInfo(model_id="my-model-v1", is_default=True)]
    capabilities = ProviderCapabilities(text=True, tool_calling=True, streaming=True)

_default_registry.register(MyProvider())
```

然后 `export PROVIDER=my_llm && bash restart.sh`，完成。

---

## 9. 数字说话

| 维度 | 数据 |
|------|------|
| Provider 数量 | 7（Qwen/OpenAI/Gemini/Claude/Kimi/MiniMax/GLM） |
| 单测数量 | 605（9 个测试套件） |
| SLO 达标 | 5/5 PASS |
| 延迟 p95 | 459ms |
| 核心依赖 | 0（纯 Python 标准库） |
| Quick Start 时间 | 10 分钟 |
| Golden Test Trace | 521ms, 39 tokens |
| Cron Jobs | 28 个 active（论文监控/KB/备份/监控） |
| 安全维度 | 7 维度评分（密钥/测试/完整性/部署/传输/审计/可用性） |
| 故障演练场景 | 5（GPU 超时/断路器/快照/SLO/Watchdog） |

---

## 10. 方法论：从"能用"到"别人能用"的三个转变

### 转变一：证据优先于功能

> 系统已有但证据密度不足——这是我们导师评审中收到的最尖锐反馈。

我们的应对：每个 milestone 必须产出**可复现的证据**，而不只是代码。SLO Benchmark 报告、Golden Test Trace、兼容性矩阵——这些都不是文档，是**实验结果**。

### 转变二：可用性是架构决策

零依赖不是因为懒，是因为 `pip install` 是别人跑不起来的第一道坎。自动检测 Provider 不是锦上添花，是因为"先读 README 搞清楚该设哪个环境变量"是第二道坎。

**每减少一步手动操作，就多一个能跑通的用户。**

### 转变三：控制平面先于能力平面

Agent 系统最大的诱惑是不断加功能。我们的经验是：**控制（治理、限流、降级、观测、审计）必须先于能力（模型路由、工具编排、多模态）**。否则能力越强，系统越难控。

这就是为什么 Tool Proxy 要限制工具数量、为什么要有电路断路器、为什么 SLO 是一等公民。

---

## 写在最后

这个项目从 V27 到 V35，经历了 crontab 事故（清空全部定时任务）、Gateway 进程管理双主控冲突、393 个单测全过但 PA 说"没有项目"的尴尬……每一个教训都沉淀成了一条工程规则。

我们相信 Agent 系统的未来不在于模型多聪明，而在于 **Runtime 有多可靠**。如果你也在做类似的事，欢迎来跑一下我们的 Quick Start，提 Issue，或者直接加一个新 Provider。

```bash
git clone https://github.com/bisdom-cell/openclaw-model-bridge.git
cd openclaw-model-bridge
export OPENAI_API_KEY='sk-...'
bash quickstart.sh
```

十分钟后见。

---

**项目地址**：[github.com/bisdom-cell/openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge)

**标签**：#LLM #Agent #中间件 #开源 #Qwen #OpenAI #Kimi #GLM #MiniMax #AgentRuntime

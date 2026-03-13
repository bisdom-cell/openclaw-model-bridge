# openclaw-model-bridge 总体架构梳理

> 面向仓库代码与运维脚本的架构级解读（以当前 main 分支为准）。

## 1. 项目定位

`openclaw-model-bridge` 是一个 **OpenClaw Gateway 与上游大模型之间的双层中间件**，目标是把 OpenClaw 的多工具、多通道能力稳定接入第三方 LLM（默认 Qwen3-235B，也支持 OpenAI/Gemini/Claude）。

核心价值：
- 在 Gateway 与模型 API 之间增加“协议适配 + 策略治理”能力。
- 把复杂的生产约束（工具白名单、请求截断、SSE 兼容、告警监控）沉淀到可测试代码层。
- 通过 system cron + openclaw cron 的双调度体系，支撑自动化任务与运维闭环。

## 2. 架构分层（4 层）

### 2.1 实时对话链路层

主链路如下：

```text
WhatsApp 用户
  ↕
OpenClaw Gateway :18789
  ↕
Tool Proxy :5002   (HTTP 路由 + 策略执行 + 监控)
  ↕
Adapter :5001      (Provider 适配 + 鉴权 + 参数清洗)
  ↕
Remote LLM API     (Qwen/OpenAI/Gemini/Claude)
```

其中，Tool Proxy 与 Adapter 分别承担“策略层”和“协议层”职责，避免单体代理膨胀。

### 2.2 任务自动化层（定时任务）

通过 `jobs_registry.yaml` 统一登记定时任务，区分两类：
- `system`：由系统 crontab 执行，适合确定性脚本任务。
- `openclaw`：由 OpenClaw cron 执行，适合需要 LLM 会话语义的任务。

任务覆盖论文监控、社区动态、运维巡检、自动部署、消息保活等。

### 2.3 监控告警层

监控来自三条线：
- Proxy 运行指标（token 使用率、错误计数）与 `/stats` 端点。
- 服务健康探针（Gateway/Proxy/Adapter 的 `/health` 级联）。
- Job watchdog + keepalive 的主动探测与 WhatsApp 告警。

### 2.4 DevOps 与发布层

`auto_deploy.sh` 承担持续部署入口：轮询代码、同步文件、条件重启、漂移检测。配合 `restart.sh`、`health_check.sh`、`ROLLBACK.md` 构成可回滚运维闭环。

## 3. 核心运行组件

## 3.1 Tool Proxy（`tool_proxy.py`）

职责边界：
- 作为外部统一入口，接收来自 Gateway 的 OpenAI-compatible 请求。
- 对 `/chat/completions` 做预处理：消息截断、工具过滤、无工具模式识别。
- 将流式请求下沉为非流式调用，再按 SSE 规范回转，保证上游兼容。
- 统计 usage/error，触发阈值告警并异步推送 WhatsApp。

这使它成为“网关与模型之间的**流量整形器 + 可靠性守门员**”。

## 3.2 Policy 纯函数层（`proxy_filters.py`）

该模块将策略逻辑从网络 I/O 中剥离，形成可单测的纯函数集合：
- 工具白名单与前缀放行。
- Tool schema 清洗，减少模型参数幻觉。
- 请求体大小控制（旧消息截断）。
- tool arguments 自修复（参数别名映射、多余字段剥离、browser profile 注入）。
- 标准响应转 SSE chunk。
- token/error 统计与阈值策略。

这是“可维护性与鲁棒性”的关键设计：**HTTP 层最薄、策略层最纯**。

## 3.3 Adapter（`adapter.py`）

Adapter 负责“向外兼容多 Provider，向内稳定单协议”：
- 通过 `PROVIDERS` 注册表声明 base_url、鉴权方式、默认模型。
- 启动时基于 `PROVIDER` 环境变量选择目标 Provider。
- 清洗消息结构（尤其是多模态 content），保留 `tool_calls` 语义。
- 统一转发到目标 `/chat/completions`，并返回标准 JSON。
- 暴露本地 `/health`，避免健康检查依赖远程 API。

因此，切换模型供应商通常不需要改 Tool Proxy 逻辑。

## 4. 数据与控制流

### 4.1 请求数据流

1. 用户消息从 WhatsApp 进入 OpenClaw Gateway。
2. Gateway 将请求发送到 Tool Proxy。
3. Tool Proxy 在本地执行策略过滤和请求瘦身。
4. Adapter 进行 Provider 协议适配和鉴权注入。
5. 远端模型返回结果，经 Adapter 回传给 Proxy。
6. Proxy 对工具调用参数做修正（必要时），再回给 Gateway。
7. Gateway 执行工具并把最终答复发回用户。

### 4.2 运维控制流

1. 任务定义在 `jobs_registry.yaml` 统一登记。
2. `check_registry.py` 在变更后校验任务完整性。
3. crontab/openclaw cron 根据调度器字段执行任务。
4. 任务日志与 watchdog 汇总异常并告警到 WhatsApp。
5. auto_deploy 周期性拉取更新并部署到运行环境。

## 5. 关键设计原则（架构约束）

- 双层代理分治：Proxy 管策略，Adapter 管协议。
- 工具能力做减法：限制工具数量与调用复杂度，优先稳定性。
- 健康检查本地化：`/health` 尽量不依赖外部网络。
- 任务注册中心化：所有 cron 任务必须进注册表再校验。
- 单一主控原则：避免同一组件被多 watchdog 重复接管。

## 6. 目录视图（按架构职责）

- 对话中间件：`tool_proxy.py`、`proxy_filters.py`、`adapter.py`
- 自动化任务：`jobs/**`、`run_hn_fixed.sh`、`kb_*.sh`、`wa_keepalive.sh`
- 任务治理：`jobs_registry.yaml`、`check_registry.py`
- 运维脚本：`auto_deploy.sh`、`restart.sh`、`health_check.sh`、`diagnose.sh`
- 文档基线：`README.md`、`docs/openclaw_architecture.md`、`docs/config.md`

## 7. 一句话总结

这是一个以 **“策略前置 + 协议解耦 + 任务编排 + 运维闭环”** 为核心思想的 LLM 中间件项目：
- 既保障 OpenClaw 侧的工具/会话能力不失真，
- 又把第三方模型在生产环境中的不确定性收敛到可控边界内。


## 8. 下一步优化建议

详见：`docs/optimization_roadmap.md`（按优先级分组，逐条给出原因、风险、对策）。

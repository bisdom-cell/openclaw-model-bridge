# Design: doubao_21 作 primary 的 workload 路由（方案 A2 单点规则）

> V37.9.221（2026-07-02）。回应 2026-07-02 reasoning-primary 拖垮批量 job 事故
> （`ontology/docs/cases/reasoning_model_primary_breaks_batch_jobs_case.md`）——doubao_21 迁移真门槛。

---

## 1. 问题

doubao_21（reasoning 模型）作 primary 时：
- **PA 交互**（短对话，有 tools）：doubao_21 reasoning 质量好，延迟可容忍 ✅
- **批量 cron job**（分析 repo/论文/新闻，长 prompt + 大结构化输出，纯推理无 tools）：reasoning 延迟 7-9 分钟 >> job HTTP client 超时 → broken pipe → adapter 级联全 fallback 失败 → 502 ❌

**核心矛盾**：job 和 PA 共用同一个全局 `PROVIDER` env，无法区分 workload。且 fallback 是「故障」的保险不是「延迟」的保险（qwen 在链里但排第 4，被前面 500s 累积延迟埋葬，client 早断，详见 case doc Q2）。

## 2. 决策：单点 adapter workload 规则（A2）

grounding 发现 LLM 调用点是 **24 个异构站点**（19 shell curl 硬编码 + 5 变量 + 2 端口 + Python kb_*）。逐站点显式加 `?provider=qwen`（A1）= 脆弱、高 churn、违一物一形、新消费方必须记得加。

**A2 = 在 adapter 一处按 workload 信号路由**：

| 信号 | 含义 | 路由 |
|------|------|------|
| **无 tools** | 纯推理 = 批量 workload（规则 #27：cron job 一律纯推理直连） | → **fast provider**（`FAST_PROVIDER`，如 qwen） |
| **有 tools** | PA（OpenClaw tool-calling agent 每轮带 tool schema） | → **primary**（doubao_21，reasoning 质量） |

信号可靠性（对本系统）：PA 是 tool-calling agent，每轮请求带 tools；所有批量消费方（jobs + kb_*）是纯推理无 tools。**一处规则自动覆盖全部 24 个消费方**，新批量 job 无需任何改动即正确路由（一物一形）。

## 3. 实现

**`adapter.py::_classify_fast_route(clean, clean_msgs, has_multimodal, use_model, primary_name)`**（纯函数，可单测）返回 `"workload" | "smart" | None`：
- `"workload"`：无 tools → fast，**不依赖 classify_complexity**（批量 prompt 内容 complex 但延迟敏感，应走快路 —— 这正是修复点：内容复杂度 ≠ 该用 reasoning 模型）
- `"smart"`：有 tools + simple → fast（V37.9.76 既有行为，向后兼容）
- `None`：留 primary（PA 复杂 tool-call / 多模态 / `?provider=` override / 非默认 model）

复用现有 `FAST_ROUTE`（`FAST_PROVIDER` env → 快 provider 配置）+ do_POST 的 fast-route 转发路径。**零新机制**（sunset law：复用不新增）。

## 4. no-op-until-flip（安全属性）

`FAST_ROUTE` 仅在 `FAST_PROVIDER != PROVIDER`（adapter.py:261）时非空。故：
- **当前 PROVIDER=qwen + FAST_PROVIDER=qwen → FAST_ROUTE=None → 路由完全 no-op**（helper 首个 guard 返回 None）
- 只有 flip `PROVIDER=doubao_21`（FAST_PROVIDER 仍 qwen，二者不等）→ FAST_ROUTE 激活 → 批量 no-tools → qwen，PA 有 tools → doubao_21

代码合并部署后**不改变任何当前行为**，是 flip 的前置基础设施。（注：A2 用 FAST_ROUTE，**不需要** `ROUTER_ENFORCE`——那是 `?provider=` override 的开关。）

## 5. Mac Mini 部署 + 验证 + flip runbook

**阶段 0：部署代码（no-op）**
git fetch+reset 同步 → auto_deploy 把 adapter.py rsync 到运行时 → adapter 重启（bootout/bootstrap）。此时 PROVIDER=qwen、FAST_PROVIDER 未设 → 零行为变化。

**阶段 1：机制验证（PROVIDER 仍 qwen，临时反向探针）**
临时设 `FAST_PROVIDER=doubao_21`（与 PROVIDER=qwen 不等 → FAST_ROUTE 激活），发两个 curl 探针即刻验证 + 立即 unset：
- 无 tools 请求 → adapter.log 应现 `WORKLOAD ROUTE: pure-inference -> doubao_21` + 响应来自 doubao_21
- 有 tools 请求 → 无 WORKLOAD ROUTE 日志，留 qwen
（探针用单次手动 curl，非跑整个 job，避免批量流量误走慢 provider。验证后 unset FAST_PROVIDER 恢复 no-op。）

**阶段 2：正式 flip**
设 `FAST_PROVIDER=qwen` + `PROVIDER=doubao_21`（`.env_shared` + adapter plist EnvironmentVariables）→ bootout/bootstrap 重载。
- `/health` 应显示 `provider: doubao_21`
- 重跑 `bash ~/.openclaw/jobs/github_trending/run_github_trending.sh` → 应 10/10 快速成功（WORKLOAD ROUTE → qwen）
- WhatsApp 发消息 → PA 走 doubao_21（reasoning 质量）
- 观察几个 cron 周期（freight 14:00 / hf 10:00 / arxiv 每 3h）全部快速成功

**回滚（Plan B，随时可用）**：`PROVIDER=qwen` + bootout/bootstrap → 恢复今天的稳定态。

## 6. 边界与取舍（诚实登记）

- **PA 的 [NO_TOOLS] 消息**：用户显式标记纯推理的 PA 消息 → 无 tools → 路由到 fast provider。罕见，且用户既然要纯推理，走快 provider 可接受。
- **fast provider 失败路径**：~~批量 job fallback 到 reasoning 链仍可能慢/502~~ → **V37.9.224 B1 fallback 传播已修**：批量请求 fallback 时按各 fallback provider **自己的** `reasoning_off_body` 重算注入——reasoning provider（doubao_21/deepseek_full/doubao/deepseek）拿 thinking-off 走快路（~15-18s 非 7-9min），无声明 provider（qwen vLLM）剥掉未测参数。V37.9.220 不再在 fallback 路径重演。
- **多模态批量**：图片请求（has_multimodal）不走 fast（快 provider 可能无视觉）→ 留 primary。doubao_21 单模型多模态，图片 OK。

## 7. 守卫

`test_adapter.py::TestWorkloadRouting`（10 单测，行为级 monkeypatch adapter globals）：no-tools→workload / 不依赖 classifier / tools+simple→smart / tools+complex→primary / 多模态→primary / 非默认 model→primary / ?provider= override→primary / FAST_ROUTE 未配置→None / 空 tools→workload / 源码守卫 do_POST 用 helper。sabotage 反向验证：破坏 no-tools 分支 → 3 测试 FAIL。

## 8. 状态

A2 代码 V37.9.221 实现 → 2026-07-02 A2 flip（doubao_21 primary + FAST_PROVIDER=qwen）E2E 通过 → **同日 B1 flip 上线（V37.9.222：撤 FAST_PROVIDER，doubao_21 单模型通吃——批量 thinking-off / PA reasoning，且实测批量比 qwen 分流更快）**。V37.9.223 全四家 doubao/deepseek 声明 `reasoning_off_body`（任一家可独立成 primary）。**V37.9.224 fallback 传播补全**（批量 fallback 按 fb 自己的声明重算注入，见 §6/§10）。当前态：PROVIDER=doubao_21 无 FAST_PROVIDER，qwen 仅 fallback 兜底，观察 N 天 → production_observed。

## 9. 终局：reasoning-only 未来（qwen 完全退役后）— B1 已实测确认

**问题**：A2（批量→`FAST_PROVIDER`）依赖有一个快速非-reasoning provider（qwen）。qwen 完全退役后剩 doubao/deepseek 全是 reasoning 模型 → A2 的快 provider 无处可指 → 批量 job 又回慢。

**洞察**：reasoning 是 per-request **模式**，不是模型固有属性。批量需要的是「快速非-reasoning 推理路径」，可以是 (a) 另一个非-reasoning 模型（B2/qwen），或 **(b) 同一 reasoning 模型把 reasoning 关掉（B1）**。

**B1 实测确认（2026-07-02，Mac Mini 直连 doubao-seed-2-1-pro Ark 端点，同 prompt 对照）**：

| 请求 | reasoning_tokens | 延迟 | 结论 |
|------|------------------|------|------|
| `thinking:{"type":"disabled"}` | **0** | **17.7s**（500 tok 满） | reasoning 完全关 + 内容质量正常 |
| 默认（reasoning on） | **5138** | **166s（2:46）** | 约 10× 慢 |

→ **`thinking:{"type":"disabled"}` 是 doubao-seed-2-1-pro 的合法 Ark 参数，B1 可行**：doubao_21 一个模型能同时服务 PA（thinking on，reasoning 质量）+ 批量（thinking off，17s 级）。**qwen 退役后不需要任何替代快 provider。**

**B1 flip 生产上线（2026-07-02，PROVIDER=doubao_21 + 撤 FAST_PROVIDER）+ 意外惊喜**：Mac Mini flip 后 github_trending 10/10 成功，且**批量在 doubao_21 thinking-off 上比 A2（批量→qwen 分流）时更快**——火山 Ark 托管云高并发 > 自建 qwen W8A8 端点（有 queue_wait_time）。**结论升级：qwen 退役从「可行」（doubao_21 能接批量）变为「有收益」（doubao_21 接批量更快）**。B1 是终局主路径（去 qwen 依赖 + 性能更优），A2（FAST_PROVIDER 分流）降为可选/回滚路径。

**B1 双 provider 实测确认（2026-07-02，两家都支持 `thinking:{"type":"disabled"}`）**：

| provider | 端点 | thinking:disabled 结果 |
|----------|------|------------------------|
| doubao_21 | Ark 原生 | reasoning_tokens 0 / 17.7s（vs 默认 5138 / 166s） ✅ |
| deepseek_full | ai-tokenhub（**Bifrost 网关归一化** `thinking` 参数） | `completion_tokens_details:{}` 空 + 无 reasoning 通道 + content 完整 / 15s ✅ |

deepseek_full 探针旁证：`enable_thinking:false` **被忽略**（reasoning 照跑）；`reasoning_effort` 有效但值域 `[high/low/max/medium/minimal]`（可作 B3 压缩，非全关）；唯 `thinking:{"type":"disabled"}` 全关。→ **`thinking:{"type":"disabled"}` 是跨 provider 可靠的 reasoning-off 参数**（Ark 原生 + Bifrost 网关归一）。

**终局架构（三层，按可用性降级）**：
```
批量/纯推理 workload → 快速非-reasoning 路径:
  ① B1 (最优, 双 provider 已实测): 服务 provider 支持 thinking-off → 注入 thinking:{type:disabled}
     (doubao_21 ✅ / deepseek_full ✅ — 两家都能单模型通吃, 无单点依赖)
  ② B2 (过渡): 有独立快 provider (qwen) → 路由过去 (A2 已建, V37.9.221)
  ③ 都没有 → 留 primary (慢兜底)
交互 (有 tools) → reasoning 模型 (质量)
```

**核心原则（永久）**：不管未来剩哪几家 provider，系统必须始终保有一条快速非-reasoning 推理路径给批量 job。实现（关 reasoning vs 换型号）随各家 API 能力，但需求永久。A2 的 workload 检测（`_classify_fast_route` no-tools）是承载它的正确接缝。

## 10. B1 落地方案（✅ V37.9.222 已实现 + flip 上线；V37.9.224 fallback 传播补全）

原「待 qwen 退役排期时实现」的 defer 决策被真实需求提前触发（V37.9.222：用户要求 doubao_21 单模型通吃批量，real consumer 驱动，原则 #18 兑现）。实现即下列方案：

1. **providers.py**：per-provider 声明 `reasoning_off_body`（doubao_21 = `{"thinking":{"type":"disabled"}}` ✅实测 / deepseek_full = `{"thinking":{"type":"disabled"}}` ✅实测（ai-tokenhub Bifrost 归一）/ qwen = None 本就非-reasoning）。默认 None。**两家主 reasoning provider 都实测支持同一参数**，无单点依赖。
2. **adapter**：批量请求（复用 V37.9.221 `_classify_fast_route` no-tools 检测）→ 服务 provider（primary 或 FAST_ROUTE provider）若有 `reasoning_off_body` → merge 进 clean 请求体。与 A2 互补：有 FAST_PROVIDER 先走 A2（qwen 无需注入），无 FAST_PROVIDER 时 B1 在 primary 注入 thinking-off。**V37.9.224 fallback 传播**：chain entry 携带 `reasoning_off_body` + `_fallback_batch_body()` 纯函数——批量 fallback 先剥 serving 注入的 keys 再注入 fb 自己的片段（reasoning fb 拿 thinking-off / qwen 剥掉未测参数），非批量原样（PA fallback 保留 reasoning 质量）。
3. **守卫**：行为级单测（批量+doubao_21→注入 thinking:disabled / 批量+qwen→不注入 / PA→不注入）+ sabotage。
4. **Mac Mini E2E**：flip 到 PROVIDER=doubao_21 + 无 FAST_PROVIDER → 批量 job 请求确认带 thinking:disabled + reasoning_tokens=0 + 快速成功。

**deepseek_full thinking-off 已实测确认（2026-07-02）**：`thinking:{"type":"disabled"}` 有效（ai-tokenhub 用 Bifrost 网关归一化该参数）；`enable_thinking:false` 被忽略；`reasoning_effort` 值域 `[high/low/max/medium/minimal]`（可作 B3 压缩）。→ doubao_21 与 deepseek_full **同一参数** `reasoning_off_body={"thinking":{"type":"disabled"}}`，落地时一份声明两家复用。

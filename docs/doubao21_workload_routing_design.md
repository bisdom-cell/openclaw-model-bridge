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
- **fast provider 失败路径**：批量 job 路由到 qwen，若 qwen 宕 → fallback 链 [doubao_21, deepseek_full, ...] 全是慢 reasoning → 该请求仍可能慢/502。但 qwen 极少宕，属失败路径边界，非常态。
- **多模态批量**：图片请求（has_multimodal）不走 fast（快 provider 可能无视觉）→ 留 primary。doubao_21 单模型多模态，图片 OK。

## 7. 守卫

`test_adapter.py::TestWorkloadRouting`（10 单测，行为级 monkeypatch adapter globals）：no-tools→workload / 不依赖 classifier / tools+simple→smart / tools+complex→primary / 多模态→primary / 非默认 model→primary / ?provider= override→primary / FAST_ROUTE 未配置→None / 空 tools→workload / 源码守卫 do_POST 用 helper。sabotage 反向验证：破坏 no-tools 分支 → 3 测试 FAIL。

## 8. 状态

代码已实现（V37.9.221，no-op 直到 flip）。剩余 = Mac Mini 阶段 0-2 部署验证 + flip（用户执行）。flip 成功后 doubao_21 正式作 primary（PA reasoning 质量 + 批量 job 走 qwen 快路），qwen 从 primary 降为 batch-fast-provider + fallback 链首。

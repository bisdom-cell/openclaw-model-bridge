# 案例：reasoning 模型作 primary 拖垮批量 cron job（2026-07-02）

> 类别：能力平面 provider 迁移事故 / MR-4 silent-failure 家族 / fail-plausible taxonomy 候选（Class C 吞噬稀释变种）
> 触发：primary 从 qwen（非 reasoning）切换到 doubao_21（reasoning 模型）后，批量分析型 cron job 全部 502

---

## 一、症状（用户视角）

2026-07-02 14:00–14:45，用户连续收到三条 `[SYSTEM_ALERT]`：
- 货代 Watcher LLM 调用失败（14:00）
- github_trending 全部 10 repo LLM 分析失败（14:00，`last reason` **为空**）
- hn_watcher 全部 3 条 LLM 分析失败（14:45，`last reason` **为空**）

三个 job 缓存文件 `llm_raw_last.txt` 全空 / 仅 `returncode=0 stdout 空`。而同期 **PA WhatsApp 对话正常**（前一天 doubao_21 primary 上线时 E2E「延迟非故障」通过）。

## 二、完整因果链

```
14:00  freight/github_trending cron 发批量分析请求（大 prompt: 10 repos / 货代数据 + 大 max_tokens 结构化输出）
       │
       → adapter PROVIDER=doubao_21（今日切换，reasoning 模型）
       │   证据: /health 200 正常 + 简单请求 "reply OK"(max_tokens=10) 7.3s 通
       │   但该简单请求竟产 reasoning_tokens=173 → doubao_21 每次都先生成海量 reasoning，且 reasoning 不受 max_tokens 约束
       │
       → job-sized 长请求 → reasoning 阶段爆炸 → 响应耗时 >> job HTTP client 超时
       → job client 超时断开 socket (Broken pipe)
       │
14:57  adapter 拿到 provider 200 但写回客户端失败 → 误判为 provider FAILED → 走 fallback
       ├─ FALLBACK deepseek_full: OK 200 (11833B) 457078ms → FAILED Broken pipe（客户端早断）
       ├─ FALLBACK doubao:        OK 200 (6733B)  496123ms → FAILED Broken pipe
       ├─ FALLBACK deepseek:      HTTP 500
       ├─ FALLBACK qwen:          OK 200 (5341B)  526554ms → FAILED Broken pipe
       └─ ALL 4 FALLBACKS FAILED → 502 → 连 502 都写不回 (BrokenPipeError)
       │
       → job 收到 502/空 → 降级不推送（避免占位符污染）→ raw cache 空 = 告警里 "last reason 为空"
```

## 三、三层根因（触发器 → 放大器 → 掩护者）

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | primary 切 doubao_21（reasoning 模型） | 所有 cron job 流量转到 reasoning 模型；批量长请求 reasoning 开销爆炸到 7-9 分钟 |
| **放大器** | fallback chain 也全是 reasoning/慢模型 + adapter broken-pipe 后仍傻跑完 4 个 fallback | deepseek_full R1 / doubao 2.0 均 reasoning；客户端断开后 adapter 浪费 500s 级联到死 socket |
| **掩护者** | job 错误解析 `last reason` 空 + PA 短请求正常掩盖批量延迟 | timeout 无 response body → 解析不到原因；PA E2E「延迟非故障」验证时没暴露批量 job 的 reasoning 延迟（不同请求 profile） |

## 四、为什么以前没发生（条件组合）

| 条件 | 以前（qwen primary） | 现在（doubao_21 primary） |
|------|------|------|
| primary 是否 reasoning 模型 | 否（Qwen3-235B-Instruct 非 reasoning，秒级） | 是（每次先生成 reasoning，不受 max_tokens 约束） |
| job 请求 profile | 大 prompt + 大输出 → qwen 快速完成 | 大 prompt → reasoning 爆炸 → 7-9 分钟 |
| job/PA 共用 PROVIDER | 都在 qwen（都快） | 都在 doubao_21，但 PA 短请求容忍、job 长请求超时 |

**核心矛盾**：job 和 PA 共用同一个 `PROVIDER` env，无法轻易分开 —— PA（短对话）适合 reasoning 模型，批量 job（长分析 + 大结构化输出）不适合。单一 primary 无法同时满足两种 workload。

## 五、修复

**立即（运维回滚，Plan B，V37.9.203 设计）**：`PROVIDER=qwen`（`~/.env_shared` + adapter plist EnvironmentVariables）→ bootout/bootstrap 重载 → doubao_21 退回 fallback 链首 `[doubao_21, deepseek_full, doubao, deepseek]`。验证：qwen 直连 0.412s（vs doubao_21 7.3s、job 7-9 分钟）。恢复到 V37.9.218 设计并验证过的状态。

**排除项**（不是这些）：adapter 宕机（/health 200 + 短请求 7s）/ doubao_21 端点故障（test OK）/ auth / env / fallback 链配置 —— 全部正常。**是 reasoning 模型对批量长请求的延迟问题**。

## 六、遗留（doubao_21 迁移真门槛）

让 doubao_21 能重新作 primary 的前提是**批量 job 走快速路径**，候选：
1. **capability router 分流**（V37.9.76 shadow）：PA/交互 → doubao_21（reasoning 质量），批量 job → 快速非 reasoning provider
2. **doubao_21 reasoning 上限/关闭**：批量 job 请求带参数抑制 reasoning（若端点支持）
3. **job 用独立快 provider**：批量 job 显式指定 provider（需 adapter 支持 per-request provider override，当前 adapter 用全局 PROVIDER env 忽略请求 model）

在此之前 doubao_21 保持 fallback 链首，不作 primary。

## 七、次生观察（不阻塞，登记）

**adapter broken-pipe 级联浪费**：客户端断开（primary 阶段 broken pipe）后，adapter 仍傻跑完全部 4 个 fallback（浪费 ~500s 到死 socket）。robustness 候选：检测客户端 broken pipe 时立即中止 fallback 级联，不再尝试写回已断开的 socket。治标不治本（reasoning 延迟才是根因），低优。

## 八、元教训

- **provider 迁移必须按 workload profile 验证，不能只测一种请求**：PA 短对话 E2E 通过 ≠ 批量分析 job 可用。同一 provider 对不同请求 profile 表现天差地别（reasoning 模型尤甚）。（对应原则 #6 E2E 验证需覆盖真实 workload + 原则 #13 用户视角）
- **reasoning 模型的 reasoning tokens 不受 max_tokens 约束**：`reply OK`(max_tokens=10) 产 173 reasoning tokens = 延迟不可控。批量/低延迟场景选 provider 必须实测 reasoning 开销。
- **优雅降级掩盖结构性失败**：job「降级不推送 + last reason 空」让三个 job 全挂看起来像正常降级，靠用户收到告警才发现（MR-4 silent-failure 家族；fail-plausible 变种——失败被包装成正常降级）。

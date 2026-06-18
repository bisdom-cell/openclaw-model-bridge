# 案例分析：微信推送 contextToken 根因 — `✅ Sent` 的假成功

> 日期：2026-06-18 | 触发事件：迁移到微信（openclaw-weixin）作推送通道后，cron 内容推送 3 小时零到达微信，Discord 全程正常；多轮排查才挖到 provider 日志的 `contextToken missing`。

---

## 事件

WhatsApp 频道连续 3 天 408 限流被临时禁用（V37.9.162/170），项目把推送主通道切到腾讯微信插件 `@tencent-weixin/openclaw-weixin`，并用 Path B（V37.9.171-178）把全栈推送收编进 `notify.sh`，路由到 `openclaw-weixin`。

实测结果：**微信交互问答正常**（用户给 clawbot 发消息，PA 能回复），但**所有 cron 定时推送（论文/技术/财经/KB 摘要等）3 小时零到达微信**，同样的 `notify` 调用 Discord 全部正常收到。

## 表面现象 vs 真实根因

| 层级 | 表面判断 | 深挖后的真实根因 |
|------|----------|-----------------|
| 第一眼 | "WEIXIN_TARGET 没设" | 错——`.env_shared:27` 已设 `o9cq…@im.wechat` |
| 第二层 | "target 值无效" | 错——直发 `openclaw message send` 返回 `✅ Sent + Message ID`，且交互回复能到达同一 target |
| 第三层 | "notify 没路由到 weixin" | 错——`notify --topic X` rc=0，CLI 确实调用了 openclaw-weixin |
| 第四层 | "微信 48h 客服窗口" | 接近——但比窗口更彻底 |
| 第五层（铁证） | "provider 日志说了什么？" | **`sendWeixinOutbound: contextToken missing for to=o9cq…@im.wechat, sending without context`（每条 outbound 都打）** |
| 第六层（机制） | "为什么 CLI 发起的就没 token？" | 微信客服/对话平台（ilinkai.weixin.qq.com）outbound **必须携带 contextToken**（来自用户**入站消息**的会话上下文）。`notify → openclaw message send` 是独立 CLI 发起、不绑任何入站 → 永远无 token → "sending without context" → **微信丢弃** |

## 完整因果链架构图

```
微信客服/对话平台 (ilinkai.weixin.qq.com)
  契约: outbound 消息必须携带 contextToken = 用户入站消息的会话上下文 token
        │
  ┌─────┴───────────────────────────────────────────────┐
  │ 路径 A：PA 回复用户入站消息                            │
  │   用户在微信发消息 → Gateway 收 inbound（带 contextToken）│
  │   → PA handler 在该 inbound 的上下文里生成回复          │
  │   → outbound 持有 contextToken → ✅ 微信投递成功        │
  │   （这就是"交互层通了"的原因）                         │
  └──────────────────────────────────────────────────────┘
        │
  ┌─────┴───────────────────────────────────────────────┐
  │ 路径 B：cron 定时推送（我们的全部内容推送）            │
  │   cron → notify.sh → openclaw message send             │
  │     --channel openclaw-weixin --target o9cq…@im.wechat  │
  │   = 独立 CLI 发起的 outbound，不绑任何 inbound          │
  │   → 没有 contextToken                                  │
  │   → provider: "contextToken missing, sending without context" │
  │   → 微信平台丢弃（无会话上下文）                        │
  │   → CLI 仍返回 ✅ Sent + Message ID（本地接受确认）     │
  │   → notify rc=0 → 我们以为成功 ← ❌ fail-plausible      │
  └──────────────────────────────────────────────────────┘
        │
  用户视角：微信 3 小时零到达；Discord（无此契约）全程正常
```

## 三层根因（触发器 → 放大器 → 掩护者）

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | WhatsApp 408 禁用 → 迁移微信作推送通道 | openclaw-weixin 是**客服/对话**通道，架构上 outbound 需 contextToken |
| **放大器** | 我们的推送模型全是 CLI 发起的 outbound（notify → message send），与任何 inbound 解耦 | CLI 发起的 outbound **永远**拿不到 contextToken（不止"窗口外"，是"压根不在会话处理上下文里"） |
| **掩护者** | `✅ Sent` / `rc=0` / `Message ID` 都是**插件本地接受确认**，不代表微信投递 | 多个 session 都把 `rc=0`/`✅ Sent` 当成投递成功（fail-plausible），直到读 provider 自有日志 `/tmp/openclaw/openclaw-*.log` 才见真相 |

## 为什么以前没发现（条件组合分析）

| 条件 | 以前 | 现在 |
|------|------|------|
| 推送通道 | WhatsApp（无 contextToken 契约，CLI 发起可直投） | 微信客服通道（CLI 发起 outbound 需 token） |
| 验证标准 | — | 我误用 `rc=0`/`✅ Sent` 当投递成功，没要求"用户微信里真实看到" |
| 日志位置 | gateway.log | 真发送结果在 **provider 自有日志** `/tmp/openclaw/openclaw-*.log`（gateway.log 只有启动行）——不知道这个日志就看不到 `contextToken missing` |
| 交互层 | — | 交互回复正常（路径 A 有 token），制造了"微信通道没问题"的错觉，掩盖了路径 B 的失败 |

多条件组合：**新客服通道 + CLI 发起的 outbound + 用 rc=0 当投递成功 + 真日志在非常规位置 + 交互层正常掩护** —— 单独任一条件都不致命，组合起来让"零投递"潜伏了整个迁移周期。

## 修复（V37.9.179）

**不是修代码 bug**（notify 路由、CLI 调用、target 全对）——是**通道选型错误**：微信客服通道架构上做不了无人值守定时推送。

- `notify.sh` 默认 `NOTIFY_CHANNELS` 从 `openclaw-weixin,discord` 退回 **`discord`**（当前唯一可靠的定时推送通道）。
- WhatsApp 408 缓解后恢复：设 `NOTIFY_CHANNELS="whatsapp,discord"`（无 contextToken 契约，CLI 发起可直投）。
- weixin 分支保留（显式配置 / 交互 / 调试用），但不在默认推送通道。
- WeChat 退回**交互问答**角色（路径 A 正常）。

## 关键教训

1. **`✅ Sent` / `rc=0` / `Message ID` ≠ 投递成功** —— 这些是发送侧"已接受"的本地确认，不是接收侧"已送达"。验证推送通道必须以**用户端真实看到**为准（原则 #11 结果验证优先 + 原则 #13 像用户一样用）。这是 fail-plausible（系统报告成功、实际失败）的教科书案例。

2. **真相常在非常规日志位置** —— gateway.log 只有启动行，真正的发送/投递结果在 provider 自有日志 `/tmp/openclaw/openclaw-*.log`。排查投递问题要先问"这个通道的 provider 把详细日志写哪了"。

3. **通道能"交互回复" ≠ 能"主动推送"** —— 客服/对话类 IM 通道（微信公众号/对话平台、很多客服 SDK）普遍区分"会话内回复"（有上下文 token）与"主动群发"（需模板/订阅授权）。选推送通道前必须验证它支持**无会话上下文的主动 outbound**。

4. **迁移的核心前提必须先验证** —— 整条微信迁移建立在"openclaw-weixin 能主动推送"这个**从未被验证**的假设上。下次引入新通道作推送目标，第一件事是发一条 cron 式（CLI 发起、无 inbound）测试消息并**确认用户端收到**，而不是看 `rc=0`。

5. **工程正确 ≠ 选型正确** —— Path B（notify 收编全栈推送）本身是对的工程，不浪费（服务 WhatsApp 恢复 + Discord）。错的是把微信客服通道当推送目标。区分"实现质量"与"前提正确性"。

## 相关

- `notify.sh` — `_NOTIFY_CHANNELS` 默认 V37.9.179 退回 discord；weixin 分支 + V37.9.176 一次性 WARN 保留
- `ontology/docs/cases/pa_alert_contamination_case.md` — 另一个 fail-plausible 家族（V37.9.175 proxy 告警 .py 旁路）
- `docs/paper/silent_failures_taxonomy/` — fail-plausible 概念（本案是"发送侧假成功掩盖接收侧零投递"的典型，可补入 taxonomy）
- `test_notify_weixin.py` — 默认 discord-only + weixin 显式配置 WARN 守卫

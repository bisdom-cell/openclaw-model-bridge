# 推送链路丢失面审计（4.27 冷调用回归 follow-up (d)）

> 2026-06-15 · 闭合 V37.9.145 登记的 follow-up (d)「直推无重试 job 丢失面评估」
> 方法：dev 全仓代码级审计（无需 Mac Mini 数据）。把「当前证据=无丢失」从声明升级为可证审计。

## TL;DR

**无真·总丢失面。** 每一条承载真实内容的推送都至少有一层恢复机制：
notify.sh 的 3 次重试+失败队列，**或** 一个 Discord 孪生发送。4.27 冷调用回归下的残留风险是
**WhatsApp 通道的瞬时缝隙**（attempt-1 冷超时），但同条内容在 Discord 存活 → 降级为单通道，
不是数据丢失。这与用户实测「两天推送没少收」一致。

## 4.27 冷调用交互模型

V37.9.145 实证：冷调用第一发 WS >10s 必超时（Gateway 侧 WS 连接冷启动），一分钟内暖窗口正常（A2 9.9s）。
暖窗口是 **Gateway/WS 连接级**，不是 CLI 进程级——每个 `openclaw message send` 是独立 CLI 进程，
但都连同一个 running Gateway。**第一次发送暖化 WS 路径后，几秒内的后续发送落在暖窗口。**

## 三类推送路径（按恢复能力）

| 类别 | 机制 | 4.27 冷超时下的行为 | 丢失面 |
|---|---|---|---|
| **1. notify.sh 路径** | 3 次重试（2s/4s/8s 指数退避）+ 失败队列 `~/.kb/notify_queue/` + 可重放 | attempt-1 冷超时 → 重试落暖窗口；全失败 → 入队后续重放 | **零**（全恢复） |
| **2. 直推·双通道** | WhatsApp 直推 + Discord 直推（两次独立无条件调用，无重试） | WhatsApp attempt-1 冷超时丢失 → 但暖了 WS → Discord 调用落暖窗口存活 | **WhatsApp 缝隙**，内容在 Discord 不丢 |
| **3. 直推·单通道真内容** | 无 notify.sh、无 Discord 孪生的单发真内容 | 冷超时 → 总丢失 | **审计结论：不存在此类** |

## 全仓站点分类（40+ job / 127 直推站点）

- **无数据/错误通知**（line ~72-91 `$msg`，如「今日无新论文」「LLM 失败降级」）：
  全部是 `if command -v notify; then notify --topic alerts; else <直推 WhatsApp>; fi` 模式
  —— **notify.sh 优先**，直推 `else` 分支仅 dev/notify 不可用时触发。Mac Mini 生产走 notify.sh（类别 1）。
  即便丢失也是「无新内容」低价值通知，非数据丢失。

- **chaspark 主内容**（line 747-755 `WA_MSG`）：`notify "$WA_MSG" --topic daily`（类别 1），
  Discord 经 notify.sh `--topic` 路由。直推为 dev fallback。

- **论文/feed 类 job 主内容**（hf_papers / arxiv / dblp / acl_anthology / ai_leaders_bsky / karpathy_x /
  github_trending / rss_blogs 等的 MSG_CONTENT + 多窗口 CHUNK_CONTENT）：
  **直推双通道**（类别 2）——WhatsApp `if ... 2>SEND_ERR; then` 紧接 Discord `|| true`。
  冷超时下 WhatsApp 缝隙，Discord 存活。

- **告警路径**（job_watchdog / wa_keepalive / cron_monitor_fatal_handler / auto_deploy quiet_alert）：
  WhatsApp + Discord #alerts 双通道（V37.8.13 起「告警链不依赖失效主体自身」），Discord 兜底。

**结论**：扫遍 127 直推站点，零「单通道真内容无恢复」站点 → 类别 3 为空集。

## 残留风险与硬化建议（非紧急，需 Mac Mini E2E）

唯一残留 = **类别 2 的 WhatsApp 瞬时缝隙**（仅 attempt-1 冷窗口，多数 cron run 在暖窗口）。
内容始终在 Discord，且 4.27 冷调用是上游回归（非本项目 bug）。

**日落法 / MR-8 硬化路径（候选，deferred）**：把类别 2 的论文类 job 主内容推送从「手搓直推双通道」
迁移到 notify.sh `--topic`（已有 3 重试+队列 **且** 双通道路由）。收益是**一物一形**——
消除 ~8 个 job 各自手搓同款双通道直推（copy-paste-is-a-bug-class），顺带消除 WhatsApp 缝隙。
成本：每个 job 有多窗口分片逻辑，迁移需逐一改 + Mac Mini E2E 验证（原则 #6）。
非紧急（Discord 已覆盖内容，缝隙瞬时），登记为未来候选。

## 与 4.27 上游回归追踪的关系

本审计闭合 follow-up (d)（丢失面=无总丢失）。其余 follow-up 需 Mac Mini 生产数据：
(a) `openclaw message send --help` timeout 旋钮探测（可配则调大 attempt-1 噪声消失）
(b) `ls -la ~/.openclaw/plugin-runtime-deps/` 多 hash dir 检查（staging 间歇复发机制）
(c) 上游 releases 查 CLI 冷启动/staging 修复（tripwire 证据收集，不自动触发升级）

# WhatsApp Session Logout 静默 7h 血案 (V37.9.162)

> **TL;DR**: 2026-06-16 凌晨 6h 重连风暴（Baileys 428/499/503，每次 Retry 1/12）触发 WhatsApp 账号级反滥用 → 08:34 服务端 `session logged out` → channel exited → 静默 7h，用户上午发现。**全程 Gateway HTTP:18789 = 200、Discord = connected、HEARTBEAT.md 干净、launchctl PID 稳定——唯一死的是 WhatsApp 频道本身。** 根因：`wa_keepalive.sh` 名为"WhatsApp 保活"实际只探 Gateway HTTP 端口，与频道链接状态解耦 → Gateway 健康时对频道掉线完全盲 → 零告警。这个盲区是 V37.8.13 INV-WA-001（守 Gateway-DOWN）**留下的相邻洞**，潜伏 7 个月。MR-4 silent-failure 谱系延续（V37.8.13 #11 同族 WhatsApp 静默血案的镜像变体）。

## 与 V37.8.13 的本质区别

| 维度 | V37.8.13（Gateway 静默死亡） | V37.9.162（频道服务端登出） |
|------|------------------------------|------------------------------|
| Gateway 进程 | **真死**（launchd jettison，HTTP 000） | **健康**（HTTP 200，PID 99020 exit 0 全程稳定） |
| 死的是什么 | 整个 Gateway | Gateway 内的 WhatsApp **channel**（独立 logged out） |
| 触发 | restart.sh bootstrap 后 21s 崩溃 | Baileys 凌晨重连风暴 → WhatsApp 账号级限流 → 服务端登出 |
| 告警为何不响 | 告警链全死（quiet_alert 吞 + keepalive 不告 + restart 不验证） | keepalive **看不见**（只探 Gateway 端口，频道死它不知道） |
| 修复 | INV-WA-001 守 Gateway-DOWN 告警走 Discord | V37.9.162 守频道链接状态（Gateway 健康但频道掉线也告警） |

**核心洞察**：V37.8.13 修好了"容器死了要告警"，但留下"容器活着、里面的频道死了"这个相邻失败模式没有监控。一个修复堵住一个洞，却留下紧挨着的洞——7 个月后被今天的血案暴露。

## 完整因果链架构图

```
2026-06-15 夜 ~ 2026-06-16 凌晨
  ├─ Gateway (:18789) HTTP 200 ✅  /  Discord connected ✅
  ├─ WhatsApp channel：开始不稳定（Baileys WS 反复断开重连）
  │
01:59:00  [whatsapp] Web connection closed (status 428 Precondition Required
          Connection Terminated). Retry 1/12 in 2.15s…
  ├─ WhatsApp 服务端开始拒绝该账号连接（428 = 前置条件失败）
  │
03:59:05  [whatsapp] watchdog timeout (app-silent) - restarting connection
03:59:05  [whatsapp] Web connection closed (status 499). Retry 1/12…
05:21:54  [whatsapp] Web connection closed (status 503 Service Unavailable
          Stream Errored). Retry 1/12…
07:21:58  [whatsapp] watchdog timeout (app-silent) - restarting connection
07:21:58  [whatsapp] Web connection closed (status 499). Retry 1/12…
  │
  ├─ ★ 触发器：6 小时内每次断开都 Retry 1/12（最多 12 次/轮），
  │    几十次连接尝试 = Baileys（非官方客户端）触发 WhatsApp 反滥用阈值
  │
08:34:27  [whatsapp] session logged out. Run `openclaw channels login
          --channel whatsapp` to relink.   ←━━━━━━ 服务端强制登出
08:34:27  [whatsapp] [default] channel exited without an error
  │
  ├─ WhatsApp 账号被服务端登出该设备 → channel 退出 → 无活监听器
  ├─ 但 Gateway 进程本身完全健康（HTTP 200 持续返回）
  │
08:34 ~ 15:24（7 小时黑洞）
  ├─ [wa_keepalive] 每 30min：curl localhost:18789 → HTTP 200 → "OK: Gateway
  │   reachable" → echo "0" 重置计数 → 零告警
  │   └─ ★ 放大器：keepalive 只探 Gateway 端口，对死掉的频道完全盲
  ├─ [delivery-recovery] 40+ 次 "No active WhatsApp Web listener" → 排队消息
  │   全部投递失败（用户发来的消息无人接）
  ├─ Discord 全程 connected / HEARTBEAT.md 干净 / launchctl PID 99020 稳定
  │   └─ ★ 掩护者：每一个监控信号都是绿的，唯一死的东西没有任何探针
  │
~15:15 HKT
  └─ 用户察觉"8:15 之后 WhatsApp 没收到任何信息" → 人工发现（唯一的 L3 效果层）

——— 诊断 + 恢复（数据驱动，零盲改，原则 #28 / 血案 #93）———

  ├─ channels status → WhatsApp: not linked, stopped, disconnected, error:not linked
  │   （Gateway reachable + Discord connected）→ 锁定唯一死的是频道
  ├─ gateway.err.log grep → 决定性证据 08:34 `session logged out`
  ├─ restart.sh → Gateway 重启后仍 not linked（频道登出不是 Gateway 重启能修的）
  ├─ channels logout → "No WhatsApp Web session found"（非死凭据，本地无残留会话）
  ├─ curl web.whatsapp.com/ws/chat → 400 / 0.34s（网络 + WS 前端正常，排除网络阻断）
  ├─ channels login → 408 WebSocket 握手超时，无二维码（×6 over 1h）
  ├─ 手机 WhatsApp：一度无法发消息 → 顶部正常 + 时钟不卡 → 重启手机后 ✓✓ 正常
  │   └─ 确诊：WhatsApp 账号级临时限流（非永久封禁，手机 8min 自愈）
  └─ 发消息限流先清（手机能发）/ 设备链接限流后清（login 仍 408，且每次失败重新触发）
```

## 三层根因

| 层级 | 发现 | 证据 |
|------|------|------|
| **触发器** | Baileys（非官方 WhatsApp Web 客户端）凌晨 6h 重连风暴（428/499/503，每次 Retry 1/12）触发 WhatsApp 账号级反滥用 → 08:34 服务端强制登出该设备 | gateway.err.log 01:59→08:34 连续状态码 + `session logged out` |
| **放大器** | `wa_keepalive.sh` 只探 Gateway HTTP:18789 端口存活，与 WhatsApp 频道链接状态**解耦** → Gateway 健康（200）时对频道掉线完全盲 → 7h 零告警。这是 V37.8.13 INV-WA-001（守 Gateway-DOWN）留下的相邻洞，潜伏 7 个月 | wa_keepalive.log 全是 "OK: Gateway reachable HTTP 200" |
| **掩护者** | Gateway HTTP 200 + Discord connected + HEARTBEAT.md 干净 + launchctl PID 稳定——每个监控信号都绿，唯一死的频道没有任何探针 → "一切健康"错觉。比 V37.8.13 更彻底（那次 Gateway 真死至少 HTTP 000，今天 Gateway 真健康，掩护更完整） | channels status：Gateway reachable / Discord connected / WhatsApp 唯一 disconnected |

## 时间线还原

| 时间 (HKT) | 事件 | 影响 |
|------------|------|------|
| 01:59 | WhatsApp 428 Connection Terminated，Retry 1/12 | 重连风暴起点 |
| 03:59 / 05:21 / 07:21 | watchdog timeout + 499 / 503 反复 | 6h 数十次连接尝试喂养限流 |
| 08:34:27 | **`session logged out` + channel exited** | 频道死亡，无活监听器 |
| 08:34~15:24 | wa_keepalive 每 30min "OK HTTP 200" + 40+ delivery-recovery 失败 | **7h 静默黑洞** |
| ~15:15 | **用户人工发现** | 唯一 L3 效果层 |
| 15:24~16:36 | 诊断（channels status / err.log / curl WS / 手机症状）+ login ×6 408 | 确诊设备链接限流 |
| ~16:10 | 手机重启 → 能正常发消息 | 发消息限流清，账号非永久封禁 |
| 待 | 等 2-3h 设备链接限流冷却 → 单次 login 扫码 | 恢复（用户动作） |

## 为什么以前没发生（条件组合）

| 条件 | 以前 | 现在 |
|------|------|------|
| ① Baileys 重连风暴 | 偶发轻微重连 | 6h 持续风暴（428/499/503 × Retry 1/12） |
| ② WhatsApp 账号级登出 | 罕见 | 风暴触发反滥用 → 08:34 强制登出 |
| ③ keepalive 对频道状态盲 | **一直如此**（7 个月潜伏盲区） | 一直如此——但只在 Gateway 健康 ∧ 频道死亡同时成立时暴露 |
| ④ V37.8.13 只守 Gateway-DOWN | 设计如此 | 频道-DOWN-while-Gateway-UP 从未被守 |

**四条件同时成立**：Baileys 风暴 + 账号登出 + keepalive 盲 + 相邻洞未守 → 7h 静默。前三个任何一个不成立都不会爆（风暴不到阈值不登出 / Gateway 也死了 V37.8.13 会告警 / keepalive 探频道就会发现）。

## 修复（V37.9.162 预防）+ 恢复（限流冷却）

| # | 类型 | 内容 | 效果 |
|---|------|------|------|
| A | 预防（代码） | `wa_channel_status.py` 纯函数解析 `channels status` 的 WhatsApp 行（FAIL-OPEN + token 精确区分 not-linked/linked + 手机号不泄漏） | 频道状态可机读 |
| B | 预防（代码） | `wa_keepalive.sh` Gateway-OK 分支 `_wa_channel_check`：频道掉线独立计数，连续 2 次（1h）升级 Discord #alerts（MR-14 走 Discord） | **7h → 1h 检出** |
| C | 预防（治理） | INV-WA-001 断言精确化（`--channel whatsapp` → `message send --channel whatsapp`，避误伤恢复指令文本） | 治理 91/91 不破 |
| D | 恢复（运维） | 手机重启清卡死连接 + 停止 login 尝试等限流自然冷却 → 单次 `channels login` 扫码重链 | 用户动作 |

## MR-4 / MR-14 谱系

- **MR-4 silent-failure**：应该响的告警没响——延续 V37.8.13 #11（同族 WhatsApp 静默）。今天的**新形态**：监控主体（Gateway）健康，但被监控的子频道（WhatsApp channel）死亡，监控探错了对象。
- **MR-14 alert-path-must-not-depend-on-failing-subject**：修复让频道掉线告警走 Discord（WhatsApp 已死不能走 WhatsApp）。V37.8.13 立 MR-14 时守的是 Gateway-down 告走 Discord，今天复用同一元规则守频道-down。

## 元教训

1. **探端口 ≠ 探频道**：监控"容器"（Gateway HTTP）不等于监控"内容"（WhatsApp 频道链接状态）。一个监控必须探它声称要保护的**那个东西本身**，而非它的代理信号。wa_keepalive 名为"WhatsApp 保活"却只探 Gateway 端口——名实不符 7 个月。
2. **修复一个洞会留下相邻的洞**：V37.8.13 INV-WA-001 堵住 Gateway-DOWN 告警，却留下"频道 DOWN 而 Gateway UP"未守。每个修复都应问："这个修复**没覆盖**哪个紧邻的失败模式？"
3. **全绿不等于健康**：HTTP 200 + Discord connected + HEARTBEAT 干净 + launchctl 稳定——每个信号都绿，唯一死的东西没有探针。绿色仪表盘掩盖**未被instrument的失败模式**。绿不是"健康"的证据，只是"被监控的维度健康"的证据。
4. **重试会喂养限流（恢复教训）**：反复失败的 login 重新触发 WhatsApp 设备链接限流。Baileys 非官方客户端的反复失败链接有把"临时限流"升级为"更长/永久封禁"的真实风险。恢复的解药是**时间 + 零尝试**，不是技巧。
5. **数据驱动诊断零盲改（原则 #28 / 血案 #93）**：全程用 curl WS 测试、channels logout "无 session"、手机重启自愈等证据定性，从未凭直觉 `rm auth`（血案 #93 教训兑现）。

## 本体喂养

- V37.9.162: `wa_channel_status.py`（FAIL-OPEN 纯函数解析）+ `wa_keepalive.sh::_wa_channel_check`（独立计数 + Discord 升级）+ 46 单测（test_wa_channel_status.py）。
- INV-WA-001 断言精确化（发送路径 vs 恢复指令文本）。
- **已决（V37.9.188 日落法退役 INV 计划）**：INV-WA-CHANNEL-001（whatsapp-channel-disconnect-escalates-when-gateway-healthy）原登记一周观察后立治理不变式。一周观察期（6/16→6/23）已过，复审决定**不立治理 INV，由 `test_wa_channel_status.py`（已在 full_regression）守护契约即足够**。理由（V37.9.166 三类判据 category A）：MR-14 Discord-not-WhatsApp 发送路径 + 手机号泄漏 + FAIL-OPEN + wa_keepalive 集成 + auto_deploy 部署 + 行为级端到端**全部已被该单测守护**（CI 跑），原则 #21「什么坏了没人发现」判据不成立（test 抓回归）→ 加治理 INV 是 governance bloat（外部评审2「降组合复杂度」+ 日落法北极星 + V37.9.186 同款"用单测不立 INV"）。仅剩 Mac Mini 生产 false-positive 观察（dev 不可执行，登记 unfinished）。
- **战略 deferred**：Baileys（非官方客户端）重复封禁风险评估——降低重连激进度 / WhatsApp 官方 Business API 替代成本 / 接受风险 + 监控（已由 V37.9.162 wa_channel_status 覆盖检出）。
- **观察 deferred**：4.27 openclaw CLI 插件 staging churn（每次 channels login 全量重 staging 9+ 无关插件，13-39s，plugin-runtime-deps 未持久化，吃掉连接超时预算）。

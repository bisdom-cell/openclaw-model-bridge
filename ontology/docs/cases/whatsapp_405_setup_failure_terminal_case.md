# WhatsApp 405 停摆 12.5 小时：一次建连失败即永久停止 + 告警指向错误的恢复动作

> 2026-10-20 · V37.9.374 · 分类：Gateway 自身缺陷（4.27）× 上游版本号回退 bug × 我方告警文案
> 状态：已恢复（restart.sh 一次）· 告警文案已修 · 上游 bug 未修（openclaw/openclaw#125673）

## 一句话

服务端一次普通的 428 断线后，Gateway 4.27 重建连接时取 WhatsApp Web 版本号失败、
静默回退到一年前的内置版本，握手被 405 拒绝；4.27 对建连阶段的失败不重试，监听循环
直接退出，频道停在 `stopped` 12.5 小时。凭据一直有效，重启一次即恢复——而我们自己的
告警每 3 小时提示一次「扫码登录」，那是唯一会把事情变糟的操作。

## 步骤一：因果链

```
00:37  [WhatsApp 服务端] 断开 → status 499
       └─ [Gateway 4.27] Retry 1/12 → 重建成功（当时取版本号正常）

02:28:31  [WhatsApp 服务端] 断开 → status 428 "Connection Terminated"
       ├─ [Gateway] monitor 循环: decideOnClose → retry → "Retry 1/12 in 2.15s"
02:28:33  ├─ controller.openConnection()
       │    ├─ createWaSocket()
       │    │    └─ fetchLatestBaileysVersion()
       │    │         GET raw.githubusercontent.com/WhiskeySockets/Baileys/master/src/Defaults/index.ts
       │    │         失败 → catch → return { version: 内置 [2,3000,1027934701], isLatest:false }
       │    │         ← 调用方只解构 version，回退无日志（上游 #125673）
       │    └─ waitForWaConnection(sock)
02:29:08  │         [WhatsApp 服务端] 拒绝旧客户端版本 → 405 "Connection Failure"
       │         → close 先于 open → reject
       ├─ monitor 循环 catch: isRetryableAuthUnstableError(err) → FALSE → throw err
       └─ finally: statusController.markStopped() → healthState = "stopped"
          （不经过 12 次重试计数；建连阶段的失败对 4.27 是终止性的）

02:30 起  [wa_keepalive :30/:00] openclaw channels status → "disconnected; stopped; error=405…"
       ├─ 计数 1 (02:30), 2 (03:00) → 首次升级 Discord #alerts
       ├─ 8 / 14 / 20 / 26 次各重复一次（每 3h）
       └─ 告警文案: "恢复: openclaw channels login（需手机扫码）"   ← 错误方向

15:00  用户收到第 26 次告警
15:00 后 排查：curl 版本号源 → 可达（拿到 1043857760）
       → bash restart.sh → Gateway 新进程重新取版本号成功 → connected
```

## 步骤二：三层根因

| 层级 | 发现 |
|---|---|
| 触发器 | 02:28 一次普通的服务端 428 断线，加上重建连接那一刻 GitHub raw 取不到（35 秒后才返回 405，符合取版本号超时后回退的形态） |
| 放大器 | ① 4.27 取版本号失败时静默回退到 Baileys rc.9 内置版本（2025-11），WhatsApp 对它必回 405；② 4.27 监听循环只对「认证状态不稳定」重试，其余建连失败直接退出，一次失败 = 永久停摆 |
| 掩护者 | 我方告警不分状态，一律建议扫码。频道仍 linked、凭据有效时，扫码不仅无效（登录走同一段取版本代码，同样 405），还会丢掉仍然有效的会话；照做的人会以为是登录出了问题，继续反复扫码（6 月 408 血案的路径） |

## 步骤三：时间线

| 时间 (HKT) | 事件 | 影响 |
|---|---|---|
| 00:37 | 499 断线 → 重试 1 次成功 | 无 |
| 02:28:31 | 428 断线 → 安排重试 | — |
| 02:29:08 | 重建连接握手 405 → 监听循环退出 | WhatsApp 频道停止，PA 在 WhatsApp 上不可用 |
| 03:00 | wa_keepalive 第 2 次 WARN → 首次 Discord 告警 | 告警建议扫码 |
| 06:00 / 09:00 / 12:00 / 15:00 | 每 6 次重复告警 | 同上 |
| 15:00 后 | 确认版本号源可达 → restart.sh | connected；队列积压 30 条待补发 |

Discord 推送全程正常（notify 部分送达返回 0，WhatsApp 那份入队，V37.9.359 语义）。

## 步骤四：为什么以前没发生（条件组合）

| 条件 | 以前 | 这次 |
|---|---|---|
| 服务端断线 | 常见（00:37 当晚就有一次） | 同 |
| 重建连接时取版本号 | 成功 → 当前版本号 → 连上 | 失败 → 回退到 2025-11 的内置版本 |
| 内置版本号是否还被接受 | 2025 年底仍可用 | 2026 年中起已被拒（Baileys 社区 405/client_too_old 报告） |
| 建连失败后的行为 | 未触发过 | 4.27 一次即停，不再重试 |

单独任何一条都不会导致长时间停摆：断线每天都有；取版本号偶尔失败，但只要内置版本还被接受就无害；
建连失败即停在历史上没被触发过。三者叠加才出现。内置版本号「过期」是随时间静默逼近的条件，
没有任何代码变化——这是持有 4.27 的成本里一条此前没被量化的项。

**对 09-22 那次的补充（只追加，不改写 V37.9.358 原文）**：V37.9.358 把 09-22 的 8 小时停摆
记为「4.27 撞 428 不自动重连」。按本次读到的 4.27 代码，428 本身会重试一次；停摆来自重试的
建连阶段失败（终止性）。09-22 的建连失败码当时未取证，推断是同一机制。

## 步骤五：喂养本体与处置

- **告警文案修复（V37.9.374）**：`wa_channel_status.py` 增加 `action` 字段 —— `not linked`
  或错误含 401/403/419 / "logged out" → `login`；仍 linked 但停了 → `restart`。`wa_keepalive.sh`
  据此给出恢复建议；未知值（含部署窗口内旧 parser 的三段输出）一律落到重启，因为重启无损、
  扫码有损。判据与上游 4.27 自己的提示一致（stopped/reconnecting/conflict → restart gateway；
  logged-out / not linked → channels login）。
- **刻意不做**：不让 wa_keepalive 自动重启 Gateway（Gateway 进程由 launchd 单一管理，
  V37.9.13；自动重启会引入重启风暴的新故障面，原则 #8 / #34）。不给 Baileys 打补丁
  （改第三方 dist，升级即丢）。
- **升级评估数据点**（下次评估纳入）：最新版 10.5 的 WhatsApp 插件仍调用同一个
  `fetchLatestBaileysVersion()`、浏览器标识相同，#125673 在 10-18 复核时仍 open → 升级不能
  消除回退 bug；但 7.1 起建连阶段失败也会重试 12 次（issue 日志「closed during setup (status 405).
  Retry 2/12」），短暂的 GitHub 抖动可以自愈 —— 这是 hold 的一项具体成本，不改变 hold 结论。
- **不新增治理不变式**：守卫测试（`test_wa_channel_status.py` I 类）已覆盖告警文案契约；
  上游行为不在我方治理范围内。

## 恢复操作（仅供下次参考）

先确认版本号源可达，再重启；不要扫码。

```
curl -sS --max-time 15 https://raw.githubusercontent.com/WhiskeySockets/Baileys/master/src/Defaults/index.ts | sed -n 7p
```

```
bash ~/openclaw-model-bridge/restart.sh
```

```
openclaw channels status
```

只有重启后状态显示 `not linked`，才执行 `openclaw channels login --channel whatsapp`。

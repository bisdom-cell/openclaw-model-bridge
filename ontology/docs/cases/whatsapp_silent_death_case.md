# WhatsApp 静默死亡血案 (V37.8.13)

> **TL;DR**: 2026-04-16 00:30 HKT Gateway 进程死亡 → WhatsApp 全断 9 小时 → Discord 正常但无 Gateway 相关告警。三层放大器同时失效：auto_deploy 凌晨静默期吞掉 CRITICAL 告警 + wa_keepalive 只写日志不告警 + restart.sh 不验证 Gateway 健康。MR-4 silent-failure 第 11 次演出。

## 完整因果链架构图

```
2026-04-15 23:30 HKT
  ├─ Gateway (:18789) reachable HTTP 200 ✅
  ├─ Proxy (:5002) ✅  /  Adapter (:5001) ✅
  ├─ Discord 健康  /  WhatsApp 健康
  │
2026-04-16 00:19 HKT
  V37.8.10 PR #404 合并到 main
  auto_deploy 每 2min 轮询 → 拉到 tool_proxy.py 变更 → NEED_RESTART=true
  │
  ├─ [restart.sh:13] launchctl bootout ai.openclaw.gateway ← Gateway 从 launchd 卸载
  ├─ [restart.sh:15-17] lsof kill 端口 18789/5001/5002
  ├─ [restart.sh:21] nohup adapter.py ✅
  ├─ [restart.sh:25] nohup tool_proxy.py ✅
  ├─ [restart.sh:47] launchctl bootout (冗余，已卸载)
  ├─ [restart.sh:49] launchctl bootstrap Gateway ← Gateway 重新注入 launchd
  ├─ [restart.sh:50] echo "Gateway loaded via launchd (KeepAlive enabled)" ✅
  ├─ [restart.sh:57] echo "[restart] Done!" ✅
  │
00:20:17 HKT
  gateway.err.log: [plugins] plugins.allow is empty ← Gateway 进程启动
  gateway.err.log: [delivery-recovery] No active WhatsApp Web listener
  │
  ├─ Gateway 进程启动但 WhatsApp 插件未加载
  ├─ Gateway 可能在尝试初始化 WhatsApp 连接时崩溃
  ├─ KeepAlive=true + ThrottleInterval=1 → launchd 1s 重启
  ├─ 但如果 rapid crash cycling → macOS launchd jettison 整个 service
  ├─ gateway.err.log 无更多重启尝试记录 → 说明 jettison 极快
  │
00:20:38 HKT ←━━━━━━━━ 确认死亡
  │
  ├─ [auto_deploy] preflight_check.sh ❌ Gateway :18789 不可达 (HTTP 000)
  ├─ [auto_deploy] quiet_alert("🔴 部署后体检失败")
  │   └─ [quiet_alert:31-33] is_quiet_hours() = TRUE (00:20 在 00-07 静默期内)
  │   └─ [quiet_alert:32] "静默期跳过推送" → 只写日志 → WhatsApp 跳过 + Discord 也跳过 ❌
  │   └─ ★ 放大器主谋：CRITICAL 告警被静默期无差别吞没
  │
00:30:01 HKT
  [wa_keepalive] WARN: Gateway 不可达 (HTTP 000)
  ├─ 写入 ~/wa_keepalive.log → 无告警推送
  ├─ 代码注释: "端到端推送失败由 job_watchdog.sh 的日志扫描覆盖"
  └─ ★ 放大器 2：wa_keepalive 只写日志不告警

00:48 / 00:52 HKT
  auto_deploy 再次拉到 V37.8.11 / V37.8.12 doc → preflight → 再次 ❌ Gateway 不可达
  → 再次被 [QUIET] 吞没 → 用户在 Discord 看不到任何告警

01:00~08:00 HKT (7 小时黑洞)
  ├─ wa_keepalive 每 30min 写 WARN (18 次) → 全部只写日志
  ├─ job_watchdog 08:30 运行 → 11 告警但无 "Gateway 服务无响应" ← 未解之谜
  ├─ Dream 03:00 → Discord daily ✅ / WhatsApp 失败 → 入队 (14KB × 2)
  ├─ ArXiv 08:02 → Discord alerts ✅ 但只报 LLM 失败，不提 Gateway
  │
08:30 HKT
  job_watchdog 推送到 Discord #alerts：🟡 WARNING 11 项
  ├─ 🔴 HN 11h 未更新 / 货代推送失败 / KB 晚间 llm_failed
  ├─ 🟡 Issues 19h 未更新
  ├─ 🔧 各种日志错误
  └─ ❌ 不含 "Gateway 服务无响应" — 第四层掩护者

09:XX HKT
  └─ 用户手动对比 WhatsApp (空) vs Discord (满) → 人工发现 9h 故障
```

## 三层根因

| 层级 | 发现 | 证据 |
|------|------|------|
| **触发器** | restart.sh bootstrap 后 Gateway 启动 21s 内崩溃 → launchd rapid-crash jettison 卸载 service | gateway.err.log 只有 00:20:17-18 四行，之后 9h 空白；launchctl list 无 ai.openclaw.gateway |
| **放大器主谋** | auto_deploy quiet_alert 凌晨静默期**同时跳过 WhatsApp + Discord**，3 次 CRITICAL 失败全被吞 | auto_deploy.log 86597: `[QUIET] 静默期跳过推送` |
| **放大器 2** | wa_keepalive 只写日志不推送任何告警 | wa_keepalive.sh 原代码注释 "不报错退出" |
| **掩护者** | Discord 健康让系统整体"正常"错觉掩盖 WhatsApp 断连；watchdog 08:30 实际运行但未检出 Gateway | Discord #告警 08:30 告警无 Gateway 相关内容 |

## 时间线还原

| 时间 (HKT) | 事件 | 影响 |
|------------|------|------|
| 00:19 | V37.8.10 merged | auto_deploy 触发 restart.sh |
| 00:20:17 | Gateway 启动（restart.sh bootstrap） | 4 行日志后沉默 |
| 00:20:38 | preflight Gateway ❌ HTTP 000 | [QUIET] 吞没告警 |
| 00:30:01 | wa_keepalive 首次 WARN | 只写日志 |
| 00:48 | 第 2 次 preflight ❌ | [QUIET] 再次吞没 |
| 00:52 | 第 3 次 preflight ❌ | [QUIET] 第三次吞没 |
| 03:05 | Dream WhatsApp 失败，入队 14KB×2 | Discord daily 收到 |
| 03:06 | Dream 自检告警 → Discord #alerts | 不提 Gateway 根因 |
| 08:30 | watchdog 告警 → Discord #alerts | 不含 Gateway 检测 |
| 09:XX | **用户手动发现** | 唯一的人工 L3 效果层 |

## 为什么以前没发生（条件组合）

| 条件 | 以前 | 现在 |
|------|------|------|
| ① Gateway 崩溃 | 偶发 | 00:20 restart.sh 后 21s 崩溃 |
| ② launchd jettison | 理论可能 | KeepAlive 重启循环过快触发 |
| ③ 凌晨时段 | 非凌晨时 quiet_alert 正常推送 | **00:20 在静默期 00-07** |
| ④ wa_keepalive 不告警 | 一直如此 | 一直如此（设计缺陷） |
| ⑤ Discord 掩盖 | 用户注意力分散 | Dream/ArXiv 在 Discord 正常 |

**五条件同时成立**：Gateway 崩 + launchd 放弃 + 凌晨静默 + keepalive 不告 + Discord 掩盖 → 组合出 9h 黑洞。

## 修复清单

| # | 修复 | 文件 | 效果 |
|---|------|------|------|
| A | quiet_alert 静默期仅跳过 WhatsApp，Discord 始终推送 | auto_deploy.sh | CRITICAL 永远到达 Discord |
| B | wa_keepalive 连续 2 次 WARN → Discord #alerts | wa_keepalive.sh | 1h 内自动告警 |
| C | restart.sh post-bootstrap 15s 健康验证 | restart.sh | Gateway 死亡立即可见 |
| D | INV-WA-001 + INV-QUIET-001 治理不变式 | governance_ontology.yaml | 防回退 |

## MR-4 Silent Failure 演出史

| 次 | 版本 | 形态 | 修复 |
|----|------|------|------|
| 1 | V37.3 | governance summary 吞 error | INV-GOV-001 |
| 2 | V37.4 | Dream Map budget 溢出 | INV-DREAM-001/002 |
| 3 | V37.4.3 | PA 告警污染对话 | INV-PA-001/002 |
| 4 | V37.5 | kb_review 6-bug 静默降级 | INV-REVIEW-001 |
| 5 | V37.6 | KB 数据层三合一 | INV-KB/SRC/DEDUP-001 |
| 6 | V37.7 | 双跑审计 dangling refs | INV-DEDUP-002 |
| 7 | V37.8.6 | Dream 自引用幻觉 | INV-DREAM-003 |
| 8 | V37.8.7 | ontology_sources 位置解析 | INV-ONTOLOGY-001 |
| 9 | V37.8.10 | LLM 错误链三层稀释 | INV-OBSERVABILITY-001 |
| 10 | V37.8.11 | drift 告警噪声 | INV-DEPLOY-003 |
| **11** | **V37.8.13** | **Gateway 宕 9h 告警链全死** | **INV-WA-001 + INV-QUIET-001** |

## 元教训

1. **告警链不得依赖失效主体自身**：Gateway 宕则 WhatsApp 不通，用 WhatsApp 告警 Gateway 宕 = 死循环。告警路径必须独立于被监控对象。
2. **静默期不等于聋哑期**：凌晨静默是为了不打扰用户（WhatsApp），不是为了让系统失去观察力（Discord）。两者必须分开。
3. **"Done!" 不等于 "Alive!"**：restart.sh 报 "Done!" 只代表 bootstrap 命令执行了，不代表 Gateway 真的在跑。Post-action validation 是必须的。
4. **Discord 健康是假阳性信号**：单通道健康让人默认整体正常——用户用 Discord 接收了 Dream/ArXiv 正常推送，对 WhatsApp 断连毫无察觉。双通道系统的告警必须覆盖跨通道差异。

## 本体喂养

- INV-WA-001: wa-keepalive-escalates-to-discord-on-consecutive-warn (MR-4, critical, 7 checks)
- INV-QUIET-001: quiet-alert-sends-discord-during-silence-period (MR-4, critical, 4 checks)
- MR-14 候选: `alert-path-must-not-depend-on-failing-subject`（本案核心教训，待评估是否升级为元规则）

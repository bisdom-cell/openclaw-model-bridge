# V37.9.80 — MOVESPEED TCC Sandbox 60 天血案真因终结

> **TL;DR**: macOS Big Sur+ TCC Sandbox **拒绝 cron 派生进程访问外置卷** /Volumes/MOVESPEED。所有"权限"假说 60 天来全错——真因是 macOS 系统级 sandbox，不是 fskit / ACL / ownership / TM snapshot / SSD 物理掉线。修复 = 系统设置加 `/usr/sbin/cron` 到完全磁盘访问权限 (FDA)。

## 决定性铁证 (2026-05-18)

V37.9.30 incident_analyzer 累积一周 (110 条 incident) 后用户视角推进 V37.9.80 调查，跑 `log show --predicate` 抓 macOS 系统日志：

```
2026-05-18 11:24:24 kernel (Sandbox) System Policy: rsync(92664) deny(1) file-read-data /Volumes/MOVESPEED/KB
2026-05-18 11:24:55 kernel (Sandbox) System Policy: touch(92697) deny(1) file-write-create /Volumes/MOVESPEED/.incident_probe_top_92680
2026-05-18 11:24:55 kernel (Sandbox) System Policy: ls(92694) deny(1) file-read-data /Volumes/MOVESPEED
```

**kernel + Sandbox + deny + file-read-data / file-write-create + /Volumes/MOVESPEED** 这五个字面量同行出现是 TCC sandbox 拒绝的**唯一指纹**。

## 60 天 6 个假说全证伪表

| 版本 | 假说 | 状态 | 证伪证据 |
|------|------|------|----------|
| V37.9.4 | exfat fskit transient EPERM | ❌ | APFS 重建后 60 天仍 fail |
| V37.9.27 | rsync retry 缺失放大 | ✅ 真但非主因 | retry helper 部署后仍 ~19/24h |
| V37.9.29 | noowners + UID 错位 (root:wheel vs UID 99) | ❌ | chown 后 110/110 `top=501:20 kb=501:20` 100% 正确, EPERM 持平 |
| V37.9.30 (a) | ACL deny — chown 不清 ACL | ❌ | 110/110 `acl_top: total 0` 无 ACL |
| V37.9.30 (b) | macOS daemon 抢占 SSD I/O | ❌ | 100% 5 daemon 共现是巧合 — daemon 一直在跑 |
| V37.9.30 (c) | TM 本地快照锁 metadata | ❌ | 110/110 `snap_0` 无快照 |
| V37.9.80 (本) | macOS TCC Sandbox 拒绝 cron 派生进程 | ✅ **铁证** | kernel Sandbox deny log + 5 个字面量同行 |

## 完整因果链架构图

```
┌─────────────────────────────────────────────────────────────┐
│ V37.9.4 起 (2026-04-23) — 60 天潜伏期                       │
└────────────────────────┬────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │ Mac Mini 升级 macOS │
              │   Big Sur+ TCC       │
              │ Sandbox 对 cron 生效 │
              └──────────┬──────────┘
                         │
                         ▼
        ┌────────────────────────────────────────────┐
        │  cron daemon 无 Full Disk Access 权限       │
        │  → 派生进程 (rsync/ls/touch/lsof/cat) 都被  │
        │     kernel Sandbox 拒绝访问 /Volumes/X      │
        └────────────────────┬───────────────────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
┌────────────────────┐ ┌──────────┐ ┌──────────────────┐
│ rsync 派生进程触发  │ │ ls 派生  │ │ touch 派生进程   │
│ → file-read-data   │ │ → deny   │ │ → file-write-    │
│   /Volumes/MOVE... │ │          │ │     create deny  │
│   被 deny(1)       │ │          │ │                  │
└──────────┬─────────┘ └────┬─────┘ └──────┬───────────┘
           │                │              │
           └────────────────┼──────────────┘
                            ▼
              ┌────────────────────────────┐
              │ rsync exit=1 + EPERM       │
              │ V37.9.4 capture.sh 写入    │
              │ ~/.kb/movespeed_incidents  │
              │ .jsonl                     │
              │ probe_top/kb 也是 EPERM    │
              │ (因为 probe 也走 cron 派生) │
              └────────────┬───────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │ V37.9.26 watchdog 主动告警  │
              │ 24h ≥5 → WhatsApp           │
              │ 60 天周报持续告警           │
              │ (但无人查 macOS 系统日志!)  │
              └────────────┬───────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │ V37.9.27 retry helper       │
              │ V37.9.29 chown 修复         │
              │ V37.9.30 ACL/snapshot 扩展  │
              │ — 全部"修复"但 EPERM 持平   │
              │ (因为真因不在文件系统层而   │
              │  在 macOS 系统 TCC 层)      │
              └────────────┬───────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │ V37.9.80 (本案)             │
              │ 用户视角推进 → 跑 log show │
              │ → kernel Sandbox deny 日志  │
              │ → 60 天血案真因终于浮现     │
              └─────────────────────────────┘
```

## 三层根因

### 触发器: macOS TCC 默认安全模型
macOS Big Sur+ 引入 TCC (Transparency, Consent, Control) — 默认拒绝所有进程访问"受保护"位置 (Desktop / Documents / Downloads / 外置卷)。`cron` daemon 作为 launchd 子进程**默认无 FDA 权限**，其派生进程 (rsync/ls/touch) 访问 /Volumes/X 全部被 kernel sandbox 拒绝。

### 放大器: cron 派生进程链路完全 sandbox 化
不只 rsync — `lsof / ls -le@ / touch` (V37.9.30 capture.sh 内调的取证命令) 在 cron 上下文都被拒绝。证据：
- `lsof: len=0 content=[]` ← V37.9.30 lsof 取证 100% empty
- `acl_top: total 0` (仅 ls 表头, 实际 ls 也被拒)

V37.9.30 扩展的 ACL/lsof/snapshot 三维度因为采集自身被 sandbox 拒绝，**取证维度都成为盲区** — 我们以为"110/110 normal/empty"是"无异常"，实际是**采集失败**！

### 掩护者: V37.4.3 案例当时错误判定 + 60 天无人查 macOS 系统日志
**V37.4.3 案例当时写**: "PA 编造 FDA 指令...launchd 管理的 cron 从不需要 FDA"——这是错的。

V37.4.3 PA 当时**场景错配** (看到无关告警乱回复 FDA), 但**FDA 修复方向本身是正确的**。该案例错误判定让 60 天来所有诊断都**绕开 FDA 方向**，转向 fskit / ACL / ownership / snapshot 等文件系统层假说，每个都被证伪。

**60 天没人查 `log show --predicate` macOS 系统日志** — 这是诊断盲区。如果 V37.9.4/27/29/30 任一阶段查过 macOS 系统日志，TCC sandbox deny 字面量会立即指向真因。

## V37.9.80 修复路径

### 立即操作 (用户必做)
```
1. 系统设置 → 隐私与安全性 → 完全磁盘访问权限
2. 点 + → Shift+Cmd+G → 输入 /usr/sbin/cron
3. 选中 cron 添加并勾选 ☑
4. 重启 Mac Mini (或重启 cron daemon)
```

### 验证 (24h 后)
```bash
python3 ~/movespeed_incident_analyzer.py --window 24h
# 预期: incidents 从 ~13/24h → 0 或 ≤2/24h
```

如还有 5+ incidents → 说明 FDA 没生效，需要排查：
- `csrutil status` 看 SIP 状态
- `tccutil reset SystemPolicyAllFiles` 重置 TCC
- 给 launchd 本身加 FDA (cron 是 launchd 子进程)

## 元教训 — 5 条

### MR-16 候选: macos-cron-derived-processes-need-fda
新元规则候选: macOS Big Sur+ 上, cron daemon 派生进程默认无 FDA, 访问外置卷会被 kernel TCC sandbox 拒绝. 任何依赖 cron 跑文件 I/O 到 /Volumes/X 的 job 都需要给 `/usr/sbin/cron` 加 FDA. 这不是"竞争争用"或"权限错配"或"文件系统 bug"——是 macOS 系统级 sandbox 设计。

### MR-10 understand-before-fix 反向第 5 次教训
V37.9.4 → V37.9.27 → V37.9.29 → V37.9.30 → V37.9.80 — 5 个版本 60 天 6 个假说全错, 每次都是"看到 EPERM 就猜文件系统层", **从未跑 macOS 系统日志**。如果第一天就跑 `log show --predicate 'eventMessage CONTAINS "Sandbox"'` 真因当天就能定位。

### 取证维度盲区: 采集器自身被沙箱
V37.9.30 lsof / ACL / snapshot 三个新维度采集**自身被 sandbox 拒绝**，但代码返回了 "normal / empty / snap_0" 这种**看似正常**的字面量。修复方向: capture.sh 应**显式区分** "未采集" vs "采集到空" — empty 字符串当 unknown 不当 "normal/no_data"。

### V37.4.3 案例错误判定纠正
原 V37.4.3 案例认为"PA 编造 FDA" — 实际 PA 当时**场景错配但修复方向正确**. 本案例提醒: PA 回复包含 *表面错乱 + 局部真值* 时, 不能因为表面错乱就否定局部真值. V37.4.3 案例应更新加入"FDA 修复方向 V37.9.80 证实是对的, 但当时场景错配"备注。

### 用户视角原则 #13 第 N+5 次正向兑现
V37.9.78 用户实测 weekly 报告 → V37.9.79 SLO 三项闭环 → V37.9.79-hotfix CI 失败实测 → **V37.9.80 推进 #3 MOVESPEED 真因终结**. 同 session 四阶段闭环, 全部数据驱动。

## V37.9.80 governance 闭环

立 **INV-MOVESPEED-TCC-001** (meta_rule=MR-4, severity=critical, verification_layer=[declaration]):
- 守 movespeed_incident_capture.sh 收集 mount/diskutil/lsof/ACL/snapshot 字段
- 守 case doc 引用 (修正 V37.4.3 认知)
- 守 V37.4.3 案例文档加 V37.9.80 备注 (FDA 方向 60 天后证实)

## 案例衍生

V37.9.80 这次闭环将 V37.9.x 系列 60 天血案的真因永久记录, 让未来:
- 任何"EPERM + 外置卷 + macOS" 类问题第一时间想到 macOS TCC sandbox
- V37.4.3 类 PA 回复 "场景错配但局部真值" 的认知不被误判否定
- capture.sh 取证扩展时区分"未采集" vs "采集到空" (V37.9.81+ 候选)

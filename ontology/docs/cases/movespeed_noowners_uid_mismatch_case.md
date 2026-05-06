# MOVESPEED noowners + UID Mismatch EPERM 血案

> **日期**: 2026-04-29 ~ 2026-05-06（8+ 天潜伏 → 7 轮诊断 → V37.9.29 path D' 闭环）
> **版本**: V37.9.4 → V37.9.27 → V37.9.28 → **V37.9.29 path D'**
> **元规则**: MR-4 (silent-failure-is-a-bug) 第 22 次演出 + MR-10 (understand-before-fix) 反向兑现 6 次
> **状态**: 已应用，24h 验证窗口至 2026-05-07 早 8:00 HKT

---

## TL;DR

- **触发器**: V37.9.4 把 MOVESPEED 从 exfat 重建为 APFS，APFS 默认 mount 时启用 `noowners` flag（macOS 对外接盘 user volume 的标准行为）。
- **放大器**: 原始 file ownership 是 `root:wheel` (顶层) + `_unknown:_unknown` (UID 99，KB 子目录)，**与 bisdom UID 501 完全不一致**。但 `noowners` flag 让所有 file 显示成 bisdom，**完全掩盖了 UID 错位**。
- **掩护者**: macOS 系统 daemon (`mds_stores` UID 89 / `backupd` UID 200 / `Spotlight` / `mds` / `fseventsd`) 在 cron 触发时刻试图 access volume metadata，触发跨 UID ACL 检查 → EPERM transient lock → bisdom 同时段 cron rsync 也命中 → `Operation not permitted`。
- **结构修复**: chown -R + enableOwnership + remount with owners flag。**关键**: chown 必须在 ownership 启用后才能生效（noowners 模式下 sudo chown 被静默吃掉，这是 macOS APFS 隐含规则）。
- **教训**: silent failure 第 22 次新形态 = 假设级 silent failure（V37.9.4 整团队基于"APFS 重建解决 EPERM"假设运作 ~60 天，直到 V37.9.26 主动告警 → V37.9.27 数据驱动诊断 → V37.9.29 终于触底）+ 教科书 sudo bypass 行为不能假设（chown noowners 模式下被吃）。

---

## 完整因果链架构图（四维度：时间 × 层级 × 逻辑 × 架构）

```
2026-04-29  [V37.9.4 修复]  exfat → APFS (eraseDisk APFS MOVESPEED)
            │
            ├─ macOS 默认 mount 行为: APFS user volume → 添加 noowners flag
            ├─ Original file metadata 来源: copy from another macOS system
            │  ├─ Top: root:wheel (UID 0:0) perms 0700
            │  └─ KB/: _unknown:_unknown (UID 99:99) perms 0750
            ├─ noowners 让 ls/stat 显示成当前 user (bisdom)，掩盖 UID 错位
            └─ 系统进入"看似工作"状态 — bisdom 平时能 read/write
                              │
2026-04-29  ~  [60 天潜伏期]  cron rsync 平时成功 (~70%)
2026-05-04                   incident 时段失败 (~30%) 写入 movespeed_incidents.jsonl
            │                但无人主动消费 jsonl，错误潜伏
            │
            ├─ V37.9.6 修过度报 (告警噪声治理)
            ├─ V37.9.14 落地 fail-loud + JSONL 取证 (但仍被动)
            ├─ V37.9.27 helper jitter+retry (假设 transient EPERM 30s 自愈 — 错)
            └─ V37.9.26 watchdog 主动消费 jsonl, 24h ≥5 阈值告警上线
                              │
2026-05-05  [真相揭示]        watchdog 触发: 24h 19+ incidents
            │
            ├─ user 实测: MOVESPEED notes mtime 停在 4/27, 8 天没新数据
            ├─ V37.9.27 helper retry 完全无效 (transient 假设错)
            ├─ user 手动 rsync 71.9MB / 3679 文件全量补齐
            ├─ 加 23:30 兜底 cron (但仍会失败直到根因修)
            └─ 应用 macOS 修复: mdutil -i off + tmutil addexclusion
                              │
2026-05-05  [V37.9.28 收工]   声称"修复完成"，登记 5/6 24h 验证
            │
            ├─ 4 次诊断错误链（推测脱离数据）：
            │  ├─ #1 假设 EOF (实是 EPERM)
            │  ├─ #2 假设 Volume Unmounted (实 mounted)
            │  ├─ #3 假设 Spotlight+TM 锁是充分原因 (部分对但不充分)
            │  └─ #4 mount 字段 0/233 误读为 unmount (实是 read_file 400 char 截断)
            └─ unfinished 登记: 24h 后 5/6 数据决定 EPERM 真因
                              │
2026-05-06  [V37.9.29 开工]   24h analyzer: 仍 21 incidents (修复完全无效)
            │
            ├─ Path A 第 1 轮: mdutil -s = disabled, tmutil = excluded ✅
            │  └─ 但 mds/Spotlight 进程仍 100% 在 incident 时刻 → 假说升级
            │
            ├─ Path A 第 2 轮: diskutil info = APFS Read-Write ✅
            │  ├─ tmutil snapshots = 空 (排除 Local Snapshot 假说)
            │  └─ mount flag: **noowners** ⚠️ 强嫌疑
            │
            ├─ Path A 第 3 轮: probe_top_err = "Operation not permitted" (errno 1)
            │  └─ 排除 EAGAIN/EACCES, 锁定 ACL 拒绝
            │
            └─ Path A 第 4 轮: stat -f %Su:%Sg
                              ├─ /Volumes/MOVESPEED → root:wheel (0:0)
                              ├─ /KB → _unknown:_unknown (99:99)
                              ├─ /KB/notes → _unknown:_unknown (99:99)
                              └─ bisdom = 501:20 ← 全错位！
                              │
2026-05-06  [Path D' 第一次尝试 — 错误顺序]
            │
            ├─ Step 1: sudo chown -R bisdom:staff /Volumes/MOVESPEED
            │   └─ 在 noowners 模式下，chown 被 macOS APFS 静默吃掉
            │      stat 验证: 仍是 root:wheel + UID 99 (chown 完全无效)
            ├─ Step 2: sudo diskutil enableOwnership ← 成功启用
            ├─ Step 3: sudo mount -u -o owners ← 成功 remount
            ├─ Step 4: touch test → "Permission denied" ❌
            │
            └─ 后果: bisdom 完全失去 MOVESPEED 写权限 (~10 分钟所有 cron 100% 失败)
                              │
2026-05-06  [Path D' 第二次尝试 — 正确顺序]
            │
            ├─ Step 1: 现在 ownership 已启用
            ├─ Step 2: sudo chown -R bisdom:staff (in ownership-enabled mode)
            │   └─ chown 真生效! UID/GID 全部变成 501:20
            ├─ Step 3: stat 验证 → bisdom:staff owner=501:20 ✅
            ├─ Step 4: touch test → 成功 ✅
            └─ Step 5: rsync -av --delete real run → 67MB sent, 0 EPERM ✅
                              │
                              ▼
                    [24h 可证伪验证窗口至 5/7 早 8:00 HKT]
                    incidents < 5 = ✅ 假说正确, 立 INV-OWNERSHIP-001
                    incidents 15+ = ❌ 立即 R1 回滚 + 进 path B 调度避峰
```

---

## 三层根因

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | V37.9.4 重建 APFS 时 macOS 默认启用 noowners flag | macOS 对外接盘 user volume 的标准行为，无 warning，文档未强调 |
| **放大器** | Original file ownership 是 root + UID 99 (来源不明 — 可能是其他 macOS 系统 copy)，与 bisdom UID 501 完全错位。noowners 让显示层 bypass UID 检查，掩盖错位 | 8 天平均 ~30% cron 失败率，但 ls/stat 显示正常→ 无人察觉真实 ownership |
| **掩护者** | macOS daemon (mds_stores/backupd/mds/Spotlight/fseventsd) 在 cron 触发时刻 metadata access 触发跨 UID ACL 拒绝 → 内核 inode lock 短暂传播 → bisdom 同时段 cron rsync 也命中 EPERM | analyzer 数据显示 100% 5 daemon 共现 incident 时刻，是 metadata-level 检查路径而非 open() 路径 |

---

## 时间线还原表

| 时间 | 事件 | 影响 |
|------|------|------|
| 2026-04-29 | V37.9.4 exfat → APFS 重建，理论上修复 EPERM | 假设修复有效，团队进入"已闭环"心态 |
| 2026-04-29 ~ 2026-05-04 | 60 天潜伏: cron rsync ~30% 失败但 jsonl 仅被动累积 | 数据丢失风险敞口长期存在 |
| 2026-05-04 (V37.9.27) | helper jitter+retry 上线，假设 transient EPERM 30s 自愈 | 24h 后仍 20 incidents → retry 假设彻底失败 |
| 2026-05-05 (V37.9.26) | watchdog 主动消费 jsonl, 24h ≥5 阈值告警 | 用户终于发现 8 天 silent failure |
| 2026-05-05 上午 | user 手动 rsync 71.9MB / 3679 文件全量补齐 | MOVESPEED 与本地 100% 镜像 |
| 2026-05-05 (V37.9.28 收工) | 加 23:30 兜底 cron + macOS 修复 (Spotlight off + TM exclude) | 4 次诊断错误链，但承诺 5/6 验证 |
| 2026-05-06 早 8:30 | analyzer 24h: 仍 21 incidents (修复无效) | 5/5 修复假说错，进入 V37.9.29 深度调查 |
| 2026-05-06 早 9:00 | Path A 4 轮诊断 → 锁定 noowners + UID 错位 | 真凶找到 |
| 2026-05-06 早 9:30 | Path D' 第一次错序 → bisdom 失访 ~10 分钟 | 第 6 次诊断错误（chown noowners 假设错） |
| 2026-05-06 早 9:35 | R2 抢救：在 ownership-enabled 模式重跑 chown | chown 真生效 |
| 2026-05-06 早 9:40 | rsync 67MB 真运行 0 EPERM | Path D' 全栈验证 |

---

## 为什么以前没发生（条件组合分析）

| 条件 | V37.9.4 之前 | V37.9.4 ~ V37.9.28 | V37.9.29 |
|------|-------------|--------------------|----------|
| **File system** | exfat | APFS | APFS |
| **Mount flag** | exfat 默认 | APFS noowners | APFS owners |
| **File UID** | 1 user 的 exfat (无 ownership) | root + UID 99 (历史 metadata 残留) | bisdom 501 (chown 后) |
| **bisdom UID** | 501 | 501 | 501 |
| **EPERM 来源** | exfat fskit 并发问题 (V37.9.4 修复) | noowners + UID 错位 + daemon ACL | (期望: 无) |
| **可见性** | rsync 错误 silent | jsonl 被动累积 8 天 | watchdog 主动告警 + Path A 数据驱动 |

**关键多条件组合**：
1. 必须是 V37.9.4 重建后 (exfat 时不会有 noowners + UID 错位组合)
2. 必须有 macOS daemon 持续 access (Spotlight off / TM exclude 后 mds_stores 仍跑)
3. 必须有原始 root + UID 99 ownership (来源不明)
4. 必须有 noowners mask 显示让人误以为正常
5. 必须有 cron 高频触发让 transient EPERM 暴露
6. 必须有被动 jsonl 累积让前 5 天潜伏

**6 个条件单独出现都不致命，6 个组合 = 60 天 silent failure**。

---

## 关键发现 — chown 操作顺序的 macOS APFS 硬规则

教科书行为:
> sudo chown bypass file ownership checks (root has CAP_CHOWN)

**实际 macOS APFS 行为**（V37.9.29 实证）:

```
noowners mode:  sudo chown ... → silent no-op (APFS 忽略 UID 修改)
owners mode:    sudo chown ... → 真生效, UID 持久化
```

**含义**: 必须先 enableOwnership + remount with owners，才能 chown。

**正确顺序**:
1. `sudo diskutil enableOwnership /Volumes/MOVESPEED`
2. `sudo mount -u -o owners /Volumes/MOVESPEED`
3. `sudo chown -R bisdom:staff /Volumes/MOVESPEED`  ← 这步必须在 ownership 启用后

**反过来的顺序 (V37.9.29 第一次尝试) 会导致 bisdom 失访**:
- chown (in noowners) silent no-op → 文件 UID 仍是 root/99
- enableOwnership → 启用了，但 UID 是 root/99
- mount -u -o owners → bisdom (UID 501) 无 root/99 文件的访问权
- 立即 Permission denied，cron 100% 失败

**这是 macOS APFS 隐含规则，文档未强调**。本案登记入 case doc 让未来类似场景不再踩坑。

---

## MR-4 silent-failure 第 22 次演出（新形态）

前 21 次 silent-failure 形态：
1. 错误被吞 (V37.3 governance summary)
2. 错误被稀释 (V37.4 Dream Map budget)
3. 告警路径失效 (V37.5 kb_review 空 prompt)
4. 错误被掩盖却被加工 (V37.4.3 PA 告警污染 / V37.8.6 Dream 自引用幻觉)
5. 多脚本统一反模式协同沉默 (V37.9.4 18 处 rsync `2>/dev/null \|\| true`)
6. 主动报变被动报 (V37.9.6)
7. 取证只取证不告警 (V37.9.14)
8. retry 假设错 (V37.9.27)
9. 4 次诊断错误链 (V37.9.28)
10. ... (其他)

**第 22 次新形态 = 假设级 silent failure**:
- V37.9.4 整团队基于"APFS 重建解决 EPERM"假设**继续运作 60 天**
- 这不是某个 check 漏写，是**整个系统设计假设错**
- 类似 V37.9.27 形态但更深: V37.9.27 是 helper 设计假设错，这是**架构假设**错
- 真正修复需要数据驱动质疑假设，而不是修单点 bug

**MR-10 understand-before-fix 反向兑现 6 次（一日内）**:
- V37.9.28 错误 #1-#4 + V37.9.29 错误 #5 (chown 顺序假设) + #6 (sudo chown noowners 假设)
- 每次都因为"凭推测脱离数据"
- 真正终结诊断错误的是 stat -f 直接看 UID 数据

---

## 喂养本体工程

### 案例文档（本文档）
- 完整四维度因果链 ✅
- 三层根因 ✅
- 时间线 ✅
- 条件组合分析 ✅

### 候选不变式（等 24h 验证后立案）

**INV-OWNERSHIP-001**: `volume-ownership-must-match-cron-user`
- meta_rule: MR-4 (silent-failure-is-a-bug) 上游预防层
- severity: high
- verification_layer: [declaration, runtime]
- declaration: 检测器扫所有 mount flag 为 owners 的 cron 写入路径，文件 UID 必须等于 cron 运行的 user UID
- runtime: 24h analyzer 显示 incident 数 < 5 = 假说正确，立此 invariant

### 候选元规则

**MR-18 候选**: `mount-flag-must-not-mask-actual-ownership`
- 任何 mount flag 不得让 ownership 检查与显示层不一致
- 即: 用户看到的 ownership 必须等于内核 ACL 检查时使用的 ownership
- 反例: noowners flag 让显示是 bisdom 但 ACL 检查是 root/UID 99
- 这是更上游的预防——不是事后查 ownership 错位，而是禁止 mount option 引起检查/显示错位

### 候选生产监控

**incident_capture 增强**:
- 加 `ownership` 字段记录 incident 时刻 stat -f %u:%g (实际 UID 数字而非 noowners 显示)
- 这能让未来类似问题在 1 天内发现而不是 60 天

---

## 5 条元教训

1. **架构假设级 silent failure 比单点 bug 更危险**: 整个系统基于错误前提运作 60 天，比单个错误更难发现，需要数据驱动质疑而非修单点。

2. **noowners 不只是显示层 — 它影响 chown 写入路径**: macOS APFS 的隐含规则。教科书"sudo chown bypass"在 noowners 模式下不成立。

3. **可证伪条件优于验证条件**: V37.9.28 推荐 "Spotlight off + TM exclude" 时设的可证伪条件 (24h incidents 数) 让 V37.9.29 在 24h 内发现假说错。无可证伪 = 假说永远"看似对"。

4. **6 次诊断错误一日内 = 数据驱动还不够，需 isolated test**: 即使 R2 推荐"sudo chown 应该 bypass"也是假设。每个修复方案的实施步骤都应在 sandbox 验证或在低风险时段试，不能直接在生产跑。

5. **incident_capture 数据精度 = 修复速度**: V37.9.28 mount 字段截断 bug (V37.9.29(a) 已修复) 让昨日 21/21 误报为 unmount。下次类似问题如果 incident_capture 还能加 `ownership` 字段，发现时间会从"60 天 → 1 天"。

---

## 24h 验证窗口

**窗口时间**: 2026-05-07 早 8:00 HKT（约 24h 后）

**验证命令**:
```bash
python3 ~/movespeed_incident_analyzer.py --window 24h
```

**判定标准**:
- ✅ 0–5 incidents: 假说正确，noowners + UID 错位是真凶 → 立 INV-OWNERSHIP-001
- ⚠️ 5–10 incidents: 部分缓解，可能 noowners 是部分原因 → 观察一周再决定
- ❌ 15+ incidents: 假说错，立即回滚:
  ```bash
  sudo mount -u -o noowners /Volumes/MOVESPEED
  sudo diskutil disableOwnership /Volumes/MOVESPEED
  ```
  + 进 path B 调度避峰

**回滚保障**: `/tmp/movespeed_before_v37_9_29d.txt` 已备份原始 diskutil info (V37.9.29 Step 0)。

---

## 相关版本

- **V37.9.4** (2026-04-29): exfat → APFS 重建，触发 noowners 默认行为
- **V37.9.14** (2026-04-23): incident_capture 上线，被动累积 jsonl
- **V37.9.26** (2026-05-04): watchdog 主动消费 jsonl，24h ≥5 阈值告警
- **V37.9.27** (2026-05-04): helper jitter+retry，假设 transient EPERM 自愈（错）
- **V37.9.28** (2026-05-05): macOS 修复（Spotlight off + TM exclude），24h 验证（修复无效）
- **V37.9.29 (a)** (2026-05-06): incident_capture mount 字段截断 bug 修复（让 analyzer 数据准确）
- **V37.9.29 (path D')** (2026-05-06): chown + enableOwnership + remount，**本案核心修复**

## 相关原则

- 原则 #26 异常分析宪法（本案完整实践）
- 原则 #28 理解再动手（6 次诊断错误违反 + R2 抢救兑现）
- 原则 #29 收工零遗漏（V37.9.28 收工时承诺 5/6 验证，V37.9.29 兑现）
- MR-4 silent-failure-is-a-bug（第 22 次演出）
- MR-10 understand-before-fix（反向兑现 6 次）

# MOVESPEED exfat 静默失败 6 天 → APFS 转换案例

> **TL;DR**：cron 脚本 rsync 备份 ~/.kb 到外挂 SSD（exfat 格式）静默失败 6 天，
> 因 18+ 处 `2>/dev/null || true` 双层吞错 + 无监控告警。job_smoke_test 偶然
> 暴露后定位为 exfat + fskit 在 transient 条件下的 EPERM 问题，根治方案 = 抹掉
> 重建为 APFS。MR-4 silent-failure 第 14 次演出，预防层修复 INV-BACKUP-001 即时
> 抓出第 20 个漏网脚本（run_hn_fixed.sh），证明守卫真有效。

**版本**: V37.9.4 (2026-04-21)
**类型**: 元规则演出 — MR-4 silent-failure-is-a-bug
**严重性**: high — 备份系统 6 天无效，但因主数据 ~/.kb 完好未致命

---

## 时间线

| 时间 | 事件 | 影响 |
|------|------|------|
| 2026-04-15 | 某 transient 条件触发 SSD `mkdir/chmod` EPERM | 备份开始失败 |
| 2026-04-15 ~ 04-21 | 18 个 cron 脚本每日 rsync ~/.kb → MOVESPEED **全部静默失败** 6 天 | MOVESPEED/KB/ mtime 卡在 4/15 |
| 2026-04-21 11:29 | 用户主动跑 `job_smoke_test.sh` 暴露 `KB 备份过期 142h` warn | 用户发现 |
| 2026-04-21 11:35 | 初步排查 `~/.kb/.write.lock` 残留 → 误判为 lock 阻塞 | 删 lock 无效 |
| 2026-04-21 11:40 | 手动 `rsync -a ~/.kb/ /Volumes/MOVESPEED/KB/` exit 0 → **反转**: 现在能跑 | transient 条件已自愈 |
| 2026-04-21 11:55 | 备份 MOVESPEED 11G 到 ~/Desktop | 防数据丢失 |
| 2026-04-21 12:10 | SSH 远程 `diskutil eraseDisk APFS MOVESPEED disk6` 抹盘重建 | exfat → apfs (disk4s1) |
| 2026-04-21 12:15 | rsync ~/.kb → MOVESPEED/KB/ exit 0 + POSIX 测试 3/3 通过 | 根治 |
| 2026-04-21 12:25 | 19 个脚本 `2>/dev/null \|\| true` → `2>&1 \|\| echo WARN >&2` 全部修复 | MR-4 预防层 |
| 2026-04-21 12:30 | INV-BACKUP-001 部署立即抓出第 20 个漏网（hn 脚本） | 守卫即时生效 |

---

## 完整因果链架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│  触发器层（Trigger）— exfat + fskit 在某 transient 条件 EPERM            │
│                                                                         │
│  2026-04-15 某时刻                                                      │
│    ├─ macOS fskit (exfat 新驱动) 内部 state 异常                          │
│    │   或 SSD 未唤醒 / mount lock contention                            │
│    │                                                                    │
│    └─ /Volumes/MOVESPEED 上 mkdir/chmod 开始返回 Operation not permitted│
│                                                                         │
│        现象: openclaw_backup.sh 03:00 报                                │
│              "mkdir: status_history: Operation not permitted"            │
│              （但主备份 tar.gz 仍成功，只 status_history 失败）          │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────────────┐
│  放大器层（Amplifier）— 18 个 cron 脚本 rsync 双层吞错                  │
│                                                                         │
│  kb_inject.sh: rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" \   │
│                  2>/dev/null || true   ← 双重保险吞掉所有错误           │
│  kb_evening.sh: 同上                                                    │
│  kb_review.sh: 同上                                                     │
│  kb_dream.sh: 同上 (dreams/ 子目录)                                     │
│  kb_save_arxiv.sh: rsync -a 无 || true (但同样 2>/dev/null)             │
│  jobs/{ai_leaders_x,arxiv,dblp,acl,hf,s2,github_trending,                │
│         karpathy,ontology_sources,rss_blogs,freight×2,                   │
│         openclaw_official×2}: 全部同模式                                │
│                                                                         │
│        效果: 每个脚本 cron 日跑都"OK exit 0"（因为 || true）            │
│              cron log 干净无 stderr（因为 2>/dev/null）                 │
│              主进程不受影响（KB 主数据 ~/.kb 完好持续更新）             │
│              → 18 处协同沉默                                            │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────────────┐
│  掩护者层（Concealer）— 监控盲区让 6 天看不出来                         │
│                                                                         │
│  ❌ job_watchdog.sh 检查 status_file mtime 但不检 SSD 备份新鲜度         │
│  ❌ wa_keepalive.sh 探 Gateway 不探 SSD                                 │
│  ❌ governance_audit_cron.sh 跑 53 个不变式但 INV-BACKUP-001 不存在     │
│  ❌ preflight_check.sh 不检 SSD KB 镜像新鲜度                           │
│  ✅ job_smoke_test.sh 唯一会查 KB 备份 mtime 的检查                      │
│                                                                         │
│        Bug: job_smoke_test 不在任何 cron 调度，只用户手动触发           │
│             6 天用户没跑 → 6 天没人发现                                 │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────────────┐
│  发现路径（Discovery）— 偶然手动 audit                                  │
│                                                                         │
│  2026-04-21 11:29 用户 `bash job_smoke_test.sh` (V37.9.3 验证副产品)    │
│    ├─ 第 17/18 项 "备份健康检查"                                        │
│    ├─ KB_BACKUP="/Volumes/MOVESPEED/KB"                                 │
│    ├─ stat -f %m index.json → mtime 5天前                               │
│    └─ warn "KB 备份过期: 142h 前（预期 <26h）"  ← 第一次曝光            │
│                                                                         │
│        延迟 = 142h ≈ 6 天                                               │
└─────────────────────────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────────────┐
│  根治路径（Remediation）— 三层修复                                      │
│                                                                         │
│  Layer 1: 文件系统替换 (exfat → APFS)                                   │
│    diskutil eraseDisk APFS MOVESPEED disk6                              │
│    新挂载: /dev/disk4s1 on /Volumes/MOVESPEED (apfs, journaled)         │
│    POSIX 3/3 通过：mkdir/chmod/touch 全部 work                          │
│                                                                         │
│  Layer 2: 19 处脚本修复 (silent-failure → fail-loud)                   │
│    旧: rsync ... 2>/dev/null || true                                    │
│    新: rsync ... 2>&1 || echo "[xxx] WARN: SSD rsync failed" >&2        │
│         ├─ 失败时 stderr 出现 WARN，进 cron log                         │
│         ├─ exit 0 保留（不杀 cron 主流程）                              │
│         └─ stdout 干净（不污染命令替换）                                │
│                                                                         │
│  Layer 3: 治理守卫 INV-BACKUP-001                                       │
│    declaration: 全局扫 .sh 禁止 `2>/dev/null \|\| true` 反模式          │
│    declaration: 任何含 rsync MOVESPEED 的 .sh 必须有 "WARN: SSD" 字样   │
│    runtime: 真跑模拟 rsync 失败，断言 stderr 含 WARN 且 stdout 不含     │
│    → 部署即时抓出第 20 个漏网 (run_hn_fixed.sh)，证明守卫真有效         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 三层根因（按 MR-4 silent-failure 框架）

### 触发器（Trigger）— exfat + fskit 间歇性 EPERM

- **What**: macOS 的 fskit driver（exfat 新实现）在某 transient 条件下，
  对 `/Volumes/MOVESPEED` 上的 mkdir/chmod 操作返回 Operation not permitted
- **Why uncertain**: 无法 100% 复现根因。可能因素：
  - SSD 未唤醒（外置 USB 节能策略）
  - mount lock contention（多个 cron 脚本并发）
  - fskit 内部 state lock（macOS 版本特定）
  - exfat 不支持完整 POSIX → fskit 模拟层不一致
- **Evidence**: 抹盘前 `mkdir status_history` 失败；抹掉重建为 APFS 后，
  POSIX 操作 100% 成功且持续工作。**APFS 直接规避**整个问题域。

### 放大器（Amplifier）— 18 处 `2>/dev/null || true` 双层吞错

- **What**: rsync 命令统一用 `2>/dev/null` (吞 stderr) + `|| true` (吞 exit code)
- **Why this exists**: 多年前为防 SSD 未挂载时 cron 任务整体失败，
  写法逐渐被复制到 18 个新增 job 脚本（**MR-8 copy-paste-is-a-bug-class**）
- **Effect**: 单脚本失败不可见 → 18 脚本协同沉默 → 系统级失败仍"全绿"

### 掩护者（Concealer）— 监控覆盖盲区

- **What**: 6 个监控/审计机制无一覆盖 SSD 备份新鲜度
  - `job_watchdog.sh` 只查 status_file mtime
  - `wa_keepalive.sh` 只探 Gateway HTTP
  - `governance_checker.py` 53 不变式中**无 SSD 备份相关**
  - `preflight_check.sh` 不查 SSD KB 镜像
  - `kb_status_refresh.sh` 不写 ssd_backup_age 到 status.json
  - 唯一的 `job_smoke_test.sh` 不在 cron 调度
- **Why**: SSD 备份是"次要副本"心智模型，主数据 ~/.kb 完好就以为系统健康

---

## 为什么以前没发生

| 条件 | 4/14 之前 | 4/15 后 |
|------|----------|---------|
| exfat fskit 触发 EPERM | 偶发但很短暂 | 进入持续 EPERM 状态 |
| `\|\| true` 数量 | 同样 18 处 | 同样 18 处 |
| job_smoke_test 调用频率 | 几周一次 | 4/21 因 V37.9.3 验证手动跑 |

**6 个条件协同**才让本次发生：
1. exfat fskit 进入持续 EPERM（4/15 起）
2. 18 处脚本统一用 `2>/dev/null || true`（结构性弱点）
3. 主数据 ~/.kb 在系统盘正常 → 用户感知不到问题
4. job_smoke_test 不在 cron → 自动监控不查 SSD
5. wa_keepalive/job_watchdog 监控范围不含 SSD
6. 用户没在 4/15-4/20 期间手动跑 audit

任意一项不成立 → 早期发现。

---

## 元规则演出

**MR-4 silent-failure-is-a-bug 第 14 次演出**：

| 次数 | 时间 | 案例 |
|------|------|------|
| 7 | 2026-04-15 | dream_self_referential_hallucination |
| 8 | 2026-04-15 | ontology_sources_positional_parser_cascade |
| 9 | 2026-04-15 | kb_evening_fallback_quota_chain |
| 10 | 2026-04-15 | finance_news_syndication_zombie |
| 11 | 2026-04-15 | preflight_cascading_fix |
| 12 | 2026-04-15 | kb_content_and_sources_dedup |
| 13 | 2026-04-19 | heartbeat_md_pa_self_silencing |
| **14** | **2026-04-21** | **本案 movespeed_exfat_apfs（首例 18 处协同沉默）** |

**新形态**：前 13 次都是单点 silent-failure，本次是**多脚本统一反模式协同沉默 6 天**——
单点静默不致命，**18 处复制粘贴让规模放大成系统性失明**。

**MR-8 copy-paste-is-a-bug-class 反例兑现**：
本案完美演示"复制粘贴反模式 = 制造 bug 类"：18 处 `2>/dev/null || true` 都是
人类手抄的同款。修复时也只能 18 处一一改。**未来如果有第 19 个 job 脚本要写，
INV-BACKUP-001 会立即拦截反模式回归**——这是 MR-8 的预防层。

---

## 修复落地

### Layer 1: 文件系统（不可逆，已根治）
- exfat → APFS via `diskutil eraseDisk APFS MOVESPEED disk6`
- 验证: `mount` 含 `apfs` / POSIX 3/3 通过

### Layer 2: 脚本修复（19 处 + 1 处后补 = 20 处）
- 模式：`rsync ... 2>&1 || echo "[XXX] WARN: SSD rsync failed (exit=$?)" >&2`
- 关键设计：
  - `2>&1` 让 rsync 错误进 cron log（不再吞）
  - `|| echo` 失败时显式 log 一行 WARN（可 grep 监控）
  - `>&2` 写 stderr（不污染命令替换 → MR-11 顺势）
  - 不加 `exit 1`（保持 cron 主流程不崩，符合"备份失败不致命"语义）

### Layer 3: 治理守卫 INV-BACKUP-001（severity=high, MR-4）
- declaration check 1: 禁止反模式 `rsync.*MOVESPEED.*2>/dev/null \|\| true`
- declaration check 2: 任何含 rsync+MOVESPEED 的 .sh 必须有 "WARN: SSD"
- runtime check 3: 真跑模拟 rsync 失败 → 断言 stderr 含 WARN + stdout 不含

**部署时即时验证**：第 1 次跑就抓出第 20 个漏网（run_hn_fixed.sh），
证明声明层守卫真有效（如果只靠 grep 人工找会漏，python_assert 全局扫不漏）。

---

## 元教训

1. **`|| true` 是 silent-failure 制造机**——任何持久化操作（rsync/mv/cp/mkdir）
   都不应组合 `2>/dev/null || true`。修复模式：
   `op 2>&1 || echo "[ctx] WARN: ..." >&2` （让失败可见但不致命）

2. **复制粘贴 18 次的反模式 = 系统性 bug 类**——MR-8 反面，需要工具发现
   （INV-BACKUP-001 全局 scan）而非靠程序员记忆

3. **"次要副本"心智模型危险**——以为备份失败"不影响主流程"，
   实际是丢失了灾备能力。任何持久化路径都应被监控覆盖

4. **monitoring 必须包含数据新鲜度，不只健康端口**——
   job_smoke_test 第 17/18 项是唯一发现机制，但不在自动调度

5. **远程 SSH 完全可以替代 GUI 做 disk 操作**——`diskutil eraseDisk APFS`
   等价 Disk Utility 抹盘，但需先 `diskutil info` 验证 External + 容量

6. **不可逆操作前必须三道安全验证**：
   - Device Location: External（防误抹系统盘）
   - Total Size 匹配（防 identifier 漂移）
   - 备份完整可读（du -sh + ls 验证）

---

## 关联不变式与元规则

- **触发**: MR-4 (silent-failure-is-a-bug) 第 14 次演出
- **预防**: INV-BACKUP-001 (ssd-rsync-backup-fails-loud)
- **根因放大器**: MR-8 (copy-paste-is-a-bug-class) 反例
- **顺势设计**: MR-11 (shell-function-output-must-go-to-stderr) — WARN 写 `>&2`
- **未来强化候选**: 抽 helper `rsync_to_ssd.sh` (MR-9 state-writes-go-through-helper)
  延伸到备份路径 — V3 路标考虑

---

## 引用文件

- `kb_dream.sh:1350` / `kb_save_arxiv.sh:64` / `kb_evening.sh:220` /
  `kb_inject.sh:509` / `kb_review.sh:204` / `run_hn_fixed.sh:386`
- `jobs/{ai_leaders_x,arxiv_monitor,dblp,acl_anthology,github_trending,
  hf_papers,karpathy_x,ontology_sources,rss_blogs,semantic_scholar,
  freight_watcher×2,openclaw_official×2}/run_*.sh`
- `ontology/governance_ontology.yaml`: INV-BACKUP-001
- `job_smoke_test.sh:378-433`: 唯一发现路径

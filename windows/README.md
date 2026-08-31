# Windows E 盘每日镜像（Mac Mini 项目数据 → E:\openclaw-model-bridge）

> V37.9.335。Mac Mini 上本项目生成的全部数据，每天 05:00 由 Windows 任务计划程序
> 经 WSL + rsync 增量拉取到 `E:\openclaw-model-bridge`。Mac 端零改动（复用已开启的 SSH）。

## 架构

- **方向 = Windows 拉取**：Mac Mini 已开 SSH；反向推送需要 Windows 开服务 + Mac 存 Windows 凭据，脆且重
- **增量 = rsync**：备份归档 7×1GB 级，全量每日拷不现实；rsync 只传变化部分
- **05:00 = Mac 端干净窗口**：03:00 备份 / 03:30 kb_embed / 04:00 SSD 同步已收尾，06:00 radar 未开始
- **防灾难镜像**：每个 `--delete` 配 `--max-delete` 保险丝——Mac 端事故性清空不会被镜像到 E 盘（rc=25 中止并告警，V37.9.324 血案教训）
- **带宽现实（2026-08-30 实测定性）**：办公室网络封锁对外 UDP（dig@223.5.5.5 超时 + Mac 端 `zerotier-cli info` = TUNNELED + tcpFallbackActive:true 三重实证）→ 家↔办公室的 ZeroTier 只能走 TCP 中继，实测 ~36KB/s，家庭侧无解。ssd_backup（每日全新 1GB tar.gz，文件名含日期 rsync 无法增量）**每日只拉最新一份**——单大文件实测 ~22 分钟/1GB（清晨管道空闲，比深夜几千小文件快 ~20 倍；曾按 8h 误估降为每周日，2026-08-31 依实测恢复每日，用户决策）；整目录镜像被禁止（--delete 会删掉超出 Mac 7 天轮转的历史档）。其余模块每日增量几十 MB。若未来想提速可选 Tailscale（DERP 香港 TCP 443 中继，通常快一个量级；装好后把其 IP 加进 `OPENCLAW_SYNC_HOSTS` 即可，架构不变）

## 数据清单

| E 盘子目录 | Mac 源 | 说明 |
|---|---|---|
| `kb\` | `~/.kb/` | KB 全量（notes/sources/dreams/deep_dives/status.json/audit.jsonl…；排除三个机器衍生物见下） |
| `home\` | `~` 顶层 `*.log` + `proxy_stats.json` + `.cron_canary` + `.crontab_backups\` | 运行日志与状态 |
| `openclaw\logs\` `jobs\` `media\` | `~/.openclaw/` 对应子目录 | 部署日志 / job 缓存 / WhatsApp 媒体 |
| `movespeed_backup\` | `/Volumes/MOVESPEED/openclaw_backup/` | Gateway 全量备份 tar.gz，**每日拉取最新一份**（单文件 ~22 分钟实测），**全部历史档长期保留**（+365GB/年，所有者接受；⚠️ 含凭据，E 盘访问控制自行负责） |
| `_sync\` | — | 同步日志 `sync.log` + `last_sync.json` + `upstream\`（脚本更新通道） |

刻意不拉：代码仓库（家在 GitHub）/ `/Volumes/MOVESPEED/KB/`（`~/.kb` 的副本，拉原件不拉复制品）/ `~/.openclaw` 顶层凭据活文件（已在备份 tar.gz 内）/ `~/.notify_queue`（瞬态）/ **`.kb` 内三个机器衍生物**：`dreams/.map_cache/`（dream Map 日期前缀缓存，每天生灭 ~2400 文件——镜像它会让 kb 每天撞删除保险丝，2026-08-31 首个 05:00 运行实证 2144 待删除）+ `text_index/`、`mm_index/`（向量索引，每晚重写 ~100MB 且 `--reindex` 可完全再生，中继+drvfs 下 rsync 校验易翻车）——三者零归档价值，灾难恢复时在还原的 notes/sources/media 上重跑 kb_embed / mm_index 即可重建。

## 一次性安装（Windows 上执行）

### 第 1 步：确认 WSL

管理员 PowerShell：

```powershell
wsl --status
```

若报错或未安装（需重启一次）：

```powershell
wsl --install
```

### 第 2 步：WSL 内准备 rsync + SSH key

打开 WSL（开始菜单 Ubuntu，或 PowerShell 输 `wsl`），逐块执行。装 rsync（多数发行版自带，缺则装）：

```bash
sudo apt-get update && sudo apt-get install -y rsync
```

生成 key（已有则跳过，一路回车）：

```bash
ssh-keygen -t ed25519
```

把公钥装到 Mac Mini（会要一次 Mac 密码；办公室内网用 10.102.0.23，家里 ZeroTier 用 10.120.230.23，哪个通用哪个）：

```bash
ssh-copy-id bisdom@10.102.0.23
```

验证免密（应直接输出 ok 不问密码）：

```bash
ssh -o BatchMode=yes bisdom@10.102.0.23 echo ok
```

### 第 3 步：取脚本 + 首跑

仍在 WSL 内：

```bash
mkdir -p /mnt/e/openclaw-model-bridge/_sync
```

```bash
scp bisdom@10.102.0.23:openclaw-model-bridge/windows/sync_from_macmini.sh /mnt/e/openclaw-model-bridge/_sync/
```

首跑（首次全量；同一内网几分钟到十几分钟；家↔办公室走 ZeroTier TCP 中继时 ~36KB/s，视 kb/media 体量可能磨数小时到一天以上，保持该 WSL 窗口开着别关，之后每天只有增量就快了）：

```bash
bash /mnt/e/openclaw-model-bridge/_sync/sync_from_macmini.sh
```

预期结尾输出 `同步完成: 全部模块 OK`。然后在 Windows 资源管理器确认 `E:\openclaw-model-bridge\` 下出现 kb / home / openclaw / movespeed_backup 子目录。

### 第 4 步：注册每天 05:00 任务

PowerShell（当前用户身份即可）。时限设 12 小时：每日备份档拉取实测 20-40 分钟，12 小时是网络极差日不被 Windows 中途掐掉的富余保险：

```powershell
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-e bash /mnt/e/openclaw-model-bridge/_sync/sync_from_macmini.sh"
$trigger = New-ScheduledTaskTrigger -Daily -At 05:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Register-ScheduledTask -TaskName "OpenClaw-MacMini-Sync" -Action $action -Trigger $trigger -Settings $settings
```

立即手动触发一次验证任务通道本身：

```powershell
Start-ScheduledTask -TaskName "OpenClaw-MacMini-Sync"
```

约一两分钟后核对结果（LastTaskResult 应为 0）：

```powershell
Get-ScheduledTaskInfo -TaskName "OpenClaw-MacMini-Sync"
```

```powershell
wsl -e tail -5 /mnt/e/openclaw-model-bridge/_sync/sync.log
```

## 日常观测

- 每次运行追加 `E:\openclaw-model-bridge\_sync\sync.log`；机器可读结果在 `last_sync.json`（`"ok":true/false` + 每模块 rc）
- 成功时会回写 Mac `~/.kb/last_windows_sync.json` 时间戳（best-effort，供未来 Mac 端监控拉取死活）
- rc=24 = 活系统文件传输中消失，良性；rc=25 = 删除保险丝触发，**先人工核实 Mac 端是否真的合法删了大量文件**再处理
- 单实例锁：`/tmp/openclaw_sync.lock`——上一次同步还没跑完时新触发会直接退出（日志有说明行），不会互踩

## 已知边界（诚实登记）

1. **Windows 05:00 需处于开机状态**（锁屏可以，关机不行；睡眠依赖唤醒策略——`-StartWhenAvailable` 让错过的任务在下次开机/唤醒后尽快补跑）
2. 任务默认只在**当前用户已登录**（含锁屏）时运行；若习惯注销/重启后不登录，需换 S4U 方案（找 Claude 加）
3. 备份 tar.gz 含 Gateway 凭据（WhatsApp auth 等）——E 盘落盘后访问控制/加密（如 BitLocker）由所有者决定
4. E 盘 `movespeed_backup\` 每日拉取最新一份，**全部历史档长期保留不删除**（用户决策 2026-08-30/31；每日 +1GB ≈ +365GB/年，E 盘空间由所有者规划）；Mac 端 SSD 上仍是每日 7 天轮转。拉取按实测约 20-40 分钟（05:00 后台运行，单实例锁防重叠）
5. 脚本更新通道：每次同步会把仓库 `windows/` 最新版拉到 `_sync\upstream\`；**活跃副本是 `_sync\sync_from_macmini.sh`**，upstream 出新版本时手动覆盖一次（`cp /mnt/e/openclaw-model-bridge/_sync/upstream/sync_from_macmini.sh /mnt/e/openclaw-model-bridge/_sync/`），次日生效

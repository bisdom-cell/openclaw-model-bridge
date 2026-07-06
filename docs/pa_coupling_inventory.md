# PA 耦合机器化盘点（PA Coupling Inventory）

> **任务**：`docs/charter_execution_plan_20260705.md` §1 **H1-B / B1**（P0 · 2026 Q3）。
> **目的**：机器化盘点"个人 PA（Wei）配置面"，把两次外部评审共同点名的短板（个人 PA 耦合深 / 可迁移性不足）**从声明式承认升级为逐项、可分级、可追踪的清单**，作为 B2（config 化 backlog）与 B3（第一批 config 化落地）的依据。
> **哲学定位（技术纲领 §4.3）**：PA 耦合**不是"待清除的债"，是"须管理的边界"**。边界内（个人配置 / 中文 prompt / macOS 特定）→ **明确标注 + config 化**；边界外（引擎 / bench / 方法论）→ **保证零 PA 依赖**。本清单同时验证两侧。
> **确立版本**：V37.9.249（2026-07-06） | **盘点基线**：VERSION 0.37.9.101，files=286，env=43（纲领 §0 快照）。

---

## §0 执行摘要

**一句话结论**：系统的可迁移性远好于"评审印象"——约 **80% 的耦合已 env 参数化**（带占位默认值），真正的残余耦合集中在 **4 个可枚举点**，其中最高 blast-radius 的是 **launchd 进程管理**（第二实例的真门槛），最干净可立即修的是 **3 个 KB 脚本的 `/Users/bisdom/.kb` 默认值**（与 15+ job 脚本的 `$HOME/.kb` 不一致 = 一物一形违反 + 可迁移性 bug）。

**关键正面证据**：**边界外（引擎 / examples / bench）零 PA 泄漏**（机器扫描确认，见 §2）——可抽取的 `ontology-engine` 与三个 demo（`minimal_runtime` / `minimal_consumer` / `external_dogfood`）已经是 PA-free。可迁移性的底座已就位，缺的是 runtime 侧的第二实例实证（H1-C）。

**分级总览**（详见 §3）：

| 等级 | 定义 | 命中 |
|------|------|------|
| 🔴 高 | 阻塞第二实例 / 影响非-Mac 核心 runtime | C1 launchd 进程管理 |
| 🟡 中 | 拖累非-HK/非中文实例，但核心可跑 | A1 KB 路径默认（本次已修）· D2 内容 prompt 中文 · D3 TZ=HKT |
| 🟢 低 | 表面 / env 可覆盖 / 仅 ops / 仅 test | A2 diagnose 路径 · C2 /opt/homebrew PATH · D1 SOUL 身份（已 config 部署） |
| ✅ 已 config | 已参数化，无需动作 | B 号码/推送目标 · 40 个 Python `expanduser` 路径 · C3 md5 fallback |

---

## §1 盘点方法（可复现）

机器扫描，非人工印象。四个耦合类别各自的扫描命令（在仓库根运行，排除 `docs/` 与 `test_*` 以聚焦运行时代码）：

```
grep -rnE "/Users/|bisdom" --include=*.py --include=*.sh . | grep -vE "docs/|test_"
grep -rnE "\+852[0-9]{6,}|WEIXIN_TARGET|OPENCLAW_PHONE" --include=*.py --include=*.sh .
grep -rlE "launchctl|launchd|\.plist|/opt/homebrew|osascript|diskutil|sw_vers" --include=*.py --include=*.sh .
grep -rlE "Asia/Hong_Kong|严禁|你是|要点|摘要" --include=*.py --include=*.sh . | grep -vE "docs/|test_"
```

覆盖四类：**① 个人文件系统路径**（personal paths）/ **② 占位号码与推送目标**（placeholder numbers）/ **③ macOS 与进程管理假设**（macOS assumptions）/ **④ 中文 prompt 与 locale**。

---

## §2 边界完整性（先验证正面）

**边界外零 PA 依赖 — 已达成（纲领 §4.3 要求）**：对 `examples/`（三个 demo）+ ontology 引擎核心（`engine.py` / `governance_checker.py` / `tool_ontology.yaml`）扫描 `/Users|bisdom|+852|Wei|三方宪法` → **零命中**。可抽取产品（引擎 + bench + demo）本就 PA-free，`external_dogfood` 甚至在仓库外用 `import ontology_engine` 消费 PyPI wheel。**这是"可迁移性"批评的一半反驳：抽包路径已经干净。**

**已有扫描器（信用登记 + 覆盖缺口）**：

| 扫描器 | 守护对象 | 与本清单的关系 |
|--------|----------|----------------|
| `cross_os_quirk_scanner.py` | bash/OS quirk **bug**（`cmd&&\|\|`、`grep\|head`、awk LC_ALL、zsh、errtrace、未括号 CJK 变量） | 守 OS-quirk 崩溃，**不守 config 默认值耦合** |
| `cross_env_path_scanner.py` | dev vs Mac Mini canonical 路径漂移 | 守 `_resolve_*_path` 类，不守脚本 KB 默认值 |
| `path_consistency_scanner.py` | 路径一致性 | 同上 |
| `cron_monitor_scanner.py` / `governance_runtime_isolation_scanner.py` | 监控脚本契约 / test-pollutes-production | 与耦合正交 |

**缺口**：目前**无扫描器守护"PA-specific 硬编码默认值"**（个人路径/号码作为 fallback 默认值）。这正是 B3 done-criteria 的"新增 PA-specific 硬编码有扫描器拦截"。本次先用**定向源码守卫**（`test_pa_coupling_kb_paths.py`）封住已修的 3 个点，扫描器框架化留 B3（日落法：不为 B1 造一整个新扫描器机器）。

---

## §3 耦合清单（逐项 + blast-radius + 证据）

### A. 个人文件系统路径

| ID | 命中 | 证据 | 等级 | 说明 |
|----|------|------|------|------|
| **A1** | 3 个 KB 脚本默认 `${KB_BASE:-/Users/bisdom/.kb}` | `kb_write.sh:3` · `kb_search.sh:13` · `kb_inject.sh:12` | 🟡 中→**本次已修** | env 可覆盖，但**默认值是 Mac 个人路径**，且与 **15+ job 脚本的 `${KB_BASE:-$HOME/.kb}`** 不一致（一物一形违反）。非-bisdom 用户不设 KB_BASE → 落到坏路径。**§5 已改为 `$HOME/.kb`**（Mac Mini 上 `$HOME=/Users/bisdom` → 行为完全不变）。 |
| **A2** | `diagnose.sh` 硬编码 `/Users/bisdom/.openclaw/openclaw.json` | `diagnose.sh:77` | 🟢 低 | ops 手动诊断工具，Mac 特定。config 化：`${OPENCLAW_HOME:-$HOME/.openclaw}`。→ B2 backlog。 |
| **A3** | 测试 fixture 用 `/Users/bisdom/...` 路径 | `test_tool_proxy.py`、`test_movespeed_*` 等 | 🟢 低 | 测试数据，无运行时影响。表征 macOS 路径假设但不阻塞迁移。**仅登记**。 |
| ✅ | 40 个 Python 文件用 `os.path.expanduser("~/.kb")` | `kb_trend.py:25`、`memory_plane.py`、`slo_dashboard.py` 等 | ✅ 已 config | 可移植（`~` 在任何 OS 解析到 `$HOME`）。**系统主体路径解析已可移植**。 |

### B. 占位号码 / 推送目标 — ✅ 已 config（无动作）

| 命中 | 证据 | 说明 |
|------|------|------|
| WhatsApp 号码 `${OPENCLAW_PHONE:-+85200000000}` | `notify.sh:47`、`kb_trend.py:28`、`job_watchdog.sh:45`、多个 job | env 驱动 + **占位默认值是假号码**（`+85200000000`）。blast-radius = 无。 |
| 微信目标 `${WEIXIN_TARGET:-}` | `notify.sh:48` | env 驱动，空默认 = 微信分支安全跳过。 |
| LLM 端点 `${REMOTE_BASE_URL}` / `${QWEN_BASE_URL}` | `providers.py`（V37.9.211）、`deploy/install_openclaw_macmini.sh:43` | env 驱动，第二实例指向自己端点即可。默认是用户端点（公开域名，非机密）。 |
| 私有 IP（10.102.0.23 等） | **仅在 `CLAUDE.md` ssh 连接段（doc），代码零命中** | 无代码耦合。 |

**结论**：推送/号码/端点是**参数化设计的正面样板**——占位默认值 + env 覆盖。第二实例只需填自己的 `.env_shared`。

### C. macOS / 进程管理假设

| ID | 命中 | 证据 | 等级 | 说明 |
|----|------|------|------|------|
| **C1** | launchd 进程管理 | `restart.sh`、`job_watchdog.sh`、`wa_keepalive.sh`、`cron_doctor.sh`、`ontology/convergence.py`（`_apply_services_launchctl_bootstrap`） | 🔴 高 | Linux 用 systemd/nohup。`restart.sh` 已有 nohup fallback。**这是第二实例（H1-C）的真门槛**——但 `minimal_runtime`（可移植核心 demo）不依赖 launchd，所以 config 化可增量：先跑无 launchd 的最小闭环。 |
| **C2** | `/opt/homebrew/bin` PATH 前置 | 59 个脚本首行 `export PATH="/opt/homebrew/bin:..."` | 🟢 低 | Linux 上是**无害的附加 PATH 项**（目录不存在，PATH 回落到系统路径）。宽表面但零 blast。config 化：共享 PATH-init 片段 → B2 低优。 |
| **C3** | `md5 -q`（BSD） | `preflight_check.sh:261`、`auto_deploy.sh:465`、`kb_dream.sh:496` | ✅ 已 config | **已有 `\|\| md5sum` 跨平台 fallback**（关键路径）。可移植。 |
| **C4** | `diskutil`/`sw_vers`/`osascript`/外挂 SSD | `movespeed_incident_*`、`install_openclaw_macmini.sh` | 🟢 低（本质 Mac） | MOVESPEED 外挂 SSD 监控 + Mac Mini 安装器 = **本质 Mac 特定 job**，不在可移植核心范围。已有 `\|\| uname -a` 类 Linux 降级。 |

### D. 中文 prompt / locale

| ID | 命中 | 证据 | 等级 | 说明 |
|----|------|------|------|------|
| **D1** | PA 身份 prompt | `SOUL.md`（Wei 身份 / 三方宪法 / 个人焦点 / 号码 / backend）、`ops_soul.md` | 🟢 低（已 config 部署） | 部署为 `~/.openclaw/SOUL.md` = **已是外部 config 文件（非代码）**。身份耦合高，但改文件即可，不碰代码。**边界内——标注管理，不清除**（纲领：杀 PA = 杀数据复利）。 |
| **D2** | 内容 job 的中文 LLM prompt | ~71 个 job/collector 文件（arxiv/hf_papers/finance_news/dream/observer…） | 🟡 中 | 用户是中文读者 → 中文输出是**特性非 bug**。第二实例若也是中文用户则原样可用；英文用户实例才需 prompt-template/locale 层（中等工程量）。**边界内——低优 config 化**。 |
| **D3** | `TZ=Asia/Hong_Kong` | 36 个运行时文件（`job_watchdog.sh`、各 job 的 date 命令） | 🟡 中 | V37.9.241 刻意统一为 HKT（一物一形，用户时区）。locale 耦合。config 化：`${SYSTEM_TZ:-Asia/Hong_Kong}`（36 处宽表面，低风险）→ B2 Q4。 |

---

## §4 config 化 backlog（B2 · 分级）

> 每条：**config 化方式 / 风险 / blast-radius / 是否本季度做**。排序 = 价值密度（低风险高价值优先）。

| # | 项 | config 化方式 | 风险 | blast | 本季度？ |
|---|----|--------------|------|-------|----------|
| 1 | **A1 KB 脚本默认路径** | `/Users/bisdom/.kb` → `$HOME/.kb`（对齐 15+ job 脚本） | 极低（Mac 上行为不变） | 🟡→🟢 | ✅ **本次已落地**（§5） |
| 2 | A2 diagnose 路径 | 硬编码 → `${OPENCLAW_HOME:-$HOME/.openclaw}` | 极低（ops 工具） | 🟢 | Q4（低优，与 D3 一批） |
| 3 | D3 TZ | `TZ=Asia/Hong_Kong` → `TZ=${SYSTEM_TZ:-Asia/Hong_Kong}` | 低（36 处机械替换，HK 用户 no-op） | 🟡 | Q4（宽表面，需守卫防漏改） |
| 4 | C2 /opt/homebrew PATH | 共享 `path_init.sh` 片段 / 或保留（无害） | 低 | 🟢 | 待定（价值低，59 处 churn 未必值得） |
| 5 | **C1 launchd 抽象** | 进程管理接口（launchd/systemd/nohup 三态） | 高（核心进程管理） | 🔴 | **归 H1-C**（第二实例 PoC 范围，非纯 config 化；`minimal_runtime` 不依赖它，可先跑最小闭环） |
| 6 | D2 内容 prompt locale 层 | prompt-template + locale 参数 | 中（71 文件工程量） | 🟡 | **延后**（边界内，中文用户实例原样可用） |

**明确不做**（两次 DEFER 维持）：大目录重排 / 3-engine-merge。

---

## §5 本次已落地（B3 第一批 · 最低 blast-radius demo）

**A1：3 个 KB 脚本默认路径 `/Users/bisdom/.kb` → `$HOME/.kb`**

- `kb_write.sh:3`、`kb_search.sh:13`、`kb_inject.sh:12`：`${KB_BASE:-/Users/bisdom/.kb}` → `${KB_BASE:-$HOME/.kb}`。
- **为什么是最安全的第一刀**：Mac Mini 上 `$HOME=/Users/bisdom` → `$HOME/.kb` ≡ `/Users/bisdom/.kb`，**生产行为逐字节不变**；非-Mac 实例从"坏路径"变"正确路径"。同时消除一物一形违反（3 脚本对齐 15+ job 脚本 + `auto_deploy.sh` FILE_MAP 已用 `$HOME`）。
- **守卫**：`test_pa_coupling_kb_paths.py` 源码守卫——断言这 3 脚本用 `$HOME/.kb` 默认、**不含 `/Users/bisdom` 硬编码默认**（反向验证：改回 `/Users/bisdom` 立即 FAIL）。这是 B3"扫描器拦截"的定向种子（未框架化，日落法）。
- **VERSION 不变**（生产 no-op，job/工具级，镜像 V37.9.241 TZ 一物一形惯例）。

---

## §6 诚实边界 & 维护契约

- **本清单是 B1（盘点），非 B2/B3 全量落地**。除 A1 外的 config 化按 §4 分级排期（多数 Q4 或延后）。
- **中文 prompt / SOUL 身份 / TZ = 边界内**（纲领 §4.3）：目标是"标注 + 可 config"，**不是清除**——PA 是活体实验室，是 28+ postmortem 语料的来源。
- **未纳入本清单的耦合面**（诚实登记）：`.env_shared` 的具体 env 值（机密，本就不入库）；Mac Mini 特定 cron 调度（system crontab，第二实例用自己的 crontab/systemd timer）；OpenClaw Gateway 自身的 macOS 安装（`install_openclaw_macmini.sh`，H1-C 需并行 Linux 安装脚本）。
- **维护契约**：随纲领季度复核滚动更新（执行计划 Part 2 R5 反目标自查覆盖"PA 耦合是否失控"）；已落地项迁入 changelog，不回填本清单。

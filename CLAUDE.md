# CLAUDE.md — openclaw-model-bridge 项目背景

> 每次新会话开始时自动读取。当前版本：v28.1（2026-03-12）

---

## 项目简介

将任意大模型（当前：Qwen3-235B）接入 OpenClaw（WhatsApp AI助手框架）的双层中间件。
运行于 Mac Mini (macOS)，用户：bisdom。

## 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户层 (WhatsApp)                            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│  ① 核心数据通路（实时对话）                                          │
│                                                                     │
│  WhatsApp ←→ Gateway (:18789) ←→ Tool Proxy (:5002) ←→ Adapter (:5001) ←→ 远程GPU  │
│              [launchd管理]        [策略过滤+监控]       [认证+转发]     [Qwen3-235B] │
│                  │                    │                    │                        │
│                  │               /health ──→ /health       │                        │
│                  │               /stats (token监控)        │                        │
│                  │                    │                    │                        │
└──────────────────┼────────────────────┼────────────────────┼────────────────────────┘
                   │                    │                    │
┌──────────────────▼────────────────────▼────────────────────▼────────────────────────┐
│  ② 定时任务层（system crontab，不经过 LLM 链路）                                     │
│                                                                                     │
│  每3h    ArXiv论文监控 ──→ KB写入 + WhatsApp推送                                     │
│  每3h    HN热帖抓取 ──→ KB写入 + WhatsApp推送                                        │
│  每天×3  货代Watcher ──→ LLM分析(直接curl) + KB写入 + WhatsApp推送                    │
│  每天    OpenClaw Releases ──→ LLM富摘要 + KB写入 + WhatsApp推送                     │
│  每小时  Issues监控 ──→ KB写入 + WhatsApp推送                                        │
│  每天    KB晚间整理                                                                  │
│  每周    KB跨笔记回顾                                                                │
│  每周    健康周报 ──→ WhatsApp推送                                                    │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────────┐
│  ③ 监控层（多级自动告警）                                                            │
│                                                                                     │
│  每30min  wa_keepalive ──→ 真实发送零宽字符 ──→ 失败则记录日志                         │
│  每小时   job_watchdog ──→ 检查所有job状态文件 + 日志扫描 ──→ 超时/失败→WhatsApp告警   │
│  实时     proxy_stats ──→ token用量 + 连续错误计数 ──→ 阈值告警                       │
│  /health  三层健康端点：Gateway(:18789) → Proxy(:5002) → Adapter(:5001)              │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────────┐
│  ④ DevOps层（自动部署 + 体检）                                                       │
│                                                                                     │
│  GitHub (main) ──→ auto_deploy.sh (每2min轮询)                                      │
│                     ├─ git fetch + pull                                              │
│                     ├─ 单测验证（proxy_filters变更时）                                 │
│                     ├─ 文件同步（仓库→运行时，17个文件映射）                            │
│                     ├─ 每小时漂移检测（md5全量比对）                                   │
│                     ├─ 按需restart（核心服务文件变更时）                                │
│                     └─ preflight_check.sh --full（部署后自动体检 9项）                 │
│                         ├─ 单元测试 (proxy_filters + registry)                       │
│                         ├─ 注册表校验                                                │
│                         ├─ 文档漂移检测                                              │
│                         ├─ 脚本语法 + 权限检查                                       │
│                         ├─ Python语法检查                                            │
│                         ├─ 部署文件一致性（仓库 vs 运行时）                            │
│                         ├─ 环境变量检查（bash -lc 模拟cron）                          │
│                         ├─ 服务连通性（5001/5002/18789）                              │
│                         └─ 安全扫描（API key + 手机号泄漏）                           │
│                                                                                     │
│  开发流程：Claude Code → claude/分支 → GitHub PR → main → auto_deploy → Mac Mini     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 核心组件详情

| 组件 | 端口 | 文件 | 功能 | 进程管理 |
|------|------|------|------|----------|
| OpenClaw Gateway | 18789 | npm全局安装 | WhatsApp接入、工具执行、会话管理 | launchd (KeepAlive) |
| Tool Proxy | 5002 | `tool_proxy.py` + `proxy_filters.py` | 工具过滤(24→12)、Schema简化、SSE转换、截断、token监控 | launchd plist |
| Adapter | 5001 | `adapter.py` | 多Provider转发(Qwen/OpenAI/Gemini/Claude)、认证、/health端点 | launchd plist |
| 远程GPU | — | hkagentx.hkopenlab.com | Qwen3-235B推理 (262K context) | 外部服务 |

## 关键文件（本仓库）

| 文件 | 用途 |
|------|------|
| `tool_proxy.py` | HTTP 层（收发请求、日志） |
| `proxy_filters.py` | **V27新增** 策略层（过滤、修复、截断、SSE转换），纯函数无网络依赖 |
| `adapter.py` | API适配层（认证用环境变量 `$REMOTE_API_KEY`） |
| `jobs_registry.yaml` | **V27新增** 统一任务注册表（system + openclaw 双 cron） |
| `check_registry.py` | **V27新增** 注册表校验脚本 |
| `ROLLBACK.md` | **V27新增** 回滚指南（30秒恢复到V26） |
| `upgrade_openclaw.sh` | Gateway升级SOP脚本（必须SSH直连执行，禁止WhatsApp触发） |
| `restart.sh` | 一键重启 Proxy + Adapter + Gateway（含 PATH 修复，可在 cron 环境使用） |
| `health_check.sh` | 每周健康周报脚本（V27: +JSON输出） |
| `kb_write.sh` | KB写入脚本（含目录锁+原子写） |
| `kb_review.sh` | KB跨笔记回顾脚本 |
| `kb_save_arxiv.sh` | ArXiv监控结果写入KB + rsync备份 |
| `auto_deploy.sh` | **V27.1新增** 仓库→部署自动同步 + 漂移检测（md5全量比对+WhatsApp告警） |
| `test_tool_proxy.py` | proxy_filters 单测（43个用例） |
| `test_check_registry.py` | **V28新增** check_registry.py 单测（18个用例） |
| `gen_jobs_doc.py` | **V28新增** 从 registry 自动生成任务文档 + 漂移检测 |
| `smoke_test.sh` | **V28新增** 端到端 smoke test（单测+注册表+连通性） |
| `wa_keepalive.sh` | **V28新增** WhatsApp session 保活（每30分钟真实发送验证） |
| `preflight_check.sh` | **V28新增** 收工前全面体检（9项检查：单测+注册表+语法+部署一致性+环境变量+连通性+安全扫描） |
| `docs/config.md` | 完整系统配置文档（含所有历史变更） |
| `docs/GUIDE.md` | 完整中英文集成指南 |

## V27 变更摘要

1. **Proxy 拆层**：`tool_proxy.py`（HTTP层）+ `proxy_filters.py`（策略层），策略可独立测试
2. **任务注册表**：`jobs_registry.yaml` 统一登记所有 system/openclaw 定时任务
3. **注册表校验**：`check_registry.py` 自动检查 ID 唯一、路径存在、字段完整
4. **Health JSON**：`health_check.sh` 同时输出 `~/health_status.json` 供自动化消费
5. **回滚机制**：`git tag v26-snapshot` + `ROLLBACK.md`，30秒可回退

## V27.1 变更摘要（2026-03-10）

1. **rsync SSD 备份**：`run.sh` 和 `kb_review.sh` 补齐 rsync 备份到外挂 SSD
2. **jobs_registry.yaml 修正**：3处日志路径 + 1处 interval 与 crontab 实际不一致
3. **run_discussions.sh STATUS_FILE 冲突修复**：改为独立文件名避免与 run.sh 冲突
4. **冗余文件清理**：删除根目录 `run_discussions.sh`，统一为 `jobs/` 下唯一版本
5. **auto_deploy.sh 自更新**：加入自身到 FILE_MAP（解决 bootstrapping 问题）
6. **漂移检测**：`auto_deploy.sh` 新增每小时全量 md5 比对 + WhatsApp 告警
7. **每日文档刷新宪法**：CLAUDE.md 和 config.md 在"开始/结束今天的工作"时强制 read+write

## V28 变更摘要（2026-03-11）

1. **check_registry.py 单测**：`test_check_registry.py` 新增 18 个用例，覆盖 YAML 解析（含 inline comment、boolean、空文件）、validate()、FILE_MAP 完整性检查
2. **原则精简分级**：34 条工作原则 → 5 条"每次必查" + 13 条"按需查阅"（折叠），降低认知负担
3. **文档自动生成**：`gen_jobs_doc.py` 从 `jobs_registry.yaml` 自动生成任务表格 + `--check` 漂移检测模式
4. **端到端 smoke test**：`smoke_test.sh` 一键验证单测 + 注册表 + 文档漂移 + 5002/5001 连通性
5. **开发流程明确化**：Claude Code 只推 `claude/` 分支，Mac Mini 只从 main 拉取，写入 CLAUDE.md 原则
6. **WhatsApp CLI 语法修复**：3个脚本从废弃的 `--channel whatsapp -t -m` 改为 `--target --message --json`
7. **stderr 可观测性**：8个 job 脚本从 `2>&1` 改为 `2>"$SEND_ERR"`，失败时记录具体错误
8. **WhatsApp session 保活**：`wa_keepalive.sh` 每30分钟真实发送验证，防止手机休眠导致 session 断连

## V28.1 变更摘要（2026-03-12）

1. **adapter.py /health 端点**：新增本地健康检查拦截，不再转发到远程GPU（修复502误报）
2. **tool_proxy.py /health 端点**：新增级联健康检查（proxy自身 + adapter连通性），返回 `{"ok":true,"proxy":true,"adapter":true}`
3. **job_watchdog.sh 日志扫描**：新增最近1小时推送失败检测（不依赖status_file），覆盖之前的监控盲区
4. **wa_keepalive.sh 真实发送**：从无效的 `--dry-run` 改为发送零宽字符消息，真正验证WhatsApp通道可用性
5. **preflight_check.sh 全面体检**：9项自动化检查（单测+注册表+语法+部署一致性+环境变量+连通性+安全扫描）
6. **auto_deploy.sh 部署后体检**：每次部署后自动运行 `preflight_check.sh --full`，失败推 WhatsApp 告警
7. **环境变量修复**：`OPENCLAW_PHONE` + `REMOTE_API_KEY` 同步到 `~/.bash_profile`（修复 cron 环境缺失）
8. **架构图全面更新**：四层架构（数据通路→定时任务→监控→DevOps）完整可视化

## 常用命令

```bash
# 启动服务
nohup python3 ~/adapter.py > ~/adapter.log 2>&1 &
nohup python3 ~/tool_proxy.py > ~/tool_proxy.log 2>&1 &

# 健康检查
curl http://localhost:5002/health

# 一键重启
bash ~/restart.sh

# 运行单测
python3 test_tool_proxy.py
python3 test_check_registry.py

# 校验任务注册表
python3 check_registry.py

# 一键 smoke test（单测+注册表+连通性）
bash smoke_test.sh

# 收工前全面体检（dev 环境）
bash preflight_check.sh
# 收工前全面体检（Mac Mini，含部署一致性+环境变量+连通性）
bash preflight_check.sh --full

# 生成任务文档 / 检测文档漂移
python3 gen_jobs_doc.py           # 输出 markdown 表格
python3 gen_jobs_doc.py --check   # 对比 docs/config.md 检测漂移

# 查询远端当前模型ID
curl -s https://hkagentx.hkopenlab.com/v1/models \
  -H "Authorization: Bearer $REMOTE_API_KEY" \
  | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if 'Qwen3' in m['id']]"

# GitHub push前安全扫描（必须全部为空才允许push）
grep -r "sk-[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" | grep -v ".git"
grep -r "BSA[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" | grep -v ".git"
```

## 关键规则

### 模型ID规则
| 位置 | 格式 |
|------|------|
| `adapter.py` / `tool_proxy.py` | 裸ID（无前缀） |
| `openclaw.json` agents.defaults.model.primary | **必须带 `qwen-local/` 前缀** |
| `jobs.json` payload.model | **不指定**（继承默认值） |

### 硬性限制
- 工具数量 <= 12（超出导致模型混乱）
- 每任务工具调用 <= 2次（超出超时风险指数级上升）
- 请求体 <= 200KB（硬限制280KB，留buffer）
- `--thinking` 合法值：`off, minimal, low, medium, high, adaptive`（**禁止用 `none`**，这是v26修复的bug #92）

### 双 Cron 归属规则（V27新增）

| 调度器 | 管理方式 | 是否经过 LLM | 登记位置 |
|--------|----------|-------------|----------|
| `system` | macOS `crontab -e` | 否 | `jobs_registry.yaml` scheduler=system |
| `openclaw` | `openclaw cron add` | 是 | `jobs_registry.yaml` scheduler=openclaw |

**原则**：确定性脚本（清理、备份、抓取）用 `system`；需要 LLM 理解/生成的用 `openclaw`。
**新增任务必须先登记到 `jobs_registry.yaml`，运行 `python3 check_registry.py` 通过后才能注册 cron。**

### 安全规则（GitHub push前强制）
- API Key 必须通过环境变量：`os.environ.get("REMOTE_API_KEY")`
- 配置文档（含真实手机号/密钥）永不入库（已在 .gitignore）
- 公开仓库手机号统一用 `+85200000000` 占位

## 工作原则

### 🔴 每次必查（6条，优先级最高）

| # | 原则 | 一句话 |
|---|------|--------|
| 1 | **开工先读 config** | 读 `docs/config.md` 获取系统状态 + 踩坑记录，避免重复犯错 |
| 2 | **改完先测** | 新脚本手动验证 → 新任务先写 `jobs_registry.yaml` 并 `python3 check_registry.py` 通过 → 才能注册 cron |
| 3 | **push前必扫描** | 安全扫描（见上方命令）全部为空才允许 push |
| 4 | **故障先回滚** | 线上故障 → `git checkout v26-snapshot` 恢复服务 → 再排查根因；多任务同时挂 → 先查远端模型ID |
| 5 | **做减法不做加法** | 新增防护/监控前先问"谁已经在管这件事"；每加一层保险 = 多一个故障源（#95教训） |
| 6 | **收工提醒 preflight** | "结束今天的工作"时，提醒用户在 Mac Mini 上运行 `bash preflight_check.sh --full`（用户自行执行，Claude 负责提醒） |

### 🟡 按需查阅（操作 & 架构参考）

<details>
<summary>展开查看完整原则列表（13条）</summary>

**操作类**
- **收工全量同步** — "今天工作结束" → `bash preflight_check.sh` 全面体检 → 扫描全部文档同步当日变更 → 提交推送
- **每日文档刷新** — `CLAUDE.md` + `docs/config.md` 在开工/收工时强制 read → write
- **纯推理绕过Gateway** — 不需要工具的LLM任务直接 curl 调 API，禁止用 `openclaw agent`（#94）
- **macOS sed禁用OR语法** — `\|` 在 BSD sed 不支持，用 Python 替代
- **禁用交互式编辑器** — git merge 用 `--no-edit`，commit 用 `-m`，crontab 用管道
- **分支合并由用户在GitHub操作** — 推送到 `claude/xxx` 分支 → 提醒用户创建 PR → 用户 `git pull origin main`

**架构类**
- **进程管理单一主控** — Gateway 由 launchd 管理，禁止再加 cron watchdog（#95）
- **cron 脚本显式声明 PATH** — 首行 `export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"`
- **健康检查只检目标组件** — `curl localhost:18789`，不走完整 LLM 链路
- **`--thinking` 参数** — 合法值：`off, minimal, low, medium, high, adaptive`（禁止 `none`，#92）
- **工具数量 <= 12** — 超出导致模型混乱；每任务工具调用 <= 2次
- **双 cron 职责分工** — 确定性脚本用 system crontab；需 LLM 参与的用 openclaw cron
- **开发流程** — Claude Code 只推 `claude/` 分支，Mac Mini 只从 main 拉取，避免双向提交同一分支

</details>

## 当前待办（v27遗留）

| 优先级 | 任务 |
|--------|------|
| ✅ | 货代Watcher V2：ImportYeti手动查询SOP（docs/importyeti_sop.md） |
| 低 | 货代Watcher V3：Bing News API替代GoogleNews |
| ✅ | Blog中文标题升级为LLM动态生成 |
| ✅ | WhatsApp target号码统一为 OPENCLAW_PHONE |
| 低 | 探索Claude/GPT-4o替换Qwen3 |

## 远程连接（本机）

```bash
ssh bisdom@10.102.0.217      # 办公室内网
ssh bisdom@10.120.230.23     # ZeroTier（回家后）
```

## Git 仓库

```
git@github.com:bisdom-cell/openclaw-model-bridge.git
```
Remote 已改为 SSH（v25修复HTTPS认证失败）。
回滚标签：`v26-snapshot`（V27变更前的完整快照）。

### Git 分支规则
- 默认直接在 main 分支上开发提交，除非任务指令明确指定其他分支
- 即使 session 系统指令指定了 `claude/xxx` 分支，仍优先遵守本规则在 main 上开发

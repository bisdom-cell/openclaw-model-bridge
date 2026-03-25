# CLAUDE.md — openclaw-model-bridge 项目背景

> 每次新会话开始时自动读取。当前版本：v29.4（2026-03-25）

---

## 项目简介

将任意大模型（当前：Qwen3-235B + Qwen2.5-VL-72B）接入 OpenClaw（WhatsApp AI助手框架）的双层中间件，支持多模态（图片理解）。
运行于 Mac Mini (macOS)，用户：bisdom。

## 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户层 (WhatsApp)                            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│  ① 核心数据通路（实时对话 + 多模态）                                  │
│                                                                     │
│  WhatsApp ←→ Gateway (:18789) ←→ Tool Proxy (:5002) ←→ Adapter (:5001) ←→ 远程GPU        │
│              [launchd管理]        [策略过滤+监控]       [认证+VL路由]     [Qwen3-235B]      │
│              [媒体存储]           [图片base64注入]      [Fallback降级]    [Qwen2.5-VL-72B]  │
│                  │                    │                    │                               │
│                  │               /health ──→ /health       │                               │
│                  │               /stats (token监控)        │                               │
│                                                                     │
│  图片流程：Gateway存储jpg → Proxy检测<media:image> → base64注入       │
│           → Adapter检测image_url → 路由到Qwen2.5-VL → 图片理解回复    │
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
│  每天    KB每日摘要 ──→ ~/.kb/daily_digest.md（LLM对话时可查）                          │
│  每周    KB跨笔记回顾 ──→ LLM深度分析 + WhatsApp推送                                   │
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
│                     ├─ 文件同步（仓库→运行时，31个文件映射）                            │
│                     ├─ 每小时漂移检测（md5全量比对）                                   │
│                     ├─ 按需restart（核心服务文件变更时）                                │
│                     └─ preflight_check.sh --full（部署后自动体检 11项）                │
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
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────────┐
│  ⑤ 三方共享状态层（实时同步）                                                         │
│                                                                                     │
│  ┌──────────┐     ┌─────────────────────┐     ┌──────────────┐                      │
│  │ 用户      │     │ ~/.kb/status.json   │     │ Claude Code  │                      │
│  │ (WhatsApp)│────→│  priorities[]       │←────│ 开工: 读全部   │                      │
│  │ 反馈+决策 │ PA  │  feedback[]         │     │ 收工: 写变更   │                      │
│  │          │写入  │  recent_changes[]   │     │ 更新优先级     │                      │
│  └──────────┘     │  health{}           │     └──────────────┘                      │
│                   │  focus              │                                            │
│                   └────────┬────────────┘                                            │
│                            │                                                         │
│                   ┌────────┴────────────┐                                            │
│                   │ Cron 脚本自动更新     │                                            │
│                   │ auto_deploy → deploy │                                            │
│                   │ preflight → health   │                                            │
│                   │ kb_trend → trend     │                                            │
│                   └─────────────────────┘                                            │
│                                                                                     │
│  宪法：用户提供专业深度 + Claude Code 提供高效设计部署 + OpenClaw 提供数据复利            │
│        三者合一成为有生命的闭环系统                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 核心组件详情

| 组件 | 端口 | 文件 | 功能 | 进程管理 |
|------|------|------|------|----------|
| OpenClaw Gateway | 18789 | npm全局安装 | WhatsApp接入、**媒体存储**、工具执行、会话管理 | launchd (KeepAlive) |
| Tool Proxy | 5002 | `tool_proxy.py` + `proxy_filters.py` | 工具过滤(24→12)、**图片base64注入**、Schema简化、SSE转换、截断、token监控 | launchd plist |
| Adapter | 5001 | `adapter.py` | 多Provider转发、认证、**多模态路由**（文本→Qwen3，图片→Qwen2.5-VL）、Fallback降级 | launchd plist |
| 远程GPU | — | hkagentx.hkopenlab.com | **Qwen3-235B**（文本, 262K context）+ **Qwen2.5-VL-72B**（视觉理解） | 外部服务 |

## 关键文件（本仓库）

| 文件 | 用途 |
|------|------|
| `tool_proxy.py` | HTTP 层（收发请求、日志） |
| `proxy_filters.py` | **V27新增** 策略层（过滤、修复、截断、SSE转换），纯函数无网络依赖 |
| `adapter.py` | API适配层（认证 `$REMOTE_API_KEY`，Fallback降级 `$FALLBACK_PROVIDER`） |
| `openclaw_backup.sh` | **V29.1新增** 每日Gateway state备份到外挂SSD（保留7天） |
| `jobs_registry.yaml` | **V27新增** 统一任务注册表（system + openclaw 双 cron） |
| `check_registry.py` | **V27新增** 注册表校验脚本 |
| `ROLLBACK.md` | **V27新增** 回滚指南（30秒恢复到V26） |
| `upgrade_openclaw.sh` | Gateway升级SOP脚本（必须SSH直连执行，禁止WhatsApp触发） |
| `restart.sh` | 一键重启 Proxy + Adapter + Gateway（含 PATH 修复，可在 cron 环境使用） |
| `health_check.sh` | 每周健康周报脚本（V27: +JSON输出） |
| `kb_write.sh` | KB写入脚本（含目录锁+原子写） |
| `kb_review.sh` | **V29升级** KB跨笔记回顾（LLM深度分析+WhatsApp推送） |
| `kb_search.sh` | **V29新增** KB按需查询工具（关键词/标签/来源/统计概览） |
| `kb_inject.sh` | **V29新增→V29.4升级** 每日KB摘要+运维知识精华注入workspace CLAUDE.md；文档按需查阅路径 `~/.kb/docs/` |
| `mm_index.py` | **V29.1新增** Multimodal Memory 索引器（Gemini Embedding 2，支持图片/音频/视频/PDF） |
| `mm_search.py` | **V29.1新增** Multimodal Memory 语义搜索（文本查询→cosine similarity→匹配媒体） |
| `mm_index_cron.sh` | **V29.1新增** MM 索引定时任务包装脚本（每2小时） |
| `local_embed.py` | **V29.3新增** 本地 Embedding 引擎（sentence-transformers，中英双语，零API调用） |
| `kb_embed.py` | **V29.3新增** KB 文本向量索引器（notes+sources 分块→本地 embedding→~/.kb/text_index/） |
| `kb_rag.py` | **V29.3新增** KB RAG 语义搜索（--context LLM注入 / --json 脚本调用） |
| `kb_trend.py` | **V29.5新增** KB周趋势报告（本周vs上周关键词+LLM分析+WhatsApp推送） |
| `status_update.py` | **V29.5新增** 三方共享项目状态工具（原子读写 ~/.kb/status.json，Claude Code + PA + cron 共用） |
| `kb_save_arxiv.sh` | ArXiv监控结果写入KB + rsync备份 |
| `auto_deploy.sh` | **V27.1新增** 仓库→部署自动同步 + 漂移检测（md5全量比对+WhatsApp告警） |
| `test_tool_proxy.py` | proxy_filters 单测（43个用例） |
| `test_check_registry.py` | **V28新增** check_registry.py 单测（18个用例） |
| `gen_jobs_doc.py` | **V28新增** 从 registry 自动生成任务文档 + 漂移检测 |
| `smoke_test.sh` | **V28新增** 端到端 smoke test（单测+注册表+连通性） |
| `wa_keepalive.sh` | **V28新增** WhatsApp session 保活（每30分钟真实发送验证） |
| `preflight_check.sh` | **V28新增** 收工前全面体检（11项检查：单测+注册表+语法+部署一致性+环境变量+连通性+安全扫描+数据流+货代监控） |
| `docs/config.md` | 完整系统配置文档（含所有历史变更） |
| `docs/GUIDE.md` | 完整中英文集成指南 |
| `docs/openclaw_architecture.md` | **V28.2新增** OpenClaw 开源架构完整参考（每日开工自动刷新） |

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
4. **wa_keepalive.sh 回退为纯HTTP探测**：零宽字符在WhatsApp仍显示为空消息气泡（打扰用户），移除真实发送，仅保留Gateway HTTP健康检查；端到端推送失败由 job_watchdog 日志扫描覆盖
5. **preflight_check.sh 全面体检**：9项自动化检查（单测+注册表+语法+部署一致性+环境变量+连通性+安全扫描）
6. **auto_deploy.sh 部署后体检**：每次部署后自动运行 `preflight_check.sh --full`，失败推 WhatsApp 告警
7. **环境变量修复**：`OPENCLAW_PHONE` + `REMOTE_API_KEY` 同步到 `~/.bash_profile`（修复 cron 环境缺失）
8. **架构图全面更新**：四层架构（数据通路→定时任务→监控→DevOps）完整可视化

## V28.2 变更摘要（2026-03-13）

1. **单线程阻塞修复**：`tool_proxy.py` + `adapter.py` 从 `TCPServer` 改为 `ThreadingMixIn`（`daemon_threads=True`），解决单请求挂起阻塞所有后续请求的问题
2. **日志时间戳**：proxy/adapter 所有日志行加 `%Y-%m-%d %H:%M:%S` 前缀，支持事后排查
3. **货代 ImportYeti 修复三连**：Python PATH 冲突（`/usr/bin/python3` 硬编码）、中文企业名 LLM 翻译、Cloudflare 反爬重试
4. **货代静默降级监控**：`run_freight.sh` 新增 `deep_dive` 状态写入 `last_run.json`（ok/no_data/skipped），scraper 失败捕获退出码
5. **preflight 扩展至 11 项**：新增第 10 项 Job 数据流 smoke test + 第 11 项货代 deep_dive 静默失败检测（含 scraper.log 错误扫描、playwright 可用性）
6. **macOS BSD grep 兼容**：`grep -ci "\|"` → `grep -ciE "|"` + `|| true` 修复（`grep -c` 返回 0 行时退出码 1 导致双行输出）
7. **docs/config.md 漂移修复**：补齐 `kb_evening`、修正 `run_hn_fixed.sh` 路径

## V28.3 变更摘要（2026-03-13）

1. **开工流程新增 OpenClaw 架构同步**：每次"开始今天的工作"时，查 OpenClaw 最新 release → 对比 `docs/openclaw_architecture.md` 记录的版本 → 有变更则研读源码并更新文档，确保中间件始终与上游架构同步
2. **每次必查原则扩展至 7 条**：新增第 1 条"开工刷新 OpenClaw 架构"，原 1-6 顺延为 2-7
3. **每日文档刷新范围扩展**：`docs/openclaw_architecture.md` 加入开工/收工强制 read→write 循环

## V29 变更摘要（2026-03-13）

1. **KB 搜索工具**：`kb_search.sh` 支持全文搜索（关键词/标签/天数/来源组合）、`--summary` 统计概览（条目数/来源大小/热门标签/7天活跃度）、`--source` 来源归档搜索
2. **KB 回顾升级为 LLM 深度分析**：`kb_review.sh` 收集最近N天的 notes + sources 完整内容 → curl proxy:5002 调 LLM 跨领域分析（亮点/关联/行动建议/知识空白） → 推送 WhatsApp + 写入 review 文件；LLM 失败时 graceful fallback
3. **KB 每日摘要**：`kb_inject.sh` 每天 07:00 生成 `~/.kb/daily_digest.md`（notes 精华 + sources 关键段），LLM 对话时通过 read 工具查阅
4. **WhatsApp LLM 自动查 KB**：workspace CLAUDE.md 添加知识库查询指引，用户问"最近有什么新论文"等问题时 LLM 自动读取 daily_digest.md 回答
5. **kb_review.sh 从 openclaw cron 改为 system cron**：直接 curl 调 LLM 分析，不再依赖 openclaw agent
6. **auto_deploy.sh FILE_MAP 扩展至 19 个文件**：新增 kb_search.sh + kb_inject.sh

## V29.5 变更摘要（2026-03-25）

1. **KB 周趋势报告**：`kb_trend.py` 新增 — 每周六 09:00 自动运行，对比本周 vs 上周的 ArXiv/HN/笔记关键词频率变化（TF + 领域关键词加权），识别上升趋势/新出现热词/消退话题，调用 LLM 生成趋势解读+下周预测，推送 WhatsApp；支持 `--json` 脚本调用、`--no-llm` 纯统计模式
2. **模型智能路由**：`proxy_filters.py` 新增 `classify_complexity()` 纯函数，根据对话轮数、用户消息长度、是否有工具/多模态判断请求复杂度（simple/complex）；`adapter.py` 新增 `FAST_PROVIDER` + `FAST_MODEL_ID` 环境变量，simple 请求可路由到快速模型（如 Gemini Flash），降低延迟和成本，complex 请求继续用 Qwen3-235B
3. **三方共享状态**：`status_update.py` + `~/.kb/status.json` — Claude Code（开工读/收工写）、PA（用户反馈写/状态查询读）、cron（部署/体检/趋势报告自动更新）三方通过同一个 JSON 文件实时同步项目状态（优先级、反馈、系统健康、本周焦点）
4. **单测扩展至 67 个**：新增 9 个 `classify_complexity` 测试用例（短问答/多轮/工具/多模态/NO_TOOLS/空消息等边界场景）
5. **jobs_registry 新增 kb_trend**：每周六 09:00，通过 proxy:5002 调 LLM，无需独立 API Key

## V29.4 变更摘要（2026-03-25）

1. **多模态图片理解**：WhatsApp 发送图片 → Gateway 存储到 `~/.openclaw/media/inbound/` → Proxy 检测 `<media:image>` 标记并读取图片 base64 编码注入 `image_url` → Adapter 检测多模态内容自动路由到 Qwen2.5-VL-72B-Instruct → 返回图片描述/理解
2. **Adapter VL 模型路由**：`adapter.py` 新增 `vl_model_id` 配置，检测 `image_url`/`image`/`audio`/`video` content 类型时自动切换模型，纯文本继续用 Qwen3-235B；`/health` 端点暴露 VL 模型信息
3. **Proxy 图片注入**：`proxy_filters.py` 新增 `inject_media_into_messages()` — 检测 `<media:image>` 标记 → 查找最近5分钟内的图片 → base64 编码（10MB上限）→ 转为 OpenAI 多模态消息格式；支持图片和文字分开发送的场景
4. **restart.sh 修复**：`#48703 hotfix` 中的 grep 管道在无匹配时返回非零退出码，被 `set -e` 捕获导致脚本在 Gateway 启动前中断
5. **openclaw.json 更新**：模型声明 `input` 从 `["text"]` 改为 `["text", "image"]`；research agent 工具白名单新增 `image`
6. **WhatsApp PA 运维知识注入**：`kb_inject.sh` 升级 — workspace CLAUDE.md 新增系统架构简版、9条核心运维原则、常用运维命令、深度文档按需查阅路径（`~/.kb/docs/`）；`auto_deploy.sh` FILE_MAP 新增 3 个文档文件（GUIDE.md/config.md/CLAUDE.md → `~/.kb/docs/`），PA 遇架构/故障问题时可用 read 工具查阅完整文档

## V29.3 变更摘要（2026-03-24）

1. **本地 Embedding 引擎**：`local_embed.py` 基于 sentence-transformers（paraphrase-multilingual-MiniLM-L12-v2，384维，50+语言），零API调用/零限速/零成本；Mac Mini Apple Silicon 单条~10ms，批量100条~500ms
2. **KB 文本向量索引**：`kb_embed.py` 扫描 notes+sources → 分块（400字/块，80字重叠）→ 本地 embedding → `~/.kb/text_index/`（meta.json + vectors.bin）；增量索引（文件hash去重），模型变更自动重建
3. **KB RAG 语义搜索**：`kb_rag.py` 自然语言查询KB知识库 → cosine similarity → top-K相关片段；支持 `--context`（LLM可直接注入的格式）、`--json`（脚本调用）、`--top N`
4. **算力本地化**：KB文本搜索完全本地化，不再依赖外部API；Multimodal Memory（图片/音频/视频）仍使用 Gemini Embedding 2（本地文本模型无法处理多模态）
5. **preflight Python语法检查修复**：`.py` entry文件改用 `ast.parse()` 检查（之前误用 `bash -n`）
6. **jobs_registry.yaml 新增 kb_embed 任务**：每4小时增量索引，无需API Key

## V29.2 变更摘要（2026-03-23）

1. **OpenClaw 架构文档同步至 v2026.3.23**：`docs/openclaw_architecture.md` 全面更新，覆盖 6 项 Breaking Change（Plugin SDK 路径、Legacy 环境变量/目录移除、Chrome relay 废弃等）、Provider 架构重构（bundled plugins）、WhatsApp #48703 修复、Health monitor 可配置阈值
2. **WhatsApp #48703 确认已修复**：listener Map 被 bundler code-splitting 拆成多实例导致 outbound send 失败 → v2026.3.22 通过 `globalThis` singleton 修复
3. **升级决策：维持 v2026.3.13-1，暂不升级**：v2026.3.22 起 WhatsApp plugin unbundled，需从 ClawHub 安装；ClawHub 限流（429）且 WhatsApp plugin 仅 0.0.5-Alpha，生产风险不可接受。v2026.3.23 的 launchd 锁冲突修复虽有价值，但不值得冒 WhatsApp 通道中断风险。等 WhatsApp plugin >= 1.0.0 稳定版再评估
4. **中间件无需变更**：我们的代码已使用 `OPENCLAW_*` 环境变量和 `~/.openclaw` 路径，不受 Breaking Changes 影响

## V29.1 变更摘要（2026-03-14）

1. **Model Fallback 降级链**：`adapter.py` 支持 primary→fallback 自动降级（Qwen3→Gemini 2.5 Flash），PA 永不离线；环境变量 `FALLBACK_PROVIDER` + `GEMINI_API_KEY` 控制
2. **每日自动备份**：`openclaw_backup.sh` 每天 03:00 备份 Gateway state 到外挂 SSD（`/Volumes/MOVESPEED/openclaw_backup/`），保留 7 天自动清理
3. **Bootstrap KB 自动注入**：`kb_inject.sh` 生成摘要后同步写入 `~/.openclaw/workspace/.openclaw/CLAUDE.md`，每个新 WhatsApp session 自动加载知识库上下文
4. **Context Pruning 配置**：Gateway 启用 `cache-ttl` 模式（`ttl: "6h"`，`keepLastAssistants: 3`），自动清理过期上下文
5. **Multi-Agent 专业化**：配置 research（研究助手）+ ops（运维助手）独立 agent，session 隔离避免上下文污染
6. **Gateway 升级至 v2026.3.13**：cron delivery isolation breaking change（v2026.3.12 引入），所有 openclaw cron 任务已迁移至 system crontab；v2026.3.13 修复 compaction token count 校验、session reset 状态保留
7. **Multimodal Memory**：`mm_index.py` + `mm_search.py` — Gemini Embedding 2 多模态索引（图片/音频/视频/PDF），文本语义搜索已索引媒体
8. **auto_deploy.sh FILE_MAP 扩展至 23 个文件**：新增 openclaw_backup.sh + mm_index.py + mm_search.py + mm_index_cron.sh
9. **jobs_registry.yaml 新增 2 个任务**：openclaw_backup（03:00）+ mm_index（每2小时）

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

# KB 搜索 / 摘要
bash kb_search.sh "关键词"         # 全文搜索
bash kb_search.sh --summary        # 统计概览
bash kb_search.sh --source arxiv   # 搜索来源归档
bash kb_inject.sh                  # 手动生成每日摘要

# Multimodal Memory（需要 pip3 install google-genai numpy）
python3 mm_index.py                # 增量索引媒体文件
python3 mm_index.py --reindex      # 重建全部索引
python3 mm_search.py "猫的照片"    # 语义搜索媒体
python3 mm_search.py --stats       # 索引统计

# KB 本地 Embedding + RAG（需要 pip3 install sentence-transformers）
python3 local_embed.py --bench     # 性能基准测试
python3 kb_embed.py                # 增量索引 KB 文本
python3 kb_embed.py --reindex      # 重建全部索引
python3 kb_embed.py --stats        # 索引统计
python3 kb_rag.py "Qwen3 模型"     # 语义搜索 KB
python3 kb_rag.py --context "AI论文" # LLM 可直接注入的上下文格式
python3 kb_rag.py --json "shipping" # JSON 输出（供脚本调用）

# 生成任务文档 / 检测文档漂移
python3 gen_jobs_doc.py           # 输出 markdown 表格
python3 gen_jobs_doc.py --check   # 对比 docs/config.md 检测漂移

# 查询远端当前模型ID
curl -s https://hkagentx.hkopenlab.com/v1/models \
  -H "Authorization: Bearer $REMOTE_API_KEY" \
  | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if 'Qwen3' in m['id']]"

# Mac Mini 同步仓库（禁止用 git pull，会因历史 merge commit 分叉失败）
cd ~/openclaw-model-bridge && git fetch origin main && git reset --hard origin/main

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

### 🔴 三合一宪法

> **用户提供专业深度 + Claude Code 提供高效设计部署 + OpenClaw 提供数据复利 — 三者合一成为有生命的闭环系统。**
>
> 共享状态：`~/.kb/status.json`（三方实时同步优先级、反馈、系统健康）

### 🔴 每次必查（9条，优先级最高）

| # | 原则 | 一句话 |
|---|------|--------|
| 1 | **开工刷新 OpenClaw 架构（先读决策再评估）** | 先读 `docs/config.md` 中现有的升级 hold 决策和版本状态，再查 OpenClaw 最新 release；如已有明确 hold 决策且上游无新版本，跳过重复评估；有新版本时对比决策条件是否变化，变化则重新评估，否则沿用。**禁止"上游已修复"就改代码——必须确认本地已部署该版本**（#48703教训） |
| 2 | **开工先读 config** | 读 `docs/config.md` 获取系统状态 + 踩坑记录，避免重复犯错 |
| 3 | **开工先读/收工必写 status.json** | `python3 status_update.py --read --human` 查看三方共享状态（优先级、反馈、系统健康）；收工时更新 priorities + recent_changes：`python3 status_update.py --add recent_changes '{"date":"...","what":"...","by":"claude_code"}' --by claude_code` |
| 4 | **改完先测** | 新脚本手动验证 → 新任务先写 `jobs_registry.yaml` 并 `python3 check_registry.py` 通过 → 才能注册 cron |
| 5 | **push前必扫描** | 安全扫描（见上方命令）全部为空才允许 push |
| 6 | **故障先查自身代码** | 排查问题时默认从我们自己的代码和架构中找 bug（shell 数据传递、cron 环境、进程管理等），不归因于上游服务不稳定（#97教训） |
| 7 | **做减法不做加法** | 新增防护/监控前先问"谁已经在管这件事"；每加一层保险 = 多一个故障源（#95教训） |
| 8 | **收工提醒 preflight + 更新 status.json** | "结束今天的工作"时，提醒用户在 Mac Mini 上运行 `bash preflight_check.sh --full`；同时用 `status_update.py` 写入今天的变更摘要和优先级更新 |
| 9 | **相信 OpenClaw，用好 OpenClaw** | 优先利用 OpenClaw 已有能力（Multi-Agent、contextPruning、workspace CLAUDE.md、tools 等），而非重新造轮子；遇到新需求先查 OpenClaw 文档和 release notes，充分发挥其潜力来提升效率和创新 |

### 🟡 按需查阅（操作 & 架构参考）

<details>
<summary>展开查看完整原则列表（13条）</summary>

**操作类**
- **故障先回滚** — 线上故障 → `git checkout v26-snapshot` 恢复服务 → 再排查根因
- **收工全量同步** — "今天工作结束" → `bash preflight_check.sh` 全面体检 → 扫描全部文档同步当日变更 → 提交推送
- **每日文档刷新** — `CLAUDE.md` + `docs/config.md` + `docs/openclaw_architecture.md` 在开工/收工时强制 read → write
- **纯推理绕过Gateway** — 不需要工具的LLM任务直接 curl 调 API，禁止用 `openclaw agent`（#94）
- **macOS sed禁用OR语法** — `\|` 在 BSD sed 不支持，用 Python 替代
- **禁用交互式编辑器** — git merge 用 `--no-edit`，commit 用 `-m`，crontab 用管道
- **分支合并由用户在GitHub操作** — 推送到 `claude/xxx` 分支 → 提醒用户创建 PR → 用户在 Mac Mini 同步
- **Mac Mini 同步用 reset 不用 pull** — `git fetch origin main && git reset --hard origin/main`（Mac Mini 是纯消费端，无本地 commit；`git pull` 会因历史 merge commit 导致分叉失败）

**架构类**
- **进程管理单一主控** — Gateway 由 launchd 管理，禁止再加 cron watchdog（#95）
- **cron 脚本显式声明 PATH** — 首行 `export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"`
- **健康检查只检目标组件** — `curl localhost:18789`，不走完整 LLM 链路
- **`--thinking` 参数** — 合法值：`off, minimal, low, medium, high, adaptive`（禁止 `none`，#92）
- **工具数量 <= 12** — 超出导致模型混乱；每任务工具调用 <= 2次
- **双 cron 职责分工** — 确定性脚本用 system crontab；需 LLM 参与的用 openclaw cron
- **开发流程** — Claude Code 只推 `claude/` 分支，Mac Mini 只从 main 拉取，避免双向提交同一分支

</details>

## 当前待办

| 优先级 | 任务 |
|--------|------|
| 低 | 知识图谱：AI大模型领域知识图谱构建（需6-12个月数据积累，暂缓） |
| 低 | 货代Watcher V3：Bing News API替代GoogleNews |
| 低 | 语音消息支持：WhatsApp语音→STT→LLM回复 |
| 低 | MM搜索接入对话：mm_search.py 注册为 OpenClaw tool |
| ✅ | **KB 周趋势报告 + 模型智能路由（V29.5）** |
| ✅ | **多模态图片理解：Qwen2.5-VL-72B 自动路由（V29.4）** |
| ✅ | Multimodal Memory：Gemini Embedding 2 索引图片/音频/视频/PDF（V29.1） |
| ✅ | Model Fallback 降级链（V29.1） |
| ✅ | 每日自动备份（V29.1） |
| ✅ | Multi-Agent 专业化（V29.1） |
| ✅ | Bootstrap KB 自动注入（V29.1） |
| ✅ | Context Pruning 配置（V29.1） |
| ✅ | KB三件套：搜索/LLM深度回顾/每日摘要注入（V29） |

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

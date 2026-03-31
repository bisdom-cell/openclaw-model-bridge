# CLAUDE.md — openclaw-model-bridge 项目背景

> 每次新会话开始时自动读取。当前版本：v30.5（2026-03-31）

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
│                  │                [自定义工具注入]          │                               │
│                  │                    │                    │                               │
│                  │               /health ──→ /health       │                               │
│                  │               /stats (token监控)        │                               │
│                  │               /data_clean/* (REST)      │                               │
│                                                                     │
│  图片流程：Gateway存储jpg → Proxy检测<media:image> → base64注入       │
│           → Adapter检测image_url → 路由到Qwen2.5-VL → 图片理解回复    │
│  数据清洗：Gateway存储文件 → LLM调用data_clean工具 → Proxy拦截执行     │
│           → data_clean.py本地处理 → 格式化结果返回 → WhatsApp展示      │
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
| Tool Proxy | 5002 | `tool_proxy.py` + `proxy_filters.py` | 工具过滤(24→12)、**图片base64注入**、**自定义工具注入+拦截执行**（data_clean、search_kb）、Schema简化、SSE转换、截断、token监控、`/data_clean/*` REST端点 | launchd plist |
| Adapter | 5001 | `adapter.py` | 多Provider转发、认证、**多模态路由**（文本→Qwen3，图片→Qwen2.5-VL）、Fallback降级 | launchd plist |
| 远程GPU | — | hkagentx.hkopenlab.com | **Qwen3-235B**（文本, 262K context）+ **Qwen2.5-VL-72B**（视觉理解） | 外部服务 |

## 关键文件（本仓库）

| 文件 | 用途 |
|------|------|
| `tool_proxy.py` | HTTP 层（收发请求、日志、**自定义工具拦截执行**（data_clean本地执行+search_kb混合检索+followup LLM调用）、`/data_clean/*` REST端点） |
| `proxy_filters.py` | **V27新增** 策略层（过滤、修复、截断、SSE转换、**自定义工具注入**（data_clean+search_kb）），纯函数无网络依赖 |
| `data_clean.py` | **V30.3新增** 数据清洗 CLI 工具（profile/execute/validate/history，7种操作，支持CSV/TSV/JSON/JSONL/Excel） |
| `test_data_clean.py` | **V30.3新增** 数据清洗单测（80个用例：格式检测/读写/操作/端到端/多格式） |
| `data_clean_poc/` | **V30.3新增** Phase 0 验证材料（3个脏数据样本+LLM判断力测试脚本） |
| `SOUL.md` | **V30.4新增** OpenClaw 最高优先级 system prompt（PA身份Wei、三方宪法、行为指令、项目状态实时快照，每小时自动刷新） |
| `ops_soul.md` | **V31新增** Ops Agent 运维助手 SOUL.md（系统健康检查/日志排查/cron诊断/维护操作，部署到 `~/.openclaw/SOUL.md`） |
| `ops_health.sh` | **V31新增** Ops Agent 健康检查包装脚本（Qwen3 拒绝直接 curl localhost，通过脚本包装绕过） |
| `status.json` | **V30.4新增（仓库副本）** 三方共享意识锚点（priorities/feedback/incidents/quality/operating_rules/session_context），Mac Mini 每小时 git push 同步 |
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
| `test_tool_proxy.py` | proxy_filters 单测（67个用例，含自定义工具注入） |
| `test_check_registry.py` | **V28新增** check_registry.py 单测（18个用例） |
| `gen_jobs_doc.py` | **V28新增** 从 registry 自动生成任务文档 + 漂移检测 |
| `smoke_test.sh` | **V28新增** 端到端 smoke test（单测+注册表+连通性） |
| `wa_keepalive.sh` | **V28新增** WhatsApp session 保活（每30分钟真实发送验证） |
| `preflight_check.sh` | **V28新增→V30.3升级** 收工前全面体检（16项检查：单测+注册表+语法+部署一致性+环境变量+连通性+安全扫描+数据流+货代监控+crontab路径一致性+推送通道E2E） |
| `job_smoke_test.sh` | **V30.3新增** 全量 job smoke test（20个启用任务×6维度：脚本存在/crontab注册/运行时路径/日志活跃/状态文件/锁文件+KB完整性+crontab条目数） |
| `docs/config.md` | 完整系统配置文档（含所有历史变更） |
| `docs/GUIDE.md` | 完整中英文集成指南 |
| `docs/openclaw_architecture.md` | **V28.2新增** OpenClaw 开源架构完整参考（每日开工自动刷新） |
| `cron_doctor.sh` | **V30新增** 定时任务全面诊断工具（7项检查：crontab/锁文件/心跳/服务/环境/时效/系统） |
| `cron_canary.sh` | **V30新增** Cron 心跳金丝雀（每10分钟，零依赖，原子写入） |
| `crontab_safe.sh` | **V30新增** 安全 crontab 操作（自动备份+条目数验证+回滚保护） |
| `test_cron_health.py` | **V30新增** 定时任务健康检测单测（94个用例：锁/心跳/告警/原子写入/损坏恢复/daemon检测/完整性/状态刷新） |
| `kb_status_refresh.sh` | **V30.1新增** 每小时刷新 status.json 系统健康字段（三层服务/模型ID/KB统计/过期job），补齐三方宪法实时同步 |
| `kb_integrity.py` | **V30.1新增** KB 文件完整性校验器（SHA256 指纹比对、目录文件数监控、权限检查、status.json 结构验证） |
| `test_status_update.py` | **V30.1新增** status_update.py 全量单测（33个用例：原子读写/嵌套字段/数组操作/优先级CRUD/CLI接口） |
| `test_adapter.py` | **V30.1新增** adapter.py 全量单测（36个用例：Provider注册表/认证头/多模态路由/Fallback/智能路由/健康端点） |
| `test_kb_business.py` | **V30.1新增** KB全业务逻辑单测（44个用例：kb_embed/kb_rag/kb_trend/mm_index/mm_search/kb_integrity/安全模式） |
| `full_regression.sh` | **V30.1新增** 全量回归测试一键运行器（四层：单元测试+注册表文档+安全扫描+代码质量，393个用例，100%通过才允许推送） |
| `audit_log.py` | **V30.2新增** 链式哈希审计日志（JSONL append-only，SHA256 链式校验，篡改/删除可检测） |
| `test_audit_log.py` | **V30.2新增** 审计日志单测（19个用例：写入/链式哈希/篡改检测/删除检测/统计） |
| `security_score.py` | **V30.2新增** 系统安全评分（7维度100分：密钥/测试/完整性/部署/传输/审计/可用性） |
| `jobs/dblp/run_dblp.sh` | **V30.5新增** DBLP CS论文监控（多关键词搜索、免费API、每日12:00推送+KB写入） |
| `jobs/hf_papers/run_hf_papers.sh` | **V30.5新增** HuggingFace Daily Papers 监控（热门AI论文、每日10:00推送+KB写入） |
| `jobs/semantic_scholar/run_semantic_scholar.sh` | **V30.5新增** Semantic Scholar 论文监控（引用量排序、每日11:00推送+KB写入） |
| `jobs/acl_anthology/run_acl_anthology.sh` | **V30.5新增** ACL Anthology NLP论文监控（顶会论文、每日09:30推送+KB写入） |
| `jobs/pwc/run_pwc.sh` | **V31新增** Papers with Code 论文+代码监控（免费API、每日13:00推送+KB写入，核心价值：论文+代码仓库关联） |
| `jobs/openreview/run_openreview.sh` | **V30.5禁用** OpenReview 论文监控（API 403 post-security-incident，已停用） |
| `preference_learner.py` | **V30.4新增** 用户偏好自动学习器（从对话历史推断偏好，写入 status.json） |
| `activate_openclaw_features.py` | **V30.5新增** OpenClaw 功能激活脚本（检查+启用 agent 工具配置） |

## V30.5 变更摘要（2026-03-31）

> 论文监控矩阵扩展 + search_kb 混合检索工具 + 数据复利闭环

### 功能变更

1. **search_kb 自定义工具**：PA 搜索本地知识库的专用工具（和 data_clean 同架构的 proxy 拦截模式）。混合检索：语义搜索（kb_rag.py, sentence-transformers embedding + cosine similarity）优先 + 关键词补充（精确匹配）。搜索结果注入对话后 followup LLM 调用，PA 用自然语言解读结果回答用户。解决了 Qwen3 倾向用 web_search 而非 read 的工具选择偏好问题
2. **DBLP CS论文监控上线**：`jobs/dblp/run_dblp.sh` — 多关键词搜索（LLM/RAG/多模态/对齐等5个领域）、DBLP Search API（免费、CC0、无需认证）、每日12:00 HKT、作者字段 dict/list 归一化
3. **论文监控矩阵完成**：ArXiv + HuggingFace Daily Papers + Semantic Scholar + DBLP + ACL Anthology = 5个来源全覆盖；OpenReview 因 API 403（2025-11安全事件后锁定）已禁用
4. **KB 下游管道扩展**：kb_inject.sh、kb_review.sh、kb_evening.sh 均已添加 5 个新论文来源到 sources_map，每日摘要和周回顾包含全部来源
5. **kb_evening frontmatter 修复**：`grep -v '^---'` 只删除分隔线但保留 `date:`/`tags:` 等元数据行 → 改为完整 Python YAML frontmatter 解析器
6. **SOUL.md search_kb 指令**：PA 行为指令 #1 从"用 read 工具读 KB 文件"改为"调用 search_kb 工具"，并明确禁止用 web_search 代替
7. **ArXiv 429 根因分析**：Fastly CDN（Varnish-based）限速策略变化，非我们使用量问题（8次/天远低于1次/3秒限制）

### search_kb 混合检索架构

```
用户 WhatsApp 问"找关于模型对齐的研究"
  ↓
Gateway → Proxy 注入 search_kb 到 LLM 工具列表（12个工具）
  ↓
LLM 调用 search_kb(query="模型对齐", source="all")
  ↓
Proxy 拦截，本地执行：
  ① 语义搜索：kb_rag.py --json（sentence-transformers embedding → cosine similarity → top 8）
     "模型对齐" → 能匹配 "RLHF alignment"、"preference optimization" 等语义相关内容
  ② 关键词补充：grep sources/*.md + notes/*.md（精确匹配，补充语义搜索遗漏）
  ③ 合并去重（按文件名去重）
  ↓
搜索结果注入对话 → followup LLM 调用（无工具，纯推理）
  ↓
LLM 自然语言解读 → 返回 WhatsApp

两套数据并存：
  ~/.kb/sources/*.md + notes/*.md = 原始文本（人可读，关键词搜索）
  ~/.kb/text_index/vectors.bin    = 384维向量（机器可读，语义搜索）
```

## V30.4 变更摘要（2026-03-28）

> 三方宪法闭环验证 + 方法论进化 + SOUL.md 激活 + OpenClaw 能力深挖

### 功能变更

1. **SOUL.md 激活**：OpenClaw 最高优先级 system prompt，首次写入 PA 身份（Wei）、三方宪法、5 条行为指令、性格定义；部署后 PA 首次正确回答"项目进展如何"（之前说"没有项目"）
2. **SOUL.md 嵌入项目状态**：直接在 SOUL.md 中嵌入任务优先级、最近完成、当前约束、系统健康——PA 无法忽略（之前放在 CLAUDE.md 中被"lost in the middle"）
3. **status.json 扩展为三方共享意识锚点**：新增 `session_context`（开发连续性）、`quality`（安全评分/测试数/覆盖率）、`incidents`（未解决事件，最新在前，上限30条）、`operating_rules`（当前约束/决策）
4. **status.json 跨环境同步**：Mac Mini `kb_status_refresh.sh` 每小时 → git push → Claude Code dev 读取仓库副本；解决 dev 环境 status.json 始终为空的问题
5. **kb_status_refresh.sh 扩展**：新增第6步刷新 SOUL.md 状态区段 + git 同步 SOUL.md
6. **kb_inject.sh 瘦身**：workspace CLAUDE.md 从 17KB → 14KB，身份/宪法/localhost 信息移至 SOUL.md；新增 status snapshot 注入和 SOUL.md 同步
7. **ArXiv seen_ids 修复**：write-after-success 模式，推送失败不标记已发送，下次运行自动重试
8. **Session 重建验证流程**：发现 workspace 文件更新后需清空 session 才能生效（`sessions.json → {}`），已记录为标准操作

### 🔴 V30.4 方法论进化：从 Vibe Coding 到 Outcome-Driven Development

> **2026-03-28 反思**：393 个单测全部通过，但 status.json"共享了个寂寞"——PA 从不引用；SOUL.md 空置数月——最高价值的 LLM 注入点被忽视。根因：我们验证了代码能跑，但没验证系统价值是否实现。

**三个系统性盲区：**

| 盲区 | 具体表现 | 根因 |
|------|----------|------|
| **建设者偏见** | 测了 status.json 能写入，没测 PA 会不会引用 | 验证写入侧，忘了消费侧 |
| **上下文工程缺失** | 17KB CLAUDE.md 塞满细节，SOUL.md 空置 | 没把 LLM 注意力当设计约束 |
| **功能堆积 ≠ 系统进化** | V27→V30.3 加了 40 个文件，但三方宪法核心承诺是断的 | 只做加法，不验证核心价值主张 |

**进化后的三条原则（已写入每次必查）：**
1. **结果验证优先于功能建设** — 先定义"从用户视角，成功长什么样"，再写代码
2. **上下文工程是一等公民** — SOUL.md = 宪法级（身份+状态），CLAUDE.md = 手册级（工具+详情）；信息放哪里、占多少 token、LLM 能否注意到——都是架构决策
3. **定期像用户一样使用系统** — 不是跑单测，而是在 WhatsApp 上实际测试 PA 行为；每次大变更后必须 E2E 验证 PA 的表现

### OpenClaw 未充分利用的能力（三方协作机会）

| 能力 | 当前状态 | 三方协作潜力 |
|------|----------|-------------|
| **memory_search / memory_get** | ⚠️ 已配置但不生效 | Qwen3 不主动调用 memory 工具（V30.4验证），等模型升级后重新验证 |
| **sessions_spawn + sessions_send** | 未使用 | PA 可自主生成子 agent 处理复杂任务（如数据清洗 Phase 2 的三 Agent 架构）|
| **sessions_history** | 未使用 | PA 可回溯过去的对话，实现"你上次说过..."的连续性体验 |
| **session compaction memory** | 被动使用 | 可定制 compaction 策略，确保关键信息（项目状态、用户偏好）在压缩后保留 |
| **queue steer/interrupt 模式** | 未使用 | 紧急告警可中断当前对话直接推送，而非排队等待 |
| **FORCE_SYSTEM injection** | 未使用 | 比 SOUL.md 更高优先级的系统消息覆盖，可用于紧急约束注入 |
| **per-agent tool allow/deny** | 基础使用 | ops agent 可限制为只有运维工具，research agent 只有研究工具，更精准 |
| **sandbox.mode** | 未配置 | 可限制 PA 的文件系统写入范围，防止误操作 |
| **redactSensitive** | 未配置 | 日志中自动脱敏工具调用参数，提升安全性 |
| **healthMonitor per-channel** | 未配置 | 可为 WhatsApp 通道配置独立的健康监控阈值 |
| **agents_list tool** | 未使用 | PA 可查看可用 agent 列表，按需委派任务给不同专业 agent |
| **hot config reload** | 被动 | 可动态调整 agent 配置（如临时切换模型）无需重启 |

## V30.3 变更摘要（2026-03-27）

> 数据清洗工具 + 自定义工具注入机制 + WhatsApp E2E 验证

1. **数据清洗 CLI 工具**：`data_clean.py` — 5 子命令（profile/execute/validate/history/list-ops）+ 7 清洗操作（dedup/dedup_near/trim/fix_dates/fix_case/fill_missing/remove_test）；支持 CSV/TSV/JSON/JSONL/Excel 5 种格式自动检测；版本链 + 原子写入 + 审计日志
2. **自定义工具注入机制**：`proxy_filters.py` 新增 `CUSTOM_TOOLS` 列表，`filter_tools()` 自动注入到 LLM 工具列表中；LLM 像调用 `read`/`write` 一样调用自定义工具，Proxy 拦截执行，Gateway 无感知
3. **自定义工具拦截执行**：`tool_proxy.py` 检测 LLM 响应中的自定义工具调用 → 本地执行 `data_clean.py` → 格式化结果为可读文本 → 直接返回给 Gateway（跳过 followup LLM 调用，更可靠）
4. **Qwen3 `<tool_call>` XML 解析**：Qwen3 可能将 tool_call 嵌入文本内容中作为 XML 标签，新增 `_extract_tool_calls_from_text()` 从文本中提取
5. **LLM 参数容错**：接受 `action:"clean"` 作为 `"execute"` 别名；从 LLM 生成的 `config` 对象中推断清洗操作和目标列
6. **Phase 0 LLM 判断力验证**：3 个脏数据样本（订单/商品/联系人，30 个已知问题）验证 Qwen3 数据质量判断力，覆盖率 90-100%
7. **REST 端点**：`/data_clean/profile`、`/data_clean/execute`、`/data_clean/validate`、`/data_clean/list-ops`、`/data_clean/report`、`/data_clean/help`
8. **WhatsApp E2E 验证通过**：用户发 Excel → PA 调用 data_clean 工具 → Proxy 拦截执行 → 真实清洗结果展示在 WhatsApp
9. **单测 80 个**：格式检测（6）+ 值转换（7）+ TSV 读写（3）+ JSON 读写（8）+ JSONL 读写（4）+ Excel 读写（4）+ 多格式端到端（4）+ 操作逻辑（12）+ 列画像（7）+ 重复检测（3）+ CSV 读写（2）+ 端到端（5）+ 样本文件（4）+ 辅助函数（11）
10. **auto_deploy.sh FILE_MAP 扩展**：新增 `data_clean.py` → `~/data_clean.py`

### 自定义工具注入架构

```
Gateway 发送 6 个工具 → Proxy filter_tools() 注入 data_clean → LLM 看到 7 个工具
LLM 调用 data_clean → 响应回到 Proxy → Proxy 拦截检测
→ 执行 data_clean.py（subprocess） → 格式化结果
→ 替换 tool_call 为文本回复 → 返回 Gateway → WhatsApp 展示

特点：
- Gateway 无感知（不需要修改 Gateway 配置）
- LLM 无抵触（与 read/write 同级的正规工具，不需要 web_fetch localhost）
- 可扩展（CUSTOM_TOOLS 列表可添加更多自定义工具）
```

## V30.2 变更摘要（2026-03-26）

> 安全评分体系 + 审计日志 + 持续安全机制

1. **链式哈希审计日志**：`audit_log.py` — 每次状态变更自动记录（JSONL append-only），每条包含操作者/动作/目标/摘要/前一条SHA256哈希；篡改或删除中间记录可被 `--verify` 检测；`status_update.py` 写入路径自动触发审计
2. **安全评分体系**：`security_score.py` — 7 维度量化评分（密钥管理/测试门禁/数据完整性/部署安全/传输安全/审计追踪/可用性），满分 100；支持 `--json` 脚本调用和 `--update` 写入 status.json
3. **full_regression 扩展至四层**：新增第四层代码质量（代码覆盖率统计 + bandit 静态安全分析 + 审计日志完整性校验），总计 393 个测试用例
4. **单测新增 19 个**：`test_audit_log.py` 覆盖写入/链式哈希/篡改检测/删除检测/尾查/统计/语法/CLI
5. **持续安全提升机制**：每次收工 `security_score.py --update` 写入评分 → 评分趋势可追踪；每次新功能发布 `full_regression.sh` 含 bandit + 审计校验 → 安全不退化

### 持续安全提升机制

| 机制 | 触发时机 | 保障内容 |
|------|----------|----------|
| **发布门禁** | 每次 push 前 `full_regression.sh` | 393 用例 100% 通过 + 安全扫描 + 审计完整性 |
| **安全评分** | 每次收工 `security_score.py --update` | 7 维度量化，分数不允许下降 |
| **审计日志** | 每次 status.json 写入自动触发 | 操作不可否认，链式哈希防篡改 |
| **bandit 静态分析** | 每次 `full_regression.sh` | Python 代码中高危漏洞扫描 |
| **代码覆盖率** | 每次 `full_regression.sh` | 覆盖率趋势监控 |
| **preflight 体检** | 收工前 Mac Mini 运行 | 14 项全面检查（含部署一致性+环境+连通性） |

## V30 变更摘要（2026-03-26）

1. **事故根因：crontab 意外清空**：2026-03-25 添加 `kb_trend.py` 时使用 `echo ... | crontab -`（无 `crontab -l` 前缀），导致 crontab 被替换为只有 1 条条目，其余 18 条全部丢失，所有定时任务停止推送
2. **Crontab 安全操作工具**：`crontab_safe.sh` — `add`（自动备份+条目数验证+回滚保护）、`backup`（手动备份到 `~/.crontab_backups/`）、`restore`（从备份恢复）、`verify`（条目数检查）；**严禁 `echo ... | crontab -`**
3. **Cron 心跳金丝雀**：`cron_canary.sh` 每10分钟写 epoch 到 `~/.cron_canary`（零依赖、零锁文件、原子写入），供 watchdog/doctor 验证 cron daemon 存活
4. **Cron 全面诊断**：`cron_doctor.sh` 7 项检查 — crontab 完整性、陈旧锁文件、cron 心跳、三层服务状态、环境变量(cron模拟)、job 执行时效、系统状态(磁盘/重启/日志)；每项给出修复命令
5. **Watchdog 陈旧锁自愈**：`job_watchdog.sh` 新增陈旧锁自动清理（>1h 的 lockdir 自动 rmdir）、自身锁恢复（>30min 强制清理）、cron 心跳监控；watchdog 不再能被自身锁文件锁死
6. **auto_deploy.sh crontab 监控**：每小时检查 crontab 条目数，低于 10 条立即 WhatsApp 告警 + 每日自动备份 crontab
7. **preflight_check.sh 扩展至 14 项**：新增第 13 项陈旧锁文件检测 + 第 14 项 cron 心跳检测
8. **单测扩展至 157 个**：新增 72 个 cron_health 测试用例（锁检测/心跳解析/告警逻辑/路径一致性/边界条件/脚本语法/原子写入/损坏恢复/告警回退/daemon检测）
9. **原子写入加固**：`proxy_filters.py` `_write_stats()` 和 `mm_index.py` `save_meta()` 均改为 `tmp + os.replace()` 模式，crash 时不损坏目标文件；`mm_index.py` `load_meta()` 新增 `JSONDecodeError` 恢复（备份损坏文件 → 自动重建索引）
10. **本地告警回退**：`job_watchdog.sh` WhatsApp 推送失败时写入 `~/.openclaw_alerts.log`（打破 WhatsApp↔Gateway 循环依赖）；无论推送成功与否都写本地日志；自动截断超过 500 行
11. **Cron daemon 直接检测**：`cron_doctor.sh` 新增 launchctl（macOS）/ pgrep（Linux）直接检测 cron daemon 进程，不依赖心跳文件

### V30 事故反思与系统性缺陷分析

> **核心教训**：系统中的"不可见"风险比"可见"错误更危险。crontab 被清空后所有 job 静默退出（exit 0），无异常日志、无告警、无 crash — 这种"安静的死亡"是最难排查的。

**5 类系统性缺陷已识别并修复：**

| 类别 | 问题 | 修复 |
|------|------|------|
| 1. 静默失败 | `mkdir` 锁获取失败时 `exit 0`（无日志/告警）；`echo \| crontab -` 无验证直接替换 | watchdog 陈旧锁自愈 + crontab_safe.sh 条目数验证 |
| 2. 非原子写入 | `proxy_stats.json`、`mm_index/meta.json` 直接 `open("w")` + `json.dump`，crash 时半写损坏 | 全部改为 `tmp + os.replace()` 原子模式 |
| 3. 监控循环依赖 | 所有告警通过 WhatsApp 推送，但 WhatsApp 依赖 Gateway — Gateway 故障时告警系统失聪 | 新增 `~/.openclaw_alerts.log` 本地回退 + `cron_doctor.sh` 扫描未送达告警 |
| 4. 心跳盲区 | cron daemon 本身停止时无人知道（之前只检查 job 状态文件时效） | `cron_canary.sh` 心跳 + `cron_doctor.sh` daemon 进程直检（launchctl/pgrep） |
| 5. 单点保护不足 | crontab 是所有定时任务的唯一调度源，无备份/无监控/无验证 | 三层保护：预防（crontab_safe.sh）、检测（auto_deploy 条目数监控）、恢复（自动备份+restore） |

**设计原则强化：**
- **任何 `exit 0` 的代码路径都应有日志** — 静默成功和静默失败外观相同
- **共享状态文件必须原子写入** — 被多进程读写的文件（stats/meta/status）一律 `tmp + replace`
- **监控不能依赖被监控对象** — 告警通道必须有独立于被告警服务的回退

## V30.1 变更摘要（2026-03-26）

> 三方宪法安全加固：status.json 实时刷新 + KB 完整性校验 + 原子写入全覆盖

1. **status.json 每小时自动刷新**：`kb_status_refresh.sh` — 聚合三层服务状态、模型ID、KB统计、过期job，写入 `status.json`；PA/Claude Code 读到的不再是几小时前的快照
2. **KB 完整性校验器**：`kb_integrity.py` — SHA256 指纹比对关键文件（index.json/status.json/daily_digest.md）、目录文件数骤降检测、权限检查（other 不可读）、status.json 结构完整性验证；支持 `--init`/`--update`/`--json`
3. **kb_inject.sh 原子写入**：daily_digest.md 和 workspace CLAUDE.md 均改为 `tmp + replace/mv`，防 crash 损坏
4. **KB 目录权限收紧**：`~/.kb/` 设为 750、关键文件设为 640，阻止 other 用户读取
5. **status.json 独立备份**：`openclaw_backup.sh` 新增 status_history/ 目录，每日独立备份，保留 30 天历史；备份后自动刷新完整性指纹
6. **status_update.py 字段扩展**：新增 `kb_stats`、`stale_jobs`、`last_refresh` 字段，供三方实时消费
7. **单测扩展至 179→374 个**：新增 22 个测试（状态刷新/完整性校验/原子写入/备份/字段完整性）+ 全量回归测试框架
8. **全量回归测试框架**：`full_regression.sh` 一键运行 374 个测试用例（12个测试套件+注册表校验+文档漂移+安全扫描），100% 通过才允许推送；新增 `test_status_update.py`（33用例）、`test_adapter.py`（36用例）、`test_kb_business.py`（44用例）

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

# 全量回归测试（393个用例，发布前必须100%通过）
bash full_regression.sh

# 安全评分（7维度100分）
python3 security_score.py
python3 security_score.py --update  # 写入 status.json

# 审计日志
python3 audit_log.py --tail 20      # 查看最近操作
python3 audit_log.py --verify       # 校验链式哈希
python3 audit_log.py --stats        # 统计概览

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

# 数据清洗（支持 CSV/TSV/JSON/JSONL/Excel）
python3 data_clean.py profile data.xlsx --format text   # 数据画像
python3 data_clean.py execute data.xlsx --ops trim,dedup,fix_dates  # 执行清洗
python3 data_clean.py validate original.csv cleaned.csv  # 验证结果
python3 data_clean.py list-ops                           # 可用操作
python3 data_clean.py history data.xlsx                  # 版本历史
curl http://localhost:5002/data_clean/help               # REST 端点帮助

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

### 🔴 每次必查（13条，优先级最高）

| # | 原则 | 一句话 |
|---|------|--------|
| 1 | **开工刷新 OpenClaw 架构（先读决策再评估）** | 先读 `docs/config.md` 中现有的升级 hold 决策和版本状态，再查 OpenClaw 最新 release；如已有明确 hold 决策且上游无新版本，跳过重复评估；有新版本时对比决策条件是否变化，变化则重新评估，否则沿用。**禁止"上游已修复"就改代码——必须确认本地已部署该版本**（#48703教训） |
| 2 | **开工先读 config** | 读 `docs/config.md` 获取系统状态 + 踩坑记录，避免重复犯错 |
| 3 | **开工先读/收工必写 status.json** | `python3 status_update.py --read --human` 查看三方共享状态（优先级、反馈、系统健康）；收工时更新 priorities + recent_changes：`python3 status_update.py --add recent_changes '{"date":"...","what":"...","by":"claude_code"}' --by claude_code` |
| 4 | **改完先测** | 新脚本手动验证 → 新任务先写 `jobs_registry.yaml` 并 `python3 check_registry.py` 通过 → 才能注册 cron |
| 5 | **push前必扫描** | 安全扫描（见上方命令）全部为空才允许 push |
| 6 | **新功能必须 Mac Mini E2E 验证** | dev 环境单测通过不算完成；**必须提醒用户在 Mac Mini 上运行 `bash preflight_check.sh --full` + `bash job_smoke_test.sh` + 手动触发目标 job**，确认端到端有效果（消息到达 WhatsApp / 文件生成 / 日志正常）。dev 通过 ≠ 生产工作。 |
| 7 | **故障先查自身代码** | 排查问题时默认从我们自己的代码和架构中找 bug（shell 数据传递、cron 环境、进程管理等），不归因于上游服务不稳定（#97教训） |
| 8 | **做减法不做加法** | 新增防护/监控前先问"谁已经在管这件事"；每加一层保险 = 多一个故障源（#95教训） |
| 9 | **收工提醒 preflight + 安全评分 + job smoke test + 更新 status.json** | "结束今天的工作"时：① `security_score.py --update` 写入安全评分 ② 提醒用户在 Mac Mini 上运行 `bash preflight_check.sh --full` + `bash job_smoke_test.sh` ③ `status_update.py` 写入变更摘要和优先级 |
| 10 | **相信 OpenClaw，用好 OpenClaw** | 优先利用 OpenClaw 已有能力（Multi-Agent、contextPruning、workspace SOUL.md/CLAUDE.md、memory、sessions_spawn 等），而非重新造轮子；遇到新需求先查 OpenClaw 文档和 release notes |
| 11 | **🆕 结果验证优先于功能建设** | 先定义"从用户视角，成功长什么样"，再写代码。status.json 的成功标准不是"能写入"，而是"PA 能正确回答项目进展"。（2026-03-28教训：393个单测通过但 PA 说"没有项目"） |
| 12 | **🆕 上下文工程是一等公民** | SOUL.md = 宪法级（身份+关键状态，LLM 注意力最高），CLAUDE.md = 手册级（工具+详情）；信息放哪里、占多少 token、LLM 能否注意到——都是架构决策，和 API 设计同等严肃。（2026-03-28教训：SOUL.md 空置数月，17KB CLAUDE.md 信息被"lost in the middle"） |
| 13 | **🆕 定期像用户一样使用系统** | 不是跑单测，而是在 WhatsApp 上实际问 PA 问题。每次涉及 PA 行为的变更后，必须清空 session（`echo '{"sessions":[]}' > sessions.json`）+ 重启 Gateway + WhatsApp 实测。单测验证组件内部，系统价值在组件之间的接缝处。 |

### 🟡 按需查阅（操作 & 架构参考）

<details>
<summary>展开查看完整原则列表（13条）</summary>

**操作类**
- **故障先回滚** — 线上故障 → `git checkout v26-snapshot` 恢复服务 → 再排查根因
- **收工全量同步** — "今天工作结束" → `bash preflight_check.sh` 全面体检 → 扫描全部文档同步当日变更 → 提交推送
- **每日文档刷新** — `CLAUDE.md` + `docs/config.md` + `docs/openclaw_architecture.md` 在开工/收工时强制 read → write
- **纯推理绕过Gateway** — 不需要工具的LLM任务直接 curl 调 API，禁止用 `openclaw agent`（#94）
- **macOS sed禁用OR语法** — `\|` 在 BSD sed 不支持，用 Python 替代
- **禁用交互式编辑器** — git merge 用 `--no-edit`，commit 用 `-m`
- **crontab 安全操作** — **严禁 `echo ... | crontab -`**（2026-03-25事故：清空全部 cron），必须用 `bash crontab_safe.sh add '<行>'`
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
| 中高 | **数据清洗 Phase 2**：三 Agent 架构（Profiler/Planner/Executor，可用 `sessions_spawn` 实现）、语义去重、自定义清洗规则、清洗模板积累到 KB、清洗后文件回传 WhatsApp |
| 中 | **PA 长期记忆**：启用 `memory_search`/`memory_get` 工具，让 PA 跨 session 记住用户偏好。⚠️ V30.4验证：Qwen3不主动调用memory工具（基础设施已就绪，等模型升级后重新验证） |
| 中 | **PA 子 Agent 委派**：利用 `sessions_spawn` + `sessions_send` 让 PA 自主创建子任务（如 research agent 查资料→返回主 agent 汇总） |
| ✅ | **ops agent 激活**：ops_soul.md 运维身份 + 工具白名单(exec/read/write/message/web_fetch) + auto_deploy 部署（V31） |
| 中低 | **安全加固**：配置 `sandbox.mode: restricted` + `redactSensitive: "tools"` 限制 PA 文件系统写入范围和日志脱敏 |
| 中低 | **紧急告警中断**：配置 queue `interrupt` 模式，watchdog 告警可中断当前对话直接推送用户 |
| 低 | 知识图谱：AI大模型领域知识图谱构建（需6-12个月数据积累，暂缓） |
| 低 | 货代Watcher V3：Bing News API替代GoogleNews |
| 低 | 语音消息支持：WhatsApp语音→STT→LLM回复 |
| 低 | MM搜索接入对话：mm_search.py 注册为 OpenClaw tool |
| 低 | KB 静态加密：status.json / index.json 使用 age/gpg 加密存盘 |
| 低 | 依赖漏洞扫描：pip-audit 集成到 full_regression.sh |
| ✅ | **search_kb 混合检索 + 数据复利闭环：语义搜索+关键词+LLM解读（V30.5）** |
| ✅ | **论文监控矩阵：ArXiv+HF+S2+DBLP+ACL 5源全覆盖（V30.5）** |
| ✅ | **三方宪法闭环验证：SOUL.md + status.json 实时同步 + PA 主动感知（V30.4）** |
| ✅ | **数据清洗 Phase 1：CLI + 自定义工具注入 + WhatsApp E2E（V30.3）** |
| ✅ | **安全评分体系 + 审计日志 + 持续安全机制（V30.2）** |
| ✅ | **全量回归测试框架 393 用例（V30.1）** |
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

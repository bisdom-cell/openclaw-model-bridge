# CLAUDE.md — openclaw-model-bridge 项目背景

> 每次新会话开始时自动读取。当前版本：v37.5.1 / 0.37.5（2026-04-11）

---

## 项目简介

将任意大模型（当前：Qwen3-235B + Qwen2.5-VL-72B）接入 OpenClaw（WhatsApp AI助手框架）的双层中间件，支持多模态（图片理解）。
运行于 Mac Mini (macOS)，用户：bisdom。

## 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                 用户层 (WhatsApp + Discord 双通道)                    │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│  ① 核心数据通路（实时对话 + 多模态）                                  │
│                                                                     │
│  WhatsApp ←┐                                                        │
│  Discord  ←┼→ Gateway (:18789) ←→ Tool Proxy (:5002) ←→ Adapter (:5001) ←→ LLM (7 Providers) │
│  notify.sh ┘  [launchd管理]        [策略过滤+监控]       [认证+VL路由]     [Qwen3/GPT-4o/     │
│               [媒体存储]           [图片base64注入]      [Fallback降级]     Gemini/Claude/     │
│               [双通道推送]         [自定义工具注入]                         Kimi/MiniMax/GLM]  │
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
│  每3h    ArXiv论文监控 ──→ KB写入 + WhatsApp+Discord推送                              │
│  每3h    HN热帖抓取 ──→ KB写入 + WhatsApp+Discord推送                                 │
│  每天×3  货代Watcher ──→ LLM分析(直接curl) + KB写入 + WhatsApp+Discord推送             │
│  每天    OpenClaw Releases ──→ LLM富摘要 + KB写入 + WhatsApp+Discord推送              │
│  每小时  Issues监控 ──→ KB写入 + WhatsApp+Discord推送                                 │
│  每天    KB晚间整理                                                                  │
│  每天    KB每日摘要 ──→ ~/.kb/daily_digest.md（LLM对话时可查）                          │
│  每周    KB跨笔记回顾 ──→ LLM深度分析 + WhatsApp+Discord推送                            │
│  每周    健康周报 ──→ WhatsApp+Discord推送                                             │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────────┐
│  ③ 监控层（多级自动告警）                                                            │
│                                                                                     │
│  每30min  wa_keepalive ──→ 真实发送零宽字符 ──→ 失败则记录日志                         │
│  每小时   job_watchdog ──→ 检查所有job状态文件 + 日志扫描 ──→ 超时/失败→WhatsApp+Discord告警│
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
| LLM Providers (7) | — | `providers.py` | **7 Provider**：Qwen3-235B + VL-72B（主力）/ GPT-4o / Gemini 2.5 / Claude / Kimi / MiniMax / GLM | 外部服务 |

## 关键文件（本仓库）

| 文件 | 用途 |
|------|------|
| `tool_proxy.py` | HTTP 层（收发请求、日志、**自定义工具拦截执行**（data_clean本地执行+search_kb混合检索+followup LLM调用）、`/data_clean/*` REST端点） |
| `proxy_filters.py` | **V27新增→V37.4.3扩展** 策略层（过滤、修复、截断、SSE转换、**自定义工具注入**（data_clean+search_kb）、**V37.4.3 新增 `SYSTEM_ALERT_MARKER` + `filter_system_alerts()` 告警剥离函数**），纯函数无网络依赖 |
| `data_clean.py` | **V30.3新增** 数据清洗 CLI 工具（profile/execute/validate/history，7种操作，支持CSV/TSV/JSON/JSONL/Excel） |
| `test_data_clean.py` | **V30.3新增** 数据清洗单测（80个用例：格式检测/读写/操作/端到端/多格式） |
| `docs/archive/data_clean_poc/` | **V30.3新增→V35归档** Phase 0 验证材料（3个脏数据样本+LLM判断力测试脚本） |
| `SOUL.md` | **V30.4新增→V37.4.3升级** OpenClaw 最高优先级 system prompt（PA身份Wei、三方宪法、行为指令、**规则9批判性思考（反迎合+禁模糊关联+保存验证）**、**规则10告警消息不跟进（2026-04-11 血案规则，主题对齐硬规则+macOS FDA 幻觉防线）**、项目状态实时快照，每小时自动刷新） |
| `ops_soul.md` | **V31新增** Ops Agent 运维助手 SOUL.md（系统健康检查/日志排查/cron诊断/维护操作，部署到 `~/.openclaw/SOUL.md`） |
| `ops_health.sh` | **V31新增** Ops Agent 健康检查包装脚本（Qwen3 拒绝直接 curl localhost，通过脚本包装绕过） |
| `status.json` | **V30.4新增（仓库副本）** 三方共享意识锚点（priorities/feedback/incidents/quality/operating_rules/session_context），Mac Mini 每小时 git push 同步 |
| `providers.py` | **V34新增→V35+扩展** Provider Compatibility Layer（BaseProvider 抽象 + **7 个实现**（Qwen/GPT-4o/Gemini/Claude/Kimi/MiniMax/GLM）+ ProviderRegistry 动态注册 + 能力声明 + CLI 兼容性矩阵输出） |
| `test_providers.py` | **V34新增** providers.py 单测（48个用例：能力声明/模型查找/认证头/注册表/向后兼容/CLI输出） |
| `adapter.py` | API适配层（认证 `$REMOTE_API_KEY`，Fallback降级 `$FALLBACK_PROVIDER`）。V34: 从 `providers.py` 导入 PROVIDERS |
| `docs/strategic_review_20260403.md` | **V34新增** 导师战略复盘文档（Stage判断/主战场定位/V1-V3路标/三个高价值模块/话语权输出/三块差距） |
| `docs/compatibility_matrix.md` | **V34新增** Provider 兼容性矩阵（验证状态/降级路径/添加新 Provider 指南） |
| `slo_benchmark.py` | **V35新增** SLO Benchmark 报告生成器（读取 proxy_stats.json 真实数据→Markdown/JSON 报告：延迟p50/p95/p99、成功率、错误分类、降级率、恢复率） |
| `test_slo_benchmark.py` | **V35新增** SLO Benchmark 单测（17个用例：全通过/各类违规/零请求/格式化/文件读取） |
| `quickstart.sh` | **V35新增** 一键 Quick Start（4阶段：前置检查→启动服务→健康验证→Golden Test Trace，10分钟跑通全栈） |
| `docs/golden_trace.json` | **V35新增** Golden Test Trace（quickstart.sh 生成的真实请求/响应/延迟记录，可复现证据） |
| `docs/slo_benchmark_report.md` | **V35新增** 首份 SLO Benchmark 实验报告（5/5 PASS，p95=459ms） |
| `openclaw_backup.sh` | **V29.1新增** 每日Gateway state备份到外挂SSD（保留7天） |
| `jobs_registry.yaml` | **V27新增** 统一任务注册表（system + openclaw 双 cron） |
| `check_registry.py` | **V27新增** 注册表校验脚本 |
| `ROLLBACK.md` | **V27新增** 回滚指南（30秒恢复到V26） |
| `upgrade_openclaw.sh` | Gateway升级SOP脚本（必须SSH直连执行，禁止WhatsApp触发） |
| `restart.sh` | 一键重启 Proxy + Adapter + Gateway（含 PATH 修复，可在 cron 环境使用） |
| `health_check.sh` | 每周健康周报脚本（V27: +JSON输出） |
| `kb_write.sh` | KB写入脚本（含目录锁+原子写） |
| `kb_review.sh` | **V29升级→V37.5 重写** KB跨笔记回顾 thin wrapper（调用 kb_review_collect.py，fail-fast + [SYSTEM_ALERT]，不再机械 fallback） |
| `kb_review_collect.py` | **V37.5新增** KB 周度回顾数据采集器 + LLM 调用（纯 Python 模块：load_sources_from_registry 从 jobs_registry.yaml 读源 + extract_recent_sections H2 drill-down + call_llm 80-char 最小内容阈值 + run() 接受 llm_caller 注入可单测） |
| `test_kb_review.py` | **V37.5新增** kb_review_collect 单测（44 个用例：registry 发现 / H2 parser 边界 / collect_notes 时间过滤 / call_llm 空短失败 / run() 失败不伪装成功 / 输出契约 / shell 源文件锁） |
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
| `auto_deploy.sh` | **V27.1新增** 仓库→部署自动同步 + 漂移检测（md5全量比对+WhatsApp+Discord告警） |
| `test_tool_proxy.py` | proxy_filters 单测（118个用例，含自定义工具注入 + **V37.4.3 新增 21 个告警隔离回归：TestFilterSystemAlerts(14) + TestNotifyShAlertMarker(5) + TestToolProxyImportsAlertFilter(2)**） |
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
| `governance_audit_cron.sh` | **V37.1新增** 每日定时治理审计（governance_checker --full + engine --check，失败推送 alerts），ontology 从被动验证升级为主动监控 |
| `security_score.py` | **V30.2新增** 系统安全评分（7维度100分：密钥/测试/完整性/部署/传输/审计/可用性） |
| `reliability_bench.py` | **V36新增** Agent Reliability Bench（7场景47检查：Provider宕机/工具超时/畸形参数/超大请求/KB未命中/Cron漂移/状态损坏，mock-based可在dev运行） |
| `test_reliability_bench.py` | **V36新增** Reliability Bench 单测（36个用例：7场景×独立验证+报告格式+CLI） |
| `docs/reliability_bench_report.md` | **V36新增** 首份 Reliability Bench 实验报告（7/7 PASS，47/47 checks） |
| `memory_plane.py` | **V36新增** Memory Plane v1 统一接口（4层：KB语义/多媒体/偏好/状态，统一 query/context/stats） |
| `test_memory_plane.py` | **V36新增** Memory Plane 单测（45个用例：4层×可用性/搜索/统计+统一查询+优雅降级+CLI） |
| `docs/memory_plane.md` | **V36新增** Memory Plane 架构文档（分层设计/API/数据流/CLI） |
| `docs/security_boundaries.md` | **V36新增** 安全边界文档（8节：认证/网络/输入验证/数据保护/LLM安全/运维/评分/已知风险+Checklist） |
| `docs/resilience_report.md` | **V36新增** 运维韧性实验报告（7场景故障注入+Recovery Time+GameDay对比+改进建议） |
| `docs/ontology/` | **V36新增** Ontology KB — 本体论驱动的企业智能架构知识库（16文件：三角架构论述/核心概念/AI治理/OpenClaw本体审视/文献/人物/README/流派对比/供应链本体） |
| `ontology/` | **V36.1新增→V37.4.3升级** Ontology 独立子项目 — Tool Engine（81条声明式规则+推理引擎+**classify_tool_call语义分类**）+ **Governance Ontology v3.4**（**28不变式**+67可执行检查+**7元规则**+**MR-7 治理自观察**+**INV-GOV-001 summary 不吞 error**+**INV-DREAM-001/002 + INV-CACHE-002 Dream Map 预算+缓存契约**+**INV-PA-001/002 告警污染+SOUL.md 规则 10 双防线**+验证深度三层模型+Phase 0元规则自主发现+MRD-LAYER-001深度盲区发现）+ governance_checker.py 执行引擎（V37.2+ silent error 修复：error 状态计入 failed_invs）+ 宪法6条 + 语义查询PoC + **Phase 2 shadow模式**(off→shadow→on) + 立场文章(EN+ZH) |
| `ontology/tests/test_governance_cron_matcher.py` | **V37.2+新增** INV-CRON-003/004 匹配器回归测试（18 单测）：① endswith+word-boundary 精确匹配 ② Map-Reduce split 不误报 ③ prefix-subset entries 不混淆 ④ exec() 作用域陷阱正向对照 ⑤ YAML-matcher in-sync guard |
| `ontology/tests/test_governance_summary.py` | **V37.3新增** INV-GOV-001 silent error bug 回归测试（7 单测）：① error 状态注入→汇总不说"所有不变式成立" ② error 状态 exit_code≠0 ③ error 汇总用 💥 ④ pure fail 仍用 ❌ ⑤ all-pass 仍说✅ ⑥ mixed fail+error 各自计数 ⑦ 源码级 grep 守卫 `r["status"] in ("fail","error")` |
| `ontology/docs/cases/governance_silent_error_case.md` | **V37.3新增** Governance 自身三层嵌套盲区案例（子串匹配→exec 作用域→silent error summary）— 观察者的自我盲区 + MR-7 提案 + INV-GOV-001 提案 |
| `ontology/docs/cases/dream_map_budget_overflow_case.md` | **V37.4新增** Dream Map 预算溢出 + cache key 漂移案例（workload 增长 × cache key 对 mtime/sort 敏感 × Reduce 不相信缓存 → 连续超时，Reduce 从未执行）完整因果链架构图 + 三层根因 + 预算/契约/稳定三层修复 |
| `ontology/docs/cases/pa_alert_contamination_case.md` | **V37.4.3新增** PA 告警污染血案案例（系统告警经 Gateway sessions.json 污染对话上下文 × Proxy truncate 无语义理解 × Qwen3 attention 跨主题错误关联 × LLM 编造 macOS FDA 指令）六层根因深挖 + 完整因果链架构图 + 六条件组合爆炸分析 + 结构+行为双防线修复论证 |
| `ontology/tests/test_dream_cache_stability.py` | **V37.4新增→V37.4.2扩展** Dream Fix A+B+C+retry 回归测试（32 单测）：① Fix A cache-only fast path + SKIP_MAP_LOOPS 三处门控 ② Fix B 批次阈值 30/24000 ③ Fix C per-note content_hash 缓存 + 跨平台 md5 fallback + signal dedup ④ 动态 DREAM_TIMEOUT_SEC 预算 ⑤ flush_pending_batch 辅助函数语义 ⑥ Sources 缓存键保持不变 ⑦ **V37.4.1 Retry fallback**：MIN_DREAM_CHARS=3000 / MIN_ACCEPTABLE_CHARS=1500 / BEST_RESULT 追踪 / 温度单调递减 / 致命错误门槛用 MIN_ACCEPTABLE_CHARS ⑧ **V37.4.2 结构+变体**：UNIQ_NOTE_SIG_BLOCKS 数组 / 覆盖统计 header / 编号 cluster header / bland header 已移除 / retry 2+ 变体 prompt / llm_call 引用 \$cur_prompt |
| `kb_dream.sh` | **V36新增→V37.4.2升级** Agent Dream v2 MapReduce 全量 KB 探索引擎（Phase1 Map 14源+293笔记逐一提取信号 → Phase2 Reduce 跨域深度分析；Map-Reduce 分离调度：00:00 Map预热缓存→00:40 Notes 预热→03:00 Reduce 跨域关联+推送）。**V37.4 Fix A+B+C**：Reduce cache-only fast path（SKIP_MAP_LOOPS 门控）+ Notes 批次 15/12000→30/24000 + per-note 缓存（key=md5(content)）+ 动态预算 Map-Only 5400s / Reduce 3600s。**V37.4.1 retry 修复**：MIN_DREAM_CHARS 4000→3000 + BEST_RESULT 追踪防止丢弃好结果 + 温度单调递减 0.85→0.6→0.4 + MIN_ACCEPTABLE_CHARS=1500 真失败 floor。**V37.4.2 结构+变体**：cache-only 读路径带编号总数 header（`笔记信号簇 N/22`）替代 bland 重复 header + retry 2/3 变体 prompt 前缀（`【第 N 次尝试】不少于 2500 汉字`）同时改 hash 绕过 server cache 并强制长度 |
| `gameday.sh` | **V33新增** GameDay 故障演练（5场景：GPU超时/断路器/快照/SLO/Watchdog，`bash gameday.sh --all`） |
| `jobs/dblp/run_dblp.sh` | **V30.5新增** DBLP CS论文监控（多关键词搜索、免费API、每日12:00推送+KB写入） |
| `jobs/hf_papers/run_hf_papers.sh` | **V30.5新增** HuggingFace Daily Papers 监控（热门AI论文、每日10:00推送+KB写入） |
| `jobs/semantic_scholar/run_semantic_scholar.sh` | **V30.5新增** Semantic Scholar 论文监控（引用量排序、每日11:00推送+KB写入） |
| `jobs/acl_anthology/run_acl_anthology.sh` | **V30.5新增** ACL Anthology NLP论文监控（顶会论文、每日09:30推送+KB写入） |
| `jobs/pwc/run_pwc.sh` | **V31新增** Papers with Code 论文+代码监控（免费API、每日13:00推送+KB写入，核心价值：论文+代码仓库关联） |
| ~~`jobs/openreview/`~~ | **V35 已移除**（API 403 post-security-incident，S2 已覆盖顶会论文） |
| `preference_learner.py` | **V30.4新增** 用户偏好自动学习器（从对话历史推断偏好，写入 status.json） |
| `activate_openclaw_features.py` | **V30.5新增** OpenClaw 功能激活脚本（检查+启用 agent 工具配置） |
| `notify.sh` | **V33新增→V35+升级** 统一消息推送（WhatsApp + Discord 双通道 + **自动重试3次指数退避** + **失败队列持久化+自动重放**，`source notify.sh && notify "msg"`） |
| `kb_harvest_chat.py` | **V37新增→V37.1升级** 对话精华提炼器 MapReduce 版（从 proxy 捕获的每日对话中 LLM 分段提取+去重合并，零数据丢失，每日 06:00 cron） |
| `jobs/ontology_sources/run_ontology_sources.sh` | **V37.1新增** Ontology 专属信息源监控（4 RSS: W3C/JWS/DKE/KBS + 两层关键词过滤 + LLM 中文摘要+要点+价值 + Discord #ontology 推送 + KB 归档，cron 10:00/20:00） |
| `providers.d/` | **V37新增** Provider Plugin 目录（YAML/Python 插件自动发现，`_example.yaml` + `_example_provider.py` 示例） |
| `docs/provider_plugin_guide.md` | **V37新增** Provider Plugin Extension Guide（60秒添加新 Provider，合约验证，YAML/Python 双模式） |
| `ontology/docs/architecture/industrial_ai_paradigm.md` | **V37新增** 工业AI范式文档（三平面架构+五项工业需求+范式对比，切断 Dream→PA 链式幻觉） |
| `ontology/docs/architecture/target_architecture.md` | **V37.1新增** Ontology 终态架构（四层设计+六域概念模型+策略引擎+三阶段门控+迁移路径 Phase 3-5） |
| `ontology/docs/cases/pa_echo_chamber_case.md` | **V37.1新增** PA 迎合性回复案例分析（三环反馈陷阱根因+SOUL.md 批判性规则修复+Phase 4 结构性修复路径） |
| `ontology/docs/cases/dream_quota_blast_radius_case.md` | **V37.2新增** Dream MapReduce 配额耗尽跨 Job 爆炸链案例（30+ LLM 调用 × Qwen3 宕机 → Gemini 配额耗尽 → HN 垃圾推送，三层修复+2 新不变式） |

## 版本变更历史

> 完整变更记录见 `docs/changelog.md`，按需 read 查阅。

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| V37.5.1 | 2026-04-11 | **kb_review.sh `pipe+heredoc` stdin 冲突热修复（V37.5 第一次 Mac Mini E2E 即触发的子血案）** — ① **触发**：V37.5 commit db6136f 推送后 Mac Mini 首次 `bash ~/kb_review.sh` 即崩溃 → `File "<stdin>", line 3, in <module>` → `json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`（stdin 读到空串） ② **根因**（经典 shell 反模式）：`echo "$COLLECTOR_OUTPUT" \| python3 - "$REVIEW_FILE" << 'PYEOF' ... json.load(sys.stdin) ... PYEOF` — pipe 把 JSON 喂进 `python3` 的 stdin，heredoc **同时**把代码喂进同一 stdin；`python3 -` 优先消费 heredoc 作为代码源，pipe 的 JSON 被丢弃，`json.load(sys.stdin)` 读到空串直接炸 ③ **为什么 V37.5 的 44 单测 + 14 governance checks 全过却漏掉**：测试全部是声明层 grep guard（文件包含 `fail-fast`、包含 `[SYSTEM_ALERT]`、包含 H2 parser 函数…），**从未真正 `subprocess.run()` 跑一次 shell 脚本** → 直接喂养 **MR-6 (grep 守卫不足，验证深度必须包含运行时层)** ④ **三层修复**：(1) **代码层** `kb_review.sh:154-163`：删除 `echo ... \| python3 - << PYEOF` 反模式，改用 `COLLECTOR_OUTPUT="$COLLECTOR_OUTPUT" REVIEW_FILE="$REVIEW_FILE" python3 << 'PYEOF'` 环境变量模式 — heredoc 只承载代码，零 stdin 竞争 (2) **治理层** `INV-REVIEW-001` +2 checks：`V37.5.1 反模式` python_assert 行扫禁止 `\| python3 -`（跳过 `#` 注释以允许文档引用反模式）+ `V37.5.1 runtime` python_assert **真实 `subprocess.run()`** 跑 kb_review.sh（mock collector 喂 canned JSON，断言 review 文件落盘 + stderr 不含 `JSONDecodeError`）(3) **测试层** `test_kb_review.py` +4 单测（44→48）：`test_no_pipe_heredoc_stdin_collision`（静态守卫）+ `TestKbReviewShellRuntime` 新类 3 测试：`test_env_var_heredoc_pattern_writes_file`（正向隔离验证）+ `test_pipe_heredoc_antipattern_actually_fails`（负向反证 JSONDecodeError）+ `test_kb_review_sh_end_to_end_mock_collector`（真 E2E subprocess + 临时目录 + mock collector） ⑤ **Mac Mini 生产 E2E 验证通过**：`bash ~/kb_review.sh` → `开始 LLM 深度分析（7 天回顾，from registry）... 回顾文件已生成: /Users/bisdom/.kb/daily/review_20260411.md ... 回顾已推送（WhatsApp + Discord #daily） ... 知识回顾 20260411 \| 覆盖 12 源 \| 本期笔记 299 篇 \| LLM: ✓` — 验证了 (a) registry-driven 发现 12 源（governance 断言 `>=12` ✓）(b) H2 drill-down 从 7 天窗口提取 299 notes (c) LLM 调用成功未触发 fail-fast (d) V37.5.1 env var 模式写入 review 文件零 JSONDecodeError (e) notify.sh 双通道推送 WhatsApp + Discord #daily 成功 ⑥ **governance_ontology.yaml v3.5 → v3.5.1**（29 不变式不变，81→83 checks）⑦ **血案元认知**：同一个 MR-4 血案类三天内第二次演出（V37.5 主血案 → V37.5.1 子血案），子血案证明**主血案修复本身也可能埋坑**，`test_kb_review.py::TestKbReviewShellGuards` 全部 grep 通过但从未运行脚本 → MR-6 不再是"建议"而是"基础假设" |
| V37.5 | 2026-04-11 | **kb_review 6-issue silent degradation 血案闭环修复（fail-fast + registry-driven + H2 drill-down）** — ① **血案类归属**：与 V37.4.3 PA 告警污染同属 MR-4 (silent failure is a bug) 效果层静默降级血案类——声明层检查全过、status.json 全绿，但端到端效果完全失效 ② **6 个相互掩护的 bug**：(1) shell `export NOTES_CONTENT` 在 `python3 -c` 子进程调用**之后**才写入 → subprocess fork 时 env 快照已定型，`os.environ.get()` 永远拿空字符串 → LLM 收到空壳 prompt (2) 硬编码源枚举 7 项，漏掉 V37 新增 `ai_leaders_x` + V37.1 新增 `ontology_sources` (3) LLM 失败后**机械 fallback** 把"行级日期匹配"的容器标题残渣（例如 `## 今日arXiv精选(2026-04-10)`）直接写入 review 文件伪装成功 (4) `status.json` 永远硬写 `"llm": true`，不区分 ok/failed (5) 行级日期匹配只保留含日期的单行，把整个 H2 section 的论文 body 全部过滤掉 — 结构化 markdown 粒度错配 (6) 悬空承诺"回复任何话题可深入讨论"但 LLM 从未真正看过回顾内容 ③ **结构性修复**：(1) **全 Python 化** `kb_review_collect.py` 作为纯模块（load_sources_from_registry + extract_recent_sections H2 drill-down + call_llm 80-char 最小阈值 + run(llm_caller=...) 可注入 mock），消除 shell scope bug class (2) **jobs_registry.yaml 扩展 `kb_source_file` + `kb_source_label` 字段到 12 个 enabled source-producing job**（arxiv/hf_papers/semantic_scholar/dblp/acl_anthology/github_trending/rss_blogs/ai_leaders_x/freight/openclaw_run/hn/ontology_sources），registry-driven 消除硬编码漂移 (3) **kb_review.sh 重写为 thin wrapper**：调用 collector 后按 status 分派 — `ok` 写 review 文件 + 推送 daily topic，`llm_failed`/`collector_failed`/`send_failed` 均推送 `[SYSTEM_ALERT]` + `exit 1` fail-fast (4) status.json 诚实字段 `llm_status: ok\|failed\|unknown`，不再永远写 true (5) 移除悬空 follow-up 承诺 ④ **INV-REVIEW-001** `kb-review-fails-loud-not-silent`（severity=critical, verification_layer=[declaration, runtime]）：14 个 check 覆盖声明层（V37.5 版本标记/`[SYSTEM_ALERT]` 存在/不含机械 fallback/不含悬空承诺/三个核心函数定义/jobs_registry.yaml kb_source_file 字段/**fail-fast 顺序锁 python_assert**（llm_failed 分支 500 字符内必须 exit 1））+ 运行时层（**registry 真实发现 >=12 源含 ai_leaders_x + ontology_sources**/**H2 parser drill-down 过滤正确性**（窗口内 Paper A 保留 + 窗口外 Paper B 剔除 + 今日 Paper C 保留）/**mock LLM 失败契约**（status=llm_failed 不伪装 ok + 不产出 review_markdown/wa_message 伪造产物）/call_llm 最小内容阈值声明） ⑤ **新增 44 单测** `test_kb_review.py`：TestLoadSourcesFromRegistry(7) + TestExtractRecentSections(8) + TestCollectNotes(4) + TestCallLlm(6, urlopen mock) + TestRunOrchestrator(6, llm_caller 注入) + TestBuildOutputs(4) + TestCollectSources(1) + TestKbReviewShellGuards(8) ⑥ **案例文档** `ontology/docs/cases/kb_review_silent_degradation_case.md`（四维度完整因果链架构图 + 三层根因 + 时间线还原 + 6-bug 条件组合分析 + V37.4.3 血案类对照表 + MR-4 持续扩展 + 元规则 MR-8 候选"跨文件枚举必须声明单一真理源"） ⑦ **governance_ontology.yaml v3.4 → v3.5**（28→29 不变式，67→81 checks） ⑧ 接入 full_regression.sh 第一层 |
| V37.4.3 | 2026-04-11 | **PA 告警污染血案闭环修复（A+C 双防线）** — ① **事件**：13:06 用户问"AI Agent 终极架构：本体×随机×贝叶斯"，PA 回复"已收到系统告警跟进任务，请打开系统偏好设置添加 /usr/sbin/cron 到完全磁盘访问权限"——完全答非所问 + 编造错误的 macOS FDA 指令（launchd 管理的 cron 从不需要 FDA）② **根因六层深挖**：(a) 12:30 job_watchdog 通过 `notify.sh --topic alerts` 推送 WARNING (b) Gateway 把推送写入 `sessions.json` 作为 `assistant` role 消息——**不区分"PA 主动回答"和"系统告警推送"** (c) Proxy `truncate_messages()` 保留"最近 N 条"，36 min 内的告警仍在窗口内 (d) Qwen3 attention 读到 assistant 告警中的 "排查建议" + "cron_doctor.sh"，把它当"PA 自己说过的话" (e) 用户新问题被跨主题错误关联为"告警跟进响应" (f) LLM 编造 macOS FDA 指令（training data 模板填空）③ **Path A 结构性隔离**（主防线）：(1) 推送侧注入标记——`notify.sh` 在 `topic=alerts` 分支、`auto_deploy.sh` 在 `quiet_alert()`、`run_hn_fixed.sh`/`jobs/openclaw_official/run.sh`/`run_discussions.sh` 的直接 send 路径，统一在告警消息首行加 `[SYSTEM_ALERT]`  (2) 消费侧剥离——`proxy_filters.py` 新增 `SYSTEM_ALERT_MARKER = "[SYSTEM_ALERT]"` 常量、`filter_system_alerts(messages, log_fn)` 函数（str content + OpenAI content blocks 双格式支持，system role 保留例外，`_message_starts_with_alert_marker` 只在首行匹配避免误伤）(3) **顺序硬约束**——`tool_proxy.py` 在 `truncate_messages()` **之前**调用 `filter_system_alerts()`（单测 `test_integration_with_truncate` + INV-PA-001 的 python_assert 检查源码中位置 `filter_idx < trunc_idx`，双重锁定）④ **Path C 治理+行为双防线**：(1) **SOUL.md 规则 10 "告警消息不跟进（2026-04-11 血案规则）"**——最高优先级的问答对齐规则，包含：识别标志（`[SYSTEM_ALERT]` + 告警模式）、禁止行为（已收到跟进/系统偏好指令/编造 macOS FDA）、**主题对齐硬规则**（回复必须与用户最新消息主题直接对齐）、幻觉防线（launchd 不需要 FDA）、13:06 案例警示原文 (2) **INV-PA-001** `alert-does-not-pollute-chat-context`（severity=critical, verification_layer=[declaration, runtime]）：10 个 check 覆盖 SYSTEM_ALERT_MARKER 定义 + filter_system_alerts 函数 + tool_proxy 导入 + **顺序锁 python_assert**（filter_idx < trunc_idx）+ 5 个推送脚本标记注入 + **运行时 python_assert**（真实告警消息注入 → filter 正确剥离，4 个 sub-assertion：普通 assistant 告警剥离/system role 保留/OpenAI content blocks/标记只在开头匹配不误伤）(3) **INV-PA-002** `soul-rule-10-present`（severity=high, verification_layer=[declaration]）：5 个 check 确保 SOUL.md 包含规则 10 核心声明（"告警消息不跟进" / "2026-04-11 血案规则" / "主题对齐" / "完全磁盘访问权限" / `[SYSTEM_ALERT]`）⑤ **案例文档** `ontology/docs/cases/pa_alert_contamination_case.md`（六层根因深挖 + 完整因果链架构图 + 时间线 + **六条件组合爆炸分析**：告警频率/时间差/可执行触发词/话题跳变/SOUL 无隔离规则/Proxy 无剥离——六个条件同时出现才爆炸 + 纵深防御论证）⑥ **新增 21 单测**：`TestFilterSystemAlerts`（14 个：marker 常量/assistant 剥离/user 剥离/system 保留/content blocks/leading whitespace/空消息/缺失 content/log_fn 调用/truncate 集成/多告警全剥离等）+ `TestNotifyShAlertMarker`（5 个：notify.sh/auto_deploy/run_hn_fixed/run_discussions/run.sh grep）+ `TestToolProxyImportsAlertFilter`（2 个：import + 顺序）⑦ **governance_ontology.yaml v3.3 → v3.4**（26→28 不变式，52→67 checks）⑧ **全量回归**：29 suites / **807 tests** / 0 fail / 28/28 invariants / 56/67 checks / 安全评分 93/100 |
| V37.4.2 | 2026-04-11 | **Dream Reduce 短输出 bug 二段闭环修复** — ① 触发：V37.4.1 Mac Mini 14:01 run 三次重试全是 876/876/927 chars，完全一致的失败输出 → 定位根因是 Qwen3 server-side prompt caching + 缓存读取路径 bland 重复 header 让 LLM 陷入"简短总结"收敛模式 ② **结构化修复**（cache-only 读取路径 line 414-465）：用 bash 数组 `UNIQ_NOTE_SIG_BLOCKS=()` 收集去重后的 signal blocks → 拼接带覆盖统计的 meta header（`> 覆盖 N 条用户笔记，提取出 M 个独立信号簇`）→ 每簇编号 `### 笔记信号簇 i / N` 赋予独立身份；Phase 1b cache-hit header 同步改为 `## 用户笔记缓存命中: $name`（用文件名区分）③ **重试变体修复**（line 1011-1024）：retry=1 用原 `REDUCE_PROMPT`，retry≥2 prepend `【第 N 次尝试 — 上一次响应过短，不合格】` + 显式硬性要求"6 章节全覆盖 + 不少于 2500 汉字"，变体 prompt prefix 改变 hash 直接绕过任何 server cache + 显式长度约束顶破收敛 ④ 新增 6 单测 `TestV37_4_2_CacheReadStructureAndRetryVariation`（UNIQ_NOTE_SIG_BLOCKS 数组/覆盖 meta header/编号簇/bland header 移除/变体 prompt/llm_call 用 $cur_prompt）⑤ **Mac Mini 15:02 生产验证**：retry 1 = 1402 chars 短仍被 server cache 命中（证据确凿）→ retry 2 变体 prompt = **21871 chars / 7000 汉字**，6 章节完整展开，9m24s 总耗时，3/3 WhatsApp+Discord 推送成功，内容识别出"本体+LLM+Agent 深度融合"主题并给出可执行建议 ⑥ 32 suites / 32 Dream 单测 / 0 fail |
| V37.4.1 | 2026-04-11 | **Dream Reduce 重试降级机制 第一次尝试** — ① 触发：V37.4 Mac Mini Run 1 (c957075) Fix A cache-only fast path 生效（11s 缓存加载），但 Phase 2 Reduce retry 1 = 3967 chars → retry 2 = 906 chars 直接砸穿，用户反馈"已经超过5min 触发LLM重试" ② **重试降级修复**（line 975-1045）：引入 `BEST_RESULT` 跟踪历次最长结果 + 温度衰减数组 `REDUCE_TEMPS=(0.85 0.6 0.4)` + 双阈值 `MIN_DREAM_CHARS=3000`（目标 ≈1000 汉字）/ `MIN_ACCEPTABLE_CHARS=1500`（真正失败底线）+ fallback `DREAM_RESULT=$BEST_RESULT` 保留最佳尝试 + fatal exit 用 MIN_ACCEPTABLE_CHARS 而非 MIN_DREAM_CHARS 以避免可接受输出被错误丢弃 ③ 新增 6 单测 `TestReduceRetryFallback`（MIN_DREAM_CHARS 阈值/REDUCE_TEMPS 衰减/BEST_RESULT 跟踪/fallback 赋值/fatal 底线阈值/retry 循环结构）④ **验证失败**：Mac Mini Run 2 (8dd0260) 三次重试 876/876/927 chars 完全一致 → 暴露"简单重试无法突破 server-side prompt caching"这一更深层根因，为 V37.4.2 铺路 |
| V37.4 | 2026-04-11 | **Dream Map 预算溢出 + cache key 稳定性 三层修复** — ① 触发：2026-04-11 `--map-notes`(00:40) + full run(03:00) 连续 60 min 超时，Reduce 从未执行，WhatsApp 告警"Dream 失败"。根因三层：(a) 286 notes × 76s/batch ÷ 4.4 = **82 min > 60 min** workload 数学溢出 (b) Reduce full run 重跑 Phase 1a/1b LLM 循环，不相信缓存 (c) Notes cache key = md5(批次拼接) 对 mtime/sort/batch 组合敏感，00:40 写的缓存 03:00 全部 miss ② **Fix A (契约)**：Reduce path 新增 cache-only fast path — 扫 `$MAP_DIR` 填充 MAP_SIGNALS / NOTES_SIGNALS，`SKIP_MAP_LOOPS=true` 门控 Phase 1a + Phase 1b + Fast-mode elif，03:00 full run 从 60 min 压到 <5 min ③ **Fix B (效率)**：Notes 批次 15 条/12000B → **30 条/24000B**，LLM 调用次数减半（~47→~24） ④ **Fix C (稳定)**：per-note cache，key=`md5(content)` 前 12 位（+ macOS `md5 -q` fallback），同 batch N 个 note 共享 signals（dedup via signal hash），mtime/sort/batch 组合漂移都不再触发 miss；第二天只有新增 notes 是 cache miss ⑤ **预算动态化**：`DREAM_TIMEOUT_SEC` 按 MAP_ONLY 5400s (90min) / 3600s (60min) 分配；`DREAM_TIMEOUT_SEC_OVERRIDE` env 支持调试 ⑥ 案例文档 `ontology/docs/cases/dream_map_budget_overflow_case.md`（四维度因果链 + 三层根因 + 时间线 + 条件组合 + 设计决策）⑦ 3 新不变式 `INV-DREAM-001` (map-budget-scales-with-mode) + `INV-DREAM-002` (reduce-path-must-not-re-run-map-loops, severity=critical) + `INV-CACHE-002` (notes-cache-key-stable-under-mtime-drift, severity=critical)，governance v3.2 → v3.3, 23→26 不变式 / 43→52 检查 ⑧ `test_dream_cache_stability.py` 20 单测锁定三 fix + 预算动态 + flush 辅助函数 + Sources 键不变 ⑨ 接入 full_regression.sh → **29 suites / 774 tests / 0 fail / 26/26 invariants / 41/52 checks** |
| V37.3 | 2026-04-11 | **Governance 自观察元规则 MR-7 + INV-GOV-001 summary-counts-all-non-pass** — ① 案例文档 `ontology/docs/cases/governance_silent_error_case.md`（四维度因果链架构图 + 三层根因 + 时间线 + 条件组合 + 本体喂养）完整分析 V37.2+ 三层嵌套盲区：子串匹配→exec 作用域陷阱→silent error summary，提炼"观察者的自我盲区"元洞察（治理系统从未对自己应用 MR-4） ② 新增 **MR-7 governance-execution-is-self-observable**：治理系统自身是一等被治理对象，check 层所有状态（pass/fail/skip/error）必须在汇总层有观察路径，不允许聚合时被透明化 ③ 新增 **INV-GOV-001 governance-summary-counts-all-non-pass**（meta_rule: MR-7，severity: critical，verification_layer: [declaration, runtime]）：声明层 grep 守卫 `r["status"] in ("fail","error")` + 运行时层构造 error 注入 probe 验证汇总不说"所有不变式成立"（含 JSON_MODE 全局状态 save/restore，解决嵌套调用视角污染） ④ 新增 `ontology/tests/test_governance_summary.py` 7 单测专门锁定 bug 3：error 状态不被吞、💥 图标正确、pure fail 仍用 ❌、all-pass 仍说 ✅、mixed fail+error 各自计数、源码级 grep 守卫 ⑤ 接入 full_regression.sh，**28 suites / 754 tests / 0 fail, governance 23/23 invariants / 32/43 checks** ⑥ governance_ontology.yaml 升级 v3.1 → v3.2（total_invariants: 22→23, meta_rules: 6→7, total_checks: 41→43） |
| V37.2+ | 2026-04-11 | **Governance INV-CRON 三层 bug 修复 + silent error detection 加固** — ① INV-CRON-003/004 子串匹配 false-positive（`kb_dream.sh` 被 Map-Reduce split 误报 ×3，`sname in l` 无法区分 prefix-subset entries）→ `endswith(entry)` + word-boundary `cmd[idx-1] in "/ \t\"'"` 精确匹配 ② YAML `_exec_python_assert` exec() 作用域陷阱（`def _cron_cmd_invokes` 在 exec 局部，`sum(1 for l in lines if ...)` 生成器表达式新作用域看不到，`NameError` 每次都炸）→ for-loop 替代生成器表达式 ③ `governance_checker.py` silent error bug（`failed_invs` 只数 `status=="fail"`，`error` 状态被忽略，💥 icon 藏在正文，汇总行显示 ✅ 所有不变式成立）→ 区分 `fail`/`error` 并在汇总行显式标注（`❌ N 违反, 💥 M 出错`） ④ 2 个新回归测试锁定：`test_yaml_no_generator_expression_over_cron_cmd_invokes`（grep 守卫）+ `test_exec_scope_trap_is_real`（正向对照，自带 Python 语义文档） ⑤ Mac Mini 生产验证：governance `--full` 40/41+💥 → **41/41 ✅**，preflight 75/1/2 → **76/0/2**（`kb_embed.py` 补齐 3 未索引 notes + 2 过期文件）⑥ 27 suites / **747 tests** / 0 fail |
| V37.2 | 2026-04-10 | **系统韧性加固 + Adapter Hot-Reload + 幻觉工具过滤 + 异常分析宪法** — ① Dream 配额爆炸链修复（智能退避 429/524 + Map 熔断 3 次 + inter-call 节流）② HN 垃圾推送修复（RSS 描述注入 + JSON 输出 + `__LLM_FAILED__` 信号 + 3 次重试）③ Adapter FALLBACK_CHAIN hot-reload（`_build_fallback_chain()` 提取 + daemon 线程 + feature flag `ADAPTER_HOT_RELOAD` + /health 暴露）④ Proxy 幻觉工具过滤（`<tool_call>` XML 清理：主响应路径 + search_kb followup 路径）⑤ 原则 #26 升级为宪法级异常分析五步法 ⑥ Dream 配额爆炸链案例文档 + 2 新治理不变式（INV-QUOTA-001/INV-PUSH-001，19 不变式）⑦ 718 测试 |
| V37.1 | 2026-04-09 | **Ontology 信息源 + 对话数据零丢失 + 治理主动监控 + PA 批判性思考** — ① Ontology 专属信息源（4 RSS: W3C/JWS/DKE/KBS + 两层关键词过滤 + LLM 摘要 + Discord #ontology + KB 归档，cron 10:00/20:00）② kb_harvest_chat MapReduce 升级（分段提炼+去重，零对话数据丢失，28 单测）③ DBLP/S2 加 ontology 关键词 ④ X 监控加 4 位 ontology 先驱（Barry Smith/Guizzardi/Hitzler/Horrocks）⑤ adversarial_audit 合并入 governance_ontology（17 不变式）⑥ 每日 governance_audit cron（07:00 自动执行+失败告警）⑦ SOUL.md 规则 9 批判性思考（反迎合+禁模糊关联+PA 回声室案例分析）⑧ Ontology 终态架构文档（四层+三阶段门控+迁移 Phase 3-5）⑨ ScienceDirect 描述正则修复 ⑩ 692 测试 |
| V37 | 2026-04-08 | **V3 路标启动 + LLM 协作方法论 + 对话数据闭环** — ① Provider Plugin Interface（YAML/Python 插件+合约验证+Extension Guide+128单测）② Capability-Based Routing（find_by_capability+build_fallback_chain+auto-discovery fallback chain 接入 adapter.py）③ 对话数据闭环（proxy 热路径捕获→kb_harvest_chat.py 冷路径提炼→MEMORY.md 索引→KB 可检索）④ KB 统一（HN+Freight 双写 notes 对齐 12/12 job）⑤ LLM 协作 4 条新原则（#22 顺势设计/#23 链式幻觉防范/#24 触发词机制/#25 对话数据一等公民）⑥ industrial_ai_paradigm.md 切断幻觉链 ⑦ 692 测试 |
| V36.3 | 2026-04-08 | **Runtime Governance + Ontology Shadow Mode** — ① 遗留修复(crontab漂移3job+重复清理35→28+DBLP/Dream推送恢复+notify.sh zsh兼容+smoke test python3检测) ② Governance v3(12→15不变式: 运行时层INV-CRON-003/004+INV-ENV-002, MR-6多层深度要求) ③ 验证深度三层模型(声明/运行时/效果, governance自我意识盲区, MRD-LAYER-001) ④ 语义推理落地(classify_tool_call从属性推理risk_level+policy_tags) ⑤ Phase 2 shadow模式(off→shadow→on三档, Mac Mini生产上线) ⑥ 832测试 |
| V36.2 | 2026-04-07 | **Governance Ontology + 对抗审计体系** — ① Dream修复(printf注入+残留锁) ② 凌晨静默期(00-07零推送) ③ Crontab漂移检测(registry vs crontab) ④ 对抗审计22个声明-实际断裂→adversarial_audit.py(9项) ⑤ governance_ontology.yaml v2(12不变式+28可执行检查+5元规则) ⑥ Phase 0元规则自主发现(23个未覆盖job) ⑦ 工具数量硬断言(MAX_TOOLS import+截断) ⑧ 立场文章(EN+ZH) + Ontology子项目深度建设(Tool Engine+宪法+PoC+特性开关) |
| V36.1 | 2026-04-06 | **Agent Dream v2 + Ontology KB** — MapReduce 全量 KB 探索（14源+226笔记）+ Notes 一等信号 + Ontology KB 创建（7文件）+ 论文监控加 ontology 关键词 + X 监控 9→15 人（+Palantir）+ Cron 调度优化（~55→30 次/天）+ 凌晨 GPU 黄金窗口 |
| V36 | 2026-04-05 | **V2 路标双P0完成** — Agent Reliability Bench（7场景47检查） + Memory Plane v1（4层统一接口+45单测+架构文档） + 560 测试 |
| V35 | 2026-04-05 | **V1 路标冲刺** — SLO Benchmark 实验报告 + Quick Start 一键 demo + Golden Test Trace + Sub-agent PoC（链路通，deferred 等模型升级）+ 605 测试 |
| V34 | 2026-04-03 | **Stage2 启动** — Provider Compatibility Layer + 导师战略复盘嵌入治理体系 + V1/V2/V3 路标 + 461 测试 |
| V33 | 2026-04-03 | Discord 双通道支持 + 统一推送 notify.sh + Gateway 不升级可用 |
| V30.5 | 2026-03-31 | search_kb 混合检索 + 论文监控矩阵 5 源全覆盖 + DBLP 上线 |
| V30.4 | 2026-03-28 | SOUL.md 激活 + 三方宪法闭环 + 方法论进化（结果验证优先） |
| V30.3 | 2026-03-27 | 数据清洗 Phase 1 + 自定义工具注入机制 + WhatsApp E2E |
| V30.2 | 2026-03-26 | 安全评分体系 + 审计日志 + 持续安全机制 |
| V30.1 | 2026-03-26 | status.json 实时刷新 + KB 完整性 + 全量回归 374 测试 |
| V30 | 2026-03-26 | crontab 事故修复 + cron 安全三层保护 + 原子写入加固 |
| V29.5 | 2026-03-25 | KB 周趋势报告 + 模型智能路由 + 三方共享状态 |
| V29.4 | 2026-03-25 | 多模态图片理解（Qwen2.5-VL-72B 自动路由） |
| V29.3 | 2026-03-24 | 本地 Embedding + KB RAG 语义搜索 |
| V29.1 | 2026-03-14 | Fallback 降级链 + 每日备份 + Multi-Agent + MM 索引 |
| V29 | 2026-03-13 | KB 搜索/LLM 回顾/每日摘要注入 |
| V28.x | 2026-03-11~13 | 线程化 + preflight 体检 + WhatsApp 保活 + OpenClaw 架构同步 |
| V27.x | 2026-03-10 | Proxy 拆层 + 任务注册表 + auto_deploy + 回滚机制 |

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

# 全量回归测试（461个用例，发布前必须100%通过）
bash full_regression.sh

# 治理审计（ontology-native，23不变式+7元规则+6元发现）
python3 ontology/governance_checker.py              # dev 模式
python3 ontology/governance_checker.py --full        # Mac Mini（含 crontab/env/服务）
python3 ontology/governance_checker.py --json        # JSON 输出

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

# Quick Start（一键 10 分钟跑通全栈）
bash quickstart.sh                # 完整 4 阶段（前置检查→启动→健康→demo）
bash quickstart.sh --check        # 仅检查前置条件
bash quickstart.sh --demo         # 仅运行 demo 请求

# Memory Plane（统一记忆平面）
python3 memory_plane.py layers                    # 层可用性检查
python3 memory_plane.py stats                     # 各层统计
python3 memory_plane.py query "Qwen3"             # 统一搜索
python3 memory_plane.py query --context "AI论文"   # LLM可注入格式
python3 memory_plane.py query --layers kb "RAG"    # 仅搜索KB层

# Agent Dream v2（Map-Reduce 三阶段分离调度）
bash kb_dream.sh --map-sources # Sources Map 预热（00:00 cron，~10min）
bash kb_dream.sh --map-notes   # Notes Map 预热（00:25 cron，~10min）
bash kb_dream.sh              # Reduce：两层缓存命中→跨域关联→推送（03:00 cron，~3.5min）
bash kb_dream.sh --map-only   # 全部 Map 预热（Sources + Notes，~20min）
bash kb_dream.sh --dry-run    # 素材统计（不调 LLM）
bash kb_dream.sh --fast       # 跳过 Map，直接采样做梦（旧模式）
cat ~/.kb/dreams/2026-04-06.md  # 查看梦境结果

# Agent Reliability Bench（7场景故障评测）
python3 reliability_bench.py            # Markdown 报告
python3 reliability_bench.py --json     # JSON 格式
python3 reliability_bench.py --save     # 保存到 docs/reliability_bench_report.md
python3 reliability_bench.py --scenario 3  # 运行单个场景

# SLO Benchmark（真实生产数据报告）
python3 slo_benchmark.py          # Markdown 报告
python3 slo_benchmark.py --json   # JSON 格式
python3 slo_benchmark.py --save   # 保存到 docs/slo_benchmark_report.md

# Provider 兼容性矩阵
python3 providers.py              # Markdown 表格
python3 providers.py --json       # JSON 格式（供脚本调用）

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

### 🔴 战略定位（2026-04-03 导师评审确立）

> **项目已跨过关键门槛：Stage 1 完成（系统构建者）→ 正在冲刺 Stage 2（被社区认可的系统作者）。**
>
> **主战场**：不再是抽象的"LLM推理系统"，已收敛为两条线：
> 1. **Agent Runtime / Inference Gateway / Control Plane** — 模型接入 + 工具治理 + 可观测性 + 故障恢复
> 2. **Agent Memory Plane / KB-RAG / Job-Orchestrated Intelligence** — 记忆系统 + 知识检索 + 作业编排
>
> **旗舰叙事**：`OpenClaw Runtime Control Plane for Tool-Calling Agents`
>
> **核心洞察**：项目最强的不是单独算法，而是把模型接入、工具治理、可观测性、故障恢复、记忆与作业系统编织成可运行的 agent runtime。顶级专家不是死守最初设想，而是顺着已证明的能力继续放大。
>
> **12 个月路线图**：
> - **V1（0-4月）别人能跑**：安装稳定 / 配置清晰 / 文档闭环 / 最小 demo / golden test trace
> - **V2（4-8月）别人敢用**：benchmark / SLO dashboard / incident drill / 兼容矩阵 / semver
> - **V3（8-12月）别人会扩展**：provider plugin / tool policy plugin / memory plane plugin / SDK / extension guide
>
> **距离世界顶级的三块差距**：可迁移性（去硬编码场景依赖）/ 证据密度（benchmark+演练+案例）/ 话语权输出（代码+文档+评测+方法论=完整叙事）
>
> **详细战略文档**：`docs/strategic_review_20260403.md`

### 🔴 每次必查（20条，优先级最高）

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
| 9 | **🔴 收工必须执行完整检查清单（不可跳过）** | "结束今天的工作"时，**必须逐项执行以下清单，每项完成后打勾确认，禁止跳过或合并**：**A. 全量回归** `bash full_regression.sh`（必须 0 fail，完成后自动更新 status.json test_count）**B. 安全评分** `python3 security_score.py --update` **C. 安全扫描** API key + 手机号 + BSA 泄漏扫描 **D. 治理审计** `python3 ontology/governance_checker.py` **E. 文档刷新**（逐一 check）：`status.json`（recent_changes/session_context/focus）→ `CLAUDE.md`（版本/文件表/changelog/待办交叉校验）→ `docs/config.md`（如有配置变更）→ `README.md`（如有架构变更）→ `SOUL.md`（如有 PA 行为变更）→ `docs/ontology/`（如有 ontology 变更）**F. 交叉校验** 原则#17：commits vs CLAUDE.md 待办一致性 **G. 全部提交推送** **H. 提醒 Mac Mini 验证** `preflight_check.sh --full` + `job_smoke_test.sh` — **不是"提醒"而是必须等用户执行并确认结果** **I. 遗留问题登记** 未完成事项写入 session_context.unfinished。（2026-04-08教训：长 session 后"赶紧结束"心态导致跳过 6 项检查，用户两次追问才补齐） |
| 10 | **相信 OpenClaw，用好 OpenClaw** | 优先利用 OpenClaw 已有能力（Multi-Agent、contextPruning、workspace SOUL.md/CLAUDE.md、memory、sessions_spawn 等），而非重新造轮子；遇到新需求先查 OpenClaw 文档和 release notes |
| 11 | **🆕 结果验证优先于功能建设** | 先定义"从用户视角，成功长什么样"，再写代码。status.json 的成功标准不是"能写入"，而是"PA 能正确回答项目进展"。（2026-03-28教训：393个单测通过但 PA 说"没有项目"） |
| 12 | **🆕 上下文工程是一等公民** | SOUL.md = 宪法级（身份+关键状态，LLM 注意力最高），CLAUDE.md = 手册级（工具+详情）；信息放哪里、占多少 token、LLM 能否注意到——都是架构决策，和 API 设计同等严肃。（2026-03-28教训：SOUL.md 空置数月，17KB CLAUDE.md 信息被"lost in the middle"） |
| 13 | **🆕 定期像用户一样使用系统** | 不是跑单测，而是在 WhatsApp 上实际问 PA 问题。每次涉及 PA 行为的变更后，必须清空 session（`echo '{"sessions":[]}' > sessions.json`）+ 重启 Gateway + WhatsApp 实测。单测验证组件内部，系统价值在组件之间的接缝处。 |
| 14 | **🔴 合并 PR 后下一步首先同步 Mac Mini** | **这是卡点，不是提醒。** GitHub PR 合并到 main 后，**下一步必须是 Mac Mini 同步，不做任何其他操作**。同步命令：`cd ~/openclaw-model-bridge && git fetch origin main && git reset --hard origin/main`。同步后立即验证：`bash preflight_check.sh --full`。**不要在 Mac Mini 上测试未同步的代码**——多次返工都是因为合并后没同步就直接跑旧代码。不要等 auto_deploy 轮询。（2026-04-01教训：合并后 preflight 8 项失败全是部署漂移；2026-04-06教训：合并后直接测试跑的是旧代码，浪费时间排查不存在的 bug） |
| 15 | **🆕 测试必须全量：单测 + full_regression + WhatsApp 业务验证** | 每次变更后测试三层缺一不可：① `bash full_regression.sh`（394 单测 + 注册表 + 安全扫描 + 代码质量）② `bash preflight_check.sh --full` + `bash job_smoke_test.sh`（Mac Mini 部署验证）③ **WhatsApp 端实际业务测试**（用户视角发消息验证 PA 回复、search_kb 检索、图片理解等核心功能）。只跑单测不算测完——单测验证组件，WhatsApp 验证系统。（2026-04-01教训：394 单测全过但 preflight 8 项失败） |
| 16 | **🆕 所有推送必须双通道（WhatsApp + Discord）** | 新增或修改任何消息推送时，**必须同时覆盖 WhatsApp 和 Discord 两个通道**，不允许遗留单通道发送。优先使用 `notify.sh`（`source notify.sh && notify "msg" --topic papers`）统一推送；若直接调用 `openclaw message send`，每个 WhatsApp 发送后必须紧跟对应的 Discord 发送（成功路径→对应 topic 频道，错误/告警→`DISCORD_CH_ALERTS`）。审计方法：`grep -c "message send.*whatsapp"` 与 `grep -c "message send.*discord"` 计数必须一致。（2026-04-03教训：货代客户画像推送遗漏 Discord，11 个脚本错误路径缺 Discord） |
| 17 | **🆕 收工必须交叉校验待办状态** | 收工时不仅更新 `status.json`，还必须**扫描 CLAUDE.md 待办列表**，对照本次 session 的 commits 和 recent_changes，将已实现的任务标记 ✅。实现代码 + 更新待办 = 一个完整的交付，缺一不可。同时检查：① CLAUDE.md 待办 vs 实际代码一致 ② status.json priorities vs CLAUDE.md 待办一致 ③ 版本号/文件表/常用命令是否需要同步更新。（2026-04-03教训：V32 实现了 7 个 P0+P1 任务但 CLAUDE.md 全部未标记，直到 V33 审计才发现） |
| 18 | **🆕 补证据而非补功能** | 下一阶段最该补的不是功能，而是**可对外复述的证据链**：A.兼容性矩阵（provider/模型/模态/工具模式验证 matrix+checklist）B.性能/SLO 实验结果（延迟/成功率/降级恢复时间）C.运维韧性证据（故障注入+恢复时间统计）D.可复现证据（一键启动+demo transcript）。新增功能前先问"这能产出什么证据？"（2026-04-03导师评审：系统已有但证据密度不足） |
| 19 | **🆕 纵向做深不横向铺开** | 沿 `providers.py` 已证明的方向继续放大，不轻易开新战线。每个改动必须对应 V1/V2/V3 路标中的具体目标：V1=别人能跑，V2=别人敢用，V3=别人会扩展。对照 `docs/strategic_review_20260403.md` 和 status.json 路标检查。偏离路标的功能需要明确理由。（2026-04-03导师建议：不是再做更多功能，而是把已有能力做成证据链） |
| 20 | **🆕 话语权输出是一等公民** | 代码只是第一步，真正的顶级专家把代码、文档、评测、方法论、复盘文章串成完整叙事。每个 milestone 完成后考虑：能否产出一篇架构型/证据型/立场型文章？README 里的方法论要持续扩写成观点体系。（2026-04-03导师建议：建立"话语权上层建筑"） |
| 21 | **🆕 对抗审计：问"什么坏了我们发现不了"（含治理自身）** | 每月至少一次 adversarial review：不问"检查了什么"，而问"**什么东西坏了我们会发现不了？**"。**治理系统自己也在这个问题的范围里**——MR-7 (governance-execution-is-self-observable) 要求 check 层所有状态（pass/fail/skip/error）必须在汇总层有观察路径。治理审计（`ontology/governance_checker.py`，每日 07:00 自动执行+失败告警）防止已知漏洞回归；人工对抗思维发现新维度盲区。每发现一个"没人会发现"的答案，就转化为 `governance_ontology.yaml` 的新不变式（ontology-native，不用硬编码）。检查体系最危险的漏洞不是某个检查没写好，而是某个维度从未被纳入检查。（2026-04-09：adversarial_audit.py 9个检查合并入 governance_ontology.yaml；2026-04-11 升级：V37.3 MR-7 治理自观察 + INV-GOV-001 summary 不吞 error，23 不变式 / 7 元规则 / 6 元发现，首次实现"治理系统治理自己"） |
| 22 | **🆕 顺势设计：适配模型行为，不对抗** | LLM 工具使用服从训练分布，不服从"应该"。通用工具（write/read/exec）自然使用，专用工具（memory/sessions）需 SOUL.md 强制规则才触发。设计系统时**顺着模型的自然行为**：PA 自然用 write 写 MEMORY.md → 我们把 MEMORY.md 接入 KB 索引；PA 不会主动调 kb_write.sh → 我们在 proxy 层静默捕获对话。对抗模型倾向的设计必然失败。（2026-04-08教训：等了一个月让 Qwen3 调 memory_create，从未成功；改为顺势捕获后一天闭环） |
| 23 | **🆕 链式幻觉防范：LLM 链路中每一跳都会放大幻觉** | 多个 LLM 共享上下文时，一个 LLM 的幻觉会被下游 LLM 当作事实执行。Dream LLM 编造文件名 → PA LLM 尝试读取 → 失败。防范方法：① 讨论密度必须等于文档密度（KB 里高频讨论的主题必须有对应文档，否则 LLM 会补全出不存在的文件名）② LLM 生成的行动建议不能直接执行，需要 grounding 检查（文件是否存在、工具是否可用）③ 上下文中提供明确的文件清单，让 LLM 知道"有什么"和"没有什么"。（2026-04-08教训：industrial_ai_paradigm.md 幻觉链——Dream 生成→PA 执行→文件不存在） |
| 24 | **🆕 SOUL.md 触发词是唯一可靠的工具调用机制** | 当前 Qwen3 不会自主决定调用专用工具。唯一可靠的强制方式是 SOUL.md 的"遇到X必须调Y"规则+具体触发词列表。ops agent 的 sessions_spawn 成功不是因为 Qwen3 学会了，而是触发词"排查/超时/告警"命中了 SOUL.md 硬规则。新增专用工具时，必须同步更新 SOUL.md 触发词规则，否则工具永远不会被调用。（2026-04-08教训：memory 工具上线数周零调用，ops spawn 靠触发词 100% 成功） |
| 25 | **🆕 对话数据是最高质量信号源** | 用户与 PA 的对话包含决策、偏好、专业洞察、领域判断——这些是 cron 抓取的论文/新闻无法替代的一手数据。必须确保对话数据被捕获并进入 KB 索引（`tool_proxy.py` 热路径捕获 → `kb_harvest_chat.py` 冷路径提炼 → KB notes）。同时关注 PA 自主写入的文件（如 `MEMORY.md`），将其纳入索引范围。数据流失 = 系统失忆。（2026-04-08教训：240 条 KB notes 全是机器抓取，零对话数据；PA 自主写入的 MEMORY.md 也是孤岛） |
| 26 | **🔴 异常分析宪法：必须输出完整因果链架构图** | 见下方独立章节"异常分析宪法"。（2026-04-10 升级为宪法级） |

### 🔴 异常分析宪法（原则 #26 展开，无例外强制执行）

> **修代码之前先画图。画不清因果链 = 还没理解问题。**

遇到任何 bug/异常/故障，必须按以下流程输出，**不可跳过任何步骤**：

#### 步骤一：完整因果链架构图（强制，必须是第一个输出）

按**时间线 × 层级 × 逻辑 × 架构**四维度，画从上游触发到下游用户感知的全链路 ASCII 图：

```
HH:MM  [组件A] 事件描述
       │
       ├─ [组件B] 具体行为 → 具体错误码/现象
       ├─ [组件C] 因为B的错误 → 触发什么后果
       │
HH:MM  [组件D] 下游事件
       ├─ 调用链: 组件X(:端口) → 组件Y(:端口)
       ├─ 组件Y 尝试A → 失败（具体原因）
       ├─ 组件Y 尝试B → 失败（具体原因）
       ├─ 组件Y 返回错误码
       │
       ├─ [消费方] 收到错误 → 但未正确处理
       ├─ 具体代码逻辑: variable = "" ← 标注关键 bug
       ├─ 具体检查: if condition → TRUE/FALSE ← 标注为什么没拦住
       └─ [用户] 最终感知到的现象
```

**四维度要求**：
- **时间线**：左侧标注精确到分钟的时间戳
- **层级**：标注每个组件名称和端口（如 Adapter:5001、Proxy:5002）
- **逻辑**：标注关键代码分支的 TRUE/FALSE 走向和变量值
- **架构**：标注组件间调用关系和错误传播路径

#### 步骤二：三层根因（触发器 → 放大器 → 掩护者）

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | 什么外部事件引爆？ | （填写） |
| **放大器** | 什么架构缺陷让影响扩散？ | （填写） |
| **掩护者** | 什么缺失让问题被隐藏到用户发现？ | （填写） |

#### 步骤三：时间线还原表

| 时间 | 事件 | 影响 |
|------|------|------|
| HH:MM | ... | ... |

#### 步骤四：为什么以前没发生（条件组合分析）

| 条件 | 以前 | 现在 |
|------|------|------|
| 条件A | ... | ... |

必须找到**多条件组合**：哪些条件单独出现不会触发，组合才触发。

#### 步骤五：喂养本体工程

- 案例文档：写入 `ontology/docs/cases/`
- 提炼 governance 不变式（INV-xxx）加入 `governance_ontology.yaml`
- 更新相关原则（如有新认知）

**参考案例**：`ontology/docs/cases/dream_quota_blast_radius_case.md`

### 🟡 按需查阅（操作 & 架构参考）

<details>
<summary>展开查看完整原则列表（16条）</summary>

**操作类**
- **故障先回滚** — 线上故障 → `git checkout v26-snapshot` 恢复服务 → 再排查根因
- **收工全量同步** — "今天工作结束" → `bash preflight_check.sh` 全面体检 → 扫描全部文档同步当日变更 → 提交推送
- **每日文档刷新** — `CLAUDE.md` + `docs/config.md` + `docs/openclaw_architecture.md` 在开工/收工时强制 read → write
- **纯推理绕过Gateway** — 不需要工具的LLM任务直接 curl 调 API，禁止用 `openclaw agent`（#94）
- **macOS sed禁用OR语法** — `\|` 在 BSD sed 不支持，用 Python 替代
- **禁用交互式编辑器** — git merge 用 `--no-edit`，commit 用 `-m`
- **crontab 安全操作** — **严禁 `echo ... | crontab -`**（2026-03-25事故：清空全部 cron），必须用 `bash crontab_safe.sh add '<行>'`
- **分支合并由用户在GitHub操作** — 推送到 `claude/xxx` 分支 → 用户合并 PR → **⚠️ 合并后下一步首先同步 Mac Mini，不做任何其他操作**（`git fetch origin main && git reset --hard origin/main`）→ 同步后再测试（见必查 #14，2026-04-06 再次踩坑）
- **Mac Mini 同步用 reset 不用 pull** — `git fetch origin main && git reset --hard origin/main`（Mac Mini 是纯消费端，无本地 commit；`git pull` 会因历史 merge commit 导致分叉失败）
- **全量测试三层标准** — 单测通过 ≠ 测完，必须 full_regression + preflight + **WhatsApp 业务验证**（见必查 #15）

**战略类（导师建议 2026-04-03）**
- **不再是桥接器，是控制平面产品** — 项目已远超 "bridge"，实际是 agent runtime control plane。旗舰叙事：`OpenClaw Runtime Control Plane for Tool-Calling Agents`
- **沿此仓库继续长 12 个月** — 不轻易切换去做完全不同的大项目，把这个仓库做成第一性代表作
- **下次汇报准备 5 样东西** — 版本变化 + 1-2 个关键架构图 + benchmark 数据 + 一次真实故障案例 + 对下一阶段的取舍判断

**架构类**
- **进程管理单一主控** — Gateway 由 launchd 管理，禁止再加 cron watchdog（#95）
- **cron 脚本显式声明 PATH** — 首行 `export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"`
- **健康检查只检目标组件** — `curl localhost:18789`，不走完整 LLM 链路
- **`--thinking` 参数** — 合法值：`off, minimal, low, medium, high, adaptive`（禁止 `none`，#92）
- **工具数量 <= 12** — 超出导致模型混乱；每任务工具调用 <= 2次
- **双 cron 职责分工** — 确定性脚本用 system crontab；需 LLM 参与的用 openclaw cron
- **开发流程** — Claude Code 只推 `claude/` 分支，Mac Mini 只从 main 拉取，避免双向提交同一分支

</details>

## 系统定位：三平面架构

> 来源：2026-04-01 外部专业评审反馈 + 2026-04-03 导师战略复盘

```
控制平面（先做强 → 60%→90%）：治理、限流、降级、观测、审计
  → V34: Provider Compatibility Layer (providers.py) 已实现
能力平面（持续演进 → 80%）    ：模型路由、工具编排、多模态能力
  → V34: 兼容性矩阵 + 能力声明 (ProviderCapabilities) 已实现
记忆平面（长期投入 → 60%）    ：知识沉淀、冲突消解、可信度评分
  → V2 路标: Memory Plane v1 统一叙事（将 5 个散落组件统一）
```

**核心原则：控制平面先行。否则能力越强，系统越难控。**

### 导师确立的三个高价值模块（V34+）

| 模块 | 目标 | 当前基础 | 路标 |
|------|------|----------|------|
| **Provider Compatibility Layer** | auth/chat/tool-calling/multimodal/streaming/fallback 标准接口 | `providers.py`+`adapter.py` 重构完成 | **V1 (in progress)** |
| **Agent Reliability Bench** | 系统性可靠性评测（7场景47检查：provider宕机/tool timeout/malformed args/oversized/kb miss-hit/cron drift/state corruption） | `reliability_bench.py`+`gameday.sh` | **V2 (✅ V36 done)** |
| **Memory Plane v1** | 4层统一接口（KB语义/多媒体/偏好/状态）+ query/context/stats + 优雅降级 | `memory_plane.py` + 5个底层组件 | **V2 (✅ V36 done)** |

## 当前待办（按导师 V1/V2/V3 路标组织）

> 来源：2026-04-03 导师战略复盘。路标 = 时间窗口 + 成功标准。详见 `docs/strategic_review_20260403.md`

### V1 路标（0-4 个月）：别人能跑

| 优先级 | 任务 | 状态 |
|--------|------|------|
| **V1-P0** | **Provider Compatibility Layer**：`providers.py` BaseProvider 抽象 + **7 个实现** + ProviderRegistry + 能力声明 + CLI 兼容性矩阵（V34 实现，V35+ 扩展至 7 provider） | ✅ V35+ 完成，7 provider 生产验证通过 |
| **V1-P0** | **兼容性矩阵 + SLO Benchmark 证据**：`docs/compatibility_matrix.md` + `slo_benchmark.py` + `docs/slo_benchmark_report.md`（5/5 PASS，p95=459ms） | ✅ V35 完成 |
| **V1-P1** | **一键启动 + 最小 demo + golden test trace**：`quickstart.sh` 4阶段（前置检查→启动→健康→demo），19/19 通过，`docs/golden_trace.json` | ✅ V35 完成 |
| **V1-P1** | **可复现证据**：Quick Start + golden trace + SLO benchmark report 均已入库 | ✅ V35 完成 |

### V2 路标（4-8 个月）：别人敢用

| 优先级 | 任务 | 状态 |
|--------|------|------|
| **V2-P0** | **Agent Reliability Bench**（导师建议模块二）：`reliability_bench.py` 7场景47检查（Provider宕机/工具超时/畸形参数/超大请求/KB未命中/Cron漂移/状态损坏），mock-based可在dev运行，36单测 | ✅ V36 完成 |
| **V2-P0** | **Memory Plane v1 统一叙事**（导师建议模块三）：`memory_plane.py` 4层统一接口（KB语义/多媒体/偏好/状态）+ query/context/stats API + 45单测 + 架构文档 | ✅ V36 完成 |
| **V2-P1** | **运维韧性证据**：`docs/resilience_report.md` — 7场景故障注入实验 + Recovery Time 汇总 + Dev Bench vs Production GameDay 对比 + 5项改进建议 | ✅ V36 完成 |
| **V2-P1** | **SLO Dashboard**：`slo_dashboard.py` 历史快照追踪 + sparkline 趋势 + cron 定时采集 + 31 单测 | ✅ V36 完成 |
| **V2-P1** | **semver 版本治理**：`VERSION` 文件 + /health 端点暴露版本 + changelog 格式升级 | ✅ V36 完成 |
| **V2-P1** | **安全边界说明文档**：`docs/security_boundaries.md`（8节：认证/网络/输入验证/数据保护/LLM安全/运维/评分/已知风险） | ✅ V36 完成 |
| **V2-P1** | **Memory Plane v2**：跨层去重（filename+text）+ 置信度加权（4层权重+新鲜度衰减）+ 冲突消解（优先级vs偏好矛盾检测）+ 64单测 | ✅ V36 完成 |

### V3 路标（8-12 个月）：别人会扩展

| 优先级 | 任务 | 状态 |
|--------|------|------|
| **V3** | **Provider Plugin Interface**：YAML/Python 插件 + 合约验证 + 能力路由 + auto-discovery fallback chain + Extension Guide + 128 单测 | ✅ V37 完成 |
| **V3** | **Tool Policy Plugin + Memory Plane Plugin**：可插拔的工具策略和记忆平面扩展 | 待启动 |
| **V3** | **Job Template/Registry SDK + Extension Guide**：让别人能基于框架扩展 | 待启动 |

### 话语权输出（持续推进）

| 类型 | 任务 | 状态 |
|------|------|------|
| 架构型 | **Why Agent Systems Need a Control Plane** — `docs/articles/why_control_plane.md`（问题→三平面架构→7场景证据→5条教训→立场） | ✅ V36 完成 |
| 证据型 | **Benchmark Report** / **Failure Injection Report** / **Lessons from 461-test Regression** | 待写 |
| 立场型 | **为什么 agent 系统首先是治理问题** / **为什么 control plane 必须先于 capability plane** | 待写 |
| 立场型 | **Why Enterprise AI Needs Ontology Before It Needs More Models** — Ontology+LLM+Agent 三角架构论述 | ✅ V36.2 完成（EN dev.to + ZH 知乎发布） |

### Ontology 子项目（终态架构：Semantic Control Plane）

> 终态架构文档：`ontology/docs/architecture/target_architecture.md`
> 迁移路径：Phase 2 Shadow（当前）→ Phase 3 渐进替换 → Phase 4 完全推理 → Phase 5 对外输出

#### Phase 2 已完成（Shadow 观察，V36.1-V37.1）

<details>
<summary>展开查看 17 项已完成</summary>

| 任务 | 版本 |
|------|------|
| Ontology KB 目录结构 + 初始文件（7 文件） | V36.1 |
| 论文监控加 ontology 关键词（ArXiv/S2/DBLP） | V36.1 |
| X 监控加 ontology 人物（Marcus/Leskovec/Witbrock/Palantir×3） | V36.1 |
| Ontology 独立子项目：engine.py+tool_ontology.yaml+diff.py+宪法+PoC+tests | V36.2 |
| Tool Ontology Engine：81条声明式规则 + 推理引擎 + 一致性校验 | V36.2 |
| Ontology 宪法：6条+最高条款+价值评估矩阵 | V36.2 |
| BFO/DOLCE/UFO 流派对比、Neuro-Symbolic、供应链本体 | V36.1 |
| 语义查询 PoC：从枚举到推理 | V36.2 |
| 特性开关 Phase 1：equivalence proof + 3模式 rollback | V36.2 |
| 验证深度三层模型：声明/运行时/效果 + MR-6 + MRD-LAYER-001 | V36.3 |
| classify_tool_call() 从属性推理 risk_level + policy_tags | V36.3 |
| Feature Flag Phase 2：shadow 模式 Mac Mini 生产上线 | V36.3 |
| 立场文章：Why Enterprise AI Needs Ontology（EN+ZH 发布） | V36.2 |
| Governance Ontology v3：17不变式+35检查+6元规则+执行引擎 | V37.1 |
| Phase 0 元规则自主发现：23 个未覆盖 job | V36.2 |
| 对抗审计 ontology-native 化 + 每日定时审计 + 效果层启动 | V37.1 |
| Ontology 专属信息源（4 RSS + 两层过滤 + LLM 摘要 + Discord） | V37.1 |

</details>

#### Phase 3: 渐进替换（近期，P0-P1）

| 优先级 | 任务 | 状态 | 说明 |
|--------|------|------|------|
| **P0** | **ONTOLOGY_MODE=on 切换**：shadow 观察无 drift 后正式切换，引擎数据替代硬编码 | shadow 观察中 | 等价已证明，需确认生产无 drift 后切换 |
| **P0** | **filter_tools() 改用引擎**：内部调用 `ontology.query_tools()` 替代 `ALLOWED_TOOLS` 枚举 | 待启动 | 依赖 ONTOLOGY_MODE=on |
| **P0** | **fix_tool_args() 改用引擎**：参数修复调用 `ontology.resolve_alias()` 替代硬编码映射 | 待启动 | 依赖 ONTOLOGY_MODE=on |
| **P1** | **夜间阻止语义化**：从手动维护阻止列表改为 `infer("side_effects==true")` 自动覆盖 | 待启动 | Phase 3 标志性交付 |
| **P1** | **新工具只加 YAML**：推广 V37 Provider Plugin 模式到 Tool，新增工具零 Python 改动 | 待启动 | 需要 Tool Plugin YAML schema |
| **P1** | **元规则扩展**：MR-7 新策略必须有 shadow 观察期 / MR-8 概念变更触发影响分析 | 待启动 | 治理体系自我演进 |
| **P1** | 用本体论视角重新审视 OpenClaw（深度版，含工具语义本体实验） | 进行中 | `cases/openclaw_as_ontology.md` |

#### Phase 4: 完全推理（中期，P1-P2）

| 优先级 | 任务 | 状态 | 说明 |
|--------|------|------|------|
| **P1** | **domain_ontology.yaml**：六域概念模型（Actor/Tool/Resource/Task/Provider/Memory），概念间关系推理 | 待启动 | Layer 1 终态：从工具列表到领域模型 |
| **P1** | **policy_ontology.yaml**：策略声明式定义（静态+时序+路由三类策略统一） | 待启动 | Layer 2 终态：evaluate_policy() 统一评估 |
| **P1** | **三阶段门控**：Pre-check（前置条件）→ Runtime Gate → Post-verify（后置验证）接入请求管线 | 待启动 | Layer 3 终态：Neuro-Symbolic 四耦合点 |
| **P2** | **审计带规则链**：每条审计记录包含 policy_evaluated + rule_chain + rationale | 待启动 | Layer 4 终态：从"做了什么"到"基于什么规则" |
| **P2** | **策略冲突检测**：策略间矛盾自动发现（如夜间阻止 vs 紧急通知） | 待启动 | 策略引擎高级能力 |
| **P2** | **影响分析工具**：`ontology.impact_analysis("修改 max_tools")` → 受影响策略/工具列表 | 待启动 | 变更安全保障 |
| **P2** | **效果层覆盖率 ≥ 60%**：30+ 不变式中至少 18 个有 L3 效果验证 | 待启动 | MR-9 元规则 |

#### Phase 5: 对外输出（长期，V3 路标对齐）

| 优先级 | 任务 | 状态 | 说明 |
|--------|------|------|------|
| **P2** | **Tool Policy Plugin**：`tool_policy.yaml` 声明式工具策略扩展接口 | 待启动 | V3 路标对齐 |
| **P2** | **Memory Policy Plugin**：`memory_policy.yaml` 记忆平面策略扩展 | 待启动 | V3 路标对齐 |
| **P2** | **Ontology Extension Guide**：第三方基于 ontology 框架扩展的指南 | 待启动 | V3 路标对齐 |
| **P3** | **可发布引擎**：ontology 引擎可独立 pip install 的治理组件 | 待启动 | 长期目标 |
| **P3** | 证据型文章：从 17 不变式到 30+ 的治理演进实战 | 待启动 | 话语权输出 |

### 现有功能任务（V1 稳定后继续推进）

| 优先级 | 任务 | 状态 |
|--------|------|------|
| 中 | **数据清洗 Phase 2**：三 Agent 架构（Profiler/Planner/Executor，用 `sessions_spawn`）、语义去重、自定义规则 | active |
| deferred | **PA 子 Agent 委派**：`sessions_spawn` + `sessions_send`。V35 PoC 完成：链路通（显式触发可用），但 Qwen3 隐式触发不可靠（优先从上下文回答）。等下一代模型 | deferred |
| deferred | **PA 长期记忆**：Qwen3 不主动调用 memory 工具，等模型升级后重新验证 | blocked |
| 低 | **可迁移性抽象**：去除个人/场景定制痕迹，抽象成别人也能迁移的框架（导师指出三块差距之一） | V2-V3 |
| 低 | **记忆系统分层**：短期/长期/任务，含冲突消解和可信度评分 → 纳入 Memory Plane v1 | V2 |
| 低 | **成本-质量-时延联动调度**：根据查询复杂度动态选择模型/参数组合 | V2+ |
| 低 | 知识图谱：AI 大模型领域知识图谱构建（需 6-12 个月数据积累） | V3 |
| 低 | 货代 Watcher V3：Bing News API 替代 GoogleNews | 按需 |
| 低 | 语音消息支持：WhatsApp 语音→STT→LLM 回复 | 按需 |
| 低 | **视频号内容转录分析**：firethinker 视频号监控 → STT 转录 → LLM 提炼 → KB 沉淀 | 按需 |
| 低 | KB 静态加密：status.json / index.json 使用 age/gpg 加密存盘 | 按需 |

### 已完成里程碑（V27-V35）

<details>
<summary>展开查看 25+ 项已完成任务</summary>

| 版本 | 任务 |
|------|------|
| V35 | **V1 路标完成** — SLO Benchmark 实验报告（5/5 PASS, p95=459ms）+ Quick Start 一键 demo（19/19）+ Golden Test Trace + Sub-agent PoC（链路通，deferred）+ 605 测试 |
| V34 | **Provider Compatibility Layer**：providers.py + 48 单测 + 兼容性矩阵 + adapter.py 重构 |
| V33 | SLO 最小集 + 阈值中心化 + 旅程级 E2E 进 CI + 故障快照机制 + Job 分层治理 + Fallback Matrix + 变更影响评估 + GameDay 故障演练 + Discord 双通道 + notify.sh + pip-audit |
| V32 | 配置中心化 + 变更审计 + config_loader.py |
| V30.5 | search_kb 混合检索 + 论文监控矩阵 5 源全覆盖 + DBLP |
| V30.4 | SOUL.md 激活 + 三方宪法闭环 + 方法论进化 |
| V30.3 | 数据清洗 Phase 1 + 自定义工具注入 + WhatsApp E2E |
| V30.2 | 安全评分体系 + 审计日志 + 持续安全机制 |
| V30.1 | status.json 实时刷新 + KB 完整性 + 全量回归 374→461 测试 |
| V30 | crontab 事故修复 + cron 安全三层保护 |
| V29.x | KB 搜索/RAG/趋势/MM 索引/Fallback/备份/Multi-Agent |
| V28.x | preflight 体检 + WhatsApp 保活 + CI + pre-commit |
| V27.x | Proxy 拆层 + 任务注册表 + auto_deploy + 回滚机制 |

</details>

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

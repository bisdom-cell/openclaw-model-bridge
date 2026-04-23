# CLAUDE.md — openclaw-model-bridge 项目背景

> 每次新会话开始时自动读取。当前版本：v37.9.13 / 0.37.9.13（2026-04-23）

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
| `restart.sh` | 一键重启 Proxy + Adapter + Gateway（含 PATH 修复，可在 cron 环境使用）。**V37.9.13 架构清理**：Adapter + Proxy 改用 `launchctl kickstart -k` 走 launchd 管理，消除 V37.9.12.1 双管理血案；plist 缺失时 fallback 到 nohup；V37.8.13 Gateway 健康验证模式扩展到 adapter/proxy（5×2s curl /health 探测） |
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
| `ontology/` | **V36.1新增→V37.9.13 Phase 4 P2 context evaluator** Ontology 独立子项目 — Tool Engine（81条声明式规则+推理引擎+**classify_tool_call语义分类**）+ **Governance Ontology v3.9**（**35不变式**+139可执行检查+**9元规则**+**MR-6 critical-invariants-need-depth 从警告升级为 hard-enforcing via INV-LAYER-001**+MR-7 治理自观察+MR-8 copy-paste-is-a-bug-class+MR-9 state-writes-go-through-helper+**INV-GOV-001 summary 不吞 error**+**INV-DREAM-001/002 + INV-CACHE-002 Dream Map 预算+缓存契约（V37.8 补齐运行时层）**+**INV-PA-001/002 告警污染+SOUL.md 规则 10 双防线**+**INV-LAYER-001 治理深度自我强制**+验证深度三层模型+Phase 0元规则自主发现+MRD-LAYER-001/MRD-NOTIFY-002 检测器重写）+ governance_checker.py 执行引擎（V37.2+ silent error 修复：error 状态计入 failed_invs, V37.8 `_discover_silent_channels` 双层检测 source+activity）+ 宪法6条 + 语义查询PoC + **Phase 2 shadow模式**(off→shadow→on) + 立场文章(EN+ZH) + **V37.9.12 engine.py 新增 3 个 Phase 4 P1 纯函数**: `load_domain_ontology()` / `find_by_domain(domain)` / `evaluate_policy(policy_id, context)` + `_parse_limit_from_rule` 文本 regex fallback，`proxy_filters.py::filter_tools` 已切换读 policy.max-tools-per-agent.limit 替代硬编码 `_CFG_MAX_TOOLS`（safe-fallback 链：on→ontology / shadow→config+observe / off→config / 加载失败→config）+ **V37.9.13 Phase 4 P2 context evaluator**：engine.py 新增 6 个 matcher 纯函数 (`_eval_quiet_hours` / `_eval_task_match` / `_eval_has_alert` / `_eval_has_image` / `_eval_need_fallback` / `_eval_data_clean_keywords`) + `_CONTEXT_EVALUATORS` dispatch table 覆盖 6 条 temporal/contextual policy + `evaluate_policy()` 三档扩展 (context=None / evaluator 未注册 / evaluator 抛异常) + 第二条 policy 切换 `_resolve_max_tool_calls_per_task_limit()` 镜像 V37.9.12 5 档模式（`_MAX_TOOL_CALLS_PER_TASK_RESOLVED` 常量由 Phase 4 P2 wiring 产出，enforcement 点留待 P3）|
| `ontology/domain_ontology.yaml` | **V37.9.9新增（骨架）→V37.9.12 P1 wiring 依据** Phase 4 Layer 1 领域模型 — 六域概念 Actor/Tool/Resource/Task/Provider/Memory + 域间关系模板 + Phase 4 实施路径 P1/P2/P3。V37.9.12: `find_by_domain()` 纯函数读此文件, Actor 6/Resource 7/Task 6/Memory 4 层/Provider 4 type 可查。|
| `ontology/policy_ontology.yaml` | **V37.9.9新增（骨架）→V37.9.12 P1 wiring 依据→V37.9.13 P2 context evaluator 对齐** Phase 4 Layer 2 策略声明 — 10 条 policy 按 static/temporal/contextual 三类分：max-tools-per-agent / alert-context-isolation / quiet-hours-00-07 / dream-map-budget / multimodal-routing / fallback-chain-capability 等 + 策略冲突检测 stub + 元规则 MR-7/8/16 引用。**V37.9.12 给 3 条 hard_limit policy 加 `limit` 字段**（max-tools-per-agent=12, max-tool-calls-per-task=2, max-request-body-size=200000）。**V37.9.13 meta 升级** version 0.1-skeleton → 0.2-p2-partial，status → wired_static_and_registered_contextual，新增 `policies_with_context_evaluator` 清单（6 条）+ `policies_wired_via_proxy_filters` 清单（2 条）+ next_step 指向 Phase 4 P3 三阶段门控。|
| `test_phase4_ontology_skeleton.py` | **V37.9.9新增→V37.9.12扩展** Phase 4 骨架守卫（17 单测）：六域声明/description 长度/Actor 核心实例/source_of_truth 对齐/Memory 四层匹配 memory_plane/policy 三类分类/policy 必填字段/hard_limit 仅 static/alert-isolation ordering_constraint/跨 ontology 引用一致性 + **V37.9.12 hard_limit 必须有 `limit` 字段 + max-tools-per-agent.limit == 12 横向守卫** |
| `ontology/tests/test_engine_phase4.py` | **V37.9.12新增** Phase 4 P1 wiring 契约（38 单测）：`load_domain_ontology()` 加载 + path 参数 + 缺文件抛错；`find_by_domain()` Actor/Resource/Task/Memory 归一化 + Provider.types 字符串包装 + Tool 主动返回 []（source_of_truth=tool_ontology）+ 未知域返回 [] + 预加载 ontology 注入 + 纯函数不改输入；`evaluate_policy()` 返回 12-key 稳定结构 + unknown found=False + static applicable=True + contextual/temporal applicable=None+reason=needs_context_evaluator + load_failed 不抛异 + context 参数兼容 + pre-loaded policy_data 注入；**max-tools-per-agent 契约**: limit=12, hard_limit=True, type=static, governance_invariant=INV-TOOL-001, 横向一致 config_loader.MAX_TOOLS; **_parse_limit_from_rule**: ≤/<=/< 提取 + 下划线千位分隔 + None 回退 |
| `ontology/tests/test_governance_cron_matcher.py` | **V37.2+新增** INV-CRON-003/004 匹配器回归测试（18 单测）：① endswith+word-boundary 精确匹配 ② Map-Reduce split 不误报 ③ prefix-subset entries 不混淆 ④ exec() 作用域陷阱正向对照 ⑤ YAML-matcher in-sync guard |
| `ontology/tests/test_governance_summary.py` | **V37.3新增** INV-GOV-001 silent error bug 回归测试（7 单测）：① error 状态注入→汇总不说"所有不变式成立" ② error 状态 exit_code≠0 ③ error 汇总用 💥 ④ pure fail 仍用 ❌ ⑤ all-pass 仍说✅ ⑥ mixed fail+error 各自计数 ⑦ 源码级 grep 守卫 `r["status"] in ("fail","error")` |
| `ontology/docs/cases/governance_silent_error_case.md` | **V37.3新增** Governance 自身三层嵌套盲区案例（子串匹配→exec 作用域→silent error summary）— 观察者的自我盲区 + MR-7 提案 + INV-GOV-001 提案 |
| `ontology/docs/cases/dream_map_budget_overflow_case.md` | **V37.4新增** Dream Map 预算溢出 + cache key 漂移案例（workload 增长 × cache key 对 mtime/sort 敏感 × Reduce 不相信缓存 → 连续超时，Reduce 从未执行）完整因果链架构图 + 三层根因 + 预算/契约/稳定三层修复 |
| `ontology/docs/cases/pa_alert_contamination_case.md` | **V37.4.3新增** PA 告警污染血案案例（系统告警经 Gateway sessions.json 污染对话上下文 × Proxy truncate 无语义理解 × Qwen3 attention 跨主题错误关联 × LLM 编造 macOS FDA 指令）六层根因深挖 + 完整因果链架构图 + 六条件组合爆炸分析 + 结构+行为双防线修复论证 |
| `ontology/docs/cases/heartbeat_md_pa_self_silencing_case.md` | **V37.8.16新增** PA 自残 HEARTBEAT.md 血案案例（2026-04-19 09:09 PA 把"任务完成"写进 OpenClaw 保留文件 HEARTBEAT.md → 13h 潜伏 → OpenClaw heartbeat 机制激活让 LLM 对所有用户消息回 HEARTBEAT_OK → Gateway stripTokenAtEdges 剥离 → 13h 完全静默）完整时间线 + 三层根因 + 六条件组合爆炸分析 + MR-15 元规则提炼 + 4/19 深夜原则 #28 违规复盘（Bad MAC 错归因让用户 auth reset）+ 6 轮假说错五轮的教训 |
| `ontology/tests/test_dream_cache_stability.py` | **V37.4新增→V37.4.2扩展** Dream Fix A+B+C+retry 回归测试（32 单测）：① Fix A cache-only fast path + SKIP_MAP_LOOPS 三处门控 ② Fix B 批次阈值 30/24000 ③ Fix C per-note content_hash 缓存 + 跨平台 md5 fallback + signal dedup ④ 动态 DREAM_TIMEOUT_SEC 预算 ⑤ flush_pending_batch 辅助函数语义 ⑥ Sources 缓存键保持不变 ⑦ **V37.4.1 Retry fallback**：MIN_DREAM_CHARS=3000 / MIN_ACCEPTABLE_CHARS=1500 / BEST_RESULT 追踪 / 温度单调递减 / 致命错误门槛用 MIN_ACCEPTABLE_CHARS ⑧ **V37.4.2 结构+变体**：UNIQ_NOTE_SIG_BLOCKS 数组 / 覆盖统计 header / 编号 cluster header / bland header 已移除 / retry 2+ 变体 prompt / llm_call 引用 \$cur_prompt |
| `kb_dream.sh` | **V36新增→V37.8.6升级** Agent Dream v2 MapReduce 全量 KB 探索引擎（Phase1 Map 14源+293笔记逐一提取信号 → Phase2 Reduce 跨域深度分析；Map-Reduce 分离调度：00:00 Map预热缓存→00:40 Notes 预热→03:00 Reduce 跨域关联+推送）。**V37.4 Fix A+B+C** / **V37.4.1 retry** / **V37.4.2 结构+变体**（见下方 changelog）。**V37.8.6 自引用幻觉防御**：① `log()` 改写 stderr（`>&2`）阻断 `signals=$(llm_call ...)` 命令替换把错误日志捕获进 cache 的通道（血案根因：错误日志→cache→Reduce LLM → 编造 Hugging Face 危机）② heredoc 内嵌 `_sanitize(s)` 清洗 U+D800-U+DFFF 孤立代理 → U+FFFD，防 json.dump 触发 UnicodeEncodeError 导致 body_file 截断 → adapter 400 Bad JSON ③ file open 显式 `encoding='utf-8', errors='replace'` 第二防线 ④ REDUCE/CHUNK1/2/3 system prompt 加反污染守卫，明示禁止把 HTTP 错误码/Python 异常/错误页 HTML/U+FFFD 当外部信号，禁止推断 Hugging Face/GitHub/npm 等平台状态 |
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
| `jobs/finance_news/run_finance_news.sh` | **V37.8.2新增→V37.8.5升级** 全球财经/政策新闻每日汇总（双通道：8 RSS 直连 + 21 X/Twitter Syndication API，72h 窗口覆盖周末，LLM 结构化分析+价值评级+国内外对比+投资建议+风险提示，cron 07:30）。V37.8.5：僵尸检测从 heredoc 内嵌严格相等升级为导入 `finance_news_zombie.classify_zombie` 模块，三层 tier（stub/stale/alive）+ count 守卫防止低频活跃账号误报 |
| `jobs/finance_news/finance_news_zombie.py` | **V37.8.5新增** Syndication API 僵尸账号三层检测纯函数模块（Tier 1 stub: no_data=0 + total=0 闭合 SingTaoDaily 0-tweet 盲区；Tier 2+3 stale: count=0 + old*10 >= total*9 整数比较闭合 CNS1952 99% 近僵尸盲区；count 参数守卫避免低频活跃账号误报） |
| `test_finance_news_zombie.py` | **V37.8.5新增** 僵尸检测 24 单测：3 层 tier + count 守卫 + V37.8.4 向后兼容 + shell 集成（import/call-with-count/env-export/tier-prefix/禁 inline fallback）+ auto_deploy 映射 + 常量契约 |
| `test_wa_gateway_resilience.py` | **V37.8.13新增** Gateway 宕机韧性三层修复 21 单测：TestQuietAlertDiscordAlways(4) quiet_alert 静默期 Discord 穿透 + TestWaKeepaliveEscalation(9) 连续 WARN 升级 + TestRestartGatewayVerification(5) post-bootstrap 健康验证 + TestCrossFileGuards(3) 跨文件一致性 |
| `test_restart_launchd.py` | **V37.9.13新增** restart.sh 架构清理 20 单测：TestRestartVialaunchdHelper(6) helper 函数契约（kickstart -k / bootstrap fallback / 5×2s 健康验证 / return 2 / launchctl 可用性检测）+ TestAdapterViaLaunchd(3) + TestProxyViaLaunchd(2) + TestSingleManagerInvariant(5) V37.9.12.1 双管理血案防线（nohup 必在 fallback 分支 + V37.8.13 Gateway 逻辑保留 + #48703 hotfix 保留）+ TestShellExecutability(3) + TestRuntimeHelperBehavior(1) subprocess 实跑 helper 验证 return 2 |
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
| V37.9.13+arch-cleanup | 2026-04-23 | **restart.sh 架构清理 — 单一 manager 契约（消除 nohup + launchd 双管理血案）** — ① **触发**：V37.9.12.1 Mac Mini 实测发现 `com.openclaw.adapter.plist` + `com.openclaw.proxy.plist` KeepAlive 管理的 launchd 进程，与 `restart.sh nohup python3 adapter.py/tool_proxy.py` 启动的 manual 进程同时抢占 :5001 / :5002 端口，launchd 侧持续显示 crash-loop（两路进程 ExitCode ≠ 0 反复重启）。status.json unfinished 登记 "V37.9.13 候选 (架构清理): restart.sh 改用 launchctl kickstart 替代 nohup python 启动 adapter/proxy"。② **修复方案**：restart.sh 新增 `restart_via_launchd <label> <port> <plist> <display>` helper 函数（返回 0=成功 / 1=launchd 失败 / 2=launchctl 或 plist 不可用，signal to caller to fallback），主路径用 `launchctl kickstart -k gui/$(id -u)/<label>` 触发 launchd 内部重启（modern idempotent API），首次加载时 `bootout + bootstrap` 兜底。③ **健康验证扩展**：V37.8.13 Gateway 的 5×3s 健康验证循环从 Gateway-only 扩展到 adapter/proxy — helper 内置 `for _attempt in 1 2 3 4 5; sleep 2; curl http://localhost:$port/health` 循环，HTTP 200 判定健康。Gateway 的验证逻辑完整保留（`V37.8.13 Post-bootstrap health verification` 注释 + `GATEWAY_HEALTHY=false` + 5×3s curl 循环 + 失败不 exit 1）。④ **向后兼容 fallback**：plist 缺失（dev 环境 / 未装 plist / 恢复场景）时 helper 返回 2，调用方用 `_ad_rc=$?` / `_px_rc=$?` 捕获并走 `nohup python3 adapter.py` 原路径；set -e 下用 `|| _ad_rc=$?` 安全捕获避免脚本整个退出。⑤ **#48703 hotfix 段完整保留**：V37.9.13 重构与该段无关，不得误删 `const listeners = /* @__PURE__ */ new Map()` 的 sed 补丁逻辑。⑥ **INV-RESTART-001 新增**（meta_rule=MR-9 state-writes-go-through-helper，severity=high，verification_layer=[declaration, runtime]）8 checks：restart_via_launchd helper 定义 + kickstart -k 源码模式 + adapter/proxy 调用参数精确匹配（`"com.openclaw.adapter" 5001` / `"com.openclaw.proxy" 5002`）+ V37.9.13 注释存在 + **2 个 runtime python_assert**：regex 扫所有 `nohup python3 ...adapter.py` / `...tool_proxy.py` 出现位置，前 300 字符内必须含 `plist not found` 或 `_ad_rc`/`_px_rc` 字样（证明在 fallback 分支内，V37.9.12.1 双管理血案的源码级防线）+ **set -e 安全模式守卫** `restart_via_launchd "com.openclaw.adapter" \|\| _ad_rc=$?` 必须用 `\|\|` 捕获返回码。⑦ **20 新单测** `test_restart_launchd.py`：TestRestartVialaunchdHelper(6 测试 kickstart -k / bootstrap fallback / 健康验证循环 / return 2 契约 / launchctl 可用性检测) + TestAdapterViaLaunchd(3) + TestProxyViaLaunchd(2) + TestSingleManagerInvariant(5 测试 nohup adapter/proxy 必在 fallback 分支 + V37.9.13 注释 + V37.8.13 Gateway 逻辑保留 + #48703 hotfix 保留) + TestShellExecutability(3 bash -n + set -euo pipefail + `\|\|` 模式) + **TestRuntimeHelperBehavior(1)** 实际 subprocess 跑 helper，用 awk 提取函数体 eval 后调用空 plist 场景，断言返回 2 不崩溃（用环境变量 `RESTART_SH` 注入路径避免 Python `.format()` 与 awk `/^}/` 花括号冲突）。⑧ **full_regression.sh 接入** `restart_launchd` suite。⑨ **accumulated**: 42 → **43 suites (+1)** / 1299 → **1319 tests (+20)** / 0 fail / governance 59→60 invariants / 安全 95/100 / VERSION 保持 0.37.9.13（同日累积改动）。⑩ **MR-9 正面兑现**：本次清理是 MR-9 state-writes-go-through-helper 的扩展 — 原 MR-9 针对 KB sources 写入（14 个 cron 走 `kb_append_source.sh` 不直 `>>` 重定向），V37.9.13 把"单一 manager"原则应用到进程管理层 — 进程启停必须走 launchd 唯一管理通道，不允许脚本同时用两种机制管理同一服务。⑪ **下次 Mac Mini 验证**：用户合并本 PR 后在 Mac Mini 同步 + `bash ~/restart.sh`，应看到 `[restart] Adapter: launchctl kickstart -k gui/501/com.openclaw.adapter` 日志 + `[restart] Adapter healthy (HTTP 200 after Nx2s)`；`launchctl list \| grep openclaw` 应显示 com.openclaw.adapter / proxy 的 PID 稳定不再 crash-loop。|
| V37.9.13 | 2026-04-23 | **Phase 4 P2 context evaluator + 第二条 policy 切换（max-tool-calls-per-task）** — ① **触发**：V37.9.12 Phase 4 P1 完成后 status.json 焦点明确登记两项 P2 任务 (context evaluator + 第二条 policy 切换)，验证 V37.9.12 wiring 模式可扩展而非一次性 hack。② **engine.py 扩展**：新增 6 个 context 匹配器纯函数 — `_eval_quiet_hours`（temporal：`context.hour ∈ [0,7)` 或 `context.now.hour`，含 int 类型转换 / 范围 [0,23] 校验 / 半开区间 7 点严格判否）/ `_eval_task_match`（temporal：`task==expected`）/ `_eval_has_alert`（contextual：messages 中任一 content 含 `[SYSTEM_ALERT]`，支持 str content 和 OpenAI content blocks 双格式）/ `_eval_has_image`（contextual：优先 `has_image` flag，否则扫 messages 里的 `image_url`/`image` 类型 block）/ `_eval_need_fallback`（contextual：显式 `need_fallback` flag）/ `_eval_data_clean_keywords`（contextual：10 个中英文关键词大小写不敏感）。③ **`_CONTEXT_EVALUATORS` dispatch table** 把 6 条 policy 映射到 matcher：quiet-hours-00-07 / dream-map-budget / alert-context-isolation / multimodal-routing / fallback-chain-capability / data-clean-tool-injection。未注册 policy 走 `reason="no_context_evaluator_registered"`——P2 承诺是"可扩展路径"不是"全部做完"。④ **`evaluate_policy()` 三档扩展**：static 仍 applicable=True；temporal/contextual 分三档 — context=None → `needs_context_evaluator`（向后兼容 P1）/ evaluator 未注册 → `no_context_evaluator_registered` / evaluator 抛异常 → `evaluator_error: <type>` 不冒泡。每个 evaluator 缺字段都有具体 reason（`context_missing_hour` / `context_hour_invalid_type` / `context_messages_must_be_list` 等）便于运维调试。⑤ **第二条 policy wiring**：`config_loader.py` 加 `MAX_TOOL_CALLS_PER_TASK`（读 config.yaml 已声明但从未加载的 `max_tool_calls_per_task: 2`）；`proxy_filters.py` 新增 `_resolve_max_tool_calls_per_task_limit()` 纯镜像 V37.9.12 5 档 safe-fallback 模式（off / load_failed / policy_miss / shadow / on+valid）+ `_MAX_TOOL_CALLS_PER_TASK_RESOLVED` 启动期一次性计算常量。启动日志 `[proxy] max-tool-calls-per-task: using ontology limit=2 (Phase 4 P2 wiring active)` 与 P1 日志配对。⑥ **enforcement 与 wiring 解耦**：当前 Python 尚无 tool_call 计数 enforcement 点（config.yaml 声明但未落实），P2 只做"阈值 wiring"不做"新 enforcement"——有意设计让 wiring 模式先稳定再考虑 enforce 何时何地。⑦ **policy_ontology.yaml::meta 升级**：version 0.1-skeleton → 0.2-p2-partial，status → wired_static_and_registered_contextual，新增 policies_with_context_evaluator 6 条 + policies_wired_via_proxy_filters 2 条清单，next_step 指向 Phase 4 P3 三阶段门控。⑧ **47 新单测**：test_engine_phase4.py +37（TestContextEvaluatorQuietHours×9 含 7 点 boundary + datetime 解析 + 类型/越界 reason / TestContextEvaluatorAlertIsolation×5 含 content blocks 双格式 / TestContextEvaluatorMultimodal×5 / TestContextEvaluatorDreamBudget×3 / TestContextEvaluatorDataCleanKeywords×4 / TestContextEvaluatorFallbackChain×3 / TestContextEvaluatorUnregistered×1 契约"未注册 policy 不崩溃" / TestContextEvaluatorExceptionSafe×1 契约"evaluator 抛异常不冒泡" / TestMaxToolCallsPolicyWiring×6）+ test_tool_proxy.py +9（TestPolicyDrivenMaxToolCallsPerTask 5 档 safe-fallback 各自 monkey-patch + 契约"查询正确 policy_id" + **MR-8 copy-paste 防御源码守卫** `assertNotIn("max-tools-per-agent", src)` 防止 P2 resolver 意外指向 P1 policy）+ test_phase4_ontology_skeleton.py +1（`test_max_tool_calls_per_task_is_2` 横向一致性）。⑨ **accumulated**: 42 suites / 1252 → **1299 tests (+47)** / 0 fail / 安全 95/100 / VERSION 0.37.9.12 → 0.37.9.13。⑩ **MR-8 正面案例兑现**：`_resolve_max_tool_calls_per_task_limit` 与 `_resolve_max_tools_limit` 95% 逻辑相同，但写两份独立函数 + 源码级 grep 守卫优于泛化 `_resolve(policy_id, default)` —— 每条 policy 启动期日志要独立字符串便于运维 grep、未来可能为特定 policy 加自定义日志/告警/指标、5 档 fallback 分支抛出的 WARN 要追溯到具体 policy 名字。⑪ **Phase 4 P3 候选**：(a) 三阶段门控（pre-check → runtime-gate → post-verify）把 `evaluate_policy(policy_id, context)` 接入请求管线 (b) 第三条 policy 切换候选 `max-request-body-size`（已有 explicit limit）或 `alert-context-isolation`（已有 ordering_constraint）(c) MR-7 兑现：为每个 context evaluator 添加 governance_ontology.yaml 对应 INV-* 让 wiring 本身被治理监控。|
| V37.9.12 | 2026-04-22 | **Phase 4 P1 wiring 落地 — engine.py 三纯函数 API + max-tools-per-agent policy 首切换** — ① **触发**：V37.9.9 完成 Phase 4 骨架（domain_ontology.yaml + policy_ontology.yaml + 15 单测）但 `engine.py` 未实现 wiring API，两个 YAML 停留在声明层没有消费者，status.json unfinished 登记 "Phase 4 P1+ wiring: engine.py 新增 load_domain_ontology() + find_by_domain() + evaluate_policy() 三个纯函数 API"。② **三纯函数 API 实现**（engine.py 第 650~800 行）: (a) `load_domain_ontology(path=None) -> dict` 加载 YAML 无缓存 (b) `find_by_domain(domain_name, ontology=None, path=None) -> list` 六域归一化: Actor→instances / Resource→categories / Task→taxonomy / Memory→layers / Provider.types 字符串包装成 `{"id":s}` / Tool 主动返回 [] 因 source_of_truth=tool_ontology.yaml / 未知域返回 [] 不抛 (c) `evaluate_policy(policy_id, context=None, policy_data=None, path=None) -> dict` 12-key 稳定返回: policy_id / found / type / hard_limit / limit / applicable / rule / rationale / enforcement_site / governance_invariant / scope / reason；Phase 4 P1 覆盖 static 策略（applicable=True），contextual/temporal 返回 applicable=None+reason=needs_context_evaluator 留给 Phase 4 P2；load 失败返回 found=False+reason=load_failed 不抛异。(d) `_parse_limit_from_rule()` helper 从 rule 文本提取 ≤/<=/< 数值（下划线千位分隔支持）作为 YAML 未显式声明 `limit` 字段时的回退。③ **policy_ontology.yaml 3 条 hard_limit policy 加 `limit` 字段**：max-tools-per-agent=12 / max-tool-calls-per-task=2 / max-request-body-size=200000，作为机器可读阈值，`evaluate_policy().limit` 优先读此字段>`value`>regex-parse rule。④ **proxy_filters.py 首切换**: filter_tools 的硬性截断阈值从 `_CFG_MAX_TOOLS` 改为 `_MAX_TOOLS_RESOLVED`（模块启动时由 `_resolve_max_tools_limit()` 解析）。Safe-fallback 5 档: (i) ONTOLOGY_MODE=off → config (ii) _onto_mod 未加载 → config+log warn (iii) policy not found / limit=None → config+log warn (iv) shadow mode → config 但 log observe ontology vs config 差异 (v) on mode + valid limit → ontology limit+log drift if any。启动日志: `[proxy] max-tools-per-agent: using ontology limit=12 (Phase 4 P1 wiring active)`。改 12 只需改 policy_ontology.yaml 一处（Phase 4 终态部分兑现）。⑤ **38 新单测** `ontology/tests/test_engine_phase4.py`: TestLoadDomainOntology(4)/TestLoadPolicyOntology(1)/TestFindByDomain(9)/TestEvaluatePolicyContract(7)/TestMaxToolsPolicyWiring(6)/TestParseLimitFromRuleFallback(7)/TestEvaluatePolicyLimitFallbackChain(4)。**11 新单测** `test_tool_proxy.TestPolicyDrivenMaxTools`: 5 档回退路径每档独立 monkey-patch 测试 + filter_tools 截断/custom tools 保留 + 源码守卫 `>\s*_CFG_MAX_TOOLS` 禁止回退模式。**2 扩展单测** `test_phase4_ontology_skeleton.py`: hard_limit 必须有 `limit` 字段 + max-tools-per-agent.limit==12 横向一致。⑥ **full_regression.sh 接入** `engine_phase4` suite。⑦ **accumulated**: 41→42 suites / 1201→**1252 tests (+51)** / 0 fail / 38/38 invariants / 安全 95/100 / VERSION 0.37.9.11 → 0.37.9.12。⑧ **部署策略（Mac Mini 实测结果修正 V37.9.12.1）**：`$HOME/ontology` 是 **symlink → `$HOME/openclaw-model-bridge/ontology`**，`ontology/` 未入 FILE_MAP 但通过 symlink 跨越——`$HOME/proxy_filters.py` 能直接找到 `$HOME/ontology/engine.py`。**Phase 4 P1 wiring 在 Mac Mini 真实生效**（不是 safe-fallback），`/health` 返回 `version: 0.37.9.12`，日志确认 `[proxy] max-tools-per-agent: using ontology limit=12 (Phase 4 P1 wiring active)`。宪法"删除后原系统正常"仍成立（只需删 symlink，系统走 config fallback）。**同时发现 launchd plist 配置债**：`com.openclaw.proxy.plist` 无 `ONTOLOGY_MODE` env 但 launchd 进程仍使用 shadow 模式（来源未追到，疑似 bytecode cache 或系统 env 继承），通过 `PlistBuddy Add :EnvironmentVariables:ONTOLOGY_MODE string on` 显式覆盖已修复；launchd 进程 PID 73456 Python 3.9 现以 on 模式 serve（Phase 4 wiring active 日志生效）。附带发现 adapter plist 同款双管理冲突（restart.sh manual 进程 + launchd KeepAlive 抢端口），登记 V37.9.13 架构清理。⑨ **元价值**: Phase 4 P1 是 V37.9.9 骨架到 V3 路标"pip install ontology-engine"终极目标的关键桥梁 — 从"本体只描述"升级为"本体可查询"。Phase 4 P2 下次推进: (a) contextual/temporal policy 的 context evaluator 实现（hour_of_day / request context matcher）(b) 选第二条 policy 做切换（max-tool-calls-per-task 最相似路径）(c) `evaluate_policy().applicable` 真值覆盖到 100% 策略。|
| V37.9.11 | 2026-04-22 | **MRD-RESERVED-FILES-001 闭环：BOOTSTRAP.md + SKILL.md 加入保留文件列表** — ① **触发**：V37.9.9 Mac Mini governance --full 扫 OpenClaw dist/*.js 639 个 JS 文件，发现 3 个 runtime 保留文件模式（`f.name === "X.md"`），但 `proxy_filters.RESERVED_FILE_BASENAMES` 只登记了 HEARTBEAT.md，漏掉 BOOTSTRAP.md + SKILL.md。MRD 连续 warn 提示未闭环。② **同类风险**：BOOTSTRAP.md / SKILL.md 虽未发生血案，但 MRD 源码扫描证实它们与 HEARTBEAT.md 同属 OpenClaw runtime 保留文件 —— LLM 写入会触发 runtime bootstrap/skill 机制，同款 13h 静默风险。③ **四层扩展**：(a) `proxy_filters.py::RESERVED_FILE_BASENAMES` 从 1 个扩到 3 个 basename (b) `RESERVED_FILE_SAFE_CONTENT` 从 HEARTBEAT-specific 改为通用 comment-only 骨架（首行从 `# HEARTBEAT.md` 改为 `# OpenClaw runtime reserved file — comments-only safe placeholder`，3 文件共享同一安全占位） (c) `SOUL.md 规则 11` 从单文件扩展到 3 文件条款，加入 basename 明细表 + 更新禁止行为 (d) **INV-HB-001 扩展 4 checks**：2 个 file_contains（BOOTSTRAP.md / SKILL.md 字面量存在）+ 1 个 runtime python_assert（for-loop 遍历 2×basename × 3×path_prefix = 6 场景 + edit 工具同款覆盖，避开 V37.3 exec scope trap）。④ **8 新 test_tool_proxy 单测** `TestReservedFileWriteBlock` 扩展：basename 常量守卫 ×2 / 精确集合契约（禁止漂移必须走 MRD） / BOOTSTRAP workspace 路径拦截 / SKILL home 路径拦截 / 2 文件 edit 工具拦截 / fix_tool_args BOOTSTRAP 恶意内容（`exec('rm -rf /')`）完全清除 / SAFE_CONTENT 文件无关性（首行不硬编码特定文件名）。⑤ **累计**: governance 59 inv / 283→287 checks (+4) / 41 suites / **1201 tests (+8)** / 0 fail / 安全 95/100 (Mac Mini 应保持 100/100). ⑥ **元价值**: MR-15 从"血案后 post-hoc 响应"进化为"源码扫描 pre-emptive 覆盖"— MRD-RESERVED-FILES-001 主动发现 OpenClaw 新增的 runtime 保留文件而不是等血案。此次闭环是 MR-7 (governance-execution-is-self-observable) 的生产级兑现：治理工具主动告警 → 人类修复 → 治理自证闭环。|
| V37.9.10 | 2026-04-22 | **audit_log 原子导出 + fsync 持久化加固（数据完整性 13→15 满分）** — ① **触发**：V37.9.9 Mac Mini 生产验证 security_score 跃升 93→98/100（bandit 已装 + 32 test suites），但 `数据完整性 13/15` 仍是唯一未满分项（`atomic_count=2` 因 audit_log.py 使用 append-only JSONL 未含 `os.replace`）。② **有意义的修复（非凑分数）**：(a) `audit()` 写入后加 `f.flush() + os.fsync(f.fileno())` + try/except OSError 兜底 tmpfs 不支持 fsync —— 防止 cron 环境进程崩溃时内核缓冲区尾部记录丢失 (b) 新增 `snapshot(dest_path)` 函数 — 用 `tmp + os.replace` 真原子模式导出审计日志快照到目标路径，保证"完整有效 JSONL 或根本不修改"，适用于备份/跨主机同步 (c) 新增 `--snapshot DEST` CLI 模式。③ **7 新单测 TestSnapshot**：缺审计文件返回 ok=False（非抛异常）/ snapshot 字节级一致副本 / 不留 .tmp 残余（atomic pattern 契约）/ 自动创建目标目录 / 覆盖已存在目标（os.replace 语义）/ 源码级守卫 os.replace + def snapshot / 源码级守卫 os.fsync。④ **累计**: security_score 数据完整性 13→15 / 总分 93→95（dev）/ 98→100（Mac Mini 预期）/ 41 suites / **1193 tests (+7)** / 0 fail / VERSION 0.37.9.9 → 0.37.9.10。⑤ **元价值**: 原则 #18 "补证据而非补功能"的正向兑现 — 每个改动对应真实运维场景（fsync=durability / snapshot=backup），不是为了让 grep 检查通过。|
| V37.9.9 | 2026-04-22 | **路线 C Step 4: security_score library 直调 + Ontology Phase 4 骨架** — ① **任务 A（Mac Mini V37.9.8 验证）**：ACL cron 今日 HKT 9:30:00 准时触发 + 10 篇论文推送成功（4/15 失败的任务复活；V37.9.8 调宽 28d 阈值策略生效）；auto_deploy heartbeat 每小时整点完美出行（`Wed Apr 22 06:00:06 ~ 10:00:06 HKT heartbeat: no change`）；watchdog 真实日志名为 `gateway_watchdog.log`（非 job_watchdog，对清单做修正）；**新发现 MOVESPEED rsync Operation not permitted 间歇性复发**（V37.9.4 APFS 重建后理论闭环但又报错，顶层权限 OK 但 rsync 子操作失败，登记观察）。② **任务 C（security_score 库化）**：INV-SEC-001 check 2 从 `subprocess.run(["python3","security_score.py"])` + 中文正则 parse `"安全评分：(\d+)/100"` 升级为 `from security_score import compute_score` 直调 + 从 `security_config.total_threshold` 读阈值。三问题一并解决：(a) 消除 ~200ms fork 开销每次 audit (b) 消除正则 parse 失败静默 pass 的塌陷盲区 (c) 消除硬编码 90 与 YAML 阈值漂移风险。三个 INV-SEC-001 runtime check 现全部 library + YAML 真理源统一。**3 新 regression test** `TestInvSec001UsesLibraryDirectCall`：(1) `test_no_subprocess_call_*` 锁定 active code 禁止 subprocess 反模式（智能跳过注释/文档块） (2) `test_inv_sec_001_imports_security_score_library` 契约 (3) `test_inv_sec_001_reads_total_threshold_from_yaml` 禁硬编码 90。③ **任务 B（Phase 4 骨架）**：新增 `ontology/domain_ontology.yaml`（六域概念模型 Actor/Tool/Resource/Task/Provider/Memory + 域间关系 + 实施路径 Phase 4 P1/P2/P3 清单）+ `ontology/policy_ontology.yaml`（10 条声明式策略 = 3 static + 2 temporal + 3 contextual + INV-PA-001 ordering_constraint + 元规则 MR-7/8/16 + 策略冲突检测 stub）+ `test_phase4_ontology_skeleton.py`（15 单测：六域声明/description 长度/Actor 核心实例/source_of_truth 对齐/Memory 四层匹配 memory_plane/policy 三类分类/policy 必填字段/hard_limit 仅 static/alert-isolation ordering_constraint/跨 ontology 引用一致性）。**状态**: declaration_only，未 wiring。下次 B 推进从 `engine.py::load_domain_ontology()` + `evaluate_policy()` 开始。④ **governance v3.30 → v3.31**: 58 inv / 272→273 checks。⑤ **全量回归**: 41 suites / **1186 tests** / 0 fail / 安全 93/100。⑥ **元价值**: 本次 C 是 MR-16（security-governance 双轨统一）的终态 — security_config YAML 真正成为 security_score.py + governance_checker 共同读的唯一数据源，观察者盲区彻底闭合。|
| V37.9.6 | 2026-04-21 | **告警噪声治理 + watchdog 行级时间戳过滤 (MR-4 反向变种修复)** — ① **触发**：12:30 watchdog 推送 12 项告警，**6 项是陈旧错误** (kb_evening 4/14-15 已 V37.8.10 闭环 / openclaw_discussions 4/8 推送失败 / kb_inject gateway 1006 / 等)。**告警疲劳是真实成本**——用户每天看到一半"幽灵告警"会逐渐麻木真告警。② **MR-4 反向变种**：前 14 次 silent-failure 都是"漏报"，第 15 次是"过度报"——同样违反"告警必须信号化"原则。③ **根因**：`job_watchdog.sh:scan_logs()` 只过滤文件 mtime（24h 内文件才扫），但 `tail -50 grep` 扫到的行内可能含 13 天前错误（持续更新的日志文件历史尾部）。④ **修复 awk 行级时间戳过滤**：解析 `[YYYY-MM-DD ...]` 前缀，仅保留 24h 内行；`in_recent` 状态机让 Python Traceback 多行延续跟随上一时间戳行状态（旧 Traceback 自动丢弃）。⑤ **失效源清理**：(a) `rss_blogs.sh` 注释掉 LangChain RSS（持续 9 次 HTTP 404 死链） (b) `job_watchdog.sh` ACL Anthology 阈值 8d → 14d（ACL 学术源更新频率本就低，4/8 后真无新内容不应报噪）。⑥ **INV-WATCHDOG-FRESHNESS-001 新增**（meta_rule=MR-4, severity=high, layer=[declaration, runtime]）7 checks：6 declaration scan watchdog 源码（V37.9.6 标记/cutoff_date/in_recent/recent_window grep/1209600 ACL 阈值/LangChain 注释）+ 1 runtime subprocess 调外部 `test_watchdog_freshness.py` 12 单测。⑦ **12 个新单测**：TestTimestampFilter(7) 锁定今日/昨日保留 + 13d 前过滤 + 旧 Traceback 多行延续过滤 + 新 Traceback 保留 + state 切换正确 + TestWatchdogShellInvariants(4) 源码守卫 + TestRssBlogsCleanup(1) LangChain 死链清理。⑧ **关键澄清**：08:28 端口冲突初判为 silent failure 实为**误读**——incident_snapshot.py `_tail_file` 读 proxy.log **历史尾部 100 行**含 traceback，proxy_stats.last_success_time=08:15:16 证明 8:15 后 proxy 正常运行。watchdog 在 8:30:03 因 11 alerts 触发 incident 写入。**告警通道工作正常**，问题在告警内容质量。⑨ **累计**：governance v3.29→v3.30 (57→58 inv +1 INV-WATCHDOG-FRESHNESS-001 / 265→272 checks +7) / 40 suites / 1159 tests (+12) / 0 fail / 安全 93/100 / VERSION 0.37.9.5 → 0.37.9.6。|
| V37.9.5 | 2026-04-21 | **数据全景审计 + workspace .md 接入 KB 索引（接缝盲区闭合）** — ① **数据全景审计**：用户直觉"~/.kb 67M 数月业务积累太少"驱动深度审计。发现：(a) ~/.kb 67M 含 text_index 44M 派生 → 真业务 23M；(b) ~/.openclaw/workspace 109M 中 109M 几乎全是 venv 53M+venv_pptx 56M（Python 包不是业务），真业务 .md ~50KB；(c) ~/.openclaw/logs 105M 中 90M 是 gateway.err.log 噪声，真业务 ~11M；(d) ~/.openclaw/media 41M 由 mm_index 单独管。**真业务总数 ~75MB**——精炼模式合理，但发现 workspace 顶层 PA 自主 .md（AGENTS.md / IDENTITY.md / SOUL.md / OPENCLAW_*_SUMMARY 等）从未接入 text_index。② **kb_harvest 压缩比验证**：4/20 提炼 78 条对话 84KB，合理无需修改。③ **任务 3 实施**（`kb_embed.py:scan_kb_files`）：扩展从只索引 `MEMORY.md` 单文件到扫描整个 `~/.openclaw/workspace/*.md`（顶层）。新增 `WORKSPACE_EXCLUDE_BASENAMES = {"MEMORY.md", "HEARTBEAT.md"}` —— MEMORY.md 已单独索引为 `source_type=memory`（向后兼容），HEARTBEAT.md 严格排除（V37.8.16 INV-HB-001 OpenClaw 控制文件不得当业务知识，避免下个血案）。`.bak` / `~` 排除（备份/临时文件）。其他 .md 用新 `source_type=workspace`。④ **INV-KB-COVERAGE-001 新增**（meta_rule=MR-4, severity=high, layer=[declaration, runtime]）4 checks：3 declaration scan kb_embed.py（含 workspace_dir 扫描 + HEARTBEAT.md 排除 + .bak 过滤）+ 1 runtime subprocess 调外部 `test_kb_embed_workspace.py` 单测（避开 V37.3 exec scope trap 的 YAML 内嵌闭包陷阱）。⑤ **10 个新单测** `test_kb_embed_workspace.py`：TestWorkspaceIndexing(7) + TestExclusionList(3) 锁定 AGENTS/IDENTITY 被索引 + HEARTBEAT.md/.bak 排除 + MEMORY.md `source_type=memory` 向后兼容 + 其他 .md `source_type=workspace` + 目录不存在不崩溃 + 常量 + V37.8.16 注释引用。⑥ **MR-4 silent-failure 第 15 次演出**：本次新形态 = 数据接缝盲区——~50KB workspace .md 数月以来 PA 自己搜不到自己写的内容，因 kb_embed.py 范围限定遗漏。每次新增类似 PA 文档源都需要回扫——INV-KB-COVERAGE-001 守卫的就是这个。⑦ **累计**：governance v3.28→v3.29 (56→57 inv +1 INV-KB-COVERAGE-001 / 261→265 checks +4) / 39 suites / 1147 tests (+10) / 0 fail / 安全 93/100 / VERSION 0.37.9.4 → 0.37.9.5。|
| V37.9.4 | 2026-04-21 | **B 问题闭环 + MR-4 silent-failure 第 14 次演出系统化预防** — ① **MOVESPEED exfat → APFS 转换**（SSH 远程 `diskutil eraseDisk APFS MOVESPEED disk6`，新挂载 `/dev/disk4s1 (apfs, journaled)`，POSIX 3/3 通过 mkdir/chmod/touch）解决 6 天 cron rsync 静默失败盲区。② **20 处 cron rsync 反模式批量修复**：旧 `rsync ... 2>/dev/null \|\| true` → 新 `rsync ... 2>&1 \|\| echo "[XXX] WARN: SSD rsync failed (exit=$?)" >&2`。覆盖 5 个根目录脚本（kb_dream/kb_save_arxiv/kb_evening/kb_inject/kb_review）+ run_hn_fixed.sh + 13 个 jobs/ 脚本（含 freight 2 处）= 共 18 文件 20 处。设计：失败时 stderr 出 WARN 进 cron log（可见+可 grep），exit 0 保留（不杀 cron 主流程），写 `>&2` 避免污染命令替换（顺势 MR-11）。③ **INV-BACKUP-001 新增**（meta_rule=MR-4, severity=high, layer=[declaration, runtime]）3 checks: declaration 全局扫 .sh 禁止 `2>/dev/null \|\| true` 反模式 + 任何含 rsync MOVESPEED 必须有 "WARN: SSD" 字样 + runtime 真跑模拟 rsync 失败断言 stderr 含 WARN + stdout 不含。**部署即时验证**：第 1 次跑就抓出第 20 个漏网（run_hn_fixed.sh），声明层守卫真有效。④ **案例文档** `ontology/docs/cases/movespeed_exfat_silent_backup_failure_case.md`：完整四维度因果链架构图 + 三层根因（exfat fskit transient EPERM 触发 / 18 处复制粘贴反模式放大 / 监控盲区掩护）+ 时间线还原 + 6 条件组合分析 + MR-4 14 次演出对照表 + MR-8 copy-paste-is-a-bug-class 反例兑现 + 6 条元教训。⑤ **元洞察**：本次是 MR-4 silent-failure 的**新形态**——前 13 次都是单点静默，第 14 次是**多脚本统一反模式协同沉默 6 天**。修复证明 MR-8 反例：复制粘贴 18 次的反模式 = 系统性 bug 类，需要工具发现（INV-BACKUP-001 全局 scan）而非靠程序员记忆。⑥ **累计**：governance v3.27→v3.28 (56 inv (+1 INV-BACKUP-001) / 261 checks (+3) / 15 meta rules / 13 MRD scanners) / 38 suites / 1137 tests / 0 fail / 安全 93/100 / 对抗审计 16/16 / VERSION 0.37.9.3 → 0.37.9.4。|
| V37.9.3 | 2026-04-21 | **四项顺序推进：X 僵尸告警可观测性 + MR-6 9 项 high 补齐 + audit-of-audit 4 维度扩展 + 路线 C Step 3 终态** — ① **#1 X 僵尸告警路径诊断**（`jobs/finance_news/run_finance_news.sh`）：4/20 07:30 三天连续命中但 WhatsApp+Discord #alerts 零到达。Mac Mini 日志证实执行到 `notify` 调用但静默，手动测试证明 notify+前缀+topic 路径正常工作 → 定性为 Gateway 瞬时故障（已自愈）。最小修复：(a) DAY/TS 去掉显式 `TZ=Asia/Hong_Kong`，与 YESTERDAY/DAY_BEFORE 统一用系统默认 TZ，消除时区不一致漏洞 (b) 告警路径每个分支加诊断 log（5 种跳过原因识别：日期解析失败/昨-前日文件不存在/文件为空/交集为空/NOTIFY_LOADED=false）(c) `notify` 调用加 `\|\| log "exit=$?"` 捕获失败 exit code。下次再发故障能留下决定性证据。② **#2 MR-6 9 个单层 high 全部补齐** — MRD-LAYER-002 从 ⚠️ 9 个 warn 转为 ✅ pass。分三批：Batch A 纯 metadata 修正（`INV-TOOL-003 / INV-CRON-002 / INV-NOTIFY-003` 已有 python_assert 性质为 runtime，只标签错）；Batch B 真缺 runtime（`INV-NOTIFY-002 / INV-HEALTH-001 / INV-NOTIFY-004 / INV-JOB-GOVAUDIT-001 / INV-JOB-HARVEST-001` 各加一个 subprocess 或 AST 级 runtime python_assert，notify 空 channel stderr ERROR / 空 channel token 捕获 / AST MapReduce 函数存在 / governance_audit_cron 因果链 + alerts topic 路由）；Batch C 缺 declaration（`INV-CRON-004` 加 YAML 源码 `_cron_cmd_invokes` + `cmd.endswith(entry)` 守卫，与 INV-CRON-003 共享策略）。25 个 high 不变式全部 ≥2 层验证。③ **#3 audit-of-audit 从 1 维扩展到 4 维**（`ontology/governance_checker.py`）— 原 `MRD-AUDIT-PERF-001` 仅 `wall_time_ms`，扩展为：`wall_time_ms`（保留 1.3x+300ms）+ `peak_memory_mb`（新，1.5x+10MB，跨平台 `resource.ru_maxrss`）+ `bootstrap_ms`（新，2.0x+500ms，SESSION_START 到第一个 check 的启动开销）+ `skip_rate_pct`（新，绝对 +20pct 突增）。`_AUDIT_FIRST_CHECK_TIME` 全局变量在 `run_all` 第一次 iteration 时 stamp。老 history 缺字段时 median=0 自动跳过该维度判定（向后兼容）。新增 14 单测 `ontology/tests/test_audit_perf_dimensions.py`：4 维度独立阈值边界 + 向后兼容 + 跨平台内存转换 + 3 new fields 写入完整性。④ **#4 路线 C Step 3 数据源统一到 ontology** — V37.9 Step 1 `INV-SEC-001` 总分 ≥ 90 / V37.9.1 Step 2 per-dimension MIN_THRESHOLDS 硬编码在 YAML python_assert / V37.9.3 Step 3 阈值上升到 `governance_ontology.yaml` 顶层 `security_config.dimensions` 作为**唯一真理源**。INV-SEC-001 从 `security_config` 读阈值（不再硬编码），新增"ontology 维度名 vs security_score.py 实际输出对齐"守卫（防改名漂移）。`security_score.py` 新增 `load_ontology_thresholds()` 纯函数 + `check_ontology_thresholds(data)` 纯函数 + `--check-ontology-thresholds` CLI 模式（读 YAML 对比 exit 0/1）。新增 12 单测 `test_security_ontology_alignment.py`：YAML 段存在 + 7 维度声明 + 名字跨源对齐 + 阈值 ≤ 满分 + INV-SEC-001 不得重新硬编码 + CLI 模式 + 纯函数 pass/fail 三场景。⑤ **累计**: governance v3.26→v3.27 (55 inv / 258 checks / 15 meta rules / 13 MRD scanners, 新增 security_config 顶层段) / 38 suites / 1137 tests (+26) / 0 fail / 安全 93/100 / 对抗审计 16/16 (100%) / VERSION 0.37.9.2 → 0.37.9.3 / Opus 4.7。|
| V37.9 / V37.9.1 / V37.9.2 | 2026-04-20 | **Stage 2 验证者阶段四轮交付：路线 A 血案回填 + 路线 B 对抗审计 + V37.8.18 盲区修复 + V37.9 结构加固 + V37.9.1 安全统一 + V37.9.2 MR-6 深度补齐** — 从"修血案"进入"建制度"。① **路线 A 实证交付** `ontology/docs/audit_coverage_retrospective.md` (618 行): 15 个真实血案逐一回填 Q1/Q2/Q3 三问 → **0% 预防率 + 87% 回归率 + 80% 空白类别**。② **路线 B 对抗审计** `ontology/docs/adversarial_audit_report.md` (177 行) + `ontology/tests/adversarial_chaos_audit.py` (572 行, 16 场景): Cat A 10/10 (已知血案回归 100% 防御) + Cat B 6/6 (探测盲区全部闭合，V37.8.17 前 0/6 → 今日 6/6)。③ **V37.8.17 MR-14/MR-15 MRD 三步跃迁** (单点修复 → 元规则 → MRD 扫描器): MRD-ALERT-INDEPENDENCE-001 + MRD-RESERVED-FILES-001。④ **V37.8.18 P1+P2 修复 5/6 盲区**: C14 INV-KB-001 扩展 dict 防御 / C11 MRD-SILENT-EXCEPT-001 新增 / C15 MRD-PUSH-ROUTE-001 新增 / C12 INV-COST-001 retry 上限 / C13 INV-OBSERVABILITY-001 扩展 last_run 一致性。改进 adversarial counter 从 boolean → 按 "N 处" 数字解析 (工具自身递归 MR-7 自观察)。⑤ **V37.9 四项结构加固**: (a) adversarial Cat A 集成 full_regression layer 3.5 (每 PR 前跑防御力回归) (b) MRD-LAYER-002 渐进 warn severity=high 12 项技术债 (c) MRD-AUDIT-PERF-001 audit-of-audit (SESSION_START 实时 wall + 中位数双阈值 1.3x+300ms, C16 对抗 sleep 0.5→1.5s UNEXPECTED catch) (d) MR-16 + INV-SEC-001 security+governance 双轨统一 (score ≥ 90 守卫)。⑥ **V37.9.1 路线 C Step 2**: INV-SEC-001 按 7 维度 MIN_THRESHOLDS 细化 (密钥 15/测试 13/数据 10/部署 15/传输 9/审计 15/可用 8), 防"单维度塌陷但总分被其他拉高"。⑦ **V37.9.1 Stage 2 话语权** `docs/articles/audit_is_regression_not_prevention.md`: 立场型文章（第 4 份公开资产）论证 "audit 不是 prevention 是 regression engine"，6 条可落地原则。⑧ **V37.9.2 MR-6 深度补齐** (12→9 单层 high): INV-PA-002 runtime 解析规则 10 完整块 / INV-DREAM-001 runtime 解析 DREAM_TIMEOUT_SEC 动态映射 / INV-DEPLOY-003 runtime 扫双侧 status.json 邻近 continue。全部避开 V37.3 exec scope trap 用 iterative for。⑨ **原则 #30 升级 Opus 4.7**: 合法模型家族 = Opus (4.6/4.7/更新)，升级不是降级。⑩ **今日 X 僵尸清理**: ForeignPolicy/Carnegie_Endow/PDChina/CNS1952/SingTaoDaily 5 账号 4/18-19-20 三天连续命中从 FINANCE_X_ACCOUNTS 移除 + 6 个 file_not_contains 守卫。⑪ **KB 数据闭环验证**: chaspark.md 35 chunks + finance_daily.md 116 chunks 全部进入 text_index，kb_dream 今日提及 4 次，数据复利正向循环确认。⑫ **累计数字**: governance v3.19→v3.26 (55 inv / 256 checks / 15 meta rules / 13 MRD scanners) / 37 suites / 1111 tests / 安全 93/100 / 对抗审计 16/16 (100%) / VERSION 0.37.8.15 → 0.37.9.2 / 今日 30+ commits (e21d086 → 10a8492)。|
| V37.8.16 | 2026-04-20 | **PA 自残 HEARTBEAT.md 血案闭环（MR-4 silent-failure 第 12 次演出 + MR-15 新元规则立案）** — ① **血案**：2026-04-19 09:09 PA (Wei) 处理 HN 告警对话时调用 write 工具把"[SYSTEM_ALERT] HN热帖抓取已恢复 / 任务完成 / 下一步监控"三行写进 `~/.openclaw/workspace/HEARTBEAT.md`（把它当普通 TODO 文件名用）。13 小时潜伏期后（4/19 22:09）用户开始发 WhatsApp 消息，OpenClaw heartbeat 机制 (`auth-profiles-*.js:48796 isHeartbeatContentEffectivelyEmpty=false`) 激活 `runKind="heartbeat"` 模式，system prompt 变成默认 "Read HEARTBEAT.md if it exists. Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK." → Qwen3 严格执行 + 看到 HEARTBEAT.md 里"任务完成"判定无需关注 → 对每条用户消息都回 "HEARTBEAT_OK"（12 字符）→ Gateway `stripTokenAtEdges` 剥离 12 字符 → outbound 空 → 用户完全看不到 PA 回复 13 小时直到人肉察觉。② **六条件组合爆炸**：(1) WhatsApp plugin 4/10 14:53 被 OpenClaw 自动安装（WhatsApp routing 从 core 改为 plugin）(2) PA 有 write 工具 + workspace 访问（一直有）(3) HEARTBEAT.md 是 OpenClaw 保留文件（一直是）(4) PA 不知道它有特殊语义（一直不知道）(5) `agents.defaults.heartbeat.prompt` 未配置 → fallback 默认 prompt（一直未配置）(6) HEARTBEAT.md 非空非注释 ← **4/19 09:09 PA 自写触发第 6 项，前 5 项长期潜伏**。③ **反面教训 (原则 #28 违规记录)**：4/19 深夜我（Claude Code）初诊时看到 proxy /stats 502 + p99 126s → 先猜 primary/fallback 问题；看到 Gateway auth 日志 "Bad MAC" → 立刻联想 auth corruption → 让用户 `rm -rf auth` + QR 扫码重新登录。**这是不可逆操作**，幸好用户回滚无损失。正确路径是今天早上走的：读 status.json 焦点 → 查 git log 排除自己改动 → 读 OpenClaw 源码 `resolveHeartbeatPrompt` → diff 4/3 vs 4/10 openclaw.json → 读 HEARTBEAT.md 实际内容（锁定 PA 自写）— **六轮假说错五轮才到达根因**。④ **最小修复**：`cat > ~/.openclaw/workspace/HEARTBEAT.md` 置为只含注释（3 行 # 开头），`isHeartbeatContentEffectivelyEmpty()` 立即返回 true → heartbeat 不再激活。2026-04-20 09:16 WhatsApp E2E 实测"早上好 你在吗" → PA 5 秒延迟回复"早上好，我在。系统状态正常" ✅。⑤ **MR-15 新元规则立案** `reserved-files-must-not-be-writable-by-llm`：任何被底层 runtime（OpenClaw/Gateway/Proxy/Adapter）赋予特殊语义的文件路径，不得暴露给 LLM 的 write/edit 工具作无约束写入目标。典型违反 = HEARTBEAT.md。合规做法四层联合防御：(1) 声明层 `RESERVED_FILES` 明细表 (2) 行为层 SOUL.md 规则 11 + 血案注释 (3) Proxy 拦截层 `detect_reserved_file_write()` 纯函数 + `fix_tool_args` 集成改写 `args.content` 为 `RESERVED_FILE_SAFE_CONTENT`（只含 # 注释行）(4) 测试层单测覆盖每条保留路径 + 每种 path alias。**MR-15 与 MR-4 关系**：MR-4 是问题分类（静默故障是 bug），MR-15 是上游预防层（别让 LLM 碰能制造静默故障的 runtime 文件）。⑥ **INV-HB-001 新增** `heartbeat-md-reserved-file-not-llm-writable`（meta_rule=MR-15, severity=critical, verification_layer=[declaration, runtime]）12 checks：proxy_filters.py `RESERVED_FILE_BASENAMES`/`RESERVED_FILE_SAFE_CONTENT`/`detect_reserved_file_write`/`fix_tool_args` 调用 + SOUL.md 规则 11 三个 grep 守卫（"禁止写 OpenClaw 保留文件"/"2026-04-19 血案规则"/"MR-15"）+ runtime python_assert 真跑血案场景（构造 PA 写入 HEARTBEAT.md 的 tool_call JSON 断言 content 被改写为 SAFE_CONTENT、原"任务完成"字样消失、SAFE_CONTENT 所有非空行以 # 开头）。⑦ **SOUL.md 规则 11 新增**（位置：规则 10 "告警消息不跟进"之后，`## 我的性格` 之前，避开 `## 当前项目状态` 自动刷新区）：禁止路径列表、识别规则（精确名 HEARTBEAT.md）、禁止行为（write/edit 任何内容）、替代方案（kb_write.sh / status_update.py）、血案时间与字符串锚点、四层结构修复引用。⑧ **16 新单测** `TestReservedFileWriteBlock`：纯函数 detect_reserved_file_write 11 测（blocked/allowed/case-sensitive/alias/trailing-slash/非 dict 参数/空字符串/非字符串 path 等）+ fix_tool_args 集成 3 测（write content 被改写 / edit new_text 被改写 / 正常 write 不受影响）+ 常量守卫 2 测（RESERVED_FILE_BASENAMES 含 HEARTBEAT.md / SAFE_CONTENT 只含 # 注释）。⑨ **governance v3.19 → v3.20**：52→53 不变式，236→248 checks，14→15 meta rules。**全量回归 36 suites / 1111 tests / 0 fail / 53/53 invariants / 234/234 checks / 15/15 meta rules / 安全评分 93/100**。⑩ **案例文档** `ontology/docs/cases/heartbeat_md_pa_self_silencing_case.md`（完整四维度因果链架构图 + 三层根因 + 时间线还原 + 六条件组合爆炸 + MR-15 元规则提炼 + 4/19 深夜原则 #28 违规复盘 + 6 轮假说错五轮的教训）。⑪ **未解决项登记到 unfinished**：WhatsApp 入站自 4/10 起路由到 research agent 而非 main（main/sessions.json mtime 停留在 Mar 30），但 HEARTBEAT.md 修复后 research agent 正常响应，不致命；长期应考虑 `channels.whatsapp.agent` 显式配置或 `agents.list` 顺序调整。⑫ **元教训**：MR-4 silent-failure 第 12 次演出的新形态——不是错误被吞、不是错误被稀释、不是告警路径失效，是 **LLM 正确响应系统 prompt + Gateway 正确剥离 ack token + 但 LLM 误读了 system prompt 的适用范围**。整条链路每一环都"按设计工作"，但设计本身有语义 gap：HEARTBEAT.md 作为 runtime 控制文件 vs LLM 视为普通 TODO 文件 —— 这个 gap 没被任何一层填补。**MR-15 正是填补这种 gap**：把 runtime 边界的保留语义显式声明给所有可能触碰的层。|
| V37.8.15 | 2026-04-17 | **preflight push test 垃圾消息修复 + Mac Mini 分支错位修复 + GitHub 全面刷新 + 外部评审吸收** — ① **触发**：用户报告每 2 分钟收到 `🔧 preflight push test` WhatsApp 消息（22 分钟内 12 条）。② **双层根因**：(a) Mac Mini repo 在 `claude/start-daily-work-Mql3p` 分支（非 main），`git fetch origin claude/...` 拿到旧 tip，与 HEAD（merge commit）永远不等 → `HAS_NEW_COMMITS=true` 无限循环 (b) `preflight_check.sh` step 16 push test 无速率限制，每次 `--full` 都发 WhatsApp。③ **三层修复**：Fix A Mac Mini `git checkout main` 切回正确分支（根因）/ Fix B `auto_deploy.sh` 传 `SKIP_PUSH_TEST=1` 给 preflight（auto_deploy 有自己的 quiet_alert）/ Fix C `preflight_check.sh` push test 加每小时速率限制（defense-in-depth）。④ **GitHub 全面刷新**：README v37.8.15（治理 52 inv / 35 jobs / Phase 3 ontology / 81 FILE_MAP / 15 案例）+ Issue #132 进度更新（393→1093 tests 增长表）。⑤ **OpenClaw 升级评估**：上游 v2026.4.15（+6 版本），#59265 经 14 版本仍 OPEN，继续 hold。⑥ **外部代码级评审吸收**：六优势（控制平面意识/Provider 抽象/韧性/阈值/门禁/战略）+ 五劣势（脚本特征/环境耦合/PyYAML/Memory 接口/文档过载）。录入 4 项 P0/P1 优先级：可迁移性闭环 + 证据链产品化 + 插件生态接口 + 可观测闭环。核心三字诀：**标准化 + 可比较 + 生态协议**。⑦ **Nikkei Asia 遗留关闭**：X Syndication 17/21 条正常，非僵尸。⑧ **全量回归**：36 suites / **1095 tests** / 0 fail / 52/52 inv / 安全 93/100。|
| V37.8.13 | 2026-04-16 | **Gateway 宕 9h 静默血案闭环 + auto_deploy FILE_MAP 自复制修复 + status.json 形状修复（MR-4 第 11 次演出）** — ① **触发**：2026-04-16 00:30 HKT Gateway 进程死亡（restart.sh bootstrap 后 21 秒崩溃 → launchd rapid-crash jettison），WhatsApp 全断 9 小时。② **三层放大器全失**：(a) auto_deploy `quiet_alert` 凌晨静默期 **同时跳过 WhatsApp 和 Discord**，3 次 CRITICAL preflight 失败全被 `[QUIET]` 吞（主谋）(b) `wa_keepalive` 每 30min 写 WARN 到日志**但不推送任何告警**（18 次 WARN 沉默 9h）(c) `restart.sh` 报 "Done!" 但不验证 Gateway 是否真活。③ **三层修复**：Fix A `quiet_alert` 静默期仅跳过 WhatsApp、Discord 始终推送 / Fix B `wa_keepalive` 连续 2 次 WARN 自动升级 Discord #alerts（告警链不依赖失效主体自身——Gateway 宕则 WhatsApp 不通，必须走 Discord）/ Fix C `restart.sh` post-bootstrap 5×3s 健康验证循环。④ **INV-WA-001**（meta_rule=MR-4, severity=critical, verification_layer=[declaration, runtime]）7 checks + **INV-QUIET-001** 4 checks。⑤ **V37.8.12 修复**：auto_deploy FILE_MAP 自复制行移除 + 双循环自复制守卫（`$REPO_DIR/$SRC == $DST` 跳过，防 macOS cp 非零 + set -e 杀脚本）。⑥ **status.json 形状修复**：`format_human` 防崩（非 dict 条目防御） + 数据修正（`session_context.unfinished` str→list、扁平 key 清理、`recent_changes` 损坏条目重建）。⑦ **21 单测** `test_wa_gateway_resilience.py`：TestQuietAlertDiscordAlways(4) + TestWaKeepaliveEscalation(9) + TestRestartGatewayVerification(5) + TestCrossFileGuards(3)。⑧ **governance v3.17→v3.18**：40→42 不变式，199→210 checks，12/12 MR。⑨ **全量回归**：37 suites / **1092 tests** / 0 fail / 42/42 inv / 196/207 checks / 安全 93/100。⑩ **案例文档** `whatsapp_silent_death_case.md`：完整四维度因果链 + MR-4 第 11 次演出叙事 + 元教训"告警链不得依赖失效主体自身"。⑪ **MR-14 候选**：`alert-path-must-not-depend-on-failing-subject`（本案核心教训升级为元规则）。|
| V37.8.11 | 2026-04-15 | **auto_deploy 每小时漂移告警噪声闭环（MR-4 第 10 次演出：expected-behavior 被错分类为 error）** — ① **触发**：用户反馈"几乎每小时都收到 `[SYSTEM_ALERT] 漂移检测: 修复 1 个部署文件不一致，已自动覆盖`"。用户确认**问题存在很久了，只是没注意**——V37.4.3 `[SYSTEM_ALERT]` 前缀让长期潜伏的告警在 WhatsApp 显性化。② **根因**：V37.8.1 曾给 preflight_check.sh drift 循环加过 `if SRC == status.json then continue` 豁免（因为仓库 status.json 是 Claude Code 快照，runtime `~/.kb/status.json` 被 kb_status_refresh cron 每小时重写 health/quality 字段，两侧设计上永远不一致），但 **auto_deploy.sh 的等价 drift 循环没有同步加豁免**——遗漏至今。每小时整点（minute<2）auto_deploy 轮询 → md5 比对发现不一致 → `cp` 覆盖 runtime（清空 runtime 数据） → `quiet_alert` 推送 [SYSTEM_ALERT] → 下个整点 kb_status_refresh 重写 runtime → 恶性循环。③ **MR-4 第 10 次演出新形态**：前 9 次 silent-failure 都是"错误发生了但不可见/被稀释"。第 10 次是**expected-behavior 被系统错分类为 error，然后产出噪声告警**——不是错误被吞，是**正常行为被当错误**。④ **结构修复**：auto_deploy.sh drift 循环（line 317 起）镜像 preflight 同款豁免 `if [[ "$SRC" == "status.json" ]]; then continue; fi`，保留 **new-commit 同步路径**（上方 CHANGED_FILES 循环）继续把 Claude Code 的 priorities/unfinished/recent_changes intent 单向下传。契约："one-way intent flow (repo → runtime via new-commit) + exempt from two-way drift detection"。⑤ **INV-DEPLOY-003 新增**（meta_rule=MR-4, severity=high, verification_layer=[declaration]）5 checks：preflight 豁免守卫×2 + auto_deploy 豁免关键字 + V37.8.11 注释标记 + FILE_MAP 仍含 status.json（intent 通道不能丢）。⑥ **案例扩展**：`kb_evening_fallback_quota_chain_case.md` 新增 V37.8.11 章节登记此血案类（"告警噪声也是 observability 问题"）。⑦ **governance v3.16 → v3.17**：39→40 不变式，194→199 checks。⑧ **全量回归**：36 suites / **1071 tests** / 0 fail / 40/40 invariants / 185/196 checks / 12/12 MR / 安全 93/100。⑨ **Mac Mini E2E**：下一个整点（2026-04-16 00:00 HKT）drift 告警应不再触发（只要 auto_deploy 已通过 git pull 拉到 V37.8.11）。⑩ **关键原则反思**：本案印证 **原则 #15 "定期像用户一样使用系统"**——这个 bug 如果我们自己用 WhatsApp 实际观察日常推送，半天就能发现，而不是等用户受够了主动上报。观察盲区的体现：**开发环境所有单测/治理/preflight 全绿，但生产环境用户每天被 24 条噪声告警折磨**。⑪ **元教训**：每当一个类别的 check 被加豁免（如 V37.8.1 的 preflight），应该**系统性扫描**同类 drift 检测脚本是否都需要同款豁免——auto_deploy 和 preflight 是两份独立的 FILE_MAP 消费者，V37.8.1 只修了一半。|
| V37.8.10 | 2026-04-15 | **kb_evening 连续 2 天 22:00 告警"HTTP 502: Bad Gateway"血案闭环 — LLM 错误链三层稀释（MR-4 第 9 次演出）** — ① **触发**：2026-04-14 首次 + 2026-04-15 再次，用户同一时段收到 `[SYSTEM_ALERT] kb_evening 失败 原因: LLM 晚间整理失败: HTTP 502: Bad Gateway`。连续 2 天定性为结构性。② **三层根因**：(a) **触发器**：2026-04-14 某时刻起 primary Qwen3 (hkagentx.hkopenlab.com) 开始不稳定，`adapter._CircuitBreaker.consecutive_errors` 累积到阈值 → breaker OPEN（22:00 kb_evening 请求到达时直接 skip primary，2026-04-15 23:53 primary last_success_time 证实时好时坏非宕机） (b) **放大器**：`FALLBACK_CHAIN = ["gemini"]` **只配 1 个 fallback**。gemini 2.5-flash 免费层 daily/rate quota 被白天 ~20 个 LLM cron (kb_inject/finance_news/arxiv/daily_ops/ai_leaders_x/hf_papers/ontology_sources/s2/dblp/pwc/github_trending/rss_blogs/freight…) 持续磨耗 → 22:00 gemini 也 429，整条链路死亡 (c) **掩护者（本血案核心）**：**三层错误链稀释**——adapter `_send_json(502, {"error": "ALL 1 FALLBACKS FAILED: gemini 429"})` → proxy `except Exception as e: str(e)` 只拿"HTTP Error 502: Bad Gateway"，**从未调 `e.read()`** → client `rc.call_llm` HTTPError `f"HTTP {e.code}: {e.reason}"`，body 再次丢弃。真实原因"gemini 429 quota 耗尽"经 3 跳稀释完全不可见。③ **结构修复**（两侧缺一不可）：(1) `proxy_filters.py` 新增 `compose_backend_error_str(exc)` 纯函数 + `MAX_UPSTREAM_BODY_CHARS=500` 常量，对 HTTPError 读 `e.read()` body + JSON `error` 字段提取 + 截断 + fail-open try/except（observability 增强绝不能成为新故障源）(2) `tool_proxy.py except Exception` 分支 import 并调 `compose_backend_error_str(e)` 替代 `str(e)` (3) `kb_review_collect.py` 镜像 helper `_compose_http_reason(http_error)` + `MAX_UPSTREAM_BODY_CHARS=400` + `call_llm` HTTPError 分支 `return False, "", _compose_http_reason(e)` (4) **架构契约 MR-8 正向兑现**：helper 放在 `proxy_filters.py`（纯函数无网络依赖，符合文件表架构）而非 `tool_proxy.py`（import 即启动 HTTP server 不可测），让 `test_tool_proxy` 可直接导入测试。④ **INV-OBSERVABILITY-001 新增**（meta_rule=MR-4, severity=high, verification_layer=[declaration, runtime]）12 checks：proxy 侧 4 check（helper 定义 / MAX 常量 / read body / fail-open return base）+ tool_proxy 侧 3 check（import compose_backend_error_str / 不重新定义 helper 守卫×2）+ client 侧 3 check（helper 定义 / MAX 常量 / HTTPError 分支调 helper）+ 2 runtime python_assert 真跑血案场景（proxy helper 拼 upstream body 含"gemini 429" + kb_review helper fail-open on e.read 抛异常）。⑤ **21 新单测** `test_kb_review.TestComposeHttpReason`(10) + `test_tool_proxy.TestComposeBackendErrorStr`(11)：JSON error 提取 / 血案实际场景复现 / 非 JSON raw text fallback / empty body 向后兼容 / 超长 body 截断 / utf8 decode errors 替换 / e.read 抛异常 fail-open / 端到端 call_llm integration / tool_proxy.py import guard 禁止重新定义 helper。⑥ **Fix D 配套**：Mac Mini adapter 进程自 V36 (0.36.0) 起**首次重启**到 0.37.8.9——`bash ~/restart.sh` 后 `/health version: "0.37.8.9"` 生效（之前 V37.1~V37.4 三次 adapter.py commit 一直未在运行时）。但 `fallback_chain` 仍只显示 `["gemini"]`，说明 V3 capability routing 真跑但 `providers.d/` 下其他 provider 缺 API key env var → **Fix B 扩展 fallback 是独立的运维决策**，登记为 V37.8.11 候选。⑦ **MR-4 silent-failure 第 9 次演出（新形态）**：前 8 次都是"错误发生了但没被发现"。第 9 次是**错误被发现了但原因不可见**——用户看到告警，误以为观察机制工作正常，实际信息密度已经是零。这是更隐蔽的变种。⑧ **governance v3.15 → v3.16**：38→39 不变式，168+12→194 checks，meta rules 12/12 不变。⑨ **全量回归**：36 suites / **1071 tests** / 0 fail / 39/39 invariants / 180/180 checks (11 skip runtime-only) / 12/12 meta rules / 安全 93/100。⑩ **登记未解决项（不和本血案捆绑）**：(a) 扩展 fallback_chain 到 ≥2 provider — 需用户决策 API key (b) 其他 17+ LLM cron 加 `last_run_*.json` — 统一 observability 入口 (c) proxy_stats `auto_recovery_rate_pct=194.1%` bug（>100% 数学错误）(d) MR-13 候选元规则 `error-chain-must-preserve-upstream-cause-across-layers` — 把本血案教训升级为元规则 (e) gemini quota 消耗审计：白天 ~20 cron 哪些可降频。⑪ **案例文档** `ontology/docs/cases/kb_evening_fallback_quota_chain_case.md`（TL;DR + 完整因果链架构图 + 三层根因 + 时间线还原 + 七条件组合分析 + MR-4 演出史更新 + 5 条结构教训）。⑫ **Mac Mini E2E 待观察**：明日 2026-04-16 22:00 下次 kb_evening 告警（若再失败）应显示完整 upstream chain，形如"HTTP 502: Bad Gateway \| upstream: ... \| upstream: ALL 1 FALLBACKS FAILED: gemini HTTP 429"。|
| V37.8.9 | 2026-04-15 | **MR-11 / MR-12 运行时检测落地 — 元规则从"声明层"升级为"CI 可检测层"** — ① **触发**：V37.8.8 完成 MR-11 + MR-12 立案但两条元规则停留在 governance_ontology.yaml 的 `meta_rules:` 段声明层，无运行时扫描器，未来任何新代码违反不会被 CI 捕获。用户要求"#4 + #5 MRD 落地"把两条元规则从**被动声明**升级为**主动检测**。② **MRD-LOG-STDERR-001 实现**：`_discover_log_stderr_violations()` 在 governance_checker.py 扫所有 shell 文件（`*.sh` + `jobs/**/*.sh`）查找 log/debug/status/warn/info/notice/err/err_log 等诊断函数定义，识别单行 `log() { echo ...; }` + 多行 `log() {\\n ... echo ... \\n}` 两种形态。`_is_echo_to_stdout()` 纯函数判定：echo 行有 `>&2` 或 `1>&2` = stderr 合规；`>> file` 或 `> file` = 重定向到文件（不污染 stdout）；其余 = 违规。**白名单豁免 11 个用户交互诊断工具**（`cron_doctor/preflight_check/job_smoke_test/full_regression/daily_ops_report/health_check/smoke_test/quickstart/gameday/governance_audit_cron/kb_status_refresh`）因为它们 stdout 是用户终端的合法输出目标，不会被命令替换捕获。③ **MRD-LLM-PARSER-POSITIONAL-001 实现**：`_discover_llm_parser_positional_violations()` 扫 LLM 调用脚本集合（`jobs/*/run_*.sh` + `kb_*.{py,sh}` + `run_hn_fixed.sh` + `jobs/**/*.py` 共 37 个）检测三种反模式：`lines\[i+N\]` (任何 N≥1) / `i += N` (N≥2, 排除合法 while 遍历的 `i += 1`) / `(content\|text\|response\|result\|output\|raw\|llm_content).split()[N]`（白名单变量名避免误报）。**跨行 docstring 状态机豁免**（V37.8.9 edge case）：进入 `"""` 或 `'''` 块后跳过所有行直到遇到配对关闭符，这样历史血案注释里提到的 `lines[i+1] / lines[i+2]` 字样（V37.8.7 case doc）不被误判。**test_\*.py 文件豁免**、**assertNotIn/assertRaises 行豁免**、**注释行豁免**。④ **批量修复 19 个 job 脚本 log() 写 stderr**：用 sed 把 `log() { echo "..."; }` 替换为 `log() { echo "..." >&2; }` 覆盖 jobs/（acl_anthology/ai_leaders_x/arxiv_monitor/dblp/finance_news/freight_watcher/github_trending/hf_papers/karpathy_x/ontology_sources/openclaw_official×2/pwc/rss_blogs/semantic_scholar） + 主仓库（kb_evening/kb_inject/kb_review/run_hn_fixed）。格式统一的单行 log 定义使批量修复安全可行。修复后重新 scan：`所有 38 个 shell 文件的 log/debug/status/warn 函数均写 stderr（11 个诊断工具豁免）`。⑤ **governance_checker.py mr_used 计数器扩展**：原实现只从 `results` (invariants) 收集 meta_rule，V37.8.9 新增同时收集 `meta_rule_discovery` 引用的元规则——从 `元规则: 10/12` 升到 **`12/12`** 覆盖率。未来 MR-13/MR-14 加入后，只要任一 invariant 或 MRD 引用它们就会进入 used 集合。⑥ **32 单测** `test_governance_mrd_v8_9.py` 加入 full_regression：TestIsEchoToStdout(6) + TestScanShellLogFunctions(7) + TestDiscoverLogStderr(3) + TestPositionalPatterns(9) + TestDiscoverLlmParser(2) + TestMRDDeclarationInYaml(4) + 1 集成。关键边界：`i += 1` 豁免（合法 while 遍历）、`i += 2/3/4+` 捕获（跳多行即 MR-12 违反）、`echo ... >> file` 不报（重定向到文件）、多行函数体整体 `} >&2` 后置重定向合规、跨行 docstring 内的反模式字样豁免（避免血案注释被误判）。⑦ **governance v3.14 → v3.15**，38 不变式不变（MR 是元规则不是 invariant），168 checks 不变；两个新 MRD 加入 `meta_rule_discovery:` 段作为 Phase 0 可执行元规则发现。⑧ **全量回归**：35 suites / **1050 tests** / 0 fail / 38/38 invariants / 168/179 checks / **12/12 元规则**（从 V37.8.8 的 10/12 升满） / 安全 93/100。⑨ **未来保障**：任何新增 shell 脚本的 log/debug 函数写 stdout 会被 MRD-LOG-STDERR-001 自动 warn；任何新增 LLM 调用脚本用 `lines[i+N]` 或 `i += N` (N≥2) 解析会被 MRD-LLM-PARSER-POSITIONAL-001 自动 warn。两条元规则从**依赖人类记忆**的纪律问题变成了**CI 自动 enforce** 的架构约束，V37.8.6 和 V37.8.7 类血案未来不会再发生。|
| V37.8.8 | 2026-04-15 | **双元规则正式立案：MR-11 (log→stderr) + MR-12 (LLM-output-key-based)** — ① **触发**：V37.8.6 (Dream) + V37.8.7 (ontology_sources) 同日双血案都是 MR-4 silent-failure 的演出，但两个血案的**防御机制**是独立的跨领域架构规则，不该靠个别 INV 级别 check 一事一议。用户要求"立即立案"把两条元规则从"案例教训"正式升级为 ontology 治理体系的硬规则。② **MR-11 shell-function-output-must-go-to-stderr-if-not-returned-value**：shell 函数内的诊断/日志/状态输出（log / debug / status / warn）必须写到 stderr（`>&2`），不得写 stdout。原因：调用方常用 `RESULT=$(func ...)` 命令替换捕获 stdout 作为"返回值"，如果 log 也写 stdout 会污染 RESULT → 被当作业务数据向下游传递 → cache → 下游 LLM 幻觉。血案教训来自 V37.8.6：kb_dream.sh `log() { echo ...; }` 写 stdout 让 llm_call 失败时的错误日志被 `signals=$(llm_call ...)` 捕获 → cache 污染 → LLM 编造"Hugging Face 危机"。**一个 `>&2` 阻断整条幻觉链**，证明 MR-11 不是纪律问题而是架构硬规则。③ **MR-12 llm-output-parser-must-be-key-based-not-positional**：解析 LLM 文本输出的代码不得用严格位置索引（`lines[i] / lines[i+1] / lines[i+2] / i += N / content.split()[0/1/2]`）。允许模式按推荐度：(1) JSON/YAML schema 校验 (2) key-based prefix dict (3) 语义 state-machine + pending_* + 特征字符块边界 (4) 正则直接提取。强制要求：每个字段缺失有 sentinel 默认值 + 输出层有兜底（如 `cn_title = paper['title']`） + 块相互独立无级联。血案教训来自 V37.8.7：ontology_sources `i += 3` 步进 + 无 fallback → 级联错位 → 用户 WhatsApp 看到 `*---*` 作 cn_title。④ **V37.8.7 MR-12 扫描结果**：扫完 15 个 LLM 输出解析器，除已修的 kb_dream/ontology_sources，其他都合规：7 个论文类 job（arxiv/semantic_scholar/hf_papers/dblp/pwc/acl_anthology/github_trending/rss_blogs）共享 state-machine + key-based + 三重 fallback 模式（`pending_title` / `'⭐' in line` 块边界 / `paper['title']` 兜底），ai_leaders_x 用 pure dict key-based，run_hn_fixed 用 JSON+regex fallback。**无紧急修复任务**。⑤ **违反模式正则**（为未来 CI 扫描预埋）：MR-11 扫 `^\s*(log\|debug\|status\|warn\|info)\(\)\s*\{\s*echo\s+.*[^&]2?`；MR-12 扫 `lines\[i\s*[+]\s*[0-9]+\]` / `^\s*i\s*\+=\s*[0-9]+\s*$` / `content\.split\([^)]*\)\[[0-9]+\]`。⑥ **audit_metadata.meta_rules 10→12**（MR-1~MR-12，新增 MR-11/MR-12 作为 MR-4 silent-failure 的上游预防层）。governance_checker 自动跟随 audit_metadata 动态计数，显示 `元规则: 10/12`（12 条声明 / 10 条被现存 INV 引用）— MR-11/MR-12 暂无 invariant 关联，等 V37.8.9 迭代新增 MRD 扫描器落地运行时检测。⑦ **governance v3.13 → v3.14**：38 不变式不变（MR 是元规则不是 invariant），168 checks 不变。⑧ **全量回归**：34 suites / **1018 tests** / 0 fail / 38/38 invariants / 168/179 checks / 安全 93/100。⑨ **遗留登记（V37.8.9 候选）**：(a) MRD-LOG-STDERR-001：扫所有 shell 函数定义确认 log/debug/status 用 >&2 (b) MRD-LLM-PARSER-POSITIONAL-001：扫所有 Python heredoc 识别 `lines[i+N] / i+=N` 反模式 (c) 这两个 MRD 一旦落地就把 MR-11/MR-12 从"声明层"升级为"CI 可检测层"，符合 MR-6 ≥2 层验证深度要求。|
| V37.8.7 | 2026-04-15 | **ontology_sources 推送格式错位血案闭环（MR-4 第 8 次演出，同日双血案）** — ① **触发**：用户 WhatsApp 收到 ontology_sources 推送出现严重错位 — 第 2 篇 cn_title 显示为 `*---*`（分隔符被当成中文标题）、中文标题串到 highlight 槽、第 3 篇 cn_title 显示为 `*价值：⭐⭐⭐⭐*`（前一篇的"价值"行错位上来）。同一天 V37.8.6 修了 Dream 血案，下午又遇 ontology_sources 血案 — 双血案同源 MR-4 silent-failure。② **三层根因**：(a) **触发器**：LLM 偶尔漏一行（如第 2 篇缺"要点"）— Qwen3 的指令遵循训练让它倾向"提供有用输出"而非严格遵循三行格式 (b) **放大器**：原解析器 `run_ontology_sources.sh:300-313` 用严格位置 `lines[i], lines[i+1], lines[i+2]` + `i += 3` 步进 — LLM 漏一行后所有后续条目的 (cn_title, highlight, stars) 全部右移一格**级联污染** (c) **掩护者**：emit 端 `*{cn_title}*` 直接照 parse 结果输出，没有任何字段语义校验（如"cn_title 不应是 ---"、"cn_title 不应以'价值：'开头"）— 错位输出毫无 degraded 标记直接推送给用户。③ **结构修复**：(1) 抽到独立模块 `jobs/ontology_sources/ontology_parser.py`（纯函数 `parse_llm_blocks(content) -> list[(cn,hl,stars)]` 可单测，避开 V37.5 heredoc-only 不可测血案）。 (2) **separator 切块**：用 `re.split(r'(?:^\|\n)\s*[-=*_]{3,}\s*(?:\n\|$)', cleaned)` 按 `---/===/***` 等分隔符切块，单块缺行不影响其他块。 (3) **块内 key-based 解析**：按前缀（"中文标题：" / "标题：" / "要点：" / 含 "⭐"）识别字段而非依赖位置；缺字段留空字符串。 (4) **shell `export ONTOLOGY_JOBS_DIR=$JOB_DIR`** + heredoc `from ontology_parser import parse_llm_blocks`（heredoc 走 `python3 - <<EOF` stdin 不带脚本目录到 sys.path，需主动注入）。 (5) auto_deploy FILE_MAP 补齐 `ontology_parser.py`。④ **INV-ONTOLOGY-001 新增**（meta_rule=MR-4, severity=high, verification_layer=[declaration, runtime]）7 checks：parse_llm_blocks 函数定义 + `_SEPARATOR_RE.split` 用法 + 块内 startswith('中文标题') key-based 识别 + shell `export ONTOLOGY_JOBS_DIR` + shell `from ontology_parser import parse_llm_blocks` + auto_deploy FILE_MAP 部署 + **runtime python_assert** 真跑用户 2026-04-15 实际看到的污染场景：构造"3 篇但第 2 篇缺要点"输入，断言 (a) 解析出 3 个 block (b) block 0 完整 (c) block 1 highlight 为空但其他正确 (d) block 2 完全不被 block 1 缺行影响 (e) 任何 cn_title 不含 `---` 或"价值"字样。⑤ **24 单测** `test_ontology_parser.py` 加入 full_regression：TestNormalCases(4) + **TestBloodLessonRegression(5)**（第 2 篇缺要点不级联 / 缺价值 / 缺标题 / 超额空行 / 连续分隔符）+ TestSeparatorVariants(5) + TestLLMOutputVariants(5) + TestShellScriptIntegration(4) + TestActualBloodLessonScenario(1) 端到端复现用户血案场景。⑥ **MR-4 第 8 次演出**：演出史 V37.3 → V37.4 → V37.4.3 → V37.5 → V37.6 → V37.7 → V37.8.6 → **V37.8.7 ontology_sources 位置解析**（同日双血案，证明 silent failure 在系统中持续以不同形态出现）。⑦ **元洞察**：V37.8.6 (Dream) + V37.8.7 (ontology_sources) 同日双血案揭示——**LLM 输出永远不能假设格式严格遵守**，所有 LLM 输出解析必须 (a) 容忍单条缺/多/重排 (b) 用语义键（如前缀/特征字符）而非位置定位 (c) 字段独立提取，不级联 (d) emit 前做最小语义校验（如"cn_title 不应是分隔符"）。⑧ **governance v3.12 → v3.13**：37→38 不变式（+INV-ONTOLOGY-001），175→182 检查。⑨ **全量回归**：34 suites / **1018 tests** / 0 fail / 38/38 invariants / 168/179 checks / 10/10 meta rules / 安全 93/100。⑩ **遗留登记**：(a) emit 端字段语义校验（"cn_title 不应是分隔符 / 不应以已知前缀开头"）作为 V37.8.8 候选 (b) 同模式审查其他 LLM 输出解析器（finance_news/dblp/hf_papers/semantic_scholar/freight_watcher 是否有类似位置解析）。|
| V37.8.6 | 2026-04-15 | **Dream 自引用幻觉血案闭环（MR-4 第 7 次演出 + 原则 #23 链式幻觉典型实证）** — ① **触发**：V37.8.5 Mac Mini E2E 同步后用户察觉 2026-04-15 03:00 Dream run 推送的"Hugging Face 平台危机"分析存在违和 — 信号一是"Papers with Code 沉默"但行动一却是"监控 Hugging Face"，主题断裂。日志里同时出现 4 次 `adapter.py:386 send_error(400, "Bad JSON")` 但 Dream 仍产出完整 6 章节推送。② **三层根因深挖**：(a) **触发器**：某个 source/note 抓取内容含 U+D800-U+DFFF 孤立 UTF-16 代理码点（smoke_test 已报 UnicodeEncodeError × 8）→ `llm_call` 内 `python3 -c` heredoc 的 `json.dump(body, f, ensure_ascii=False)` + 文件默认 utf-8 编码 → 孤立代理写入时 UnicodeEncodeError → body_file 截断为不完整 JSON → curl 上传破损 JSON 到 adapter :5001 → `adapter.py:383` json.loads 炸 JSONDecodeError → 返回 456 bytes HTML 错误页 "Bad JSON 400"。(b) **放大器（本次血案核心）**：`kb_dream.sh:109` 的 `log() { echo ...; }` 用 plain echo 写 stdout，而 Map 循环用 `signals=$(llm_call "$prompt" ... || true)` 命令替换捕获 stdout → log() 发出的 "LLM raw response: <HTML>Error code: 400 Bad JSON..." 错误日志全部被捕获进 `$signals` 变量 → `if [ -n "${signals// }" ]` 非空检查通过 → `echo "$signals" > "$cache_file"` 把错误日志写入 cache file。(c) **掩护者**：Reduce cache-only fast path (V37.4 Fix A) 读 `$MAP_DIR/2026-04-15_*.txt` 把错误日志作为"信号"注入 REDUCE_MATERIAL → LLM 看到 "Bad JSON 400" 字样后基于训练数据里"平台报错 + 用户在讨论分析"的分布，自动编造"某平台出了问题"的叙事 → 最高概率的 AI 平台 = Hugging Face → 输出虚假的"Hugging Face 平台危机"分析并经 WhatsApp+Discord 推送给用户 = 原则 #23 链式幻觉三跳累积（第 0 跳 adapter 错误 → 第 1 跳污染 cache → 第 2 跳 LLM 编造 → 第 3 跳推送用户）。③ **四层结构修复（defense-in-depth）**：(1) **log() 改写 stderr**：`log() { echo ... >&2; }` 阻断 stdout 污染 `$(...)` 命令替换的通道，四层中最根本的一层，一个 `>&2` 直接阻断整条幻觉链。(2) **heredoc 内嵌 `_sanitize(s)`**：U+D800-U+DFFF 孤立代理 → U+FFFD 替换字符，保持字符边界与 token 对齐，同时应用于 `prompt` 和 `system_msg`，防 json.dump 触发 UnicodeEncodeError。(3) **file open 双属性**：`open(..., encoding='utf-8', errors='replace')` 作为第二防线，万一 sanitize 漏网其他无效字节也能 replace 不炸裂。(4) **REDUCE/CHUNK1/2/3 system prompt 反污染守卫**：明示禁止把 HTTP 错误码/Python 异常/错误页 HTML/U+FFFD 连续片段/工具链名(curl/jq/grep) 当作外部信号，禁止基于这些字样推断 Hugging Face/GitHub/npm 等平台状态，禁止在行动建议中针对"平台错误"给建议。即使前三层全失效，LLM 也被明示约束。④ **INV-DREAM-003 新增**（meta_rule=MR-4, severity=critical, verification_layer=[declaration, runtime]，**首次登记即符合 MR-6 hard-enforcing ≥2 层要求**）16 checks：log() 源码模式 + V37.8.6 血案注释 + _sanitize 函数定义 + U+D800-U+DFFF 范围 + U+FFFD 替换 + 双字段应用 + errors='replace' + encoding='utf-8' + REDUCE_SYSTEM 反污染守卫 + Bad JSON 禁令 + Hugging Face/GitHub/npm 举例 + CHUNK1/2/3 各自反污染 + runtime python_assert 真跑 sanitize 五场景（孤立高位/低位代理/正常串/代理范围边界/sanitize 后 json+utf-8 编码不炸）。⑤ **19 单测** `test_dream_surrogate_sanitize.py` 加入 full_regression.sh 第一层：TestLogGoesToStderr(2) + TestSanitizeSurrogates(7) + TestShellSanitizeImplementation(5) + TestAntiPollutionSystemPrompt(3) + TestShellScriptIntegrity(1) + 含 negative-control "raw surrogate would crash json dump" 证明问题真实存在。⑥ **MR-4 第 7 次演出**（silent-failure-is-a-bug）：演出史 V37.3 governance summary 吞 error → V37.4 Dream Map budget → V37.4.3 PA 告警污染 → V37.5 kb_review 空 prompt → V37.6 KB dedup → V37.7 双跑审计 → **V37.8.6 Dream 自引用幻觉（迄今最隐蔽：错误不仅被掩盖，还被 LLM 加工成看似合理的业务分析再推送给用户）**。⑦ **原则 #23 典型实证**：本案例成为 CLAUDE.md 原则 #23 "LLM 链路中每一跳都会放大幻觉"的教科书级案例，未来引用可指向 `ontology/docs/cases/dream_self_referential_hallucination_case.md`（详细四维度因果链 + 三层根因 + LLM 合理化本能分析 + 四层防御逻辑 + 7 次 MR-4 演出对照表）。⑧ **潜伏态血案元洞察**：V37.1+ 四个条件（surrogate 累积 + log→stdout + `$(cmd)` 捕获 + cache 非空检查）一直存在，Dream 可能已经静默输出幻觉内容数天或数周而无人察觉，只有条件 5"LLM 编造被用户发现"首次触发才让血案浮现。⑨ **governance v3.11 → v3.12**：36→37 不变式（+INV-DREAM-003），159→175 检查。⑩ **全量回归**：33 suites / **994 tests** / 0 fail / 37/37 invariants / 161/172 checks / 10/10 meta rules / 安全 93/100。⑪ **遗留登记（下次迭代）**：(a) MR-11 候选 `shell-function-output-must-go-to-stderr-if-not-returned-value`（扫所有 shell 函数的 log/debug/status 输出）(b) 系统化 surrogate 清洗（爬虫/KB 写入源头先清洗，不只 LLM 边界）(c) LLM 输出 grounding allow-list（平台名/公司名必须在信号里出现才能提）(d) Dream 失败率可观测（Map 批次失败 > 阈值 → `[DEGRADED]` 标记或跳过推送）(e) 审计今日 pwc_daily / 其他 source 文件是否真的有 surrogate 字符。|
| V37.8.5 | 2026-04-15 | **僵尸检测边缘盲区闭合（V37.8.4 修复的修复，MR-10 第 2 次演出）** — ① **触发**：V37.8.4 收工 Mac Mini E2E 用户主动验证 finance_news 时，系统独立发现 SCMPNews 95/95 超窗口成功标记，但用户肉眼比对时发现同一 run 有两个边缘账号被漏标：**CNS1952 98/99 超窗口 + 1 过短**（严格 `old == total` 放过 99% 老化率）+ **SingTaoDaily 0-tweet stub**（`total > 0` 前置门槛直接排除空 HTML 骨架）。V37.8.4 作为"登记 unfinished"延后处理。② **MR-10 第 2 次演出**：V37.8.4 引入的检测器**本身**是 bug 源——第三问"最小修复"被浅化为"严格相等 + total>0 就够了"，但严格相等是所有 `old/total >= X` 的最窄特例，0-tweet stub 完全不在 `total > 0` 的论域。③ **结构修复**：(1) 把 heredoc 内嵌逻辑提炼为独立纯函数模块 `jobs/finance_news/finance_news_zombie.py`，签名 `classify_zombie(diag, count) -> (bool, tier)`，返回 `stub`/`stale`/`alive` 可区分 (2) Tier 1 "stub"：`no_data == 0 and total == 0` 闭合 SingTaoDaily (3) Tier 2+3 "stale"：`count == 0 and total > 0 and old * 10 >= total * 9`，整数比较避免浮点，阈值 9/10 编码为常量 `ZOMBIE_STALE_NUM/DEN`，CNS1952 98/99 触发 (4) `count == 0` 守卫防止"99% 老化但还产出 1 条新鲜"的低频活跃账号误报 (5) heredoc 改为 `from finance_news_zombie import classify_zombie`，禁止 inline fallback。④ **治理升级**：INV-X-001 `verification_layer` 从 `[declaration]` → `[declaration, runtime]`（主动遵循 MR-6 建议，即使 severity=high 不强制），新增 `python_assert` runtime check 真跑 5 个 tier 场景（Tier 1 stub / Tier 2 V37.8.4 原始 / Tier 3 CNS1952 / count 守卫 / no_data=1 false positive）。13 check → 20 check 净增 7（-1 旧严格谓词，+8 新检查含 import/call-with-count/env-export/模块存在/Tier 1/Tier 2+3/auto_deploy/runtime）。⑤ **24 单测** `test_finance_news_zombie.py` 加入 full_regression.sh 第一层：TestTier1Stub(3) + TestTier2Stale100(2) + TestTier3NearZombie(4) + TestCountGuard(3) + TestAliveAccounts(3) + TestConstants(2) + TestShellScriptIntegration(6) + TestAutoDeployMapping(1)。⑥ **MR-8 兑现**：`test_script_has_no_inline_zombie_fallback` 单测 grep shell 源码禁止 `def classify_zombie` 内嵌（防模块缺失时静默退回 V37.8.4 行为 = copy-paste-is-a-bug-class 反面）。⑦ **诊断可观测**：`⚠️ ZOMBIE嫌疑[stub]` / `⚠️ ZOMBIE嫌疑[stale]` 日志前缀 tier 可区分。⑧ **案例文档** `ontology/docs/cases/zombie_detection_edge_case_closure.md`（四维度因果链 + 三层根因 + 时间线 + 五条件组合分析 + MR-10/MR-6/MR-8/MR-4 四元规则兑现 + 本体喂养清单）。⑨ **auto_deploy FILE_MAP** 补齐 `jobs/finance_news/finance_news_zombie.py` 部署映射。⑩ **governance v3.10 → v3.11**：36 不变式不变（INV-X-001 升级非新增），152 → 159 检查。⑪ **全量回归**：32 suites / **975 tests** / 0 fail / 36/36 invariants / 145/156 checks（11 skip 运行时层）/ 10/10 meta rules / 安全 93/100。⑫ **VERSION** 文件 0.37.5 → 0.37.8.5（V37.5 后未同步的 rot 修复）。⑬ **元洞察**：血案二次演出证明"修复会埋坑"；登记 unfinished 是人类 TODO 不是 CI——原则 #28 理解再动手 + #29 收工零遗漏 两者缺一不可。|
| V37.8.4 | 2026-04-14 | **finance_news X 僵尸账号血案闭环 + INV-X-001 + 原则 #27 升级** — ① **触发**：V37.8.3 修改 3 个 X handle（CaixinGlobal→caixin, YicaiGlobal→yicaichina, STcom→straits_times）**上线第二天（V37.8.4 开工验证）发现三个改名后的 handle 全是僵尸账号**，Syndication API 返回最新推文分别是 2227/3364/420 天前。② **全量审计 22 账号** 7 个僵尸（~32% 污染率）：caixin 2227d/yicaichina 3364d/straits_times 420d/Reuters 253d/BrookingsInst 585d/WorldBank 2KB stub/ChannelNewsAsia 2955d。③ **Path C 最小修复 + 全量审计**：删 7 僵尸 → 剩 15 handle（IMFNews/business/WSJ/ReutersBiz/TheEconomist/XHNews/PDChina/CGTNOfficial/ChinaDaily/globaltimesnews/CNS1952/SCMPNews/NikkeiAsia/SingTaoDaily/asahi），依赖生产 cron 每天自然暴露剩余 14 个（429 限流绕过）。④ **静默失败检测**：`is_zombie_suspect = (diag["total"] > 0 and diag["old"] == diag["total"])` 写入 `cache/zombies_${DAY}.txt` + `⚠️ ZOMBIE嫌疑` 诊断前缀 + 3 天连续命中告警（macOS `date -v-Nd` / Linux `date -d 'N days ago'` 兼容 fallback）。⑤ **INV-X-001 新增**（meta_rule=MR-10, severity=high, verification_layer=[declaration]）13 checks：ZOMBIE_FILE 声明 + is_zombie_suspect 解析 + zombie_file append + ⚠️ 前缀 + `comm -12 - <\(sort -u "\$Y_FILE"\)` 3 天匹配 + 7 个 file_not_contains 守卫（禁止 Reuters/WorldBank/BrookingsInst/caixin/yicaichina/ChannelNewsAsia/straits_times 任一 handle 回归）+ CLAUDE.md "账号健康 ≠ HTTP 200" 声明。⑥ **CLAUDE.md 原则 #27 升级**：标题从"X/Twitter 是高质量实时数据的第一选择"变为"**X/Twitter 是高质量实时数据的第一选择（但账号健康 ≠ HTTP 200）**"，强制条款："账号健康必须通过最新推文时间戳验证（newest_tweet_dt > now - N 天），新增账号前必须先抽查最新推文时间，生产脚本必须对"抓到内容但 0 条过时间窗"的账号打 ZOMBIE 嫌疑标记，连续多天命中触发告警"。⑦ **MR-10 正向兑现**：V37.8.3 引入 MR-10 understand-before-fix 后**第二天就被违反**（第三问"最小修复"被浅层化为"改到字面上正确的 handle"而不是"改到可用信源"），V37.8.4 把违规事件本身固化为 case doc + INV-X-001 让下次改 X handle 必须先验证最新推文时间。⑧ **元洞察**：协议层绿灯 ≠ 内容层健康（本项目第 3 次：V37.4 Dream reduce cache bland 重复 → V37.5 kb_review 空 prompt → V37.8.4 X Syndication 僵尸快照）。⑨ **Mac Mini E2E 验证**：finance_news 手动运行 27 篇推送，系统自主发现 **SCMPNews** 为第 8 个僵尸嫌疑（95/95 条超 72h，ZOMBIE 嫌疑标记生效），zombies_2026-04-14.txt 写入 SCMPNews，等 3 天连续命中（2026-04-16 决策窗口）。⑩ **案例文档** `ontology/docs/cases/finance_news_syndication_zombie_case.md`（TL;DR + 完整因果链架构图 + 三层根因 + 时间线 + 五条件组合爆炸分析 + MR-10 正向兑现叙事）。⑪ **governance v3.9 → v3.10**：35→36 不变式，139→152 checks。⑫ **遗留登记**（3 项）：SCMPNews 3 天连续观察 / CNS1952 近僵尸检测阈值差距（98/99 被 strict equality 放过，需 ≥90% 阈值）/ SingTaoDaily 0 推文 stub 检测路径缺失（total=0 绕过 total>0 阈值）。⑬ **全量回归**：31 suites / 951 tests / 0 fail / 36/36 invariants / 138/149 checks / 10 meta rules / 安全 93/100。|
| V37.8.3 | 2026-04-13 | **连锁修复血案教训 + 方法论升级 + 运维修复** — ① **preflight 连锁修复闭环**：原始问题是 `~/auto_deploy.sh` HOME 副本未同步（只需 `cp`），被误诊为"缺少 FILE_MAP 配置"，触发 5 轮连锁修复（加 FILE_MAP 条目→双目标部署→SCRIPT_DIR HOME 检测→dict→list 解析器→job_smoke_test 同 fix），最终 preflight 77/0/4 + job_smoke_test 34/182/0/16 全部通过 ② **MR-10 understand-before-fix 新元规则**：修复前必答三问（之前存在吗/哪个改动引入的/最小修复是什么），连锁修复是最危险的系统破坏模式，governance v3.9→v3.10（10 元规则）③ **原则 #28 理解再动手**：禁止条件反射式修复 ④ **原则 #29 开工收工零遗漏**：清单逐项执行不允许"差不多就行" ⑤ **第 0 号宪法**：最强模型不降配不降速不妥协 ⑥ **finance_news X 账号修复**：CaixinGlobal→caixin, YicaiGlobal→yicaichina, STcom→straits_times ⑦ **Dream MIN_ACCEPTABLE_CHARS** 1500→3000 bytes + BEST_CHARS 未定义变量修复 ⑧ **kb_evening E2E 测试时区闪烁修复** ⑨ **案例文档** `ontology/docs/cases/preflight_cascading_fix_case.md` ⑩ **全量回归**：951 tests / 35/35 invariants / 125/125 checks / 10 meta rules / 安全 93/100 |
| V37.8.2 | 2026-04-12 | **finance_news 全球财经/政策新闻 job 上线 + 反幻觉修复 + 运维巩固** — ① **preflight 三修复**：(1) status.json 仓库 vs 运行时合法分叉豁免（仓库快照 vs cron 每小时刷新）(2) KB 索引阈值从 count-based（<=5 stale）改为 percentage-based（>=90% 覆盖率 = WARN，<90% = FAIL），33 cron 日产 15 新 notes + 21 modified sources 导致假 FAIL (3) MRD-NOTIFY-002 activity 层假阳性根治：`_discover_silent_channels()` 重写为 TOPIC_JOB_MAP 映射 + registry-driven job log mtime 检测，替代原 grep `--topic` 字符串（28/32 job bypass notify.sh 永不产生该字符串）② **kb_evening/kb_review LLM 反幻觉约束**：两个 prompt builder 加入 `⚠️ 严格约束（违反则整份输出作废）`——只使用明确出现的信息 / 每条标注来源标签 / 严禁虚构发布公告+开源事件+人物言论 / 无数据跳过不编造。触发：evening digest 编造"OpenClaw 开源发布 openclaw-model-bridge"（私有仓库从未公开发布）③ **finance_news 全新 job**：双通道 8 RSS 直连（Fed/NBER/FT/ECB/BIS/Yahoo/SCMP/36Kr）+ 21 X/Twitter Syndication API（7 国际：Reuters/IMFNews/WorldBank/BrookingsInst/Bloomberg/WSJ/ReutersBiz + 14 亚太：XHNews/PDChina/CGTNOfficial/ChinaDaily/globaltimesnews/CNS1952/CaixinGlobal/YicaiGlobal/SCMPNews/NikkeiAsia/SingTaoDaily/ChannelNewsAsia/STcom/asahi）。72h 时间窗口覆盖周末 + 按天自动重置 seen 缓存 + LLM 结构化分析（价值⭐评级/国内外对比/投资建议/风险提示）+ KB 归档 + Discord+WhatsApp 推送。诊断模式：0 条账号打印过滤原因（纯RT/过短/已见/超窗口/无数据）。Mac Mini 验证：32 篇（16 国际 + 16 国内），0 源失败 ④ **CLAUDE.md 原则 #27**：X/Twitter 是高质量实时数据的第一选择——Syndication API 无需认证/零成本/100% 成功率 vs RSSHub 公共实例 100% 失败率 ⑤ **auto_deploy FILE_MAP** 补齐 finance_news 部署映射 ⑥ **全量回归**：930 tests / 35/35 invariants / 125/125 checks / 9/9 meta rules / 安全评分 93/100 |
| V37.8 | 2026-04-11 | **MR-6 深度补齐 + MRD-NOTIFY-002 语义修复（治理自观察第二轮：治理深度从"建议"升级为"硬强制"）** — ① **触发**：V37.7 双跑审计后 `governance_checker --full` 的 Phase 0 MRD 段持续报 4 个 warning，其中 `MRD-LAYER-001` 发现 **7 个 critical 不变式只有单层验证**（MR-6 "critical-invariants-need-depth" 被静默违反），`MRD-NOTIFY-002` 报 6 个 Discord 频道有 4 个"7 天无活动"但 Mac Mini 实际每天都在推送 — 即**检测器假阳性 + 治理元规则未被强制**双重漏洞。② **MR-6 深度补齐 7 个 critical 单层不变式**：(1) **INV-TOOL-001 tool-count-limit**：原 python_assert 实际跑 filter_tools() 是运行时，但 verification_layer 错标 `[declaration]` — 新增 `file_contains` 声明层 check (proxy_filters.py 静态引用 MAX_TOOLS) + 修正标签为 `[declaration, runtime]` (2) **INV-TOOL-002 max-tools-config-imported**：已有 file_contains + python_assert 双层但 verification_layer 错标 — 纯元数据修复 (3) **INV-DEPLOY-002 auto-deploy-never-modifies-crontab**：原单层静态文本扫描 — 新增 `shlex.shlex(posix=True)` AST-tokenize 运行时 check 遍历 auto_deploy.sh 拆出 token 序列检测 `crontab -e/-i/-r/-` 或 `crontab_safe.sh add/remove/set/replace` 危险组合 (4) **INV-CRON-003 bash-lc-enforcement**：原单层运行时 (--full 模式 crontab -l 解析) 在 dev 环境完全 skip — 新增 `file_contains` 声明层守卫 V37.2+ 的 `_cron_cmd_invokes` 精确匹配器 + `cmd.endswith(entry)` 词边界模式，防止未来重构回退到子串匹配 (5) **INV-ENV-002 cron-env-smoke-test**：原单层运行时 (bash -lc 读 OPENCLAW_PHONE) dev 环境 skip — 新增声明层 2 个 check：preflight_check.sh `REQUIRED_VARS=(...)` 数组必含 `OPENCLAW_PHONE` (regex 提取数组字面量) + YAML 源码必须保留占位号 `+85200000000` 字面量作为 runtime 比较的反例守卫 (6) **INV-DREAM-002 reduce-path-must-not-re-run-map-loops**：原三 file_contains 声明层可证 `SKIP_MAP_LOOPS=true` 字面量和门控存在，但无法证明**门控在循环之前生效** — 新增运行时顺序锁 python_assert 读 kb_dream.sh 行号断言 `SKIP_MAP_LOOPS=true` 赋值 `assign_line < gate_1a_line` 且 `< gate_1b_line`，防止重构把赋值移到循环之后的回归 (7) **INV-CACHE-002 notes-cache-key-stable-under-mtime-drift**：原四 file_contains 只能证明字面量存在，无法证明"同内容不同 mtime 真的产生相同 hash" — 新增运行时 python_assert 用 `tempfile.TemporaryDirectory` 构造 3 个文件 (p1/p2 同内容不同 mtime, p3 不同内容)，`os.utime` 人为分离时间戳模拟 mm_index/kb_embed 触碰，inline `hashlib.md5(content).hexdigest()[:12]` 断言 `h1==h2` 且 `h1!=h3`。**注意**：`hashlib.md5` 调用必须 inline，**不能** 包成 `def _content_hash(s): ...` 嵌套函数——exec() 作用域陷阱下嵌套 def 看不到 exec locals 的 import（第一次运行因此 NameError）。③ **INV-LAYER-001 新增 (自我强制 meta-invariant)**：`critical-invariants-have-two-layers`, meta_rule=MR-6, severity=critical, verification_layer=[declaration, runtime]，把 MRD-LAYER-001 的 `severity_when_missing: warn` 升级为 fail-stopping hard invariant —— python_assert 遍历自身 YAML，任何 `severity=="critical"` 但 `len(verification_layer) < 2` 的不变式都让 governance_checker 直接 `exit 1`，未来任何新增 critical 不变式只要少于 2 层就被 CI 拦截。④ **MRD-NOTIFY-002 检测器重写 (根治假阳性)**：原 `_discover_silent_channels()` 只 grep `~/*.log` 里的 `--topic X` 字符串，但 32 个 job 中**只有 4 个用 notify.sh** (arxiv/dblp/semantic_scholar/ontology_sources)，其余 28 个直接 `openclaw message send --channel-id "$DISCORD_CH_X"` bypass notify.sh 永不产生 `--topic` 字样 → ontology/tech/freight 等频道误报 silent。重写为两层检测：**source layer** (dev + full 都跑，扫 `jobs/*/run_*.sh` + `*.sh`，每个 topic 的 caller 可以是 `--topic T` 或 `DISCORD_CH_<T_upper>` 任一形式) + **activity layer** (仅 --full 且 `~/.kb` 存在，用 `notify_queue/*.json` 和 `jobs/*/cache/*.log` 的 **mtime** 而非字符串 grep；只对"源码有 caller 但运行时静默"的 topic 报警)。⑤ **governance_ontology.yaml v3.8 → v3.9**：34→35 不变式 (+INV-LAYER-001)，126→139 检查 (+10: TOOL-001/DEPLOY-002/DREAM-002/CACHE-002 各 +1, CRON-003/ENV-002 各 +2, LAYER-001 +2)，9 meta rules 不变。⑥ **全量回归**：31 suites / **927 tests** / 0 fail / 35/35 invariants / 125/125 checks in dev (136 total, 11 skip runtime-only) / **9/9 meta rules** (从 V37.7 的 8/9 提升) / MRD-LAYER-001 **✅ "所有 15 个 critical 不变式都有 ≥2 层验证深度"** / MRD-NOTIFY-002 **✅ "所有 6 个频道均有 source-layer caller"** / 安全评分 93/100。⑦ **血案元规则兑现**：V37.8 是 MR-7 (governance-execution-is-self-observable) 的第二轮兑现 — V37.3 MR-7 首次引入 + INV-GOV-001 修复 summary 吞 error；V37.8 把 MR-6 从"警告层"提升为"强制层"，同时修复检测器本身的假阳性 (MRD-NOTIFY-002)。治理系统真正做到"不仅治理别人，也治理自己"。 |
| V37.7 | 2026-04-11 | **双跑审计闭环 — 三合一修复 + MR-8/MR-9 元规则形式化（MR-4 第 4/5 次演出 + 元规则预防层首次加入）** — ① **Bucket 1 (P0) `run_discussions.sh` V37.6 第 16 处漏网** (`jobs/openclaw_official/run_discussions.sh:104`)：V37.6 migration 14 个 cron 到 `kb_append_source.sh` helper 时漏掉 discussions（4×/天 08:15/12:15/16:15/20:15 的直接 `>> "$KB_SRC"` append），MR-4 silent-failure 第 5 次演出。修复删除 loop 内直写 + post-loop 走 helper + **`SLOT_TAG="$(date +%H:%M)"`** 区分同日多 run 避免 H2 section dedup 误吞第二次。② **Bucket 2A `audit_log.verify_chain` JSON parse error prev_hash 级联 bug**：V37.6 verify_chain 在 JSON parse 失败时**未更新 `prev_hash`** → 单行损坏后所有下游有效行都被误报为 prev-mismatch。修复改 `prev_hash: "str \| None"`，parse error 时置 None + 下行跳过 prev 指针检查（仍独立验证自身 hash），首个有效 hash 后重新续链。③ **Bucket 2B `kb_dedup.find_duplicate_notes` dangling index refs 假重复**：V37.6 find_duplicate_notes 把"index 有 entry 但磁盘无文件"的孤儿条目当作正常 entry → 按 summary 分组 → 和健在兄弟被误判为重复 → `--apply` 时删掉**活着**的兄弟文件（因为 dangling 排第一个被 kept，健在文件被 removed），MR-4 第 4 次演出。修复 (a) 新增 `find_dangling_index_entries(index)` 独立 reporter (b) `find_duplicate_notes` 内部 V37.7 pre-filter 用 `os.path.exists()` 剔除 dangling refs 后再分组 (c) `main()` 在 `--apply` 时把 dangling 从 `index.entries` 直接移除（**不**动磁盘文件），dry-run 时打印 warning。④ **Bucket 2C `kb_evening_collect` label semantics 修复**：V37.6 WA header `今日笔记 298 篇` 把 `note_count`（`rc.read_index_stats` 返回的**历史累计**笔记总数）错标为"今日"——evening 报告整个定位就是"今日"但数字是 298 天累计。修复新增 `import glob` + `count_today_notes(kb_dir, today)` 扫 `notes/` 前缀匹配 `YYYYMMDD` 的 .md 文件 + `build_evening_prompt/markdown/wa_message` 全部加 `today_note_count` 参数（分列"笔记总数 298"和"今日新增 5"），evening.sh wrapper 同步提取 `TODAY_NOTE_COUNT` 并打印到日志。⑤ **Bucket 3A MR-8 `copy-paste-is-a-bug-class` 从 candidate 正式形式化**：V37.6 changelog 提过 MR-8 candidate 但从未进入 `meta_rules:` 段——V37.7 正式加入，description 阐述"跨模块复制相似逻辑 = 引入 bug 类"+ "正确做法是提取公共原语为 import 模块 + 差异注入扩展"+ "正向兑现 = V37.6 `kb_evening_collect` 通过 `import kb_review_collect as rc` 复用 5 个原语 + 只覆写 evening-specific 行为"，INV-EVENING-001 从 MR-4 重归属到 MR-8。⑥ **Bucket 3B MR-9 `state-writes-go-through-helper-not-raw-redirect` 新增元规则**：把"shell `>>` 直写持久化 state 文件必须走 helper"从"14 个 caller 都要记得"的纪律问题升级为"ontology 架构硬规则"，包含 enforcement (INV-SRC-001 声明 scan + 运行时 15 job 校验) + derivative_invariants [INV-SRC-001, INV-DEDUP-001, INV-DEDUP-002] + blood_lessons（438 行 sources 污染 + run_discussions 第 16 处漏网），MR-9 是 MR-4 silent-failure 的**上游预防层**——让 CI/governance 自动拦截而不是靠程序员记忆。INV-SRC-001 从 MR-4 重归属到 MR-9。⑦ **INV-GOV-001 check 2 去 global mutation 重构**：V37.6 把 `governance_checker.JSON_MODE` 全局变量 save/mutate/restore 来注入 fake 状态测 summary——MR-8 的反面例子"靠 global state 做 dependency injection"。修复 `print_results(results, json_mode=None)` 加显式参数（None 时 fallback 到 module-level global 保持向后兼容），INV-GOV-001 check 2 改为 `print_results(fake_results, json_mode=False)` 直接注入。⑧ **INV-SRC-001 catch-all scan 加固 (Bucket 1)**：新增 2 个运行时 check — `V37.7 全量扫描 jobs/**/*.sh 不得有任何 >> "$KB_SRC" 直写`（catches 任何位置，不限 loop-tail）+ `V37.7 catch-all 任何 >> .*sources/.*\\.md 路径必须通过 helper`（路径级 regex，future-proof 对抗新增 sources 变量名）+ job_sites 列表扩到 15 job（加入 run_discussions.sh 的 1 处写入点）。⑨ **INV-DEDUP-002 新增不变式** `kb-dedup-pre-filters-dangling-index-entries`（meta_rule=MR-4, verification_layer=[declaration, runtime], severity=high）4 个 check：声明层 grep `def find_dangling_index_entries` + `V37.7` 注释标记；运行时层 2 个 python_assert 用 tempfile 隔离 KB_BASE/NOTES_DIR/SOURCES_DIR/INDEX_FILE/REPORT_JSON — (a) 构造 2 entries 同 summary 一死一活，assert exact_dupes=0 + dangling=1 (b) 端到端 main `--apply` 路径测 monkey-patched `sys.argv`，assert dangling 从 index 移除且健在文件仍存在。⑩ **governance_checker.py meta_rules counter 动态化**：原硬编码 `元规则: {len(mr_used)}/6`，V37.7 改为 `_load().get("audit_metadata",{}).get("meta_rules", len(mr_used))`，跟随 audit_metadata 自动刷新。⑪ **governance_ontology.yaml v3.7 → v3.8**：33→34 不变式，119→126 checks，7→9 meta rules，v3.8 changelog 记录 8 项闭环。⑫ **新增 12 单测**：test_kb_dedup.py `TestDanglingIndexRefs` 4 测试 (find_dangling / no-dangling when all exist / dangling not reported as duplicate / apply cleans dangling from index) + test_audit_log.py `test_json_parse_error_does_not_cascade` 1 测试（parse error 1 行，line 3 不被 prev-mismatch 误报）+ test_kb_evening.py 更新 `TestBuildEveningPrompt/Markdown/WaMessage` 3 类测试到新签名 + 新增 `TestCountTodayNotes` 3 测试 + `test_markdown_distinguishes_total_vs_today_counts` + `test_header_distinguishes_total_vs_today`。⑬ **`full_regression.sh` 接入 test_kb_evening.py**（V37.6 新增但从未登记）。⑭ **全量回归**：31 suites / **927 tests** / 0 fail / 34/34 invariants / 115/126 checks / 8/9 meta rules / 安全评分 93/100 |
| V37.6 | 2026-04-11 | **数据层 P0 三合一修复 + P1 kb_evening 架构对齐（MR-4 第三次演出 + MR-8 正向兑现）** — ① **P0-1 Content-blocks repr bug**：`proxy_filters.extract_user_text()` 把 OpenAI content blocks 列表直接 `str()` 成 `[{'type': 'text', 'text': '...'}]` 字面量被当"用户输入"写进 KB，发现于 kb_dedup dry-run 报告。引入 `flatten_content(content)` 只提取 text 字段，tool_proxy.py 对话捕获路径同步调用。② **P0-2 sources H2 repeat-append**：14 个 RSS/cron 抓取 job 各自独立 `>> sources/*.md` 把同一天数据追加多次，滚动窗口同条目在 `## $DAY` 下重复出现 N 次。新增 `kb_append_source.sh` helper（`$SLOT_TAG` 支持多 run/day 如 ontology_sources 10:00/20:00），14 个 job 全部迁移。③ **P0-3 kb_dedup.py 二次演出**：(a) file-level `seen` 集合把跨 H2 section 的合法日期复现误判为 duplicate 并在 `--apply` 时删除历史 (b) `find_duplicate_notes` 只扫 `index.json`，漏掉未索引的 `notes/` 孤儿。修复：`seen` 在每个 `## ` H2 边界重置；find_duplicate_notes 改为扫 NOTES_DIR 目录。④ **P1 kb_evening 架构对齐**：原 kb_evening.sh 用"文件名前 80 字"作假摘要 + 硬编码 7 源枚举 + 零 LLM 智能 + 无 fail-fast。新建 `kb_evening_collect.py` via `import kb_review_collect as rc` **复用**所有采集原语（load_sources_from_registry/extract_recent_sections H2 drill-down/collect_notes/collect_sources/call_llm），只覆写三件事：①DAYS 默认 1（今日窗口）②build_evening_prompt 改为"今日要闻/一条行动/明日关注/健康度"行动导向结构 ③build_evening_wa_message 🌙 emoji。kb_evening.sh 重写为 thin wrapper 遵循 V37.5 kb_review.sh 同款模式（fail-fast + [SYSTEM_ALERT] + env var heredoc 无 stdin 冲突 + 保留 kb_dedup 健康附注拼接 + 日志轮转）。⑤ **auto_deploy FILE_MAP 补齐 V37.5 部署缺口**：kb_review_collect.py 在 V37.5 发布时**从未被加入 FILE_MAP**（能跑纯靠 auto_deploy 的 git pull + kb_review.sh 的 $HOME/openclaw-model-bridge/ fallback 路径），V37.6 一并补齐 `kb_dedup.py` / `kb_review_collect.py` / `kb_evening_collect.py` 三个 Python 模块。⑥ **INV-EVENING-001** `kb-evening-fails-loud-not-silent`（meta_rule=MR-4, severity=high, verification_layer=[declaration, runtime]）17 个 check：reuse 契约（import kb_review_collect + 禁止 load_sources_from_registry/extract_recent_sections/call_llm 重定义）+ thin wrapper 契约（V37.6 标记/[SYSTEM_ALERT]/不含 review_${DATE}.md 粒度错配）+ FILE_MAP 部署契约 + 反模式 python_assert 行扫 `\| python3 -`（regex 精确区分 `python3 -c 'inline'` 安全形式）+ fail-fast 顺序锁（llm_failed 分支 500 字符内必须 exit 1）+ 运行时 DAYS=1 过滤验证（直接调 `rc.collect_notes` 避免 V37.2+ exec() 闭包陷阱）+ mock_fail 不伪造产物契约。⑦ **新增 31 单测** `test_kb_evening.py`（8 类）：TestReusesKbReviewHelpers(3) + TestOneDayWindow(2) + TestBuildEveningPrompt(4) + TestBuildEveningMarkdown(4) + TestBuildEveningWaMessage(3) + TestRunOrchestrator(4) + TestKbEveningShellGuards(10) + TestKbEveningShellRuntime(1，真 subprocess + stub collector E2E）。⑧ **test_kb_dedup.py 陈旧测试同步**：原测试把跨 H2 section 的同一行视为 duplicate，V37.6 H2-scoped 后这是合法的滚动窗口复现，更新 2 个 test case（test_duplicate_lines/test_apply_source_dedup）改为 section 内重复。⑨ **governance v3.5.1 → v3.7**：32→33 不变式，102→119 checks（新增 INV-KB-001/INV-SRC-001/INV-DEDUP-001/INV-EVENING-001 四个不变式 17 checks）。⑩ **案例文档** `ontology/docs/cases/kb_content_and_sources_dedup_case.md` 记录 MR-4 第三次演出（P0）+ MR-8 候选"copy-paste-is-a-bug-class"正向兑现（P1 via import reuse）。⑪ **全量回归**：30 suites / **886 tests** / 0 fail / 33/33 invariants / 108/119 checks / 安全评分 93/100 |
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

### 🔴🔴🔴 第 0 号宪法：最强模型，不降配不降速不妥协

> **每次使用 Claude Code 必须使用家族中最强的 Opus 级模型。当前合法模型（按发布时间新旧）：Claude Opus 4.7（`claude-opus-4-7`，2026 发布，当前最强）> Claude Opus 4.6（`claude-opus-4-6`，向下兼容）。**
>
> 禁止为了"快一点"切换到较弱模型家族（Sonnet / Haiku），禁止开启 fast mode 牺牲推理深度，禁止因为 token 用量或响应速度而降级。
>
> 这个项目的核心竞争力是**思考深度和工程质量**，不是速度。用弱模型省下的 10 分钟，会在后续的 bug 修复、连锁故障、返工中付出 10 倍代价。
>
> **1111 个测试、53 个治理不变式、15 条元规则——这个系统的复杂度要求最强的推理能力来驾驭，没有妥协空间。**
>
> **"更强的新版本不是降级"原则**：Anthropic 发布更强的 Opus 版本（4.6 → 4.7 → 4.8 ...）时，自动纳入合法列表。**降级的判定不是"模型 ID 变了"，而是"推理能力变弱了"**。把 Opus 4.6 换成 Opus 4.7 是升级，不是违反第 0 号宪法；把 Opus 换成 Sonnet/Haiku 才是违反。
>
> **执行机制（2026-04-14 强制 + 2026-04-20 升级）**：每次新 session 开工的**第一条回复**必须包含一行模型声明，格式为：
> `🤖 本次 session 模型：Claude Opus <X.Y> (claude-opus-<x-y>) | 最强级别，无降级 | fast mode 状态见 /fast 命令`
> 其中 `<X.Y>` 是当前实际运行的 Opus 版本号（4.6 / 4.7 / 未来更新）。声明放在开工报告之前，独占一行，前面不加任何寒暄。如果运行时检测到模型**不是 Opus 家族**（例如被错配到 Sonnet 4.6 / Haiku 4.5），**立即停止工作并告知用户**。见原则 #30。

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

### 🔴 每次必查（30条，优先级最高）

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
| 27 | **🆕 X/Twitter 是高质量实时数据的第一选择（但账号健康 ≠ HTTP 200）** | 当 RSS 源失效（官方取消、CDN 封锁、RSSHub 公共实例 403）时，**X/Twitter Syndication API 是最可靠的替代通道**——无需认证、零成本、覆盖全球权威机构官方账号。Twitter Syndication API (`syndication.twitter.com/srv/timeline-profile/screen-name/{handle}`) 返回 HTML 内含 `__NEXT_DATA__` JSON，可解析出完整推文内容+时间+ID。新增数据抓取任务时，优先评估目标机构的 X 账号是否活跃，再考虑 RSS/API。实践证明：14 个财经 X 账号 100% 成功率 vs 10 个 RSSHub 路由 100% 失败率。**🔴 V37.8.4 血案补充：X 账号健康 ≠ HTTP 200**——Syndication API 对已停更账号返回 stale 快照（HTTP 200 + 可解析 HTML + 推文数据，但最新推文可能是几百天甚至几年前），embed-disabled 账号则返回 2KB 空 stub。**账号健康必须通过最新推文时间戳验证**（`newest_tweet_dt > now - N 天`），不能依赖 HTTP 状态码或 HTML 大小。新增账号前必须先抽查最新推文时间；生产脚本必须对"抓到内容但 0 条过时间窗"的账号打 ZOMBIE 嫌疑标记，连续多天命中触发告警。（2026-04-13 教训：finance_news 12/20 RSS 源失败；2026-04-14 教训：V37.8.3 修复把 3 个正常 handle 改名成僵尸账号——caixin/yicaichina/straits_times 分别停更 2227/3364/420 天，Reuters 253 天，BrookingsInst 585 天，WorldBank 返回 stub。这是 MR-10 violation：看到失败先猜原因改代码而不是先验证账号真实状态） |
| 28 | **🔴 理解再动手：修复前必答三问** | 看到报错/失败时，**禁止直接开始"修"**，必须先回答三个问题：**(1) 这个问题之前存在吗？** 如果之前不存在，那是我的改动引入的，不是系统本身的 bug。**(2) 是我的哪个改动引入的？** 精确定位因果链，不猜测。**(3) 最小修复方案是什么？** 不引入新概念、新模式、新复杂度。三个问题答不上来 = 还没理解问题，禁止动手。**每次"修复"都可能制造下一个 bug——连锁修复是最危险的系统破坏模式。**（2026-04-13教训：preflight 一个部署同步问题被误诊为"缺少配置"，5 轮连锁修复、4 层新复杂度、用户 Mac Mini 手动测试 5 次，才修好一个原本只需 `cp` 一下的问题） |
| 29 | **🔴 开工/收工清单逐项执行，零遗漏** | 开工和收工的检查清单**每一项都必须逐一执行并确认**，不允许"差不多就行"、不允许合并跳过、不允许因为 session 太长而"赶紧结束"。系统已达 951 测试 / 35 不变式 / 34 cron job 的复杂度，任何一项遗漏都可能造成连锁故障。**开工时**：逐项执行原则 #1~#3（读 config → 读 status.json → 检查 OpenClaw 版本），不跳过任何一步。**收工时**：原则 #9 的 A~I 九项清单逐一执行并打勾，每完成一项立即标记，禁止批量处理。如果某项因客观原因无法执行，必须**显式说明原因并登记到 session_context.unfinished**，而不是静默跳过。（2026-04-13教训：开工/收工流程逐渐敷衍，原则执行不彻底导致遗留问题积累；2026-04-08教训：长 session 后"赶紧结束"心态跳过 6 项检查） |
| 30 | **🔴🔴🔴 开工必须主动声明模型（第 0 号宪法执行机制）** | 每次新 session 开工的**第一条回复必须以模型声明开头**，独占一行，格式为：`🤖 本次 session 模型：Claude Opus <X.Y> (claude-opus-<x-y>) \| 最强级别，无降级 \| fast mode 状态见 /fast 命令`。`<X.Y>` 是当前实际运行的 Opus 版本号（4.6 / 4.7 / 未来更新），读取 `$0` 或环境注入信息确定。**用户无需询问也必须声明**，这是第 0 号宪法的自动执行机制，不是可选项。**合法模型家族 = Opus**（4.6/4.7/更新版本均合规）；如果运行时检测到模型**不是 Opus 家族**（即被错配到 Sonnet / Haiku 或其他），**立即停止工作，把错配告知用户，等待处理后再继续**。升级到更新的 Opus 版本（4.6 → 4.7 → …）自动合规无需告警——那是升级不是降级。禁止"先干活再说"的侥幸思维——弱模型写的代码会在后续造成连锁故障。声明位置：在任何寒暄、分析、工具调用之前。（2026-04-14教训：用户必须手动追问"是否最强模型"才能确认，声明应主动前置而非被动回答；2026-04-20升级：Opus 4.7 发布后原"硬编码 4.6"规则误把升级当降级，改为"Opus 家族"通配） |
| 31 | **🆕 跨消费者豁免必须全量同步** | 同一配置数据（如 FILE_MAP）被多个脚本独立消费时（preflight / auto_deploy / job_smoke_test），**任何豁免规则必须在所有消费者中同步落实**，不得只改一个。发现新豁免需求时，必须**系统性扫描所有消费者**并打包修复，禁止单点修复。（2026-04-15教训 V37.8.11：V37.8.1 给 preflight 加了 status.json 豁免但 auto_deploy 漏改，导致每小时 drift 告警噪声长期潜伏；2026-04-16 又因 FILE_MAP 自复制行触发 cp 非零 + set -e 杀脚本，是同一原理的第二次演出。**正确做法**：每次改 FILE_MAP 豁免前 `grep -l "FILE_MAP\|for mapping" ` 找出所有消费者 → 打包一起改 → 加单测守卫跨文件一致性。）|
| 32 | **🆕 每周用户视角观察制度（30min）** | 每周固定时段（建议周一早）做一次**纯用户视角**的系统观察，**不写代码不修 bug**，专门看四个维度：① **告警噪声**（WhatsApp + Discord #告警 频道里告警频率/重复度/可读性）② **推送延迟**（cron job 推送到达手机的延时分布）③ **信息密度**（推送内容是否真有用 / 有没有水货）④ **用户感知**（自己使用时 PA 是否答非所问 / 慢 / 错乱）。每发现一个问题立刻登记到 status.json unfinished 而不是当场修。这是原则 #13 (定期像用户用) 和 #15 (测试全量) 的运营层兑现 — 单测/治理只能验证组件正确，**用户感知质量只能用户视角观察**。（2026-04-15 V37.8.11 教训：drift 噪声告警长期存在但开发环境永远绿灯，用户半天 WhatsApp 观察就发现；2026-04-16 V37.8.13 教训：Gateway 宕 9h 静默，原因之一就是没人主动巡视双通道告警频道。**执行机制**：周一早开工时第一件事执行此观察，30min 时间盒，发现项写入 status.json `--add unfinished` + `recent_changes` 标记为 weekly_observation 类目，本周内择机修复。） |

| 33 | **🆕 信源可达性必须在部署环境验证，SPA 必查 API** | 判定一个网站"不可抓取"前，必须完成三步验证：**(1) Mac Mini curl 测试**（不是 dev 环境）— 带 User-Agent 请求首页，dev 环境的 403 可能只是 IP/地域限制 **(2) 读 HTML 源码找 API** — SPA 网站的后端 API 必然在 JS 里（如 chaspark `/chasiwu/v1/`），读 `<script>` 标签找 `fetch/XMLHttpRequest/makeRequest` 调用 **(3) 只有三步都失败，才考虑复杂方案**（Puppeteer/间接渠道/放弃）。**禁止在 dev 环境得到 403 就下"全站封锁"的结论。X 账号是僵尸 ≠ 该机构停止发布 — 检查其官网 RSS/API。**（2026-04-17 教训：chaspark.com 在 dev 环境 403，被误判为"全站封锁+SPA 不可抓取"，提议 Puppeteer 重方案。实际 Mac Mini 首页 200，HTML 源码里 API 明文写着，5 分钟就抓到 39 篇文章。同日审计发现 Brookings Atom + WorldBank JSON API 也可直达，此前从未尝试。） |

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
> 迁移路径：Phase 2 Shadow（完成）→ Phase 3 渐进替换（✅ P0 已切换）→ Phase 4 完全推理 → Phase 5 `pip install ontology-engine`（终极目标）

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
| **P0** | **ONTOLOGY_MODE=on 切换**：引擎数据替代硬编码 | ✅ V37.8.14 完成 | 等价验证通过，249 测试，已切换为默认 |
| **P0** | **filter_tools() 改用引擎**：内部调用 `ontology.query_tools()` 替代 `ALLOWED_TOOLS` 枚举 | 待启动 | 依赖 ONTOLOGY_MODE=on |
| **P0** | **fix_tool_args() 改用引擎**：参数修复调用 `ontology.resolve_alias()` 替代硬编码映射 | 待启动 | 依赖 ONTOLOGY_MODE=on |
| **P1** | **夜间阻止语义化**：从手动维护阻止列表改为 `infer("side_effects==true")` 自动覆盖 | 待启动 | Phase 3 标志性交付 |
| **P1** | **新工具只加 YAML**：推广 V37 Provider Plugin 模式到 Tool，新增工具零 Python 改动 | 待启动 | 需要 Tool Plugin YAML schema |
| **P1** | **元规则扩展**：MR-7 新策略必须有 shadow 观察期 / MR-8 概念变更触发影响分析 | 待启动 | 治理体系自我演进 |
| **P1** | 用本体论视角重新审视 OpenClaw（深度版，含工具语义本体实验） | 进行中 | `cases/openclaw_as_ontology.md` |

#### Phase 4: 完全推理（中期，P1-P2）

| 优先级 | 任务 | 状态 | 说明 |
|--------|------|------|------|
| **P1** | **domain_ontology.yaml**：六域概念模型（Actor/Tool/Resource/Task/Provider/Memory），概念间关系推理 | ✅ V37.9.9 骨架 + V37.9.12 wiring API (`find_by_domain`) | Layer 1 终态：从工具列表到领域模型 |
| **P1** | **policy_ontology.yaml**：策略声明式定义（静态+时序+路由三类策略统一） | ✅ V37.9.9 骨架 + V37.9.12 `evaluate_policy()` + V37.9.13 P2 context evaluator + 2 条 policy 切换 | Layer 2 终态部分兑现：static + 6 条 contextual/temporal 已 wire，剩 4 条待注册 |
| **P1** | **三阶段门控**：Pre-check（前置条件）→ Runtime Gate → Post-verify（后置验证）接入请求管线 | 待启动 | Layer 3 终态：Neuro-Symbolic 四耦合点 |
| **P2** | **contextual/temporal policy 的 context evaluator**：实现 hour_of_day / request-context matcher 让 `evaluate_policy().applicable` 对非 static policy 也返回真值 | ✅ V37.9.13 完成 — 6 个 matcher（quiet_hours / task_match / has_alert / has_image / need_fallback / data_clean_keywords）+ `_CONTEXT_EVALUATORS` dispatch + 三档 reason（needs_context / no_evaluator / evaluator_error） | 未注册 policy 走"可扩展路径"不崩溃 |
| **P2** | **第二条 policy 切换**：选 `max-tool-calls-per-task` 或 `max-request-body-size` 做第二次 wiring，复用 V37.9.12 `_resolve_*` 模式 | ✅ V37.9.13 完成 — `_resolve_max_tool_calls_per_task_limit()` 镜像 V37.9.12 5 档 safe-fallback + MR-8 copy-paste 防御源码守卫 | 验证 wiring 模式可扩展性 ✓ |
| **P2** | **审计带规则链**：每条审计记录包含 policy_evaluated + rule_chain + rationale | 待启动 | Layer 4 终态：从"做了什么"到"基于什么规则" |
| **P2** | **策略冲突检测**：策略间矛盾自动发现（如夜间阻止 vs 紧急通知） | 待启动 | 策略引擎高级能力 |
| **P2** | **影响分析工具**：`ontology.impact_analysis("修改 max_tools")` → 受影响策略/工具列表 | 待启动 | 变更安全保障 |
| **P2** | **效果层覆盖率 ≥ 60%**：30+ 不变式中至少 18 个有 L3 效果验证 | 待启动 | MR-9 元规则 |

#### Phase 5: 对外输出（长期，V3 路标对齐）— 终极目标：`pip install ontology-engine`

> **项目终极目标**：将引擎和规则解耦为二层结构，让任何 Agent Runtime 项目只需编写自己的 YAML 就能获得工具治理+语义查询+governance 审计能力。
>
> **Layer 1 — `ontology-engine` 通用 pip 包**（项目无关）：
> - Tool Ontology Engine：YAML → 白名单/schema/参数映射/别名解析/语义查询/分类
> - Governance Checker 框架：5 种 check 执行器 + 元规则自动发现 + 三档特性开关(off/shadow/on)
> - 策略推理引擎：`infer_policy_targets("side_effects==true")` 零硬编码
>
> **Layer 2 — 项目级 YAML 配置**（项目特定）：
> - `tool_ontology.yaml`：工具声明（名称/类别/副作用/参数/别名）
> - `governance_ontology.yaml`：治理规则（不变式/元规则/check 定义）
> - `providers.d/*.yaml`：Provider 插件
>
> **当前基础**：Phase 3 ONTOLOGY_MODE=on 已切换，52 不变式 100% job 覆盖，89 引擎单测。
> **瓶颈**：领域本体(Phase 4) + 引擎包化(Phase 5) 尚未开始。

| 优先级 | 任务 | 状态 | 说明 |
|--------|------|------|------|
| **P1** | **引擎包化**：`ontology-engine` 可独立 `pip install` 的通用治理引擎 | 待启动 | **终极目标核心交付物** |
| **P2** | **Tool Policy Plugin**：`tool_policy.yaml` 声明式工具策略扩展接口 | 待启动 | V3 路标对齐 |
| **P2** | **Memory Policy Plugin**：`memory_policy.yaml` 记忆平面策略扩展 | 待启动 | V3 路标对齐 |
| **P2** | **Ontology Extension Guide**：第三方基于 ontology 框架扩展的指南 | 待启动 | V3 路标对齐 |
| **P3** | 证据型文章：从 17 不变式到 52 的治理演进实战 | 待启动 | 话语权输出 |

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

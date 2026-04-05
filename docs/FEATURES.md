# OpenClaw Model Bridge — 系统特性一览表

> V35 (2026-04-05) | 605 tests | 7 providers | 28 active jobs | 5 SLO metrics (5/5 PASS) | 19 preflight checks | WhatsApp + Discord dual-channel

| 分类 | 特性 | 说明 | 核心文件 |
|------|------|------|----------|
| **核心服务** | Gateway | WhatsApp 接入、媒体存储、工具执行、会话管理 | npm 全局 (:18789) |
| | Tool Proxy | 工具过滤(24→12)、自定义工具拦截、图片 base64 注入、SSE 转换、SLO 采集 | `tool_proxy.py` + `proxy_filters.py` (:5002) |
| | Adapter | 多 Provider 转发、认证、多模态路由(文本→Qwen3, 图片→VL)、Fallback 降级 | `adapter.py` (:5001) |
| **LLM Provider (7)** | Qwen3-235B (主力) | 文本对话，262K context | `providers.py` + `adapter.py` |
| | Qwen2.5-VL-72B | 图片理解，自动路由 | 检测 image_url 自动切换 |
| | Gemini Flash (降级) | 主力不可用时自动切换 | FALLBACK_PROVIDER |
| | OpenAI / Claude | 手动切换备选 | 环境变量 PROVIDER= |
| | Kimi K2 (Moonshot) | 国内长上下文 131K | MOONSHOT_API_KEY |
| | MiniMax M1 | 百万 token 上下文 | MINIMAX_API_KEY |
| | GLM-4-Plus (Zhipu) | 含 GLM-4V 视觉 | GLM_API_KEY |
| **自定义工具** | search_kb | 混合检索：语义搜索(embedding) + 关键词补充 + source/时间过滤 → followup LLM 解读 | `proxy_filters.py` 注入 |
| | data_clean | 数据清洗：7 种操作(dedup/trim/fix_dates 等)、5 种格式(CSV/JSON/Excel 等) | `data_clean.py` |
| **本地 AI** | KB RAG 语义搜索 | sentence-transformers 384 维 50+ 语言，零 API 调用 | `local_embed.py` + `kb_embed.py` + `kb_rag.py` |
| | 多媒体语义搜索 | Gemini Embedding 2 (768 维) 图片/音频/视频/PDF | `mm_index.py` + `mm_search.py` |
| **信息采集 (10 源)** | 论文矩阵 (5 源) | ArXiv(3h) + HF Papers + Semantic Scholar + DBLP + ACL Anthology | `jobs/` 目录 |
| | HackerNews | 每 3h 热帖抓取 | `run_hn_fixed.sh` |
| | 货代 Watcher | 每天 ×3 ImportYeti 数据 + LLM 分析 | `jobs/freight_watcher/` |
| | OpenClaw Releases | 每天 GitHub release + LLM 摘要 | `jobs/openclaw_official/` |
| | GitHub Trending | 每天 ML/AI 热门仓库 | `jobs/github_trending/` |
| | RSS 博客 | 每天 ×2 技术博客订阅 | `jobs/rss_blogs/` |
| **知识处理** | KB 每日摘要 | 每天 07:00 生成 daily_digest.md | `kb_inject.sh` |
| | KB 向量索引 | 每 4h 增量索引 (本地 embedding) | `kb_embed.py` |
| | KB 晚间整理 | 每天 22:00 | `kb_evening.sh` |
| | KB 智能去重 | 每天 23:00 (dry-run) | `kb_dedup.py` |
| | KB 深度回顾 | 每周五 LLM 跨笔记分析 | `kb_review.sh` |
| | KB 周趋势报告 | 每周六 关键词频率 + LLM 分析 | `kb_trend.py` |
| **SLO 监控 (5 指标)** | 延迟 p95 < 30s | 实时延迟百分位追踪 | `proxy_filters.py` ProxyStats |
| | 工具成功率 > 95% | 自定义工具执行结果统计 | record_tool_call() |
| | 降级率 < 5% | Fallback Provider 触发占比 | record_fallback() |
| | 超时率 < 3% | 错误自动分类(timeout/context/backend) | record_error() 分类 |
| | 自动恢复率 > 90% | 连续错误→恢复转换追踪 | recovery tracking |
| **阈值中心化** | config.yaml | 70+ 参数 9 分区 (SLO/proxy/tokens/alerts/routing/truncation/watchdog/incidents/jobs) | `config.yaml` + `config_loader.py` |
| **通知推送** | 双通道统一推送 | WhatsApp + Discord 同时推送，6 个 topic 频道(papers/freight/alerts/daily/tech/DM) | `notify.sh` |
| **监控运维** | Job Watchdog | 8 维元监控(job/日志/服务/锁/心跳/stats/磁盘/KB) → WhatsApp+Discord 告警 | `job_watchdog.sh` |
| | WhatsApp 保活 | 每 30min Gateway HTTP 探测 | `wa_keepalive.sh` |
| | 故障快照 | 连续 3 次错误→自动采集三层日志+stats+服务状态 → ~/.kb/incidents/ | `incident_snapshot.py` |
| | Cron 心跳 | 每 10min 金丝雀写入，watchdog 检测 | `cron_canary.sh` |
| | 对话质量日报 | 响应时间/成功率/工具分布/token 用量 | `conv_quality.py` |
| | Token 用量日报 | 消耗/分布/context 压力/趋势 | `token_report.py` |
| | 健康周报 | 每周一服务/任务/KB 综合报告 | `health_check.sh` |
| **DevOps** | auto_deploy | 每 2min Git→运行时同步(35 文件) + 漂移检测 + 按需 restart | `auto_deploy.sh` |
| | Preflight 体检 | 19 项检查(单测/注册表/语法/部署/安全/E2E/SLO) | `preflight_check.sh` |
| | GitHub Actions CI | 9 套单测 + 注册表 + config 校验 + 安全扫描 + bandit | `.github/workflows/ci.yml` |
| | pre-commit hook | API key/手机号泄漏 + Python 语法检查 | `.githooks/pre-commit` |
| **安全** | 审计日志 | 链式 SHA256 哈希，篡改/删除可检测 | `audit_log.py` |
| | 安全评分 | 7 维度 100 分 | `security_score.py` |
| | 安全扫描 | push 前强制 API key + 手机号扫描 | CI + pre-commit |
| **三方共享状态** | status.json | 优先级/反馈/事件/健康/SLO/偏好 — Claude Code + PA + Cron 实时同步 | `status_update.py` |
| | SOUL.md | PA 宪法级 system prompt (身份/状态/行为指令) | `SOUL.md` |
| | 用户偏好学习 | 每天自动从对话历史推断偏好 | `preference_learner.py` |
| **可复现证据** | Quick Start | 10 分钟一键跑通全栈（4 阶段，provider 自动检测） | `quickstart.sh` |
| | Golden Test Trace | 真实请求穿越全栈的完整记录 (521ms, 可复现) | `docs/golden_trace.json` |
| | SLO Benchmark | 真实生产数据报告 (5/5 PASS, p95=459ms) | `slo_benchmark.py` |
| | GameDay 故障演练 | 5 场景故障注入 (GPU 超时/断路器/快照/SLO/Watchdog) | `gameday.sh` |
| | 兼容性矩阵 | 7 Provider 能力声明 + 验证状态 | `providers.py` + `docs/compatibility_matrix.md` |
| **测试** | 10 套单测 | 605 用例全部通过 | `test_*.py` |
| | 全量回归 | full_regression.sh (单测+注册表+安全+代码质量) | `full_regression.sh` |
| | E2E Smoke | 基础对话 / 工具注入 / KB 搜索 端到端验证 | `wa_e2e_test.sh` |

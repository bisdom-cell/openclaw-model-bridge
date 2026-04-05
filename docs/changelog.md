# Changelog — openclaw-model-bridge

> 从 CLAUDE.md 提取的完整版本变更历史。Agent 按需 `read docs/changelog.md` 查阅。

## V35 变更摘要（2026-04-05）

> V1 路标冲刺完成 + 中国 Provider 扩展 + 可复现证据链闭环

### 功能变更

1. **Provider Compatibility Layer 扩展至 7 家**：新增 Kimi (Moonshot AI)、MiniMax、GLM (Zhipu AI) 三个中国国内 LLM Provider，均为 OpenAI-compatible API 格式。`providers.py` 导出 `PROVIDERS` dict 向后兼容 `adapter.py`
2. **SLO Benchmark 实验报告**：`slo_benchmark.py` 读取 `proxy_stats.json` 真实生产数据，生成 Markdown/JSON 报告（5/5 PASS，p95=459ms）。17 个单测覆盖
3. **Quick Start 一键跑通**：`quickstart.sh` 4 阶段（前置检查→启动服务→健康验证→Golden Test Trace），Provider 自动检测（7 个 API key 环境变量），10 分钟跑通全栈
4. **Golden Test Trace**：`docs/golden_trace.json` — 真实请求穿越全栈的完整记录（521ms，可复现证据）
5. **Sub-agent PoC**：sessions_spawn E2E 链路验证通过（Schema 注入→Qwen3 工具调用→Gateway 执行→ops agent 会话创建）。Qwen3 隐式触发不可靠，deferred 至下一代模型
6. **仓库整理**：移除 3 个废弃文件（-672 行），归档 13 个 PoC 文件到 `docs/archive/`
7. **README 全面刷新**：Quick Start 强调、7 Provider 矩阵、SLO 实测数据、Discord 双通道、Evidence Chain 证据链表

### 受影响文件

| 文件 | 变更 |
|------|------|
| `providers.py` | +KimiProvider/MiniMaxProvider/GLMProvider（4→7 Provider） |
| `adapter.py` | fallback dict 同步新增 3 个 Provider |
| `quickstart.sh` | 新增，4 阶段 Quick Start + 7-provider 自动检测 |
| `slo_benchmark.py` | 新增，SLO Benchmark 报告生成器 |
| `test_slo_benchmark.py` | 新增，17 个单测 |
| `test_providers.py` | 48→58 单测（+10 中国 Provider 测试） |
| `notify.sh` | V33 统一推送，WhatsApp + Discord 双通道 |
| `docs/golden_trace.json` | 新增，Golden Test Trace |
| `docs/slo_benchmark_report.md` | 新增，首份 SLO 实验报告 |
| `docs/compatibility_matrix.md` | 7 Provider 兼容性矩阵 |
| `docs/FEATURES.md` | V35 全面更新 |
| `README.md` | 全面刷新（605测试/7providers/SLO/Quick Start/Discord） |

---

## V34 变更摘要（2026-04-03）

> Stage2 启动 — Provider Compatibility Layer + 导师战略复盘

### 功能变更

1. **Provider Compatibility Layer**：`providers.py` — BaseProvider 抽象 + 4 个实现（Qwen/OpenAI/Gemini/Claude）+ ProviderRegistry 动态注册 + ProviderCapabilities 能力声明 + CLI 兼容性矩阵。48 个单测
2. **导师战略复盘**：Stage 判断（Stage1→Stage2）、主战场定位（Agent Runtime Control Plane）、V1/V2/V3 路标、三个高价值模块、话语权输出、三块差距
3. **兼容性矩阵文档**：`docs/compatibility_matrix.md` — 验证状态/降级路径/添加新 Provider 指南

---

## V33 变更摘要（2026-04-03）

> Discord 双通道 + 统一推送 + GameDay 故障演练

### 功能变更

1. **Discord 双通道支持**：所有推送同时发送 WhatsApp + Discord，6 个 topic 频道（papers/freight/alerts/daily/tech/DM）
2. **统一推送 notify.sh**：`source notify.sh && notify "msg" --topic papers`，替代零散的 `openclaw message send`
3. **GameDay 故障演练**：`gameday.sh --all`，5 场景（GPU 超时/断路器/快照/SLO/Watchdog）
4. **Gateway 不升级可用**：确认 OpenClaw v2026.3.23 升级 hold 决策不变

---

## V32 变更摘要（2026-04-01）

> 控制平面先行 + search_kb 全面加固 + 预发布验证体系

### 功能变更

1. **search_kb 全面加固**：新增 `recent_hours` 参数支持时间过滤（"今天有什么新内容"）；`source` 参数传递给 kb_rag 语义搜索（按来源过滤 arxiv/hf/dblp/acl/hn/notes）；fcntl 文件锁保护索引（排他写 LOCK_EX / 共享读 LOCK_SH）；哈希算法从 MD5 迁移至 SHA256（需一次性 `--reindex`）；结果截断改为段落边界对齐（上限 6000 字符）；notes 关键词搜索扩展到 topics 目录
2. **Watchdog 告警频率优化**：从每小时 → 每4小时（24次/天 → 6次/天，减少重复告警噪音）
3. **Pre-commit hook**（`.githooks/pre-commit`）：5项检查（API key 泄漏 / 手机号泄漏 / 危险 crontab 模式 / Python 语法 / Shell 语法），`git config core.hooksPath .githooks` 安装
4. **GitHub Actions CI**（`.github/workflows/ci.yml`）：8 个测试套件（proxy_filters/registry/cron_health/status_update/adapter/kb_business/audit_log/data_clean）+ 注册表校验 + 安全扫描 + bandit，PR 自动触发
5. **V32 方法论：控制平面先行**：三平面架构（Control 70% / Capability 85% / Memory 60%），SLO 最小集定义，E2E 旅程测试集成 CI，故障快照机制，阈值中心化

### 受影响文件

| 文件 | 变更 |
|------|------|
| `kb_rag.py` | +recent_hours/source 参数、fcntl 共享锁、SHA256 迁移 |
| `kb_embed.py` | +fcntl 排他锁、MD5→SHA256 |
| `tool_proxy.py` | +source 验证、recent_hours 提取、段落截断、超时60s |
| `proxy_filters.py` | +search_kb schema 新参数（recent_hours/source） |
| `jobs_registry.yaml` | watchdog: `30 * * * *` → `30 */4 * * *` |
| `.githooks/pre-commit` | 新增 |
| `.github/workflows/ci.yml` | 新增 |
| `CLAUDE.md` | +三平面架构、P0/P1/P2 路线图 |
| `status.json` | +V32 methodology、控制平面先行原则 |
| `README.md` | 全面刷新（396测试/32jobs/V32方法论） |
| `docs/architecture.svg` | v32 Five-Layer Control Plane First |

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


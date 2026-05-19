# Failure Modes Catalog — 血案分类索引

> **V37.9.81 索引快照 (2026-05-19)** | 21 个血案案例 / 19 元规则 (MR-1~MR-19) + 候选 MR-16 / 80 不变式 / governance v3.47

## 元价值

把 21 个血案 (MR-4 silent-failure 27 次演出) 从"散落 case 文件"升级为**可索引知识库**：

- **新血案来时**：先查此目录，类似模式？已立 INV？已有元规则？
- **新功能上线时**：先查此目录，5 大类别有没有覆盖？
- **新人入项目时**：21 个血案 = 21 个项目演化关键节点
- **V3 路标关键支柱**：21 个血案 = 21 个防御机会 = 21 个元规则候选源

## TL;DR 统计

| 维度 | 数量 |
|---|---|
| 总案例数 | 21 个血案 |
| MR-4 silent-failure 演出 | **27 次** (在 21 个案例中演出 27 次，多案例多次演出) |
| Class A (OS/Platform quirk) | 1 |
| Class B (设计假设错配) | 3 |
| Class C (自动批量盲点) | 2 |
| Class D (静默失败链 / MR-4 主战场) | 12 |
| Class E (取证盲区 / 60 天潜伏) | 3 |
| 已立元规则 | **19** (MR-1~MR-19) + 候选 MR-16 |
| 已立不变式 | **80** (governance v3.47) |

---

## Class A — OS/Platform Quirk (1 案例)

跨 OS 兼容性 / 平台特定行为 / 客户端层未声明限制。

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `whatsapp_client_display_folding_case.md` | V37.9.35 | 单条 8131 字符消息在 WhatsApp 显示为 2 个气泡同时间戳 | WhatsApp **客户端层**自动折叠 ~4000-4500 字符（非协议层），不是 OpenClaw / Baileys / Gateway 切片 | 5 层切片排查 + Path A 保守路径 budget 1400→4000 (`kb_review/kb_evening/kb_deep_dive`, 信息密度 2.86×, 保留手动分屏防御) | — (设计文档级修复，无 INV) |

**元教训**：字符数限制要看真实生产数据不是 documented limit。V37.9.21 的 1400 是没实测的"安全值"。

---

## Class B — 设计假设错配 (3 案例)

代码与契约不一致 / 跨脚本依赖隐式假设 / 测试设计与生产 caller 形态不符。

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `preflight_cascading_fix_case.md` | V37.8.3 | 一个 `cp` 漏同步被误诊为"配置缺失"，触发 5 轮连锁修复 / 4 层新复杂度 | 修复前未做原则 #28 三问 (之前存在/我引入/最小修复)，**条件反射式修复** | 5 轮回滚 + 立 **MR-10 understand-before-fix** | MR-10 |
| `governance_silent_error_case.md` | V37.3 | INV-CRON-003/004 子串匹配误报，YAML exec scope trap, summary 吞 error 三层嵌套 | 治理系统从未对自己应用 MR-4 (观察者的自我盲区) | 立 **MR-7 治理自观察** + **INV-GOV-001 summary-counts-all-non-pass** | MR-7, INV-GOV-001 |
| `ontology_sources_positional_parser_cascade_case.md` | V37.8.7 | LLM 漏一行导致 cn_title 显示为 `*---*` 或前一行价值字段，所有后续条目右移一格级联污染 | LLM 输出位置解析器假设严格 N 行格式，但 LLM 偶发漏行 → `lines[i], lines[i+1], lines[i+2]` + `i += 3` 步进级联错位 | 抽到独立模块 `ontology_parser.py` (纯函数可单测) + separator 切块 + 块内 key-based 解析 + 字段语义校验; 立 **MR-12 LLM-output-parser-must-be-key-based-not-positional** | MR-12, INV-ONTOLOGY-001 |

**元教训 (Class B 共通)**：
- 测试覆盖逻辑正确 ≠ 测试覆盖生产 caller 真实输入形态。
- LLM 输出永远不能假设格式严格遵守。
- 跨脚本依赖必须显式声明契约（MR-9 state-writes-go-through-helper 来源）。

---

## Class C — 自动批量盲点 (2 案例)

N 文件同款 bug × N 倍放大 / 批量注入工具引入跨文件系统性问题。

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `kb_content_and_sources_dedup_case.md` (P0-2/P0-3 部分) | V37.6 | 14 个 RSS/cron 抓取 job 各自独立 `>> sources/*.md`，同条目在 `## $DAY` 下重复出现 N 次 | 14 个 caller 各自实现"持久化 state 文件"逻辑，无统一 helper | 新建 `kb_append_source.sh` helper + 14 个 job 全部迁移；立 **MR-9 state-writes-go-through-helper-not-raw-redirect** | MR-9, INV-SRC-001 |
| (V37.9.58-hotfix 系列, 无独立 case 文件) | V37.9.50-hotfix → V37.9.58-hotfix | V37.9.57 `inject_level_4_to_aligned_jobs.py` 自动给 8 ALIGNED jobs 加 `prompt += os.environ.get(...)` 但**未补 `import os`** → 8/8 重演同款 NameError，silent fallback 跳过 rule_check | 自动批量工具是 bug 放大器 — 单点设计错误立即传播到 N 文件，`bash -n` 永远查不到 Python heredoc 内 NameError | (V37.9.58-hotfix2) **heredoc_import_scanner.py** AST scanner FAIL-CLOSE; 立 **MR-18 auto-batch-injection-must-validate-runtime-semantics** + **INV-HEREDOC-IMPORT-001** | MR-8, MR-18, INV-HEREDOC-IMPORT-001 |

**元教训 (Class C)**：**MR-8 (copy-paste-is-a-bug-class)** 是 Class C 的元规则。任何"复制相同逻辑到 N 处"必须升级为"提取公共原语 + 差异注入"。MR-18 是 MR-4 silent-failure 的**上游预防层**（让 CI/governance 自动拦截而不是靠程序员记忆）。

---

## Class D — 静默失败链 (12 案例) — MR-4 silent-failure 主战场

错误发生但用户视角无感知 / 错误被吞 / 错误被稀释 / 错误推送编造 / 错误链跨层级稀释。

### D-1 Dream 类 (3 案例)

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `dream_quota_blast_radius_case.md` | V37.2 | Qwen3 宕机 → adapter fallback 触发 30+ Gemini 调用 → Gemini quota 耗尽 → HN 垃圾推送 | Dream Map 配额爆炸链 (单 job 失败造成跨 job 雪崩) | 智能退避 429/524 + Map 熔断 3 次 + inter-call 节流; 立 **INV-QUOTA-001 + INV-PUSH-001** | MR-4 (第 1 次), INV-QUOTA-001 |
| `dream_map_budget_overflow_case.md` | V37.4 | Dream Map 预算溢出 + cache key 漂移导致连续 60min 超时 Reduce 从未执行 | workload 增长 × cache key 对 mtime/sort 敏感 × Reduce 不相信缓存 | Fix A cache-only fast path / Fix B 批次扩 / Fix C per-note content_hash 缓存 + signal dedup / 动态 DREAM_TIMEOUT_SEC 预算 | MR-4 (第 2 次), INV-DREAM-001/002, INV-CACHE-002 |
| `dream_self_referential_hallucination_case.md` | V37.8.6 | Dream 推送编造"Hugging Face 平台危机"分析 (Signal 与 Action 主题断裂) | 错误日志 (`HTTP 400 Bad JSON`) 写入 stdout → 被 cache 捕获 → LLM 把错误字样当外部信号 → 编造平台危机 (**原则 #23 链式幻觉典型实证**) | 四层 defense-in-depth: log→stderr / `_sanitize(U+D800-DFFF)` / `encoding='utf-8' errors='replace'` / REDUCE/CHUNK1/2/3 system prompt 反污染守卫; 立 **MR-11 shell-function-output-must-go-to-stderr** + **INV-DREAM-003** | MR-4 (第 7 次), MR-11, INV-DREAM-003 |

### D-2 KB / Daily Job 类 (4 案例)

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `kb_review_silent_degradation_case.md` | V37.5 | review 文件写入"行级日期匹配"的容器标题残渣，伪装成功推送 | 6 issue 相互掩护：shell `export` 顺序 / 硬编码源枚举漏 V37 新源 / LLM 失败机械 fallback / status.json 永远写 `llm:true` / 行级日期匹配粒度错配 / 悬空 follow-up 承诺 | 全 Python 化 `kb_review_collect.py` + registry-driven 源发现 + H2 drill-down + fail-fast 推送 [SYSTEM_ALERT]; 立 **INV-REVIEW-001** | MR-4 (第 4 次), INV-REVIEW-001 |
| `kb_evening_fallback_quota_chain_case.md` | V37.8.10 | 连续 2 天 22:00 告警 "HTTP 502: Bad Gateway" — 用户看到告警但**真实原因不可见** | 三层错误链稀释 (adapter 502 → proxy str(e) 只拿"HTTP 502" 不调 e.read() → client HTTPError 再丢) + FALLBACK_CHAIN 只配 1 个 fallback (gemini) + gemini quota 耗尽 | proxy `compose_backend_error_str(exc)` 纯函数读 e.read() body + JSON error 字段提取; 立 **INV-OBSERVABILITY-001** | MR-4 (第 9 次), INV-OBSERVABILITY-001 |
| `kb_content_and_sources_dedup_case.md` (P0-1) | V37.6 | OpenAI content blocks 列表直接 `str()` 成 `[{'type':'text',...}]` 字面量写进 KB | `proxy_filters.extract_user_text()` 不处理 content blocks list 形态 | `flatten_content(content)` 只提取 text 字段 | MR-4 (第 3 次), INV-KB-001 |
| `kb_deep_dive_cron_unregistered_case.md` | V37.9.18 | V37.9.16 上线 kb_deep_dive 但**没人手动 crontab add**, 2 天预期 22:30 触发完全静默 | 三层 silent 协同 (preflight 只 grep "间隔漂移"吞 warning + crontab_safe.sh 不检查 exit code + 35→35 count `<` 比较谎报 ✅) | preflight 加第二 grep + DRIFT_FAILED 标志 + crontab_safe 改 `-ne $((count_before+1))` 严格相等 + 失败 exit 1; **MR-4 第 16 次演出新形态: 三层独立 silent bug 协同掩护简单运维遗漏** + **MR-17 候选** declared-state-must-converge-via-machine-not-memory | MR-4 (第 16 次), MR-17, INV-CRON-005/006 候选 |

### D-3 PA / Agent 行为类 (3 案例)

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `pa_echo_chamber_case.md` | V37.1 | PA 迎合性回复 (用户 doubt 即附和，无批判性) | 三环反馈陷阱 (PA 看到用户反对 → SOUL.md 无批判规则 → 默认顺从) | SOUL.md 规则 9 批判性思考 (反迎合 + 禁模糊关联 + PA 回声室案例) | MR-4 (第 5 次) |
| `pa_alert_contamination_case.md` | V37.4.3 | PA 回复"已收到系统告警跟进任务，请打开系统偏好设置添加 /usr/sbin/cron 到 FDA"（完全答非所问 + 编造 macOS FDA 指令）**[注: V37.9.80 更新 — FDA 修复方向当时判定错, 60 天后证实 FDA 真是必需]** | 告警推送写入 sessions.json 作为 assistant role 消息 → proxy `truncate_messages` 保留窗口内 → Qwen3 attention 跨主题错误关联 → 用户新问题被当告警跟进响应 | 结构隔离层: `notify.sh` 注入 `[SYSTEM_ALERT]` 标记 / `proxy_filters.filter_system_alerts()` 在 `truncate_messages` 之前剥离 / SOUL.md 规则 10 主题对齐硬规则 | MR-4 (第 6 次), INV-PA-001/002 |
| `heartbeat_md_pa_self_silencing_case.md` | V37.8.16 | PA 把"任务完成"写进 `HEARTBEAT.md` (OpenClaw 保留文件) → 13h 潜伏 → Heartbeat 机制激活让 LLM 对所有用户消息回 `HEARTBEAT_OK` → Gateway stripTokenAtEdges 剥离 → 13h 完全静默 | 六条件组合爆炸 (WhatsApp plugin 4/10 自动安装 + PA 有 write 工具 + HEARTBEAT.md 是 OpenClaw 保留文件 + PA 不知道它有特殊语义 + heartbeat prompt 未配置 + HEARTBEAT.md 非空) | 四层联合防御: `RESERVED_FILE_BASENAMES` + SOUL.md 规则 11 + Proxy 拦截层 `detect_reserved_file_write()` + 测试层覆盖. 立 **MR-15 reserved-files-must-not-be-writable-by-LLM** + **INV-HB-001** | MR-4 (第 12 次), MR-15, INV-HB-001 |

### D-4 监控 / 推送链类 (4 案例)

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `whatsapp_silent_death_case.md` | V37.8.13 | Gateway 进程死亡 → WhatsApp 全断 9 小时 → Discord 正常但无 Gateway 相关告警 | 三层放大器同时失效 (auto_deploy `quiet_alert` 凌晨静默吞 CRITICAL / wa_keepalive 只写日志不告警 / restart.sh 不验证 Gateway 健康) | quiet_alert 静默期 Discord 始终推送 / wa_keepalive 连续 2 次 WARN 升级 Discord / restart.sh 5×3s post-bootstrap 健康验证. 立 **MR-14 alert-path-must-not-depend-on-failing-subject** + **INV-WA-001 / INV-QUIET-001** | MR-4 (第 11 次), MR-14, INV-WA-001/QUIET-001 |
| `finance_news_syndication_zombie_case.md` | V37.8.4 | finance_news 推送 7 个僵尸账号 (caixin 2227 天 / yicaichina 3364 天 / 等) | X/Twitter Syndication API 对已停更账号返回 stale 快照 (HTTP 200 + 可解析 HTML, 但最新推文 N 天/年前) — **HTTP 200 ≠ 账号健康** | INV-X-001 三层检测: stub (no_data=0+total=0) / stale (≥90% 老化) / count 守卫. 立 **CLAUDE.md 原则 #27 升级** ("账号健康 ≠ HTTP 200") | MR-10 (第 1 次), INV-X-001 |
| `zombie_detection_edge_case_closure.md` | V37.8.5 | V37.8.4 修复后立即发现 2 个边缘账号被漏标 (CNS1952 98/99 + SingTaoDaily 0-tweet stub) | V37.8.4 第三问"最小修复"浅化为"严格相等 + total>0 就够了"，但严格相等是窄特例，0-tweet stub 不在 `total>0` 论域 | 抽到 `finance_news_zombie.py` 纯函数模块 + tier 1 stub + tier 2/3 stale + count 守卫 + 整数比较避浮点 | MR-10 (第 2 次), INV-X-001 升级 [declaration, runtime] |
| `rsync_helper_set_e_regression_case.md` | V37.9.31 | V37.9.27 helper exit 透传 rsync 失败码 → 20 个 set -e caller 每天被杀 mid-script | V37.9.27 retry helper 行为破坏 caller 的 set -e 假设 — 旧 inline `\|\| true` 保证 exit 0，V37.9.27 helper 改写后透传非 0 | helper fail-open `exit 0` (best-effort backup 不杀脚本) + fail-loud 通过 stderr + JSONL forensic + watchdog 主动告警三层保留 | MR-4 (第 18 次新形态: 修复引入更深 silent failure) |

---

## Class E — 取证盲区 / 60 天潜伏类 (3 案例)

调试工具自己被屏蔽 / 假说级 silent failure / 数据驱动诊断方法论案例。

| 案例文件 | 版本 | 一句话症状 | 真根因 | 修复 | 关联 MR/INV |
|---|---|---|---|---|---|
| `movespeed_exfat_silent_backup_failure_case.md` | V37.9.4 | exfat fskit transient EPERM → 18 处 cron rsync 反模式协同沉默 6 天 | exfat fskit 不稳定 + 18 处复制粘贴反模式 (`2>/dev/null \|\| true`) + 监控盲区掩护 | exfat→APFS 转换 + 20 处反模式全部 fail-loud + JSONL forensic 取证 + 立 **INV-BACKUP-001** (全局 scan 反模式禁止) | MR-4 (第 14 次新形态: 复制粘贴反模式系统性沉默), INV-BACKUP-001 |
| `movespeed_noowners_uid_mismatch_case.md` | V37.9.29 | V37.9.4 APFS 修复后 EPERM 持续 → 假说 noowners + UID 错位 → chown 修复后 EPERM **100% 持平** | 假说部分证伪：UID 错位是真 bug (V37.9.4 引入 60 天潜伏) 但**不是 EPERM 主因** — Path D' chown 真生效但不解决目标问题 | V37.9.30 取证扩展第 8-11 维度 (ownership / ACL / lsof / snapshots), 让数据告诉我们哪个假说成立 | MR-4 (第 22 次新形态: 修复确实生效但修错了 bug), INV-OWNERSHIP-001 |
| `movespeed_tcc_sandbox_blood_case.md` ⭐ | **V37.9.80** | 60 天 6 个假说全证伪 (APFS / noowners UID / ACL deny / daemon 抢占 / TM 快照 / SSD 物理) → 真因 = **macOS TCC Sandbox 拒绝 cron 派生进程访问外置卷** | macOS Big Sur+ TCC 默认拒绝所有进程访问"受保护"位置 (外置卷). cron daemon 作为 launchd 子进程**默认无 FDA**, 派生进程 (rsync/ls/touch/lsof) 全部被 kernel sandbox 拒绝 | 用户手动 GUI: 系统设置 → 隐私与安全性 → FDA → 添加 `/usr/sbin/cron`. **V37.9.81 闭环**: 24h 数据回归铁证 (12h window 0 incidents / FDA 后 ~19h 0 / kernel sandbox deny 0 条) + INV-MOVESPEED-TCC-001 hard governance guard + capture.sh stderr 区分 sandbox_denied vs 真空 (修 V37.9.30 取证盲区根因) | MR-4 (第 N 次), MR-10 (反向第 5 次教训), **MR-16 候选** macos-cron-derived-processes-need-fda, INV-MOVESPEED-TCC-001 |

**Class E 元教训**：
1. **取证维度盲区** — V37.9.30 lsof/ACL/snapshot 采集器自身被 sandbox 拒绝, 返回空内容被误读为 "normal/empty" 6 周潜伏。**V37.9.81 B 修复**: capture.sh 4 处 stderr 独立捕获 + Python 端 `[sandbox_denied]` / `[tool_unavailable]` marker.
2. **数据驱动诊断方法论** — V37.9.14 取证 → V37.9.26 主动告警 → V37.9.27 主动修复 → V37.9.28 数据驱动诊断工具 → V37.9.29 ownership 维度 → V37.9.30 ACL/handle/snapshot → **V37.9.80 macOS 系统日志最后 1 跳真因** → V37.9.81 治理层固化. **每次假说被证伪都不靠盲改代码而靠扩取证维度** — 这是与"看到失败就改代码"反模式的本质区别。
3. **`log show --predicate`** 是 macOS 系统层取证的核武器 — 60 天 6 假说错都因为从未跑过这条命令。未来同款 macOS 问题首选思路：`log show --last Xh --predicate 'eventMessage CONTAINS "X"'`.

---

## 跨类元规则索引

| 元规则 | 案例触发 | 立案版本 | 派生 INV |
|---|---|---|---|
| **MR-1 ~ MR-3** (历史规则) | (前序系统规则) | V36-V37.1 | 多个 |
| **MR-4 silent-failure-is-a-bug** ⭐ | 全部 Class D + Class E 主战场 | V37.1 | 21 个 INV (主要) |
| **MR-5 cron-must-use-bash-lc-protective-pattern** | (历史 cron 安全规则) | V30 | INV-CRON-003/004 |
| **MR-6 critical-invariants-need-depth (≥2 层验证)** | governance audit 自身 | V36.3 | INV-LAYER-001 |
| **MR-7 governance-execution-is-self-observable** | governance_silent_error_case | V37.3 | INV-GOV-001 |
| **MR-8 copy-paste-is-a-bug-class** | 14 个 cron caller 直写 + 8 ALIGNED jobs heredoc os 漏 | V37.7 | INV-SRC-001, INV-HEREDOC-IMPORT-001 |
| **MR-9 state-writes-go-through-helper-not-raw-redirect** | kb_content_and_sources_dedup_case (P0-2) | V37.7 | INV-SRC-001 |
| **MR-10 understand-before-fix (修复前必答三问)** | preflight_cascading_fix_case / 2 X 僵尸案例 / V37.9.80 反向第 5 次 | V37.8.3 | (元规则级，无 INV) |
| **MR-11 shell-function-output-must-go-to-stderr** | dream_self_referential_hallucination_case | V37.8.8 | INV-DREAM-003 |
| **MR-12 llm-output-parser-must-be-key-based-not-positional** | ontology_sources_positional_parser_cascade_case | V37.8.8 | INV-ONTOLOGY-001 |
| **MR-13 candidate** error-chain-must-preserve-upstream-cause-across-layers | kb_evening_fallback_quota_chain_case | V37.8.10 候选 | INV-OBSERVABILITY-001 |
| **MR-14 alert-path-must-not-depend-on-failing-subject** | whatsapp_silent_death_case | V37.8.13 | INV-WA-001 |
| **MR-15 reserved-files-must-not-be-writable-by-LLM** | heartbeat_md_pa_self_silencing_case | V37.8.16 | INV-HB-001 |
| **MR-16 candidate** macos-cron-derived-processes-need-fda | movespeed_tcc_sandbox_blood_case | V37.9.80 (一周观察期 5/26 后立) | INV-MOVESPEED-TCC-001 |
| **MR-17 declared-state-must-converge-via-machine-not-memory** | kb_deep_dive_cron_unregistered_case | V37.9.19 | INV-CONVERGENCE-CRON/PROVIDERS/OPENCLAW/KB/INTEGRATION/SERVICES-001 (6 个) |
| **MR-18 auto-batch-injection-must-validate-runtime-semantics** | V37.9.50-hotfix → V37.9.58-hotfix 8 jobs 系统性 bug | V37.9.58-hotfix2 | INV-HEREDOC-IMPORT-001 |
| **MR-19 monitor-must-self-alarm-on-silent-abort** | watchdog 自身 silent abort 7 天 | V37.9.58-hotfix3 | INV-WATCHDOG-SELF-001 + INV-CRON-MONITOR-001 |

---

## 防御策略汇总

| 策略 | 实施版本 | 防御类别 |
|---|---|---|
| **声明式 governance** (`governance_ontology.yaml` v3.47, 80 invariants + 19 MR + 14 MRD scanners + 731 checks) | V37.1 → V37.9.81 | 所有类别 |
| **每日 governance audit cron** (07:00 自动 + 失败告警) | V37.1 | A/B/C/D/E |
| **fail-fast LLM cron** (V37.5 + V37.9.36-62 17/21 ALIGNED) | V37.5 → V37.9.62 | D-2 |
| **告警双通道独立** (notify.sh WhatsApp + Discord 不共享主体) | V37.8.13 | D-4 |
| **预防层元规则** (MR-15 reserved files / MR-18 auto-inject scanner) | V37.8.16, V37.9.58-hotfix2 | C, D-3 |
| **取证机制** (JSONL incident_capture + log show + analyzer 数据驱动诊断) | V37.9.14 → V37.9.80 | E |
| **反向 sabotage 守卫验证** (每次新 INV 必须验证 sabotage 后真 fail) | V37.4+ | 所有类别 |
| **三层 FAIL-OPEN** (dev silent pass / 损坏数据 skip / IO 失败 silent pass) | V37.9.18+ (普及) | E (governance audit 不能在 dev 误报缺生产数据) |
| **每周用户视角观察 30min** (原则 #32, 周一开工第一件事) | V37.8.11 | D-4 (用户感知缺口) |

---

## 使用指南

### 新血案来时

1. 查 5 大类别哪个最贴近 — A/B/C/D/E?
2. 同类案例的"真根因"列哪个最像?
3. 查关联 MR/INV — 已有元规则吗？已立守卫吗？
4. 如果都不像 → **新类别**或**新元规则候选**，登记到 V37.9.X+ 收工承诺。
5. 修复后必须立 INV (declaration + runtime ≥2 层, MR-6) + 反向 sabotage 验证 + 加入 ontology/docs/cases/ + 更新本目录索引。

### 新功能上线时

新功能开发前查 5 大类别表格：
- **A**: 跨 OS 兼容性测试做了吗？
- **B**: 跨脚本/跨模块假设有显式 contract 吗？
- **C**: 重复代码 ≥3 处了吗？该抽 helper 吗？
- **D**: silent failure 路径有吗？告警链不依赖被监控对象自身吗？
- **E**: 取证维度足够吗？调试工具自己会不会被屏蔽？

### 新人入项目时

按 D-1 → D-2 → D-3 → D-4 → E 顺序读案例文档，理解 27 次 MR-4 演出 = 项目 V37 演化的关键节点。

---

## 索引维护契约

- **添加新案例**：本目录"案例数"列 +1，所属类别表格新增一行，关联 MR/INV 同步更新。
- **新 MR 立案**：跨类元规则索引表新增一行，描述案例触发 + 派生 INV。
- **每次 governance v3.X 升级**：本目录 TL;DR 统计同步刷新 (inv 总数 / MR 总数 / checks 总数)。
- **元规则状态变化** (候选 → 立案 → 真激活)：跨类元规则索引表的 MR 行状态更新。

---

## 参考

- `ontology/governance_ontology.yaml` — 80 invariants + 19 MR + 14 MRD scanners 完整声明
- `ontology/docs/cases/*.md` — 21 个血案完整因果链架构图 + 三层根因 + 时间线
- `CLAUDE.md` 变更历史表 — V37.1 → V37.9.81 完整版本演化
- `docs/articles/audit_is_regression_not_prevention.md` — 立场文章: audit 不是 prevention 是 regression engine
- `docs/strategic_review_20260403.md` — 2026-04-03 导师战略复盘

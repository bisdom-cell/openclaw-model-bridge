# Ontology Audit 防御率实证报告 — 血案回填审计（路线 A）

> 2026-04-20 启动 | 15 个真实血案 × 3 个诚实问题 = 45 格矩阵
> 目标：**用数据证明 ontology audit 对本系统的真实作用力**，不是理论推测
> 方法：对每个血案回答"爆发前 audit 能否发现 / 为什么 / 补的能防下次吗"

---

## ⚖ 评分标准（必须诚实，不得美化）

### Q1：血案爆发前 audit 能否发现？

| 标记 | 含义 |
|---|---|
| ✅ 能 | audit 当时已有相关不变式且会 fail → 阻止爆发（实际未阻止因为没跑到那一层） |
| ⚠️ 部分 | audit 有相关 check 但未覆盖触发路径（粒度/维度不够） |
| ❌ 不能 | audit 完全无相关不变式 — **真实盲区** |
| N/A | 血案发生时 audit 系统尚未建立 |

### Q2：为什么不能发现？（Q1 非 ✅ 时）

| 类别 | 含义 |
|---|---|
| **空白类别** | 这一类故障**根本没被纳入**不变式设计 |
| **粒度不够** | 有不变式但 check 粗糙（只查存在不查语义 / 只查静态不查运行时） |
| **维度缺失** | 只有声明层没有运行时层（MR-6 违反） |
| **观察者盲区** | audit 本身就是故障源（无法自我观察） |
| **上游外部** | 故障发生在上游组件（OpenClaw / Qwen3），audit 无法直接观察 |

### Q3：补的不变式能防下次同类吗？

| 标记 | 含义 |
|---|---|
| 🛡️ 完全 | 结构性修复 + 单测 + 运行时回归，同类变种几乎不可能逃 |
| 🛡️ 一半 | 能防相同触发条件，但不同变种可能漏（如保留文件新成员） |
| ⚠️ 表层 | 只补了表面现象，根因没根除 |

---

## 📊 一览矩阵（15 血案 × Q1/Q2/Q3）

> **全部 15 个已诚实填完**

| # | 版本 | 血案 | Q1 | Q2 | Q3 | 新增不变式 |
|---|---|---|---|---|---|---|
| 1 | V37.3 | governance_silent_error | ❌ 不能 | 观察者盲区 | 🛡️ 一半 | INV-GOV-001 + MR-7 |
| 2 | V37.4 | dream_map_budget_overflow | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-DREAM-001/002 + INV-CACHE-002 |
| 3 | V37.4.3 | pa_alert_contamination | ❌ 不能 | 空白类别 | 🛡️ 完全 | INV-PA-001 + INV-PA-002 |
| 4 | V37.5 | kb_review_silent_degradation | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-REVIEW-001 |
| 5 | V37.6 | kb_content_and_sources_dedup | ❌ 不能 | 空白类别 | 🛡️ 完全 | INV-KB-001/SRC-001/DEDUP-001 |
| 6 | V37.1 | pa_echo_chamber | ❌ 不能 | 空白类别 | ⚠️ 表层 | SOUL.md 规则 9（无不变式） |
| 7 | V37.8.3 | preflight_cascading_fix | ⚠️ 部分 | 观察者盲区 | ⚠️ 表层 | MR-10（纯元规则无不变式） |
| 8 | V37.8.4 | finance_news_syndication_zombie | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-X-001（V37.8.4 仅声明层） |
| 9 | V37.8.5 | zombie_detection_edge_case_closure | ⚠️ 部分 | 粒度不够 | 🛡️ 完全 | INV-X-001 升级（+runtime） |
| 10 | V37.8.6 | dream_self_referential_hallucination | ❌ 不能 | 空白类别 | 🛡️ 完全 | INV-DREAM-003 |
| 11 | V37.8.7 | ontology_sources_positional_parser_cascade | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-ONTOLOGY-001 + MR-12 候选 |
| 12 | V37.8.10 | kb_evening_fallback_quota_chain | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-OBSERVABILITY-001 + MR-13 候选 |
| 13 | V37.2 | dream_quota_blast_radius | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-QUOTA-001 + INV-PUSH-001 |
| 14 | V37.8.13 | whatsapp_silent_death | ❌ 不能 | 空白类别 | 🛡️ 完全 | INV-WA-001 + INV-QUIET-001 + MR-14 |
| 15 | V37.8.16 | heartbeat_md_pa_self_silencing | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-HB-001 + MR-15 |

### 📈 统计分布

| 维度 | 分布 |
|---|---|
| **Q1 能否预防** | ✅ 能 **0 / 15 (0%)** · ⚠️ 部分 **2 / 15 (13%)** · ❌ 不能 **13 / 15 (87%)** |
| **Q2 原因类别** | 空白类别 **12 / 15 (80%)** · 观察者盲区 **2 / 15 (13%)** · 粒度不够 **1 / 15 (7%)** |
| **Q3 修复强度** | 🛡️ 完全 **5 / 15 (33%)** · 🛡️ 一半 **8 / 15 (53%)** · ⚠️ 表层 **2 / 15 (13%)** |

**核心数据**：
- **Audit 预防率 = 0%** —— 15 个血案爆发前，没有 1 次是被 audit 提前拦住的
- **Audit 回归率 = 86%** —— 爆发后 13/15 血案补上了 ≥半 强度的不变式
- **"空白类别"占 80%** —— 每次新故障暴露的都是审计从未思考过的维度

---

## 🔬 逐案详述（试填 3 个典型样本）

### 🟢 #1 V37.3 governance_silent_error（最 meta 的盲区 — audit 自己看不见自己）

**血案简述**：governance_checker.py 汇总行 `failed_invs = sum(1 for r in results if r["status"]=="fail")` 只统计 `status=="fail"`，忽略 `status=="error"`（不变式执行时炸异常）。汇总行印出"✅ 所有不变式成立"但实际有多个不变式抛出 NameError 或 exec 作用域错误被静默。

**Q1: 爆发前 audit 能否发现？** ❌ **不能**

audit 当时的不变式全部针对"被审计对象"（proxy_filters / adapter / jobs 等），**没有一条不变式审计 audit 自己**。这是 meta 层盲区。即使 audit 跑了 1000 次也发现不了 summary 逻辑吞 error。

**Q2: 为什么不能？** **观察者盲区**

没有"审计系统必须被审计"这一思维模型。当时的假设是 audit 本身是可靠的（类比：监控系统从不监控自己）。直到三层嵌套真实爆发（子串匹配 → exec 作用域陷阱 → silent error summary），才暴露出 audit 自己也要被审计。

**Q3: 补的不变式能防下次同类吗？** 🛡️ **一半**

补了：
- `INV-GOV-001` (governance-summary-counts-all-non-pass)：2 个 check — 源码 grep `r["status"] in ("fail","error")` 守卫 + runtime 真跑构造 error 注入场景断言"所有不变式成立"不会显示
- `MR-7` meta_rule "governance-execution-is-self-observable"：要求 check 层所有状态（pass/fail/skip/error）必须在汇总层有观察路径

**能防相同 bug 吗？** 能。精确锁定"error 不进 failed_invs 集合"这个模式。

**能防所有变种吗？** 不能。
- 其他 meta 盲区仍可能存在（例如：如果未来新增 warn/skipped/deferred 状态，汇总逻辑是否能正确处理？INV-GOV-001 只覆盖 fail/error 二元，新状态需手动扩展）
- 更深层问题：**audit 只能审计自己**已知维度的自我盲区（"summary 正确性"），对**未知维度的盲区**（如 audit 性能、audit 资源消耗）仍无法发现

**教训**：MR-7 是正确方向，但需要持续扩展 audit 的自我观察维度。当前只做了 1 步。

---

### 🟢 #3 V37.4.3 pa_alert_contamination（行为层血案 — audit 完全空白）

**血案简述**：job_watchdog 推送 "🚨 WARNING / 排查建议 / cron_doctor.sh" 告警 → Gateway 写入 sessions.json 作为 assistant role → Proxy truncate_messages 保留最近 N 条（含告警） → Qwen3 attention 跨主题关联 → 用户问哲学时 LLM 编造"请打开系统偏好设置添加 /usr/sbin/cron 到完全磁盘访问权限"（完全编造，launchd 管理的 cron 根本不需要 FDA）。

**Q1: 爆发前 audit 能否发现？** ❌ **不能**

当时的不变式覆盖了：
- 工具数量限制（MR-1）
- 部署一致性（MR-3）
- Cron 调度（MR-2）
- 环境变量（MR-5）
- 但**完全没有"消息元数据分类 + LLM context 纯度"维度**

audit 无法区分"这条 assistant role 是 PA 真实回复"vs"这条是被 Gateway 转写的系统告警"。

**Q2: 为什么不能？** **空白类别**

"context 污染"这类故障模式当时根本没被纳入不变式设计。视角缺口：audit 一直看"组件是否正确工作"，从未问过"组件间数据流的语义是否干净"。

**Q3: 补的不变式能防下次同类吗？** 🛡️ **完全**

补了双防线（结构 + 行为）：
- `INV-PA-001` (alert-does-not-pollute-chat-context)：10 checks
  - 声明层：`SYSTEM_ALERT_MARKER` 常量 + `filter_system_alerts` 纯函数 + tool_proxy 导入
  - **顺序锁 python_assert**：filter_idx < trunc_idx（filter 必须在 truncate 之前调用）
  - 运行时 python_assert：构造真实告警消息注入 → 断言 filter 正确剥离
  - 5 个推送脚本的 marker 注入守卫（grep notify.sh / auto_deploy / run_hn_fixed / run_discussions / run.sh）
- `INV-PA-002` (soul-rule-10-present)：5 checks 锁定 SOUL.md 规则 10 核心文本存在

**能防相同 bug 吗？** 能。结构层（proxy filter + 顺序锁）+ 行为层（LLM 规则 10）双保险。

**能防所有变种吗？** 基本能。**最后一公里风险**：
- 如果未来引入新的推送通道且绕过 notify.sh（直接 `openclaw message send` 不加 `[SYSTEM_ALERT]`），filter 无法识别 → INV-PA-001 不能自动发现新通道
- 缓解：目前 32 个推送脚本已审计过（见 MRD-NOTIFY-002），但新增脚本需要开发者记得加 marker
- 评级：**实践上完全，理论上需持续审计新增推送入口**

**教训**：这是 MR-4 silent-failure 最成熟的防御样本。两侧契约 + 顺序锁 + 运行时回归 = 结构性修复范式。

---

### 🟢 #15 V37.8.16 heartbeat_md_pa_self_silencing（今日最新 — 整个"runtime 保留文件"维度空白）

**血案简述**：PA (Wei) 调用 write 工具把"HN 告警已恢复 / 任务完成 / 下一步监控"三行写进 `~/.openclaw/workspace/HEARTBEAT.md`（把它当 TODO 文件名用）。13h 后用户发 WhatsApp 消息，OpenClaw heartbeat 机制（`auth-profiles-*.js:48796`）检测文件非空非注释 → `isHeartbeatContentEffectivelyEmpty=false` → 激活 runKind=heartbeat + 默认 prompt "If nothing needs attention, reply HEARTBEAT_OK" → Qwen3 严格执行回 HEARTBEAT_OK（12 字符）→ Gateway stripTokenAtEdges 剥离 → 用户完全看不到 PA 回复 13h。

**Q1: 爆发前 audit 能否发现？** ❌ **不能**

当时 53 条不变式中**零条**涉及"runtime 保留文件"概念。audit 从来没考虑过：
- OpenClaw workspace 下的某些文件是 runtime 控制文件（特殊语义）
- LLM write 工具对 workspace 无约束是潜在风险
- "文件内容影响 runtime 行为"这种耦合从未进入 audit 维度

**Q2: 为什么不能？** **空白类别**

V37.8.16 之前的 audit 关注：
- 代码层（proxy / adapter 是否正确过滤）
- 配置层（cron / FILE_MAP / env）
- 状态层（status.json 一致性）
- 推送层（告警链路）

**但"LLM 工具调用 × runtime 文件语义"这个交叉维度是完全空白**。

**Q3: 补的不变式能防下次同类吗？** 🛡️ **一半**

补了：
- `INV-HB-001` 12 checks：
  - 声明层：`RESERVED_FILE_BASENAMES` 常量 + `detect_reserved_file_write` 纯函数 + `fix_tool_args` 集成调用 + SOUL.md 规则 11 三个 grep 守卫
  - 运行时层：3 个 python_assert 真跑血案场景（detect 函数正确识别 / fix_tool_args 改写 args.content / SAFE_CONTENT 只含注释）
- `MR-15` 新元规则 "reserved-files-must-not-be-writable-by-llm"

**能防相同 bug 吗？** 能。对 `HEARTBEAT.md` 这个文件 100% 能防（proxy 拦截 + SOUL.md 规则 + 单测三层）。

**能防所有变种吗？** ❌ **不能 — 这是关键弱点**：

如果未来 OpenClaw 上游新增其他保留文件（例如：
- `AGENTS.md` 被赋予"覆盖 agent 配置"语义
- `BOOTSTRAP.md` 被赋予"影响启动流程"语义
- `.openclawignore` 被赋予"排除某些 workspace 路径"语义）

则 `RESERVED_FILE_BASENAMES` **不会自动发现**这些新成员。audit 只能在人类手动登记后覆盖。

**缓解建议（V37.8.17 候选）**：
1. 每次 OpenClaw 升级时，grep 新版本 dist/ 里 `\.md`\| `params\.files\.filter` 等模式，自动发现新保留文件候选
2. 或建立 `MRD-RESERVED-FILES-001`：扫 OpenClaw dist/*.js 的 file-name-special-semantic 声明，对齐到 RESERVED_FILE_BASENAMES

**评级**：对当前已知保留文件（HEARTBEAT.md）防御**完全**；对未来新增保留文件防御**空白**。综合 🛡️ **一半**。

---

## 📈 当前 3 样本初步观察

| 观察 | 证据 |
|---|---|
| **Q1 全部 ❌ 不能** | 3/3 血案爆发前 audit 完全无相关不变式 |
| **Q2 主要是"空白类别"** | 2/3（pa_alert + heartbeat_md），1/3 是"观察者盲区"（governance self） |
| **Q3 主要是"一半"** | 2/3 样本能防相同 bug 但变种可能漏（meta / 新保留文件） |

**初步结论（样本量 3）**：
- Ontology audit 的价值**不在"预防"，而在"回归"**——每次血案后新增不变式锁定不会重复发生
- audit 的**维度扩展滞后于故障发生**——每类新故障都需要 1 次真实爆发才能被纳入审计
- **MR-15 类"元规则升级"**是扩展 audit 维度的关键机制，但依赖人的洞察，无法自动触发

等完成 15 个样本后，会给出**最终防御率数字 + 盲区分布图 + 改进路线**。

---

## 📝 批量详填 — 第 1 批（按矩阵 # 顺序）

### #6 V37.1 pa_echo_chamber（2026-04-09）

**血案**：用户提系统论"五维模型"，PA 回复时模糊关联 MEMORY.md 的"本体-代理-Token"，用"异曲同工"代替具体分析，并声称"已保存到知识库"（实际未调工具）。

**Q1: ❌ 不能** — audit 当时完全没有"LLM 回复质量维度"。无法检测"回复是否迎合""模糊关联""是否真的调用了保存工具"。

**Q2: 空白类别** — 对话质量 / 批判性思考 / 保存声明真实性都是 runtime 行为层问题，当时 audit 只关注"系统组件是否正确工作"，从未问过"PA 回复对用户是否有价值"。

**Q3: ⚠️ 表层** — 只补了 SOUL.md 规则 9（"批判性思考 / 禁止模糊关联 / 保存必须真实"）作为**行为层指令**。**无对应 governance 不变式**。audit 层仍无从检测规则 9 是否被遵守 — 只能靠 LLM 自觉 + 用户事后察觉。迎合性回复、虚假保存声明在未来仍可能再发生，且无监测信号。

**教训**：行为层规则 ≠ 治理覆盖。规则 9 属于"顺势设计"（原则 #22）给 LLM 看的 prompt；audit 层需要独立的 L3 效果层不变式（待 V37.x Phase 4 扩展）。

---

### #13 V37.2 dream_quota_blast_radius（2026-04-10）

**血案**：Qwen3 524 超时 + Dream MapReduce V2 单次 30+ LLM 调用 × fallback 到 Gemini → Gemini 日配额耗尽 → 同一 Adapter 上所有 Job 共享配额 → HN 推送全部"技术内容，详见原文"硬编码回退文案，用户以为"正常推送"。

**Q1: ❌ 不能** — audit 无"跨 Job 共享资源消耗模型"维度。Dream 和 HN 在依赖图上独立，但共享 Adapter→Gemini 配额链，这种**隐式耦合**从未被不变式建模。

**Q2: 空白类别** — 共享资源、功能增强的 blast radius、静默回退路径 3 个维度都缺失。V36.1 的 MapReduce 升级没人评估它的"最坏调用数 × fallback 配额消耗"。

**Q3: 🛡️ 一半** — 补了 2 个不变式：
- `INV-QUOTA-001` "LLM Quota Blast Radius"：grep `kb_dream.sh` 含 `MAP_CONSECUTIVE_FAILS` 熔断字样
- `INV-PUSH-001` "No Silent Garbage Push"：grep `run_hn_fixed.sh` 含 `__LLM_FAILED__` 信号传播

**能防相同 bug**：能。Dream 连续 3 次失败熔断 + HN 3 次重试 + 失败不推送。

**能防变种**：❌ 只针对 Dream + HN 两个具体 Job。如果未来新增其他 MapReduce 类 Job（finance_news / ontology_sources / 未来任何批处理）没有熔断逻辑，audit 不会自动发现。

**教训**：按"具体脚本 grep"写的不变式只能锁定具体脚本。要普适化需提升为元规则（如 "所有批量 LLM 调用必须有连续失败熔断机制" + 扫所有 heredoc）。

---

### #2 V37.4 dream_map_budget_overflow（2026-04-11）

**血案**：286 notes × 76s/batch ÷ 4.4 notes/batch = **82 min > 60 min 预算**，Dream Map 必然超时；且 Notes cache key = md5(批次拼接) 对 mtime 敏感，00:40 预热的 cache 03:00 Reduce 时全 miss → Reduce 路径重跑 Phase 1b 循环 → 连续 N 天"全局超时"告警，Reduce 从未执行。

**Q1: ❌ 不能** — audit 无"预算 vs workload 比例"或"cache key 稳定性"或"分离调度契约"维度。固定预算 3600s 在 workload 增长时静默失效。

**Q2: 空白类别** — "workload 增长检测" / "cache key 时间正交性" / "Map-Reduce 分离调度的代码路径契约"3 个维度完全空白。

**Q3: 🛡️ 一半** — 补了 3 个不变式共 ~10 checks：
- `INV-DREAM-001` (map-budget-scales-with-mode)：声明层 + runtime 预算字面量匹配
- `INV-DREAM-002` (reduce-path-must-not-re-run-map-loops)：声明层 + runtime 顺序锁（SKIP_MAP_LOOPS 赋值行号 < 门控行号）
- `INV-CACHE-002` (notes-cache-key-stable-under-mtime-drift)：声明层 + runtime tempfile 真跑 hash 对比

**能防相同 bug**：能（INV-CACHE-002 的 runtime 断言直接构造同内容不同 mtime 的 2 个 tempfile 验证 hash 相等）。

**能防变种**：⚠️ 只针对 Dream 具体路径。mm_index / kb_embed 等其他批处理若出现类似问题（预算 vs workload 脆弱 / cache key 对时间敏感）不会被自动发现。

**教训**：三个不变式全部 runtime 层是 V37.8 MR-6 执行后的正面样本。缺的是：把三个原则（预算弹性 / cache 正交 / 分离调度契约）上升为元规则。

---

### #4 V37.5 kb_review_silent_degradation（2026-04-11）

**血案**：kb_review.sh 一次运行 6 个相互掩护的 bug（shell export 时序错 / 硬编码源枚举漏 2 源 / 行级日期匹配过滤掉 H2 section body / 机械 fallback 把残渣当回顾 / status.json 永远写 `llm:true` / 悬空 follow-up 承诺）→ 用户连续 N 周收到推送但回顾内容全是日期标题，无任何论文实体，audit 全绿。

**Q1: ❌ 不能** — audit 当时没有任何"推送内容质量"或"源列表漂移检测"或"LLM 失败真实状态"维度。

**Q2: 空白类别** — 6 个 bug 中每一个都在审计空白：shell 作用域陷阱 / 硬编码 vs registry / markdown 结构解析粒度 / fail-fast / 状态真实性 / 悬空承诺，每一个都是 V37.5 第一次被纳入审计。

**Q3: 🛡️ 一半** — 补了 `INV-REVIEW-001` 14 checks（9 声明层 + 5 runtime）：
- 声明层：版本标记、[SYSTEM_ALERT] 存在、不含机械 fallback、不含悬空承诺、3 个核心函数定义、jobs_registry.yaml 声明 kb_source_file、fail-fast 顺序锁
- Runtime：真跑 load_sources_from_registry 发现 ≥12 源（含 ai_leaders_x + ontology_sources）、H2 parser 窗口过滤正确性、mock LLM 失败 → status=llm_failed 不伪装

**能防相同 bug**：能。fail-fast 顺序锁 + registry-driven 消除了 6 个 bug class。

**能防变种**：⚠️ 只针对 kb_review 本身。V37.6 kb_evening 血案后才补 INV-EVENING-001（MR-8 "copy-paste is a bug class" 首次兑现）。仍未普适化为"所有 LLM 驱动的 review 类 Job 必须 fail-fast 不伪造状态"。

**教训**：6-bug silent degradation 是 MR-4 最经典样本。修复范式（结构 + 治理 + 回归）已成熟；缺的是"该范式应用到所有同类 Job"的元规则。

---

## 📝 批量详填 — 第 2 批

### #5 V37.6 kb_content_and_sources_dedup（2026-04-11）

**血案**：两个独立 bug 同日暴露：① `str(m["content"])` 对 OpenAI 多模态 list 产生 Python repr 字面量 `[{'type': 'text', ...}]` → 污染 KB note 标题；② 14 个 cron job 复制粘贴 `} >> $KB_SRC` 反模式 → 同一天多次 run 在 sources 文件里追加多个 `## YYYY-MM-DD` H2 section → 438 行重复。kb_dedup.py 自己的 file-level seen set 算法又把合法跨 H2 日期重复当 bug 处理。

**Q1: ❌ 不能** — audit 对 OpenAI content `str \| list` 类型歧义 / 14 处 bug pattern / dedup 算法正确性都完全空白。

**Q2: 空白类别** — "类型系统歧义的防御性转换" / "跨 job 复制粘贴 bug class 识别" / "事后清理工具自身正确性"3 个维度都是第一次被纳入审计。

**Q3: 🛡️ 完全** — 补了 3 个不变式 19 checks（含 5 个 runtime 级 subprocess 驱动）：
- `INV-KB-001` content-blocks-flattened-before-kb-write：声明层 flatten_content 函数 + tool_proxy.py 调用 + runtime 构造 content blocks 真跑
- `INV-SRC-001` sources-writes-are-idempotent-at-source：14 个 job 全部走 `kb_append_source.sh` helper + catch-all 运行时 scan 任何 `>> $KB_SRC` 直写
- `INV-DEDUP-001` kb-dedup-is-h2-scoped-and-scans-unindexed-notes：声明层 + runtime 构造双 H2 文件验证不误判

**能防相同 bug**：能。

**能防变种**：🛡️ 较完整。`INV-SRC-001` 的 catch-all scan 是少见的"规则级覆盖"——不针对具体 job 而是扫所有 `jobs/**/*.sh`，这让未来新增 job 若违反幂等原则会自动报警。这是审计覆盖**从具体到普适**的难得样本。

**教训**：MR-8 "copy-paste-is-a-bug-class" 候选在本案浮现但未立案，留到 V37.7 正式形式化。catch-all scan 是未来更多元规则应该追求的覆盖模式。

---

### #7 V37.8.3 preflight_cascading_fix（2026-04-13）

**血案**：`~/auto_deploy.sh`（HOME 副本）是旧版本，没有仓库新增的 `finance_news` FILE_MAP 条目 → 与 crontab 不一致 → preflight 报 20 失败。正确修复是 1 条 `cp` 命令，但被误诊为"FILE_MAP 缺少 preflight 条目"→ Claude Code 进行 5 轮连锁修复 + 4 层新复杂度（双目标部署 / SCRIPT_DIR HOME 检测 / dict-of-lists 解析器 / 等）→ 系统永久携带这些不必要的复杂度。

**Q1: ⚠️ 部分** — audit 能报"preflight 失败 20 项"（不变式正常工作），但**无法检测"正在做连锁修复"这种流程问题**。audit 看的是结果，看不到 Claude Code 的推理过程。

**Q2: 观察者盲区** — audit 不能观察修复行为本身。"5 轮连锁修复而不是 1 条 cp 命令"是人（Claude Code）的决策失败，audit 层无从介入。

**Q3: ⚠️ 表层** — 补了 **MR-10 "understand-before-fix"** 元规则（修复前必答三问）。但 MR-10 是**纯声明式元规则**，**无对应 governance 不变式**，无法在 CI 层强制。只能依赖人在每次修复前回忆并执行三问。

**能防相同 bug**：⚠️ 不可靠。MR-10 诞生**第二天**（V37.8.4）就被违反（finance_news X handle 改名没验证活跃度，见 #8）。即 MR-10 本身的有效性依赖执行者的纪律。

**能防变种**：❌ 不能。连锁修复的诱因（"看到报错就想改"）是认知偏差，audit 层无监测手段。

**教训**：最重要的元规则之一（MR-10）居然没有 governance 层锁定，只靠"Claude Code 自己记得"。未来可尝试：每次 commit 前 hook 扫 session 日志看是否违反了"修复前三问"（如 commit message 提到"再次修复" / 短时间多次 edit 同文件等 heuristic）。

---

### #8 V37.8.4 finance_news_syndication_zombie（2026-04-14）

**血案**：V37.8.3 改名 3 个 X handle（CaixinGlobal→caixin 等），**上线第二天发现三个改名后的 handle 全是僵尸**（最新推文 2227 / 3364 / 420 天前）。再审计 22 handle 发现 ~32% 污染率（Reuters 253 天 / BrookingsInst 585 天 / WorldBank 2KB stub / ChannelNewsAsia 2955 天 / ...）。Syndication API 对僵尸账号返回 HTTP 200 + 可解析 JSON + 有推文数据三层全绿，但最新推文是几年前。

**Q1: ❌ 不能** — audit 无"X 账号时效性"维度。"协议层绿灯 ≠ 内容层健康"从未进入审计模型。

**Q2: 空白类别** — 任何外部 API 的"协议 vs 内容"健康分离都是新维度。

**Q3: 🛡️ 一半** — 补了 `INV-X-001` 13 checks（仅声明层 `[declaration]`），包括：
- parser 检测 `diag["total"] > 0 and diag["old"] == diag["total"]` 模式
- ⚠️ ZOMBIE嫌疑 诊断前缀
- 独立 `zombies_${DAY}.txt` 文件
- 3 天 comm -12 连续告警
- 7 个已确认僵尸 handle `file_not_contains` 守卫

**能防相同 bug**：部分能。`file_not_contains` 守卫防止已确认僵尸 handle 回归（7 handle 逐个锁定）。

**能防变种**：⚠️ 严重缺陷。V37.8.4 检测器**自身埋下两个边缘盲区**（严格相等 `old == total` 漏 99% 老化；`total > 0` 门槛漏 0-tweet stub）→ CNS1952 98/99 / SingTaoDaily 0 tweet 都没被检出。且 INV-X-001 当时是 `[declaration]` 单层（MR-6 违反），声明层 grep 只能证明 pattern 存在，无法证明"逻辑覆盖所有僵尸情形"。下次 V37.8.5 用血案暴露并补齐。

**教训**：INV-X-001 V37.8.4 的 `[declaration]` 单层审计是 MR-6 反面样本——"有治理 ≠ 治理正确"。

---

### #9 V37.8.5 zombie_detection_edge_case_closure（2026-04-15）

**血案**：V37.8.4 Mac Mini E2E 即发现检测器的两个边缘盲区（严格相等 / total>0 门槛），用户手动观察发现后登记 unfinished 延后处理。次日 V37.8.5 开工按原则 #28 三问处理——即"修复本身埋坑"的连续第二次演出。

**Q1: ⚠️ 部分** — V37.8.4 的 INV-X-001 `[declaration]` 声明层能 grep 到检测器存在（"治理有"），但**不能检测检测器的边缘盲区**（"治理正确吗"）。

**Q2: 粒度不够** — 声明层 `file_contains` 只能证明 pattern 存在，无法证明逻辑覆盖所有应有情形。这是 MR-6 "critical-invariants-need-depth" 精确指向的问题。

**Q3: 🛡️ 完全** — V37.8.5 结构化闭合：
- 提炼独立纯函数模块 `finance_news_zombie.py` `classify_zombie(diag, count) -> (bool, tier)`
- 三层 tier：stub (no_data=0 + total=0) / stale (old*10 >= total*9) / alive，加 count 守卫防低频活跃误报
- INV-X-001 升级 `[declaration]` → `[declaration, runtime]`，13 → 20 checks
- 新增 python_assert 真跑 5 个 tier 场景
- 24 独立单测
- MR-8 兑现：禁止 shell 内嵌 `def classify_zombie` inline fallback（防模块缺失时静默退回 V37.8.4 行为）

**能防相同 bug**：能。runtime 层 python_assert 覆盖 5 tier 场景包括两个血案边缘 case。

**能防变种**：🛡️ 较完整。Tier 可扩展（未来可加 Tier 4 "垃圾推文类僵尸"等）。MR-8 inline fallback 禁止让 shell 侧未来不可能 "回退"到 V37.8.4 简单逻辑。

**教训**：本案是 **MR-6 强制深度的正面兑现**——INV-X-001 升级为双层深度，从"pattern 存在"升级为"逻辑覆盖证明"。也是"修复的修复"范式，说明 audit 需要**迭代迭代再迭代**。

---

## 📝 批量详填 — 第 3 批

### #10 V37.8.6 dream_self_referential_hallucination（2026-04-15）

**血案**：某 source/note 含 surrogate UTF-8 → `json.dump(body, f, ensure_ascii=False)` UnicodeEncodeError → body_file 截断 → adapter 返回 400 "Bad JSON"（456B HTML 错误页）→ `log()` 用 `echo` 写 stdout → `signals=$(llm_call ...)` 命令替换捕获错误日志进 cache → Reduce LLM 读 cache 看到"Bad JSON 400"字样 → 编造"Hugging Face 平台危机"推送给用户。LLM 把系统自己的运行时痕迹当成外部平台事件，编造一整套合理化叙事。

**Q1: ❌ 不能** — audit 无"shell log 输出通道 × LLM 合理化本能"交叉维度。Dream 4 次重试全部失败 vs 仍产出完整推送这种"失败伪装成功"模式当时完全不在审计范围。

**Q2: 空白类别** — 4 个独立机制叠加（surrogate 编码炸 / log 写 stdout / `$(cmd)` 捕获 log / cache 非空检查只看字数不看质量）每一个单独看都是"设计选择"，只有组合才暴露为 bug class。

**Q3: 🛡️ 完全** — 补了 `INV-DREAM-003` 16 checks（declaration + runtime 四层防御）：
- 第 1 层 `log() >&2`：阻断 stdout 污染通道（最根本，一个 `>&2` 阻断整条幻觉链）
- 第 2 层 `_sanitize(U+D800-U+DFFF → U+FFFD)`：json.dump 前清洗孤立代理码点
- 第 3 层 `open(..., encoding='utf-8', errors='replace')`：第二道编码防线
- 第 4 层 REDUCE/CHUNK1/2/3 system prompt 反污染守卫：明示禁止把 HTTP 错误码/Python 异常/错误页 HTML 当外部信号
- 加上 19 单测 + runtime python_assert 真跑 sanitize 5 场景

**能防相同 bug**：能。四层任一单独都能阻断，叠加几乎不可能再发生。

**能防变种**：🛡️ 较完整。四层中第 1 层（log→stderr）后来被 V37.8.8 抽象为 **MR-11 元规则** `shell-function-output-must-go-to-stderr-if-not-returned-value`，V37.8.9 落地 `MRD-LOG-STDERR-001` 运行时扫描器覆盖所有 shell 文件。从"单点修复"真正升级为"架构硬规则"。

**教训**：本案是**从单个血案提炼出跨文件适用元规则**的经典样本——不只是防 Dream 再出 bug，更防所有 shell 脚本的同类 log 反模式。原则 #23（链式幻觉）的教科书级实证。

---

### #11 V37.8.7 ontology_sources_positional_parser_cascade（2026-04-15，与 #10 同日）

**血案**：LLM 输出偶尔漏一行"要点：" → `run_ontology_sources.sh:300-313` 用严格位置 `lines[i], lines[i+1], lines[i+2]` + `i += 3` 步进 → 所有后续条目的 cn_title / highlight / stars 全部右移一格级联污染。用户 WhatsApp 看到 `*---*` 作为文章标题、`*价值：⭐⭐⭐⭐*` 作为标题。emit 端 `*{cn_title}*` 直接输出无任何语义校验。

**Q1: ❌ 不能** — audit 无"LLM 输出解析器必须 key-based 而非位置"维度。同样的 `i += N` / `lines[i+N]` 反模式早在 V37.5 kb_review 和其他多处就存在，但从未被元规则化。

**Q2: 空白类别** — LLM 输出解析的脆弱性在 audit 层完全空白。原因：audit 一直关注"代码是否正确工作"，没问过"代码是否能容忍 LLM 输出的分布偏移"。

**Q3: 🛡️ 一半** — 补了 `INV-ONTOLOGY-001` 7 checks（含 runtime python_assert 真跑用户实际看到的污染场景，构造"3 篇但第 2 篇缺要点"输入断言级联不发生）：
- parse_llm_blocks 函数定义（纯函数可单测）
- `_SEPARATOR_RE.split` 按 `---/===/***` 切块
- 块内 `startswith('中文标题')` key-based 识别
- 24 单测覆盖正常/缺要点/缺价值/缺标题/端到端血案重现

**能防相同 bug**：能。单块缺行不再影响其他块。

**能防变种**：⚠️ 严重缺陷。**V37.8.7 时只修 ontology_sources 一个 job**。MR-12 候选"llm-output-parser-must-be-key-based-not-positional"当时仅为**候选**未立案。其他 LLM 输出解析器（finance_news / dblp / hf_papers / semantic_scholar 等）未被自动扫描。

**后续兑现**：V37.8.8 正式立案 MR-12 元规则，V37.8.9 落地 `MRD-LLM-PARSER-POSITIONAL-001` 运行时扫描所有 `jobs/*/run_*.sh` + `kb_*.py` 共 37 个 LLM 调用脚本。从"单点修复 → 跨 37 文件扫描"，V37.8.6 类同演化路径。

**教训**：同一类错误在系统内必然多次出现。第一次触发时修单点（V37.8.7）= 🛡️ 一半；系统化为元规则（V37.8.8-9）= 🛡️ 完全。本案与 #10 一起开创了"血案 → 案例级修复 → 元规则立案 → MRD 运行时扫描"四步跃迁路径。

---

### #12 V37.8.10 kb_evening_fallback_quota_chain（2026-04-14~15 连续 2 天）

**血案**：2026-04-14 / 04-15 连续 2 天 22:00 用户收到 `[SYSTEM_ALERT] kb_evening 失败 原因: HTTP 502: Bad Gateway`。真实原因是 primary Qwen3 circuit breaker OPEN + fallback gemini HTTP 429 quota exhausted，但 adapter→proxy→client 三跳每跳都把 upstream 错误 body 丢弃（adapter `{"error": "ALL 1 FALLBACKS FAILED: gemini 429"}` → proxy `str(HTTPError)` 只拿 "HTTP Error 502" 不读 body → client `e.reason` 再次丢 body）→ 告警经三次稀释完全变成无意义的"Bad Gateway"。

**Q1: ❌ 不能** — audit 无"错误链透明度"（错误信息在层间传递时是否保留根因）维度，也无"LLM 配额跨 cron 协调"维度。

**Q2: 空白类别** — "observability 架构缺陷"这种"错误没被吞只是被稀释到无意义"的 silent failure 新形态当时完全没被纳入审计模型。

**Q3: 🛡️ 一半** — 补了 `INV-OBSERVABILITY-001` 12 checks（declaration + runtime 两侧契约）：
- Proxy 侧：`compose_backend_error_str(exc)` 纯函数读 `exc.read()` body + JSON error 字段提取 + `MAX_UPSTREAM_BODY_CHARS=500` 截断 + fail-open try/except 契约
- Client 侧：`kb_review_collect._compose_http_reason` 镜像 helper
- **架构契约 MR-8 兑现**：helper 放 proxy_filters.py（纯函数可测）而非 tool_proxy.py（import 启动 HTTP server 不可测）
- 21 单测 + runtime 真跑血案场景（proxy helper 拼 upstream body 含 "gemini 429"）

**能防相同 bug**：能。下次 kb_evening 告警会显示完整链，形如 "HTTP 502 \| upstream: ALL 1 FALLBACKS FAILED: gemini HTTP 429"。

**能防变种**：⚠️ 仍有缺口：
- **根因未根治**：`FALLBACK_CHAIN = ["gemini"]` 只配 1 个 fallback 的事实没变。gemini 429 了仍然会失败，只是用户能看到真实原因。"扩展 fallback_chain ≥2 provider" 登记到 unfinished 需用户决策 API key（至今未做）
- 其他 17+ LLM cron 仍无 `last_run_*.json` 统一 observability 入口
- `MR-13 候选`"error-chain-must-preserve-upstream-cause-across-layers" 仅为候选未立案

**教训**：本案是**修告警信息密度 ≠ 修根因**的典型。audit 层补的是 observability，根治性修复（扩展 provider 数）被登记到 unfinished 后至今未做。这是"半修复"常见模式——"补到用户能看到真相"而不是"让真相不再发生"。

---

### #14 V37.8.13 whatsapp_silent_death（2026-04-16）

**血案**：00:20 auto_deploy 触发 restart.sh → Gateway 启动 21s 内崩溃 → launchd rapid-crash jettison 卸载 service → WhatsApp 全断 9h。三层放大器同时失效：① auto_deploy `quiet_alert` 凌晨 00-07 静默期**同时跳过 WhatsApp 和 Discord**，3 次 CRITICAL preflight 失败全被 `[QUIET]` 吞没 ② `wa_keepalive` 每 30min 写 WARN 到日志**但不推送任何告警**（18 次沉默 9h）③ `restart.sh` 报 "Done!" 但不验证 Gateway 是否真活。用户手动对比 WhatsApp(空) vs Discord(满) 才发现。

**Q1: ❌ 不能** — audit 当时无"告警路径独立于失效主体""静默期分通道""restart 后健康验证"三个维度。

**Q2: 空白类别** — 告警链路自身的可靠性（告警链不能依赖被监控对象自身）当时不在审计模型里。

**Q3: 🛡️ 完全** — 补了 2 个 critical 不变式共 11 checks + 21 单测 + `MR-14` 元规则：
- `INV-WA-001` (wa-keepalive-escalates-to-discord-on-consecutive-warn) 7 checks：WARN_COUNT_FILE 计数器 / ESCALATE_FIRST 阈值 / Discord 升级路径 / V37.8.13 血案注释 / 恢复命令提示 / source env vars / **runtime python_assert 断言告警路径不走 WhatsApp**
- `INV-QUIET-001` (quiet-alert-sends-discord-during-silence-period) 4 checks：静默期路径含 discord send / V37.8.13 注释 / runtime python_assert 断言 quiet_alert 体含 discord 无 whatsapp / [SYSTEM_ALERT] 前缀在 is_quiet_hours 判断之前
- **MR-14 元规则**"alert-path-must-not-depend-on-failing-subject"：告警链不得依赖被监控对象自身（Gateway 宕则 WhatsApp 不通 → 用 WhatsApp 告警 Gateway 宕 = 死循环）
- restart.sh post-bootstrap 5×3s 健康验证循环

**能防相同 bug**：能。INV-WA-001 的 runtime 断言直接检查 `wa_keepalive.sh` 告警路径里没有 `--channel whatsapp`。

**能防变种**：🛡️ 较完整。MR-14 已立案为元规则，未来任何"监控 X 的告警走 X 自身通道"设计会在代码评审时被发现。restart.sh 的健康验证循环也可复用到其他服务重启。但：MR-14 还没有对应的 `MRD-ALERT-INDEPENDENCE-001` 运行时扫描器（类似 V37.8.9 MR-11/12 的落地方式），所以 MR-14 目前仍停留在"声明层"。若未来新增告警路径违反该原则，audit 不会自动拦截。

**教训**：本案是**从血案提炼元规则**的又一样本，但元规则的"运行时扫描器落地"尚未完成——延续 V37.8.8→V37.8.9 的"MR 立案 → MRD 落地"两步模式，MR-14 走到了第一步还没走到第二步。

---

_共 14/15 详填完成（#15 V37.8.16 已在开头作为典型样本详填，见前文）。下面是**最终洞察总结**。_

---

# 🔬 最终洞察总结（基于 15 血案完整样本）

## 🎯 数据层：硬性事实

```
预防率 (Q1 ✅)        0 / 15 = 0%
部分预警率 (Q1 ⚠️)    2 / 15 = 13%
完全漏检率 (Q1 ❌)   13 / 15 = 87%

事后回归率 (Q3 🛡️)   13 / 15 = 87%   (🛡️ 完全 5 + 🛡️ 一半 8)
根因未根治率 (Q3 ⚠️)   2 / 15 = 13%

维度缺失主因：
  空白类别        12 / 15 = 80%   ← 审计从未思考过的维度
  观察者盲区        2 / 15 = 13%   ← audit 看不见 audit 本身 / 修复行为
  粒度不够          1 / 15 =  7%   ← 有 check 但粒度粗糙
```

**核心数字**：**Audit 事前防御率 = 0%，事后回归率 = 87%**。

这不是失败——这是一个**回归工程**（regression engineering）系统，不是**预测工程**（predictive engineering）系统。和软件测试一样：测试不能预测所有 bug，但能确保修过的 bug 不再回归。

---

## 📈 时间演化：审计维度如何扩张

按不变式引入时间排列：

| 时代 | 新增审计维度 | 代表不变式 | 触发血案 |
|---|---|---|---|
| V37.2 | LLM 配额消耗 / 静默推送 | INV-QUOTA-001, INV-PUSH-001 | Dream Quota Blast |
| V37.3 | Governance 自观察 | INV-GOV-001 + MR-7 | Governance silent error |
| V37.4 | 预算弹性 / cache key 稳定 / 分离调度契约 | INV-DREAM-001/002, INV-CACHE-002 | Dream Map Budget |
| V37.4.3 | LLM context 污染 | INV-PA-001/002 | PA Alert Contamination |
| V37.5 | 推送内容质量 / registry-driven | INV-REVIEW-001 | kb_review Silent |
| V37.6 | 类型歧义 / 跨 job copy-paste | INV-KB/SRC/DEDUP-001 | KB Content+Sources |
| V37.7 | 双跑审计 | INV-DEDUP-002 | Dangling refs |
| V37.8 | MR-6 深度强制 | INV-LAYER-001 | 治理深度审查 |
| V37.8.3 | 修复流程约束 | MR-10（纯元规则） | Preflight Cascading |
| V37.8.4 | 外部账号时效性 | INV-X-001 [declaration] | X Zombie |
| V37.8.5 | 检测器本身的覆盖正确性 | INV-X-001 升级 [+runtime] | Zombie Edge Case |
| V37.8.6 | LLM 输入污染链 | INV-DREAM-003 + MR-11 | Dream Self-Hallucination |
| V37.8.7 | LLM 输出解析鲁棒性 | INV-ONTOLOGY-001 + MR-12 | ontology_sources Parser |
| V37.8.8-9 | 元规则 → MRD 运行时扫描 | MRD-LOG-STDERR-001, MRD-LLM-PARSER-* | （跨血案） |
| V37.8.10 | 错误链透明度 | INV-OBSERVABILITY-001 + MR-13 | kb_evening Fallback |
| V37.8.13 | 告警路径独立性 | INV-WA-001, INV-QUIET-001 + MR-14 | WhatsApp Silent Death |
| V37.8.16 | runtime 保留文件语义 | INV-HB-001 + MR-15 | HEARTBEAT.md PA Self-Silencing |

**审计体系从 V37.2 到 V37.8.16 的 45 天内，从 ~10 个不变式扩张到 53 个不变式 / 15 个元规则**。每次扩张都是**真实血案"喂养"**的结果——没有任何一个维度是理论推演出来的，全部来自生产环境的硬教训。

---

## 🕳 当前审计的已知盲区（按 Q2 分布）

### 盲区 1: 空白类别（12/15 = 80%）主因是**维度设计滞后**

每次新故障暴露一个"之前没想到的维度"。这是**预期之内**的——audit 不可能预测所有失败模式。但可以加速维度识别：

**可行手段**：
1. **对抗性审计（路线 B）**：手工构造"故意破坏"场景看 audit 能抓到几个，主动暴露新维度
2. **同类系统参考**：看 Observability / Chaos Engineering 社区常见 bug class，提前纳入
3. **上游故障注入**：OpenClaw 升级前扫新版 dist/ 寻找新 semantic file / runtime 约束（如 MR-15 HEARTBEAT.md 本可以从上游发现）

### 盲区 2: 观察者盲区（2/15 = 13%）— 审计自己的不可观察性

2 个样本：
- **V37.3 governance_silent_error**：audit summary 逻辑自己 bug — **audit 自己从不 audit 自己**
- **V37.8.3 preflight_cascading_fix**：audit 看不见 Claude Code 的"连锁修复"决策过程

**已部分修复**：MR-7 "governance-execution-is-self-observable" + INV-GOV-001 summary 不吞 error。但**只覆盖"summary 正确性"一个子维度**。audit 的性能、资源消耗、执行时间、跳过率等仍未被自观察。

**可行手段**：建立 `audit-of-audit` 子系统 — governance_checker 输出自身运行 metric（耗时 / skip count / error 类型分布），反过来 audit。

### 盲区 3: 粒度不够（1/15 = 7%）— 声明层 grep 的本质限制

V37.8.5 暴露：`file_contains "old == total"` 只能证明 pattern 存在，不能证明"逻辑覆盖所有僵尸情形"。MR-6 "critical-invariants-need-depth" 正是为此而设。但目前只强制 critical 不变式 ≥2 层，**severity=high 仍允许单层**——这是下一个待强制的维度（V37.8.5 主动兑现 INV-X-001 从 [declaration] 升到 [declaration, runtime] 是正面样本，但无制度性约束）。

---

## 🏗 元规则与 MRD 的跃迁路径（最重要的发现）

审计最有价值的**结构性进步**，不是每次新增单个不变式，而是**从单点修复 → 元规则立案 → MRD 运行时扫描**的三步跃迁：

```
V37.8.6 Dream 血案
  ↓ 单点修复 log→stderr
V37.8.8 MR-11 立案
  ↓ "shell 函数输出必须 stderr"
V37.8.9 MRD-LOG-STDERR-001 落地
  ↓ 扫 38 个 shell 文件自动发现违规
≡ 从"1 个 bug 被修"升级为"38 个文件被约束"
```

**已完成三步跃迁的元规则**（2 个）：
- MR-11 (shell-output-stderr) → MRD-LOG-STDERR-001
- MR-12 (llm-parser-key-based) → MRD-LLM-PARSER-POSITIONAL-001

**走了一步/两步但未落地 MRD 的元规则**（4 个）：
- MR-13 (error-chain-upstream-cause) — 候选未立案
- MR-14 (alert-path-independence) — V37.8.13 立案，无 MRD
- MR-15 (reserved-files-not-llm-writable) — V37.8.16 立案，无 MRD
- MR-10 (understand-before-fix) — V37.8.3 立案，**本质上无法 MRD**（流程问题）

**行动建议**：**V37.8.17 候选**：给 MR-14 / MR-15 各自落地 MRD 扫描器
- MRD-ALERT-INDEPENDENCE-001：扫所有 `--topic alerts` 路径 + 被监控对象，发现"监控 X 用 X"死循环
- MRD-RESERVED-FILES-001：扫 OpenClaw dist/*.js 的 `params.files.filter(f => f.name === ...)` 模式，自动发现未登记的 runtime 保留文件

---

## 🎯 对用户原始问题的回答

> "最近一段时间不会增加新的任务，但会对原有系统进行加固和优化处理，**尤其是验证子项目 ontology 对本系统的 audit 的作用**。"

**基于 15 个血案回填审计的结论**：

1. **Ontology audit 是一个 regression engineering 工程，不是 prevention engineering 工程**。它的价值体现在"血案爆发后，下次不会再以同样方式出现"——**87% 的事后回归率**是真实有效的防御力。

2. **审计的结构性进步在"元规则 → MRD 跃迁"**，不在"不变式数量增加"。单个不变式只能锁定单个 case；元规则能约束整个 bug class；MRD 运行时扫描能覆盖所有同类文件。**53 个不变式的价值**远不如**2 个完整跃迁的元规则（MR-11/12）**大。

3. **当前最紧的盲区是"空白类别"（80%）**。对抗性审计（路线 B）是系统性暴露新维度的最佳手段。现在不做，下次血案仍会照常发生。

4. **"观察者盲区"是 audit 的永久盲点**。MR-7 只补了 summary 正确性一个点，audit 性能 / 资源 / 执行时间 / 跳过率仍无自观察。建议建立 **audit-of-audit 子系统**作为 V2 路标（"别人敢用"）的核心能力。

**一句话总结**：**Ontology audit 的真实作用 = 把 45 天内每一次真实血案固化为"下一次不会以同样方式再发生"的结构性保证**。它不能预测未知故障（预防率 0%），但能确保已知故障 87% 不回归。审计的演化是**驱动于血案、提炼于元规则、普适于 MRD**的三级跃迁机制——这是一个**活着**的审计体系，不是死的清单。

---

## 🗺 路线 A 后续可执行项（给用户决策）

| 优先级 | 任务 | 预期价值 |
|---|---|---|
| **P0** | 路线 B 对抗性审计 | 主动暴露"空白类别"新维度，突破 0% 预防率 |
| **P0** | V37.8.17 MRD-ALERT-INDEPENDENCE-001 | MR-14 第二步跃迁 |
| **P0** | V37.8.17 MRD-RESERVED-FILES-001 | MR-15 第二步跃迁，对齐 OpenClaw 新版本 |
| **P1** | MR-6 扩展到 severity=high（当前只强制 critical） | 消除 V37.8.5 类粒度盲区 |
| **P1** | audit-of-audit 子系统 | 补 MR-7 观察者盲区的其他维度 |
| **P1** | 路线 C Security + Governance 双轨统一 | MR-16 + 7 INV-SEC-* |
| **P2** | 给 MR-10 寻找可检测代理 | 如 commit message heuristic / session 日志扫描 |

---

_本报告是路线 A 的最终交付。一旦你确认，将 commit 并登记到 status.json 作为"Stage2 验证者阶段"的首份知识资产。_

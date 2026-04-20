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

> 试填版本：3 个典型样本（标 🟢）已完成，12 个（标 ⚪）待批量完成

| # | 版本 | 血案 | Q1 | Q2 | Q3 | 新增不变式 |
|---|---|---|---|---|---|---|
| 🟢 1 | V37.3 | governance_silent_error | ❌ 不能 | 观察者盲区 | 🛡️ 一半 | INV-GOV-001 + MR-7 |
| ⚪ 2 | V37.4 | dream_map_budget_overflow | _pending_ | | | INV-DREAM-001/002 + INV-CACHE-002 |
| 🟢 3 | V37.4.3 | pa_alert_contamination | ❌ 不能 | 空白类别 | 🛡️ 完全 | INV-PA-001 + INV-PA-002 |
| ⚪ 4 | V37.5 | kb_review_silent_degradation | _pending_ | | | INV-REVIEW-001 |
| ⚪ 5 | V37.6 | kb_content_and_sources_dedup | _pending_ | | | INV-KB-001/SRC-001/DEDUP-001 |
| ⚪ 6 | V37.1 | pa_echo_chamber | _pending_ | | | SOUL.md 规则 9（无对应不变式） |
| ⚪ 7 | V37.8.3 | preflight_cascading_fix | _pending_ | | | MR-10 元规则 |
| ⚪ 8 | V37.8.4 | finance_news_syndication_zombie | _pending_ | | | INV-X-001 |
| ⚪ 9 | V37.8.5 | zombie_detection_edge_case_closure | _pending_ | | | INV-X-001 升级 |
| ⚪ 10 | V37.8.6 | dream_self_referential_hallucination | _pending_ | | | INV-DREAM-003 + MR-11 候选 |
| ⚪ 11 | V37.8.7 | ontology_sources_positional_parser_cascade | _pending_ | | | INV-ONTOLOGY-001 + MR-12 候选 |
| ⚪ 12 | V37.8.10 | kb_evening_fallback_quota_chain | _pending_ | | | INV-OBSERVABILITY-001 + MR-13 候选 |
| ⚪ 13 | V37.2 | dream_quota_blast_radius | _pending_ | | | INV-QUOTA-001 + INV-PUSH-001 |
| ⚪ 14 | V37.8.13 | whatsapp_silent_death | _pending_ | | | INV-WA-001 + INV-QUIET-001 + MR-14 |
| 🟢 15 | V37.8.16 | heartbeat_md_pa_self_silencing | ❌ 不能 | 空白类别 | 🛡️ 一半 | INV-HB-001 + MR-15 |

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

## 📝 待批量完成（12 个）

2 / V37.4 / dream_map_budget_overflow
4 / V37.5 / kb_review_silent_degradation
5 / V37.6 / kb_content_and_sources_dedup
6 / V37.1 / pa_echo_chamber
7 / V37.8.3 / preflight_cascading_fix
8 / V37.8.4 / finance_news_syndication_zombie
9 / V37.8.5 / zombie_detection_edge_case_closure
10 / V37.8.6 / dream_self_referential_hallucination
11 / V37.8.7 / ontology_sources_positional_parser_cascade
12 / V37.8.10 / kb_evening_fallback_quota_chain
13 / V37.2 / dream_quota_blast_radius
14 / V37.8.13 / whatsapp_silent_death

用户确认本报告格式合理后，将依次阅读对应 case doc 并批量完成。

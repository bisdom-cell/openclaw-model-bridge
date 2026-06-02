# Failure Modes Catalog — openclaw-model-bridge 静默失败全谱系

> **单一权威版 (canonical) · V37.9.99 / 2026-06-02**
> 22 案例 · MR-4 silent-failure ~28 次演出 · 5 类失败**机制** · governance v3.52 (85 不变式 + 21 元规则 + 15 MRD scanners + 790 checks)
>
> **本文件是唯一权威版。** 历史 `docs/failure_modes_catalog.md`（V37.9.86 机制分类版）+
> `ontology/docs/failure_modes_catalog.md`（V37.9.81 位置索引版）两份分叉副本已**合并入本文件并删除冗余副本**
> （V37.9.99，兑现 MR-8 copy-paste-is-a-bug-class 单一真理源 + 回应外部评审 doc-drift 批评）。
> 记录失败模式的文档，自己不能是失败模式。

---

## 元价值 — 为什么这本目录存在

把 22 个散落在 `ontology/docs/cases/` 的血案，从"一次性反思"升级为**可索引、可比较、可对外复述的知识库**：

| 使用场景 | 用法 |
|---|---|
| **新血案来时** | 先查 5 大机制类别 → 同类"真根因"列哪个最像 → 查关联 MR/INV（已有元规则？已立守卫？）→ 都不像则是新类别/新元规则候选 |
| **新功能上线时** | 对照 5 类别 checklist（跨 OS 测了吗？跨脚本契约显式吗？重复代码 ≥3 处了吗？silent 路径有吗？取证维度够吗？） |
| **新人入项目** | 22 个血案 = V37 演化的 22 个关键节点 = 22 个防御机会 |
| **对外话语权（Stage 2→3）** | 50 天真实生产数据的失败模式分类法，是 ArXiv *"Silent Failures Taxonomy"* 论文的证据基础，也是评审者 5 分钟理解"这个系统最容易在哪出问题"的入口 |

---

## TL;DR 统计

| 维度 | 数量 |
|---|---|
| 真失败案例（独立 case doc） | **22** |
| MR-4 *silent-failure-is-a-bug* 演出 | **~28 次**（在 22 案例中跨形态演出；编号有跳号，后期以"新形态"记） |
| Class A 环境/平台 Quirk | 1 case + 6 quirk 子事件 |
| Class B 设计假设错配 | 4 |
| Class C 错误吞噬与稀释 | 5 |
| Class D 链式幻觉与编造 | 4 |
| Class E 运维遗漏与取证盲区 | 8 |
| 已立元规则 | **21**（MR-1~MR-21） |
| 已立不变式 | **85**（governance v3.52） |
| MRD 主动扫描器 | **15** |
| 治理 checks | **790** |

> 数据基准：VERSION `0.37.9.68` / CLAUDE.md `v37.9.99` / governance_ontology.yaml `v3.52`。
> 排除 2 个非失败 case 文件：`openclaw_as_ontology.md`（架构探讨）、`cron_line_full_comparison_audit_2026_06_02.md`（"审计后决定不动"决策文档，正确的非动作）。

---

## 分类法：按失败**机制**而非发生**位置**

> 本目录采用**机制导向**分类（错误如何失效），而非位置导向（错误在哪个 job 失效）。
> 理由：同一机制可在 Dream / KB / PA / 监控多处复现，按机制分类才能让"防御一处 = 免疫一类"。
> 这也是 ArXiv taxonomy 论文的组织原则——读者要的是"失败的种类"，不是"出问题的文件列表"。

```
A 环境/平台 Quirk      逻辑正确，运行环境隐含行为导致失效（dev 绿灯，target 暴露）
B 设计假设错配         代码假设 vs 实际（部署拓扑 / 契约 / 测试形态 / 涌现行为）
C 错误吞噬与稀释       错误发生但被某层静默吃掉 / 跨层稀释失 cause / 自动批量放大
D 链式幻觉与编造       LLM 把污染数据当事实，编造合理叙事推送给用户（最危险）
E 运维遗漏与取证盲区   代码正确但部署/注册步骤遗漏 / 调试工具自身被屏蔽长潜伏
```

---

### Class A — 环境/平台 Quirk

> 系统逻辑正确，但运行环境（OS / shell / 客户端 / 文件系统）的**隐含行为**导致意外。
> **共性**：dev（Linux/root）永远绿灯，只有 Mac Mini（macOS/bash 3.2/zsh）真跑才暴露。

| 案例 | 版本 | 一句话症状 | 真根因 | 关联 MR/INV |
|---|---|---|---|---|
| `whatsapp_client_display_folding_case.md` | V37.9.35 | 单条 8131 字符消息在 WhatsApp 显示为 2 气泡同时间戳 | WhatsApp **客户端层**自动折叠 ~4000 字符（非协议层），不是 OpenClaw/Baileys/Gateway 切片 | — (设计文档级修复) |

**quirk 子事件**（无独立 case 文件，记录于 `ontology/docs/reflections/2026-05-13_complexity_bug_taxonomy.md`）：

| 子事件 | 版本 | 根因 |
|---|---|---|
| bash `set -e` 不传播 ERR trap | V37.9.58-h4 | bash 3.2 function 内 fail 默认不触发 ERR trap，需 `set -E` (errtrace) |
| macOS BSD awk multibyte | V37.9.58-h3 | bsd awk 处理无效 UTF-8 字节抛 `towc: multibyte conversion failure` exit 1 + pipefail + set -e → 整脚本 abort |
| macOS BSD 无 `timeout` 命令 | V37.9.78-h | `timeout 30 ...` 在 macOS 不存在，需 `command -v timeout / gtimeout` 三档 fallback |
| CJK 全角变量解析 | V37.9.43-h2 | bash 3.2 UTF-8 边界混淆 `$VAR）` → unbound variable，需 `${VAR}` 显式 brace |
| zsh `interactive_comments` OFF | V37.9.56-h2 | `bash X.sh # 注释` 中 `#` 被 zsh 当 `$1` 传给 bash → `int("#")` ValueError |
| zsh `*` glob 双引号外 expansion | V37.9.66 | 不引号 `*` 在 zsh 会 glob，无匹配报 "unknown file attribute" |

**防御**：`cross_os_quirk_scanner.py`（INV-CROSS-OS-001，V37.9.67）主动检测 4 种 quirk 模式 + MR-20 *framework-level-fix-must-validate-in-target-environment*（dev 全绿不代表修复工作）。

**元教训**：字符数/命令/语法的"安全假设"必须看真实生产数据，不是 documented limit。V37.9.21 的 1400 字符是从没实测的"安全值"。

---

### Class B — 设计假设错配

> 代码基于某个假设（部署拓扑 / 跨脚本契约 / 测试输入形态 / 模型涌现行为），但假设与实际不一致。
> **共性**：单测覆盖逻辑正确 ≠ 覆盖生产 caller 真实形态。

| 案例 | 版本 | 假设 vs 实际 | 真根因 | 关联 MR/INV |
|---|---|---|---|---|
| `preflight_cascading_fix_case.md` | V37.8.3 | "缺 FILE_MAP 配置" vs 只需 `cp` | 修复前未做三问，条件反射式修复触发 5 轮连锁、4 层新复杂度 | **MR-10** understand-before-fix（立案于此） |
| `ontology_sources_positional_parser_cascade_case.md` | V37.8.7 | LLM 输出严格 N 行 vs 偶发漏行 | `lines[i], lines[i+1], lines[i+2]` + `i += 3` 步进 → LLM 漏一行 → 全部右移级联污染（用户看到 cn_title = `*---*`） | **MR-12** key-based-not-positional, INV-ONTOLOGY-001 |
| `v37_9_92_observer_path_blood_case.md` ⭐ | V37.9.94 | `_resolve_*_path` 三候选含 Mac Mini 真实位置 vs 全 miss `$HOME/openclaw-model-bridge/` | auto_deploy FILE_MAP 不把 yaml 拷到 `$HOME` → 5 天 registry filter 静默 fallback + status.json 闭环承诺零兑现 | MR-15 第 4 次演出, **INV-CROSS-ENV-PATH-001**（V37.9.94 立 + scanner 首扫即抓 V37.9.91 第 5 次 near-miss） |
| `dream_map_budget_overflow_case.md` | V37.4 | cache key 在 mtime/sort 漂移下稳定 vs 敏感 | workload 增长 × cache key 对 mtime/sort 敏感 × Reduce 不相信缓存 → 60min 超时 Reduce 从未执行 | MR-4, INV-DREAM-001/002, INV-CACHE-002 |

**path 假设子事件**（被 INV-PATH-CONSISTENCY-001 统一守护）：V37.9.56-h `top_alignment_picker` cache_paths（`~/jobs/X/` vs `~/.openclaw/jobs/X/`）、V37.9.66 `_format_cron_line`（`jobs/` → `.openclaw/`）、V37.9.85 `.py` 按 bash exec 假设、V37.9.68-h Dream "4 段自然产 4 chunks" vs 合并算法、V37.9.73 测试用 `# header` vs 生产用 `## ` 开头。

**防御**：`path_consistency_scanner.py`（INV-PATH-CONSISTENCY-001，V37.9.82）三方一致性 audit + `cross_env_path_scanner.py`（INV-CROSS-ENV-PATH-001，V37.9.94）AST 扫所有 `_resolve_*_path` 函数 + MR-12 key-based parser。

**元教训**：跨脚本/跨环境依赖必须**显式声明契约**（MR-9 来源），LLM 输出永远不能假设格式严格遵守（MR-12 来源），测试输入必须镜像生产 caller 真实形态（V37.9.73 教训）。

---

### Class C — 错误吞噬与稀释

> 错误真实发生了，但被某一层静默吃掉，或跨层传递时上游 cause 被稀释，最终用户视角看到的是"成功"或一个失去信息的告警。

| 案例 | 版本 | 吞错/稀释点 | 真根因 | 关联 MR/INV |
|---|---|---|---|---|
| `governance_silent_error_case.md` | V37.3 | 汇总层 | `failed_invs` 只数 `status=="fail"`，漏 `error` → 汇总行说"所有不变式成立"（**观察者的自我盲区**） | **MR-7** governance-self-observable（立案于此）, INV-GOV-001 |
| `kb_content_and_sources_dedup_case.md` | V37.6 | KB 写入层 + sources append | (P0-1) OpenAI content blocks 列表 `str()` 成 `[{'type':'text',...}]` 写进 KB；(P0-2) 14 cron 各自 `>> sources/*.md` H2 重复污染 | MR-4 (第 3 次), **MR-9** state-writes-through-helper, INV-KB-001/SRC-001 |
| `kb_evening_fallback_quota_chain_case.md` | V37.8.10 | adapter→proxy→client 三跳 | adapter 502 → proxy `str(e)` 只拿"HTTP 502" 不调 `e.read()` → client HTTPError 再丢 body；真因 "gemini 429 quota 耗尽" 经 3 跳稀释完全不可见 | MR-4 (第 9 次), **MR-13** error-chain-preserve-cause, INV-OBSERVABILITY-001 |
| `rsync_helper_set_e_regression_case.md` | V37.9.31 | caller 的 set -e | V37.9.27 retry helper `exit $rc` 透传 rsync 失败码 → 20 个 set -e caller 每天被杀 mid-script（**修复引入更深 silent failure**） | MR-4 (第 18 次新形态) |
| `dream_quota_blast_radius_case.md` | V37.2 | fallback 链 | Qwen3 宕机 → adapter fallback 触发 30+ Gemini 调用 → Gemini quota 耗尽 → HN 垃圾推送（单 job 失败造成跨 job 雪崩） | MR-4 (第 1 次), INV-QUOTA-001/PUSH-001 |

**自动批量放大子事件**：V37.9.50-h → V37.9.58-h，`inject_level_4_to_aligned_jobs.py` 自动给 8 ALIGNED jobs 加 `prompt += os.environ.get(...)` 但**未补 `import os`** → 8/8 重演 NameError → FAIL-OPEN 跳过 rule_check（**自动批量工具是 bug 放大器**）。立 **MR-18** auto-batch-injection-must-validate-runtime-semantics + INV-HEREDOC-IMPORT-001。

**监控自身静默子事件**：V37.9.58-h3 watchdog 因 bsd awk multibyte abort → 7 天只有 heartbeat 无 ALERT，**监控自己死了无人知**。立 **MR-19** monitor-must-self-alarm-on-silent-abort + INV-WATCHDOG-SELF-001 + INV-CRON-MONITOR-001（V37.9.60 framework 化到 7 个 governed scripts）。

**防御**：MR-11（log→stderr）+ MR-13（错误链保留 cause）+ MR-18（批量注入验证）+ MR-19（监控自报）+ INV-OBSERVABILITY-001 + INV-BACKUP-001（全局 scan `2>/dev/null \|\| true` 反模式）。

---

### Class D — 链式幻觉与编造（最危险）

> LLM 把错误数据/污染上下文当作事实，**编造合理化叙事**推送给用户。错误不是消失，而是被加工成看起来正常的内容。
> **原则 #23 链式幻觉**：LLM 链路中每一跳都会放大幻觉。前一个 LLM 的幻觉会被下游 LLM 当事实执行。

| 案例 | 版本 | 幻觉链路 | 真根因 | 关联 MR/INV |
|---|---|---|---|---|
| `dream_self_referential_hallucination_case.md` ⭐ | V37.8.6 | surrogate → json.dump 炸 → log 污染 cache → LLM 编造 | 错误日志 `HTTP 400 Bad JSON` 写 stdout → 被 `signals=$(llm_call)` 命令替换捕获 → 写 cache → Reduce LLM 把错误字样当外部信号 → 编造"Hugging Face 平台危机" | MR-4 (第 7 次), **MR-11** log→stderr（**一个 `>&2` 阻断整条幻觉链**）, INV-DREAM-003 |
| `pa_alert_contamination_case.md` | V37.4.3 | Gateway sessions.json 混入告警 → Qwen3 跨主题关联 | 告警推送写入 sessions.json 作 assistant role → proxy truncate 保留窗口内 → 用户新问题被当"告警跟进"，编造 macOS FDA 指令（答非所问） | MR-4 (第 6 次), INV-PA-001/002 |
| `kb_review_silent_degradation_case.md` | V37.5 | 6 issue 相互掩护 | shell `export` 顺序 / 硬编码源枚举漏新源 / LLM 失败机械 fallback 写容器标题残渣伪装成功 / status.json 永远写 `llm:true` | MR-4 (第 4 次), INV-REVIEW-001 |
| `pa_echo_chamber_case.md` | V37.1 | 三环反馈陷阱 | PA 看到用户 doubt → SOUL.md 无批判规则 → 默认顺从 → 用户引用 → 循环放大 | MR-4 (第 5 次), SOUL.md 规则 9 |

**编造项目动态子事件**：V37.9.56-h3 Evening 把今日高对齐 Top 5 paper 注入 prompt → LLM 推断"用户在做 OpenClaw 项目 + 必有动态" → 编造"OpenClaw 社区发布 v26"（v26 是内部 changelog 编号非社区版本，sources_used 无 [openclaw] 源）。修复：软化注入 + 反幻觉具体字面禁令。

**防御**：`hallucination_guards.py` 5 档累积式模板（V37.9.57，LEVEL_1~6，INV-HALLUCINATION-001 守 9 task 必须 import）+ SOUL.md 规则 9（批判性思考）/ 规则 10（告警不跟进）+ MR-11（防 log 污染 cache）+ `source_credibility.py`（V37.9.98，5 档出处可信度）。

---

### Class E — 运维遗漏与取证盲区

> 两个子机制：(1) **运维遗漏** — 代码正确但部署/注册/配置步骤被漏（声明态 ≠ 运行时态）；
> (2) **取证盲区** — 调试工具自身被屏蔽返回空内容，被误读为"正常"，导致超长潜伏。

| 案例 | 版本 | 遗漏项 / 盲区 | 真根因 | 关联 MR/INV |
|---|---|---|---|---|
| `kb_deep_dive_cron_unregistered_case.md` | V37.9.18 | crontab 未注册 | jobs_registry 有但没人手动 `crontab_safe add`；三层 silent 协同（preflight 只 grep 间隔漂移 + crontab_safe 不检 exit code + 35→35 count `<` 谎报 ✅） | MR-4 (第 16 次), **MR-17** declared-converge-via-machine, INV-CRON-005/006 |
| `heartbeat_md_pa_self_silencing_case.md` ⭐ | V37.8.16 | OpenClaw 保留文件 | PA write 工具把"任务完成"写进 `HEARTBEAT.md` → 13h 潜伏 → heartbeat 机制激活让 LLM 对所有消息回 `HEARTBEAT_OK` → Gateway 剥离 → 13h 完全静默 | MR-4 (第 12 次), **MR-15** reserved-files-not-LLM-writable, INV-HB-001 |
| `whatsapp_silent_death_case.md` | V37.8.13 | 告警链依赖失效主体 | Gateway 死亡 → WhatsApp 全断 9h；三放大器同时失效（quiet_alert 凌晨吞 CRITICAL + wa_keepalive 只写日志不告警 + restart.sh 不验证健康） | MR-4 (第 11 次), **MR-14** alert-path-not-depend-on-failing-subject, INV-WA-001/QUIET-001 |
| `finance_news_syndication_zombie_case.md` | V37.8.4 | HTTP 200 ≠ 健康 | X Syndication API 对停更账号返回 stale 快照（HTTP 200 + 可解析 HTML，但最新推文 2227/3364 天前） | **MR-10** (第 1 次), INV-X-001, 原则 #27 升级 |
| `zombie_detection_edge_case_closure.md` | V37.8.5 | 修复的修复 | V37.8.4 第三问"最小修复"浅化为"严格相等就够了"，但 `old==total` 放过 99% 老化，`total>0` 漏 0-tweet stub | MR-10 (第 2 次), INV-X-001 升级 [declaration, runtime] |
| `movespeed_exfat_silent_backup_failure_case.md` | V37.9.4 | 18 处反模式协同 | exfat fskit transient EPERM × 18 处复制粘贴 `2>/dev/null \|\| true` × 监控盲区 → 6 天静默 | MR-4 (第 14 次新形态：复制粘贴反模式系统性沉默), INV-BACKUP-001 |
| `movespeed_noowners_uid_mismatch_case.md` | V37.9.29 | 假说证伪 | chown 修复后 EPERM **100% 持平** — UID 错位是真 bug 但不是 EPERM 主因（**修对了但修错 bug**） | MR-4 (第 22 次新形态), INV-OWNERSHIP-001 |
| `movespeed_tcc_sandbox_blood_case.md` ⭐ | **V37.9.80** | 60 天 6 假说全证伪 | 真因 = macOS TCC Sandbox 拒绝 cron 派生进程访问外置卷（cron daemon 默认无 FDA）；V37.9.30 lsof/ACL **采集器自身被 sandbox 拒绝**返回空，被误读 normal/empty 6 周 | MR-4, MR-10（反向第 5 次教训）, **macos-cron-fda 候选元规则**, INV-MOVESPEED-TCC-001 |

**MOVESPEED 三部曲 = 数据驱动诊断方法论案例**：V37.9.14 取证 → V37.9.26 主动告警 → V37.9.27 主动修复 → V37.9.28 诊断工具 → V37.9.29 ownership 维度 → V37.9.30 ACL/handle/snapshot → **V37.9.80 `log show --predicate` 最后 1 跳真因** → V37.9.81 治理固化。**每次假说被证伪都不靠盲改代码而靠扩取证维度** — 这是与"看到失败就改代码"反模式的本质区别。`log show --last Xh --predicate 'eventMessage CONTAINS "X"'` 是 macOS 系统层取证的核武器（60 天 6 假说错都因从未跑过它）。

**防御**：convergence framework（V37.9.19+，声明态→运行时态机器同步）+ MR-15（保留文件）+ MR-17（declared converge）+ INV-X-001（账号健康 ≠ HTTP 200）+ JSONL incident_capture（stderr 区分 `[sandbox_denied]` vs 真空）+ `log show` 取证。

---

## 横向洞察（话语权 / ArXiv 素材）

### 1. 潜伏期分布 — silent failure 的"沉默时长"

| 潜伏期 | 案例 | 启示 |
|---|---|---|
| 60 天 | MOVESPEED TCC sandbox (V37.9.80) | 架构假设级 silent，整团队基于"已闭环"假设运作 |
| 13h / 9h / 7 天 | HEARTBEAT.md / Gateway 死亡 / watchdog abort | 监控/通道层 silent，比代码层更隐蔽 |
| 6 天 / 5 天 | exfat 备份 / Observer path | 复制粘贴反模式 / 部署拓扑假设 |
| 数小时~2 天 | kb_deep_dive cron / kb_evening 502 | 运维遗漏 / 错误稀释 |

**洞察**：潜伏期与"发现渠道"强相关——代码层 bug 单测能抓（潜伏短），但**架构假设 / 部署拓扑 / 监控自身 / 取证盲区**类只有真实生产 + 用户视角能抓（潜伏长）。潜伏期是 silent failure 危险度的直接度量。

### 2. 发现渠道分布 — 谁最终抓到了真问题

| 渠道 | 占比（定性） | 典型案例 |
|---|---|---|
| **用户视角**（WhatsApp 实际收推送 / 周一观察） | 最高（~70%） | drift 噪声告警、Qwen-BIM 重复、kb_evening 502、HEARTBEAT.md 静默 |
| **Mac Mini 真跑**（dev 全绿 + target 暴露） | 高 | 所有 Class A quirk、Observer path、top_alignment_picker 0 picks |
| **Observer / governance 自观察** | 上升中 | V37.9.92（Observer 自己 critique 驱动自我修复）、governance summary 吞 error |
| **单测 / preflight** | 低（对 silent failure） | 单测验证组件内部，silent failure 在组件接缝处 |

**洞察**：**单测全绿 + 治理全绿 + preflight 全绿，都不能替代用户视角观察**（原则 #13/#15）。这是把"用户感知 = 一等可观测信号"制度化的根据（原则 #32 每周观察）。

### 3. 三层根因模式 — 触发器 / 放大器 / 掩护者

几乎每个血案都有这个结构（原则 #26 异常分析宪法）：

```
触发器 (Trigger)   外部事件引爆           例: exfat transient EPERM / LLM 偶发漏行 / surrogate 字节
   ↓
放大器 (Amplifier) 架构缺陷让影响扩散     例: 18 处复制粘贴反模式 / 位置解析级联 / log 污染 cache
   ↓
掩护者 (Concealer) 缺失让问题潜伏到用户   例: 2>/dev/null 吞 stderr / status.json 谎报 ok / 监控自身 silent
```

**洞察**：修复只动触发器 = 治标；真正的 framework 级修复要同时消除**放大器**（抽 helper / key-based parser）和**掩护者**（fail-loud / 取证 / 监控自报）。这是"为什么 18 处反模式比 1 个 bug 更难修"的根据。

### 4. 防御演进 — 单点修复 → 元规则 → MRD 扫描器（三步跃迁）

silent failure 防御能力的成熟度，体现在它停留在哪一步：

```
第 1 步  单点修复       修这一个 bug                          (易回归)
第 2 步  立元规则 MR-N   把教训升级为跨案例硬规则               (靠记忆遵守)
第 3 步  MRD 扫描器      把元规则机器化为 CI/governance 主动检测  (回归即被拦)
```

**实证**：MR-14/MR-15 经历"单点 → 元规则 → MRD"完整跃迁（V37.8.17）；INV-CROSS-OS-001 / INV-PATH-CONSISTENCY-001 / INV-CROSS-ENV-PATH-001 / heredoc_import_scanner / cron_monitor_scanner 都是第 3 步产物。**只停在第 1 步的教训必然重演**（V37.9.50-h → V37.9.58-h 同款 bug 8 天后重演，正是因为第一次没立元规则 + 没加扫描器）。

### 5. MR-4 *silent-failure-is-a-bug* 演出史（完整时间线）

| # | 版本 | 形态 |
|---|---|---|
| 1 | V37.2 | Dream quota 爆炸跨 job 雪崩 |
| 2 | V37.4 | Dream Map 预算溢出 + cache key 漂移 |
| 3 | V37.6 | content blocks `str()` 写进 KB |
| 4 | V37.5 | kb_review 机械 fallback 伪装成功 |
| 5 | V37.6/V37.7 | run_discussions 第 16 处漏迁移 helper |
| 6 | V37.4.3 | PA 告警污染编造 FDA 指令 |
| 7 | V37.8.6 | Dream log 污染 cache → 编造 HF 危机 |
| 8 | V37.8.7 | ontology_sources 位置解析级联 |
| 9 | V37.8.10 | kb_evening 502 三层错误链稀释 |
| 10 | V37.8.11 | drift 噪声告警（expected-behavior 被错分类为 error） |
| 11 | V37.8.13 | Gateway 死亡 9h 三放大器同失效 |
| 12 | V37.8.16 | HEARTBEAT.md PA 自残 13h 静默 |
| 14 | V37.9.4 | exfat 18 处复制粘贴反模式协同沉默 |
| 15 | V37.9.5 | workspace .md 数据接缝盲区 |
| 16 | V37.9.18 | kb_deep_dive cron 三层 silent 协同 |
| 18 | V37.9.31 | rsync helper exit 透传杀 caller（修复引入更深 silent） |
| 22 | V37.9.29 | noowners UID 修对了但修错 bug |
| 24 | V37.9.31 | rsync exit code 回归 |
| 25 | V37.9.34 | bash 内嵌 ASCII 双引号字符串提前终止 |
| 27 | V37.9.73 | split 测试形态 vs 生产 caller 形态 |
| 28 | V37.9.73 | （同案，分片合并算法） |
| 新形态 | V37.9.75 | llm_call 把"客观异常 partial"当正常短答返回 |
| 新形态 | V37.9.87 | daily_observer 双写污染 score_history |
| 新形态 | V37.9.92/93 | Observer 自身 5 天 silent + sampling artifact |

> 编号有跳号（13/17/19-21/23/26 散见于 hotfix），后期形态以"新形态"记。这本身是个数据点：**silent failure 不是会被根除的 bug，而是会不断以新形态出现的 bug 类**——这正是它需要 MR-4 这条元规则而非单个 INV 的根本原因。

---

## 跨类元规则索引（21 条）

| 元规则 | 名称 | 案例触发 | 立案版本 | 派生 INV |
|---|---|---|---|---|
| MR-1~MR-3 | declaration/enforcement/propagation | 前序系统规则 | V36-V37.1 | 多个 |
| **MR-4** ⭐ | silent-failure-is-a-bug | 全部 Class C/D/E 主战场 | V37.1 | ~21 个 INV |
| MR-5 | health-fields-have-freshness-guarantee | security_score 无时间戳 | V37.1 | INV-HEALTH-001 |
| MR-6 | critical-invariants-need-depth (≥2 层) | governance 自身单层假绿 | V36.3 | INV-LAYER-001 |
| **MR-7** | governance-execution-is-self-observable | governance_silent_error | V37.3 | INV-GOV-001 |
| **MR-8** | copy-paste-is-a-bug-class | 14 cron 直写 + 8 jobs heredoc os 漏 | V37.7 | INV-SRC-001, INV-HEREDOC-IMPORT-001, INV-PATH-CONSISTENCY-001 |
| **MR-9** | state-writes-go-through-helper-not-raw-redirect | kb_content_and_sources_dedup | V37.7 | INV-SRC-001 |
| **MR-10** | understand-before-fix（修复前三问） | preflight_cascading_fix / 2 X 僵尸 / V37.9.80 反向第 5 次 | V37.8.3 | (元规则级，无 INV) |
| **MR-11** | shell-function-output-must-go-to-stderr | dream_self_referential_hallucination | V37.8.8 | INV-DREAM-003 |
| **MR-12** | llm-output-parser-must-be-key-based-not-positional | ontology_sources_positional_parser | V37.8.8 | INV-ONTOLOGY-001 |
| **MR-13** | error-chain-must-preserve-upstream-cause-across-layers | kb_evening_fallback_quota_chain | V37.8.10 | INV-OBSERVABILITY-001 |
| **MR-14** | alert-path-must-not-depend-on-failing-subject | whatsapp_silent_death | V37.8.13 | INV-WA-001 |
| **MR-15** | reserved-files-must-not-be-writable-by-llm | heartbeat_md_pa_self_silencing | V37.8.16 | INV-HB-001, INV-CROSS-ENV-PATH-001 |
| **MR-16** | security-dimensions-governed-by-invariants | security_score 与 governance 双轨统一 | V37.9 | INV-SEC-001 |
| **MR-17** | declared-state-must-converge-to-runtime-via-machine-not-memory | kb_deep_dive_cron_unregistered | V37.9.19 | INV-CONVERGENCE-{CRON/PROVIDERS/OPENCLAW/KB/INTEGRATION/SERVICES}-001（6 个） |
| **MR-18** | auto-batch-injection-must-validate-runtime-semantics | V37.9.50-h → V37.9.58-h 8 jobs | V37.9.58-h2 | INV-HEREDOC-IMPORT-001 |
| **MR-19** | monitor-must-self-alarm-on-silent-abort | watchdog 自身 silent abort 7 天 | V37.9.58-h3 | INV-WATCHDOG-SELF-001, INV-CRON-MONITOR-001 |
| **MR-20** | framework-level-fix-must-validate-in-target-environment | V37.9.58-h3→h4 dev 全绿但 Mac Mini 才暴露 | V37.9.86 | (元规则级) |
| **MR-21** | monitor-coverage-must-track-registry | V37.9.59 watchdog 覆盖率 47%→74% | V37.9.86 | (元规则级) |

> **候选元规则**：`macos-cron-derived-processes-need-fda`（movespeed_tcc_sandbox，未获编号，仍候选）。
> **史料修正**：旧 catalog 误记"MR-16 候选 = macos-cron-fda"——实际 MR-16 = security-dimensions-governed-by-invariants（V37.9），macos-fda 从未获编号。

---

## 防御策略汇总

| 策略 | 实施版本 | 防御类别 |
|---|---|---|
| **声明式 governance**（v3.52: 85 inv + 21 MR + 15 MRD scanners + 790 checks） | V37.1 → V37.9.99 | A/B/C/D/E |
| **每日 governance audit cron**（07:00 自动 + 失败告警） | V37.1 | 全部 |
| **MRD 主动扫描器**（cross_os / path_consistency / cross_env_path / heredoc_import / cron_monitor / ...） | V37.9.60+ | A/B/C |
| **fail-fast LLM cron**（17/21 ALIGNED jobs，[SYSTEM_ALERT] + exit 1，不机械 fallback） | V37.5 → V37.9.62 | C/D |
| **告警双通道独立**（notify.sh WhatsApp + Discord 不共享失效主体） | V37.8.13 | E |
| **预防层元规则**（MR-15 保留文件 / MR-18 自动注入扫描 / MR-19 监控自报） | V37.8.16+ | C/D/E |
| **取证机制**（JSONL incident_capture + `log show` + analyzer 数据驱动诊断） | V37.9.14 → V37.9.81 | E |
| **反幻觉守卫**（hallucination_guards.py 5 档 + source_credibility 5 档 + SOUL.md 规则 9/10） | V37.9.57/98 | D |
| **反向 sabotage 守卫验证**（每个新 INV 必须验证 sabotage 后真 fail） | V37.4+ | 全部 |
| **三层 FAIL-OPEN**（dev silent pass / 损坏数据 skip / IO 失败 silent pass） | V37.9.18+ | E（governance 不能在 dev 误报缺生产数据） |
| **每周用户视角观察 30min**（原则 #32，周一开工第一件事） | V37.8.11 | 全部（用户感知缺口） |

---

## 使用指南

### 新血案来时

1. 查 5 大机制类别哪个最贴近（A 环境 / B 假设 / C 吞噬 / D 幻觉 / E 运维取证）。
2. 同类案例"真根因"列哪个最像？
3. 查关联 MR/INV — 已有元规则吗？已立守卫吗？
4. 都不像 → **新类别**或**新元规则候选**，登记到收工承诺。
5. 修复后必须：立 INV（declaration + runtime ≥2 层，MR-6）+ 反向 sabotage 验证 + 写 `ontology/docs/cases/` + **更新本目录索引**。

### 新功能上线时（5 类别 checklist）

- **A**: 跨 OS 兼容性在 Mac Mini 真跑测了吗？bash 3.2 / BSD 工具差异考虑了吗？
- **B**: 跨脚本/跨模块/跨环境假设有显式 contract 吗？测试输入镜像生产 caller 形态吗？
- **C**: 重复代码 ≥3 处了吗？该抽 helper 吗？错误链每跳保留 cause 吗？监控自身会 silent 吗？
- **D**: LLM 看到的上下文有污染源吗？输出有 grounding allow-list 吗？反幻觉守卫注入了吗？
- **E**: 部署/注册步骤是机器同步还是靠记忆？取证维度足够吗？调试工具自己会被屏蔽吗？

### 新人入项目

按 **D → C → E → B → A** 顺序读案例（先读最危险/最隐蔽的链式幻觉与吞噬，再到环境 quirk），理解 ~28 次 MR-4 演出 = V37 演化的关键节点。

---

## 索引维护契约

- **添加新案例**：TL;DR 统计"案例数" +1，所属机制类别表格新增一行，关联 MR/INV 同步更新，MR-4 演出史表追加（如适用）。
- **新 MR 立案**：跨类元规则索引表新增一行（案例触发 + 立案版本 + 派生 INV）。
- **governance v3.X 升级**：TL;DR 统计同步刷新（inv / MR / MRD scanner / checks 总数），头部版本号刷新。
- **元规则状态变化**（候选 → 立案 → 真激活）：跨类元规则索引表的 MR 行状态更新。
- **唯一权威**：本文件是 canonical，禁止再创建分叉副本（MR-8）。README / governance / scanner 的引用统一指向 `ontology/docs/failure_modes_catalog.md`。

---

## 参考

- `ontology/governance_ontology.yaml` — 85 invariants + 21 MR + 15 MRD scanners 完整声明（v3.52）
- `ontology/docs/cases/*.md` — 22 个血案完整因果链架构图 + 三层根因 + 时间线
- `ontology/docs/reflections/2026-05-13_complexity_bug_taxonomy.md` — 复杂性 bug 分类与治理反思（本目录的设计源之一）
- `CLAUDE.md` 变更历史表 — V37.1 → V37.9.99 完整版本演化
- `docs/articles/audit_is_regression_not_prevention.md` — 立场文章：audit 不是 prevention 是 regression engine
- `docs/articles/seven_failure_scenarios.md` — 7 场景故障注入实验
- `docs/strategic_review_20260403.md` — 2026-04-03 导师战略复盘

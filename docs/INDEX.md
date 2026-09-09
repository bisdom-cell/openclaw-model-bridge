# 📚 文档导航树（INDEX.md）

> **何时读什么文档** — 2026-09-09 刷新（v37.9.352）。计数会随版本漂移：文档数以 `find . -name '*.md' -not -path './.git/*' | wc -l` 为准（本次 133），其余数字以 README 徽章为准。

本项目约 **130+ 个 markdown 文档**，按用途分 7 类。本 INDEX 帮你按场景快速定位。

---

## 🚪 第一次接触本项目？

按这个顺序读：

1. **[README.md](../README.md)** — 项目入口，5 分钟了解全貌（四平面架构、13-provider 矩阵、quickstart）
2. **[docs/FEATURES.md](FEATURES.md)** — 系统特性一览表（一张表看完所有能力）
3. **[docs/GUIDE.md](GUIDE.md)** — 完整双语集成指南 + 27 条生产教训
4. **[CLAUDE.md](../CLAUDE.md)** — 项目背景 + 第 0 号宪法 + 36 条工作原则 + 近期 changelog（**最长但最重要**，AI 协作必读；V37.9.239 及更早 changelog 见 `docs/changelog.md`）

---

## 🎯 按角色查阅

### 我是 **新接入的开发者**
- [README.md](../README.md) → quickstart → 跑通 demo
- [examples/minimal_runtime/](../examples/minimal_runtime/README.md) — 10 分钟最小 Core Runtime demo（无网络无 key）
- [docs/GUIDE.md](GUIDE.md) → 27 lessons learned
- [docs/openclaw_architecture.md](openclaw_architecture.md) → OpenClaw 上游架构（v2026.3.x 快照，头注指向现状）

### 我是 **运维 / SRE**
- [docs/config.md](config.md) — 系统配置 + 历史踩坑（每次开工必读）
- [docs/gateway_upgrade_eval_v2026.4.md](gateway_upgrade_eval_v2026.4.md) — Gateway 升级评估（十次评估，当前 hold 4.27；判据与 SOP 在第七节 / 最新评估节）
- [ROLLBACK.md](../ROLLBACK.md) — 代码回滚（git revert + auto_deploy 同步 + restart.sh；2026-09-09 重写）
- [docs/importyeti_sop.md](importyeti_sop.md) — 货代 ImportYeti SOP
- [docs/baileys_ban_risk_assessment.md](baileys_ban_risk_assessment.md) — WhatsApp 重连封禁风险评估
- [windows/README.md](../windows/README.md) — Windows E 盘每日镜像安装 runbook（V37.9.335）

### 我是 **架构师 / 评审者**
- [docs/technical_charter_20260705.md](technical_charter_20260705.md) — 中长期技术纲领（2026H2→2028，双轨战略 + 8 判据门控）
- [docs/charter_execution_plan_20260705.md](charter_execution_plan_20260705.md) — 纲领执行计划 + 每季度五项复核协议
- [docs/strategic_review_20260403.md](strategic_review_20260403.md) — 导师战略复盘（2026-04 时点，V1/V2 路标已完成）
- [docs/compatibility_matrix.md](compatibility_matrix.md) — Provider 兼容性矩阵（三张机器表 + 验证四档）
- [docs/security_boundaries.md](security_boundaries.md) — 8 节安全边界分析
- [docs/memory_plane.md](memory_plane.md) — Memory Plane 架构
- [docs/complexity_budget.md](complexity_budget.md) — 复杂度预算账本（日落法操作化）
- [docs/pa_coupling_inventory.md](pa_coupling_inventory.md) — PA 耦合机器化盘点
- [docs/second_instance_poc_selection_20260707.md](second_instance_poc_selection_20260707.md) — 第二实例 PUSH-only 选型

### 我是 **要扩展系统的开发者**
- [docs/provider_plugin_guide.md](provider_plugin_guide.md) — 60s 添加新 Provider（`providers.d/` YAML/Python 插件）
- [docs/tool_policy_plugin_guide.md](tool_policy_plugin_guide.md) — Tool Policy Plugin（`policies.d/`，chunk 1 observability）
- [docs/ontology_engine_extension_guide.md](ontology_engine_extension_guide.md) — `pip install openclaw-ontology-engine` 后写自己的 YAML 接入治理
- [docs/ontology_engine_packaging.md](ontology_engine_packaging.md) — 引擎包化记录
- [examples/minimal_consumer/](../examples/minimal_consumer/README.md) / [examples/external_dogfood/](../examples/external_dogfood/README.md) — 引擎消费方 demo（WeatherBot / 仓库外 wheel dogfood）

### 我是 **PA（OpenClaw runtime）**
- [SOUL.md](../SOUL.md) — 灵魂文件 + 行为规则（最高优先级 system prompt）
- [ops_soul.md](../ops_soul.md) — Ops 子 agent 运维身份

### 我是 **Claude Code（AI 协作）**
- [CLAUDE.md](../CLAUDE.md) — 36 条原则（开工必读 #1~#3，收工必读 #9）+ 研究攻关 #1 宪法块 + 近期 changelog
- [docs/technical_charter_20260705.md](technical_charter_20260705.md) — "要不要做 X" 以纲领 §8 判据门控为准

### 我是 **研究者（论文 / bench）**
- [docs/paper/silent_failures_taxonomy/](paper/silent_failures_taxonomy/) — 论文 #1（arXiv:2606.14589 已发表；IEEE Software in-review；ISSRE 已拒稿 → 期刊优先）
- [docs/paper/mechanizing_the_eye/](paper/mechanizing_the_eye/) — 论文 #2 LLM-Observer（投稿包完成，arXiv 提交待作者）
- [docs/llm_observer_design.md](llm_observer_design.md) — 机械化人眼设计文档（Stage 0-6 + §9.1 flip 预注册 + §9.2 数据口径预注册）
- [docs/llm_observer_ground_truth.yaml](llm_observer_ground_truth.yaml) — 24 incident 带标注验证集
- [docs/fail_plausible_bench.md](fail_plausible_bench.md) / [docs/llm_observer_scorecard.md](llm_observer_scorecard.md) — 社区可跑 bench + scorecard

---

## 📊 按主题查阅

### 证据 / 性能 / 可靠性
- [docs/slo_benchmark_report.md](slo_benchmark_report.md) — SLO 基准报告（仓库副本为 V37.9.303 快照，最新在 Mac Mini 重生成）
- [docs/reliability_bench_report.md](reliability_bench_report.md) — 可靠性 17 场景 103 检查
- [docs/resilience_report.md](resilience_report.md) — 故障注入 + Recovery Time
- [docs/golden_trace.json](golden_trace.json) — 一键 demo 真实记录

### 治理 / 本体 / 血案档案
- [ontology/CONSTITUTION.md](../ontology/CONSTITUTION.md) — 本体宪法七条
- [ontology/README.md](../ontology/README.md) — Ontology 子项目入口（91 不变式 / 23 元规则 / ONTOLOGY_MODE=on）
- [ontology/docs/failure_modes_catalog.md](../ontology/docs/failure_modes_catalog.md) — 静默失败全谱系（canonical）
- [ontology/docs/cases/](../ontology/docs/cases/) — **28 篇血案档案**（按文件名自述版本，最新在前；完整清单以目录为准）。代表性入口：
  - `pa_alert_contamination_case.md` — V37.4.3 告警污染对话（六层根因）
  - `heartbeat_md_pa_self_silencing_case.md` — V37.8.16 PA 自残 13h 静默
  - `reasoning_model_primary_breaks_batch_jobs_case.md` — V37.9.220 reasoning primary 拖垮批量 job
  - `weixin_contexttoken_push_blocked_case.md` — V37.9.179/182 微信通道机制
  - `why_so_many_incidents_2026_06_05_reflection.md` — 日落法起源反思
  - `dream_quota_blast_radius_case.md` — V37.2 Dream 配额爆炸
  - `whatsapp_silent_death_case.md` — V37.8.13 Gateway 9h 静默

### 工业 AI / Ontology 思想
- [ontology/docs/why_ontology.md](../ontology/docs/why_ontology.md) (EN) / [why_ontology_zh.md](../ontology/docs/why_ontology_zh.md) (中文)
- [ontology/docs/meta_governance_en.md](../ontology/docs/meta_governance_en.md) / [meta_governance_zh.md](../ontology/docs/meta_governance_zh.md) — 元治理
- [ontology/docs/architecture/](../ontology/docs/architecture/) — Industrial AI Paradigm / Neuro-Symbolic / Target Architecture / 六域参考模型
- [ontology/docs/foundations/](../ontology/docs/foundations/) — BFO/DOLCE/UFO 流派对比
- [ontology/docs/enterprise/](../ontology/docs/enterprise/) — Governance / Supply Chain Ontology

### 话语权输出（已发表文章，正文冻结、头注指向现状）
- [docs/articles/why_control_plane.md](articles/why_control_plane.md) / [why_control_plane_zh.md](articles/why_control_plane_zh.md) — Why Agent Systems Need a Control Plane
- [docs/articles/why_runtime_not_wrapper.md](articles/why_runtime_not_wrapper.md) / [_zh](articles/why_runtime_not_wrapper_zh.md) — 为什么是 runtime 不是 wrapper
- [docs/articles/why_control_plane_is_convergence_engine.md](articles/why_control_plane_is_convergence_engine.md)（+ en / devto）— 控制平面即收敛引擎
- [docs/articles/audit_is_regression_not_prevention.md](articles/audit_is_regression_not_prevention.md) — 审计是回归引擎不是预防工具
- [docs/articles/seven_failure_scenarios.md](articles/seven_failure_scenarios.md) — 七个失败场景剖析
- [docs/articles/when_errors_become_narratives_zh.md](articles/when_errors_become_narratives_zh.md) — 论文 #1 中文解读
- [docs/articles/ai_partnership_first_principles_zh.md](articles/ai_partnership_first_principles_zh.md) — AI 协作第一性原理
- [docs/articles/expert_escalation_design.md](articles/expert_escalation_design.md) — expert_escalate 设计
- [docs/articles/zhihu_provider_compatibility.md](articles/zhihu_provider_compatibility.md) — Provider 兼容性话题（知乎）

---

## 🗄️ 已归档 / 时点文档

不再维护但保留作为历史档案（内容为写作当时快照）：

- [docs/changelog.md](changelog.md) — V27 ~ V37.9.239 完整 changelog（301 版，三轮归档）
- [docs/archive/](archive/) — 早期知乎文章历史版本、数据清洗 PoC Phase 0
- [docs/awesome_openclaw_usecase.md](awesome_openclaw_usecase.md) — awesome-openclaw 投稿稿（V37.8.13 快照）
- [docs/ai_leaders_source_alternatives.md](ai_leaders_source_alternatives.md) — ai_leaders 信息源调研（V37.9.102）
- [docs/push_path_loss_surface_audit_2026_06_15.md](push_path_loss_surface_audit_2026_06_15.md) / [docs/llm_cron_fail_fast_audit.md](llm_cron_fail_fast_audit.md) — 一次性审计记录
- [IMPROVEMENTS.md](../IMPROVEMENTS.md) — 旧版改进归档

---

## 🔍 查找小贴士

```bash
grep -rn "关键词" --include="*.md" .
```

```bash
find . -name "*.md" -mtime -7 -not -path "./.git/*" | xargs ls -lt
```

```bash
git log -1 --format="%ai" -- docs/某文档.md
```

---

## 📅 维护节奏

| 类别 | 维护频率 | 维护者 |
|------|---------|-------|
| README / FEATURES / config 头部徽章 | 每次收工（`gen_readme_badges.py --write` 机器同步） | Claude Code |
| README / CLAUDE / SOUL prose | 每个 PR | Claude Code |
| 案例档案 (cases/) | 每次血案 | Claude Code 撰写 + 用户确认 |
| 纲领 / 执行计划 | 季度复核（执行计划 Part 2 协议） | 用户主导 |
| 已发表文章 | 正文冻结，只加头注 | — |
| 已归档 | 不维护 | — |

---

> **本 INDEX 由 V37.8.13 引入，2026-09-09（v37.9.352）全面刷新。** 目录数字前缀重组（00-getting-started / 01-architecture …）未执行，按日落法不再计划。

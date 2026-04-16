# 📚 文档导航树（INDEX.md）

> **何时读什么文档** — V37.8.13 (2026-04-16) 现状

本项目共 **67 个 markdown 文档**，按用途分 6 类。本 INDEX 帮你按场景快速定位。

---

## 🚪 第一次接触本项目？

按这个顺序读：

1. **[README.md](../README.md)** — 项目入口，5 分钟了解全貌（架构图、provider 矩阵、quickstart）
2. **[docs/FEATURES.md](FEATURES.md)** — 系统特性一览表（一张表看完所有能力）
3. **[docs/GUIDE.md](GUIDE.md)** — 完整双语集成指南 + 26 条生产教训
4. **[CLAUDE.md](../CLAUDE.md)** — 项目背景 + 32 条工作原则 + 完整 changelog（**最长但最重要**，AI 协作必读）

---

## 🎯 按角色查阅

### 我是 **新接入的开发者**
- [README.md](../README.md) → quickstart → 跑通 demo
- [docs/GUIDE.md](GUIDE.md) → 26 lessons learned
- [docs/openclaw_architecture.md](openclaw_architecture.md) → OpenClaw 上游架构

### 我是 **运维 / SRE**
- [docs/config.md](config.md) — 系统配置 + 历史踩坑（每次开工必读）
- [docs/importyeti_sop.md](importyeti_sop.md) — 货代 ImportYeti SOP
- [docs/gateway_upgrade_eval_v2026.4.md](gateway_upgrade_eval_v2026.4.md) — Gateway 升级评估
- [ROLLBACK.md](../ROLLBACK.md) — 紧急回滚（V27 时代档案，V37 后基本不用）

### 我是 **架构师 / 评审者**
- [docs/strategic_review_20260403.md](strategic_review_20260403.md) — 战略复盘 + V1/V2/V3 路标
- [docs/compatibility_matrix.md](compatibility_matrix.md) — 7 Provider 兼容性矩阵
- [docs/security_boundaries.md](security_boundaries.md) — 8 节安全边界分析
- [docs/memory_plane.md](memory_plane.md) — Memory Plane v1 架构

### 我是 **要扩展系统的开发者**
- [docs/provider_plugin_guide.md](provider_plugin_guide.md) — 60s 添加新 Provider（V37 落地）
- [docs/compatibility_matrix.md](compatibility_matrix.md) — 验证规范

### 我是 **PA（OpenClaw runtime）**
- [SOUL.md](../SOUL.md) — 灵魂文件 + 10 条行为规则（最高优先级 system prompt）
- [ops_soul.md](../ops_soul.md) — Ops 子 agent 运维身份

### 我是 **Claude Code（AI 协作）**
- [CLAUDE.md](../CLAUDE.md) — 32 条原则（开工必读 #1~#3，收工必读 #9）+ 完整 changelog
- [docs/strategic_review_20260403.md](strategic_review_20260403.md) — 战略定位

---

## 📊 按主题查阅

### 证据 / 性能 / 可靠性
- [docs/slo_benchmark_report.md](slo_benchmark_report.md) — SLO 基准实验（5/5 PASS, p95=459ms）
- [docs/reliability_bench_report.md](reliability_bench_report.md) — 可靠性 7 场景 47 检查
- [docs/resilience_report.md](resilience_report.md) — 故障注入 + Recovery Time
- [docs/golden_trace.json](golden_trace.json) — 一键 demo 真实记录

### 治理 / 本体 / 血案档案
- [ontology/CONSTITUTION.md](../ontology/CONSTITUTION.md) — 本体宪法 6 条
- [ontology/README.md](../ontology/README.md) — Ontology 子项目入口
- [ontology/docs/cases/](../ontology/docs/cases/) — **13 篇血案档案**（V37.3-V37.8.13），按时间倒序：
  - `whatsapp_silent_death_case.md` — V37.8.13 Gateway 9h 静默
  - `kb_evening_fallback_quota_chain_case.md` — V37.8.10/11 双血案
  - `dream_self_referential_hallucination_case.md` — V37.8.6 Dream 自引用幻觉
  - `ontology_sources_positional_parser_cascade_case.md` — V37.8.7 LLM 解析级联
  - `zombie_detection_edge_case_closure.md` — V37.8.5 僵尸检测边缘
  - `finance_news_syndication_zombie_case.md` — V37.8.4 X 僵尸账号
  - `preflight_cascading_fix_case.md` — V37.8.3 连锁修复教训
  - `pa_alert_contamination_case.md` — V37.4.3 告警污染对话
  - `dream_quota_blast_radius_case.md` — V37.2 Dream 配额爆炸
  - `governance_silent_error_case.md` — V37.3 治理自观察
  - `dream_map_budget_overflow_case.md` — V37.4 Dream 预算
  - `kb_review_silent_degradation_case.md` — V37.5 kb_review 6-bug
  - `kb_content_and_sources_dedup_case.md` — V37.6 KB 三合一

### 工业 AI / Ontology 思想
- [ontology/docs/why_ontology.md](../ontology/docs/why_ontology.md) (EN)
- [ontology/docs/why_ontology_zh.md](../ontology/docs/why_ontology_zh.md) (中文)
- [ontology/docs/architecture/](../ontology/docs/architecture/) — Industrial AI Paradigm / Neuro-Symbolic / Target Architecture
- [ontology/docs/foundations/](../ontology/docs/foundations/) — BFO/DOLCE/UFO 流派对比
- [ontology/docs/enterprise/](../ontology/docs/enterprise/) — Governance / Supply Chain Ontology

### 话语权输出（已发布文章）
- [docs/articles/why_control_plane.md](articles/why_control_plane.md) — Why Agent Systems Need a Control Plane (EN, dev.to)
- [docs/articles/why_control_plane_zh.md](articles/why_control_plane_zh.md) — 中文版（知乎）
- [docs/articles/seven_failure_scenarios.md](articles/seven_failure_scenarios.md) — V37.8.13 七个失败场景剖析
- [docs/articles/zhihu_provider_compatibility.md](articles/zhihu_provider_compatibility.md) — Provider 兼容性话题

---

## 🗄️ 已归档文档

不再维护但保留作为历史档案：

- [docs/changelog.md](changelog.md) — V27-V36 changelog（V37 后转入 CLAUDE.md）
- [docs/archive/zhihu_*.md](archive/) — 早期知乎文章历史版本
- [docs/archive/data_clean_poc/INTEGRATION.md](archive/data_clean_poc/INTEGRATION.md) — 数据清洗 PoC Phase 0
- [ROLLBACK.md](../ROLLBACK.md) — V27 紧急回滚指南
- [IMPROVEMENTS.md](../IMPROVEMENTS.md) — 旧版改进归档

---

## 🔍 查找小贴士

```bash
# 全文搜索文档
grep -rn "关键词" --include="*.md" .

# 找最近改动的文档
find . -name "*.md" -mtime -7 -not -path "./.git/*" | xargs ls -lt

# 看某文档最后维护时间
git log -1 --format="%ai" -- docs/某文档.md
```

---

## 📅 维护节奏

| 类别 | 维护频率 | 维护者 |
|------|---------|-------|
| README / CLAUDE / SOUL | 每个 PR | Claude Code |
| 案例档案 (cases/) | 每次血案 | Claude Code 撰写 + 用户确认 |
| 战略类（strategic_review）| 季度 | 用户主导 |
| 已归档 | 不维护 | — |

---

> **本 INDEX 由 V37.8.13 引入。后续目录重组（阶段 2）将进一步按 00-getting-started / 01-architecture / 02-operations / 03-evidence / 04-extension / 05-articles 数字前缀分类。**

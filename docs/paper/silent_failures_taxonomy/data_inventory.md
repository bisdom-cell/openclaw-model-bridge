# Data Inventory — Silent Failures Taxonomy Paper

> 论文 `draft.md` 中每一个数字/比例/日期的仓库内溯源。
> 目的：(1) 审稿/读者可验证 (2) 防 doc drift（MR-8 单一真理源精神）(3) 投稿前最终校对清单。
> 快照基准：2026-06-11 / `VERSION 0.37.9.70` / governance v3.56 / CLAUDE.md V37.9.138。
> 维护契约：governance/测试/案例数字变化后，本表与 draft.md 必须同步刷新（仅在投稿前重新对表，平时不追）。

## 核心计数

| 论文数字 | 值 | 仓库来源 | 验证命令/位置 |
|---|---|---|---|
| Documented incident case studies | **22** | `ontology/docs/cases/` 25 文件 − 3 非失败（`openclaw_as_ontology.md` 架构探讨 / `cron_line_full_comparison_audit_2026_06_02.md` 决策文档 / `why_so_many_incidents_2026_06_05_reflection.md` 反思文档） | `ls ontology/docs/cases/*.md \| wc -l` |
| MR-4 manifestations | **≥28** | `ontology/docs/failure_modes_catalog.md` TL;DR + §5 演出史表（编号有跳号；6/2 后另有新形态未计入，保守取 ≥28） | catalog 第 31 行 |
| Incident window | **2026-04-09 → 2026-06-02（~8 周）** | 最早案例 `pa_echo_chamber_case.md`（V37.1, 4/9）；catalog 截止 6/2（V37.9.99） | CLAUDE.md changelog |
| Continuous production since | **2026-03 上旬** | V27 (2026-03-10) 起 system crontab 生产调度 | CLAUDE.md 版本表 |
| Unit tests / suites | **4,286 / 121** | `status.json quality.test_count / test_suites`（full_regression 2026-06-11 自动写入） | `python3 -c "...quality..."` |
| Governance invariants | **90** | `status.json quality.governance_invariants` = 90/90 | governance_checker 运行时权威计数 |
| Declared checks | **827** | V37.9.136 changelog（826→827）；audit_metadata.total_checks | `ontology/governance_ontology.yaml` |
| Meta-rules | **23** | MR-1~MR-23（MR-22/23 V37.9.117 立） | `grep -oE 'MR-[0-9]+' ontology/governance_ontology.yaml \| sort -u` |
| MRD scanners | **14** | **governance_ontology.yaml meta_rule_discovery 条目数 = 权威源**（gen_readme_badges 同源）。⚠️ catalog V37.9.99 曾写 15 系笔误，2026-06-11 投稿前对表抓出并已全局修正（draft/LaTeX/README/catalog 共 14 处）— data_inventory 机制的第一次实战命中 | `grep -cE "^\s+- id: MRD-" ontology/governance_ontology.yaml` |
| Scheduled jobs | **~40** | `jobs_registry.yaml`（check_registry "40 jobs validated"，V37.9.135） | `python3 check_registry.py` |
| LLM providers | **8** | 7 built-in + doubao plugin（V37.9.52） | `python3 providers.py --json` |
| Supervised services | **3** | `services_registry.yaml`（adapter/proxy/gateway） | V37.9.25 |
| KB notes | **~1,100+** | status.json（V37.9.136 提及 1103 KB；论文用 "~1,100+"） | kb_search.sh --summary |

## 回填审计（§5.6）

| 论文数字 | 值 | 来源 |
|---|---|---|
| Ex-ante prevention | **0/15 = 0%** | `ontology/docs/audit_coverage_retrospective.md` 统计段 |
| Partial early warning | 2/15 = 13% | 同上 |
| Ex-post regression blocking | **13/15 = 87%** | 同上（注意：文档首段写 86%，统计表写 87%=13/15；论文采用统计表） |
| Blank-category misses | **12/15 = 80%** | 同上 |
| Adversarial audit | **16/16（Cat A 10 + Cat B 6）** | `ontology/docs/adversarial_audit_report.md`；Cat B 首轮 0/6 → 修复后 6/6 |

## 潜伏期 / 发现渠道（§5.1 / §5.2）

| 论文数字 | 值 | 来源 |
|---|---|---|
| 60 天 | MOVESPEED TCC sandbox | `movespeed_tcc_sandbox_blood_case.md`（V37.9.4 4/21 → V37.9.80 5/18） |
| 7 天 | watchdog 自身静默 | V37.9.58-hotfix3（5/5 16:30 → 5/12） |
| 5 天 / 6 天 | observer path / exfat 备份 | `v37_9_92_observer_path_blood_case.md` / `movespeed_exfat_silent_backup_failure_case.md` |
| 13h / 9h | HEARTBEAT.md / Gateway 死亡 | `heartbeat_md_pa_self_silencing_case.md` / `whatsapp_silent_death_case.md` |
| ~70% user-view 发现 | 定性 | catalog 横向洞察 §2（"占比（定性）"——论文已标注 qualitative） |

## Class D 防御效果（§6 pillar 4）

| 论文数字 | 值 | 来源 |
|---|---|---|
| 多跳因果链下降 92% | 4.75 → 0.4 行/天 | V37.9.100 changelog（LEVEL_6 5 天观察 verdict PASS） |
| "因此"句式下降 53% | 3.0 → 1.4 | 同上 |
| 证据 tag 0 → ~9.4/天 | [强证据]+[弱关联] | 同上（论文写 "~9 per day"） |
| 反幻觉守卫档数 | **6 级**（LEVEL_1~6） | V37.9.57 立 5 档 + V37.9.89 LEVEL_6 |
| 接入 task 数 | **9** | V37.9.57 changelog |
| 来源可信度档数 | **5 tier / 14+ 源** | `source_credibility.py`（V37.9.98） |

## 单个案例数字（§4 叙事）

| 论文数字 | 值 | 来源 |
|---|---|---|
| 67 vacuous checks / 21 invariants | V37.9.100 | changelog（assertion: 字段空跑血案） |
| 20 个 set -e callers 被杀 | V37.9.31 | `rsync_helper_set_e_regression_case.md` |
| 18 处复制粘贴抑制反模式 | V37.9.4 | `movespeed_exfat_silent_backup_failure_case.md`（修复时实为 20 处含漏网） |
| import 缺漏 2 天后 8 处重演 | V37.9.50-hotfix (5/10) → V37.9.58-hotfix (5/12) | changelog（**catalog §4 写 "8 天" 是笔误，8 = jobs 数；论文用 2 天**） |
| 6 假说全证伪（60 天） | V37.9.80 | `movespeed_tcc_sandbox_blood_case.md` |
| 5 轮连锁修复 vs 一次 cp | V37.8.3 | `preflight_cascading_fix_case.md` |
| ~290 notes map-reduce | V37.4 era（286-293 波动） | `dream_map_budget_overflow_case.md`（论文 "~290"） |
| 告警污染 36 分钟窗口 | V37.4.3 13:06 事件 | `pa_alert_contamination_case.md`（12:30 告警 → 13:06 提问） |
| convergence 观察窗口 | 每 spec 一周零漂移 | V37.9.19→23→58→97→133（alert_only→dry-run→激活三阶段） |
| 16 destruction scenarios | 10 replay + 6 probe | `adversarial_chaos_audit.py` |

## 引用状态（References）

| # | 状态 | 备注 |
|---|---|---|
| 1 Gray Failure (HotOS'17) | ✅ 全验证 | 作者列表 + DOI 经 WebSearch 验证 2026-06-11 |
| 2 Fail-Slow at Scale (FAST'18) | ✅ 全验证 | 101→114 reports；TOS 14(3) 扩展版 |
| 3 MAST (arXiv:2503.13657) | ✅ 全验证 | Cemri/Pan/Yang 首三作者 + κ=0.88 验证 |
| 4 arXiv:2511.07424 | ✅ **arXiv API 终核** | Ranganathan/Zhang/Wu（来源：search snippet 2026-06-11；投稿前对 abs 页终核） |
| 5 arXiv:2508.07935 SHIELDA | ✅ **arXiv API 终核** | Zhou/Chen/Lu/Zhao/Zhu + 36 异常类型/12 artifacts |
| 6 arXiv:2602.11749 AIR | ✅ **arXiv API 终核** | Xiao/Sun/Chen |
| 7 arXiv:2603.05637 MCP faults | ✅ **arXiv API 终核** | Taraghi/Morovati/Khomh |
| 8 SRE book | ✅ 常识级 | — |
| 9 arXiv:2508.14231 Incident Analysis for AI Agents | ✅ 全验证 | Ezell/Roberts-Gaal/Chan + **AIES 2025 正式发表**（AAAI/ACM proceedings 确认）；已并入 §2.2（第二轮） |
| 10 arXiv:2606.05339 MCP runtime faults | ✅ **arXiv API 终核** | Owotogbe/Kumara/van den Heuvel/Tamburri/Iannillo/Natella（来源：X 帖子转录） |
| 11 arXiv:2311.05232 hallucination survey | ✅ 全验证 | Huang Lei 等 11 作者（scirp 引用页 + arXiv 确认）；已并入 §2.3（第二轮） |
| 12 Chaos Engineering (IEEE Software 2016) | ✅ 全验证 | Basiri/Behnam/de Rooij/Hochstein/Kosewski/Reynolds/Rosenthal, 33(3):35-41, DOI 10.1109/MS.2016.60；已并入新 §2.4 |
| 13 How to Fight Production Incidents (SoCC'22) | ✅ 全验证 | Ghosh/Shetty/Bansal/Nath, Best Paper, 152 Microsoft Teams severe incidents；已并入新 §2.4 |
| 14 LLM-as-a-Judge (NeurIPS'23) | ✅ 全验证 | Zheng/Chiang/Sheng 等 13 作者, arXiv:2306.05685, >80% human agreement；已并入 §5.2（含"judge 自身继承本 taxonomy 全部类别"回喂论点，实证 = observer 自身 Class B path bug + sampling artifact） |

**第二轮（2026-06-11 同日）已完成**：#9 核实并入 §2.2（含"潜伏时长+发现者两字段应纳入 agent incident report"的回喂论点）/ #11-13 三类引用补齐（hallucination survey → §2.3；chaos engineering + AIOps incident study → 新 §2.4，含 sabotage validation = "chaos engineering applied to guards" 连接点）/ #14 LLM-as-judge → §5.2 / Fig.3 D1 幻觉链 ASCII 图入 §4.4（trigger-amplifier-concealer 标注，LaTeX 阶段重绘正式版）/ 正文引用风格统一为作者-年份。**本稿登记的 related-work pass 全部完成（14 条 verified references）。**
**✅ 终核完成（2026-06-11 Mac Mini）**：6 篇 arXiv 引用经官方 API（export.arxiv.org，https+-L）逐一对照 main.tex thebibliography，**作者列表 100% 一致，零修改**。投稿前引用工作全部清零。终核命令存档：

```
curl -sL "https://export.arxiv.org/api/query?id_list=2511.07424,2508.07935,2602.11749,2603.05637,2606.05339,2508.14231" | grep -E "<title>|<name>"
```

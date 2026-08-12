# Data Inventory — Observer Paper (#2)

> 论文 #2 `draft.md` 中每一个数字/日期/比例的仓库内溯源（镜像论文 #1 `data_inventory.md` 纪律）。
> 目的：(1) 审稿/读者可验证 (2) 防 doc drift（MR-8 单一真理源）(3) 投稿前最终对表清单。
> 快照基准：**study cutoff 2026-08-09 23:59 HKT**（§9.2.3 预注册冻结规则，2026-08-10 V37.9.296 落表）；
> 草稿撰写日 2026-08-12，`VERSION 0.37.9.132` / CLAUDE.md v37.9.296。
> 维护契约：仅投稿前重新对表；若观察窗延长后重新落表，须按 §9.2 协议以新 cutoff 机械重跑，本表登记新版本（append，不改写）。

## 生产窗口数据（§6 全部数字的单一真理源 = design doc §9.2.7）

| 论文数字 | 值 | 仓库来源 | 验证 |
|---|---|---|---|
| shadow 窗口 | 2026-06-30 → 07-25，26/26 天，fired=0 | `docs/llm_observer_design.md` §9.2.7 regime 对账表 | Mac Mini `~/.kb/self_critique/score_history.jsonl` |
| on 窗口（observed） | 2026-07-27 → 08-07，12 行，fired=0 | 同上 | 同上（`fp_mode=="on"` 行） |
| 边界剔除行 | 07-26，1 行（fp_mode 字段部署边界） | §9.2.7 regime 表 + §9.2.2 plumbing 规则 | 预注册规则先于数据 |
| 窗外不计 | 06-29 ×3（wiring 日） | §9.2.7 regime 表 | — |
| 设计窗口 vs 实际覆盖 | 14 天设计（07-26→08-08），12/14 观测 | §9.2.7 脚注② | 08-08 缺行=Mac Mini 关机（良性，用户核查确认 V37.9.296 后记） |
| 污染剔除规则命中 | 0 行 | §9.2.7 脚注①（2026-05-25 llm_failed fixture 规则） | V37.9.276 清污染 no-op 再确认 |
| 报告标签/字段 1 天错位 | daily_critique_20260725/26 标 [on]，行无 fp_mode | §9.2.7 脚注③ | 规则先于标签，不改判 |
| H1 live precision | 未定义（0 分母），不给数字 | §9.2.7 H1 表 + §9.2.1/§9.2.5 冻结口径 | 12 报告全「✅ 无 fail-plausible 信号」双源一致 |
| H2 latency | 构造性 ≤24h，0 live 样本 | §9.2.7 H2 表 | 06:30 日频 cron |
| H3 manifested in-scope FN | 0；预防通道分开报告 | §9.2.7 H3 表 | 见下「审计轮」 |
| C1-C3 flip 判据 12 天延续 | 全成立 | §9.2.7 末段 | — |
| flip 决策日 / 落地 / on 首日 | 07-24 / 07-25（env）/ 07-26 | §9.1.1 决策记录 + V37.9.276 changelog | — |
| 预注册日期 | flip criteria = V37.9.210（07-XX 门控收敛日）；analysis protocol = 2026-07-31（V37.9.284） | design doc §9.1 / §9.2 头注 | changelog V37.9.210/284 |
| shadow 抓系统性 FP（S5 评分字段） | 1 例（06-30 shadow 首次生产运行，分析 06-29 wiring 日产出 = §9.2.7 窗外 fp_med=1 行），当天 shadow 内修复 + 守卫 + scorecard 复跑；注册窗 26/26 天全 post-fix 干净 | changelog V37.9.199（freight「评级：⭐⭐⭐⭐」×3 误报 → `_descriptive_char_count` 阈值修复）+ §9.1 C1 文案 | test_llm_observer +3 守卫在 repo |
| jobs_total 12→14（08-07 行） | 监控面扩大证据 | §9.2.7 脚注④（V37.9.287 A-F2） | — |

## Ground truth / corpus（§4）

| 论文数字 | 值 | 仓库来源 | 验证命令 |
|---|---|---|---|
| case 文件总数 | 28 | `ontology/docs/cases/` | `ls ontology/docs/cases/*.md \| wc -l` = 28（2026-08-12 实测） |
| 已标注 incidents | 24 | `docs/llm_observer_ground_truth.yaml` summary.total_labeled | `grep total_labeled` |
| 论文 #1 canonical | 22（24 − 2 post-cutoff） | 同上 paper_canonical | — |
| 非失败排除 | 3 | 同上 non_failure_excluded | — |
| 标注后新增未标 | 1（reasoning_model_primary_breaks_batch_jobs_case.md, V37.9.220） | 28 − 24 − 3 = 1 | ground truth last_updated 2026-06-29 |
| observer_in_scope 划分 | yes 3 / partial 5 / no 16 | ground truth summary | draft §4.2 表 |
| fail_plausible | yes 4 / partial 4 | ground truth summary | — |
| golden seeds | 5 | ground truth summary.golden_seed_count | — |
| 标注 schema 字段 | taxonomy_class / fail_plausible / llm_fabrication / observable_artifact / discovery_channel / observer_in_scope / expected_signal / category / silence_span | ground truth 头注 + design doc §4.2 | — |

## Bench / 离线 scorecard（§5）

| 论文数字 | 值 | 仓库来源 | 验证命令 |
|---|---|---|---|
| defense rate | 100%（6/6） | `docs/llm_observer_scorecard.md` + `llm_observer_selfcheck.py` | `python3 llm_observer_selfcheck.py --json` |
| false-positive rate | 0%（0/4） | 同上 | 同上 |
| held-out FN（Category B） | 100%（4/4）→ 论文写 recall 0% | 同上 | 同上 |
| corpus 划分 | A=6 / clean=4 / B=4 | `docs/fail_plausible_bench.md` corpus 表 | `--manifest` |
| sabotage 全 load-bearing | 6/6 detector | scorecard sabotage 表 | `--sabotage` |
| bench id/version | fail-plausible-detection-bench / 0.1 | `docs/fail_plausible_bench.md` | `--manifest` |
| B 盲区 4 案例名 | fabricated_acceptance / unsupported_strong_claim / paraphrased_pollution / fabricated_author | scorecard Category B 段 | — |
| Layer 1 五信号 S1-S5 | pollution / credibility-mismatch / fabrication-phrase / provenance-gap / coherence | `llm_observer.py` + design doc §3.1 + ground truth signals 字典 | — |
| Layer 2 四维 | grounding / intent-alignment / pollution-evidence / fabricated-success | `llm_observer.py` FAIL_PLAUSIBLE_SYSTEM | — |
| 逐字 grounding 降级规则 | ungrounded evidence drop → 无 grounded 证据降级 clean | `llm_observer.py` `_evidence_grounded()`（V37.9.196） | test_llm_observer TestEvidenceGrounding |
| cheap-path | 干净日零 Layer 2 调用 | design doc §3.2 + §9.2.7 C3 | — |

## Observer 自身事故（§7 表，O1-O8）

| # | 论文描述 | 仓库来源 |
|---|---|---|
| O1 | registry path fallback 5 天 + status.json 闭环未实现 | `ontology/docs/cases/v37_9_92_observer_path_blood_case.md`（V37.9.92） |
| O2 | sampling 幻觉「truncation」（2000-char 边界） | 同 case 文件 V37.9.93 段 |
| O3 | --dry-run 持久化污染 | changelog V37.9.274 SF2 |
| O4 | 测试套件污染生产 score_history（05-25 fixture ~3×/天，约 2 个月） | changelog V37.9.276 ②（MR-9 家族）+ §9.1.1 数据污染剔除段 |
| O5 | 死 flip 开关（daily_observer.sh 唯一不 source .env_shared） | changelog V37.9.276 ③ |
| O6 | on 模式评分集成 inert（fp 在 critique prompt 之后 extend；报告标「已集成」而分数逐字节同 shadow） | changelog V37.9.279 OBS-F1 |
| O7 | confidence 校准腐蚀（L2 clean 置信度贴到 L1-only verdict） | changelog V37.9.279 OBS-F2 |
| O8 | wrapper 报告写失败吞错 + status ok（fabricated success in shell） | changelog V37.9.279 OBS-F5 |

## 审计轮（§6.5 H3 预防通道）

| 论文数字 | 值 | 仓库来源 |
|---|---|---|
| 第一轮 | 2026-07-26 四镜头 14 findings（三批修复） | changelog V37.9.279/280/281 |
| 第二轮 | 2026-08-07 三镜头 15 candidates（10 修复 + 4 登记后 08-10 修复 + 1 证伪剔除） | changelog V37.9.287/288 + V37.9.292-295 + §9.2.7 H3 表 |
| 例子：mm_index 静默失效家族 / KB 幽灵向量 | B-F1 / B-F4 | changelog V37.9.292 / V37.9.293 |

## 论文 #1 复用数字（frozen，不随仓库演进）

| 论文数字 | 值 | 来源 |
|---|---|---|
| ~70% user-view discovery | ~70% | 论文 #1 §5.2（arXiv:2606.14589） |
| audit 0% / 87% | 0/15、13/15 | 论文 #1 §5.6 |
| 论文 #1 时代 stack | 4,286 tests / 827 checks / 19-point preflight | 论文 #1 §3.1（study cutoff 2026-06-11 冻结） |
| silence spans | 13h → 60 days | 论文 #1 §5.1 |
| 22 incidents / 5 classes / D1-D4 | — | 论文 #1 §3-4 |

## 系统规模快照（§3 系统上下文，2026-08 时点）

| 数字 | 值 | 来源 | 说明 |
|---|---|---|---|
| tests / suites | 5,761 / 167 | `docs/config.md` 头注 v37.9.296 = status.json quality | 草稿正文仅在 §1 用「4,000+ unit tests」量级表述（论文 #1 冻结数与现值并存，避免双 canonical） |
| governance checks / invariants | 839 / 91 | 同上 | 同上（正文用「hundreds of checks」量级） |
| providers | 12 | compat matrix / badges | 如正文需要精确值再引 |
| scheduled jobs | ~40 | jobs_registry / check_registry | — |

## 新引用核验（2026-08-12 WebSearch）

| 引用 | 核验结果 |
|---|---|
| Nosek, Ebersole, DeHaven, Mellor. *The preregistration revolution.* PNAS 115(11):2600-2606, 2018. doi:10.1073/pnas.1708274114 | ✅ 作者列表与 PNAS 页一致（pnas.org/doi/abs/10.1073/pnas.1708274114） |
| Panickssery, Bowman, Feng. *LLM Evaluators Recognize and Favor Their Own Generations.* NeurIPS 2024; arXiv:2404.13076 | ✅ NeurIPS 2024 proceedings + arXiv 页一致 |
| Ernst & Baldassarre. *Registered Reports in Software Engineering.* EMSE 28(2), 2023; doi:10.1007/s10664-022-10277-5; arXiv:2302.03649 | ✅ 2026-08-12 检索核验（Springer EMSE + arXiv 一致）。用途 = §2.3 把「操作性预注册」定位于 SE 已有的**发表侧**注册报告传统之旁（MSR 2020 起设 track），并据此**收窄新颖性主张**（"uncommon rather than unprecedented"，不宣称 unprecedented） |
| Rebedea, Dinu, Sreedhar, Parisien, Cohen. *NeMo Guardrails.* EMNLP 2023 System Demos; arXiv:2310.10501 | ✅ 2026-08-12 检索核验（ACL Anthology 2023.emnlp-demo.40 + arXiv 一致）。用途 = §2.3 对照：既有 runtime-guard 框架规定 guard **enforce 什么**，不规定团队如何**事先**决定 guard 何时获得 enforcement 授权 |
| 其余 7 条 | 复用论文 #1 已终核引用（arXiv API 作者列表 100% 一致，见论文 #1 data_inventory References 段） |

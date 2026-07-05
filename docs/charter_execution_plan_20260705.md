# 纲领执行计划（Charter Execution Plan）

> **文档性质**：`docs/technical_charter_20260705.md`（技术纲领）的**配套执行文档**。纲领管方向与判据；本文档管"怎么执行 + 每季度怎么复核"。二者一物一形：本文档不复制纲领的方向判断，只把纲领的 §5 地平线 / §8 判据门 / §10 落实建议落成**可执行任务 + 可运行复核协议**。
> **确立版本**：V37.9.248（2026-07-05） | **基线快照**：见 §0。
> **维护契约**：随纲领季度复核滚动更新（Part 2 R4）；已完成任务不回填，迁入 status.json / changelog。

---

## 第一部分：具体可执行计划

### §0 执行基线（2026-07-05 快照 · 每季度复核的对比锚点）

> 这是首次复核（2026 Q3）的**基线数字**。之后每季度计算相对本快照的 delta（Part 2 R2）。

**复杂度四维度（机械采集，方法见 Part 2 R2）**

| 维度 | 基线值 | 采集方法 |
|------|--------|----------|
| files（代码文件 py+sh，排除 docs/） | **286** | `git ls-files '*.py' '*.sh' \| grep -vE "^(docs/\|ontology/docs/)" \| wc -l` |
| env（distinct 环境变量） | **43** | `grep -rhoE "os\.environ\.get\(['\"][A-Z_]+" *.py providers.d/*.py \| grep -oE "[A-Z_]+$" \| sort -u \| wc -l` |
| jobs（enabled / total） | **40 / 46** | `python3 -c "import yaml;d=yaml.safe_load(open('jobs_registry.yaml'));j=d['jobs'];print(sum(x.get('enabled') for x in j),'/',len(j))"` |
| runtime-state-sources（distinct 状态文件） | **25** | 见 Part 2 R2 命令块 |

**证据规模（徽章事实，`gen_readme_badges.py` 单一真理源）**

| 指标 | 基线值 |
|------|--------|
| tests / suites | 5448 / 153 |
| invariants / meta-rules / checks | 91 / 23 / 839 |
| MRD scanners / case docs | 14 / 28 |
| providers | 11（7 built-in + 4 插件） |
| security_score | 95/100 |
| VERSION / semver | v37.9.247 / 0.37.9.101 |

**影响力指标（B1，外部数据 · 诚实边界：值待首次复核采集，命令见 Part 2 R1）**

| 指标 | 基线值 | 说明 |
|------|--------|------|
| arXiv:2606.14589 引用数 | 待采集 | Semantic Scholar / Google Scholar |
| PyPI openclaw-ontology-engine 下载 | 待采集 | pypistats / pepy.tech |
| GitHub stars / issues / PRs（外部） | 待采集 | 本仓 bisdom-cell/openclaw-model-bridge |
| bench 外部贡献 case 数 | 0 | fail_plausible_bench + reliability_bench |
| 文章阅读 / inbound 询问 | 待采集 | 用户账号，Mac Mini/本人手动 |

> ⚠️ 影响力值不编造（原则 #23）。首次复核用真实账号/命令采集填入 status.json，作为**下季度增长率的分母**。

**判据门控当前态（纲领 §8，2026-07-05）**

| 门 | 决策 | 当前态 |
|----|------|--------|
| G1 | Observer shadow→on flip | shadow 周中，~7/7 按 §9.1 判 |
| G2 | 第二实例 PoC 启动 | 前置进行中（H1-B 首批 config 化 + scanner 0 violations） |
| G3 | observer-engine 抽包 | 未触发（需外部需求信号） |
| G4 | 商业化评估 | 未触发（需 ≥3 独立 inbound） |
| G5 | 团队/资金/托管 | 未触发 |
| G6 | OpenClaw 升级 | 跟踪中（eval doc §17 三收敛判据；第六次评估 hold） |
| G7 | MCP/协议兼容层 | 未触发 |
| G8 | engine 1.0 semver | 未触发 |

---

### §1 H1 地平线可执行任务分解（2026H2，0–6 个月）

> 每个任务：**目标 / 交付物（artifact）/ done-criteria / 依赖·门控 / 优先级 / 目标季度**。优先级 P0=宪法级或阻塞其他 / P1=本地平线核心 / P2=有价值可延后。

#### H1-A｜LLM-Observer 收官（宪法级 #1，最高优先）

| 任务 | 目标 | 交付物 | done-criteria | 依赖/门控 | 优先级·季度 |
|------|------|--------|---------------|-----------|-------------|
| A1 | Observer shadow→on flip 决策 | status.json + changelog 记 flip/extend 决策 | 按 design doc §9.1 预注册三判据（C1 零系统性 FP / C2 ≥1 TP 或干净周 / C3 成本可持续）逐条判，产出决策记录 | **G1**；需 Mac Mini `score_history` fp_high/med 7 天序列 + 每日报告 fail_plausible 段人工判 TP/FP | P0 · Q3 |
| A2 | flip 后精度观察 | 两周 live precision 记录 | flip 后 14 天 fp verdict 逐条人工判，live fp_rate 入 scorecard §5.4 | A1 flip=on | P0 · Q3 |
| A3 | 新 TP/FP case 回灌 ground-truth + bench | `llm_observer_ground_truth.yaml` + `fail_plausible_bench` 新增 case | 每个生产真 TP/FP 加机器标签进 ground-truth；新 fail-plausible 模式归 Category B | A2 | P1 · Q3–Q4 |
| A4 | 论文 #2 数据表填充 | 论文 #2 草稿数据段 | detection latency（Observer 抢在用户前多少小时）+ live precision + held-out recall（含诚实 negative result） | A2 生产数据积累 | P0 · Q4 |

**H1-A 退役**：Observer 转正后退役"人工逐条扫已知 fail-plausible 模式"的人工动作（design doc 日落法第一问）。

#### H1-B｜PA 解耦第一批（config 化，非目录重排）

| 任务 | 目标 | 交付物 | done-criteria | 依赖/门控 | 优先级·季度 |
|------|------|--------|---------------|-----------|-------------|
| B1 | PA 耦合机器化盘点 | `docs/pa_coupling_inventory.md` | 扫描个人配置面（个人路径 / 占位号码 / 中文 prompt / macOS 假设），逐项登记 + blast-radius 分级（低/中/高） | — | P0 · Q3 |
| B2 | config 化 backlog 分级 | inventory 内 backlog 表 | 每条耦合标"config 化方式 + 风险 + 是否本季度做" | B1 | P1 · Q3 |
| B3 | 低风险批次 config 化落地 | 合并的 PR + 新耦合扫描器 | 第一批低-blast-radius 耦合 config 化；新增 PA-specific 硬编码有 scanner 拦截 | B2；**明确不做**大目录重排 / 3-engine-merge（两次 DEFER 维持） | P1 · Q4 |

**H1-B 退役**：PA 硬编码假设 → config（每批次退役一批硬编码）。

#### H1-C｜第二实例 PoC（可迁移性从声明到实证）

| 任务 | 目标 | 交付物 | done-criteria | 依赖/门控 | 优先级·季度 |
|------|------|--------|---------------|-----------|-------------|
| C1 | 第二实例最小闭环选型 + 前置门控核对 | 选型记录 | **G2** 前置满足：H1-B 首批 config 化合并 + `cross_os_quirk_scanner` 持续 0 violations + `minimal_runtime` golden 跨机 MATCH | B3 + G2 | P1 · Q4 |
| C2 | Linux 容器/VPS 跑通最小闭环 | 运行日志 + 截图 | 非 Mac Mini 机器上 E2E 绿：`minimal_runtime` + governance audit + 1–2 内容 job + notify（Discord-only 可） | C1 | P1 · Q4 |
| C3 | portability report | `docs/articles/` 证据型文章 | 报告发表（EN/ZH）：跨机迁移的真实坑与解 = 对两次评审可迁移性批评的终局回答 | C2 | P1 · Q4–2027Q1 |

**H1-C 退役**：单机绑定风险（bus factor 部分缓解，R1）。

#### H1-D｜证据刷新持续（评审2 指令）

| 任务 | 目标 | 交付物 | done-criteria | 依赖 | 优先级·季度 |
|------|------|--------|---------------|------|-------------|
| D1 | Mac Mini SLO 报告重生成 | `docs/slo_benchmark_report.md` 更新 | Mac Mini `python3 slo_benchmark.py --save` 真实数据重生成入库（评审2 P0 收尾） | Mac Mini | P1 · Q3 |
| D2 | bench manifest / compat matrix 保持 current | 无 stale 证据 | 季度复核时三 doc-drift 机器检查全绿 + scorecard live 值一致 | 已机器化 | P1 · 每季度 |

#### H1-E｜复杂度预算年度复盘

| 任务 | 目标 | 交付物 | done-criteria | 依赖 | 优先级·季度 |
|------|------|--------|---------------|------|-------------|
| E1 | 2026 年度复杂度账本汇总 | changelog 年度复盘条目 | 四维度全年净变化 + 净退役清单；2027 目标：runtime-state-sources 净零增 | 四季度 R2 数据 | P1 · Q4 |

---

### §2 H2/H3 里程碑（粗粒度，全部判据门控 · 不过度规划未来）

> 纲领 §5 明确 H2/H3 由门控驱动。此处只列里程碑锚点，具体任务分解在对应季度复核时（触发门后）再展开——避免"计划本身成为复杂度来源"（纲领 §0-1）。

**H2（2027）产品化与生态**

| 里程碑 | 门控 | 锚点 |
|--------|------|------|
| ontology-engine 0.x → 1.0 | **G8**（外部 issue/PR 存在 + API 连续 2 minor 无 breaking） | 2027 |
| bench 社区化（首个外部贡献 case 合并） | — | 2027 |
| observer-engine 抽包 | **G3**（≥1 真实外部需求信号） | 2027 |
| 论文 #2 投稿 | A4 完成 | 2027 H1 |
| 能力平面纵深（成本-质量-时延联动调度 / Memory Plane plugin） | 需求驱动（原则 #18） | 2027 |

**H3（2028）标准与影响力**

| 里程碑 | 门控 | 锚点 |
|--------|------|------|
| Agent Reliability 参考架构白皮书 | H2 采用信号 ≥ 基线 | 2028 |
| 教学/布道形态 | 白皮书/论文自然流量证明需求 | 2028 |
| 商业化评估 | **G4**（≥3 独立组织 inbound） | 2028 |
| 纲领 2.0 修订 | — | 2028 年中 |

---

### §3 依赖与门控关系（一图看清阻塞链）

```
H1-A (Observer 收官, 宪法级 P0)
  A1 flip[G1] → A2 精度观察 → A3 回灌 bench → A4 论文#2数据
                                                    │
                                                    ▼ (2027)
                                              论文#2 投稿 → 论文被引 ┐
                                                                    │
H1-B (PA 解耦 config 化)                                            ├→ B1 影响力漏斗
  B1 盘点 → B2 backlog → B3 低风险落地 ─┐                          │
                                        │                          │
H1-C (第二实例 PoC)                     ▼                          │
  C1 前置[G2] ← (B3 + scanner 0 + golden MATCH)                   │
  C1 → C2 跑通 → C3 portability report ──→ 可迁移性实证 ──────────┘

H1-D 证据刷新 (D1 SLO / D2 每季度) ──→ 持续 current
H1-E 年度复盘 (E1, Q4) ←── 四季度 R2 数据汇总

门控闸: G1→A1 / G2→C1 / G3→observer-engine / G8→engine1.0 / G4→商业化
```

---

## 第二部分：每季度五项固定复核协议（可运行运行手册）

### §4 复核节奏与自我约束

- **节奏**：每季度末（3/6/9/12 月）各一次；重大外部信号（论文接收/拒稿、外部评审、首个外部采用、上游剧变）随时触发一次即时复核（纲领 §11.2）。
- **时间盒**：约一个 session。产物 = status.json 记录 + 必要时纲领增量修订。
- **🔴 自我约束（日落法，纲领 §0-1 + `complexity_budget.md` §二）**：本协议**零新增常驻机器**——不写 `quarterly_review.py`，不建新状态源。所有命令复用已有工具（`gen_readme_badges` / `governance_checker` / git / 公开 API），记录落已有的 `status.json`。协议靠"照单跑命令 + 人工判断 + changelog 留痕"执行。**本协议刻意无守卫测试**（MR-22：用新 check 守护复核约定，本身就是新复杂度）。

**执行顺序**：R2（复杂度，纯机械）→ R1（影响力，采集）→ R3（门控核对）→ R5（反目标自查）→ R4（增量修订，收尾把前四项结论落纲领/status）。

---

### §5 R1 · 影响力指标记录

**目的**：量化 B1 影响力漏斗（论文被引→bench 被跑→引擎被装→inbound）的季度增长；诚实复盘。

**数据源与命令**（值填入 status.json，作下季度增长率分母）：

```
pip install --quiet pypistats
pypistats recent openclaw-ontology-engine --json
curl -s "https://api.semanticscholar.org/graph/v1/paper/arXiv:2606.14589?fields=citationCount,influentialCitationCount"
curl -s "https://api.github.com/repos/bisdom-cell/openclaw-model-bridge" | python3 -c "import json,sys;d=json.load(sys.stdin);print('stars',d.get('stargazers_count'),'issues',d.get('open_issues_count'))"
```

- **bench 外部贡献 case**：`git log --oneline -- docs/fail_plausible_bench.md 'ontology/tests/adversarial_chaos_audit.py'` 查非本人作者的 case 合并（或 GitHub PR 列表）。
- **文章阅读 / inbound 询问**：用户手动（知乎/dev.to 后台 + 邮件/私信），本人在 Mac Mini 侧填。
- **GitHub 外部 star/issue/PR**：本会话可用 GitHub MCP（`get_file_contents`/`list_issues`/`search_issues`）或上方 curl。

**🔴 采集环境说明（2026-07-05 dev 探测实证）**：这些命令**须在有真实访问的环境跑**——dev 容器出站探测证实 S2/PyPI 端点从此处不可达、GitHub 直连 API 被拦（需连接 GitHub App）。正确采集处 = Mac Mini（有网络/凭据）或本人账号后台；本会话内可用 GitHub MCP 工具（`search_repositories`/`list_issues`/`list_pull_requests`）取 GitHub 侧。诚实边界：值在采集前一律标"待采集"，绝不用估计值占位（原则 #23）。

**记录位置**：`status_update.py --add recent_changes '{"date":"...","type":"quarterly_impact","what":"引用 N / 下载 N / star N / 外部PR N / inbound N"}'`

**done-criteria + 诚实复盘规则**：五项值全部采集入库（不可采集的显式标"账号侧待填"）。**连续两季度全部指标零增长 → 触发叙事/渠道策略反思**（纲领 §6-B1），反思记入 R4 增量修订，不自欺。

---

### §6 R2 · 复杂度账本小结

**目的**：让"净新增无处可藏"（`complexity_budget.md` §一）。计算四维度相对**上一季度快照**（首季度对比 §0 基线）的 delta。

**命令块**（全部只读，零副作用）：

```
git ls-files '*.py' '*.sh' | grep -vE "^(docs/|ontology/docs/)" | wc -l
grep -rhoE "os\.environ\.get\(['\"][A-Z_]+" *.py providers.d/*.py | grep -oE "[A-Z_]+$" | sort -u | wc -l
python3 -c "import yaml;d=yaml.safe_load(open('jobs_registry.yaml'));j=d['jobs'];print(sum(bool(x.get('enabled')) for x in j),'/',len(j))"
python3 gen_readme_badges.py --write
```

runtime-state-sources 采集：

```
python3 -c "import re,glob; s=set(); pat=re.compile(r'(last_run_[a-z_]*\.json|proxy_stats\.json|status\.json|index\.json|sessions\.json|audit\.jsonl|canary[a-z_]*\.json|\.audit_metrics\.jsonl|[a-z_]+_incidents\.json)'); [s.update(x.split('/')[-1] for x in pat.findall(open(f,encoding='utf-8',errors='ignore').read())) for f in glob.glob('*.py')+glob.glob('*.sh')+glob.glob('jobs/*/*.sh')+glob.glob('jobs/*/*.py')]; print(len(s))"
```

**判断问题（对每个净新增*机制*，非证据/文档）**：
1. 这个季度四维度各净变化多少？（files/env/jobs/state 的 +/-）
2. 每个净新增机制"退役了一个等价旧机制吗？还是真加价值的新能力，而非又一道接缝？"
3. tests/checks 增长是好事（证据）；invariants/MRD 增长须每个说明"防哪类事故"。

**记录位置**：`status_update.py --add recent_changes '{"date":"...","type":"quarterly_complexity","what":"files X(+/-) env X jobs X state X | 净退役: ... | 净新增机制: ..."}'`；年度（Q4）汇总入 changelog（H1-E1）。

---

### §7 R3 · 判据门控核对

**目的**：逐门核对纲领 §8 的 G1–G8，判"触发/未触发/进展"，把散落信号收敛为单一决策记录。

**逐门核对法**（数据源）：

| 门 | 核对信号 | 数据源 |
|----|----------|--------|
| G1 Observer flip | shadow 周 §9.1 三判据是否满足 | Mac Mini score_history + 每日报告 |
| G2 第二实例 | H1-B 首批 config 化合并？scanner 0 violations？golden 跨机 MATCH？ | git log + `cross_os_quirk_scanner` + `minimal_runtime` |
| G3 observer-engine | 出现 ≥1 外部"想装到我的系统"需求信号？ | GitHub issue / 邮件 |
| G4 商业化 | ≥3 独立组织 inbound？ | R1 inbound 记录 |
| G5 团队化 | G4 满足 + 两季度 inbound 不衰减 + 用户决策？ | R1 + 用户 |
| G6 OpenClaw 升级 | eval doc §17 三收敛判据（连续 2 stable 无 SQLite 迁移 PR / 周稳定 ≤1 / node ≥22.19）？ | `check_upgrade.sh` + npm registry |
| G7 MCP 兼容 | 工具治理引擎出现 MCP 生态真实消费需求？ | GitHub / 社区 |
| G8 engine 1.0 | 外部 issue/PR 存在 + API 连续 2 minor 无 breaking？ | PyPI + GitHub |

**判断规则**：门"触发"= 判据**全满足**才推进对应任务；任一未满足 = 保持未触发（"条件不满足就不做"是体面结局，纲领 §0-3）。

**记录位置**：核对结果更新纲领 §8 表"当前态"列（经 R4 增量修订）+ `status_update.py --add recent_changes '{"type":"quarterly_gates","what":"G1 ... G6 ..."}'`。

---

### §8 R4 · 纲领增量修订

**目的**：把本季度 R1–R3+R5 的结论沉淀进纲领，保持纲领 current 但不改写历史判断。

**方式（纲领 §11.2）**：
1. **只追加**纲领"修订记录"表一行（版本递增 1.1/1.2…，日期，变更摘要）。
2. **正文最小增量**：仅更新 §8 门控"当前态"列 + §1 现状盘点必要处 + §10 落实建议滚动（已完成项不回填，迁 status/changelog）。
3. **历史判断不回改**：§2 趋势置信度即使判错也不改正文——在修订记录里承认，"这本身是方法论的一部分"。

**触发**：每季度复核收尾必做一次（哪怕只加"本季无方向性变化"一行）；重大外部信号即时触发。

**记录位置**：`docs/technical_charter_20260705.md` 修订记录表 + 本执行计划 §0 基线快照滚动。

---

### §9 R5 · 反目标自查

**目的**：逐条核对纲领 §4.4 六条反目标，本季度有无违反项（防止"局部看都合理"的漂移，`complexity_budget.md` §一）。

**逐条自查（本季度是否有违反）**：

| # | 反目标 | 自查问题 |
|---|--------|----------|
| 1 | 不做通用 agent framework | 本季度有没有为"通用性"而非真实需求加的框架层？ |
| 2 | 不追新模型/协议每班车 | provider/协议接入是真实需求驱动，还是"别人有我也要"？ |
| 3 | 不做 UI/前端/托管 | 有没有越界做界面/托管（超个人+AI 运营半径）？ |
| 4 | enforcement 永久 human-approval | three-gate Phase D 有没有偷偷加自动删改用户可见内容？ |
| 5 | 不假设团队/资金 | 有没有规划依赖"招人/买服务"作前提？ |
| 6 | 纲领自身不膨胀 | 本季度纲领/本文档有没有"加一节而没退役一节"？ |

**处置**：发现违反项 → 记入 R4 增量修订 + 立即或计划纠偏；无违反 → 记"本季反目标自查通过"。

**记录位置**：`status_update.py --add recent_changes '{"type":"quarterly_antigoals","what":"6 条自查: 通过 / 违反项 ..."}'`

---

### §10 季度复核记录模板（copy-paste 到 status.json / 新建季度小结）

```
## 季度复核 YYYY-Qn（日期）

R2 复杂度账本:
  files X (Δ vs 上季 ±N) | env X (±N) | jobs X/Y | state X (±N)
  tests X | suites X | invariants X | checks X | providers X | security X
  净退役: ...
  净新增机制: ... (每个答"退役了什么/是否新接缝")

R1 影响力指标:
  arXiv 引用 N | PyPI 下载 N | GitHub star N issue N PR N
  bench 外部 case N | 文章/inbound N
  增长复盘: (连续两季零增长? → 策略反思)

R3 判据门控:
  G1 ... G2 ... G3 ... G4 ... G5 ... G6 ... G7 ... G8 ...
  本季触发的门: ... → 推进任务: ...

R5 反目标自查:
  1-6 逐条: 通过 / 违反项 ...

R4 增量修订:
  纲领修订记录追加: v1.n — ...
  下季度重点: ...
```

---

### §11 首次实例：2026 Q3 复核（部分预填，季末补全）

> 基线数字见 §0（已采）。以下为 Q3 复核的**预填框架**，季末（9 月底）跑命令补全外部值 + 判门 + 自查。

- **R2 复杂度**：基线已采（files 286 / env 43 / jobs 40-46 / state 25 / tests 5448 / checks 839）。Q3 末计算相对本基线 delta。
- **R1 影响力**：命令见 §5，季末采集（arXiv/PyPI/GitHub）+ 用户填 inbound/文章。**首次采集值即"分母"**。
- **R3 门控**：G1（Observer flip，~7/7 优先判）/ G6（OpenClaw 三收敛判据跟踪）为 Q3 活跃门；其余未触发。
- **R5 反目标**：Q3 交付（纲领 + 本执行计划）均纯文档、判据门控自约束——预判反目标 #6（纲领膨胀）需重点自查：本执行计划是否"加节而未退役"？答：它退役的是"落实建议散落在纲领 §10 无法直接执行"的状态，把清单落成可跑命令 = 净负复杂度（可见性提升，零常驻机器）。
- **R4 修订**：Q3 末纲领追加 v1.1 修订记录。

**Q3 具体交付（对应 §1 H1 任务，本季度目标）**：
- [ ] A1 Observer flip 决策（P0，~7/7，G1）
- [ ] B1 PA 耦合机器化盘点 → `pa_coupling_inventory.md`（P0）
- [ ] D1 Mac Mini SLO 报告重生成（P1）
- [ ] A4 论文 #2 数据口径预注册（P0，镜像 §9.1 方法学）
- [ ] 季度复核 #1（9 月底）：跑本协议五项 + 纲领 v1.1

---

## 附：与既有文档的分工（一物一形，纲领 §11.1 延伸）

| 文档 | 角色 | 与本执行计划关系 |
|------|------|------------------|
| `technical_charter_20260705.md` | 方向 + 判据（单一真理源） | 本文档执行它，不复制判断 |
| **本文档** | 任务分解 + 复核运行手册 | 纲领的"怎么做" |
| `status.json` | 当前执行态 + 季度复核记录 | 复核结论落这里 |
| `complexity_budget.md` | 复杂度约定 | R2 的执行依据 |
| `llm_observer_design.md` §9.1 | Observer flip 预注册判据 | R3-G1 / A1 的判据源 |
| `gateway_upgrade_eval_v2026.4.md` §17 | OpenClaw 升级三判据 | R3-G6 的判据源 |
| CLAUDE.md | 操作手册 + changelog | 战略定位区放本文档指针 |

---

### 修订记录

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-07-05 | 初版（V37.9.248）。Part 1 纲领 H1/H2/H3 任务分解（H1 五组 15 任务带 done/gate/季度 + 依赖链图）+ Part 2 五项固定复核可运行协议（R1–R5 命令+判断+记录+模板）+ 2026 Q3 首次实例预填 + §0 基线快照。 |

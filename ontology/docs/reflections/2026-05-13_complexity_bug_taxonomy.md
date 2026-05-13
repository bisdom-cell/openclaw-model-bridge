# 复杂性 Bug 分类与治理反思 — 2026-05-13

> **场景**: V37.9.58 → V37.9.66 一周内连续闭环 8 个版本（含 5 个 hotfix）。表面看"系统在自我修复"，深层看是**复杂性累积到一定程度后，bug 不再是单点错误，而是多组件边界假设错配的涌现**。本次反思把这周的 bug 谱系系统化，识别真规律，制定主动避免策略。
>
> **触发**: 用户提问 _"随着系统的复杂性累积，显性报错和无意间发现了很多潜在的 bug，有什么好办法规避？是否需事前好的架构？还是事后全方位的测试？另外我们的子项目 ontology 是否可以有效规避？"_
>
> **目的**: 把单次 session 的反思**沉淀为可查文档**，让未来 Claude / 协作者 / 用户都能引用，避免反思本身只活在一次对话里。

---

## 一、本周 Bug 谱系（V37.9.58 → V37.9.66 真实数据）

### 类别 A — OS / Runtime quirk

| 版本 | Bug | 根因 |
|---|---|---|
| V37.9.58-hotfix3 | watchdog bsd awk multibyte 7 天 silent | macOS bsd awk 处理无效 UTF-8 字节抛 `towc: multibyte conversion failure` exit 1, pipefail + set -e 让 watchdog 整脚本 abort |
| V37.9.58-hotfix4 | watchdog hotfix3 修复后仍 silent | bash `set -e` 默认在 function 内 fail **不传播 ERR trap**, 需 `-E` (errtrace) |
| V37.9.60-hotfix | governance_audit / daily_ops / auto_deploy false-positive FATAL | `grep | head` pipe + pipefail + set -eE: grep no-match exit 1 → pipefail 全 pipe fail → ERR trap 误触发. 需 `|| true` 容错 |
| V37.9.56-hotfix2 | kb_evening 接收 `#` 当 DAYS | zsh `interactive_comments` 默认 OFF, `#` 被当 $1 传给 bash |
| V37.9.65 → V37.9.66 (用户实测) | zsh `*` glob 双引号外 expansion 失败 | zsh 不引号 `*` 会 glob expansion, 双引号外 + 无匹配 = "unknown file attribute" 错误 |

**共性**: dev Linux 跑过，Mac Mini macOS 才暴露。

### 类别 B — 设计假设错配

| 版本 | Bug | 假设 vs 现实 |
|---|---|---|
| V37.9.66 | `_format_cron_line` 拼 `~/jobs/...` | 假设 yaml entry 直接 `~/{entry}`, 现实 FILE_MAP 加 `.openclaw/` 前缀部署 |
| V37.9.66 | framework `line_contains_identifier` 不检 interval/log | 假设单向 sync 够用, 现实用户改 yaml 后 framework 报零漂移真 cron 没改 |
| V37.9.63 | 6 个 fatal_handler 第二层 FAIL-OPEN CLI 是死代码 | 假设用 `--channel-id` + `--content`, 现实 canonical CLI 是 `--target` + `--message` + `--json` |
| V37.9.4 (60 天前) | MOVESPEED rsync 一直 silent EPERM | 假设 APFS 重建解决 fskit EPERM, 现实 UID 错位 + noowners 掩盖 60 天 |

**共性**: 单看每个组件合理，组合后假设悄悄不一致。

### 类别 C — 自动批量工具盲点

| 版本 | Bug | 工具 |
|---|---|---|
| V37.9.58-hotfix | 8 ALIGNED jobs 全 NameError | `inject_level_4_to_aligned_jobs.py` 给 8 jobs 加 `prompt += os.environ.get(...)` 但未补 `import os` |
| V37.9.50-hotfix | semantic_scholar emit heredoc 缺 os | 同款单点 fix, 没机器化 |
| V37.9.57 | LEVEL_2 jobs 同款盲点未抓 | 同款盲点跨 N 文件传播 |

**共性**: 工具是 bug 放大器 — 单点设计错误立即传播到 N 文件。`bash -n` 看不到 Python heredoc 内部 NameError。

### 类别 D — 静默失败链

| 版本 | Bug | 静默层 |
|---|---|---|
| V37.9.58-hotfix3 | watchdog silent 7 天 | macOS bsd awk → pipefail → set -e → 没 ERR trap → cron 不发邮件 → 用户视角才发现 |
| V37.8.10 | LLM 错误链三层稀释 | adapter "ALL FALLBACKS FAILED" → proxy `str(HTTPError)` 不读 body → client `e.reason` 再次丢 body → 用户看到"HTTP 502: Bad Gateway" 全无原因 |
| V37.8.6 | Dream 自引用幻觉 | log 写 stdout 污染 `$(...)` → cache 污染 → Reduce LLM 读到错误日志 → 编造"Hugging Face 危机" |
| V37.8.16 | PA 自残 HEARTBEAT.md 13h | PA 写"任务完成"到 HEARTBEAT.md → Gateway 激活 heartbeat 模式 → Qwen3 回 HEARTBEAT_OK → stripTokenAtEdges 剥离 → 用户 13h 静默 |

**共性**: 错误不被吞, 但每层稀释一次, 用户看到的是"装饰过的成功"或"包装过的错误"。

### 类别 E — 修复引入新 Bug

| 版本 | 引入 Bug 的修复 | 触发 |
|---|---|---|
| V37.9.21 | send_wa_parts 函数末尾 `&& sleep 1` 短路返回 1 | set -e 杀 caller → kb_deep_dive 5 天 silent → V37.9.60-hotfix3 数据驱动诊断发现 |
| V37.9.58-hotfix3 | watchdog 加 trap ERR 但漏 set -E | V37.9.58-hotfix4 补 |
| V37.9.60-hotfix2 | kb_dream 路径修复 + 整数除法 | 引入两个新 bug → V37.9.60-hotfix3 揭露 |
| V37.9.65 | `grep -cF \|\| echo 0` 在无匹配输出"0\n0" | 开发期发现, 改 `\|\| true` |

**共性**: 单次 fix 改一行, 但破坏上层调用方对返回码 / 字段 / 路径的隐式契约。

---

## 二、深层规律 — 为什么这些 bug 不可避免

**核心观察**: 几乎所有 bug 都是 **多个看似无害的组合在边界场景同时出现**。

- V37.9.58-hotfix3 = bsd awk + pipefail + set -e + 无 ERR trap + 没 canary writer + 没 watchdog 自监控 (6 个条件)
- V37.9.66 = yaml entry 相对路径 + FILE_MAP `.openclaw/` 前缀 + `_format_cron_line` 拼 `~/{entry}` + framework `line_contains_identifier` 不检 interval + 用户改 yaml interval 真激活 (5 个条件)
- V37.8.16 PA 自残 = WhatsApp plugin 自动安装 + PA 有 write 工具 + HEARTBEAT.md 是 OpenClaw 保留文件 + PA 不知道特殊语义 + heartbeat 默认 prompt 严格 + PA 自写非空内容 (6 个条件)

**单看每个条件都"合理"**，组合后产生意外行为。这不是"代码质量差"，是**复杂系统不可避免的现象**。

问题不是"如何消灭所有 bug"（不可能），而是 **"如何把发现时间从用户/Mac Mini 实测后前置到代码合并前"**。

---

## 三、三层防御策略

### 层 1 — 架构层（事前预防）

**能预防**:
| 模式 | 元规则 | 实例 |
|---|---|---|
| 单一真理源 | MR-8 | V37.9.63 抽 fatal_handler helper 消除 90 行 copy-paste |
| 状态写走 helper | MR-9 | `crontab_safe.sh` / `kb_append_source.sh` 不直 `>>` |
| 三层验证深度 | MR-6 | critical 不变式 ≥2 层 (declaration + runtime) |
| 错误链透明 | INV-OBSERVABILITY-001 | proxy 读 `e.read()` body 不只 `str(HTTPError)` |
| 监控自观察 | MR-19 | 7 governed scripts 强制 trap ERR + canary heartbeat |

**无法预防**:
- `bsd awk multibyte` — OS 实现, 不是我们架构能选
- `bash set -e + 函数 fail 不传 ERR trap` — bash 60 年历史包袱
- 用户什么时候提"不要 vim" — 不可预测的用户视角

### 层 2 — 测试层（事后捕获）

**已捕获**（V37.9.66 现状）:
- 单测: 2853 tests / 83 suites
- 治理审计: 75 invariants / 629 checks / 19 meta rules
- 反向验证守卫: sabotage testing 证明守卫真有效非装饰
- 三层测试 (原则 #15): 单测 + Mac Mini preflight + WhatsApp 业务验证
- 全量回归: full_regression.sh 393→2853 tests 4 年增长

**漏掉**:
- **跨 OS quirk**: dev Linux 单测全过, Mac Mini bsd 工具 quirk 暴露
- **时间维度 bug**: kb_deep_dive 5 天 silent — 测试不能跑 5 天等
- **用户视角**: 单测不能模拟"用户在 WhatsApp 看到 freight 3 次/天过频"
- **自动批量工具语义层**: `bash -n` 看不到 Python heredoc 内部 NameError
- **涌现行为**: log→stdout + `$()` + cache + LLM 四层独立都正常, 组合产生幻觉

### 层 3 — Ontology 治理层（机器化主动监控）

**已规避的 bug 类**（硬证据 INV-* + 兑现版本）:

| INV | 防御的 Bug 类 | 兑现 |
|---|---|---|
| INV-WATCHDOG-SELF-001 | watchdog silent abort | V37.9.58-hotfix3 → V37.9.59/60/61/63 framework 化扩展 |
| INV-HEREDOC-IMPORT-001 | 自动批量工具 import 漏 | V37.9.58-hotfix2 scanner 主动扫所有 heredoc |
| INV-CRON-MONITOR-001 | cron monitor silent | 7 governed scripts 强制 trap ERR + helper |
| INV-CONVERGENCE-CRON-001 | _format_cron_line path bug | V37.9.66 守卫, 未来重构回退立即 fail |
| INV-PA-001 / INV-HB-001 | PA 告警污染 / 保留文件污染 | V37.4.3 + V37.8.16 |
| INV-OBSERVABILITY-001 | LLM 错误链稀释 | V37.8.10 错误必须包含 upstream cause |
| MR-4 silent-failure | 错误被吞 | 已 26 次演出, 每次转化为新不变式 |
| MR-19 monitor-self-alarm | 监控自身死亡 | watchdog → 7 governed scripts → 未来新增 monitor 自动 enforce |

**当前规避不了的**:
- 用户视角 gap: ontology 不知道用户嫌 vim 体验差 / 嫌推送过频 / 嫌内容浅
- 新 runtime quirk: bsd awk multibyte 是 V37.9.58-hotfix3 发生后才加守卫
- 全新 framework gap: V37.9.66 path bug + 单向 sync 是用户实测发现的

**Ontology 的真正价值**: 不是预防所有 bug, 是 **把"每一次血案的教训机器化为不变式 + 元规则 + scanner"**, 让同款问题不再发生。这是迭代式治理 — 75 不变式 / 629 checks 都是这样一条条长出来的。

---

## 四、未来主动避免策略（5 项具体行动）

### 🟢 行动 1: 跨 OS 测试矩阵 — INV-CROSS-OS-001（V37.9.67+ P0）

**问题**: 这周大半 bug 是 macOS bsd 工具 quirk, dev Linux 测不出。

**方案**:
- dev 环境 docker 镜像装 BSD-mode tools (gnu-coreutils `--bsd-compat`)
- 关键 cron 脚本加 OS-specific 单测组合: `LC_ALL=C` + `set -eEo pipefail`
- shell 函数禁 zsh-specific 语法 (V37.9.56-hotfix2 教训)
- scanner 扫 `awk` / `sed` / `date` 是否带 OS-portable 选项
- INV-CROSS-OS-001 declaration: 任何 cron 脚本必须显式声明已 dev BSD-mode 测过

**预期防御**: V37.9.58-hotfix3 / V37.9.60-hotfix / V37.9.56-hotfix2 等 OS quirk 类 bug

### 🟢 行动 2: 路径一致性 audit — INV-PATH-CONSISTENCY-001（V37.9.67 直接立）

**问题**: yaml entry / FILE_MAP / runtime crontab path 三方不一致是 V37.9.66 暴露的真 gap。34 个 jobs 都没 audit 过。

**方案**:
- INV-PATH-CONSISTENCY-001 守 34 jobs 三方对齐
- scanner: `jobs_registry.yaml entry` ∈ `auto_deploy FILE_MAP src` 且 `_format_cron_line({entry})` ∈ Mac Mini runtime crontab line
- 不对齐 → governance audit fail
- 顺便完成 V37.9.67 候选 (spec yaml 切换 cron_lines_set_diff 真激活双向 sync)

**预期防御**: V37.9.66 类 path bug

### 🟡 行动 3: 失败模式库 (`failure_modes_catalog.md`) — 一周内立

**问题**: 未来每个新 session / 新协作者重复踩同样的坑。当前血案散落在 ontology/docs/cases/ 17 个独立文档, 没分类索引。

**方案**: 创建 `ontology/docs/failure_modes_catalog.md` 把所有血案分类索引:
- 类别 A: OS quirk (bsd awk / bash set -e / zsh glob / APFS noowners) — 5 个血案
- 类别 B: 设计假设错配 (yaml/runtime path / framework 单向) — 4 个血案
- 类别 C: 自动批量工具盲点 — 2 个血案
- 类别 D: 静默失败链 — 6 个血案
- 类别 E: 修复引入新 bug — 4 个血案

每条列: 触发条件 + 检测方法 + 元规则 + INV + 已发生次数 + 案例文档链接。

**预期防御**: 新 session 开工读 catalog → 看到本周类似工作前先查防御策略

### 🟡 行动 4: MR-18 真激活 — 一周内做

**问题**: V37.9.58-hotfix2 立了 MR-18 + heredoc_import_scanner, 但 inject 工具**没自己集成 scanner** — 还是靠 governance audit 事后抓。

**方案**: 每个 `inject_*.py` / `migrate_*.py` / `batch_*.py` 工具必须:
1. 注入前 dry-run AST 解析验证
2. 注入后内嵌 `heredoc_import_scanner.scan_file()` semantic validation
3. 失败整批 rollback (不留半改半未改状态)
4. 工具自身加 source-level 守卫: import scanner 必须出现

**INV-AUTO-INJECT-001**: 任何 inject_*.py 工具必须 self-validate (V37.9.58-hotfix2 MR-18 真兑现).

**预期防御**: V37.9.57 类自动批量工具盲点

### 🔵 行动 5: 用户视角观察制度真执行（原则 #32 加固）

**问题**: 原则 #32 立了周一 30min 用户视角观察, 但**执行不彻底** — 这周好多 bug 是用户主动反馈才发现 (vim 体验差 / freight 3 次过频 / framework 零漂移但 cron 没改)。

**方案**:
- 每周一开工**第一件事**做 30min 观察, **不写代码不修 bug**
- 检查 4 维度:
  - 告警噪声 (WhatsApp + Discord #告警 频率/重复度/可读性)
  - 推送延迟 (cron job 到手机延时)
  - 信息密度 (推送内容是否真有用)
  - PA 表现 (实际使用时 PA 答非所问 / 慢 / 错乱)
- 发现项立即 `status_update.py --add unfinished` 不当场修
- **持续坚持 4 周** → 立 `INV-WEEKLY-USER-OBSERVATION-001` 守卫"周一观察项必有"

**预期防御**: 用户视角 gap (类别 E 反过来) — 把 reactive feedback 升级为 proactive observation

---

## 五、心态 — "下次同款不再"

> **今天发生的所有 bug 不是耻辱, 是 ontology 治理体系的食物。**

每一次 bug 必须完成的闭环:
1. **复盘三层根因** (触发器 / 放大器 / 掩护者)
2. **抽象为元规则** (e.g. MR-19 monitor-must-self-alarm)
3. **写成 INV-* 不变式** (declaration + runtime 双层)
4. **可能时立 scanner** (auto detection 反 reactive 监控)
5. **加反向验证守卫** (sabotage 证明守卫真有效)

这就是 ontology 治理体系从 V36.2 **17 不变式** 长到 V37.9.66 **75 不变式 / 629 checks / 19 meta rules** —— 每一次"血案"都是 framework 进化的硬实证。

**真正的目标不是"消灭所有 bug"**（不可能），是:
1. **首次发生的 bug** 必须**复盘到三层根因**（不是修症状）
2. **同款 bug 不再发生**（机器化守卫强制）
3. **未发生的 bug 主动识别**（adversarial audit / 用户视角观察 / scanner）

---

## 六、Ontology 子项目在这个目标里的位置

**V3 路标 `pip install ontology-engine` 终极目标**:

让**任何 Agent Runtime 项目**都能通过:
```bash
pip install ontology-engine
```
+ 一个项目级 YAML 配置 (`tool_ontology.yaml` + `governance_ontology.yaml`)

获得:
- 工具治理 + 语义查询
- governance 审计 (declaration + runtime + 三档特性开关 off/shadow/on)
- 反向验证守卫框架
- failure_modes 主动监控

这是从"我们自己用"升级为"行业可用"的关键。当前 75 不变式都是 **"我们项目的具体血案 + 我们项目的具体 INV"**, V3 目标是 **抽象出来让别人也能套用**。

---

## 七、一句话总结

> **架构防"已知模式", 测试防"已知场景", ontology 防"已知教训重演" — 三层缺一不可。**
> 
> 但 framework 真正的护城河是 **"每次血案都转化为下次的免疫力"的迭代机制** — 这是当前 75 inv / 629 checks 已经在做的, 也是 V3 路标 `pip install ontology-engine` 的核心承诺。

---

## 八、登记到 status.json 的 V37.9.67+ 候选

本次反思转化为 5 个具体行动, 已登记 status.json `unfinished`:

1. INV-CROSS-OS-001 — 跨 OS 测试矩阵 (P0)
2. INV-PATH-CONSISTENCY-001 — 路径一致性 audit + V37.9.67 真激活双向 sync (P0)
3. `failure_modes_catalog.md` — 失败模式库 (P1, 一周内)
4. MR-18 真激活 — inject 工具 self-validate (P1, 一周内)
5. 原则 #32 用户视角观察制度真执行 — 4 周后立 INV-WEEKLY-USER-OBSERVATION-001 (P2)

---

## 参考

- V37.9.58 → V37.9.66 commit history (5 hotfix + 5 主版本)
- `ontology/docs/cases/` (17 个具体血案文档)
- `ontology/governance_ontology.yaml` (75 invariants / 629 checks / 19 meta rules)
- 原则 #28 (理解再动手) / #29 (零遗漏) / #32 (周一观察) / #13 (定期像用户用)
- 元规则 MR-4 (silent-failure) / MR-6 (验证深度) / MR-7 (治理自观察) / MR-8 (single-source-of-truth) / MR-9 (state-writes-helper) / MR-10 (understand-before-fix) / MR-17 (declared↔runtime) / MR-18 (auto-batch validate) / MR-19 (monitor-self-alarm)

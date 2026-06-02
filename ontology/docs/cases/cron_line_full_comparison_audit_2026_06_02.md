# Cron-Line 全行比较审计 — 为什么不切换 cron_lines_set_diff

> 日期: 2026-06-02 (V37.9.98 session) · 决策者: Claude Code + 用户授权
> 关联: unfinished "convergence framework cron 行完整比较升级" / V37.9.66 双向 sync primitives / INV-CONVERGENCE-CRON-001
> 类型: 设计决策审计（非血案）—— 数据驱动地决定**不做**某个升级

---

## TL;DR

`jobs_to_crontab` convergence spec 当前用 `line_contains_identifier` parser
（检脚本 basename 是否出现在某条 crontab 行）。V37.9.66 实现了全行比较
primitives（`_extract_jobs_to_full_cron_lines` + `_parse_cron_lines_set_diff`
+ `ConvergenceResult.extra_in_runtime`）但**故意没切换**，留下 unfinished
"切换 parser 真激活（需先审计 34 job 路径三方一致性）"。

2026-06-02 在 Mac Mini 真实 crontab 上跑了这个审计。结论：

**绝不切换 `cron_lines_set_diff`。保持 `line_contains_identifier`。**

数据：`declared=37 observed=38 missing=17 extra=18`。把 17 missing 逐一配对
18 extra，**没有一条是"脚本真缺失"**——全是功能等价的格式变体（15 条）+ 2 条
真差异（都不是 set-diff 设计要抓的"脚本缺失/多余"语义）。切换会让 governance
audit 每次报 17+18 false drift；machine_sync 真激活会加 17 重复 + 删 18 "多余"
= **搞坏正常工作的 crontab**。

---

## 审计方法

```python
# Mac Mini, repo at main, V37.9.66 primitives 已部署
import sys, subprocess
sys.path.insert(0, 'ontology')
import convergence as cv
spec = {"declaration": {"source": "jobs_registry.yaml"}}
declared = cv._extract_jobs_to_full_cron_lines(spec)   # _format_cron_line 生成 37 条声明行
raw = subprocess.run(['crontab','-l'], capture_output=True, text=True).stdout
observed = cv._parse_cron_lines_set_diff(spec, raw, declared)  # 真 crontab 38 条
missing = declared - observed   # 声明行未逐字命中
extra   = observed - declared   # crontab 行无声明匹配
```

`cron_lines_set_diff` 是**纯字面集合比较**——任何空格/引号/路径/重定向差异都让
同一 job 同时进 missing + extra。

---

## 配对分析: 17 missing × 18 extra 全是格式变体

| 差异类型 | 数量 | 例子 (declared → real crontab) |
|---|---|---|
| 单引号 → 双引号 | 7 | `bash -lc 'bash ~/X'` → `bash -lc "bash ~/X"` (chaspark / mm_index / movespeed_daily_sync / daily_observer / finance_news / job_watchdog / slo_snapshot) |
| 缺 log 重定向 | 2 | `bash ~/cron_canary.sh >> ~/.cron_canary_log 2>&1` → `bash ~/cron_canary.sh` (canary/wa_keepalive 脚本自身写日志) |
| `~/` vs `$HOME/` 字面量 | 1 | `~/kb_status_refresh.sh` → `$HOME/kb_status_refresh.sh` |
| `mkdir -p ...; ` 前缀 + 直接 exec + 双引号 | 3 | `bash -lc 'bash ~/.openclaw/jobs/X/run.sh >> log'` → `mkdir -p $HOME/.openclaw/logs/jobs; bash -lc "$HOME/.openclaw/jobs/X/run.sh >> log"` (github_trending / rss_blogs / ai_leaders_x) |
| cd 前缀 / 重定向在 `bash -lc` 引号外 / 无 bash | 2 | `bash -lc 'python3 ~/kb_harvest_chat.py >> log'` → `bash -lc "cd ~/openclaw-model-bridge && python3 kb_harvest_chat.py" >> log` ; governance_audit `bash -lc 'bash ~/X >> log'` → `bash -lc "~/X" >> log` |
| 路径 repo vs $HOME | 1 | `bash ~/auto_deploy.sh` → `bash ~/openclaw-model-bridge/auto_deploy.sh` (auto_deploy 从 repo 跑) |
| **真实参数差异 (registry 未记)** | 1 | `python3 ~/preference_learner.py >> log` → `python3 ~/preference_learner.py --apply --days 7 >> log` |
| **registry 外手动条目** | 1 | `30 23 * * * bash -c 'rsync -av --delete $HOME/.kb/ /Volumes/MOVESPEED/KB/ ...'` (与 `0 4` movespeed_daily_sync 功能重复) |

**15 条功能等价格式变体 + 2 条真差异。0 条"脚本真缺失"。**

---

## 三层根因 (为什么 _format_cron_line 永远无法逐字匹配)

1. **触发器 — registry `entry` 字段是简化抽象**: jobs_registry 只存
   `interval / entry (脚本相对路径) / log`，不存真实命令的 args（如
   preference_learner `--apply --days 7`）、cd 前缀（kb_harvest_chat）、
   quote-style、`mkdir -p` 前缀。`_format_cron_line` 只能从这 3 字段拼，
   信息天然不足以重建真实命令行。

2. **放大器 — 生产 crontab 是多版本手工演化的产物**: 这些条目跨 V27→V37.9
   由不同 session 用不同写法（单/双引号、~/$HOME、redirect 内/外）注册，
   功能都对但字面五花八门。没有任何一次强制过"统一格式"。

3. **掩护者 — line_contains_identifier 一直工作得很好**: identifier 匹配
   只关心"脚本 basename 在不在某行"，对格式变体完全免疫，且精确捕获真正
   重要的故障类（V37.9.18 血案: 脚本声明 enabled 但 crontab 没注册）。
   全行比较想多抓的"interval drift / 多余条目"价值，被它带来的 17 条
   false drift 风险远远盖过。

---

## 决策

| 选项 | 评估 | 结论 |
|---|---|---|
| A. 切 cron_lines_set_diff | 17+18 false drift / machine_sync 会搞坏 crontab | ❌ 否决 |
| B. 重写生产 crontab 统一格式后再切 | 高风险改 37 条生产 cron，收益仅"格式整齐"（功能本就对）| ❌ 不值 |
| C. 加 fuzzy-match parser (归一化引号/~/redirect 后比较) | 仍会因 registry 不记 args（preference_learner）/cd（kb_harvest）误报；要先做 registry↔crontab 命令完整性对账 = 更大工程 | ⏸ 未来候选，非今天 |
| **D. 保持 line_contains_identifier (现状)** | identifier 匹配免疫格式变体 + 精确抓"脚本缺失" + missing=0 零误报 | ✅ **采纳** |

**保持 D。** V37.9.66 primitives 保留在代码库（向后兼容 + 未来 C 选项的基础），
但 jobs_to_crontab spec 的 parser 不切换。

---

## 顺带发现的 2 条真差异 (登记，非本次修)

1. **preference_learner 真跑 `--apply --days 7`，jobs_registry entry 没记这些 args**
   —— registry `entry: preference_learner.py` 是简化。功能正常（cron 实跑带 args），
   但 registry 不是命令的完整真理源。未来若做 registry↔crontab 对账需补 args 字段。

2. **`30 23` 一条独立 rsync→MOVESPEED 手动 cron，与 `0 4` movespeed_daily_sync 功能重复**
   —— 两条都 rsync `~/.kb/` 到 MOVESPEED，一条经脚本（0 4）一条裸 rsync（30 23）。
   可能是有意双备份时间，也可能是冗余。需用户确认是否清理 `30 23` 那条。

---

## 元教训

1. **声明式全行同步在"手工演化的异构生产状态"上不可行** —— 除非从第一天就强制
   统一格式 + registry 捕获完整命令。这是 declarative convergence framework 的
   真实边界，值得对外叙事（V3 路标话语权: "为什么我们的 cron sync 用 identifier
   匹配而非全行 diff"）。

2. **"实现了 primitive" ≠ "应该用 primitive"** —— V37.9.66 实现全行比较能力是对的
   （留作未来基础），但是否启用必须用真生产数据决策。本次审计正是 MR-10
   (understand-before-fix) 在"升级决策"层的应用: 先量化再决定，不为了"功能完整"
   而引入 false-drift。

3. **identifier 匹配是务实的最优解** —— 它精确覆盖了真正的血案类（脚本缺失），
   对一切无害的格式异质性免疫。"够用且鲁棒" 胜过 "精确但脆弱"。

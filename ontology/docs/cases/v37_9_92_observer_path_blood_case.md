# V37.9.92 Observer Path 血案：5 天潜伏 + MR-15 第 4 次演出 + 自我修复闭环

> 2026-06-01 闭环 | MR-4 silent-failure 双重演出 | MR-15 第 4 次演出 | 催生 INV-CROSS-ENV-PATH-001 (V37.9.94 立)

## TL;DR

V37.9.84 (5/27) 上线 Daily Self-Critique Observer 时设计承诺"三方共享意识锚点": observer 分数应写到 `~/.kb/status.json` `quality.observer` 让 PA/cron 可见. V37.9.88 (5/29) 加 registry filter 让 observer 只扫 enabled jobs. **但两个改动在 Mac Mini 真生产同时 silent 5 天**:

- V37.9.88 LAYER 1: `_resolve_registry_path()` 三个 candidate (env / `$HOME/X` / script-adj) **全 miss** Mac Mini 真实位置 `$HOME/openclaw-model-bridge/jobs_registry.yaml` (auto_deploy FILE_MAP 不把 yaml 拷到 `$HOME`). 每天 06:30 cron `WARN: registry filter fallback` 但 stderr 不告警 → 仍扫 15 jobs (含 3 个 disabled), 假警从未消除.
- V37.9.84 设计闭环: `daily_observer.py` 从未写入 status.json, `quality.observer = {}` 空字典 5 天累计. PA 无法读 observer 分数. 三方共享承诺零兑现.

**5 天没有任何错误日志触发告警, 只有 stderr WARN 沉睡在 daily_observer.log 里**, 直到 6/1 周一开工 Observer 自己 5/31 LLM critique 主动提及 stale 警告埋底问题, 才驱动我去深查 V37.9.84 整个数据链路, 发现两个潜伏问题. 同 session V37.9.92 完整闭环, V37.9.93 修 Observer 自身 sampling artifact, V37.9.94 立 INV-CROSS-ENV-PATH-001 framework 化 MR-15 防御.

**Observer 设计为发现别人的 silent failure, 自己却 silent 了 5 天 — 但最终 Observer 自己的 LLM critique 反过来驱动了对自身的修复**. 这是 MR-7 (治理自观察) 与 MR-15 (deployment-layout-must-be-tested-on-target) 同时达到最高境界的同 session 三连闭环.

## 影响

- **5 天潜伏期** (2026-05-27 → 2026-06-01)
- **零业务告警** — observer log 显示 ✅ "critique complete (score=5)" 每日成功, daily_critique markdown 正常生成, score_history.jsonl 正常 append. 表面一切正常.
- **2 个真问题潜伏**: (a) registry filter 失效让 anomalies 含 3 个 disabled job 假警 (pwc/karpathy_x/openclaw_official) (b) status.json quality.observer 永远空字典
- **同 session 5 个版本闭环** (V37.9.92 + V37.9.93 + V37.9.94 三个 commit, 含 V37.9.93 自我发现修复)
- **scanner 首扫意外发现 V37.9.91 expert_escalation.load_status 同款隐患 (MR-15 5th near-miss)**, 顺手修

## 最小修复 (按 V37.9.92 Tier 2 scope)

### V37.9.92 (Part 1 — V37.9.88 path)
- `daily_observer.py::_resolve_registry_path()` candidates 加第 4 个: `os.path.expanduser("~/openclaw-model-bridge/jobs_registry.yaml")`
- 顺序锁: env override → $HOME root → **Mac Mini canonical (V37.9.92 新)** → script-adj

### V37.9.92 (Part 2 — status.json 闭环)
- 新增 `_write_observer_to_status(kb_dir, target_date, overall_score, anomalies, status, job_statuses) -> bool` helper (~50 行)
- `run()` 在 `append_score_history()` 之后调用 (顺序锁, source-level test 守)
- FAIL-OPEN: lazy import `status_update`, try/except 不冒泡
- 走 `status_update.save_status()` 原子 tmpfile + os.replace (MR-9 helper compliant)
- 写 9 字段: score / status / anomalies_high / anomalies_med / jobs_ok / jobs_total / last_run_date / last_updated_at / v37_9_92: True

### V37.9.93 (顺势发现的 Observer 自身 bug)
- Observer 修好 path bug 后, 5/31 LLM critique 仍报 `[MED] dream 输出截断 (末尾为 'AgentScope 1')`
- 取证: 5/31 dream 文件 14750 bytes 完整含 footer marker. Dream 没截断, **Observer 在误报**
- 真根因: `_read_file_sample` `MAX_SAMPLE_CHARS=2000` 只截前 2000 chars 给 LLM 看, 第 2000 字符位置恰在 "AgentScope 1.0" 中间, LLM 看到自己输入末尾停 "AgentScope 1" 合理推断"文件截断"
- 修复: smart head + 中间 marker + tail sampling + `CRITIQUE_SYSTEM` 加 V37.9.93 采样说明段明示 LLM 不要把 sampling 当文件截断

### V37.9.94 (framework 化预防 MR-15 第 5 次)
- 新文件 `cross_env_path_scanner.py` (~250 行) — AST 扫所有 `_resolve_*_path` 函数, 检测 `~/<config>.yaml` + script-adj fallback 模式但缺 `~/openclaw-model-bridge/<file>` canonical
- FAIL-CLOSE: 任一 violation → exit 1
- INV-CROSS-ENV-PATH-001 (meta_rule=MR-15, severity=high, verification_layer=[declaration, runtime], 9 checks)
- 接入 full_regression
- **首次 scan 立即抓 1 个真 violation**: `expert_escalation.load_status` 有 `~/status.json` + script-adj 但缺 canonical (script-adj 在 Mac Mini = `$HOME/status.json` 重合 candidate 2, PATH 重合 bug 同款 V37.9.76-hotfix), 顺手修

## 时间线还原

```
日期/时间               事件                                              影响
─────────────────────────────────────────────────────────────────────────────────
2026-05-27  09:57   V37.9.84 Daily Observer 上线 commit                   设计就位
                    "READ-ONLY: 只读取 ~/.kb/ 和 jobs/"
                    "输出: ~/.kb/self_critique/daily_critique_*.md"
                    "三方共享意识锚点 quality.observer" (设计承诺)         未实现 ⚠️
                    
2026-05-29  06:30   V37.9.88 registry filter 上线后第一次 cron            silent fallback
                    [observer] WARN: V37.9.88 registry filter fallback     ⚠️ 但 stderr
                    [observer] jobs: 15 scanned (应 12)                    不触发告警
                    
2026-05-29  09:57   V37.9.88 收工 commit                                  自信 changelog
                    "Mac Mini 部署后 observer 扫 12 enabled"
                    实际从未生效                                          ⚠️ 假承诺
                    
2026-05-29 → 06-01  5 天 06:30 cron 连续 fallback warn                   每日 silent
                    daily_critique markdown 正常生成 score 4-5            表面正常
                    score_history.jsonl 正常 append                       persistence OK
                    push 推送正常 (notify.sh 双通道)                      用户感知 OK
                    quality.observer = {}                                 三方承诺零兑现
                    
2026-06-01  09:00   周一开工 用户问 Observer 5 天数据                     启动复盘
                    
2026-06-01  09:15   probe ~/.kb/score_history.jsonl                       发现真问题
                    + ~/.kb/self_critique/daily_critique_*.md
                    + observer log
                    "WARN: V37.9.88 registry filter fallback" × 5 天
                    "quality.observer = {}" 永远空字典
                    
2026-06-01  09:18   Mac Mini 手动验证 V37.9.92 修复后                     双闭环成功
                    [observer] V37.9.88 registry filter:                  ✓ Bug 1 修
                      excluded 3 disabled job(s): pwc,karpathy_x,...
                    [observer] jobs: 12 scanned                            ✓ 真生效
                    quality.observer = {score:5, anomalies_high:0,         ✓ Bug 2 修
                      anomalies_med:0, jobs_ok:12, ..., v37_9_92:true}
                    
2026-06-01  10:00   V37.9.93 Smart Sampling 修 Observer 自身             同 session 第 2 修复
                    Observer LLM critique 5/31 报 "dream 截断"
                    取证: dream 文件 14750 bytes 完整含 footer
                    真因: MAX_SAMPLE_CHARS=2000 让 LLM 看不到 footer
                    修: head + 中间 marker + tail + CRITIQUE_SYSTEM 明示
                    
2026-06-01  11:00   V37.9.94 立 INV-CROSS-ENV-PATH-001                   同 session 第 3 闭环
                    scanner 主动扫 framework
                    首次 scan 抓 expert_escalation.load_status            framework 级预防
                    第 5 次 MR-15 near-miss, 已修
```

## 完整因果链架构图

```
【时间线 × 层级 × 逻辑 × 架构】

V37.9.84 上线
(2026-05-27)
    │
    │   设计承诺:
    │   ├─ "READ-ONLY: 只读取 ~/.kb/ 和 jobs/" ← 真实现 ✓
    │   ├─ "score_history.jsonl 持久化" ← 真实现 ✓
    │   ├─ "daily_critique_*.md markdown 输出" ← 真实现 ✓
    │   ├─ "三方共享意识锚点 (status.json quality.observer)" ← **未实现** ⚠️
    │   └─ "LLM-as-judge 第三方视角" ← 真实现 ✓
    │
V37.9.88 上线 (LAYER 1 + LAYER 2 同时落)
(2026-05-29)
    │
    │   LAYER 1 (registry filter):
    │   ├─ _resolve_registry_path() 三档:
    │   │   ├─ env OBSERVER_REGISTRY_PATH ← 未设置
    │   │   ├─ $HOME/jobs_registry.yaml ← Mac Mini 不存在 ⚠️
    │   │   └─ script-adj dirname(__file__)/jobs_registry.yaml ← Mac Mini 是 $HOME 重合 ⚠️
    │   │
    │   │   ❌ 三档全 miss 真实位置 $HOME/openclaw-model-bridge/jobs_registry.yaml
    │   │      (auto_deploy FILE_MAP 不拷 yaml 到 $HOME)
    │   │
    │   ├─ 触发 fallback path: log("WARN: registry filter fallback...")
    │   ├─ → JOBS_SUBDIRS 硬编码 15 jobs 全扫 (含 disabled pwc/karpathy_x/openclaw_official)
    │   └─ 但 WARN 在 stderr, 不触发 [SYSTEM_ALERT] 告警链
    │
    │   LAYER 2 (stale + HIGH suppression):
    │   └─ ✅ 独立工作 — stale jobs 仍被检测 MED + HIGH 抑制 (V37.9.88 LAYER 2 设计成功)
    │
    │   ↓ 5 天累积:
    │   ├─ score_history.jsonl 正常 append (每天 1 行 post-V37.9.87 single-call)
    │   ├─ daily_critique_*.md 正常生成 (LLM-as-judge 工作)
    │   ├─ push 推送正常 (Discord #daily + WhatsApp)
    │   ├─ anomalies_high 0-2 / anomalies_med 0-3
    │   ├─ score 4-5 (健康)
    │   └─ quality.observer = {} 永远空 (V37.9.84 承诺零兑现)
    │
    │   表面: 一切正常 ✓
    │   实质: 真生产 LAYER 1 失效 + 设计承诺零兑现 = 双 silent failure 5 天
    │
2026-06-01 周一开工
    │
    │   用户选方向 A "Observer 5 天数据驱动复盘"
    │   → 让我去探 ~/.kb/score_history.jsonl + observer_reports + log
    │
    │   第 1 轮探针 (probe 1)
    │   ├─ 我误以为 ~/.kb/score_history.jsonl 直接位置 (实际在 self_critique/ 子目录)
    │   └─ 报误警: "score_history 空" "observer_reports 空"
    │       ↑ 我自己也走 silent failure (probe 路径假设错)
    │
    │   第 2 轮探针 (probe 2 — 找真路径)
    │   ├─ 发现 ~/.kb/self_critique/score_history.jsonl 10 行健康数据
    │   ├─ 发现 ~/.kb/self_critique/daily_critique_*.md 5 天完整报告
    │   ├─ 发现 ~/openclaw-model-bridge/jobs_registry.yaml 真实位置
    │   ├─ daily_observer.py 内 _resolve_registry_path 三档源码
    │   └─ 真相: V37.9.88 path bug + V37.9.84 quality.observer 未实现 双 silent
    │
    │   ↓
    │
V37.9.92 同 session 修复
    │
    │   Part 1 (path): _resolve_registry_path 加第 4 candidate
    │       "$HOME/openclaw-model-bridge/jobs_registry.yaml"
    │       
    │   Part 2 (status.json closure):
    │       新增 _write_observer_to_status() FAIL-OPEN helper
    │       run() 在 append_score_history 之后调一次
    │       9 字段写入 status.json:quality.observer
    │
    │   27 新单测 4 类 (含反向 sabotage 守卫真有效)
    │
    │   Mac Mini 09:18 实测:
    │   ├─ [observer] V37.9.88 registry filter: excluded 3 disabled jobs ✓
    │   ├─ [observer] jobs: 12 scanned ✓
    │   ├─ quality.observer = {score:5, jobs:12/12, v37_9_92:true} ✓
    │   └─ status.json updated 09:18:44 by daily_observer ✓
    │
    │   双 silent failure 同时闭环 ← V37.9.92 Tier 2 完整兑现
    │
    ↓
    │
V37.9.93 顺势自我修复 (同 session 第 2 阶段)
    │
    │   Observer 修好 path bug 后, 5/31 LLM critique 仍报:
    │   "[MED] dream 输出截断 (末尾为 'AgentScope 1')"
    │
    │   ↓ 取证 5/31 dream 文件
    │   ├─ wc -c: 14750 bytes
    │   ├─ tail -c 200: "*Generated by kb_dream.sh v2 ...* every signal counts.*"
    │   └─ ✓ 文件完整 4 段 + footer marker — Dream 没 bug
    │
    │   ↓ 那 LLM 看到的"截断"是什么?
    │   ├─ grep _read_file_sample → MAX_SAMPLE_CHARS = 2000
    │   ├─ return content[:max_chars], len(content)
    │   └─ ✗ LLM 只看前 2000 字, 末尾停在 "AgentScope 1" 合理推断"截断"
    │
    │   真根因: Observer 自己的 sampling 把"自己看的截断"
    │   传递给 LLM, LLM 解读为"文件被截断" — false positive
    │
    │   修复 (双层防御 V37.9.93):
    │   ├─ Smart head+tail sampling (1400+marker+500 = ≤MAX)
    │   │   LLM 现能看到 footer 验证完整性
    │   └─ CRITIQUE_SYSTEM 加 V37.9.93 采样说明段
    │       明示 LLM 不要把 sampling 截断当文件截断
    │
    │   18 新单测 3 类 + 反向 sabotage (4+2 fails)
    │
    │   Mac Mini 实测:
    │   ├─ smart sampling 真生效 (omitted=4882 chars 数学完美)
    │   ├─ Observer 不再报 "file truncation" ✓
    │   └─ LLM 反过来报 "marker confusion risk" (合理谨慎, 接受)
    │
    │   Observer 自己发现自己的 sampling 限制 → 自我修复 ← V37.9.93
    │
    ↓
    │
V37.9.94 framework 化 MR-15 防 5th occurrence (同 session 第 3 阶段)
    │
    │   MR-15 演出史:
    │   ├─ V37.9.56-hotfix: top_alignment_picker
    │   ├─ V37.9.76-hotfix: router_decide  
    │   ├─ V37.9.78-hotfix: health_check
    │   └─ V37.9.92:        daily_observer  ← 4 次达"立 INV"门槛
    │
    │   新文件 cross_env_path_scanner.py (~250 行 AST scanner):
    │   ├─ 扫所有 _resolve_*_path 函数 + 任何 `~/<config>.yaml` + script-adj 模式
    │   ├─ 检查是否含 `~/openclaw-model-bridge/<same_file>` canonical
    │   └─ FAIL-CLOSE: 任一 violation → exit 1
    │
    │   INV-CROSS-ENV-PATH-001 (9 checks, meta_rule=MR-15)
    │   接入 full_regression
    │
    │   ↓ 首扫意外发现:
    │   └─ ❌ expert_escalation.load_status (V37.9.91 wiring 时引入)
    │       3 candidates: $kb_dir/X + $HOME/X + script-adj/X (后两个重合)
    │       缺 ~/openclaw-model-bridge/status.json canonical
    │       ⚠️ MR-15 第 5 次潜在演出
    │
    │   顺手修 expert_escalation.load_status:
    │   └─ candidates 4 档: $kb_dir + $HOME + canonical (V37.9.94 新) + script-adj
    │
    │   ↓ 再扫:
    │   └─ ✅ 0 violations across all 62 Python files
    │
    │   未来任何新 resolver 漏 canonical → scanner exit 1 + INV 失败
    │   ← 把"修血案"升级为"机器化预防" framework 价值
```

## 三层根因

| 层 | 描述 | 影响 |
|---|---|---|
| **触发器** | V37.9.88 上线时 `_resolve_registry_path()` 候选列表只覆盖 dev 环境 + Mac Mini 旧路径假设, 没覆盖真实 Mac Mini 部署位置 `$HOME/openclaw-model-bridge/jobs_registry.yaml` (auto_deploy FILE_MAP 决定的真实部署 layout) | LAYER 1 在 Mac Mini 真生产**永远不工作** |
| **放大器** | (a) LAYER 1 失效时只 stderr `log("WARN: ...")`, 不推 [SYSTEM_ALERT] 告警链 (b) LAYER 2 (stale 检测) **独立工作**让 anomalies 表面看起来正常, 掩盖 LAYER 1 失效 (c) score_history / daily_critique / push 全部 OK 让操作者看不到任何异常 | 5 天 silent 不被发现 |
| **掩护者** | (a) V37.9.84 设计承诺 "quality.observer = {score, ...}" 但 daily_observer.py 从未写入 status.json, 也无任何 check 守这个承诺 (b) auto_deploy FILE_MAP 对 jobs_registry.yaml 不拷到 $HOME 是合理设计, 但脚本 resolver 假设错 (c) MR-15 deployment-layout 已 3 次演出, 但没立 framework 级 scanner, 全靠"下次记得加 canonical" | 第 4 次重演 + 设计承诺 5 天零兑现 |

## 多条件组合分析

| 条件 | 之前 | 现在 (V37.9.88 → V37.9.92) |
|---|---|---|
| Observer 上线 | ✗ 不存在 | ✓ V37.9.84 |
| Observer 写 quality.observer 设计承诺 | ✗ | ✓ V37.9.84 (但未实现) |
| Registry filter (12 enabled) | ✗ | ✓ V37.9.88 (但 Mac Mini 失效) |
| auto_deploy FILE_MAP 不拷 yaml 到 $HOME | ✓ 一直如此 | ✓ |
| script-adj path 在 Mac Mini = $HOME 重合 | ✓ 部署 layout 决定 | ✓ |
| LAYER 1 失效时 silent fallback | ✓ 设计 fail-open | ✓ |
| stderr WARN 不进告警链 | ✓ 设计 | ✓ |
| MR-15 已 3 次但没 scanner | ✓ V37.9.78 后 | ✓ V37.9.92 第 4 次 |
| Observer 自己 LLM critique 主动推动复盘 | ✗ 不存在 | ✓ 5/31 critique 提示 stale 告警埋底 → 6/1 复盘动机 |

**只有当 V37.9.88 上线 (引入 path bug) + V37.9.84 承诺未实现 + MR-15 模式累积到 4 次 + Observer 自己驱动复盘 五个条件全集合时, V37.9.92 同 session 三阶段闭环才发生**.

## 元教训

### MR-15 (deployment-layout-must-be-tested-on-target) 第 4 次硬实证, 必须立 framework

V37.9.56-hotfix → V37.9.76-hotfix → V37.9.78-hotfix → V37.9.92 — 每次发现都是单点修复, 没立 framework 级 scanner. 第 4 次时门槛达, V37.9.94 立 INV-CROSS-ENV-PATH-001 + scanner. **首扫立即抓出 V37.9.91 expert_escalation.load_status 的第 5 次潜在演出, 顺手修** — 完美兑现 framework 价值: 把"修血案"升级为"机器化预防", 让"靠下次记得"变成"机器自动 enforce".

### MR-7 (governance-execution-is-self-observable) 最高境界

V37.9.84 Observer 设计目标"观察其他系统". V37.9.92 是它**第一次观察自身**(Observer trend 复盘暴露 V37.9.88 + V37.9.84 双 silent). V37.9.93 是它**第二次观察自身**(Observer LLM critique 暴露自己的 sampling 限制). MR-7 不只要求"治理工具被治理", 还要求"治理工具能自我治理". V37.9.92→93 同 session 双闭环是 MR-7 最强硬实证.

### "设计承诺 vs 生产现实" 5 天潜伏 — V37.9.84 设计契约层 silent

V37.9.84 changelog 写"三方共享意识锚点 quality.observer", 但代码层从未实现. 5 天里没有任何 check 守这个承诺. 单测 + governance + preflight 全过, 因为它们守的是 daily_observer.py 内部行为, 不是设计承诺的兑现.

**教训**: 任何 "三方共享" / "anchor" / "single source of truth" 类设计承诺必须立**契约层 INV** — 不只是"代码工作", 而是"承诺的字段在真实位置出现". V37.9.92 写完没立 INV-OBSERVER-001 是 V37.9.93+ 主硬卡 (7 天观察后立).

### MR-4 (silent-failure-is-a-bug) 双重演出新形态

前 N 次 MR-4 都是"代码 bug 没告警"或"错误被吞". V37.9.92 是**两个 silent 同时存在 5 天**:
- silent #1: registry filter 失效, log WARN 不告警
- silent #2: 设计承诺不实现, 单测全过没人守

**同时发生** = 互相掩盖. silent #1 让 anomalies 含假警, silent #2 让 quality.observer 永空. 操作者看 quality 字段空以为"还没数据", 看 anomalies 含 pwc 以为"job 真坏". 双 silent 协同制造"系统大致正常"的错觉. 直到 Observer 自己驱动复盘.

### "三阶段同 session 闭环" 是 framework 演进的健康节奏

V37.9.92 (修两个 silent) → V37.9.93 (修 Observer 自身 sampling 限制) → V37.9.94 (framework 化 MR-15 防 5th occurrence) — 三个版本在同一 session 内连环兑现. 每个版本都基于上一个版本暴露的新问题. 这是反思转化为机制的硬证据 — 不是把每个 bug 单 commit 修, 而是在同 session 内沿着同一条思路一路深掘.

## 相关 INV / MR

| ID | 状态 | 关系 |
|---|---|---|
| INV-CROSS-ENV-PATH-001 | ✅ V37.9.94 立 | 直接 framework 防御 (本案催生) |
| MR-15 | ✅ 5+ 次演出 framework 化 | 元规则 (本案第 4 次演出) |
| MR-7 | ✅ V37.9.92/93 最高境界 | 治理自观察元规则 |
| MR-4 | ✅ N+1 次演出 (双重 silent) | silent-failure 元规则 |
| INV-OBSERVER-001 | ⏳ V37.9.95+ 候选 (7 天观察后立) | Observer 自身契约守卫 |
| INV-OBSERVER-SAMPLING-001 | ⏳ V37.9.95+ 候选 | V37.9.93 smart sampling 不退化守 |
| INV-PROXY-PLIST-ENV-001 | ⏳ V37.9.92+ 候选 (Doubao 提议) | proxy plist ARK env 守 |

## 防御链路总览

| 攻击向量 | 防御层 | 何时拦下 |
|---|---|---|
| 新 `_resolve_*_path` 函数漏 Mac Mini canonical | **cross_env_path_scanner** (V37.9.94) | dev / CI 阶段 |
| 新增 resolver 的 PR | **INV-CROSS-ENV-PATH-001 governance audit** | governance_audit_cron 每日 07:00 |
| LAYER 1 失效 silent fallback | (待 V37.9.95+ INV-OBSERVER-001) | 守 fallback warn 必须升告警 |
| 设计承诺零兑现 | (待 V37.9.95+ INV-OBSERVER-001) | 守 quality.observer 字段 schema |
| Observer 自身 sampling artifact | **smart head+tail sampling** (V37.9.93) + **CRITIQUE_SYSTEM 明示** | LLM 评估时 |
| Observer 误报 file truncation | smart sampling 让 LLM 看到 footer 自动判定 | LLM 评估时 |
| Observer 自身 silent 缺自我观察 | Observer LLM critique 反过来报自身问题 | 每日 06:30 cron + 用户 weekly review |
| 操作者忽略 stderr WARN | (待 V37.9.95+) WARN → [SYSTEM_ALERT] 升级 | 升级日志层 |

## 历史经验链

| 案例 | 共同模式 | 学到 |
|---|---|---|
| `pa_alert_contamination_case.md` (V37.4.3) | PA 系统告警污染对话 | 告警链不依赖失效主体自身 |
| `kb_evening_fallback_quota_chain_case.md` (V37.8.10) | LLM 错误三层稀释 | 跨层 error 必须保留 upstream cause |
| `dream_self_referential_hallucination_case.md` (V37.8.6) | LLM 看到自己错误日志当外部信号 | log → stderr 否则 cmd 替换污染 |
| `kb_review_silent_degradation_case.md` (V37.5) | 6-bug silent degradation | fail-fast 取代机械 fallback |
| `kb_deep_dive_cron_unregistered_case.md` (V37.9.18) | 三层 silent 协同掩护 | 关键运维步骤必须机器化 |
| `heartbeat_md_pa_self_silencing_case.md` (V37.8.16) | LLM 触碰 runtime 保留文件 | RESERVED_FILES + INV-HB-001 |
| **本案 v37_9_92_observer_path_blood_case.md** | **MR-15 第 4 次 + V37.9.84 设计承诺双 silent + 自我修复闭环** | **scanner 化 + 自我治理纪律** |

## 反思方法论 — Observer 5 天 silent 的元元层洞察

本案最深一层教训: **当一个观察者本身是 silent failure 的源头时, 谁来观察观察者?**

V37.9.84 Observer 的整个设计目的是"观察推送质量, 主动发现真问题". 但它自己有 2 个 silent failure (path bug + 设计承诺零兑现), 5 天里没有任何"元观察者"发现这点. 直到 Observer 自己 5/31 LLM critique 提到一个**unrelated** issue (stale 警告埋底), 才驱动我去复盘 Observer 整个生态. 这是巧合.

**改进方向 (V37.9.95+ candidates)**:
- INV-OBSERVER-001: governance audit 主动 enforce quality.observer 必须每日更新 (last_updated_at < 24h)
- INV-OBSERVER-LAYER1-001: log WARN "registry filter fallback" 出现时升级为 governance audit warning
- Observer-of-Observer: 第二个 cron 每日扫 daily_observer.log 找 "WARN" / "fallback" 关键字, 累计 ≥ N 次升级 [SYSTEM_ALERT]

但这些都还是被动的. 真正的元元层防御是 **MR-15 第 4 次演出后立 framework 级 scanner** (V37.9.94 已立) — 把"我希望下次记得"转化为"机器自动 enforce". 这是项目演进过程从"故事化经验"升级为"机器化规则"的关键拐点.

---

*生成于 2026-06-01. 案例 V37.9.92 主修复 commit 41ed044 (含 V37.9.93). V37.9.94 立 INV-CROSS-ENV-PATH-001 + scanner 后, 此案进入"已 framework 化预防"状态.*

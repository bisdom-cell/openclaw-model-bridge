# 案例分析：Dream Map 阶段预算溢出与缓存键不稳定

> 日期：2026-04-11 | 触发事件：Dream 00:40 `--map-notes` 与 03:00 full run 连续全局超时，Reduce 从未执行，推送"Dream 失败"告警

---

## 事件

用户于 2026-04-11 收到 WhatsApp 告警：

```
⚠️ Agent Dream 失败 (2026-04-11): 全局超时 — Map 阶段耗时过长，跳过 Reduce — 检查 ~/kb_dream.log
```

检查 `~/kb_dream.log` + `.last_map.json` + `.last_run.json`：

- 00:40 `--map-notes`：耗时 3645s 后超时，处理到 207/286 notes、47 批
- 03:00 full run：耗时 3662s 后超时，依然停在 207/286、47 批（与 00:40 几乎相同位置）
- Sources Map 缓存 15/15 完美命中（说明 00:00 `--map-sources` 成功）
- Notes 47 个批次缓存文件全部 miss（即使 00:40 刚写过）

**当天用户对 Dream 的依赖最重**：每日梦境 = 唯一的跨域关联发现引擎，失败 = 当天无洞察推送。

## 表面现象 vs 真实根因

| 层级 | 表面判断 | 深挖后的真实根因 |
|------|----------|-----------------|
| 第一眼 | "Qwen3 夜间慢 / retry hell" | 日志显示每批 LLM 调用 ~76s，属于正常范围，没有慢成 5x |
| 第二层 | "60 min 预算太紧" | 这是必要条件之一，但不是结构性问题 |
| 第三层 | "Notes Map 本身需要 82 分钟" | 286 notes ÷ 4.4 notes/batch × 76s = **82 min > 60 min**，数学上不可能 |
| 第四层 | "但 00:00 的 --map-notes 不是专门预热吗" | **Reduce 路径从未相信缓存**：03:00 full run 重跑整个 Phase 1b 循环 |
| 第五层 | "Notes 缓存 miss 也太诡异" | **cache key = md5(每批拼接文本)**：mtime 漂移导致 sort 顺序变化 → 批次组成变化 → hash 全变 → 00:40 写的缓存 03:00 全部读不到 |

## 完整因果链架构图

```
00:00  [cron] 00 0 * * *  kb_dream.sh --map-sources
       │
       ├─ Phase 1a Map Sources: 15 sources × ~60s ≈ 12 min
       ├─ 缓存 key = ${name}_${file_size}_${prompt_hash}  ← 稳定
       └─ ✅ 15/15 成功，写入 .map_cache/2026-04-11_<name>_<size>_v3.txt
       │
00:40  [cron] 40 0 * * *  kb_dream.sh --map-notes
       │
       ├─ Phase 1b Map Notes: 286 notes / 动态批次(15/12KB) ≈ 47 批
       ├─ 每批 cache key = md5(batch_拼接文本)  ← ❌ 脆弱
       ├─ SORTED_NOTES 按 mtime DESC 排序 → mtime 波动 = 顺序波动
       │    → 批次组成波动 → md5 波动 → cache 永远写新文件
       ├─ 47 批 × 76s/批 ≈ 59.5 min (刚好超预算)
       │
01:40:45  DREAM_TIMEOUT_SEC=3600 → check_deadline() 返回 false
       ├─ 状态：map_notes_done, notes_map_count=207/286
       └─ 47 个新 hash 写入缓存（与 03:00 将要看的 key 不同！）
       │
03:00  [cron]  0 3 * * *  kb_dream.sh    ← 无参数 = Reduce full run
       │
       ├─ Phase 1a Map Sources:
       │    ├─ 扫 ALL_SOURCES → 计算 ${name}_${size}_${prompt_hash}
       │    ├─ .map_cache/ 里 15 个文件名匹配 ✅
       │    └─ 全部 cache hit，0 LLM 调用 (1s)
       │
       ├─ Phase 1b Map Notes:  ← ❌ 灾难点
       │    ├─ 脚本重新进 while 循环 (`if FAST_MODE=false ... notes ...`)
       │    ├─ SORTED_NOTES 按 mtime DESC：3 小时里 notes 目录可能有新笔记
       │    ├─ 重新按 15/12KB 分批 → 47 个新批次
       │    ├─ 每批重新 md5(拼接文本)：
       │    │     - 即使内容一字不差，batch 边界可能漂
       │    │     - 即使边界不漂，锚点 note 换了 mtime 就换了
       │    │     - batch 拼接顺序变 → md5 变
       │    ├─ 47 批全部 cache miss → 47 × 76s ≈ 59 min
       │    └─ 到第 47 批时再次撞预算
       │
04:01:02  check_deadline → false
       ├─ 状态：timeout, map_count=15, notes_map_count=0 (此次 run 内)
       └─ dream_fail_alert("全局超时 — Map 阶段耗时过长，跳过 Reduce")
       │
       ├─ 简报模式：只写已有 MAP_SIGNALS（15 个 source 信号）
       │    + 空的 NOTES_SIGNALS（因为这次 run 一批都没 flush 成功）
       ├─ Reduce LLM 调用：**从未发生**
       │
       └─ [用户] 收到 "Dream 失败 — 检查 ~/kb_dream.log" WhatsApp 告警
```

### 四维度层级对照

- **时间线**：00:00 → 00:40 → 01:40 → 03:00 → 04:01（两次 Map 超时 + 无 Reduce）
- **层级**：cron → kb_dream.sh → llm_call → Adapter(:5001) → Qwen3
- **逻辑**：`if [ -f "$cache_file" ]` 每次 FALSE，`MAP_CONSECUTIVE_FAILS` 每次 reset（缓存看似未命中但不是 LLM 失败），`check_deadline` 每次把循环打断在同一位置
- **架构**：Map-Reduce 分离调度预期"Map 预热 → Reduce 读缓存"，但缓存层没契约化 → Reduce 路径自己重跑 Map

## 三层根因（触发器 → 放大器 → 掩护者）

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | 286 notes × 76s/batch ÷ 4.4 notes/batch = **82 min > 60 min** 预算 | Notes 总量增长超过了原 Phase 1b 设计时的假设（原设计针对 ~150 notes）|
| **放大器 1** | **Reduce 路径不相信缓存**：full run 把整个 Phase 1a/1b 循环跑一遍，即使 00:00/00:40 已把缓存写好 | 当初设计 `--map-only` 和 full run 共用一份代码是为了"最大化缓存复用"，但共用的是 *循环*，不是 *读缓存的捷径* |
| **放大器 2** | **Notes cache key = md5(批次拼接)**：对 mtime、sort 顺序、新 note 插入都敏感 | 03:00 与 00:40 之间任何一个 note 被 mm_index.py 触碰 mtime → 排序变 → 批次组成变 → hash 全变 |
| **掩护者** | dream_fail_alert 成功发送 → 用户以为"系统知道坏了，我等明天修"，但隐藏了"Reduce 从未执行 → 连续 N 天没有梦境洞察"的累积损害 | 告警粒度停留在"是否超时"，没区分"Map 产出了部分信号（可降级）"vs"Reduce 从未跑"（核心价值归零）|

## 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| 00:00:05 | `--map-sources` 启动 | Sources Map 开始 |
| 00:12:* | Sources Map 15/15 完成 | 写入 15 个稳定 key 的 cache 文件 |
| 00:40:00 | `--map-notes` 启动 | Notes Map 开始 |
| 00:40-01:40 | 47 批 × ~76s，处理 207/286 notes | 47 个 batch-hash cache 文件写入 |
| 01:40:45 | check_deadline 超时，map_notes_done 写入 | **79 条 notes 孤儿**，47 个 cache 是"将死" |
| 03:00:00 | cron full run 启动 | Reduce 路径开始 |
| 03:00:01 | Sources Map 15/15 cache hit | 1 秒完成 |
| 03:00:05 | Phase 1b 重新分批 → 47 个**新** hash | 所有之前写的 cache 都变成孤儿文件 |
| 03:00-04:01 | 47 批 × ~76s → 再次超时 | Reduce 从未执行 |
| 04:01:02 | dream_fail_alert 触发 | 用户收到告警，无梦境输出 |

## 为什么以前没发生

| 条件 | V37.2 之前 | V37.2+ | 2026-04-11 |
|------|-----------|--------|------------|
| Notes 总数 | ~150 | ~240 | **286** |
| 批次数 | ~24 | ~40 | **47** |
| 单批耗时 | ~70s | ~75s | ~76s |
| 总 Notes Map 耗时 | ~28 min | ~50 min | **~59.5 min** |
| DREAM_TIMEOUT_SEC | 3600s | 3600s | 3600s |
| cache key 稳定 | 否（同样问题）| 否 | 否 |
| **Reduce 再跑 Map 循环** | 是 | 是 | 是 |

**两个条件同时穿线**：① Notes 总量超过了 60 min 预算能容纳的阈值 ② 即使预热了也白预热（cache key 漂）。前者单独出现 = 勉强能跑完；后者单独出现 = 白浪费 --map-notes 但 full run 能完成；**两者叠加 = 每天都超时**。

## 修复（三层对应三因）

| 层级 | 修复 | 原理 |
|------|------|------|
| **预算** | `DREAM_TIMEOUT_SEC` 动态化：MAP_ONLY=5400 (90min) / Reduce=3600 (60min) | Map 是一次性 bootstrap，给 90 min 即可跑完 286 notes；Reduce 只读缓存+1 次 LLM，60 min 足矣 |
| **契约** | Reduce 路径增加 "cache-only fast path"：扫 `.map_cache/` 匹配 Sources 文件名 + Notes content_hash，跳过 Phase 1a/1b 循环 | 分离调度的约定必须在代码里硬编码"Reduce 只读缓存"。缓存全缺 → 降级到简报；缓存部分缺 → 部分信号加原 notes |
| **稳定** | Notes cache key = `md5(content)`，每 note 一个文件 | mtime 漂、sort 变、batch 组合变都不影响 cache key；第二天只有新增 notes 是 miss |
| **效率** | Notes batch 从 15条/12KB → 30条/24KB | LLM 调用次数减半（47→24），首日 bootstrap 从 60 min 压到 30 min |

### 关键设计选择

**为什么按 content 做 cache key，不用 note 文件 mtime + path？**

- mtime 不稳定（mm_index.py、kb_embed.py 都会触碰）
- path 稳定但内容变了（用户编辑 note）应该重新提取信号
- content hash 捕捉"这段文本是否还是原来那段"——唯一正确的语义

**为什么一次 LLM 产出的 signals 要写到所有参与 note 的 cache 文件（同内容多副本）？**

- 换取 cache key 稳定性：每个 note 独立存在，明天有没有这个 note 不影响别人
- 代价：磁盘 30x 冗余。286 notes × 500B signals ≈ 150KB，可接受
- 收益：增量更新粒度从"批次"降到"单 note"，新增 5 个 note 只需要跑 1 批而不是重算整批

**为什么读取时要按 signal hash 去重？**

- 一批 30 个 note 共享同一份 signals，如果全部加进 NOTES_SIGNALS 会重复 30 次
- dedup key = md5(signals)，相同内容只纳入一次
- 保留 NOTES_MAP_COUNT 原样递增（统计意义上每个 note 都"命中了"）

## 从本体论视角的启示

### 1. 分离调度的隐式契约必须硬编码

Map-Reduce 分离调度（00:00/00:40 Map + 03:00 Reduce）本质是一个契约：

> Reduce 假定 Map 已经把所有结果放进缓存，Reduce 只读缓存。

但这个契约只存在于 CLAUDE.md 的文档里，代码层 Reduce 路径仍然调用完整的 Phase 1b 循环。**文档上的契约不算契约，代码里的分支才算契约。**

**本体论映射**：
- 概念：`ScheduleContract`（Map 调度器 ↔ Reduce 调度器之间的协议）
- 关系：`produces(MapPhase, CacheFile)`, `consumes(ReducePhase, CacheFile)`
- 不变式：**"被分离调度的 Map/Reduce 必须在代码层有独立的执行路径，不共享循环体"**

### 2. Cache key 必须对时间正交

任何会因为"时间流逝"而改变的输入都不该进 cache key：
- ❌ mtime（会被其他索引器触碰）
- ❌ sort 顺序（依赖 mtime）
- ❌ 批次组合（依赖 sort 顺序）
- ❌ 行号（新增内容会推移）
- ✅ content hash（语义锚点）
- ✅ 文件大小（近似 content）
- ✅ prompt 模板版本（内容变更信号）

**不变式**：**"cache key 在不变的内容上必须产生不变的 hash，与调度时间、扫描顺序无关"**

### 3. 预算与 workload 必须成比例

`DREAM_TIMEOUT_SEC=3600` 这样的固定预算在 workload 增长时会静默失效。**每一个硬编码时间预算都是一个定时炸弹**，workload 长到某一天就会炸。

**设计原则**：
- 固定预算的脚本必须有"workload 增长检测"：当 `actual_items / budget_items_per_sec` 接近预算时告警
- 或者：按模式动态预算（这次的修复）+ 对每一类调用记录"需要多少秒"
- 最根本的：**把预算当成资源约束而不是超时保护**，资源不够 → 分阶段推进 → 保证核心产出

### 4. 原则 #26 的二次应用

这是 Principle #26（异常必须深挖爆炸链）的第二个典型案例：
- 第一次：`dream_quota_blast_radius_case.md`（Qwen3 宕机 × MapReduce 放大 → Gemini 配额溃坝）
- 第二次：本案例（workload 增长 × cache key 不稳定 × Reduce 不相信缓存 → Map 预算溢出）

两次都是**架构级别**的问题，不是"哪行代码写错了"。两次都需要画完因果链才看清根因——**不画图就会当成"Qwen3 不稳定"或"预算太小"处理掉**。

## Governance 不变式提案

```yaml
# 新增不变式 — 从本案例提炼，合入 ontology/governance_ontology.yaml

- id: INV-DREAM-001
  name: "Map budget scales with workload"
  description: >
    Notes Map 的总耗时（批次数 × 单批平均时延）必须在 MAP_ONLY 模式的预算内。
    workload 增长导致预期耗时 > 预算时必须在设计层报警，而非运行时超时。
  severity: high
  meta_rule: MR-4  # silent-failure-is-a-bug
  verification_layer: [declaration, runtime]
  checks:
    - name: dream_timeout_sec_dynamic
      layer: declaration
      type: grep
      pattern: 'DREAM_TIMEOUT_SEC=5400'
      file: kb_dream.sh
      must_exist: true
    - name: dream_map_budget_guard
      layer: declaration
      type: grep
      pattern: 'MAP_ONLY.*5400|5400.*90 min'
      file: kb_dream.sh
      must_exist: true

- id: INV-CACHE-002
  name: "Cache key stability under mtime drift"
  description: >
    Dream Map 的 notes cache key 必须只依赖 note content，不依赖 mtime / sort 顺序 /
    批次组合。同一条 note 在 00:40 --map-notes 和 03:00 full run 必须生成相同 cache key。
  severity: critical
  meta_rule: MR-6  # verification-depth-must-cover-runtime
  verification_layer: [declaration, runtime]
  checks:
    - name: notes_cache_key_is_content_hash
      layer: declaration
      type: grep
      pattern: 'md5.*content.*note|content_hash.*note'
      file: kb_dream.sh
      must_exist: true
    - name: old_batch_hash_pattern_removed
      layer: declaration
      type: grep
      pattern: 'batch_hash=.*echo.*BATCH.*md5sum'
      file: kb_dream.sh
      must_not_exist: true

- id: INV-DREAM-002
  name: "Reduce path must not re-run Map loops"
  description: >
    当 MAP_ONLY=false 且 FAST_MODE=false 时（默认 full run），必须跳过 Phase 1a/1b
    的 LLM 循环，只从 .map_cache/ 读取。否则分离调度的契约形同虚设。
  severity: critical
  meta_rule: MR-4  # silent-failure-is-a-bug（重复烧预算 = 隐形失败）
  verification_layer: [declaration]
  checks:
    - name: reduce_cache_only_fast_path
      layer: declaration
      type: grep
      pattern: 'SKIP_MAP_LOOPS=true'
      file: kb_dream.sh
      must_exist: true
    - name: phase_1a_guarded_by_skip_map_loops
      layer: declaration
      type: grep
      pattern: 'SKIP_MAP_LOOPS.*=.*false.*SRC_COUNT|Phase 1a.*SKIP_MAP'
      file: kb_dream.sh
      must_exist: true
```

---

*本案例是原则 #26（异常必须深挖因果链，喂养本体工程）的第二个实践。与 `dream_quota_blast_radius_case.md` 同属 Dream 子系统，但根因完全不同：上次是"功能增强的 blast radius 盲区"，这次是"分离调度的隐式契约 × cache key 不稳定 × workload 预算脆弱"的三重叠加。*

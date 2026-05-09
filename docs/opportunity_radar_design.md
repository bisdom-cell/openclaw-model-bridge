# Opportunity Radar Design — 机会点雷达三件套

> 设计版本：v0.1（V37.9.44 后立项）
> 设计日期：2026-05-09
> 设计者：Claude Code（Opus 4.7）
> 目的：从"广度数据复利"升级为"深度机会点挖掘"，把每天 ~290 notes / 14 sources 的累积转化为"可识别早期信号"的智能雷达
> 路标：V37.9.45（#1 PoC）→ V37.9.46（#2 集成）→ V37.9.47（#3 集成）→ V37.9.48（三者协同）→ V37.9.50（产品化）

---

## 一、战略愿景

### 1.1 用户两轴意图

```
广度轴（数据复利）           深度轴（机会点挖掘）
14 sources × 290 notes/day → 弱信号检测 + 趋势加速 + 项目对齐
KB 25750 chunks 累积           ↓
                            "技术机会点雷达"
                            （早 30 天发现风口前的弱信号）
```

### 1.2 三件套的统一愿景

> **Opportunity Radar = 弱信号聚合（横向）× 项目对齐度（垂直）× 趋势加速度（时间维度）**

三个维度独立但**协同放大**：
- **#1 跨 source 共振** = 横向（同一时刻，多 source 同时关注 → 早期信号）
- **#2 项目对齐度** = 垂直（信噪比过滤，与 OpenClaw/ontology 直接相关 → 高价值）
- **#3 趋势加速度** = 时间（动态而非静态，加速 → 风口预警）

任一单独都有限：
- 仅 #1 → 信号噪声大（什么主题都可能共振，但你不全关心）
- 仅 #2 → 静态对齐（错失新概念新方向）
- 仅 #3 → 加速但可能不重要（流量噪声 vs 真趋势）

**三者交集 = 真正的"高价值早期信号"**：
> "今日 3 个 source 共振 + 与 OpenClaw 高对齐 + 本周加速 1.8x" → 🚨 红色机会点雷达警报

### 1.3 与现有深度 job 的关系

| 现有 | 角色 | 三件套补强 |
|------|------|-----------|
| **kb_dream** (03:00) | 跨域关联 / Map-Reduce | #1 注入"早期信号雷达"段 + #3 注入"加速主题"段 |
| **kb_deep_dive** (22:30) | 单篇 ⭐≥4 深度分析 | #2 把项目对齐度作为 picker 二级排序键 |
| **kb_evening** (22:00) | 今日要闻 | #1+#2+#3 三段标准化"机会点速报" |
| **kb_review** (周日) | 周回顾 | #3 趋势加速 = 周报核心数据源 |
| **kb_trend.py** (周) | 关键词频率 | #3 升级为加速度版（替换/扩展） |

**新建 1 个独立合成 job**：`kb_radar.sh`（每日 06:00 HKT），三件套数据生产 + 早晨推送"昨日机会点雷达扫描结果"。

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────────┐
│ 数据采集层（已有）                                            │
│   14 sources × ~290 notes/day → ~/.kb/notes/                │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ 三件套合成层（新建）                                          │
│                                                              │
│  ┌─────────────────────────────┐                            │
│  │ #1 cross_source_signal       │  → ~/.kb/radar/           │
│  │    _aggregator.py            │     daily_signals_DD.json │
│  │   (sentence-transformer +    │                            │
│  │    DBSCAN + 跨 source 检测)   │                            │
│  └─────────────────────────────┘                            │
│                                                              │
│  ┌─────────────────────────────┐                            │
│  │ #2 project_alignment_scorer  │  → 注入所有迁移脚本        │
│  │   (project_concepts.yaml +   │     的 LLM prompt           │
│  │    LLM prompt 字段扩展)       │     新字段 🎚️ 对齐度        │
│  └─────────────────────────────┘                            │
│                                                              │
│  ┌─────────────────────────────┐                            │
│  │ #3 trend_acceleration       │  → ~/.kb/radar/            │
│  │    .py                       │     weekly_trends_WW.json  │
│  │   (kb_trend 扩展加加速度)     │                            │
│  └─────────────────────────────┘                            │
│                                                              │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ 雷达推送层                                                    │
│                                                              │
│  ┌─────────────────────────────┐                            │
│  │ kb_radar.sh (06:00 HKT)      │  → WhatsApp + Discord      │
│  │   每日机会点雷达扫描          │     #daily 双通道         │
│  │   (#1 + #3 数据源)            │                            │
│  └─────────────────────────────┘                            │
│                                                              │
│  ┌─────────────────────────────┐                            │
│  │ kb_dream.sh (03:00, 已有)    │  → 增强现有推送            │
│  │   注入 #1 信号 + #3 加速主题  │                            │
│  └─────────────────────────────┘                            │
│                                                              │
│  ┌─────────────────────────────┐                            │
│  │ kb_evening.sh (22:00, 已有)  │  → 增强现有推送            │
│  │   今日要闻段加机会点高对齐 Top │                            │
│  └─────────────────────────────┘                            │
│                                                              │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
                    用户视角
            （早 30 天发现技术机会点）
```

---

## 三、组件 #1: 跨 source 弱信号聚合器

### 3.1 模块设计

**文件**: `cross_source_signal_aggregator.py`（纯 Python，可单测）

**输入**：
- `~/.kb/notes/` 目录下今日（YYYYMMDD 前缀）的 .md 文件
- 每个 note 含 frontmatter: `source_type` / `source_name` / `tags` / 标题 / 摘要

**算法**（DBSCAN + sentence-transformer 路径，已有 `local_embed.py` 复用）：

```
1. scan_today_notes(date) → list[Note]
   - filter by 今日时间窗 (YYYYMMDD prefix in filename)
   - parse frontmatter + extract: id / source_name / title / abstract / url

2. embed_notes(notes) → np.array (N × 384)
   - 复用 local_embed.py / sentence-transformers
   - text = title + abstract[:500]
   - cache to ~/.kb/radar/embedding_cache/{date}.npz

3. cluster_dbscan(embeddings, min_samples=3, eps=0.35) → list[cluster_id]
   - 参数 calibration: dev 环境用 ~290 notes 跑 + 手动调
   - eps 0.35 经验值（sentence-transformer all-MiniLM-L6-v2 同主题阈值）
   - min_samples=3 = "至少 3 篇 notes 才算共振"

4. filter_cross_source(clusters, notes) → list[Signal]
   - 对每个 cluster, 统计 unique_source_names
   - 仅保留 unique_sources >= 2 的 cluster (跨 source 共振契约)
   - Signal = {
       cluster_id, notes (list), 
       sources (set), 
       source_count, note_count,
       avg_intra_similarity (0-1),
       suggested_topic (LLM 一句话总结, 调 call_llm)
     }

5. rank_signals(signals) → list[Signal] (sorted)
   - score = source_count * 2 + note_count + avg_intra_similarity * 5
   - top 10 returned

6. emit_radar_json(signals, date) → ~/.kb/radar/daily_signals_{date}.json
   - 完整 dump for downstream consumers
```

### 3.2 失败模式与契约

- **依赖**: sentence-transformers 已在 Mac Mini 装（V29.3+）
- **FAIL-OPEN**: 任意环节失败 → 写空数组到 daily_signals.json + log WARN，下游不阻塞
- **FAIL-FAST 路径**: V37.9.39+ 同款 — 如 LLM 调用失败 → `[SYSTEM_ALERT]` + `status:llm_failed` + exit 1
- **缓存**: embedding 按日缓存（mtime 不变性 V37.4 同款）

### 3.3 与 kb_dream 集成

`kb_dream.sh` 现有 Map-Reduce，新增中间步骤（在 Phase 2 Reduce **之前**）：

```bash
# 新增 Phase 1.5: 跨 source 信号聚合
python3 ~/cross_source_signal_aggregator.py --date $DAY \
    > ~/.kb/radar/daily_signals_${DAY}.json
SIGNAL_COUNT=$(jq '. | length' ~/.kb/radar/daily_signals_${DAY}.json)
log "跨 source 信号: ${SIGNAL_COUNT} 个"
```

`kb_dream` Reduce LLM prompt 注入新材料段：

```
## 今日跨 source 共振信号 (>=2 source 关注同主题)
[signal 1] 主题: X | 源: arxiv,github,X | 篇数: 5 | 相似度: 0.82
  - note_id_1: title (source)
  - note_id_2: title (source)
  ...

请优先在 Reduce 输出中识别这些"早期信号"并标 🚨 早期机会点.
```

### 3.4 推送格式（Dream / Radar 增强段）

```
🚨 今日早期机会点雷达 (跨 source 共振信号 ≥3 篇 + ≥2 源)

🔥 [信号 1] CoT pruning 早期信号 (相似度 0.85)
   📡 来源: arxiv (2 篇) + github (1 repo) + X (1 tweet)
   🎚️ 项目对齐: ⭐⭐⭐⭐ (与 agent runtime tool optimization 直接相关)
   📈 趋势: 🚀 加速 1.8x (本周 vs 上周)
   📚 详情:
     - [arxiv] 2505.12345 "Speculative Token Pruning for Agent Reasoning"
     - [github] xyz/cot-prune (⭐ 87, 4 天前)
     - [X] @karpathy: "interesting CoT efficiency idea"

🔥 [信号 2] ...
```

### 3.5 单测计划（test_cross_source_signal_aggregator.py）

| 测试类 | 场景 | 数量 |
|--------|------|------|
| TestScanTodayNotes | 日期过滤 / frontmatter parse / 缺字段健壮 | 5 |
| TestEmbedNotes | 缓存命中 / 缓存失效 / 文本截断 / 向后兼容 | 4 |
| TestClusterDbscan | 参数边界 / 无聚类返回空 / 单 cluster / 多 cluster | 5 |
| TestFilterCrossSource | unique_sources≥2 contract / 单 source 拒绝 / 跨 4 源接受 | 4 |
| TestRankSignals | 公式正确性 / top 10 截断 / 空输入 | 3 |
| TestEmitRadarJson | JSON 格式 / 路径生成 / 缺目录自动 mkdir | 3 |
| TestFailOpenContract | embedding 失败 / DBSCAN 异常 / 文件读不到 | 4 |
| TestSourceLevelGuards | V37.9.45 marker / FAIL-OPEN 注释 / 反模式扫描 | 5 |
| **合计** | | **33** |

---

## 四、组件 #2: 项目对齐度评分

### 4.1 配置文件设计

**新文件**: `project_concepts.yaml`（项目本体清单，作为 LLM 评分 ground truth）

```yaml
# project_concepts.yaml — OpenClaw 项目核心概念清单
# 用于 LLM 评分"这条信息与本项目的相关度"
# 维护: Claude Code 收工时同步本周新增概念
# version: 2026-05-09 (V37.9.46)

project:
  name: openclaw-model-bridge
  version: 0.37.9.44
  strategic_position: Agent Runtime Control Plane

core_planes:
  control_plane:
    desc: "治理 / 限流 / 降级 / 观测 / 审计"
    keywords: [governance, audit, fail-fast, slo, circuit breaker, fallback,
               rate limiting, observability, ontology, policy engine]
    weight: 5  # 高优先级
  capability_plane:
    desc: "模型路由 / 工具编排 / 多模态"
    keywords: [provider abstraction, tool calling, multimodal routing,
               function calling, agent orchestration, tool plugin]
    weight: 4
  memory_plane:
    desc: "知识沉淀 / 冲突消解 / 可信度评分"
    keywords: [vector index, RAG, semantic search, embedding, conflict
               resolution, knowledge graph, KB harvest, dedup]
    weight: 5

active_research_directions:
  ontology:
    desc: "Phase 4-5 ontology engine 产品化"
    keywords: [tool ontology, declarative policy, semantic reasoning,
               domain ontology, governance checker, BFO, DOLCE, UFO]
    weight: 5
  agent_reliability:
    desc: "V2 Agent Reliability Bench"
    keywords: [scenario testing, failure injection, gameday, recovery time,
               resilience, chaos engineering]
    weight: 4
  data_compounding:
    desc: "数据复利"
    keywords: [knowledge accumulation, signal aggregation, weak signal,
               trend acceleration, opportunity radar]
    weight: 5

excluded_topics:
  # 明确不感兴趣的主题（即使关键词命中也降权）
  - desc: "纯 NLP fine-tuning details"
    keywords: [LoRA optimization, distillation tricks, hyperparameter sweep]
  - desc: "纯硬件优化"
    keywords: [CUDA kernel, GPU sharding, hardware-specific optimization]
```

### 4.2 算法设计

**两层混合评分**（LLM 主观 + 离线规则验证）：

**Layer 1**: LLM prompt 注入（每个迁移脚本的 5 字段 prompt 加第 6 字段）：

```
🎚️ 项目对齐度: ⭐ × N (1-5)
   - 5 = 与 control plane / agent runtime / ontology 等核心方向直接相关
   - 4 = 间接相关 (如 tool plugin / memory plane 演进)
   - 3 = 一般 AI/ML 趋势 (可借鉴但非核心)
   - 2 = 无明显关联 (但可能未来有用)
   - 1 = 完全无关 (噪声)
   原因: 一句话说明 (≤30 字)
```

**Layer 2**: 离线 rule_check 验证（防 LLM 任意打分）

`project_alignment_validator.py`:
```python
def validate_alignment_score(content: str, llm_score: int,
                             concepts: dict) -> dict:
    """LLM 给的分 vs 关键词命中率, 检测 hallucination"""
    keyword_hits = count_keyword_hits(content, concepts)
    expected_score_range = compute_expected_range(keyword_hits)
    if llm_score in expected_score_range:
        return {"validated": True, "llm_score": llm_score}
    else:
        return {"validated": False, "llm_score": llm_score,
                "rule_score": expected_score_range[0],
                "reason": f"LLM 评 {llm_score} 但仅命中 {keyword_hits} 关键词"}
```

> 推送时显示 LLM 评分 + 如 validated=False 加 ⚠ 标记。

### 4.3 接入路径

10 个已对齐脚本 + 11 个未对齐脚本（V37.9.45+ 迁移时同步加）：

**统一改动**（每个 5 字段 prompt 加第 6 字段）：
```diff
- ⭐ 评级: ⭐ × N + 推荐场景
+ ⭐ 评级: ⭐ × N + 推荐场景
+
+ 🎚️ 项目对齐度: ⭐ × N (1-5) + 一句话原因
```

**emit 端解析**：parse_5field_output → parse_6field_output（向后兼容缺字段为空）。

### 4.4 推送格式

每条 repo/paper/post 末尾加：
```
⭐⭐⭐⭐ / 推荐场景: ...
🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ - 直接对应 OpenClaw control plane
```

**Dream/Radar 推送的"高对齐 Top 5"段**：
```
🎯 今日高对齐 Top 5 (与 OpenClaw 直接相关)

1. ⭐⭐⭐⭐⭐ Speculative Tool Calls for Agent Runtime
   [arxiv 2505.12345 | 1500 字深度分析 | 跨 source 信号 #1 同时命中]
2. ⭐⭐⭐⭐⭐ control-plane-engine v0.3 release
   [github trending | 与你的 ontology engine 终极目标直接呼应]
...
```

### 4.5 单测计划（test_project_alignment_scorer.py）

| 测试类 | 场景 | 数量 |
|--------|------|------|
| TestProjectConceptsYaml | 文件存在 / schema 正确 / weight 范围 / keywords 非空 | 5 |
| TestKeywordHitCounting | 多关键词命中 / 大小写 / 中英混合 / excluded_topics 降权 | 6 |
| TestExpectedScoreRange | 0 命中 → ⭐1-2 / 5 命中 → ⭐4-5 / 边界 | 4 |
| TestValidateAlignmentScore | LLM 评 5 但 0 命中 → not validated / 一致 → validated | 4 |
| TestPromptInjection | 6 字段 prompt 含 🎚️ 字段 + 评分指南 + 原因要求 | 3 |
| TestParse6FieldOutput | 完整 6 字段解析 / 缺字段 / 字段顺序错乱 | 5 |
| TestSourceLevelGuards | V37.9.46 marker / project_concepts 引用 / 反模式 | 4 |
| **合计** | | **31** |

---

## 五、组件 #3: 趋势加速检测

### 5.1 模块设计

**文件**: `kb_trend_acceleration.py`（扩展 `kb_trend.py`，不替换以保向后兼容）

**算法**：

```
1. extract_keywords_per_week(week_offset) → dict[keyword → freq]
   - 已有 kb_trend.py 复用 (jieba/英文 NLP 抽取关键词)
   - 计算 freq = count / total_notes (本周占比)

2. compute_acceleration(week_keywords) → dict[keyword → metrics]
   for each keyword in union(week-1, week-2, week-3):
       w1 = week-1 freq
       w2 = week-2 freq
       w3 = week-3 freq
       if w2 == 0: continue  # avoid div0
       accel_1w = w1 / w2
       accel_2w = w1 / w3 if w3 > 0 else None
       metrics[kw] = {
           freq_w1: w1, freq_w2: w2, freq_w3: w3,
           accel_1w, accel_2w,
           classification: classify(accel_1w, accel_2w)
       }

3. classify(a1, a2) → str
   - 🚀 strong_acceleration: a1 >= 1.5 AND a2 >= 1.5 (连续 2 周加速)
   - 📈 mild_acceleration: a1 >= 1.5 (本周加速)
   - 💧 deceleration: a1 < 0.7 (本周减速)
   - ⚰️ obsolescence: a1 < 0.5 AND a2 < 0.5 (连续 2 周衰退)
   - 📊 stable: 其他

4. rank_signals(metrics) → list[(kw, metrics)]
   - 优先 🚀 (strong) > 📈 > 💧 > ⚰️
   - 同档按 |a1| 排
   - top 10 strong + top 5 stable + top 3 obsolescence

5. emit_radar_json(signals, week) → ~/.kb/radar/weekly_trends_{week}.json
```

### 5.2 与 kb_dream / kb_evening 集成

Dream 推送加"加速主题"段：
```
📈 本周技术加速主题 (周占比连续上升)

🚀 1. agent reasoning trace (本周占比 2.3%, 上周 0.9%, 上上周 0.4% — 4 周加速)
   - 关联 notes: 12 篇 (arxiv 5, github 3, X 4)
   - 项目对齐: ⭐⭐⭐⭐ (与本项目 ontology engine 验证深度相关)

🚀 2. CoT pruning (本周 1.5%, 上周 0.7% — 2.1x 加速)
   ...

⚰️ 减速主题 (供参考):
- "static prompt engineering" (4 周连续衰退至 0.2%)
```

Evening 推送加"明日关注"扩展：
```
🌙 明日关注
- 本周 2 个主题加速 (CoT pruning / agent reasoning trace)
- 建议: 明日 deep_dive 优先 picker 加权这两个主题
```

### 5.3 单测计划（test_kb_trend_acceleration.py）

| 测试类 | 场景 | 数量 |
|--------|------|------|
| TestExtractKeywords | jieba/英文兼容 / 停用词 / 短词过滤 | 4 |
| TestComputeAcceleration | 4 周历史 / 缺周降级 / 新词 (w2=0) skip | 5 |
| TestClassify | 5 档分类边界 / 同时 a1+a2 满足条件优先 strong | 6 |
| TestRankSignals | 优先级排序 / top 截断 / 空输入 | 4 |
| TestEmitJson | JSON 格式 / 路径生成 / 历史归档 | 3 |
| TestBackwardCompat | kb_trend.py 旧接口仍工作 (不 break) | 3 |
| TestSourceLevelGuards | V37.9.47 marker / 5 档分类常量 | 3 |
| **合计** | | **28** |

---

## 六、新合成 job: kb_radar.sh

### 6.1 设计

**调度**: 每日 06:00 HKT cron（早晨上班前看到夜间累积的雷达扫描）

**职责**: 三件套数据消费 + 推送早晨"机会点雷达"

**流程**:
```bash
#!/bin/bash
# kb_radar.sh — 每日机会点雷达
#
# 数据来源:
# - ~/.kb/radar/daily_signals_${YESTERDAY}.json  (#1 跨 source 共振)
# - ~/.kb/radar/weekly_trends_current.json        (#3 趋势加速)
# - 各 cron job 的 last_run.json (#2 高对齐内容)
#
# 输出:
# - WhatsApp + Discord #daily 双通道
# - ~/.kb/radar/daily_briefing_${TODAY}.md (KB 归档)

YESTERDAY=$(date -v-1d '+%Y-%m-%d')
SIGNAL_FILE="$HOME/.kb/radar/daily_signals_${YESTERDAY}.json"
TREND_FILE="$HOME/.kb/radar/weekly_trends_current.json"

# 1. 读三件套数据
# 2. LLM 综合分析 (重要性排序 + 联动检测)
# 3. 生成早晨 briefing markdown
# 4. 双通道推送
# 5. KB 归档
```

**推送格式**（雷达综合报告）:

```
🛸 早晨机会点雷达 (2026-05-10)

═══ 红色机会点 (三件套交集) ═══

🚨 [机会点 1] CoT pruning 综合信号
   📡 跨 source 共振: arxiv 2 + github 1 + X 1
   🎚️ 项目对齐度: ⭐⭐⭐⭐⭐
   📈 趋势加速: 🚀 1.8x (本周 vs 上周)
   💡 建议行动: 优先今日 22:30 deep_dive picker

═══ 黄色信号 (二件套命中) ═══

⚠️ [信号 2] Agent reasoning trace
   (跨 source ✓ + 趋势加速 ✓, 但项目对齐 ⭐⭐⭐ 中等)

═══ 蓝色趋势观察 (单件套) ═══

📈 加速主题 (无明显共振):
- speculative decoding (本周 +60%)

⚰️ 减速主题:
- static prompt eng (4 周衰退)

═══ 数据复利状态 ═══

📊 KB 累积: 26015 chunks (+265 since yesterday)
📡 14 源运行中 (10/14 高级 fail-fast)
🎯 高对齐 Top 5: [link to daily.md]
```

### 6.2 失败模式

- 三件套任一文件缺失 → 该段降级为"暂无数据"，不阻塞其他段
- LLM 综合分析失败 → fail-fast (V37.9.39 同款)

---

## 七、实施路径（V37.9.45 → V37.9.50）

### 7.1 渐进交付

| Stage | 版本 | 交付物 | 估算 |
|-------|------|-------|------|
| **Stage 1** | V37.9.45 | #1 PoC: cross_source_signal_aggregator.py + 33 单测 + dev 验证 + Mac Mini 验证（不集成 kb_dream） | 1 session 4-6h |
| **Stage 2** | V37.9.46 | #2 PoC: project_concepts.yaml + project_alignment_scorer.py + 31 单测 + 灰度集成 1 个脚本（如 V37.9.45+ hf_papers 同步加 6 字段） | 1 session 4-6h |
| **Stage 3** | V37.9.47 | #3 PoC: kb_trend_acceleration.py + 28 单测 + 集成 kb_dream/kb_evening 推送 | 1 session 3-5h |
| **Stage 4** | V37.9.48 | 三件套协同：#1 集成 kb_dream Phase 1.5 + #2 全 11 未对齐脚本 (P1+P2+P3) 同步加 6 字段 + #3 替换 kb_trend.py 默认 | 1 session 4-6h |
| **Stage 5** | V37.9.49 | kb_radar.sh 新 job + 06:00 cron 注册 + 综合分析 LLM prompt 设计 + Mac Mini E2E | 1 session 3-5h |
| **Stage 6** | V37.9.50 | 数据可视化（kb_concept_map.py + 周生成 HTML） + governance 加 INV-RADAR-001 + 路线图收工文档 | 1 session 3-5h |

**总估算**: 6 session / 约 20-30h 工作 / 跨 1-2 周完成。

### 7.2 各 Stage 的"成功定义"

**Stage 1 成功** = `cross_source_signal_aggregator.py --date today` 在 dev / Mac Mini 都跑通，输出 `daily_signals_DATE.json` 含 ≥3 个跨 source 共振信号（基于真实数据）。

**Stage 2 成功** = 选 hf_papers 作为 V37.9.45+ 迁移目标 + 同步加 6 字段 + Mac Mini 推送收到 🎚️ 项目对齐度评分 + LLM 评分与 rule_check 一致率 ≥80%。

**Stage 3 成功** = `kb_trend_acceleration.py` 跑出"4 周历史关键词加速度"+ Dream 周日推送出现"加速/减速主题"段（与 Stage 2 重叠时段）。

**Stage 4 成功** = Dream 03:00 推送出现"早期信号雷达"+"加速主题"双段，且与 V37.9.36+ fail-fast 模式无回退。

**Stage 5 成功** = kb_radar.sh 06:00 推送出现"红/黄/蓝"三档机会点 + Mac Mini cron 实跑 + 推送收到。

**Stage 6 成功** = 知识地图 HTML 周日发到 Discord #daily + 一周内用户实际查看反馈。

---

## 八、测试与守卫策略

### 8.1 单测三阶层

| 阶层 | 工具 | 范围 |
|------|------|------|
| **L1 单元测试** | unittest + 各组件 test_*.py | 92 单测（33+31+28），全部纯函数无 I/O 依赖 |
| **L2 集成测试** | full_regression.sh 接入 | 跑通三件套 + kb_dream/evening/radar 集成 |
| **L3 实测验证** | Mac Mini E2E 真实数据 | 跑出真实 signals + alignment + acceleration 数据 |

### 8.2 反向验证守卫

每个 Stage 必须包含**反向验证守卫**（V37.9.43-hotfix 同款模式）：
- sed 注入反模式（如 #2 把对齐评分硬编码为 ⭐3）→ 单测立即 fail
- 还原后全过

### 8.3 source-level grep 守卫

每个新模块必须有：
- V37.9.4X marker 字面量
- 关键算法常量字面量（如 #1 `min_samples=3` / `eps=0.35`）
- FAIL-OPEN 注释字面量

---

## 九、Governance 集成

### 9.1 新不变式（Stage 6 立案）

**INV-RADAR-001** `opportunity-radar-three-components-aligned`
- meta_rule: MR-7 (governance-execution-is-self-observable)
- severity: high
- verification_layer: [declaration, runtime]
- 14 checks:
  - 6 declaration: 三件套模块文件存在 + V37.9.4X marker
  - 4 declaration: project_concepts.yaml schema 完整
  - 2 declaration: kb_radar.sh + 06:00 cron 注册
  - 2 runtime: subprocess 跑各组件单测端到端

### 9.2 governance 元规则候选

**MR-18 候选** `opportunity-radar-must-cross-validate-llm-vs-rule`
- 描述: 任何 LLM 评分（如 #2 项目对齐度）必须有 rule_check 验证层 + LLM 与 rule 不一致时显式标 ⚠
- 防止: LLM hallucination 在评分中扩散
- 立案时机: Stage 2 完成后（积累足够实例）

---

## 十、Mac Mini 部署路径

### 10.1 auto_deploy.sh FILE_MAP 扩展

新增 5 个 entry（Stage 1-6 同步加）：
```
"cross_source_signal_aggregator.py|$HOME/cross_source_signal_aggregator.py"
"project_concepts.yaml|$HOME/project_concepts.yaml"
"project_alignment_scorer.py|$HOME/project_alignment_scorer.py"
"kb_trend_acceleration.py|$HOME/kb_trend_acceleration.py"
"kb_radar.sh|$HOME/kb_radar.sh"
```

### 10.2 Mac Mini 依赖

- sentence-transformers ✓ 已装（V29.3）
- scikit-learn (DBSCAN) — 待装：`pip3 install scikit-learn`
- jieba (中文分词，#3 复用 kb_trend.py 已装) ✓

### 10.3 cron 注册

Stage 5 完成后：
```bash
bash ~/crontab_safe.sh add '0 6 * * * bash -lc "bash ~/kb_radar.sh >> ~/.openclaw/logs/jobs/kb_radar.log 2>&1"'
```

### 10.4 验证步骤（每 Stage 完成后）

1. 用户合并 PR → Mac Mini `git fetch + reset --hard origin/main`
2. `bash ~/openclaw-model-bridge/auto_deploy.sh`（手动触发避免 2-min 等待）
3. dev 环境跑通的命令在 Mac Mini 重跑（如 Stage 1 的 `python3 ~/cross_source_signal_aggregator.py --date today`）
4. preflight `--full` 验证（应 79+/0/3-/0+ 范围）
5. 等下次 cron 触发（如 kb_dream 03:00 / kb_radar 06:00）观察实际推送

---

## 十一、元价值与风险评估

### 11.1 元价值

**为什么这个三件套是真正的杠杆点**：

1. **从"信息推送"升级为"决策支持"** — 当前 14 sources 14 段独立推送，用户看到 50 条但无法决策。三件套交集后可能只剩 3 条但每条都"必看"，**注意力杠杆 10×**。

2. **真正实现"数据复利"** — 当前 KB 是"数据冰库"（存了但很少回查）。三件套让 KB 成为"信号雷达基础设施"（每次新数据进来都触发跨历史信号检测）。

3. **从"单点深度"升级为"系统智能"** — 当前 kb_dream/deep_dive 是 single-LLM-call 智能。三件套让多个独立信号在统计/embedding 层先聚合再 LLM，**信噪比 10×**。

4. **可外推性** — 三件套的"早期信号雷达"概念是普适的，未来 V3 路标 `pip install ontology-engine` 时这套可以独立成 `pip install opportunity-radar` 包，是项目话语权 + 商业化潜力的种子。

### 11.2 风险与缓解

| 风险 | 严重度 | 缓解 |
|------|-------|------|
| **DBSCAN 参数 calibration 困难** | 中 | Stage 1 dev 用真实 KB 数据手动调 + Mac Mini 二次验证 + 暴露 env var 让运维调 |
| **LLM 项目对齐评分不稳定** | 中 | Stage 2 加 rule_check 验证层，cross-check 不一致时显式 ⚠ |
| **趋势加速度噪声大（小基数）** | 中 | Stage 3 设最小基数门槛（如周占比 < 0.5% 不算加速）|
| **kb_radar.sh 06:00 推送过早被忽略** | 低 | Stage 5 选可调时段 + 用户视角验证 |
| **三件套复杂度让维护成本上升** | 中 | 每个组件独立 + FAIL-OPEN + 充分测试 + 文档先行（本文档）|
| **Mac Mini 依赖 scikit-learn 装失败** | 低 | Stage 1 先验证 pip install + 提供降级路径（基于关键词 Jaccard 替代 DBSCAN）|

### 11.3 何时算项目交付完成

**最终交付定义** = 用户在某天早晨 06:00 收到雷达推送，点开看到"红色机会点 1：CoT pruning 综合信号"，**当天就 fork 了相关 repo 并加入了 V37.9.X+ 实施 backlog** —— 即"广度数据 → 深度信号 → 用户行动"完整闭环可重复发生。

---

## 十二、与现有 V37.9.x 路线图的关系

### 12.1 不冲突，并行推进

- **audit P1+P2+P3 续**（hf_papers / acl_anthology / karpathy_x / openclaw_official x2 / 等 11 个）继续按 V37.9.45+ 机械迁移
- **三件套**作为"深度合成层"扩展，与"广度迁移层"并行不冲突
- **协同优势**：Stage 2 #2 项目对齐度评分**正好可以同步加到** V37.9.45+ 迁移脚本的 5 字段 prompt（变 6 字段），一个 session 同时推进两条线

### 12.2 与 5/11 决策窗口的关系

- 三件套是**新增能力**，不依赖 jobs_to_crontab / kb_sources_to_index machine_sync 激活
- Stage 1-3 在 5/11 之前可独立推进
- Stage 4-6 在 5/11 决策后再视情况推进

### 12.3 与 OpenClaw 升级的关系

- 三件套**完全独立**于 OpenClaw Gateway 版本（仍 v2026.3.13-1 hold）
- 不需要 Gateway 升级前提
- 即使 OpenClaw 6/15 升级时也不冲突

---

## 十三、设计文档版本管理

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-05-09 | 初始版本（V37.9.44 后立项）|
| v0.2 | TBD | Stage 1 PoC 完成后回填实施细节 |
| v0.3 | TBD | Stage 2-3 完成后回填集成数据 |

下次 session 实施时，直接对照本文档逐 Stage 推进。如发现设计与实际不符，更新本文档版本（v0.X+1）保持单一真理源。

---

> **文档结尾承诺**：本设计已涵盖三件套的所有关键决策点，**下次实施 session 应能直接按 Stage 1-6 路径推进，无需再做高层方案选择**。如需具体代码实现细节，参考各组件下的"模块设计"+"算法"+"单测计划"段。

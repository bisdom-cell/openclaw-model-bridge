# V37.9.38 LLM Cron Fail-Fast Audit Report

> 血案背景：V37.9.36 rss_blogs LLM 双 provider 故障 + 占位符 silent fallback 推送 3 篇 `要点：技术深度文章 / 价值：⭐⭐⭐` 给用户。本报告扫描所有 LLM cron 候选脚本是否对齐 V37.9.36-37 fail-fast 模式。

## 概览

- 候选脚本总数：21
- ✅ 已对齐：9（V37.5 / V37.8.10 / V37.9.16 / V37.9.36-37）
- ❌ 未对齐：12（含占位符或缺 fail-fast 标志）
- ⚠️ 缺失文件：0
- 📌 占位符 finding 总数：7

## ✅ 已对齐脚本（视为合规）

| 脚本 | 对齐版本 | 占位符数 | SYSTEM_ALERT | send_alert | status:failed | exit 1 |
|---|---|---|---|---|---|---|
| `./jobs/arxiv_monitor/run_arxiv.sh` | V37.9.43 | 0 | ✓ | ✓ | ✓ | ✗ |
| `./jobs/semantic_scholar/run_semantic_scholar.sh` | V37.9.39 | 0 | ✓ | ✓ | ✓ | ✗ |
| `./jobs/dblp/run_dblp.sh` | V37.9.40 | 0 | ✓ | ✓ | ✓ | ✗ |
| `./jobs/rss_blogs/run_rss_blogs.sh` | V37.9.36-37 | 0 | ✓ | ✓ | ✓ | ✗ |
| `./jobs/ai_leaders_x/run_ai_leaders_x.sh` | V37.9.40 | 0 | ✓ | ✓ | ✓ | ✗ |
| `./kb_evening.sh` | V37.8.10 | 0 | ✓ | ✓ | ✗ | ✗ |
| `./kb_review.sh` | V37.5 | 0 | ✓ | ✓ | ✗ | ✗ |
| `./kb_deep_dive.sh` | V37.9.16 | 0 | ✓ | ✓ | ✗ | ✓ |
| `./run_hn_fixed.sh` | V37.9.41 | 0 | ✓ | ✓ | ✓ | ✗ |

## ❌ 未对齐脚本（V37.9.38+ 修复目标）

按占位符 finding 数降序排列（高 finding 数 = 高血案风险）。

| 脚本 | LLM | SYSTEM_ALERT | source_notify | send_alert | status:failed | exit 1 | 占位符数 | 评分 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---:|---:|
| `./jobs/hf_papers/run_hf_papers.sh` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | 3 | 0/6 |
| `./jobs/openclaw_official/run.sh` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | 1 | 1/6 |
| `./jobs/openclaw_official/run_discussions.sh` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | 1 | 1/6 |
| `./jobs/acl_anthology/run_acl_anthology.sh` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | 1 | 0/6 |
| `./jobs/karpathy_x/run_karpathy_x.sh` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | 1 | 0/6 |
| `./jobs/finance_news/run_finance_news.sh` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | 0 | 2/6 |
| `./jobs/freight_watcher/run_freight.sh` | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | 0 | 2/6 |
| `./kb_dream.sh` | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | 0 | 2/6 |
| `./jobs/chaspark/run_chaspark.sh` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | 0 | 1/6 |
| `./jobs/github_trending/run_github_trending.sh` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | 0 | 1/6 |
| `./jobs/ontology_sources/run_ontology_sources.sh` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | 0 | 1/6 |
| `./kb_inject.sh` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | 0 | 1/6 |

## 占位符反模式 findings 详情

### `./jobs/hf_papers/run_hf_papers.sh`

- **L325** 命中 `贡献：AI领域相关研究`
  - 上下文: `[quoted_inline_sq] pending_contrib or '贡献：AI领域相关研究',`
- **L361** 命中 `贡献：AI领域相关研究`
  - 上下文: `[quoted_inline_dq] contrib = "贡献：AI领域相关研究"`
- **L362** 命中 `价值：⭐⭐⭐`
  - 上下文: `[quoted_inline_dq] stars = "价值：⭐⭐⭐"`

### `./jobs/acl_anthology/run_acl_anthology.sh`

- **L341** 命中 `价值：⭐⭐⭐`
  - 上下文: `[quoted_inline_dq] stars = "价值：⭐⭐⭐"`

### `./jobs/karpathy_x/run_karpathy_x.sh`

- **L359** 命中 `价值：⭐⭐⭐`
  - 上下文: `[quoted_inline_dq] msg_lines.append("价值：⭐⭐⭐")`

### `./jobs/openclaw_official/run.sh`

- **L136** 命中 `价值：⭐⭐⭐`
  - 上下文: `[shell_multiline_close] 价值：⭐⭐⭐"`

### `./jobs/openclaw_official/run_discussions.sh`

- **L155** 命中 `价值：⭐⭐⭐`
  - 上下文: `[shell_multiline_close] 价值：⭐⭐⭐"`

## V37.9.38+ 修复路线图

**今日 V37.9.38 完成**：MRD-LLM-PLACEHOLDER-FALLBACK-001 扫描器 + audit 报告 + arxiv_monitor PoC fix + INV-LLMCRON-AUDIT-001。

**V37.9.39+ 候选脚本**（按风险优先级，逐次 1-2 个收敛）：

**P1 — 多 finding 高风险（同款 V37.9.36 血案模式）**：
- `./jobs/hf_papers/run_hf_papers.sh` — 3 findings, score 0/6

**P2 — 单 finding**：
- `./jobs/acl_anthology/run_acl_anthology.sh` — score 0/6
- `./jobs/karpathy_x/run_karpathy_x.sh` — score 0/6
- `./jobs/openclaw_official/run.sh` — score 1/6
- `./jobs/openclaw_official/run_discussions.sh` — score 1/6

**P3 — 无占位符 finding 但缺 fail-fast 标志（潜在静默风险）**：
- `./jobs/freight_watcher/run_freight.sh` — score 2/6
- `./jobs/finance_news/run_finance_news.sh` — score 2/6
- `./kb_dream.sh` — score 2/6
- `./jobs/github_trending/run_github_trending.sh` — score 1/6
- `./jobs/ontology_sources/run_ontology_sources.sh` — score 1/6
- `./jobs/chaspark/run_chaspark.sh` — score 1/6
- `./kb_inject.sh` — score 1/6

## 合规标准（V37.9.36-37 reference）

1. **`source notify.sh`** + 定义 `send_alert()` helper
2. **`[SYSTEM_ALERT]`** 前缀（V37.4.3 PA 上下文隔离不变式）
3. **LLM 三层检测**：HTTP error / JSON parse fail / empty content（任一触发 → fail-fast）
4. **`status: llm_failed`** 写 last_run JSON（不谎报 `ok`）
5. **`exit 1`** 在告警之后（让 cron 退出码可观测）
6. **emit 端禁占位符 fallback**（不能硬编码 `价值：⭐⭐⭐` 等）

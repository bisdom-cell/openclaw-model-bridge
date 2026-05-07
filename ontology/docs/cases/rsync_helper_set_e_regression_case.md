# V37.9.27 rsync_helper 回归杀 20 caller 血案

> **日期**: 2026-05-04 (V37.9.27 引入) → 2026-05-07 (V37.9.31 修复)
> **版本**: V37.9.27 → V37.9.30 (EPERM 100% 持平揭示) → **V37.9.31 fail-open**
> **元规则**: MR-4 (silent-failure-is-a-bug) 第 24 次演出 + MR-10 (understand-before-fix) 反向兑现
> **状态**: 已修复，待 Mac Mini 5/8 8:00 cron 验证

---

## TL;DR

**V37.9.27 引入的回归 bug 让 20 个 set -e caller 每天被杀 mid-script，潜伏 3 天**：

- **触发器** — V37.9.27 movespeed_rsync_helper.sh `exit "$EXIT_CODE"` 透传 rsync 失败码
- **放大器** — V37.9.30 EPERM 21/24h 100% 持平，每次 helper 都失败 exit 非 0
- **掩护者** — V37.9.4-V37.9.26 EPERM 偶发时 retry 通常成功，回归 bug 没暴露

**用户视角症状**: preflight 报告"货代 deep_dive: last_run.json 无 deep_dive 字段（旧版本脚本？）"。**真因**: Step 8-10 从未执行（被 helper exit 非 0 + caller set -e 杀掉），last_run.json 在 Step 5 已写但缺字段。

---

## 完整因果链架构图

```
2026-05-04 V37.9.27 上线
    │
    ├─ helper line 116: exit "$EXIT_CODE"  ← 引入回归
    ├─ 设计假设: "transient EPERM 30s 内自愈, retry 通常成功"
    │
    │  这个假设当时正确 (V37.9.4-V37.9.26 EPERM 偶发 ~19/24h)
    │  helper retry 大部分成功 → exit 0 → caller 继续
    │  少数失败时 caller set -e 杀脚本, 但每天只发生 ~1 次, 无人察觉
    │
2026-05-06 V37.9.29 ownership 假说部分修复
    │
    ├─ chown 让 UID 统一 bisdom:staff (修了真 bug)
    ├─ 但 EPERM 100% 持平 (UID 错位非 EPERM 根因)
    │
2026-05-07 V37.9.30 取证扩展 (上午)
    │
    ├─ 添加 ACL/lsof/snapshot 取证维度
    ├─ 数据揭示: EPERM 21/24h 100%, 每次都失败
    │
    │  此时 V37.9.27 回归从"偶发"变"100% 触发"
    │
2026-05-07 上午 8:00 freight cron 跑
    │
    ├─ Step 1-4 RSS抓取 + LLM 分析 ✅
    ├─ Step 5 推送 WhatsApp ✅
    ├─    └─ STATUS_FILE 写: status=ok, new=5, sent=true (无 deep_dive 字段)
    ├─ Step 6 KB 归档 ✅
    ├─ Step 7 rsync /Volumes/MOVESPEED  ❌ EPERM
    │       └─ rsync_helper 全 retry 失败 → exit "$EXIT_CODE" (e.g. 23)
    │
    ├─ caller run_freight.sh: set -eo pipefail (line 9)
    │       └─ 立即杀脚本 (mid-script kill)
    │
    ├─ Step 8 提取高星企业 — 永不执行 ❌
    ├─ Step 9 ImportYeti scraper — 永不执行 ❌
    ├─ Step 10 客户画像 — 永不执行 ❌
    │
2026-05-07 上午 9:13 用户跑 preflight --full
    │
    ├─ 检查 last_run.json deep_dive 字段
    ├─ d.get('deep_dive', 'missing') → 'missing' ✓ (字段不存在)
    ├─ Preflight 报告: "货代 deep_dive: last_run.json 无 deep_dive 字段（旧版本脚本？）"
    │       └─ 误导性诊断 — 不是旧脚本, 是脚本被 set -e 杀
    │
2026-05-07 用户反馈 "彻底解决包括反爬"
    │
    ├─ 用户假设是反爬问题 (基于"deep_dive 字段缺失" + "可能 Cloudflare 拦截"提示)
    ├─ Claude 按原则 #28 三问 + 真实数据查证
    ├─ 真根因揭示: rsync_helper exit code → set -e 杀脚本
    │
2026-05-07 V37.9.31 修复 (4 + 1 fix)
    ├─ F1 helper line 116: exit "$EXIT_CODE" → exit 0  ← 核心修复
    ├─ F2 4 exit path 写 deep_dive 字段 (schema 完整性)
    ├─ F3 preflight 容忍新 status
    ├─ F4 反爬升级 (用户要求, 即便不是主因也是预防)
    └─ F5 53 新单测 (含 set -e caller subprocess 真验证)
```

---

## 三层根因分析

### 触发器 (Triggering Cause)

`movespeed_rsync_helper.sh` line 116: `exit "$EXIT_CODE"` 透传 rsync 失败码（V37.9.27 引入）。

**为什么会引入这个 bug**: V37.9.27 的设计目标是"集中 retry+jitter+capture 三层包装替代 20 处 inline pattern"。文档明确写 `Exit codes: N rsync's exit code if all retries failed`，**作者主动选择**透传 exit code，认为"helper 应该诚实报告 rsync 状态"。

**为什么这个选择是 bug**: helper 的 caller 都用 `set -eo pipefail`，他们想要的不是"诚实状态"，而是"backup 是 best-effort，失败不应杀脚本"。V37.9.27 之前的 inline pattern `rsync ... 2>&1 || echo WARN` 用 `||` 保证整个语句 exit 0，**caller 实际依赖这个 invariant**。V37.9.27 helper 破坏了这个隐含契约。

### 放大器 (Amplifying Factor)

**V37.9.4-V37.9.26 时期 EPERM 偶发**：~19 次/24h transient EPERM 中，helper retry 大部分成功 → 失败时杀 caller 的事件每天 ~1-2 次，无人主动察觉，没有报告。

**V37.9.30 EPERM 100% 持平揭示真相**：21 incidents/24h 全部 chown 后仍 EPERM，每次 helper 都全 retry 失败 → exit 非 0 → 每天 20 个 set -e caller 全部被杀 mid-script。

### 掩护者 (Concealing Conditions)

**last_run.json 在 Step 5 已写 status=ok+sent=true**：从外观看 "freight 跑了，状态正常"。Step 5 的"成功"指 WhatsApp 推送成功，与 Step 8-10（深挖）无关。这种"半成功"状态特别难诊断。

**preflight 错误诊断 "旧版本脚本"**：preflight 看到 deep_dive 字段缺失就 grep 字符串报"旧脚本"，把"脚本中段被杀"误诊为"脚本是旧版本"。这是 preflight 端的二次掩护。

**Step 8-10 文件 mtime 漂移检测困难**：`high_star_companies.txt` / `scraper.log` / `enriched_data.txt` 全部停在 5/4 14:00（最后一次 rsync 成功的日期），但需要主动 `ls -lat` 才能发现，preflight 没扫这个维度。

---

## 时间线还原

| 时间 | 事件 |
|---|---|
| **2026-05-04** | V37.9.27 movespeed_rsync_helper.sh 上线，replace 20 个 inline rsync site |
| 5/4-5/6 | V37.9.27-V37.9.29 期间 EPERM 偶发，helper retry 大部分成功，回归 bug 仅 1-2 次/天触发，未察觉 |
| 5/4 14:00 | 最后一次 rsync helper 成功（freight Step 7 之后），高星企业文件最后一次写入 |
| 5/5-5/6 | freight cron 每天跑 3 次（8:00/14:00/20:00），rsync 失败时被 set -e 杀，但用户没注意 |
| **2026-05-07 8:00** | freight cron 跑：Step 1-7 完成，Step 7 rsync 失败 → helper exit 23 → caller set -e 杀，Step 8-10 永不执行 |
| 5/7 9:13 | 用户跑 `bash preflight_check.sh --full`，报告"货代 deep_dive: last_run.json 无 deep_dive 字段（旧版本脚本？）"|
| 5/7 9:30 | 用户反馈"彻底解决包括反爬"，Claude 按原则 #28 查证 |
| 5/7 10:00 | 真根因揭示：set -e + helper exit 非 0，反爬非主因 |
| 5/7 10:30 | V37.9.31 修复完成（F1-F5 + 53 新单测），1990 tests / 0 fail，commit + push |

---

## 六条件组合爆炸分析

V37.9.31 真因要求**6 个条件同时满足**才触发：

| 条件 | 状态 | 必要性 |
|---|---|---|
| 1. helper line 116 透传 rsync exit code | ✅ V37.9.27 引入 | 触发器 |
| 2. caller 用 `set -eo pipefail` | ✅ run_freight.sh line 9 | 放大器 |
| 3. EPERM 高频 (~100%) | ✅ V37.9.30 数据揭示 | 放大器 |
| 4. caller 在 rsync 之后还有重要逻辑 | ✅ Step 8-10 (高星/scraper/画像) | 放大器 |
| 5. last_run.json 在 rsync 之前已写 | ✅ Step 5 写, Step 7 才 rsync | 掩护者 |
| 6. preflight 把 missing 字段误判为"旧脚本" | ✅ V3 时期 case 分支 | 掩护者 |

**任何一个条件不满足，问题都不会以"deep_dive 字段缺失被误诊为旧脚本"形式浮现**。这是 MR-4 silent-failure 的多条件组合典型 — 需要 stack 的 6 层全部对齐才暴露。

---

## MR-4 silent-failure 第 24 次演出

| 演出 | 形态 |
|---|---|
| 第 1-22 次 | 错误处理失败 (吞 / 稀释 / 误推送) |
| 第 23 次 | V37.9.30 — 修复成功+验证生效+但目标问题没改善 |
| **第 24 次** | **V37.9.31 — 修复 (V37.9.27) 引入更深 silent failure (20 caller daily silent loss 3 天)** |

**新形态特征**: 修复破坏了 caller 的隐含契约 (set -e + best-effort backup)，下游每天 20 caller 被杀但**所有现存观测路径都看不到**：
- 单测看不到（V37.9.27 没测 set -e caller 场景）
- preflight 看不到（只检查 deep_dive 字段，不检查 step 完成度）
- watchdog 看不到（只关心 rsync 失败次数，不关心 caller 是否被杀）
- last_run.json 看不到（Step 5 已写 ok+sent=true，看起来正常）

直到 V37.9.30 EPERM 100% 持平把回归从"偶发"变"100% 触发"，用户用 `ls -lat` 看到文件 mtime 漂移才浮现。

---

## V37.9.31 修复结构 (F1-F5)

### F1 (核心修复): movespeed_rsync_helper.sh fail-open exit 0

```bash
# 修改 line 116: exit "$EXIT_CODE" → exit 0
# 加 V37.9.31 注释 + set -e contract 文档
exit 0  # V37.9.31: fail-open — 恢复 V37.9.4-V37.9.26 invariant
```

**rationale**: helper 的 fail-loud 通过三路保留：
1. stderr "WARN: SSD" 字面量（INV-BACKUP-001 契约）
2. JSONL forensic record (V37.9.14 capture helper)
3. V37.9.26 watchdog 24h ≥5 alert chain

**不需要**透传 exit code，因为 caller 不消费它。

### F2: run_freight.sh 4 exit path 写 deep_dive

| Exit path | deep_dive value |
|---|---|
| NEW_COUNT=0 | `skipped_no_news` |
| LLM 失败 (exit 1) | `skipped_llm_failed` |
| 解析率<50% (exit 2) | `skipped_parse_low` |
| Step 5 推送成功 | `pending` (Step 9 后续覆盖) |
| Step 5 推送失败 | `pending` |
| Step 9 完成 | `ok` / `no_data` / `skipped` |

### F3: preflight 容忍新 status

`pass`: skipped_no_news. `warn`: skipped_llm_failed / skipped_parse_low / pending. `fail`: missing (V37.9.31 后是真异常).

### F4: ImportYeti 反爬升级 (用户要求)

虽然非主因，但反爬升级是合理预防（V37.9.31 fail-open 后 scraper 真的会跑每天）：

- `playwright-stealth` 50+ patches (用户选专业库方案，Mac Mini 需 `pip3 install`)
- 4 modern Chrome UA 轮换 (Chrome 130/131 macOS + Windows + Edge)
- 5 真实 viewport (1280-1920 × 720-1080)
- 完整 sec-ch-ua / sec-fetch-* / accept-language q-value headers
- Cloudflare 检测纯函数 (5 patterns: just a moment / challenge / cf-chl / verifying / block page)
- Backoff [30,60,120]s 替代 V3 fixed 10s
- 公司间 delay 5-12s 随机替代 V3 fixed 3s

### F5: 53 新单测

- `test_freight_schema_v9_31.py` 13 单测 (schema 完整性 + preflight 容忍)
- `test_importyeti_scraper_anti_crawl.py` 39 单测 (UA / viewport / headers / CF detection / backoff / delay / source guards)
- `test_movespeed_rsync_helper.py::test_caller_with_set_e_survives_rsync_failure_v37_9_31` 真 subprocess 跑 set -e caller + helper，验证 POST_RSYNC_REACHED 字面量出现 = caller 没被杀

---

## 元教训

### 1. 修复必须验证所有 caller 行为契约

V37.9.27 设计 helper 时只测了 helper 自身行为，**没测 caller 在 helper 失败时的行为**。helper 的 exit code 是它的"对外契约"，改变这个契约会破坏 caller 的隐含假设。

**改进**: 任何 helper 修改, 测试矩阵必须包括 "caller 用 set -e + helper 失败" 的端到端场景。V37.9.31 已加 `test_caller_with_set_e_survives_rsync_failure_v37_9_31`。

### 2. 数据驱动诊断方法论第 7 步

V37.9.x 系列方法论现在是 7 步：

| 步 | 版本 | 内容 |
|---|---|---|
| 1 | V37.9.14 | 取证 (被动 JSONL) |
| 2 | V37.9.26 | 主动告警 (24h ≥5 阈值) |
| 3 | V37.9.27 | 主动修复 (retry+jitter+helper) |
| 4 | V37.9.28 | 数据驱动诊断工具 (analyzer) |
| 5 | V37.9.29 | ownership 维度 |
| 6 | V37.9.30 | ACL/handle/snapshot 维度 |
| **7** | **V37.9.31** | **修复必须验证 caller 行为契约 (反向兑现)** |

### 3. preflight 误诊也是一种 silent failure

preflight 看到 deep_dive 字段缺失就报"旧版本脚本？"，把真因（脚本被 set -e 杀）掩护掉。**诊断工具自己也可能是 silent failure 的一部分**。

**改进**: preflight 在 V37.9.31 已升级语义，pending 状态指向 V37.9.31 上下文 (rsync_helper 修复)，避免未来同类误诊。

### 4. 用户假设可能错，但仍要按用户要求做

用户假设是反爬问题，要求"包括反爬"。Claude 按原则 #28 查证发现真因是 rsync_helper，但**仍按用户要求做反爬升级**（playwright-stealth）。这不是浪费工作 — V37.9.31 fail-open 后 scraper 真的会每天跑，反爬升级是合理预防。

**改进**: 用户视角的"问题"和工程视角的"根因"可能不一致，但都需要解决。修真因 + 满足用户要求是双轨并行。

---

## 相关原则

- 原则 #26 异常分析宪法（本案完整实践）
- 原则 #28 理解再动手（rsync_helper 真因 vs 反爬假设）
- MR-4 silent-failure-is-a-bug（第 24 次演出新形态）
- MR-10 understand-before-fix（反向兑现）
- MR-11 shell-function-output-must-go-to-stderr（V37.8.6 兑现，本案保留）

---

## 与其他案例的关系

- **V37.9.27 retry helper**: 引入此回归的修复
- **V37.9.30 forensic dimensions**: 揭示 EPERM 100% 持平，让此回归从"偶发"变"100% 触发"
- **V37.9.31 (本案)**: 闭环
- **未来 V37.9.32+**: Mac Mini 验证修复有效 + V37.9.30 ACL/lsof/snapshot 数据回归

V37.9.x 系列至今已是 7 步数据驱动诊断方法论的完整闭环。每一步都不是凭推测，都是数据证伪假说后的精确扩展。

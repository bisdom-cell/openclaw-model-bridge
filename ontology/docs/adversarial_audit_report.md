# 对抗性混沌审计实证报告（路线 B）

> 2026-04-20 交付 | 16 场景 × 真实破坏注入 = audit 真实防御率客观测量
> **核心价值**：不是理论推测，是**对真实仓库文件做真实破坏**，看 audit 能不能抓到

---

## 📊 核心数据

```
Category A 真实防御率:  10/10  (100%)   ← 已知血案回归攻击
Category B 意外 catch:   0/6   ( 0%)   ← 探测攻击 (预期盲区)
总计 PASS:              16/16  (100%)   ← 符合预期（A 全抓 + B 全不抓）
```

**核心结论**：
- ✅ Audit 对 V37.3~V37.8.17 已登记的 53 条不变式 + 4 MRD 运行时扫描器覆盖的维度，**真实防御率 100%**
- ⚠️ Audit 对 6 个未覆盖的维度（裸 except / LLM 成本 / last_run 一致性 / dict 污染变种 / 推送 bypass / audit 自性能）**真实防御率 0%**，这是**未知盲区的量化证据**

---

## 🎯 方法论

### 场景分类

**Category A（回归攻击）**：10 个场景注入**已知血案**的核心破坏模式
- 期望 audit 100% 抓到（无漏 = 合格）
- PASS = audit 正确报警
- FAIL = BLIND SPOT（真实漏洞）

**Category B（探测攻击）**：6 个场景注入 audit **预期未覆盖**的维度
- 期望 audit 抓不到（= 证实盲区真实存在）
- PASS = 符合预期（盲区被量化）
- UNEXPECTED = 意外 catch（audit 覆盖比想象广）

### 注入机制

- 对真实仓库文件做临时 mutation（regex 替换）
- 跑 `governance_checker.py` 观察 violation 数量变化
- `try/finally` + git status 验证现场还原干净
- 三防线：脚本崩溃 → try/finally 恢复 → 预期文件已还原 → 验证 git clean

### Catch 判定

```python
delta = mutated_violations - baseline_violations
caught = delta > 0
```

按 violation 数量差异而非 boolean 判定。避免 "baseline 本来就有 warn → 无法区分" 的 INCONCLUSIVE 噪声。

---

## 📝 Category A 逐场景结果

| # | 场景 | 破坏 | 期望抓到的不变式 | 结果 | delta |
|---|---|---|---|---|---|
| C1 | delete_soul_rule_10 | 删 SOUL.md 规则 10（告警不跟进） | INV-PA-002 | ✅ PASS | +1 fail |
| C2 | inflate_max_tools | config.yaml `max_tools: 12` → 999 | INV-TOOL-001/002 | ✅ PASS | +1 fail |
| C3 | delete_system_alert_marker | `SYSTEM_ALERT_MARKER = None` | INV-PA-001 + 5 runtime | ✅ PASS | +3 errors |
| C4 | corrupt_reserved_safe_content | RESERVED_FILE_SAFE_CONTENT 插非注释行 | INV-HB-001 | ✅ PASS | +17 errors 🎉 |
| C5 | empty_reserved_file_basenames | 清空 frozenset | INV-HB-001 | ✅ PASS | +1 fail |
| C6 | wa_keepalive_alerts_via_whatsapp | ESCALAT 路径注入 `--channel whatsapp` | INV-WA-001 + MRD-ALERT-INDEPENDENCE-001 | ✅ PASS | +1 fail + 1 mrd_warn |
| C7 | remove_quiet_alert_discord | 删 "Discord 仍推" 注释 | INV-QUIET-001 | ✅ PASS | +1 fail |
| C8 | dream_log_to_stdout | kb_dream log() 移除 `>&2` | INV-DREAM-003 + MRD-LOG-STDERR-001 | ✅ PASS | +1 fail |
| C9 | positional_parser_in_kb_review | kb_review_collect 末尾加 `i += 3` | MRD-LLM-PARSER-POSITIONAL-001 | ✅ PASS | +1 mrd_warn |
| C10 | reinstate_zombie_pdchina | FINANCE_X_ACCOUNTS 加回 PDChina | INV-X-001 file_not_contains | ✅ PASS | +1 fail |

**Category A 真实防御率 = 10/10 = 100%**

---

## 📝 Category B 逐场景结果（盲区暴露）

| # | 场景 | 破坏 | 为什么 audit 抓不到 | 结果 |
|---|---|---|---|---|
| C11 | silent_error_swallow | adapter.py 末尾加 `try: ... except: pass` | **无"裸 except 禁止"不变式** — MR-4 silent-failure 主要覆盖已发生的具体 silent 模式，未普适化为"所有 try/except pass 都是嫌疑" | ✅ 未 catch（符合预期） |
| C12 | llm_cost_runaway | adapter.py 插入 `MAX_RETRIES = 999` | **无 LLM 成本/retry 上限不变式** — INV-QUOTA-001 只防"连续失败熔断"不防"单次爆炸 retry 次数" | ✅ 未 catch |
| C13 | missing_last_run_write | kb_inject.sh 改 last_run_inject.json 路径 | **无"所有 LLM job 必须写 last_run_*.json"不变式** — INV-OBSERVABILITY-001 只覆盖 kb_inject + kb_harvest_chat 两个 job | ✅ 未 catch |
| C14 | kb_write_dict_repr | tool_proxy 末尾加 dict 直接 str() 函数 | **INV-KB-001 只防 list content blocks** — dict 变种 `str({"k":"v"})` = `{'k': 'v'}` 同样产生 repr 污染，但 flatten_content 只处理 list | ✅ 未 catch |
| C15 | push_bypass_notify_sh | 新建 `chaos_rogue_pusher.sh` 直接 openclaw message send | **MRD-NOTIFY-001 按 topic 扫 caller**，但不检测"推送脚本必须走 notify.sh" — 新建 rogue script 绕过 notify.sh 无人拦 | ✅ 未 catch |
| C16 | audit_performance_regression | governance_checker.py 启动时 `time.sleep(0.5)` | **MR-7 只覆盖 summary 正确性** — audit 自身性能 / 资源 / 执行时间 / 跳过率 均无自观察（观察者盲区的残留） | ✅ 未 catch |

**Category B 意外 catch = 0/6 = 0%** → 6 个盲区全部被**量化确认**

---

## 🔬 盲区分类 + V37.8.18 候选修复路线

Category B 的 6 个盲区按紧迫性和可行性分为三档：

### 🔴 高优先级（下轮就补）

| 盲区 | 新不变式/MRD | 修复工作量 |
|---|---|---|
| **C14 dict repr pollution** | 扩展 INV-KB-001 → 支持 dict 类型 + runtime 断言 `flatten_content({"k": "v"})` 不含 `{` | S — 加 1 runtime check + `flatten_content` 补一个分支 |
| **C15 push bypass notify.sh** | 新增 INV-PUSH-002 "所有 `openclaw message send` 必须来自 notify.sh" + MRD 扫描所有 `*.sh` 中的 `openclaw message send` call 是否在 notify.sh / 白名单脚本内 | M — 需要白名单机制 |
| **C11 silent error swallow** | 新增 MRD-SILENT-EXCEPT-001 扫描所有 `.py` 的裸 `except:` 或 `except Exception: pass` | M — 正则扫 + 白名单（部分 test fixture 合法） |

### 🟡 中优先级（1-2 周内）

| 盲区 | 新不变式/MRD | 修复工作量 |
|---|---|---|
| **C13 last_run_* 不一致** | 扩展 INV-OBSERVABILITY-001 → registry-driven 检查所有 LLM job 都有 last_run_*.json 写入 | M — 需要遍历 registry + scan pattern |
| **C12 LLM cost runaway** | 新增 INV-COST-001 "所有 retry 常量 ≤ 上限"（默认 5） | S — 声明层 grep |

### ⚫ 低优先级（观察者盲区，长期）

| 盲区 | 新不变式/MRD | 修复工作量 |
|---|---|---|
| **C16 audit performance** | 新增 audit-of-audit 子系统测 audit 自身 wall-time / memory / skip rate | L — 需要独立 metric collector |

---

## 💡 元洞察

### 1. Audit 的真实防御率是"已登记维度的 100%"

对抗性测试证实 audit 在其**覆盖范围内**是**可靠**的（Category A 10/10）。问题不在"audit 失效"，而在"audit 覆盖不全"。这和路线 A 结论一致（**0% 预防率 + 87% 回归率 = regression engineering 系统**）。

### 2. Category B 的 6 个盲区全部落在"空白类别"

所有 6 个盲区都对应路线 A 分析出的 **"空白类别"占 80%** 的主因。这证明路线 A 的盲区分类是正确的。

更重要的是：**对抗性测试把"抽象的空白类别"变成"具体的 C11~C16 六个可量化的盲区"**。每个都可以独立立案修复。

### 3. 三种"修补路径"的优先级

对抗性测试天然把未覆盖维度分类：
- **能直接扩展已有不变式**（C14 扩 INV-KB-001）→ 最快
- **需要新增不变式 + 白名单**（C11 / C15）→ 中等
- **观察者盲区**（C16）→ 架构级（audit-of-audit 子系统）

### 4. 对抗性审计本身可以纳入 full_regression

当前 `adversarial_chaos_audit.py` 是手动运行工具。如果加入 full_regression 定期运行，可以：
- 每次 PR 合并后跑一次，确认 regression 场景（C1-C10）仍全部抓到
- 定期跑探测场景（C11-C16）看是否因其他修复**意外覆盖了新维度**

**建议**：把 Category A 部分加入 full_regression（因为是无破坏的 mutation test），Category B 留作**每月运维脚本**定期审计盲区演化。

---

## 🗺 下一步建议

### V37.8.18 (立刻可启动)

- **C14 dict pollution 扩展 INV-KB-001**（1-2 小时）
- **C15 push bypass MRD-PUSH-ROUTE-001**（3-4 小时）
- **C11 silent except MRD-SILENT-EXCEPT-001**（2-3 小时）

### V37.8.19 (下周)

- **C13 last_run 扩展 INV-OBSERVABILITY-001**（半天）
- **C12 retry 上限 INV-COST-001**（2 小时）

### V37.9 (月度目标)

- **audit-of-audit 子系统** 填补 C16 观察者盲区
- **对抗性审计集成到 full_regression**
- **Category C 新场景**：从真实 PR 回顾中提取"如果某次修复没做，会引发什么新破坏"→ 前瞻性场景

---

## 📂 交付物

- **脚本**：`ontology/tests/adversarial_chaos_audit.py` (570 行)
- **报告**：本文件 `ontology/docs/adversarial_audit_report.md`
- **路线 A 参考**：`ontology/docs/audit_coverage_retrospective.md` (618 行) — 提供盲区分类理论基础
- **元规则基础**：
  - MR-11 / MR-12 三步跃迁完成（V37.8.8/9）
  - MR-14 / MR-15 三步跃迁完成（V37.8.17）
  - MR-10 仍停在声明层（无法 MRD 化）

---

_本报告是 Stage 2 验证者阶段的第二份实证交付（路线 A 之后）。与路线 A 一起构成"audit 真实作用力"的完整画像：路线 A 告诉你**过去 15 血案的修复强度**，路线 B 告诉你**未来 16 类破坏的真实抵抗力**。_

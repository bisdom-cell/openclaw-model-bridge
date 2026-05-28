# Failure Modes Catalog — MR-4 Silent Failure 全谱系

> V37.9.86 初版 | 22 case docs / 28+ MR-4 演出 / 5 类失败模式
>
> 目的：把散落在 22 个 case doc 中的失败模式结构化，让未来的 Claude session 和外部评审者
> 在 5 分钟内理解"这个系统最容易在哪里出问题"。

---

## 失败模式分类

### Class A: 环境/OS Quirk

系统本身逻辑正确，但运行环境的隐含行为导致意外。

| # | 案例 | 根因 | 版本 | 元规则 |
|---|------|------|------|--------|
| A1 | MOVESPEED exfat EPERM | exfat fskit transient EPERM + 18 处 `2>/dev/null \|\| true` 静默 | V37.9.4 | MR-4, MR-8 |
| A2 | MOVESPEED noowners UID | noowners mount flag 掩盖 UID 0:0 / 99:99 错位 | V37.9.29 | MR-4, MR-10 |
| A3 | MOVESPEED TCC sandbox | macOS cron 派生进程无 FDA 被 kernel sandbox 拒绝 60 天 | V37.9.80 | MR-4, MR-10 |
| A4 | watchdog awk multibyte | macOS BSD awk 处理 UTF-8 surrogate exit 1 + set -e 杀脚本 | V37.9.58-h3 | MR-19 |
| A5 | bash trap override | 第二个 `trap ... EXIT` 完全覆盖第一个，lockdir 不清理 | V37.9.86 | MR-4 |
| A6 | safe_call 无 timeout | macOS BSD 无 `timeout` 命令，safe_call 三档 fallback | V37.9.78-h | MR-20 |
| A7 | bash set -e 不传播 ERR | bash 3.2 function 内 fail 不触发 ERR trap，需 set -E | V37.9.58-h4 | MR-20 |
| A8 | CJK 全角变量解析 | macOS bash 3.2 UTF-8 边界混淆 `$VAR）` → unbound | V37.9.43-h2 | MR-15 |

**防御**: INV-CROSS-OS-001 scanner (V37.9.67, 4 种 quirk 主动检测)

### Class B: 设计假设错配

代码设计基于某个假设，但假设与实际行为不一致。

| # | 案例 | 假设 vs 实际 | 版本 | 元规则 |
|---|------|-------------|------|--------|
| B1 | Dream cache key | cache key 对 mtime/sort 敏感 vs content-stable 需求 | V37.4 | MR-4 |
| B2 | kb_review shell scope | `export VAR` 在 subprocess 之后 vs 之前 | V37.5 | MR-4 |
| B3 | Dream 多窗口分片 | "4 段自然产 4 chunks" vs 合并算法 | V37.9.68-h | MR-4 |
| B4 | split 生产 caller 形态 | 测试用 `# header` vs 生产用 `## ` 开头 | V37.9.73 | MR-4 |
| B5 | path 部署假设 | `~/jobs/X/` vs `~/.openclaw/jobs/X/` | V37.9.56-h | MR-15 |
| B6 | _format_cron_line path | `.py` 文件按 bash exec 假设 vs python3 shebang | V37.9.85 | MR-8 |
| B7 | ontology parser 位置 | `lines[i+N]` 步进 vs LLM 漏行级联 | V37.8.7 | MR-12 |
| B8 | Convergence 单向 sync | 只检 missing 不检 extra | V37.9.64 | MR-17 |

**防御**: INV-PATH-CONSISTENCY-001 scanner (V37.9.82) + MR-12 key-based parser

### Class C: 错误被吞/稀释

错误发生了但被某层静默吃掉，最终用户看不到。

| # | 案例 | 吞错层 | 版本 | 元规则 |
|---|------|--------|------|--------|
| C1 | rss_blogs 占位符 | `except pass` + `log WARN 不 exit` + 硬编码 `⭐⭐⭐` | V37.9.36 | MR-4 |
| C2 | kb_evening 502 稀释 | adapter 502 → proxy `str(e)` → client 再丢 body | V37.8.10 | MR-4 |
| C3 | governance summary 吞 error | `failed_invs` 只数 fail 漏 error → 汇总说"全过" | V37.3 | MR-7 |
| C4 | watchdog 7 天 silent | watchdog 自身 abort 无 ERR trap → 全部告警 silent | V37.9.58-h3 | MR-19 |
| C5 | Dream 自引用幻觉 | log→stdout 污染 `$(cmd)` → cache → LLM 编造 | V37.8.6 | MR-11 |
| C6 | rsync helper exit 非 0 | V37.9.27 helper 透传 rsync exit → set -e 杀 caller | V37.9.31 | MR-4 |
| C7 | V37.9.57 import os 缺 | batch inject 8 jobs 缺 `import os` → NameError → FAIL-OPEN | V37.9.58-h | MR-18 |

**防御**: MR-11 (log→stderr) + MR-18 (batch inject 验证) + INV-OBSERVABILITY-001

### Class D: 链式幻觉/编造

LLM 把错误数据当事实，编造合理化叙事推送给用户。

| # | 案例 | 幻觉链路 | 版本 | 元规则 |
|---|------|---------|------|--------|
| D1 | Dream "HF 危机" | surrogate → json.dump 炸 → log 污染 cache → LLM 编造 | V37.8.6 | MR-4, MR-11 |
| D2 | PA 告警跟进 | Gateway sessions.json 混入告警 → Qwen3 跨主题关联 | V37.4.3 | MR-4 |
| D3 | Evening "v26 发布" | Top 5 paper 注入 → LLM 推断项目动态 → 编造 v26 | V37.9.56-h3 | MR-4 |
| D4 | PA 迎合回声室 | 用户提示 → PA 迎合 → 用户引用 → 循环放大 | V37.1 | — |

**防御**: hallucination_guards.py 5 档模板 (V37.9.57) + SOUL.md 规则 9/10

### Class E: 运维/流程遗漏

代码正确但部署/注册/配置步骤遗漏。

| # | 案例 | 遗漏项 | 版本 | 元规则 |
|---|------|--------|------|--------|
| E1 | kb_deep_dive cron 未注册 | jobs_registry 有但没人手动 `crontab_safe add` | V37.9.18 | MR-17 |
| E2 | drift 告警噪声 | auto_deploy drift 循环漏 status.json 豁免 | V37.8.11 | MR-4 |
| E3 | Gateway 宕 9h | quiet_alert 跳 Discord + wa_keepalive 不升级 | V37.8.13 | MR-4 |
| E4 | HEARTBEAT.md 自残 | PA write 工具碰 OpenClaw 保留文件 | V37.8.16 | MR-15 |
| E5 | 连锁修复 5 轮 | 误诊"缺配置"实际只需 `cp` | V37.8.3 | MR-10 |

**防御**: convergence framework (V37.9.19+) + MR-10 三问 + MR-15 保留文件

---

## 统计

| 类别 | 案例数 | 核心防御 |
|------|--------|----------|
| A 环境 Quirk | 8 | INV-CROSS-OS-001 scanner |
| B 设计错配 | 8 | INV-PATH-CONSISTENCY-001 + MR-12 |
| C 错误吞/稀释 | 7 | MR-11 + MR-18 + INV-OBSERVABILITY-001 |
| D 链式幻觉 | 4 | hallucination_guards 5 档 |
| E 运维遗漏 | 5 | convergence framework + MR-10 |
| **总计** | **32** | **21 meta rules + 83 invariants** |

## 元教训

1. **Silent failure 不是单一 bug class** — 它有 5 种完全不同的失败模式，每种需要不同的防御层
2. **Class A (OS quirk) 最隐蔽** — dev 环境永远绿灯，只有 Mac Mini 真跑才暴露
3. **Class D (链式幻觉) 最危险** — 错误不是消失而是被 LLM 加工成合理叙事再推送
4. **Class E (运维遗漏) 最反直觉** — 1478 单测全过但一个 `crontab add` 漏了就全废
5. **每个 Class 都有对应的框架级防御** — 不再靠"记得检查"，而是 CI/governance 主动抓

# 案例分析：Governance 自身的三层嵌套盲区 — 观察者的自我盲区

> 日期：2026-04-11 | 触发事件：Mac Mini full-mode 审计汇总行说"✅ 所有不变式成立"，正文里却藏着 3 个 💥 崩溃

---

## 事件

2026-04-11 凌晨 Mac Mini 例行审计，`python3 ontology/governance_checker.py --full` 输出：

```
  不变式: 22 | 检查: 41 执行, 2 跳过
  通过: 38/41 checks | 元规则: 6/6

  ✅ 所有不变式成立
```

看起来绿灯。但通过翻阅正文注意到三个 💥 图标：INV-CRON-003/004 的 python_assert 抛出 `NameError`，检查执行出错。汇总行的"✅"和正文里的"💥"同时存在——**汇总在说谎**。

深挖后发现这是一条**三层嵌套的盲区链**：每一层 bug 都被下一层 bug 掩盖，最外层还被 governance 自身的"silent error"掩盖。这是 governance 系统第一次在**自己身上**暴露 MR-4（silent-failure-is-a-bug）。

## 表面现象 vs 真实根因

| 层级 | 表面判断 | 深挖后的真实根因 |
|------|----------|-----------------|
| 第一眼 | "INV-CRON-004 误报 kb_dream x3 重复" | 子串匹配对 prefix-subset 条目失效 |
| 第二层 | "修了子串匹配，换成 helper 函数应该行了" | exec() 作用域陷阱：生成器表达式看不到 exec 本地 helper，`NameError` 每次都抛 |
| 第三层 | "那为什么 full 审计没发现？汇总说所有不变式都成立" | `failed_invs` 仅统计 `status=="fail"`，`status=="error"` 被 ✅ 汇总行完全吞掉 |
| 元层 | "为什么我们没有早发现" | 观察者没有观察自己的观察链路 |

## 完整因果链

```
HH:MM  [registry] kb_dream.sh 拆成 3 条 crontab 条目
       ├─ kb_dream.sh                   # Reduce job
       ├─ kb_dream.sh --map-sources     # Map Sources
       └─ kb_dream.sh --map-notes       # Map Notes
       │
       ▼
[Bug 1 触发器] INV-CRON-003/004 子串匹配 false-positive
       │  YAML check: `if sname in l` / `if script in line`
       │  "kb_dream.sh" 是 "kb_dream.sh --map-sources" 的 prefix
       │  → Reduce 条目被算成 3 次 → INV-CRON-004 报"重复 x3"
       │  → 违反了它自己的声明（"同脚本不同参数是合法的拆分调度"）
       │
       ▼
[Bug 1 修复] 用 helper `_cron_cmd_invokes` 做 endswith + 词边界判定
       │  def _cron_cmd_invokes(line, entry):
       │      ...词边界检查...
       │  count = sum(1 for l in lines if _cron_cmd_invokes(l, entry))  ← 陷阱!
       │
       ▼
[Bug 2 放大器] Python exec() 作用域陷阱
       │  governance_checker._exec_python_assert() 用 exec(compile(code, ...))
       │  code 里的 `def _cron_cmd_invokes` 存在于 exec 的**局部作用域**
       │  `sum(1 for l in lines if _cron_cmd_invokes(l, entry))` 是生成器表达式
       │  生成器表达式创建**自己的新作用域**
       │  新作用域只能看到：
       │    ├─ 它的封闭函数局部变量（没有，因为 exec 不是函数）
       │    └─ 模块全局变量（helper 不在全局）
       │  → NameError: name '_cron_cmd_invokes' is not defined
       │  → 检查状态 = "error"，每次运行都炸
       │
       ▼
[Bug 3 掩护者] governance_checker.print_results() silent error bug
       │  ```python
       │  failed_invs = 0
       │  for r in results:
       │      ...
       │      if r["status"] == "fail":    # ← 只数 fail，不数 error
       │          failed_invs += 1
       │
       │  if failed_invs:
       │      print(f"❌ {failed_invs} 个不变式被违反")
       │  else:
       │      print(f"✅ 所有不变式成立")  # ← error 存在时仍然打印这行!
       │  ```
       │  正文里有 💥 图标（check 层 status 标记正确）
       │  但汇总只看 `failed_invs`，它只统计 "fail"
       │  → error 状态在汇总聚合中被**透明化**
       │
       ▼
[元层 掩护者] 治理系统没有观察自己
       │  MR-4 (silent-failure-is-a-bug) 是治理对**被治理系统**的要求
       │  但治理系统**自己**的执行失败从未被治理自己检查
       │  "谁来治理治理者？" ← 盲区
       │
       ▼
[用户发现] 人眼翻正文才看到 3 个 💥；汇总"✅"原本是要信任的
```

## 三层根因总结

| 层级 | 问题 | 发现 |
|------|------|------|
| **触发器** | crontab 同脚本拆分调度（Dream Map-Reduce V37.2） | Bug 1 隐藏的条件依赖：需要 prefix-subset 条目才能触发 |
| **放大器** | Python exec() + 生成器表达式双重作用域 | Bug 2 是 Python 语义级陷阱，不是代码错误 |
| **掩护者** | governance_checker 汇总聚合只覆盖 `fail` 不覆盖 `error` | Bug 3 是治理系统对自己的监控空洞 |
| **元掩护者** | 治理系统没有把自己当被治理对象 | "观察者的自我盲区"：MR-4 没有被递归应用到治理代码 |

## 时间线还原

| 时间 | 事件 | 影响 |
|------|------|------|
| 2026-04-10 | Dream V37.2 上线 Map-Reduce 分离调度，crontab 出现 3 条 kb_dream.sh 条目 | 构造了 prefix-subset 条件 |
| 2026-04-11 01:00 | Mac Mini 例行 governance `--full` 审计 | Bug 1 首次命中：INV-CRON-004 误报 kb_dream x3 |
| 2026-04-11 01:27 | 修 Bug 1：`_cron_cmd_invokes` helper + endswith/词边界（commit 2937198） | 但立刻踩中 Bug 2 |
| 2026-04-11 01:40 | 重跑审计，governance 汇总行仍然是 "✅ 所有不变式成立" | Bug 3 掩盖 Bug 2 — **完全没看到 INV-CRON-004 崩溃** |
| 2026-04-11 01:42 | 偶然翻正文才发现 3 个 💥 图标 | Bug 2 + Bug 3 同时暴露 |
| 2026-04-11 01:42 | 修 Bug 2（for-loop 替代生成器）+ Bug 3（`failed_invs` 区分 fail/error）（commit bf454e1） | 27 suites / 747 tests / 0 fail |
| 2026-04-11 01:55 | Mac Mini 验证：40/41 + 💥 → **41/41 ✅** | 真实状态首次被准确报告 |

## 为什么以前没发生

| 条件 | 2026-04-10 之前 | 2026-04-10 之后 | 2026-04-11 |
|------|-----------------|----------------|-----------|
| crontab prefix-subset 条目 | ❌（只有单条 kb_dream.sh） | ✅（Map-Reduce 拆分） | ✅ |
| `_cron_cmd_invokes` helper | ❌（原始是简单 `in` 匹配） | ❌ | ✅（修 Bug 1 引入） |
| `failed_invs` 仅数 fail | ✅（一直如此） | ✅ | ✅ |
| 三者同时出现 | — | — | ✅ → 全链暴露 |

**三条件组合**才会让这个 bug 链完整出现：
- 只出现条件 1（prefix-subset）：会触发 Bug 1（误报 x3），但汇总会显示 "❌ 1 个不变式被违反"，能看见。
- 只出现条件 2（helper 存在）：没有 prefix-subset 时 Bug 1 不触发，helper 不会被调用，Bug 2 不暴露。
- 只出现条件 3（silent error）：没有执行出错的 check，Bug 3 没有暴露的对象。

**Bug 3 是结构性的永久漏洞**，但需要前两个 bug 一起出现才能让它的后果被人类看到。它在仓库里默默存在了多久？**从 governance_checker v1 开始就存在**（至少数月）。

## 修复（三层对应三因，按"剥洋葱"顺序）

| 顺序 | 层级 | 修复 | 原理 | Commit |
|------|------|------|------|--------|
| 1 | **触发器** | 子串匹配 → `endswith(entry)` + 词边界 `cmd[idx-1] in "/ \t\"'"` | prefix-subset 条目必须由路径分隔符/空白/引号隔开 | 2937198 |
| 2 | **放大器** | 生成器表达式 → 普通 `for` 循环 | 避免创建新作用域，for-loop 共享封闭作用域可以看到 exec 局部 helper | bf454e1 |
| 3 | **掩护者** | `failed_invs` 计入 `status in ("fail", "error")`；汇总行区分"❌ N 违反, 💥 M 出错" | 任何非 pass 状态都不能被透明化；汇总必须覆盖 check 层可能的全部状态 | bf454e1 |
| 4 | **本次** | 补 MR-7 元规则 + INV-GOV-001 不变式 + 对 print_results 的回归测试 | 结构化地防止"治理自身 silent error" 再次发生 | 本 session |

## 从本体论视角的启示

### 1. 观察者的自我盲区（Observer Self-Blindness）

治理系统建立起来是为了回答"什么东西坏了我们发现不了？"。但我们一直忽略了一个问题：**如果治理系统自己坏了呢？**

MR-4（silent-failure-is-a-bug）是对**被治理**系统的约束。Bug 3 揭示了一个结构性漏洞：**这条规则从未被递归应用到治理代码本身**。governance_checker 本身是运行时代码，有它自己的异常路径、自己的聚合逻辑、自己的"静默失败"模式，和它要治理的其他代码没有本质区别。

> **元原则**：治理系统是一等的运行时代码。它必须遵守它自己强制执行的所有规则，包括 MR-4。

### 2. 检查层级与聚合层级的断裂

本案例暴露了一个**数据模型级缺陷**：

- 在 check 层：status 可以是 `pass / fail / skip / error`（4 态）
- 在 invariant 层：`r["status"]` 在 `_run_invariant` 里被设成 `"fail"` 或 `"error"`（基于 checks 的状态）
- 在 summary 聚合：`failed_invs` 用的判断是 `== "fail"`（2 态）

**4 态的底层状态被 2 态的聚合过滤掉**。这不是代码 bug 而是**数据模型 bug**——类型系统的表达力与聚合逻辑不匹配。

**本体论映射**：
- 概念：`CheckStatus ∈ {pass, fail, skip, error}`
- 谓词：`is_not_passing(status) := status ∉ {pass, skip}`
- 不变式：所有 summary 级别的"失败"计数必须基于 `is_not_passing` 而非硬编码 `== "fail"`

### 3. 剥洋葱式 Bug：每层掩护下一层

| 层 | 掩盖的是什么 |
|---|-------------|
| Bug 3（silent error）| 掩盖 Bug 2（exec scope trap） |
| Bug 2（exec 陷阱） | 掩盖 Bug 1 修复是否正确 |
| Bug 1（子串匹配） | 掩盖了数月没有 prefix-subset 条目这个"运气" |

这种叠层掩护是**治理体系最危险的失败模式**：一个修复动作可能只是把 bug 从一层推到下一层。本案例的 Bug 1 修复引入的 helper 是 bug-free 的，但放进去的地方（exec 环境）+ 写法（生成器）组合出了 Bug 2。**修复的 commit 本身是干净的**，但它在一个隐藏着 Bug 3 的环境里运行，导致 Bug 2 完全不可见。

**本体论启示**：修复动作不是原子的。一个修复 = 原 bug 消失 + 新代码进入新环境 + 新环境可能有新的交互。治理体系应该对"刚落地的修复"做**独立验证**，而不是信任汇总行。

### 4. 原则 #6（Mac Mini E2E 验证）的活教材

> "dev 环境单测通过不算完成；必须在 Mac Mini 运行 preflight 确认端到端有效果"

本案例三个 bug 都是 Mac Mini full-mode 审计才暴露的：
- Bug 1 依赖真实 crontab（dev 环境没有 crontab 数据）
- Bug 2 需要 INV-CRON-004 的 check 真的被执行（dev 环境 `requires_full=true` 跳过）
- Bug 3 只在有 error 状态的 check 存在时才能被人类看到

**dev 全过 ≠ 生产正确**。治理系统的 dev 模式有 20 个不变式通过，full 模式才暴露出它自己的 silent error。

### 5. 原则 #26（异常必须深挖爆炸链）的首次治理层实践

本案例是第一次把原则 #26 应用到**治理系统自身**，而不是业务系统。以往的案例（dream_quota_blast_radius, pa_echo_chamber）都是业务层故障。现在我们有了一个"治理自己的 bug 链"案例，这标志着治理体系进入了**自省阶段**——它开始能够分析自己的失败模式。

## Governance 不变式 + 元规则提案

### 新元规则 MR-7

```yaml
- id: MR-7
  name: governance-execution-is-self-observable
  description: |
    治理系统自身是一等运行时代码，必须遵守它强制执行的所有规则，包括 MR-4。
    任何 check 层可能出现的状态（pass/fail/skip/error）都必须在汇总层有明确的
    观察路径——不允许在聚合时被"透明化"。治理的汇总行必须不会说谎。
  lesson: |
    2026-04-11: governance_checker.print_results() 仅统计 status=="fail"，
    status=="error" 被 ✅ 汇总行完全吞掉。INV-CRON-003/004 连续数次 NameError
    崩溃，汇总行持续显示"所有不变式成立"。这是"观察者的自我盲区"：治理系统
    没有把自己当被治理对象。
```

### 新不变式 INV-GOV-001

```yaml
- id: INV-GOV-001
  name: governance-summary-counts-all-non-pass
  meta_rule: MR-7
  verification_layer: [declaration, runtime]
  severity: critical
  declaration: "governance_checker 汇总层必须区分并统计 fail 和 error，两者都不能被 ✅ 汇总行吞掉"
  checks:
    - name: "print_results 的 failed_invs 同时统计 fail 和 error"
      check_type: file_contains
      file: ontology/governance_checker.py
      pattern: 'r\["status"\] in \("fail", "error"\)'
    - name: "error 状态被注入后汇总行不会说 '所有不变式成立'"
      check_type: python_assert
      code: |
        # 构造一个注定 error 的虚假 result，验证汇总不会说谎
        from ontology.governance_checker import print_results
        import io, contextlib
        fake = [{
            "id": "INV-TEST", "name": "silent-error-regression",
            "status": "error", "severity": "critical", "meta_rule": "MR-7",
            "declaration": "注入的 error result", "checks": [
                {"name": "synthetic", "status": "error", "message": "NameError: fake"}
            ],
        }]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = print_results(fake)
        out = buf.getvalue()
        assert "所有不变式成立" not in out, "error 状态被 ✅ 汇总吞掉"
        assert exit_code != 0, f"error 状态未计入失败: exit={exit_code}"
```

---

## 写给未来的自己

当治理系统自己坏了的时候，**汇总行是最不可信的地方**。以后看 governance 输出，不要只看最后一行 ✅，**要翻正文数 💥**。

更深的教训：不要信任你没测过的聚合逻辑，特别是**当聚合逻辑没有独立的回归测试时**。Bug 3 在仓库里至少存在了数月，它的检测时间窗口是"恰好有 check 陷入 error 状态且有人翻了正文"——这不是检测，这是运气。

**本案例是原则 #26（异常必须深挖爆炸链）在治理层自身的首次实践。每次治理体系自己失败，不是修完就结束，而是把治理体系的自省能力再往前推一步的材料。**

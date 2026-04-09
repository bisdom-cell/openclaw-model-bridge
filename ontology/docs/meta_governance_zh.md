# 当治理系统开始审计自己：一个元规则自动发现机制的工程实践

> 692 个测试全绿、安全评分 93/100、四层验证体系——然后 WhatsApp 推送静默失败了三天，没有任何一层检查发现。

---

## 事故

2026 年 4 月 8 日。我们的 AI Agent 系统有 692 个单元测试（全部通过）、17 条治理不变式（全部达标）、安全评分 93 分。系统看起来很健康。

然后用户说："我三天没收到 DBLP 论文推送了。"

排查发现：三个定时任务（DBLP 论文监控、Agent Dream 引擎、Job Watchdog）的 crontab 条目缺少 `bash -lc` 前缀。没有这个前缀，cron 环境下环境变量不会加载，`OPENCLAW_PHONE` 变量拿到的是占位号 `+85200000000` 而不是真实号码——所有 WhatsApp 推送静默失败，零错误日志。

**这不是第一次了。** 一个月前的 4 月 7 日，我们发现了 22 处"声明与现实"断裂：文档说工具数 ≤ 12，实际每次发 18 个；注册表说 ArXiv 08:00/20:00 运行，crontab 还是旧的每 3 小时；`MAX_TOOLS = 12` 定义了但从未被代码 import。

两次事故有一个共同模式：**所有检查层都在回答同一个问题——"现有规则是否被遵守？"但从来没人问："是否有该存在但不存在的规则？"**

## 传统治理的盲区

大多数治理系统的架构是这样的：

```
定义规则 → 编写检查 → 执行检查 → 报告结果
```

这个流程有一个根本性的假设：**规则是完备的**。如果你定义了 17 条不变式，系统就检查这 17 条。第 18 条？不存在。

问题是：谁来检查规则本身的完备性？

传统答案是人工 code review。但人工审查有天然的认知盲区——你不知道自己不知道什么。我们的 17 条不变式覆盖了工具治理、调度治理、通知治理、环境变量、健康检查、部署安全六个域——听起来很全面，直到你发现系统有 31 个定时任务，但只有 5 个被不变式覆盖。

**治理体系最危险的漏洞不是某个检查没写好，而是某个维度从未被纳入检查。**

## 解法：让治理系统审计自己

我们的做法是在治理体系中加入一层"元治理"——不检查业务规则是否被遵守，而检查**治理规则本身是否完备**。

架构变成了三层：

```
┌─────────────────────────────────────────┐
│ 元规则层（Meta-Rules）                    │
│ "治理规则是否完备？是否有盲区？"            │
│                                          │
│ MR-1: 每个声明必须有执行代码               │
│ MR-2: 每个执行代码必须有测试验证            │
│ MR-3: 声明变更必须传播到所有执行层          │
│ MR-4: 静默失败是 bug                      │
│ MR-5: 健康字段必须有新鲜度保证             │
│ MR-6: critical 不变式必须有 ≥2 层验证深度  │
└──────────────────┬──────────────────────┘
                   │ 约束
┌──────────────────▼──────────────────────┐
│ 不变式层（Invariants）                    │
│ "业务规则是否被遵守？"                     │
│                                          │
│ 17 条不变式 × 36 个可执行检查              │
│ 覆盖：工具/调度/通知/环境/健康/部署         │
└──────────────────┬──────────────────────┘
                   │ 执行
┌──────────────────▼──────────────────────┐
│ 运行时（Runtime）                         │
│ 实际的代码、配置、crontab、环境变量         │
└─────────────────────────────────────────┘
```

但光有 6 条元规则还不够。元规则是**原则**——"每个声明必须有执行代码"很好，但具体哪些声明没有执行代码？你还是需要人去逐一检查。

关键创新在下一步。

## Phase 0：元规则的自动发现引擎

我们为每条元规则实现了**自动发现程序**——不是等人来检查，而是让系统自动扫描结构化数据源，找出违反元规则的实例。

```
┌─────────────────────────────────────────────────────────┐
│ MRD-CRON-001: "每个 enabled job 应有治理覆盖"            │
│                                                         │
│ 数据源: jobs_registry.yaml (31 个注册任务)                │
│ 扫描: enabled=true && scheduler=system 的每个 job        │
│ 比对: 该 job 的脚本名是否出现在任何不变式的检查代码中       │
│                                                         │
│ 发现: 26 个 job 没有被任何不变式覆盖                      │
│       → health_check, arxiv_monitor, hf_papers, ...     │
│       → 建议为每个新增不变式                              │
└─────────────────────────────────────────────────────────┘
```

6 个自动发现规则，各扫描不同的数据源：

| 发现规则 | 对应元规则 | 扫描什么 | 发现了什么 |
|---------|----------|---------|----------|
| **MRD-CRON-001** | MR-3 | jobs_registry.yaml | 26 个 enabled job 没有治理覆盖 |
| **MRD-ENV-001** | MR-1 | jobs_registry.yaml + preflight | needs_api_key 字段是否被代码消费 |
| **MRD-NOTIFY-001** | MR-4 | notify.sh + 所有 .sh 文件 | 4 个 topic 是否都有路由映射 |
| **MRD-ERROR-001** | MR-4 | 所有 .sh 文件 | **51 处推送调用静默吞掉了错误** |
| **MRD-NOTIFY-002** | MR-4 | 7 天日志 + 推送队列 | 6 个 Discord 频道最近 7 天零推送 |
| **MRD-LAYER-001** | MR-6 | governance_ontology.yaml | 5 个 critical 不变式只有单层验证 |

MRD-ERROR-001 是最典型的例子。传统方式下，你需要人工 grep 每个脚本的错误处理。自动发现规则直接扫描所有 `.sh` 文件中的 `message send.*>/dev/null 2>&1` 模式——找到 51 处。这 51 处中的每一处都意味着：推送失败时，没有任何错误日志，问题完全不可观测。

## 三层验证深度模型

元规则 MR-6 引出了另一个发现：检查本身也有深浅之分。

```
Layer 1 — 声明层：代码/配置中有没有这个东西？
           → file_contains, python_assert
           → 能发现：代码缺失、配置不一致
           → 盲区：代码正确但从未执行

Layer 2 — 运行时层：执行环境中这个东西是否生效？
           → env_var_exists, command_succeeds
           → 能发现：环境变量缺失、cron 路径错误
           → 盲区：执行正确但结果错误

Layer 3 — 效果层：这个东西达到了预期目的吗？
           → log_activity_check
           → 能发现：端到端失败（组件都 OK 但系统不工作）
           → 盲区：需要外部反馈（用户确认收到消息）
```

**真实案例的时间线**：

| 日期 | 发现 | 教训 |
|------|------|------|
| 4 月 7 日 | 声明层 17/17 通过，但 22 处断裂存在 | 声明层给出虚假安全感 |
| 4 月 8 日 | `bash -lc` 缺失导致推送失败 3 天 | 运行时层发现了声明层的盲区 |
| 4 月 9 日 | Ontology Discord 频道配置完整，但从未收到消息 | 效果层发现了运行时层的盲区 |

MRD-LAYER-001 自动发现了 5 个 critical 级别的不变式只有单层验证。这意味着这 5 个最重要的检查，恰恰最容易给出虚假安全感——它们在声明层说"通过"，但运行时可能完全不是那回事。

## 自反性：治理的治理

这个机制最有意思的特性是**自反性**——它可以审计自己。

MRD-LAYER-001 检查的是"critical 不变式是否有足够的验证深度"。如果我们新增了一个 critical 不变式但只写了声明层检查，MRD-LAYER-001 会在下次运行时自动发现这个新增的盲区——不需要任何人记得去检查。

```
新增不变式 INV-XXX-001 (severity: critical, verification_layer: [declaration])
    ↓
下次 governance_checker.py 运行
    ↓
MRD-LAYER-001 自动扫描所有 critical 不变式
    ↓
发现 INV-XXX-001 只有 1 层验证（< 2 层要求）
    ↓
输出警告："INV-XXX-001 需要增加运行时或效果层验证"
```

这形成了一个**自我改进的闭环**：治理体系的每一次扩展，都会被元规则自动审计是否扩展得足够深。

## 工程实现

整个机制用 YAML 声明 + Python 执行引擎实现，核心代码不到 700 行。

**声明层**（`governance_ontology.yaml`，639 行）：

```yaml
meta_rules:
  - id: MR-6
    name: critical-invariants-need-depth
    principle: "severity=critical 的不变式必须有 ≥2 层验证深度"
    lesson: "2026-04-08: 声明层 12/12 通过但推送失败 3 天"

meta_rule_discovery:
  - id: MRD-LAYER-001
    meta_rule: MR-6
    name: "severity=critical 的不变式应有 ≥2 层验证深度"
    check_type: python_assert
    code: |
      shallow = []
      for inv in data['invariants']:
          if inv.get('severity') == 'critical':
              layers = inv.get('verification_layer', [])
              if len(layers) < 2:
                  shallow.append(f"{inv['id']} ({', '.join(layers)})")
      # 输出警告而非失败（避免静态分析误报）
      result = shallow  # 空列表 = 通过
```

**执行引擎**（`governance_checker.py`，614 行）：

```python
def run_meta_discovery(data):
    """Phase 0: 扫描结构化数据源，发现未被不变式覆盖的维度"""
    
    # 收集所有不变式已覆盖的关键词
    all_check_code = _collect_invariant_coverage(data)
    
    # 对每个 MRD 规则，扫描外部数据源
    for mrd in data.get('meta_rule_discovery', []):
        if mrd['id'] == 'MRD-CRON-001':
            result = _discover_uncovered_jobs(all_check_code)
        elif mrd['id'] == 'MRD-ERROR-001':
            result = _discover_silent_error_suppression()
        elif mrd['id'] == 'MRD-LAYER-001':
            result = _discover_shallow_critical(data)
        # ...
```

**运行方式**：

```bash
# 开发环境（声明层检查）
python3 ontology/governance_checker.py

# 生产环境（含运行时+效果层，每日 07:00 自动执行）
python3 ontology/governance_checker.py --full
```

**输出示例**：

```
✅ 17 invariants, 35/35 checks pass

⚠️ [MRD-CRON-001] 26 个 enabled job 未被不变式覆盖
⚠️ [MRD-ERROR-001] 51 处推送调用静默吞错误
⚠️ [MRD-LAYER-001] 5 个 critical 不变式仅有单层验证
```

## 思考

做了这个机制之后，我的认知发生了一个转变：

**治理的核心问题不是"规则是否被遵守"，而是"规则是否覆盖了应该覆盖的维度"。**

传统的合规检查像考试——老师出 100 道题，学生答对 98 道，得 98 分。但如果考试本身只覆盖了 60% 的知识点呢？98/100 的分数掩盖了 40% 的盲区。

元规则机制做的事情是：**出一份审计考试覆盖率的元考试**。它不替代考试本身，而是确保考试不会遗漏关键知识点。

对于 AI Agent 系统，这个问题尤其严重。Agent 的工具调用、模型路由、定时任务、推送通知——每一个都是一个可能静默失败的点。传统的测试覆盖率（行覆盖、分支覆盖）回答的是"代码被测到了吗"，但不回答"该有的治理规则存在吗"。

692 个测试全绿不代表系统健康。它只代表**你检查了的部分**是健康的。

---

## 关键数据

| 指标 | 数值 |
|------|------|
| 元规则 | 6 条（MR-1 ~ MR-6） |
| 治理不变式 | 17 条 |
| 可执行检查 | 36 个 |
| 自动发现规则 | 6 个（MRD-*） |
| 自动发现的盲区 | 26 个未覆盖 job + 51 处静默错误 + 5 个浅层 critical |
| 验证层 | 3 层（声明/运行时/效果） |
| 核心代码 | ~1,250 行（YAML 639 + Python 614） |
| 检查类型 | 6 种（python_assert / file_contains / file_not_contains / env_var_exists / command_succeeds / log_activity_check） |

## 项目

这个机制是 [openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge) 的 ontology 子项目的一部分——一个将大模型接入 WhatsApp AI 助手框架的中间件系统。完整的治理体系代码在 `ontology/` 目录下，欢迎审阅。

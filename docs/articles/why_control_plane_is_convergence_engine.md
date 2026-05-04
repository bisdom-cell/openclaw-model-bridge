# Why Your Control Plane Is a Convergence Engine, Not a Policy Engine

> 2026-05-04 | Stage 2 立场资产 | V37.9.24 | OpenClaw Runtime Control Plane

## TL;DR

我用 11 天在生产 Agent Runtime 上做了一件大多数控制平面框架没做的事：**让声明态自动同步到运行时态**。

```
              声明态                          运行时态
       (jobs_registry.yaml)              (macOS crontab -l)
              │                                   │
              │     ──[ verify_convergence ]──   │
              │                                   │
              └──[ machine_sync_via_helper ]─────┘
                  (V37.9.24 Plan B dry-run)
```

11 天前这条同步链是"靠 Claude Code 记得 commit 后跑一行 `crontab_safe.sh add`"。
今天这条链是 framework 在每次 governance audit cron 自动检测漂移、自动拼出
36 行 cron line、自动通过 `crontab_safe.sh add` 同步进 crontab。

**记忆是最弱的可靠性原语**。这篇文章解释为什么"声明 → 决策"的策略引擎不够，
为什么 control plane 必须升级为 **convergence engine**，以及 OpenClaw 用 6 个版本
（V37.9.19 → V37.9.24）走完这条路径的工程证据。

如果你在搭 Agent Runtime / 内部平台 / 工具治理系统，这篇会节省你几个月的迭代。

---

## 第一个错觉：Control Plane = Policy Engine

主流的"控制平面"叙事大体是：

> 声明你的策略 → 系统在请求时评估 → 允许或拒绝。

OPA (Open Policy Agent) / Cedar / Casbin / Kyverno 都是这个范式。Kubernetes
admission controller 也是。它们解决的问题是：

```
input (request) ──[policy]──→ decision (allow / deny / mutate)
```

很优雅。**但它们不解决一件事**：你声明的状态和系统真实的运行时状态**不一致时怎么办**。

举例：你声明 36 个 cron job，每个有 entry / interval / log。但 macOS crontab 真实
状态可能漏一个、多一个、或漂移到错误的间隔。OPA 帮你做的是"判断当前状态合不合规"，
但**判断完之后，谁去同步**？答案永远是某个人记得跑某条命令。

```
        OPA 风格                    我们 V37.9.18 之前
     ──────────────             ──────────────────────
     声明 → 评估 → 决定         声明 → 评估 → 告警 → 等人
                                                     ↑
                                             记忆 = 最弱的可靠性原语
```

## 第二个错觉：写更多 audit 规则就能让系统稳定

我之前一篇文章 *[Audit is Regression, Not Prevention](audit_is_regression_not_prevention.md)*
量化过：在 45 天内 53 个治理不变式 + 15 条元规则的环境下，audit 对**未知维度**的
预防率是 **0%**。

数字残酷但意义清晰：**audit 不能让没发生过的故障不发生，只能让发生过的故障不再发生**。

V37.9.18 给我演示了这个原则的硬实践版本：

> kb_deep_dive job 在 V37.9.16 上线，jobs_registry 声明 enabled=true，**但没人手动
> 跑 `crontab_safe.sh add`**。两次预期 22:30 触发完全静默，48 小时后用户察觉浮现。

我做完根因分析后立了 **MR-17**：

> **declared-state-must-converge-to-runtime-via-machine-not-memory**
>
> 任何声明态资源（yaml/registry/config）都必须有对应的运行时态（cron/process/http/filesystem）。
> 漂移检测从"靠人记得 commit 后跑命令"升级为"机器周期性自动检测+同步"。

这条规则改写了控制平面的边界：control plane 不再只是 policy engine，**它必须包含
convergence engine** —— declared → runtime 的实际同步机制，而不只是评估机制。

## 三个工程证据：Convergence Framework 11 天演进

V37.9.19 → V37.9.24 累积 6 个版本，每个版本只做一件事：

### V37.9.19 — Framework 起步 + 第一个 spec

`ontology/convergence.py` 引入 `ConvergenceResult` namedtuple + `verify_convergence(spec_id)`
顶层 API + named-dispatch 三表（extractors / observers / parsers）。Decoupled from
ONTOLOGY_MODE: convergence 是 governance-layer observability，不是 request-path enforcement。

第一个 spec：`jobs_to_crontab`（drift_action: alert_only — 高 blast-radius 谨慎起步）。

```yaml
- id: jobs_to_crontab
  declaration:
    source: jobs_registry.yaml
    extractor: registry_enabled_system_jobs
  runtime_observable:
    method: shell_command
    command: "crontab -l"
    parser: line_contains_identifier
  drift_action: alert_only   # V37.9.19 — 一周观察期内只告警不同步
```

### V37.9.20 — 扩展性证据 (named-dispatch first proof)

新加 `providers_to_adapter` spec — `providers.py ProviderRegistry.list_names()` vs
adapter `:5001/health` 的 `fallback_chain`。**核心 framework 改动 = 0 行**，全部走
named-dispatch 三表新增条目：

```python
_DECLARED_EXTRACTORS["providers_from_registry"] = _extract_providers_from_registry
_RUNTIME_OBSERVERS["http_endpoint"] = _observe_http_endpoint
_IDENTIFIER_PARSERS["json_set_union"] = _parse_json_set_union
```

证明 framework 的"添加新 spec 类型零 framework 改动"承诺不是空头支票。

### V37.9.22 — 跨扩展粒度 + 集成入主 audit

第三 spec `openclaw_config_to_runtime`（mid-extension 路径：抽出
`_walk_json_paths_to_set` 共用 helper）+ 第四 spec `kb_sources_to_index`（minimal
extension：只新加 1 个 extractor，复用 V37.9.19 observer + parser）。

最后一步：把 framework **真集成进 governance audit 主流程**：

```python
# governance_checker.py main flow
results = run_invariants()
discovery = run_meta_discovery()
convergence = run_convergence_specs()   # ← V37.9.22 加入
```

framework 从"被 INV runtime 间接调用"升级为"每次 audit cron 主动消费"。

### V37.9.23 — Plan B 渐进 dry-run + 真同步路径

5/3 决策窗口到达（V37.9.19 baseline + 7d 观察）。一周生产数据：`declared=36
observed=36` 零漂移零误报。**升级 jobs_to_crontab drift_action: alert_only → machine_sync**。

引入 `_format_cron_line(job)` 纯函数（拼出 V37.9.18 INV-CRON-003 模式 cron line +
拒绝 shell metacharacter defense-in-depth）+ `_apply_machine_sync(spec, missing, dry_run)`
orchestrator（调 `crontab_safe.sh add` 真同步）+ `_is_dry_run()` env reader。

```yaml
drift_action: machine_sync             # V37.9.23 escalation
convergence_method:
  implemented: machine_sync_via_helper  # 替代 V37.9.19 planned
  helper: "bash $HOME/crontab_safe.sh add '<line>'"
  dry_run_env_var: CONVERGENCE_DRY_RUN
  dry_run_default: true                  # 安全网: V37.9.24+ 一周后切关
```

**Plan B 渐进 dry-run** 的关键：drift_action 升级 + 默认 dry-run env 控制。
operator 看 governance audit 的 `apply[dry-run]=36` 字面量验证 cron line 拼接正确，
确信后 V37.9.24+ 切关 env 真激活。这是 V37.9.13 P2 context evaluator 的 "shadow → on"
模式在 convergence 层的兑现。

### V37.9.24 — Named-dispatch for apply functions + 第二个 machine_sync spec

观察到 `kb_sources_to_index` spec 的 apply 模式与 jobs_to_crontab 不同：

| 维度 | jobs_to_crontab | kb_sources_to_index |
|------|---|---|
| Helper | crontab_safe.sh | kb_embed.py |
| Pattern | per-entry call | one-shot incremental |
| 启动开销 | <100ms | ~3s (load embedding model) |
| 输入 | 单条 cron line | 整个 KB (mtime diff) |

如果让 V37.9.23 的 `_apply_machine_sync` 同时支持两种模式 = if-else 分派 +
spec_id-hardcoded。违反 V37.9.20 named-dispatch 设计原则。

V37.9.24 把 `_apply_machine_sync` 重构为顶层 dispatcher，按 spec yaml 的
`convergence_method.apply_function` 字段路由：

```python
_APPLY_FUNCTIONS = {
    "jobs_to_crontab_per_entry": _apply_jobs_to_crontab_per_entry,
    "kb_embed_incremental": _apply_kb_embed_incremental,
}

def _apply_machine_sync(spec, missing_entries, dry_run=None):
    method = spec.get("convergence_method") or {}
    fn_name = method.get("apply_function") or ""
    fn = _APPLY_FUNCTIONS.get(fn_name)
    return fn(spec, missing_entries, dry_run)
```

新增 `kb_sources_to_index` 升级 machine_sync 只需：
1. 实现 `_apply_kb_embed_incremental`（one-shot single subprocess call）
2. 注册到 `_APPLY_FUNCTIONS` dict
3. spec yaml 加 `apply_function: kb_embed_incremental`

`_apply_machine_sync` 顶层 dispatcher **零改动**。

## 实证：governance audit 输出

在生产 Mac Mini 跑 `python3 ontology/governance_checker.py` 后看 convergence 段：

```
──────────────────────────────────────────────────────────────────────
  CONVERGENCE FRAMEWORK (Phase 4 Layer 5) — 4 spec(s)
──────────────────────────────────────────────────────────────────────
  ✅ [jobs_to_crontab] — declared=36 observed=36 (no drift)
  ⚠️  [providers_to_adapter] — declared=7 observed=2 missing=5 (drift_action=alert_only)
  ⚠️  [openclaw_config_to_runtime] — declared=1 observed=1 (no drift)
  ⚠️  [kb_sources_to_index] — declared=14 observed=11 missing=3 (drift_action=machine_sync) apply[dry-run]=1 apply_errors=0
```

四个 spec 三种 drift_action：
- `jobs_to_crontab` (machine_sync 真同步) — zero drift, no apply needed
- `kb_sources_to_index` (machine_sync 真同步) — 3 missing, 1 行 dry-run
  one-shot summary
- `providers_to_adapter` (alert_only_permanent) — 5 provider 缺 API key,
  framework 不能 magically provision keys，operator decision
- `openclaw_config_to_runtime` (alert_only_permanent) — Gateway runtime
  state changes are intentional operator actions

**framework 知道每个 spec 的 apply 路径不同 → 用 named-dispatch 路由 → 输出可观测 log**。

## 第三个洞察：drift_action 是 4 档不是 1 档

主流策略引擎只有"allow / deny"或"warn"两档行为。OpenClaw convergence framework
明确把 drift_action 拆成 4 档：

| drift_action | 含义 | 典型 spec |
|---|---|---|
| `alert_only` | 仅产 alert，operator 决定怎么修 | (起步谨慎模式) |
| `alert_only_permanent` | 结构性决策 — framework 永远不能 magically 修 | API keys / Gateway state |
| `machine_sync` | framework 自动同步 declared → runtime | jobs_to_crontab / kb_sources_to_index |
| `block_until_human` | 漂移阻塞后续审计直到人工确认 | 安全敏感 spec |

每档对应不同的工程承诺。看到一个 spec 标 `alert_only_permanent`，operator 知道"我
不要等 framework 帮我修，这是我永久要看的 dashboard 信号"；看到 `machine_sync` +
`dry_run_default: true`，operator 知道"一周后我应该把 dry-run 切关，不然 framework
不会真做事"。

**drift_action 的存在让 declared → runtime 同步从一个**二元决策**变成一个**渐进光谱****。

## 这跟 OPA / Kyverno 的差异在哪里

| 维度 | OPA / Kyverno | OpenClaw Convergence Framework |
|---|---|---|
| 主语 | "请求是否合规" | "声明是否在 runtime 真实存在" |
| 输入 | request body | declared spec + runtime observation |
| 输出 | allow/deny/mutate | 4 档 drift_action 信号 + 自动同步 |
| 部署 | sidecar / admission webhook | governance audit cron + helper subprocess |
| 风险 | reject 错误请求 | 错误同步可破坏 runtime state |
| 安全网 | rule simulation / shadow mode | drift_action 4 档 + dry-run env (Plan B 渐进) |

**OPA 是请求路径的把关人，Convergence Framework 是声明态的同步引擎。** 二者不互
替，是控制平面的两个互补支柱。一个完整的 control plane 应该两者都有。

## V3 路标：`pip install ontology-engine`

V37.9.19 → V37.9.24 在 OpenClaw 内部跑通了。下一步是把这套从"治理这个项目的代码"
升级为"对外输出的通用 framework"：

```python
# pip install ontology-engine
from ontology_engine.convergence import verify_convergence, ConvergenceResult
from ontology_engine.governance import run_invariants

# 用户写自己的 yaml
result = verify_convergence("my_custom_spec",
                             path="my_project/convergence_ontology.yaml")
```

这是 V3 路标"别人会扩展"的核心交付物。OpenClaw 11 天演进是这个路标的工程证据：
framework 设计的扩展性已被 4 个 spec + 2 种 apply 模式 + 多种扩展粒度（full
三件套 / mid-extension 抽 helper / minimal 1 件 / named-dispatch refactor）实测验证。

## 五条可落地原则

如果你在搭类似的控制平面：

1. **声明 → 决策**不够 — 必须有**声明 → 运行时事实**的同步机制。
2. **drift_action 至少 4 档** — alert_only / alert_only_permanent / machine_sync /
   block_until_human。每档对应不同的工程承诺。
3. **machine_sync 必须有 dry-run 安全网** — env var 控制，default safe。Plan B
   渐进路径让 operator 看 cron line 拼接结果再决定真激活。
4. **named-dispatch 比 if-else 分派可扩展** — 新 spec 类型 / 新 apply 模式只需
   注册新条目，不改 framework 代码。
5. **framework 必须集成进 audit 主流程** — 仅在测试时被调用 ≠ 生产消费。每天
   audit cron 主动跑 verify_convergence。

## 一句话总结

> 你的 control plane 不只是 policy engine，**它是 convergence engine**。声明态
> 与运行时态之间的差距，由机器同步而不是由人的记忆同步。

V37.9.18 教训：**记忆是最弱的可靠性原语**。
V37.9.24 答复：把记忆替换成 framework。

---

## 引用

- `ontology/convergence.py` — Convergence Framework V37.9.19 ~ V37.9.24
- `ontology/convergence_ontology.yaml` — 4 spec 声明
- `ontology/governance_ontology.yaml` — INV-CONVERGENCE-* 5 不变式 + MR-17
- `ontology/docs/cases/kb_deep_dive_cron_unregistered_case.md` — V37.9.18 血案
- [`audit_is_regression_not_prevention.md`](audit_is_regression_not_prevention.md) — 配套立场文章
- [`why_control_plane.md`](why_control_plane.md) — 项目级控制平面叙事

# Tool Policy Plugin Guide (V37.9.160 chunk 1)

> 声明式工具策略插件扩展接口 — 让消费方项目通过自己的 YAML 添加工具策略，
> 无需改 `policy_ontology.yaml` 主文件，无需理解引擎内部。镜像 [Provider Plugin](provider_plugin_guide.md) 的 `providers.d/` 自动发现模式。
>
> **状态**: chunk 1（自动发现 + 校验 + `evaluate_policy` 加性集成 + CLI）。
> enforcement 真接入（如 proxy 在请求路径上读插件策略硬截断）留后续 chunk。

---

## Overview

`pip install openclaw-ontology-engine` 后，工具策略有两层：

| 层 | 内容 | 谁写 |
|----|------|------|
| **主文件** `policy_ontology.yaml` | 项目核心工具策略（如 `max-tools-per-agent`） | 项目本体作者 |
| **插件** `policies.d/*.yaml` | 模块化/可组合的额外策略（如成本守卫、按域限流） | 任何扩展者 |

引擎在 `evaluate_policy(policy_id)` 查不到主文件策略时，自动查 `policies.d/` 插件（**加性，主文件优先不被覆盖**）。

---

## Quick Start（60 秒接入）

**1. 在你的本体目录下建插件目录 + 文件**（`<ONTOLOGY_CONFIG_DIR>/policies.d/cost_guard.yaml`）：

```yaml
policies:
  - id: max-tool-cost-per-task
    type: static
    scope: [Tool]
    rule: "sum(tool_call_cost) <= 0.50 USD per task"
    limit: 0.50
    hard_limit: true
    enforcement_site: "tool_proxy.py::do_POST cost accumulator"
    rationale: "防单任务工具调用成本失控。"
    governance_invariant: INV-COST-001
```

**2. 校验**（catch 拼写/缺字段错误）：

```
python3 ontology/engine.py --validate-policies
```

输出 `✅ max-tool-cost-per-task [static]` 即合规；任一插件非法 → 列出 error + `exit 1`。

**3. 用**（base 文件无此 id → 自动从插件解析）：

```python
from ontology.engine import evaluate_policy
r = evaluate_policy("max-tool-cost-per-task")
print(r["found"], r["limit"], r["hard_limit"])   # True 0.5 True
```

消费方场景设 `export ONTOLOGY_CONFIG_DIR=/my/project/ontology` → 引擎从 `/my/project/ontology/policies.d/` 发现。

---

## Policy Schema

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `id` | ✅ | str | 唯一标识（如 `max-tool-cost-per-task`） |
| `type` | ✅ | str | `static` / `temporal` / `contextual` |
| `rule` | ✅ | str | 人类可读规则文本 |
| `enforcement_site` | ✅ | str | 该策略在哪强制（代码位置） |
| `scope` | — | list/str | 作用域（如 `[Tool]` / `[Provider]`） |
| `limit` | — | number | 数值阈值（供 `evaluate_policy().limit` 直出） |
| `hard_limit` | — | bool | 是否硬限制 |
| `rationale` | — | str | 为何需要 |
| `governance_invariant` | — | str | 关联 `INV-*`（治理交叉引用） |

文件可含三种结构：`{policies: [...]}`（推荐）/ 顶层 `list` / 单条 `{id:...}` dict。

---

## Discovery & Naming Rules（镜像 providers.d/）

- 目录：`<ONTOLOGY_CONFIG_DIR>/policies.d/`（默认 `ontology/policies.d/`）。
- 扩展名：`.yaml` / `.yml`。
- **`_` 或 `.` 前缀文件被跳过**（如 `_example.yaml`、隐藏文件）。
- **FAIL-OPEN**：单插件文件解析/校验失败 → 收集 error 继续其他插件，绝不抛异、绝不阻塞整个评估（与 `ProviderRegistry.load_plugins` 同款契约）。
- 加性：插件**只能新增** policy_id，**不覆盖**主文件同名策略（主文件优先）。

---

## API Reference

| 函数 | 作用 |
|------|------|
| `engine.discover_policy_plugins(config_dir=None) -> (policies, errors)` | 发现 + 校验所有 `policies.d/` 插件策略 |
| `engine.validate_policy_dict(policy) -> list[str]` | 校验单条策略 dict，返回 error 列表（空=合法） |
| `engine.evaluate_policy(policy_id, ...)` | 评估策略（base 文件无则查插件） |
| `python3 ontology/engine.py --validate-policies [--json]` | CLI 校验，exit 1 if invalid |

---

## chunk 2+ follow-up（诚实登记）

- **enforcement 接入**：当前插件策略只在 `evaluate_policy()` 可查（observability），尚未在请求路径上硬强制。后续 chunk 让 `tool_proxy.py` / `three_gate.py` 读插件策略真截断（镜像 `max-tools-per-agent` 的 `_resolve_max_tools_limit`）。
- **真消费方驱动**：Provider Plugin 由 Doubao（第 8 provider）真接入才证明可扩展；Tool Policy Plugin 同样应等一个真实扩展策略需求（如成本守卫）驱动 enforcement chunk，避免 speculative 复杂度（原则 #18 + 日落法 #34）。
- **缓存**：当前每次 `evaluate_policy` 未命中 base 即重扫 `policies.d/`（纯函数无缓存，镜像引擎"每次重新读盘"哲学）。高频场景的缓存留后续。

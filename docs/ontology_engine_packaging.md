# Ontology Engine 包化设计 — 二层架构与迁移路标

> **V37.9.99-pkg / 2026-06-02 · Phase 5 chunk 1** + **V37.9.104 / 2026-06-04 · chunk 4 (Extension Guide + 最小消费方 demo)**
> 终极目标 `pip install <ontology-engine>` + 项目级 YAML 配置的奠基文档。
> 当前基础: Phase 3 ONTOLOGY_MODE=on / Phase 4 P1-P3 evaluate_policy + three_gate / 89 不变式 / 23 元规则 / governance v3.55。
> chunk 4 交付: `examples/minimal_consumer/` (WeatherBot 真消费方) + `docs/ontology_engine_extension_guide.md` + `test_ontology_extension_demo.py` (12 端到端单测)。

---

## 1. 终极目标

让**任何 Agent Runtime 项目**只需：

```bash
pip install openclaw-ontology-engine        # 装引擎 (Layer 1)
export ONTOLOGY_CONFIG_DIR=/myproject/ontology   # 指向自己的 YAML (Layer 2)
openclaw-ontology-audit                      # 跑治理审计
```

就获得**工具治理 + 语义查询 + governance 审计**能力，**无需理解引擎内部**。

核心价值不是"最强 LLM"，而是 **control plane 让任何 LLM 都能安全运行**——这是 V3 路标"别人会扩展"的终极交付物，也是 `docs/articles/ai_partnership_first_principles_zh.md` 第一性原理"协作本质是边界不是信任"的工程兑现。

---

## 2. 二层架构

| 层 | 内容 | 归属 | 当前位置 |
|---|---|---|---|
| **Layer 1 — 引擎 (项目无关)** | `ToolOntology` 类 / `evaluate_policy` / `find_by_domain` / 6 context evaluator dispatch · 5 个 check executor (`file_contains`/`file_not_contains`/`python_assert`/`env_var_exists`/`command_succeeds`) + `run_all` / `run_meta_discovery` MRD 框架 · convergence framework · 三阶段门控 three_gate · 三档特性开关 (off/shadow/on) | **本 pip 包** | `ontology/{engine,governance_checker,convergence,three_gate,diff}.py` |
| **Layer 2 — 配置 (项目特定)** | `tool_ontology.yaml` (工具声明) · `governance_ontology.yaml` (不变式 + 元规则 + check 定义) · `domain_ontology.yaml` · `policy_ontology.yaml` · `convergence_ontology.yaml` · 项目特定 MRD 扫描模式 (jobs_registry / notify.sh 路径等) | **消费方项目** | `ontology/*.yaml` (本仓库自带, 作默认参考) |

**关键判据**：代码是否含**项目特定知识**？
- `_exec_file_contains(check)` 执行 check 定义里的 pattern → 项目无关 → Layer 1
- `governance_ontology.yaml` 里 `INV-MOVESPEED-TCC-001` 守的是本项目的 MOVESPEED 备份 → 项目特定 → Layer 2

---

## 3. Config-Injection 契约（chunk 1 的 keystone）

引擎代码**不绑定**配置所在位置。解析优先级（两个 env 共享语义）：

| 环境变量 | 控制 | 默认 (向后兼容) |
|---|---|---|
| `ONTOLOGY_CONFIG_DIR` | 所有 YAML (`tool_ontology.yaml` / `governance_ontology.yaml` / `domain` / `policy`) 所在目录 | 引擎代码同目录 (`dirname(__file__)`) |
| `ONTOLOGY_PROJECT_ROOT` | governance 的 `file_contains` / `python_assert` 相对路径基准 + MRD 扫描根 | 引擎代码仓库根 (`dirname(dirname(__file__))`) |

```python
# engine.py
def _resolve_config_dir():
    env_dir = os.environ.get("ONTOLOGY_CONFIG_DIR", "").strip()
    return os.path.abspath(os.path.expanduser(env_dir)) if env_dir \
        else os.path.dirname(os.path.abspath(__file__))

# governance_checker.py — 同款 + _resolve_project_root()
```

**细粒度覆盖仍可用**：`ToolOntology(path=...)` / `evaluate_policy(..., path=...)` / `find_by_domain(..., path=...)` 的 `path` 参数优先于 `ONTOLOGY_CONFIG_DIR`，env 只是"未指定 path 时"的兜底。

**为什么这是 keystone**：没有它，pip 装的引擎只能读引擎自己目录下的 YAML（= 本项目的 YAML），消费方无法用自己的配置。有了它，"装引擎 + 指向我的 YAML" 才成立。chunk 1 之后的所有包化工作都建立在这个抽象上。

---

## 4. chunk 1 已交付（本 session）

1. **engine.py**: `_resolve_config_dir()` + `ONTOLOGY_CONFIG_DIR` env 注入（模块级 `_ONTOLOGY_FILE` 等通过它解析）。向后兼容（默认 = 当前 ontology/）。
2. **governance_checker.py**: `_resolve_config_dir()` + `_resolve_project_root()` + `ONTOLOGY_PROJECT_ROOT` env。`__main__` 块抽出 `main()`（行为不变，cron 仍走 `__main__ → main()`）供 console 入口。
3. **pyproject.toml**: 声明 `openclaw-ontology-engine` 0.1.0，`packages=["ontology"]`，`package-data` 含 YAML + CONSTITUTION.md，console_scripts `openclaw-ontology-audit` / `openclaw-ontology-query`。`pip install -e .` 后 `import ontology` 工作。
4. **本文档** + `test_ontology_packaging.py`（注入单测 + 反向验证）。
5. **验证**: full_regression 0 fail + governance 全绿 + **删除 ontology 后 proxy FAIL-OPEN 正常**（宪法第一条）。

---

## 5. 已知耦合（诚实登记，迁移时处理）

| 耦合 | 位置 | 性质 | 处理 |
|---|---|---|---|
| `from proxy_filters import ALLOWED_TOOLS,...` | `engine.py::main()` 的一致性 diff CLI | **lazy + try-guarded**，仅 CLI `--diff` 功能用，核心引擎不依赖 | 包内 `--diff` 无 proxy_filters 时 try 失败优雅降级；属 Layer 2 消费方耦合，记录即可 |
| `import convergence` / `from ontology import convergence` | `governance_checker.py::run_convergence_specs()` | convergence 是 `ontology` 子模块，双 fallback 已 package-aware | `packages=["ontology"]` 自动含，无需处理 |
| ~~**convergence spec 路径不读 `ONTOLOGY_CONFIG_DIR`**~~ ✅ **V37.9.107 chunk-3a 已修复** | `convergence.py` 现有 `_resolve_config_dir()` + `_resolve_project_root()`（镜像 engine.py / governance_checker.py） | **chunk-4 demo 暴露 → chunk-3a 闭环**：convergence 现读 `ONTOLOGY_CONFIG_DIR` 的 spec + 经 `ONTOLOGY_PROJECT_ROOT` 解析源文件（jobs_registry.yaml / *.json）。无 env 时默认分支保持 V37.9.19 `.resolve()` 语义字节级不变（零回归）。 | ✅ 已修。demo 新增 `convergence_ontology.yaml` + `weatherbot_state.json`，run_demo.py section 5 端到端验证（消费方读自己 spec 非 bridge）。14 新单测 + 反向 sabotage 守卫 |
| ~~MRD 扫描单文件名 (`jobs_registry.yaml` / `notify.sh` / `preflight_check.sh` / 诊断白名单)~~ ✅ **V37.9.126 chunk-3b 已修复** | `governance_checker.py::_discover_*()` | **单文件项目引用已 config-inject**：`_load_mrd_patterns()` 读 `governance_ontology.yaml::mrd_scan_patterns`，消费方可 override 自己的文件名；缺段 → `_MRD_DEFAULTS` (bridge 字节级一致)。WeatherBot demo 已验证端到端 (`weatherbot_jobs.yaml` 经注入读取)。 | ✅ 已修。byte-identical 验证 (89 inv/14 MRD discovery 不变) + 24 单测 + 反向 sabotage + demo `mrd_scan_patterns`。**chunk-3b.2 待启动**：per-scanner glob 形状 (`**/*.sh` vs `[*.sh, jobs/**/*.sh]` 等各异泛型) + push-route 白名单 (低价值高回归风险, 非紧急) |
| `governance_ontology.yaml` 的 python_assert 引用项目文件 | Layer 2 配置内 | 本就是 Layer 2（项目特定不变式） | 无需处理，消费方写自己的 |

---

## 6. 迁移路标

| chunk | 内容 | 风险 | 状态 |
|---|---|---|---|
| **1** | config-injection keystone + pyproject + 二层契约文档 + 包结构 | 低（向后兼容） | ✅ 完成 (V37.9.99-pkg) |
| **4 (本次)** | Extension Guide + 最小可跑 demo (WeatherBot 消费方) + config-injection 端到端验证 + 端到端测试 | 低（纯新增） | ✅ 完成 (V37.9.104) |
| 2 | import 名去泛化 (`ontology` → `ontology_engine` 或命名空间)：改 proxy_filters lazy-load 路径 + Mac Mini symlink + 全部 import + tests | **高**（破坏性，触发"删除后正常"宪法全验证） | 待启动 |
| **3a (本次)** | **convergence config-injection**：`convergence.py` 加 `_resolve_config_dir()` + `_resolve_project_root()`，spec 路径 + 源文件根（6 src_path + json + 2 sys.path）全经 env 注入。demo 加 `convergence_ontology.yaml` + `weatherbot_state.json` + run_demo.py section 5 端到端验证 | 低（向后兼容，默认 `.resolve()` 字节级不变） | ✅ 完成 (V37.9.107) — chunk-4 demo 暴露的耦合闭环 |
| 3b | ✅ **V37.9.126 完成 (单文件部分)**：MRD 扫描**单文件名** (`registry_file`/`notify_file`/`preflight_file`/诊断白名单) 移到 Layer 2 `mrd_scan_patterns`；`_load_mrd_patterns()` + `_MRD` 模块常量 + FAIL-OPEN observable except。byte-identical (89 inv/MRD discovery 不变) + 24 单测 + demo `weatherbot_jobs.yaml` 端到端注入。 | 中（触碰 MR-4 血案防护扫描器，已 byte-identical 验证） | ✅ 已完成 (单文件) |
| 3b.2 | per-scanner **glob 形状**参数化 (`**/*.sh` / `[*.sh, jobs/**/*.sh]` / `jobs/*/run_*.sh` / alert_path `wa_*.sh`+`*watchdog*.sh` 等各异) + push-route 白名单 (`_PUSH_ROUTE_WHITELIST`) | 中-高（glob-set 改变风险回归 + 各扫描器形状不同，每个需精确默认 + byte-identical 守卫） | 待启动（低价值高风险，非紧急；当前 glob 泛型且优雅 no-op） |
| 5 (Phase 5 终态) | 真 sdist/wheel 构建 + `pip install` (非 editable) 冒烟 + 发布决策 (PyPI 名 / license / readme) + 版本治理 (semver) + 第三方项目接入验证 | — | 待启动 |

---

## 6.1 推荐推进顺序（专业建议）

> **不按数字顺序推，按"先证明价值、再做破坏性投入"推。**
> 推荐顺序：**chunk 4 → chunk 3a → chunk 2 → chunk 3b → chunk 5**
> （V37.9.107 调整：chunk 3a（convergence config-injection）是 demo 暴露的低风险真 bug 且 de-risk chunk 2（更少东西硬编码到 `ontology/` 目录），提前到 chunk 2 之前做。）

| 顺位 | chunk | 为什么这个位置 |
|---|---|---|
| **① 先做** ✅ **V37.9.104 完成** | **chunk 4** Extension Guide + 最小可跑 demo | **风险最低（纯新增，不改现有代码），价值最直观。** 第一次有"第二个项目"真用引擎 → 端到端验证 config-injection + 四大能力（工具查询/域查询/策略评估/治理审计）。这是引擎的 **"Doubao 时刻"**：Provider Plugin 直到 Doubao（第 8 个 provider，V37.9.52）真接入才被证明可扩展；引擎同样需要一个真实消费方 demo 来证明抽象正确。**交付**: `examples/minimal_consumer/` (WeatherBot — 工具/域/策略/不变式全部不同于 bridge) + `run_demo.sh` (端到端跑通) + 12 单测（含反向验证：不注入 env → 引擎读 bridge 配置，证明 demo 通过依赖注入）。**demo 自然暴露 convergence 路径耦合**（§5 新登记），印证 ③ 的预期。demo 用当前 `import ontology`，显式标注"import 名是临时的，chunk 2 会改"。 |
| **② 次之** | **chunk 2** import 名去泛化 | **最高风险（破坏性：proxy lazy-load + Mac Mini symlink + 全部 import + tests），必须全宪法验证**（删除后正常 + full_regression + 删除安全）。放在 ① 之后是因为：demo 已证明能力成立 → rename 退化为"有明确目标的机械重构"。**绝不第一个做**——在没验证价值前先做破坏性重构，是把风险放在收益前面。 |
| **② 次之** ✅ **V37.9.107 完成** | **chunk 3a** convergence config-injection | demo（①）真实暴露的耦合（convergence 是 demo 唯一规避的阶段）。低风险（向后兼容）、纯 dev、无需定包名/symlink。提前到 chunk 2 之前做因为它 **de-risk chunk 2**：convergence 不再硬编码到 `ontology/` 目录，rename 时更少东西要改。**交付**: `convergence.py` 两 resolver + demo `convergence_ontology.yaml`/`weatherbot_state.json` + run_demo.py section 5 + 14 单测 + 反向 sabotage 守卫。 |
| **③ 第三** | **chunk 2** import 名去泛化 → **chunk 3b** MRD 模式参数化 | chunk 2 仍是最高风险破坏性 rename（见 ② 原 chunk 2 段，需定包名 + Mac Mini symlink + 全宪法验证）。chunk 3b（MRD 文件名模式 → Layer 2）放最后因为它触碰 ~10 个 MR-4 血案防护扫描器，需聚焦 session，且当前已优雅 no-op（非紧急）。 |
| **④ 最后** | **chunk 5** 发布 | 需 chunk 2/3 完成 + 名称/license/PyPI 决策 + sdist/wheel 真构建。 |

**核心工程智慧**：先用便宜的 spike/demo 验证整个包化前提，再投入昂贵的破坏性重构——如果 demo 暴露抽象有问题，就避免了白做 chunk 2 的破坏性 rename。这与本项目 **原则 #18（补证据而非补功能）** + **Provider Plugin 由真消费方（V37.9.52 Doubao）驱动** 一脉相承：扩展接口的价值，只有真实消费方接入那一刻才被证明。

---

## 7. 开放决策（发布前必答）

1. **import 名**: `ontology` 太泛化（消费方 env 易冲突）。候选 `ontology_engine` / `openclaw_ontology` / `ocre`。**chunk 1 刻意不改**——它是破坏性变更（proxy_filters `spec_from_file_location("ontology/engine.py")` + Mac Mini `$HOME/ontology` symlink + `from ontology import convergence` + `ontology/tests/`），违反"第一块要安全"。留 chunk 2 专门做 + 全宪法验证。
2. **分发名**: `ontology-engine` 0.1.0 公共 PyPI 已被他人占用（status.json 已登记撞名）。chunk 1 用 `openclaw-ontology-engine`。是否申请 scoped / 改名 = 发布时决定。
3. **license**: 仓库未声明 license。chunk 1 pyproject 不写 license 字段（避免错误断言）。发布前补。
4. **readme**: chunk 1 不设 `readme`（避免 build 脆弱）。发布前指向本文档或专门 README。

---

## 8. 向后兼容 & 宪法（删除后原系统正常）

chunk 1 **零破坏**：
- 无 env 时所有路径解析 = 当前行为（`dirname(__file__)`），Mac Mini / dev / cron 行为不变。
- `proxy_filters.py` 仍 lazy-load `ontology/engine.py`（`spec_from_file_location`），env 未设 → 默认 → 正常；**删除整个 `ontology/` → proxy FAIL-OPEN 回退 config**（宪法第一条，chunk 1 未触碰此机制）。
- `governance_audit_cron.sh` 仍 `cd repo && python3 ontology/governance_checker.py --full` → `__main__ → main()` → 行为不变。
- `pyproject.toml` 是**新增**文件，不被任何现有运行时读取（仅 `pip install` 时用），对生产零影响。

---

## 9. 用法

### 本仓库内（chunk 1 已可用）

```bash
pip install -e .                              # editable, import ontology 指向 ontology/
openclaw-ontology-audit                       # = python3 ontology/governance_checker.py
ONTOLOGY_CONFIG_DIR=/other/cfg openclaw-ontology-query --json   # 引擎指向别处 YAML
```

### 未来消费方（chunk 4 Extension Guide 完整化后）

```bash
pip install openclaw-ontology-engine
mkdir myproject/ontology && cp <templates> myproject/ontology/
export ONTOLOGY_CONFIG_DIR=$PWD/myproject/ontology
export ONTOLOGY_PROJECT_ROOT=$PWD/myproject
openclaw-ontology-audit                       # 审计 myproject 的不变式
```

---

## 参考

- `ontology/CONSTITUTION.md` — Ontology 宪法（最高条款：删除后原系统正常）
- `ontology/engine.py` / `governance_checker.py` — Layer 1 引擎
- `docs/provider_plugin_guide.md` — Provider Plugin 模式（chunk 4 Extension Guide 的镜像模板）
- `docs/strategic_review_20260403.md` — V3 路标"别人会扩展"
- `docs/articles/ai_partnership_first_principles_zh.md` — control plane = 边界系统的第一性原理

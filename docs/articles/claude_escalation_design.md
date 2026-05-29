# Claude Escalation Capability — V37.9.90 设计文档

> Direction 2 from V37.9.83 strategic sediment
> 2026-05-29 PoC / `claude_escalation.py` + `test_claude_escalation.py`
> Author: Claude Opus 4.7 + 项目作者 (V37.9.90 同 session 交付)

---

## TL;DR

PA (Qwen3-235B Mac Mini) 在遇到**复杂判断**时调一个名为 `escalate` 的 custom tool，该 tool 进行一次 Anthropic SDK 调用（Opus 4.7 优先 / Sonnet 4.6 降级），喂入 `status.json + 14 天 changelog + 相关 case docs` 作为 prompt-cached 上下文，返回**结构化 JSON proposal**（read-only，不含 shell 命令）。每次调用都被审计日志记录，每日有调用配额。

**核心约束**：
- **One-shot SDK 调用** — 不是新 Claude Code session（不同语义、不同成本、不同失败模式）
- **Read-only output** — Claude 提建议，PA/用户 执行；不嵌入可执行 shell
- **Prompt caching on context** — 重复调用 ~90% cost reduction
- **FAIL-CLOSE on missing API key** — 不静默 fallback 到 Qwen3（boundaries > trust，V37.9.83 第一原理）
- **Mockable transport** — dev 环境无 ANTHROPIC_API_KEY 可跑 `--dry-run` + 单测

---

## 一、背景与动机（V37.9.83 战略沉淀）

### V37.9.83 三大第一性原理回顾

1. **协作本质是边界不是信任** — `boundaries > trust`. 100% 权限假设不能消除 LLM emergent misalignment（reward hacking / distributional shift / Goodhart's law / compounding errors 四力学）。
2. **系统自我成长 = 用户视角机器化** — 5 个月数据 70% 真问题来自用户 WhatsApp 一句反馈。
3. **LLM 判断三层（价值递减）** — 工作记忆 / 判断模式 / 协作惯性。判断模式真正 gap 可部分蒸馏，但 5+ 元规则案例库的隐含判断（哪条规则在当前情境最重要 / 多条规则冲突时如何取舍）目前只能在 Claude session 内一致地复现。

### V37.9.90 解决的痛点

> **痛点：Claude Code 不在场时，系统判断力不够。**

具体场景（5 个 use cases，对应 5 类痛点）：

| 场景 | 痛点 | escalate 价值 |
|------|------|---------------|
| **U1** PA 收到用户反馈"今日 dream 推送质量低"，需要决定是临时降级 prompt 还是立 V37.9.91 hotfix | PA 看 case docs 不会主动应用 MR-15 (deployment-must-be-tested) | Claude 综合 status.json focus + 21 case docs + 14d changelog → "建议立 V37.9.91 hotfix 而非临时降级，因 MR-15 已三次被违反" |
| **U2** PA 注意到 governance audit 报警 "S2 fetch_failed 2 天连续"，需要决定是直接 retry 还是申请 API key | PA 不知道 MR-10 (understand-before-fix) 和 V37.9.83 边界原则在此情境的相对优先级 | Claude 输出 "申请免费 S2 API key 是 clean fix；continuing retry 是 silent failure 模式（MR-4 演出第 N+1 次）" + refs `[V37.9.84, MR-4, MR-10]` |
| **U3** 用户问 PA "V37.9.85 OK 还是有问题？" PA 没有跨 case docs 推理能力 | PA 答案靠 status.json focus 字段单一来源，缺乏 cross-doc inference | Claude 综合 status.json + recent changelog + 相关 case docs，提 "V37.9.85 mm_index 路径迁移已稳定 3 天，但 V37.9.86 trap override 修复是更近的关键节点" |
| **U4** 用户问 PA "今天哪个任务最该做？" | PA 优先级排序逻辑是 status.json `priorities` 字段（静态），不考虑当前周焦点 + recent_changes 动态信号 | Claude 输出 ranked proposal + rationale 引用具体 status.json 字段 |
| **U5** PA 遇到不在 SOUL.md 规则触发词范围内的"边缘判断"（例如新提议的架构变更） | PA 默认顺从用户、容易迎合性回复（V37.4.3 PA 回声室案例） | Claude 引用规则 9（批判性思考）和案例库，给出**反方向**的批判性 proposal |

---

## 二、架构

```
┌────────────────────────────────────────────────────────────────┐
│  用户 (WhatsApp)                                                │
│      ↓ 复杂问题                                                  │
│  PA (Qwen3-235B Mac Mini)                                       │
│      ├─ SOUL.md 规则 12 触发词识别 ("Claude 深度判断" 等)        │
│      ↓                                                          │
│  Tool: claude_escalate(question)                                │
│      ├─ 走 Tool Proxy 自定义工具注入机制 (V30.3 已有)           │
│      ↓                                                          │
│  claude_escalation.escalate(question)                           │
│      ├─ Quota check (~/.kb/audit/claude_escalations.jsonl)     │
│      ├─ Load context:                                           │
│      │     ├─ status.json (~/.kb/status.json + 仓库备份)        │
│      │     ├─ CLAUDE.md 14 天 changelog                          │
│      │     └─ ontology/docs/cases/ keyword-relevant 3 docs      │
│      ├─ Build system blocks:                                    │
│      │     [0] SYSTEM_PROMPT (no cache)                         │
│      │     [1] context block (cache_control: ephemeral) ←━━━━━━━━━┓
│      ├─ Anthropic SDK call:                                     │  │
│      │     ├─ model: claude-opus-4-7 (fall back claude-sonnet-4-6)  │ ~90% cost
│      │     ├─ thinking: adaptive                                │  │ reduction
│      │     ├─ output_config.effort: high                        │  │ on repeated
│      │     └─ messages: user message (volatile)                 │  │ calls
│      ├─ Parse response JSON                                     │  │
│      ├─ Validate read-only contract                             │  │
│      │     ├─ shell code fence?      → FAIL-CLOSE              │  │
│      │     ├─ command substitution? → FAIL-CLOSE              │  │
│      │     └─ dangerous tokens?     → FAIL-CLOSE              │  │
│      ├─ Audit log (JSONL append)                                │  │
│      └─ Return structured proposal                              │  │
│      ↓                                                          │  │
│  PA 把 proposal 回复给用户                                       │  │
│      ↓                                                          │  │
│  用户决定是否执行                                                 │  │
└────────────────────────────────────────────────────────────────┘  │
                                                                    │
Anthropic API (Claude Opus 4.7 / Sonnet 4.6) ───────────────────────┘
```

### 关键设计决策

#### D1：为什么是 SDK 调用而不是新 Claude Code session

| 维度 | SDK 调用（V37.9.90） | Claude Code session |
|------|----------------------|---------------------|
| 语义 | 一问一答（专家咨询） | 多轮对话+工具循环 |
| 延迟 | ~3-10s | ~20-60s+ |
| 成本 | 单次 + prompt-cached | 每次完整启动 + token 累积 |
| 失败模式 | API 错误（清晰） | 工具循环卡死、读文件超时 |
| 工具暴露 | 无（read-only） | 完整工具集（写文件等高危） |
| 上下文 | 我们构造（精选） | 模型 + 全部 docs/CLAUDE.md |

V37.9.90 的语义是"专家咨询"，SDK 调用是天然匹配。Claude Code session 是给开发者用的，给 PA 用是 over-engineering 且增加 blast radius。

#### D2：为什么 cache_control 在 context block 不在 system prompt

prompt 渲染顺序：`tools → system → messages`. 任何前部变化会让后部缓存失效。

```python
system_blocks = [
    {"type": "text", "text": SYSTEM_PROMPT},           # ← 稳定但短
    {"type": "text", "text": context_md,                # ← 稳定+长
     "cache_control": {"type": "ephemeral"}},
]
```

- `SYSTEM_PROMPT`（~2KB）是稳定的，但它在前面已经"自然 caching" — 因为后面有 cache_control 的 context block 会把 cache key 一直延伸到 context 末尾。
- `context_md`（~30-80KB）是稳定的，放 cache_control 是关键 — 让重复调用读 ~0.1x 成本。
- 用户问题（volatile, 每次不同）放 messages，不缓存。

**典型成本估算**：
- 第一次调用：~80K input @ 5/M = $0.40
- 第二次调用（同日）：~80K cache_read @ 0.5/M + ~500 token volatile = ~$0.04
- 第三次同款：同上 $0.04
- 每日 10 次配额上限 → ~$0.40 + 9 × $0.04 = ~$0.76/day cap

#### D3：为什么 Read-only enforcement 而不是 PA 自己过滤

**因为 PA 是 Qwen3，不是 Claude**。LLM-as-validator 不可靠（V37.9.89 LEVEL_6 血案：Qwen3 把"严禁推测"狭隘解读，对跨域创造性桥接网开一面）。

更可靠的做法：
- 程序化 regex 扫描（4 类：shell code fence / command substitution / dangerous tokens / fork bomb pattern）
- 一旦发现违规 → `status = "read_only_violation"`、FAIL-CLOSE 不返回该 proposal
- 审计记录违规计数

这是 V37.9.83 boundaries-over-trust 原则在 framework 层的实现。

#### D4：为什么 Daily Quota = 10

- 单日 10 次 × $0.04（cached） + 第一次 $0.40 = ~$0.76/day
- 如 PA 每次小问题都 escalate（误触发） → 10 次封顶，不会爆账单
- Mac Mini operator 可改 `--max-daily` 调整

#### D5：为什么需要 Dry-run mode

Dev 容器无 `ANTHROPIC_API_KEY`，且测试不应真调 API（确定性 + 速度 + 成本）。Dry-run 满足：
- 单测可在 dev 跑通 72/72
- PoC 演示无需密钥
- 生产部署可先 `--dry-run` 验证上下文组装是否成功

---

## 三、Trigger 机制（SOUL.md 规则 12）

CLAUDE.md 原则 #24 明确：**SOUL.md 触发词是唯一可靠的工具调用机制**（Qwen3 不会自主决定调用专用工具）。

### 规则 12 触发词候选

| 类别 | 触发词（用户消息中含此类词时 PA 必须调 escalate） |
|------|---------------------------------------------------|
| **显式 escalate** | "让 Claude 看看" / "Claude 深度判断" / "需要 Claude" / "请 escalate" |
| **不确定** | "你不太确定" / "拿不准" / "要不要 Claude 来" |
| **复杂取舍** | "这两个方案哪个好" / "应该优先做哪个" / "需要权衡" |
| **跨案例推理** | "这个跟之前 X 血案像吗" / "翻一下案例库" |
| **批判性** | "帮我反过来想" / "你觉得这有什么风险" / "有没有 trap" |

**触发条件硬规则**：
1. 消息含上述任一触发词 → PA 必须调 `escalate(question=用户原话)`
2. PA 不得回 "我帮你想想"自己处理 — 必须走 escalate
3. escalate 返回后 PA 转发 proposal + rationale + confidence 给用户
4. 用户决定是否执行；PA 不自动执行 proposal 中的任何建议

---

## 四、输出契约（read-only proposal）

Claude 必须返回严格的 JSON 对象：

```json
{
  "proposal": "1-3 段 plain prose。建议考虑做什么。绝无嵌入式 shell 命令、code blocks。可在 prose 中提及命令名（如 'review with git status'）但不嵌入可执行行。",
  "rationale": "1-2 段。为什么这个 proposal — 引用 status.json/changelog/case docs 中的具体事实。",
  "confidence": "high" | "medium" | "low",
  "refs": ["file path / version / case doc ID / commit SHA — 仅出现在 provided context 中的"]
}
```

### Read-only 验证层

`validate_read_only(proposal_dict)` 递归扫描所有 string 字段：

| 模式 | 例子 | 处理 |
|------|------|------|
| Shell code fence | ` ```bash\nrm -rf\n``` ` | FAIL-CLOSE |
| Command substitution | `$(whoami)` / backtick | FAIL-CLOSE |
| 危险 token | `rm -rf` / `sudo ` / `chmod -R` / `mkfs` / `dd if=` / `>/dev/sd` | FAIL-CLOSE |
| Fork bomb | `:(){:|:&};:` | FAIL-CLOSE |
| Curl-pipe-bash | `curl | bash` / `wget | sh` | FAIL-CLOSE |

任一命中 → 整个 response 被拒绝 + 审计记录 `read_only_violation` + 用户看不到该 proposal。

---

## 五、成本模型

### 单次调用成本明细

| 项 | Token 估计 | 单价（Opus 4.7） | 成本 |
|----|-------------|-------------------|------|
| System prompt | ~500 | $5/M input | $0.0025 |
| Context block（首次写入） | ~70K | $5/M × 1.25 = $6.25/M | $0.44 |
| User question | ~200 | $5/M | $0.001 |
| Output | ~1000 (read-only proposal) | $25/M | $0.025 |
| **首次调用** | — | — | **~$0.47** |
| Context block（缓存读） | ~70K | $0.5/M | $0.035 |
| **后续调用（同日）** | — | — | **~$0.06** |

### 日上限

`max_daily = 10` + 假设 1 次首次 + 9 次缓存 = ~$0.47 + 9 × $0.06 = **~$1.01/day cap**

### Sonnet 4.6 fallback 成本（同样的 token 量）

- 首次：$3/M × 70K × 1.25 + $15/M × 1K = $0.26 + $0.015 = $0.275
- 缓存：$0.3/M × 70K + $15/M × 1K = $0.021 + $0.015 = $0.036
- 10 次 cap：$0.275 + 9 × $0.036 = **~$0.60/day cap**

→ 即使 fallback Sonnet 4.6，日上限 < $1。这是**可接受的边际成本**对比"Claude 不在场时 PA 判断失误"的产品风险。

---

## 六、Failure modes + mitigation

| 故障模式 | 触发条件 | Mitigation | 用户感知 |
|----------|----------|-------------|----------|
| **`no_context`** | status.json + changelog + case docs 全部 unloadable | 返回明确 status，PA 回 "无足够上下文" | 用户知道是上下文问题 |
| **`api_unavailable`** | Anthropic API 故障 / API key missing | 不静默 fallback Qwen3；PA 回 "Claude 暂不可用" | 用户知道走 PA fallback |
| **`quota_exceeded`** | 当日已 ≥10 次 | 返回明确状态；PA 回 "今日 Claude 咨询已用完，建议直接问开发者" | 用户知道配额机制存在 |
| **`parse_failed`** | Claude 返回非 JSON | 尝试 fallback model；如都失败 → status 同 | 用户看到 "Claude 响应格式错误，重试" |
| **`read_only_violation`** | proposal 含 shell 命令 | FAIL-CLOSE：proposal 被拒绝，记录违规 | 用户看到 "Claude 给出不合规建议，已自动拒绝" |
| **`dry_run`** | dev 环境 / 验证模式 | 返回 synthetic response | 用户清楚是 dry-run 不是真 Claude |

**关键不变量**：FAIL-CLOSE 优先于 FAIL-OPEN（V37.9.83 boundaries 原则）。

---

## 七、Risks

### R1 — Cost runaway

- **风险**：PA 过度调用 → 月费不可控
- **缓解**：daily quota = 10 + per-call max_tokens = 4000 + audit log 监控

### R2 — Prompt injection 攻击

- **风险**：用户 question 中嵌入 prompt injection → Claude 输出绕过 system prompt
- **缓解**：read-only validator + 输出契约严格（JSON-only）；即使 prompt 被绕过，输出 shell 命令仍被拒绝

### R3 — Context bloat

- **风险**：case docs 持续增长 → context block 超 max_chars 截断关键信息
- **缓解**：keyword scoring 选 top 3 cases + per-doc 截断 8KB + 总上限 80KB

### R4 — Read-only validator 误报

- **风险**：proposal 中合法提及命令名（如 "use git status to check"）被误判
- **缓解**：仅拒绝 `$()` / 反引号包裹 / fenced code blocks / 危险 token 字面量。Plain prose 提命令名 OK（has tests）

### R5 — Single point of failure

- **风险**：Anthropic API down → escalate 100% 失败
- **缓解**：Opus 4.7 → Sonnet 4.6 fallback（不同模型不同 capacity）；最终 fallback 是 status=api_unavailable + PA 回 fallback 消息

### R6 — LLM 内部状态泄漏

- **风险**：Claude 输出含训练数据中的真实人物/事件名
- **缓解**：grounding 约束（系统 prompt 严令引用必须 traceable 到 context）+ 监控审计日志中的 refs 字段

---

## 八、Roadmap

### V37.9.90 (今日 PoC) — 完成

- ✅ `claude_escalation.py` (~470 行)
- ✅ `test_claude_escalation.py` (72 单测 / 12 测试类)
- ✅ `docs/articles/claude_escalation_design.md` (本文)
- ✅ SOUL.md 规则 12 注册触发词

**不做**：tool_proxy 集成 / Mac Mini 部署 / 生产 ANTHROPIC_API_KEY 配置。

### V37.9.91+ (下次 session)

- (1) **tool_proxy 集成**：proxy_filters.py 加 `claude_escalate` 自定义工具注入（V30.3 同款模式）
- (2) **Mac Mini 部署**：
  - `auto_deploy.sh` FILE_MAP 加 `claude_escalation.py`
  - `ANTHROPIC_API_KEY` 安全配置（plist EnvironmentVariables，不入 git）
  - `pip3 install anthropic`（Mac Mini Python 环境）
- (3) **SOUL.md 规则 12 真激活**：触发词列表落地 + PA 行为验证
- (4) **第一次真生产调用**：用户在 WhatsApp 实测，观察 proposal 质量 + 成本

### V37.9.92+ (一周观察后)

- (5) **INV-ESCALATION-001 立 governance**：
  - claude_escalation.py V37.9.90 marker present
  - audit log path correct
  - read-only validator 4 patterns 测试 cover
  - max_daily ≤ 10
- (6) **统计仪表板**：每周生成 escalation 调用统计（次数 / proposal confidence 分布 / read-only 违规率）
- (7) **prompt caching 真激活验证**：审计日志看 `cache_read_input_tokens` 累计百分比

### V37.9.95+ (Stage 3 战略推进)

- (8) **Context 增强**：考虑加 `failure_modes_catalog.md` 32 案例 + adversarial_chaos_audit 16 场景作为可选 context
- (9) **Multi-turn**：未来如需多轮对话（"Claude 你刚才说 X，那 Y 呢？"）评估 SDK 多轮 vs 短 conversation history
- (10) **Cost 优化**：1h TTL 缓存（如同日 ≥3 次调用）

---

## 九、Open questions

1. **谁触发 `--dry-run`？** 生产环境是否需要 dry-run（如 "PA 想 escalate 但不耗 quota，先看看 context 组装结果"）？
2. **API key 轮换策略？** ANTHROPIC_API_KEY 失效时如何 graceful failover？
3. **Proposal 是否要 KB 归档？** 如 PA 一周内 escalate 10 次都是 freight 相关，是否提议加 freight-specific 触发词降低 escalation 频率？
4. **审计日志 retention？** JSONL 文件无大小上限 — 30 天 rotate？

---

## 十、一句话总结

V37.9.83 思想升华文档第二原理"系统自我成长 = 用户视角机器化"在 framework 层的**关键拐点**：从"Claude 在场时系统判断力强"升级为"Claude 不在场时仍可按需调用 Claude 判断力，且边界明确（read-only / quota / FAIL-CLOSE）"。

这是 Stage 2 → Stage 3 拐点（V37.9.83 确立）的**第二个具体兑现**（第一个是 V37.9.84 Daily Self-Critique Observer）。AI Partnership Framework 不再是空头叙事，而是可机器化、可治理、可成本控制的工程实现。

---

**附录 A — 模块文件清单**

```
claude_escalation.py      (~470 lines)  核心模块 + CLI
test_claude_escalation.py (~700 lines)  72 单测 / 12 测试类
docs/articles/claude_escalation_design.md  (本文)
SOUL.md 规则 12          (~30 行)      PA 触发词契约
```

**附录 B — V37.9.90 测试覆盖率**

- TestLoadStatus (4)
- TestLoadChangelogWindow (6)
- TestSelectRelevantCaseDocs (4)
- TestBuildContextBlock (4)
- TestValidateReadOnly (10)
- TestCheckDailyQuota (4)
- TestAuditRecord (4)
- TestParseResponseJson (5)
- TestCallClaudeWithMock (5)
- TestEscalateOrchestrator (11)
- TestCliInterface (2)
- TestV37990SourceGuards (13)

**合计**：72 单测，0 fail，覆盖 11 类故障模式 + 5 个 source-level drift 守卫。

---

🤖 Generated by Claude Opus 4.7 + 项目作者 (V37.9.90 PoC, 2026-05-29)

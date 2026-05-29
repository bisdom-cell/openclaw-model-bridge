# Expert Escalation Capability — V37.9.90-r1 设计文档

> V37.9.83 Direction 2 (AI Partnership Framework) — 重设计 2026-05-29
> Backend: Doubao Seed 2.0 Pro (已运行) / Claude pending future-flip
> 主交付：`expert_escalation.py` + `test_expert_escalation.py` + SOUL.md 规则 12

---

## TL;DR

PA (Qwen3-235B Mac Mini) 在遇到复杂判断时调一个 custom tool `expert_escalate`，**路由到已经在生产运行的 Doubao Seed 2.0 Pro**（V37.9.55 已 verified text/streaming/tool_calling/reasoning，cap_score=16 > Qwen3=14）。喂入 `status.json + 14d CLAUDE.md changelog + 相关 case docs`，返回**结构化 JSON proposal**（read-only，不含 shell 命令）。每次调用都被审计日志记录，daily 配额 30 次（成本 ~$0.30/day cap）。

**Claude 暂时 pending**：backend 选择器保留 `claude_pending` 分支，未来配齐 `ANTHROPIC_API_KEY` 后可一键 flip。当前 dev/Mac Mini 都用 Doubao。

**重设计理由**（vs v1 Claude SDK）：

| 维度 | v1 Claude | r1 Doubao | 改变 |
|------|-----------|-----------|------|
| Backend 状态 | 待集成 / 待装包 / 待付费 | **已在 production 跑** (V37.9.52/55) | -3 个安装步骤 |
| 单次成本 | ~$0.47 首调 / $0.06 cached | **~$0.01-0.02**（缓存自动） | **30x 便宜** |
| 依赖 | `pip install anthropic` | stdlib urllib | **零新依赖** |
| 鉴权配置 | ANTHROPIC_API_KEY 待申请 | ARK_API_KEY 已在 plist | **零配置** |
| Daily quota | 10（cost cap $1） | 30（cost cap $0.30）| 3x 宽松 |
| 网络延迟 | 国际 ~3-10s | **国内 ~2-5s** | **更稳定** |
| Reasoning 能力 | adaptive thinking | reasoning_content 字段 | 等价 |
| Prompt caching | manual `cache_control` | **Volcengine Context Cache 自动** | 简化 |

---

## 一、为什么 Doubao first（不是 Claude）

### 系统现状（CLAUDE.md V37.9.55 已确认）

```
fallback_chain (V37.9.55 验证后):
  doubao (cap_score=16)     ← 主候选, verified text+streaming+tool_calling+reasoning
  qwen3 (cap_score=14)      ← 当前 PROVIDER_NAME=qwen, primary 不动
  gemini, glm, ...          ← 其余 fallback
```

**Doubao 在系统中的状态**：
- ✅ V37.9.52 接入（doubao_provider.py 插件）
- ✅ V37.9.53 reasoning capability 立案
- ✅ V37.9.54 verified_vision flip + INV-PLIST-ENV-001 立 governance
- ✅ V37.9.55 flip verified_tool_calling + verified_streaming（5/5 verified）
- ✅ V37.9.77 capability router enforcement framework（doubao 可被 ?provider= 选中）
- ✅ Mac Mini plist 已配 ARK_API_KEY + ARK_ENDPOINT_ID（V37.9.54 INV-PLIST-ENV-001 守卫）
- ✅ `pip install` 零负担（OpenAI Chat Completions 兼容，stdlib urllib 即可调）

**Doubao 不在主路径只因 `PROVIDER_NAME=qwen` 未改**（V37.9.55 收工承诺中已说明）。这次 V37.9.90-r1 把它**显式用在 expert escalation 这个新场景**，不动 PA 主路径。

### Claude 暂时 pending 的理由

- ANTHROPIC_API_KEY 尚未申请 + 配置
- Mac Mini 网络访问 anthropic.com 未测试
- `pip install anthropic` 需要 Mac Mini 操作
- 成本对比之下 Doubao 30x 便宜 → 没必要急

**Future-flip 机制**：`expert_escalation.py` 已留 `backend="claude_pending"` 分支返回明确状态。未来配齐后只需：
1. `pip install anthropic` on Mac Mini
2. plist 加 `ANTHROPIC_API_KEY`
3. 在 expert_escalation.py 加 Anthropic transport class
4. 改 `DEFAULT_BACKEND` 或加 backend priority list
5. 单测加 Claude transport 测试

---

## 二、架构

```
┌────────────────────────────────────────────────────────────────┐
│  用户 (WhatsApp)                                                │
│      ↓ 复杂问题                                                  │
│  PA (Qwen3-235B Mac Mini)                                       │
│      ├─ SOUL.md 规则 12 触发词识别 ("帮我深度判断" 等)            │
│      ↓                                                          │
│  Tool: expert_escalate(question)                                │
│      ├─ 走 Tool Proxy 自定义工具注入机制 (V30.3 已有)           │
│      ↓                                                          │
│  expert_escalation.escalate(question, backend="doubao")         │
│      ├─ Backend gate:                                           │
│      │     ├─ "doubao"  → 继续                                  │
│      │     ├─ "claude_pending" → 返回 stub status               │
│      │     └─ unknown   → 返回 unknown_backend                  │
│      ├─ Quota check (~/.kb/audit/expert_escalations.jsonl)     │
│      ├─ Load context:                                           │
│      │     ├─ status.json                                       │
│      │     ├─ CLAUDE.md 14d changelog                            │
│      │     └─ ontology/docs/cases/ keyword-relevant 3 docs      │
│      ├─ Build context block (stable prefix for Volcengine cache)│
│      ↓                                                          │
│  DoubaoTransport.call()                                         │
│      ├─ POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
│      ├─ Authorization: Bearer ${ARK_API_KEY}                    │
│      ├─ payload:                                                │
│      │     model: ${ARK_ENDPOINT_ID} (e.g. ep-2026...)         │
│      │     messages: [system (SYSTEM_PROMPT + context_md),     │
│      │                user (question)]                          │
│      │     max_tokens: 4000                                     │
│      │     temperature: 0.3   ← Doubao 支持, Claude 不支持      │
│      ├─ Volcengine Context Cache 自动 (prefix ≥ 1024 tokens)    │
│      ↓                                                          │
│  Response: {choices[0].message.content + reasoning_content,     │
│             usage.prompt_tokens_details.cached_tokens}          │
│      ├─ Parse JSON proposal                                     │
│      ├─ Validate read-only contract (4 patterns FAIL-CLOSE)     │
│      ├─ Audit log (JSONL append, 含 backend + usage)            │
│      └─ Return structured proposal                              │
│      ↓                                                          │
│  PA 转发 proposal + rationale + confidence + refs 给用户         │
│  (标注"来自 Doubao Seed 2.0 Pro")                               │
│      ↓                                                          │
│  用户决定是否执行                                                 │
└────────────────────────────────────────────────────────────────┘
```

### 关键设计决策

#### D1：为什么 system + context **合成单条** system message

Anthropic SDK 支持 `system=[{}, {with cache_control}]` 多 block 模式。Doubao（OpenAI 兼容）API **只接受 `messages[0].role="system"` 单条**。

合成策略：
```python
full_system = SYSTEM_PROMPT + "\n\n" + context_md
payload["messages"] = [
    {"role": "system", "content": full_system},
    {"role": "user", "content": user_message},
]
```

**Volcengine Context Cache 仍生效** — 它按字节级 prefix 匹配，不需要显式 cache_control。只要 `full_system` 的前缀稳定（system prompt + context 顺序固定），重复调用就命中缓存。

#### D2：为什么 temperature=0.3

- Doubao 支持 temperature（Claude Opus 4.7/4.8 移除了）
- 0.3 = 低方差 → expert advisory 场景需要"稳定、可解释、可重现"
- 完全确定性（temp=0）有时让 LLM 退化为模板化回答，0.3 是经验最佳点

#### D3：为什么 daily quota = 30（vs v1 = 10）

Doubao 单次 ~$0.01-0.02（含缓存命中）→ 日上限：
- 30 calls × $0.02 ≈ **$0.30/day** (vs Claude $1/day)
- 即使 PA 误触发频繁 30 次，月成本 ~$10 完全可接受

#### D4：为什么 backend 选择器不直接删除 Claude 路径

预留 future-flip。当 Claude API key 配齐后：
1. 不修改 PA 调用方代码（仍是 `expert_escalate(question)`）
2. 仅改 `DEFAULT_BACKEND` 或加 backend priority
3. 历史 audit log 用 `backend` 字段区分两条路径的统计

#### D5：为什么用 stdlib urllib 不是 openai SDK

- 零新依赖（Mac Mini 不需要 `pip install`）
- 完全可控（超时、错误处理、连接复用）
- mockable（subclass override `_post()` 注入测试响应）
- Volcengine Ark 的 OpenAI 兼容已经过 V37.9.55 实测验证

#### D6：read-only contract 保持不变

- shell code fence (` ```bash ` 等)
- command substitution (`$(...)`, backticks)
- dangerous tokens (`rm -rf`, `sudo `, `mkfs`, fork bomb 等)
- 任一命中 → FAIL-CLOSE，proposal 拒绝 + 审计违规

Doubao 输出风格更"工程口语化"，但**read-only 验证是程序化 regex 扫描，与 LLM 无关**。

---

## 三、Trigger 机制（SOUL.md 规则 12）

CLAUDE.md 原则 #24：SOUL.md 触发词是唯一可靠的工具调用机制。规则 12 触发词与 v1 一致：

| 类别 | 触发词 |
|------|--------|
| **显式 escalate** | "让 Claude 看看" / "深度判断" / "需要 escalate" / "请让真人看看" |
| **不确定** | "你不太确定" / "拿不准" / "要不要专家看看" |
| **复杂取舍** | "这两个方案哪个好" / "应该优先做哪个" / "帮我决定" |
| **跨案例推理** | "这个跟之前 X 血案像吗" / "翻一下案例库" |
| **批判性** | "帮我反过来想" / "有什么风险" / "给我泼盆冷水" |

**v1 → r1 change**：tool 名 `claude_escalate` → `expert_escalate`，但 SOUL.md 表面仍可以说"让 Claude 看看"（用户视角不变）。内部 backend 是 Doubao，PA 转发时标注"来自 Doubao Seed 2.0 Pro（V37.9.55 verified reasoning model）"或"来自 expert 模型"。

---

## 四、输出契约 + 状态码

JSON 结构同 v1：

```json
{
  "proposal": "1-3 段 plain prose，无 shell 命令",
  "rationale": "1-2 段，cite 具体 status.json/changelog/case docs",
  "confidence": "high" | "medium" | "low",
  "refs": ["仅 provided context 中真实存在的路径/版本/case ID"]
}
```

`escalate()` 返回 status 类型：

| status | 含义 | PA 回复模板 |
|--------|------|-------------|
| `ok` | 成功 | "Doubao 建议：[proposal]。理由：[rationale]。置信度 [confidence]。" |
| `dry_run` | 测试模式 | （仅 dev/测试使用，PA 不应在生产看到）|
| `quota_exceeded` | 当日 ≥30 次 | "今日 expert 咨询配额已用完（30/30），建议直接联系开发者" |
| `api_unavailable` | Volcengine API 故障 / API key missing | "Doubao 暂不可用，我用基础模式回答" |
| `no_context` | 无任何上下文加载 | "无足够上下文，请提供更多细节" |
| `parse_failed` | Doubao 返回非 JSON | "Doubao 响应格式错误，重试一次" |
| `read_only_violation` | proposal 含 shell 命令 | "Doubao 给出不合规建议（含可执行命令），已自动拒绝" |
| `claude_pending` | backend=claude_pending | "Claude backend 暂未启用，请使用默认 Doubao 路径" |
| `unknown_backend` | backend 参数错误 | （PA 不应触发此分支）|

---

## 五、成本模型

### Volcengine Ark 定价（Doubao Seed 2.0 Pro，2026-05）

- Input: 约 $0.0008-0.001 / 1K tokens（人民币 ~5-7 元 / 1M tokens）
- Output: 约 $0.002-0.003 / 1K tokens
- Cached input: ~25% 输入价（自动命中，无需手动 cache_control）

### 单次调用成本

| 项 | Tokens | 单价 | 成本 |
|----|--------|------|------|
| System prompt + context block | ~5000 | $0.001/K | $0.005 |
| User question | ~100 | $0.001/K | $0.0001 |
| Output | ~1000 | $0.0025/K | $0.0025 |
| Reasoning content (Doubao 计费一部分) | ~500 | $0.0025/K | $0.00125 |
| **首调（无缓存）** | — | — | **~$0.009** |
| Context 缓存命中后 | ~4500 cached | $0.00025/K | $0.00112 |
| **后续调用** | — | — | **~$0.005** |

### 日上限

`max_daily=30`：
- 假设 1 次首调 + 29 次缓存：$0.009 + 29 × $0.005 = **~$0.16/day cap**
- 即使全 30 次都是首调（cache 失效）：30 × $0.009 = **~$0.27/day cap**

**对比 v1 Claude $1/day cap，r1 Doubao 节省 70-85%。**

---

## 六、Failure modes + mitigation

| 故障模式 | 触发 | Mitigation |
|----------|------|-------------|
| `no_context` | 三种 context 全部 unloadable | 返回明确状态 + PA fallback |
| `api_unavailable` | Volcengine 故障 / ARK_API_KEY missing | FAIL-CLOSE 不静默 Qwen3 fallback |
| `quota_exceeded` | 当日 ≥30 次 | PA 回 "配额用完" |
| `parse_failed` | Doubao 返回非 JSON | 返回错误（不 retry，留 V37.9.91+ 加 retry 机制）|
| `read_only_violation` | proposal 含 shell | FAIL-CLOSE 拒绝 + 审计 |
| `claude_pending` | backend 选择 claude_pending | 返回 stub |
| `unknown_backend` | backend 选择 unknown | 返回错误 |

---

## 七、Risks

| 风险 | 缓解 |
|------|------|
| **R1 Cost runaway** | daily quota 30 + per-call max_tokens 4000 + audit log 监控 |
| **R2 Prompt injection** | read-only validator + output 严格 JSON; 即使 prompt 被绕过, shell 仍被拒绝 |
| **R3 Volcengine API 限流** | FAIL-CLOSE 状态明确，不静默切 Qwen3 (避免 V37.4.3 PA 告警污染血案重演) |
| **R4 Doubao reasoning hallucination** | grounding 约束 (system prompt 严令 cite context) + read-only validator + 用户最终决定 |
| **R5 ARK_API_KEY 泄漏** | plist EnvironmentVariables (V37.9.54 INV-PLIST-ENV-001 守卫); 不入 git; CLAUDE.md push 前安全扫描 |
| **R6 国内网络抖动** | Doubao 国内 endpoint，理论上比 anthropic.com 更稳；timeout=60s 容忍长 reasoning |

---

## 八、Roadmap

### V37.9.90-r1（今日，已完成）

- ✅ `expert_escalation.py` (~520 行) + `test_expert_escalation.py` (89 单测 / 13 类)
- ✅ 设计文档（本文）
- ✅ SOUL.md 规则 12 更新（v1 → r1 backend description）
- ✅ full_regression.sh 名称更新

**不做（留 V37.9.91+）**：tool_proxy 集成 / Mac Mini 部署 / 真生产调用。

### V37.9.91+（下次 session）

1. **tool_proxy.py 集成**：proxy_filters.py 加 `expert_escalate` 自定义工具注入
2. **Mac Mini 部署**：
   - auto_deploy FILE_MAP 加 `expert_escalation.py`
   - 验证 ARK_API_KEY 已在 plist（V37.9.54 已配，仅 verify）
   - `pip install anthropic`（**不做** — Doubao 不需要）
3. **SOUL.md 规则 12 真激活**：PA 行为验证 + 第一次真生产调用
4. **观察一周**：cost / proposal 质量 / read-only 违规率 / cache hit rate

### V37.9.92+（一周观察后）

5. **INV-EXPERT-ESCALATION-001 立 governance**
6. **统计仪表板**：每周 escalation 调用统计
7. **Volcengine Context Cache 验证**：审计 `cache_read_input_tokens` 累计占比应 > 60%

### V37.9.95+（可选）

8. **Claude flip**（当 ANTHROPIC_API_KEY 配齐时）：
   - 加 `AnthropicTransport` class
   - 改 `DEFAULT_BACKEND` 或 backend priority list
   - 历史 audit 用 backend 字段对比 Doubao vs Claude proposal 质量
9. **Context 增强**：加 failure_modes_catalog.md / adversarial_chaos_audit 场景作可选 context
10. **Multi-turn**：如需多轮（"刚才说 X，那 Y 呢？"）评估 conversation history 设计

---

## 九、与 v1 (Claude SDK) 的差异总结

| 改动 | v1 | r1 |
|------|----|----|
| 文件名 | `claude_escalation.py` | `expert_escalation.py` |
| Tool 函数名 | `claude_escalate` | `expert_escalate`（用户视角可仍说"Claude 帮忙"）|
| 主要 backend | Anthropic SDK (Opus 4.7) | Volcengine Ark (Doubao Seed 2.0 Pro) |
| 第二 backend | Sonnet 4.6 fallback | 无（Claude 整体 pending）|
| 依赖 | `pip install anthropic` | 零新依赖（stdlib urllib）|
| 鉴权 env | `ANTHROPIC_API_KEY`（待申请）| `ARK_API_KEY`（已配 V37.9.54）|
| Prompt caching | manual `cache_control` ephemeral | Volcengine Context Cache **自动** |
| Thinking | `adaptive` + `effort: high` | `reasoning_content` 字段（自动）|
| Temperature | 不支持 | 0.3 显式 |
| Daily quota | 10 (~$1/day) | 30 (~$0.30/day) |
| Audit log | `~/.kb/audit/claude_escalations.jsonl` | `~/.kb/audit/expert_escalations.jsonl` |
| Backend selector | 无（多 model 优先级）| `backend="doubao"` / `"claude_pending"` |
| Mac Mini 准备 | 待 API key / pip install / 网络测试 | **零额外步骤**（V37.9.55 已就绪）|

---

## 十、一句话总结

V37.9.90 v1 (Claude SDK 设计) 的核心问题不在架构，而在**选错了 backend**——Claude 尚未集成 + 待付费 + 待网络验证；而 **Doubao 已经在生产跑了一周（V37.9.55+），cap_score=16，零额外配置**。

r1 用 Doubao 替换 Claude（保留 Claude future-flip 选项），同样的架构得到：
- **30x 更便宜的单次成本**
- **零新依赖**
- **零 Mac Mini 配置步骤**（V37.9.55 已就绪）
- **更稳定的国内网络**

这是 V37.9.83 "boundaries > trust" 原则的延伸——**先用已经验证的工具完成 PoC，等真有需求再激活 Claude（避免 over-engineering）**。

V37.9.83 思想升华文档第二原理"系统自我成长 = 用户视角机器化"在 framework 层的兑现路径**保持不变**：PA → escalate → 专家 LLM → 结构化 proposal → 用户决定。

只是"专家"换成了**已经付费、已经 verified、已经稳定运行**的 Doubao。

---

**附录 A — 模块文件清单**

```
expert_escalation.py      (~520 lines)  核心模块 + Doubao transport + CLI
test_expert_escalation.py (~700 lines)  89 单测 / 13 测试类
docs/articles/expert_escalation_design.md  (本文)
SOUL.md 规则 12          (~30 行)      PA 触发词契约
```

**附录 B — V37.9.90-r1 测试覆盖率**

- TestLoadStatus (4) / TestLoadChangelogWindow (6) / TestSelectRelevantCaseDocs (4)
- TestBuildContextBlock (4) / TestValidateReadOnly (10) / TestCheckDailyQuota (4)
- TestAuditRecord (4) / TestParseResponseJson (5)
- TestDoubaoTransport (11) — HTTP 契约 + Volcengine 响应解析 + 错误处理
- TestEscalateOrchestratorDoubao (11) — 端到端 + 7 类故障路径
- TestBackendSelector (4) — Doubao 默认 / claude_pending stub / unknown 拒绝
- TestCliInterface (3) — subprocess + Doubao 标识
- TestV37990R1SourceGuards (19) — V37.9.90-r1 marker + Volcengine 字面量 +
  zero-anthropic-import + Doubao constants + Context Cache 文档守卫

**合计**：89 单测，0 fail，覆盖 9 类 status 故障路径 + 5 个 drift 守卫维度。

---

🤖 Generated by Claude Opus 4.7 + 项目作者 (V37.9.90-r1, 2026-05-29)

# Complete Integration Guide / 完整接入指南

> Production-tested guide for connecting any LLM to OpenClaw via two-layer middleware.
> 基于生产验证的双层中间件接入指南，适用于任意大模型。

---

## Table of Contents / 目录

- [Architecture / 架构设计](#architecture--架构设计)
- [Core Challenges / 核心挑战](#core-challenges--核心挑战)
- [Tool Proxy / 工具代理层](#tool-proxy--工具代理层)
- [Adapter / 适配器层](#adapter--适配器层)
- [Configuration / 配置说明](#configuration--配置说明)
- [Quick Start / 快速开始](#quick-start--快速开始)
- [Validation / 验证测试](#validation--验证测试)
- [26 Hard-Won Lessons / 26条踩坑经验](#26-hard-won-lessons--26条踩坑经验)

---

## Architecture / 架构设计

```
WhatsApp
   ↕
OpenClaw Gateway  :18789   (npm global install)
   ↕
Tool Proxy        :5002    tool_proxy.py   ← This repo
   ↕
Adapter           :5001    adapter.py      ← This repo
   ↕
Remote GPU API              e.g. Qwen3-235B
```

**Why two layers? / 为什么要两层？**

OpenClaw Gateway speaks the OpenAI API format, but open-source models often choke on:
- Too many tools (24+ causes confusion)
- Complex tool schemas with nested objects
- Non-streaming responses when streaming is expected
- Unsupported parameters silently breaking inference

The two-layer design cleanly separates concerns:

| Layer | Responsibility |
|-------|---------------|
| Tool Proxy (5002) | Tool filtering, schema simplification, SSE conversion, request truncation |
| Adapter (5001) | Auth injection, model ID override, multimodal content stripping, parameter filtering |

OpenClaw Gateway 使用 OpenAI API 格式，但开源模型常见以下问题：
- 工具数量过多（24+个导致模型混乱）
- 复杂的嵌套工具 schema
- 需要流式输出但后端返回非流式
- 不支持的参数导致推理静默失败

双层设计职责分明，互不耦合。

---

## Core Challenges / 核心挑战

Integrating an open-source LLM with an agent framework like OpenClaw involves five non-obvious challenges:

将开源大模型接入 OpenClaw 这类 Agent 框架，有五个关键难点：

### 1. Tool Overload / 工具数量过多

OpenClaw exposes 24+ tools. Most open-source models degrade significantly beyond 12 tools — they start hallucinating tool names or refusing to call tools at all.

**Solution:** The Tool Proxy filters down to ≤12 allowed tools before the request reaches the model.

OpenClaw 默认暴露 24+ 个工具，但大多数开源模型超过 12 个后会出现工具幻觉或拒绝调用。

**解决方案：** Tool Proxy 在请求到达模型前过滤为 ≤12 个工具。

### 2. Schema Complexity / Schema 过于复杂

OpenClaw's tool schemas include nested objects, union types, and optional fields — formats that open-source models handle poorly.

**Solution:** The Tool Proxy replaces tool schemas with hand-crafted minimal versions that keep only required fields.

OpenClaw 的工具 schema 含嵌套对象、联合类型等复杂结构，开源模型处理能力弱。

**解决方案：** Tool Proxy 对每个工具替换为手写的最简 schema，只保留必要字段。

### 3. Streaming Format Mismatch / 流式格式不兼容

OpenClaw requires Server-Sent Events (SSE) streaming. Many GPU APIs return standard JSON responses, not SSE.

**Solution:** The Tool Proxy forces `stream: false` to the backend, receives the JSON response, then re-wraps it as SSE chunks before returning to OpenClaw.

OpenClaw 要求 SSE 流式输出，但很多 GPU API 仅返回标准 JSON。

**解决方案：** Tool Proxy 强制 `stream: false` 向后端请求，收到 JSON 后重新包装为 SSE chunks 返回。

### 4. Request Size Limits / 请求体大小限制

Long conversations can exceed the backend's payload limit (~280KB). Exceeding this causes opaque 500 errors.

**Solution:** The Tool Proxy truncates the oldest non-system messages to keep the request body under 200KB.

长对话的请求体可能超过后端 ~280KB 的限制，导致 500 错误且无明显提示。

**解决方案：** Tool Proxy 保留系统消息，从最旧的对话轮次开始截断，控制在 200KB 以内。

### 5. Parameter Incompatibility / 参数不兼容

Models may return tool calls with wrong parameter names (e.g. `file_path` instead of `path`, `cmd` instead of `command`). They may also inject unsupported parameters.

**Solution:** The Adapter strips unsupported request parameters. The Tool Proxy fixes common parameter name aliases in model responses.

模型可能返回错误的参数名（如 `file_path` 而非 `path`），也可能在请求中注入不支持的参数。

**解决方案：** Adapter 过滤请求中的非法参数，Tool Proxy 修复响应中常见的参数名别名。

---

## Tool Proxy / 工具代理层

**File:** `tool_proxy.py` | **Port:** 5002

### Request Pipeline / 请求处理流程

```
Incoming request from OpenClaw
        │
        ├─ Force stream=false (SSE handled on the way out)
        ├─ Truncate messages to ≤200KB
        ├─ Filter tools to allowed list (≤12)
        ├─ Replace complex schemas with clean minimal schemas
        └─ Forward to Adapter (5001)
                │
        Adapter returns JSON
                │
        ├─ Fix tool argument aliases (cmd→command, file_path→path, etc.)
        ├─ Fix browser profile field
        ├─ If original request was streaming: convert JSON → SSE chunks
        └─ Return to OpenClaw
```

### Allowed Tools / 允许的工具列表

```python
ALLOWED_TOOLS = {
    "web_search", "web_fetch",          # Web
    "read", "write", "edit",            # File operations
    "exec",                             # Shell execution
    "memory_search", "memory_get",      # Memory
    "cron", "message", "tts",           # Scheduling & messaging
}
ALLOWED_PREFIXES = ["browser"]          # Prefix match for browser_* tools
```

### Schema Simplification / Schema 简化

Each tool in `CLEAN_SCHEMAS` has a hand-written minimal schema. Example:

```python
"web_search": {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query string"}
    },
    "required": ["query"],
    "additionalProperties": False
}
```

### Parameter Alias Fixing / 参数别名修复

The proxy automatically remaps common model hallucinations:

| Tool | Wrong name | Correct name |
|------|-----------|--------------|
| `read` | `file_path`, `file`, `filepath`, `filename` | `path` |
| `exec` | `cmd`, `shell`, `bash`, `script` | `command` |
| `write` | `text`, `data`, `body`, `file_content` | `content` |
| `web_search` | `search_query`, `q`, `keyword` | `query` |

---

## Adapter / 适配器层

**File:** `adapter.py` | **Port:** 5001

### What it does / 功能说明

1. **Auth injection** — Adds `Authorization: Bearer $REMOTE_API_KEY` header
2. **Model ID override** — Forces the correct model ID regardless of what OpenClaw sends
3. **Multimodal stripping** — Converts list-format content (image + text) to plain text, since Qwen3 vision is not enabled
4. **Parameter filtering** — Only forwards parameters the remote API actually supports
5. **User-Agent spoofing** — Sets `User-Agent: curl/8.0` to avoid bot-blocking

---

1. **认证注入** — 自动添加 `Authorization: Bearer $REMOTE_API_KEY` 请求头
2. **模型 ID 强制** — 无论 OpenClaw 发送什么，强制使用正确的模型 ID
3. **多模态内容剥离** — 将列表格式的 content（含图片）转换为纯文本
4. **参数过滤** — 仅转发远端 API 支持的参数
5. **User-Agent 伪装** — 设置为 `curl/8.0` 避免被反爬拦截

### Allowed Parameters / 允许的参数

```python
ALLOWED_PARAMS = {
    "model", "messages", "max_tokens", "temperature", "top_p",
    "stream", "stop", "tools", "tool_choice", "n",
    "presence_penalty", "frequency_penalty", "seed"
}
```

---

## Configuration / 配置说明

### Environment Variables / 环境变量

```bash
# ~/.zshrc or ~/.bashrc
export REMOTE_API_KEY="your-api-key-here"
```

**Never hardcode API keys in source files. Always use environment variables.**

**永远不要在源码中硬编码 API Key，必须使用环境变量。**

### Model ID Rules / 模型 ID 规则

| Location | Format |
|----------|--------|
| `adapter.py` | Bare ID, e.g. `Qwen3-235B-A22B-Instruct-2507-W8A8` |
| `tool_proxy.py` | Bare ID (same as above) |
| `openclaw.json` `agents.defaults.model.primary` | **Must include `qwen-local/` prefix** |
| `jobs.json` cron tasks | **Do not specify** — inherit default |

### Hard Limits / 硬性限制

| Limit | Value | Reason |
|-------|-------|--------|
| Max tools | 12 | More causes model confusion |
| Tool calls per task | ≤ 2 | More causes exponential timeout risk |
| Request body | ≤ 200KB | Buffer before the 280KB hard limit |

---

## Quick Start / 快速开始

### 1. Install dependencies / 安装依赖

```bash
pip install flask requests
```

### 2. Configure / 配置

```bash
# Set your API key
export REMOTE_API_KEY="your-remote-api-key"

# Edit adapter.py: verify REAL_MODEL_ID matches your backend
# Edit tool_proxy.py: adjust ALLOWED_TOOLS if needed
```

### 3. Start services / 启动服务

```bash
nohup python3 adapter.py > adapter.log 2>&1 &
nohup python3 tool_proxy.py > tool_proxy.log 2>&1 &
```

### 4. Point OpenClaw to the proxy / 配置 OpenClaw 指向代理

In `~/.openclaw/openclaw.json`:

```json
{
  "models": {
    "providers": {
      "qwen-local": {
        "baseUrl": "http://127.0.0.1:5002/v1",
        "apiKey": "any-non-empty-string",
        "api": "openai-completions",
        "models": [{
          "id": "YOUR-MODEL-ID",
          "name": "Your Model Name",
          "contextWindow": 131072,
          "maxTokens": 8192
        }]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "qwen-local/YOUR-MODEL-ID" }
    }
  }
}
```

### 5. One-command restart / 一键重启

```bash
bash restart.sh
```

---

## Validation / 验证测试

### Health check / 健康检查

```bash
curl http://localhost:5002/health
curl http://localhost:5001/health
```

### Verify model ID / 验证模型 ID

```bash
curl -s https://your-api-endpoint/v1/models \
  -H "Authorization: Bearer $REMOTE_API_KEY" \
  | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data']]"
```

### Watch logs / 查看日志

```bash
tail -f ~/tool_proxy.log    # Tool calls, filtering, SSE conversion
tail -f ~/adapter.log       # API forwarding, auth, parameter filtering
```

### End-to-end test via WhatsApp / 端到端测试

Send to your WhatsApp number: `你好` — model should reply directly without onboarding prompts.

发送"你好"到 WhatsApp，模型应直接回复，不应出现 onboarding 欢迎语。

---

## 26 Hard-Won Lessons / 26条踩坑经验

Lessons learned from production operation. Read these before debugging.

生产运营中积累的踩坑经验，排查问题前请先阅读。

| # | Lesson / 经验 |
|---|--------------|
| 1 | **Tools ≤ 12** — More causes hallucination or refusal. Hard limit, not a guideline. / 工具超过12个导致幻觉或拒绝调用，这是硬限制。 |
| 2 | **Tool calls per task ≤ 2** — Each extra call multiplies timeout probability exponentially. / 每任务调用超过2次，超时风险指数级上升。 |
| 3 | **Request body ≤ 200KB** — The 280KB hard limit gives no useful error message when exceeded. / 超出280KB限制时后端无有效报错，200KB是安全线。 |
| 4 | **Force `stream: false` to backend** — Re-wrap as SSE yourself for reliable streaming. / 强制非流式请求后端，自行重新封装为SSE，可靠性更高。 |
| 5 | **Strip all unsupported parameters** — Unknown params silently break inference on many APIs. / 未知参数会静默破坏推理，必须过滤。 |
| 6 | **Replace complex schemas** — Nested objects and union types cause open-source models to hallucinate field names. / 复杂schema导致模型幻觉字段名，必须手写最简版本。 |
| 7 | **Model ID changes without warning** — Check remote model ID weekly. Build a health check. / 远端模型ID会无预警变更，需要每周检查并建立健康检查机制。 |
| 8 | **Multiple tasks failing simultaneously → check model ID first** — This is almost always the root cause. / 多任务同时失败的第一反应：检查模型ID。 |
| 9 | **`openclaw.json` model.primary must include provider prefix** — `qwen-local/MODEL-ID`, not just `MODEL-ID`. / `openclaw.json`中的模型ID必须带提供商前缀。 |
| 10 | **`jobs.json` cron tasks must NOT specify model** — Let them inherit the default. Specifying causes routing failures. / cron任务不指定model，继承默认值，否则路由失败。 |
| 11 | **Never use `--thinking none`** — Valid values are: `off, minimal, low, medium, high, adaptive`. `none` is rejected with a cryptic error. / `--thinking none`是非法值，合法值为`off, minimal`等。 |
| 12 | **All cron `openclaw agent` calls need `--session isolated`** — Shared sessions cause 502 deadlocks when Gateway is busy. / cron中的agent调用必须加`--session isolated`，避免502死锁。 |
| 13 | **Session cleanup every 6 hours, not daily** — Daily cleanup leaves too much history; sessions.json must also be deleted. / session清理每6小时一次，且必须同时删除sessions.json。 |
| 14 | **macOS BSD `sed` does not support `\|` for OR** — Use Python for any complex text substitution on macOS. / macOS的sed不支持`\|`，复杂替换用Python。 |
| 15 | **System crontab vs openclaw cron** — Use openclaw's built-in cron only for tasks that need LLM. Use system crontab for pure shell tasks. / 纯Shell任务用系统crontab，需要LLM的任务才用openclaw内建cron。 |
| 16 | **Use `bash -lc` in crontab** — Loads the full login environment including `$HOME`, `$PATH`, and custom exports. / crontab中用`bash -lc`加载完整登录环境，避免环境变量丢失。 |
| 17 | **Test before registering cron** — Always run a script manually and verify output before `openclaw cron add`. / 新脚本必须手动验证后才能注册为cron任务。 |
| 18 | **Silent errors are worse than noisy ones** — A script that reports success without verifying results is a liability. Check return codes and output content explicitly. / 静默错误比报错更危险，脚本必须主动验证结果，不能只确认调用完成。 |
| 19 | **LLM raw output logging** — Save every LLM call's raw stdout+stderr to a debug file (`llm_raw_last.txt`). It's the only way to diagnose model failures. / 每次LLM调用的原始输出（含stderr）必须保存到调试文件，否则无法定位模型失败原因。 |
| 20 | **Parse success rate < 50% → alert and exit** — Never push business content when the model output is mostly garbage. / 解析成功率低于50%时主动告警并退出，不推送业务内容。 |
| 21 | **`subprocess capture_output=True` hides stderr** — LLM call errors won't surface unless you explicitly log stderr. / `capture_output=True`会隐藏stderr，必须显式记录。 |
| 22 | **API keys in environment variables, never in source** — Even "private" repos get leaked. Use `os.environ.get("KEY")`. / API Key必须用环境变量，即使是私有仓库也会泄露。 |
| 23 | **Phone numbers in public repos → use placeholders** — Scan for real numbers before every push. / 公开仓库中手机号必须用占位符，每次push前扫描。 |
| 24 | **Context window size is the least important model metric** — A 262K context model that can't reliably call 3 tools is worse than a 8K model that can. / 上下文窗口大小是最不重要的模型指标，工具调用可靠性才是关键。 |
| 25 | **Pure inference tasks must bypass Gateway** — Use direct `curl` to `proxy:5002/v1/chat/completions` without `tools` in the payload. `openclaw agent` injects tools via Gateway, causing models like Qwen3 to enter infinite tool-call loops (e.g., 29× `web_search` until 600s timeout). / 纯推理任务（不需要工具）必须绕过Gateway直接调API，`openclaw agent`会注入工具导致模型失控循环调用（如29次web_search直到超时）。← #94 |
| 26 | **End-of-day full doc sync is mandatory** — When user says "今天工作结束", scan ALL docs (CLAUDE.md, docs/*.md, README.md, IMPROVEMENTS.md, etc.) and sync every change made during the session. Ensure consistency across work principles, lessons, checklists, and todo status. Security scan → commit → push. / 每日收工时必须扫描全部文档同步当日变更，确保工作原则、经验、清单、待办状态跨文档一致。 |

---

## Security Checklist / 安全检查清单

Run before every `git push`:

每次 `git push` 前必须执行：

```bash
# Check for leaked API keys / 检查泄露的API Key
grep -r "sk-[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" --include="*.json" | grep -v ".git"

# Check for other API keys / 检查其他密钥
grep -r "BSA[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" --include="*.json" | grep -v ".git"

# All output must be empty before pushing / 全部为空才允许push
```

---

## License / 许可证

MIT — See [LICENSE](../LICENSE)

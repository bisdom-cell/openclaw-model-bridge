# Provider Compatibility Matrix

> 自动生成：`python3 providers.py` | 最后更新：2026-04-03
> 数据来源：`providers.py` — Provider Compatibility Layer (V34)

---

## 支持的 Provider

| Provider | Default Model | VL Model | Auth | Base URL |
|----------|--------------|----------|------|----------|
| Qwen (Remote GPU) | Qwen3-235B-A22B-Instruct-2507-W8A8 | Qwen2.5-VL-72B-Instruct | Bearer | hkagentx.hkopenlab.com |
| OpenAI | gpt-4o | — | Bearer | api.openai.com |
| Google Gemini | gemini-2.5-flash | — | Bearer | generativelanguage.googleapis.com |
| Anthropic Claude | claude-sonnet-4-6 | — | x-api-key | api.anthropic.com |

## 能力矩阵

| Provider | Text | Vision | Audio | Video | Tool Calling | Streaming | JSON Mode | Context Window |
|----------|------|--------|-------|-------|-------------|-----------|-----------|---------------|
| Qwen (Remote GPU) | Yes | Yes | — | — | Yes | Yes | — | 262K |
| OpenAI | Yes | Yes | Yes | — | Yes | Yes | Yes | 128K |
| Google Gemini | Yes | Yes | — | — | Yes | Yes | Yes | 1048K |
| Anthropic Claude | Yes | Yes | — | — | Yes | Yes | — | 200K |

## 验证状态

> "Verified" = 在生产环境中实际测试并确认功能正常

| Provider | 角色 | Text | Vision | Tool Calling | Streaming | Fallback |
|----------|------|------|--------|-------------|-----------|----------|
| **Qwen** | Primary | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| **Gemini** | Fallback | :white_check_mark: | — | — | — | :white_check_mark: |
| OpenAI | Available | — | — | — | — | — |
| Claude | Available | — | — | — | — | — |

### 验证详情

**Qwen (Primary Provider)** — 5/5 verified
- Text: 日常 WhatsApp 对话，262K context 稳定运行
- Vision: Qwen2.5-VL-72B 图片理解，自动路由
- Tool Calling: 12 工具 + 2 自定义工具（data_clean, search_kb）
- Streaming: SSE 转换，非流式→流式
- Fallback: 作为 primary 被 Gemini fallback，电路断路器验证

**Gemini (Fallback Provider)** — 2/5 verified
- Text: 作为 fallback 处理降级请求
- Fallback: 在 Qwen 超时/故障时自动接管，60s 超时

**OpenAI / Claude** — 0/5 verified
- 注册表中已配置，但未在生产环境验证
- 可通过 `PROVIDER=openai` 或 `PROVIDER=claude` 环境变量切换

## 部署配置

### 当前生产配置

```bash
# Primary
PROVIDER=qwen
MODEL_ID=Qwen3-235B-A22B-Instruct-2507-W8A8
VL_MODEL_ID=Qwen2.5-VL-72B-Instruct

# Fallback
FALLBACK_PROVIDER=gemini
FALLBACK_MODEL_ID=gemini-2.5-flash

# Smart Routing (目前禁用)
FAST_PROVIDER=    # 空 = 禁用
```

### 切换 Provider

```bash
# 切换到 OpenAI
export PROVIDER=openai
export OPENAI_API_KEY=sk-...

# 切换到 Claude
export PROVIDER=claude
export ANTHROPIC_API_KEY=sk-ant-...

# 重启 adapter
bash restart.sh
```

## 降级路径

```
Qwen3-235B (Primary, 5min timeout)
    ↓ 失败 / 超时 / 电路断路
Gemini 2.5 Flash (Fallback, 1min timeout)
    ↓ 也失败
502 Error (两个错误信息一起返回)
```

**电路断路器**：连续 5 次失败后自动短路，直接走 Fallback。300 秒后半开尝试恢复。

## 添加新 Provider

```python
# 在 providers.py 中添加：
class MyProvider(BaseProvider):
    name = "my_provider"
    display_name = "My Custom Provider"
    base_url = "https://api.example.com/v1"
    api_key_env = "MY_API_KEY"
    auth_style = "bearer"  # 或 "x-api-key"
    models = [
        ModelInfo(
            model_id="my-model-v1",
            display_name="My Model v1",
            modalities=["text"],
            context_window=32768,
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        tool_calling=True,
        streaming=True,
    )

# 注册到默认注册表
_default_registry.register(MyProvider())
```

然后：
```bash
export PROVIDER=my_provider
export MY_API_KEY=...
bash restart.sh
```

## 工具模式验证

| 模式 | Qwen | Gemini | OpenAI | Claude |
|------|------|--------|--------|--------|
| 单工具调用 | :white_check_mark: | — | — | — |
| 多工具并行 | :white_check_mark: | — | — | — |
| 自定义工具拦截 | :white_check_mark: | — | — | — |
| Schema 简化 | :white_check_mark: | — | — | — |
| 参数修复/别名映射 | :white_check_mark: | — | — | — |

---

*此文档由 `providers.py` 的能力声明驱动，`python3 providers.py --json` 可获取机器可读版本。*

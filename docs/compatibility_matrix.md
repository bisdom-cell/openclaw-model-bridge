# Provider Compatibility Matrix

> 数据真理源：`providers.py`（`python3 providers.py` 人读 / `--json` 机读 / `--capability-matrix` 能力矩阵直出）| 最后刷新：2026-06-12（v37.9.143）
> **8 Providers**（7 built-in + 1 plugin）。**漂移防护已接入（V37.9.143）**：本文档的两张机器表（"支持的 Provider" + "能力矩阵"）由 `gen_compat_matrix.py --check` 在 full_regression doc-drift 层守卫，漂移时 CI 失败；`--fix` 一键重写。人工段落（验证档位 / Fallback 路径 / 工具模式验证）不参与机器比对。

---

## 支持的 Provider

| Provider | Models | Modalities | Tool Calling | Streaming | Context | Verified |
|----------|--------|------------|-------------|-----------|---------|----------|
| Qwen (Remote GPU) | Qwen3-235B-A22B-Instruct-2507-W8A8, Qwen2.5-VL-72B-Instruct | text, vision | Yes | Yes | 262K | text, vision, tool_calling, streaming, fallback |
| OpenAI | gpt-4o | text, vision, audio | Yes | Yes | 128K | none |
| Google Gemini | gemini-2.5-flash | text, vision | Yes | Yes | 1048K | text, fallback |
| Anthropic Claude | claude-sonnet-4-6 | text, vision | Yes | Yes | 200K | none |
| Kimi (Moonshot AI) | kimi-k2.5 | text, vision | Yes | Yes | 262K | none |
| MiniMax | MiniMax-M2.7 | text, vision | Yes | Yes | 204K | none |
| GLM (Zhipu AI) | glm-5, glm-5v-turbo | text, vision | Yes | Yes | 202K | none |
| Doubao Seed 2.0 Pro (Volcengine Ark) | doubao-seed-2-0-pro | text, vision | Yes | Yes | 262K | text, vision, tool_calling, streaming, reasoning |

插件接入：Doubao 经 `providers.d/doubao_provider.py`（V37 Provider Plugin Interface，V37.9.52 接入）。

## 验证档位（诚实标注，外部评审 2026-06-11 建议采纳）

> 四档语义：**production_observed**（真实生产流量运行过）> **feature_verified**（分项 E2E 实测通过）> **smoke_tested**（最小 text 调用通过）> **declared**（能力仅来自文档/配置声明，未实测）。"支持" ≠ "生产验证"。

| Provider | 档位 | 依据 |
|----------|------|------|
| Qwen (Remote GPU) | **production_observed** | 主力 provider，全部生产流量（V27 起）；5 capability 实测 |
| Doubao Seed 2.0 Pro | **production_observed** | fallback 链第 1 位真实接管（V37.9.129 起唯一真 fallback）+ expert_escalate 真生产调用（V37.9.91）；text/vision/tool_calling/streaming/reasoning 5/5 E2E 实测（V37.9.53-55） |
| Google Gemini | **production_observed（已退役出 fallback 链）** | 曾为生产 fallback 真 fire（V37.8.10 等）；**V37.9.129 实证香港 geo-block（HTTP 400 User location is not supported）永久退役**，`config.yaml fallback.exclude_providers: [gemini]` |
| OpenAI / Claude / Kimi / MiniMax / GLM | **declared** | 能力声明完整 + 合约校验通过，0/N 生产验证（无 API key 配置） |

## 能力矩阵

| Provider | Text | Vision | Audio | Video | Tool Calling | Streaming | JSON Mode | Reasoning | Context Window |
|----------|------|--------|-------|-------|-------------|-----------|-----------|-----------|---------------|
| Qwen (Remote GPU) | Yes | Yes | — | — | Yes | Yes | — | — | 262K |
| OpenAI | Yes | Yes | Yes | — | Yes | Yes | Yes | — | 128K |
| Google Gemini | Yes | Yes | — | — | Yes | Yes | Yes | — | 1048K |
| Anthropic Claude | Yes | Yes | — | — | Yes | Yes | — | — | 200K |
| Kimi (Moonshot AI) | Yes | Yes | — | — | Yes | Yes | Yes | — | 262K |
| MiniMax | Yes | Yes | — | — | Yes | Yes | Yes | — | 204K |
| GLM (Zhipu AI) | Yes | Yes | — | — | Yes | Yes | Yes | — | 202K |
| Doubao Seed 2.0 Pro (Volcengine Ark) | Yes | Yes | — | — | Yes | Yes | Yes | Yes | 262K |

> Reasoning 维度 V37.9.53 新增（doubao seed reasoning model 实证驱动）。cap_score: doubao 16 > Qwen3 14（framework 视角 doubao 是 registry 最强 provider，V37.9.55）。

## Fallback 降级路径（V37.9.129 现状）

```
Qwen3-235B (Primary, 300s timeout)
    ↓ 失败 / 超时 / 电路断路 (连续 5 次失败 open, 300s 后 half-open)
Doubao Seed 2.0 Pro (Fallback, 300s timeout — V37.9.129: 60s→300s 给大请求足够时间)
    ↓ 也失败
502 Error (完整 upstream 错误链一起返回, V37.8.10 compose_backend_error_str)
```

- **Gemini 不在链中**：V37.9.129 实证香港 geo-block 后经 `fallback.exclude_providers` 永久排除（key 保留, 地理不可达）。`available`（有 key）≠ `working`（地理可达）。
- 电路断路器参数中心化于 `config.yaml`：`circuit_breaker_threshold: 5` / `circuit_breaker_reset_seconds: 300`。
- fallback 链由 `ProviderRegistry.build_fallback_chain(require_available=True)` 按 cap_score 自动推导（V37 capability routing），非硬编码。

## 添加新 Provider

**首选：插件方式（V37 Provider Plugin Interface，零核心代码改动）** — 在 `providers.d/` 放 YAML 或 Python 文件即自动发现，详见 `docs/provider_plugin_guide.md`（60 秒接入）。真实样例：`providers.d/doubao_provider.py`（第 8 个 provider 即此路径接入）。

```yaml
# providers.d/my_provider.yaml
name: my_provider
display_name: My Custom Provider
base_url: https://api.example.com/v1
api_key_env: MY_API_KEY
auth_style: bearer
models:
  - model_id: my-model-v1
    modalities: [text]
    context_window: 32768
    is_default: true
capabilities:
  text: true
  tool_calling: true
  streaming: true
```

也可继承 `BaseProvider` 写 Python 插件（需要动态逻辑时，如 Doubao 从 env 读 endpoint ID）。注册后：

```bash
python3 providers.py --validate
export MY_API_KEY=...
bash restart.sh
```

## 工具模式验证

| 模式 | Qwen | Doubao | Gemini | OpenAI | Claude | Kimi | MiniMax | GLM |
|------|------|--------|--------|--------|--------|------|---------|-----|
| 单工具调用 | :white_check_mark: | :white_check_mark: (V37.9.55) | — | — | — | — | — | — |
| 多工具并行 | :white_check_mark: | — | — | — | — | — | — | — |
| 自定义工具拦截 | :white_check_mark: | — | — | — | — | — | — | — |
| Schema 简化 | :white_check_mark: | — | — | — | — | — | — | — |
| 参数修复/别名映射 | :white_check_mark: | — | — | — | — | — | — | — |

---

*此文档由 `providers.py` 的能力声明驱动，`python3 providers.py --json` 可获取机器可读版本。人工段落（验证档位/Fallback 路径）的事实锚点：config.yaml + V37.9.129/V37.9.55 changelog。*

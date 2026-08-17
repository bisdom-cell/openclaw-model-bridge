# Provider Compatibility Matrix

> 数据真理源：`providers.py`（`python3 providers.py` 人读 / `--json` 机读 / `--capability-matrix` 能力矩阵直出 / `--tier-matrix` 验证档位直出）| 最后刷新：2026-08-17（v37.9.312）
> **12 Providers**（7 built-in + 5 plugins：Doubao×2 + DeepSeek×2 + GLM-5.2 coding）。**漂移防护已接入（V37.9.143 → V37.9.146）**：本文档的三张机器表（"支持的 Provider" + "验证档位" + "能力矩阵"）由 `gen_compat_matrix.py --check` 在 full_regression doc-drift 层守卫，漂移时 CI 失败；`--fix` 一键重写。人工段落（Fallback 路径 / 添加新 Provider / 工具模式验证）不参与机器比对。

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
| DeepSeek-V4-Pro 满血版 (ai-tokenhub) | deepseek-v4-pro-huakun | text | Yes | Yes | 1048K | text, tool_calling, reasoning |
| DeepSeek-V4-Pro | DeepSeek-V4-Pro | text | Yes | Yes | 65K | text, tool_calling, streaming |
| Doubao Seed 2.0 Pro (ai-tokenhub) | doubao-seed-2.0-pro-huakun | text, vision | Yes | Yes | 262K | text, reasoning |
| Doubao Seed 2.1 Pro (Volcengine Ark) | doubao-seed-2-1-pro-260628 | text, vision | Yes | Yes | 262K | text, vision, tool_calling, streaming, reasoning |
| GLM-5.2 Coding (ai-tokenhub) | glm-5.2-huakun | text | Yes | Yes | 131K | text, reasoning |

插件接入：Doubao 经 `providers.d/doubao_provider.py`（V37 Provider Plugin Interface，V37.9.52 接入）。

## 验证档位

> **字段化已接入（V37.9.146，外部评审2 P2(a)）**：本表由 `providers.py` 的 `verification_tier` 字段直出（`--tier-matrix`），退役 V37.9.142 手写表 = 一物一形，`gen_compat_matrix.py --check` 守卫漂移。诚实标注 "支持" ≠ "生产验证"。
> 四档语义：**production_observed**（真实生产流量运行过）> **feature_verified**（分项 E2E 实测通过）> **smoke_tested**（最小 text 调用通过）> **declared**（能力仅来自文档/配置声明，未实测）。tier 声明与 `verified_*` 布尔由 `--check-tiers` 守卫可证一致（防"改了 verified_* 忘改 tier"漂移）。

| Provider | 档位 | 依据 |
|----------|------|------|
| Qwen (Remote GPU) | **production_observed** | V27 起承载全部生产流量至 2026-07（V37.9.222 primary flip 至 doubao_21 后转 fallback 兜底）；5 capability 实测 |
| OpenAI | **declared** | 能力声明完整 + 合约校验通过，0/N 生产验证（无 API key 配置） |
| Google Gemini | **production_observed**（已退役出 fallback 链） | 曾为生产 fallback 真 fire（V37.8.10 等）；V37.9.129 实证香港 geo-block 永久退役，config.yaml fallback.exclude_providers: [gemini] |
| Anthropic Claude | **declared** | 能力声明完整 + 合约校验通过，0/N 生产验证（无 API key 配置） |
| Kimi (Moonshot AI) | **declared** | 能力声明完整 + 合约校验通过，0/N 生产验证（无 API key 配置） |
| MiniMax | **declared** | 能力声明完整 + 合约校验通过，0/N 生产验证（无 API key 配置） |
| GLM (Zhipu AI) | **declared** | 能力声明完整 + 合约校验通过，0/N 生产验证（无 API key 配置） |
| DeepSeek-V4-Pro 满血版 (ai-tokenhub) | **feature_verified** | Mac Mini E2E 实测 2026-06-30 (时为 model=deepseek-v4-pro-260425): text/tool_calling/reasoning 3/3 通过 (干净中文+finish_reason / finish_reason=tool_calls+arguments / reasoning 字段填充+reasoning_tokens=55 R1 通道)；无乱码 token (优于量化版 w4a8)；vision 实测不支持 (400) / json_mode 围栏非严格 / streaming 未单测 / 未真生产 fallback。2026-08-07 model ID 更名 deepseek-v4-pro-huakun (V37.9.289, 同端点同 key), 更名后 E2E 待 Mac Mini 复测 |
| DeepSeek-V4-Pro | **feature_verified** | Mac Mini E2E 实测 2026-06-30: text/streaming/tool_calling/json_mode 4/4 通过 (content+finish_reason / SSE chunk+[DONE] / finish_reason=tool_calls+arguments / response_format=json_object 干净 JSON)；vision 实测不支持 (400 非多模态) / reasoning 无 R1 reasoning_content 通道 / 未真生产 fallback 接管。部署=w4a8-mtp 量化, 推理响应偶发乱码 token |
| Doubao Seed 2.0 Pro (ai-tokenhub) | **feature_verified**（2026-08-08 平台切换 ai-tokenhub 后 E2E 半升档 (text+reasoning)） | ai-tokenhub E2E 探针 2026-08-08 (V37.9.291): text+reasoning 2/2 通过 (200 + finish_reason=stop + 正确 content + reasoning 字段完整 reasoning_tokens=263, 响应 model 回显 doubao-seed-2.0-pro = 别名路由正确; 注意 tokenhub 字段名 reasoning 非 Ark 的 reasoning_content)；vision/tool_calling/streaming 未经 tokenhub 实测保持 False; Ark 时代 production_observed 史见 docstring |
| Doubao Seed 2.1 Pro (Volcengine Ark) | **production_observed** | 唯一 primary 承载全部生产流量 2026-07-02 起 (V37.9.222 B1 flip, 22+ 天 cron 周期零 provider 事故); B1 批量 thinking-off 注入实测大规模开火 (adapter.log 7654 次 @2026-07-24, dream curl 超时 0 复发); E2E 2026-07-02: text/vision/tool_calling/streaming/reasoning 5/5 (bat-ball 0.05 / vision 全命中 / tool_calls / chunk+[DONE] / reasoning_tokens=255) 无乱码；json_mode 声明未单测 / 未真生产 fallback 接管 |
| GLM-5.2 Coding (ai-tokenhub) | **feature_verified**（2026-08-08 平台切回 ai-tokenhub 后 E2E 半升档 (text+reasoning)） | ai-tokenhub E2E 探针 2026-08-08 (V37.9.291): text+reasoning 2/2 通过 (200 + finish_reason=stop + 正确 print 代码 + reasoning 字段真实推理链, 响应 model 回显 glm-5.2 = 别名路由正确; reasoning 为 tokenhub 新发现 — Ark ep- 时代 reasoning_tokens=0 无通道, V37.9.258 曾据此判 False)；tool_calling/streaming 未经 tokenhub 实测保持 False (Ark 时代史见 docstring) |

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
| DeepSeek-V4-Pro 满血版 (ai-tokenhub) | Yes | — | — | — | Yes | Yes | — | Yes | 1048K |
| DeepSeek-V4-Pro | Yes | — | — | — | Yes | Yes | Yes | — | 65K |
| Doubao Seed 2.0 Pro (ai-tokenhub) | Yes | Yes | — | — | Yes | Yes | Yes | Yes | 262K |
| Doubao Seed 2.1 Pro (Volcengine Ark) | Yes | Yes | — | — | Yes | Yes | Yes | Yes | 262K |
| GLM-5.2 Coding (ai-tokenhub) | Yes | — | — | — | Yes | Yes | — | Yes | 131K |

> Reasoning 维度 V37.9.53 新增（doubao seed reasoning model 实证驱动）。cap_score: doubao_21 16 登顶 registry（V37.9.290 后 doubao 2.0 迁 ai-tokenhub 复测为 10；Qwen3 14；framework 视角 doubao_21 是 registry 最强 provider）。

## Fallback 降级路径（V37.9.222 现状）

```
Doubao Seed 2.1 Pro (Primary = PROVIDER env, 300s timeout)
    ↓ 失败 / 超时 / 电路断路 (连续 5 次失败 open, 300s 后 half-open)
DeepSeek-V4-Pro 满血 → Doubao 2.0 → DeepSeek 量化 → Qwen3-235B
    (FALLBACK_ORDER env 显式有序链, V37.9.218; 逐级降, image 请求自动跳过纯文本 provider)
    ↓ 全链失败
502 Error (完整 upstream 错误链一起返回, V37.8.10 compose_backend_error_str)
```

- **Gemini 不在链中**：V37.9.129 实证香港 geo-block 后经 `fallback.exclude_providers` 永久排除（key 保留, 地理不可达）。`available`（有 key）≠ `working`（地理可达）。
- 电路断路器参数中心化于 `config.yaml`：`circuit_breaker_threshold: 5` / `circuit_breaker_reset_seconds: 300`。
- fallback 链权威 = `FALLBACK_ORDER` env（显式有序，V37.9.218；primary 自动排除 + 无 key/geo-block 跳过）；`build_fallback_chain()` cap_score 自动推导仅作 env 未设时的兜底。

## 添加新 Provider

**首选：插件方式（V37 Provider Plugin Interface，零核心代码改动）** — 在 `providers.d/` 放 YAML 或 Python 文件即自动发现，详见 `docs/provider_plugin_guide.md`（60 秒接入）。真实样例：`providers.d/doubao_provider.py` 与 `providers.d/deepseek_full_provider.py`（均经此路径接入）。

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

| 模式 | Qwen | doubao_21 | Doubao 2.0 | DeepSeek 满血 | DeepSeek 量化 | GLM-5.2 coding | Gemini | 其余 built-in |
|------|------|-----------|-----------|--------------|--------------|----------------|--------|--------------|
| 单工具调用 | :white_check_mark: | :white_check_mark: (V37.9.217) | :white_check_mark: (V37.9.55, Ark 时代) | :white_check_mark: (V37.9.205) | :white_check_mark: (V37.9.202) | :white_check_mark: (V37.9.258) | ~~退役~~ | — |
| 多工具并行 | :white_check_mark: | — | — | — | — | — | — | — |
| 自定义工具拦截 | :white_check_mark: | :white_check_mark: (生产 primary) | — | — | — | — | — | — |
| Schema 简化 | :white_check_mark: | :white_check_mark: (生产 primary) | — | — | — | — | — | — |
| 参数修复/别名映射 | :white_check_mark: | :white_check_mark: (生产 primary) | — | — | — | — | — | — |

---

*此文档由 `providers.py` 的能力声明驱动，`python3 providers.py --json` 可获取机器可读版本。三张机器表（支持的 Provider / 验证档位 / 能力矩阵）由 `gen_compat_matrix.py --check` 守卫；人工段落（Fallback 路径 / 工具模式验证）的事实锚点：`FALLBACK_ORDER`/`PROVIDER` env（V37.9.218/222）+ 各 provider E2E changelog（V37.9.202/205/217/258）。*

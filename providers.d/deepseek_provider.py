"""V37.9.201 — DeepSeek-V4-Pro Provider (用户自建 OpenAI 兼容推理端点)
   V37.9.202 — Mac Mini E2E 实测: text/streaming/tool_calling 3/3 通过 → feature_verified

接入第 9 个 provider: DeepSeek-V4-Pro (self-hosted OpenAI-compatible gateway).

🔴 设计契约 (镜像 doubao_provider.py V37.9.52 公开 repo 安全底线):
- **API key 严格走 env `DEEPSEEK_API_KEY`** — 绝不硬编码。用户在对话里贴的明文 key
  (sk-...) 不入库, 即便用户豁免也守公开 repo 安全底线。
- **base_url 走 env `DEEPSEEK_BASE_URL`** — 用户端点是【裸 IP + 路径 token】= 类机密,
  本仓库是公开仓库 → 不入库。dev 无 env 时 fallback 到公开 `https://api.deepseek.com/v1`
  让 ProviderContract 通过 + `ProviderRegistry.available()` 因缺 `DEEPSEEK_API_KEY`
  自动排除 (与 doubao endpoint-id fallback 同一模式)。Mac Mini 配 env → 真实私有端点注入。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = feature_verified** (V37.9.202) — 分项 E2E 实测通过但未真生产流量。
- **Mac Mini E2E 实测 2026-06-30 (3/3 通过)**:
    text         — curl 返回 content="OK" + finish_reason=stop
    streaming    — stream:true → chat.completion.chunk SSE + delta.content + [DONE]
    tool_calling — tools 入参 → finish_reason=tool_calls + tool_calls[].function.arguments
- **未实测 → 保持 False (诚实)**: reasoning (全程 reasoning:null/reasoning_tokens:0 未触发) /
  vision / json_mode / fallback (未真生产 fallback 接管, 同 doubao verified_fallback 留 False)。
- context_window / max_output_tokens 是 DeepSeek 谱系保守占位值, 待进一步实测确认。

OpenAI 兼容: base_url 以 /v1 结尾 + `Authorization: Bearer` (auth_style=bearer 默认)。
"""
import os

from providers import BaseProvider, ModelInfo, ProviderCapabilities

# dev fallback = 公开 DeepSeek API (非机密, 让合约通过; 缺 key 时 available() 排除, 永不真调用)。
# Mac Mini 设 DEEPSEEK_BASE_URL=<真实私有端点> 覆盖。私有端点 (裸 IP + 路径 token) 绝不入库。
_DEEPSEEK_PUBLIC_FALLBACK_BASE = "https://api.deepseek.com/v1"


class DeepSeekProvider(BaseProvider):
    name = "deepseek"
    display_name = "DeepSeek-V4-Pro"
    api_key_env = "DEEPSEEK_API_KEY"
    auth_style = "bearer"

    def __init__(self):
        # base_url 从 env 解析 (机密端点不入库); dev 无 env → 公开 fallback (合约通过)
        self.base_url = (os.environ.get("DEEPSEEK_BASE_URL", "").strip()
                         or _DEEPSEEK_PUBLIC_FALLBACK_BASE)
        self.models = [
            ModelInfo(
                model_id="DeepSeek-V4-Pro",
                display_name="DeepSeek-V4-Pro",
                modalities=["text"],
                context_window=65536,       # 保守占位, 待 Mac Mini 实测确认
                max_output_tokens=8192,     # 保守占位, 待 Mac Mini 实测确认
                is_default=True,
            ),
        ]
        # 🔴 能力声明 (原则 #23 — 只声明实测过的): V37.9.202 Mac Mini E2E 实测 text/streaming/
        # tool_calling 3/3 → declare+verified=True; reasoning/json_mode/vision 未实测 → 保持 False。
        self.capabilities = ProviderCapabilities(
            text=True,
            vision=False,           # 未实测, 保守不声明
            audio=False,
            video=False,
            tool_calling=True,      # V37.9.202 Mac Mini E2E 实测: finish_reason=tool_calls
            streaming=True,         # V37.9.202 Mac Mini E2E 实测: SSE chunk + [DONE]
            json_mode=False,        # 未实测, 待确认
            reasoning=False,        # 未触发 (reasoning:null/reasoning_tokens:0), 保持 False
            context_window=65536,
            max_output_tokens=8192,
            # V37.9.202 verified_* — 仅实测通过的 3 项 flip True (诚实, 镜像 doubao 渐进验证)
            verified_text=True,         # E2E: content + finish_reason=stop
            verified_vision=False,
            verified_tool_calling=True, # E2E: tools → tool_calls[].function.arguments
            verified_streaming=True,    # E2E: stream:true → delta.content chunks + [DONE]
            verified_fallback=False,    # 未真生产 fallback 接管 (同 doubao verified_fallback 留 False)
            # feature_verified: 分项 E2E 实测通过但未真生产流量 (tier_evidence 必须显式引用证据)
            verification_tier="feature_verified",
            tier_evidence="Mac Mini E2E 实测 2026-06-30: text (content+finish_reason=stop) / "
                          "streaming (chat.completion.chunk SSE + delta.content + [DONE]) / "
                          "tool_calling (finish_reason=tool_calls + get_weather arguments) 3/3 通过；"
                          "reasoning 未触发 (reasoning:null) / vision/json_mode 未测 / 未真生产 fallback 接管",
        )

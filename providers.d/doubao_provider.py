"""V37.9.52 — Doubao Seed 2.0 Pro Provider (Volcengine Ark)

接入第 8 个 provider: doubao-seed-2-0-pro (火山引擎 Ark, 主力 LLM, 多模态)。

设计契约:
- API key 严格走 env ARK_API_KEY (不可硬编码, 即便用户豁免也守公开 repo 安全底线)
- Endpoint ID 走 env ARK_ENDPOINT_ID (Volcengine 的 model 字段接收 endpoint ID
  而不是 model name, 这是 Volcengine Ark 与 OpenAI Compatible 端点的关键差异)
- dev 环境无 env → fallback 到公开 model 标识符 doubao-seed-2-0-pro
  (合约通过 + ProviderRegistry.available() 会因缺 ARK_API_KEY 自动排除)
- Mac Mini 配 env → 真实 endpoint ID 注入, fallback chain 激活

API 路径选择: OpenAI Chat Completions 兼容 (走 /chat/completions 标准 schema),
与现有 7 个 provider 一致, adapter.py 零改动. 若 Mac Mini 实测端点不支持,
V37.9.53+ 再加 Responses API translator (input_image/input_text schema).

V37.9.52 ALIGNED 状态: verified_text=False / verified_vision=False
(待 Mac Mini 实测 WhatsApp + image 端到端后再 flip).
"""
import os

from providers import BaseProvider, ModelInfo, ProviderCapabilities


_DOUBAO_FALLBACK_MODEL = "doubao-seed-2-0-pro"


class DoubaoSeedProvider(BaseProvider):
    name = "doubao"
    display_name = "Doubao Seed 2.0 Pro (Volcengine Ark)"
    base_url = "https://ark.cn-beijing.volces.com/api/v3"
    api_key_env = "ARK_API_KEY"
    auth_style = "bearer"

    def __init__(self):
        endpoint_id = os.environ.get("ARK_ENDPOINT_ID", "").strip() or _DOUBAO_FALLBACK_MODEL
        self.models = [
            ModelInfo(
                model_id=endpoint_id,
                display_name="doubao-seed-2-0-pro-260215",
                modalities=["text", "vision"],
                context_window=262144,
                max_output_tokens=16384,
                is_default=True,
                is_vision=True,
            ),
        ]
        self.capabilities = ProviderCapabilities(
            text=True,
            vision=True,
            audio=False,
            video=False,
            tool_calling=True,
            streaming=True,
            json_mode=True,
            context_window=262144,
            max_output_tokens=16384,
            verified_text=False,
            verified_vision=False,
            verified_tool_calling=False,
            verified_streaming=False,
            verified_fallback=False,
        )

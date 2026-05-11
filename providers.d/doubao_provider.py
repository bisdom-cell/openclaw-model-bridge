"""V37.9.52/53/54 — Doubao Seed 2.0 Pro Provider (Volcengine Ark)

接入第 8 个 provider: doubao-seed-2-0-pro (火山引擎 Ark, 主力 LLM, 多模态 + reasoning).

设计契约:
- API key 严格走 env ARK_API_KEY (不可硬编码, 即便用户豁免也守公开 repo 安全底线)
- Endpoint ID 走 env ARK_ENDPOINT_ID (Volcengine 的 model 字段接收 endpoint ID
  而不是 model name, 这是 Volcengine Ark 与 OpenAI Compatible 端点的关键差异)
- dev 环境无 env → fallback 到公开 model 标识符 doubao-seed-2-0-pro
  (合约通过 + ProviderRegistry.available() 会因缺 ARK_API_KEY 自动排除)
- Mac Mini 配 env → 真实 endpoint ID 注入, fallback chain 激活

API 路径: V37.9.53 Mac Mini E2E 实测确认 OpenAI Chat Completions 100% 兼容
(/chat/completions + messages schema), 零 translator 需要.
V37.9.54 Mac Mini E2E 实测 vision schema 同样 OpenAI 兼容 — content list
含 {type:image_url, image_url:{url:...}} + {type:text, text:...} 工作正常.

V37.9.54 状态升级 (vs V37.9.53):
- verified_vision=True (Mac Mini curl 实测 image_url + 文本混合 200 OK +
  完整中文描述 + reasoning_content + finish_reason=stop)

V37.9.53 状态 (保留):
- verified_text=True (Mac Mini curl 实测 200 + 合规 JSON + 完整 content + finish_reason=stop)
- reasoning=True (doubao seed 2.0 是推理模型, 响应含 reasoning_content 字段类似 o1/DeepSeek-R1)
- verified_reasoning=True (同次 curl 实测看到 reasoning_content 完整输出)
- verified_fallback=False (未作为 fallback 在生产中 fire, 留 V37.9.55+ 真 fallback 触发后再 flip)
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
            reasoning=True,  # V37.9.53: doubao seed 2.0 是推理模型, 实测响应含 reasoning_content
            context_window=262144,
            max_output_tokens=16384,
            # V37.9.53/54 实测通过的 verified flags
            verified_text=True,  # V37.9.53 Mac Mini curl 实测 200 OK + 完整 content
            verified_reasoning=True,  # V37.9.53 同次实测看到 reasoning_content 字段
            verified_vision=True,  # V37.9.54 Mac Mini curl 实测 image_url + 完整图片中文描述
            # 未实测的 verified flags (待 V37.9.55+ tool_calling/streaming/真 fallback fire 后 flip)
            verified_tool_calling=False,
            verified_streaming=False,
            verified_fallback=False,
        )

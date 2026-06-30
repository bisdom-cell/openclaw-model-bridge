"""V37.9.204 — DeepSeek-V4-Pro 满血版 Provider (ai-tokenhub 托管, 非量化候选)

接入第 10 个 provider: deepseek-v4-pro-260425 (满血版, via ai-tokenhub API hub).
定位 = Qwen3 迁移的【新候选】, 替代被 pending 的量化版 deepseek (w4a8-mtp 偶发乱码)。

🔴 安全契约 (镜像 doubao/deepseek V37.9.52 公开 repo 安全底线):
- **API key 严格走 env `DEEPSEEK_FULL_API_KEY`** — 绝不硬编码。用户在对话里贴的明文 key
  (sk-...) 不入库, 即便用户豁免也守公开 repo 安全底线。
- **base_url = `https://ai-tokenhub.com/api/v1`** — 公开域名 (非裸 IP/路径 token, 不含机密),
  可入库 (与 openai/claude/doubao 等公开 base_url 同理)。无 key 时 available() 自动排除。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = declared** — 全部能力仅声明, **未经 Mac Mini E2E 实测**。
- 保守只声明 OpenAI /v1 安全基线 text + streaming; tool_calling/json_mode/reasoning/vision
  未实测 → False (避免 reasoning 误声明打断 capability router; 待 E2E 后逐项 flip,
  镜像 deepseek V37.9.201→203 渐进验证)。
- context_window / max_output_tokens 保守占位, 待端点实测确认。

OpenAI 兼容: base_url 以 /v1 结尾 + `Authorization: Bearer` (auth_style=bearer 默认)。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class DeepSeekFullProvider(BaseProvider):
    name = "deepseek_full"
    display_name = "DeepSeek-V4-Pro 满血版 (ai-tokenhub)"
    base_url = "https://ai-tokenhub.com/api/v1"   # 公开域名, 非机密
    api_key_env = "DEEPSEEK_FULL_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="deepseek-v4-pro-260425",
            display_name="deepseek-v4-pro-260425 (满血版)",
            modalities=["text"],
            context_window=65536,      # 保守占位, 待实测
            max_output_tokens=8192,    # 保守占位, 待实测
            is_default=True,
        ),
    ]
    # 🔴 保守声明 (原则 #23): 未经 E2E 实测 → 仅 text + streaming 安全基线, 其余 False。
    capabilities = ProviderCapabilities(
        text=True,
        vision=False,
        audio=False,
        video=False,
        tool_calling=False,    # 未实测, 待 E2E flip
        streaming=True,        # OpenAI /v1 兼容标准基线
        json_mode=False,       # 未实测, 待 E2E flip
        reasoning=False,       # 未实测, 不抢 reasoning 路由
        context_window=65536,
        max_output_tokens=8192,
        # declared 档位: verified_* 全 False, tier_evidence 走派生默认 (不手写)
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
        verified_reasoning=False,
        verification_tier="declared",
    )

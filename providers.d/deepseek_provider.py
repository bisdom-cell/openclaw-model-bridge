"""V37.9.201 — DeepSeek-V4-Pro Provider (用户自建 OpenAI 兼容推理端点)

接入第 9 个 provider: DeepSeek-V4-Pro (self-hosted OpenAI-compatible gateway).

🔴 设计契约 (镜像 doubao_provider.py V37.9.52 公开 repo 安全底线):
- **API key 严格走 env `DEEPSEEK_API_KEY`** — 绝不硬编码。用户在对话里贴的明文 key
  (sk-...) 不入库, 即便用户豁免也守公开 repo 安全底线。
- **base_url 走 env `DEEPSEEK_BASE_URL`** — 用户端点是【裸 IP + 路径 token】= 类机密,
  本仓库是公开仓库 → 不入库。dev 无 env 时 fallback 到公开 `https://api.deepseek.com/v1`
  让 ProviderContract 通过 + `ProviderRegistry.available()` 因缺 `DEEPSEEK_API_KEY`
  自动排除 (与 doubao endpoint-id fallback 同一模式)。Mac Mini 配 env → 真实私有端点注入。

诚实语义 (原则 #23 — 不编造未实测能力):
- **verification_tier = declared** — 全部能力仅声明, **未经 Mac Mini E2E 实测**。
- verified_* 全 False — text/tool_calling/streaming/reasoning 待 Mac Mini curl 实测后
  再逐项 flip (镜像 doubao V37.9.53→55 渐进验证, 不人为造)。
- context_window / max_output_tokens 是 DeepSeek 谱系保守占位值, 待端点实测确认。
- vision 不声明 (DeepSeek-V4-Pro 多模态能力未知, 保守)。

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
        # 🔴 能力保守声明 (原则 #23): DeepSeek-V4-Pro 是【未知自建端点】(非公开文档化模型),
        # 无依据声明 tool_calling/json_mode/reasoning/vision → 仅声明 OpenAI /v1 安全基线
        # text + streaming。待 Mac Mini E2E 实测后逐项 flip 为 True (镜像 doubao 渐进验证)。
        # (与 openai/claude 等"已知公开模型声明全集"不同——它们能力是公开事实, 本端点未知。)
        self.capabilities = ProviderCapabilities(
            text=True,
            vision=False,           # 未知, 保守不声明
            audio=False,
            video=False,
            tool_calling=False,     # 未知, 待实测 (不影响 reasoning/tool 路由误选 keyless deepseek)
            streaming=True,         # OpenAI /v1 兼容标准基线
            json_mode=False,        # 未知, 待实测
            reasoning=False,        # 未知, 待实测 (不抢 reasoning 路由)
            context_window=65536,
            max_output_tokens=8192,
            # 🔴 verified_* 全 False — 未经 Mac Mini E2E 实测 (守诚实语义, 同 doubao 初始)
            verified_text=False,
            verified_vision=False,
            verified_tool_calling=False,
            verified_streaming=False,
            verified_fallback=False,
            # declared 档位: tier_evidence 走 _DECLARED_TIER_EVIDENCE 派生 (单一真理源, 不手写)。
            # 待 Mac Mini E2E 实测 text/tool_calling/streaming/reasoning 后逐项 flip verified_* →
            # 升 tier (镜像 doubao V37.9.53→55 渐进验证)。
            verification_tier="declared",
        )

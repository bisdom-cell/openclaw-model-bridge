"""V37.9.201 — DeepSeek-V4-Pro Provider (用户自建 OpenAI 兼容推理端点)
   V37.9.202 — Mac Mini E2E 实测: text/streaming/tool_calling 3/3 通过 → feature_verified
   V37.9.203 — 能力探针实测: +json_mode (verified) / vision 确认不支持 / reasoning 无 R1 通道
   V37.9.204 — ⏸ PENDING (暂缓作 Qwen3 迁移候选): w4a8-mtp 量化偶发乱码 token (bat-ball 实测)
               + 改评估满血版 (deepseek_full / ai-tokenhub, deepseek-v4-pro-260425)。本 provider
               保留注册可用 (有 DEEPSEEK_API_KEY 时), 但不推进迁移; 迁移候选转 deepseek_full。

接入第 9 个 provider: DeepSeek-V4-Pro w4a8-mtp (self-hosted OpenAI-compatible gateway).
真实部署 = `DeepSeek-V4-Pro-w4a8-mtp` (4-bit 权重 8-bit 激活量化 + multi-token prediction;
错误响应路径泄露)。⚠️ 质量观察: 推理类响应偶发乱码 token 注入 (w4a8-mtp 量化产物,
如 "dollars35367"/"only12") — 答案正确但夹杂无关 token, 替换 Qwen3 (fp) 时须权衡。

🔴 设计契约 (镜像 doubao_provider.py V37.9.52 公开 repo 安全底线):
- **API key 严格走 env `DEEPSEEK_API_KEY`** — 绝不硬编码。用户在对话里贴的明文 key
  (sk-...) 不入库, 即便用户豁免也守公开 repo 安全底线。
- **base_url 走 env `DEEPSEEK_BASE_URL`** — 用户端点是【裸 IP + 路径 token】= 类机密,
  本仓库是公开仓库 → 不入库。dev 无 env 时 fallback 到公开 `https://api.deepseek.com/v1`
  让 ProviderContract 通过 + `ProviderRegistry.available()` 因缺 `DEEPSEEK_API_KEY`
  自动排除 (与 doubao endpoint-id fallback 同一模式)。Mac Mini 配 env → 真实私有端点注入。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = feature_verified** — 分项 E2E 实测通过但未真生产流量。
- **Mac Mini E2E 实测 2026-06-30**:
    text         ✅ content="OK" + finish_reason=stop (V37.9.202)
    streaming    ✅ chat.completion.chunk SSE + delta.content + [DONE] (V37.9.202)
    tool_calling ✅ finish_reason=tool_calls + tool_calls[].function.arguments (V37.9.202)
    json_mode    ✅ response_format=json_object → 干净 {"name":"Bob","age":30} 无报错 (V37.9.203)
- **确认不支持 / 未暴露 → False (实测得知, 非未知)**:
    vision       ❌ 400 "is not a multimodal model" — w4a8-mtp 非多模态 (V37.9.203 实测)
    reasoning    ❌ reasoning:null/reasoning_tokens:0 — content 内能 CoT 但不暴露 R1 风格
                    reasoning_content 通道 (不符合项目 reasoning 定义, V37.9.203 实测)
- verified_fallback=False — 未真生产 fallback 接管 (同 doubao, 真接管后才 flip)。
- context_window / max_output_tokens 保守占位 (端点无 /models 端点, 404; 真值待测)。

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
    # V37.9.223 B1: 声明 per-request 关 reasoning (镜像 deepseek_full 的 thinking:disabled)。
    # 探针实测 2026-07-02 (self-host 网关 curl): thinking:disabled → HTTP 200 + finish_reason=stop
    #   + reasoning_tokens:0, 无 400 → 网关接受该参数, 声明安全保留 (400 风险已排除)。
    # ⚠️ 剩余诚实注记 (原则 #23): 本 provider reasoning=False (无 R1 通道) → thinking-off 实际
    #   no-op (batch 走快路靠的是本就无 reasoning 开销), 但无害。仍 PENDING (w4a8 量化偶发乱码);
    #   非 primary 时注入 inert (B1 仅在该 provider 作 serving provider 时 fire)。
    reasoning_off_body = {"thinking": {"type": "disabled"}}

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
        # 🔴 能力声明 (原则 #23 — 只声明实测过的): V37.9.202/203 Mac Mini E2E 探针实测。
        # text/streaming/tool_calling/json_mode ✅; vision 确认不支持(非多模态)/reasoning 无 R1 通道。
        self.capabilities = ProviderCapabilities(
            text=True,
            vision=False,           # V37.9.203 实测: 400 "is not a multimodal model"
            audio=False,
            video=False,
            tool_calling=True,      # V37.9.202 E2E: finish_reason=tool_calls
            streaming=True,         # V37.9.202 E2E: SSE chunk + [DONE]
            json_mode=True,         # V37.9.203 E2E: response_format=json_object → 干净 JSON (无 verified_* 字段)
            reasoning=False,        # V37.9.203 实测: reasoning:null, 不暴露 R1 风格 reasoning_content 通道
            context_window=65536,   # 保守占位 (端点无 /models, 真值待测)
            max_output_tokens=8192, # 保守占位
            # verified_* (6 维跟踪 text/vision/tool_calling/streaming/fallback/reasoning; json_mode 无字段)
            verified_text=True,         # E2E: content + finish_reason=stop
            verified_vision=False,      # 确认不支持 (非多模态)
            verified_tool_calling=True, # E2E: tools → tool_calls[].function.arguments
            verified_streaming=True,    # E2E: stream:true → delta.content chunks + [DONE]
            verified_fallback=False,    # 未真生产 fallback 接管 (真接管后 flip)
            verified_reasoning=False,   # 不暴露 reasoning_content 通道
            # feature_verified: 分项 E2E 实测通过但未真生产流量 (tier_evidence 必须显式引用证据)
            verification_tier="feature_verified",
            tier_evidence="Mac Mini E2E 实测 2026-06-30: text/streaming/tool_calling/json_mode 4/4 通过 "
                          "(content+finish_reason / SSE chunk+[DONE] / finish_reason=tool_calls+arguments / "
                          "response_format=json_object 干净 JSON)；vision 实测不支持 (400 非多模态) / "
                          "reasoning 无 R1 reasoning_content 通道 / 未真生产 fallback 接管。"
                          "部署=w4a8-mtp 量化, 推理响应偶发乱码 token",
        )

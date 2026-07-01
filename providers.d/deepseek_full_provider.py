"""V37.9.204 — DeepSeek-V4-Pro 满血版 Provider (ai-tokenhub 托管, 非量化候选)
   V37.9.205 — Mac Mini E2E 实测: text/tool_calling/reasoning 3/3 → feature_verified;
               🌟 有 R1 reasoning_content 通道 (量化版无) + 无乱码 token (量化版有);
               vision 确认不支持; json_mode 返回围栏 (非严格 response_format, 保持 False)。

接入第 10 个 provider: deepseek-v4-pro-260425 (满血版, via ai-tokenhub API hub).
定位 = Qwen3 迁移的【新候选】, 替代被 pending 的量化版 deepseek (w4a8-mtp 偶发乱码)。

⚠️ 运维注意: 满血版是推理模型, 先生成 reasoning 再生成 content (reasoning 占 token 预算)。
生成任务须给足 max_tokens, 否则 content 可能空/截断 (E2E 实测 600 tokens 被 reasoning 吃掉)。

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
            context_window=1048576,    # V37.9.207 端点规格 1M (1024K, 用户/端点确认; 原 65536 是未实测占位)
            max_output_tokens=8192,    # 保守占位, 待实测
            is_default=True,
        ),
    ]
    # 🔴 能力声明 (原则 #23 — 只声明实测过的): V37.9.205 Mac Mini E2E 探针实测。
    # text/tool_calling/reasoning ✅ verified; vision 确认不支持; json_mode 围栏非严格 → False。
    capabilities = ProviderCapabilities(
        text=True,
        vision=False,          # V37.9.205 实测: 400 Bad Request (DeepSeek V 系无视觉)
        audio=False,
        video=False,
        tool_calling=True,     # V37.9.205 E2E: finish_reason=tool_calls + get_weather arguments
        streaming=True,        # OpenAI /v1 标准基线 (本探针未单测 streaming, verified 留 False)
        json_mode=False,       # V37.9.205 实测: response_format 返回 ```json 围栏 (非严格), 不声明
        reasoning=True,        # 🌟 V37.9.205 E2E: reasoning 字段填充 + reasoning_tokens=55 (R1 通道)
        context_window=1048576,  # V37.9.207 端点规格 1M (1024K, 用户/端点确认; 原 65536 是未实测占位)
        max_output_tokens=8192,
        # verified_* (6 维跟踪); 仅实测通过的 3 项 flip True
        verified_text=True,        # E2E: 干净中文 content + finish_reason=stop
        verified_vision=False,     # 确认不支持
        verified_tool_calling=True,  # E2E: tools → tool_calls[].function.arguments
        verified_streaming=False,  # 本探针未单测 streaming (declared True, 待补测)
        verified_fallback=False,   # 未真生产 fallback 接管
        verified_reasoning=True,   # E2E: reasoning_content 通道 + reasoning_tokens>0
        # feature_verified: 分项 E2E 实测通过但未真生产流量 (tier_evidence 必须显式引用证据)
        verification_tier="feature_verified",
        tier_evidence="Mac Mini E2E 实测 2026-06-30: text/tool_calling/reasoning 3/3 通过 "
                      "(干净中文+finish_reason / finish_reason=tool_calls+arguments / "
                      "reasoning 字段填充+reasoning_tokens=55 R1 通道)；无乱码 token (优于量化版 w4a8)；"
                      "vision 实测不支持 (400) / json_mode 围栏非严格 / streaming 未单测 / 未真生产 fallback",
    )

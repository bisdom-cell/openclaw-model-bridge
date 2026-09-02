"""V37.9.254 — GLM-5.2 Coding Provider → V37.9.290 平台切换回 ai-tokenhub
   → V37.9.339 🔴 槽位换模型: glm-5.2-huakun → glm-5-3-260814 (GLM-5.3)

接入第 12 个 provider: glm-5-3-260814 (GLM-5.3 coding 场景专用, ai-tokenhub 托管)。

平台史 (原则 #23 渐进验证的完整走程):
- V37.9.254 初接 ai-tokenhub → V37.9.255 刷新 Volcengine Ark → V37.9.256-258 Ark E2E 3/3
- V37.9.290 切回 ai-tokenhub (用户提供 GLM 专属 sk- key) → V37.9.291 tokenhub E2E text+reasoning
- **V37.9.339 (2026-09-02 用户指令, 后台 LLM Provider 刷新)**: 模型 glm-5.2-huakun →
  glm-5-3-260814, 同网关新 key。**不是更名, 是换版本** → 5.2 证据不迁移, tier 回 declared。

定位 = **coding 场景专用**——按需显式调用 (`?provider=glm5_coding` / chat `glm ` 前缀
V37.9.271 / code_assist.sh), 不是 primary 也不默认进 auto-fallback 链。
区别于第 7 个 built-in `glm` (Zhipu open.bigmodel.cn)。registry name `glm5_coding` 是
历史槽位名 (5 系 coding 槽位), 当前版本 5.3。

设计契约:
- 🔴 API key 严格走 env GLM5_API_KEY (不可硬编码; V37.9.339 起该 env 的**值**换成
  GLM-5.3 专属 key — Mac Mini plist + .env_shared 替换值, env 名不变)
- base_url = https://ai-tokenhub.com/api/v1 (公开域名可入库, 与 deepseek_full/doubao 同网关)
- dev 环境无 env → ProviderRegistry.available() 因缺 GLM5_API_KEY 自动排除

🔴 诚实语义: tier declared (V37.9.339 重置), verified_* 全 False, Mac Mini E2E 复测后逐项升档。
capability 声明保持 GLM-5 系 coding 家族画像 (text/tool_calling/streaming True;
json_mode False — 5.2 在 Ark 实测 400 不支持, 5.3 未知保守沿用待翻案;
reasoning True — 5.2 在 tokenhub Bifrost 网关实测暴露 reasoning 字段, 视为网关+家族性质;
vision False 模型本体如此)。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class Glm5CodingProvider(BaseProvider):
    name = "glm5_coding"
    display_name = "GLM-5.3 Coding (ai-tokenhub)"
    base_url = "https://ai-tokenhub.com/api/v1"
    api_key_env = "GLM5_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            # V37.9.339 (2026-09-02 用户变更): glm-5.2-huakun → glm-5-3-260814
            model_id="glm-5-3-260814",
            display_name="glm-5-3-260814 (GLM-5.3 Coding)",
            modalities=["text"],
            context_window=131072,      # 128K 保守占位, 待端点规格/实测确认
            max_output_tokens=8192,     # 保守占位, 待实测
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=False,          # coding 文本模型, GLM-5V 是独立模型
        audio=False,
        video=False,
        tool_calling=True,     # GLM-5 系 coding 家族 (5.2 Ark/tokenhub 时代实测过)
        streaming=True,        # 同上
        json_mode=False,       # 5.2 Ark 实测 400 不支持; 5.3 未知保守沿用
        # reasoning_off_body 保持不声明: thinking 参数未在本模型实测 且 glm5 不进
        # batch/auto-fallback 消费路径 (原则 #23 不投机 declare)。
        reasoning=True,        # 5.2 tokenhub Bifrost 网关实测暴露 reasoning 字段 (V37.9.291)
        context_window=131072,
        max_output_tokens=8192,
        # V37.9.339 换版本重置: 全部 verified_* False, 5.3 E2E 复测后逐项 flip
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
        verified_reasoning=False,
        verification_tier="declared",
        tier_note="2026-09-02 槽位换版本 GLM-5.3 (V37.9.339), 5.2 时代 E2E 证据不迁移, ai-tokenhub 复测待 Mac Mini",
    )

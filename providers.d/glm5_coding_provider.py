"""V37.9.254 — GLM-5.2 Coding Provider → V37.9.290 平台切换回 ai-tokenhub

接入第 12 个 provider: glm-5.2-huakun (GLM-5.2 coding 场景专用;
V37.9.289 更名, 原 glm-5-2-260617)。

平台史 (原则 #23 渐进验证的完整走程):
- V37.9.254 初接 ai-tokenhub (用户初始 endpoint) → 当时 key 10101 InvalidCredentials
  + /v1/models 无 GLM scope → V37.9.255 判定错平台刷新到 Volcengine Ark
- V37.9.256-258 Ark ep- 接入点 E2E: text/streaming/tool_calling 3/3 实测 (feature_verified)
- **V37.9.290 (2026-08-08 用户变更) 切回 ai-tokenhub**: V37.9.289 更名后 Mac Mini 实测
  Ark 不认 glm-5.2-huakun (NotFound), 而 ai-tokenhub 报 10406 InsufficientScope =
  名字存在缺 scope; 用户提供 GLM 专属 ai-tokenhub key (sk- 格式, 恰为 V37.9.255 时代
  10101 的那把 — 华坤侧已激活) → 整体切回 ai-tokenhub (镜像 deepseek_full 结构,
  去 GLM5_ENDPOINT_ID ep- 间接层)。

定位 = **coding 场景专用**——按需显式调用 (`?provider=glm5_coding` / chat `glm ` 前缀
V37.9.271 / code_assist.sh), 不是 primary 也不默认进 auto-fallback 链。
区别于第 7 个 built-in `glm` (Zhipu open.bigmodel.cn)。

设计契约:
- 🔴 API key 严格走 env GLM5_API_KEY (不可硬编码; V37.9.290 起为 ai-tokenhub 的
  sk- 格式 key, 不再是 Volcengine ark- 格式 — Mac Mini plist 需替换值)
- base_url = https://ai-tokenhub.com/api/v1 (公开域名可入库, 与 deepseek_full/doubao 同网关)
- dev 环境无 env → ProviderRegistry.available() 因缺 GLM5_API_KEY 自动排除

🔴 诚实语义: Ark 时代 feature_verified (V37.9.256-258 3/3 E2E) 在新 serving 栈不迁移
→ tier 回 declared (镜像 V37.9.255 同款平台切换纪律), Mac Mini E2E 复测后逐项升档。
capability 声明保持 Ark 实测画像 (text/tool_calling/streaming True; json_mode False
曾 Ark 400 实测不支持 — ai-tokenhub 网关行为未知, 保守沿用 False 待探针翻案;
vision/reasoning False 模型本体如此)。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class Glm5CodingProvider(BaseProvider):
    name = "glm5_coding"
    display_name = "GLM-5.2 Coding (ai-tokenhub)"
    base_url = "https://ai-tokenhub.com/api/v1"
    api_key_env = "GLM5_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="glm-5.2-huakun",
            display_name="glm-5.2-huakun (GLM-5.2 Coding)",
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
        tool_calling=True,     # Ark 时代 V37.9.258 实测过模型本体支持
        streaming=True,        # Ark 时代 V37.9.257 实测过模型本体支持
        json_mode=False,       # Ark 时代实测 400 不支持; 新网关未知, 保守沿用
        # V37.9.291 翻案: Ark ep- 时代 reasoning_tokens=0 判无通道; tokenhub (Bifrost)
        # E2E 实测响应含 reasoning 字段且有真实推理内容 (usage 无 reasoning_tokens 计数,
        # 网关归一化暴露)。reasoning_off_body 保持不声明 (thinking 参数未在本模型实测
        # 且 glm5 不进 batch/auto-fallback 消费路径, 原则 #23 不投机declare)。
        reasoning=True,
        context_window=131072,
        max_output_tokens=8192,
        # V37.9.290 平台切换重置 → V37.9.291 tokenhub E2E 探针逐项升档
        verified_text=True,          # V37.9.291 E2E 2026-08-08: 200 + 正确代码 + finish_reason=stop
        verified_vision=False,
        verified_tool_calling=False, # 未经 tokenhub 实测 (Ark 时代曾实测通过)
        verified_streaming=False,    # 未经 tokenhub 实测
        verified_fallback=False,
        verified_reasoning=True,     # V37.9.291 E2E: reasoning 字段含真实推理内容 (Bifrost 归一化)
        verification_tier="feature_verified",
        tier_note="2026-08-08 平台切回 ai-tokenhub 后 E2E 半升档 (text+reasoning)",
        tier_evidence="ai-tokenhub E2E 探针 2026-08-08 (V37.9.291): text+reasoning 2/2 通过 "
                      "(200 + finish_reason=stop + 正确 print 代码 + reasoning 字段真实推理链, "
                      "响应 model 回显 glm-5.2 = 别名路由正确; reasoning 为 tokenhub 新发现 — "
                      "Ark ep- 时代 reasoning_tokens=0 无通道, V37.9.258 曾据此判 False)；"
                      "tool_calling/streaming 未经 tokenhub 实测保持 False (Ark 时代史见 docstring)",
    )

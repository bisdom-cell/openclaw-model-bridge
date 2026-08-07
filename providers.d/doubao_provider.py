"""V37.9.52/53/54/55 — Doubao Seed 2.0 Pro Provider → V37.9.290 平台切换 ai-tokenhub

接入第 8 个 provider: doubao-seed-2.0-pro-huakun (Doubao Seed 2.0 Pro, 多模态 + reasoning)。

V37.9.290 (2026-08-08 用户变更) 平台切换 Volcengine Ark → ai-tokenhub:
- V37.9.289 更名 doubao-seed-2-0-pro → doubao-seed-2.0-pro-huakun 后, Mac Mini E2E
  实测 Ark 不认新名 (InvalidEndpointOrModel.NotFound), 而 ai-tokenhub /v1/models 证实
  -huakun 是该网关的别名家族 (deepseek-v4-pro-huakun 同款已验证工作), 10406
  InsufficientScope 证实 doubao/glm 新名在 ai-tokenhub 存在但需各自 scope 的 key。
  用户提供 doubao 专属 key → 本 provider 整体切到 ai-tokenhub (镜像 deepseek_full 结构)。
- 结构变化: 去掉 Ark 的 ep- endpoint-ID 间接层 (ai-tokenhub 的 model 字段直接接收
  model 名, ARK_ENDPOINT_ID 不再消费), models 回到类级静态声明。

设计契约:
- API key 严格走 env DOUBAO_API_KEY (不可硬编码, 即便用户豁免也守公开 repo 安全底线;
  原 ARK_API_KEY 是 Volcengine key, 平台切换后本 provider 不再消费)
- base_url = https://ai-tokenhub.com/api/v1 (公开域名可入库, 与 deepseek_full 同网关)
- dev 环境无 env → ProviderRegistry.available() 因缺 DOUBAO_API_KEY 自动排除

版本史: V37.9.52 接入 / V37.9.53 text+reasoning E2E / V37.9.54 vision E2E /
V37.9.55 tool_calling+streaming E2E / V37.9.289 更名 / V37.9.290 平台切换。

历史证据 (Ark 时代, 平台切换后不迁移 — 原则 #23 渐进验证):
- V37.9.53-55 Mac Mini E2E: text/vision/tool_calling/streaming/reasoning 5/5 实测
  (OpenAI Chat Completions 100% 兼容 / image_url schema / finish_reason=tool_calls /
  SSE chunks / reasoning_content 通道)
- production_observed: fallback 链真实接管 (V37.9.129–V37.9.218 唯一真 fallback) +
  expert_escalate 真生产调用 (V37.9.91)
→ 以上全部在 Ark ep- 接入点上挣得; ai-tokenhub 是不同 serving 栈 (网关/计费/可能的
  量化差异, 参考 deepseek 量化版 w4a8 乱码教训), tier 诚实回 declared,
  E2E 复测通过后按探针结果逐项升档。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class DoubaoSeedProvider(BaseProvider):
    name = "doubao"
    display_name = "Doubao Seed 2.0 Pro (ai-tokenhub)"
    base_url = "https://ai-tokenhub.com/api/v1"
    api_key_env = "DOUBAO_API_KEY"
    auth_style = "bearer"
    # V37.9.290: reasoning-off 片段保留 — ai-tokenhub 用 Bifrost 网关归一化 thinking
    # 参数, deepseek_full 在同一网关 2026-07-02 实测 thinking:disabled 生效
    # (V37.9.222 B1)。本 provider 切到同网关后证据家族反而更近 (原则 #23:
    # 同网关实测 > 跨平台推断), 但本模型未单独实测, 作 primary 前须探针。
    reasoning_off_body = {"thinking": {"type": "disabled"}}
    models = [
        ModelInfo(
            model_id="doubao-seed-2.0-pro-huakun",
            display_name="doubao-seed-2.0-pro-huakun",
            modalities=["text", "vision"],
            context_window=262144,
            max_output_tokens=16384,
            is_default=True,
            is_vision=True,
        ),
    ]
    # 能力声明 = 模型级能力 (Doubao Seed 2.0 Pro 文档 + Ark 时代实测过的模型本体能力);
    # verified_* = 当前部署 (ai-tokenhub) 的实证 — 平台切换后全部重置, 探针后逐项 flip。
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,           # 模型多模态; ai-tokenhub 网关 image_url 透传待实测
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=True,
        reasoning=True,        # 推理模型, Ark 时代实测 reasoning_content 通道
        context_window=262144,
        max_output_tokens=16384,
        # V37.9.290 平台切换重置 → V37.9.291 tokenhub E2E 探针逐项升档:
        # text+reasoning 实测通过; vision/tool_calling/streaming 待探针 (诚实 False)
        verified_text=True,          # V37.9.291 E2E 2026-08-08: 200 + 正确 content + finish_reason=stop
        verified_vision=False,       # tokenhub 网关 image_url 透传未实测
        verified_tool_calling=False, # 未经 tokenhub 实测
        verified_streaming=False,    # 未经 tokenhub 实测
        verified_reasoning=True,     # V37.9.291 E2E: reasoning 字段完整 + reasoning_tokens=263
        verified_fallback=False,     # 未真生产 fallback 接管 (tokenhub 时代)
        verification_tier="feature_verified",
        tier_note="2026-08-08 平台切换 ai-tokenhub 后 E2E 半升档 (text+reasoning)",
        tier_evidence="ai-tokenhub E2E 探针 2026-08-08 (V37.9.291): text+reasoning 2/2 通过 "
                      "(200 + finish_reason=stop + 正确 content + reasoning 字段完整 "
                      "reasoning_tokens=263, 响应 model 回显 doubao-seed-2.0-pro = 别名路由正确; "
                      "注意 tokenhub 字段名 reasoning 非 Ark 的 reasoning_content)；"
                      "vision/tool_calling/streaming 未经 tokenhub 实测保持 False; "
                      "Ark 时代 production_observed 史见 docstring",
    )

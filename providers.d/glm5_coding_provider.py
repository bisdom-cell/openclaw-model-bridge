"""V37.9.254 — GLM-5.2 Coding Provider (Volcengine Ark, coding 专用按需调用)
   V37.9.255 — 端点刷新: ai-tokenhub → Volcengine Ark (用户确认 GLM-5.2 实际托管在
               火山引擎 Ark, 同 doubao_21 平台; ai-tokenhub 账号无 GLM scope, 旧 key 10101)。

接入第 12 个 provider: glm-5-2-260617 (GLM-5.2, 火山引擎 Ark 托管, 同 doubao_21 平台)。
定位 = **coding 场景专用**——有需要编码的场景时按需显式调用 (`?provider=glm5_coding`),
不是 primary 也不默认进 auto-fallback 链 (由 Mac Mini FALLBACK_ORDER env 控制是否纳入)。
区别于第 7 个 built-in `glm` (Zhipu open.bigmodel.cn) —— 本 provider 是火山 Ark 托管的
GLM-5.2, 独立 endpoint ID + 独立 key。

设计契约 (镜像 doubao_seed_21_provider.py V37.9.216 Ark 安全模式):
- 🔴 API key 严格走 env `GLM5_API_KEY` (不可硬编码, 即便用户豁免也守公开 repo 安全底线;
  push 前 ark-/sk- 泄漏扫描会拦截)。用独立 env (非复用 doubao_21 的 ARK_21_API_KEY),
  让 GLM 与 doubao_21 各自持 key/endpoint 独立配置。key 是 Volcengine `ark-...` 格式。
- Endpoint ID 走 env `GLM5_ENDPOINT_ID` (Volcengine 的 model 字段接收 endpoint ID `ep-...`
  而非 model name — Volcengine Ark 与 OpenAI Compatible 端点的关键差异, 同 doubao_21)。
- base_url = https://ark.cn-beijing.volces.com/api/v3 是公开 Volcengine 域名 (非裸 IP/
  非机密) → 可入库 (与 doubao 2.0/2.1 同域名)。
- dev 环境无 env → fallback 到公开 model 标识符 glm-5-2-260617 (合约通过 +
  ProviderRegistry.available() 因缺 GLM5_API_KEY 自动排除)。
- Mac Mini 配 env → 真实 endpoint ID 注入, 可显式 `?provider=glm5_coding` 调用。

🔴 诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = feature_verified** — V37.9.254 declared → V37.9.255 端点刷新 Ark →
  **V37.9.256-258 Mac Mini 直连 Ark E2E 全探针: text/streaming/tool_calling 3/3 实测通过**
  (is_prime 正确代码+finish_reason=stop+model=glm-5-2-260617+reasoning_tokens=0 /
  --stream SSE chunk+[DONE] / finish_reason=tool_calls+get_weather({city:东京}) arguments)。
- **实测能力**: text ✅ / streaming ✅ / tool_calling ✅。
- **实测不支持 / False (原则 #23 诚实)**:
    json_mode — V37.9.258 E2E 实测 HTTP 400 "json_object is not supported by this model" → False
    vision    — coding 文本模型, 非多模态 (GLM-5V 是独立模型) → False
    reasoning — reasoning_tokens=0, 本 endpoint 无 reasoning 通道 → False
  (故不设 reasoning_off_body: 无 reasoning 通道不声明 batch-reasoning-off, 原则 #23。)
- context_window = 131072 (128K, GLM-5 系典型值保守占位, 待端点规格/实测确认; 描述性
  metadata 不影响路由评分, 同 deepseek 原 65536 占位惯例)。
"""
import os

from providers import BaseProvider, ModelInfo, ProviderCapabilities


_GLM5_FALLBACK_MODEL = "glm-5-2-260617"


class Glm5CodingProvider(BaseProvider):
    name = "glm5_coding"
    display_name = "GLM-5.2 Coding (Volcengine Ark)"
    base_url = "https://ark.cn-beijing.volces.com/api/v3"   # 公开 Volcengine 域名, 非机密
    api_key_env = "GLM5_API_KEY"
    auth_style = "bearer"

    def __init__(self):
        endpoint_id = (os.environ.get("GLM5_ENDPOINT_ID", "").strip()
                       or _GLM5_FALLBACK_MODEL)
        self.models = [
            ModelInfo(
                model_id=endpoint_id,       # Volcengine: model 字段接收 endpoint ID (ep-...)
                display_name="glm-5-2-260617 (GLM-5.2 Coding)",
                modalities=["text"],
                context_window=131072,      # 128K 保守占位, 待端点规格/实测确认
                max_output_tokens=8192,     # 保守占位, 待实测
                is_default=True,
            ),
        ]
        # 🔴 能力声明 (原则 #23 — declared tier: 来自 GLM-5 系文档 + OpenAI 兼容基线, 0 生产验证):
        # coding 典型集 text/tool_calling/streaming/json_mode declared True; vision/reasoning 保守 False;
        # verified_* 全 False (dev 无 key 无法 E2E), tier_evidence 留空走派生。
        self.capabilities = ProviderCapabilities(
            text=True,
            vision=False,          # coding 文本模型, GLM-5V 是独立模型
            audio=False,
            video=False,
            tool_calling=True,     # V37.9.258 E2E: finish_reason=tool_calls + get_weather arguments
            streaming=True,        # V37.9.257 E2E: SSE chunk 流
            json_mode=False,       # V37.9.258 E2E 实测不支持: HTTP 400 "json_object is not supported by this model"
            reasoning=False,       # reasoning_tokens=0 (本 endpoint 无 reasoning 通道)
            context_window=131072,
            max_output_tokens=8192,
            # verified_* — V37.9.256-258 Mac Mini 直连 Ark E2E 全探针: text/streaming/tool_calling 3/3 通过;
            # json_mode 实测不支持 (400) / vision 非多模态 / reasoning 无通道 → 诚实 False
            verified_text=True,          # E2E: is_prime 正确代码 + finish_reason=stop + model=glm-5-2-260617
            verified_vision=False,
            verified_tool_calling=True,  # V37.9.258 E2E: finish_reason=tool_calls + get_weather({"city":"东京"})
            verified_streaming=True,     # E2E: --stream SSE chunk 流 (快排解释) + finish_reason=stop + [DONE]
            verified_fallback=False,     # 未真生产 fallback 接管
            verified_reasoning=False,    # reasoning_tokens=0 (本调用无 reasoning, 与 reasoning=False 一致)
            # feature_verified: 全能力探针 E2E (text/streaming/tool_calling 3/3 通过); tier_evidence 显式引用证据
            verification_tier="feature_verified",
            tier_evidence="Mac Mini 直连 Volcengine Ark E2E 全探针 2026-07-07: text/streaming/tool_calling 3/3 通过 "
                          "(is_prime 正确代码+finish_reason=stop+model=glm-5-2-260617+reasoning_tokens=0 / "
                          "--stream SSE chunk+[DONE] / finish_reason=tool_calls+get_weather({city:东京}) arguments)；"
                          "json_mode 实测不支持 (HTTP 400 json_object is not supported by this model) / "
                          "vision 非多模态 / reasoning 无通道 / 未真生产 fallback 接管",
        )

"""V37.9.216 — Doubao Seed 2.1 Pro Provider (Volcengine Ark, 旗舰版)

接入第 11 个 provider: doubao-seed-2-1-pro (火山引擎 Ark, 新旗舰, 多模态 + reasoning),
区别于第 8 个 doubao_provider.py (Doubao Seed 2.0 Pro)。

设计契约 (镜像 doubao_provider.py V37.9.52 安全模式):
- 🔴 API key 严格走 env ARK_21_API_KEY (不可硬编码, 即便用户豁免也守公开 repo 安全底线;
  push 前 sk-/ark- 泄漏扫描会拦截)。用独立 env (非复用 doubao 2.0 的 ARK_API_KEY),
  让 2.1 旗舰与 2.0 各自持 key/endpoint 独立配置。
- Endpoint ID 走 env ARK_21_ENDPOINT_ID (Volcengine 的 model 字段接收 endpoint ID
  而非 model name — Volcengine Ark 与 OpenAI Compatible 端点的关键差异)。
- base_url = https://ark.cn-beijing.volces.com/api/v3 是公开 Volcengine 域名 (非裸 IP/
  非机密) → 可入库 (与 doubao 2.0 同域名)。
- dev 环境无 env → fallback 到公开 model 标识符 doubao-seed-2-1-pro-260628
  (合约通过 + ProviderRegistry.available() 因缺 ARK_21_API_KEY 自动排除)。
- Mac Mini 配 env → 真实 endpoint ID 注入, fallback chain 可激活。

🔴 诚实语义 (原则 #23 + 渐进验证纪律):
- V37.9.216 接入 declared (能力仅声明未实测) → V37.9.217 Mac Mini E2E 实测 5 项
  (text/vision/tool_calling/streaming/reasoning) 全通过 → 升 feature_verified。
- verification_tier = "feature_verified" — 分项 E2E 实测通过但未真生产流量。
  verified_text/vision/tool_calling/streaming/reasoning=True; verified_fallback=False
  (未真生产 fallback 接管, 同 doubao 2.0/deepseek 惯例)。
- 能力声明 (含 json_mode=True) = Doubao Seed 2.1 Pro advertised 能力; json_mode 声明
  未单测。旗舰质量: bat-ball 答对 0.05 + 完整 reasoning 通道 + 无乱码 (优于 deepseek 量化版)。
- 镜像 doubao 2.0 V37.9.53-55 / deepseek V37.9.201→205 渐进验证。
"""
import os

from providers import BaseProvider, ModelInfo, ProviderCapabilities


_DOUBAO_21_FALLBACK_MODEL = "doubao-seed-2-1-pro-260628"


class DoubaoSeed21Provider(BaseProvider):
    name = "doubao_21"
    display_name = "Doubao Seed 2.1 Pro (Volcengine Ark)"
    base_url = "https://ark.cn-beijing.volces.com/api/v3"
    api_key_env = "ARK_21_API_KEY"
    auth_style = "bearer"

    def __init__(self):
        endpoint_id = (os.environ.get("ARK_21_ENDPOINT_ID", "").strip()
                       or _DOUBAO_21_FALLBACK_MODEL)
        self.models = [
            ModelInfo(
                model_id=endpoint_id,
                display_name="doubao-seed-2-1-pro-260628",
                modalities=["text", "vision"],
                context_window=262144,
                max_output_tokens=16384,
                is_default=True,
                is_vision=True,
            ),
        ]
        self.capabilities = ProviderCapabilities(
            # 能力声明 (Doubao Seed 2.1 Pro 旗舰 advertised 能力, 同 2.0 Pro 家族)
            text=True,
            vision=True,
            audio=False,
            video=False,
            tool_calling=True,
            streaming=True,
            json_mode=True,
            reasoning=True,  # Doubao Seed Pro 是推理模型 (2.0 实测有 reasoning_content)
            context_window=262144,
            max_output_tokens=16384,
            # verified_* (6 维跟踪); V37.9.217 Mac Mini E2E 实测 5 项通过 → flip True
            verified_text=True,          # E2E: content="0.05" bat-ball 答对 + finish_reason=stop
            verified_vision=True,        # E2E: 湖面/橙色皮划艇/针叶林/覆雪山脉 全命中
            verified_tool_calling=True,  # E2E: finish_reason=tool_calls + get_weather({"city":"东京"})
            verified_streaming=True,     # E2E: chat.completion.chunk 流 + finish_reason=stop + [DONE]
            verified_fallback=False,     # 未真生产 fallback 接管 (同 doubao 2.0/deepseek 惯例)
            verified_reasoning=True,     # E2E: reasoning_content 通道 + reasoning_tokens=255
            # feature_verified: 分项 E2E 实测通过但未真生产流量 (tier_evidence 必须显式引用证据)
            verification_tier="feature_verified",
            tier_evidence="Mac Mini E2E 实测 2026-07-02: text/vision/tool_calling/streaming/reasoning 5/5 通过 "
                          "(content=0.05 bat-ball 答对+finish_reason=stop / 湖面皮划艇针叶林覆雪山脉全命中 / "
                          "finish_reason=tool_calls+get_weather arguments / chunk 流+[DONE] / "
                          "reasoning_content 通道+reasoning_tokens=255)；无乱码 token (优于 deepseek 量化版 w4a8)；"
                          "json_mode 声明未单测 / 未真生产 fallback 接管",
        )

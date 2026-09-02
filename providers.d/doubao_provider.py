"""V37.9.52/53/54/55 — Doubao Seed 2.0 Pro Provider → V37.9.290 平台切换 ai-tokenhub
   → V37.9.339 🔴 槽位换模型: doubao-seed-2.0-pro-huakun → kimi-k3-260716 (Kimi K3)

🔴 命名债 (诚实登记, 一物一形 #34 规则 2 的已知例外):
  registry name `doubao` / env `DOUBAO_API_KEY` / 类名 DoubaoSeedProvider 是**历史槽位名**,
  V37.9.339 起该槽位实际服务 **Kimi K3 (kimi-k3-260716 via ai-tokenhub)**。
  保留旧名的理由 = 零 blast radius: 生产 plist 的 FALLBACK_ORDER=deepseek_full,doubao,...
  不用改、env 名不用改 (只换值)、expert_escalation 的 DOUBAO_* 常量族 + 治理
  INV-PLIST-ENV-001/INV-PROXY-PLIST-ENV-001 的 DOUBAO_API_KEY 集合不用动。
  代价 = adapter.log 里 "FALLBACK doubao OK" 实为 Kimi。若用户决定改名 (kimi_k3 /
  KIMI_K3_API_KEY), 那是独立的一次 rename (镜像 V37.9.290 规模 ~45 测试 + 用户 plist
  FALLBACK_ORDER 一处编辑), 本版刻意不做。

接入第 8 个 provider 槽位, 当前模型 kimi-k3-260716 (Moonshot Kimi K3, ai-tokenhub 托管)。
区别于第 5 个 built-in `kimi` (Moonshot 官方 api.moonshot.ai, MOONSHOT_API_KEY, kimi-k2.5)。

设计契约:
- API key 严格走 env DOUBAO_API_KEY (历史 env 名; V37.9.339 起**值**换成 Kimi K3 专属
  key — 不可硬编码, 即便用户豁免也守公开 repo 安全底线)
- base_url = https://ai-tokenhub.com/api/v1 (公开域名可入库, 与 deepseek_full/glm5_coding 同网关)
- dev 环境无 env → ProviderRegistry.available() 因缺 DOUBAO_API_KEY 自动排除
- ai-tokenhub 无 endpoint-ID 间接层 (ARK_ENDPOINT_ID 自 V37.9.290 起不再消费), model 名直接进请求体

版本史: V37.9.52 接入 / V37.9.53 text+reasoning E2E / V37.9.54 vision E2E (image_url content
block) / V37.9.55 tool_calling+streaming E2E (finish_reason=tool_calls / SSE chunks) / V37.9.289 更名 / V37.9.290 平台切换 ai-tokenhub /
V37.9.291 tokenhub E2E text+reasoning / **V37.9.339 换模型 Kimi K3**。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = declared** (V37.9.339 重置) — Kimi K3 是完全不同的模型 (不同厂商),
  Doubao Seed 2.0 Pro 时代的全部证据 (Ark 5/5 + tokenhub 2/2 + production_observed) **不迁移**。
- capability 声明**保守**: 无 Kimi K3 一手文档 → 只声明 OpenAI /v1 基线 (text/tool_calling/
  streaming); vision/json_mode/reasoning 未声明 (未声明 ≠ 不支持, 探针实测后翻案 —
  镜像 glm5_coding V37.9.258→291 reasoning 翻案先例)。**vision 刻意不声明**: V37.9.218
  capability-aware vision fallback 会把 image 请求路由到声明 vision 的 fallback, 未实测
  的 image_url 透传若 400 会打断多模态降级链 (under-declare 是安全方向)。
- reasoning_off_body **不声明** (None): `thinking` 片段是 Ark/DeepSeek 家族参数, Kimi 未
  实测; V37.9.224 已登记未测参数可能 400 打断 fallback 的风险 → 不注入 = Kimi 以默认
  行为服务 batch fallback (宁慢勿断)。
- context_window=262144: Kimi 家族基线 (built-in kimi-k2.5 同值), K3 待实测。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class DoubaoSeedProvider(BaseProvider):
    # 🔴 name/api_key_env 是历史槽位名 (见模块 docstring "命名债"), 当前模型 = Kimi K3
    name = "doubao"
    display_name = "Kimi K3 (ai-tokenhub)"
    base_url = "https://ai-tokenhub.com/api/v1"
    api_key_env = "DOUBAO_API_KEY"
    auth_style = "bearer"
    # V37.9.339: 不声明 reasoning_off_body — Kimi 未实测 thinking 参数 (见 docstring)。
    reasoning_off_body = None
    models = [
        ModelInfo(
            # V37.9.339 (2026-09-02 用户变更): doubao-seed-2.0-pro-huakun → kimi-k3-260716
            model_id="kimi-k3-260716",
            display_name="kimi-k3-260716 (Kimi K3)",
            modalities=["text"],
            context_window=262144,     # Kimi 家族基线, K3 待实测
            max_output_tokens=16384,   # 保守占位, 待实测
            is_default=True,
        ),
    ]
    capabilities = ProviderCapabilities(
        text=True,
        vision=False,          # 未实测不声明 (探针后翻案; 见 docstring vision 说明)
        audio=False,
        video=False,
        tool_calling=True,     # OpenAI /v1 基线 (Kimi 家族支持 tool calling)
        streaming=True,        # OpenAI /v1 基线
        json_mode=False,       # 未实测不声明 (V37.9.254 over-declare 教训)
        reasoning=False,       # 未实测不声明 (K3 若有 reasoning 通道, 探针后翻案)
        context_window=262144,
        max_output_tokens=16384,
        # V37.9.339 换模型重置: 全部 verified_* False
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_reasoning=False,
        verified_fallback=False,
        verification_tier="declared",
        tier_note="2026-09-02 槽位换模型 Kimi K3 (V37.9.339), Doubao 时代证据不迁移, ai-tokenhub E2E 待 Mac Mini",
    )

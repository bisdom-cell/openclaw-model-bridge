"""V37.9.52/53/54/55 — Doubao Seed 2.0 Pro Provider → V37.9.290 平台切换 ai-tokenhub
   → V37.9.339 槽位曾换 Kimi K3 (kimi-k3-260716)
   → V37.9.341 🔴 更正: 该 key 的 scope 实为 doubao-seed-2-1-pro-260628, 不含 kimi-k3
                 → 槽位改指向 key 真能服务的模型 = Doubao Seed 2.1 Pro @ ai-tokenhub

🔴 为什么本槽位与 doubao_21 是同一个模型 (刻意的, 不是重复 — 未来 session 勿"去重"):
  `doubao_21` = doubao-seed-2-1-pro-260628 @ **Volcengine Ark** (生产 primary, ARK_21_API_KEY)
  `doubao`    = doubao-seed-2-1-pro-260628 @ **ai-tokenhub**    (fallback #2, DOUBAO_API_KEY)
  同模型 × 两个独立 serving 平台 = **平台冗余**: Ark 侧故障 (网关/计费/区域) 时同一模型仍可
  经 ai-tokenhub 达到。这是链上唯一的平台维度冗余 (其余 fallback 都是换模型)。
  代价诚实登记: 模型**本身**的问题 (能力边界/幻觉/该模型下线) 不会被本槽位兜住 —— 兜那种
  故障的是链上的 deepseek/qwen。

🔴 命名债 (诚实登记, 一物一形 #34 规则 2 的已知例外):
  registry name `doubao` / env `DOUBAO_API_KEY` / 类名 DoubaoSeedProvider 是**历史槽位名**
  (V37.9.52 接入 Doubao Seed 2.0 Pro 时命名)。现服务 Doubao Seed **2.1** Pro —— 厂商对了,
  版本号不精确, 且与 `doubao_21` 名字易混。保留旧名 = 零 blast radius (生产 plist 的
  FALLBACK_ORDER=deepseek_full,doubao,… 不改、env 名不改只换值、expert_escalation 的
  DOUBAO_* 常量族 + 治理 INV-PLIST-ENV-001/INV-PROXY-PLIST-ENV-001 的 DOUBAO_API_KEY
  集合不动)。若用户要改名 (如 doubao_21_tokenhub), 是独立一次 rename。

设计契约:
- API key 严格走 env DOUBAO_API_KEY (不可硬编码, 即便用户豁免也守公开 repo 安全底线)
- base_url = https://ai-tokenhub.com/api/v1 (公开域名可入库, 与 deepseek_full/glm5_coding 同网关)
- dev 环境无 env → ProviderRegistry.available() 因缺 DOUBAO_API_KEY 自动排除
- ai-tokenhub 无 endpoint-ID 间接层 (ARK_ENDPOINT_ID 自 V37.9.290 起不再消费), model 名直接进请求体
  ⚠️ 与 doubao_21 的关键差异: 后者的 model 字段收 Ark 接入点 ID (ep-...), 本槽位收 model 名。

版本史: V37.9.52 接入 2.0 Pro / V37.9.53 text+reasoning E2E / V37.9.54 vision E2E (image_url
content block) / V37.9.55 tool_calling+streaming E2E (finish_reason=tool_calls / SSE chunks) /
V37.9.289 更名 -huakun / V37.9.290 平台切换 ai-tokenhub / V37.9.291 tokenhub E2E text+reasoning /
V37.9.339 换 Kimi K3 / **V37.9.341 更正为 doubao-seed-2-1-pro-260628 (key scope 实证)**。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = declared** — 能力**声明**镜像 doubao_21 (同一个模型, 模型本体能力
  由 V37.9.217 Ark E2E 5/5 实证); **verified_* 全 False** 因为**平台不同** (ai-tokenhub Bifrost
  网关 vs Volcengine Ark 原生 = 不同 serving 栈, 证据不跨平台迁移 — V37.9.290/339 同款纪律)。
- Mac Mini E2E 复测通过后逐项 flip。特别待测: vision (image_url 经 Bifrost 网关透传) /
  tool_calling / streaming。
- reasoning_off_body 声明: 模型侧 Ark 实测 thinking:disabled 生效 (V37.9.222, reasoning_tokens
  0 + 17.7s vs 166s); 网关侧 deepseek_full 在**同一个 ai-tokenhub Bifrost 网关**实测该片段生效
  (V37.9.222 B1)。两侧证据交汇 → 声明; 若 E2E 探针发现 400 则退役本声明。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class DoubaoSeedProvider(BaseProvider):
    # 🔴 name/api_key_env 是历史槽位名 (见模块 docstring "命名债"), 当前模型 = Doubao Seed 2.1 Pro
    name = "doubao"
    display_name = "Doubao Seed 2.1 Pro (ai-tokenhub)"   # 区别于 doubao_21 的 "(Volcengine Ark)"
    base_url = "https://ai-tokenhub.com/api/v1"
    api_key_env = "DOUBAO_API_KEY"
    auth_style = "bearer"
    # 模型侧 Ark 实测 + 网关侧 deepseek_full 在同网关实测, 两侧证据交汇 (见 docstring)
    reasoning_off_body = {"thinking": {"type": "disabled"}}
    models = [
        ModelInfo(
            # V37.9.341 (2026-09-02): key scope 实证 → doubao-seed-2-1-pro-260628
            # (V37.9.339 曾写 kimi-k3-260716, 但该 key 的 /v1/models scope 不含它 →
            #  InsufficientScope; scope 里唯一的模型就是本行)
            model_id="doubao-seed-2-1-pro-260628",
            display_name="doubao-seed-2-1-pro-260628 (ai-tokenhub 路径)",
            modalities=["text", "vision"],
            context_window=262144,
            max_output_tokens=16384,
            is_default=True,
            is_vision=True,
        ),
    ]
    # 能力声明 = 模型本体能力 (与 doubao_21 同模型, 由其 V37.9.217 Ark E2E 5/5 实证);
    # verified_* = **本平台 (ai-tokenhub)** 的实证 → 全 False 待探针 (平台证据不迁移)。
    capabilities = ProviderCapabilities(
        text=True,
        vision=True,           # 模型多模态; Bifrost 网关 image_url 透传待实测
        audio=False,
        video=False,
        tool_calling=True,
        streaming=True,
        json_mode=True,
        reasoning=True,        # Doubao Seed Pro 是推理模型 (Ark 实测 reasoning_content 通道)
        context_window=262144,
        max_output_tokens=16384,
        # V37.9.341 平台维度重置: ai-tokenhub 路径零实证, E2E 复测后逐项 flip
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_reasoning=False,
        verified_fallback=False,
        verification_tier="declared",
        tier_note="2026-09-02 槽位更正为 doubao-seed-2-1-pro-260628 @ ai-tokenhub (V37.9.341, key scope 实证), 平台维度零实证待 Mac Mini E2E",
    )

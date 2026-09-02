"""V37.9.52/53/54/55 — Doubao Seed 2.0 Pro Provider → V37.9.290 平台切换 ai-tokenhub
   → V37.9.339 槽位曾换 Kimi K3 (kimi-k3-260716)
   → V37.9.341 🔴 更正: 该 key 的 scope 实为 doubao-seed-2-1-pro-260628, 不含 kimi-k3
                 → 槽位改指向 key 真能服务的模型 = Doubao Seed 2.1 Pro @ ai-tokenhub
   → V37.9.342 Mac Mini E2E (ai-tokenhub 路径): tool_calling + reasoning 2 项实测 →
                 feature_verified; verified_text 当时刻意仍 False (探针带 tools → content 空)
   → V37.9.343 补跑**不带 tools** 的纯文本探针 → verified_text 达标 flip True (3/6 verified)

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
- **verification_tier = feature_verified** (V37.9.342) — 能力**声明**镜像 doubao_21 (同一个模型,
  模型本体能力由 V37.9.217 Ark E2E 5/5 实证); **verified_\* 按本平台 (ai-tokenhub) 实测逐项 flip**,
  Ark 侧证据不跨 serving 栈迁移 (V37.9.290/339/341 同款纪律)。
- **V37.9.342 Mac Mini E2E 实测 (verified True)**: tool_calling (finish_reason=tool_calls +
  tool_calls[] 长度 1) / reasoning (reasoning 字段填充; 注意 tokenhub 字段名是 `reasoning`,
  非 Ark 的 `reasoning_content` — V37.9.291 同款观察)。usage 466/94, 响应 model 回显本 model id。
- **V37.9.343 text 补齐**: 不带 tools 的纯文本探针 → content 4 字符 (bat-ball 答案) +
  finish_reason=stop + reasoning 字段, usage 76/247 → **verified_text flip True**。
  🔴 **为什么要单独跑这一针 (durable lesson, 勿删)**: V37.9.342 那轮探针带 tools, 模型选择
  调工具而非作答 → content 为空 + finish_reason=tool_calls。本项目对 verified_text 的既定
  证据标准是「有 content + finish_reason=stop」(doubao_21 V37.9.217 / deepseek_full+glm5
  V37.9.340 皆如此)。**一次成功的 tool_calls 响应证明的是 tool_calling, 不是 text** ——
  把「模型工作正常」笼统当成「每一维都验证过」正是 verified_* 四档机制要防的事 (原则 #23)。
- 仍待测: vision (image_url 经 Bifrost 网关透传) / streaming / json_mode。
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
        # V37.9.341 平台维度重置 → V37.9.342 ai-tokenhub 路径 E2E 逐项 flip
        verified_text=True,          # V37.9.343 纯文本探针: content 4 字符 + finish_reason=stop
                                     #   (V37.9.342 那轮带 tools 故 content 空, 见 docstring 说明)
        verified_vision=False,       # Bifrost 网关 image_url 透传未测
        verified_tool_calling=True,  # V37.9.342 E2E: finish_reason=tool_calls + tool_calls 长度 1
        verified_streaming=False,    # 本平台未单测
        verified_reasoning=True,     # V37.9.342 E2E: reasoning 字段填充 (tokenhub 字段名 reasoning)
        verified_fallback=False,     # 未真生产 fallback 接管 (本平台)
        verification_tier="feature_verified",
        tier_note="2026-09-02 槽位更正 doubao-2.1 @ ai-tokenhub (V37.9.341) 后 E2E 升档 (V37.9.342 tool_calling+reasoning, V37.9.343 补 text)",
        tier_evidence="ai-tokenhub E2E 探针 2026-09-02 (V37.9.342, model=doubao-seed-2-1-pro-260628): "
                      "tool_calling+reasoning 2/2 通过 (finish_reason=tool_calls + tool_calls 长度 1 + "
                      "reasoning 字段填充, usage 466/94, 响应 model 回显本 model id = ai-tokenhub 路由正确)；"
                      "V37.9.343 补跑不带 tools 的纯文本探针: content 4 字符 + finish_reason=stop + "
                      "reasoning 字段, usage 76/247 → verified_text 达标 flip True "
                      "(V37.9.342 那轮带 tools 故 content 为空, 够不着「有 content + finish_reason=stop」标准)；"
                      "vision/streaming/json_mode 本平台未测; Ark 侧 doubao_21 的 5/5 证据不跨平台迁移",
    )

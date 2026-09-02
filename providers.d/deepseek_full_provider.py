"""V37.9.204 — DeepSeek-V4-Pro 满血版 Provider (ai-tokenhub 托管, 非量化候选)
   V37.9.205 — Mac Mini E2E 实测: text/tool_calling/reasoning 3/3 → feature_verified
   V37.9.289 — 更名 deepseek-v4-pro-260425 → deepseek-v4-pro-huakun (同端点同 key, tier 保留)
   V37.9.339 — 🔴 槽位换模型 (2026-09-02 用户指令, 后台 LLM Provider 刷新):
               deepseek-v4-pro-huakun → deepseek-v4-pro-ga-260813 (GA 版), 同网关新 key。
               **不是更名, 是换模型** → 旧证据不迁移, tier 诚实回 declared, E2E 复测后逐项升档。
   V37.9.340 — Mac Mini E2E 2026-09-02: text+reasoning 2/2 通过 → feature_verified (半升档)。

接入第 10 个 provider: deepseek-v4-pro-ga-260813 (满血版 GA, via ai-tokenhub API hub)。
定位 = 生产 fallback 链首 (FALLBACK_ORDER=deepseek_full,doubao,deepseek,qwen, V37.9.218)。

⚠️ 运维注意: 满血版是推理模型, 先生成 reasoning 再生成 content (reasoning 占 token 预算)。
生成任务须给足 max_tokens, 否则 content 可能空/截断 (E2E 实测 600 tokens 被 reasoning 吃掉)。

🔴 安全契约 (镜像 doubao/deepseek V37.9.52 公开 repo 安全底线):
- **API key 严格走 env `DEEPSEEK_FULL_API_KEY`** — 绝不硬编码。用户在对话里贴的明文 key
  (sk-...) 不入库, 即便用户豁免也守公开 repo 安全底线。V37.9.339 起该 env 的**值**换成
  GA 模型专属的新 key (Mac Mini plist + .env_shared 替换值, env 名不变)。
- **base_url = `https://ai-tokenhub.com/api/v1`** — 公开域名 (非裸 IP/路径 token, 不含机密),
  可入库 (与 openai/claude/doubao 等公开 base_url 同理)。无 key 时 available() 自动排除。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = feature_verified** (V37.9.340 半升档) — GA 是不同的模型构建, -huakun
  别名时代的证据 (V37.9.205 3/3 / V37.9.291) **不迁移**; V37.9.339 重置 declared 后经 Mac Mini
  E2E 2026-09-02 实测 text+reasoning 2/2 重新挣得 (tool_calling/streaming 未探针保持 False)。
- capability 声明保持 DeepSeek V4 Pro 家族画像 (text/tool_calling/streaming/reasoning True;
  vision False 家族无视觉; json_mode False 围栏非严格), 与 verified_* 解耦。
- verified_* 全 False, Mac Mini E2E 复测通过后按探针结果逐项 flip。
- context_window = 1M (V37.9.207 端点规格, 假定 GA 沿用; 待实测); max_output_tokens=8192 保守占位。

历史证据 (供复测对照, 不作当前档位依据):
- V37.9.205 (deepseek-v4-pro-260425): text/tool_calling/reasoning 3/3, 无乱码 token,
  R1 reasoning 通道 reasoning_tokens=55; vision 400; json_mode 围栏非严格。
- V37.9.222/291 (ai-tokenhub Bifrost 网关): thinking:disabled 生效 / reasoning 字段名
  为 `reasoning` (非 Ark 的 reasoning_content)。

OpenAI 兼容: base_url 以 /v1 结尾 + `Authorization: Bearer` (auth_style=bearer 默认)。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class DeepSeekFullProvider(BaseProvider):
    name = "deepseek_full"
    display_name = "DeepSeek-V4-Pro 满血版 (ai-tokenhub)"
    base_url = "https://ai-tokenhub.com/api/v1"   # 公开域名, 非机密
    api_key_env = "DEEPSEEK_FULL_API_KEY"
    auth_style = "bearer"
    # V37.9.222 B1: ai-tokenhub 用 Bifrost 网关归一化 thinking 参数 (2026-07-02 实测
    # thinking:disabled → completion_tokens_details 空 + content 完整; enable_thinking:false
    # 被忽略)。V37.9.339 保留: 同网关 + 同 DeepSeek V4 Pro 家族 (网关层归一化, 与具体
    # 构建无关), GA 模型上待复测; 若 GA 对该片段 400 则退役本声明。
    reasoning_off_body = {"thinking": {"type": "disabled"}}
    models = [
        ModelInfo(
            # V37.9.339 (2026-09-02 用户变更): deepseek-v4-pro-huakun → deepseek-v4-pro-ga-260813
            # (同网关 ai-tokenhub, 新 key; ai-tokenhub 无 endpoint-ID 间接层, 本 model 名直接进请求体)。
            model_id="deepseek-v4-pro-ga-260813",
            display_name="deepseek-v4-pro-ga-260813 (满血版 GA)",
            modalities=["text"],
            context_window=1048576,    # V37.9.207 端点规格 1M (假定 GA 沿用, 待实测)
            max_output_tokens=8192,    # 保守占位, 待实测
            is_default=True,
        ),
    ]
    # 🔴 能力声明 = DeepSeek V4 Pro 家族画像 (原则 #23: 声明与 verified_* 解耦)。
    capabilities = ProviderCapabilities(
        text=True,
        vision=False,          # DeepSeek V 系无视觉 (V37.9.205 实测 400, 家族性质)
        audio=False,
        video=False,
        tool_calling=True,     # 家族支持 (V37.9.205 -huakun 时代实测过)
        streaming=True,        # OpenAI /v1 标准基线
        json_mode=False,       # 家族 response_format 返回围栏非严格 → 不声明
        reasoning=True,        # 满血版 R1 reasoning 通道 (家族性质, V37.9.205/291 实测过)
        context_window=1048576,
        max_output_tokens=8192,
        # V37.9.339 换模型重置 → V37.9.340 Mac Mini E2E 逐项 flip (text+reasoning)
        verified_text=True,          # V37.9.340 E2E 2026-09-02: 200 + finish_reason=stop + 正确 content
        verified_vision=False,       # 家族无视觉
        verified_tool_calling=False, # GA 模型未探针
        verified_streaming=False,    # GA 模型未探针
        verified_fallback=False,     # 未真生产 fallback 接管 (GA 时代)
        verified_reasoning=True,     # V37.9.340 E2E: reasoning 字段填充 (145 completion tokens 含推理)
        verification_tier="feature_verified",
        tier_note="2026-09-02 槽位换 GA 模型 (V37.9.339) 后 E2E 半升档 (text+reasoning, V37.9.340)",
        tier_evidence="ai-tokenhub E2E 探针 2026-09-02 (V37.9.340, model=deepseek-v4-pro-ga-260813): "
                      "text+reasoning 2/2 通过 (200 + finish_reason=stop + bat-ball 题 4 字符答案 + reasoning "
                      "字段填充, usage 113/145, 响应 model 回显 GA 名 = 路由正确)；"
                      "tool_calling/streaming 未在 GA 模型探针保持 False; -huakun 时代史见 docstring",
    )

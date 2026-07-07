"""V37.9.254 — GLM-5.2 Coding Provider (ai-tokenhub 托管, coding 专用按需调用)

接入第 12 个 provider: glm-5-2-260617 (GLM-5.2, via ai-tokenhub API hub, 同 deepseek_full host).
定位 = **coding 场景专用**——有需要编码的场景时按需显式调用 (`?provider=glm5_coding`),
不是 primary 也不默认进 auto-fallback 链 (由 Mac Mini FALLBACK_ORDER env 控制是否纳入)。
区别于第 7 个 built-in `glm` (Zhipu open.bigmodel.cn) —— 本 provider 是 ai-tokenhub 托管的
GLM-5.2, 独立 endpoint + 独立 key。

🔴 安全契约 (镜像 deepseek_full/doubao_21 V37.9.204/216 公开 repo 安全底线):
- **API key 严格走 env `GLM5_API_KEY`** — 绝不硬编码。用户在对话里贴的明文 key (sk-...)
  不入库, 即便用户豁免也守公开 repo 安全底线。建议用完轮换 (对话历史可能被记录)。
- **base_url = `https://ai-tokenhub.com/api/v1`** — 公开域名 (非裸 IP/路径 token, 不含机密),
  可入库 (与 deepseek_full 同 host, 同理可入库)。无 key 时 available() 自动排除 (dev 优雅降级)。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **verification_tier = declared** — 能力声明来自 GLM-5 系文档 + OpenAI 兼容基线, **0 生产验证
  (dev 无 key 无法 E2E)**。verified_* 全 False。tier_evidence 留空走 _DECLARED_TIER_EVIDENCE 派生
  (单一真理源)。待 Mac Mini E2E 实测 (text/tool_calling/streaming/json_mode) 后逐项 flip verified_*
  + 升 tier (镜像 deepseek V37.9.201→205 / doubao_21 V37.9.216→217 渐进验证路径)。
- 声明的能力 (未实测, coding 模型典型集): text / tool_calling / streaming / json_mode。
- **未声明 / 保守 False**:
    vision    — coding 文本模型, 非多模态 (GLM-5V 是独立模型) → False
    reasoning — GLM-5 系有 thinking, 但本 endpoint 是否暴露 reasoning_content 未实测 → 保守 False
  (故不设 reasoning_off_body: 未验证 reasoning 不声明 batch-reasoning-off, 原则 #23)。
- context_window = 131072 (128K, GLM-5 系典型值保守占位, 待端点规格/实测确认; 描述性 metadata
  不影响路由评分, 同 deepseek 原 65536 占位惯例)。

OpenAI 兼容: base_url 以 /v1 结尾 + `Authorization: Bearer` (auth_style=bearer 默认)。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class Glm5CodingProvider(BaseProvider):
    name = "glm5_coding"
    display_name = "GLM-5.2 Coding (ai-tokenhub)"
    base_url = "https://ai-tokenhub.com/api/v1"   # 公开域名, 非机密
    api_key_env = "GLM5_API_KEY"
    auth_style = "bearer"
    models = [
        ModelInfo(
            model_id="glm-5-2-260617",
            display_name="glm-5-2-260617 (GLM-5.2 Coding)",
            modalities=["text"],
            context_window=131072,     # 128K 保守占位, 待端点规格/实测确认
            max_output_tokens=8192,    # 保守占位, 待实测
            is_default=True,
        ),
    ]
    # 🔴 能力声明 (原则 #23 — declared tier: 来自 GLM-5 系文档 + OpenAI 兼容基线, 0 生产验证):
    # coding 典型集 text/tool_calling/streaming/json_mode declared True; vision/reasoning 保守 False;
    # verified_* 全 False (dev 无 key 无法 E2E), tier_evidence 留空走派生。
    capabilities = ProviderCapabilities(
        text=True,
        vision=False,          # coding 文本模型, GLM-5V 是独立模型
        audio=False,
        video=False,
        tool_calling=True,     # coding agent 用工具 (declared, 未实测)
        streaming=True,        # OpenAI /v1 标准基线 (declared, 未实测)
        json_mode=True,        # coding 常需结构化输出 (declared, 未实测)
        reasoning=False,       # GLM-5 系有 thinking 但本 endpoint 未实测暴露 → 保守 False
        context_window=131072,
        max_output_tokens=8192,
        # verified_* 全 False (declared tier: 0 生产验证, dev 无 key)
        verified_text=False,
        verified_vision=False,
        verified_tool_calling=False,
        verified_streaming=False,
        verified_fallback=False,
        verified_reasoning=False,
        # declared: 能力声明 + 合约校验, 0 生产验证 (tier_evidence 留空走 _DECLARED_TIER_EVIDENCE 派生)
        verification_tier="declared",
    )

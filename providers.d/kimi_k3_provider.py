"""V37.9.345 — Kimi K3 Provider (ai-tokenhub 托管, 第 13 个 provider)

接入 `kimi-k3-260716` (Moonshot Kimi K3, via ai-tokenhub API hub)。

来历 (原则 #23 渐进验证的完整走程, 值得记住因为它绕了一圈):
- V37.9.339 用户指令把 `doubao` 槽位换成 Kimi K3 —— 但当时 env 名仍叫 DOUBAO_API_KEY,
  用户自然往里填了 doubao 的 key → 探针 InsufficientScope → 我连续三版误判为「供应商
  key 与模型名不匹配」(V37.9.341/342/343)。
- V37.9.344 keymap 实测四把 key 的 scope: 供应商标注**逐条正确**, 错的是我的命名债 ——
  Kimi 的 key (scope 实测 = kimi-k3-260716) 从未被部署到任何槽位。
- V37.9.345 用户决策: Kimi K3 接入为**独立的第 13 个 provider**, 同时把 `doubao` 槽位
  改名 `doubao_21_tokenhub` 退役命名债。**独立 env `KIMI_K3_API_KEY` 让「哪把 key 属于
  哪个模型」在变量名上就是自明的** —— 这正是 V37.9.344 血案的结构性修复。

区别于第 5 个 built-in `kimi` (Moonshot 官方 api.moonshot.ai, MOONSHOT_API_KEY, kimi-k2.5):
本 provider 是 ai-tokenhub 托管的 K3, 独立 endpoint + 独立 key。

设计契约:
- 🔴 API key 严格走 env `KIMI_K3_API_KEY` (不可硬编码, 即便用户豁免也守公开 repo 安全底线)
- base_url = https://ai-tokenhub.com/api/v1 (公开域名可入库, 与 deepseek_full/glm5_coding/
  doubao_21_tokenhub 同网关)
- dev 环境无 env → ProviderRegistry.available() 因缺 KIMI_K3_API_KEY 自动排除

定位: **不是 primary, 也不默认进 auto-fallback 链** —— 是否纳入由 Mac Mini 的
`FALLBACK_ORDER` env 控制 (V37.9.218 权威), 或 `?provider=kimi_k3` 显式调用。

诚实语义 (原则 #23 — 只声明实测过的能力):
- **V37.9.346 Mac Mini E2E 实测 3/3 通过 → declared 升 feature_verified**:
  text (finish_reason=stop + content "$0.05" 正确) / tool_calling (finish_reason=tool_calls
  + get_weather{"city":"Tokyo"}) / streaming (478 SSE 行)。
- 🔴 **reasoning 翻案**: V37.9.345 保守声明 `reasoning=False` (无一手文档), 但 text 探针
  实测返回 `reasoning` 字段 + `reasoning_tokens=190` → **K3 有独立 reasoning 通道**,
  声明改 True + verified_reasoning=True。这正是「未声明 ≠ 不支持, 探针实测后翻案」的
  兑现 (镜像 glm5_coding V37.9.258→291 同款先例)。
- **vision / json_mode 未探测 → 保持 False** (「探针通过了」不等于「全绿」,
  V37.9.342 忍住不翻 verified_text 的同款纪律; 守卫用 assertNotIn 钉死)。
- **vision 刻意不声明**: V37.9.218 capability-aware vision fallback 会把 image 请求路由到
  声明 vision 的 fallback; 未实测的 image_url 透传若 400 会打断多模态降级链 —— under-declare
  是安全方向 (V37.9.339 同款判断)。
- **reasoning_off_body 仍不声明 (None)**: 即便 V37.9.346 证实 K3 有 reasoning 通道, `thinking`
  片段是 Ark/DeepSeek 家族的**关-reasoning 请求体参数**, 与「有没有 reasoning」是两件事 ——
  未在 Kimi 实测, V37.9.224 已登记「未测参数可能 400 打断 fallback」→ 不注入。
  (kimi_k3 不在生产 FALLBACK_ORDER 内, 无批量路径会撞上 reasoning 延迟。)
- context_window=262144: Kimi 家族基线 (built-in kimi-k2.5 同值), K3 待实测。
"""
from providers import BaseProvider, ModelInfo, ProviderCapabilities


class KimiK3Provider(BaseProvider):
    name = "kimi_k3"
    display_name = "Kimi K3 (ai-tokenhub)"   # 区别于 built-in kimi 的 "(Moonshot AI)"
    base_url = "https://ai-tokenhub.com/api/v1"
    api_key_env = "KIMI_K3_API_KEY"
    auth_style = "bearer"
    # V37.9.345: 不声明 reasoning_off_body — thinking 参数未在 Kimi 实测 (见 docstring)
    reasoning_off_body = None
    models = [
        ModelInfo(
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
        vision=False,          # 未实测不声明 (见 docstring vision 说明)
        audio=False,
        video=False,
        tool_calling=True,     # OpenAI /v1 基线
        streaming=True,        # OpenAI /v1 基线
        json_mode=False,       # 未实测不声明 (V37.9.254 over-declare 教训)
        reasoning=True,        # V37.9.346 实测翻案: reasoning 字段 + reasoning_tokens=190
        context_window=262144,
        max_output_tokens=16384,
        # V37.9.346 Mac Mini E2E: 只 flip 实测过的四项; vision/json_mode 未探测保持 False
        verified_text=True,
        verified_vision=False,
        verified_tool_calling=True,
        verified_streaming=True,
        verified_reasoning=True,
        verified_fallback=False,   # 未真在生产 fallback 链中接管过
        verification_tier="feature_verified",
        tier_evidence="V37.9.346 Mac Mini E2E 3/3: text(stop + content '$0.05') / tool_calling(finish_reason=tool_calls + get_weather Tokyo) / streaming(478 SSE 行); reasoning 字段 + reasoning_tokens=190 证 reasoning 通道 (V37.9.345 保守 False 经实测翻案). vision/json_mode 未探测保持 False",
    )

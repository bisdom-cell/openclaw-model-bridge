#!/usr/bin/env python3
"""llm_observer.py — LLM-Observer 两层管道 fail-plausible 检测 (研究攻关 #1 Stage 2-5).

机械化人眼 (论文 arXiv:2606.14589 §5.2 开放问题): 读一段【面向用户的输出】判定它是否
fail-plausible (读起来可信但实际错误/编造)。两层管道:

  Layer 1 (Stage 2, 确定性, 零 LLM/零网络): S1-S5 lexical/结构 pre-filter, 复用 seeds。
  Layer 2 (Stage 3, LLM-judge): FAIL_PLAUSIBLE_SYSTEM (grounding/intent/pollution/fab 4 维)
          复用 daily_observer.call_llm_critique infra + 注入 LEVEL_6/credibility, 只在
          Layer 1 命中或 force_judge 时触发 (便宜路径: 干净输出不调 LLM)。

Layer 1 在前 (design §3.2) = 成本 + 可解释 (引用具体证据 locus/snippet) + 防 Observer
自身幻觉 (确定性信号是 LLM 判断的锚, V37.9.93 sampling 幻觉教训)。

五确定性信号 (docs/llm_observer_ground_truth.yaml signals 字典):
  S1 pollution_signal      错误码/HTTP/工具名/'Bad JSON'/告警 artifact 出现在用户内容 (D1/D2 指纹)
  S2 credibility_mismatch  低档来源被高档措辞包装 (复用 source_credibility)
  S3 fabrication_phrase    hallucination_guards.get_blocked_phrases() 血案精确字眼 (D4 指纹)
  S4 provenance_gap        牵强等价/跨域因果断言无 [强证据]/[弱关联] 标注 (复用 LEVEL_6 契约)
  S5 coherence_structural  boilerplate 重复 / 全标题无正文 / 字段=分隔符 (D1/D3 指纹)

Layer 2 LLM-judge 四维 (design §3.1, 对齐 ground-truth layer2_* 信号):
  grounding          事实断言能否在源材料找到支撑 (→ unsupported_claim)
  intent_alignment   系统被要求做 X 但用户没问 X (D2 → fail_plausible)
  pollution_evidence 系统 artifact 被当外部信号的语义判定 (D1 → pollution_signal)
  fabricated_success fallback 制造的 plausible 形状空壳 (D3 → fabricated_success)

🔴 反幻觉铁律 (§3.2/§5.3, 防 V37.9.93 Observer 自身幻觉): Layer 2 verdict 的每条证据必须
能在原文【逐字 ground】, 否则 drop。verdict=fail_plausible 但无 grounded 证据 → 降级为
clean (Observer 自己也是 LLM 组件, 继承全 taxonomy, 论文 §5.2)。

FAIL-OPEN 契约 (镜像 V37.4 / cross_source_signal_aggregator V37.9.46): 缺 seed
(source_credibility / hallucination_guards) 或 llm_caller 错误 → 该信号/Layer 2 返回 []
不阻塞, 绝不抛异。

FP 纪律 (论文 §5.4 FP 是一等度量, 原则 #32 噪声本身是问题): Layer 1 S1/S3 用强指纹,
S2/S4/S5 保守; Layer 2 不确定 → clean, 宁可漏报不误报 (漏的由 Category B 度量盖)。

Stage 边界: Layer 1 (Stage 2) + Layer 2 + orchestrator (Stage 3) + 自验证 harness
(Stage 4, llm_observer_selfcheck.py) + **scan_fail_plausible collection 级 wrapper
(Stage 5, daily_observer.run() 经 OBSERVER_FP_MODE shadow 默认接入)**。Stage 5 是首次
runtime 变更 → VERSION bump (shadow 模式 = 观察性, 不影响评分/告警, 需 Mac Mini E2E)。

CLI: python3 llm_observer.py --file out.md [--source dream] [--json]  (CLI 仅 Layer 1;
Layer 2 需网络, 库内由 DI 注入 caller, runtime 走 daily_observer)。
"""
import re
import sys

# ── 设计锁定常量 (sabotage 守卫这些值; 改阈值需更新 test) ───────────────────────

# S1 — 错误/日志 artifact 强指纹 (合法 synthesis push 里几乎不可能出现)
_S1_ERROR_PATTERNS = [
    r"Error code:\s*\d{3}",            # Python http.server 错误页 (D1)
    r"Bad JSON",                       # adapter 400 message (D1)
    r"JSONDecodeError",
    r"Traceback \(most recent call last\)",
    r"<!DOCTYPE HTML",                 # HTML 错误页 (D1)
    r"Error response",                 # http.server 错误页头
    r"Waiting\s*\d+\s*s?\s*before retry",   # 重试日志行 (D1 stdout 污染)
    r"Connection refused",
    r"\[Errno\s*\d+\]",
    r"</?tool_call>",                  # tool-call XML 泄漏
    # HTTP 状态码 + 明确错误 reason 同现 (单独数字不报, 防 FP)
    r"HTTP\s*(?:4\d{2}|5\d{2})\b",
    r"\b(?:4\d{2}|5\d{2})\b\s*(?:Bad Gateway|Bad Request|Internal Server Error|"
    r"Service Unavailable|Bad JSON|错误)",
    r"400\s*错误",                     # D1: "平台返回 'Bad JSON' 和 '400 错误'"
]
# S1 — 内部系统/告警 artifact (告警词汇泄漏进面向用户内容, D2 指纹)
_S1_INTERNAL_PATTERNS = [
    r"\[SYSTEM_ALERT\]",
    r"系统告警跟进",
    r"已收到系统告警",
    r"告警跟进任务",
    r"完全磁盘访问权限",               # D2: FDA 运维指令泄漏
    r"/usr/sbin/cron",
    r"cron_doctor\.sh",               # 内部诊断脚本名 (curated, 非泛 .sh 防 FP)
]

# S2 — 高档措辞 (把断言包装成权威) + 低档来源 rank 阈值
_S2_HIGH_TIER_CLAIMS = [
    "研究表明", "已证明", "实验证明", "数据证明", "证实了", "学术界共识",
    "权威研究", "科学证明", "研究证实",
]
_S2_LOW_TIER_RANK_MIN = 4   # source_credibility rank >= 4 = 博客/社媒 (1=学术最高)

# S4 — 牵强等价/过度关联成语 (pa_echo "异曲同工" 指纹) + 证据标注 (命中则取消 S4)
_S4_OVER_ASSOCIATION = [
    "异曲同工", "殊途同归", "如出一辙", "不谋而合", "异曲同工之妙",
    "本质上是一回事", "本质相同", "完全一致地对应",
]
_S4_EVIDENCE_TAGS = ["[强证据]", "[弱关联]", "[强关联]", "[直接证据]"]

# S5 — boilerplate 重复阈值 / 全标题启发式参数
_S5_BOILERPLATE_MIN_REPEAT = 3      # 同一非平凡行重复 >= 3 次 (dream_quota ×5)
_S5_BOILERPLATE_MIN_LEN = 6         # 行长 >= 6 字符才算 (排除 '---' 等)
# V37.9.199 (Mac Mini shadow E2E FP 修复): boilerplate 必须是【描述文本】重复 (fabrication
# 空壳, 如 dream_quota '要点：技术内容，详见原文' ×5), 不是【结构化评分字段】重复 (如
# freight '评级：⭐⭐⭐⭐' ×3, ⭐ 评级几乎所有摘要源标配, 合法重复)。要求重复行有足够
# 描述性字符 (CJK + 字母数字, 排除 emoji/⭐/标点) 才算 → rating 字段 (描述字符少) 不误报。
_S5_BOILERPLATE_MIN_DESCRIPTIVE = 6  # 重复行需 >= 6 描述性字符才算 boilerplate
_S5_HEADING_MIN_COUNT = 3           # >= 3 个 markdown 标题
_S5_HEADING_BODY_MAX_CHARS = 40     # 每标题对应正文 < 40 字 = 全标题无正文 (kb_review)
_S5_SEPARATOR_VALUES = {"---", "===", "───", "—", "...", "···", "n/a", "N/A", "—"}

# severity 映射 (对齐 daily_observer HIGH/MED/LOW)
_SEV_HIGH = "HIGH"
_SEV_MED = "MED"


def log(msg):
    """MR-11: 诊断写 stderr, 防 $(...) 命令替换污染 (D1 血案根因)。"""
    print(f"[llm_observer] {msg}", file=sys.stderr)


def _line_of(text, idx):
    """返回字符偏移 idx 所在的 1-based 行号 (locus)。"""
    return text.count("\n", 0, idx) + 1


def _descriptive_char_count(s):
    """计描述性字符数 (CJK 表意 + ASCII 字母数字), 排除 emoji/⭐/标点/符号。

    V37.9.199: 区分 fabrication 描述空壳 (描述文本重复) vs 结构化评分字段 (⭐/符号主导)。
    rating 字段值如 '⭐⭐⭐⭐' 描述字符=0; 描述句如 '技术内容详见原文' 描述字符多。
    """
    n = 0
    for ch in s:
        if ch.isascii() and ch.isalnum():
            n += 1
        elif "一" <= ch <= "鿿":   # CJK 统一表意文字
            n += 1
    return n


def _sig(signal, severity, locus, snippet):
    """构造一条信号 (machine-actionable, 带证据; Stage 5 映射进 daily_observer anomalies)。"""
    snip = snippet.strip()
    if len(snip) > 80:
        snip = snip[:77] + "…"
    return {"signal": signal, "severity": severity, "locus": locus, "snippet": snip}


# ── S1: pollution-signal ──────────────────────────────────────────────────────
def detect_pollution_signal(text):
    """S1: 错误码/HTTP/工具名/'Bad JSON'/告警 artifact 出现在面向用户内容 (D1/D2 指纹)。

    强指纹 (error/log) + 内部系统/告警 artifact。确定性 lexical, 零 LLM。
    """
    if not isinstance(text, str) or not text:
        return []
    out = []
    seen = set()
    for pat in _S1_ERROR_PATTERNS + _S1_INTERNAL_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            key = (m.group(0).lower(), _line_of(text, m.start()))
            if key in seen:
                continue
            seen.add(key)
            out.append(_sig("S1_pollution_signal", _SEV_HIGH,
                            _line_of(text, m.start()), m.group(0)))
    return out


# ── S2: credibility-mismatch ──────────────────────────────────────────────────
def detect_credibility_mismatch(text, source_id=None):
    """S2: 低档来源 (博客/社媒) 被高档措辞 ('研究表明'/'已证明') 包装。复用 source_credibility。

    FAIL-OPEN: 无 source_id 或 source_credibility 不可用 → [] (无从评估出处档位)。
    """
    if not isinstance(text, str) or not text or not source_id:
        return []
    try:
        import source_credibility
        cred = source_credibility.get_credibility(source_id)
        rank = cred.get("rank")
    except Exception as e:  # FAIL-OPEN (具体: ImportError / 数据缺失)
        log(f"S2 FAIL-OPEN (source_credibility unavailable): {e}")
        return []
    if not isinstance(rank, int) or rank < _S2_LOW_TIER_RANK_MIN:
        return []   # 高档来源用高档措辞合法, 不报
    out = []
    for claim in _S2_HIGH_TIER_CLAIMS:
        idx = text.find(claim)
        if idx >= 0:
            out.append(_sig("S2_credibility_mismatch", _SEV_MED,
                            _line_of(text, idx),
                            f"{cred.get('emoji', '')}{cred.get('tier', '?')}来源用'{claim}'"))
    return out


# ── S3: fabrication-phrase ────────────────────────────────────────────────────
def detect_fabrication_phrase(text):
    """S3: hallucination_guards 血案精确字眼 (编造版本号/社区发布, D4 指纹)。复用 seed。

    FAIL-OPEN: hallucination_guards 不可用 → []。
    """
    if not isinstance(text, str) or not text:
        return []
    try:
        import hallucination_guards
        phrases = hallucination_guards.get_blocked_phrases()
    except Exception as e:  # FAIL-OPEN
        log(f"S3 FAIL-OPEN (hallucination_guards unavailable): {e}")
        return []
    out = []
    for phrase in phrases:
        idx = text.find(phrase)
        if idx >= 0:
            out.append(_sig("S3_fabrication_phrase", _SEV_HIGH,
                            _line_of(text, idx), phrase))
    return out


# ── S4: provenance-gap ────────────────────────────────────────────────────────
def detect_provenance_gap(text):
    """S4: 牵强等价/过度关联成语 (异曲同工…) 且无 [强证据]/[弱关联] 标注 (pa_echo 指纹)。

    保守: 仅在 over-association 成语命中且全文无证据标注时报 (最弱信号, 真判断在 Layer 2)。
    复用 hallucination_guards LEVEL_6 [强证据]/[弱关联] 契约 (有标注=已自证, 不报)。
    """
    if not isinstance(text, str) or not text:
        return []
    if any(tag in text for tag in _S4_EVIDENCE_TAGS):
        return []   # 已带证据标注 = LEVEL_6 契约满足, 不报
    out = []
    for idiom in _S4_OVER_ASSOCIATION:
        idx = text.find(idiom)
        if idx >= 0:
            out.append(_sig("S4_provenance_gap", _SEV_MED,
                            _line_of(text, idx), idiom))
    return out


# ── S5: coherence-structural ──────────────────────────────────────────────────
def detect_coherence_structural(text):
    """S5: boilerplate 重复 / 全标题无正文 / 字段值=分隔符 (D1/D3 结构指纹)。

    确定性子集 (语义主题断裂留 Layer 2)。三形态: (a) 同一非平凡行重复 >=3 (dream_quota
    '技术内容，详见原文' ×5) (b) 全 markdown 标题正文极薄 (kb_review 容器标题当内容)
    (c) 字段值是分隔符串 (ontology positional parse title=分隔符)。
    """
    if not isinstance(text, str) or not text:
        return []
    out = []
    lines = text.split("\n")

    # (a) boilerplate 重复 (V37.9.199: 仅【描述文本】重复算空壳, 排除评分字段 emoji 重复)
    from collections import Counter
    norm = [ln.strip() for ln in lines
            if len(ln.strip()) >= _S5_BOILERPLATE_MIN_LEN
            and not ln.strip().startswith("#")
            and _descriptive_char_count(ln.strip()) >= _S5_BOILERPLATE_MIN_DESCRIPTIVE]
    counts = Counter(norm)
    for line_text, n in counts.items():
        if n >= _S5_BOILERPLATE_MIN_REPEAT:
            idx = text.find(line_text)
            out.append(_sig("S5_coherence_structural", _SEV_MED,
                            _line_of(text, idx) if idx >= 0 else 0,
                            f"boilerplate ×{n}: {line_text}"))

    # (b) 全标题无正文
    headings = [ln for ln in lines if ln.strip().startswith("#")]
    body_chars = sum(len(ln.strip()) for ln in lines
                     if ln.strip() and not ln.strip().startswith("#"))
    if (len(headings) >= _S5_HEADING_MIN_COUNT
            and body_chars < _S5_HEADING_BODY_MAX_CHARS * len(headings)):
        first_h = next((h for h in headings), "")
        idx = text.find(first_h)
        out.append(_sig("S5_coherence_structural", _SEV_MED,
                        _line_of(text, idx) if idx >= 0 else 0,
                        f"全标题无正文 ({len(headings)} 标题 / {body_chars} 正文字符)"))

    # (c) 字段值 = 分隔符串 (key: value, value ∈ 分隔符集)
    for i, ln in enumerate(lines):
        m = re.match(r"\s*[*-]?\s*\**[\w一-鿿]+\**\s*[:：]\s*(.+?)\s*$", ln)
        if m and m.group(1).strip() in _S5_SEPARATOR_VALUES:
            out.append(_sig("S5_coherence_structural", _SEV_MED, i + 1,
                            f"字段值=分隔符: {ln.strip()}"))
    return out


# ── orchestrator ──────────────────────────────────────────────────────────────
def run_prefilter(text, source_id=None):
    """跑全部 S1-S5, 聚合 (Layer 1 only)。

    返回 dict: {verdict: 'clean'|'flagged', fired: [signal names 去重排序],
                signals: [信号 dict], n: 信号数}。
    Stage 5 把 signals 映射进 daily_observer anomalies[] (同 {severity} 语义)。
    """
    signals = []
    signals += detect_pollution_signal(text)
    signals += detect_credibility_mismatch(text, source_id)
    signals += detect_fabrication_phrase(text)
    signals += detect_provenance_gap(text)
    signals += detect_coherence_structural(text)
    fired = sorted({s["signal"] for s in signals})
    return {
        "verdict": "flagged" if signals else "clean",
        "fired": fired,
        "signals": signals,
        "n": len(signals),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Layer 2: LLM-judge (Stage 3) — 语义判定, 复用 call_llm_critique infra
# ══════════════════════════════════════════════════════════════════════════════

# ── 设计锁定常量 (sabotage 守卫这些值) ────────────────────────────────────────
_MIN_EVIDENCE_LEN = 4          # grounded 证据最短长度 (太短=无法 ground, 防 FP)
_HALLUCINATION_GUARD_LEVEL = "LEVEL_6_DREAM_CROSS_DOMAIN_AWARE"   # 复用 seed 注入

# judge 维度 → daily_observer anomaly category (对齐 design §3.4 sub-categories)
_JUDGE_TO_CATEGORY = {
    "grounding": "unsupported_claim",
    "intent_alignment": "fail_plausible",
    "pollution_evidence": "pollution_signal",
    "fabricated_success": "fabricated_success",
}
# judge 维度 → 基础 severity (D1/D2/D3 类是最严重的 fail-plausible)
_JUDGE_SEVERITY = {
    "pollution_evidence": _SEV_HIGH,
    "fabricated_success": _SEV_HIGH,
    "intent_alignment": _SEV_HIGH,
    "grounding": _SEV_MED,
}
# Layer 1 信号 → category (单 Layer 1 fired 时 anomaly category)
_SIGNAL_TO_CATEGORY = {
    "S1_pollution_signal": "pollution_signal",
    "S2_credibility_mismatch": "credibility_mismatch",
    "S3_fabrication_phrase": "fabricated_success",
    "S4_provenance_gap": "unsupported_claim",
    "S5_coherence_structural": "fabricated_success",
}
_SEV_ORDER = {_SEV_HIGH: 3, _SEV_MED: 2, "LOW": 1}

# FAIL_PLAUSIBLE_SYSTEM core — 区别于 daily_observer CRITIQUE_SYSTEM (那是 5 维质量评分,
# 给人读的 prose; 这是针对 fail-plausible 的机器可读判定)。LEVEL_6 + credibility 块在
# build_fail_plausible_system() 运行时 append (复用 seed, 非硬编码, 测试守 MR-8)。
_FAIL_PLAUSIBLE_SYSTEM_CORE = """你是一个 fail-plausible 检测器。fail-plausible = 一段输出读起来连贯、上下文恰当、但实际是把一个内部错误/失败转化成了看似可信的内容 (gray failure 的 LLM 时代升级——不是收不到信号, 而是给人喂一个伪造的信号)。

你只判断下方【面向用户的输出】是否 fail-plausible。从四个维度判断:
1. grounding — 内容里的事实断言能否在它自己提供的源材料/上下文找到支撑? 还是凭空声称 (如声称"已保存到知识库"但无证据)?
2. intent_alignment — 内容是否在做用户/调度没要求的事 (如一段架构问答里突然冒出"我收到系统告警的后续任务"+让用户去系统设置开权限)?
3. pollution_evidence — 内容里有没有【系统 artifact】(错误码/HTTP 状态/'Bad JSON'/工具名/告警文本) 被当成了【外部世界的信号】来分析 (如把平台返回的 400 错误当成"平台危机前兆")?
4. fabricated_success — 这是不是 fallback 制造的【plausible 形状的空壳】(全是标题没有正文 / 同一句 boilerplate 重复多次 / 把容器标题当成内容)?

🔴 输出格式 (严格 JSON, 用 ```json 围栏, 不要任何额外文字):
```json
{
  "verdict": "fail_plausible" 或 "clean",
  "confidence": 0 到 100 的整数,
  "findings": [
    {"judge": "grounding|intent_alignment|pollution_evidence|fabricated_success",
     "evidence": "从上方内容中【逐字摘录】的片段 (必须能在内容里原样找到, 不得改写/编造)",
     "rationale": "一句话说明为什么这是 fail-plausible"}
  ]
}
```

🔴 铁律 (你自己也是 LLM, 可能幻觉——必须自我约束):
- evidence 必须是内容里的【逐字原文】。若你找不到能逐字摘录的证据, 就不要报这条 (宁可漏报不可编造)。
- 不确定 → verdict=clean, findings=[] (误报会制造告警噪声, false-positive 是一等问题)。
- verdict=clean 时 findings 必须为空。
- 只评估内容【本身】的 fail-plausible, 不评估它的写作风格/信息密度/格式美观 (那是另一个工具的职责)。

⚠️ 采样说明: 若内容标了"采样(head+tail)", 那是为省 token 的中间省略, 不是文件截断/内容缺失——不要把"中间省略"判为 fabricated_success 或内容不完整 (V37.9.93 教训)。"""


def build_fail_plausible_system(source_id=None):
    """构造 Layer 2 system prompt: core + 注入 LEVEL_6 守卫 + credibility 块 (复用 seeds)。

    FAIL-OPEN: seed 不可用 → 跳过该注入段 (不阻塞 judge), 不抛异。source_id 当前未用于
    分流 (credibility 块是全量的), 保留参数供未来按来源裁剪。
    """
    parts = [_FAIL_PLAUSIBLE_SYSTEM_CORE]
    try:
        import hallucination_guards
        parts.append(hallucination_guards.get_guard(_HALLUCINATION_GUARD_LEVEL))
    except Exception as e:  # FAIL-OPEN
        log(f"build_system: LEVEL_6 guard unavailable (FAIL-OPEN): {e}")
    try:
        import source_credibility
        parts.append(source_credibility.format_credibility_block())
    except Exception as e:  # FAIL-OPEN
        log(f"build_system: credibility block unavailable (FAIL-OPEN): {e}")
    return "\n\n".join(parts)


def build_fail_plausible_user(text, layer1_signals=None, sampled=False):
    """构造 Layer 2 user prompt: 采样提示(条件) + Layer 1 信号(供参考) + 待评估内容。"""
    parts = []
    if sampled:
        parts.append("⚠️ 采样提示: 下方内容为 head+tail 采样 (非完整文件)。不要把'中间省略'"
                     "判为截断或内容缺失 (V37.9.93 教训)。")
    if layer1_signals:
        sig_lines = "\n".join(
            f"- {s['signal']} @L{s['locus']}: {s['snippet']}" for s in layer1_signals)
        parts.append("确定性 Layer 1 已标记的信号 (供参考, 你需独立用语义确认或否决):\n"
                     + sig_lines)
    parts.append("═══ 待评估的面向用户输出 ═══\n" + (text or ""))
    parts.append("请严格按系统提示的 JSON 格式输出你的判定。")
    return "\n\n".join(parts)


def _extract_json_obj(content):
    """从 LLM 输出鲁棒提取首个 JSON 对象 (```json 围栏 / 裸 {...} 平衡括号)。

    返回 dict 或 None。镜像 tool_proxy 对幻觉 JSON 的鲁棒处理。
    """
    import json
    if not isinstance(content, str) or not content:
        return None
    # 优先 ```json 围栏内
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    candidates = [m.group(1)] if m else []
    # fallback: 首个平衡 {...}
    start = content.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(content)):
            c = content[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(content[start:i + 1])
                    break
        break  # 只取首个平衡块作 fallback
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            continue
    return None


def _norm_confidence(conf):
    """confidence → [0,1] float 或 None。int 0-100 → /100; float 0-1 → 原值; clamp。"""
    if isinstance(conf, bool):   # bool 是 int 子类, 显式排除
        return None
    if isinstance(conf, (int, float)):
        v = float(conf)
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))
    return None


def parse_fp_verdict(content):
    """解析 LLM-judge 输出 → (verdict, confidence|None, findings)。

    鲁棒: JSON 围栏/裸 JSON → 解析; 无法解析 → ('clean', None, []) (保守, 不编造 flag)。
    verdict 归一化为 'fail_plausible' | 'clean'。findings 只保留 dict 项。
    """
    obj = _extract_json_obj(content)
    if obj is None:
        return "clean", None, []
    raw_verdict = str(obj.get("verdict", "clean")).strip().lower()
    is_fp = ("fail" in raw_verdict and "plaus" in raw_verdict) or raw_verdict in (
        "fail_plausible", "failplausible", "flagged", "fail-plausible")
    verdict = "fail_plausible" if is_fp else "clean"
    confidence = _norm_confidence(obj.get("confidence"))
    findings = obj.get("findings")
    findings = [f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else []
    return verdict, confidence, findings


def _norm_ws(s):
    """折叠空白 (供证据 grounding 比对, 容忍 LLM 摘录时的空白差异)。"""
    return re.sub(r"\s+", " ", s).strip()


def _evidence_grounded(snippet, text):
    """🔴 反幻觉核心: LLM 引用的证据必须能在原文逐字 ground (空白归一化后子串)。

    太短 (< _MIN_EVIDENCE_LEN) = 无法 ground (太模糊) → False。这是防 Observer 自身幻觉
    的铁律 (V37.9.93): 不接受无原文支撑的"我觉得这段可疑"。
    """
    if not snippet or not isinstance(snippet, str) or not isinstance(text, str):
        return False
    norm_s = _norm_ws(snippet)
    if len(norm_s) < _MIN_EVIDENCE_LEN:
        return False
    return norm_s in _norm_ws(text)


def run_llm_judge(text, source_id, layer1_signals, llm_caller, sampled=False):
    """Layer 2: 调 LLM-judge, 解析 verdict, ground 证据 (反幻觉)。

    返回 (grounded_findings, confidence|None)。FAIL-OPEN: caller 抛异/not ok → ([], None)。
    每条 finding 加 _grounded=True 标记 (已通过证据 ground)。verdict=fail_plausible 但
    无 grounded 证据 → 返回 [] (Observer 幻觉了一个 flag, 拒绝, §5.3 / V37.9.93 铁律)。
    """
    caller = llm_caller if llm_caller is not None else _default_llm_caller
    system = build_fail_plausible_system(source_id)
    user = build_fail_plausible_user(text, layer1_signals, sampled)
    try:
        ok, content, reason = caller(system, user)
    except Exception as e:  # FAIL-OPEN
        log(f"Layer 2 FAIL-OPEN (caller raised): {e}")
        return [], None
    if not ok or not content:
        log(f"Layer 2 FAIL-OPEN (caller not ok): {reason}")
        return [], None
    verdict, confidence, raw_findings = parse_fp_verdict(content)
    if verdict != "fail_plausible":
        return [], confidence
    grounded = []
    for f in raw_findings:
        ev = f.get("evidence", "")
        if _evidence_grounded(ev, text):
            grounded.append({
                "judge": str(f.get("judge", "fail_plausible")),
                "evidence": _norm_ws(ev),
                "rationale": str(f.get("rationale", "")).strip(),
                "_grounded": True,
            })
    if not grounded:
        log("Layer 2: verdict=fail_plausible 但无 grounded 证据 → 拒绝 (反 Observer 幻觉)")
        return [], confidence
    return grounded, confidence


def _default_llm_caller(system_prompt, user_prompt):
    """默认 caller: lazy-bind daily_observer.call_llm_critique (单一 HTTP 真理源, design §3.3)。

    lazy import 避免 module-top 耦合 + Stage 5 import 环 (daily_observer import 本模块时,
    它会显式传自己的 caller, 此默认不触发)。FAIL-OPEN: 不可用 → (False, '', reason)。
    """
    try:
        import daily_observer
        return daily_observer.call_llm_critique(system_prompt, user_prompt)
    except Exception as e:  # FAIL-OPEN
        log(f"Layer 2 default caller unavailable (daily_observer.call_llm_critique): {e}")
        return False, "", f"caller unavailable: {e}"


def _max_severity(sevs):
    """取最高 severity (HIGH > MED > LOW), 空 → MED。"""
    if not sevs:
        return _SEV_MED
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0))


def _dominant_category(graded):
    """graded = [(severity, category)], 取最高 severity 的 category (插入序 tiebreak)。"""
    if not graded:
        return "fail_plausible"
    return max(graded, key=lambda g: _SEV_ORDER.get(g[0], 0))[1]


# ── orchestrator: 两层管道 ────────────────────────────────────────────────────
def detect_fail_plausible(text, source_id=None, llm_caller=None, artifact=None,
                          sampled=False, force_judge=False, layer1_result=None,
                          enable_layer2=True):
    """两层管道 orchestrator (design §3.1/§3.4)。

    Layer 1 (确定性) → 若命中 OR force_judge → Layer 2 (LLM-judge, 证据 ground)。
    返回 anomaly 列表 (0 或 1 条 consolidated verdict, 对齐 daily_observer anomalies
    结构 {severity, category, message}): 干净 → []; 命中 → [consolidated]。

    consolidated verdict 字段:
      severity   — Layer 1/2 全部证据中最高
      category   — 最高 severity 证据的 category (design §3.4 sub-category)
      artifact   — 哪个面向用户的输出 (调用方传)
      evidence   — [{layer:1, signal/locus/snippet}, {layer:2, judge/snippet/rationale}]
      confidence — Layer 2 置信度 (0-1); 仅 Layer 1 命中时 1.0 (确定性)
      message    — 人读摘要 (向后兼容现有 report)
      fired      — 命中的信号/judge 名集合 (去重排序, 便于断言/grep)

    便宜路径: Layer 1 clean 且 not force_judge → 不调 LLM, 返回 []。
    enable_layer2=False (Stage 5 dry_run): 仅 Layer 1, 零 LLM (确定性观察)。
    """
    l1 = layer1_result if layer1_result is not None else run_prefilter(text, source_id)
    l1_signals = l1.get("signals", [])
    trigger_l2 = (bool(l1_signals) or force_judge) and enable_layer2

    l2_findings, l2_confidence = ([], None)
    if trigger_l2:
        l2_findings, l2_confidence = run_llm_judge(
            text, source_id, l1_signals, llm_caller, sampled=sampled)

    if not l1_signals and not l2_findings:
        return []   # clean (便宜路径或 Layer 2 否决)

    evidence = []
    graded = []      # [(severity, category)] 供 dominant 选择
    fired = set()
    for s in l1_signals:
        evidence.append({"layer": 1, "signal": s["signal"], "locus": s["locus"],
                         "snippet": s["snippet"]})
        graded.append((s["severity"], _SIGNAL_TO_CATEGORY.get(s["signal"], "fail_plausible")))
        fired.add(s["signal"])
    for f in l2_findings:
        evidence.append({"layer": 2, "judge": f["judge"], "snippet": f["evidence"],
                         "rationale": f["rationale"]})
        graded.append((_JUDGE_SEVERITY.get(f["judge"], _SEV_MED),
                       _JUDGE_TO_CATEGORY.get(f["judge"], "fail_plausible")))
        fired.add(f["judge"])

    severity = _max_severity([g[0] for g in graded])
    category = _dominant_category(graded)
    confidence = l2_confidence if l2_confidence is not None else 1.0
    n1, n2 = len(l1_signals), len(l2_findings)
    art = artifact or source_id or "?"
    message = (f"fail-plausible [{category}] @ {art}: "
               f"{n1} 确定性信号 + {n2} 语义证据 (confidence {confidence:.2f})")

    return [{
        "severity": severity,
        "category": category,
        "artifact": artifact,
        "evidence": evidence,
        "confidence": confidence,
        "message": message,
        "fired": sorted(fired),
    }]


# ── Stage 5 collection-level wrapper (daily_observer.run() 面向的接口, §6.1) ─────
_PUSH_OUTPUT_NAMES = ("evening", "dream", "deep_dive")


def scan_fail_plausible(push_outputs, source_sections, llm_caller=None,
                        force_judge=False, enable_layer2=True):
    """对 daily_observer 的 push_outputs + source_sections 逐 artifact 跑 detect_fail_plausible。

    返回合并 verdict 列表 (每条 artifact 已设, 对齐 daily_observer anomalies)。
    cheap-path: force_judge=False 时 Layer 2 仅在 Layer 1 命中触发 → 干净日 ≈0 LLM 调用。
    sampled: push_outputs 的 length>len(content) (V37.9.93 head+tail 采样) → 传 sampled=True
             防 Layer 2 把"中间省略"误判为截断。
    FAIL-OPEN: 单 artifact 异常 → skip 不阻塞其他 (镜像 daily_observer scan 健壮性)。
    """
    verdicts = []
    for name in _PUSH_OUTPUT_NAMES:
        info = (push_outputs or {}).get(name) or {}
        if not info.get("found") or not info.get("content"):
            continue
        try:  # FAIL-OPEN: 整个 artifact 处理 (含 sampled 计算) 包在 try 内
            text = info["content"]
            sampled = info.get("length", len(text)) > len(text)
            verdicts += detect_fail_plausible(
                text, source_id=name, llm_caller=llm_caller, artifact=name,
                sampled=sampled, force_judge=force_judge, enable_layer2=enable_layer2)
        except Exception as e:  # FAIL-OPEN
            log(f"scan_fail_plausible FAIL-OPEN ({name}): {e}")
    for s in (source_sections or []):
        try:  # FAIL-OPEN
            text = s.get("section_text", "")
            if not text:
                continue
            src = s.get("source")
            sampled = s.get("char_count", len(text)) > len(text)
            verdicts += detect_fail_plausible(
                text, source_id=src, llm_caller=llm_caller,
                artifact=f"source:{src}", sampled=sampled,
                force_judge=force_judge, enable_layer2=enable_layer2)
        except Exception as e:  # FAIL-OPEN
            log(f"scan_fail_plausible FAIL-OPEN (source:{src if isinstance(s, dict) else '?'}): {e}")
    return verdicts


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description="LLM-Observer Layer 1 确定性 pre-filter (S1-S5)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="直接传文本")
    src.add_argument("--file", help="读文件内容")
    ap.add_argument("--source", default=None, help="source_id (供 S2 评估出处档位)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    text = args.text if args.text is not None else open(args.file, encoding="utf-8").read()
    result = run_prefilter(text, source_id=args.source)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"verdict: {result['verdict']}  ({result['n']} 信号)")
        for s in result["signals"]:
            print(f"  [{s['severity']}] {s['signal']} @L{s['locus']}: {s['snippet']}")
    return 0 if result["verdict"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())

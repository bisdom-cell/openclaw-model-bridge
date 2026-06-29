#!/usr/bin/env python3
"""llm_observer.py — LLM-Observer 确定性 Layer 1 pre-filter (研究攻关 #1 Stage 2).

机械化人眼 (论文 arXiv:2606.14589 §5.2 开放问题) 的第一层: 零 LLM、零网络的确定性
fail-plausible 信号 pre-filter。读一段【面向用户的输出】, 跑 S1-S5 五个确定性检测,
产出 machine-actionable 信号 (带证据 locus/snippet)。

设计 (docs/llm_observer_design.md §3.1/§3.2):
  两层管道 Layer 1 (本模块, 确定性) → Layer 2 (LLM-judge, Stage 3)。Layer 1 在前 =
  成本 (大多数干净输出走便宜路径不调 LLM) + 可解释 (引用具体证据) + 防 Observer 自身
  幻觉 (确定性信号是 LLM 判断的锚, V37.9.93 sampling 幻觉教训)。

五信号 (docs/llm_observer_ground_truth.yaml signals 字典):
  S1 pollution_signal      错误码/HTTP/工具名/'Bad JSON'/告警 artifact 出现在用户内容 (D1/D2 指纹)
  S2 credibility_mismatch  低档来源被高档措辞包装 (复用 source_credibility)
  S3 fabrication_phrase    hallucination_guards.get_blocked_phrases() 血案精确字眼 (D4 指纹)
  S4 provenance_gap        牵强等价/跨域因果断言无 [强证据]/[弱关联] 标注 (复用 LEVEL_6 契约)
  S5 coherence_structural  boilerplate 重复 / 全标题无正文 / 字段=分隔符 (D1/D3 指纹)

FAIL-OPEN 契约 (镜像 V37.4 / cross_source_signal_aggregator V37.9.46): 缺 seed
依赖 (source_credibility / hallucination_guards) → 该信号返回 [] 不阻塞其他信号, 绝不抛异。

FP 纪律 (论文 §5.4 false-positive 是一等度量, 原则 #32 噪声本身是问题): S1/S3 用强
指纹 (合成 push 里几乎不可能合法出现); S2/S4/S5 保守 (只在高置信形态命中), 宁可漏报
不误报 (漏的由 Layer 2 + Category B 度量盖)。

Stage 边界: 本模块仅 Layer 1。Stage 3 加 Layer 2 LLM-judge + 合并为 detect_fail_plausible
orchestrator; Stage 5 wire 进 daily_observer.run() (detect_anomalies 后)。在 wire 进
runtime 前本模块零 runtime 影响 (PoC 库 + CLI + 守卫)。

CLI: python3 llm_observer.py --file out.md [--source dream] [--json]
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

    # (a) boilerplate 重复
    from collections import Counter
    norm = [ln.strip() for ln in lines if len(ln.strip()) >= _S5_BOILERPLATE_MIN_LEN
            and not ln.strip().startswith("#")]
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

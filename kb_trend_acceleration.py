#!/usr/bin/env python3
"""kb_trend_acceleration.py — V37.9.48 Opportunity Radar Stage 3

#3 趋势加速检测 — 4 周历史关键词加速度分析

设计文档: docs/opportunity_radar_design.md 第 5 节
背景: V37.9.46 Stage 1 (#1 跨 source 共振) + V37.9.47 Stage 2 (#2 项目对齐度)
      已就位, Stage 3 补全第三维度 = 时间维度的趋势加速度
      (kb_trend.py 只算"本周 vs 上周"频率变化, 加速度 = 二阶导数, 反映动态)

核心算法 (设计文档 5.1 节锁定):
  extract_keywords_per_week(week_offset) → dict[keyword → freq] (复用 kb_trend.py)
  compute_acceleration(week_keywords) → dict[keyword → metrics]
    accel_1w = w1/w2  (本周 vs 上周倍率)
    accel_2w = w1/w3  (本周 vs 上上周, 验证连续性)
  classify(a1, a2) → str (5 档):
    🚀 strong_acceleration: a1 ≥ 1.5 AND a2 ≥ 1.5  (连续 2 周加速 = 高信号)
    📈 mild_acceleration:   a1 ≥ 1.5                (本周加速)
    💧 deceleration:        a1 < 0.7                (本周减速)
    ⚰️ obsolescence:         a1 < 0.5 AND a2 < 0.5   (连续 2 周衰退)
    📊 stable: 其他
  rank_signals: 优先 🚀 > 📈 > 💧 > ⚰️, top 10 strong + top 5 stable + top 3 obs
  emit_radar_json → ~/.kb/radar/weekly_trends_{week}.json

设计文档 11.2 风险表登记: 趋势加速度噪声大 (小基数) → 设最小基数门槛
  (周占比 < MIN_FREQ_PCT 不算趋势, V37.9.48 默认 0.005 = 0.5%)

FAIL-OPEN 契约 (V37.9.46/47 同款 lazy import 模式):
  - kb_trend.py 缺失 → log WARN + 退化为空结果
  - KB notes/sources 缺失 → 返回空 keyword dict (不阻塞)
  - 任何环节失败 → emit_radar_json([]) 不阻塞下游 (kb_dream / radar 消费方)

Stage 3 范围 (V37.9.46 Stage 1 / V37.9.47 Stage 2 同款不 cascade):
  - 28 单测覆盖 7 测试类
  - 不集成 kb_dream/kb_evening (Stage 4 才集成)
  - 不替换 kb_trend.py (向后兼容, kb_trend.py 旧接口仍工作)
"""

import os
import sys
import json
import glob
from datetime import datetime, timedelta
from collections import Counter

# V37.9.48 marker (governance source-level guard 字面量)
_V37_9_48_MARKER = "V37.9.48 Opportunity Radar Stage 3"

# ── 算法常量 (设计文档 5.1 锁定值) ───────────────────────────────────────
ACCEL_STRONG_THRESHOLD = 1.5    # ≥1.5 倍率 = 加速 (兼 a1, a2)
ACCEL_DECEL_THRESHOLD = 0.7     # <0.7 倍率 = 减速
ACCEL_OBS_THRESHOLD = 0.5       # <0.5 + 连续 2 周 = obsolescence
TOP_K_STRONG = 10               # top 10 strong signals
TOP_K_STABLE = 5                # top 5 stable
TOP_K_OBS = 3                   # top 3 obsolescence

# 最小基数门槛 (设计文档 11.2 风险表 — 周占比 < 此值不算趋势)
MIN_FREQ_PCT = 0.005            # 0.5% = 200 中至少 1 个

# 5 档分类常量 (排序优先级)
ARCHETYPE_STRONG = "🚀 strong_acceleration"
ARCHETYPE_MILD = "📈 mild_acceleration"
ARCHETYPE_STABLE = "📊 stable"
ARCHETYPE_DECEL = "💧 deceleration"
ARCHETYPE_OBS = "⚰️ obsolescence"

# 排序优先级 (rank_signals 用)
_ARCHETYPE_PRIORITY = {
    ARCHETYPE_STRONG: 0,   # 最优先
    ARCHETYPE_MILD: 1,
    ARCHETYPE_DECEL: 2,
    ARCHETYPE_OBS: 3,
    ARCHETYPE_STABLE: 4,   # 最末
}

# ── 默认路径 ──────────────────────────────────────────────────────────
KB_DIR_DEFAULT = os.path.expanduser(os.environ.get("KB_BASE", "~/.kb"))
RADAR_DIR_DEFAULT = os.path.join(KB_DIR_DEFAULT, "radar")


def log(msg):
    """V37.9.46 同款 stderr 输出 (MR-11)"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] kb_trend_acceleration: {msg}", file=sys.stderr)


# ── 1. extract_keywords_per_week ─────────────────────────────────────
def extract_keywords_per_week(week_offset, kb_dir=None, today=None):
    """提取指定周 (week_offset 周前) 的关键词频率.

    Args:
        week_offset: 1=上周 (今天-7~今天-1), 2=上上周 (今天-14~今天-8), etc.
        kb_dir: KB 根目录 (默认 ~/.kb)
        today: datetime 用于测试注入 (默认 datetime.now())

    Returns:
        dict {keyword: freq_count} (来自 kb_trend.extract_keywords)

    FAIL-OPEN: kb_trend.py 缺失 → log WARN + 返回空 dict
    """
    if today is None:
        today = datetime.now()
    if kb_dir is None:
        kb_dir = KB_DIR_DEFAULT
    if not isinstance(week_offset, int) or week_offset < 1:
        log(f"WARN: invalid week_offset {week_offset!r}")
        return {}

    # week_offset=1 → 今天 - 7 到 今天 - 1 (7 整天)
    end_date = today - timedelta(days=(week_offset - 1) * 7 + 1)
    start_date = today - timedelta(days=week_offset * 7)

    # Lazy import kb_trend (FAIL-OPEN)
    try:
        sys.path.insert(0, os.path.expanduser("~"))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import kb_trend as _kt
    except ImportError as e:
        log(f"WARN: kb_trend unavailable, return empty: {e}")
        return {}

    try:
        text = _kt.extract_period_text(kb_dir, start_date, end_date)
    except Exception as e:
        log(f"WARN: extract_period_text failed for week {week_offset}: {e}")
        return {}

    if not text:
        return {}

    try:
        kw_counter = _kt.extract_keywords(text, top_n=500)
    except Exception as e:
        log(f"WARN: extract_keywords failed: {e}")
        return {}

    # extract_keywords returns Counter — convert to dict {kw: count}
    return dict(kw_counter)


# ── 2. compute_acceleration ──────────────────────────────────────────
def compute_acceleration(week_keywords, min_freq_pct=MIN_FREQ_PCT):
    """计算 4 周历史关键词加速度.

    Args:
        week_keywords: dict {1: {kw: freq}, 2: {kw: freq}, 3: {kw: freq}}
                       (key = week_offset, 1=上周, 2=上上周, 3=三周前)
        min_freq_pct: 最小基数门槛 (周占比), default 0.005

    Returns:
        dict {keyword: {
            'freq_w1': int, 'freq_w2': int, 'freq_w3': int,
            'pct_w1': float, 'pct_w2': float, 'pct_w3': float,
            'accel_1w': float | None,  # w1 / w2 (None if w2=0)
            'accel_2w': float | None,  # w1 / w3 (None if w3=0)
            'classification': str (一档),
        }}

    契约:
      - w2 == 0 (新词) → skip (无 accel_1w 计算基础)
      - w3 == 0 → accel_2w=None (但 accel_1w 仍计)
      - freq < min_freq_pct → skip (小基数噪声门槛)
    """
    if not isinstance(week_keywords, dict):
        return {}

    w1 = week_keywords.get(1, {}) or {}
    w2 = week_keywords.get(2, {}) or {}
    w3 = week_keywords.get(3, {}) or {}

    total_w1 = sum(w1.values()) if w1 else 0
    total_w2 = sum(w2.values()) if w2 else 0
    total_w3 = sum(w3.values()) if w3 else 0

    if total_w1 == 0 and total_w2 == 0:
        # 都为 0, 无加速度可算
        return {}

    # 全部出现过的 keyword union (本周 + 上周 + 上上周)
    all_keywords = set(w1.keys()) | set(w2.keys()) | set(w3.keys())

    metrics = {}
    for kw in all_keywords:
        freq_w1 = w1.get(kw, 0)
        freq_w2 = w2.get(kw, 0)
        freq_w3 = w3.get(kw, 0)

        # 占比 (用各周自己的总数, 防总数差距大时倍率失真)
        pct_w1 = freq_w1 / total_w1 if total_w1 > 0 else 0.0
        pct_w2 = freq_w2 / total_w2 if total_w2 > 0 else 0.0
        pct_w3 = freq_w3 / total_w3 if total_w3 > 0 else 0.0

        # 最小基数门槛: w1/w2 都低于 min_freq_pct → skip (噪声)
        if pct_w1 < min_freq_pct and pct_w2 < min_freq_pct:
            continue

        # 加速度: w2 == 0 → skip (新词无 baseline)
        if pct_w2 == 0:
            continue

        accel_1w = pct_w1 / pct_w2
        accel_2w = (pct_w1 / pct_w3) if pct_w3 > 0 else None

        classification = classify(accel_1w, accel_2w)

        metrics[kw] = {
            "freq_w1": freq_w1,
            "freq_w2": freq_w2,
            "freq_w3": freq_w3,
            "pct_w1": round(pct_w1, 5),
            "pct_w2": round(pct_w2, 5),
            "pct_w3": round(pct_w3, 5),
            "accel_1w": round(accel_1w, 3),
            "accel_2w": round(accel_2w, 3) if accel_2w is not None else None,
            "classification": classification,
        }

    return metrics


# ── 3. classify (5 档) ───────────────────────────────────────────────
def classify(accel_1w, accel_2w=None):
    """5 档分类 (设计文档 5.1 锁定边界).

    Args:
        accel_1w: 本周 vs 上周倍率 (float, must be > 0)
        accel_2w: 本周 vs 上上周倍率 (float | None)

    Returns:
        str: 5 档之一 (ARCHETYPE_STRONG / MILD / STABLE / DECEL / OBS)

    优先级 (顺序锁定):
      1. 🚀 strong  if a1 ≥ 1.5 AND a2 ≥ 1.5    (连续 2 周加速)
      2. ⚰️ obs     if a1 < 0.5 AND a2 < 0.5    (连续 2 周衰退)
      3. 📈 mild    if a1 ≥ 1.5                 (本周加速)
      4. 💧 decel   if a1 < 0.7                 (本周减速)
      5. 📊 stable  其他
    """
    if not isinstance(accel_1w, (int, float)):
        return ARCHETYPE_STABLE

    a1 = float(accel_1w)
    a2 = float(accel_2w) if isinstance(accel_2w, (int, float)) else None

    # 1. Strong: a1 ≥ 1.5 AND a2 ≥ 1.5 (必须 a2 不为 None)
    if a1 >= ACCEL_STRONG_THRESHOLD and a2 is not None and a2 >= ACCEL_STRONG_THRESHOLD:
        return ARCHETYPE_STRONG

    # 2. Obsolescence: a1 < 0.5 AND a2 < 0.5 (必须 a2 不为 None)
    if a1 < ACCEL_OBS_THRESHOLD and a2 is not None and a2 < ACCEL_OBS_THRESHOLD:
        return ARCHETYPE_OBS

    # 3. Mild: a1 ≥ 1.5 (a2 不要求, 或 a2 < 1.5 但 a1 仍加速)
    if a1 >= ACCEL_STRONG_THRESHOLD:
        return ARCHETYPE_MILD

    # 4. Decel: a1 < 0.7
    if a1 < ACCEL_DECEL_THRESHOLD:
        return ARCHETYPE_DECEL

    # 5. Stable: 其他 (0.7 ≤ a1 < 1.5)
    return ARCHETYPE_STABLE


# ── 4. rank_signals ──────────────────────────────────────────────────
def rank_signals(metrics, top_strong=TOP_K_STRONG, top_stable=TOP_K_STABLE,
                 top_obs=TOP_K_OBS):
    """按 archetype 优先级 + |a1| 排序, 截取 top_k.

    Args:
        metrics: dict {kw: {classification, accel_1w, ...}} from compute_acceleration
        top_strong: top N strong/mild signals
        top_stable: top N stable
        top_obs: top N obsolescence

    Returns:
        list[dict]: [{keyword, ...metrics, rank, archetype_priority}]
                    Sorted: archetype priority asc, then |accel_1w - 1| desc
                    截取: top_strong (strong+mild) + top_stable + top_obs
    """
    if not metrics or not isinstance(metrics, dict):
        return []

    # 按 archetype 分桶
    buckets = {
        "high_priority": [],   # strong + mild
        "stable": [],
        "decel_obs": [],       # decel + obs
    }

    for kw, m in metrics.items():
        cls = m.get("classification", ARCHETYPE_STABLE)
        entry = dict(m)
        entry["keyword"] = kw
        entry["archetype_priority"] = _ARCHETYPE_PRIORITY.get(cls, 99)

        if cls in (ARCHETYPE_STRONG, ARCHETYPE_MILD):
            buckets["high_priority"].append(entry)
        elif cls == ARCHETYPE_STABLE:
            buckets["stable"].append(entry)
        elif cls in (ARCHETYPE_DECEL, ARCHETYPE_OBS):
            buckets["decel_obs"].append(entry)

    # 按 archetype priority + |a1 - 1| desc 排序
    def _sort_key(e):
        a1 = e.get("accel_1w", 1.0)
        # 偏离 1 (= 不变) 越远越显著
        return (e["archetype_priority"], -abs(a1 - 1.0))

    for bucket_name in buckets:
        buckets[bucket_name].sort(key=_sort_key)

    # 截取并合并
    result = []
    result.extend(buckets["high_priority"][:top_strong])
    result.extend(buckets["stable"][:top_stable])
    result.extend(buckets["decel_obs"][:top_obs])

    # Add rank field (post-truncation)
    for i, e in enumerate(result):
        e["rank"] = i + 1

    return result


# ── 5. emit_radar_json ───────────────────────────────────────────────
def emit_radar_json(signals, week, output_dir=None):
    """写 weekly_trends_{week}.json 到 radar 输出目录.

    Args:
        signals: list[dict] from rank_signals
        week: str (e.g. "2026-W19" or "20260510")
        output_dir: 输出目录 (default ~/.kb/radar/)

    Returns:
        str: 写入的文件路径
    """
    if output_dir is None:
        output_dir = RADAR_DIR_DEFAULT
    os.makedirs(output_dir, exist_ok=True)

    out_path = os.path.join(output_dir, f"weekly_trends_{week}.json")

    # 序列化 (确保 set 等不可序列化对象转 list)
    serializable_signals = []
    for s in signals:
        entry = {k: v for k, v in s.items()
                 if isinstance(v, (str, int, float, type(None), list, dict))
                 or v is None}
        serializable_signals.append(entry)

    payload = {
        "week": week,
        "version": _V37_9_48_MARKER,
        "generated_at": datetime.now().isoformat(),
        "signal_count": len(serializable_signals),
        "archetype_summary": _summarize_archetypes(serializable_signals),
        "signals": serializable_signals,
    }

    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)

    return out_path


def _summarize_archetypes(signals):
    """统计每个 archetype 出现次数 (用于 JSON header)."""
    counter = Counter()
    for s in signals:
        cls = s.get("classification", "")
        if cls:
            counter[cls] += 1
    return dict(counter)


# ── CLI orchestrator ─────────────────────────────────────────────────
def run(today=None, kb_dir=None, output_dir=None):
    """主 orchestrator: scan 4 weeks → compute → classify → rank → emit.

    Returns: dict {'status', 'signal_count', 'output_path', 'archetype_summary',
                   'reason'}
    """
    if today is None:
        today = datetime.now()
    if kb_dir is None:
        kb_dir = KB_DIR_DEFAULT
    if output_dir is None:
        output_dir = os.path.join(kb_dir, "radar")

    week_label = today.strftime("%Y-W%U")  # ISO week
    log(f"start: today={today.date()}, week={week_label}")

    # 提取最近 3 周关键词 (w1=上周, w2=上上周, w3=三周前)
    week_keywords = {}
    for offset in (1, 2, 3):
        kw = extract_keywords_per_week(offset, kb_dir=kb_dir, today=today)
        week_keywords[offset] = kw
        log(f"week {offset} keywords: {len(kw)}")

    if not any(week_keywords.values()):
        path = emit_radar_json([], week_label, output_dir=output_dir)
        return {"status": "no_data", "signal_count": 0,
                "output_path": path, "archetype_summary": {},
                "reason": "no kb data in past 3 weeks"}

    # Compute acceleration
    metrics = compute_acceleration(week_keywords)
    log(f"computed acceleration for {len(metrics)} keywords")

    # Rank
    signals = rank_signals(metrics)
    summary = _summarize_archetypes(signals)
    log(f"ranked signals: {len(signals)}, archetype summary: {summary}")

    # Emit
    path = emit_radar_json(signals, week_label, output_dir=output_dir)

    return {
        "status": "ok",
        "signal_count": len(signals),
        "output_path": path,
        "archetype_summary": summary,
        "reason": "",
    }


def main():
    """CLI entry."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Weekly trend acceleration analyzer (V37.9.48 Stage 3)")
    parser.add_argument("--kb-dir", default=None,
                        help="KB root dir (default ~/.kb)")
    parser.add_argument("--output-dir", default=None,
                        help="Output dir (default <kb-dir>/radar)")
    parser.add_argument("--json", action="store_true",
                        help="Print result as JSON to stdout")
    args = parser.parse_args()

    result = run(kb_dir=args.kb_dir, output_dir=args.output_dir)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Status: {result['status']}")
        print(f"Signals: {result['signal_count']}")
        print(f"Archetypes: {result['archetype_summary']}")
        print(f"Output: {result['output_path']}")
        if result.get("reason"):
            print(f"Reason: {result['reason']}")

    # Exit codes:
    #   0 = ok / no_data (FAIL-OPEN 不阻塞下游)
    #   1 = unexpected failure
    return 0 if result["status"] in ("ok", "no_data") else 1


if __name__ == "__main__":
    sys.exit(main())

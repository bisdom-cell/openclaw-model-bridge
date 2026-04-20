"""finance_news_zombie — V37.8.5 三层僵尸账号检测（纯函数，可单测）

V37.8.4 首次引入僵尸检测，但用严格相等 (old == total) 漏掉两个边缘：
  - CNS1952 (99 总/98 超时间窗/1 过短) — 99% 老化未触发 100% 阈值
  - SingTaoDaily (0 tweets 空 stub) — total=0 绕过 total>0 门槛

V37.8.5 重构为三层：
  Tier 1 "stub"          : HTML 结构完整（no_data=0）但解析出 0 推文
                            = embed-disabled 或返回空骨架
  Tier 2+3 "stale"       : ≥90% 推文超 72h 窗口
                            = 100% 和 99% 案例统一捕获，
                              整数比较 (old*10 >= total*9) 避免浮点

三层独立判定，是 `stub OR stale` 的简单并集，保证 V37.8.4 用例全部保留
（strict old==total 永远在 old*10 >= total*9 之内）。

MR-10 (understand-before-fix)：V37.8.4 修复本身引入的检测盲区，
V37.8.5 闭合——血案成为"修复也可能埋坑"的活教材。

签约：输入 diag dict，必须包含 total / old / no_data 三个键，
     类型都是 int。返回 (is_zombie_suspect: bool, tier: str)。
     tier 取值：'stub' / 'stale' / 'alive'。
"""

ZOMBIE_STALE_NUM = 9   # V37.8.5 ≥90% 老化阈值的分子
ZOMBIE_STALE_DEN = 10  # 分母（避免浮点，纯整数比较）


def classify_zombie(diag: dict, count: int = 0) -> tuple:
    """三层僵尸检测。

    Args:
        diag: 解析统计字典，必须包含键：
            - total (int)   : 原始推文数（含 RT/过短/已见/超窗口/接受）
            - old (int)     : 因超 72h 时间窗被过滤的数量
            - no_data (int) : 1=HTML 中未找到 __NEXT_DATA__，0=找到
        count: 最终被接受的推文数（用于守卫 Tier 2+3 误报）。
               count > 0 时即使老化率 ≥90% 也不判定 stale——
               账号仍在以低频产出可用内容，不应被视为死账号。
               V37.8.4 下 old==total 自动等价 count==0，无需该参数。

    Returns:
        (is_zombie_suspect, tier)
        - is_zombie_suspect: 是否僵尸嫌疑
        - tier: 'stub' | 'stale' | 'alive'
    """
    total = diag.get("total", 0)
    old = diag.get("old", 0)
    no_data = diag.get("no_data", 0)

    # Tier 1: stub — HTML 结构完整但无推文（V37.8.4 漏: SingTaoDaily）
    # no_data=1 时 total 天然=0 但这是不同的失败（HTML 格式/rate limit），
    # 只认 no_data=0 + total=0 的"明确空 stub"为僵尸嫌疑。
    if no_data == 0 and total == 0:
        return (True, "stub")

    # Tier 2+3: stale — ≥90% 超窗口 + 零接受（V37.8.4 漏: CNS1952 98/99=99% 零接受）
    # 整数比较等价于 old/total >= 0.9，覆盖原 V37.8.4 的 old==total 全部用例：
    #   total=1, old=1 → 10>=9 ✓（原 100% 情形保留）
    #   total=99, old=98 → 980>=891 ✓（新增 CNS1952 99% 情形）
    #   total=10, old=8 → 80>=90 ✗（80% 不触发，避免误报）
    # count==0 守卫防止"还在产出 1 条新鲜内容"被误判死亡。
    if count == 0 and total > 0 and old * ZOMBIE_STALE_DEN >= total * ZOMBIE_STALE_NUM:
        return (True, "stale")

    return (False, "alive")


if __name__ == "__main__":
    # 命令行 smoke test：python3 finance_news_zombie.py
    test_cases = [
        # (diag, count, expected_is_zombie, expected_tier, description)
        ({"total": 0, "old": 0, "no_data": 0}, 0, True, "stub", "Tier 1: 空 stub (SingTaoDaily)"),
        ({"total": 0, "old": 0, "no_data": 1}, 0, False, "alive", "非 stub: HTML 无 __NEXT_DATA__"),
        ({"total": 10, "old": 10, "no_data": 0}, 0, True, "stale", "Tier 2: 100% 超窗口 (V37.8.4 原用例)"),
        ({"total": 99, "old": 98, "no_data": 0}, 0, True, "stale", "Tier 3: 99% 超窗口 (CNS1952)"),
        ({"total": 10, "old": 9, "no_data": 0}, 1, False, "alive", "90% 老化但 count=1 守卫避免误报"),
        ({"total": 10, "old": 8, "no_data": 0}, 0, False, "alive", "80% 不触发"),
        ({"total": 5, "old": 2, "no_data": 0}, 0, False, "alive", "40% 活跃账号"),
        ({"total": 3, "old": 0, "no_data": 0}, 0, False, "alive", "全部新鲜"),
    ]
    all_pass = True
    for diag, count, expected_zombie, expected_tier, desc in test_cases:
        got_zombie, got_tier = classify_zombie(diag, count)
        ok = (got_zombie == expected_zombie and got_tier == expected_tier)
        marker = "✓" if ok else "✗"
        print(f"{marker} {desc}: diag={diag} count={count} → ({got_zombie}, {got_tier})")
        if not ok:
            all_pass = False
    exit(0 if all_pass else 1)

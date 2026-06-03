"""finance_news_zombie — V37.8.5 三层僵尸检测 → V37.9.103 四档「冻结≠死亡」重构

V37.8.4 首次引入僵尸检测，但用严格相等 (old == total) 漏掉两个边缘：
  - CNS1952 (99 总/98 超时间窗/1 过短) — 99% 老化未触发 100% 阈值
  - SingTaoDaily (0 tweets 空 stub) — total=0 绕过 total>0 门槛

V37.8.5 重构为三层（stub / stale / alive），用整数比较 old*10 >= total*9
闭合 ≥90% 老化盲区。

V37.9.103 血案修复（「冻结 ≠ 死亡」— 来自 V37.9.102 ai_leaders_x 复盘）：
  V37.9.102 实测发现 X Syndication API **平台级退化**——把天天发推的活账号
  （新华/CGTN/环球时报/路透商业/karpathy/LeCun...）快照**冻结在 6-10 月前**。
  V37.8.5 的 "stale"（≥90% 超 72h 窗口）无法区分三种情况：
    (a) 真死/改名 handle（年久快照，如 caixin 2227 天）
    (b) 快照冻结但活着（Syndication 平台退化，账号天天发推）
    (c) 慢但活着（每 4-5 天发一次，本次恰好全 >72h）
  三者在 72h 窗口下完全一样 → V37.8.14/16 把 (b)(c) 当 (a) 错杀了
  新华/CGTN/环球时报/路透商业等明显活跃的机构账号。

  V37.9.103 用**最新推文年龄**区分（血案精确归因）：
    - 被错杀的 XHNews/CGTN/环球时报/路透商业是 **total>0 的 frozen**（有老推文、快照冻结、
      账号天天发推）——这才是血案核心，frozen 必须**勿移除**。
    - stub（0 推文、embed-disabled）和 dead（年久）是真正可移除的：
      stub = Syndication 永远拿不到（V37.9.102 "Syndication 里真死"，clean+reduce 选项"清死账号"）；
      dead = newest_age >= DEAD_AGE_DAYS(730 天 ~2 年) 真死/改名。
    - frozen = stale 但 newest_age < DEAD_AGE_DAYS（或无法判定）= 冻结/低频，账号可能活着。

  **is_zombie_suspect（= 建议移除候选）= stub OR dead，但 frozen 永远 False**。
  frozen 仍被分类标记（可观测，避免 X 渠道静默退化），但不进「建议移除」告警——
  Syndication 把活账号快照冻在 6-10 月前 ≠ 账号死亡（V37.9.102 核心血案）。
  阈值 DEAD_AGE_DAYS 保守设 730 天：远超实测冻结范围(6-10 月)，确保有老推文的活账号
  不被 72h 窗口误判为可移除（V37.8.14 XHNews/CGTN 97/97、99/99 错杀的根因）。

MR-10 (understand-before-fix)：V37.8.4 修复埋盲区 → V37.8.5 闭合 →
  V37.9.103 发现 V37.8.5 的 "stale=可移除" 本身在 Syndication 退化下错杀活账号。
  每一层「修复」都可能在更深的环境变化下成为新 bug。
DEAD_AGE_DAYS 与 jobs/ai_leaders_x/ai_leaders_rotation.py 的同名常量保持一致
（跨模块一致性，由 test_finance_news_zombie.py 守卫）。

签约：输入 diag dict，必须包含 total / old / no_data 三个键（int）。
     V37.9.103 新增可选键 newest_age_days（int 或 None）= 最新本人推文距今天数。
     返回 (is_zombie_suspect: bool, tier: str)。
     tier 取值：'stub' / 'dead' / 'frozen' / 'alive'。
"""

ZOMBIE_STALE_NUM = 9   # V37.8.5 ≥90% 老化阈值的分子
ZOMBIE_STALE_DEN = 10  # 分母（避免浮点，纯整数比较）
# V37.9.103: 区分「冻结(活)」vs「真死」的最新推文年龄阈值（天）。
# 保守 730 天(~2 年)远超 V37.9.102 实测 Syndication 冻结范围(6-10 月)，
# 确保只有年久未更/改名 handle 才判 dead 建议移除。
DEAD_AGE_DAYS = 730


def classify_zombie(diag: dict, count: int = 0) -> tuple:
    """四档僵尸检测（V37.9.103「冻结≠死亡」）。

    Args:
        diag: 解析统计字典，必须包含键：
            - total (int)   : 原始推文数（含 RT/过短/已见/超窗口/接受）
            - old (int)     : 因超 72h 时间窗被过滤的数量
            - no_data (int) : 1=HTML 中未找到 __NEXT_DATA__，0=找到
            可选键（V37.9.103）：
            - newest_age_days (int|None) : 最新本人推文距今天数。
              None = 无可解析日期 → 保守判 frozen（防错杀）。
        count: 最终被接受的推文数（守卫 stale 误报）。
               count > 0 即使老化率 ≥90% 也判 alive（账号在低频产出）。

    Returns:
        (is_zombie_suspect, tier)
        - is_zombie_suspect: 是否「建议移除」候选。V37.9.103: stub OR dead 为 True，
          frozen 永远 False（Syndication 拿不到有老推文的活账号 ≠ 死亡，V37.9.102 血案）。
        - tier: 'stub' | 'dead' | 'frozen' | 'alive'
    """
    total = diag.get("total", 0)
    old = diag.get("old", 0)
    no_data = diag.get("no_data", 0)
    newest_age_days = diag.get("newest_age_days")

    # Tier "stub" — HTML 结构完整但 0 推文（embed-disabled，V37.8.4 漏: SingTaoDaily）。
    # V37.9.103: stub = Syndication 永远拿不到推文（embed-disabled 是账号主的持久设置），
    # 对 Syndication job 永久无用 → 可移除（需 3 天连续确认防瞬态）。
    if no_data == 0 and total == 0:
        return (True, "stub")

    # stale 检测：≥90% 超窗口 + 零接受（V37.8.4 漏: CNS1952 98/99=99% 零接受）
    # 整数比较等价 old/total >= 0.9。count==0 守卫防「还在低频产出」误判。
    if count == 0 and total > 0 and old * ZOMBIE_STALE_DEN >= total * ZOMBIE_STALE_NUM:
        # V37.9.103 核心：用最新推文年龄区分 dead(真死,可移除) vs frozen(冻结/低频,勿移除)
        if newest_age_days is not None and newest_age_days >= DEAD_AGE_DAYS:
            return (True, "dead")
        # newest < 730 天 或 无法判定 → 判 frozen，不建议移除（V37.9.102 血案核心：
        # Syndication 把天天发推的活账号 XHNews/CGTN 快照冻在 6-10 月前，
        # 有 total>0 老推文，72h 窗口判 stale 会错杀活账号）
        return (False, "frozen")

    return (False, "alive")


if __name__ == "__main__":
    # 命令行 smoke test：python3 finance_news_zombie.py
    test_cases = [
        # (diag, count, expected_is_zombie, expected_tier, description)
        ({"total": 0, "old": 0, "no_data": 0}, 0, True, "stub",
         "stub: 空 stub embed-disabled (SingTaoDaily) — Syndication 永久拿不到, 可移除"),
        ({"total": 0, "old": 0, "no_data": 1}, 0, False, "alive",
         "non-stub: HTML 无 __NEXT_DATA__ (429/格式变化, 瞬态)"),
        ({"total": 99, "old": 98, "no_data": 0, "newest_age_days": 200}, 0, False, "frozen",
         "frozen: 99% 超窗口但最新推文 200 天前 (Syndication 冻结, 活账号, 勿移除)"),
        ({"total": 99, "old": 98, "no_data": 0, "newest_age_days": 2227}, 0, True, "dead",
         "dead: 99% 超窗口 + 最新推文 2227 天前 (caixin 真死, 可移除)"),
        ({"total": 10, "old": 10, "no_data": 0}, 0, False, "frozen",
         "frozen: 100% 超窗口但无 newest_age_days → 保守判 frozen (防错杀)"),
        ({"total": 10, "old": 10, "no_data": 0, "newest_age_days": 730}, 0, True, "dead",
         "dead: 边界 newest=730 天 (恰好 >=DEAD_AGE_DAYS)"),
        ({"total": 10, "old": 10, "no_data": 0, "newest_age_days": 729}, 0, False, "frozen",
         "frozen: 边界 newest=729 天 (<DEAD_AGE_DAYS, 勿移除)"),
        ({"total": 99, "old": 98, "no_data": 0, "newest_age_days": 200}, 1, False, "alive",
         "alive: 99% 老化但 count=1 (低频活账号, 守卫优先于 stale)"),
        ({"total": 10, "old": 8, "no_data": 0, "newest_age_days": 1000}, 0, False, "alive",
         "alive: 80% 不触发 stale (即使 newest 年久也不判 dead)"),
        ({"total": 20, "old": 0, "no_data": 0, "newest_age_days": 1}, 3, False, "alive",
         "alive: 全部新鲜健康账号"),
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

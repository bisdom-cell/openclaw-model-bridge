"""ai_leaders_rotation — V37.9.101 账号轮换抓取 + 健康分类（纯函数，可单测）

背景（2026-06-03 复盘实测）:
  V37.9.95 把 ai_leaders_x 账号 19→31。每次 cron 31 个 X Syndication 请求触发 429
  限流——单条隔离请求都返回 `HTTP:429 SIZE:20`（IP 被标记），job 产 0 推文。
  V37.8.4/V37.9.99 当时把"0 新推文"误读成 seen-dedup，实际是 no_data（限流）。
  V37.9.99 的 5s inter-account 节流对单 run 内有帮助，但 31 个请求仍超阈值。

修复策略（用户 2026-06-03 选"建轮换抓取修复"）:
  - 轮换抓取: 每 run 只抓 ~11 个账号子集（按 rotation_idx 轮换 + 环绕），3 run 覆盖
    全部 31。单 run 请求量 31→11 降到限流阈值下，配合 5s 节流 = 55s 摊开。
  - 健康分类: 区分 rate_limited(429/no_data，**瞬态不误杀**) / stub(embed-disabled
    空壳) / frozen / dead / alive。这是把 V37.8.4 finance_news ZOMBIE 检测 promote 到
    ai_leaders 的关键适配——research 账号低频，必须用 newest-age 判据（而非 finance_news
    的高频老化比例），且**429 绝不判僵尸**（避免限流期间误杀活账号，V37.8.4 promote 盲区）。

V37.9.103（冻结≠死亡，来自 V37.9.102 ai_leaders_x 复盘 + 用户 clean+reduce 决策）:
  V37.9.102 实测发现 X Syndication API 平台级退化——把天天发推的活账号(karpathy/LeCun/
  Hinton)快照冻结在 6-10 月前。原 STATUS_STALE(newest>7d=真僵尸)会让这些活账号在 7d 窗口下
  全被判僵尸。修复：拆成 STATUS_FROZEN(7d<newest<730d，冻结/低频，活账号勿移除) vs
  STATUS_DEAD(newest≥730d，真死/改名可移除)。is_zombie_suspect 仅含 stub+dead，frozen 不判。
  这是预防 #18 zombie loop-wiring（尚未接入 run 脚本）将来错杀活账号的防线，且与
  finance_news_zombie.py 的 frozen/dead 语义保持一致（DEAD_AGE_DAYS=ZOMBIE_DEAD_DAYS=730）。

纯函数零 I/O：rotation 状态文件读写 + 抓取由 shell 负责，本模块只算"抓哪些 + 是否僵尸"。
"""

import math

DEFAULT_BATCH_SIZE = 11   # 每 run 抓取账号数 (31/11 = 3 batch 全覆盖, 1 run/天 → 每账号每 3 天)
ZOMBIE_STALE_DAYS = 7     # newest_tweet > 7d = 超活跃窗口 (task 锁定阈值)
# V37.9.103: 区分 frozen(冻结/低频,活账号勿移除) vs dead(真死,可移除) 的最新推文年龄阈值(天)。
# 与 jobs/finance_news/finance_news_zombie.py 的 DEAD_AGE_DAYS 保持一致(跨模块, test 守卫)。
# 保守 730 天(~2 年)远超 V37.9.102 实测 Syndication 冻结范围(6-10 月)，确保天天发推但快照被
# 冻结的活账号(karpathy/LeCun/Hinton)不被 7d 窗口误判为可移除(#18 wiring 错杀防线)。
ZOMBIE_DEAD_DAYS = 730

# 健康状态常量
STATUS_RATE_LIMITED = "rate_limited"  # 429 / 无 __NEXT_DATA__ / 抓取失败 — 瞬态非僵尸
STATUS_STUB = "stub"                  # HTML 结构完整但 0 本人推文 — embed-disabled 空壳, 可移除
STATUS_FROZEN = "frozen"              # 7d < 最新推文 < 730d — 冻结/低频, 账号可能活着, **勿移除**
STATUS_DEAD = "dead"                  # 最新推文 ≥ 730d — 真死/改名, 可移除
STATUS_ALIVE = "alive"                # 最新推文 ≤ 7d — 活跃


def select_batch(num_accounts, rotation_idx, batch_size=DEFAULT_BATCH_SIZE):
    """返回本 run 应抓取的账号索引列表（轮换 + 环绕，确定性）。

    Args:
        num_accounts: 总账号数 (如 31)
        rotation_idx: 持久轮换计数器 (每 run 递增, 鲁棒于漏跑)
        batch_size: 每 run 抓取数 (默认 11)

    Returns:
        list[int] — 本 run 抓取的账号索引 (0-based)。
        同 (num, idx, batch) 永远同结果。环绕保证 ceil(num/batch) run 覆盖全部。

    契约:
        - num<=0 或 batch<=0 → [] (防御)
        - batch >= num → 全部账号 (退化为不轮换, 单 batch)
        - 最后一批不足 batch_size 时只取剩余, 不重复填充 (不抓重复账号)
    """
    if num_accounts <= 0 or batch_size <= 0:
        return []
    if batch_size >= num_accounts:
        return list(range(num_accounts))
    num_batches = math.ceil(num_accounts / batch_size)
    batch = rotation_idx % num_batches
    start = batch * batch_size
    end = min(start + batch_size, num_accounts)
    return list(range(start, end))


def classify_account(http_ok, has_next_data, tweet_count, newest_age_days):
    """账号健康分类。返回 STATUS_* 之一。

    Args:
        http_ok: 抓取 HTTP 200 (bool)
        has_next_data: HTML 含 __NEXT_DATA__ (bool)
        tweet_count: 解析出的本人推文数 (int, 含超窗口/已见, 全量计数)
        newest_age_days: 最新本人推文距今天数 (int 或 None)

    Returns:
        STATUS_RATE_LIMITED / STATUS_STUB / STATUS_FROZEN / STATUS_DEAD / STATUS_ALIVE

    关键设计:
        - rate_limited (429/无 NEXT_DATA/抓取失败) **绝不判僵尸** — 瞬态, 不误杀活账号
          (V37.8.4 promote 盲区: 限流期间 no_data 全 100% 会让 naive 检测误杀全部).
        - V37.9.103 (冻结≠死亡, 来自 V37.9.102 复盘): 7d < newest < 730d 判 frozen 而非
          僵尸——Syndication 把天天发推的活账号(karpathy/LeCun/Hinton)快照冻结在 6-10 月前,
          7d 窗口若直接判僵尸会误杀活账号. 仅 newest ≥ 730d (年久真死/改名) 才判 dead 可移除.
    """
    if not http_ok or not has_next_data:
        return STATUS_RATE_LIMITED
    if tweet_count == 0:
        return STATUS_STUB
    if newest_age_days is not None and newest_age_days >= ZOMBIE_DEAD_DAYS:
        return STATUS_DEAD
    if newest_age_days is not None and newest_age_days > ZOMBIE_STALE_DAYS:
        return STATUS_FROZEN
    return STATUS_ALIVE


def is_zombie_suspect(status):
    """stub/dead 是可移除候选; rate_limited/frozen/alive 不是。

    V37.9.103: frozen 永远不判僵尸——Syndication 拿不到有老推文的活账号 ≠ 账号死亡
    (V37.9.102 核心血案: XHNews/CGTN 类被错杀). rate_limited 不判僵尸是 V37.9.101 核心契约
    (429 限流期间不能误杀活账号). 仅 stub(embed-disabled 永久无用) + dead(年久真死) 可移除。
    """
    return status in (STATUS_STUB, STATUS_DEAD)


# ─────────────────────────────────────────────────────────────────────────
# CLI: shell 调用 — select 模式输出索引, classify 模式输出状态
# ─────────────────────────────────────────────────────────────────────────
def _main(argv):
    import sys
    if len(argv) >= 2 and argv[1] == "select":
        # ai_leaders_rotation.py select <num> <idx> [batch]
        num = int(argv[2]) if len(argv) > 2 else 0
        idx = int(argv[3]) if len(argv) > 3 else 0
        batch = int(argv[4]) if len(argv) > 4 else DEFAULT_BATCH_SIZE
        print(" ".join(str(i) for i in select_batch(num, idx, batch)))
        return 0
    if len(argv) >= 2 and argv[1] == "classify":
        # ai_leaders_rotation.py classify <http_ok 0/1> <has_next_data 0/1> <count> <newest_age|-1>
        http_ok = argv[2] == "1" if len(argv) > 2 else False
        has_nd = argv[3] == "1" if len(argv) > 3 else False
        count = int(argv[4]) if len(argv) > 4 else 0
        age_raw = argv[5] if len(argv) > 5 else "-1"
        age = None if age_raw in ("-1", "", "None") else int(age_raw)
        st = classify_account(http_ok, has_nd, count, age)
        print(f"{st} {'1' if is_zombie_suspect(st) else '0'}")
        return 0
    print("usage: ai_leaders_rotation.py select <num> <idx> [batch] | "
          "classify <http_ok> <has_next_data> <count> <newest_age>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))

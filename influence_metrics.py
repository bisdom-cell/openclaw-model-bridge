#!/usr/bin/env python3
"""V37.9.349 影响力指标采集 — 纲领 §5 R1 三条手工命令的机械化（每周随 health_check 跑）.

背景（2026-09-02，用户问「是否需要增加新的内容源」→ 结论不加新源，唯一真缺口是
「我们自己的输出被接收得怎样」这一维从未被机器观察）：
  - `docs/charter_execution_plan_20260705.md` §5 R1 把三条采集命令（S2 引用 / PyPI 下载 /
    GitHub star）写成**每季度手工跑一次**，值一律标「待采集」，两季度零增长才触发反思。
  - 手工季度采集的问题不是慢，是**没人跑**：V37.9.248 建协议至今没有一条实测值入库。
  - 本模块把三条命令收进一个纯 stdlib 模块，由 `health_check.sh`（每周一 09:00 既有 cron）
    作第 10 段「📣 影响力」调用 —— 零新 job、零新 cron、零新状态文件（历史写进既有
    `status.json` 的 `quality.influence`，经 `status_update` 的锁 + 原子写 = MR-9）。
  - 日落法账本：退役的是 R1 里「每季度手工跑三条 curl」这个动作（协议改为读本模块的
    历史），新增的是 1 个纯函数模块 + 1 段周报渲染。

诚实契约（V37.9.322 F3「跑不动 ≠ 没问题」家族）：
  - 每个来源独立 FAIL-OPEN：不可达 / 非 JSON / 字段漂移 → 该来源 `ok=False` 带原因码，
    渲染为「⚠️不可达(原因)」，**绝不把不可达写成 0**（0 会被读作「零引用/零下载」）。
  - 三源全部不可达 → 本周**不记录**（不往历史里塞一行全 None 假装采集过）。
  - 原因码刻意用下划线形态（`http_403` / `connect_failed`），不匹配 job_watchdog 的
    err_pattern（`HTTP[/ ]4xx` / `ERROR:`）—— 周报正文里的诊断信息不该变成告警。

数据源（与 charter_execution_plan §5 R1 一物一形，命令块已改为指向本模块）：
  - Semantic Scholar graph API: citationCount / influentialCitationCount（可选 S2_API_KEY）
  - pypistats.org recent: last_week / last_month（无需安装 pypistats 包）
  - GitHub REST: stargazers_count / forks_count / open_issues_count（可选 GITHUB_TOKEN）

用法:
  python3 influence_metrics.py                 # 渲染一行（不记录）
  python3 influence_metrics.py --record        # 采集 + 写入 status.json quality.influence + 渲染
  python3 influence_metrics.py --json          # 机器可读快照（配 --record 可选）
"""
import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# 单一真理源常量（test_v37_9_349 用 MR-8 契约钉住：pyproject name / 论文 arXiv ID）
# ---------------------------------------------------------------------------
ARXIV_ID = "2606.14589"
PYPI_PACKAGE = "openclaw-ontology-engine"
GITHUB_REPO = "bisdom-cell/openclaw-model-bridge"

S2_URL = ("https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv}"
          "?fields=citationCount,influentialCitationCount")
PYPI_URL = "https://pypistats.org/api/packages/{package}/recent"
GITHUB_URL = "https://api.github.com/repos/{repo}"

HTTP_TIMEOUT_SEC = 8          # 三源串行最坏 24s < health_check safe_call 的 30s 上限
HISTORY_MAX = 60              # 周粒度 ≈ 14 个月，够两季度对比 + 一点余量
FLAT_STREAK_WARN_WEEKS = 13   # 一个季度零增长即在周报里显式标出（纲领 §6-B1 反思触发点是两季度）
USER_AGENT = "openclaw-model-bridge-health-check (+https://github.com/%s)" % GITHUB_REPO

_METRIC_KEYS = ("citations", "influential", "downloads_week", "downloads_month",
                "stars", "forks", "open_issues")


def _warn(msg):
    # MR-11: 诊断走 stderr，不污染 $(...) 捕获的渲染行
    print("[influence] WARN: %s" % msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# HTTP（可注入 getter，测试零网络）
# ---------------------------------------------------------------------------
def _get_json(url, headers=None, timeout=HTTP_TIMEOUT_SEC):
    """返回 (obj, None) 或 (None, reason_code)。reason_code 刻意不匹配 watchdog err_pattern。"""
    req = urllib.request.Request(url, headers=dict(headers or {}))
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return None, "http_%s" % e.code
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).replace(" ", "_")[:24]
        return None, "connect_failed:%s" % reason
    except (OSError, ValueError) as e:  # socket.timeout 是 OSError 子类
        return None, "connect_failed:%s" % type(e).__name__
    try:
        return json.loads(raw.decode("utf-8", errors="replace")), None
    except ValueError:
        return None, "bad_json"


def _as_int(v):
    """字段存在且是整数才算数；缺字段/None/非数值 → None（不伪装成 0）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _fail(err, **fields):
    out = {"ok": False, "error": err}
    out.update({k: None for k in fields})
    return out


def fetch_s2(arxiv_id=ARXIV_ID, api_key=None, getter=_get_json):
    headers = {"x-api-key": api_key} if api_key else {}
    obj, err = getter(S2_URL.format(arxiv=arxiv_id), headers=headers)
    if err:
        return _fail(err, citations=None, influential=None)
    if not isinstance(obj, dict):
        return _fail("bad_json", citations=None, influential=None)
    cites = _as_int(obj.get("citationCount"))
    infl = _as_int(obj.get("influentialCitationCount"))
    if cites is None:
        return _fail("schema_drift:citationCount", citations=None, influential=None)
    return {"ok": True, "error": None, "citations": cites, "influential": infl}


def fetch_pypi(package=PYPI_PACKAGE, getter=_get_json):
    obj, err = getter(PYPI_URL.format(package=package))
    if err:
        return _fail(err, downloads_week=None, downloads_month=None)
    data = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(data, dict):
        return _fail("schema_drift:data", downloads_week=None, downloads_month=None)
    week = _as_int(data.get("last_week"))
    month = _as_int(data.get("last_month"))
    if week is None:
        return _fail("schema_drift:last_week", downloads_week=None, downloads_month=None)
    return {"ok": True, "error": None, "downloads_week": week, "downloads_month": month}


def fetch_github(repo=GITHUB_REPO, token=None, getter=_get_json):
    headers = {"Authorization": "Bearer %s" % token} if token else {}
    obj, err = getter(GITHUB_URL.format(repo=repo), headers=headers)
    if err:
        return _fail(err, stars=None, forks=None, open_issues=None)
    if not isinstance(obj, dict):
        return _fail("bad_json", stars=None, forks=None, open_issues=None)
    stars = _as_int(obj.get("stargazers_count"))
    if stars is None:
        return _fail("schema_drift:stargazers_count", stars=None, forks=None, open_issues=None)
    return {"ok": True, "error": None, "stars": stars,
            "forks": _as_int(obj.get("forks_count")),
            "open_issues": _as_int(obj.get("open_issues_count"))}


def collect(getter=_get_json, env=None, today=None):
    """三源采集 → 快照 dict。每源独立 FAIL-OPEN。"""
    env = os.environ if env is None else env
    date = (today or _dt.date.today()).isoformat()
    s2 = fetch_s2(api_key=env.get("S2_API_KEY") or None, getter=getter)
    pypi = fetch_pypi(getter=getter)
    gh = fetch_github(token=env.get("GITHUB_TOKEN") or None, getter=getter)
    for name, src in (("S2", s2), ("PyPI", pypi), ("GitHub", gh)):
        if not src["ok"]:
            _warn("%s 不可达 (%s)" % (name, src["error"]))
    return {"date": date, "s2": s2, "pypi": pypi, "github": gh}


def any_source_ok(snapshot):
    return any(snapshot[k]["ok"] for k in ("s2", "pypi", "github"))


def flatten(snapshot):
    """快照 → 历史行（扁平，不可达字段 None）。"""
    s2, pypi, gh = snapshot["s2"], snapshot["pypi"], snapshot["github"]
    return {
        "date": snapshot["date"],
        "citations": s2.get("citations"),
        "influential": s2.get("influential"),
        "downloads_week": pypi.get("downloads_week"),
        "downloads_month": pypi.get("downloads_month"),
        "stars": gh.get("stars"),
        "forks": gh.get("forks"),
        "open_issues": gh.get("open_issues"),
    }


# ---------------------------------------------------------------------------
# 历史 / 趋势
# ---------------------------------------------------------------------------
def compute_deltas(row, prev_row):
    """逐指标 delta；任一侧 None → None（不可比，不算 0）。"""
    if not prev_row:
        return {k: None for k in _METRIC_KEYS}
    out = {}
    for k in _METRIC_KEYS:
        a, b = row.get(k), prev_row.get(k)
        out[k] = (a - b) if (a is not None and b is not None) else None
    return out


def _pair_flat(newer, older):
    """两行可比且所有可比指标都未增长 → True；无可比指标 → None（不计入 streak）。"""
    comparable = [(newer.get(k), older.get(k)) for k in _METRIC_KEYS
                  if newer.get(k) is not None and older.get(k) is not None]
    if not comparable:
        return None
    return all(a <= b for a, b in comparable)


def flat_streak(history):
    """从最新行往回数连续「零增长」周数。

    每次拿「当前锚行」与更早一行比：有增长即停；不可比（那周三源都没采到）的行跳过但锚
    不动 —— 全 None 的一周既不打断 streak 也不算一周零增长（没数据不等于没增长）。
    """
    streak = 0
    if not history:
        return 0
    newer = history[-1]
    for older in reversed(history[:-1]):
        verdict = _pair_flat(newer, older)
        if verdict is None:
            continue
        if not verdict:
            break
        streak += 1
        newer = older
    return streak


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _fmt_delta(d, first):
    if first:
        return " (首次)"
    if d is None:
        return ""
    if d > 0:
        return " (+%d)" % d
    if d < 0:
        return " (%d)" % d
    return " (±0)"


def render_line(snapshot, deltas=None, first=False, streak=0):
    """单行周报段。三源全不可达时明说「本周不记录」。"""
    s2, pypi, gh = snapshot["s2"], snapshot["pypi"], snapshot["github"]
    deltas = deltas or {}
    if not any_source_ok(snapshot):
        return ("📣 影响力: ⚠️ 三源均不可达 (S2 %s / PyPI %s / GitHub %s)，本周不记录"
                % (s2["error"], pypi["error"], gh["error"]))
    parts = []
    if s2["ok"]:
        infl = "" if s2.get("influential") is None else "/影响力引用 %d" % s2["influential"]
        parts.append("引用 %d%s%s" % (s2["citations"], _fmt_delta(deltas.get("citations"), first), infl))
    else:
        parts.append("引用 ⚠️不可达(%s)" % s2["error"])
    if pypi["ok"]:
        parts.append("PyPI 周下载 %d%s" % (pypi["downloads_week"],
                                          _fmt_delta(deltas.get("downloads_week"), first)))
    else:
        parts.append("PyPI ⚠️不可达(%s)" % pypi["error"])
    if gh["ok"]:
        forks = "" if gh.get("forks") is None else " fork %d" % gh["forks"]
        parts.append("⭐ %d%s%s" % (gh["stars"], _fmt_delta(deltas.get("stars"), first), forks))
    else:
        parts.append("GitHub ⚠️不可达(%s)" % gh["error"])
    line = "📣 影响力: " + " | ".join(parts)
    if streak >= FLAT_STREAK_WARN_WEEKS:
        line += " | ⚠️ 连续 %d 周零增长（纲领 §6-B1: 两季度零增长触发叙事反思）" % streak
    return line


# ---------------------------------------------------------------------------
# 持久化（MR-9: 只经 status_update 的锁 + 原子写；零新状态文件）
# ---------------------------------------------------------------------------
def _load_history_readonly():
    try:
        import status_update
        data = status_update.load_status()
        infl = (data.get("quality") or {}).get("influence") or {}
        return list(infl.get("history") or [])
    except Exception as e:  # noqa: BLE001 — 周报 FAIL-OPEN
        _warn("读取 status.json 历史失败 (%s)，按无历史渲染" % type(e).__name__)
        return []


def _merge_history(history, row):
    """同日重跑覆盖当日行（幂等，镜像 V37.9.105 周报去重语义）；否则追加；封顶 HISTORY_MAX。"""
    history = [h for h in history if isinstance(h, dict)]
    if history and history[-1].get("date") == row["date"]:
        history[-1] = row
    else:
        history.append(row)
    return history[-HISTORY_MAX:]


def record(snapshot):
    """写入 status.json quality.influence。返回 (recorded, prev_row, history_after)。

    三源全不可达 → 不写、不追加（诚实：没采到就是没采到）。
    """
    if not any_source_ok(snapshot):
        return False, None, _load_history_readonly()
    row = flatten(snapshot)
    try:
        import status_update
        with status_update.status_lock():
            data = status_update.load_status()
            quality = data.setdefault("quality", {})
            infl = quality.get("influence") or {}
            history = [h for h in (infl.get("history") or []) if isinstance(h, dict)]
            prev = None
            for h in reversed(history):
                if h.get("date") != row["date"]:
                    prev = h
                    break
            history = _merge_history(history, row)
            quality["influence"] = {
                "updated": row["date"],
                "latest": row,
                "history": history,
                "sources": {
                    "s2": "arXiv:%s" % ARXIV_ID,
                    "pypi": PYPI_PACKAGE,
                    "github": GITHUB_REPO,
                },
            }
            status_update.save_status(data, updated_by="health_check")
        return True, prev, history
    except Exception as e:  # noqa: BLE001 — 记录失败不阻塞周报
        _warn("写入 status.json 失败 (%s)，本周只渲染不记录" % type(e).__name__)
        history = _load_history_readonly()
        prev = history[-1] if history else None
        return False, prev, history


def run(record_it=False, getter=_get_json, env=None, today=None):
    """采集 → (可选) 记录 → 渲染。返回 dict 供 CLI 输出。"""
    snapshot = collect(getter=getter, env=env, today=today)
    row = flatten(snapshot)
    if record_it:
        recorded, prev, history = record(snapshot)
    else:
        recorded = False
        history = _load_history_readonly()
        prev = None
        for h in reversed(history):
            if h.get("date") != row["date"]:
                prev = h
                break
        history = _merge_history(history, row) if any_source_ok(snapshot) else history
    deltas = compute_deltas(row, prev)
    streak = flat_streak(history) if history else 0
    line = render_line(snapshot, deltas=deltas, first=(prev is None and any_source_ok(snapshot)),
                       streak=streak)
    return {"line": line, "snapshot": snapshot, "row": row, "deltas": deltas,
            "prev": prev, "recorded": recorded, "flat_streak_weeks": streak}


def main(argv=None):
    p = argparse.ArgumentParser(description="影响力指标采集（纲领 R1 机械化，随 health_check 每周跑）")
    p.add_argument("--record", action="store_true",
                   help="写入 status.json quality.influence（默认只渲染不写，供手工查看）")
    p.add_argument("--no-record", action="store_true", help="显式只渲染（health_check 隔离测试用）")
    p.add_argument("--json", action="store_true", help="输出机器可读快照而非单行")
    args = p.parse_args(argv)
    result = run(record_it=(args.record and not args.no_record))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["line"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

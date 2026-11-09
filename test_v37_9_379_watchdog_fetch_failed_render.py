#!/usr/bin/env python3
"""V37.9.379 — job_watchdog fetch_failed 告警只渲染状态文件里真存在的字段。

血案（2026-11-09 08:30 watchdog 告警实录）:
    🟡 DBLP论文监控: 最近一次抓取失败 (HTTP )
`run_dblp.sh` 的 fetch_failed 状态文件只写 time/status/new，不写 http_code，而 watchdog 无条件渲染
"(HTTP $HTTP_CODE)" → 空括号。全仓 14 个 fetch_failed 写入方里 10 个不写 http_code（多源 job 写 errors
计数；单源 dblp/s2/freight/hn 什么都不写），所以这不是 dblp 一家的问题，是消费端渲染假设了生产端没承诺的字段。

修复: (a) 消费端 watchdog 按字段存在性三分支渲染（HTTP 码 / N 源全部失败 / 无括号）；
      (b) dblp / s2 生产端补 `errors` 计数（镜像 blogs/bsky/finance/ontology/rss_blogs 多源写法）。

守卫原则（V37.9.300/328 惯例）: 从 job_watchdog.sh 真源码抽状态检查块跑真 bash（防守卫-实现漂移）；
fetch_failed 写入方从 jobs/ 源码发现、按各自 printf 模板合成状态文件逐一喂（不写死名单，新写入方自动进检查）；
反向证据把旧渲染行放回必须复现空括号（证明 harness 看得见这个 bug，不是空转）。
"""
import glob
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_WATCHDOG = os.path.join(_HERE, "job_watchdog.sh")
_BLOCK_START = "    # 读取 last_run.json 中的时间戳和状态"
_BLOCK_END = "    # 货代 deep_dive 特殊检查"


def _read(rel):
    with open(os.path.join(_HERE, rel), encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def _extract_status_block():
    src = _read("job_watchdog.sh")
    i = src.index(_BLOCK_START)
    j = src.index(_BLOCK_END, i)
    block = src[i:j]
    # 防空转：抽到的块必须包含 fetch_failed 分支与 esac 收尾
    assert "fetch_failed)" in block and "esac" in block and 'ALERTS+=("$_status_msg")' in block, block[:200]
    return block


def _hkt_now_str(minutes_ago=5):
    # watchdog 把 last_run.time 当 HKT 解析（dt - 8h → UTC），fixture 也按 HKT 写
    dt = datetime.now(timezone.utc) + timedelta(hours=8) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _run_block(block, status, display_name="DBLP论文监控", tier="auxiliary", job_id="dblp"):
    """把真源码状态块放进最小壳跑真 bash，返回 (rc, lines)。"""
    with tempfile.TemporaryDirectory() as td:
        status_file = os.path.join(td, "last_run.json")
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
        script = f"""
set -u
NOW_EPOCH=$(date +%s)
ALERTS=(); CORE_ALERTS=(); AUX_ALERTS=(); EXP_ALERTS=()
STATS_PASS=0; STATS_WARN=0
job_id={json.dumps(job_id)}; status_file={json.dumps(status_file)}
max_silence=86400; display_name={json.dumps(display_name)}; tier={json.dumps(tier)}
for _once in 1; do
{block}
done
echo "N_ALERTS=${{#ALERTS[@]}}"
for a in "${{ALERTS[@]}}"; do echo "A|$a"; done
for a in "${{AUX_ALERTS[@]}}"; do echo "AUX|$a"; done
for a in "${{CORE_ALERTS[@]}}"; do echo "CORE|$a"; done
for a in "${{EXP_ALERTS[@]}}"; do echo "EXP|$a"; done
"""
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        return proc.returncode, proc.stdout.splitlines(), proc.stderr


_EMPTY_PAREN = re.compile(r"\(\s*\)|\(HTTP\s*\)")


class TestFetchFailedRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block = _extract_status_block()

    def test_blood_case_dblp_shape_no_empty_parenthesis(self):
        """血案回归：dblp 旧形态 fetch_failed（无 http_code 无 errors）不得渲染 "(HTTP )"。"""
        rc, lines, err = _run_block(self.block, {"time": _hkt_now_str(), "status": "fetch_failed", "new": 0})
        self.assertEqual(rc, 0, err)
        alerts = [l for l in lines if l.startswith("A|")]
        self.assertEqual(len(alerts), 1, lines)
        self.assertIn("最近一次抓取失败", alerts[0])
        self.assertIsNone(_EMPTY_PAREN.search(alerts[0]), alerts[0])
        self.assertNotIn("HTTP", alerts[0], "无状态码时不得出现 HTTP 字样")

    def test_http_code_rendered_when_present(self):
        """检出力不减：arxiv 形态带 http_code 仍渲染 (HTTP 403)。"""
        rc, lines, err = _run_block(self.block, {"time": _hkt_now_str(), "status": "fetch_failed", "new": 0, "http_code": "403"},
                                    display_name="ArXiv论文监控", job_id="arxiv_monitor")
        self.assertEqual(rc, 0, err)
        alerts = [l for l in lines if l.startswith("A|")]
        self.assertEqual(len(alerts), 1, lines)
        self.assertIn("最近一次抓取失败 (HTTP 403)", alerts[0])

    def test_errors_count_rendered_for_multi_source_shape(self):
        """多源 job 形态（errors 计数）渲染为「N 源全部失败」。"""
        rc, lines, err = _run_block(self.block, {"time": _hkt_now_str(), "status": "fetch_failed", "new": 0, "errors": 13},
                                    display_name="财经新闻", job_id="finance_news")
        self.assertEqual(rc, 0, err)
        alerts = [l for l in lines if l.startswith("A|")]
        self.assertEqual(len(alerts), 1, lines)
        self.assertIn("最近一次抓取失败 (13 源全部失败)", alerts[0])
        self.assertIsNone(_EMPTY_PAREN.search(alerts[0]), alerts[0])

    def test_http_code_wins_over_errors_when_both_present(self):
        rc, lines, err = _run_block(self.block, {"time": _hkt_now_str(), "status": "fetch_failed", "new": 0,
                                                 "http_code": "503", "errors": 2})
        self.assertEqual(rc, 0, err)
        alerts = [l for l in lines if l.startswith("A|")]
        self.assertIn("(HTTP 503)", alerts[0])

    def test_ok_status_emits_no_alert(self):
        """噪声不增：ok 状态零告警。"""
        rc, lines, err = _run_block(self.block, {"time": _hkt_now_str(), "status": "ok", "new": 3, "sent": True})
        self.assertEqual(rc, 0, err)
        self.assertIn("N_ALERTS=0", lines, lines)

    def test_tier_routing_preserved(self):
        """auxiliary → 🟡 AUX / core → 🔴 CORE 分桶不受渲染改动影响。"""
        _, lines, _ = _run_block(self.block, {"time": _hkt_now_str(), "status": "fetch_failed", "new": 0}, tier="auxiliary")
        self.assertTrue(any(l.startswith("AUX|🟡 ") for l in lines), lines)
        _, lines, _ = _run_block(self.block, {"time": _hkt_now_str(), "status": "fetch_failed", "new": 0}, tier="core")
        self.assertTrue(any(l.startswith("CORE|🔴 ") for l in lines), lines)

    def test_other_status_branches_unchanged(self):
        """parse_failed / 未知状态分支逐字不变（只改 fetch_failed 一支）。"""
        _, lines, _ = _run_block(self.block, {"time": _hkt_now_str(), "status": "parse_failed", "new": 0})
        self.assertTrue(any("最近一次解析异常 (parse_failed)" in l for l in lines), lines)
        _, lines, _ = _run_block(self.block, {"time": _hkt_now_str(), "status": "llm_failed", "new": 0})
        self.assertTrue(any("异常状态 (llm_failed)" in l for l in lines), lines)

    def test_reverse_evidence_old_render_reproduces_empty_parenthesis(self):
        """反向证据：把旧的无条件渲染行放回同一块，同一 fixture 必产 "(HTTP )" —— 证 harness 看得见血案。"""
        new_branch = re.search(r"        fetch_failed\)\n(.*?)\n            ;;", self.block, re.S)
        self.assertIsNotNone(new_branch, "未抽到 fetch_failed 分支")
        old_block = self.block.replace(
            new_branch.group(0),
            '        fetch_failed)\n            _status_msg="$display_name: 最近一次抓取失败 (HTTP $HTTP_CODE)"\n            ;;')
        self.assertNotEqual(old_block, self.block, "替换未发生 = 空转")
        rc, lines, err = _run_block(old_block, {"time": _hkt_now_str(), "status": "fetch_failed", "new": 0})
        self.assertEqual(rc, 0, err)
        alerts = [l for l in lines if l.startswith("A|")]
        self.assertTrue(any("(HTTP )" in a for a in alerts), alerts)


class TestAllFetchFailedWritersRenderCleanly(unittest.TestCase):
    """全部 fetch_failed 写入方从源码发现，按各自 printf 模板合成状态文件喂给真 watchdog 块：
    任一写入方产出的 fetch_failed 都不得渲染出空括号。新增写入方自动进检查。"""

    _PRINTF = re.compile(r"""printf\s+'(\{[^']*"status":"fetch_failed"[^']*\})\\n'""")

    @classmethod
    def setUpClass(cls):
        cls.block = _extract_status_block()
        cls.writers = []
        for path in sorted(glob.glob(os.path.join(_HERE, "jobs", "*", "run_*.sh"))):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            for m in cls._PRINTF.finditer(src):
                cls.writers.append((os.path.relpath(path, _HERE), m.group(1)))

    def _synth(self, template):
        # %s → 占位串, %d → 7；再把 time 换成新鲜 HKT 时间；裸 %s（如 "http":%s）也要能成 JSON
        j = template.replace('"%s"', '"x"').replace("%s", "7").replace("%d", "7")
        d = json.loads(j)
        d["time"] = _hkt_now_str()
        return d

    def test_writer_discovery_not_empty(self):
        self.assertGreaterEqual(len(self.writers), 10, self.writers)
        paths = {p for p, _ in self.writers}
        # V37.9.383: 血案写入方 dblp 已退役删除，改钉同为单源旧形态的 S2
        self.assertTrue(any("run_semantic_scholar.sh" in p for p in paths), paths)
        self.assertTrue(any("run_finance_news.sh" in p for p in paths), paths)

    def test_every_writer_renders_without_empty_parenthesis(self):
        for path, template in self.writers:
            with self.subTest(writer=path):
                status = self._synth(template)
                self.assertEqual(status.get("status"), "fetch_failed", template)
                rc, lines, err = _run_block(self.block, status, display_name="J", job_id=os.path.basename(os.path.dirname(path)))
                self.assertEqual(rc, 0, err)
                alerts = [l for l in lines if l.startswith("A|")]
                self.assertEqual(len(alerts), 1, (path, lines))
                self.assertIsNone(_EMPTY_PAREN.search(alerts[0]), (path, alerts[0]))

    def test_s2_writes_errors_count(self):
        """生产端：s2 的多关键词 fetch_failed 带 errors 计数（镜像 blogs/bsky/finance/ontology/rss_blogs；dblp V37.9.383 退役）。"""
        for rel in ("jobs/semantic_scholar/run_semantic_scholar.sh",):
            with self.subTest(script=rel):
                src = _strip_comments(_read(rel))
                self.assertRegex(
                    src,
                    r'"status":"fetch_failed","new":0,"errors":%d\}\\n\' "\$TS" "\$FETCH_ERRORS"',
                    f"{rel}: fetch_failed 应携带 errors=$FETCH_ERRORS")


class TestSourceGuards(unittest.TestCase):
    def test_http_render_is_conditional(self):
        src = _strip_comments(_read("job_watchdog.sh"))
        m = re.search(r'\n([ \t]*)if \[ -n "\$HTTP_CODE" \]; then\n[ \t]*_status_msg="\$display_name: 最近一次抓取失败 \(HTTP \$HTTP_CODE\)"\n'
                      r'[ \t]*elif \[ -n "\$ERRORS_COUNT" \]; then\n[ \t]*_status_msg="\$display_name: 最近一次抓取失败 \(\$\{ERRORS_COUNT\} 源全部失败\)"\n'
                      r'[ \t]*else\n[ \t]*_status_msg="\$display_name: 最近一次抓取失败"\n[ \t]*fi', src)
        self.assertIsNotNone(m, "fetch_failed 渲染须按字段存在性三分支")
        # 无条件形态必须退役（可执行行里不得再出现紧跟 case 标签的旧单行渲染）
        self.assertNotRegex(src, r'fetch_failed\)\n[ \t]*_status_msg="\$display_name: 最近一次抓取失败 \(HTTP \$HTTP_CODE\)"')

    def test_errors_field_plumbed_from_json_to_shell(self):
        src = _strip_comments(_read("job_watchdog.sh"))
        self.assertIn("errors = d.get('errors', '')", src)
        self.assertIn("{new_count}|{errors}", src)
        self.assertIn("read -r LAST_TIME LAST_STATUS HTTP_CODE DEEP_DIVE NEW_COUNT ERRORS_COUNT", src)

    def test_registered_in_full_regression(self):
        self.assertIn("test_v37_9_379_watchdog_fetch_failed_render.py", _read("full_regression.sh"))

    def test_bash_syntax(self):
        for rel in ("job_watchdog.sh", "jobs/semantic_scholar/run_semantic_scholar.sh"):
            r = subprocess.run(["bash", "-n", os.path.join(_HERE, rel)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (rel, r.stderr))


if __name__ == "__main__":
    unittest.main(verbosity=2)

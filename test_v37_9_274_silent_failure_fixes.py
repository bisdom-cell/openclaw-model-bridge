#!/usr/bin/env python3
"""V37.9.274 — 修 2 个审计 silent-failure 发现 (SF1 + SF2; SF3 verified accept-by-design).

SF1 (MED): ontology_sources/finance_news 的 V37.9.227 fetch_failed 守卫对 parse 失败盲——
  FETCH_ERRORS 只在 curl HTTP 失败增, HTTP 200 但 XML 解析失败(反爬 HTML/非 XML)不计
  → 全源 200-but-unparseable → status:ok 静默(守卫内部的「无数据=失败」盲区)。
  修: (a) parse-fail sys.exit(0)→sys.exit(2) 信号回 shell (b) 根元素校验(well-formed
  反爬 HTML 不触发 ParseError 却根=<html>≠RSS/Atom → exit 2) (c) PARSE_RC!=0 → FETCH_ERRORS++。

SF2 (MED): daily_observer.py run() --dry-run 无条件 append_score_history +
  _write_observer_to_status → 污染宪法级 observer 自己的 score_history.jsonl/status.json
  (llm_ok=False → 假 llm_failed/null 记录, 侵蚀 Stage 5.1 flip 精度)。修: if not dry_run 守卫。

SF3 (LOW): kb_harvest_chat any(ok)→ok 掩盖混合窗口单日全失败 = **V37.9.130 文档化设计权衡**
  (status 枚举保持 ok|error|empty 供 watchdog 契约, 降级走 map_failed/degraded 独立字段;
  改成 any(error)→error 会让常见瞬态混合窗口过度告警) → 对抗验证 accept-by-design 不改。
"""
import os
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ONTO = os.path.join(_HERE, "jobs", "ontology_sources", "run_ontology_sources.sh")
_FIN = os.path.join(_HERE, "jobs", "finance_news", "run_finance_news.sh")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class TestSF1CronParseGuards(unittest.TestCase):
    """SF1 源码守卫: 两 cron 脚本 parse 失败信号回 shell + 根元素校验 + FETCH_ERRORS++。"""

    def _assert_parse_guard(self, src, name):
        import re
        # parse-fail 分支(XML解析失败)须紧跟 sys.exit(2) — 不能被根校验里的另一个
        # sys.exit(2) 占位满足 (V37.9.178 守卫被别处占位教训)。退役静默 sys.exit(0)。
        self.assertRegex(
            src, r"XML解析失败[\s\S]{0,120}?sys\.exit\(2\)",
            f"{name}: parse-fail(XML解析失败) 分支须紧跟 sys.exit(2) 信号回 shell",
        )
        # 捕获退出码
        self.assertIn("PARSE_RC=0", src, f"{name}: 须 init PARSE_RC=0")
        self.assertIn("|| PARSE_RC=$?", src, f"{name}: 须捕获 heredoc 退出码")
        # 根元素校验 (well-formed 反爬 HTML 不触发 ParseError 却根≠RSS/Atom)
        self.assertIn("_root_tag", src, f"{name}: 须有根元素校验(闭合 well-formed HTML 盲区)")
        self.assertIn("('rss', 'feed', 'rdf', 'channel')", src,
                      f"{name}: 根元素白名单 RSS/Atom/RDF/channel")
        # PARSE_RC 非零 → FETCH_ERRORS++
        import re
        self.assertRegex(
            src,
            r'if \[ "\$PARSE_RC" != "0" \]; then[\s\S]*?FETCH_ERRORS=\$\(\(FETCH_ERRORS \+ 1\)\)',
            f"{name}: PARSE_RC!=0 须增 FETCH_ERRORS",
        )
        self.assertIn("V37.9.274", src, f"{name}: 须有 V37.9.274 marker")

    def test_ontology_parse_guard(self):
        self._assert_parse_guard(_read(_ONTO), "ontology_sources")

    def test_finance_parse_guard(self):
        self._assert_parse_guard(_read(_FIN), "finance_news")

    def test_finance_json_branch_also_signals(self):
        # finance JSON 分支 (JSON feed 返回 HTML) 也须 sys.exit(2) 非静默 fall-through
        src = _read(_FIN)
        import re
        self.assertRegex(
            src,
            r"JSON解析失败[\s\S]{0,120}?sys\.exit\(2\)",
            "finance JSON 分支须 sys.exit(2) 信号",
        )


class TestSF1RootValidityLogic(unittest.TestCase):
    """SF1 行为级: 复现根元素校验 (well-formed 反爬 HTML vs 真 RSS/Atom)。"""

    _VALID = ("rss", "feed", "rdf", "channel")

    @staticmethod
    def _root_tag(content):
        import xml.etree.ElementTree as ET
        return ET.fromstring(content).tag.split("}")[-1].lower()

    def _blocked(self, content):
        import xml.etree.ElementTree as ET
        try:
            return self._root_tag(content) not in self._VALID
        except ET.ParseError:
            return True  # 畸形 → 也拦截(exit 2)

    def test_wellformed_antibot_html_blocked(self):
        # 血案核心: well-formed 反爬 HTML parse 成功但根=<html> → 拦截(此前静默 0 item)
        self.assertTrue(self._blocked("<html><body>Access Denied</body></html>"))

    def test_malformed_html_blocked(self):
        self.assertTrue(self._blocked("<html><meta charset=utf-8><br>broken & unescaped"))

    def test_real_rss_passes(self):
        self.assertFalse(self._blocked(
            "<rss version='2.0'><channel><item><title>x</title></item></channel></rss>"))

    def test_real_atom_passes(self):
        self.assertFalse(self._blocked(
            "<feed xmlns='http://www.w3.org/2005/Atom'><entry/></feed>"))

    def test_real_rdf_passes(self):
        self.assertFalse(self._blocked(
            "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'><channel/></rdf:RDF>"))


class TestSF2ObserverDryRun(unittest.TestCase):
    """SF2: daily_observer --dry-run 不持久化(不污染 observer 自己的证据)。

    强制 has_content=True (mock scan_source_sections 非空) + mock 下游 report/prompt
    builders, 让 run() 真到达持久化代码 (line 1600), 才能 exercise `if not dry_run` 守卫
    (否则 has_content=False 会在 line 1535 early-return, 持久化根本不执行 = 假通过)。
    """

    def setUp(self):
        import daily_observer as do
        self.do = do
        self.calls = {"append": 0, "write": 0}
        self._saved = {
            k: getattr(do, k) for k in (
                "append_score_history", "_write_observer_to_status",
                "scan_source_sections", "build_critique_prompt",
                "build_report_markdown", "build_discord_summary",
                "load_score_history",
                "build_trend_section", "build_trend_discord_suffix",
                "build_fail_plausible_section",
            )
        }
        do.append_score_history = lambda *a, **k: self.calls.__setitem__("append", self.calls["append"] + 1)
        do._write_observer_to_status = lambda *a, **k: self.calls.__setitem__("write", self.calls["write"] + 1)
        # 强制 has_content=True (line 1533: source_sections 非空)
        do.scan_source_sections = lambda *a, **k: [{"source": "s", "content": "c"}]
        # mock 下游 (source_sections shape 不重要, 只要 run() 到达持久化不崩)
        do.build_critique_prompt = lambda *a, **k: "prompt"
        do.build_report_markdown = lambda *a, **k: "report"
        do.build_discord_summary = lambda *a, **k: "discord"
        do.load_score_history = lambda *a, **k: []
        do.build_trend_section = lambda *a, **k: ""
        do.build_trend_discord_suffix = lambda *a, **k: ""
        do.build_fail_plausible_section = lambda *a, **k: ""

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(self.do, k, v)

    def test_dry_run_does_not_persist(self):
        # 血案: dry_run + has_content → status=llm_failed 但不得持久化假记录
        kb = tempfile.mkdtemp()
        self.do.run(kb_dir=kb, jobs_dir=kb, dry_run=True)
        self.assertEqual(self.calls["append"], 0, "dry_run 不得 append_score_history")
        self.assertEqual(self.calls["write"], 0, "dry_run 不得 _write_observer_to_status")

    def test_non_dry_run_still_persists(self):
        # 回归: 正常(非 dry_run)仍持久化 (fake llm_caller 避免真 LLM)
        kb = tempfile.mkdtemp()
        fake_caller = lambda *a, **k: (True, "ok content", "ok")
        self.do.run(kb_dir=kb, jobs_dir=kb, dry_run=False, llm_caller=fake_caller)
        self.assertEqual(self.calls["append"], 1, "非 dry_run 须 append_score_history")
        self.assertEqual(self.calls["write"], 1, "非 dry_run 须 _write_observer_to_status")


class TestSF2SourceGuard(unittest.TestCase):
    def test_persistence_guarded_by_not_dry_run(self):
        import re
        src = _read(os.path.join(_HERE, "daily_observer.py"))
        # append_score_history + _write_observer_to_status 须在 if not dry_run: 块内
        self.assertRegex(
            src,
            r"if not dry_run:[\s\S]*?append_score_history\([\s\S]*?_write_observer_to_status\(",
            "两持久化调用须由 if not dry_run 守卫",
        )
        self.assertIn("V37.9.274", src)


if __name__ == "__main__":
    unittest.main()

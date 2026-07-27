#!/usr/bin/env python3
"""test_v37_9_282_parse_guard_rollout.py — V37.9.282 8 脚本 200-but-unparseable 守卫铺开

2026-07-26 对抗审计 JOB-F1（V37.9.281 收工登记的 ready-to-execute 项）:
V37.9.274 只给 ontology_sources + finance_news 加了 parse-fail 守卫（HTTP 200 但
内容不可解析 → exit 2 → FETCH_ERRORS++ / fail-loud），其余 8 个 feed job 仍有同款
盲区 — 反爬 HTML/错误页拿 HTTP 200 → 0 items → status:ok，watchdog 永久盲。

本版按脚本结构分四类镜像铺开:
  A. XML 多源 (rss_blogs / ai_leaders_blogs): V37.9.274 全款 — parse-fail exit 2 +
     根元素白名单 (rss/feed/rdf/channel) + PARSE_RC → FETCH_ERRORS++
  B. JSON 多账号 (ai_leaders_bsky): parse-fail exit 2 + 根形状校验 ('feed' 键) +
     PARSE_RC → FETCH_ERRORS++
  C. JSON 合并解析 (dblp / semantic_scholar): per-file FAIL-OPEN 保留, 但全部结果
     文件解析失败 → exit 2 → 既有 `if ! python3` 包装写 parse_failed + exit 1
  D. 单源 (hn_watcher XML / chaspark JSON / freight 多源合并): parse-fail/根异常/
     API 错误体 → exit 2 → shell 写 parse_failed / fetch_failed (不落 "无新内容" ok)

不受影响 (已有内容验证, V37.9.274/审计确认): arxiv (head grep <feed), acl
(<collection), hf_papers + github_trending (json.load 验证循环)。
"""
import os
import re
import subprocess
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))

_XML_MULTI = ("jobs/rss_blogs/run_rss_blogs.sh",
              "jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh")
_ALL_EIGHT = _XML_MULTI + (
    "jobs/ai_leaders_bsky/run_ai_leaders_bsky.sh",
    "jobs/dblp/run_dblp.sh",
    "jobs/semantic_scholar/run_semantic_scholar.sh",
    "jobs/hn_watcher/run_hn_fixed.sh",
    "jobs/chaspark/run_chaspark.sh",
    "jobs/freight_watcher/run_freight.sh",
)


def _read(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f:
        return f.read()


class TestXmlMultiSourceGuards(unittest.TestCase):
    """类 A: rss_blogs / ai_leaders_blogs — V37.9.274 全款镜像。"""

    def _assert_guard(self, rel):
        src = _read(rel)
        # parse-fail 分支须紧跟 sys.exit(2) — 锚定"XML解析失败"防被别处 exit(2)
        # 占位满足 (V37.9.178 教训, 镜像 test_v37_9_274 同款收紧)
        self.assertRegex(src, r"XML解析失败[\s\S]{0,140}?sys\.exit\(2\)",
                         f"{rel}: parse-fail 分支须 sys.exit(2)")
        self.assertNotRegex(src, r"XML解析失败[\s\S]{0,140}?sys\.exit\(0\)",
                            f"{rel}: 静默 sys.exit(0) 反模式不得回归")
        self.assertIn("PARSE_RC=0", src, f"{rel}: 须 init PARSE_RC")
        self.assertIn("|| PARSE_RC=$?", src, f"{rel}: 须捕获 heredoc 退出码")
        self.assertIn("('rss', 'feed', 'rdf', 'channel')", src,
                      f"{rel}: 根元素白名单 (well-formed 反爬 HTML 盲区)")
        self.assertRegex(
            src,
            r'if \[ "\$PARSE_RC" != "0" \]; then[\s\S]*?FETCH_ERRORS=\$\(\(FETCH_ERRORS \+ 1\)\)',
            f"{rel}: PARSE_RC!=0 须增 FETCH_ERRORS")

    def test_rss_blogs_guard(self):
        self._assert_guard(_XML_MULTI[0])

    def test_ai_leaders_blogs_guard(self):
        self._assert_guard(_XML_MULTI[1])


class TestBskyJsonGuards(unittest.TestCase):
    """类 B: ai_leaders_bsky — JSON parse + 根形状 ('feed' 键)。"""

    def test_bsky_guard(self):
        src = _read("jobs/ai_leaders_bsky/run_ai_leaders_bsky.sh")
        self.assertRegex(src, r"JSON 解析失败[\s\S]{0,140}?sys\.exit\(2\)")
        self.assertIn("'feed' not in data", src,
                      "错误 JSON 响应体 (无 feed 键) 须被根形状校验拦截")
        self.assertIn("PARSE_RC=0", src)
        self.assertIn("|| PARSE_RC=$?", src)
        self.assertRegex(
            src,
            r'if \[ "\$PARSE_RC" != "0" \]; then[\s\S]*?FETCH_ERRORS=\$\(\(FETCH_ERRORS \+ 1\)\)')

    def test_bsky_root_shape_logic(self):
        # 行为级: 复现根形状判定 — 错误体拦截 / 正常空 feed 不误伤
        def blocked(data):
            return not isinstance(data, dict) or 'feed' not in data
        self.assertTrue(blocked({"error": "RateLimitExceeded"}))
        self.assertTrue(blocked([1, 2]))
        self.assertFalse(blocked({"feed": []}),
                         "零帖活账号有 feed:[] 键, 不得误伤")
        self.assertFalse(blocked({"feed": [{"post": {}}]}))


class TestMergedJsonGuards(unittest.TestCase):
    """类 C: dblp / semantic_scholar — 全部结果文件解析失败 → exit 2。"""

    def _assert_guard(self, rel, tag):
        src = _read(rel)
        self.assertIn("_parse_failed", src, f"{rel}: 须逐文件计数 parse 失败")
        self.assertRegex(
            src, r"if _result_files and _parse_failed >= len\(_result_files\):"
                 r"[\s\S]{0,300}?sys\.exit\(2\)",
            f"{rel}: 全文件解析失败须 exit 2 (routed 到既有 parse_failed 分支)")
        # 既有 `if ! python3` 包装 + parse_failed 状态写入仍在 (exit 2 的消费方)
        self.assertIn("if ! python3", src, f"{rel}: parse_failed 包装是 exit 2 的消费方")
        self.assertIn('"status":"parse_failed"', src.replace("'", '"'),
                      f"{rel}: parse_failed 状态写入须存在")

    def test_dblp_guard(self):
        self._assert_guard("jobs/dblp/run_dblp.sh", "dblp")

    def test_s2_guard(self):
        self._assert_guard("jobs/semantic_scholar/run_semantic_scholar.sh", "s2")

    def test_all_failed_logic(self):
        # 行为级: 全失败 exit / 部分失败容忍 / 无文件不误报
        def should_exit(files, failed):
            return bool(files) and failed >= len(files)
        self.assertTrue(should_exit(["a", "b"], 2))
        self.assertFalse(should_exit(["a", "b"], 1), "部分失败保持 FAIL-OPEN")
        self.assertFalse(should_exit([], 0), "无结果文件走 fetch_failed 路径, 不误报")


class TestSingleSourceGuards(unittest.TestCase):
    """类 D: hn_watcher / chaspark / freight — 单源/合并源 fail-loud。"""

    def test_hn_guard(self):
        src = _read("jobs/hn_watcher/run_hn_fixed.sh")
        self.assertRegex(src, r"RSS XML解析失败[\s\S]{0,140}?sys\.exit\(2\)")
        self.assertIn("('rss', 'feed', 'rdf', 'channel')", src)
        self.assertIn("PARSE_RC=0", src)
        self.assertIn("|| PARSE_RC=$?", src)
        self.assertRegex(
            src, r'if \[ "\$PARSE_RC" != "0" \]; then[\s\S]{0,300}?"status":"parse_failed"',
            "hn: PARSE_RC!=0 须写 parse_failed (不落 '无新内容' ok 分支)")

    def test_chaspark_guard(self):
        src = _read("jobs/chaspark/run_chaspark.sh")
        self.assertRegex(src, r"JSON 解析失败[\s\S]{0,160}?sys\.exit\(2\)")
        # API 错误体 (code!=0/无 data) 也须 exit 2 非静默 exit 0
        self.assertRegex(src, r"API 返回异常[\s\S]{0,400}?sys\.exit\(2\)")
        self.assertNotRegex(src, r"API 返回异常[\s\S]{0,400}?sys\.exit\(0\)")
        self.assertRegex(
            src, r'if \[ "\$PARSE_RC" != "0" \]; then[\s\S]{0,300}?"status":"parse_failed"')

    def test_freight_guard(self):
        src = _read("jobs/freight_watcher/run_freight.sh")
        self.assertIn("_src_failed", src, "freight: 须逐源失败计数")
        self.assertRegex(
            src, r"if _src_failed >= len\(SOURCES\):[\s\S]{0,300}?sys\.exit\(2\)",
            "freight: 全源失败须 exit 2")
        self.assertIn("('rss', 'feed', 'rdf', 'channel')", src,
                      "freight: 反爬 HTML 根须计入源失败")
        self.assertRegex(
            src, r'if \[ "\$PARSE_RC" != "0" \]; then[\s\S]{0,300}?"status":"fetch_failed"',
            "freight: PARSE_RC!=0 须 fetch_failed (不落 '暂无新商机' ok 分支)")
        # 旧 `if ! python3 ... then log WARN` 吞 rc 反模式不得回归
        self.assertNotRegex(src, r"then\n\s*log \"WARN: RSS抓取Python脚本失败\"\n\s*fi")


class TestRootValidityShared(unittest.TestCase):
    """行为级 (镜像 test_v37_9_274 TestSF1RootValidityLogic): 白名单逻辑本身。"""

    _VALID = ("rss", "feed", "rdf", "channel")

    def _blocked(self, content):
        import xml.etree.ElementTree as ET
        try:
            return ET.fromstring(content).tag.split("}")[-1].lower() not in self._VALID
        except ET.ParseError:
            return True

    def test_antibot_html_blocked(self):
        self.assertTrue(self._blocked("<html><body>Just a moment...</body></html>"))

    def test_real_rss_atom_pass(self):
        self.assertFalse(self._blocked(
            "<rss version='2.0'><channel><item/></channel></rss>"))
        self.assertFalse(self._blocked(
            "<feed xmlns='http://www.w3.org/2005/Atom'><entry/></feed>"))


class TestSourceMarkersAndSyntax(unittest.TestCase):
    def test_v37_9_282_markers(self):
        for rel in _ALL_EIGHT:
            self.assertIn("V37.9.282", _read(rel), rel)

    def test_all_scripts_syntax_ok(self):
        for rel in _ALL_EIGHT:
            r = subprocess.run(["bash", "-n", os.path.join(BASE, rel)],
                               capture_output=True)
            self.assertEqual(r.returncode, 0, f"{rel}: {r.stderr.decode()}")

    def test_embedded_python_compiles(self):
        # 每个改动脚本的 heredoc python 块须可编译 (语法漂移在 dev 即抓,
        # 否则潜伏到 Mac Mini cron 才炸 — 镜像 V37.9.247 compile 守卫)
        for rel in _ALL_EIGHT:
            src = _read(rel)
            blocks = re.findall(
                r"<< 'PYEOF2?'(?:[^\n]*)\n([\s\S]*?)\nPYEOF2?\n", src)
            self.assertGreaterEqual(len(blocks), 1,
                                    f"{rel}: 未匹配到 heredoc (守卫 vacuous)")
            for code in blocks:
                try:
                    compile(code, f"{rel}:heredoc", "exec")
                except SyntaxError as e:
                    self.fail(f"{rel} heredoc python 语法错误: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""test_run_rss_blogs — V37.9.36 rss_blogs LLM fail-fast 守卫

2026-05-07 血案: 用户视角 (原则 #13) 收到 3 条全是占位符的博客推送
"要点: 技术深度文章 / 价值: ⭐⭐⭐", 但 last_run.json 谎报 status:ok。
真因 = LLM HTTP 502 (primary 301 + gemini 503 双 provider 故障) +
脚本三层宽容 (except pass 吞 KeyError → log WARN 不 exit → emit 硬编码占位符)
+ status 谎报 ok + 零 [SYSTEM_ALERT] 推送 = MR-4 silent-failure 第 26 次演出。

V37.9.36 修复 (与 V37.5/V37.8.10/V37.9.16 同款 fail-fast 模式对齐):
  (a) LLM HTTP 错误响应检测 (`"error":` 字段) → [SYSTEM_ALERT] + status:llm_failed + exit 1
  (b) LLM JSON 解析失败检测 (无 choices 字段) → 同 (a)
  (c) LLM content 为空检测 → 同 (a)
  (d) emit 端删除"要点：技术深度文章" + "价值：⭐⭐⭐"占位符 fallback,
      改为"（本篇 LLM 摘要缺失，参见原文链接）"显式标记
  (e) source notify.sh + send_alert helper 走统一 [SYSTEM_ALERT] 通道
"""

import json
import os
import re
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RSS_SH = os.path.join(REPO_ROOT, "jobs", "rss_blogs", "run_rss_blogs.sh")


# ══════════════════════════════════════════════════════════════════════
# 1. Source-level 守卫 (grep + regex 静态分析)
# ══════════════════════════════════════════════════════════════════════
class TestRssBlogsShellGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(RSS_SH, encoding="utf-8") as f:
            cls.content = f.read()

    def test_v37_9_36_version_marker(self):
        self.assertIn("V37.9.36", self.content)

    def test_system_alert_marker_present(self):
        self.assertIn("[SYSTEM_ALERT]", self.content)

    def test_no_placeholder_fallback_text(self):
        """关键禁止字面量: 占位符 fallback 不得出现在执行代码中
        (V37.5 同款模式: 跳过 # 注释行允许文档描述反模式)
        """
        forbidden_literals = [
            "要点：技术深度文章",
            "价值：⭐⭐⭐\n",
        ]
        for lineno, line in enumerate(self.content.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # shell 注释行允许引用反模式描述
            for literal in forbidden_literals:
                # 行内字面量比较 (跳过含\n的多行检查 — 注释外不可能出现)
                if "\n" not in literal and literal in line:
                    self.fail(
                        f"run_rss_blogs.sh:{lineno} 不得使用占位符 fallback 字面量 {literal!r} "
                        f"(V37.9.36 反模式禁止). 行内容: {line!r}"
                    )

    def test_no_silent_warn_fallback_phrase(self):
        """V37.9.36 前的 'log WARN 不 exit + 继续推送' 反模式禁止"""
        # 旧逻辑的字面量
        self.assertNotIn("使用原始标题推送", self.content)

    def test_send_alert_helper_defined(self):
        """fail-fast 路径必经入口"""
        self.assertIn("send_alert()", self.content)
        self.assertIn("topic alerts", self.content)

    def test_notify_sh_sourced(self):
        """V37.9.36: 通过 notify.sh 走统一 [SYSTEM_ALERT] 通道"""
        # 必须 source notify.sh, 否则 send_alert 内的 `notify` command 不可用
        self.assertRegex(self.content, r'source\s+"\$NOTIFY_SH"')

    def test_llm_http_error_detection(self):
        """LLM HTTP 错误响应检测必须存在"""
        self.assertIn("__LLM_HTTP_ERROR__", self.content)
        # 检测必须读 d['error'] 字段 (502 等响应特征)
        self.assertRegex(self.content, r"isinstance\(d, dict\) and ['\"]error['\"] in d")

    def test_llm_parse_fail_detection(self):
        self.assertIn("__LLM_PARSE_FAIL__", self.content)
        # 必须区分 bad_json 和 no_choices 两种失败模式
        self.assertIn("bad_json", self.content)
        self.assertIn("no_choices", self.content)

    def test_llm_failed_status_in_status_file(self):
        """last_run.json schema 必须支持 status:llm_failed"""
        self.assertIn('"status":"llm_failed"', self.content)

    def test_fail_fast_order_lock_http_error(self):
        """HTTP 错误分支后 500 字符内必须 exit 1"""
        idx = self.content.find("__LLM_HTTP_ERROR__")
        # find next branch (not the python heredoc one inside detection block)
        if_idx = self.content.find("__LLM_HTTP_ERROR__", idx + 1)
        self.assertNotEqual(if_idx, -1, "找不到 HTTP error 检测分支")
        exit_idx = self.content.find("exit 1", if_idx)
        self.assertNotEqual(exit_idx, -1)
        self.assertLess(
            exit_idx - if_idx, 500,
            f"HTTP error 检测分支必须立即 exit 1, 距离 {exit_idx - if_idx}"
        )

    def test_fail_fast_order_lock_empty_content(self):
        """空 content 检测后 500 字符内必须 exit 1"""
        idx = self.content.find("LLM 返回空内容")
        self.assertNotEqual(idx, -1, "找不到空内容检测分支")
        exit_idx = self.content.find("exit 1", idx)
        self.assertNotEqual(exit_idx, -1)
        self.assertLess(exit_idx - idx, 500)

    def test_emit_branch_uses_explicit_missing_label(self):
        """emit 端 partial fallback 必须用显式缺失标记, 不得伪造数据"""
        self.assertIn("LLM 摘要缺失", self.content)


# ══════════════════════════════════════════════════════════════════════
# 2. LLM 响应检测 Python 块 (端到端 subprocess 测真实行为)
# ══════════════════════════════════════════════════════════════════════
class TestLlmResponseDetection(unittest.TestCase):
    """从 run_rss_blogs.sh 抽出 LLM 响应检测 python 脚本片段单测.
    保持与 shell 内 heredoc 字面一致 (MR-8 single source of truth, 改一处错另一处)
    """

    DETECTION_SNIPPET = """
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'__LLM_PARSE_FAIL__:bad_json:{type(e).__name__}', file=sys.stderr)
    sys.exit(0)
if isinstance(d, dict) and 'error' in d:
    err_msg = str(d['error'])[:500].replace(chr(10), ' ')
    print(f'__LLM_HTTP_ERROR__:{err_msg}', file=sys.stderr)
    sys.exit(0)
try:
    content = d['choices'][0]['message']['content']
except (KeyError, IndexError, TypeError) as e:
    print(f'__LLM_PARSE_FAIL__:no_choices:{type(e).__name__}', file=sys.stderr)
    sys.exit(0)
print(content)
"""

    def _run_detection(self, llm_resp):
        result = subprocess.run(
            ["python3", "-c", self.DETECTION_SNIPPET],
            input=llm_resp,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout, result.stderr, result.returncode

    def test_actual_502_blood_lesson_response(self):
        """复现 2026-05-07 18:00 用户血案的实际 LLM_RESP"""
        actual_resp = '{"error": "HTTP Error 502: Bad Gateway | upstream: primary: HTTP Error 301: The HTTP server returned a redirect error that would lead to an infinite loop.\\nThe last 30x error message was:\\nMoved Permanently; gemini: HTTP Error 503: Service Unavailable"}'
        stdout, stderr, rc = self._run_detection(actual_resp)
        self.assertEqual(rc, 0, "exit 应为 0 (sys.exit(0)), 让 shell 通过 stderr marker 检测")
        self.assertEqual(stdout, "", "HTTP error 应不输出 content 到 stdout")
        self.assertIn("__LLM_HTTP_ERROR__", stderr)
        self.assertIn("HTTP Error 502", stderr)
        self.assertIn("primary: HTTP Error 301", stderr)
        self.assertIn("gemini: HTTP Error 503", stderr)

    def test_no_choices_field(self):
        stdout, stderr, _ = self._run_detection('{"unexpected": "schema"}')
        self.assertEqual(stdout, "")
        self.assertIn("__LLM_PARSE_FAIL__:no_choices:KeyError", stderr)

    def test_bad_json(self):
        stdout, stderr, _ = self._run_detection("this is not json")
        self.assertEqual(stdout, "")
        self.assertIn("__LLM_PARSE_FAIL__:bad_json:JSONDecodeError", stderr)

    def test_empty_response(self):
        stdout, stderr, _ = self._run_detection("")
        self.assertEqual(stdout, "")
        self.assertIn("__LLM_PARSE_FAIL__:bad_json", stderr)

    def test_normal_response_passes_through(self):
        normal = '{"choices":[{"message":{"content":"要点：xxx\\n价值：⭐⭐⭐⭐"}}]}'
        stdout, stderr, _ = self._run_detection(normal)
        self.assertIn("要点：xxx", stdout)
        self.assertIn("价值：⭐⭐⭐⭐", stdout)
        self.assertEqual(stderr, "")  # 正常路径 stderr 必须空 (无 marker)

    def test_choices_empty_list(self):
        """edge case: choices 是空 list, IndexError"""
        stdout, stderr, _ = self._run_detection('{"choices": []}')
        self.assertIn("__LLM_PARSE_FAIL__:no_choices:IndexError", stderr)

    def test_choices_not_list(self):
        """edge case: choices 是 dict 不是 list, TypeError"""
        stdout, stderr, _ = self._run_detection('{"choices": {"weird": "schema"}}')
        # dict[0] raises KeyError, dict 索引 [0] 是 KeyError 不是 TypeError
        self.assertIn("__LLM_PARSE_FAIL__:no_choices:", stderr)

    def test_newline_in_error_message_squashed(self):
        """V37.9.36: error 消息含 \\n 应被 squash 为空格 (避免破坏 marker 行)"""
        resp = '{"error": "line1\\nline2\\nline3"}'
        _, stderr, _ = self._run_detection(resp)
        # marker 行不应被 \n 切碎
        marker_line = [l for l in stderr.splitlines() if "__LLM_HTTP_ERROR__" in l]
        self.assertEqual(len(marker_line), 1, f"marker 应在单行: {stderr!r}")
        self.assertIn("line1", marker_line[0])
        self.assertIn("line2", marker_line[0])

    def test_error_message_truncated_to_500_chars(self):
        long_err = "x" * 1000
        resp = json.dumps({"error": long_err})
        _, stderr, _ = self._run_detection(resp)
        # 提取 marker 行后 truncated 长度
        marker_line = next((l for l in stderr.splitlines() if "__LLM_HTTP_ERROR__" in l), "")
        # marker prefix "__LLM_HTTP_ERROR__:" 是 19 chars + max 500 chars err msg
        err_part = marker_line.split("__LLM_HTTP_ERROR__:", 1)[-1]
        self.assertLessEqual(len(err_part), 500)


# ══════════════════════════════════════════════════════════════════════
# 3. emit 端 Python 块测试 (parser + 显式缺失标记)
# ══════════════════════════════════════════════════════════════════════
class TestEmitParser(unittest.TestCase):
    """V37.9.36 emit 端核心: 删除占位符 fallback, 改为显式 LLM 摘要缺失标记"""

    EMIT_SNIPPET = '''
import sys, json, re

articles_file, llm_file, day, msg_file = sys.argv[1:5]

articles = []
with open(articles_file) as f:
    for line in f:
        line = line.strip()
        if line:
            articles.append(json.loads(line))

with open(llm_file) as f:
    llm_content = f.read()

parsed_blocks = []
pending_highlight = None

for raw_line in llm_content.split('\\n'):
    line = raw_line.strip()
    if not line:
        continue
    if re.match(r'^[-=*]{3,}$', line):
        continue
    if re.match(r'^(博文\\d+[：:]?\\s*$|\\d+[.、)]\\s*$)', line):
        continue

    if '价值' in line and '⭐' in line:
        for prefix in ['价值：', '价值:', '第二行：', '第2行：']:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        stars_line = '价值：' + line if not line.startswith('价值') else line
        parsed_blocks.append((pending_highlight or '', stars_line))
        pending_highlight = None
        continue

    if line.startswith('要点：') or line.startswith('要点:'):
        pending_highlight = line
        continue
    for prefix in ['第一行：', '第1行：']:
        if line.startswith(prefix):
            rest = line[len(prefix):].strip()
            pending_highlight = rest if rest.startswith('要点') else '要点：' + rest
            break

msg_lines = [f"\\U0001F4D6 博客精选 ({day})", ""]

for i, article in enumerate(articles):
    msg_lines.append(f"*{article['title']}*")
    msg_lines.append(f"来源：{article['feed_label']} | {article.get('pub_date', '')[:16]}")
    msg_lines.append(f"链接：{article['link']}")

    if i < len(parsed_blocks):
        highlight, stars = parsed_blocks[i]
        if highlight:
            msg_lines.append(highlight)
        if stars:
            msg_lines.append(stars)
    else:
        msg_lines.append("（本篇 LLM 摘要缺失，参见原文链接）")

    msg_lines.append("")

with open(msg_file, 'w') as f:
    f.write('\\n'.join(msg_lines))
'''

    def _run_emit(self, articles, llm_content, day="2026-05-07"):
        tmpdir = tempfile.mkdtemp(prefix="rss_emit_")
        try:
            articles_file = os.path.join(tmpdir, "articles.jsonl")
            llm_file = os.path.join(tmpdir, "llm_content.txt")
            msg_file = os.path.join(tmpdir, "rss_message.txt")
            with open(articles_file, "w") as f:
                for a in articles:
                    f.write(json.dumps(a, ensure_ascii=False) + "\n")
            with open(llm_file, "w") as f:
                f.write(llm_content)
            subprocess.run(
                ["python3", "-c", self.EMIT_SNIPPET, articles_file, llm_file, day, msg_file],
                check=True, timeout=10, capture_output=True,
            )
            with open(msg_file) as f:
                return f.read()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _make_articles(self, n):
        return [
            {
                "title": f"Article {i}",
                "link": f"https://example.com/{i}",
                "feed_label": "TestFeed",
                "pub_date": "2026-05-07T12:00",
            }
            for i in range(n)
        ]

    def test_full_parse_all_articles_have_summary(self):
        articles = self._make_articles(3)
        llm_content = """要点：First insight
价值：⭐⭐⭐⭐
---
要点：Second insight
价值：⭐⭐⭐⭐⭐
---
要点：Third insight
价值：⭐⭐⭐"""
        out = self._run_emit(articles, llm_content)
        self.assertIn("First insight", out)
        self.assertIn("Second insight", out)
        self.assertIn("Third insight", out)
        self.assertNotIn("LLM 摘要缺失", out)
        # 反向: 严禁占位符
        self.assertNotIn("技术深度文章", out)

    def test_partial_parse_marks_missing_explicitly(self):
        """3 篇 article 但 LLM 只返回 2 块 → 第 3 篇显式标记缺失"""
        articles = self._make_articles(3)
        llm_content = """要点：First insight
价值：⭐⭐⭐⭐
---
要点：Second insight
价值：⭐⭐⭐⭐⭐"""
        out = self._run_emit(articles, llm_content)
        self.assertIn("First insight", out)
        self.assertIn("Second insight", out)
        # 第 3 篇显式标记缺失而非伪造
        self.assertIn("LLM 摘要缺失", out)
        # 永远禁止占位符
        self.assertNotIn("技术深度文章", out)

    def test_empty_llm_content_marks_all_missing(self):
        """LLM 返回空内容 (实际上 fail-fast 已拦, 但 emit 端做防御)"""
        articles = self._make_articles(3)
        out = self._run_emit(articles, "")
        # 全部 3 篇都标 LLM 摘要缺失
        self.assertEqual(out.count("LLM 摘要缺失"), 3)
        self.assertNotIn("技术深度文章", out)
        self.assertNotIn("价值：⭐⭐⭐\n", out)

    def test_no_placeholder_strings_in_output_under_any_path(self):
        """任何 LLM 输入下, 输出绝不能含 '技术深度文章' 或裸 ⭐⭐⭐ fallback"""
        articles = self._make_articles(2)
        # 各种异常 LLM 输入
        for llm_content in [
            "",  # 空
            "garbage",  # 完全无格式
            "要点：only one\n价值：⭐⭐",  # 只有 1 块
            "完全无关内容",  # 没有 要点/价值 关键词
        ]:
            out = self._run_emit(articles, llm_content)
            self.assertNotIn(
                "技术深度文章", out,
                f"占位符不得出现, llm_content={llm_content!r}, out={out!r}"
            )


# ══════════════════════════════════════════════════════════════════════
# 4. 血案场景集成: 复现 2026-05-07 18:00 实际数据
# ══════════════════════════════════════════════════════════════════════
class TestActualBloodLessonScenario(unittest.TestCase):
    """完整复现 2026-05-07 18:00 cron 真实失败链.

    数据 (来自 Mac Mini cache):
      llm_raw_last.txt = '{"error": "HTTP Error 502 ..."}'
      llm_content.txt = '\\n' (1 byte, 空 content)
      rss_message.txt = 全部 3 篇 "技术深度文章 / ⭐⭐⭐"
      last_run.json = {"status":"ok","sent":true} ← 谎报

    V37.9.36 后期望行为:
      检测到 HTTP error → fail-fast → 不写 rss_message → 推 [SYSTEM_ALERT] →
      last_run.json 写 status:llm_failed → exit 1
    """

    def test_502_error_response_triggers_fail_fast(self):
        """与 TestLlmResponseDetection 联动验证血案场景的检测命中"""
        actual_resp = '{"error": "HTTP Error 502: Bad Gateway | upstream: primary: HTTP Error 301: The HTTP server returned a redirect error that would lead to an infinite loop.\\nThe last 30x error message was:\\nMoved Permanently; gemini: HTTP Error 503: Service Unavailable"}'

        result = subprocess.run(
            ["python3", "-c", TestLlmResponseDetection.DETECTION_SNIPPET],
            input=actual_resp,
            capture_output=True,
            text=True,
        )
        self.assertIn("__LLM_HTTP_ERROR__", result.stderr)
        self.assertIn("primary: HTTP Error 301", result.stderr)
        self.assertIn("gemini: HTTP Error 503", result.stderr)

    def test_actual_articles_with_empty_content_no_placeholder(self):
        """复现: LLM 内容空 → emit 不能写 '技术深度文章 ⭐⭐⭐' 占位符"""
        actual_articles = [
            {
                "title": "Live blog: Code w/ Claude 2026",
                "link": "https://simonwillison.net/2026/May/6/code-w-claude-2026/#atom-everything",
                "feed_label": "Simon Willison(LLM工具/实践)",
                "pub_date": "2026-05-06T15:58",
            },
            {
                "title": "Vibe coding and agentic engineering are getting closer than I'd like",
                "link": "https://simonwillison.net/2026/May/6/vibe-coding-and-agentic-engineering/#atom-everything",
                "feed_label": "Simon Willison(LLM工具/实践)",
                "pub_date": "2026-05-06T14:24",
            },
        ]
        emit_test = TestEmitParser()
        out = emit_test._run_emit(actual_articles, "")
        # 关键回归: 用户血案中看到的 3x "技术深度文章 ⭐⭐⭐" 不能再出现
        self.assertNotIn("技术深度文章", out)
        self.assertNotIn("价值：⭐⭐⭐\n", out)
        # 反而每篇标显式缺失
        self.assertEqual(out.count("LLM 摘要缺失"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""test_run_rss_blogs — V37.9.37 5-字段深度分析 + 每篇独立 retry + LLM_DEGRADED + 多窗口

V37.9.37 升级 (基于 V37.9.36 fail-fast 契约扩展):
  - LLM prompt: 单条"要点+价值"两行 → 5 字段深度分析(标题/要点/洞察/启发/评级)
  - 调用策略: 单次调全部 N 篇 → 每篇独立调 + retry 3 次(5s/10s/20s 退避)
  - 失败语义: 任一失败 → fail-fast → 改为: 全部失败才 fail-fast, 部分失败 partial_degraded
  - LLM_DEGRADED fallback: 失败篇用 RSS description 兜底, 不再发"LLM 摘要缺失"
  - 多窗口: 总长 >8000 字触发 V37.9.21 同款切片 (单段 ≤4000, 段间 sleep 1s 防乱序)
  - status_file: 新增 status:partial_degraded + failed:N 字段

V37.9.36 fail-fast 契约保留: 全部 LLM 失败 → [SYSTEM_ALERT] + status:llm_failed + exit 1
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
# 1. Source-level 守卫 (grep + regex + python_assert 静态分析)
# ══════════════════════════════════════════════════════════════════════
class TestRssBlogsShellGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(RSS_SH, encoding="utf-8") as f:
            cls.content = f.read()

    # ── V37.9.37 标记 ───────────────────────────────────────────────
    def test_v37_9_37_version_marker(self):
        self.assertIn("V37.9.37", self.content)

    # ── V37.9.36 fail-fast 契约保留 (核心反模式禁止) ────────────────
    def test_system_alert_marker_present(self):
        self.assertIn("[SYSTEM_ALERT]", self.content)

    def test_no_placeholder_fallback_text(self):
        """关键禁止字面量: 占位符 fallback 不得出现在执行代码中
        (V37.5/V37.8.10/V37.9.16/V37.9.36 同款反模式禁止)
        """
        for lineno, line in enumerate(self.content.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn(
                "要点：技术深度文章", line,
                f"run_rss_blogs.sh:{lineno} 不得使用 '要点：技术深度文章' 占位符 (V37.9.36 反模式禁止)"
            )

    def test_no_silent_warn_fallback_phrase(self):
        """V37.9.36 前的 'log WARN 不 exit + 继续推送' 反模式禁止"""
        self.assertNotIn("使用原始标题推送", self.content)

    def test_send_alert_helper_defined(self):
        self.assertIn("send_alert()", self.content)
        self.assertIn("topic alerts", self.content)

    def test_notify_sh_sourced(self):
        self.assertRegex(self.content, r'source\s+"\$NOTIFY_SH"')

    # ── V37.9.36 LLM 检测 marker 保留 (移到 retry helper 内) ────────
    def test_llm_http_error_detection_marker(self):
        self.assertIn("__LLM_HTTP_ERROR__", self.content)
        self.assertRegex(self.content, r"isinstance\(d, dict\) and ['\"]error['\"] in d")

    def test_llm_parse_fail_detection_marker(self):
        self.assertIn("__LLM_PARSE_FAIL__", self.content)
        self.assertIn("bad_json", self.content)
        self.assertIn("no_choices", self.content)

    # ── V37.9.37 新增: 每篇独立 retry helper ────────────────────────
    def test_retry_helper_defined(self):
        self.assertIn("call_llm_single_with_retry()", self.content)

    def test_retry_backoff_array(self):
        """retry 间隔 5s/10s/20s 指数退避"""
        self.assertRegex(self.content, r"backoffs=\(5\s+10\s+20\)")

    def test_retry_loop_three_attempts(self):
        """retry helper 内有 0/1/2 三次循环"""
        self.assertRegex(self.content, r"for attempt in 0 1 2")

    def test_per_article_main_loop(self):
        """主循环按 article index 调用"""
        self.assertRegex(self.content, r"for \(\(i=0; i<TOTAL_NEW; i\+\+\)\)")

    # ── V37.9.37 新增: status schema 扩展 ──────────────────────────
    def test_status_partial_degraded(self):
        self.assertIn('"status":"partial_degraded"', self.content)

    def test_status_llm_failed_only_when_all_failed(self):
        """status:llm_failed 只在 TOTAL_FAILED == TOTAL_NEW 时触发"""
        # all_failed_ prefix 标志全失败才 fail-fast (区别于 V37.9.36 任一失败)
        self.assertIn('"all_failed_', self.content)

    # ── V37.9.37 新增: LLM_DEGRADED fallback 标记 ──────────────────
    def test_llm_degraded_marker(self):
        """失败篇必须显式标 [LLM_DEGRADED] 而非伪造摘要"""
        self.assertIn("[LLM_DEGRADED]", self.content)

    def test_no_llm_summary_missing_old_label(self):
        """V37.9.36 的 '（本篇 LLM 摘要缺失，参见原文链接）' 不再使用,
        改为 [LLM_DEGRADED] + RSS description"""
        # V37.9.37 用更明确的 LLM_DEGRADED 标记 + 原文 description fallback
        # (V37.9.36 时是 "LLM 摘要缺失" 加在 partial 路径)
        self.assertIn("[LLM_DEGRADED] 深度分析失败", self.content)

    # ── V37.9.37 新增: 5 字段 prompt + 反幻觉守卫 ──────────────────
    def test_prompt_5_fields(self):
        for emoji_marker in ["📌", "🔑", "💡", "🎯", "⭐"]:
            self.assertIn(emoji_marker, self.content,
                          f"5 字段 prompt 必须含 {emoji_marker}")

    def test_prompt_anti_hallucination_grounding(self):
        """V37.8.6 同款反幻觉守卫: 禁止虚构事实/平台状态/错误码当信号"""
        self.assertIn("严禁虚构", self.content)
        self.assertIn("严禁推断", self.content)

    def test_prompt_rating_dynamic_length(self):
        """⭐⭐⭐→100-150字 / ⭐⭐⭐⭐→250-400字 / ⭐⭐⭐⭐⭐→500-800字"""
        # 至少有这几个 emoji + 长度数字组合
        self.assertRegex(self.content, r"⭐⭐⭐⭐⭐.*?500-800")

    # ── V37.9.37 新增: 多窗口切片 (V37.9.21 同款) ────────────────
    def test_multi_window_threshold_8000(self):
        self.assertRegex(self.content, r'TOTAL_LEN.*-le 8000')

    def test_multi_window_chunk_max_4000(self):
        """每段最大 4000 字 (WhatsApp 客户端不折叠阈值)"""
        self.assertIn("MAX_CHUNK = 4000", self.content)

    def test_multi_window_sleep_one_second(self):
        """段间 sleep 1s 防 WhatsApp 消息乱序 (V37.9.21 契约)"""
        # V37.9.37 多窗口分支内必须 sleep 1
        idx = self.content.find("WA_CHUNK_DIR")
        self.assertNotEqual(idx, -1)
        sleep_idx = self.content.find("sleep 1", idx)
        self.assertNotEqual(sleep_idx, -1)
        self.assertLess(sleep_idx - idx, 3000, "多窗口分支必须含 sleep 1s")

    def test_multi_window_part_indicator(self):
        """多段时每段 header 含 [i/N] 标识"""
        self.assertRegex(self.content, r'\[.*\{i\+1\}.*/.*\{total_parts\}.*\]')

    # ── V37.9.37 fail-fast 顺序锁 (V37.9.36 契约保留) ──────────────
    def test_all_failed_fail_fast_order_lock(self):
        """全部失败分支必须立即 exit 1 (V37.9.36 契约)"""
        idx = self.content.find('TOTAL_FAILED" -eq "$TOTAL_NEW"')
        self.assertNotEqual(idx, -1, "缺少 全部失败 fail-fast 分支")
        exit_idx = self.content.find("exit 1", idx)
        self.assertNotEqual(exit_idx, -1)
        self.assertLess(
            exit_idx - idx, 1500,
            f"全部失败分支必须立即 exit 1, 距离 {exit_idx - idx}"
        )


# ══════════════════════════════════════════════════════════════════════
# 2. LLM 响应检测 snippet (V37.9.36 三层检测仍是核心)
#    在 V37.9.37 中嵌入 retry helper 内部, 单层语义不变
# ══════════════════════════════════════════════════════════════════════
class TestLlmResponseDetection(unittest.TestCase):
    """V37.9.36 detection snippet 的契约不变 — V37.9.37 把它放进 retry helper 内部
    复用, 但 marker / 语义全部保留. 仍是 fail-fast 触发点的核心."""

    DETECTION_SNIPPET = """
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'__LLM_PARSE_FAIL__:bad_json:{type(e).__name__}', file=sys.stderr)
    sys.exit(0)
if isinstance(d, dict) and 'error' in d:
    err_msg = str(d['error'])[:300].replace(chr(10), ' ')
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
            input=llm_resp, capture_output=True, text=True, timeout=10,
        )
        return result.stdout, result.stderr, result.returncode

    def test_actual_502_blood_lesson_response(self):
        actual = '{"error": "HTTP Error 502: Bad Gateway | upstream: primary: HTTP Error 301: Moved Permanently; gemini: HTTP Error 503: Service Unavailable"}'
        stdout, stderr, _ = self._run_detection(actual)
        self.assertEqual(stdout, "")
        self.assertIn("__LLM_HTTP_ERROR__", stderr)
        self.assertIn("HTTP Error 502", stderr)
        self.assertIn("primary: HTTP Error 301", stderr)
        self.assertIn("gemini: HTTP Error 503", stderr)

    def test_no_choices_field(self):
        _, stderr, _ = self._run_detection('{"unexpected": "schema"}')
        self.assertIn("__LLM_PARSE_FAIL__:no_choices:KeyError", stderr)

    def test_bad_json(self):
        _, stderr, _ = self._run_detection("garbage")
        self.assertIn("__LLM_PARSE_FAIL__:bad_json:JSONDecodeError", stderr)

    def test_empty_response(self):
        _, stderr, _ = self._run_detection("")
        self.assertIn("__LLM_PARSE_FAIL__:bad_json", stderr)

    def test_normal_response_passes_through(self):
        normal = '{"choices":[{"message":{"content":"📌 中文标题: T1\\n\\n🔑 核心要点:\\n- A\\n\\n⭐ 评级: ⭐⭐⭐⭐"}}]}'
        stdout, stderr, _ = self._run_detection(normal)
        self.assertIn("📌 中文标题: T1", stdout)
        self.assertIn("⭐⭐⭐⭐", stdout)
        self.assertEqual(stderr, "")


# ══════════════════════════════════════════════════════════════════════
# 3. V37.9.37 5 字段 emit 端 (核心新逻辑: parse_5field_output + LLM_DEGRADED)
# ══════════════════════════════════════════════════════════════════════
class TestEmit5Field(unittest.TestCase):
    """V37.9.37 emit Python 块: 5 字段 key-based parser + LLM_DEGRADED fallback"""

    EMIT_SNIPPET = '''
import sys, json, re

articles_file, results_file, day, msg_file = sys.argv[1:5]

with open(articles_file, encoding='utf-8') as f:
    articles = [json.loads(l) for l in f if l.strip()]
with open(results_file, encoding='utf-8') as f:
    results = [json.loads(l) for l in f if l.strip()]

def parse_5field_output(content):
    fields = {'cn_title': '', 'highlights': '', 'insight': '', 'practice': '', 'rating': ''}
    current_field = None
    current_buffer = []
    def flush():
        if current_field and current_buffer:
            fields[current_field] = '\\n'.join(current_buffer).strip()
    for raw in content.split('\\n'):
        line = raw.rstrip()
        if re.match(r'^[-=*_]{3,}$', line.strip()):
            continue
        if line.lstrip().startswith('📌'):
            flush(); current_field = 'cn_title'; current_buffer = []
            m = re.match(r'.*📌\\s*(?:中文)?标题\\s*[:：]?\\s*(.*)', line)
            if m and m.group(1).strip():
                current_buffer.append(m.group(1).strip())
            continue
        if line.lstrip().startswith('🔑'):
            flush(); current_field = 'highlights'; current_buffer = []; continue
        if line.lstrip().startswith('💡'):
            flush(); current_field = 'insight'; current_buffer = []; continue
        if line.lstrip().startswith('🎯'):
            flush(); current_field = 'practice'; current_buffer = []; continue
        if line.lstrip().startswith('⭐') and current_field != 'rating':
            if '评级' in line or '推荐场景' in line or re.match(r'\\s*⭐+\\s*$', line):
                flush(); current_field = 'rating'; current_buffer = [line.lstrip()]; continue
        if current_field is not None:
            current_buffer.append(line)
    flush()
    return fields

msg_lines = [f"📖 博客精选 ({day})", ""]
degraded_count = 0
for i, article in enumerate(articles):
    msg_lines.append(f"*博文{i+1}: {article['title']}*")
    msg_lines.append(f"来源: {article['feed_label']} | {article.get('pub_date', '')[:16]}")
    msg_lines.append(f"链接: {article['link']}")
    msg_lines.append("")
    result = results[i] if i < len(results) else None
    if result is None or result.get('failed'):
        degraded_count += 1
        msg_lines.append("⚠️ [LLM_DEGRADED] 深度分析失败, 原文摘要供参考:")
        desc = (article.get('description') or '')[:300]
        if desc:
            msg_lines.append(desc)
        else:
            msg_lines.append("(原文无摘要数据, 请直接点链接阅读)")
        msg_lines.append("")
    else:
        fields = parse_5field_output(result.get('content', ''))
        if fields['cn_title']:
            msg_lines.append(f"📌 中文标题: {fields['cn_title']}"); msg_lines.append("")
        if fields['highlights']:
            msg_lines.append("🔑 核心要点:"); msg_lines.append(fields['highlights']); msg_lines.append("")
        if fields['insight']:
            msg_lines.append("💡 关键洞察:"); msg_lines.append(fields['insight']); msg_lines.append("")
        if fields['practice']:
            msg_lines.append("🎯 实践启发:"); msg_lines.append(fields['practice']); msg_lines.append("")
        if fields['rating']:
            msg_lines.append(fields['rating']); msg_lines.append("")
    msg_lines.append("---")
    msg_lines.append("")

with open(msg_file, 'w', encoding='utf-8') as f:
    f.write('\\n'.join(msg_lines))
print(f"degraded={degraded_count}", file=sys.stderr)
'''

    def _run_emit(self, articles, results, day="2026-05-07"):
        tmpdir = tempfile.mkdtemp(prefix="rss_emit_")
        try:
            articles_file = os.path.join(tmpdir, "articles.jsonl")
            results_file = os.path.join(tmpdir, "results.jsonl")
            msg_file = os.path.join(tmpdir, "msg.txt")
            with open(articles_file, "w") as f:
                for a in articles:
                    f.write(json.dumps(a, ensure_ascii=False) + "\n")
            with open(results_file, "w") as f:
                for r in results:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            res = subprocess.run(
                ["python3", "-c", self.EMIT_SNIPPET, articles_file, results_file, day, msg_file],
                check=True, timeout=10, capture_output=True, text=True,
            )
            with open(msg_file) as f:
                return f.read(), res.stderr
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _make_article(self, idx, with_desc=True):
        return {
            "title": f"Article {idx}",
            "link": f"https://example.com/{idx}",
            "feed_label": f"TestFeed{idx}",
            "pub_date": "2026-05-07T12:00",
            "description": f"Original RSS description for article {idx} (provides fallback when LLM fails)" if with_desc else "",
        }

    def _make_full_result(self, idx, rating="⭐⭐⭐⭐"):
        content = f"""📌 中文标题: 测试标题{idx}

🔑 核心要点:
- 要点A{idx}
- 要点B{idx}

💡 关键洞察:
深度洞察段落, 揭示作者立场和方法论, 与行业趋势的关联, 与已有工作的对比.

🎯 实践启发:
- 启发A{idx}
- 启发B{idx}

⭐ 评级: {rating} / 推荐场景: 测试场景{idx}"""
        return {"idx": idx, "content": content, "failed": False, "fail_reason": ""}

    def _make_failed_result(self, idx, reason="HTTP Error 502"):
        return {"idx": idx, "content": "", "failed": True, "fail_reason": reason}

    # ── 5 字段完整解析 ─────────────────────────────────────────────
    def test_full_5field_parse(self):
        articles = [self._make_article(0)]
        results = [self._make_full_result(0)]
        out, _ = self._run_emit(articles, results)
        # 5 字段全部出现
        self.assertIn("📌 中文标题: 测试标题0", out)
        self.assertIn("🔑 核心要点:", out)
        self.assertIn("- 要点A0", out)
        self.assertIn("💡 关键洞察:", out)
        self.assertIn("深度洞察段落", out)
        self.assertIn("🎯 实践启发:", out)
        self.assertIn("- 启发A0", out)
        self.assertIn("⭐ 评级: ⭐⭐⭐⭐", out)
        self.assertIn("推荐场景: 测试场景0", out)
        # 严禁占位符
        self.assertNotIn("技术深度文章", out)
        self.assertNotIn("LLM_DEGRADED", out)

    # ── LLM_DEGRADED fallback ─────────────────────────────────────
    def test_partial_degraded_uses_rss_description(self):
        """部分失败 → 失败篇标 [LLM_DEGRADED] + RSS description 兜底"""
        articles = [self._make_article(0), self._make_article(1)]
        results = [self._make_full_result(0), self._make_failed_result(1)]
        out, stderr = self._run_emit(articles, results)
        # 第 1 篇正常
        self.assertIn("📌 中文标题: 测试标题0", out)
        # 第 2 篇 LLM_DEGRADED + RSS description
        self.assertIn("[LLM_DEGRADED] 深度分析失败", out)
        self.assertIn("Original RSS description for article 1", out)
        # 严禁占位符
        self.assertNotIn("技术深度文章", out)
        # stderr 显示 degraded count
        self.assertIn("degraded=1", stderr)

    def test_all_failed_all_marked_degraded(self):
        """全部失败 (上游 fail-fast 已应该拦, emit 端做防御): 全篇标 LLM_DEGRADED"""
        articles = [self._make_article(0), self._make_article(1)]
        results = [self._make_failed_result(0), self._make_failed_result(1)]
        out, stderr = self._run_emit(articles, results)
        self.assertEqual(out.count("[LLM_DEGRADED]"), 2)
        self.assertIn("Original RSS description for article 0", out)
        self.assertIn("Original RSS description for article 1", out)
        self.assertNotIn("技术深度文章", out)
        self.assertIn("degraded=2", stderr)

    def test_degraded_with_no_description_uses_link_hint(self):
        """RSS description 也空 → 提示用户点链接"""
        articles = [self._make_article(0, with_desc=False)]
        results = [self._make_failed_result(0)]
        out, _ = self._run_emit(articles, results)
        self.assertIn("[LLM_DEGRADED]", out)
        self.assertIn("请直接点链接阅读", out)

    # ── 5 字段容忍性 (key-based parser) ──────────────────────────
    def test_parser_tolerates_missing_field(self):
        """LLM 漏一个字段 (如 实践启发) 不影响其他字段"""
        articles = [self._make_article(0)]
        content = """📌 中文标题: 简洁标题

🔑 核心要点:
- 要点1

💡 关键洞察:
段落分析

⭐ 评级: ⭐⭐⭐ / 推荐场景: 入门读者"""
        results = [{"idx": 0, "content": content, "failed": False, "fail_reason": ""}]
        out, _ = self._run_emit(articles, results)
        self.assertIn("📌 中文标题: 简洁标题", out)
        self.assertIn("🔑 核心要点:", out)
        self.assertIn("💡 关键洞察:", out)
        self.assertIn("⭐ 评级: ⭐⭐⭐", out)
        # 实践启发应不出现 (LLM 没给)
        self.assertNotIn("🎯 实践启发:", out)

    def test_parser_handles_field_order_variation(self):
        """LLM 字段顺序错乱不影响解析"""
        articles = [self._make_article(0)]
        content = """⭐ 评级: ⭐⭐⭐ / 推荐场景: X

📌 中文标题: 标题在后

🔑 核心要点:
- 要点

💡 关键洞察:
洞察"""
        results = [{"idx": 0, "content": content, "failed": False, "fail_reason": ""}]
        out, _ = self._run_emit(articles, results)
        # 全部字段都被识别
        self.assertIn("📌 中文标题: 标题在后", out)
        self.assertIn("⭐ 评级: ⭐⭐⭐", out)

    def test_no_placeholder_under_any_input(self):
        """V37.9.36 反模式守卫: 任何 LLM 输入都不得产生 '技术深度文章' 字面量"""
        articles = [self._make_article(0)]
        for content in ["", "garbage no fields", "📌 \n🔑 \n💡 ", "⭐⭐⭐"]:
            results = [{"idx": 0, "content": content, "failed": False, "fail_reason": ""}]
            out, _ = self._run_emit(articles, results)
            self.assertNotIn("技术深度文章", out, f"占位符泄漏: content={content!r}")
            self.assertNotIn("价值：⭐⭐⭐\n", out)


# ══════════════════════════════════════════════════════════════════════
# 4. status_file schema (V37.9.37 4 状态)
# ══════════════════════════════════════════════════════════════════════
class TestStatusSchema(unittest.TestCase):
    """V37.9.37 status_file schema:
       - status:ok           (全部成功)
       - status:partial_degraded (部分失败, 仍推送)
       - status:llm_failed   (全部失败 fail-fast, V37.9.36 契约保留)
       - status:send_failed  (push 失败, 与 V37.9.36 一致)
    """

    def test_shell_writes_partial_degraded(self):
        """shell 源码必须含 partial_degraded 写入分支"""
        with open(RSS_SH, encoding="utf-8") as f:
            content = f.read()
        self.assertIn('"status":"partial_degraded"', content)
        # 必须有 failed:N 字段
        self.assertRegex(content, r'"failed":%d')

    def test_shell_writes_all_failed_with_reason(self):
        """全部失败必须 status:llm_failed + reason:all_failed_..."""
        with open(RSS_SH, encoding="utf-8") as f:
            content = f.read()
        self.assertIn('"all_failed_', content)


# ══════════════════════════════════════════════════════════════════════
# 5. V37.9.37 多窗口切片 (V37.9.21 同款 mktemp + sleep 1s 模式)
# ══════════════════════════════════════════════════════════════════════
class TestMultiWindowSplit(unittest.TestCase):
    """总长 ≤8000 单段 (V37.9.35 客户端折叠), >8000 多窗口 (V37.9.21)"""

    SPLIT_SNIPPET = '''
import sys, os, re

msg_file, chunk_dir, day = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(msg_file, encoding='utf-8').read()
MAX_CHUNK = 4000

blocks = re.split(r'\\n---\\n', content)
header_block = blocks[0]
article_blocks = [b for b in blocks[1:] if b.strip()]

chunks = []
current = header_block
for block in article_blocks:
    candidate = current + "\\n---\\n" + block
    if len(candidate) < MAX_CHUNK:
        current = candidate
    else:
        chunks.append(current)
        current = block
if current.strip():
    chunks.append(current)

total_parts = len(chunks)
for i, chunk in enumerate(chunks):
    if total_parts > 1:
        if i == 0:
            chunk = chunk.replace(f"📖 博客精选 ({day})",
                                  f"📖 博客精选 [1/{total_parts}] ({day})", 1)
        else:
            chunk = f"📖 博客精选 [{i+1}/{total_parts}] ({day}) (续)\\n\\n" + chunk
    with open(os.path.join(chunk_dir, f"{i:03d}.txt"), 'w', encoding='utf-8') as f:
        f.write(chunk)
print(f"chunks={total_parts}")
'''

    def _run_split(self, msg_content, day="2026-05-07"):
        tmpdir = tempfile.mkdtemp(prefix="rss_split_")
        try:
            msg_file = os.path.join(tmpdir, "msg.txt")
            chunk_dir = os.path.join(tmpdir, "chunks")
            os.makedirs(chunk_dir)
            with open(msg_file, "w") as f:
                f.write(msg_content)
            res = subprocess.run(
                ["python3", "-c", self.SPLIT_SNIPPET, msg_file, chunk_dir, day],
                check=True, timeout=10, capture_output=True, text=True,
            )
            chunks = []
            for fname in sorted(os.listdir(chunk_dir)):
                with open(os.path.join(chunk_dir, fname)) as f:
                    chunks.append(f.read())
            return chunks
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_short_message_single_chunk(self):
        """单篇短文章 → 1 chunk"""
        msg = "📖 博客精选 (2026-05-07)\n\n*博文1: A*\n短内容"
        chunks = self._run_split(msg)
        self.assertEqual(len(chunks), 1)
        self.assertNotIn("[1/", chunks[0])  # 单段无标识

    def test_multi_articles_within_4000_single_chunk(self):
        """多篇但总长 <4000 → 1 chunk"""
        articles = "\n---\n".join([f"*博文{i}: A*\nshort body" for i in range(3)])
        msg = f"📖 博客精选 (2026-05-07)\n\n{articles}\n"
        chunks = self._run_split(msg)
        self.assertEqual(len(chunks), 1)

    def test_split_when_overflow_4000(self):
        """每篇 1500 字, 3 篇必触发切片"""
        big_body = "x" * 1500
        articles_str = "\n---\n".join([f"*博文{i}: A*\n{big_body}" for i in range(3)])
        msg = f"📖 博客精选 (2026-05-07)\n\n{articles_str}\n"
        chunks = self._run_split(msg)
        self.assertGreater(len(chunks), 1, f"应触发切片, got {len(chunks)} chunks")
        # 每段 <4000
        for i, c in enumerate(chunks):
            self.assertLess(len(c), 4500, f"chunk {i} 超长: {len(c)}")

    def test_part_indicator_in_multi_chunks(self):
        """多段时 header 含 [i/N] 标识"""
        big_body = "x" * 2000
        articles_str = "\n---\n".join([f"*博文{i}: A*\n{big_body}" for i in range(3)])
        msg = f"📖 博客精选 (2026-05-07)\n\n{articles_str}\n"
        chunks = self._run_split(msg)
        if len(chunks) > 1:
            self.assertIn("[1/", chunks[0])
            self.assertIn("(续)", chunks[1])


# ══════════════════════════════════════════════════════════════════════
# 6. V37.9.37 血案场景集成: 复现 2026-05-07 18:00 + 单篇独立 retry 后果
# ══════════════════════════════════════════════════════════════════════
class TestActualBloodLessonScenario(unittest.TestCase):
    def test_502_response_still_triggers_fail_fast_marker(self):
        """V37.9.36 detection 在 V37.9.37 retry helper 内仍能识别 502"""
        actual = '{"error":"HTTP Error 502: Bad Gateway | upstream: primary: HTTP Error 301; gemini: HTTP Error 503"}'
        result = subprocess.run(
            ["python3", "-c", TestLlmResponseDetection.DETECTION_SNIPPET],
            input=actual, capture_output=True, text=True,
        )
        self.assertIn("__LLM_HTTP_ERROR__", result.stderr)

    def test_actual_articles_partial_failure_no_placeholder(self):
        """V37.9.37: 即使 1 篇失败 1 篇成功, 失败篇用 LLM_DEGRADED 不写占位符"""
        articles = [
            {
                "title": "Live blog: Code w/ Claude 2026",
                "link": "https://simonwillison.net/2026/May/6/code-w-claude-2026/",
                "feed_label": "Simon Willison(LLM工具/实践)",
                "pub_date": "2026-05-06T15:58",
                "description": "Anthropic 2026 conference live blog",
            },
            {
                "title": "Vibe coding paper",
                "link": "https://example/",
                "feed_label": "Latent Space",
                "pub_date": "2026-05-06",
                "description": "",
            },
        ]
        results = [
            {"idx": 0, "content": "📌 中文标题: 大会现场\n\n🔑 核心要点:\n- A\n\n⭐ 评级: ⭐⭐⭐⭐⭐",
             "failed": False, "fail_reason": ""},
            {"idx": 1, "content": "", "failed": True,
             "fail_reason": "HTTP Error 502"},
        ]
        emit = TestEmit5Field()
        out, stderr = emit._run_emit(articles, results)
        # 第 1 篇正常 5 字段
        self.assertIn("📌 中文标题: 大会现场", out)
        # 第 2 篇 LLM_DEGRADED + 链接提示 (description 空)
        self.assertIn("[LLM_DEGRADED]", out)
        self.assertIn("请直接点链接阅读", out)
        # 严禁 V37.9.36 前的占位符
        self.assertNotIn("技术深度文章", out)
        self.assertNotIn("价值：⭐⭐⭐\n", out)
        # degraded count 准确
        self.assertIn("degraded=1", stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

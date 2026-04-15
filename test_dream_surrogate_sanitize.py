#!/usr/bin/env python3
"""test_dream_surrogate_sanitize.py — V37.8.6 Dream blood lesson regression

血案：2026-04-15 03:00 Dream run。现象：
  - 4 次 LLM 调用全部返回 400 "Bad JSON"（adapter.py:386）
  - 但 Dream 依然产出"Hugging Face 平台危机"完整分析
  - 用户察觉 Signal("Papers with Code 沉默")← 行动("监控 Hugging Face")不对齐

根因三层：
  (1) Map batch prompt 含 surrogate UTF-8 → json.dump + utf-8 file write →
      UnicodeEncodeError → body_file 截断 → adapter 收到破损 JSON → 400
  (2) kb_dream.sh log() 用 `echo` 写 stdout → signals=$(llm_call ...) 捕获
      log 输出 → "LLM raw response: <HTML>Error 400 Bad JSON..." 被写入 cache
  (3) Reduce 读 cache 把错误日志当成外部信号喂给 LLM → LLM 编造 "Hugging Face 危机"
      来合理化看到的"Bad JSON 400"字样 — 原则 #23 链式幻觉典型

V37.8.6 修复：
  - log() 重定向到 stderr（阻断 stdout 污染 cache 的通道）
  - sanitize surrogates 用 U+FFFD 替换 + 文件 errors='replace' 双防线
  - REDUCE/CHUNK system prompt 加反污染守卫禁止把 HTTP error/异常当外部信号

本测试锁定 V37.8.6 合约，防止回归。
"""
import os
import re
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DREAM_SH = os.path.join(_HERE, "kb_dream.sh")


def _load_source():
    with open(_DREAM_SH, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════════
# 1. log() 必须写到 stderr（核心污染通道修复）
# ═══════════════════════════════════════════════════════════════════
class TestLogGoesToStderr(unittest.TestCase):
    def setUp(self):
        self.src = _load_source()

    def test_log_function_redirects_to_stderr(self):
        """log() 函数必须以 >&2 结尾，把输出写到 stderr 而非 stdout

        否则 signals=$(llm_call ...) 命令替换会把 log 输出捕获进 signals，
        连带把"LLM raw response: 400 Bad JSON"错误文本写入 cache file，
        下次 Reduce 读 cache 把错误日志当外部信号喂给 LLM → 编造平台危机幻觉。
        """
        # 找到 log() 函数定义行
        match = re.search(r'^log\(\)\s*\{[^\n]*\}', self.src, re.MULTILINE)
        self.assertIsNotNone(match, "log() 函数定义未找到")
        log_def = match.group(0)
        self.assertIn(">&2", log_def,
                     "log() 必须以 >&2 结尾把输出重定向到 stderr，"
                     "否则会污染 $(llm_call) 命令替换 → cache → Reduce 幻觉")

    def test_log_def_has_v37_8_6_blood_lesson_comment(self):
        """log() 定义附近必须有 V37.8.6 血案注释说明为什么要 stderr"""
        # 在 log() 定义后的 200 字节内必须提到"stderr"和"cache"或"幻觉"
        idx = self.src.find("log() {")
        self.assertGreater(idx, 0)
        trailing = self.src[idx:idx + 600]
        self.assertIn("V37.8.6", trailing,
                     "log() 附近必须有 V37.8.6 血案注释")
        self.assertIn("stderr", trailing)
        self.assertTrue("cache" in trailing or "幻觉" in trailing,
                       "必须解释 stderr 的目的（阻断 cache 污染或 LLM 幻觉链）")


# ═══════════════════════════════════════════════════════════════════
# 2. sanitize surrogates 防止 json.dump UnicodeEncodeError
# ═══════════════════════════════════════════════════════════════════
class TestSanitizeSurrogates(unittest.TestCase):
    """执行 sanitize 逻辑的等价 Python 实现并验证正确性。

    真实代码嵌入在 kb_dream.sh 的 heredoc 内，无法直接 import。
    这里手写等价实现 + grep 守卫确保 shell 里的实现与本测试一致。
    """

    @staticmethod
    def _sanitize(s):
        """V37.8.6 与 kb_dream.sh heredoc 内嵌逻辑等价"""
        if not s:
            return s
        return ''.join('\ufffd' if 0xD800 <= ord(c) <= 0xDFFF else c for c in s)

    def test_lone_high_surrogate_replaced(self):
        """孤立高位代理 U+D800 被替换为 U+FFFD"""
        result = self._sanitize("hello\ud800world")
        self.assertEqual(result, "hello\ufffdworld")

    def test_lone_low_surrogate_replaced(self):
        """孤立低位代理 U+DFFF 被替换为 U+FFFD"""
        result = self._sanitize("\udfffprefix")
        self.assertEqual(result, "\ufffdprefix")

    def test_entire_surrogate_range_replaced(self):
        """U+D800 - U+DFFF 整个范围都被替换"""
        test_codes = [0xD800, 0xD900, 0xDC00, 0xDE00, 0xDFFF]
        for code in test_codes:
            s = f"a{chr(code)}b"
            result = self._sanitize(s)
            self.assertEqual(result, f"a\ufffdb",
                           f"U+{code:04X} 未被替换")

    def test_valid_utf8_unchanged(self):
        """有效 UTF-8 字符串保持不变"""
        valid_samples = [
            "hello world",
            "中文测试 Papers with Code",
            "emoji 🎯 test",
            "accent: café",
            "",
        ]
        for s in valid_samples:
            self.assertEqual(self._sanitize(s), s)

    def test_ascii_boundary_chars_unchanged(self):
        """代理范围外的边界字符不变"""
        # U+D7FF（代理范围前一个）和 U+E000（代理范围后第一个）
        self.assertEqual(self._sanitize("\ud7ff"), "\ud7ff")
        self.assertEqual(self._sanitize("\ue000"), "\ue000")

    def test_sanitized_string_can_be_json_encoded(self):
        """sanitize 后的字符串能通过 json.dumps + utf-8 编码（原 bug 就炸在此）"""
        import json
        corrupted = "prompt with bad surrogate \ud800 inside"
        clean = self._sanitize(corrupted)
        # V37.8.4 之前会炸 UnicodeEncodeError，V37.8.6 必须成功
        try:
            encoded = json.dumps({"content": clean}, ensure_ascii=False).encode("utf-8")
            self.assertIn(b'\xef\xbf\xbd', encoded,  # U+FFFD 的 UTF-8
                         "替换字符必须出现在编码后的字节中")
        except (UnicodeEncodeError, UnicodeDecodeError) as e:
            self.fail(f"sanitize 后仍触发编码错误: {e}")

    def test_raw_surrogate_would_crash_json_dump(self):
        """负向对照：未 sanitize 的 surrogate 确实会让 json.dump 崩溃（证明问题真实存在）"""
        import json
        import io
        f = io.StringIO()
        corrupted = "prompt with bad surrogate \ud800 inside"
        # json.dumps 本身不抛错（str 可含 surrogates），但后续 utf-8 编码炸
        dumped = json.dumps({"content": corrupted}, ensure_ascii=False)
        with self.assertRaises((UnicodeEncodeError, UnicodeDecodeError)):
            dumped.encode("utf-8")


# ═══════════════════════════════════════════════════════════════════
# 3. kb_dream.sh 内嵌 sanitize 实现必须存在（shell 源码守卫）
# ═══════════════════════════════════════════════════════════════════
class TestShellSanitizeImplementation(unittest.TestCase):
    def setUp(self):
        self.src = _load_source()

    def test_llm_call_has_sanitize_function(self):
        """llm_call heredoc 内必须定义 _sanitize 函数"""
        self.assertIn("def _sanitize(s):", self.src,
                     "llm_call heredoc 必须定义 _sanitize 过滤 surrogate")

    def test_sanitize_uses_fffd_replacement(self):
        """_sanitize 实现必须用 U+FFFD (\\ufffd) 替换 surrogate，不能用 '?'"""
        # 匹配 sanitize 函数体中的关键行
        self.assertIn("\\ufffd", self.src,
                     "必须用 U+FFFD (\\ufffd) 替换 surrogate（保持 Unicode 语义）")

    def test_sanitize_covers_full_surrogate_range(self):
        """必须覆盖 U+D800-U+DFFF 整个代理范围"""
        self.assertIn("0xD800 <= ord(c) <= 0xDFFF", self.src,
                     "surrogate 范围检查必须覆盖 U+D800 到 U+DFFF")

    def test_sanitize_applied_to_both_prompt_and_system_msg(self):
        """sanitize 必须同时应用于 prompt 和 system_msg"""
        self.assertIn("prompt = _sanitize(prompt)", self.src)
        self.assertIn("system_msg = _sanitize(system_msg)", self.src)

    def test_file_open_has_errors_replace_second_defense(self):
        """open() 必须用 errors='replace' 作为第二道防线（万一 sanitize 漏网）"""
        self.assertIn("errors='replace'", self.src,
                     "open() 必须用 errors='replace' 作为第二道防线")

    def test_file_open_explicit_utf8(self):
        """open() 必须显式指定 encoding='utf-8'（不依赖系统默认）"""
        # 在 body_file 写入处必须有 encoding='utf-8'
        body_file_open = re.search(
            r"open\('\$body_file',\s*'w',[^)]*\)",
            self.src,
        )
        self.assertIsNotNone(body_file_open, "body_file open 未找到")
        self.assertIn("encoding='utf-8'", body_file_open.group(0))


# ═══════════════════════════════════════════════════════════════════
# 4. REDUCE/CHUNK system prompt 反污染守卫
# ═══════════════════════════════════════════════════════════════════
class TestAntiPollutionSystemPrompt(unittest.TestCase):
    def setUp(self):
        self.src = _load_source()

    def test_reduce_system_forbids_http_error_as_signal(self):
        """REDUCE_SYSTEM 必须明示禁止把 HTTP 错误码当外部信号"""
        # REDUCE_SYSTEM 区块（从 REDUCE_SYSTEM=" 开始，匹配到下一个 "）
        match = re.search(r'REDUCE_SYSTEM="(.+?)"', self.src, re.DOTALL)
        self.assertIsNotNone(match)
        block = match.group(1)
        self.assertIn("反污染", block, "REDUCE_SYSTEM 必须含反污染守卫")
        # 关键字：HTTP 状态码、Bad JSON、异常、错误页 HTML 必须被明示禁止
        forbid_patterns = ["400", "Bad JSON", "U+FFFD", "UnicodeEncodeError"]
        for pattern in forbid_patterns:
            self.assertIn(pattern, block,
                         f"REDUCE_SYSTEM 必须列出 {pattern} 为禁止引用的污染模式")

    def test_reduce_system_forbids_fabricating_platform_status(self):
        """REDUCE_SYSTEM 必须禁止基于错误字样推断平台状态"""
        match = re.search(r'REDUCE_SYSTEM="(.+?)"', self.src, re.DOTALL)
        block = match.group(1)
        # 关键禁令：不得推断 Hugging Face / GitHub / 平台 状态
        self.assertTrue(
            "平台" in block and "状态" in block,
            "必须明示禁止基于污染推断'平台状态'"
        )
        # 举例 Hugging Face / GitHub 以锚定语义
        self.assertIn("Hugging Face", block,
                     "必须至少举例 Hugging Face 作为禁止编造的对象")

    def test_chunk_systems_all_have_anti_pollution(self):
        """CHUNK1/2/3 三个 system prompt 都必须有反污染标记"""
        chunk_systems = re.findall(
            r'CHUNK[123]_SYSTEM="([^"]+)"', self.src)
        self.assertEqual(len(chunk_systems), 3,
                        "必须找到 3 个 CHUNK_SYSTEM")
        for i, chunk in enumerate(chunk_systems, 1):
            self.assertIn("V37.8.6 反污染", chunk,
                         f"CHUNK{i}_SYSTEM 必须含 V37.8.6 反污染标记")


# ═══════════════════════════════════════════════════════════════════
# 5. 文件完整性：kb_dream.sh 仍可 bash -n 通过
# ═══════════════════════════════════════════════════════════════════
class TestShellScriptIntegrity(unittest.TestCase):
    def test_bash_syntax_ok(self):
        """V37.8.6 修改后 bash 语法检查通过（防止引号/heredoc 破坏）"""
        result = subprocess.run(
            ["bash", "-n", _DREAM_SH],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0,
                        f"bash -n 语法检查失败: {result.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""test_harvest_chat.py — 对话精华提炼器单测

覆盖：chunk_conversations, MapReduce 逻辑, 边界情况
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Import module under test
import kb_harvest_chat as harvest


class TestChunkConversations(unittest.TestCase):
    """chunk_conversations 分块逻辑"""

    def _make_turns(self, n, user_len=100, assistant_len=100):
        """生成 n 条模拟对话 turn"""
        return [
            {
                "ts": f"2026-04-09 10:{i:02d}:00",
                "user": f"用户消息 {'x' * user_len}",
                "assistant": f"PA回复 {'y' * assistant_len}",
            }
            for i in range(n)
        ]

    def test_single_chunk_small(self):
        """少量对话 → 单个 chunk"""
        turns = self._make_turns(5)
        chunks = harvest.chunk_conversations(turns)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][1], "1-5")

    def test_single_chunk_empty(self):
        """空对话 → 空 chunks"""
        chunks = harvest.chunk_conversations([])
        self.assertEqual(len(chunks), 0)

    def test_single_turn(self):
        """单条对话 → 单个 chunk"""
        turns = self._make_turns(1)
        chunks = harvest.chunk_conversations(turns)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][1], "1-1")

    def test_multi_chunk_splits_correctly(self):
        """大对话量 → 多个 chunk，按 turn 边界切分"""
        # Each turn is ~250 chars; 45000/250 = ~180 turns per chunk
        turns = self._make_turns(400, user_len=50, assistant_len=50)
        chunks = harvest.chunk_conversations(turns)
        self.assertGreater(len(chunks), 1)
        # Verify all turns are covered (no gaps)
        all_ranges = []
        for _, turn_range in chunks:
            start, end = map(int, turn_range.split("-"))
            all_ranges.extend(range(start, end + 1))
        self.assertEqual(len(all_ranges), 400)
        self.assertEqual(all_ranges[0], 1)
        self.assertEqual(all_ranges[-1], 400)

    def test_chunk_size_within_limit(self):
        """每个 chunk 不超过 CHUNK_MAX_CHARS"""
        turns = self._make_turns(500, user_len=100, assistant_len=100)
        chunks = harvest.chunk_conversations(turns)
        for chunk_text, _ in chunks:
            self.assertLessEqual(len(chunk_text), harvest.CHUNK_MAX_CHARS + 5000,
                                 "Chunk exceeds max size (with margin for last turn)")

    def test_turn_content_preserved(self):
        """对话内容在 chunk 中完整保留"""
        turns = [
            {"ts": "2026-04-09 10:00:00", "user": "特殊内容ABC", "assistant": "回复XYZ"}
        ]
        chunks = harvest.chunk_conversations(turns)
        self.assertIn("特殊内容ABC", chunks[0][0])
        self.assertIn("回复XYZ", chunks[0][0])

    def test_turn_truncation_at_1500(self):
        """单条 turn 的 user/assistant 截断到 1500 字符"""
        turns = [
            {"ts": "2026-04-09 10:00:00",
             "user": "A" * 3000,
             "assistant": "B" * 3000}
        ]
        chunks = harvest.chunk_conversations(turns)
        text = chunks[0][0]
        # Should contain truncated content (1500 chars each)
        self.assertLessEqual(len(text), 3200)  # ~1500 + 1500 + headers

    def test_no_data_loss(self):
        """所有 turn 编号都出现在某个 chunk 中（零数据丢失）"""
        turns = self._make_turns(300, user_len=200, assistant_len=200)
        chunks = harvest.chunk_conversations(turns)
        # Collect all turn numbers mentioned in chunk texts
        total_turns_in_chunks = 0
        for chunk_text, turn_range in chunks:
            start, end = map(int, turn_range.split("-"))
            total_turns_in_chunks += (end - start + 1)
        self.assertEqual(total_turns_in_chunks, 300)


class TestBuildExtractPrompt(unittest.TestCase):
    """提取 prompt 构建"""

    def test_basic_prompt(self):
        """基本 prompt 包含日期和对话内容"""
        prompt = harvest._build_extract_prompt("对话内容", "20260409")
        self.assertIn("20260409", prompt)
        self.assertIn("对话内容", prompt)
        self.assertIn("decision", prompt)

    def test_chunk_info_included(self):
        """分块 prompt 包含段号信息"""
        prompt = harvest._build_extract_prompt("内容", "20260409", "2/3 (对话 50-100)")
        self.assertIn("2/3", prompt)
        self.assertIn("50-100", prompt)
        self.assertIn("完整提取本段", prompt)

    def test_no_chunk_info(self):
        """单块 prompt 不包含段号信息"""
        prompt = harvest._build_extract_prompt("内容", "20260409")
        self.assertNotIn("部分", prompt)


class TestExtractKeyPointsMapReduce(unittest.TestCase):
    """MapReduce 提取逻辑"""

    @patch("kb_harvest_chat._llm_call")
    def test_single_chunk_direct_call(self, mock_llm):
        """单 chunk → 直接一次 LLM 调用"""
        mock_llm.return_value = "- [insight] 测试洞察"
        turns = [
            {"ts": "10:00:00", "user": "你好", "assistant": "你好，有什么可以帮你？"}
        ]
        result = harvest.extract_key_points(turns, "20260409")
        self.assertEqual(mock_llm.call_count, 1)
        self.assertIn("测试洞察", result)

    @patch("kb_harvest_chat._llm_call")
    def test_multi_chunk_mapreduce(self, mock_llm):
        """多 chunk → Map + Reduce"""
        # Mock: map calls return partial results, reduce merges them
        call_count = [0]

        def side_effect(prompt, **kwargs):
            call_count[0] += 1
            if "分段提取" in prompt:
                # This is the reduce call
                return "- [insight] 合并后的洞察\n- [decision] 合并后的决策"
            else:
                # Map calls
                return f"- [insight] 段{call_count[0]}的洞察"

        mock_llm.side_effect = side_effect

        # Generate enough turns to force multiple chunks
        turns = []
        for i in range(500):
            turns.append({
                "ts": f"10:{i % 60:02d}:00",
                "user": f"问题 {'x' * 100}",
                "assistant": f"回答 {'y' * 100}",
            })
        result = harvest.extract_key_points(turns, "20260409")
        # Should have multiple map calls + 1 reduce call
        self.assertGreater(mock_llm.call_count, 2)
        self.assertIn("合并后", result)

    @patch("kb_harvest_chat._llm_call")
    def test_all_chunks_empty(self, mock_llm):
        """所有 chunk 都无关键内容 → 返回无关键内容"""
        mock_llm.return_value = "无关键内容"
        turns = []
        for i in range(500):
            turns.append({
                "ts": f"10:{i % 60:02d}:00",
                "user": f"你好 {'x' * 100}",
                "assistant": f"你好 {'y' * 100}",
            })
        result = harvest.extract_key_points(turns, "20260409")
        self.assertIn("无关键内容", result)

    @patch("kb_harvest_chat._llm_call")
    def test_one_chunk_has_content(self, mock_llm):
        """只有一个 chunk 有内容 → 跳过 reduce"""
        results = ["无关键内容", "- [insight] 唯一洞察", "无关键内容"]
        mock_llm.side_effect = results

        turns = []
        for i in range(500):
            turns.append({
                "ts": f"10:{i % 60:02d}:00",
                "user": f"内容 {'x' * 100}",
                "assistant": f"回复 {'y' * 100}",
            })
        result = harvest.extract_key_points(turns, "20260409")
        # No reduce call needed — only 3 map calls
        self.assertEqual(mock_llm.call_count, len(harvest.chunk_conversations(turns)))
        self.assertIn("唯一洞察", result)

    @patch("kb_harvest_chat._llm_call")
    def test_llm_failure_returns_none(self, mock_llm):
        """LLM 全部失败 → 返回 None"""
        mock_llm.return_value = None
        turns = [{"ts": "10:00:00", "user": "测试", "assistant": "回复内容够长"}]
        result = harvest.extract_key_points(turns, "20260409")
        self.assertIsNone(result)


class TestProcessDate(unittest.TestCase):
    """process_date 集成逻辑"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_chat_dir = harvest.CHAT_LOG_DIR
        self.orig_processed_dir = harvest.PROCESSED_MARKER_DIR
        harvest.CHAT_LOG_DIR = self.tmpdir
        harvest.PROCESSED_MARKER_DIR = os.path.join(self.tmpdir, ".processed")

    def tearDown(self):
        harvest.CHAT_LOG_DIR = self.orig_chat_dir
        harvest.PROCESSED_MARKER_DIR = self.orig_processed_dir
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_date(self):
        """无对话文件 → empty"""
        result = harvest.process_date("20260101")
        self.assertEqual(result, "empty")

    def test_already_processed(self):
        """已处理日期 → skipped"""
        os.makedirs(os.path.join(self.tmpdir, ".processed"), exist_ok=True)
        with open(os.path.join(self.tmpdir, ".processed", "20260101.done"), "w") as f:
            f.write("done")
        result = harvest.process_date("20260101")
        self.assertEqual(result, "skipped")

    def test_dry_run_shows_chunks(self):
        """dry-run 模式显示 chunk 数量"""
        log_file = os.path.join(self.tmpdir, "20260409.jsonl")
        with open(log_file, "w") as f:
            for i in range(10):
                f.write(json.dumps({
                    "ts": f"10:{i:02d}:00",
                    "user": f"问题{i}",
                    "assistant": f"回答{i} 这是一段较长的回复内容",
                }) + "\n")
        result = harvest.process_date("20260409", dry_run=True)
        self.assertEqual(result, "dry_run")

    def test_dry_run_large_shows_multi_chunk(self):
        """大量对话 dry-run 显示多 chunk"""
        log_file = os.path.join(self.tmpdir, "20260409.jsonl")
        with open(log_file, "w") as f:
            for i in range(500):
                f.write(json.dumps({
                    "ts": f"10:{i % 60:02d}:00",
                    "user": "x" * 200,
                    "assistant": "y" * 200,
                }) + "\n")
        result = harvest.process_date("20260409", dry_run=True)
        self.assertEqual(result, "dry_run")


class TestLoadConversations(unittest.TestCase):
    """对话日志加载"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_chat_dir = harvest.CHAT_LOG_DIR
        harvest.CHAT_LOG_DIR = self.tmpdir

    def tearDown(self):
        harvest.CHAT_LOG_DIR = self.orig_chat_dir
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_valid_jsonl(self):
        """加载正常 JSONL 文件"""
        log_file = os.path.join(self.tmpdir, "20260409.jsonl")
        with open(log_file, "w") as f:
            f.write(json.dumps({"ts": "10:00", "user": "A", "assistant": "B"}) + "\n")
            f.write(json.dumps({"ts": "10:01", "user": "C", "assistant": "D"}) + "\n")
        turns = harvest.load_conversations("20260409")
        self.assertEqual(len(turns), 2)

    def test_skip_malformed_lines(self):
        """跳过格式错误的行"""
        log_file = os.path.join(self.tmpdir, "20260409.jsonl")
        with open(log_file, "w") as f:
            f.write(json.dumps({"ts": "10:00", "user": "A", "assistant": "B"}) + "\n")
            f.write("NOT JSON\n")
            f.write(json.dumps({"ts": "10:02", "user": "E", "assistant": "F"}) + "\n")
        turns = harvest.load_conversations("20260409")
        self.assertEqual(len(turns), 2)

    def test_missing_file(self):
        """不存在的文件 → 空列表"""
        turns = harvest.load_conversations("19700101")
        self.assertEqual(turns, [])

    def test_skip_empty_lines(self):
        """跳过空行"""
        log_file = os.path.join(self.tmpdir, "20260409.jsonl")
        with open(log_file, "w") as f:
            f.write(json.dumps({"ts": "10:00", "user": "A", "assistant": "B"}) + "\n")
            f.write("\n")
            f.write("   \n")
            f.write(json.dumps({"ts": "10:01", "user": "C", "assistant": "D"}) + "\n")
        turns = harvest.load_conversations("20260409")
        self.assertEqual(len(turns), 2)


class TestPythonSyntax(unittest.TestCase):
    """基本语法和导入检查"""

    def test_syntax(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('kb_harvest_chat.py').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_imports(self):
        result = subprocess.run(
            [sys.executable, "-c", "import kb_harvest_chat"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chunk_max_chars_defined(self):
        self.assertIsInstance(harvest.CHUNK_MAX_CHARS, int)
        self.assertGreater(harvest.CHUNK_MAX_CHARS, 10000)

    def test_has_mapreduce_functions(self):
        """关键 MapReduce 函数存在"""
        self.assertTrue(callable(harvest.chunk_conversations))
        self.assertTrue(callable(harvest.extract_key_points))
        self.assertTrue(callable(harvest._reduce_key_points))
        self.assertTrue(callable(harvest._build_extract_prompt))
        self.assertTrue(callable(harvest._llm_call))


if __name__ == "__main__":
    unittest.main()

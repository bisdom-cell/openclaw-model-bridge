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
        result, meta = harvest.extract_key_points(turns, "20260409")
        self.assertEqual(mock_llm.call_count, 1)
        self.assertIn("测试洞察", result)
        self.assertEqual(meta["mode"], "single")

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
        result, meta = harvest.extract_key_points(turns, "20260409")
        # Should have multiple map calls + 1 reduce call
        self.assertGreater(mock_llm.call_count, 2)
        self.assertIn("合并后", result)
        self.assertEqual(meta["mode"], "mapreduce")
        self.assertFalse(meta["reduce_degraded"])

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
        result, meta = harvest.extract_key_points(turns, "20260409")
        self.assertIn("无关键内容", result)
        # LLM 真实回答了"无关键内容"≠ LLM 失败，map_failed 必须为 0
        self.assertEqual(meta["map_failed"], 0)

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
        result, meta = harvest.extract_key_points(turns, "20260409")
        # No reduce call needed — only 3 map calls
        self.assertEqual(mock_llm.call_count, len(harvest.chunk_conversations(turns)))
        self.assertIn("唯一洞察", result)

    @patch("kb_harvest_chat._llm_call")
    def test_llm_failure_returns_none(self, mock_llm):
        """LLM 全部失败 → 返回 None（V37.9.130: 含 retry 后仍失败）"""
        mock_llm.return_value = None
        turns = [{"ts": "10:00:00", "user": "测试", "assistant": "回复内容够长"}]
        result, meta = harvest.extract_key_points(turns, "20260409")
        self.assertIsNone(result)
        # V37.9.130: 单 chunk 路径也走 retry → 1 + LLM_RETRY 次调用
        self.assertEqual(mock_llm.call_count, 1 + harvest.LLM_RETRY)


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
        result, meta = harvest.process_date("20260101")
        self.assertEqual(result, "empty")

    def test_already_processed(self):
        """已处理日期 → skipped"""
        os.makedirs(os.path.join(self.tmpdir, ".processed"), exist_ok=True)
        with open(os.path.join(self.tmpdir, ".processed", "20260101.done"), "w") as f:
            f.write("done")
        result, meta = harvest.process_date("20260101")
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
        result, meta = harvest.process_date("20260409", dry_run=True)
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
        result, meta = harvest.process_date("20260409", dry_run=True)
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
        """关键 MapReduce 函数存在（V37.9.130: hierarchical reduce 家族）"""
        self.assertTrue(callable(harvest.chunk_conversations))
        self.assertTrue(callable(harvest.extract_key_points))
        self.assertTrue(callable(harvest._reduce_hierarchical))
        self.assertTrue(callable(harvest._mechanical_dedup))
        self.assertTrue(callable(harvest._build_reduce_prompt))
        self.assertTrue(callable(harvest._llm_call_with_retry))
        self.assertTrue(callable(harvest._build_extract_prompt))
        self.assertTrue(callable(harvest._llm_call))


# ──────────────────────────────────────────────────────────────────────
# V37.9.130: Reduce 超时血案修复测试（hierarchical reduce + fail-soft +
# retry + --days 3 自动补提炼 + last_run observability）
# 血案: 2026-06-03/04 大对话日 163 轮/37.7 万字/9 chunks, Map 全成功但
# Reduce 单次合并 9 段超时 (timeout=120s), 数据断流且失败日永不自动重试。
# ──────────────────────────────────────────────────────────────────────


class TestMechanicalDedup(unittest.TestCase):
    """V37.9.130 fail-soft 机械去重（零 LLM 兜底）"""

    def test_dedup_preserves_order(self):
        """重复行去重 + 首次出现顺序保留"""
        segs = [
            "- [insight] 洞察A\n- [decision] 决策B",
            "- [decision] 决策B\n- [insight] 洞察C",
        ]
        out = harvest._mechanical_dedup(segs)
        lines = out.split("\n")
        self.assertEqual(lines, ["- [insight] 洞察A", "- [decision] 决策B",
                                 "- [insight] 洞察C"])

    def test_segment_headers_stripped(self):
        """=== 段头被剥离，不进入产物"""
        segs = [
            "=== 第1段 (对话 1-50) ===\n- [insight] 洞察A",
            "=== 第2段 (对话 51-99) ===\n- [insight] 洞察B",
        ]
        out = harvest._mechanical_dedup(segs)
        self.assertNotIn("===", out)
        self.assertIn("洞察A", out)
        self.assertIn("洞察B", out)

    def test_blank_lines_stripped(self):
        """空白行被剥离"""
        segs = ["- [insight] 洞察A\n\n   \n- [insight] 洞察B"]
        out = harvest._mechanical_dedup(segs)
        self.assertEqual(out, "- [insight] 洞察A\n- [insight] 洞察B")

    def test_zero_data_loss_across_segments(self):
        """跨段独特内容全部存活（零数据丢失契约）"""
        segs = [f"- [insight] 独特内容{i}" for i in range(9)]
        out = harvest._mechanical_dedup(segs)
        for i in range(9):
            self.assertIn(f"独特内容{i}", out)

    def test_all_headers_fallback_to_raw_join(self):
        """极端兜底：全是段头时返回原拼接而非空字符串"""
        segs = ["=== 第1段 ===", "=== 第2段 ==="]
        out = harvest._mechanical_dedup(segs)
        self.assertTrue(out)  # 绝不返回空
        self.assertIn("第1段", out)


class TestLlmCallWithRetry(unittest.TestCase):
    """V37.9.130 LLM retry（镜像 V37.9.74/75 dream retry 模式）"""

    @patch("kb_harvest_chat._llm_call")
    def test_first_success_no_retry(self, mock_llm):
        mock_llm.return_value = "结果"
        out = harvest._llm_call_with_retry("p", label="T")
        self.assertEqual(out, "结果")
        self.assertEqual(mock_llm.call_count, 1)

    @patch("kb_harvest_chat._llm_call")
    def test_retry_recovers(self, mock_llm):
        """第一次失败第二次成功 → 返回成功值"""
        mock_llm.side_effect = [None, "恢复结果"]
        out = harvest._llm_call_with_retry("p", label="T")
        self.assertEqual(out, "恢复结果")
        self.assertEqual(mock_llm.call_count, 2)

    @patch("kb_harvest_chat._llm_call")
    def test_all_attempts_fail_returns_none(self, mock_llm):
        mock_llm.return_value = None
        out = harvest._llm_call_with_retry("p", label="T")
        self.assertIsNone(out)
        self.assertEqual(mock_llm.call_count, 1 + harvest.LLM_RETRY)


class TestHierarchicalReduce(unittest.TestCase):
    """V37.9.130 层级分批 Reduce 核心契约"""

    def _segs(self, n):
        return [f"=== 第{i}段 (对话 {i*10}-{i*10+9}) ===\n- [insight] 独特内容{i}"
                for i in range(1, n + 1)]

    @patch("kb_harvest_chat._llm_call")
    def test_two_segments_single_round(self, mock_llm):
        """2 段 → 1 批 1 次调用，不降级"""
        mock_llm.return_value = "- [insight] 合并结果"
        out, degraded = harvest._reduce_hierarchical(self._segs(2), "20260604")
        self.assertEqual(out, "- [insight] 合并结果")
        self.assertFalse(degraded)
        self.assertEqual(mock_llm.call_count, 1)

    @patch("kb_harvest_chat._llm_call")
    def test_nine_segments_blood_lesson_two_rounds(self, mock_llm):
        """血案场景 9 段 → r1 [4,4,1] 2 次 LLM + passthrough → 3 段
        → r2 1 次 → 共 3 次小请求（替代原 1 次 40K 大请求）"""
        mock_llm.side_effect = ["r1合并A", "r1合并B", "- [insight] 最终合并"]
        out, degraded = harvest._reduce_hierarchical(self._segs(9), "20260604")
        self.assertEqual(out, "- [insight] 最终合并")
        self.assertFalse(degraded)
        self.assertEqual(mock_llm.call_count, 3)

    @patch("kb_harvest_chat._llm_call")
    def test_batch_size_respected(self, mock_llm):
        """每次 Reduce 调用的输入段数 ≤ REDUCE_BATCH_SIZE"""
        prompts = []

        def record(prompt, **kwargs):
            prompts.append(prompt)
            return "合并ok"

        mock_llm.side_effect = record
        harvest._reduce_hierarchical(self._segs(9), "20260604")
        # 第一轮 prompt 含 Map 段头 "=== 第N段"，每批 ≤ REDUCE_BATCH_SIZE
        for p in prompts:
            n_headers = p.count("=== 第")
            self.assertLessEqual(n_headers, harvest.REDUCE_BATCH_SIZE,
                                 f"batch 段数超限: {n_headers}")

    @patch("kb_harvest_chat._llm_call")
    def test_single_batch_failure_fail_soft(self, mock_llm):
        """单批失败（含 retry）→ 该批机械去重，其他批不受影响，degraded=True"""
        # 9 段 r1: b1 fail(2 calls) + b2 ok(1) + 尾段 passthrough
        # → r2: 1 批 3 段 ok(1)。共 4 calls。
        mock_llm.side_effect = [None, None, "r1合并B", "- [insight] 最终合并"]
        out, degraded = harvest._reduce_hierarchical(self._segs(9), "20260604")
        self.assertEqual(out, "- [insight] 最终合并")
        self.assertTrue(degraded)
        self.assertEqual(mock_llm.call_count, 4)

    @patch("kb_harvest_chat._llm_call")
    def test_all_failures_circuit_break(self, mock_llm):
        """整轮 LLM 全失败 → 轮级熔断机械合并，不再进下一轮（防系统性故障下
        无意义重试拖长 cron）"""
        mock_llm.return_value = None
        out, degraded = harvest._reduce_hierarchical(self._segs(9), "20260604")
        self.assertTrue(degraded)
        # r1: b1 2 calls + b2 2 calls = 4，熔断后零 r2 调用
        self.assertEqual(mock_llm.call_count, 4)
        # 零数据丢失：9 段独特内容全部存活在机械合并产物里
        for i in range(1, 10):
            self.assertIn(f"独特内容{i}", out)

    @patch("kb_harvest_chat._llm_call")
    def test_four_segments_single_batch(self, mock_llm):
        """恰好 REDUCE_BATCH_SIZE 段 → 单批单轮"""
        mock_llm.return_value = "合并"
        out, degraded = harvest._reduce_hierarchical(self._segs(4), "20260604")
        self.assertEqual(mock_llm.call_count, 1)
        self.assertFalse(degraded)


class TestExtractKeyPointsMetaV130(unittest.TestCase):
    """V37.9.130 extract_key_points meta 可观测性 + Map 失败语义修复"""

    def _turns(self, n=500):
        return [{"ts": f"10:{i % 60:02d}:00",
                 "user": f"问题 {'x' * 100}",
                 "assistant": f"回答 {'y' * 100}"} for i in range(n)]

    @patch("kb_harvest_chat._llm_call")
    def test_map_all_failed_returns_none_not_empty(self, mock_llm):
        """关键 MR-4 修复：Map 全失败 → None（error 路径），
        绝不返回'无关键内容'（原 bug 会 mark_processed 永久丢数据）"""
        mock_llm.return_value = None
        result, meta = harvest.extract_key_points(self._turns(), "20260604")
        self.assertIsNone(result)
        self.assertGreater(meta["map_failed"], 0)

    @patch("kb_harvest_chat._llm_call")
    def test_map_partial_failure_counted_and_continues(self, mock_llm):
        """Map 部分失败 → 计数可观测 + 其余段继续产出"""
        # 3 chunks: 第 1 段失败 (2 calls), 第 2/3 段成功, reduce 成功
        mock_llm.side_effect = [None, None,
                                "- [insight] 段2洞察", "- [insight] 段3洞察",
                                "- [insight] 合并结果"]
        result, meta = harvest.extract_key_points(self._turns(), "20260604")
        self.assertEqual(meta["map_failed"], 1)
        self.assertIn("合并结果", result)

    @patch("kb_harvest_chat._llm_call")
    def test_reduce_degraded_propagates(self, mock_llm):
        """Reduce 全失败 → 机械合并产物返回 + reduce_degraded=True 透传"""
        def side_effect(prompt, **kwargs):
            if "分段提取" in prompt:
                return None  # 所有 reduce 调用失败
            return "- [insight] map产出"
        mock_llm.side_effect = side_effect
        result, meta = harvest.extract_key_points(self._turns(), "20260604")
        self.assertTrue(meta["reduce_degraded"])
        self.assertIn("map产出", result)  # 机械合并保住了 Map 产物

    @patch("kb_harvest_chat._llm_call")
    def test_returns_tuple_contract(self, mock_llm):
        """返回值必须是 (text, meta) 二元组，meta 含 4 个固定键"""
        mock_llm.return_value = "- [insight] 洞察"
        out = harvest.extract_key_points(
            [{"ts": "10:00", "user": "a", "assistant": "b"}], "20260604")
        self.assertIsInstance(out, tuple)
        self.assertEqual(len(out), 2)
        for key in ("chunks", "mode", "map_failed", "reduce_degraded"):
            self.assertIn(key, out[1])


class TestProcessDateDegradedV130(unittest.TestCase):
    """V37.9.130 process_date 降级标记 + 失败重试路径"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_chat_dir = harvest.CHAT_LOG_DIR
        self.orig_processed_dir = harvest.PROCESSED_MARKER_DIR
        harvest.CHAT_LOG_DIR = self.tmpdir
        harvest.PROCESSED_MARKER_DIR = os.path.join(self.tmpdir, ".processed")
        log_file = os.path.join(self.tmpdir, "20260604.jsonl")
        with open(log_file, "w") as f:
            f.write(json.dumps({"ts": "10:00", "user": "测试问题",
                                "assistant": "测试回答"}) + "\n")

    def tearDown(self):
        harvest.CHAT_LOG_DIR = self.orig_chat_dir
        harvest.PROCESSED_MARKER_DIR = self.orig_processed_dir
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("kb_harvest_chat.write_to_kb")
    @patch("kb_harvest_chat.extract_key_points")
    def test_degraded_marks_kb_content(self, mock_extract, mock_write):
        """reduce_degraded → KB 内容含 [REDUCE_DEGRADED] 标记 + 仍 ok + 标记已处理"""
        mock_extract.return_value = ("- [insight] 内容", {
            "chunks": 9, "mode": "mapreduce",
            "map_failed": 0, "reduce_degraded": True})
        mock_write.return_value = True
        status, meta = harvest.process_date("20260604")
        self.assertEqual(status, "ok")
        written_content = mock_write.call_args[0][0]
        self.assertIn("[REDUCE_DEGRADED", written_content)
        self.assertTrue(harvest.is_processed("20260604"))

    @patch("kb_harvest_chat.write_to_kb")
    @patch("kb_harvest_chat.extract_key_points")
    def test_normal_ok_no_marker(self, mock_extract, mock_write):
        """正常路径无降级标记"""
        mock_extract.return_value = ("- [insight] 内容", {
            "chunks": 2, "mode": "mapreduce",
            "map_failed": 0, "reduce_degraded": False})
        mock_write.return_value = True
        status, meta = harvest.process_date("20260604")
        self.assertEqual(status, "ok")
        written_content = mock_write.call_args[0][0]
        self.assertNotIn("[REDUCE_DEGRADED", written_content)

    @patch("kb_harvest_chat.extract_key_points")
    def test_extraction_failure_not_marked_processed(self, mock_extract):
        """提炼失败 → error + 不标记已处理（--days 3 下次 cron 自动重试的前提）"""
        mock_extract.return_value = (None, {
            "chunks": 9, "mode": "mapreduce",
            "map_failed": 9, "reduce_degraded": False})
        status, meta = harvest.process_date("20260604")
        self.assertEqual(status, "error")
        self.assertFalse(harvest.is_processed("20260604"))


class TestV379130SourceGuards(unittest.TestCase):
    """V37.9.130 源码级守卫（常量锁定 + 契约防回归）"""

    @classmethod
    def setUpClass(cls):
        with open("kb_harvest_chat.py", "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_constants_locked(self):
        """设计锁定常量：REDUCE_TIMEOUT=300 / REDUCE_BATCH_SIZE=4 / LLM_RETRY=1"""
        self.assertEqual(harvest.REDUCE_TIMEOUT, 300)
        self.assertEqual(harvest.REDUCE_BATCH_SIZE, 4)
        self.assertEqual(harvest.LLM_RETRY, 1)

    def test_v37_9_130_marker_present(self):
        self.assertIn("V37.9.130", self.src)

    def test_days_default_is_3(self):
        """--days 默认 3（失败日自动补提炼）。退回 default=1 = 失败日永不重试回归"""
        self.assertIn('"--days", type=int, default=3', self.src)

    def test_last_run_status_enum_unchanged(self):
        """watchdog 契约（V37.9.72 教训）：status 枚举只有 ok|error|empty，
        禁止引入 ok_degraded 之类新状态值"""
        self.assertNotIn('"ok_degraded"', self.src)
        self.assertNotIn("'ok_degraded'", self.src)
        # overall 三态赋值结构仍在
        self.assertIn('overall = "ok"', self.src)
        self.assertIn('overall = "error"', self.src)
        self.assertIn('overall = "empty"', self.src)

    def test_last_run_has_observability_fields(self):
        """last_run 必须含 degraded + map_failed 独立新字段"""
        self.assertIn('"degraded"', self.src)
        self.assertIn('"map_failed"', self.src)

    def test_reduce_uses_reduce_timeout_not_120(self):
        """Reduce 调用必须用 REDUCE_TIMEOUT 常量（防回退 timeout=120 硬编码）"""
        self.assertIn("timeout=REDUCE_TIMEOUT", self.src)

    def test_map_all_failed_distinguished_from_no_content(self):
        """Map 全失败与'无关键内容'语义区分的源码守卫"""
        self.assertIn('if meta["map_failed"]:', self.src)

    def test_old_single_shot_reduce_removed(self):
        """旧单次大请求 Reduce 函数已退役（日落法：替换而非堆叠）"""
        self.assertNotIn("def _reduce_key_points(", self.src)


if __name__ == "__main__":
    unittest.main()

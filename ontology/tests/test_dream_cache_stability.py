#!/usr/bin/env python3
"""
test_dream_cache_stability.py — regression test for the Dream Map budget
overflow + cache key drift bug (2026-04-11).

Background
----------
On 2026-04-11 Dream failed three ways in a row:

  1. Notes total (286) × per-batch latency (~76s) ÷ notes-per-batch (4.4) =
     ~82 min of Notes Map work, which overran the hardcoded
     DREAM_TIMEOUT_SEC=3600 budget. Both 00:40 --map-notes AND 03:00 full run
     timed out at the same spot; Reduce was never run.

  2. The 03:00 full run re-ran the entire Phase 1a/1b loop even though
     00:00 --map-sources and 00:40 --map-notes had already warmed the cache.
     The "Map-Reduce split schedule" was documented but NOT enforced in code.
     → Fix A: Reduce path now scans $MAP_DIR directly and sets
       SKIP_MAP_LOOPS=true, guarding both Phase 1a and Phase 1b.

  3. Notes cache key was md5(batch 拼接文本), which is sensitive to:
        - SORTED_NOTES order (derived from mtime — touched by mm_index)
        - batch boundaries (derived from order)
        - accumulated payload (derived from boundaries)
     Any mtime flip → all cache files invalidated. 00:40 wrote 47 new files;
     03:00 computed 47 different keys and saw 0 hits.
     → Fix C: per-note cache, key = md5(content) front 12 chars. Stable.

  Plus Fix B: batch size 15/12000B → 30/24000B to halve LLM call count
  (first-day bootstrap drops from ~60 min to ~30 min).

This file is the grep-level regression guard that locks those fixes in place.
If someone reverts any of the three fixes, the kb_dream.sh source will no
longer contain the expected patterns and these tests will fail.

See: ontology/docs/cases/dream_map_budget_overflow_case.md
"""

import os
import re
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
_KB_DREAM_SH = os.path.join(_PROJECT_ROOT, "kb_dream.sh")


def _read_kb_dream():
    with open(_KB_DREAM_SH) as f:
        return f.read()


class TestFixA_CacheOnlyFastPath(unittest.TestCase):
    """Fix A: Reduce default path must NOT re-run Phase 1a/1b LLM loops.
    It must read from $MAP_DIR and set SKIP_MAP_LOOPS=true."""

    def test_skip_map_loops_flag_exists(self):
        """A SKIP_MAP_LOOPS=true branch must exist somewhere in the Reduce path."""
        src = _read_kb_dream()
        self.assertIn(
            "SKIP_MAP_LOOPS=true",
            src,
            "Reduce cache-only fast path regressed: no SKIP_MAP_LOOPS=true found. "
            "Dream 03:00 full run will re-burn the Map budget.",
        )

    def test_skip_map_loops_initialized_to_false(self):
        """The flag must default to false so Map-only modes still run the loop."""
        src = _read_kb_dream()
        self.assertIn(
            "SKIP_MAP_LOOPS=false",
            src,
            "SKIP_MAP_LOOPS default value missing — Map-only schedules may "
            "accidentally skip their own work.",
        )

    def test_phase_1a_guarded_by_skip_map_loops(self):
        """Phase 1a (Map Sources LLM loop) must be gated on SKIP_MAP_LOOPS=false."""
        src = _read_kb_dream()
        # The Phase 1a main if-condition is the one that also checks SRC_COUNT > 0
        m = re.search(
            r'if \[ "\$SKIP_MAP_LOOPS" = false \][^\n]*\$SRC_COUNT',
            src,
        )
        self.assertIsNotNone(
            m,
            "Phase 1a is not guarded by SKIP_MAP_LOOPS — Reduce path will "
            "re-run Sources Map LLM loop even when cache is ready.",
        )

    def test_phase_1b_guarded_by_skip_map_loops(self):
        """Phase 1b (Map Notes LLM loop) must also be gated on SKIP_MAP_LOOPS=false."""
        src = _read_kb_dream()
        m = re.search(
            r'if \[ "\$SKIP_MAP_LOOPS" = false \][^\n]*\$ALL_NOTES',
            src,
        )
        self.assertIsNotNone(
            m,
            "Phase 1b is not guarded by SKIP_MAP_LOOPS — this was the exact "
            "spot that burned 60 min of budget on 2026-04-11.",
        )

    def test_fast_mode_elif_also_guarded(self):
        """The fast-mode elif (which fills NOTES_MATERIAL from raw notes) must
        also respect SKIP_MAP_LOOPS, otherwise cache-only path can be clobbered."""
        src = _read_kb_dream()
        # The elif is the "Fast 模式：直接采样 notes 原文" branch
        m = re.search(
            r'elif \[ "\$SKIP_MAP_LOOPS" = false \][^\n]*ALL_NOTES',
            src,
        )
        self.assertIsNotNone(
            m,
            "Fast-mode elif is not guarded by SKIP_MAP_LOOPS — if we come from "
            "cache-only path, this elif will overwrite NOTES_MATERIAL.",
        )


class TestFixB_BatchSizeDoubled(unittest.TestCase):
    """Fix B: Notes batch threshold was 15 notes / 12000 bytes (→ ~47 batches for
    286 notes); raised to 30 notes / 24000 bytes to halve LLM call count."""

    def test_new_thresholds_present(self):
        src = _read_kb_dream()
        # New pattern: PENDING_COUNT >= 30 OR PENDING_SIZE > 24000
        self.assertIn(
            "PENDING_COUNT",
            src,
            "New pending-batch accumulator (PENDING_COUNT) not found — "
            "Fix B regressed.",
        )
        self.assertRegex(
            src,
            r"PENDING_COUNT[^\n]*-ge 30",
            "Notes batch threshold is not 30 notes — Fix B regressed.",
        )
        self.assertRegex(
            src,
            r"PENDING_SIZE[^\n]*-gt 24000",
            "Notes batch byte-threshold is not 24000B — Fix B regressed.",
        )

    def test_old_thresholds_removed(self):
        src = _read_kb_dream()
        # Old pattern: BATCH_COUNT >= 15 OR BATCH_SIZE > 12000
        self.assertNotRegex(
            src,
            r"BATCH_COUNT[^\n]*-ge 15",
            "Old 15-note batch threshold still present — Fix B not fully "
            "applied, will still generate ~47 batches.",
        )
        self.assertNotRegex(
            src,
            r"BATCH_SIZE[^\n]*-gt 12000",
            "Old 12000B batch byte-threshold still present — Fix B not fully "
            "applied.",
        )


class TestFixC_PerNoteCacheKey(unittest.TestCase):
    """Fix C: cache key was md5(batch-concatenated-text), brittle to mtime/
    sort-order drift. Replaced with per-note md5(content)."""

    def test_content_hash_per_note(self):
        src = _read_kb_dream()
        # New pattern: content_hash=$(printf '%s' "$content" | md5sum ...)
        self.assertRegex(
            src,
            r"content_hash=.*printf.*\$content.*md5sum",
            "Per-note content hash not found — Fix C regressed, cache will "
            "drift on every mtime change.",
        )

    def test_cache_filename_uses_content_hash(self):
        src = _read_kb_dream()
        # New pattern: $MAP_DIR/${DAY}_note_${content_hash}.txt
        self.assertRegex(
            src,
            r"\$\{DAY\}_note_\$\{content_hash\}",
            "Cache filename format is not ${DAY}_note_${content_hash}.txt — "
            "Fix C regressed.",
        )

    def test_old_batch_hash_removed(self):
        src = _read_kb_dream()
        # Old pattern: batch_hash=$(echo "$BATCH" | md5sum ...)
        self.assertNotRegex(
            src,
            r"batch_hash=.*echo.*\$BATCH.*md5sum",
            "Old batch-level cache key still present — Fix C not fully "
            "applied, cache will drift whenever SORTED_NOTES order shifts.",
        )

    def test_md5_cross_platform_fallback(self):
        """Mac Mini uses md5 -q; Linux dev uses md5sum. Both paths must exist."""
        src = _read_kb_dream()
        self.assertIn(
            "md5sum 2>/dev/null",
            src,
            "Missing md5sum (Linux/dev) branch for content hashing.",
        )
        self.assertIn(
            "md5 -q 2>/dev/null",
            src,
            "Missing md5 -q (macOS) fallback for content hashing — Mac Mini "
            "production will silently produce empty hashes.",
        )

    def test_signal_dedup_on_read(self):
        """When reading the per-note cache, multiple notes from the same LLM
        batch share identical signals. They must be deduped by signal hash so
        NOTES_SIGNALS doesn't contain N copies of the same signal block."""
        src = _read_kb_dream()
        self.assertIn(
            "seen_note_signal_hashes",
            src,
            "Signal dedup set missing — cache-only path will emit N copies of "
            "identical signal blocks (one per note in a shared batch).",
        )


class TestV37_4_2_CacheReadStructureAndRetryVariation(unittest.TestCase):
    """V37.4.2 → V37.8.3: Cache-only read path must emit structured, numbered
    headers. Retry logic replaced by chunked generation in V37.8.3.

    Background: 2026-04-11 14:01 run produced retry 1 = 876 chars and retry 2
    = *exactly* 876 chars (different temps, same output) — smoking gun for
    server-side prompt caching. Combined with bland repeated headers, Qwen3
    converged on a 'terse summary' mode for this material."""

    def test_uniq_note_sig_blocks_array(self):
        """Cache-only read path must accumulate into a bash array for
        structured emission at the end, not append bland headers in-loop."""
        src = _read_kb_dream()
        self.assertIn(
            "UNIQ_NOTE_SIG_BLOCKS=()",
            src,
            "UNIQ_NOTE_SIG_BLOCKS array missing — cache read path still "
            "appends bland '## 用户笔记（缓存）' × N copies.",
        )

    def test_structured_header_has_coverage_stats(self):
        """The emitted header must surface 'covered N notes, K unique clusters'
        so Reduce LLM sees density signal, not just a flat list."""
        src = _read_kb_dream()
        self.assertIn(
            "用户笔记信号总览",
            src,
            "Structured overview header missing — Reduce LLM will read the "
            "material as homogeneous and produce a terse summary.",
        )
        self.assertRegex(
            src,
            r"覆盖\s*\$NOTES_MAP_COUNT.*笔记.*\$UNIQ_BLOCK_COUNT",
            "Coverage stats line must expose both NOTES_MAP_COUNT and "
            "UNIQ_BLOCK_COUNT so LLM sees quantity.",
        )

    def test_numbered_cluster_headers(self):
        """Each block must get a '信号簇 N / total' header — gives the LLM
        discrete chunks to synthesize over."""
        src = _read_kb_dream()
        self.assertRegex(
            src,
            r"笔记信号簇\s*\$idx\s*/\s*\$UNIQ_BLOCK_COUNT",
            "Numbered cluster headers missing — LLM won't see distinct "
            "chunks, will produce single-finding summary.",
        )

    def test_bland_header_gone(self):
        """The old bland '## 用户笔记（缓存）' in-loop append must be gone."""
        src = _read_kb_dream()
        self.assertNotIn(
            "## 用户笔记（缓存）",
            src,
            "Old bland header still present — V37.4.2 regressed, material "
            "will look like 22 duplicate sections to Qwen3.",
        )

    def test_chunked_generation_replaces_retry_loop(self):
        """V37.8.3: Chunked generation replaced the single-shot retry loop.
        The code must have CHUNK1/CHUNK2/CHUNK3 calls."""
        src = _read_kb_dream()
        self.assertIn("CHUNK1_RESULT", src,
                       "Chunked generation not found — old retry loop still in use")
        self.assertIn("CHUNK2_RESULT", src)
        self.assertIn("CHUNK3_RESULT", src)


class TestDynamicTimeoutBudget(unittest.TestCase):
    """Map-only modes need a 90-min budget (full KB bootstrap); Reduce keeps
    60 min (cache read + 1 LLM call). Dynamic assignment by mode."""

    def test_map_only_budget_is_5400(self):
        src = _read_kb_dream()
        # Pattern: elif [ "$MAP_ONLY" = true ]; then DREAM_TIMEOUT_SEC=5400
        self.assertRegex(
            src,
            r'"\$MAP_ONLY" = true[^\n]*\n\s*DREAM_TIMEOUT_SEC=5400',
            "Map-only mode budget is not 5400s (90 min) — bootstrap will "
            "time out on full KB scans.",
        )

    def test_full_run_budget_is_3600(self):
        src = _read_kb_dream()
        self.assertIn(
            "DREAM_TIMEOUT_SEC=3600",
            src,
            "Reduce full-run fallback budget (3600s / 60 min) missing.",
        )

    def test_override_env_var_supported(self):
        src = _read_kb_dream()
        self.assertIn(
            "DREAM_TIMEOUT_SEC_OVERRIDE",
            src,
            "Debug override env var missing — dev environments can't "
            "short-circuit the budget.",
        )


class TestFlushPendingBatchHelper(unittest.TestCase):
    """The flush_pending_batch helper is the flush path for Fix B+C. It must
    exist as a bash function and write signals to each participating note's
    independent cache file."""

    def test_flush_helper_defined(self):
        src = _read_kb_dream()
        self.assertIn(
            "flush_pending_batch()",
            src,
            "flush_pending_batch helper function missing — dynamic batch "
            "accumulation won't work.",
        )

    def test_flush_writes_to_all_participating_notes(self):
        """The flush body must iterate PENDING_CACHE_FILES and write $signals
        into each of them. Rather than fighting regex engines across bash
        multi-line idioms, we just verify the flush function body contains
        all four required tokens within a reasonable window."""
        src = _read_kb_dream()
        flush_start = src.find("flush_pending_batch()")
        self.assertGreater(flush_start, 0, "flush_pending_batch() not defined")
        # Scan the function body (first ~3000 chars after the def — well above
        # the current size, but bounded)
        flush_body = src[flush_start : flush_start + 3000]

        # Required tokens in the flush body that together prove "write signals
        # to each participating note's cache file"
        required = [
            "PENDING_CACHE_FILES",  # the list of cache files to write to
            "cfile",                # loop variable over that list
            'echo "$signals"',      # the write itself
            "$cfile",               # the write target
        ]
        missing = [tok for tok in required if tok not in flush_body]
        self.assertFalse(
            missing,
            "flush_pending_batch is missing tokens that prove per-note cache "
            f"write-back: {missing}. Fix C is incomplete.",
        )

    def test_flush_has_pre_llm_deadline_check(self):
        """flush_pending_batch must check the deadline BEFORE calling llm_call,
        to avoid burning quota on a doomed batch."""
        src = _read_kb_dream()
        # The flush function must contain check_deadline before llm_call
        flush_start = src.find("flush_pending_batch()")
        self.assertGreater(flush_start, 0)
        flush_body = src[flush_start : flush_start + 2000]
        self.assertIn("check_deadline", flush_body)
        self.assertIn("llm_call", flush_body)
        check_pos = flush_body.find("check_deadline")
        llm_pos = flush_body.find("llm_call")
        self.assertLess(
            check_pos,
            llm_pos,
            "check_deadline must come BEFORE llm_call in flush_pending_batch "
            "— otherwise we burn LLM budget on a doomed flush.",
        )


class TestReduceChunkedGeneration(unittest.TestCase):
    """V37.8.3: Chunked generation replaces single-shot retry loop.
    Root cause: remote GPU server output token limit (~300-500 tokens) caps
    single-call output at ~900 chars regardless of prompt engineering.
    Fix: split 6 sections into 3 LLM calls, each producing 2 sections,
    concatenate results. Each call stays within server token limit."""

    def test_min_acceptable_chars_floor(self):
        """Chunked generation must have a floor below which we give up."""
        src = _read_kb_dream()
        self.assertIn(
            "MIN_ACCEPTABLE_CHARS=1500",
            src,
            "MIN_ACCEPTABLE_CHARS floor missing — no minimum bar for "
            "chunked output, could emit near-empty Dream files.",
        )

    def test_three_chunks_defined(self):
        """Must have exactly 3 chunk calls covering all 6 sections."""
        src = _read_kb_dream()
        for i in range(1, 4):
            self.assertIn(
                f"CHUNK{i}_RESULT",
                src,
                f"CHUNK{i}_RESULT missing — not all 3 chunks defined.",
            )
            self.assertIn(
                f"CHUNK{i}_CHARS",
                src,
                f"CHUNK{i}_CHARS missing — can't verify chunk {i} output size.",
            )

    def test_chunk1_covers_theme_and_discovery(self):
        """Chunk 1 must select theme and write discovery + connections."""
        src = _read_kb_dream()
        self.assertIn("发现过程", src)
        self.assertIn("隐藏关联", src)
        self.assertIn("DREAM_THEME", src,
                       "DREAM_THEME extraction missing — chunks 2/3 won't "
                       "know what theme to continue with.")

    def test_chunk2_covers_trends_and_signals(self):
        """Chunk 2 must cover trend projection and overlooked signals."""
        src = _read_kb_dream()
        self.assertIn("趋势推演", src)
        self.assertIn("被忽视的信号", src)

    def test_chunk3_covers_actions_and_quality(self):
        """Chunk 3 must cover action recommendations and data quality."""
        src = _read_kb_dream()
        self.assertIn("行动建议", src)
        self.assertIn("数据质量备注", src)

    def test_chunks_use_system_messages(self):
        """Each chunk call must use system message for meta-instructions."""
        src = _read_kb_dream()
        for i in range(1, 4):
            self.assertIn(
                f"CHUNK{i}_SYSTEM",
                src,
                f"CHUNK{i}_SYSTEM missing — chunk {i} has no system message, "
                "model won't get focused instructions.",
            )

    def test_chunks_use_truncated_material(self):
        """Chunked mode must use truncated material (30KB), not full 80KB."""
        src = _read_kb_dream()
        self.assertIn(
            "CHUNKED_MATERIAL",
            src,
            "CHUNKED_MATERIAL missing — chunks still using full 80KB material.",
        )
        self.assertRegex(
            src,
            r'utf8_truncate\s+30000',
            "Material must be truncated to 30000 for chunked mode.",
        )

    def test_concatenation_counts_successful_chunks(self):
        """Must track how many chunks succeeded and require at least 2."""
        src = _read_kb_dream()
        self.assertIn(
            "SUCCESSFUL_CHUNKS",
            src,
            "SUCCESSFUL_CHUNKS counter missing — can't verify minimum chunks.",
        )

    def test_failure_requires_fewer_than_2_chunks(self):
        """Dream should only fail if fewer than 2 chunks succeeded."""
        src = _read_kb_dream()
        self.assertRegex(
            src,
            r'SUCCESSFUL_CHUNKS.*-lt.*2',
            "Failure check must require at least 2 successful chunks.",
        )


class TestV37_8_3_ChunkedGenerationStructure(unittest.TestCase):
    """V37.8.3: Chunked generation structure tests.
    Each chunk must pass system message to llm_call and log its output size."""

    def test_each_chunk_logged(self):
        """Each chunk call must log its output for operator visibility."""
        src = _read_kb_dream()
        self.assertIn("Chunk 1/3:", src)
        self.assertIn("Chunk 2/3:", src)
        self.assertIn("Chunk 3/3:", src)

    def test_chunk2_receives_theme_from_chunk1(self):
        """Chunk 2 must reference DREAM_THEME extracted from chunk 1."""
        src = _read_kb_dream()
        # CHUNK2_PROMPT must contain DREAM_THEME
        self.assertRegex(
            src,
            r'CHUNK2_PROMPT=.*DREAM_THEME',
            "Chunk 2 prompt doesn't reference DREAM_THEME — sections will "
            "be disconnected, not a coherent analysis.",
        )

    def test_chunk3_receives_prior_context(self):
        """Chunk 3 must receive findings from chunks 1 and 2."""
        src = _read_kb_dream()
        # CHUNK3_PROMPT is a multiline heredoc, so we check that both
        # CHUNK1_RESULT and CHUNK2_RESULT appear between CHUNK3_PROMPT= and
        # the next CHUNK3_ variable (the prompt spans multiple lines).
        chunk3_start = src.find('CHUNK3_PROMPT=')
        self.assertNotEqual(chunk3_start, -1, "CHUNK3_PROMPT not found")
        chunk3_block = src[chunk3_start:chunk3_start + 2000]
        self.assertIn(
            'CHUNK1_RESULT',
            chunk3_block,
            "Chunk 3 doesn't receive CHUNK1_RESULT — action items "
            "won't be grounded in the analysis.",
        )
        self.assertIn(
            'CHUNK2_RESULT',
            chunk3_block,
            "Chunk 3 doesn't receive CHUNK2_RESULT — action items "
            "won't reflect trend analysis.",
        )

    def test_chunked_material_is_30k(self):
        """Chunked mode uses 30KB material (not 80KB) for faster processing."""
        src = _read_kb_dream()
        m = re.search(r'CHUNKED_MATERIAL=.*utf8_truncate\s+(\d+)', src)
        self.assertIsNotNone(m, "CHUNKED_MATERIAL truncation not found")
        self.assertEqual(int(m.group(1)), 30000,
                         "Chunked material should be truncated to 30000")


class TestV37_8_3_SystemUserMessageSplit(unittest.TestCase):
    """V37.8.3: System+user message split + chunked generation.
    Root cause: remote GPU server output token limit (~300-500 tokens) caps
    each LLM call to ~900 chars regardless of prompt size/structure.
    Fix: llm_call() accepts 5th system_msg parameter, and Reduce uses
    chunked generation (3 focused LLM calls × 2 sections each) to stay
    within per-call token limits while producing full 6-section reports."""

    def test_reduce_system_variable_defined(self):
        """REDUCE_SYSTEM must be defined to carry meta-instructions."""
        src = _read_kb_dream()
        self.assertIn(
            'REDUCE_SYSTEM=',
            src,
            "REDUCE_SYSTEM variable missing — system message not implemented.",
        )

    def test_system_contains_length_mandate(self):
        """System message must contain the hard length requirement so it's
        in the model's highest-attention position."""
        src = _read_kb_dream()
        # Find the REDUCE_SYSTEM definition and check for length keywords
        m = re.search(r'REDUCE_SYSTEM="(.*?)"', src, re.DOTALL)
        self.assertIsNotNone(m, "Cannot extract REDUCE_SYSTEM content")
        system_content = m.group(1)
        self.assertIn("2000", system_content,
                       "System message must specify minimum output length")
        self.assertIn("1500", system_content,
                       "System message must warn about discard threshold")

    def test_system_contains_six_chapters(self):
        """System message must enumerate the 6 required chapters."""
        src = _read_kb_dream()
        m = re.search(r'REDUCE_SYSTEM="(.*?)"', src, re.DOTALL)
        self.assertIsNotNone(m)
        system_content = m.group(1)
        for keyword in ["发现过程", "隐藏关联", "趋势推演", "被忽视的信号", "行动建议", "数据质量备注"]:
            self.assertIn(keyword, system_content,
                          f"System message missing chapter '{keyword}'")

    def test_chunked_llm_calls_receive_system_message(self):
        """Each chunked llm_call must pass its own system message as 5th arg."""
        src = _read_kb_dream()
        # V37.8.3 chunked generation: 3 llm_call each with CHUNKn_SYSTEM
        for i in range(1, 4):
            self.assertRegex(
                src,
                rf'llm_call\s+"\$CHUNK{i}_PROMPT"\s+\d+\s+[\d.]+\s+\d+\s+"\$CHUNK{i}_SYSTEM"',
                f"Chunk {i} llm_call does not pass CHUNK{i}_SYSTEM — "
                "model will use single user message without focused instructions.",
            )

    def test_llm_call_function_accepts_system_msg_param(self):
        """llm_call() must accept a 5th parameter for system message."""
        src = _read_kb_dream()
        self.assertRegex(
            src,
            r'local system_msg="\$\{5:-\}"',
            "llm_call function does not accept system_msg as 5th parameter.",
        )

    def test_llm_call_passes_system_to_python(self):
        """llm_call must pass system_msg to the Python JSON builder via env."""
        src = _read_kb_dream()
        self.assertIn(
            '_LLM_SYSTEM_MSG="$system_msg"',
            src,
            "llm_call does not pass system_msg to Python subprocess — "
            "system message is dead code.",
        )

    def test_each_chunk_has_distinct_system_message(self):
        """Each chunk must have its own CHUNK{n}_SYSTEM with focused section
        instructions, not a shared single system message."""
        src = _read_kb_dream()
        for i in range(1, 4):
            self.assertIn(
                f'CHUNK{i}_SYSTEM=',
                src,
                f"CHUNK{i}_SYSTEM not defined — chunk {i} lacks focused "
                "section-specific system instructions.",
            )

    def test_user_prompt_no_longer_contains_writing_style(self):
        """After V37.8.3 split, the main REDUCE_PROMPT (user message) should
        NOT contain writing style instructions — those moved to REDUCE_SYSTEM."""
        src = _read_kb_dream()
        # Find the main REDUCE_PROMPT (not the retry one, not REDUCE_SYSTEM)
        # Look for REDUCE_PROMPT=" that comes right after REDUCE_SYSTEM
        m = re.search(r'REDUCE_PROMPT="(.*?)(?=\n\nPROMPT_BYTES=)', src, re.DOTALL)
        if m:
            prompt_content = m.group(1)
            self.assertNotIn(
                "像写给技术决策者",
                prompt_content,
                "Writing style instructions should be in REDUCE_SYSTEM, not "
                "REDUCE_PROMPT (user message) — defeats the split purpose.",
            )


class TestSourcesCacheFastPathUnchanged(unittest.TestCase):
    """Sources cache key (${name}_${file_size}_${prompt_hash}) was already
    stable and must remain so — don't accidentally port it to md5(content)."""

    def test_sources_cache_key_is_name_size_prompthash(self):
        src = _read_kb_dream()
        self.assertRegex(
            src,
            r'cache_key="\$\{name\}_\$\{file_size\}_\$\{prompt_hash\}"',
            "Sources cache key format changed — this was already stable and "
            "should not have been touched.",
        )

    def test_sources_cache_hit_logged(self):
        src = _read_kb_dream()
        self.assertIn(
            "Sources 缓存:",
            src,
            "Sources cache hit log line missing — operator can't verify the "
            "fast path worked.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

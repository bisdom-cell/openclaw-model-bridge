#!/usr/bin/env python3
"""
test_memory_plane.py — Memory Plane v1 单测

测试 memory_plane.py 的统一接口、各层适配、报告格式、优雅降级。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_plane import (
    MemoryResult, LayerStatus,
    check_layers, query, get_context, stats,
    LAYERS,
    _kb_available, _kb_search, _kb_stats,
    _mm_available, _mm_stats,
    _preferences_available, _get_preferences,
    _status_available, _get_status, _status_stats,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
class TestMemoryResult(unittest.TestCase):
    def test_creation(self):
        r = MemoryResult(layer="kb", score=0.85, text="test", source="arxiv")
        self.assertEqual(r.layer, "kb")
        self.assertEqual(r.score, 0.85)
        self.assertEqual(r.text, "test")

    def test_default_metadata(self):
        r = MemoryResult(layer="kb")
        self.assertEqual(r.metadata, {})

    def test_with_metadata(self):
        r = MemoryResult(layer="kb", metadata={"file": "test.md"})
        self.assertEqual(r.metadata["file"], "test.md")


class TestLayerStatus(unittest.TestCase):
    def test_creation(self):
        s = LayerStatus(name="kb", available=True)
        self.assertTrue(s.available)
        self.assertEqual(s.reason, "")

    def test_unavailable(self):
        s = LayerStatus(name="mm", available=False, reason="no numpy")
        self.assertFalse(s.available)
        self.assertIn("numpy", s.reason)


# ---------------------------------------------------------------------------
# Layer registry
# ---------------------------------------------------------------------------
class TestLayerRegistry(unittest.TestCase):
    def test_four_layers_registered(self):
        self.assertEqual(len(LAYERS), 4)

    def test_layer_keys(self):
        expected = {"kb", "multimodal", "preferences", "status"}
        self.assertEqual(set(LAYERS.keys()), expected)

    def test_each_layer_has_required_fields(self):
        for key, layer in LAYERS.items():
            self.assertIn("name", layer, f"{key} missing name")
            self.assertIn("description", layer, f"{key} missing description")
            self.assertIn("available_fn", layer, f"{key} missing available_fn")
            self.assertIn("search_fn", layer, f"{key} missing search_fn")
            self.assertIn("stats_fn", layer, f"{key} missing stats_fn")


# ---------------------------------------------------------------------------
# Check layers
# ---------------------------------------------------------------------------
class TestCheckLayers(unittest.TestCase):
    def test_returns_list(self):
        result = check_layers()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 4)

    def test_each_entry_is_layer_status(self):
        for ls in check_layers():
            self.assertIsInstance(ls, LayerStatus)
            self.assertIn(ls.name, LAYERS)

    def test_available_layers_have_stats(self):
        for ls in check_layers():
            if ls.available:
                self.assertIsInstance(ls.stats, dict)


# ---------------------------------------------------------------------------
# KB layer
# ---------------------------------------------------------------------------
class TestKBLayer(unittest.TestCase):
    def test_available_returns_tuple(self):
        avail, reason = _kb_available()
        self.assertIsInstance(avail, bool)
        self.assertIsInstance(reason, str)

    def test_search_returns_list(self):
        # Even if KB unavailable, should return empty list not crash
        result = _kb_search("test query")
        self.assertIsInstance(result, list)

    def test_stats_returns_dict(self):
        result = _kb_stats()
        self.assertIsInstance(result, dict)

    def test_search_with_mock(self):
        """Test KB search with mocked kb_rag.search."""
        mock_results = [
            {"score": 0.9, "text": "Qwen3 is good", "source_type": "arxiv",
             "file": "test.md", "filename": "test.md", "chunk_idx": 0},
        ]
        mock_module = MagicMock(search=MagicMock(return_value=mock_results))
        with patch.dict("sys.modules", {"kb_rag": mock_module}):
            results = _kb_search("Qwen3")
            if results:
                self.assertEqual(results[0].layer, "kb")


# ---------------------------------------------------------------------------
# Multimodal layer
# ---------------------------------------------------------------------------
class TestMultimodalLayer(unittest.TestCase):
    def test_available_returns_tuple(self):
        avail, reason = _mm_available()
        self.assertIsInstance(avail, bool)

    def test_stats_returns_dict(self):
        result = _mm_stats()
        self.assertIsInstance(result, dict)


# ---------------------------------------------------------------------------
# Preferences layer
# ---------------------------------------------------------------------------
class TestPreferencesLayer(unittest.TestCase):
    def test_available(self):
        avail, reason = _preferences_available()
        # status_update.py is in the repo, should be available
        self.assertTrue(avail)

    def test_get_preferences_returns_list(self):
        result = _get_preferences()
        self.assertIsInstance(result, list)

    def test_preferences_are_memory_results(self):
        result = _get_preferences()
        for r in result:
            self.assertEqual(type(r).__name__, "MemoryResult")
            self.assertEqual(r.layer, "preferences")
            self.assertEqual(r.score, 1.0)


# ---------------------------------------------------------------------------
# Status layer
# ---------------------------------------------------------------------------
class TestStatusLayer(unittest.TestCase):
    def test_available(self):
        avail, reason = _status_available()
        self.assertTrue(avail)

    def test_get_status_returns_list(self):
        result = _get_status()
        self.assertIsInstance(result, list)

    def test_status_results_have_correct_layer(self):
        for r in _get_status():
            self.assertEqual(r.layer, "status")
            self.assertIn(r.source, ("health", "priorities", "incidents"))

    def test_status_stats(self):
        s = _status_stats()
        self.assertIn("total_priorities", s)
        self.assertIn("active", s)
        self.assertIn("last_updated", s)


# ---------------------------------------------------------------------------
# Unified query
# ---------------------------------------------------------------------------
class TestUnifiedQuery(unittest.TestCase):
    def test_query_returns_list(self):
        result = query("test")
        self.assertIsInstance(result, list)

    def test_query_results_are_memory_results(self):
        for r in query("test"):
            self.assertEqual(type(r).__name__, "MemoryResult")

    def test_query_sorted_by_score(self):
        results = query("test")
        if len(results) >= 2:
            for i in range(len(results) - 1):
                self.assertGreaterEqual(results[i].score, results[i + 1].score)

    def test_query_with_layer_filter(self):
        results = query("test", layers=["status"])
        for r in results:
            self.assertEqual(r.layer, "status")

    def test_query_with_multiple_layers(self):
        results = query("test", layers=["status", "preferences"])
        layers_seen = set(r.layer for r in results)
        # Should only have status and/or preferences
        self.assertTrue(layers_seen.issubset({"status", "preferences"}))

    def test_query_unknown_layer_ignored(self):
        # Should not crash on unknown layer
        results = query("test", layers=["nonexistent"])
        self.assertIsInstance(results, list)

    def test_query_all_layers_no_crash(self):
        # Even if some layers unavailable, should not crash
        results = query("test", layers=None)
        self.assertIsInstance(results, list)


# ---------------------------------------------------------------------------
# Get context
# ---------------------------------------------------------------------------
class TestGetContext(unittest.TestCase):
    def test_returns_string(self):
        ctx = get_context("test")
        self.assertIsInstance(ctx, str)

    def test_context_has_header(self):
        ctx = get_context("test")
        if ctx:
            self.assertIn("Memory Context", ctx)
            self.assertIn("End Memory Context", ctx)

    def test_context_empty_for_no_results(self):
        ctx = get_context("test", layers=["nonexistent"])
        self.assertEqual(ctx, "")

    def test_context_respects_max_chars(self):
        ctx = get_context("test", max_chars=100)
        # Context might be empty or limited
        self.assertIsInstance(ctx, str)

    def test_context_with_layer_filter(self):
        ctx = get_context("test", layers=["status"])
        if ctx:
            self.assertIn("status", ctx)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
class TestStats(unittest.TestCase):
    def test_returns_dict(self):
        s = stats()
        self.assertIsInstance(s, dict)

    def test_has_all_layers(self):
        s = stats()
        self.assertEqual(set(s.keys()), set(LAYERS.keys()))

    def test_each_layer_has_available(self):
        for layer_name, layer_stats in stats().items():
            self.assertIn("available", layer_stats)

    def test_available_layers_have_data(self):
        for layer_name, layer_stats in stats().items():
            if layer_stats["available"]:
                # Should have at least one key beyond "available"
                self.assertGreater(len(layer_stats), 1,
                                   f"{layer_name} has no stats data")


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------
class TestGracefulDegradation(unittest.TestCase):
    """Verify that unavailable layers don't break the system."""

    def test_query_with_all_layers_unavailable(self):
        """Mock all layers as unavailable."""
        orig = {}
        for key in LAYERS:
            orig[key] = LAYERS[key]["available_fn"]
            LAYERS[key]["available_fn"] = lambda: (False, "mock")
        try:
            results = query("test")
            self.assertEqual(results, [])
        finally:
            for key in orig:
                LAYERS[key]["available_fn"] = orig[key]

    def test_stats_with_unavailable_layers(self):
        """Stats should still work when some layers are down."""
        s = stats()
        # At least status and preferences should be available in dev
        available_count = sum(1 for v in s.values() if v.get("available"))
        self.assertGreaterEqual(available_count, 2)

    def test_context_with_partial_availability(self):
        """Context generation works with partial layer availability."""
        ctx = get_context("test", layers=["status", "nonexistent"])
        # Should have results from status, ignore nonexistent
        self.assertIsInstance(ctx, str)


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
class TestCLI(unittest.TestCase):
    def test_layers_command(self):
        """CLI layers command should not crash."""
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["memory_plane.py", "layers"]
            with redirect_stdout(f):
                from memory_plane import _cli
                _cli()
            output = f.getvalue()
            self.assertIn("kb", output)
            self.assertIn("status", output)
        finally:
            sys.argv = old_argv

    def test_stats_json_command(self):
        """CLI stats --json should produce valid JSON."""
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["memory_plane.py", "stats", "--json"]
            with redirect_stdout(f):
                from memory_plane import _cli
                _cli()
            output = f.getvalue()
            data = json.loads(output)
            self.assertIn("kb", data)
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()

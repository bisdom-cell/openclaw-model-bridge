#!/usr/bin/env python3
"""test_kb_dedup.py — kb_dedup.py 单测"""
import unittest, tempfile, os, json, shutil

_tmpdir = tempfile.mkdtemp()
_kb_base = os.path.join(_tmpdir, "kb")
_notes_dir = os.path.join(_kb_base, "notes")
_sources_dir = os.path.join(_kb_base, "sources")
_index_file = os.path.join(_kb_base, "index.json")

import kb_dedup
kb_dedup.KB_BASE = _kb_base
kb_dedup.NOTES_DIR = _notes_dir
kb_dedup.SOURCES_DIR = _sources_dir
kb_dedup.INDEX_FILE = _index_file
kb_dedup.REPORT_JSON = os.path.join(_tmpdir, "kb_dedup.json")


def setup_kb():
    """Create fresh KB structure."""
    for d in (_notes_dir, _sources_dir):
        os.makedirs(d, exist_ok=True)


def write_note(filename, content, frontmatter="---\ndate: 20260324\ntags: [test]\n---\n"):
    """Write a note file."""
    with open(os.path.join(_notes_dir, filename), "w") as f:
        f.write(frontmatter + content)


def write_index(entries):
    """Write index.json."""
    with open(_index_file, "w") as f:
        json.dump({"entries": entries}, f)


def write_source(filename, content):
    """Write a source file."""
    with open(os.path.join(_sources_dir, filename), "w") as f:
        f.write(content)


class TestExactDedup(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()

    def test_no_duplicates(self):
        write_note("20260324100000.md", "# Unique note A")
        write_note("20260324110000.md", "# Unique note B")
        write_index([
            {"date": "20260324", "file": "notes/20260324100000.md", "summary": "Unique note A", "tags": ["test"]},
            {"date": "20260324", "file": "notes/20260324110000.md", "summary": "Unique note B", "tags": ["test"]},
        ])
        index = kb_dedup.load_index()
        exact, fuzzy = kb_dedup.find_duplicate_notes(index)
        self.assertEqual(len(exact), 0)

    def test_exact_summary_duplicate(self):
        write_note("20260324100000.md", "# Same content here")
        write_note("20260324110000.md", "# Same content here")
        write_index([
            {"date": "20260324", "file": "notes/20260324100000.md", "summary": "Same content here", "tags": ["test"]},
            {"date": "20260324", "file": "notes/20260324110000.md", "summary": "Same content here", "tags": ["test"]},
        ])
        index = kb_dedup.load_index()
        exact, _ = kb_dedup.find_duplicate_notes(index)
        self.assertEqual(len(exact), 1)
        kept, removed = exact[0]
        self.assertEqual(len(removed), 1)

    def test_triple_duplicate(self):
        for i in range(3):
            write_note(f"2026032410000{i}.md", "# Triple")
        write_index([
            {"date": "20260324", "file": f"notes/2026032410000{i}.md", "summary": "Triple", "tags": ["test"]}
            for i in range(3)
        ])
        index = kb_dedup.load_index()
        exact, _ = kb_dedup.find_duplicate_notes(index)
        self.assertEqual(len(exact), 1)
        self.assertEqual(len(exact[0][1]), 2)  # 2 removed, 1 kept

    def test_apply_removes_files(self):
        write_note("20260324100000.md", "# Dup A")
        write_note("20260324110000.md", "# Dup A slightly different body")
        write_index([
            {"date": "20260324", "file": "notes/20260324100000.md", "summary": "Dup A", "tags": ["test"]},
            {"date": "20260324", "file": "notes/20260324110000.md", "summary": "Dup A", "tags": ["test"]},
        ])
        index = kb_dedup.load_index()
        exact, _ = kb_dedup.find_duplicate_notes(index)
        n = kb_dedup.apply_note_dedup(exact, index)
        self.assertEqual(n, 1)
        # First entry kept, second removed
        self.assertTrue(os.path.exists(os.path.join(_notes_dir, "20260324100000.md")))
        self.assertFalse(os.path.exists(os.path.join(_notes_dir, "20260324110000.md")))
        # Index updated
        updated = kb_dedup.load_index()
        self.assertEqual(len(updated["entries"]), 1)


class TestFuzzyDedup(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()

    def test_similar_content(self):
        """Notes with same first 200 chars flagged as fuzzy duplicate."""
        long_text = "This is a long note about AI research " * 10  # >200 chars
        write_note("20260324100000.md", long_text)
        write_note("20260324110000.md", long_text + " with extra ending")
        write_index([
            {"date": "20260324", "file": "notes/20260324100000.md", "summary": "Note A", "tags": ["test"]},
            {"date": "20260324", "file": "notes/20260324110000.md", "summary": "Note B", "tags": ["test"]},
        ])
        index = kb_dedup.load_index()
        _, fuzzy = kb_dedup.find_duplicate_notes(index)
        self.assertGreaterEqual(len(fuzzy), 1)

    def test_different_content(self):
        write_note("20260324100000.md", "Completely different note about cats " * 10)
        write_note("20260324110000.md", "Totally unrelated note about dogs " * 10)
        write_index([
            {"date": "20260324", "file": "notes/20260324100000.md", "summary": "Cats", "tags": ["test"]},
            {"date": "20260324", "file": "notes/20260324110000.md", "summary": "Dogs", "tags": ["test"]},
        ])
        index = kb_dedup.load_index()
        _, fuzzy = kb_dedup.find_duplicate_notes(index)
        self.assertEqual(len(fuzzy), 0)


class TestSourceDedup(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()

    def test_no_duplicates(self):
        write_source("arxiv_daily.md", "## 2026-03-24\n- Paper A\n- Paper B\n")
        results = kb_dedup.find_duplicate_source_lines(_sources_dir)
        self.assertEqual(len(results), 0)

    def test_duplicate_lines(self):
        # V37.6: dedup is H2-scoped. Same line repeated WITHIN one H2 section
        # is a duplicate; same line across different H2 sections is legitimate
        # rolling-window recurrence and must NOT be flagged.
        write_source("arxiv_daily.md",
            "## 2026-03-24\n- Paper A: description\n- Paper B: desc\n"
            "- Paper A: description\n"  # in-section duplicate
            "## 2026-03-23\n- Paper A: description\n- Paper C: different\n"
        )
        results = kb_dedup.find_duplicate_source_lines(_sources_dir)
        self.assertIn("arxiv_daily.md", results)
        _, deduped, removed = results["arxiv_daily.md"]
        self.assertEqual(removed, 1)  # only in-section repeat of Paper A

    def test_headers_preserved(self):
        """Date headers and empty lines are never removed."""
        write_source("test.md",
            "## 2026-03-24\n\n- Item\n## 2026-03-24\n\n- Item\n"
        )
        results = kb_dedup.find_duplicate_source_lines(_sources_dir)
        if "test.md" in results:
            _, deduped, removed = results["test.md"]
            # Headers "## 2026-03-24" should be kept even if repeated
            header_count = sum(1 for l in deduped if l.strip().startswith("##"))
            self.assertEqual(header_count, 2)

    def test_apply_source_dedup(self):
        # V37.6: dedup is H2-scoped. Duplicate must be within same section.
        write_source("hn_daily.md",
            "## 2026-03-24\n- Post A\n- Post B\n- Post A\n"  # in-section dup
            "## 2026-03-23\n- Post A\n- Post C\n"  # cross-section, kept
        )
        results = kb_dedup.find_duplicate_source_lines(_sources_dir)
        n = kb_dedup.apply_source_dedup(results, _sources_dir)
        self.assertEqual(n, 1)
        # Verify file was rewritten: Post A kept once in 03-24, once in 03-23
        with open(os.path.join(_sources_dir, "hn_daily.md")) as f:
            content = f.read()
        self.assertEqual(content.count("- Post A"), 2)


class TestStats(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()

    def test_empty_kb(self):
        write_index([])
        stats = kb_dedup.generate_stats()
        self.assertEqual(stats["note_files"], 0)
        self.assertEqual(stats["index_entries"], 0)

    def test_with_data(self):
        write_note("20260324100000.md", "# Note")
        write_source("arxiv_daily.md", "data")
        write_index([{"date": "20260324", "file": "notes/20260324100000.md", "summary": "Note", "tags": []}])
        stats = kb_dedup.generate_stats()
        self.assertEqual(stats["note_files"], 1)
        self.assertEqual(stats["source_files"], 1)
        self.assertEqual(stats["index_entries"], 1)


class TestFormatReport(unittest.TestCase):

    def test_clean_report(self):
        stats = {"note_files": 10, "source_files": 3, "source_size_kb": 50.0, "index_entries": 10}
        report = kb_dedup.format_report(stats, [], [], {}, False)
        self.assertIn("无重复", report)
        self.assertIn("KB 健康", report)

    def test_report_with_dupes(self):
        stats = {"note_files": 10, "source_files": 3, "source_size_kb": 50.0, "index_entries": 10}
        exact = [
            ({"summary": "Test note"}, [{"summary": "Test note", "file": "notes/old.md"}])
        ]
        report = kb_dedup.format_report(stats, exact, [], {}, False)
        self.assertIn("精确重复", report)
        self.assertIn("--apply", report)


class TestWriteJson(unittest.TestCase):

    def test_json_output(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()
        stats = kb_dedup.generate_stats()
        kb_dedup.write_json(stats, [], [], {}, False)
        self.assertTrue(os.path.exists(kb_dedup.REPORT_JSON))
        with open(kb_dedup.REPORT_JSON) as f:
            data = json.load(f)
        self.assertIn("generated_at", data)
        self.assertEqual(data["exact_duplicates"], 0)


if __name__ == "__main__":
    unittest.main()

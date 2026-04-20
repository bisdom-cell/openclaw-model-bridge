#!/usr/bin/env python3
"""test_kb_autotag.py — kb_autotag.py 单测"""
import unittest, tempfile, os, json, shutil

_tmpdir = tempfile.mkdtemp()
_kb_base = os.path.join(_tmpdir, "kb")
_notes_dir = os.path.join(_kb_base, "notes")
_index_file = os.path.join(_kb_base, "index.json")

import kb_autotag
kb_autotag.KB_BASE = _kb_base
kb_autotag.INDEX_FILE = _index_file
kb_autotag.NOTES_DIR = _notes_dir


def setup_kb():
    os.makedirs(_notes_dir, exist_ok=True)


def write_note(filename, content, tags="技术/AI"):
    with open(os.path.join(_notes_dir, filename), "w") as f:
        f.write(f"---\ndate: 20260324\ntags: [{tags}]\nsource: direct\ntype: note\n---\n\n{content}")


def write_index(entries):
    with open(_index_file, "w") as f:
        json.dump({"entries": entries}, f)


class TestInferTags(unittest.TestCase):
    """Tag inference from content."""

    def test_ai_content(self):
        tags = kb_autotag.infer_tags("New paper on transformer architecture for LLM training")
        self.assertIn("技术/AI", tags)

    def test_ai_chinese(self):
        tags = kb_autotag.infer_tags("最新的大语言模型研究进展")
        self.assertIn("技术/AI", tags)

    def test_freight_content(self):
        tags = kb_autotag.infer_tags("Container shipping rates from Shanghai port increased")
        self.assertIn("物流/货代", tags)

    def test_freight_chinese(self):
        tags = kb_autotag.infer_tags("货代报价：上海到洛杉矶集装箱海运费上涨")
        self.assertIn("物流/货代", tags)

    def test_openclaw_content(self):
        tags = kb_autotag.infer_tags("OpenClaw gateway plugin update deployed to production")
        self.assertIn("技术/OpenClaw", tags)

    def test_arxiv_paper(self):
        tags = kb_autotag.infer_tags("arxiv paper: Attention Is All You Need - transformer research")
        self.assertIn("学术/论文", tags)
        # Should also match AI
        self.assertTrue(len(tags) >= 1)

    def test_hn_news(self):
        tags = kb_autotag.infer_tags("HackerNews top post: new startup raises funding")
        self.assertIn("科技/新闻", tags)

    def test_programming(self):
        tags = kb_autotag.infer_tags("Python HTTP API endpoint using REST framework")
        self.assertIn("技术/编程", tags)

    def test_finance(self):
        tags = kb_autotag.infer_tags("Bitcoin price surge and crypto market analysis")
        self.assertIn("财经/金融", tags)

    def test_health(self):
        tags = kb_autotag.infer_tags("改善睡眠质量的运动方法")
        self.assertIn("生活/健康", tags)

    def test_empty_content(self):
        tags = kb_autotag.infer_tags("")
        self.assertEqual(tags, ["其他/未分类"])

    def test_unknown_content(self):
        tags = kb_autotag.infer_tags("random gibberish xyzzy plugh")
        self.assertEqual(tags, ["其他/未分类"])

    def test_max_tags(self):
        """Content matching multiple categories returns at most max_tags."""
        # Matches AI + academic + programming
        content = "arxiv paper on deep learning using Python PyTorch benchmark"
        tags = kb_autotag.infer_tags(content, max_tags=2)
        self.assertLessEqual(len(tags), 2)

    def test_single_tag_string(self):
        result = kb_autotag.infer_tag_string("LLM transformer paper")
        self.assertIn("技术/AI", result)


class TestRetag(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()

    def test_retag_dry_run(self):
        write_note("20260324100000.md", "New deep learning research on transformers")
        write_index([{
            "date": "20260324",
            "file": "notes/20260324100000.md",
            "tags": ["技术/AI"],  # already correct
            "summary": "New deep learning research",
        }])
        changes = kb_autotag.retag_all(apply=False)
        # Tags should already be correct, so no changes
        # (or changes if autotag finds additional tags)
        # Either way, files should not be modified in dry-run
        with open(os.path.join(_notes_dir, "20260324100000.md")) as f:
            content = f.read()
        self.assertIn("tags: [技术/AI]", content)

    def test_retag_apply(self):
        write_note("20260324100000.md",
                    "Shanghai port container shipping freight rates analysis",
                    tags="技术/AI")  # wrong tag
        write_index([{
            "date": "20260324",
            "file": "notes/20260324100000.md",
            "tags": ["技术/AI"],
            "summary": "Shanghai port container shipping",
        }])
        changes = kb_autotag.retag_all(apply=True)
        self.assertTrue(len(changes) > 0)
        # Check file was updated
        with open(os.path.join(_notes_dir, "20260324100000.md")) as f:
            content = f.read()
        self.assertIn("物流/货代", content)
        # Check index was updated
        index = kb_autotag.load_index()
        self.assertIn("物流/货代", index["entries"][0]["tags"])

    def test_retag_no_changes(self):
        write_note("20260324100000.md", "random text nothing special xyzzy")
        write_index([{
            "date": "20260324",
            "file": "notes/20260324100000.md",
            "tags": ["其他/未分类"],
            "summary": "random text",
        }])
        changes = kb_autotag.retag_all(apply=False)
        self.assertEqual(len(changes), 0)


class TestUpdateNoteTags(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()

    def test_update_frontmatter(self):
        write_note("20260324100000.md", "test content", tags="技术/AI")
        filepath = os.path.join(_notes_dir, "20260324100000.md")
        result = kb_autotag.update_note_tags(filepath, "物流/货代")
        self.assertTrue(result)
        with open(filepath) as f:
            content = f.read()
        self.assertIn("tags: [物流/货代]", content)
        self.assertNotIn("技术/AI", content)

    def test_no_frontmatter(self):
        filepath = os.path.join(_notes_dir, "nofm.md")
        with open(filepath, "w") as f:
            f.write("No frontmatter here")
        result = kb_autotag.update_note_tags(filepath, "test")
        self.assertFalse(result)


class TestStats(unittest.TestCase):

    def setUp(self):
        if os.path.exists(_kb_base):
            shutil.rmtree(_kb_base)
        setup_kb()

    def test_empty_index(self):
        write_index([])
        # Should not crash
        kb_autotag.show_stats()

    def test_with_data(self):
        write_index([
            {"date": "20260324", "file": "a.md", "tags": ["技术/AI"], "summary": "a"},
            {"date": "20260324", "file": "b.md", "tags": ["技术/AI"], "summary": "b"},
            {"date": "20260324", "file": "c.md", "tags": ["物流/货代"], "summary": "c"},
        ])
        # Should not crash
        kb_autotag.show_stats()


if __name__ == "__main__":
    unittest.main()

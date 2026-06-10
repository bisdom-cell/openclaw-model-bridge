#!/usr/bin/env python3
"""test_v37_9_132_arxiv_first_links.py — V37.9.132 方案 A 守卫

血案: 2026-06-11 用户视角发现 deep_dive 34/45 (76%) 为摘要级分析, 与 V37.9.16
设计假设 (大部分全文 PDF 深入分析) 不符。根因 = 结构性 gap:
kb_deep_dive.fetch_pdf_text 只能从 arxiv/aclanthology/.pdf URL 派生 PDF, 但
TIER_1 六源中 dblp (doi.org 优先压过 arxiv) / hf_papers (hf.co 页面) /
semantic_scholar 无 arxiv 论文 (S2 页面) 写 KB 的链接全部无法派生 → 必然降级。
"优雅降级 + 摘要级标注" 把结构性失败伪装成正常降级 (MR-4 变种)。

方案 A (用户选): 写入层修链接 — deep_dive 零改动。
  - dblp: arxiv (ee 字段) 优先 > doi > url
  - hf_papers: paper_id 即 arxiv id → arxiv.org/abs 直链 (格式守卫 fallback hf.co)
  - semantic_scholar: arxiv > openAccessPdf (.pdf 结尾) > S2 页面 (FIELDS +openAccessPdf)

测试模式: literal-as-guard — 测试中的逻辑块 literal 必须与脚本源码逐字一致
(drift 时守卫先失败), 再 exec literal 验证行为 (MR-8 单一真理源)。
"""
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

DBLP_SH = os.path.join(REPO_ROOT, "jobs/dblp/run_dblp.sh")
HF_SH = os.path.join(REPO_ROOT, "jobs/hf_papers/run_hf_papers.sh")
S2_SH = os.path.join(REPO_ROOT, "jobs/semantic_scholar/run_semantic_scholar.sh")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── literal 逻辑块（与脚本源码必须逐字一致，由 *_literal_in_source 测试守卫）──

DBLP_LINK_LOGIC = """    if url and "arxiv.org" in url:
        link = url
    elif doi:
        link = f"https://doi.org/{doi}"
    else:
        link = url"""

HF_LINK_LOGIC = """    if paper_id and re.match(r'^\\d{4}\\.\\d{4,5}(v\\d+)?$', paper_id):
        paper_url = f"https://arxiv.org/abs/{paper_id}"
    else:
        paper_url = f"https://huggingface.co/papers/{paper_id}" if paper_id else ''"""

S2_LINK_LOGIC = """    if arxiv_id:
        link = f"https://arxiv.org/abs/{arxiv_id}"
    elif oa_pdf and oa_pdf.lower().endswith('.pdf'):
        link = oa_pdf
    else:
        link = url"""


def _run_logic(logic, namespace):
    """exec literal 逻辑块 (剥 4 空格缩进) 返回 namespace。"""
    code = "\n".join(line[4:] if line.startswith("    ") else line
                     for line in logic.split("\n"))
    ns = dict(namespace)
    ns["re"] = re
    exec(code, ns)  # noqa: S102 — 受控 literal, 与源码同步由守卫保证
    return ns


class TestDblpArxivFirstLink(unittest.TestCase):
    """dblp: arxiv (ee) 优先 > doi > url"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(DBLP_SH)

    def test_literal_in_source(self):
        """literal-as-guard: 测试逻辑块必须与源码逐字一致"""
        self.assertIn(DBLP_LINK_LOGIC, self.src,
                      "dblp 链接逻辑与测试 literal 漂移 — 同步更新两侧")

    def test_v37_9_132_marker(self):
        self.assertIn("V37.9.132", self.src)

    def test_old_doi_first_pattern_removed(self):
        """反模式守卫: 旧 doi-优先单行三元已退役"""
        self.assertNotIn(
            'link = f"https://doi.org/{doi}" if doi else url', self.src,
            "dblp 不得回退到 doi 优先 (压掉 arxiv → deep_dive 必然摘要级)")

    def test_behavior_arxiv_wins_over_doi(self):
        """血案核心场景: ee=arxiv + doi 都有 → arxiv 胜"""
        ns = _run_logic(DBLP_LINK_LOGIC, {
            "url": "https://arxiv.org/abs/2506.11111",
            "doi": "10.1234/test"})
        self.assertEqual(ns["link"], "https://arxiv.org/abs/2506.11111")

    def test_behavior_doi_when_no_arxiv(self):
        ns = _run_logic(DBLP_LINK_LOGIC, {
            "url": "https://dl.acm.org/doi/10.1234", "doi": "10.1234/test"})
        self.assertEqual(ns["link"], "https://doi.org/10.1234/test")

    def test_behavior_url_fallback(self):
        ns = _run_logic(DBLP_LINK_LOGIC, {
            "url": "https://example.org/paper", "doi": ""})
        self.assertEqual(ns["link"], "https://example.org/paper")


class TestHfArxivFirstLink(unittest.TestCase):
    """hf_papers: arxiv 格式 id → arxiv 直链, 否则保留 hf.co (格式守卫)"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HF_SH)

    def test_literal_in_source(self):
        self.assertIn(HF_LINK_LOGIC, self.src,
                      "hf 链接逻辑与测试 literal 漂移 — 同步更新两侧")

    def test_v37_9_132_marker(self):
        self.assertIn("V37.9.132", self.src)

    def test_hf_fallback_format_preserved(self):
        """test_v37_9_45 兼容: hf.co/papers 字面量必须保留 (非 arxiv id fallback)"""
        self.assertIn("https://huggingface.co/papers/", self.src)

    def test_behavior_arxiv_id_to_arxiv_link(self):
        ns = _run_logic(HF_LINK_LOGIC, {"paper_id": "2506.12345"})
        self.assertEqual(ns["paper_url"], "https://arxiv.org/abs/2506.12345")

    def test_behavior_versioned_arxiv_id(self):
        ns = _run_logic(HF_LINK_LOGIC, {"paper_id": "2506.12345v2"})
        self.assertEqual(ns["paper_url"], "https://arxiv.org/abs/2506.12345v2")

    def test_behavior_non_arxiv_id_keeps_hf(self):
        """格式守卫: 非 arxiv 格式 id 保留 hf.co (防 HF 收录非 arxiv 论文)"""
        ns = _run_logic(HF_LINK_LOGIC, {"paper_id": "some-slug"})
        self.assertEqual(ns["paper_url"],
                         "https://huggingface.co/papers/some-slug")

    def test_behavior_empty_id(self):
        ns = _run_logic(HF_LINK_LOGIC, {"paper_id": ""})
        self.assertEqual(ns["paper_url"], "")

    def test_emit_heredoc_has_re_import(self):
        """re.match 依赖 — emit heredoc 顶部必须 import re (V37.9.58-hotfix 教训)"""
        idx = self.src.find(HF_LINK_LOGIC)
        before = self.src[:idx]
        import_idx = before.rfind("import sys, json, re, os")
        self.assertGreater(import_idx, 0,
                           "hf emit heredoc 必须含 'import sys, json, re, os'")


class TestS2OaPdfChain(unittest.TestCase):
    """semantic_scholar: arxiv > OA PDF (.pdf 结尾) > S2 页面"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(S2_SH)

    def test_literal_in_source(self):
        self.assertIn(S2_LINK_LOGIC, self.src,
                      "S2 链接逻辑与测试 literal 漂移 — 同步更新两侧")

    def test_fields_includes_open_access_pdf(self):
        """S2 API FIELDS 必须请求 openAccessPdf 字段"""
        self.assertIn("externalIds,tldr,openAccessPdf", self.src)

    def test_parse_extracts_oa_pdf_none_safe(self):
        """parse 段提取 oa_pdf 必须 None 安全 (openAccessPdf 可为 null)"""
        self.assertIn(
            '(paper.get("openAccessPdf") or {}).get("url", "") or ""',
            self.src)

    def test_papers_dict_has_oa_pdf_field(self):
        self.assertIn('"oa_pdf": oa_pdf', self.src)

    def test_v37_9_132_marker(self):
        self.assertIn("V37.9.132", self.src)

    def test_behavior_arxiv_wins(self):
        ns = _run_logic(S2_LINK_LOGIC, {
            "arxiv_id": "2506.22222",
            "oa_pdf": "https://pub.org/x.pdf",
            "url": "https://www.semanticscholar.org/paper/abc"})
        self.assertEqual(ns["link"], "https://arxiv.org/abs/2506.22222")

    def test_behavior_oa_pdf_when_no_arxiv(self):
        """血案场景修复: 无 arxiv 的 OA 论文 (如医学 KG 类) → PDF 直链可抓全文"""
        ns = _run_logic(S2_LINK_LOGIC, {
            "arxiv_id": "",
            "oa_pdf": "https://journals.example.org/article/123.PDF",
            "url": "https://www.semanticscholar.org/paper/abc"})
        self.assertEqual(ns["link"],
                         "https://journals.example.org/article/123.PDF")

    def test_behavior_non_pdf_oa_keeps_s2_page(self):
        """OA 链接非 .pdf 结尾 → 保留 S2 页面 (用户体验优先, deep_dive 反正抓不了)"""
        ns = _run_logic(S2_LINK_LOGIC, {
            "arxiv_id": "",
            "oa_pdf": "https://journals.example.org/doi/pdf-redirect/123",
            "url": "https://www.semanticscholar.org/paper/abc"})
        self.assertEqual(ns["link"], "https://www.semanticscholar.org/paper/abc")

    def test_behavior_plain_fallback(self):
        ns = _run_logic(S2_LINK_LOGIC, {
            "arxiv_id": "", "oa_pdf": "",
            "url": "https://www.semanticscholar.org/paper/abc"})
        self.assertEqual(ns["link"], "https://www.semanticscholar.org/paper/abc")


class TestDeepDiveCompat(unittest.TestCase):
    """集成语义: 三源新链接格式满足 kb_deep_dive 全文派生条件 (deep_dive 零改动)"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, REPO_ROOT)
        import kb_deep_dive
        cls.dd = kb_deep_dive

    def test_arxiv_link_derives_pdf(self):
        """dblp/hf/S2 的 arxiv 直链 → fetch_pdf_text 可派生 PDF URL"""
        pdf = self.dd.arxiv_url_to_pdf("https://arxiv.org/abs/2506.12345")
        self.assertEqual(pdf, "https://arxiv.org/pdf/2506.12345.pdf")

    def test_oa_pdf_link_matches_endswith_branch(self):
        """S2 的 OA .pdf 链接满足 fetch_pdf_text 的 endswith('.pdf') 分支
        (源码级确认该分支存在且与写入侧 .pdf 过滤条件对齐)"""
        src = _read(os.path.join(REPO_ROOT, "kb_deep_dive.py"))
        self.assertIn('url.lower().endswith(".pdf")', src)

    def test_tier1_comment_updated(self):
        """deep_dive TIER_1 注释已更新为 V37.9.132 准确状态 (假设兑现)"""
        src = _read(os.path.join(REPO_ROOT, "kb_deep_dive.py"))
        self.assertIn("V37.9.132", src)


if __name__ == "__main__":
    unittest.main()

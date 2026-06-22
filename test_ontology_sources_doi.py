#!/usr/bin/env python3
"""test_ontology_sources_doi.py — V37.9.183 ontology_sources DOI 捕获单测.

背景：deep_dive 77% 摘要级结构 gap（V37.9.183）—— ontology_sources 监控的
W3C/JWS/DKE/KBS 多为 ScienceDirect/Elsevier 付费墙期刊，KB link 是 PII URL
（无 DOI），deep_dive 的 DOI→S2 OA 全文解析够不到 → 一律 abstract_only。

本修复在 KB-write 时从 RSS 提取 DOI（prism:doi / dc:identifier / guid，
Elsevier 标准用 prism:doi）→ 有 DOI 改写 link 为 doi.org/{doi} → deep_dive
V37.9.183 的 DOI→S2 解析自动接管（零 deep_dive 改动，镜像 V37.9.132）。

测试采用 literal-as-guard：从 shell 脚本抽出 _extract_item_doi 函数源码 + exec，
对真 RSS XML fixture 验证（MR-8 单一真理源，脚本逻辑变化时守卫先 fail）。
"""
import os
import re
import unittest
import xml.etree.ElementTree as ET

_REPO = os.path.dirname(os.path.abspath(__file__))
_SH = os.path.join(_REPO, "jobs", "ontology_sources", "run_ontology_sources.sh")

with open(_SH, encoding="utf-8") as _f:
    _SRC = _f.read()

# 测试用 namespace（与脚本一致）
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
}


def _extract_fn_from_script():
    """literal-as-guard：从 shell heredoc 抽出 _extract_item_doi 函数 + exec。"""
    m = re.search(r"(def _extract_item_doi\(item, ns\):\n(?:(?: {4}.*)?\n)*)", _SRC)
    if not m:
        raise AssertionError("脚本中未找到 _extract_item_doi 函数（DOI 捕获逻辑漂移？）")
    fn_ns = {"re": re}
    exec(m.group(1), fn_ns)
    return fn_ns["_extract_item_doi"]


_EXTRACT = _extract_fn_from_script()


def _item(xml):
    return ET.fromstring(xml)


class TestExtractItemDoi(unittest.TestCase):
    def test_prism_doi(self):
        x = ('<item xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">'
             '<prism:doi>10.1016/j.knosys.2026.012177</prism:doi></item>')
        self.assertEqual(_EXTRACT(_item(x), _NS), "10.1016/j.knosys.2026.012177")

    def test_dc_identifier_with_doi_prefix(self):
        x = ('<item xmlns:dc="http://purl.org/dc/elements/1.1/">'
             '<dc:identifier>doi:10.1145/3774904.3792985</dc:identifier></item>')
        self.assertEqual(_EXTRACT(_item(x), _NS), "10.1145/3774904.3792985")

    def test_guid_doi_url(self):
        x = "<item><guid>https://doi.org/10.3390/SYSTEMS14020154</guid></item>"
        self.assertEqual(_EXTRACT(_item(x), _NS), "10.3390/SYSTEMS14020154")

    def test_prism_preferred_over_dc_and_guid(self):
        x = ('<item xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/" '
             'xmlns:dc="http://purl.org/dc/elements/1.1/">'
             '<prism:doi>10.1016/prism.win</prism:doi>'
             '<dc:identifier>doi:10.1145/dc.lose</dc:identifier>'
             '<guid>10.3390/guid.lose</guid></item>')
        self.assertEqual(_EXTRACT(_item(x), _NS), "10.1016/prism.win")

    def test_no_doi_returns_empty(self):
        x = "<item><title>Some ontology paper</title><link>https://x.org/a</link></item>"
        self.assertEqual(_EXTRACT(_item(x), _NS), "")

    def test_doi_only_in_description_not_extracted(self):
        # 防 fail-plausible：摘要里引用的别人 DOI 绝不当本文 DOI（只取结构化字段）
        x = ("<item><description>This work builds on 10.9999/someone.else</description>"
             "<title>t</title></item>")
        self.assertEqual(_EXTRACT(_item(x), _NS), "")

    def test_trailing_period_stripped(self):
        x = ('<item xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">'
             '<prism:doi>10.1016/j.test.2026.001.</prism:doi></item>')
        self.assertEqual(_EXTRACT(_item(x), _NS), "10.1016/j.test.2026.001")


class TestLinkRewriteLogic(unittest.TestCase):
    """link 改写逻辑（DOI + 付费墙 PII → doi.org；arxiv/doi.org/无 DOI 不改）。

    复现脚本 line 的条件（源码守卫另测其字面存在）。
    """
    @staticmethod
    def _rewrite(link, doi):
        if doi and "arxiv.org" not in link and "doi.org" not in link:
            return "https://doi.org/" + doi
        return link

    def test_pii_link_with_doi_rewritten(self):
        out = self._rewrite(
            "https://www.sciencedirect.com/science/article/pii/S0950705126012177",
            "10.1016/j.knosys.2026.012177")
        self.assertEqual(out, "https://doi.org/10.1016/j.knosys.2026.012177")

    def test_no_doi_keeps_pii_link(self):
        pii = "https://www.sciencedirect.com/science/article/pii/S0950705126012177"
        self.assertEqual(self._rewrite(pii, ""), pii)

    def test_arxiv_link_not_rewritten(self):
        ax = "https://arxiv.org/abs/2501.12345"
        self.assertEqual(self._rewrite(ax, "10.1/2"), ax)

    def test_already_doi_link_not_doubled(self):
        d = "https://doi.org/10.1/2"
        self.assertEqual(self._rewrite(d, "10.1/2"), d)


class TestSourceGuards(unittest.TestCase):
    def test_prism_namespace_declared(self):
        self.assertIn("prismstandard.org/namespaces/basic/2.0/", _SRC,
                      "必须声明 prism namespace（Elsevier/ScienceDirect DOI 标准字段）")

    def test_v37_9_183_marker(self):
        self.assertIn("V37.9.183", _SRC)

    def test_extract_fn_defined(self):
        self.assertIn("def _extract_item_doi(item, ns):", _SRC)

    def test_link_rewrite_wired_in_loop(self):
        # link 改写必须在解析循环里调用 _extract_item_doi 并写 doi.org
        self.assertIn("doi = _extract_item_doi(item, ns)", _SRC)
        self.assertIn("link = 'https://doi.org/' + doi", _SRC)

    def test_description_not_used_for_doi(self):
        # 防回归：_extract_item_doi 字段清单不得含 description
        m = re.search(r"def _extract_item_doi.*?return ''", _SRC, re.DOTALL)
        self.assertIsNotNone(m)
        self.assertNotIn("description", m.group(0),
                         "DOI 提取不得碰 description（防摘要引用 DOI 误取）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

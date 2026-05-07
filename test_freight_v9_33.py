#!/usr/bin/env python3
"""V37.9.33 — run_freight.sh 3-tier authoritative sources + LLM 3-layer analysis.

Context: 用户论点 (V37.9.33): 全球发货信息（海运/陆运/空运）是经济晴雨表, 比咨询机构
或专家智囊判断更值得信赖. V37.9.31/32 反爬升级后, 重新评估之前因网络反爬被 pass 的
权威源. 不创建新 cron, 直接在 freight_watcher 内扩展.

V37.9.33 改动:
  - SOURCES 扩展 +12: 运价指数 (SCFI/BDI/FBX/WCI/TAC) / 班轮公司 (MAERSK/CMA/COSCO) /
    港口 (LA/Shanghai/Rotterdam) / 海关 (China/US trade data). 全部用 Google News
    RSS 精确搜索 indexed 内容 (避免直连权威源被反爬). 19→27 sources.
  - 关键词集扩展 +4: INDEX_KW / CARRIER_KW / PORT_KW / CUSTOMS_KW
  - 抓取上限 10→15: 给 Tier 1/2 权威源留空间, 防 Tier 3 行业新闻挤占
  - LLM prompt 升级三层结构化:
    📊 经济晴雨表 (运价指数 / 港口吞吐 / 海关数据)
    🏢 运营信号 (班轮公告 / 港口拥堵 / 路线变化)
    🚢 商机条目 (V25 原格式保留, Step 8 ImportYeti 入口)
  - MSG_FILE passthrough 3-section + ⭐≥4 企业信号触发 ImportYeti link
  - 末尾追加 15 条原始权威新闻链接段 (KB 归档可追溯)

向后兼容契约:
  - Step 8 line 451-452 ImportYeti regex (`企业信号：`) 只匹配 🚢 section
  - 📊 (用 `指数：`) / 🏢 (用 `动作：`) 不会误触发 ImportYeti
  - 客户画像 (Step 10) 仍只基于 ⭐≥4 企业信号
"""

import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
RUN_FREIGHT = REPO_ROOT / "jobs" / "freight_watcher" / "run_freight.sh"


class TestV37933SourcesExpanded(unittest.TestCase):
    """V37.9.33 SOURCES 列表扩展 12 个 Tier 1/2 权威源."""

    @classmethod
    def setUpClass(cls):
        cls.script = RUN_FREIGHT.read_text(encoding="utf-8")

    def test_index_sources_present(self):
        """运价指数源: SCFI/CCFI, WCI Drewry, FBX Freightos, BDI Baltic, TAC Air."""
        for query in ["SCFI", "Freightos+Baltic+Index", "Baltic+Dry+Index", "TAC+Index"]:
            self.assertIn(
                query, self.script,
                f"V37.9.33: missing index source query {query}",
            )

    def test_carrier_sources_present(self):
        """班轮公司动态源: MAERSK / CMA CGM / MSC / Hapag-Lloyd / COSCO."""
        for carrier in ["Maersk", "CMA+CGM", "Hapag-Lloyd", "COSCO"]:
            self.assertIn(
                carrier, self.script,
                f"V37.9.33: missing carrier source {carrier}",
            )

    def test_port_sources_present(self):
        """港口吞吐量 + 拥堵源."""
        for port in ["Port+of+Los+Angeles", "Long+Beach", "Shanghai+port", "Ningbo+port",
                     "port+congestion"]:
            self.assertIn(
                port, self.script,
                f"V37.9.33: missing port source {port}",
            )

    def test_customs_sources_present(self):
        """海关 / 贸易月度数据源."""
        for customs in ["China+exports", "US+imports", "Census+Bureau"]:
            self.assertIn(
                customs, self.script,
                f"V37.9.33: missing customs source {customs}",
            )

    def test_v37_9_33_marker_present(self):
        self.assertIn("V37.9.33", self.script,
                      "V37.9.33 attribution marker required")

    def test_freightwaves_kept_for_consistency(self):
        """FreightWaves 即使被 403 也保留 (脚本有 try/except, 不会崩)."""
        self.assertIn("freightwaves.com/news/feed", self.script,
                      "Don't drop legacy sources unless they actively break")

    def test_total_source_count_at_least_25(self):
        """V37.9.33 期望 SOURCES 至少 25 条 (12 原有 + 12 新 + 仍可能有几条 V3 新增)."""
        # Count tuples of form ("https://...", KW)
        m = re.search(r"SOURCES\s*=\s*\[(.+?)\n\]", self.script, flags=re.DOTALL)
        self.assertIsNotNone(m, "SOURCES list not found")
        body = m.group(1)
        # Count tuple openings
        tuple_count = len(re.findall(r'\(\s*"https?://', body))
        self.assertGreaterEqual(
            tuple_count, 25,
            f"V37.9.33: SOURCES has only {tuple_count} entries, expected ≥25",
        )


class TestV37933KeywordSets(unittest.TestCase):
    """V37.9.33 +4 keyword sets: INDEX_KW / CARRIER_KW / PORT_KW / CUSTOMS_KW."""

    @classmethod
    def setUpClass(cls):
        cls.script = RUN_FREIGHT.read_text(encoding="utf-8")

    def test_index_kw_defined(self):
        self.assertIn("INDEX_KW", self.script)
        # Must contain core index names
        self.assertIn('"scfi"', self.script.lower())
        self.assertIn('"bdi"', self.script.lower())
        self.assertIn('"fbx"', self.script.lower())

    def test_carrier_kw_defined(self):
        self.assertIn("CARRIER_KW", self.script)
        self.assertIn('"maersk"', self.script.lower())
        self.assertIn('"cma cgm"', self.script.lower())

    def test_port_kw_defined(self):
        self.assertIn("PORT_KW", self.script)
        self.assertIn('"shanghai port"', self.script.lower())
        self.assertIn('"throughput"', self.script.lower())

    def test_customs_kw_defined(self):
        self.assertIn("CUSTOMS_KW", self.script)
        self.assertIn('"customs"', self.script.lower())
        self.assertIn('"trade balance"', self.script.lower())

    def test_chinese_keywords_present(self):
        """V37.9.33: 关键词集含中英双语 (中文权威源不会被遗漏)."""
        self.assertIn("运价", self.script)
        self.assertIn("班轮", self.script)
        self.assertIn("港口", self.script)
        self.assertIn("海关", self.script)


class TestV37933CapBumped(unittest.TestCase):
    """V37.9.33 抓取上限 10 → 15 (给 Tier 1/2 留空间)."""

    @classmethod
    def setUpClass(cls):
        cls.script = RUN_FREIGHT.read_text(encoding="utf-8")

    def test_cap_is_15_not_10(self):
        # The dedup cap inside the Python heredoc
        self.assertIn("count < 15", self.script,
                      "V37.9.33: cap must be 15 (was 10)")
        # Make sure old "count < 10" doesn't accidentally remain
        # (could be in comments — only check active code blocks)
        active_lines = [
            l for l in self.script.splitlines()
            if not l.strip().startswith("#") and "count < 10" in l
        ]
        self.assertEqual(
            len(active_lines), 0,
            f"V37.9.33: legacy 'count < 10' still in active code: {active_lines}",
        )


class TestV37933LLMPromptThreeLayer(unittest.TestCase):
    """V37.9.33 LLM prompt 三层结构化 (经济晴雨表 + 运营信号 + 商机条目)."""

    @classmethod
    def setUpClass(cls):
        cls.script = RUN_FREIGHT.read_text(encoding="utf-8")

    def test_three_section_headers_in_prompt(self):
        """LLM prompt 必须含三段标题 emoji + 中文标识."""
        self.assertIn("📊", self.script, "经济晴雨表 emoji missing")
        self.assertIn("🏢", self.script, "运营信号 emoji missing")
        self.assertIn("🚢", self.script, "商机条目 emoji missing")
        self.assertIn("第一层：经济晴雨表", self.script)
        self.assertIn("第二层：运营信号", self.script)
        self.assertIn("第三层：商机条目", self.script)

    def test_section_1_uses_index_prefix(self):
        """📊 段必须用 '指数：' 前缀 (与 '企业信号：' 区分, Step 8 不会误捕)."""
        # Find the 📊 section in PROMPT
        m = re.search(r'📊.+?🏢', self.script, flags=re.DOTALL)
        self.assertIsNotNone(m, "📊 section not found in prompt")
        section1 = m.group(0)
        self.assertIn("指数：", section1,
                      "V37.9.33: 📊 section must use '指数：' prefix")
        # MUST NOT contain '企业信号：' which would trigger Step 8 ImportYeti
        self.assertNotIn("企业信号：", section1,
                         "📊 section must NOT use '企业信号：' (Step 8 conflict)")

    def test_section_2_uses_action_prefix(self):
        """🏢 段必须用 '动作：' 前缀."""
        m = re.search(r'🏢.+?🚢', self.script, flags=re.DOTALL)
        self.assertIsNotNone(m, "🏢 section not found in prompt")
        section2 = m.group(0)
        self.assertIn("动作：", section2,
                      "V37.9.33: 🏢 section must use '动作：' prefix")
        self.assertNotIn("企业信号：", section2,
                         "🏢 section must NOT use '企业信号：'")

    def test_section_3_preserves_v25_format(self):
        """🚢 段必须保留 V25 原 '企业信号：' 格式 (Step 8 ImportYeti 兼容)."""
        # Find the 🚢 section (until '⚠️ 严格约束' or end of prompt)
        m = re.search(r'🚢.+?(?:⚠️|"\s*$)', self.script, flags=re.DOTALL)
        self.assertIsNotNone(m, "🚢 section not found")
        section3 = m.group(0)
        self.assertIn("企业信号：", section3,
                      "V37.9.33: 🚢 section MUST preserve V25 '企业信号：' format")
        self.assertIn("行业信号", section3,
                      "V37.9.33: 🚢 section must preserve '行业信号' fallback")

    def test_grounding_constraint_present(self):
        """V37.9.33 prompt 末尾的反幻觉守卫."""
        self.assertIn("严禁虚构", self.script,
                      "V37.9.33: anti-hallucination guard missing")

    def test_per_section_max_5_entries_constraint(self):
        """每段最多 5 条防 WhatsApp 超长."""
        self.assertIn("每段最多输出 5 条", self.script,
                      "V37.9.33: per-section limit missing")


class TestV37933MsgFileAssembly(unittest.TestCase):
    """V37.9.33 MSG_FILE 三段式 passthrough + ⭐≥4 ImportYeti enhancement."""

    @classmethod
    def setUpClass(cls):
        cls.script = RUN_FREIGHT.read_text(encoding="utf-8")

    def test_section_aware_chunk_split(self):
        """Python heredoc must split chunks by section markers + numbered blocks."""
        self.assertIn(r"(?=\n📊|\n🏢|\n🚢|\n\d+\.)", self.script,
                      "V37.9.33: MSG_FILE assembly must use section-aware split regex")

    def test_current_section_tracking(self):
        """Tracks which section we're in (📊/🏢/🚢) for ImportYeti scoping."""
        self.assertIn("current_section", self.script,
                      "V37.9.33: must track current_section variable")
        self.assertIn('current_section = "business"', self.script,
                      "V37.9.33: 🚢 must set current_section = 'business'")

    def test_importyeti_enhancement_scoped_to_business_section(self):
        """ImportYeti link only attaches inside 🚢 business section."""
        # The if-clause must check current_section == "business"
        self.assertIn(
            'current_section == "business"', self.script,
            "V37.9.33: ImportYeti must be gated by current_section=='business'",
        )

    def test_news_links_footer_present(self):
        """V37.9.33 末尾追加 15 条原始权威新闻链接段 (KB 归档可追溯)."""
        self.assertIn("📚 本期数据来源", self.script,
                      "V37.9.33: footer with original news links missing")

    def test_extract_company_unchanged(self):
        """extract_company 函数保留 V25 原 '企业信号：' regex (Step 8 兼容)."""
        # Verify both extract_company definitions (one in MSG_FILE assembly, one in Step 8)
        m_msg = re.search(
            r"def extract_company\(block\):.+?return None\n",
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m_msg, "extract_company in MSG_FILE assembly missing")
        body = m_msg.group(0)
        self.assertIn("企业信号：(.+)", body,
                      "extract_company must keep V25 regex pattern")


class TestV37933Step8BackwardCompat(unittest.TestCase):
    """V37.9.33 Step 8 ImportYeti regex 必须保持 V25 兼容 (不能误捕 📊/🏢)."""

    @classmethod
    def setUpClass(cls):
        cls.script = RUN_FREIGHT.read_text(encoding="utf-8")

    def test_step_8_regex_only_matches_business_signal(self):
        """Step 8 line ~452 regex is `企业信号：` not `指数：` or `动作：`."""
        # Find Step 8 high-star extraction logic
        m = re.search(
            r'if len\(re\.findall\(r\'⭐\', block\)\) >= 4:.+?print\(name\)',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "Step 8 high-star extraction not found")
        block = m.group(0)
        self.assertIn("企业信号：", block,
                      "Step 8 must match '企业信号：' (V25 contract)")
        self.assertNotIn("指数：", block,
                         "Step 8 must NOT match '指数：' (else 📊 indices trigger ImportYeti)")
        self.assertNotIn("动作：", block,
                         "Step 8 must NOT match '动作：' (else 🏢 ops trigger ImportYeti)")

    def test_simulated_3_section_output_extraction(self):
        """Simulate LLM 3-section output → only 🚢 entries pass extract_company."""
        # Simulated text matching the new 3-section format
        sample = """📊 【第一层：经济晴雨表】

1. 指数：SCFI 1234.5 ↑2.3% — 上海航交所周报
解读：出口需求回暖，对货代业务利好
评级：⭐⭐⭐⭐⭐

🏢 【第二层：运营信号】

1. 动作：MAERSK 取消 5 班跨太平洋空班
影响：东西向运力减少，运价短期上升
评级：⭐⭐⭐⭐

🚢 【第三层：商机条目】

1. 企业信号：Acme Corp — 寻找跨太平洋海运合作伙伴
行动：联系采购部门，提供 LCL+FCL 报价
评级：⭐⭐⭐⭐⭐"""

        # Apply Step 8 regex logic
        company_matches = []
        for block in re.split(r'\n(?=\d+\.)', sample.strip()):
            if not block.strip():
                continue
            if len(re.findall(r'⭐', block)) >= 4:
                m = re.search(r'企业信号：(.+?)[\s]*[—–\-]', block)
                if m:
                    name = m.group(1).strip()
                    if name != "行业信号" and 2 <= len(name) <= 30:
                        company_matches.append(name)

        self.assertEqual(
            company_matches, ["Acme Corp"],
            f"Step 8 must extract ONLY 🚢 business companies, got {company_matches}. "
            f"📊 indices ('SCFI') and 🏢 carriers ('MAERSK') must be filtered out.",
        )


if __name__ == "__main__":
    unittest.main()

"""V37.9.348 — 信息源刷新守卫 (对齐纲领 T2/T3/T5/T6/T8 + 宪法级 #1).

用户 2026-09-02 指令: 依据最近业内评价与长期技术热点, 审视论文/趋势/深度分析信息源
是否需要刷新。盘点结论: 六处配置 4 个月未随项目方向演进 —— 对齐本体 project_concepts.yaml
完全没有宪法级 #1 (LLM-Observer / fail-plausible) 方向; 论文关键词零可靠性/评估/工具协议
族; arxiv 标题查询仍钉着两代前的品牌词 (ChatGPT/GPT-4); github_trending 抓 diffusion;
kb_deep_dive 主题权重无 observer/MCP/memory 族; rss_blogs 无协议一手源。

日落法: 零新 job, 全部改动落在既有 job 的关键词/feed/权重/本体上。
本守卫钉住: 每处刷新真落地 + 旧族未误删 + 外部硬约束 (GitHub OR 上限) + 无新 job。
"""
import os
import re
import subprocess
import sys
import unittest
import urllib.parse

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _bash_array(src, name):
    """提取 bash 数组元素 — 兼容单行 KEYWORDS=("a" "b") 与多行 RSS_FEEDS=( ... )。
    逐行扫描: # 开头的注释行整行跳过 (退役 feed / 说明, 其中的括号不算数组结束);
    去掉引号串后残余含 ')' 的那一行是数组结束行。"""
    m = re.search(r'^' + name + r'=\(', src, re.MULTILINE)
    assert m, f"{name} 数组必须存在"
    rest = src[m.end():]
    out = []
    for line in rest.splitlines():
        if line.strip().startswith("#"):
            continue
        out.extend(re.findall(r'"([^"]+)"', line))
        if ")" in re.sub(r'"[^"]*"', "", line):
            break
    return out


class TestProjectConceptsRefreshed(unittest.TestCase):
    def setUp(self):
        import yaml
        with open(os.path.join(REPO, "project_concepts.yaml"), encoding="utf-8") as f:
            self.c = yaml.safe_load(f)

    def test_llm_observer_direction_present(self):
        """宪法级 #1 方向必须在对齐本体里 (此前 4 个月完全缺席)。"""
        d = self.c["active_research_directions"]
        self.assertIn("llm_observer", d)
        self.assertEqual(d["llm_observer"]["weight"], 5)
        kws = " | ".join(d["llm_observer"]["keywords"]).lower()
        for k in ("fail-plausible", "silent failure", "llm-as-a-judge", "runtime observer"):
            self.assertIn(k, kws)

    def test_generative_media_excluded(self):
        ex = self.c["excluded_topics"]
        self.assertIn("generative_media", ex)
        self.assertLess(ex["generative_media"]["weight"], 0)
        self.assertIn("diffusion model", ex["generative_media"]["keywords"])

    def test_charter_trend_keywords_present(self):
        """纲领 T3/T5/T6 (reasoning 控制 / MCP / agent memory) 进能力/记忆平面。"""
        cap = " | ".join(self.c["core_planes"]["capability_plane"]["keywords"]).lower()
        mem = " | ".join(self.c["core_planes"]["memory_plane"]["keywords"]).lower()
        for k in ("model context protocol", "reasoning budget", "workload routing"):
            self.assertIn(k, cap)
        for k in ("agent memory", "context engineering"):
            self.assertIn(k, mem)

    def test_version_and_date_bumped(self):
        self.assertNotEqual(self.c["version"], "0.1-poc", "V37.9.348 升 0.2")
        self.assertGreaterEqual(str(self.c["last_updated"]), "2026-09")

    def test_scorer_behavior_observer_abstract(self):
        """行为级: observer 类摘要经 rule_check 应命中 ≥4 新关键词并与 ⭐5 一致。"""
        import project_alignment_scorer as pas
        text = ("We present a runtime observer that detects silent failures in LLM "
                "agents using an LLM-as-a-judge with evidence grounding, and report "
                "detection latency and false positive rates.")
        r = pas.validate_alignment_score(text, 5, self.c)
        self.assertTrue(r["validated"], r)
        self.assertGreaterEqual(r["positive_hits"], 4)
        self.assertIn("silent failure", r["matched_keywords"])

    def test_scorer_behavior_diffusion_abstract_downweighted(self):
        """行为级: 生成式媒体摘要被 excluded 降权, ⭐5 判不一致。"""
        import project_alignment_scorer as pas
        text = "A new diffusion model for text-to-image synthesis with improved video generation."
        r = pas.validate_alignment_score(text, 5, self.c)
        self.assertFalse(r["validated"], r)
        self.assertGreater(r["negative_hits"], 0)


class TestDeepDiveTopicWeights(unittest.TestCase):
    def setUp(self):
        import kb_deep_dive
        self.m = kb_deep_dive

    def test_new_families_present(self):
        w = self.m.TOPIC_WEIGHTS
        for k in ("llm-as-a-judge", "observability", "silent failure",
                  "model context protocol", "agent memory", "thinking budget"):
            self.assertIn(k, w)
        self.assertGreaterEqual(w["reliability"], 9)
        self.assertGreater(w["reliability"], w["evaluation"])

    def test_observer_title_outscores_plain(self):
        """行为级: 含 observer 族词的标题必须比同星级平淡标题高 ≥10 分。"""
        a = {"stars": 4, "title": "detecting silent failure with an llm-as-a-judge observer", "abstract": ""}
        b = {"stars": 4, "title": "detecting things with an observer", "abstract": ""}
        self.assertGreaterEqual(self.m.score_entry(a) - self.m.score_entry(b), 10)


class TestArxivQueryRefreshed(unittest.TestCase):
    def setUp(self):
        src = _read("jobs/arxiv_monitor/run_arxiv.sh")
        url = re.search(r'^ARXIV_URL="([^"]+)"', src, re.MULTILINE).group(1)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.terms = q["search_query"][0].split(" OR ")
        self.max_results = int(q["max_results"][0])
        self.src = src

    def test_all_terms_are_title_scoped(self):
        self.assertTrue(all(t.startswith("ti:") for t in self.terms), self.terms)

    def test_stale_brand_terms_retired(self):
        joined = " ".join(self.terms)
        self.assertNotIn("GPT-4", joined)
        self.assertNotIn("ChatGPT", joined)

    def test_charter_method_terms_added(self):
        joined = " ".join(self.terms)
        for k in ("Agentic", "Tool Use", "Function Calling", "LLM-as-a-Judge",
                  "Model Context Protocol", "Agent Memory", "Hallucination"):
            self.assertIn(k, joined, f"arxiv 标题查询缺 {k}")

    def test_ontology_family_preserved(self):
        joined = " ".join(self.terms)
        for k in ("Ontology", "Knowledge Graph", "Neuro-Symbolic", "Knowledge Representation"):
            self.assertIn(k, joined)

    def test_pool_widened_analysis_cap_unchanged(self):
        """候选池 ≥80 (OR 面变宽), 但分析上限 MAX_PAPERS 仍 10 = LLM 成本不变。"""
        self.assertGreaterEqual(self.max_results, 80)
        self.assertIsNotNone(re.search(r"^MAX_PAPERS=10\s*$", self.src, re.MULTILINE),
                             "MAX_PAPERS 分析上限必须仍是 10 (LLM 成本不随候选池变宽)")


class TestPaperKeywordFamilies(unittest.TestCase):
    LEGACY_S2 = ("large language model", "LLM agent", "RAG retrieval augmented",
                 "multimodal AI", "RLHF alignment", "ontology knowledge graph")
    ONTOLOGY6 = ("neuro-symbolic reasoning", "enterprise ontology",
                 "formal ontology information systems", "description logic OWL",
                 "semantic web linked data", "knowledge representation reasoning")

    def test_s2_and_dblp_have_16_with_reliability_family(self):
        s2 = _bash_array(_read("jobs/semantic_scholar/run_semantic_scholar.sh"), "KEYWORDS")
        dblp = _bash_array(_read("jobs/dblp/run_dblp.sh"), "KEYWORDS")
        self.assertEqual(len(s2), 16, s2)
        self.assertEqual(len(dblp), 16, dblp)
        for kw in self.LEGACY_S2:
            self.assertIn(kw, s2, "旧族不得误删")
        for kw in self.ONTOLOGY6:
            self.assertIn(kw, s2)
            self.assertIn(kw, dblp)
        for token in ("judge", "tool learning", "hallucination", "memory"):
            self.assertIn(token, " | ".join(s2).lower())
            self.assertIn(token, " | ".join(dblp).lower())


class TestGithubTrendingTopics(unittest.TestCase):
    def setUp(self):
        src = _read("jobs/github_trending/run_github_trending.sh")
        self.topics = re.search(r'^TOPICS="([^"]+)"', src, re.MULTILINE).group(1)

    def test_diffusion_retired_agent_terms_added(self):
        self.assertNotIn("diffusion", self.topics)
        for k in ("ai-agent", "mcp-server", "llm-evaluation"):
            self.assertIn(k, self.topics)

    def test_github_or_operator_hard_limit(self):
        """GitHub search 硬限制: 最多 5 个 AND/OR/NOT 操作符 — 超了整条查询 422。"""
        self.assertLessEqual(self.topics.count("+OR+"), 5)


class TestRssBlogsFeeds(unittest.TestCase):
    def setUp(self):
        self.feeds = _bash_array(_read("jobs/rss_blogs/run_rss_blogs.sh"), "RSS_FEEDS")
        self.src = _read("jobs/rss_blogs/run_rss_blogs.sh")

    def test_mcp_official_feed_present_and_well_formed(self):
        """纲领 T5/G7 的一手观察口 (dev WebFetch 验证有效 feed)。"""
        mcp = [f for f in self.feeds if "modelcontextprotocol.io" in f]
        self.assertEqual(len(mcp), 1, self.feeds)
        name, url, label = mcp[0].split("|")
        self.assertTrue(url.startswith("https://") and url.endswith("index.xml"))
        self.assertTrue(name.strip() and label.strip())

    def test_unverified_candidates_flagged_for_mac_mini(self):
        """dev 出口代理拦截无法预验的候选必须在源码里明写 Mac Mini 首跑验证 (原则 #33)。"""
        for dom in ("hamel.dev", "huyenchip.com"):
            self.assertIn(dom, self.src)
        self.assertIn("Mac Mini 首跑验证", self.src)
        self.assertIn("FAIL-OPEN", self.src)

    def test_every_feed_domain_in_no_overlap_contract(self):
        """MR-8: rss_blogs 每个 feed 域名都必须出现在 test_ai_leaders_blogs 的 no-overlap 表,
        否则「ai_leaders_blogs 不重复 rss_blogs」契约对新 feed 空转。"""
        import test_ai_leaders_blogs as tb
        for f in self.feeds:
            host = urllib.parse.urlparse(f.split("|")[1]).netloc
            self.assertTrue(any(d in host for d in tb._RSS_BLOGS_DOMAINS),
                            f"{host} 不在 _RSS_BLOGS_DOMAINS, no-overlap 契约漏它")


class TestSunsetLawNoNewJob(unittest.TestCase):
    def test_no_new_job_or_content_source(self):
        """日落法: 本次刷新零新 job / 零新内容源 (全部落在既有 job 配置)。"""
        import yaml
        with open(os.path.join(REPO, "jobs_registry.yaml"), encoding="utf-8") as f:
            r = yaml.safe_load(f)
        jobs = [j for j in r.get("jobs", r) if isinstance(j, dict)]
        self.assertEqual(len(jobs), 47)
        self.assertEqual(sum(1 for j in jobs if j.get("kb_source_file")), 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)

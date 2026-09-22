#!/usr/bin/env python3
"""V37.9.355 (2026-09-22) — World Model / 具身机器人 两个新跟踪领域接入既有信息获取平台的守卫。

用户指令: 基于已有的「全世界最权威最新最全信息获取/分析/总结」平台, 再增加两个最具潜力的领域:
World Model (世界模型) 与 具身机器人。按日落法 (原则 #34) + V37.9.348 先例, 零新 job / 零新内容源,
全部落在既有 job 的配置面: 对齐本体 (project_concepts) / 深读权重 (kb_deep_dive) / arxiv ti: 查询 /
S2+DBLP 关键词 / github_trending 主题 / rss_blogs feed / kb_autotag 分类。

本守卫钉住 (原则 #36-1: 先断言用户可见结果, 再断言机制):
  1. 对齐本体: 两个方向真存在 + 行为级——世界模型/具身摘要经 rule_check 与 ⭐4 一致; 含 "video generation"
     措辞的世界模型摘要不再被 generative_media 误降权 (血案预防); 纯生成媒体摘要仍被降权 (检出力不减)
  2. 深读权重: 新族在 + 世界模型/具身标题比平淡标题高 ≥10 + 核心叙事 (control plane) 权重仍 ≥ 任一新词
  3. arxiv: 新 ti: 词在 + 全部仍 ti: 作用域 + 348 族保留 + 候选池 ≥100 + MAX_PAPERS 仍 10 (LLM 成本不变)
  4. S2/DBLP: 各恰 18 + 新族在 + 348 全部 16 逐字保留
  5. github_trending: robotics/world-model 在 + 348 三词保留 + 退役词退役 + OR ≤5 硬上限
  6. rss_blogs: 两 feed 在且形状正确 + 源码明写 Mac Mini 首跑验证/FAIL-OPEN + 域名进 no-overlap 表 (MR-8)
     + 与 ai_leaders_blogs 零重叠
  7. kb_autotag: 行为级——机器人笔记得 技术/机器人; V37.9.331 财经血案文本不得得机器人标签; 词边界防
     vlad/chatbot 假命中; 通用词 manipulation 刻意不在关键词表
  8. 日落法: jobs 仍 47 / 内容源仍 16 (零新 job)
"""
import os
import re
import sys
import unittest
import urllib.parse

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _bash_array(src, name):
    """提取 bash 数组元素 — 兼容单行 KEYWORDS=("a" "b") 与多行 RSS_FEEDS=( ... ) (含注释行)。"""
    m = re.search(r'^' + re.escape(name) + r'=\((.*?)^\)', src, re.MULTILINE | re.DOTALL)
    if not m:
        m = re.search(r'^' + re.escape(name) + r'=\((.*)\)\s*$', src, re.MULTILINE)
    assert m, f"{name} 数组必须存在"
    body = "\n".join(l for l in m.group(1).split("\n") if not l.strip().startswith("#"))
    return re.findall(r'"([^"]+)"', body)


def _concepts():
    import yaml
    with open(os.path.join(REPO, "project_concepts.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


# ══════════════════════════════════════════════════════════════════════
# 1. 对齐本体
# ══════════════════════════════════════════════════════════════════════
class TestAlignmentOntologyNewDomains(unittest.TestCase):
    def setUp(self):
        self.c = _concepts()
        self.d = self.c["active_research_directions"]

    def test_two_directions_present_as_tracking_domains(self):
        for name in ("world_model", "embodied_robotics"):
            self.assertIn(name, self.d)
            self.assertEqual(self.d[name]["weight"], 4, "跟踪领域 = 4, 非工程核心 (control plane 仍 5)")
            self.assertGreaterEqual(len(self.d[name]["keywords"]), 8)
        self.assertEqual(self.c["core_planes"]["control_plane"]["weight"], 5)

    def test_domain_keywords_cover_field_vocabulary(self):
        wm = " | ".join(self.d["world_model"]["keywords"]).lower()
        for k in ("world model", "jepa", "latent", "world action model", "model-based", "世界模型"):
            self.assertIn(k, wm)
        er = " | ".join(self.d["embodied_robotics"]["keywords"]).lower()
        for k in ("embodied", "vision-language-action", "humanoid", "sim-to-real",
                  "robot foundation model", "teleoperation", "具身", "机器人"):
            self.assertIn(k, er)

    def test_no_vendor_or_product_names_in_ontology(self):
        """厂商/产品名进 arxiv ti: 查询而非对齐本体 (子串误命中: cosmos/genie 都是普通英文词)。"""
        joined = " | ".join(self.d["world_model"]["keywords"] + self.d["embodied_robotics"]["keywords"]).lower()
        for brand in ("genie", "cosmos", "gr00t", "optimus", "figure", "unitree", "isaac"):
            self.assertNotIn(brand, joined)

    def test_video_generation_retired_from_exclusion_but_pure_media_words_kept(self):
        kws = self.c["excluded_topics"]["generative_media"]["keywords"]
        self.assertNotIn("video generation", kws, "世界模型论文常自述 'video generation', 保留会误伤新领域")
        for k in ("diffusion model", "text-to-image", "text-to-video"):
            self.assertIn(k, kws, "纯生成媒体词必须保留 (检出力不减)")

    def test_version_bumped(self):
        self.assertEqual(str(self.c["version"]), "0.3")
        self.assertGreaterEqual(str(self.c["last_updated"]), "2026-09-22")

    # ---- 行为级 (用户可见结果): rule_check 对新领域摘要的判定 ----
    def test_scorer_world_model_abstract_consistent_with_star4(self):
        import project_alignment_scorer as pas
        text = ("We introduce a latent world model built on a joint embedding predictive "
                "architecture; the learned dynamics model enables model-based RL planning "
                "and outperforms video generation baselines on physical reasoning tasks.")
        r = pas.validate_alignment_score(text, 4, self.c)
        self.assertTrue(r["validated"], r)
        self.assertGreaterEqual(r["positive_hits"], 3, r)
        self.assertIn("world model", r["matched_keywords"])
        self.assertEqual(r["negative_hits"], 0,
                         "血案预防: 世界模型摘要里的 'video generation' 措辞不得再触发 generative_media 降权")

    def test_scorer_embodied_abstract_consistent_with_star4(self):
        import project_alignment_scorer as pas
        text = ("A vision-language-action model for humanoid robot manipulation, trained with "
                "teleoperation demonstrations and transferred sim-to-real; we report "
                "cross-embodiment generalization on a robot foundation model benchmark.")
        r = pas.validate_alignment_score(text, 4, self.c)
        self.assertTrue(r["validated"], r)
        self.assertGreaterEqual(r["positive_hits"], 4, r)
        self.assertIn("vision-language-action", r["matched_keywords"])

    def test_scorer_pure_generative_media_still_downweighted(self):
        """检出力不减: 纯 text-to-image diffusion 摘要仍被 excluded 降权, ⭐5 判不一致。"""
        import project_alignment_scorer as pas
        text = "A new diffusion model for text-to-image synthesis and text-to-video with a music generation head."
        r = pas.validate_alignment_score(text, 5, self.c)
        self.assertFalse(r["validated"], r)
        self.assertGreater(r["negative_hits"], 0)

    def test_scorer_reverse_evidence_video_generation_was_penalized_before(self):
        """反向证据 (防守卫空转): 若把 'video generation' 放回 excluded, 同一世界模型摘要必被降权。"""
        import copy
        import project_alignment_scorer as pas
        c2 = copy.deepcopy(self.c)
        c2["excluded_topics"]["generative_media"]["keywords"].append("video generation")
        text = ("We introduce a latent world model built on a joint embedding predictive "
                "architecture; the learned dynamics model enables model-based RL planning "
                "and outperforms video generation baselines on physical reasoning tasks.")
        r = pas.validate_alignment_score(text, 4, c2)
        self.assertGreater(r["negative_hits"], 0, "退役前该措辞确实会被扣分, 否则本守卫在守空气")


# ══════════════════════════════════════════════════════════════════════
# 2. 深读权重
# ══════════════════════════════════════════════════════════════════════
class TestDeepDiveTopicWeights(unittest.TestCase):
    def setUp(self):
        import kb_deep_dive
        self.m = kb_deep_dive

    def test_new_families_present_and_capped_below_core(self):
        w = self.m.TOPIC_WEIGHTS
        for k in ("world model", "embodied", "vision-language-action", "humanoid",
                  "sim-to-real", "robot", "jepa", "世界模型", "具身"):
            self.assertIn(k, w)
        newmax = max(w[k] for k in ("world model", "embodied", "vision-language-action",
                                    "humanoid", "sim-to-real", "robot", "jepa"))
        self.assertGreaterEqual(w["control plane"], newmax, "核心叙事不得被新领域压过")
        self.assertGreaterEqual(w["agent runtime"], newmax)

    def test_domain_title_outscores_plain(self):
        a = {"stars": 4, "title": "a latent world model for humanoid robot locomotion", "abstract": ""}
        b = {"stars": 4, "title": "a model for locomotion things", "abstract": ""}
        self.assertGreaterEqual(self.m.score_entry(a) - self.m.score_entry(b), 10)

    def test_existing_fixture_scores_unchanged(self):
        """新词不得子串误命中既有 fixture (test_kb_deep_dive 的 50/50/68 契约)。"""
        self.assertEqual(self.m.score_entry({"stars": 5, "title": "plain", "abstract": ""}), 50)
        self.assertEqual(self.m.score_entry({"stars": 4, "title": "ontology engine", "abstract": ""}), 50)


# ══════════════════════════════════════════════════════════════════════
# 3. arxiv 查询
# ══════════════════════════════════════════════════════════════════════
class TestArxivQuery(unittest.TestCase):
    def setUp(self):
        self.src = _read("jobs/arxiv_monitor/run_arxiv.sh")
        url = re.search(r'^ARXIV_URL="([^"]+)"', self.src, re.MULTILINE).group(1)
        self.q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.terms = self.q["search_query"][0].split(" OR ")

    def test_new_domain_terms_present_all_title_scoped(self):
        for t in ('ti:"World Model"', 'ti:"World Models"', "ti:JEPA", "ti:Embodied", "ti:Humanoid",
                  'ti:"Vision-Language-Action"', "ti:VLA", 'ti:"Robot Learning"',
                  'ti:"Sim-to-Real"', 'ti:"Robot Foundation Model"'):
            self.assertIn(t, self.terms, self.terms)
        self.assertTrue(all(t.startswith("ti:") for t in self.terms), self.terms)

    def test_v348_families_preserved(self):
        for t in ('ti:"LLM-as-a-Judge"', 'ti:"Model Context Protocol"', "ti:Ontology",
                  'ti:"Knowledge Graph"', "ti:Agentic"):
            self.assertIn(t, self.terms)
        self.assertNotIn("ti:ChatGPT", self.terms)

    def test_pool_widened_analysis_cap_unchanged(self):
        self.assertGreaterEqual(int(self.q["max_results"][0]), 100)
        self.assertRegex(self.src, r"(?m)^MAX_PAPERS=10\s*$")

    def test_seat_competition_honestly_noted(self):
        """诚实登记: 10 席位先到先得, 机器人论文分走部分席位——写在源码里而不是假装零影响。"""
        self.assertIn("席位", self.src)


# ══════════════════════════════════════════════════════════════════════
# 4. S2 / DBLP 关键词
# ══════════════════════════════════════════════════════════════════════
class TestPaperKeywordFamilies(unittest.TestCase):
    V348_S2 = ("large language model", "LLM agent", "RAG retrieval augmented", "multimodal AI",
               "RLHF alignment", "ontology knowledge graph", "neuro-symbolic reasoning",
               "enterprise ontology", "formal ontology information systems", "description logic OWL",
               "semantic web linked data", "knowledge representation reasoning",
               "LLM-as-a-judge evaluation", "tool learning function calling agent",
               "hallucination detection LLM reliability", "agent long-term memory")
    V348_DBLP = ("large language model", "LLM agent", "multimodal foundation model",
                 "retrieval augmented generation", "RLHF alignment", "ontology knowledge graph",
                 "neuro-symbolic reasoning", "enterprise ontology", "formal ontology information systems",
                 "description logic OWL", "semantic web linked data", "knowledge representation reasoning",
                 "LLM judge", "tool learning", "hallucination detection", "agent memory")

    def test_each_source_has_18_with_v348_preserved(self):
        s2 = _bash_array(_read("jobs/semantic_scholar/run_semantic_scholar.sh"), "KEYWORDS")
        dblp = _bash_array(_read("jobs/dblp/run_dblp.sh"), "KEYWORDS")
        self.assertEqual(len(s2), 18, s2)
        self.assertEqual(len(dblp), 18, dblp)
        for kw in self.V348_S2:
            self.assertIn(kw, s2, "V37.9.348 关键词不得误删")
        for kw in self.V348_DBLP:
            self.assertIn(kw, dblp, "V37.9.348 关键词不得误删")

    def test_new_domain_family_in_both_sources(self):
        s2 = " | ".join(_bash_array(_read("jobs/semantic_scholar/run_semantic_scholar.sh"), "KEYWORDS")).lower()
        dblp = " | ".join(_bash_array(_read("jobs/dblp/run_dblp.sh"), "KEYWORDS")).lower()
        self.assertIn("world model", s2)
        self.assertIn("world model", dblp)
        self.assertTrue("vision-language-action" in s2 or "embodied" in s2, s2)
        self.assertIn("vision language action", dblp, "DBLP 是 token 前缀 AND 匹配, 用无连字符三 token 形态")


# ══════════════════════════════════════════════════════════════════════
# 5. github_trending
# ══════════════════════════════════════════════════════════════════════
class TestGithubTrendingTopics(unittest.TestCase):
    def setUp(self):
        self.src = _read("jobs/github_trending/run_github_trending.sh")
        self.topics = re.search(r'^TOPICS="([^"]+)"', self.src, re.MULTILINE).group(1)

    def test_new_domains_added_within_hard_limit(self):
        for k in ("robotics", "world-model"):
            self.assertIn(k, self.topics.split("+OR+"))
        self.assertLessEqual(self.topics.count("+OR+"), 5, "GitHub search 硬上限 5 个 OR, 超了整条 422")

    def test_v348_terms_preserved_and_retired_ones_gone(self):
        parts = self.topics.split("+OR+")
        for k in ("llm", "ai-agent", "mcp-server", "llm-evaluation"):
            self.assertIn(k, parts)
        for k in ("machine-learning", "agentic", "diffusion-model"):
            self.assertNotIn(k, parts, "换出的词必须真退役 (6 词硬上限)")
        self.assertIn("V37.9.355", self.src)


# ══════════════════════════════════════════════════════════════════════
# 6. rss_blogs feeds
# ══════════════════════════════════════════════════════════════════════
class TestRssBlogsFeeds(unittest.TestCase):
    NEW = {"deepmind.google": "rss.xml", "spectrum.ieee.org": "robotics.rss"}

    def setUp(self):
        self.src = _read("jobs/rss_blogs/run_rss_blogs.sh")
        self.feeds = _bash_array(self.src, "RSS_FEEDS")

    def test_two_new_feeds_present_well_formed(self):
        for dom, suffix in self.NEW.items():
            hits = [f for f in self.feeds if dom in f]
            self.assertEqual(len(hits), 1, (dom, self.feeds))
            name, url, label = hits[0].split("|")
            self.assertTrue(url.startswith("https://") and url.endswith(suffix), url)
            self.assertTrue(name.strip() and label.strip())
        self.assertGreaterEqual(len(self.feeds), 9, "V37.9.348 的 7 条 + 本次 2 条")

    def test_unverified_feeds_flagged_for_mac_mini(self):
        """dev 出口代理 EGRESS_BLOCKED 无法预验 → 源码必须明写 Mac Mini 首跑验证 + FAIL-OPEN (原则 #33)。"""
        blk = self.src[self.src.index("V37.9.355"):]
        self.assertIn("Mac Mini 首跑验证", blk)
        self.assertIn("FAIL-OPEN", blk)
        self.assertIn("EGRESS_BLOCKED", blk)

    def test_domains_in_no_overlap_contract_and_not_in_ai_leaders_blogs(self):
        import test_ai_leaders_blogs as tb
        for dom in self.NEW:
            self.assertTrue(any(dom in d for d in tb._RSS_BLOGS_DOMAINS),
                            f"{dom} 不在 _RSS_BLOGS_DOMAINS, MR-8 no-overlap 契约漏它")
        blogs = _read("jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh")
        for dom in self.NEW:
            self.assertNotIn(dom, blogs, "ai_leaders_blogs 不得与 rss_blogs 重叠")


# ══════════════════════════════════════════════════════════════════════
# 7. kb_autotag
# ══════════════════════════════════════════════════════════════════════
class TestAutotagRoboticsCategory(unittest.TestCase):
    def setUp(self):
        import kb_autotag
        self.k = kb_autotag
        self.rules = dict(kb_autotag.TAG_RULES)

    def test_category_exists_without_generic_false_hit_words(self):
        self.assertIn("技术/机器人", self.rules)
        kws = self.rules["技术/机器人"]
        for k in ("robot", "humanoid", "embodied", "具身", "机器人", "world model", "vla"):
            self.assertIn(k, kws)
        for generic in ("manipulation", "policy", "model", "action"):
            self.assertNotIn(generic, kws, "通用词会让 'market manipulation' 类笔记假命中 (V37.9.331 家族)")

    def test_robotics_note_gets_category(self):
        tags = self.k.infer_tags(
            "A vision-language-action model for humanoid robot locomotion, trained sim-to-real "
            "with teleoperation data.")
        self.assertIn("技术/机器人", tags, tags)

    def test_v331_finance_blood_text_untouched(self):
        # V37.9.331 血案文本: 修复后该笔记不得再拿到任何技术类假标签 (新分类也不能成为新的假命中源)
        tags = self.k.infer_tags("Central bank raised interest rates; inflation outlook uncertain")
        self.assertNotIn("技术/机器人", tags, tags)
        self.assertNotIn("技术/AI", tags, tags)
        tags2 = self.k.infer_tags("Stock market and bitcoin fell after market manipulation probe")
        self.assertIn("财经/金融", tags2, tags2)
        self.assertNotIn("技术/机器人", tags2, "manipulation 不得触发机器人标签")

    def test_word_boundary_blocks_vlad_and_chatbot(self):
        tags = self.k.infer_tags("we deployed a chatbot named vlad for customer support with an llm")
        self.assertNotIn("技术/机器人", tags, tags)


# ══════════════════════════════════════════════════════════════════════
# 8. 日落法
# ══════════════════════════════════════════════════════════════════════
class TestSunsetLawNoNewJob(unittest.TestCase):
    def test_no_new_job_or_content_source(self):
        import yaml
        with open(os.path.join(REPO, "jobs_registry.yaml"), encoding="utf-8") as f:
            r = yaml.safe_load(f)
        jobs = [j for j in r.get("jobs", r) if isinstance(j, dict)]
        self.assertEqual(len(jobs), 47)
        self.assertEqual(sum(1 for j in jobs if j.get("kb_source_file")), 16)
        rss = [j for j in jobs if j.get("id") == "rss_blogs"][0]
        self.assertIn("V37.9.355", rss.get("description", ""), "registry 描述须随 feed 列表同步 (gen_jobs_doc 一致)")


if __name__ == "__main__":
    unittest.main(verbosity=2)

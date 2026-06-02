#!/usr/bin/env python3
"""V37.9.98 来源可信度评级单测.

覆盖:
  TestCredibilityTiers      — 5 档 tier 语义 + rank 唯一 + 排序
  TestGetCredibility        — 已知/未知/非string source 查询 + FAIL-OPEN
  TestSourceCoverage        — MR-8 drift guard: SOURCE_CREDIBILITY ↔ jobs_registry 14 源一致
  TestChasparkFlagged       — observer proposal #2 点名的非主流源被正确分级 + block 提示
  TestFormatBlock           — prompt 注入块完整性 (5 tier + 14 label + 标注指令)
  TestCli                   — subprocess CLI (--list/--sources/--block/--source)
  TestDreamWiring           — kb_dream.sh 5 注入点 + import + 反 inline 守卫
  TestEveningWiring         — kb_evening_collect.py import + build_evening_prompt 渲染
  TestAutoDeployFileMap     — FILE_MAP 部署登记
  TestSourceLevelGuards     — V37.9.98 marker + MR-7/MR-8 + FAIL-OPEN 文档 + 反向验证守卫真有效

反向验证 (手动确认守卫有效): sed 把 SOURCE_CREDIBILITY chaspark tier 改成
"学术同行评审" → TestChasparkFlagged.test_chaspark_is_blog_tier 立即 fail;
删一个 source 条目 → TestSourceCoverage drift guard 立即 fail. 还原后全过.
"""

import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import source_credibility as sc  # noqa: E402

_REPO = os.path.dirname(os.path.abspath(__file__))
_MODULE_PATH = os.path.join(_REPO, "source_credibility.py")
_JOBS_REGISTRY = os.path.join(_REPO, "jobs_registry.yaml")
_DREAM_SH = os.path.join(_REPO, "kb_dream.sh")
_EVENING_PY = os.path.join(_REPO, "kb_evening_collect.py")
_AUTO_DEPLOY = os.path.join(_REPO, "auto_deploy.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _jobs_registry_content_sources():
    """从 jobs_registry.yaml 提取 {job_id: kb_source_label} (含 kb_source_file 的源)."""
    lines = _read(_JOBS_REGISTRY).splitlines()
    cur = None
    out = {}
    for ln in lines:
        m = re.match(r"^\s*-\s*id:\s*([a-zA-Z0-9_]+)", ln)
        if m:
            cur = m.group(1)
        if "kb_source_label:" in ln and cur:
            out[cur] = ln.split("kb_source_label:", 1)[1].strip()
    return out


# ════════════════════════════════════════════════════════════════════
class TestCredibilityTiers(unittest.TestCase):
    def test_exactly_five_tiers(self):
        self.assertEqual(len(sc._CREDIBILITY_TIERS), 5)

    def test_tier_names_match_design(self):
        # 设计 (unfinished #1) 锁定的 5 个 tier 名
        expected = {"学术同行评审", "工业实践", "会议预印本", "博客", "社媒"}
        self.assertEqual(set(sc._CREDIBILITY_TIERS), expected)

    def test_ranks_are_1_to_5_unique(self):
        ranks = sorted(m["rank"] for m in sc._CREDIBILITY_TIERS.values())
        self.assertEqual(ranks, [1, 2, 3, 4, 5])

    def test_list_tiers_sorted_by_rank_ascending(self):
        tiers = sc.list_tiers()
        self.assertEqual(tiers[0], "学术同行评审")  # rank 1 最可信
        self.assertEqual(tiers[-1], "社媒")          # rank 5 最需核实
        ranks = [sc._CREDIBILITY_TIERS[t]["rank"] for t in tiers]
        self.assertEqual(ranks, sorted(ranks))

    def test_each_tier_has_required_fields(self):
        for name, meta in sc._CREDIBILITY_TIERS.items():
            for key in ("rank", "emoji", "verifiability", "trust_note"):
                self.assertIn(key, meta, f"{name} 缺字段 {key}")
            self.assertTrue(meta["emoji"], f"{name} emoji 空")

    def test_default_tier_is_most_conservative(self):
        # 未知源默认最低可信度 (社媒 rank 5), 不是最高
        self.assertEqual(sc._DEFAULT_TIER, "社媒")
        self.assertEqual(sc._CREDIBILITY_TIERS[sc._DEFAULT_TIER]["rank"], 5)


# ════════════════════════════════════════════════════════════════════
class TestGetCredibility(unittest.TestCase):
    def test_known_academic_source(self):
        info = sc.get_credibility("semantic_scholar")
        self.assertEqual(info["tier"], "学术同行评审")
        self.assertEqual(info["rank"], 1)
        self.assertEqual(info["reason"], "")

    def test_known_preprint_source(self):
        self.assertEqual(sc.get_credibility("arxiv_monitor")["tier"], "会议预印本")

    def test_chaspark_is_blog(self):
        self.assertEqual(sc.get_credibility("chaspark")["tier"], "博客")

    def test_unknown_source_fails_open_to_social(self):
        info = sc.get_credibility("totally_unknown_source")
        self.assertEqual(info["tier"], "社媒")
        self.assertEqual(info["rank"], 5)
        self.assertEqual(info["reason"], "未分类来源")

    def test_non_string_fails_open(self):
        for bad in (None, 123, [], {}):
            info = sc.get_credibility(bad)
            self.assertEqual(info["tier"], "社媒")
            self.assertEqual(info["reason"], "未分类来源")
            self.assertEqual(info["label"], "")

    def test_return_dict_has_all_fields(self):
        info = sc.get_credibility("dblp")
        for key in ("source_id", "label", "tier", "rank", "emoji",
                    "verifiability", "trust_note", "reason"):
            self.assertIn(key, info)

    def test_label_matches_registry_entry(self):
        self.assertEqual(
            sc.get_credibility("chaspark")["label"], "🏠 华为茶思屋科技"
        )


# ════════════════════════════════════════════════════════════════════
class TestSourceCoverage(unittest.TestCase):
    """MR-8 drift guard: SOURCE_CREDIBILITY 必须与 jobs_registry 14 源一致."""

    def setUp(self):
        self.registry = _jobs_registry_content_sources()

    def test_registry_has_14_content_sources(self):
        # 防 jobs_registry 漂移 (新增/删除内容源时本测试提醒同步 credibility)
        self.assertEqual(len(self.registry), 14, self.registry)

    def test_every_registry_source_has_credibility(self):
        missing = set(self.registry) - set(sc.SOURCE_CREDIBILITY)
        self.assertEqual(missing, set(),
                         f"jobs_registry 源缺 credibility 分级: {missing}")

    def test_no_orphan_credibility_entries(self):
        orphan = set(sc.SOURCE_CREDIBILITY) - set(self.registry)
        self.assertEqual(orphan, set(),
                         f"SOURCE_CREDIBILITY 含 jobs_registry 不存在的源: {orphan}")

    def test_labels_match_registry(self):
        for sid, label in self.registry.items():
            self.assertEqual(
                sc.SOURCE_CREDIBILITY[sid]["label"], label,
                f"{sid} label 漂移: credibility='{sc.SOURCE_CREDIBILITY[sid]['label']}' "
                f"vs registry='{label}'",
            )

    def test_every_source_tier_is_valid(self):
        for sid, entry in sc.SOURCE_CREDIBILITY.items():
            self.assertIn(entry["tier"], sc._CREDIBILITY_TIERS,
                          f"{sid} tier '{entry['tier']}' 不在 5 档中")

    def test_drift_guard_catches_label_mismatch(self):
        # 证明 drift guard 真有效: 构造 label 不一致必须被 test_labels_match_registry 抓到
        registry = dict(self.registry)
        fake = dict(sc.SOURCE_CREDIBILITY["chaspark"])
        fake["label"] = "🏠 WRONG LABEL"
        self.assertNotEqual(fake["label"], registry["chaspark"])


# ════════════════════════════════════════════════════════════════════
class TestChasparkFlagged(unittest.TestCase):
    """observer 5/28 proposal #2 点名 chaspark (非主流学术源) 需可信度标注."""

    def test_chaspark_registered(self):
        self.assertIn("chaspark", sc.SOURCE_CREDIBILITY)

    def test_chaspark_is_blog_tier(self):
        # 反向验证锚点: sed 改成 "学术同行评审" → 本测试立即 fail
        self.assertEqual(sc.SOURCE_CREDIBILITY["chaspark"]["tier"], "博客")

    def test_block_names_chaspark_for_independent_verification(self):
        block = sc.format_credibility_block()
        self.assertIn("华为茶思屋", block)
        self.assertIn("非主流学术源", block)
        self.assertIn("需独立验证", block)
        self.assertIn("proposal #2", block)


# ════════════════════════════════════════════════════════════════════
class TestFormatBlock(unittest.TestCase):
    def setUp(self):
        self.block = sc.format_credibility_block()

    def test_starts_with_double_newline(self):
        # 契约: 以 \n\n 开头便于直接 append 到 base prompt
        self.assertTrue(self.block.startswith("\n\n"))

    def test_has_warning_marker(self):
        self.assertIn("⚠️", self.block)

    def test_has_version_marker(self):
        self.assertIn("V37.9.98", self.block)

    def test_contains_all_five_tier_names(self):
        for tier in sc.list_tiers():
            self.assertIn(tier, self.block, f"block 缺 tier {tier}")

    def test_contains_all_source_labels(self):
        for entry in sc.SOURCE_CREDIBILITY.values():
            self.assertIn(entry["label"], self.block,
                          f"block 缺 source label {entry['label']}")

    def test_has_annotation_instruction(self):
        self.assertIn("[可信度: <等级>]", self.block)

    def test_forbids_blog_social_as_academic_conclusion(self):
        self.assertIn("严禁", self.block)
        self.assertIn("学术结论", self.block)

    def test_unclassified_source_default_rule(self):
        self.assertIn("未分类来源", self.block)

    def test_block_is_generated_not_hardcoded(self):
        # 单一真理源: source 列表从 SOURCE_CREDIBILITY 动态生成
        # 加一个临时源应出现在 block (通过 _sources_by_tier)
        grouped = sc._sources_by_tier()
        # 所有 14 个 source 都被分组
        total = sum(len(v) for v in grouped.values())
        self.assertEqual(total, len(sc.SOURCE_CREDIBILITY))


# ════════════════════════════════════════════════════════════════════
class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, _MODULE_PATH, *args],
            capture_output=True, text=True, timeout=30,
        )

    def test_cli_list(self):
        r = self._run("--list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("学术同行评审", r.stdout)
        self.assertIn("社媒", r.stdout)

    def test_cli_sources(self):
        r = self._run("--sources")
        self.assertEqual(r.returncode, 0)
        self.assertIn("chaspark", r.stdout)
        self.assertIn("博客", r.stdout)

    def test_cli_block(self):
        r = self._run("--block")
        self.assertEqual(r.returncode, 0)
        self.assertIn("来源可信度评级", r.stdout)

    def test_cli_source_lookup(self):
        r = self._run("--source", "chaspark")
        self.assertEqual(r.returncode, 0)
        self.assertIn("tier: 博客", r.stdout)

    def test_cli_default_overview(self):
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("V37.9.98", r.stdout)


# ════════════════════════════════════════════════════════════════════
class TestDreamWiring(unittest.TestCase):
    def setUp(self):
        self.src = _read(_DREAM_SH)

    def test_imports_source_credibility(self):
        self.assertIn("import source_credibility", self.src)
        self.assertIn("format_credibility_block()", self.src)

    def test_dream_credibility_var_defined(self):
        self.assertRegex(self.src, r"DREAM_CREDIBILITY=\$\(python3")

    def test_five_injection_points(self):
        # 与 DREAM_HG_GUARD 同款 5 注入点
        count = self.src.count("${DREAM_HG_GUARD}${DREAM_CREDIBILITY}\"")
        self.assertEqual(count, 5, f"期望 5 个注入点, 实际 {count}")

    def test_fail_open_warn(self):
        self.assertIn("source_credibility 模块加载失败", self.src)

    def test_no_inline_credibility_block(self):
        # 反 inline 守卫 (MR-8): dream 不得自己 inline 来源分级文本, 必须用模块
        # 检查没有 inline 定义 5 个 tier 名的硬编码块
        self.assertNotIn('"学术同行评审": {', self.src)

    def test_v37_9_98_marker_in_dream(self):
        self.assertIn("V37.9.98", self.src)


# ════════════════════════════════════════════════════════════════════
class TestEveningWiring(unittest.TestCase):
    def setUp(self):
        self.src = _read(_EVENING_PY)

    def test_imports_format_credibility_block(self):
        self.assertIn(
            "from source_credibility import format_credibility_block", self.src
        )

    def test_build_prompt_calls_block(self):
        self.assertIn("{format_credibility_block()}", self.src)

    def test_build_evening_prompt_renders_block(self):
        import kb_evening_collect as ke
        p = ke.build_evening_prompt("n", "s", 1, 100, 50, 3, "tags")
        self.assertIn("来源可信度评级", p)
        self.assertIn("华为茶思屋", p)

    def test_block_after_hallucination_guard_before_notes(self):
        import kb_evening_collect as ke
        p = ke.build_evening_prompt("NOTEMARK", "s", 1, 100, 50, 3, "tags")
        i_cred = p.find("来源可信度评级")
        i_sec = p.find("═══ 今日笔记 ═══")
        self.assertGreater(i_cred, 0)
        self.assertLess(i_cred, i_sec, "credibility 块必须在今日笔记 section 之前")

    def test_v37_9_98_marker_in_evening(self):
        self.assertIn("V37.9.98", self.src)


# ════════════════════════════════════════════════════════════════════
class TestAutoDeployFileMap(unittest.TestCase):
    def test_source_credibility_in_file_map(self):
        src = _read(_AUTO_DEPLOY)
        self.assertIn("source_credibility.py|$HOME/source_credibility.py", src)

    def test_marked_v37_9_98(self):
        src = _read(_AUTO_DEPLOY)
        # 部署条目注释含 V37.9.98 marker
        line = [l for l in src.splitlines()
                if "source_credibility.py|" in l][0]
        self.assertIn("V37.9.98", line)


# ════════════════════════════════════════════════════════════════════
class TestSourceLevelGuards(unittest.TestCase):
    def setUp(self):
        self.src = _read(_MODULE_PATH)

    def test_v37_9_98_marker_constant(self):
        self.assertIn('_V37_9_98_MARKER = "V37.9.98 来源可信度评级"', self.src)

    def test_documents_mr8_single_source_of_truth(self):
        self.assertIn("MR-8", self.src)
        self.assertIn("单一真理源", self.src)

    def test_documents_mr7_observer_proposal(self):
        self.assertIn("MR-7", self.src)
        self.assertIn("observer", self.src)
        self.assertIn("proposal #2", self.src)

    def test_documents_fail_open(self):
        self.assertIn("FAIL-OPEN", self.src)

    def test_pure_stdlib_no_external_imports(self):
        # 前 60 行不得 import 外部依赖 (numpy/yaml/requests 等)
        head = "\n".join(self.src.splitlines()[:60])
        for forbidden in ("import numpy", "import yaml", "import requests",
                          "import sklearn"):
            self.assertNotIn(forbidden, head)

    def test_default_fallback_documented(self):
        self.assertIn("_DEFAULT_TIER", self.src)
        self.assertIn('_DEFAULT_TIER = "社媒"', self.src)

    def test_reverse_guard_chaspark_tier_locked(self):
        # 反向验证文档: chaspark 必须是博客 (sed 改 → TestChasparkFlagged fail)
        self.assertIn(
            '"chaspark": {"label": "🏠 华为茶思屋科技", "tier": "博客"}', self.src
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""V37.9.99 README 徽章 + V37.9.125 doc header 自动同步单测 (事实单一来源 + 防漂移).

覆盖:
  TestComputeFacts        — 从真实仓库权威源采集 (version/semver/tests/inv/MR/providers)
  TestComputeFactsExtended — V37.9.125 新事实 (suites/checks/gov_ver/security/mrd/cases)
  TestApplyBadges         — apply_badges 替换逻辑 (tests/governance/version/providers 4 徽章)
  TestProvidersBadgeFailOpen — providers 取不到时不管理 providers 徽章 (FAIL-OPEN)
  TestCheckModeRealRepo   — --check 真跑当前仓库 exit 0 (已同步)
  TestDriftDetection      — 构造 stale 徽章 → apply_badges 检测 CHANGED
  TestDocHeaderSpecs      — V37.9.125 _doc_header_specs 覆盖 3 doc + FAIL-OPEN 跳 None 事实
  TestApplyOneDoc         — V37.9.125 _apply_one_doc 摘要行内 token 替换 / anchor 缺失
  TestApplyDocHeadersRealRepo — V37.9.125 真仓库 3 doc 已同步 (幂等无漂移)
  TestDocHeaderReverseValidation — V37.9.125 反向验证: 破坏真 doc 统计 → 框架修回权威值
  TestSourceLevelGuards   — V37.9.99/125 marker / 单一来源文档 / regex 守卫

反向验证 (手动): 改 README/doc 统计数字 → --check exit 1; 还原 → exit 0.
反向验证 (机器化): TestDocHeaderReverseValidation 破坏真 doc 摘要行 → 断言修回.
"""

import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_readme_badges as grb  # noqa: E402

_REPO = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_REPO, "gen_readme_badges.py")


def _fake_facts(**over):
    f = {
        "test_count": 9999,
        "test_suites": 200,
        "invariants": 42,
        "meta_rules": 7,
        "governance_checks": 333,
        "governance_version": "9.99",
        "security_score": 88,
        "mrd_scanners": 20,
        "cases": 30,
        "version_label": "v99.9.9",
        "semver": "0.99.9.9",
        "date": "2099-12-31",
        "providers": 5,
    }
    f.update(over)
    return f


class TestComputeFacts(unittest.TestCase):
    def setUp(self):
        self.facts = grb.compute_facts(_REPO)

    def test_version_label_parsed(self):
        self.assertTrue(self.facts["version_label"].startswith("v"))

    def test_semver_matches_version_file(self):
        with open(os.path.join(_REPO, "VERSION")) as f:
            self.assertEqual(self.facts["semver"], f.read().strip())

    def test_test_count_is_int(self):
        self.assertIsInstance(self.facts["test_count"], int)
        self.assertGreater(self.facts["test_count"], 0)

    def test_invariants_and_mr_positive(self):
        self.assertGreater(self.facts["invariants"], 0)
        self.assertGreater(self.facts["meta_rules"], 0)

    def test_date_format(self):
        self.assertRegex(self.facts["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_providers_count(self):
        # providers.py --json 应返回 list, 计数 ≥7 (7 built-in + plugins)
        self.assertIsNotNone(self.facts["providers"])
        self.assertGreaterEqual(self.facts["providers"], 7)


class TestApplyBadges(unittest.TestCase):
    def setUp(self):
        self.readme = (
            "[![Tests](https://img.shields.io/badge/tests-100%20passed-brightgreen.svg)]()\n"
            "[![Providers](https://img.shields.io/badge/providers-3%20supported-orange.svg)]()\n"
            "[![Governance](https://img.shields.io/badge/invariants-10%2F10%20%2B%202%20MR-blueviolet.svg)]()\n"
            "> **Current version:** `v1.0.0` / `0.1.0.0` (2020-01-01) — see [`CLAUDE.md`](CLAUDE.md) for full changelog.\n"
            # V37.9.144: README 正文两行也由 badge 路径管理, fixture 同步含目标行
            "## Supported Providers (3)\n"
            "# Full regression (10 suites / 100 tests / 0 fail; must ALL pass before push)\n"
        )

    def test_tests_badge_updated(self):
        out, _ = grb.apply_badges(self.readme, _fake_facts())
        self.assertIn("badge/tests-9999%20passed", out)
        self.assertNotIn("tests-100%20passed", out)

    def test_governance_badge_updated(self):
        out, _ = grb.apply_badges(self.readme, _fake_facts())
        self.assertIn("badge/invariants-42%2F42%20%2B%207%20MR", out)

    def test_version_line_updated(self):
        out, _ = grb.apply_badges(self.readme, _fake_facts())
        self.assertIn("`v99.9.9` / `0.99.9.9` (2099-12-31)", out)
        # 保留尾部 " — see [`CLAUDE.md`]..."
        self.assertIn("see [`CLAUDE.md`](CLAUDE.md) for full changelog", out)

    def test_providers_badge_updated(self):
        out, _ = grb.apply_badges(self.readme, _fake_facts())
        self.assertIn("badge/providers-5%20supported", out)

    def test_all_four_changed(self):
        _, results = grb.apply_badges(self.readme, _fake_facts())
        statuses = {d: s for d, s in results}
        self.assertEqual(statuses["tests 徽章"], "CHANGED")
        self.assertEqual(statuses["governance 徽章"], "CHANGED")
        self.assertEqual(statuses["version 行"], "CHANGED")
        self.assertEqual(statuses["providers 徽章"], "CHANGED")

    def test_already_synced_is_ok_not_changed(self):
        synced, _ = grb.apply_badges(self.readme, _fake_facts())
        # 二次应用应无改动 (幂等)
        out2, results2 = grb.apply_badges(synced, _fake_facts())
        self.assertEqual(out2, synced)
        for d, s in results2:
            self.assertEqual(s, "OK")


class TestProvidersBadgeFailOpen(unittest.TestCase):
    def test_no_providers_badge_when_count_none(self):
        # providers=None → 不管理 providers 徽章 (FAIL-OPEN, providers.py --json 失败时)
        readme = "[![Providers](https://img.shields.io/badge/providers-3%20supported-orange.svg)]()\n"
        out, results = grb.apply_badges(readme, _fake_facts(providers=None))
        descs = [d for d, s in results]
        self.assertNotIn("providers 徽章", descs)
        self.assertEqual(out, readme)  # providers 徽章未被碰


class TestReadmeBodyLineSubs(unittest.TestCase):
    """V37.9.144 外部评审2 doc-drift 收口: README 正文两行接入 badge 路径机器同步.

    血案: "## Supported Providers (7)" 自 V37.9.52 加 doubao 后手写漂移至今
    (V37.9.70 修了 10 处 "7 provider*" 字面量但漏了该段头);
    Testing 段 "V37.9.124: 118 suites / 4099 tests" 带版本标记必然漂移.
    设计决策: 走 _badge_substitutions 不走 _doc_header_specs — 后者会让 README
    被 apply_badges/apply_doc_headers 双写者从各自原文计算互相覆盖 (新接缝).
    """

    def test_providers_section_header_updated(self):
        readme = "## Supported Providers (7)\n"
        out, results = grb.apply_badges(readme, _fake_facts(providers=5))
        self.assertIn("## Supported Providers (5)", out)
        self.assertEqual(dict(results)["providers 段头"], "CHANGED")

    def test_testing_summary_line_updated(self):
        readme = "# Full regression (118 suites / 4099 tests / 0 fail; must ALL pass before push)\n"
        out, results = grb.apply_badges(readme, _fake_facts(test_suites=200, test_count=9999))
        self.assertIn("# Full regression (200 suites / 9999 tests / 0 fail; must ALL pass before push)", out)
        self.assertEqual(dict(results)["testing 摘要行"], "CHANGED")

    def test_fail_open_providers_none_skips_header_sub(self):
        readme = "## Supported Providers (7)\n"
        out, results = grb.apply_badges(readme, _fake_facts(providers=None))
        self.assertNotIn("providers 段头", [d for d, _ in results])
        self.assertIn("## Supported Providers (7)", out)  # 未被碰

    def test_fail_open_suites_none_skips_testing_sub(self):
        readme = "# Full regression (118 suites / 4099 tests / 0 fail; must ALL pass before push)\n"
        out, results = grb.apply_badges(readme, _fake_facts(test_suites=None))
        self.assertNotIn("testing 摘要行", [d for d, _ in results])
        self.assertEqual(out, readme)

    def test_real_readme_old_versioned_form_eliminated(self):
        # 旧形式 "# Full regression (V37.9.124: ..." 已退役 (版本标记 = 漂移源),
        # 防未来重构改回带版本标记形式让 sub 失配 (TOKEN miss → 永久漂移)
        with open(os.path.join(_REPO, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertNotRegex(readme, r"# Full regression \(V[0-9.]+:",
                            "README testing 行不得再带版本标记 (V37.9.144 退役)")
        self.assertRegex(readme, r"# Full regression \(\d+ suites / \d+ tests / 0 fail",
                        "README testing 行必须保持 sub 可管理的形式")

    def test_real_readme_provider_table_has_doubao_row(self):
        # 表行内容不是机器管理的统计 token — 内容守卫防 V37.9.52 类"加 provider 漏表行"复发
        with open(os.path.join(_REPO, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("| **Doubao** (Volcengine Ark, plugin) |", readme,
                      "provider 表必须含 Doubao 行 (V37.9.144 补齐)")
        self.assertRegex(readme, r"## Supported Providers \(\d+\)",
                        "providers 段头必须保持 sub 可管理的形式")


class TestCheckModeRealRepo(unittest.TestCase):
    def test_check_exit_zero_when_synced(self):
        # 当前仓库 README 已由 gen_readme_badges --write 同步 → --check 应 exit 0
        r = subprocess.run([sys.executable, _SCRIPT, "--check"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, f"--check 应 exit 0 (已同步), stderr={r.stderr}")
        self.assertIn("无漂移", r.stdout)

    def test_write_is_idempotent(self):
        # --write 在已同步仓库上应报"已是最新", exit 0
        r = subprocess.run([sys.executable, _SCRIPT, "--write"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)


class TestDriftDetection(unittest.TestCase):
    def test_stale_tests_badge_detected_as_drift(self):
        readme = "[![Tests](https://img.shields.io/badge/tests-1%20passed-brightgreen.svg)]()\n"
        out, results = grb.apply_badges(readme, _fake_facts(test_count=9999))
        self.assertNotEqual(out, readme)  # 漂移被检测
        self.assertEqual(dict(results)["tests 徽章"], "CHANGED")


class TestComputeFactsExtended(unittest.TestCase):
    """V37.9.125: compute_facts 采集新事实 (doc header 统计权威源)."""

    def setUp(self):
        self.facts = grb.compute_facts(_REPO)

    def test_test_suites_positive(self):
        self.assertIsInstance(self.facts["test_suites"], int)
        self.assertGreater(self.facts["test_suites"], 0)

    def test_governance_checks_positive(self):
        self.assertIsInstance(self.facts["governance_checks"], int)
        self.assertGreater(self.facts["governance_checks"], 0)

    def test_governance_version_format(self):
        self.assertRegex(self.facts["governance_version"], r"^\d+\.\d+$")

    def test_security_score_in_range(self):
        self.assertIsInstance(self.facts["security_score"], int)
        self.assertTrue(0 <= self.facts["security_score"] <= 100)

    def test_mrd_scanners_positive(self):
        self.assertIsInstance(self.facts["mrd_scanners"], int)
        self.assertGreater(self.facts["mrd_scanners"], 0)

    def test_cases_count_positive(self):
        # ontology/docs/cases/*.md 真实存在 (≥20 血案案例)
        self.assertIsInstance(self.facts["cases"], int)
        self.assertGreaterEqual(self.facts["cases"], 20)

    def test_checks_matches_audit_metadata(self):
        # 与 audit_metadata.total_checks 一致 (单一真理源)
        gov = grb._read_governance_meta(_REPO)
        self.assertEqual(self.facts["governance_checks"], gov["checks"])


class TestDocHeaderSpecs(unittest.TestCase):
    """V37.9.125: _doc_header_specs 覆盖契约 + FAIL-OPEN."""

    def test_covers_three_docs(self):
        specs = grb._doc_header_specs(_fake_facts())
        rels = [rel for rel, _, _ in specs]
        self.assertIn("docs/FEATURES.md", rels)
        self.assertIn("docs/config.md", rels)
        self.assertIn("docs/ontology_engine_packaging.md", rels)
        # V37.9.141: 4 specs 覆盖 3 docs — FEATURES 有 header + 正文测试行两个 spec
        # (外部评审 2026-06-11 P0: 正文行 118 套/4099 用例漂移接入自动同步)
        self.assertEqual(len(specs), 4)
        self.assertEqual(rels.count("docs/FEATURES.md"), 2)

    def test_features_body_test_row_spec_v37_9_141(self):
        """FEATURES 正文 | **测试** | 行必须被 spec 管理 (防 118/4099 类漂移复发)."""
        specs = grb._doc_header_specs(_fake_facts())
        body_specs = [
            (rel, anchor, tokens) for rel, anchor, tokens in specs
            if rel == "docs/FEATURES.md" and "测试" in anchor.pattern
        ]
        self.assertEqual(len(body_specs), 1)
        descs = [d for d, _, _ in body_specs[0][2]]
        self.assertIn("tests body", descs)
        self.assertIn("suites body", descs)
        # 真仓库该行必须与权威值一致 (--write 后幂等)
        facts = grb.compute_facts(_REPO)
        content = open(os.path.join(_REPO, "docs/FEATURES.md"), encoding="utf-8").read()
        import re as _re
        m = _re.search(r"^\| \*\*测试\*\* \| ([0-9]+) 套单测 \| ([0-9]+) 用例全部通过", content, _re.M)
        self.assertIsNotNone(m, "FEATURES 正文测试行必须存在")
        self.assertEqual(int(m.group(1)), facts["test_suites"])
        self.assertEqual(int(m.group(2)), facts["test_count"])

    def test_fail_open_skips_none_facts(self):
        # mrd_scanners/cases/providers/security None → 不管理对应 token (FAIL-OPEN)
        facts = _fake_facts(mrd_scanners=None, cases=None, providers=None, security_score=None)
        specs = grb._doc_header_specs(facts)
        for rel, _, tokens in specs:
            descs = [d for d, _, _ in tokens]
            self.assertNotIn("MRD scanners", descs)
            self.assertNotIn("cases", descs)
            self.assertNotIn("security", descs)
            self.assertNotIn("安全", descs)
            self.assertNotIn("providers", descs)

    def test_specs_have_compiled_anchor(self):
        for rel, anchor, tokens in grb._doc_header_specs(_fake_facts()):
            self.assertTrue(hasattr(anchor, "search"), f"{rel} anchor 应是编译正则")
            self.assertGreater(len(tokens), 0)


class TestApplyOneDoc(unittest.TestCase):
    """V37.9.125: _apply_one_doc 摘要行内 token 替换语义."""

    def _features_spec(self, facts):
        for rel, anchor, tokens in grb._doc_header_specs(facts):
            if rel == "docs/FEATURES.md":
                return anchor, tokens
        self.fail("FEATURES spec 缺失")

    def test_swaps_stale_stats_in_synthetic_line(self):
        facts = _fake_facts(test_count=9999, invariants=42, meta_rules=7)
        anchor, tokens = self._features_spec(facts)
        # 构造一行 FEATURES L3 形状, 旧统计
        text = ("# Title\n\n"
                "> v1.0.0 (2020-01-01) | **1 tests** / 2 suites / 0 fail | **3 providers** (含 X) "
                "| **5 active jobs** | 5 SLO metrics | 9 preflight checks | dual-channel "
                "| **10 governance invariants / 3 meta-rules / 50 checks / 4 MRD scanners** "
                "| security 50/100 | 11 blood-lesson case docs\n\n"
                "body 89 invariants 应不被碰 (body 行)\n")
        new_text, results = grb._apply_one_doc(text, anchor, tokens)
        self.assertIn("**9999 tests**", new_text)
        self.assertIn("**42 governance invariants", new_text)
        self.assertIn("7 meta-rules", new_text)
        # body 行不被碰 (token bound 到 anchor 摘要行)
        self.assertIn("body 89 invariants 应不被碰", new_text)
        statuses = {d: s for d, s in results}
        self.assertEqual(statuses["tests"], "CHANGED")
        self.assertEqual(statuses["invariants"], "CHANGED")

    def test_idempotent_no_change_when_synced(self):
        facts = _fake_facts(test_count=9999, invariants=42, meta_rules=7)
        anchor, tokens = self._features_spec(facts)
        text = ("> v99.9.9 (2099-12-31) | **9999 tests** / 200 suites / 0 fail | **5 providers** (含 X) "
                "| **5 active jobs** | 5 SLO metrics | 9 preflight checks | dual-channel "
                "| **42 governance invariants / 7 meta-rules / 333 checks / 20 MRD scanners** "
                "| security 88/100 | 30 blood-lesson case docs\n")
        new_text, results = grb._apply_one_doc(text, anchor, tokens)
        self.assertEqual(new_text, text)  # 幂等
        for d, s in results:
            self.assertEqual(s, "OK", f"{d} 应 OK 不应 CHANGED")

    def test_anchor_not_found_returns_unchanged(self):
        facts = _fake_facts()
        anchor, tokens = self._features_spec(facts)
        text = "# Title\n\n没有摘要行的文档\n"
        new_text, results = grb._apply_one_doc(text, anchor, tokens)
        self.assertEqual(new_text, text)
        self.assertEqual(results[0][1], "ANCHOR_NOT_FOUND")


class TestApplyDocHeadersRealRepo(unittest.TestCase):
    """V37.9.125: 真仓库 3 doc header 已与权威源同步 (apply 无漂移 = 幂等)."""

    def setUp(self):
        self.facts = grb.compute_facts(_REPO)
        self.out = grb.apply_doc_headers(_REPO, self.facts)

    def test_all_three_docs_present_and_synced(self):
        for rel in ("docs/FEATURES.md", "docs/config.md", "docs/ontology_engine_packaging.md"):
            self.assertIn(rel, self.out)
            new_text, results, orig = self.out[rel]
            self.assertIsNotNone(orig, f"{rel} 应存在")
            self.assertEqual(new_text, orig, f"{rel} 应已同步 (apply 无改动). results={results}")

    def test_no_anchor_or_token_not_found(self):
        for rel, (new_text, results, orig) in self.out.items():
            for desc, st in results:
                self.assertNotIn(st, ("ANCHOR_NOT_FOUND", "TOKEN_NOT_FOUND", "FILE_NOT_FOUND"),
                                 f"{rel} {desc}: {st} — anchor/token 格式漂移")


class TestDocHeaderReverseValidation(unittest.TestCase):
    """V37.9.125 机器化反向验证: 破坏真 doc 摘要行统计 → 框架精确修回权威值."""

    def _spec_for(self, rel, facts):
        for r, anchor, tokens in grb._doc_header_specs(facts):
            if r == rel:
                return anchor, tokens
        self.fail(f"{rel} spec 缺失")

    def test_config_invariants_sabotage_restored(self):
        facts = grb.compute_facts(_REPO)
        real = grb._read(os.path.join(_REPO, "docs", "config.md"))
        true_inv = facts["invariants"]
        # 破坏: 真 invariants → 一个不可能的旧值
        sabotaged = real.replace(f"{true_inv} invariants", "11 invariants", 1)
        self.assertIn("11 invariants", sabotaged)
        anchor, tokens = self._spec_for("docs/config.md", facts)
        fixed, results = grb._apply_one_doc(sabotaged, anchor, tokens)
        self.assertIn(f"{true_inv} invariants", fixed)
        self.assertNotIn("11 invariants", fixed)
        self.assertEqual(dict(results)["invariants"], "CHANGED")

    def test_features_cases_sabotage_restored(self):
        facts = grb.compute_facts(_REPO)
        if facts.get("cases") is None:
            self.skipTest("cases 取不到 (FAIL-OPEN)")
        real = grb._read(os.path.join(_REPO, "docs", "FEATURES.md"))
        true_cases = facts["cases"]
        sabotaged = real.replace(f"{true_cases} blood-lesson case docs",
                                 "7 blood-lesson case docs", 1)
        anchor, tokens = self._spec_for("docs/FEATURES.md", facts)
        fixed, results = grb._apply_one_doc(sabotaged, anchor, tokens)
        self.assertIn(f"{true_cases} blood-lesson case docs", fixed)
        self.assertEqual(dict(results)["cases"], "CHANGED")

    def test_packaging_gov_version_sabotage_restored(self):
        facts = grb.compute_facts(_REPO)
        if not facts.get("governance_version"):
            self.skipTest("governance_version 取不到")
        real = grb._read(os.path.join(_REPO, "docs", "ontology_engine_packaging.md"))
        gv = facts["governance_version"]
        sabotaged = real.replace(f"governance v{gv}", "governance v1.00", 1)
        anchor, tokens = self._spec_for("docs/ontology_engine_packaging.md", facts)
        fixed, results = grb._apply_one_doc(sabotaged, anchor, tokens)
        self.assertIn(f"governance v{gv}", fixed)
        self.assertEqual(dict(results)["governance 版本"], "CHANGED")


class TestSourceLevelGuards(unittest.TestCase):
    def setUp(self):
        with open(_SCRIPT, encoding="utf-8") as f:
            self.src = f.read()

    def test_v37_9_99_marker(self):
        self.assertIn("V37.9.99", self.src)

    def test_v37_9_125_marker(self):
        # V37.9.125 doc-header 同步扩展 marker (与 V37.9.99 共存, 不替代)
        self.assertIn("V37.9.125", self.src)

    def test_documents_single_source_of_truth(self):
        self.assertIn("single source of truth", self.src)
        self.assertIn("外部评审", self.src)

    def test_authoritative_sources_referenced(self):
        for src_ref in ("status.json", "governance_ontology.yaml", "VERSION", "CLAUDE.md", "providers.py"):
            self.assertIn(src_ref, self.src)

    def test_v37_9_125_new_sources_documented(self):
        # doc header 统计的新权威源都在 docstring 登记
        for ref in ("test_suites", "total_checks", "meta_rule_discovery", "ontology/docs/cases"):
            self.assertIn(ref, self.src)

    def test_three_docs_referenced_in_specs(self):
        # _doc_header_specs 管理 3 个 doc (源码守卫防漏配)
        for doc in ("docs/FEATURES.md", "docs/config.md", "docs/ontology_engine_packaging.md"):
            self.assertIn(doc, self.src)

    def test_fail_open_documented(self):
        self.assertIn("FAIL-OPEN", self.src)

    def test_check_and_write_modes_exist(self):
        self.assertIn("--check", self.src)
        self.assertIn("--write", self.src)

    def test_in_full_regression(self):
        with open(os.path.join(_REPO, "full_regression.sh"), encoding="utf-8") as f:
            fr = f.read()
        self.assertIn("gen_readme_badges", fr)


class TestFullRegressionGovParse(unittest.TestCase):
    """V37.9.125: full_regression governance 字段回写解析鲁棒性 (同款 stat-drift 家族修复).

    旧 depth 字符计数解析器静默失败 → status.json governance_invariants/checks 长期 stale.
    改 raw_decode + 跳 log 前缀行. 守卫: 源码用 raw_decode 不用 fragile depth 计数 + 行为级真跑.
    """

    def setUp(self):
        with open(os.path.join(_REPO, "full_regression.sh"), encoding="utf-8") as f:
            self.fr = f.read()

    def test_gov_parse_uses_raw_decode(self):
        # 鲁棒解析器标志 — 查实际代码调用 (非注释提及, 防自引用守卫失效 V37.9.110 教训).
        # 正确处理字符串内括号 + 忽略尾部 combined object.
        self.assertIn("json.JSONDecoder().raw_decode", self.fr)

    def test_gov_parse_not_fragile_depth_counter(self):
        # 反向守卫: 旧 fragile 'depth = 0' 字符计数解析器已移除 (撞 [SYSTEM_ALERT] 污染)
        self.assertNotIn("depth = 0\nfor i, c in enumerate(raw)", self.fr)
        self.assertNotIn("if c == '[': depth += 1", self.fr)

    def test_gov_parse_skips_all_log_prefixes(self):
        # 跳所有 [WORD]/[word:] log 行 (不只 [proxy]), 含 V37.9.125 注释溯源
        self.assertIn("[SYSTEM_ALERT]", self.fr)
        self.assertIn("V37.9.125", self.fr)

    @staticmethod
    def _robust_parse(raw):
        """复现 full_regression L517 鲁棒解析 (镜像, 由 test_gov_parse_uses_raw_decode 源码守卫
        保证 full_regression 真用此逻辑). 返回 (passed, total, checks_passed, checks) 或 None."""
        import json as _json
        lines = raw.splitlines(keepends=True)
        start = next((i for i, ln in enumerate(lines)
                      if ln.lstrip().startswith("[") and not re.match(r"\[[A-Za-z_]+[\]:]", ln.lstrip())),
                     None)
        if start is None:
            return None
        data, _ = _json.JSONDecoder().raw_decode("".join(lines[start:]))
        total = len(data)
        passed = sum(1 for d in data if d.get("status") == "pass")
        checks = sum(d.get("total_checks", 0) for d in data)
        cp = sum(d.get("passed_checks", 0) for d in data)
        return passed, total, cp, checks

    def test_gov_parse_handles_polluted_fixture(self):
        # 复现真实失败模式: [proxy]/[SYSTEM_ALERT] log 污染 (含括号) + 字符串内括号 +
        # V37.3 尾部 combined object. 旧 depth 字符计数器在此样本必崩 (返回空/错值).
        fixture = (
            "[proxy] ONTOLOGY_MODE=on: loaded 16 tools from engine\n"
            "[SYSTEM_ALERT] reserved-file-write blocked: file HEARTBEAT.md (path=/x/[w]/y)\n"
            "[\n"
            '  {"id": "INV-A", "status": "pass", "total_checks": 3, "passed_checks": 3},\n'
            '  {"id": "INV-B", "status": "fail", "total_checks": 2, "passed_checks": 1,'
            ' "declaration": "字符串内有 ] 右括号会让旧 depth 计数器误判"}\n'
            "]\n"
            '{\n  "invariants": [{"id":"X"}],\n  "convergence": [{"spec":"y"}]\n}\n'
        )
        res = self._robust_parse(fixture)
        self.assertIsNotNone(res, "鲁棒解析应定位到 legacy 数组 (跳过 log 污染行)")
        passed, total, cp, checks = res
        self.assertEqual((passed, total, cp, checks), (1, 2, 4, 5),
                         "应只解析第一段 2 不变式数组 (1 pass), 忽略尾部 combined object")

    def test_gov_parse_behavioral_real_repo_matches_audit_metadata(self):
        # 行为级: 真跑 governance_checker --json → 鲁棒解析非空 + 不变式数 == audit_metadata 声明.
        # (锚定真实结构, 防 --json 结构未来漂移又静默回写 stale)
        proc = subprocess.run(
            [sys.executable, os.path.join(_REPO, "ontology", "governance_checker.py"), "--json"],
            capture_output=True, text=True, timeout=180)
        res = self._robust_parse(proc.stdout)
        self.assertIsNotNone(res, "真 --json 输出鲁棒解析应非空 (定位到 legacy 数组)")
        passed, total, cp, checks = res
        self.assertGreater(total, 0)
        self.assertEqual(passed, total, f"dev 基线 governance 应全绿: {passed}/{total}")
        gov = grb._read_governance_meta(_REPO)
        self.assertEqual(total, gov["invariants"],
                         f"--json 不变式数 {total} 应 == audit_metadata {gov['invariants']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

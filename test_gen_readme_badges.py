#!/usr/bin/env python3
"""V37.9.99 README 徽章自动生成单测 (外部评审 P0 — 事实单一来源 + 防漂移).

覆盖:
  TestComputeFacts        — 从真实仓库权威源采集 (version/semver/tests/inv/MR/providers)
  TestApplyBadges         — apply_badges 替换逻辑 (tests/governance/version/providers 4 徽章)
  TestProvidersBadgeFailOpen — providers 取不到时不管理 providers 徽章 (FAIL-OPEN)
  TestCheckModeRealRepo   — --check 真跑当前仓库 exit 0 (已同步)
  TestDriftDetection      — 构造 stale 徽章 → apply_badges 检测 CHANGED
  TestSourceLevelGuards   — V37.9.99 marker / 单一来源文档 / regex 守卫

反向验证 (手动): 改 README tests 徽章数字 → --check exit 1; 还原 → exit 0.
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
        "invariants": 42,
        "meta_rules": 7,
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


class TestSourceLevelGuards(unittest.TestCase):
    def setUp(self):
        with open(_SCRIPT, encoding="utf-8") as f:
            self.src = f.read()

    def test_v37_9_99_marker(self):
        self.assertIn("V37.9.99", self.src)

    def test_documents_single_source_of_truth(self):
        self.assertIn("single source of truth", self.src)
        self.assertIn("外部评审", self.src)

    def test_authoritative_sources_referenced(self):
        for src_ref in ("status.json", "governance_ontology.yaml", "VERSION", "CLAUDE.md", "providers.py"):
            self.assertIn(src_ref, self.src)

    def test_check_and_write_modes_exist(self):
        self.assertIn("--check", self.src)
        self.assertIn("--write", self.src)

    def test_in_full_regression(self):
        with open(os.path.join(_REPO, "full_regression.sh"), encoding="utf-8") as f:
            fr = f.read()
        self.assertIn("gen_readme_badges", fr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

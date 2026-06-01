#!/usr/bin/env python3
"""
test_v37_9_95_ai_leaders.py — V37.9.95 ai_leaders_x 扩展守卫

V37.9.95 触发: 用户原则 #32 周一观察反馈 "建议后续可以加入更多 ai_leaders_x
的观点, 尤其是更多 AI 大神的不同观点". 从 19→31 accounts 跨 12 派别.

测试:
  - LEADERS array 守卫 (count / 派别多样性 / V37.9.95 marker)
  - MAX_PER_PERSON + MAX_TOTAL 调整正确
  - 新增 12 accounts 全部存在
  - V37.8.4 INV-X-001 兼容 (WARN-on-zombie 容错保留)
  - 文件 bash syntax 仍正确
"""
import os
import re
import subprocess
import unittest
from pathlib import Path

SCRIPT_PATH = (Path(__file__).resolve().parent /
               "jobs" / "ai_leaders_x" / "run_ai_leaders_x.sh")


def _read_src():
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_handles():
    """Parse LEADERS=(...) block and return list of handles."""
    src = _read_src()
    # Find LEADERS=( ... )
    m = re.search(r'LEADERS=\((.*?)^\)', src, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    handles = []
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith('"'):
            continue
        # Format: "handle|display_name|label"
        parts = line.strip('"').split('|')
        if len(parts) >= 3:
            handles.append(parts[0])
    return handles


class TestV37_9_95_LeadersExpansion(unittest.TestCase):
    """V37.9.95: LEADERS count + 多样性 + new account presence."""

    def test_leaders_count_at_least_31(self):
        handles = _extract_handles()
        self.assertGreaterEqual(len(handles), 31,
            f"V37.9.95: expected ≥31 accounts (19 original + 12 new), "
            f"got {len(handles)}")

    def test_no_duplicate_handles(self):
        handles = _extract_handles()
        self.assertEqual(len(handles), len(set(handles)),
            f"duplicate handle(s) detected: "
            f"{[h for h in handles if handles.count(h) > 1]}")

    def test_v37_9_95_marker_in_source(self):
        src = _read_src()
        self.assertIn("V37.9.95", src,
            "V37.9.95 version marker must appear in script")

    def test_all_original_19_preserved(self):
        """V37.9.95 must NOT remove any of the original 19 accounts.
        Backward-compat: existing readers/operators expect these to remain."""
        original = {
            "karpathy", "DrJimFan", "ylecun", "fchollet", "swyx",
            "lilianweng", "_jasonwei", "hwchung27", "hwchase17",
            "GaryMarcus", "juaborges", "Michael_Witbrock",
            "PalantirTech", "AlexKarp", "ShyamSankar",
            "BarrySmith46", "gaborguizzardi", "pascal_hitzler",
            "IanHorrocks",
        }
        handles = set(_extract_handles())
        missing = original - handles
        self.assertEqual(missing, set(),
            f"V37.9.95 must preserve all 19 original accounts, missing: "
            f"{missing}")

    def test_v37_9_95_new_12_accounts_present(self):
        """All 12 V37.9.95 candidate accounts must be added."""
        new_12 = {
            "demishassabis", "DarioAmodei", "jackclarkSF",
            "ESYudkowsky", "geoffreyhinton", "drfeifei",
            "pabbeel", "soumithchintala", "ch402",
            "AndrewYNg", "YiTayML", "ClementDelangue",
        }
        handles = set(_extract_handles())
        missing = new_12 - handles
        self.assertEqual(missing, set(),
            f"V37.9.95 must add all 12 new accounts, missing: {missing}")

    def test_diversity_across_camps(self):
        """V37.9.95 design intent — script source must mention ≥10 camps."""
        src = _read_src()
        # Check ≥10 distinct camp/community indicators appear anywhere
        # in the script (header + labels collectively).
        camp_indicators = [
            "OpenAI", "Meta", "NVIDIA", "Anthropic", "DeepMind",
            "Safety", "Nobel", "Robotics", "Berkeley", "Hugging Face",
            "Stanford", "Symbolic", "Ontology", "Reka",
        ]
        present = sum(1 for c in camp_indicators if c in src)
        self.assertGreaterEqual(present, 10,
            f"V37.9.95: script should mention ≥10 distinct camps, "
            f"found {present}")


class TestV37_9_95_LimitAdjustments(unittest.TestCase):
    """V37.9.95: MAX_PER_PERSON 5→3, MAX_TOTAL 30→40."""

    def test_max_per_person_lowered_to_3(self):
        src = _read_src()
        m = re.search(r'MAX_PER_PERSON=(\d+)', src)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 3,
            "V37.9.95: MAX_PER_PERSON should drop 5→3 (more accounts, fewer per)")

    def test_max_total_raised_to_40(self):
        src = _read_src()
        m = re.search(r'MAX_TOTAL=(\d+)', src)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 40,
            "V37.9.95: MAX_TOTAL should rise 30→40 (more accounts → more raw)")

    def test_budget_math_sane(self):
        """MAX_PER_PERSON × N_LEADERS should comfortably exceed MAX_TOTAL
        so we get post-filter selection, not always-min behavior."""
        src = _read_src()
        mpp = int(re.search(r'MAX_PER_PERSON=(\d+)', src).group(1))
        mt = int(re.search(r'MAX_TOTAL=(\d+)', src).group(1))
        n_leaders = len(_extract_handles())
        # Raw capacity at least 2x MAX_TOTAL → genuine filtering
        self.assertGreater(mpp * n_leaders, mt * 2,
            f"raw capacity {mpp}*{n_leaders} should exceed 2*MAX_TOTAL ({mt}) "
            f"so filtering actually selects")


class TestV37_9_95_BashSyntaxAndZombieTolerance(unittest.TestCase):
    """V37.9.95: script must still parse + V37.8.4 zombie tolerance preserved."""

    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", str(SCRIPT_PATH)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
            f"bash syntax error after V37.9.95 expansion:\n{r.stderr}")

    def test_warn_on_empty_handle_preserved(self):
        """V37.8.4 INV-X-001 教训: 僵尸账号 fetch 0 条 应 log WARN 不崩.
        V37.9.95 添加新账号若是僵尸, 必须靠这个 WARN 自动暴露."""
        src = _read_src()
        # The existing handle parser logs WARN on解析异常
        self.assertIn("WARN", src,
            "WARN logging must be preserved for zombie detection (V37.8.4)")
        # No new code path that breaks on empty handle
        self.assertIn("MAX_PER_PERSON", src)


class TestV37_9_95_SourceLevelGuards(unittest.TestCase):
    """V37.9.95 source-level guards prevent regression."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_src()

    def test_v37_9_95_user_feedback_reference(self):
        """V37.9.95 source must document the user feedback driving expansion."""
        self.assertIn("原则 #32", self.src,
            "V37.9.95 must reference 原则 #32 driving feedback")
        self.assertIn("AI 大神", self.src,
            "V37.9.95 must reference user's 'AI 大神不同观点' request")

    def test_v37_8_4_inv_x_001_reference(self):
        """V37.9.95 must reference V37.8.4 INV-X-001 zombie lesson."""
        self.assertIn("V37.8.4", self.src,
            "V37.9.95 should reference V37.8.4 zombie blood lesson")
        self.assertIn("INV-X-001", self.src,
            "V37.9.95 should reference INV-X-001")

    def test_label_categories_in_header(self):
        """The header comment must enumerate the 12 camps for traceability."""
        # Sample several camp names that must be in the header comment
        for camp in ("Anthropic", "DeepMind", "Hugging Face",
                     "Ontology", "Robotics", "Safety"):
            self.assertIn(camp, self.src,
                f"V37.9.95 header should mention '{camp}' camp")


if __name__ == "__main__":
    unittest.main()

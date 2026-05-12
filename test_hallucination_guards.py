#!/usr/bin/env python3
"""V37.9.57 test_hallucination_guards.py — 公共反幻觉守卫模板单测.

测试层次:
  1. 5 档守卫文本结构 (LEVEL_N 累积包含 LEVEL_(N-1) 内容)
  2. get_guard() API 契约 (默认 / 未知值 / None / 非 string fallback)
  3. V37.9.56-hotfix3 血案具体字面禁令在 LEVEL_4+ 完整出现
  4. Opportunity Radar 信号源契约在 LEVEL_5 出现
  5. CLI 接口 (--list / --blocked-phrases / --level)
  6. 源码级守卫 (反向验证 sabotage 立即 fail)
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

import hallucination_guards as hg

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class TestGuardLevels(unittest.TestCase):
    """5 档守卫存在性 + 累积式结构契约."""

    def test_5_levels_registered(self):
        """必须 5 档: MINIMAL / STANDARD / STRICT / PROJECT_AWARE / RADAR_AWARE."""
        levels = hg.list_levels()
        self.assertEqual(len(levels), 5)
        expected = ["LEVEL_1_MINIMAL", "LEVEL_2_STANDARD", "LEVEL_3_STRICT",
                    "LEVEL_4_PROJECT_AWARE", "LEVEL_5_RADAR_AWARE"]
        self.assertEqual(levels, expected)

    def test_all_levels_non_empty(self):
        """每档守卫必须非空且以 \\n\\n 开头便于 append."""
        for lv in hg.list_levels():
            text = hg.get_guard(lv)
            self.assertTrue(text.startswith("\n\n"),
                f"{lv} 必须以 \\n\\n 开头便于直接 append 到 prompt")
            self.assertGreater(len(text), 50, f"{lv} 守卫文本太短")

    def test_warning_emoji_in_each_level(self):
        """每档必须含 ⚠️ 让 LLM 注意力高位识别."""
        for lv in hg.list_levels():
            self.assertIn("⚠️", hg.get_guard(lv),
                f"{lv} 必须含 ⚠️ 标记")

    def test_v37_9_57_marker_in_each_level(self):
        """每档必须含 V37.9.57 marker (防漂移)."""
        for lv in hg.list_levels():
            self.assertIn("V37.9.57", hg.get_guard(lv),
                f"{lv} 必须含 V37.9.57 marker")

    def test_level_2_contains_level_1_core(self):
        """LEVEL_2 累积式: 含 LEVEL_1 的'严禁虚构'核心."""
        l2 = hg.get_guard("LEVEL_2_STANDARD")
        self.assertIn("严禁虚构", l2)
        # 同时含 LEVEL_2 独有
        self.assertIn("来源标签", l2)

    def test_level_3_contains_level_2_core(self):
        l3 = hg.get_guard("LEVEL_3_STRICT")
        self.assertIn("严禁虚构", l3)
        self.assertIn("来源标签", l3)
        # LEVEL_3 独有: 反链式推论
        self.assertIn("反链式推论", l3)

    def test_level_4_contains_level_3_core(self):
        l4 = hg.get_guard("LEVEL_4_PROJECT_AWARE")
        self.assertIn("严禁虚构", l4)
        self.assertIn("反链式推论", l4)
        # LEVEL_4 独有: V37.9.56-hotfix3 血案禁令
        self.assertIn("OpenClaw 社区发布", l4)
        self.assertIn("V37.9.56-hotfix3", l4)

    def test_level_5_contains_level_4_core(self):
        l5 = hg.get_guard("LEVEL_5_RADAR_AWARE")
        self.assertIn("严禁虚构", l5)
        self.assertIn("反链式推论", l5)
        self.assertIn("OpenClaw 社区发布", l5)
        # LEVEL_5 独有: Opportunity Radar 信号源契约
        self.assertIn("Opportunity Radar", l5)

    def test_level_1_does_not_contain_higher_features(self):
        """反向: LEVEL_1 不应含高 LEVEL 特性."""
        l1 = hg.get_guard("LEVEL_1_MINIMAL")
        self.assertNotIn("反链式推论", l1)
        self.assertNotIn("OpenClaw", l1)
        self.assertNotIn("Opportunity Radar", l1)


class TestGetGuardAPI(unittest.TestCase):
    """get_guard() API 契约."""

    def test_default_param_returns_level_3(self):
        """默认调用 (无参数) 返回 LEVEL_3 (安全中位数)."""
        self.assertEqual(hg.get_guard(), hg.get_guard("LEVEL_3_STRICT"))

    def test_unknown_level_fallback_to_level_3(self):
        """未知 level 字符串 fallback LEVEL_3 (不抛异)."""
        self.assertEqual(hg.get_guard("UNKNOWN_LEVEL"), hg.get_guard("LEVEL_3_STRICT"))
        self.assertEqual(hg.get_guard(""), hg.get_guard("LEVEL_3_STRICT"))
        self.assertEqual(hg.get_guard("level_5"), hg.get_guard("LEVEL_3_STRICT"))  # case-sensitive

    def test_none_fallback_to_level_3(self):
        """None level fallback LEVEL_3 (不抛异)."""
        self.assertEqual(hg.get_guard(None), hg.get_guard("LEVEL_3_STRICT"))

    def test_non_string_fallback_to_level_3(self):
        """非 string level (int/list/dict) fallback LEVEL_3."""
        self.assertEqual(hg.get_guard(1), hg.get_guard("LEVEL_3_STRICT"))
        self.assertEqual(hg.get_guard([]), hg.get_guard("LEVEL_3_STRICT"))
        self.assertEqual(hg.get_guard({}), hg.get_guard("LEVEL_3_STRICT"))

    def test_get_guard_idempotent(self):
        """同 level 多次调用返回完全相同字符串 (无 random)."""
        self.assertEqual(hg.get_guard("LEVEL_5_RADAR_AWARE"),
                         hg.get_guard("LEVEL_5_RADAR_AWARE"))

    def test_returned_text_appendable_to_prompt(self):
        """返回字符串可直接 append 到任何 base prompt."""
        base = "你是一个助手, 输出 JSON."
        for lv in hg.list_levels():
            combined = base + hg.get_guard(lv)
            self.assertTrue(combined.startswith(base))
            # \\n\\n 开头让 base prompt 自然分段
            self.assertIn("\n\n⚠️", combined)


class TestBloodLessonBlockedPhrases(unittest.TestCase):
    """V37.9.56-hotfix3 血案具体字面禁令在 LEVEL_4+ 完整出现."""

    def test_blocked_phrases_count_at_least_5(self):
        """至少 5 个血案精确字眼 (LEVEL_4+ 必含)."""
        phrases = hg.get_blocked_phrases()
        self.assertGreaterEqual(len(phrases), 5,
            "至少 5 个 V37.9.56-hotfix3 血案精确字眼")

    def test_blocked_phrases_in_level_4(self):
        """所有血案字眼必须在 LEVEL_4 守卫中出现."""
        l4 = hg.get_guard("LEVEL_4_PROJECT_AWARE")
        for phrase in hg.get_blocked_phrases():
            self.assertIn(phrase, l4,
                f'血案字眼 {phrase!r} 必须显式列在 LEVEL_4')

    def test_blocked_phrases_in_level_5(self):
        """所有血案字眼必须在 LEVEL_5 守卫中出现 (累积)."""
        l5 = hg.get_guard("LEVEL_5_RADAR_AWARE")
        for phrase in hg.get_blocked_phrases():
            self.assertIn(phrase, l5,
                f'血案字眼 {phrase!r} 必须在 LEVEL_5 (累积自 LEVEL_4)')

    def test_blocked_phrases_not_in_level_3(self):
        """LEVEL_3 不应含血案字眼 (LEVEL_4 才引入项目感知)."""
        l3 = hg.get_guard("LEVEL_3_STRICT")
        # 至少一个明显字眼不应出现在 LEVEL_3
        self.assertNotIn("OpenClaw 社区发布", l3,
            "LEVEL_3 不应含项目动态字面禁令 (LEVEL_4 责任)")

    def test_specific_blood_lesson_phrases_listed(self):
        """V37.9.56-hotfix3 5 个核心血案字眼必须在清单."""
        phrases = hg.get_blocked_phrases()
        for required in [
            "OpenClaw 社区发布",
            "OpenClaw v26",
            "v26/v27/v37 版本更新",
            "项目里程碑",
            "[openclaw]",
        ]:
            self.assertIn(required, phrases,
                f"血案字眼 {required!r} 必须在 get_blocked_phrases() 清单")


class TestRadarSignalTypes(unittest.TestCase):
    """Opportunity Radar 三件套信号源契约 (LEVEL_5 独有)."""

    def test_three_radar_types(self):
        """必须 3 个 Radar 信号类型 #1+#2+#3."""
        types = hg.get_radar_signal_types()
        self.assertEqual(len(types), 3)

    def test_radar_types_in_level_5(self):
        """所有 Radar 类型必须在 LEVEL_5 守卫中出现."""
        l5 = hg.get_guard("LEVEL_5_RADAR_AWARE")
        for t in hg.get_radar_signal_types():
            self.assertIn(t, l5,
                f"Radar 类型 {t!r} 必须在 LEVEL_5 守卫")

    def test_radar_types_not_in_level_4(self):
        """LEVEL_4 不应含 Radar 类型 (LEVEL_5 责任)."""
        l4 = hg.get_guard("LEVEL_4_PROJECT_AWARE")
        for t in hg.get_radar_signal_types():
            self.assertNotIn(t, l4,
                f"Radar 类型 {t!r} 不应在 LEVEL_4 (LEVEL_5 独有)")


class TestCliInterface(unittest.TestCase):
    """CLI 接口 (--list / --blocked-phrases / --level)."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "hallucination_guards.py"), *args],
            capture_output=True, text=True, timeout=10,
        )

    def test_cli_list_outputs_5_levels(self):
        r = self._run("--list")
        self.assertEqual(r.returncode, 0)
        lines = [l for l in r.stdout.strip().split("\n") if l]
        self.assertEqual(len(lines), 5)
        self.assertIn("LEVEL_5_RADAR_AWARE", lines)

    def test_cli_blocked_phrases_outputs_phrases(self):
        r = self._run("--blocked-phrases")
        self.assertEqual(r.returncode, 0)
        self.assertIn("OpenClaw 社区发布", r.stdout)
        self.assertIn("[openclaw]", r.stdout)

    def test_cli_level_outputs_specific_text(self):
        r = self._run("--level", "LEVEL_5_RADAR_AWARE")
        self.assertEqual(r.returncode, 0)
        self.assertIn("⚠️", r.stdout)
        self.assertIn("Opportunity Radar", r.stdout)
        self.assertIn("OpenClaw 社区发布", r.stdout)

    def test_cli_no_args_shows_summary(self):
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("LEVEL_3_STRICT", r.stdout)
        self.assertIn("V37.9.57", r.stdout)


class TestSourceLevelGuards(unittest.TestCase):
    """源码级守卫 (V37.9.57 marker + 反向验证字面常量锁定)."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "hallucination_guards.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_v37_9_57_marker_present(self):
        self.assertIn("V37.9.57", self.src)
        self.assertIn("公共反幻觉守卫模板", self.src)

    def test_v37_9_56_hotfix3_lineage_traceable(self):
        """源码必须能 grep 出 V37.9.56-hotfix3 血案来源 (历史可追溯)."""
        self.assertIn("V37.9.56-hotfix3", self.src,
            "源码必须含 V37.9.56-hotfix3 marker (血案来源可追)")
        self.assertIn("OpenClaw 社区发布", self.src,
            "源码必须含血案精确字眼便于运维 grep")

    def test_5_guards_dict_keys_locked(self):
        """GUARDS dict 必须含 5 档精确字面 key."""
        for key in ["LEVEL_1_MINIMAL", "LEVEL_2_STANDARD", "LEVEL_3_STRICT",
                    "LEVEL_4_PROJECT_AWARE", "LEVEL_5_RADAR_AWARE"]:
            self.assertIn(f'"{key}"', self.src,
                f"GUARDS dict 必须含 key {key!r}")

    def test_default_fallback_is_level_3(self):
        """_DEFAULT_FALLBACK_LEVEL 必须 LEVEL_3 (安全中位数, 防未来误改最高/最低)."""
        self.assertIn('_DEFAULT_FALLBACK_LEVEL = "LEVEL_3_STRICT"', self.src)

    def test_get_guard_function_defined(self):
        self.assertIn("def get_guard(", self.src)

    def test_list_levels_function_defined(self):
        self.assertIn("def list_levels(", self.src)

    def test_get_blocked_phrases_function_defined(self):
        self.assertIn("def get_blocked_phrases(", self.src)

    def test_get_radar_signal_types_function_defined(self):
        self.assertIn("def get_radar_signal_types(", self.src)

    def test_mr_8_single_source_of_truth_documented(self):
        """模块 docstring 必须声明 MR-8 single-source-of-truth 兑现."""
        self.assertIn("MR-8", self.src,
            "模块必须声明 MR-8 兑现")
        self.assertIn("single-source-of-truth", self.src,
            "MR-8 single-source-of-truth 字面必须在源码")


class TestV37957Contracts(unittest.TestCase):
    """V37.9.57 集成契约守卫 (各 task 应使用此模块)."""

    def test_module_importable_at_top(self):
        """模块顶部 import 不抛异 (FAIL-OPEN 不依赖外部模块)."""
        import hallucination_guards
        self.assertTrue(hasattr(hallucination_guards, "get_guard"))
        self.assertTrue(hasattr(hallucination_guards, "list_levels"))

    def test_no_external_dependencies(self):
        """模块顶部不应导入项目内其他模块 (公共模板必须独立)."""
        with open(os.path.join(REPO_ROOT, "hallucination_guards.py"), encoding="utf-8") as f:
            head = "\n".join(f.read().split("\n")[:30])
        # 严禁导入项目内模块
        for forbidden in ["import kb_review", "import kb_evening", "import kb_dream",
                          "import top_alignment", "from kb_", "from project_"]:
            self.assertNotIn(forbidden, head,
                f"模块顶部禁导入 {forbidden!r} (公共模板必须独立)")


if __name__ == "__main__":
    unittest.main()

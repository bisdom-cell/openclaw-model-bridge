#!/usr/bin/env python3
"""
test_gen_compat_matrix.py — compatibility_matrix 漂移防护单测（V37.9.143，外部评审2 P0(b)）

覆盖:
- providers.py 三张表直出纯函数 (matrix / tier / capability table_lines)
- verifiable_features 分母口径修复 ("5/4 verified" 超界 bug)
- V37.9.146: tier_table_lines 字段化 (验证档位升级为第 3 张机器表)
- gen_compat_matrix extract/check/fix 三件套
- 人工段落保护契约 (Fallback 路径不被 --fix 触碰)
- 反向验证: sabotage doc 表行 (含档位行) → --check 必抓
"""
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import gen_compat_matrix as gcm
from providers import _default_registry, ProviderCapabilities


class TestProvidersTableLines(unittest.TestCase):
    """providers.py 主矩阵表直出纯函数。"""

    def test_matrix_table_has_header_and_8_providers(self):
        lines = _default_registry.matrix_table_lines()
        self.assertTrue(lines[0].startswith("| Provider | Models |"))
        self.assertTrue(lines[1].startswith("|---"))
        # 11 providers (7 built-in + doubao + doubao_21 + deepseek + deepseek_full plugins)
        self.assertEqual(len(lines), 2 + 11)

    def test_matrix_table_contains_qwen_and_doubao(self):
        text = "\n".join(_default_registry.matrix_table_lines())
        self.assertIn("Qwen (Remote GPU)", text)
        self.assertIn("Doubao Seed 2.0 Pro", text)

    def test_print_matrix_delegates_to_table_lines(self):
        """print_matrix 必须经 matrix_table_lines（单一真理源, 防两处漂移）。"""
        with open(os.path.join(REPO, "providers.py"), encoding="utf-8") as f:
            src = f.read()
        idx = src.index("def print_matrix")
        body = src[idx:idx + 300]
        self.assertIn("matrix_table_lines", body)


class TestCapabilityTableLines(unittest.TestCase):
    """providers.py 能力矩阵表直出纯函数（V37.9.143 新增）。"""

    def test_header_has_9_dimensions(self):
        lines = _default_registry.capability_table_lines()
        header = lines[0]
        for col in ("Text", "Vision", "Audio", "Video", "Tool Calling",
                    "Streaming", "JSON Mode", "Reasoning", "Context Window"):
            self.assertIn(col, header)

    def test_doubao_reasoning_and_json_mode_yes(self):
        """V37.9.142 手动刷新漏掉的真漂移: doubao json_mode=True 被手写为 —。

        本测试锁定机器直出与 plugin 声明一致 (json_mode=Yes + reasoning=Yes)。
        """
        lines = _default_registry.capability_table_lines()
        # V37.9.216: 特指 2.0 Pro (2.1 Pro 是另一行, declared 未实测)
        doubao = [l for l in lines if "Doubao Seed 2.0 Pro" in l]
        self.assertEqual(len(doubao), 1)
        cells = [c.strip() for c in doubao[0].split("|")]
        # | '' | Provider | Text | Vision | Audio | Video | Tool | Stream | JSON | Reasoning | Ctx | '' |
        self.assertEqual(cells[8], "Yes", "doubao JSON Mode 应为 Yes (plugin 声明)")
        self.assertEqual(cells[9], "Yes", "doubao Reasoning 应为 Yes (V37.9.53)")

    def test_qwen_audio_dash(self):
        lines = _default_registry.capability_table_lines()
        qwen = [l for l in lines if "Qwen (Remote GPU)" in l][0]
        cells = [c.strip() for c in qwen.split("|")]
        self.assertEqual(cells[4], "—", "qwen Audio 应为 —")

    def test_capability_matrix_cli(self):
        """--capability-matrix CLI 直出能力矩阵表。"""
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "providers.py"), "--capability-matrix"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("| Provider | Text | Vision |", r.stdout)
        self.assertIn("Doubao", r.stdout)


class TestVerifiableFeaturesDenominator(unittest.TestCase):
    """V37.9.143 Verification Status 分母口径修复（旧 "5/4 verified" 超界 bug）。"""

    def test_qwen_verifiable_is_5(self):
        qwen = _default_registry.get("qwen")
        feats = qwen.capabilities.verifiable_features()
        self.assertEqual(
            set(feats), {"text", "vision", "tool_calling", "streaming", "fallback"})

    def test_doubao_verifiable_is_6_with_reasoning(self):
        doubao = _default_registry.get("doubao")
        feats = doubao.capabilities.verifiable_features()
        self.assertIn("reasoning", feats)
        self.assertEqual(len(feats), 6)

    def test_fallback_always_verifiable(self):
        """fallback 维度恒可验证（不依赖能力声明）。"""
        caps = ProviderCapabilities(text=False, tool_calling=False, streaming=False)
        self.assertIn("fallback", caps.verifiable_features())

    def test_numerator_never_exceeds_denominator(self):
        """分子 ≤ 分母（修 "5/4" 超界）— 对全部 11 providers 断言。"""
        for p in _default_registry.all():
            verified = p.capabilities.verified_features()
            verifiable = p.capabilities.verifiable_features()
            self.assertLessEqual(
                len(verified), len(verifiable),
                f"{p.name}: verified {len(verified)} > verifiable {len(verifiable)}")

    def test_cli_no_more_5_of_4(self):
        """providers.py 默认输出不得再出现超界 'N/M' (N>M)。"""
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "providers.py")],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("5/4 verified", r.stdout)
        self.assertIn("5/5 verified", r.stdout)   # qwen
        self.assertIn("5/6 verified", r.stdout)   # doubao (verified_fallback=False 诚实语义)

    def test_verification_status_uses_verifiable_features(self):
        """源码守卫: Verification Status 分母必须用 verifiable_features 不得回退旧口径。"""
        with open(os.path.join(REPO, "providers.py"), encoding="utf-8") as f:
            src = f.read()
        idx = src.index("## Verification Status")
        section = src[idx:idx + 600]
        self.assertIn("verifiable_features()", section)
        self.assertNotIn("supported_modalities()) +", section,
                         "旧分母口径 (modalities+tool+stream) 不得回退")


class TestTierTableLines(unittest.TestCase):
    """providers.py 验证档位表直出纯函数（V37.9.146 字段化）。"""

    def test_header_and_8_rows(self):
        lines = _default_registry.tier_table_lines()
        self.assertEqual(lines[0], "| Provider | 档位 | 依据 |")
        self.assertTrue(lines[1].startswith("|---"))
        self.assertEqual(len(lines), 2 + 11)  # header + sep + 11 providers

    def test_qwen_doubao_production_observed(self):
        text = "\n".join(_default_registry.tier_table_lines())
        self.assertIn("Qwen (Remote GPU) | **production_observed**", text)
        self.assertIn("Doubao Seed 2.0 Pro (Volcengine Ark) | **production_observed**", text)

    def test_gemini_retirement_note_rendered(self):
        """tier_note 渲染进档位列（gemini 退役）。"""
        lines = _default_registry.tier_table_lines()
        gemini = [l for l in lines if "Google Gemini" in l][0]
        self.assertIn("**production_observed**（已退役出 fallback 链）", gemini)

    def test_declared_providers_use_derived_evidence(self):
        """5 declared provider 各自一行, 走派生默认依据 (单一真理源, 退役合并行)。"""
        lines = _default_registry.tier_table_lines()
        declared = [l for l in lines if "**declared**" in l]
        # V37.9.217: doubao_21 E2E 实测升 feature_verified 离开 declared → declared 5
        self.assertEqual(len(declared), 5)  # openai/claude/kimi/minimax/glm
        for l in declared:
            self.assertIn("能力声明完整 + 合约校验通过，0/N 生产验证（无 API key 配置）", l)

    def test_tier_table_normalizes_doubao_full_display_name(self):
        """机器表用全 display_name (一致性收敛: 手写表曾用简称 'Doubao Seed 2.0 Pro')。"""
        text = "\n".join(_default_registry.tier_table_lines())
        self.assertIn("Doubao Seed 2.0 Pro (Volcengine Ark)", text)

    def test_tier_matrix_cli(self):
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "providers.py"), "--tier-matrix"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("| Provider | 档位 | 依据 |", r.stdout)
        self.assertIn("production_observed", r.stdout)


def _make_doc(tables):
    """构造含三张机器表 + 人工段落的最小 doc 文本（V37.9.146: 验证档位也是机器表）。"""
    lines = ["# Provider Compatibility Matrix", "", "> header prose", "", "---", ""]
    lines.append("## 支持的 Provider")
    lines.append("")
    lines.extend(tables["支持的 Provider"])
    lines.append("")
    lines.append("人工 prose 行（不参与比对）。")
    lines.append("")
    lines.append("## 验证档位")
    lines.append("")
    lines.append("> 四档语义人工 blockquote（验证档位标题与表之间, 不参与比对）。")
    lines.append("")
    lines.extend(tables["验证档位"])
    lines.append("")
    lines.append("## 能力矩阵")
    lines.append("")
    lines.extend(tables["能力矩阵"])
    lines.append("")
    lines.append("> Reasoning 维度说明（人工 blockquote 保留）。")
    lines.append("")
    lines.append("## Fallback 降级路径（V37.9.129 现状）")
    lines.append("")
    lines.append("人工段落内容。")
    return "\n".join(lines) + "\n"


class TestExtractTableBlock(unittest.TestCase):
    def setUp(self):
        self.tables = gcm.generate_tables()
        self.doc = _make_doc(self.tables).splitlines()

    def test_extracts_main_table(self):
        start, end = gcm.extract_table_block(self.doc, "支持的 Provider")
        self.assertIsNotNone(start)
        block = self.doc[start:end]
        self.assertEqual(block, self.tables["支持的 Provider"])

    def test_extracts_capability_table(self):
        start, end = gcm.extract_table_block(self.doc, "能力矩阵")
        block = self.doc[start:end]
        self.assertEqual(block, self.tables["能力矩阵"])

    def test_extracts_tier_table(self):
        """V37.9.146: 验证档位表可被提取 (heading/blockquote 之后第一个表格块)。"""
        start, end = gcm.extract_table_block(self.doc, "验证档位")
        self.assertIsNotNone(start)
        block = self.doc[start:end]
        self.assertEqual(block, self.tables["验证档位"])

    def test_missing_heading_returns_none(self):
        start, end = gcm.extract_table_block(self.doc, "不存在的标题")
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_heading_without_table_returns_none(self):
        doc = ["## 空标题", "", "没有表格", "", "## 下一个标题"]
        start, end = gcm.extract_table_block(doc, "空标题")
        self.assertIsNone(start)


class TestCheckAndFixDrift(unittest.TestCase):
    def setUp(self):
        self.tables = gcm.generate_tables()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.doc_path = os.path.join(self.tmpdir.name, "compat.md")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, text):
        with open(self.doc_path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_no_drift_when_consistent(self):
        self._write(_make_doc(self.tables))
        self.assertEqual(gcm.check_drift(self.doc_path), [])

    def test_drift_detected_on_modified_row(self):
        """反向验证: sabotage 表内一行 → check 必抓。"""
        text = _make_doc(self.tables).replace("Doubao Seed 2.0 Pro", "Doubao SABOTAGED")
        self._write(text)
        drifts = gcm.check_drift(self.doc_path)
        self.assertTrue(drifts, "sabotage 行未被检测到 — 守卫无效")

    def test_manual_section_change_no_drift(self):
        """人工段落改动不触发漂移（机器比对范围契约, Fallback prose）。"""
        text = _make_doc(self.tables).replace(
            "人工段落内容。", "人工段落内容。 — 编辑后")
        self._write(text)
        self.assertEqual(gcm.check_drift(self.doc_path), [])

    def test_tier_table_drift_detected(self):
        """反向验证: sabotage 档位表内 tier 值 → check 必抓（V37.9.146）。"""
        text = _make_doc(self.tables).replace(
            "Qwen (Remote GPU) | **production_observed**",
            "Qwen (Remote GPU) | **declared**")
        self._write(text)
        drifts = gcm.check_drift(self.doc_path)
        self.assertTrue(drifts, "档位 sabotage 未被检测到 — 守卫无效")

    def test_missing_doc_reports_drift(self):
        drifts = gcm.check_drift(os.path.join(self.tmpdir.name, "nonexistent.md"))
        self.assertTrue(drifts)

    def test_fix_repairs_drift_and_is_idempotent(self):
        text = _make_doc(self.tables).replace("Doubao Seed 2.0 Pro", "Doubao SABOTAGED")
        self._write(text)
        changed = gcm.fix_drift(self.doc_path)
        self.assertTrue(changed)
        self.assertEqual(gcm.check_drift(self.doc_path), [])
        # 幂等: 第二次 fix 无修改
        self.assertFalse(gcm.fix_drift(self.doc_path))

    def test_fix_preserves_manual_sections(self):
        """--fix 绝不触碰人工段落（Fallback 路径 / blockquote, V37.9.146 验证档位已机器化）。"""
        text = _make_doc(self.tables).replace("Doubao Seed 2.0 Pro", "Doubao SABOTAGED")
        self._write(text)
        gcm.fix_drift(self.doc_path)
        with open(self.doc_path, encoding="utf-8") as f:
            fixed = f.read()
        # 验证档位表/标题之间的人工 blockquote 不被触碰
        self.assertIn("> 四档语义人工 blockquote（验证档位标题与表之间, 不参与比对）。", fixed)
        self.assertIn("> Reasoning 维度说明（人工 blockquote 保留）。", fixed)
        self.assertIn("## Fallback 降级路径（V37.9.129 现状）", fixed)
        self.assertIn("人工段落内容。", fixed)


class TestRealRepoIntegration(unittest.TestCase):
    """真仓库端到端: 当前 doc 必须与 providers.py 一致（V37.9.143 已 --fix 同步）。"""

    def test_real_doc_check_passes(self):
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "gen_compat_matrix.py"), "--check"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0,
                         f"真仓库 doc 漂移: {r.stderr}\n修复: python3 gen_compat_matrix.py --fix")

    def test_check_writes_diagnostics_to_stderr(self):
        """MR-11: 诊断输出走 stderr, stdout 只有结论行。"""
        with open(os.path.join(REPO, "gen_compat_matrix.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("file=sys.stderr", src)

    def test_full_regression_wired(self):
        """full_regression.sh doc-drift 层已接入 gen_compat_matrix --check。"""
        with open(os.path.join(REPO, "full_regression.sh"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("gen_compat_matrix.py --check", src)
        self.assertIn("compat matrix drift", src)

    def test_v37_9_143_marker(self):
        with open(os.path.join(REPO, "gen_compat_matrix.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.143", src)
        self.assertIn("外部评审2 P0(b)", src)

    def test_table_specs_has_three_tables(self):
        """V37.9.146: TABLE_SPECS 从 2 张升 3 张 (验证档位字段化)。"""
        headings = [h for h, _ in gcm.TABLE_SPECS]
        self.assertEqual(headings, ["支持的 Provider", "验证档位", "能力矩阵"])
        methods = [m for _, m in gcm.TABLE_SPECS]
        self.assertIn("tier_table_lines", methods)

    def test_v37_9_146_tier_field_marker(self):
        """gen_compat_matrix.py 记录 V37.9.146 字段化背景。"""
        with open(os.path.join(REPO, "gen_compat_matrix.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.146", src)
        self.assertIn("外部评审2 P2(a)", src)

    def test_doc_no_longer_lists_tier_as_manual(self):
        """反向守卫: doc 不得再把'验证档位'列为人工段落 (升级为机器表后)。

        防回退: 若有人把 doc 标题改回带括号导致 extract 找不到, 或把验证档位
        放回人工段落清单, --check 已会抓表格漂移; 这里额外守 doc 文案一致。
        """
        with open(os.path.join(REPO, "docs", "compatibility_matrix.md"),
                  encoding="utf-8") as f:
            doc = f.read()
        # 标题必须精确 "## 验证档位" (extract_table_block 要求)
        self.assertIn("\n## 验证档位\n", doc)
        # 人工段落清单 (preamble + footer) 不再含 "验证档位"
        for marker in ("人工段落（Fallback 路径 / 添加新 Provider / 工具模式验证）",
                       "人工段落（Fallback 路径 / 工具模式验证）"):
            self.assertIn(marker, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""
test_security_ontology_alignment.py — regression test for V37.9.3 Route C Step 3

Locks the contract that security_score.py and governance_ontology.yaml agree on:
  - dimension names (YAML threshold keys must match compute_score output names)
  - threshold values (YAML is single source of truth, no hardcoded duplicates)
  - --check-ontology-thresholds mode wiring

Background
----------
V37.9 Route C Step 1: INV-SEC-001 bound total_score ≥ 90 to governance.
V37.9.1 Route C Step 2: per-dimension MIN_THRESHOLDS hardcoded inside the
  python_assert code of INV-SEC-001.
V37.9.3 Route C Step 3: per-dimension thresholds lifted to
  governance_ontology.yaml:security_config.dimensions as the single source of
  truth; both the governance check AND security_score.py read from YAML.

This test locks the alignment so future edits cannot accidentally:
  (a) rename a dimension in one side but forget the other (silent coverage loss)
  (b) reintroduce hardcoded thresholds in python_assert code (drift source)
  (c) break the --check-ontology-thresholds CLI flag
"""

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load_ontology_security_config():
    import yaml
    yaml_path = os.path.join(_HERE, "ontology", "governance_ontology.yaml")
    with open(yaml_path, "r", encoding="utf-8") as f:
        onto = yaml.safe_load(f)
    return onto.get("security_config") or {}


class TestOntologySecurityConfigPresent(unittest.TestCase):
    def test_security_config_section_exists(self):
        cfg = _load_ontology_security_config()
        self.assertIn("total_threshold", cfg,
                      "YAML 缺失 security_config.total_threshold")
        self.assertIn("dimensions", cfg,
                      "YAML 缺失 security_config.dimensions")

    def test_total_threshold_is_reasonable(self):
        cfg = _load_ontology_security_config()
        tt = cfg.get("total_threshold")
        self.assertIsInstance(tt, int)
        self.assertGreaterEqual(tt, 80, "总阈值不得低于 80（baseline 93）")
        self.assertLessEqual(tt, 100, "总阈值不得超过 100")

    def test_all_seven_dimensions_declared(self):
        cfg = _load_ontology_security_config()
        dims = cfg.get("dimensions") or {}
        self.assertEqual(
            len(dims), 7,
            f"ontology 声明维度数 {len(dims)} ≠ 预期 7（security_score 7 维度）"
        )


class TestSecurityScoreAlignment(unittest.TestCase):
    """V37.9.3: ontology dimension names must match security_score.compute_score output."""

    def test_ontology_names_match_security_score_names(self):
        from security_score import compute_score
        cfg = _load_ontology_security_config()
        ontology_names = set((cfg.get("dimensions") or {}).keys())
        data = compute_score()
        actual_names = set()
        for dim in data.get("dimensions", []):
            if dim.get("name"):
                actual_names.add(dim["name"])
        self.assertEqual(
            ontology_names, actual_names,
            f"维度名漂移 — ontology only: {ontology_names - actual_names}, "
            f"security_score only: {actual_names - ontology_names}"
        )

    def test_ontology_thresholds_not_exceed_max(self):
        """Each ontology threshold must be ≤ the max of that dimension."""
        from security_score import compute_score
        cfg = _load_ontology_security_config()
        per_dim = cfg.get("dimensions") or {}
        data = compute_score()
        for dim in data.get("dimensions", []):
            name = dim.get("name")
            if name in per_dim:
                self.assertLessEqual(
                    per_dim[name], dim.get("max", 0),
                    f"{name} 阈值 {per_dim[name]} > 维度满分 {dim.get('max')}"
                )


class TestNoHardcodedThresholdsInInvSecCheck(unittest.TestCase):
    """V37.9.3: INV-SEC-001 check code must NOT re-hardcode MIN_THRESHOLDS dict.
    Thresholds must come from YAML (ontology is single source of truth)."""

    def test_inv_sec_001_reads_from_yaml(self):
        yaml_path = os.path.join(_HERE, "ontology", "governance_ontology.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Find INV-SEC-001 block
        start = content.find("id: INV-SEC-001")
        self.assertGreater(start, 0, "INV-SEC-001 not found in YAML")
        # Find next invariant or end of invariants section
        end = content.find("  - id: INV-", start + 100)
        if end < 0:
            end = len(content)
        inv_block = content[start:end]
        # Must read from security_config.dimensions (ontology-driven)
        self.assertIn(
            "security_config", inv_block,
            "INV-SEC-001 must read thresholds from security_config (ontology 真理源)"
        )
        self.assertIn(
            ".get(\"dimensions\")", inv_block,
            "INV-SEC-001 must call .get('dimensions') on security_config"
        )


class TestInvSec001UsesLibraryDirectCall(unittest.TestCase):
    """V37.9.8 路线 C Step 4: INV-SEC-001 必须 library 直调 security_score，
    不得用 subprocess.run(["python3", "security_score.py"]) + regex parse stdout。

    旧实现（V37.9 引入）在 check 2 用 subprocess + 正则 "安全评分：(\\d+)/100"，
    三个问题:
      (a) ~200ms fork 开销 × 每次 governance audit
      (b) 正则 parse 失败时静默 pass，塌陷盲区（分数维度实际塌陷但 exit 0）
      (c) 硬编码阈值 90，与 security_config.total_threshold 漂移风险

    V37.9.8 Step 4 重构为 `from security_score import compute_score`，
    三个 INV-SEC-001 runtime 检查（总分/维度/名称对齐）全部走 library + YAML
    真理源，ontology 真正成为 security + governance 的共同数据源。

    本测试锁定：INV-SEC-001 block 内不得出现 subprocess 调用 security_score.py。
    """

    def test_no_subprocess_call_to_security_score_in_inv_sec_001(self):
        yaml_path = os.path.join(_HERE, "ontology", "governance_ontology.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        start = content.find("id: INV-SEC-001")
        self.assertGreater(start, 0, "INV-SEC-001 not found in YAML")
        end = content.find("  - id: INV-", start + 100)
        if end < 0:
            end = len(content)
        inv_block = content[start:end]

        # 只扫 active code（python_assert code 块），不扫 YAML 注释行、
        # declaration/design_decision/lesson 文档块。注释行以 `#` 起头（去掉
        # 前导空格后），文档块在 top-level field 下方，不会出现在 code: | 之后的
        # 缩进行中。策略: 过滤 `# ...` 行 + 过滤 `declaration: |` / `design_decision: |` /
        # `lesson: |` 整段（因为这些 field 下允许引用历史反模式做文档说明）。
        def _strip_docs_and_comments(text):
            out_lines = []
            skip_block = False
            for line in text.split("\n"):
                stripped = line.lstrip()
                # 进入文档块 → 跳过直到下一个顶层缩进（4 空格）的 field
                if stripped.startswith(("declaration: |", "design_decision: |", "lesson: |")):
                    skip_block = True
                    continue
                if skip_block:
                    # 文档块结束: 遇到下一个 `    checks:` 或 `    - id:` 或 `    name:`（同级 field）
                    # 或空行后下一个非 6+ 空格缩进行
                    if line.startswith("    ") and not line.startswith("      "):
                        skip_block = False
                    else:
                        continue
                # 跳过 YAML 注释行（`#` 前可能有任意空格）
                if stripped.startswith("#"):
                    continue
                out_lines.append(line)
            return "\n".join(out_lines)

        code_only = _strip_docs_and_comments(inv_block)
        # 禁止 subprocess.run([...security_score.py...]) 这种反模式
        forbidden_patterns = [
            '["python3", "security_score.py"]',
            "['python3', 'security_score.py']",
            'subprocess.run(["python", "security_score.py"]',
        ]
        for pat in forbidden_patterns:
            self.assertNotIn(
                pat, code_only,
                f"INV-SEC-001 active code 出现 subprocess 调 security_score.py 反模式: {pat!r}\n"
                f"V37.9.8 Step 4 起必须用 `from security_score import compute_score` 直调\n"
                f"（注释/文档块引用历史反模式做说明可以保留）"
            )

    def test_inv_sec_001_imports_security_score_library(self):
        """INV-SEC-001 runtime 检查必须显式 import security_score library。"""
        yaml_path = os.path.join(_HERE, "ontology", "governance_ontology.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        start = content.find("id: INV-SEC-001")
        end = content.find("  - id: INV-", start + 100)
        if end < 0:
            end = len(content)
        inv_block = content[start:end]
        self.assertIn(
            "from security_score import compute_score", inv_block,
            "INV-SEC-001 必须 library 直调 compute_score（V37.9.8 Step 4 契约）"
        )

    def test_inv_sec_001_reads_total_threshold_from_yaml(self):
        """V37.9.8 Step 4: 总分阈值必须从 security_config.total_threshold 读取，
        不得硬编码 90。"""
        yaml_path = os.path.join(_HERE, "ontology", "governance_ontology.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        start = content.find("id: INV-SEC-001")
        end = content.find("  - id: INV-", start + 100)
        if end < 0:
            end = len(content)
        inv_block = content[start:end]
        self.assertIn(
            "total_threshold", inv_block,
            "INV-SEC-001 必须引用 security_config.total_threshold"
        )
        # 硬编码 "90" 作为独立阈值数字不应出现在 assert 语句里
        # （允许出现在注释里描述历史，但不能是 active assert 比较值）
        # 粗略检查: "assert score >= 90" 或 "assert total >= 90" 反模式
        self.assertNotIn(
            "assert score >= 90", inv_block,
            "V37.9.8 Step 4 后不得硬编码 >= 90，必须从 total_threshold 读取"
        )
        self.assertNotIn(
            "assert total >= 90,", inv_block,
            "V37.9.8 Step 4 后不得硬编码 >= 90，必须从 total_threshold 读取"
        )


class TestCheckOntologyThresholdsCliMode(unittest.TestCase):
    """V37.9.3: --check-ontology-thresholds CLI mode must exist and work."""

    def test_cli_flag_returns_success_when_above_thresholds(self):
        proc = subprocess.run(
            [sys.executable, "security_score.py", "--check-ontology-thresholds"],
            capture_output=True, text=True, cwd=_HERE, timeout=60,
        )
        # Current baseline 93/100 satisfies all thresholds → exit 0
        self.assertEqual(
            proc.returncode, 0,
            f"--check-ontology-thresholds 在 baseline 下应返回 0:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        self.assertIn("满足 ontology", proc.stdout + proc.stderr)

    def test_cli_flag_exists_in_help(self):
        proc = subprocess.run(
            [sys.executable, "security_score.py", "--help"],
            capture_output=True, text=True, cwd=_HERE, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--check-ontology-thresholds", proc.stdout)

    def test_load_ontology_thresholds_returns_dict(self):
        from security_score import load_ontology_thresholds
        total, per_dim = load_ontology_thresholds()
        self.assertIsInstance(per_dim, dict)
        self.assertTrue(per_dim, "per_dim 不得为空（ontology 应加载成功）")
        self.assertGreater(total, 0)


class TestCheckOntologyThresholdsFunction(unittest.TestCase):
    """Unit test the pure function check_ontology_thresholds()."""

    def test_pass_when_score_above_thresholds(self):
        from security_score import check_ontology_thresholds
        fake_data = {
            "total": 93,
            "max": 100,
            "dimensions": [
                {"name": "密钥管理", "score": 15, "max": 15},
                {"name": "测试门禁", "score": 14, "max": 15},
                {"name": "数据完整性", "score": 12, "max": 15},
                {"name": "部署安全", "score": 15, "max": 15},
                {"name": "传输安全", "score": 10, "max": 10},
                {"name": "审计追踪", "score": 15, "max": 15},
                {"name": "可用性", "score": 12, "max": 15},
            ]
        }
        ok, violations = check_ontology_thresholds(fake_data)
        self.assertTrue(ok, f"应 pass 但有 violations: {violations}")
        self.assertEqual(violations, [])

    def test_fail_when_dimension_below_threshold(self):
        """Simulate a collapse: 密钥管理 掉到 5 → 严重违反"""
        from security_score import check_ontology_thresholds
        fake_data = {
            "total": 83,  # total might still be high
            "max": 100,
            "dimensions": [
                {"name": "密钥管理", "score": 5, "max": 15},
                {"name": "测试门禁", "score": 14, "max": 15},
                {"name": "数据完整性", "score": 12, "max": 15},
                {"name": "部署安全", "score": 15, "max": 15},
                {"name": "传输安全", "score": 10, "max": 10},
                {"name": "审计追踪", "score": 15, "max": 15},
                {"name": "可用性", "score": 12, "max": 15},
            ]
        }
        ok, violations = check_ontology_thresholds(fake_data)
        self.assertFalse(ok)
        self.assertTrue(any("密钥管理" in v for v in violations))

    def test_fail_when_total_below_threshold(self):
        """Total below 90 (even if all dims above minimums)"""
        from security_score import check_ontology_thresholds
        fake_data = {
            "total": 85,
            "max": 100,
            "dimensions": [
                {"name": "密钥管理", "score": 15, "max": 15},
                {"name": "测试门禁", "score": 13, "max": 15},
                {"name": "数据完整性", "score": 10, "max": 15},
                {"name": "部署安全", "score": 15, "max": 15},
                {"name": "传输安全", "score": 9, "max": 10},
                {"name": "审计追踪", "score": 15, "max": 15},
                {"name": "可用性", "score": 8, "max": 15},
            ]
        }
        ok, violations = check_ontology_thresholds(fake_data)
        self.assertFalse(ok)
        # Total violation should be captured
        self.assertTrue(any("总分" in v for v in violations))


if __name__ == "__main__":
    unittest.main()

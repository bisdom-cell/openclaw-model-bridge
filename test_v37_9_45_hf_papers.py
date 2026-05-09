#!/usr/bin/env python3
"""test_v37_9_45_hf_papers.py — V37.9.45 hf_papers 6 字段深度迁移守卫
   (Opportunity Radar #2 PoC — 项目对齐度评分单点验证)

V37.9.45 双交付:
  1. hf_papers V37.9.39 同款机械迁移 (audit P1 续, 5 字段 fail-fast)
  2. Opportunity Radar #2 PoC: 6 字段 (5 字段 + 🎚️ 项目对齐度)
     - V37.9.45: hf_papers 单点验证模式可行性
     - V37.9.46 Stage 2: 加 project_alignment_scorer.py rule_check 验证层
       + 9 个对齐脚本同步加 6 字段

血案防御 (V37.9.36 反模式硬规则保留):
  - V37.8 老 3 字段 fallback 占位符 (贡献：AI领域相关研究 / 价值：⭐⭐⭐) 必须清除
  - 用 [LLM_DEGRADED] 标记 + abstract 兜底替代

hf_papers-specific 适配 (与 arxiv V37.9.43 区别):
  - URL = https://huggingface.co/papers/{paper_id}
  - 显示 HF upvotes (社区关注度)
  - 保留 Step 2.5 GitHub repo enrichment (HF 独有)
  - emit 显示 github_url + github_stars + github_lang (如 enrichment 命中)
  - 头部 emoji 🔥 (vs arxiv 📚)
  - Discord 频道 DISCORD_CH_PAPERS

V37.9.45 新增 (#2 PoC):
  - LLM prompt 加第 6 字段 🎚️ 项目对齐度 (基于 project_concepts.yaml 评分指南)
  - parse_5field_output → parse_6field_output (向后兼容缺字段为空)
  - emit 加项目对齐度展示 + 末尾"高对齐 Top X"统计
  - 项目方向描述: control plane / agent runtime / ontology / governance / 等

audit 视角对齐 (V37.9.38 INV-LLMCRON-AUDIT-001):
  - ALIGNED_SCRIPTS 字典追加 'jobs/hf_papers/run_hf_papers.sh': 'V37.9.45'
  - placeholder_findings 必须为 0
  - SYSTEM_ALERT / source_notify / send_alert / status:llm_failed 标志 ✓
"""
import importlib.util
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
HF_SCRIPT = os.path.join(REPO_ROOT, "jobs", "hf_papers", "run_hf_papers.sh")
PROJECT_CONCEPTS = os.path.join(REPO_ROOT, "project_concepts.yaml")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestHfPapersV9_45ShellGuards(unittest.TestCase):
    """V37.9.45 hf_papers 脚本 source-level grep 守卫 (V37.9.43 arxiv 同款 + V37.9.45 新增)"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HF_SCRIPT)

    def test_v37_9_45_marker_present(self):
        self.assertIn("V37.9.45", self.src)

    def test_source_notify_sh_at_top(self):
        self.assertIn("NOTIFY_SH=", self.src)
        self.assertTrue(re.search(r'source\s+"\$NOTIFY_SH"', self.src))

    def test_send_alert_helper_with_system_alert(self):
        self.assertIn("send_alert()", self.src)
        m = re.search(r"send_alert\(\)\s*\{[^}]*\[SYSTEM_ALERT\]", self.src, re.DOTALL)
        self.assertIsNotNone(m)
        # 告警消息含 hf_papers 名字让运维知道是哪个 job
        self.assertIn("[SYSTEM_ALERT] hf_papers", self.src)

    def test_llm_three_layer_detection(self):
        self.assertIn("__LLM_HTTP_ERROR__", self.src)
        self.assertIn("__LLM_PARSE_FAIL__", self.src)

    def test_call_llm_single_with_retry_helper(self):
        self.assertIn("call_llm_single_with_retry()", self.src)
        self.assertIn("backoffs=(5 10 20)", self.src)
        self.assertTrue(re.search(r"for\s+attempt\s+in\s+0\s+1\s+2", self.src))

    def test_main_loop_per_paper(self):
        self.assertTrue(re.search(r"for\s+\(\(\s*i\s*=\s*0\s*;\s*i\s*<\s*TOTAL_NEW", self.src))

    def test_three_status_levels(self):
        self.assertIn('"status":"llm_failed"', self.src)
        self.assertIn('"status":"partial_degraded"', self.src)
        self.assertIn("all_failed_", self.src)

    def test_llm_failed_branch_exit_1_lock(self):
        """V37.9.45 顺序锁: status:llm_failed 写入后必须 500 字符内 exit 1 (fail-fast 契约)"""
        idx = self.src.find('"status":"llm_failed"')
        self.assertGreater(idx, 0)
        exit_idx = self.src.find("exit 1", idx)
        self.assertGreater(exit_idx, 0)
        gap = exit_idx - idx
        self.assertLess(gap, 500)

    def test_llm_degraded_marker(self):
        self.assertIn("[LLM_DEGRADED]", self.src)
        self.assertIn("[LLM_DEGRADED] 深度分析失败", self.src)

    def test_6_field_emoji_set(self):
        """V37.9.45: 6 字段 (5 字段 + 🎚️ 项目对齐度)"""
        for emoji in ("📌", "🔑", "💡", "🎯", "⭐", "🎚️"):
            self.assertIn(emoji, self.src, msg=f"Missing 6 字段 emoji: {emoji}")

    def test_anti_hallucination_guard(self):
        self.assertIn("严禁虚构", self.src)

    def test_rating_dynamic_length(self):
        """V37.9.45 prompt 必须含按评级动态长度"""
        self.assertIn("⭐⭐⭐⭐⭐", self.src)
        self.assertTrue(re.search(r"500\s*-\s*800", self.src))

    def test_multi_window_pattern(self):
        """V37.9.21 多窗口: ≤8000 单段直发, >8000 切片 + sleep 1s + [i/N] 续段"""
        self.assertTrue(re.search(r"TOTAL_LEN.*-le\s+8000", self.src))
        self.assertIn("MAX_CHUNK = 4000", self.src)
        self.assertTrue(re.search(r"sleep\s+1\s*#.*乱序", self.src))
        self.assertIn("(续)", self.src)
        self.assertTrue(re.search(r"HF社区精选论文 \[1/", self.src))

    def test_no_placeholder_fallback_text(self):
        """V37.9.36 反模式: 老 3 字段占位符 (贡献：AI领域相关研究 / 价值：⭐⭐⭐) 必须清除"""
        for line_no, line in enumerate(self.src.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # 老 fallback: contrib = "贡献：AI领域相关研究"
            if "贡献：AI领域相关研究" in line:
                self.fail(
                    f"L{line_no} V37.9.36 占位符 '贡献：AI领域相关研究' 必须已清除: {line.strip()!r}"
                )
            # 老 fallback: stars = "价值：⭐⭐⭐"
            if re.search(r"""价值：⭐⭐⭐(?:["']|$)""", line):
                self.fail(
                    f"L{line_no} V37.9.36 占位符 '价值：⭐⭐⭐' 必须已清除: {line.strip()!r}"
                )

    def test_no_legacy_3_field_emit(self):
        """V37.8 老 3 字段 emit (cn_title/贡献/价值) 已替换为 6 字段"""
        # 老 emit 模式: msg_lines.append(stars) 配合 contrib + cn_title 严格 5 行 block
        self.assertNotIn(
            "msg_lines.append(stars)", self.src,
            msg="V37.8 老 emit 'msg_lines.append(stars)' 必须已清除"
        )

    def test_hf_specific_step25_github_enrichment_preserved(self):
        """HF-specific: Step 2.5 GitHub repo enrichment 必须保留 (V37.9.45 不动)"""
        self.assertIn("Step 2.5", self.src.replace("──", "──"))  # tolerant unicode dashes
        # 实际是 ── 2.5
        self.assertTrue(
            re.search(r"#.*2\.5.*GitHub.*Search", self.src) or
            re.search(r"## 2\.5", self.src) or
            "通过 GitHub Search 查找论文关联的代码仓库" in self.src,
            msg="HF-specific Step 2.5 GitHub enrichment 必须保留"
        )
        self.assertIn("github_url", self.src)
        self.assertIn("github_stars", self.src)


class TestHfPapersLlmDegradedFallback(unittest.TestCase):
    """V37.9.45 hf_papers LLM_DEGRADED 兜底逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HF_SCRIPT)

    def test_degraded_uses_abstract(self):
        """hf_papers LLM_DEGRADED 必须用 abstract 兜底 (而非占位符)"""
        self.assertIn("⚠️ [LLM_DEGRADED] 深度分析失败, 论文摘要供参考:", self.src)
        idx = self.src.find("[LLM_DEGRADED] 深度分析失败")
        self.assertGreater(idx, 0)
        chunk = self.src[idx:idx+800]
        # abstract 提取在兜底里
        self.assertIn("paper.get('abstract'", chunk)

    def test_degraded_explicit_no_data_message(self):
        """HF 无 abstract 时显式说明"""
        self.assertIn("(HF 无摘要数据, 请直接点链接阅读)", self.src)

    def test_hf_paper_url_format_preserved(self):
        """HF paper URL 格式必须保留 https://huggingface.co/papers/{paper_id}"""
        self.assertTrue(
            re.search(r"https://huggingface\.co/papers/", self.src),
            msg="HF paper URL 格式必须保留"
        )


class TestHfPapersInAuditAligned(unittest.TestCase):
    """V37.9.45 hf_papers 必须被 audit 识别为 aligned"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "_au_v9_45", os.path.join(REPO_ROOT, "ontology", "llm_cron_audit.py"))
        self.au = importlib.util.module_from_spec(spec)
        sys.modules["_au_v9_45"] = self.au
        spec.loader.exec_module(self.au)

    def test_hf_papers_in_aligned_with_v37_9_45(self):
        self.assertIn("jobs/hf_papers/run_hf_papers.sh", self.au.ALIGNED_SCRIPTS)
        self.assertEqual(
            self.au.ALIGNED_SCRIPTS["jobs/hf_papers/run_hf_papers.sh"], "V37.9.45"
        )

    def test_aligned_scripts_count_at_least_11(self):
        """V37.9.45 后 ALIGNED_SCRIPTS ≥11 (V37.9.44 10 + hf_papers)"""
        self.assertGreaterEqual(len(self.au.ALIGNED_SCRIPTS), 11)

    def test_audit_hf_papers_aligned_True(self):
        rep = self.au.audit_script(HF_SCRIPT)
        self.assertTrue(rep.exists)
        self.assertTrue(
            rep.aligned, msg=f"hf_papers 应识别为 aligned, score {rep.compliance_score}"
        )
        self.assertEqual(rep.aligned_version, "V37.9.45")
        self.assertEqual(len(rep.placeholder_findings), 0)


class TestProjectAlignmentField(unittest.TestCase):
    """V37.9.45 Opportunity Radar #2 PoC: 项目对齐度评分字段守卫"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HF_SCRIPT)

    def test_project_concepts_yaml_exists(self):
        """project_concepts.yaml 配置文件必须存在 (V37.9.45 PoC + V37.9.46 Stage 2 真理源)"""
        self.assertTrue(
            os.path.exists(PROJECT_CONCEPTS),
            msg=f"project_concepts.yaml 必须存在: {PROJECT_CONCEPTS}"
        )

    def test_project_concepts_yaml_schema(self):
        """project_concepts.yaml schema 必须含核心段"""
        content = _read(PROJECT_CONCEPTS)
        for required_section in ("project:", "core_planes:", "active_research_directions:",
                                  "excluded_topics:", "scoring_guide:", "control_plane:",
                                  "memory_plane:", "ontology:"):
            self.assertIn(required_section, content,
                          msg=f"project_concepts.yaml 缺少必需段: {required_section}")

    def test_prompt_has_alignment_field(self):
        """LLM prompt 必须含 🎚️ 项目对齐度 第 6 字段定义"""
        self.assertIn("🎚️ 项目对齐度", self.src)
        # 必须含评分指南 (5 档)
        for star_band in ("⭐⭐⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐", "⭐⭐", "⭐"):
            self.assertIn(star_band, self.src)
        # 必须含 OpenClaw 项目核心方向词 (供 LLM 评分参考)
        self.assertTrue(
            "control plane" in self.src.lower() or "control_plane" in self.src,
            msg="prompt 必须含 OpenClaw 项目方向词 control plane"
        )
        self.assertTrue(
            "ontology" in self.src.lower(),
            msg="prompt 必须含 OpenClaw 项目方向词 ontology"
        )

    def test_emit_displays_alignment_field(self):
        """emit 端必须显示 🎚️ 项目对齐度"""
        # parse_6field_output 函数必须存在
        self.assertIn("parse_6field_output", self.src)
        # alignment 字段必须在 fields dict 中
        self.assertTrue(
            re.search(r"'alignment'\s*:\s*''", self.src),
            msg="parse_6field_output 必须含 alignment 字段初始化"
        )
        # 推送显示 🎚️ 字段
        self.assertTrue(
            re.search(r"🎚️.*项目对齐度", self.src),
            msg="emit 端必须显示 🎚️ 项目对齐度"
        )

    def test_emit_high_alignment_count(self):
        """emit 末尾必须含'高对齐论文'统计 (V37.9.45 PoC 简化版, Stage 2 加专门 Top 5 段)"""
        self.assertIn("high_alignment_count", self.src)
        self.assertTrue(
            "本轮高对齐论文" in self.src,
            msg="emit 必须有高对齐统计行"
        )


if __name__ == "__main__":
    unittest.main()

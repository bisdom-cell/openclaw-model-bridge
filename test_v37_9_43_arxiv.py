#!/usr/bin/env python3
"""test_v37_9_43_arxiv.py — V37.9.43 arxiv_monitor 5 字段深度迁移守卫

V37.9.43 把 V37.9.36-37 / V37.9.39 / V37.9.40 / V37.9.41 fail-fast + 5 字段模式横向迁移到:
  - jobs/arxiv_monitor/run_arxiv.sh (ArXiv AI 论文监控, 每 3 小时整点 HKT)

血案防御 (V37.9.36 反模式硬规则保留):
  - V37.8 老 3 字段 fallback 占位符 (贡献：AI领域相关研究 / 价值：⭐⭐⭐) 必须清除
  - 用 [LLM_DEGRADED] 标记 + arxiv abstract 兜底替代

arxiv-specific 适配:
  - URL = https://arxiv.org/abs/{arxiv_id} (保留)
  - MAX_PAPERS = 10 (保留, vs HN 5)
  - arxiv API 返回完整 abstract (LLM_DEGRADED fallback 用 abstract[:300])
  - 5 字段 prompt 适配论文场景 (📌 中文标题 / 🔑 核心贡献 / 💡 关键方法 / 🎯 实践启发 / ⭐ 评级)
  - 按评级动态调长度 (⭐⭐⭐→100-150 / ⭐⭐⭐⭐→250-400 / ⭐⭐⭐⭐⭐→500-800)

audit 视角对齐 (V37.9.38 INV-LLMCRON-AUDIT-001):
  - ALIGNED_SCRIPTS 字典追加 'jobs/arxiv_monitor/run_arxiv.sh': 'V37.9.43'
  - placeholder_findings 必须为 0
  - SYSTEM_ALERT / source_notify / send_alert / status:llm_failed 标志 ✓
"""
import importlib.util
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
ARXIV_SCRIPT = os.path.join(REPO_ROOT, "jobs", "arxiv_monitor", "run_arxiv.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestArxivV9_43ShellGuards(unittest.TestCase):
    """V37.9.43 arxiv_monitor 脚本 source-level grep 守卫"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(ARXIV_SCRIPT)

    def test_v37_9_43_marker_present(self):
        self.assertIn("V37.9.43", self.src)

    def test_source_notify_sh_at_top(self):
        self.assertIn("NOTIFY_SH=", self.src)
        self.assertTrue(re.search(r'source\s+"\$NOTIFY_SH"', self.src))

    def test_send_alert_helper_with_system_alert(self):
        self.assertIn("send_alert()", self.src)
        m = re.search(r"send_alert\(\)\s*\{[^}]*\[SYSTEM_ALERT\]", self.src, re.DOTALL)
        self.assertIsNotNone(m)
        # 告警消息含 arxiv_monitor 名字让运维知道是哪个 job
        self.assertIn("[SYSTEM_ALERT] arxiv_monitor", self.src)

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
        """V37.9.43 顺序锁: status:llm_failed 写入后必须 500 字符内 exit 1 (fail-fast 契约)"""
        idx = self.src.find('"status":"llm_failed"')
        self.assertGreater(idx, 0)
        exit_idx = self.src.find("exit 1", idx)
        self.assertGreater(exit_idx, 0)
        gap = exit_idx - idx
        self.assertLess(gap, 500)

    def test_llm_degraded_marker(self):
        self.assertIn("[LLM_DEGRADED]", self.src)
        self.assertIn("[LLM_DEGRADED] 深度分析失败", self.src)

    def test_5_field_emoji_set(self):
        for emoji in ("📌", "🔑", "💡", "🎯", "⭐"):
            self.assertIn(emoji, self.src)

    def test_anti_hallucination_guard(self):
        self.assertIn("严禁虚构", self.src)

    def test_rating_dynamic_length(self):
        """V37.9.43 prompt 必须含按评级动态长度"""
        self.assertIn("⭐⭐⭐⭐⭐", self.src)
        # 旗舰论文 500-800 字段
        self.assertTrue(re.search(r"500\s*-\s*800", self.src))

    def test_multi_window_pattern(self):
        """V37.9.21 多窗口: ≤8000 单段直发, >8000 切片 + sleep 1s + [i/N] 续段"""
        self.assertTrue(re.search(r"TOTAL_LEN.*-le\s+8000", self.src))
        self.assertIn("MAX_CHUNK = 4000", self.src)
        self.assertTrue(re.search(r"sleep\s+1\s*#.*乱序", self.src))
        self.assertIn("(续)", self.src)
        self.assertTrue(re.search(r"📚 今日arXiv精选 \[1/", self.src))

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
        """V37.8 老 3 字段 emit (cn_title/贡献/价值) 已替换为 5 字段"""
        # 老 emit 模式: msg_lines.append(contrib) + msg_lines.append(stars)
        # 配合老格式 "作者：X | 日期：Y" 严格 5 行 block
        self.assertNotIn(
            "msg_lines.append(stars)", self.src,
            msg="V37.8 老 emit 'msg_lines.append(stars)' 必须已清除"
        )

    def test_no_legacy_l2_check(self):
        """V37.8 老 L2 解析率 < 0.5 → exit 2 检查必须清除 (V37.9.43 用 partial_degraded 替代)"""
        # V37.8 老逻辑: WARNING.*解析成功率过低 → exit 2 / status:parse_quality_low
        self.assertNotIn(
            '"status":"parse_quality_low"', self.src,
            msg="V37.8 老 L2 'parse_quality_low' status 已废弃, V37.9.43 用 partial_degraded 替代"
        )
        self.assertNotIn(
            "解析成功率过低", self.src,
            msg="V37.8 老 L2 '解析成功率过低' 检查已废弃"
        )


class TestArxivLlmDegradedFallback(unittest.TestCase):
    """V37.9.43 arxiv LLM_DEGRADED 兜底逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(ARXIV_SCRIPT)

    def test_degraded_uses_arxiv_abstract(self):
        """arxiv LLM_DEGRADED 必须用 abstract 兜底 (而非占位符)"""
        self.assertIn("⚠️ [LLM_DEGRADED] 深度分析失败, 论文摘要供参考:", self.src)
        idx = self.src.find("[LLM_DEGRADED] 深度分析失败")
        self.assertGreater(idx, 0)
        chunk = self.src[idx:idx+800]
        # abstract 提取在兜底里
        self.assertIn("paper.get('abstract'", chunk)

    def test_degraded_explicit_no_data_message(self):
        """arxiv 无 abstract 时显式说明 (vs 静默不显示)"""
        self.assertIn("(arxiv 无摘要数据, 请直接点链接阅读)", self.src)

    def test_arxiv_link_format_preserved(self):
        """arxiv URL 格式必须保留 https://arxiv.org/abs/{arxiv_id}"""
        self.assertTrue(re.search(r"https://arxiv\.org/abs/\$\{?arxiv_id\}?|https://arxiv\.org/abs/\{arxiv_id\}", self.src))


class TestArxivInAuditAligned(unittest.TestCase):
    """V37.9.43 arxiv_monitor 必须被 audit 识别为 aligned"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "_au_v9_43", os.path.join(REPO_ROOT, "ontology", "llm_cron_audit.py"))
        self.au = importlib.util.module_from_spec(spec)
        sys.modules["_au_v9_43"] = self.au
        spec.loader.exec_module(self.au)

    def test_arxiv_in_aligned_with_v37_9_43_or_later(self):
        """arxiv_monitor 必须在 ALIGNED_SCRIPTS, 版本字符串 V37.9.43 (原) 或 V37.9.51 (Sub-Stage 4b 升级)"""
        self.assertIn("jobs/arxiv_monitor/run_arxiv.sh", self.au.ALIGNED_SCRIPTS)
        version = self.au.ALIGNED_SCRIPTS["jobs/arxiv_monitor/run_arxiv.sh"]
        self.assertIn(version, ("V37.9.43", "V37.9.51"),
                      f"arxiv_monitor 应映射 V37.9.43 或 V37.9.51, 实际 {version!r}")

    def test_aligned_scripts_count_at_least_9(self):
        """V37.9.43 后 ALIGNED_SCRIPTS ≥9 (V37.9.41 8 + arxiv_monitor)"""
        self.assertGreaterEqual(len(self.au.ALIGNED_SCRIPTS), 9)

    def test_audit_arxiv_aligned_True(self):
        rep = self.au.audit_script(ARXIV_SCRIPT)
        self.assertTrue(rep.exists)
        self.assertTrue(
            rep.aligned, msg=f"arxiv 应识别为 aligned, score {rep.compliance_score}"
        )
        # V37.9.51 兼容: arxiv 从 V37.9.43 升级到 V37.9.51 (Sub-Stage 4b)
        self.assertIn(rep.aligned_version, ("V37.9.43", "V37.9.51"),
                      f"aligned_version 应为 V37.9.43 或 V37.9.51, 实际 {rep.aligned_version!r}")
        self.assertEqual(len(rep.placeholder_findings), 0)


if __name__ == "__main__":
    unittest.main()

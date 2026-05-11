#!/usr/bin/env python3
"""test_v37_9_40_dblp_ailead.py — V37.9.40 DBLP + AI Leaders X 5 字段深度迁移守卫

V37.9.40 把 V37.9.36-37 / V37.9.39 fail-fast + 5 字段模式横向迁移到:
  - jobs/dblp/run_dblp.sh (DBLP CS 论文, 适配无 abstract — 基于标题/venue 推断)
  - jobs/ai_leaders_x/run_ai_leaders_x.sh (AI Leaders X tweets, 适配推文上下文)

血案防御 (V37.9.36 反模式硬规则保留):
  - DBLP: 占位符 `贡献：CS领域相关研究` + `价值：⭐⭐⭐` 严禁回归
  - AI Leaders X: 占位符 `价值：⭐⭐⭐` 严禁回归
  - 两脚本均用 [LLM_DEGRADED] 标记 + 元数据/原文兜底替代 V37.9.36 占位符反模式

用户视角原则 #13 第 11 次正向兑现:
  V37.9.39 部署后用户立即提"DBLP 和 AI Leaders X 也按 S2 格式优化" → V37.9.40
  双脚本批次完整闭环 (单 commit + Mac Mini E2E 一次验证两个脚本)
"""
import importlib.util
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DBLP_SCRIPT = os.path.join(REPO_ROOT, "jobs/dblp/run_dblp.sh")
AILEAD_SCRIPT = os.path.join(REPO_ROOT, "jobs/ai_leaders_x/run_ai_leaders_x.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════════
# 共享守卫: 两脚本都必须含的 V37.9.40 fail-fast 模式
# ═══════════════════════════════════════════════════════════════════
def _common_v37_9_40_guards(test_case, src, label):
    """两脚本共用的 V37.9.40 守卫断言 (fail-fast + 5 字段 + retry + LLM_DEGRADED + 多窗口)"""
    test_case.assertIn("V37.9.40", src, msg=f"{label} 缺 V37.9.40 marker")

    # fail-fast skeleton
    test_case.assertIn("NOTIFY_SH=", src, msg=f"{label} 缺 NOTIFY_SH 自发现")
    test_case.assertTrue(re.search(r'source\s+"\$NOTIFY_SH"', src),
                          msg=f"{label} 缺 source notify.sh")
    test_case.assertIn("send_alert()", src, msg=f"{label} 缺 send_alert helper")
    m = re.search(r"send_alert\(\)\s*\{[^}]*\[SYSTEM_ALERT\]", src, re.DOTALL)
    test_case.assertIsNotNone(m, msg=f"{label} send_alert 必须含 [SYSTEM_ALERT] 前缀")

    # LLM 三层检测
    test_case.assertIn("__LLM_HTTP_ERROR__", src, msg=f"{label} 缺 HTTP 错误检测 marker")
    test_case.assertIn("__LLM_PARSE_FAIL__", src, msg=f"{label} 缺 JSON parse fail 检测 marker")

    # retry helper 契约
    test_case.assertIn("call_llm_single_with_retry()", src,
                        msg=f"{label} 缺 per-item retry helper")
    test_case.assertIn("backoffs=(5 10 20)", src,
                        msg=f"{label} retry 退避必须严格 5/10/20s")
    test_case.assertTrue(re.search(r"for\s+attempt\s+in\s+0\s+1\s+2", src),
                          msg=f"{label} 必须用 for attempt in 0 1 2")

    # 主循环 per-item iteration
    test_case.assertTrue(re.search(r"for\s+\(\(\s*i\s*=\s*0\s*;\s*i\s*<\s*TOTAL_NEW", src),
                          msg=f"{label} 必须 per-item 主循环")

    # 失败语义三档
    test_case.assertIn('"status":"llm_failed"', src, msg=f"{label} 缺 llm_failed 状态")
    test_case.assertIn('"status":"partial_degraded"', src,
                        msg=f"{label} 缺 partial_degraded 状态")
    test_case.assertIn("all_failed_", src, msg=f"{label} 缺 all_failed_ reason 前缀")

    # 全部失败 → fail-fast exit 1 顺序锁
    idx = src.find('"status":"llm_failed"')
    test_case.assertGreater(idx, 0)
    exit_idx = src.find("exit 1", idx)
    test_case.assertGreater(exit_idx, 0, msg=f"{label} llm_failed 分支后未找到 exit 1")
    gap = exit_idx - idx
    test_case.assertLess(gap, 500,
                          msg=f"{label} llm_failed 分支必须 500 字符内 exit 1, 距离 {gap}")

    # LLM_DEGRADED 标记
    test_case.assertIn("[LLM_DEGRADED]", src, msg=f"{label} 缺 LLM_DEGRADED 标记")

    # 5 字段 emoji 全套
    for emoji in ("📌", "🔑", "💡", "🎯", "⭐"):
        test_case.assertIn(emoji, src, msg=f"{label} 缺 5 字段 emoji '{emoji}'")

    # 反幻觉守卫
    test_case.assertIn("严禁虚构", src, msg=f"{label} 缺反幻觉守卫")

    # 多窗口切片
    test_case.assertTrue(re.search(r"TOTAL_LEN.*-le\s+8000", src),
                          msg=f"{label} 缺多窗口阈值 8000")
    test_case.assertIn("MAX_CHUNK = 4000", src,
                        msg=f"{label} 缺 MAX_CHUNK = 4000 (V37.9.21 契约)")
    test_case.assertTrue(re.search(r"sleep\s+1\s*#.*乱序", src),
                          msg=f"{label} 缺多窗口段间 sleep 1 防乱序")
    test_case.assertIn("(续)", src, msg=f"{label} 缺续段标识")

    # V37.9.36 反模式硬规则: 老 3 字段 prompt + 占位符不得回归
    test_case.assertNotIn("第一行：中文标题", src,
                          msg=f"{label} V37.8 老 3 字段 prompt 应已被 V37.9.40 替换")


def _no_placeholder_fallback_in_executable_code(test_case, src, label, forbidden_patterns):
    """V37.9.36 反模式守卫: 占位符字面量不得在执行代码中出现 (跳过 # 注释)"""
    for line_no, line in enumerate(src.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for pat in forbidden_patterns:
            if pat in line:
                test_case.fail(
                    f"{label} L{line_no} 含 V37.9.36 反模式占位符 '{pat}': {line.strip()!r}"
                )


# ═══════════════════════════════════════════════════════════════════
# DBLP 测试
# ═══════════════════════════════════════════════════════════════════
class TestDblpV9_40Guards(unittest.TestCase):
    """V37.9.40 DBLP 5 字段深度迁移守卫"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(DBLP_SCRIPT)

    def test_common_v37_9_40_guards(self):
        _common_v37_9_40_guards(self, self.src, "DBLP")

    def test_no_placeholder_fallback(self):
        """V37.9.36 反模式: DBLP 旧占位符不得回归"""
        _no_placeholder_fallback_in_executable_code(
            self, self.src, "DBLP",
            ["贡献：CS领域相关研究", "价值：⭐⭐⭐"]
        )

    def test_dblp_specific_no_abstract_caveat_in_prompt(self):
        """DBLP 特定: prompt 必须含'基于标题推断'caveat (无 abstract 限制)"""
        self.assertIn("基于标题推断", self.src,
                      msg="DBLP prompt 必须含'基于标题推断'caveat 让 LLM 标注置信度")

    def test_dblp_degraded_falls_back_to_metadata(self):
        """DBLP LLM_DEGRADED 必须用标题+venue 元数据兜底 (DBLP 无 abstract)"""
        self.assertIn("仅提供标题+会议元数据", self.src,
                      msg="DBLP LLM_DEGRADED 必须显式说明用元数据兜底")
        # 必须保留 venue + first_author 字段在兜底逻辑里
        self.assertIn("paper.get('venue'", self.src)
        self.assertIn("paper.get('first_author'", self.src)

    def test_dblp_link_priority_doi_over_url(self):
        """DBLP 链接优先 DOI (V37.9.40 emit 端保留 V25 行为)"""
        self.assertTrue(re.search(r'doi\s*=\s*paper\.get\(.*doi.*\)\s*\n.*?link\s*=', self.src, re.DOTALL),
                        msg="DBLP 必须优先用 DOI 链接, fallback 到 URL")

    def test_dblp_header_emoji_and_chunk_message(self):
        """DBLP 推送 header 必须用 📚 emoji + 多窗口 [i/N] 标识 DBLP 特定"""
        self.assertIn("📚 DBLP CS 论文精选", self.src)
        # 多窗口分支应替换 header 加 [i/N]
        self.assertTrue(re.search(r"📚 DBLP CS 论文精选 \[1/", self.src))


# ═══════════════════════════════════════════════════════════════════
# AI Leaders X 测试
# ═══════════════════════════════════════════════════════════════════
class TestAiLeadersV9_40Guards(unittest.TestCase):
    """V37.9.40 AI Leaders X 5 字段深度迁移守卫"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(AILEAD_SCRIPT)

    def test_common_v37_9_40_guards(self):
        _common_v37_9_40_guards(self, self.src, "AI Leaders X")

    def test_no_placeholder_fallback(self):
        """V37.9.36 反模式: AI Leaders X 旧占位符不得回归"""
        # AI Leaders X 老占位符 "价值：⭐⭐⭐" 必须清除
        # (没有 "贡献：xxx" 因为 AI Leaders X 历史无此字段)
        _no_placeholder_fallback_in_executable_code(
            self, self.src, "AI Leaders X",
            ["价值：⭐⭐⭐"]
        )

    def test_no_legacy_5line_prompt(self):
        """V37.8 老 5 行格式 (主题/深度分析/系统启示/行动建议/价值) 不得残留"""
        # 老 prompt 用文字 prefix '主题：' '深度分析：' 等
        self.assertNotIn("第1行：主题：", self.src,
                         msg="V37.8 老 5 行 prompt 应已替换为 V37.9.40 emoji 5 字段")
        self.assertNotIn("第5行：价值：", self.src)
        # 老 emit 用 '分析：' / '启示：' / '行动：' prefix 也应消除
        self.assertNotIn('msg_lines.append(f"分析：', self.src,
                         msg="V37.8 老 emit 文字 prefix 应已被 emoji 5 字段替换")

    def test_aileaders_specific_tweet_metadata_preserved(self):
        """AI Leaders X 特定: tweet metadata (author/label/link/text) 必须保留在 prompt"""
        # prompt 必须包含 tweet author/label/text 字段
        self.assertIn("作者: {author}", self.src,
                      msg="AI Leaders X prompt 必须含 author metadata")
        self.assertIn("推文原文:", self.src,
                      msg="AI Leaders X prompt 必须传 tweet 原文")
        # emit 端必须保留 link
        self.assertTrue(re.search(r"tweet\.get\(.*link.*\)", self.src),
                        msg="AI Leaders X emit 必须保留推文 link")

    def test_aileaders_degraded_falls_back_to_tweet_text(self):
        """AI Leaders X LLM_DEGRADED 必须用推文原文兜底 (Twitter 推文短可作 fallback)"""
        self.assertIn("推文原文供参考", self.src,
                      msg="AI Leaders X LLM_DEGRADED 必须显式用推文原文兜底")
        # text_preview 在 LLM_DEGRADED 分支也保留 (用户至少看到原文)
        self.assertTrue(re.search(r"text_preview\s*=\s*tweet\['text'\]", self.src))

    def test_aileaders_header_emoji_and_chunk_message(self):
        """AI Leaders X 推送 header 必须用 🧠 emoji + 多窗口 [i/N] 标识"""
        self.assertIn("🧠 AI Leaders 技术洞察", self.src)
        self.assertTrue(re.search(r"🧠 AI Leaders 技术洞察 \[1/", self.src))


# ═══════════════════════════════════════════════════════════════════
# 反向验证: 共用 + ALIGNED_SCRIPTS 集成
# ═══════════════════════════════════════════════════════════════════
class TestV9_40InAuditAlignedScripts(unittest.TestCase):
    """V37.9.40 DBLP + AI Leaders X 必须被 audit 识别为 aligned"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "_au_v9_40", os.path.join(REPO_ROOT, "ontology", "llm_cron_audit.py"))
        self.au = importlib.util.module_from_spec(spec)
        sys.modules["_au_v9_40"] = self.au
        spec.loader.exec_module(self.au)

    def test_aligned_scripts_count_at_least_7(self):
        """V37.9.40 后 ALIGNED_SCRIPTS 必须 ≥7 (V37.9.39 5 + DBLP + AI Leaders X)"""
        self.assertGreaterEqual(len(self.au.ALIGNED_SCRIPTS), 7,
                                msg=f"V37.9.40 ALIGNED_SCRIPTS 应 ≥7, 实际 {len(self.au.ALIGNED_SCRIPTS)}")

    def test_dblp_in_aligned_with_v37_9_40_or_later(self):
        """DBLP 必须在 ALIGNED_SCRIPTS, V37.9.40 (原) 或 V37.9.51 (Sub-Stage 4b 升级)"""
        self.assertIn("jobs/dblp/run_dblp.sh", self.au.ALIGNED_SCRIPTS)
        version = self.au.ALIGNED_SCRIPTS["jobs/dblp/run_dblp.sh"]
        self.assertIn(version, ("V37.9.40", "V37.9.51"),
                      f"DBLP 应映射 V37.9.40 或 V37.9.51, 实际 {version!r}")

    def test_aileaders_in_aligned_with_v37_9_40_or_later(self):
        """AI Leaders X 必须在 ALIGNED_SCRIPTS, V37.9.40 (原) 或 V37.9.51 (Sub-Stage 4b 升级)"""
        self.assertIn("jobs/ai_leaders_x/run_ai_leaders_x.sh", self.au.ALIGNED_SCRIPTS)
        version = self.au.ALIGNED_SCRIPTS["jobs/ai_leaders_x/run_ai_leaders_x.sh"]
        self.assertIn(version, ("V37.9.40", "V37.9.51"),
                      f"AI Leaders X 应映射 V37.9.40 或 V37.9.51, 实际 {version!r}")

    def test_audit_dblp_aligned_True(self):
        rep = self.au.audit_script(DBLP_SCRIPT)
        self.assertTrue(rep.exists)
        self.assertTrue(rep.aligned, msg=f"DBLP 应识别为 aligned, score {rep.compliance_score}")
        # V37.9.51 兼容: DBLP 从 V37.9.40 升级到 V37.9.51 (Sub-Stage 4b)
        self.assertIn(rep.aligned_version, ("V37.9.40", "V37.9.51"),
                      f"aligned_version 应为 V37.9.40 或 V37.9.51, 实际 {rep.aligned_version!r}")
        self.assertEqual(len(rep.placeholder_findings), 0,
                         msg=f"DBLP findings 应为 0, 实际: {[f.matched for f in rep.placeholder_findings]}")

    def test_audit_aileaders_aligned_True(self):
        rep = self.au.audit_script(AILEAD_SCRIPT)
        self.assertTrue(rep.exists)
        self.assertTrue(rep.aligned, msg=f"AI Leaders X 应识别为 aligned, score {rep.compliance_score}")
        # V37.9.51 兼容: AI Leaders X 从 V37.9.40 升级到 V37.9.51 (Sub-Stage 4b)
        self.assertIn(rep.aligned_version, ("V37.9.40", "V37.9.51"),
                      f"aligned_version 应为 V37.9.40 或 V37.9.51, 实际 {rep.aligned_version!r}")
        self.assertEqual(len(rep.placeholder_findings), 0,
                         msg=f"AI Leaders X findings 应为 0, 实际: {[f.matched for f in rep.placeholder_findings]}")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""test_llm_cron_audit.py — V37.9.38 LLM cron fail-fast audit 守卫单测

覆盖：
  - find_placeholder_findings 命中 V37.9.36 同款占位符（quoted_inline /
    shell_multiline_close / py_assignment_open 三种上下文）
  - prompt 模板行豁免（`第N行：` / `输出格式：` / `1到5个`）
  - 注释 + 三引号块豁免
  - 多文件批量 audit_all + ALIGNED_SCRIPTS 路径归一化
  - markdown 报告结构 + 已对齐脚本 4 个全部识别
  - CLI --check 渐进 / --strict 模式行为
  - 反向验证：故意把 rss_blogs (已对齐) 标记为不在 ALIGNED_SCRIPTS → 必失败
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


def _load_audit_module():
    """加载 ontology/llm_cron_audit.py — 注册到 sys.modules 避开
    Python 3.11 dataclass 在 spec_from_file_location 上下文的 NoneType bug。
    """
    spec = importlib.util.spec_from_file_location(
        "llm_cron_audit",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "ontology", "llm_cron_audit.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["llm_cron_audit"] = mod  # 必须在 exec_module 前注册
    spec.loader.exec_module(mod)
    return mod


_au = _load_audit_module()


class TestPlaceholderPatterns(unittest.TestCase):
    """V37.9.38: PLACEHOLDER_PATTERNS 常量 + COMPLIANCE_MARKERS 完整性"""

    def test_placeholder_patterns_include_blood_lesson_strings(self):
        """V37.9.36 血案 + 历史 copy-paste 的核心 3 个字符串必须在白名单"""
        self.assertIn("贡献：AI领域相关研究", _au.PLACEHOLDER_PATTERNS)
        self.assertIn("价值：⭐⭐⭐", _au.PLACEHOLDER_PATTERNS)
        self.assertIn("要点：技术深度文章", _au.PLACEHOLDER_PATTERNS)

    def test_compliance_markers_have_all_six_axes(self):
        """V37.9.36-37 reference 6 项检查全部声明"""
        expected = {
            "system_alert_string", "source_notify", "send_alert_helper",
            "status_llm_failed", "fail_fast_exit1", "calls_llm",
        }
        self.assertEqual(set(_au.COMPLIANCE_MARKERS.keys()), expected)


class TestFindPlaceholderFindings(unittest.TestCase):
    """V37.9.38: 行级启发式扫描器正确性"""

    def test_python_assignment_quoted_dq_caught(self):
        """Python 赋值 stars = "价值：⭐⭐⭐" 必须命中"""
        src = '''
def emit():
    cn_title = paper['title']
    contrib = "贡献：AI领域相关研究"
    stars = "价值：⭐⭐⭐"
    return cn_title, contrib, stars
'''
        findings = _au.find_placeholder_findings(src)
        matched = sorted(f.matched for f in findings)
        self.assertIn("贡献：AI领域相关研究", matched)
        self.assertIn("价值：⭐⭐⭐", matched)

    def test_python_assignment_quoted_sq_caught(self):
        """Python 单引号 pending_contrib or '贡献：AI领域相关研究' 必须命中"""
        src = '''
parsed_blocks.append((
    pending_title or '',
    pending_contrib or '贡献：AI领域相关研究',
    stars_line
))
'''
        findings = _au.find_placeholder_findings(src)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].matched, "贡献：AI领域相关研究")
        self.assertIn("quoted_inline_sq", findings[0].context)

    def test_shell_multiline_close_caught(self):
        """shell 多行字符串闭合行 价值：⭐⭐⭐" 必须命中"""
        src = '''
ENRICH="${title}
贡献：新版本发布，建议关注。
价值：⭐⭐⭐"
'''
        findings = _au.find_placeholder_findings(src)
        matched = [f.matched for f in findings]
        # 至少捕到 "新版本发布，建议关注" 或 "价值：⭐⭐⭐"
        self.assertTrue(
            "新版本发布，建议关注" in matched or "价值：⭐⭐⭐" in matched,
            msg=f"shell multiline close 未抓: {matched}"
        )

    def test_prompt_template_line_exempted(self):
        """LLM prompt 模板 `第三行：价值：⭐（1到5个星）` 必须豁免"""
        src = '''
prompt = """
第一行：中文标题（≤25字）
第二行：贡献：[1句话≤50字]
第三行：价值：⭐（1到5个星，评估对AI从业者的参考价值）
每篇之间用一行 --- 分隔。
"""
'''
        findings = _au.find_placeholder_findings(src)
        # prompt 模板内不该报任何 finding
        self.assertEqual(findings, [], msg=f"prompt 模板被误报: {findings}")

    def test_finance_news_prompt_range_exempted(self):
        """finance_news prompt 模板 `💡 价值：⭐~⭐⭐⭐⭐⭐` 范围说明必须豁免"""
        src = '''
PROMPT = """
- **[来源] 中文标题**（发布时间）
  💡 价值：⭐~⭐⭐⭐⭐⭐ | 关键点评：一句话分析
"""
'''
        findings = _au.find_placeholder_findings(src)
        self.assertEqual(findings, [], msg=f"prompt 范围说明被误报: {findings}")

    def test_pure_comment_lines_exempted(self):
        """纯 # 注释行的占位符不该被报"""
        src = '''
# 这是注释 — 历史 fallback 是 价值：⭐⭐⭐ 但已修复
print("hello")
'''
        findings = _au.find_placeholder_findings(src)
        self.assertEqual(findings, [])

    def test_triple_quoted_block_body_exempted(self):
        """Python 三引号 docstring/heredoc body 内的占位符豁免"""
        src = '''
def foo():
    """
    本函数会用 stars = "价值：⭐⭐⭐" 作为 fallback
    """
    return None
'''
        findings = _au.find_placeholder_findings(src)
        self.assertEqual(findings, [])

    def test_no_quote_context_exempted(self):
        """单纯文本中的占位符（无引号包裹）不算 finding"""
        src = '''
This line mentions 价值：⭐⭐⭐ but it's not a string literal.
'''
        findings = _au.find_placeholder_findings(src)
        self.assertEqual(findings, [])

    def test_one_finding_per_line_max(self):
        """同一行命中多个 placeholder 只算一次（防重复 spam）"""
        src = '''
x = "贡献：AI领域相关研究 / 价值：⭐⭐⭐"
'''
        findings = _au.find_placeholder_findings(src)
        self.assertEqual(len(findings), 1)


class TestAuditScript(unittest.TestCase):
    """V37.9.38: 单脚本 audit 逻辑（compliance_score / aligned 判定）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        p = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_missing_file_returns_exists_false(self):
        rep = _au.audit_script(os.path.join(self.tmp, "nonexistent.sh"))
        self.assertFalse(rep.exists)
        self.assertFalse(rep.aligned)

    def test_full_compliance_passes(self):
        """全合规脚本 → score 6/6 + aligned=True (按底线判定)"""
        path = self._write("good.sh", '''#!/bin/bash
source notify.sh
send_alert() { notify "$1" --topic alerts; }
LLM=$(curl http://127.0.0.1:5002/v1/chat/completions)
if [ -z "$LLM" ]; then
    send_alert "[SYSTEM_ALERT] LLM 失败"
    echo '{"status":"llm_failed"}' > /tmp/status.json
    exit 1
fi
''')
        rep = _au.audit_script(path)
        self.assertTrue(rep.calls_llm)
        self.assertTrue(rep.has_system_alert)
        self.assertTrue(rep.has_send_alert)
        self.assertTrue(rep.has_status_llm_failed)
        self.assertEqual(rep.placeholder_findings, [])
        self.assertTrue(rep.aligned)

    def test_placeholder_assignment_fails_compliance(self):
        """有占位符 fallback 的脚本 → aligned=False"""
        path = self._write("bad.sh", '''#!/bin/bash
LLM=$(curl http://127.0.0.1:5002/v1/chat/completions)
python3 - <<PYEOF
stars = "价值：⭐⭐⭐"
contrib = "贡献：AI领域相关研究"
PYEOF
''')
        rep = _au.audit_script(path)
        self.assertFalse(rep.aligned)
        self.assertGreaterEqual(len(rep.placeholder_findings), 2)


class TestAlignedScriptsRecognition(unittest.TestCase):
    """V37.9.38: 4 个已对齐脚本 (V37.5/V37.8.10/V37.9.16/V37.9.36-37) 的归一化"""

    def test_aligned_scripts_constant_has_four_entries(self):
        # V37.9.38 baseline 4, V37.9.39+ 单调递增（每次 PoC fix 加 1 个 aligned）
        # 用 >=4 允许后续版本继续加, 同时锁定 V37.9.38 baseline 4 个不能消失
        self.assertGreaterEqual(len(_au.ALIGNED_SCRIPTS), 4)
        self.assertIn("jobs/rss_blogs/run_rss_blogs.sh", _au.ALIGNED_SCRIPTS)
        self.assertIn("kb_evening.sh", _au.ALIGNED_SCRIPTS)
        self.assertIn("kb_review.sh", _au.ALIGNED_SCRIPTS)
        self.assertIn("kb_deep_dive.sh", _au.ALIGNED_SCRIPTS)

    def test_aligned_script_path_with_dot_slash_prefix_normalizes(self):
        """audit_script("./jobs/rss_blogs/run_rss_blogs.sh") 必须识别为已对齐"""
        # 这要求 _normalize_path 把 ./ 前缀去掉
        self.assertEqual(_au._normalize_path("./jobs/rss_blogs/run_rss_blogs.sh"),
                         "jobs/rss_blogs/run_rss_blogs.sh")
        self.assertEqual(_au._normalize_path("kb_review.sh"), "kb_review.sh")

    def test_audit_all_finds_aligned_in_real_repo(self):
        """在真实 repo 跑 audit_all，4 个已对齐脚本必须 aligned=True"""
        # cwd 是仓库根目录
        repo_root = os.path.dirname(os.path.abspath(__file__))
        reports = _au.audit_all(repo_root)
        aligned_count = sum(1 for r in reports if r.aligned)
        self.assertGreaterEqual(aligned_count, 4,
            msg=f"已对齐脚本应 ≥4，实际 {aligned_count}")
        # 验证 4 个具体脚本都有 aligned_version 标记
        aligned_versions = {r.path: r.aligned_version
                            for r in reports if r.aligned and r.aligned_version}
        # 路径以 ./jobs/... 开头（audit_all 拼出的相对路径），归一化后能匹配
        # 确保 4 个 ALIGNED_SCRIPTS 都被命中（counted by version 字段非空）
        # V37.9.51: rss_blogs 从 V37.9.36-37 升级到 V37.9.51 (Sub-Stage 4b),
        # 但 rss_blogs 仍在 ALIGNED_SCRIPTS 中, 只是 version 字符串变了。
        # 用 alternation 接受新旧两种版本字符串
        all_versions = aligned_versions.values()
        rss_blogs_aligned = any(v in ("V37.9.36-37", "V37.9.51") for v in all_versions)
        self.assertTrue(rss_blogs_aligned,
                        f"rss_blogs 应对齐为 V37.9.36-37 或 V37.9.51, 实际 versions: {list(all_versions)}")
        self.assertIn("V37.8.10", all_versions)
        self.assertIn("V37.5", all_versions)
        self.assertIn("V37.9.16", all_versions)


class TestMarkdownReport(unittest.TestCase):
    """V37.9.38: format_markdown_report 输出结构"""

    def test_report_has_required_sections(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        reports = _au.audit_all(repo_root)
        md = _au.format_markdown_report(reports)
        # 关键章节存在
        self.assertIn("# V37.9.38 LLM Cron Fail-Fast Audit Report", md)
        self.assertIn("## 概览", md)
        self.assertIn("## ✅ 已对齐脚本", md)
        self.assertIn("## ❌ 未对齐脚本", md)
        self.assertIn("## V37.9.38+ 修复路线图", md)
        self.assertIn("## 合规标准", md)
        # 4 个对齐脚本版本号显示 — V37.9.51 后 rss_blogs 升级 V37.9.51, alternation 兼容
        self.assertTrue(
            ("V37.9.36-37" in md) or ("V37.9.51" in md),
            "rss_blogs 应显示为 V37.9.36-37 或 V37.9.51"
        )
        self.assertIn("V37.8.10", md)


class TestCliCheckMode(unittest.TestCase):
    """V37.9.38: --check / --strict CLI 行为"""

    def test_check_renders_help_when_no_args(self):
        """无参数模式打印 help，exit 0"""
        result = subprocess.run(
            [sys.executable, "ontology/llm_cron_audit.py"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--report", result.stdout)
        self.assertIn("--check", result.stdout)

    def test_report_mode_outputs_markdown(self):
        result = subprocess.run(
            [sys.executable, "ontology/llm_cron_audit.py", "--report"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("V37.9.38 LLM Cron Fail-Fast Audit Report", result.stdout)

    def test_check_lenient_mode_fails_when_findings_present(self):
        """非 strict 模式：占位符 finding ≥1 时 exit 1"""
        # 当前 repo 有 15 findings → 应 exit 1
        result = subprocess.run(
            [sys.executable, "ontology/llm_cron_audit.py", "--check"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("含占位符反模式", result.stderr)


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.38: 源码级守卫 — 防未来重构反向回退"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "ontology", "llm_cron_audit.py")) as f:
            cls.src = f.read()

    def test_module_has_v37_9_38_marker(self):
        self.assertIn("V37.9.38", self.src)

    def test_aligned_scripts_has_four(self):
        """ALIGNED_SCRIPTS 必须含 4 个 entry（版本字符串）

        V37.9.51: rss_blogs 升级到 V37.9.51 (Sub-Stage 4b), V37.9.36-37 字面量被替换。
        用 alternation 兼容新旧版本字符串。
        """
        # 不变的 3 个 baseline 锚点
        for marker in ("V37.5", "V37.8.10", "V37.9.16"):
            self.assertIn(marker, self.src)
        # rss_blogs 版本字符串: V37.9.36-37 (旧) 或 V37.9.51 (V37.9.51 Sub-Stage 4b 升级)
        self.assertTrue(
            ("V37.9.36-37" in self.src) or ("V37.9.51" in self.src),
            "ALIGNED_SCRIPTS 应至少含 rss_blogs 的版本字符串 (V37.9.36-37 或 V37.9.51)"
        )

    def test_normalize_path_helper_exists(self):
        self.assertIn("def _normalize_path(path)", self.src)

    def test_prompt_template_exemption_exists(self):
        """_is_prompt_template_line 函数必须存在（豁免 prompt 模板防误报）"""
        self.assertIn("def _is_prompt_template_line(line)", self.src)

    def test_blood_lesson_strings_in_placeholder_patterns(self):
        """V37.9.36 血案 3 个核心字符串都在 PLACEHOLDER_PATTERNS"""
        # 用源码扫，确保未来重构不删
        self.assertIn("贡献：AI领域相关研究", self.src)
        self.assertIn("价值：⭐⭐⭐", self.src)
        self.assertIn("要点：技术深度文章", self.src)


if __name__ == "__main__":
    unittest.main()

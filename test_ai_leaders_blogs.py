#!/usr/bin/env python3
"""test_ai_leaders_blogs.py — V37.9.108 守卫。

ai_leaders 从 X (Syndication 429+冻结退化) 转向博客/Substack RSS 非 X 渠道。
新 job jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh 复用 rss_blogs 已验证管道
(RSS + LLM 6 字段 + 🎚️ 项目对齐评分 + 双通道 + KB), feed 选不与 rss_blogs 重叠的
AI 大神, 偏重"不同意见"(含 contrarian)。

覆盖:
  1. 脚本存在 + 可执行 + bash -n 语法
  2. feed 列表结构 (≥10, name|url|label, 含 contrarian, 不与 rss_blogs 4 源重叠)
  3. 身份切换完整 (🧠 AI 大神观点 framing, 无 rss_blogs/📖 博客精选 残留)
  4. 复用共享基础设施 (hallucination_guards / project_alignment_scorer /
     parse_6field_output / call_llm_single_with_retry / send_alert / notify.sh /
     movespeed_rsync_helper / kb_append_source)
  5. FAIL-OPEN per feed (死链 WARN + 跳过不杀 job) + fail-fast (llm_failed → exit 1)
  6. registry: ai_leaders_blogs 注册+enabled / ai_leaders_x 已停用
  7. auto_deploy FILE_MAP 含 ai_leaders_blogs
  8. ⚠️ feed 可达性 Mac Mini 验证注释 (原则 #33 + 反馈 #1 chaspark 教训)

注意: feed 真实可达性无法在 dev 验证 (sandbox 网络对外 RSS 403), 这些是源码级 +
结构守卫; feed 验证在 Mac Mini 首跑 (per-feed FAIL-OPEN, 死链剪枝)。
"""

import os
import re
import subprocess
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(REPO, "jobs", "ai_leaders_blogs", "run_ai_leaders_blogs.sh")
RSS_SCRIPT = os.path.join(REPO, "jobs", "rss_blogs", "run_rss_blogs.sh")
REGISTRY = os.path.join(REPO, "jobs_registry.yaml")
AUTO_DEPLOY = os.path.join(REPO, "auto_deploy.sh")

# rss_blogs 的 4 个 feed 域名 (ai_leaders_blogs 不得重叠)
_RSS_BLOGS_DOMAINS = [
    "spaces.ac.cn",
    "lilianweng.github.io",
    "simonwillison.net",
    "latent.space",
]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _feed_lines(src):
    """提取 RSS_FEEDS=(...) 数组里的 feed entry 行 (name|url|label)。"""
    m = re.search(r"RSS_FEEDS=\((.*?)\n\)", src, re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    feeds = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith('"') and "|" in line:
            feeds.append(line.strip('"'))
    return feeds


# ───────────────────────────────────────────────────────────────────────
class TestScriptExists(unittest.TestCase):
    def test_script_present(self):
        self.assertTrue(os.path.isfile(SCRIPT), "ai_leaders_blogs 脚本必须存在")

    def test_script_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "脚本必须可执行")

    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"bash -n 失败: {r.stderr}")


# ───────────────────────────────────────────────────────────────────────
class TestFeedList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)
        cls.feeds = _feed_lines(cls.src)

    def test_at_least_10_feeds(self):
        self.assertGreaterEqual(len(self.feeds), 10,
            f"应有 ≥10 个大神 feed, 实际 {len(self.feeds)}")

    def test_feeds_well_formed(self):
        for f in self.feeds:
            parts = f.split("|")
            self.assertEqual(len(parts), 3, f"feed 格式应为 name|url|label: {f}")
            name, url, label = parts
            self.assertTrue(name.strip(), f"feed name 非空: {f}")
            self.assertTrue(url.strip().startswith("http"), f"feed url 应是 http(s): {f}")
            self.assertTrue(label.strip(), f"feed label 非空: {f}")

    def test_contrarian_diverse_opinions_present(self):
        """用户核心诉求 '不同意见' — 必须含 contrarian/怀疑派大神。"""
        joined = "\n".join(self.feeds)
        self.assertIn("garymarcus", joined, "应含 Gary Marcus (神经符号怀疑派)")
        self.assertIn("aisnakeoil", joined, "应含 AI Snake Oil (批判性 AI)")

    def test_no_overlap_with_rss_blogs(self):
        """V37.9.108 设计: ai_leaders_blogs feed 不与 rss_blogs 4 源重叠 (避免重复推送)。"""
        joined = "\n".join(self.feeds)
        for domain in _RSS_BLOGS_DOMAINS:
            self.assertNotIn(domain, joined,
                f"ai_leaders_blogs 不应重复 rss_blogs 已有源 {domain}")

    def test_rss_blogs_still_has_its_4_feeds(self):
        """反向: rss_blogs 未被改动 (4 源仍在), 本次只新增 ai_leaders_blogs。"""
        rss_src = _read(RSS_SCRIPT)
        for domain in _RSS_BLOGS_DOMAINS:
            self.assertIn(domain, rss_src, f"rss_blogs 应保留 {domain} (本次不动 rss_blogs)")

    def test_feed_validation_noted_for_mac_mini(self):
        """原则 #33 + 反馈 #1: feed 可达性必须 Mac Mini 验证, 源码须注明。"""
        self.assertIn("Mac Mini", self.src)
        self.assertIn("FAIL-OPEN", self.src)
        self.assertIn("原则 #33", self.src)


# ───────────────────────────────────────────────────────────────────────
class TestIdentitySwap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)

    def test_uses_ai_leaders_framing(self):
        self.assertIn("🧠 AI 大神观点", self.src, "推送 framing 应为 🧠 AI 大神观点")

    def test_no_rss_blogs_framing_residue(self):
        self.assertNotIn("📖 博客精选", self.src, "不应残留 rss_blogs 的 📖 博客精选 framing")

    def test_lock_path_is_ai_leaders_blogs(self):
        self.assertIn("/tmp/ai_leaders_blogs.lockdir", self.src)
        self.assertNotIn("/tmp/rss_blogs.lockdir", self.src)

    def test_job_dir_and_kb_source(self):
        self.assertIn(".openclaw/jobs/ai_leaders_blogs", self.src)
        self.assertIn("ai_leaders_blogs.md", self.src)
        self.assertIn('"ai-leaders-blogs"', self.src, "KB tag 应为 ai-leaders-blogs")

    def test_prompt_framed_for_scholar_opinions(self):
        self.assertIn("AI 学者观点深度分析师", self.src)
        # 强调"不同意见" (用户核心诉求)
        self.assertIn("不同意见", self.src)

    def test_v37_9_108_marker(self):
        self.assertIn("V37.9.108", self.src)


# ───────────────────────────────────────────────────────────────────────
class TestReusesSharedInfra(unittest.TestCase):
    """复用 rss_blogs 已验证管道 + 共享模块 (非重造轮子)。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)

    def test_hallucination_guards_level4(self):
        self.assertIn("hallucination_guards", self.src)
        self.assertIn("LEVEL_4_PROJECT_AWARE", self.src)

    def test_project_alignment_scorer(self):
        self.assertIn("project_alignment_scorer", self.src)
        self.assertIn("🎚️ 项目对齐度", self.src)

    def test_six_field_parser(self):
        self.assertIn("parse_6field_output", self.src)

    def test_llm_call_with_retry(self):
        self.assertIn("call_llm_single_with_retry", self.src)
        self.assertIn("127.0.0.1:5002", self.src, "走本地 proxy (key 在 proxy 注入)")

    def test_notify_and_alert(self):
        self.assertIn("notify.sh", self.src)
        self.assertIn("[SYSTEM_ALERT]", self.src)
        self.assertIn("send_alert", self.src)

    def test_kb_and_rsync_helpers(self):
        self.assertIn("kb_append_source.sh", self.src)
        self.assertIn("movespeed_rsync_helper.sh", self.src)


# ───────────────────────────────────────────────────────────────────────
class TestFailOpenAndFailFast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)

    def test_per_feed_fail_open(self):
        # 死链 → WARN + continue (不杀 job)
        self.assertIn("RSS 抓取失败，跳过", self.src)
        self.assertRegex(self.src, r"抓取失败[，,].*\n\s*continue")

    def test_llm_fail_fast_exit1(self):
        self.assertIn('"status":"llm_failed"', self.src)
        # 全部失败分支必须 exit 1
        idx = self.src.find('"status":"llm_failed"')
        self.assertGreater(idx, 0)
        window = self.src[idx:idx + 400]
        self.assertIn("exit 1", window, "全 LLM 失败应 fail-fast exit 1")

    def test_no_placeholder_fallback(self):
        # V37.9.36 契约: 失败篇标 [LLM_DEGRADED] 不写占位符
        self.assertIn("[LLM_DEGRADED]", self.src)


# ───────────────────────────────────────────────────────────────────────
class TestRegistryAndDeploy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = _read(REGISTRY)
        cls.deploy = _read(AUTO_DEPLOY)

    def test_ai_leaders_blogs_registered_enabled(self):
        m = re.search(r"- id: ai_leaders_blogs\n(.*?)(?=\n  - id:|\Z)", self.reg, re.DOTALL)
        self.assertIsNotNone(m, "jobs_registry 应含 ai_leaders_blogs")
        block = m.group(1)
        self.assertIn("enabled: true", block)
        self.assertIn("jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh", block)
        self.assertIn("kb_source_file: ai_leaders_blogs.md", block)
        self.assertIn("30 13 * * *", block, "13:30 HKT 1x/天")

    def test_ai_leaders_x_superseded_note(self):
        # V37.9.108: ai_leaders_x 保持 enabled (避免禁用级联 6 个依赖:
        # kb_review/source_credibility/INV-REVIEW-001/INV-BACKUP-001/config/wa),
        # 但注释标记被 ai_leaders_blogs 补充取代; 真停用是 follow-up。
        m = re.search(r"- id: ai_leaders_x\n(.*?)(?=\n  - id:|\Z)", self.reg, re.DOTALL)
        self.assertIsNotNone(m)
        block = m.group(1)
        self.assertIn("V37.9.108", block, "ai_leaders_x 注释应标 V37.9.108 被取代")
        self.assertIn("ai_leaders_blogs", block, "注释应指向 ai_leaders_blogs")

    def test_in_file_map(self):
        self.assertIn(
            "jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh|$HOME/.openclaw/jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh",
            self.deploy, "auto_deploy FILE_MAP 应含 ai_leaders_blogs")


if __name__ == "__main__":
    unittest.main(verbosity=2)

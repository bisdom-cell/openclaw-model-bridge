#!/usr/bin/env python3
"""test_ai_leaders_bsky.py — V37.9.111 守卫。

ai_leaders 加实时短观点维度: 从 Bluesky 公开 AppView API (getAuthorFeed) 收集 AI
大神实时短帖, 补足 ai_leaders_blogs (长文, V37.9.108) + ai_leaders_x (X 429+冻结
退化产 ~0) 的实时维度。复用 ai_leaders_blogs/rss_blogs 已验证管道 (LLM 6 字段 +
🎚️ 项目对齐评分 + 反幻觉守卫 + 双通道 + KB), 仅抓取层换为 Bluesky getAuthorFeed JSON。

覆盖:
  1. 脚本存在 + 可执行 + bash -n 语法
  2. 账号列表结构 (≥8, handle|label, 含 contrarian 大神)
  3. Bluesky 抓取层 (public.api.bsky.app + getAuthorFeed + posts_no_replies)
  4. 身份切换 (🦋 AI 大神实时观点 framing, 无 rss_blogs/blogs framing 残留)
  5. 复用共享基础设施 (hallucination_guards / project_alignment_scorer /
     parse_6field_output / call_llm_single_with_retry / send_alert / notify.sh /
     movespeed_rsync_helper / kb_append_source)
  6. FAIL-OPEN per account (死 handle WARN + 跳过) + fail-fast (llm_failed → exit 1)
  7. registry: ai_leaders_bsky 注册+enabled / FILE_MAP / 17:00 cron
  8. source_credibility: ai_leaders_bsky 社媒 tier (Bluesky = 实时短帖)
  9. ⚠️ handle 可达性 Mac Mini 验证注释 (原则 #33 + 反馈 #1 chaspark 教训)
 10. **runtime parser behavior** — 真跑抽取的 JSON parser 验证 skip 转发/过短 +
     URI→URL 转换 + 外链提取 + dedup (Bluesky JSON 确定性, dev 可测;
     blogs 的 RSS parser 因需真 feed 而未测, 此处补上)。

注意: handle 真实可达性无法在 dev 验证 (sandbox 网络对外 403), 这些是源码级 +
结构 + JSON parser 行为守卫; handle 验证在 Mac Mini 首跑 (per-account FAIL-OPEN, 剪枝)。
"""

import json
import os
import re
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(REPO, "jobs", "ai_leaders_bsky", "run_ai_leaders_bsky.sh")
BLOGS_SCRIPT = os.path.join(REPO, "jobs", "ai_leaders_blogs", "run_ai_leaders_blogs.sh")
REGISTRY = os.path.join(REPO, "jobs_registry.yaml")
AUTO_DEPLOY = os.path.join(REPO, "auto_deploy.sh")
SOURCE_CRED = os.path.join(REPO, "source_credibility.py")

# 用户核心诉求 "不同意见" — 必须含的 contrarian/怀疑派大神 handle
# V37.9.111-hotfix: ylecun→yann-lecun (handle 漂移修复), fchollet 剪枝 (账号停用)
_CONTRARIAN_HANDLES = ["yann-lecun", "melaniemitchell", "rao2z", "randomwalker"]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _account_lines(src):
    """提取 BSKY_ACCOUNTS=(...) 数组里的 entry 行 (handle|label)。"""
    m = re.search(r"BSKY_ACCOUNTS=\((.*?)\n\)", src, re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    accounts = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith('"') and "|" in line:
            accounts.append(line.strip('"'))
    return accounts


def _extract_json_parser(src):
    """抽取脚本里 getAuthorFeed JSON parser heredoc (第一个 PYEOF block)。"""
    # 起点: $PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$BSKY_HANDLE" "$BSKY_LABEL" << 'PYEOF'
    start = src.find('$PYTHON3 - "$FEED_FILE" "$SEEN_FILE" "$BSKY_HANDLE" "$BSKY_LABEL" << \'PYEOF\'')
    assert start >= 0, "找不到 JSON parser heredoc 起点"
    body_start = src.find("\n", start) + 1
    end = src.find("\nPYEOF", body_start)
    assert end >= 0, "找不到 parser PYEOF 结尾"
    return src[body_start:end]


# ───────────────────────────────────────────────────────────────────────
class TestScriptExists(unittest.TestCase):
    def test_script_present(self):
        self.assertTrue(os.path.isfile(SCRIPT), "ai_leaders_bsky 脚本必须存在")

    def test_script_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "脚本必须可执行")

    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"bash -n 失败: {r.stderr}")


# ───────────────────────────────────────────────────────────────────────
class TestAccountList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)
        cls.accounts = _account_lines(cls.src)

    def test_at_least_8_accounts(self):
        self.assertGreaterEqual(len(self.accounts), 8,
            f"应有 ≥8 个大神账号, 实际 {len(self.accounts)}")

    def test_accounts_well_formed(self):
        for a in self.accounts:
            parts = a.split("|")
            self.assertEqual(len(parts), 2, f"账号格式应为 handle|label: {a}")
            handle, label = parts
            self.assertTrue(handle.strip(), f"handle 非空: {a}")
            self.assertTrue(label.strip(), f"label 非空: {a}")
            # handle 不应含空格 (Bluesky actor 是 handle 或域名)
            self.assertNotIn(" ", handle.strip(), f"handle 不应含空格: {a}")

    def test_contrarian_diverse_opinions_present(self):
        """用户核心诉求 '不同意见' — 必须含 contrarian/怀疑派大神。"""
        joined = "\n".join(self.accounts)
        for h in _CONTRARIAN_HANDLES:
            self.assertIn(h, joined, f"应含 contrarian 大神 handle: {h}")

    def test_no_duplicate_handles(self):
        handles = [a.split("|")[0].strip() for a in self.accounts]
        self.assertEqual(len(handles), len(set(handles)), "handle 不应重复")

    def test_handle_reachability_noted_for_mac_mini(self):
        """原则 #33 + 反馈 #1: handle 可达性必须 Mac Mini 验证, 源码须注明。"""
        self.assertIn("Mac Mini", self.src)
        self.assertIn("FAIL-OPEN", self.src)
        self.assertIn("原则 #33", self.src)

    def test_v37_9_111_hotfix_handle_drift_corrections(self):
        """V37.9.111-hotfix: 首跑 'Profile not found' handle 漂移修复 + Chollet 剪枝。

        高粉大神迁自定义域名释放 .bsky.social → 旧 handle 404。
        ylecun→yann-lecun (验证可用) / timnitgebru→timnitgebru.blacksky.app
        (searchActors 确认) / fchollet 账号停用 → 剪枝。
        """
        joined = "\n".join(self.accounts)
        # 旧错 handle 必须移除 (防回退)
        self.assertNotIn("ylecun.bsky.social", joined,
            "ylecun.bsky.social 是错 handle (Profile not found), 应为 yann-lecun.bsky.social")
        self.assertNotIn("timnitgebru.bsky.social", joined,
            "timnitgebru.bsky.social 已迁 blacksky.app")
        self.assertNotIn("fchollet.bsky.social", joined,
            "fchollet.bsky.social 账号停用 (searchActors 搜不到), 已剪枝")
        # 现 handle 必须在
        self.assertIn("yann-lecun.bsky.social", joined, "LeCun 现 handle = yann-lecun.bsky.social")
        self.assertIn("timnitgebru.blacksky.app", joined, "Gebru 现 handle = timnitgebru.blacksky.app")


# ───────────────────────────────────────────────────────────────────────
class TestBskyFetchLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)

    def test_uses_public_appview_api(self):
        self.assertIn("public.api.bsky.app", self.src,
            "应走 Bluesky 公开 AppView API (无需认证 + 缓存)")

    def test_uses_get_author_feed(self):
        self.assertIn("app.bsky.feed.getAuthorFeed", self.src)
        self.assertIn("actor=", self.src)

    def test_filters_no_replies(self):
        self.assertIn("filter=posts_no_replies", self.src, "应过滤回复, 只要原创+转发")

    def test_skips_reposts(self):
        # reasonRepost 跳过 (只要大神自己的原创帖)
        self.assertIn("reasonRepost", self.src)

    def test_min_post_chars_filter(self):
        self.assertIn("MIN_POST_CHARS", self.src, "应有过短帖过滤 (避免对琐碎短帖做 6 字段分析)")

    def test_uri_to_web_url(self):
        # at://did/app.bsky.feed.post/rkey → https://bsky.app/profile/{handle}/post/{rkey}
        self.assertIn("bsky.app/profile/", self.src)
        self.assertIn("rsplit('/', 1)", self.src)


# ───────────────────────────────────────────────────────────────────────
class TestIdentitySwap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)

    def test_uses_bsky_framing(self):
        self.assertIn("🦋 AI 大神实时观点", self.src, "推送 framing 应为 🦋 AI 大神实时观点")

    def test_no_blogs_or_rss_framing_residue(self):
        self.assertNotIn("📖 博客精选", self.src, "不应残留 rss_blogs framing")
        self.assertNotIn("🧠 AI 大神观点", self.src, "不应残留 ai_leaders_blogs (长文) framing")

    def test_lock_path_is_ai_leaders_bsky(self):
        self.assertIn("/tmp/ai_leaders_bsky.lockdir", self.src)
        self.assertNotIn("/tmp/ai_leaders_blogs.lockdir", self.src)

    def test_job_dir_and_kb_source(self):
        self.assertIn(".openclaw/jobs/ai_leaders_bsky", self.src)
        self.assertIn("ai_leaders_bsky.md", self.src)
        self.assertIn('"ai-leaders-bsky"', self.src, "KB tag 应为 ai-leaders-bsky")

    def test_prompt_framed_for_realtime_short_posts(self):
        self.assertIn("AI 学者实时观点分析师", self.src)
        self.assertIn("不同意见", self.src)
        # 短帖防过度膨胀 (V37.9.40 ai_leaders_x 过度分析教训)
        self.assertIn("不要为短帖编造长篇分析", self.src)

    def test_v37_9_111_marker(self):
        self.assertIn("V37.9.111", self.src)


# ───────────────────────────────────────────────────────────────────────
class TestReusesSharedInfra(unittest.TestCase):
    """复用 ai_leaders_blogs/rss_blogs 已验证管道 (MR-8 非重造轮子)。"""

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

    def test_blogs_unchanged(self):
        """反向: ai_leaders_blogs 未被改动 (本次只新增 ai_leaders_bsky)。"""
        blogs = _read(BLOGS_SCRIPT)
        self.assertIn("🧠 AI 大神观点", blogs, "ai_leaders_blogs 应保留其 framing")
        self.assertIn("RSS_FEEDS", blogs, "ai_leaders_blogs 应保留 RSS 抓取 (本次不动)")


# ───────────────────────────────────────────────────────────────────────
class TestFailOpenAndFailFast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read(SCRIPT)

    def test_per_account_fail_open(self):
        # 死 handle → WARN + continue (不杀 job)。V37.9.238: 允许 FETCH_ERRORS 计数行
        # 插在 WARN 与 continue 之间（FAIL-OPEN 语义不变，失败被计数可观测）。
        self.assertIn("Bluesky 抓取失败，跳过", self.src)
        self.assertRegex(self.src, r"抓取失败[，,].*\n(\s*FETCH_ERRORS=\$\(\(FETCH_ERRORS \+ 1\)\)\n)?\s*continue")

    def test_llm_fail_fast_exit1(self):
        self.assertIn('"status":"llm_failed"', self.src)
        idx = self.src.find('"status":"llm_failed"')
        self.assertGreater(idx, 0)
        window = self.src[idx:idx + 400]
        self.assertIn("exit 1", window, "全 LLM 失败应 fail-fast exit 1")

    def test_no_placeholder_fallback(self):
        self.assertIn("[LLM_DEGRADED]", self.src)


# ───────────────────────────────────────────────────────────────────────
class TestRegistryAndDeploy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = _read(REGISTRY)
        cls.deploy = _read(AUTO_DEPLOY)

    def test_ai_leaders_bsky_registered_enabled(self):
        m = re.search(r"- id: ai_leaders_bsky\n(.*?)(?=\n  - id:|\Z)", self.reg, re.DOTALL)
        self.assertIsNotNone(m, "jobs_registry 应含 ai_leaders_bsky")
        block = m.group(1)
        self.assertIn("enabled: true", block)
        self.assertIn("jobs/ai_leaders_bsky/run_ai_leaders_bsky.sh", block)
        self.assertIn("kb_source_file: ai_leaders_bsky.md", block)
        self.assertIn("0 17 * * *", block, "17:00 HKT 1x/天")

    def test_in_file_map(self):
        self.assertIn(
            "jobs/ai_leaders_bsky/run_ai_leaders_bsky.sh|$HOME/.openclaw/jobs/ai_leaders_bsky/run_ai_leaders_bsky.sh",
            self.deploy, "auto_deploy FILE_MAP 应含 ai_leaders_bsky")


# ───────────────────────────────────────────────────────────────────────
class TestSourceCredibility(unittest.TestCase):
    """新内容源触发 MR-8 source_credibility drift guard (V37.9.108 同款)。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(SOURCE_CRED)
        cls.reg = _read(REGISTRY)

    def test_ai_leaders_bsky_in_credibility(self):
        self.assertIn('"ai_leaders_bsky"', self.src,
            "source_credibility 应含 ai_leaders_bsky (新内容源)")

    def test_bsky_is_social_media_tier(self):
        # Bluesky 实时短帖 = 社媒 tier (rank 5, 同 X 推文)
        m = re.search(r'"ai_leaders_bsky":\s*\{([^}]*)\}', self.src)
        self.assertIsNotNone(m, "应有 ai_leaders_bsky 条目")
        entry = m.group(1)
        self.assertIn("社媒", entry, "Bluesky 实时短帖应为 社媒 tier")

    def test_label_matches_registry(self):
        """MR-8: credibility label 必须与 jobs_registry kb_source_label 精确一致。"""
        cm = re.search(r'"ai_leaders_bsky":\s*\{[^}]*"label":\s*"([^"]+)"', self.src)
        self.assertIsNotNone(cm)
        cred_label = cm.group(1)
        rm = re.search(r"- id: ai_leaders_bsky\n(.*?)(?=\n  - id:|\Z)", self.reg, re.DOTALL)
        self.assertIsNotNone(rm)
        rmatch = re.search(r"kb_source_label:\s*(.+)", rm.group(1))
        self.assertIsNotNone(rmatch)
        reg_label = rmatch.group(1).strip()
        self.assertEqual(cred_label, reg_label,
            f"credibility label '{cred_label}' 必须 == registry kb_source_label '{reg_label}'")


# ───────────────────────────────────────────────────────────────────────
class TestJsonParserBehavior(unittest.TestCase):
    """runtime: 真跑抽取的 getAuthorFeed JSON parser, 验证核心解析逻辑。

    Bluesky JSON 解析是确定性的 (无网络), dev 可测 — 比 blogs 的 RSS parser
    (需真 feed) 更可测。此处补上 parser 行为守卫防回归。
    """

    @classmethod
    def setUpClass(cls):
        cls.parser_code = _extract_json_parser(_read(SCRIPT))
        cls.fixture = {
            "feed": [
                {  # 1: 实质原创帖 (应保留)
                    "post": {
                        "uri": "at://did:plc:abc/app.bsky.feed.post/3lpost1",
                        "author": {"handle": "ylecun.bsky.social", "displayName": "Yann LeCun"},
                        "record": {"text": "LLMs are an off-ramp on the road to human-level AI; we need world models and planning.", "createdAt": "2026-06-05T08:00:00Z"},
                    }
                },
                {  # 2: 过短 (应跳过)
                    "post": {
                        "uri": "at://did:plc:abc/app.bsky.feed.post/3lshort",
                        "author": {"handle": "ylecun.bsky.social"},
                        "record": {"text": "Yes! 100%", "createdAt": "2026-06-05T07:30:00Z"},
                    }
                },
                {  # 3: 转发 (应跳过)
                    "post": {
                        "uri": "at://did:plc:xxx/app.bsky.feed.post/3lrepost",
                        "author": {"handle": "other.bsky.social"},
                        "record": {"text": "A long enough reposted post that would pass length filter for sure.", "createdAt": "2026-06-05T06:00:00Z"},
                    },
                    "reason": {"$type": "app.bsky.feed.defs#reasonRepost"},
                },
                {  # 4: 带外链 (应保留 + 捕获链接)
                    "post": {
                        "uri": "at://did:plc:def/app.bsky.feed.post/3lpost4",
                        "author": {"handle": "melaniemitchell.bsky.social", "displayName": "Melanie Mitchell"},
                        "record": {"text": "New paper: the LLM reasoning benchmarks are misleading and overstate capabilities.", "createdAt": "2026-06-05T07:00:00Z"},
                        "embed": {"$type": "app.bsky.embed.external#view", "external": {"uri": "https://arxiv.org/abs/2506.99999", "title": "Can LLMs Reason?"}},
                    }
                },
            ]
        }

    def _run_parser(self, fixture, seen_lines):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "parser.py")
            ff = os.path.join(d, "feed.json")
            sf = os.path.join(d, "seen.txt")
            with open(pf, "w", encoding="utf-8") as f:
                f.write(self.parser_code)
            with open(ff, "w", encoding="utf-8") as f:
                json.dump(fixture, f)
            with open(sf, "w", encoding="utf-8") as f:
                f.write("\n".join(seen_lines))
            r = subprocess.run(
                ["python3", pf, ff, sf, "ylecun.bsky.social", "TestLabel"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, f"parser 应 exit 0 (FAIL-OPEN): {r.stderr}")
            out = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
            return out, r.stderr

    def test_skips_reposts_and_short_keeps_originals(self):
        out, _ = self._run_parser(self.fixture, [])
        # 4 帖中: 1 实质 + 1 过短(跳) + 1 转发(跳) + 1 带链 = 应保留 2
        self.assertEqual(len(out), 2, f"应保留 2 帖 (跳过过短+转发): {[o['link'] for o in out]}")
        links = [o["link"] for o in out]
        self.assertIn("https://bsky.app/profile/ylecun.bsky.social/post/3lpost1", links)
        self.assertIn("https://bsky.app/profile/melaniemitchell.bsky.social/post/3lpost4", links)

    def test_uri_converted_to_web_url(self):
        out, _ = self._run_parser(self.fixture, [])
        for o in out:
            self.assertTrue(o["link"].startswith("https://bsky.app/profile/"),
                f"URI 应转成 web URL: {o['link']}")
            self.assertIn("/post/", o["link"])

    def test_external_link_captured(self):
        out, _ = self._run_parser(self.fixture, [])
        mitchell = [o for o in out if "melaniemitchell" in o["link"]][0]
        self.assertIn("附带链接", mitchell["description"], "外链应进 description 给 LLM")
        self.assertIn("arxiv.org/abs/2506.99999", mitchell["description"])

    def test_dedup_by_seen_url(self):
        seen = ["https://bsky.app/profile/ylecun.bsky.social/post/3lpost1"]
        out, _ = self._run_parser(self.fixture, seen)
        self.assertEqual(len(out), 1, "已 seen 的帖应被去重")
        self.assertIn("melaniemitchell", out[0]["link"])

    def test_corrupt_json_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "parser.py")
            ff = os.path.join(d, "bad.json")
            sf = os.path.join(d, "seen.txt")
            with open(pf, "w", encoding="utf-8") as f:
                f.write(self.parser_code)
            with open(ff, "w", encoding="utf-8") as f:
                f.write("not json {")
            open(sf, "w").close()
            r = subprocess.run(["python3", pf, ff, sf, "x", "y"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "损坏 JSON 应 FAIL-OPEN exit 0")
            self.assertEqual(r.stdout.strip(), "", "损坏 JSON 不应产出帖子")


if __name__ == "__main__":
    unittest.main(verbosity=2)

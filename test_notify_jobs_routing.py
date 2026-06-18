#!/usr/bin/env python3
"""V37.9.171 内容 job 主推走 notify.sh 路由守卫（Path B 第一批：单频道 job）。

背景：V37.9.170 修好 notify.sh 微信分支后，发现 ~17 个内容 job 的主推/分块仍硬编码
`message send --channel whatsapp --target "$TO"` + 紧跟独立 discord 发送 —— WhatsApp
临时禁用后这些推送静默失败、微信收不到。本批把 10 个单频道 job 的主推+分块改走
`notify "$MSG" --topic X`（微信→用户 + Discord #topic + 重试 + 队列，兑现 INV-NOTIFY-003
"所有推送经 notify.sh"），退役裸 whatsapp+discord 对（日落法：每处 2 发送 → 1 notify）。

本测试守：
  - 主推 + 分块确实走 notify --topic（正确 topic）
  - 无残留裸 whatsapp 主推/分块（防回归）
  - 无重复 discord 主推/分块（notify 已发 discord，删了独立行防双发）
  - alert fallback（send_alert 的 $msg 行）刻意保留，不在守卫范围

多频道 job（arxiv/s2 papers+ontology、freight、openclaw_official、finance 特殊格式、
ontology_sources）+ 根脚本（kb_*/health_check 等）属 Path B 第二批，不在此守卫。
"""
import os
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))

# job 相对路径 → 期望 notify topic
CONVERTED_JOBS = {
    "jobs/hf_papers/run_hf_papers.sh": "papers",
    "jobs/dblp/run_dblp.sh": "papers",
    "jobs/acl_anthology/run_acl_anthology.sh": "papers",
    "jobs/hn_watcher/run_hn_fixed.sh": "tech",
    "jobs/rss_blogs/run_rss_blogs.sh": "tech",
    "jobs/github_trending/run_github_trending.sh": "tech",
    "jobs/karpathy_x/run_karpathy_x.sh": "tech",
    "jobs/ai_leaders_blogs/run_ai_leaders_blogs.sh": "tech",
    "jobs/ai_leaders_bsky/run_ai_leaders_bsky.sh": "tech",
    "jobs/ai_leaders_x/run_ai_leaders_x.sh": "tech",
    # V37.9.171 PathB-2: 多频道 job 的主推同标准结构（ontology/alerts 第二频道单独处理）
    "jobs/arxiv_monitor/run_arxiv.sh": "papers",
    "jobs/semantic_scholar/run_semantic_scholar.sh": "papers",
    "jobs/openclaw_official/run.sh": "tech",
    "jobs/openclaw_official/run_discussions.sh": "tech",
}


def _read(rel):
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


class TestJobsRouteMainPushThroughNotify(unittest.TestCase):
    def test_main_and_chunk_use_notify_with_correct_topic(self):
        for rel, topic in CONVERTED_JOBS.items():
            src = _read(rel)
            self.assertIn(f'notify "$MSG_CONTENT" --topic {topic}', src,
                          f"{rel} 主推未走 notify --topic {topic}")
            self.assertIn(f'notify "$CHUNK_CONTENT" --topic {topic}', src,
                          f"{rel} 分块未走 notify --topic {topic}")

    def test_no_raw_whatsapp_main_or_chunk(self):
        # 防回归：主推/分块不得再出现裸 whatsapp send
        for rel in CONVERTED_JOBS:
            src = _read(rel)
            for var in ("MSG_CONTENT", "CHUNK_CONTENT"):
                bad = f'--channel whatsapp --target "$TO" --message "${var}"'
                self.assertNotIn(bad, src,
                                 f"{rel} 仍有裸 whatsapp 主推/分块 ({var}) — 应走 notify")

    def test_no_duplicate_discord_main_or_chunk(self):
        # notify 已发 discord；独立 discord 主推/分块行必须删除（防双发）
        for rel in CONVERTED_JOBS:
            src = _read(rel)
            for ch in ("DISCORD_CH_PAPERS", "DISCORD_CH_TECH"):
                for var in ("MSG_CONTENT", "CHUNK_CONTENT"):
                    bad = f'--channel discord --target "${{{ch}:-}}" --message "${var}"'
                    self.assertNotIn(bad, src,
                                     f"{rel} 残留重复 discord 主推/分块 ({ch}/{var})")

    def test_jobs_still_source_notify_sh(self):
        # notify 路由前提：job 必须 source notify.sh
        for rel in CONVERTED_JOBS:
            src = _read(rel)
            self.assertIn("notify.sh", src, f"{rel} 未 source notify.sh")

    def test_alert_fallback_preserved(self):
        # send_alert 的 notify --topic alerts 应仍在（fail-fast 告警不动）
        for rel in CONVERTED_JOBS:
            src = _read(rel)
            self.assertIn("--topic alerts", src, f"{rel} 丢失 alert 推送")

    def test_v37_9_171_marker(self):
        for rel in CONVERTED_JOBS:
            src = _read(rel)
            self.assertIn("V37.9.171", src, f"{rel} 缺 V37.9.171 标记")


class TestBatch2SpecialFormatJobs(unittest.TestCase):
    """V37.9.171 PathB-2 非标准结构 job（freight/kb_inject/kb_deep_dive）路由守卫。"""

    def test_freight_routes_content_through_notify(self):
        src = _read("jobs/freight_watcher/run_freight.sh")
        self.assertIn('notify "$(cat "$MSG_FILE")" --topic freight', src)
        self.assertIn("--topic freight", src)  # 客户画像也走 freight
        self.assertIn("notify.sh", src)  # freight 本批补 source
        # 防回归：内容主推不得再用裸 whatsapp
        self.assertNotIn('--channel whatsapp --target "$TO" --message "$(cat "$MSG_FILE")"', src)
        self.assertIn("V37.9.171", src)

    def test_kb_inject_routes_daily_through_notify(self):
        src = _read("kb_inject.sh")
        self.assertIn('notify "$WA_MSG" --topic daily', src)
        self.assertIn("notify.sh", src)  # kb_inject 本批补 source
        self.assertNotIn('--channel whatsapp --target "$PHONE" --message "$WA_MSG"', src)
        self.assertIn("V37.9.171", src)

    def test_kb_deep_dive_no_forced_whatsapp_channel(self):
        # 修复 --channel whatsapp 强制（绕过微信）：notify 段推送不得再带 --channel whatsapp
        src = _read("kb_deep_dive.sh")
        self.assertNotIn('notify "$WA_SEGMENT" --channel whatsapp', src,
                         "kb_deep_dive 仍强制 --channel whatsapp，绕过微信")
        self.assertIn('notify "$WA_SEGMENT" --topic deep_dive', src)
        self.assertIn("V37.9.171", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""test_v37_9_281_push_honesty.py — V37.9.281 notify 队列完整性 + job 推送诚实守卫

2026-07-26 对抗审计 (4 镜头 14 findings) 的 notify/jobs 批次修复守卫:

  NOT-F1  notify.sh claim 偷取: rename(2) 保留 mtime → 存活 claim 被并发 drainer
          的孤儿回收 (find -mmin +10) 偷回再夺取 → 同一条消息双发。修复 = claim
          后立即 touch (孤儿判定量 claim 年龄而非队列条目年龄)。
  NOT-F2  驱逐分类器: (a) openclaw CLI 缺失时 bash "No such file or directory"
          撞裸子串 → 整个队列被清空 — 修复 = drain 前 command -v 守卫;
          (b) 裸 "no such|not found" 收紧为须带 target/channel 上下文。
  JOB-F2  arxiv/dblp/s2 ontology 推送 + finance_news 主推送: 裸 notify 在
          set -e 下失败即杀脚本 → status 已 ok 而 KB 归档/备份/status 写入被
          静默跳过。修复 = || log WARN 继续。
  JOB-F3  chaspark: notify rc 被丢弃 + 无条件 WA_SENT=true → 推送失败仍记
          status ok/sent:true (send_failed 分支不可达)。修复 = if notify 检查。
  JOB-F4  chaspark: 解析期即重写 seen 文件 → LLM 全失败日 (exit 1) 文章已标
          seen, 次日 new:0 status ok = 当日内容永久丢失且恢复日看似健康。
          修复 = NEW_IDS_FILE 延迟到推送成功后并入 (对齐 arxiv 契约)。
  NOT-F4  wa_keepalive: || true + 无条件 ESCALATED 日志把失败叙述成已送达
          (fail-plausible) 且无兜底。修复 = rc 检查 + .openclaw_alerts.log 兜底。
"""
import os
import re
import subprocess
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))


def _read(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f:
        return f.read()


class TestNotifyQueueIntegrity(unittest.TestCase):
    """NOT-F1 / NOT-F2: notify.sh 队列完整性。"""

    def setUp(self):
        self.src = _read("notify.sh")

    def test_claim_touched_after_mv(self):
        # NOT-F1: mv 夺取 claim 后必须紧跟 touch (刷新 mtime 防被孤儿回收偷走)
        m = re.search(
            r'mv "\$f" "\$claim" 2>/dev/null \|\| continue\n'
            r'(?:\s*#[^\n]*\n)*\s*touch "\$claim"',
            self.src)
        self.assertIsNotNone(
            m, "claim mv 后必须 touch — rename 保留 mtime, 存活 claim 会被并发 "
               "drainer 的 -mmin +10 孤儿回收偷回 → 双发 (NOT-F1)")

    def test_drain_guards_missing_cli(self):
        # NOT-F2a: CLI 缺失时跳过 drain 保队列 (否则 bash 'No such file or
        # directory' 撞永久错误分类器 → 整队列被清空)
        self.assertIn('command -v "$OPENCLAW"', self.src)
        self.assertIn("DRAIN SKIP", self.src)

    def _evict_pattern(self):
        m = re.search(r'grep -qiE "([^"]*unknown target[^"]*)"', self.src)
        self.assertIsNotNone(m, "evict 分类器 grep 模式必须存在")
        return m.group(1)

    def _grep_matches(self, sample, pattern):
        r = subprocess.run(["bash", "-c", 'echo "$1" | grep -qiE "$2"',
                            "_", sample, pattern])
        return r.returncode == 0

    def test_evict_classifier_ignores_non_target_errors(self):
        # NOT-F2b 血案回归: 环境/HTTP 错误不得被判永久淘汰
        pat = self._evict_pattern()
        self.assertFalse(
            self._grep_matches(
                "/opt/homebrew/bin/openclaw: No such file or directory", pat),
            "二进制缺失的 bash 错误被判永久 → 整队列清空 (NOT-F2 血案)")
        self.assertFalse(
            self._grep_matches("HTTP 404 Not Found", pat),
            "gateway 版本 skew 的 404 是瞬态, 不得淘汰")

    def test_evict_classifier_still_catches_bad_targets(self):
        # V37.9.177 原语义保留: 真 target/channel 无效仍淘汰 (防 poison 队头)
        pat = self._evict_pattern()
        self.assertTrue(self._grep_matches(
            "Error: unknown target +85200000000", pat))
        self.assertTrue(self._grep_matches('channel "xyz" not found', pat))
        self.assertTrue(self._grep_matches("no such target: foo", pat))


class TestJobPushGuards(unittest.TestCase):
    """JOB-F2: 裸 notify 在 set -e 下杀脚本的 4 个站点守卫。"""

    def test_ontology_push_guarded(self):
        for rel in ("jobs/arxiv_monitor/run_arxiv.sh",
                    "jobs/dblp/run_dblp.sh",
                    "jobs/semantic_scholar/run_semantic_scholar.sh"):
            src = _read(rel)
            self.assertRegex(
                src,
                r'notify "\$ONTO_CONTENT" --channel discord --topic ontology \|\|',
                f"{rel}: ontology notify 必须带 || 守卫 (失败杀脚本跳过 KB 归档)")
            self.assertNotRegex(
                src,
                r'notify "\$ONTO_CONTENT" --channel discord --topic ontology\n',
                f"{rel}: 裸 ontology notify 不得回归")

    def test_finance_news_push_guarded(self):
        src = _read("jobs/finance_news/run_finance_news.sh")
        self.assertRegex(
            src, r'notify "\$WA_MSG" --topic daily \|\|',
            "finance_news: 主推送裸 notify 失败会跳过 status 写入")


class TestChasparkHonesty(unittest.TestCase):
    """JOB-F3 / JOB-F4: chaspark 推送 rc + seen 延迟提交。"""

    def setUp(self):
        self.src = _read("jobs/chaspark/run_chaspark.sh")

    def test_notify_rc_checked(self):
        # JOB-F3: notify 必须走 if 检查, WA_SENT 不得无条件 true
        self.assertRegex(self.src,
                         r'if notify "\$WA_MSG" --topic daily 2>')
        self.assertNotRegex(
            self.src,
            r'notify "\$WA_MSG" --topic daily\n\s*log "推送完成',
            "无条件 WA_SENT=true 反模式不得回归 (推送失败仍记 sent:true)")

    def test_parser_writes_new_ids_not_seen(self):
        # JOB-F4: 解析期不得重写 seen 文件 — 新 id 落独立文件
        self.assertIn('NEW_IDS_FILE="$CACHE/new_ids_${DAY}.txt"', self.src)
        self.assertIn("new_ids_file", self.src)
        self.assertNotRegex(
            self.src,
            r'# 更新 seen 文件\nwith open\(seen_file, "w"\)',
            "解析期重写 seen = LLM 全失败日文章永久丢失 (JOB-F4 血案)")

    def test_seen_merged_only_after_push_success(self):
        # 推送成功分支内才 append 进 seen (对齐 arxiv 契约)
        m = re.search(
            r'if \[ "\$WA_SENT" = "true" \]; then\n'
            r'(?:[^\n]*\n){1,8}?\s*cat "\$NEW_IDS_FILE" >> "\$SEEN_FILE"',
            self.src)
        self.assertIsNotNone(
            m, "seen 并入必须在 WA_SENT=true 分支内 (失败日次日自动重试)")


class TestWaKeepaliveEscalationHonesty(unittest.TestCase):
    """NOT-F4: wa_keepalive 升级告警不得把失败叙述成已送达。"""

    def setUp(self):
        self.src = _read("wa_keepalive.sh")

    def test_no_unconditional_escalated_log(self):
        # 反模式: `... || true` 紧跟无条件 ESCALATED 日志
        self.assertNotRegex(
            self.src,
            r'--json >/dev/null 2>&1 \|\| true\n\s*echo "\[\$TS\] ESCALATED',
            "|| true + 无条件 ESCALATED = 把失败叙述成已送达 (fail-plausible)")

    def test_both_sites_rc_checked_with_fallback(self):
        # 两个升级站点 (频道掉线 + Gateway 不可达) 都必须 if-检查 + 兜底写入
        sends = re.findall(
            r'if "\$OPENCLAW" message send --channel discord[^\n]*--json '
            r'>/dev/null 2>&1; then', self.src)
        self.assertEqual(len(sends), 2, "两个升级站点都必须 rc 检查")
        self.assertEqual(self.src.count("ESCALATE_FAILED"), 2)
        self.assertEqual(self.src.count('.openclaw_alerts.log"'), 2,
                         "两站点都要 .openclaw_alerts.log 兜底 (job_watchdog 同款)")


class TestSourceMarkers(unittest.TestCase):
    def test_v37_9_281_markers(self):
        for rel in ("notify.sh", "wa_keepalive.sh",
                    "jobs/chaspark/run_chaspark.sh",
                    "jobs/arxiv_monitor/run_arxiv.sh",
                    "jobs/dblp/run_dblp.sh",
                    "jobs/semantic_scholar/run_semantic_scholar.sh",
                    "jobs/finance_news/run_finance_news.sh"):
            self.assertIn("V37.9.281", _read(rel), rel)

    def test_all_touched_scripts_syntax_ok(self):
        for rel in ("notify.sh", "wa_keepalive.sh",
                    "jobs/chaspark/run_chaspark.sh",
                    "jobs/arxiv_monitor/run_arxiv.sh",
                    "jobs/dblp/run_dblp.sh",
                    "jobs/semantic_scholar/run_semantic_scholar.sh",
                    "jobs/finance_news/run_finance_news.sh"):
            r = subprocess.run(["bash", "-n", os.path.join(BASE, rel)],
                               capture_output=True)
            self.assertEqual(r.returncode, 0, f"{rel}: {r.stderr.decode()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

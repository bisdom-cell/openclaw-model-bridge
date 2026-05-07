#!/usr/bin/env python3
"""V37.9.31 — run_freight.sh last_run.json schema completeness guards.

Context: 2026-05-07 preflight 报告 "货代 deep_dive: last_run.json 无 deep_dive
字段（旧版本脚本？）". 真因不是旧脚本, 是 V37.9.27 rsync_helper 在 set -e
caller 中 rsync 失败时透传非 0 exit code 杀掉 freight Step 8-10. last_run.json
在 Step 5 已写但无 deep_dive 字段, preflight 误报为 "missing".

V37.9.31 双层修复:
  1. movespeed_rsync_helper.sh 改为 fail-open exit 0 (本测试不直接覆盖, 见
     test_movespeed_rsync_helper.py::test_caller_with_set_e_survives_rsync_failure)
  2. run_freight.sh 在所有 exit path 都写 deep_dive 字段:
     - NEW_COUNT=0 → deep_dive=skipped_no_news
     - LLM 失败 → deep_dive=skipped_llm_failed
     - LLM 解析率 < 50% → deep_dive=skipped_parse_low
     - Step 5 (推送成功) → deep_dive=pending (Step 9 覆盖为 ok/no_data/skipped)
     - Step 9 完成 → deep_dive=ok / no_data / skipped (覆盖 pending)

本测试是 source-level 守卫 (grep run_freight.sh 字面量), 防止未来重构遗漏字段.
"""

import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
RUN_FREIGHT = REPO_ROOT / "jobs" / "freight_watcher" / "run_freight.sh"
PREFLIGHT = REPO_ROOT / "preflight_check.sh"


class TestFreightStatusFileSchemaV9_31(unittest.TestCase):
    """All exit paths in run_freight.sh write last_run.json with deep_dive field."""

    @classmethod
    def setUpClass(cls):
        cls.script = RUN_FREIGHT.read_text(encoding="utf-8")

    def test_no_news_path_writes_deep_dive_field(self):
        """NEW_COUNT=0 早 exit 0 必须写 deep_dive=skipped_no_news."""
        # Find the NEW_COUNT=0 exit block
        m = re.search(
            r'if \[ "\$NEW_COUNT" -eq 0 \].+?exit 0',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "NEW_COUNT=0 exit block not found")
        block = m.group(0)
        self.assertIn(
            'deep_dive":"skipped_no_news"', block,
            "V37.9.31: NEW_COUNT=0 path must write deep_dive=skipped_no_news",
        )

    def test_llm_failed_path_writes_deep_dive_field(self):
        """LLM 失败 exit 1 必须写 deep_dive=skipped_llm_failed."""
        # Find "LLM_OUT// }" empty check block ending with exit 1
        # Pattern: spans from "L1检查" comment to "exit 1"
        m = re.search(
            r'L1.+?LLM\s*调用失败.+?exit 1',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "LLM-empty exit 1 block not found")
        block = m.group(0)
        self.assertIn(
            'deep_dive":"skipped_llm_failed"', block,
            "V37.9.31: LLM-empty path must write deep_dive=skipped_llm_failed",
        )

    def test_parse_low_path_writes_deep_dive_field(self):
        """LLM 解析率 < 50% exit 2 必须写 deep_dive=skipped_parse_low."""
        m = re.search(
            r'L2.+?解析成功率.+?exit 2',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "LLM-parse-low exit 2 block not found")
        block = m.group(0)
        self.assertIn(
            'deep_dive":"skipped_parse_low"', block,
            "V37.9.31: LLM-parse-low path must write deep_dive=skipped_parse_low",
        )

    def test_send_success_path_writes_deep_dive_pending(self):
        """Step 5 推送成功必须写 deep_dive=pending (Step 9 后续覆盖)."""
        # Find the success branch of OPENCLAW message send
        # Pattern: contains 'sent":true' and must also have 'deep_dive":"pending"'
        success_pattern = re.compile(
            r'sent":true.*?STATUS_FILE',
            flags=re.DOTALL,
        )
        m = success_pattern.search(self.script)
        self.assertIsNotNone(m, "Step 5 send success block not found")
        block = m.group(0)
        self.assertIn(
            'deep_dive":"pending"', block,
            "V37.9.31: Step 5 success path must write deep_dive=pending "
            "so preflight knows Step 9 was attempted but not finalized",
        )

    def test_send_failed_path_writes_deep_dive_pending(self):
        """Step 5 推送失败也要写 deep_dive=pending (任务流程仍可能继续)."""
        m = re.search(
            r'sent":false.*?STATUS_FILE',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "Step 5 send failed block not found")
        block = m.group(0)
        self.assertIn(
            'deep_dive":"pending"', block,
            "V37.9.31: Step 5 send failed must also write deep_dive=pending",
        )

    def test_step_9_writes_overrides_pending(self):
        """Step 9 末尾 python3 覆盖 STATUS_FILE 写最终 deep_dive 状态."""
        # Find the Step 9 closing python3 block that updates status_file
        m = re.search(
            r"d\['deep_dive'\]\s*=\s*'\$DEEP_DIVE_STATUS'",
            self.script,
        )
        self.assertIsNotNone(
            m,
            "V37.9.31: Step 9 末尾必须用 python3 把 DEEP_DIVE_STATUS 写入 d['deep_dive']",
        )

    def test_step_9_status_values_complete(self):
        """DEEP_DIVE_STATUS 三档赋值齐全 (ok / no_data / skipped)."""
        for status in ("ok", "no_data", "skipped"):
            m = re.search(
                rf'DEEP_DIVE_STATUS="{status}"',
                self.script,
            )
            self.assertIsNotNone(
                m,
                f'V37.9.31: DEEP_DIVE_STATUS="{status}" 必须存在 — Step 9 三档输出',
            )

    def test_v37_9_31_marker_in_script(self):
        """V37.9.31 标记必须存在 (caller 出现回归后 grep 可定位)."""
        self.assertIn(
            "V37.9.31", self.script,
            "V37.9.31 attribution comment 必须在 run_freight.sh 中",
        )


class TestPreflightStatusToleranceV9_31(unittest.TestCase):
    """preflight_check.sh 接受 V37.9.31 新 deep_dive 状态值."""

    @classmethod
    def setUpClass(cls):
        cls.script = PREFLIGHT.read_text(encoding="utf-8")

    def test_skipped_no_news_passes(self):
        """skipped_no_news → pass (不是 warn 不是 fail)."""
        # Find the case statement and check skipped_no_news leads to `pass`
        m = re.search(
            r'skipped_no_news\)[^;]+',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "skipped_no_news case not found")
        block = m.group(0)
        self.assertIn(
            "pass", block,
            "V37.9.31: skipped_no_news 是合法跳过, 应该 pass 而非 warn",
        )

    def test_skipped_llm_failed_warns(self):
        """skipped_llm_failed → warn (LLM 失败需注意)."""
        m = re.search(
            r'skipped_llm_failed\)[^;]+',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "skipped_llm_failed case not found")
        block = m.group(0)
        self.assertIn("warn", block)

    def test_skipped_parse_low_warns(self):
        """skipped_parse_low → warn (解析率低需注意)."""
        m = re.search(
            r'skipped_parse_low\)[^;]+',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "skipped_parse_low case not found")
        block = m.group(0)
        self.assertIn("warn", block)

    def test_pending_warns_with_v37_9_31_context(self):
        """pending → warn 含 V37.9.31 注释指向 rsync_helper 修复."""
        m = re.search(
            r'pending\)[^;]+',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "pending case not found")
        block = m.group(0)
        self.assertIn("warn", block)
        self.assertIn(
            "V37.9.31", block,
            "pending warn 应注释 V37.9.31 上下文",
        )

    def test_missing_now_fails_not_warns(self):
        """V37.9.31 后 missing 是真异常 — 应 fail 而非 warn."""
        m = re.search(
            r'missing\)[^;]+',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "missing case not found")
        block = m.group(0)
        self.assertIn(
            "fail", block,
            "V37.9.31: 所有合法路径都写 deep_dive 后, missing 是真异常应 fail",
        )


if __name__ == "__main__":
    unittest.main()

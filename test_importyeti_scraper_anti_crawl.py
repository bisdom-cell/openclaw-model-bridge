#!/usr/bin/env python3
"""V37.9.31 — importyeti_scraper.py anti-crawl upgrade unit tests.

Coverage:
  TestUserAgentRotation     — Multi-UA pool (4 modern Chrome UAs)
  TestViewportRandomization — Multi-viewport pool (5 sizes)
  TestSecChUaHeaders        — sec-ch-ua / sec-fetch-* / accept-language
  TestCloudflareDetection   — _detect_cloudflare_challenge() pure function
  TestCloudflareBackoff     — CF_BACKOFF_SECONDS [30,60,120] + retry count
  TestInterCompanyDelay     — 5-12s random instead of fixed 3s
  TestStealthFallback       — Manual fallback when playwright-stealth missing
  TestSourceLevelGuards     — V37.9.31 attribution / structural literals

Pure functions (_detect_cloudflare_challenge, _pick_user_agent, _pick_viewport,
_inter_company_delay) are imported and tested directly. Subprocess-only paths
(actual Playwright invocation) are NOT covered here — those need Mac Mini
integration testing.

V37.9.31 motivation: V37.9.27 rsync_helper bug masked ImportYeti behavior
(Step 9 wasn't reached). Now that helper is fail-open, scraper actually runs
daily and Cloudflare may strike. This upgrade is preventive.
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCRAPER_DIR = REPO_ROOT / "jobs" / "freight_watcher"
sys.path.insert(0, str(SCRAPER_DIR))

# Import pure functions for direct testing
from importyeti_scraper import (  # noqa: E402
    CF_BACKOFF_SECONDS,
    CF_MAX_RETRIES,
    EXTRA_HEADERS,
    USER_AGENTS,
    VIEWPORTS,
    _detect_cloudflare_challenge,
    _empty_result,
    _inter_company_delay,
    _pick_user_agent,
    _pick_viewport,
    _is_test_mode,
    format_result,
)


class TestUserAgentRotation(unittest.TestCase):
    """V37.9.31: multi-UA pool (4 modern Chrome variants)."""

    def test_pool_has_at_least_4_uas(self):
        """4 个 UA 是抗指纹的最小数量."""
        self.assertGreaterEqual(len(USER_AGENTS), 4)

    def test_all_uas_are_modern_chrome(self):
        """所有 UA 必须是 Chrome 130+ (避免使用过时浏览器引发 CF 怀疑)."""
        for ua in USER_AGENTS:
            self.assertIn("Chrome/", ua, f"UA missing Chrome/: {ua}")
            # Extract version, must be ≥ 130
            import re
            m = re.search(r"Chrome/(\d+)", ua)
            self.assertIsNotNone(m)
            version = int(m.group(1))
            self.assertGreaterEqual(
                version, 130,
                f"UA {ua[:60]} uses Chrome/{version}, must be ≥ 130 for V37.9.31",
            )

    def test_pick_user_agent_returns_from_pool(self):
        """_pick_user_agent() always returns a member of USER_AGENTS."""
        for _ in range(20):  # Run enough times to hit randomness
            ua = _pick_user_agent()
            self.assertIn(ua, USER_AGENTS)

    def test_pool_includes_macos_and_windows(self):
        """覆盖多个平台 (macOS + Windows) 增加抗指纹."""
        platforms = []
        for ua in USER_AGENTS:
            if "Macintosh" in ua:
                platforms.append("macOS")
            elif "Windows" in ua:
                platforms.append("Windows")
        self.assertIn("macOS", platforms, "pool 缺少 macOS UA")
        self.assertIn("Windows", platforms, "pool 缺少 Windows UA")


class TestViewportRandomization(unittest.TestCase):
    """V37.9.31: random viewport (was fixed 1280x720)."""

    def test_pool_has_at_least_3_viewports(self):
        self.assertGreaterEqual(len(VIEWPORTS), 3)

    def test_no_fixed_1280_720_signature(self):
        """1280x720 单值是 V3 原版 (易被 CF 跟踪) — 不应是唯一选项."""
        # Fixed 1280x720 was the V3 signature — pool must contain MORE than that
        self.assertGreater(
            len(VIEWPORTS), 1,
            "V37.9.31: viewport 必须随机化 (V3 固定 1280x720 是 CF 跟踪签名)",
        )

    def test_pick_viewport_returns_dict_with_width_height(self):
        for _ in range(20):
            vp = _pick_viewport()
            self.assertIn("width", vp)
            self.assertIn("height", vp)
            self.assertIsInstance(vp["width"], int)
            self.assertIsInstance(vp["height"], int)

    def test_viewports_are_realistic_sizes(self):
        """所有 viewport 必须是真实笔记本/桌面尺寸."""
        for vp in VIEWPORTS:
            self.assertGreaterEqual(vp["width"], 1280, "宽度 < 1280 不真实")
            self.assertGreaterEqual(vp["height"], 720, "高度 < 720 不真实")
            self.assertLessEqual(vp["width"], 2560, "宽度 > 2560 是 4K, 罕见")
            self.assertLessEqual(vp["height"], 1440, "高度 > 1440 是 4K, 罕见")


class TestSecChUaHeaders(unittest.TestCase):
    """V37.9.31: sec-ch-ua / sec-fetch-* / accept-language headers."""

    def test_sec_ch_ua_present(self):
        self.assertIn("sec-ch-ua", EXTRA_HEADERS)
        self.assertIn("Chromium", EXTRA_HEADERS["sec-ch-ua"])

    def test_sec_fetch_dest_present(self):
        self.assertEqual(EXTRA_HEADERS.get("sec-fetch-dest"), "document")

    def test_sec_fetch_mode_present(self):
        self.assertEqual(EXTRA_HEADERS.get("sec-fetch-mode"), "navigate")

    def test_accept_language_realistic(self):
        """accept-language 必须是 en-US 含 q-value (真实浏览器格式)."""
        self.assertIn("accept-language", EXTRA_HEADERS)
        al = EXTRA_HEADERS["accept-language"]
        self.assertIn("en-US", al)
        self.assertIn("q=", al, "accept-language 必须含 q-value 才像真实浏览器")

    def test_upgrade_insecure_requests_present(self):
        """真实 Chrome 总是发送 upgrade-insecure-requests: 1."""
        self.assertEqual(EXTRA_HEADERS.get("upgrade-insecure-requests"), "1")


class TestCloudflareDetection(unittest.TestCase):
    """V37.9.31: _detect_cloudflare_challenge() pure function."""

    def test_just_a_moment_title_detected(self):
        """5s challenge interstitial title."""
        self.assertTrue(
            _detect_cloudflare_challenge("Just a moment...", "loading...")
        )

    def test_challenge_in_title_detected(self):
        self.assertTrue(_detect_cloudflare_challenge("Security challenge", ""))

    def test_verifying_human_detected(self):
        self.assertTrue(
            _detect_cloudflare_challenge(
                "ImportYeti", "Verifying you are human... please wait"
            )
        )

    def test_cf_chl_marker_detected(self):
        """CF managed challenge JS includes cf-chl marker."""
        self.assertTrue(
            _detect_cloudflare_challenge("ImportYeti", "<script>cf-chl-bypass</script>")
        )

    def test_cloudflare_block_page_detected(self):
        """Pure CF block page (small body + cloudflare keyword)."""
        block_page = "Cloudflare error 1015 rate limited"
        self.assertTrue(_detect_cloudflare_challenge("ImportYeti", block_page))

    def test_normal_search_page_not_detected(self):
        """正常 ImportYeti 搜索结果 → not challenge."""
        normal_body = "Total Shipments\n1,229,872\nMost recent shipment\n02/24/2026"
        self.assertFalse(
            _detect_cloudflare_challenge("IKEA - ImportYeti", normal_body)
        )

    def test_empty_inputs_not_detected(self):
        """Empty title + body → not challenge (avoid false positives)."""
        self.assertFalse(_detect_cloudflare_challenge("", ""))
        self.assertFalse(_detect_cloudflare_challenge(None, None))

    def test_cloudflare_word_alone_not_detected_when_body_long(self):
        """长 body 提及 cloudflare 不是 block page (e.g. blog 文章谈 CF)."""
        long_body = "cloudflare " + ("normal content " * 100)  # >500 chars
        self.assertFalse(_detect_cloudflare_challenge("ImportYeti", long_body))


class TestCloudflareBackoff(unittest.TestCase):
    """V37.9.31: backoff schedule [30, 60, 120] (was 10s)."""

    def test_backoff_schedule_30_60_120(self):
        self.assertEqual(CF_BACKOFF_SECONDS, [30, 60, 120])

    def test_max_retries_3(self):
        self.assertEqual(CF_MAX_RETRIES, 3)

    def test_backoff_is_exponential(self):
        """每次 retry wait 翻倍 (or close)."""
        for i in range(1, len(CF_BACKOFF_SECONDS)):
            self.assertGreater(
                CF_BACKOFF_SECONDS[i], CF_BACKOFF_SECONDS[i - 1],
                "backoff must monotonically increase",
            )

    def test_no_v3_short_10s_backoff(self):
        """V37.9.31: 10s backoff is V3 signature, must be removed."""
        self.assertNotIn(
            10, CF_BACKOFF_SECONDS,
            "V37.9.31: 10s 是 V3 backoff, 太短无法跨过 CF 的 5s+ challenge 时间窗",
        )


class TestInterCompanyDelay(unittest.TestCase):
    """V37.9.31: random delay 5-12s between companies (was fixed 3s)."""

    def test_delay_in_5_to_12_range(self):
        """20 次抽样必在 [5, 12] 内."""
        for _ in range(20):
            d = _inter_company_delay()
            self.assertGreaterEqual(d, 5.0)
            self.assertLessEqual(d, 12.0)

    def test_delay_varies_across_calls(self):
        """连续 20 次 不应是同一值 (不是 deterministic 函数)."""
        samples = [_inter_company_delay() for _ in range(20)]
        unique = set(samples)
        # 至少 5 个不同值 (避免极小概率所有都相同)
        self.assertGreater(
            len(unique), 5,
            f"delay 应随机化, 但 20 次只有 {len(unique)} 个不同值: {unique}",
        )

    def test_no_v3_fixed_3s_delay(self):
        """delay 不应是 V3 固定 3s — 100 次抽样 3.0 不应主导."""
        samples = [_inter_company_delay() for _ in range(100)]
        # 3.0 不应该出现 (不在 5-12 范围内)
        self.assertNotIn(
            3.0, samples,
            "V37.9.31: 3s 是 V3 固定值, 易被 CF rate-limit tracking",
        )


class TestTestModeBehavior(unittest.TestCase):
    """V37.9.31: IMPORTYETI_TEST_MODE=1 跳过所有 sleep (确定性测试)."""

    def setUp(self):
        self._old = os.environ.get("IMPORTYETI_TEST_MODE")
        os.environ["IMPORTYETI_TEST_MODE"] = "1"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("IMPORTYETI_TEST_MODE", None)
        else:
            os.environ["IMPORTYETI_TEST_MODE"] = self._old

    def test_test_mode_active_when_env_set(self):
        self.assertTrue(_is_test_mode())

    def test_test_mode_inactive_when_env_unset(self):
        del os.environ["IMPORTYETI_TEST_MODE"]
        self.assertFalse(_is_test_mode())


class TestEmptyResultHelper(unittest.TestCase):
    """_empty_result() returns all-N/A dict with company name."""

    def test_returns_all_na_fields(self):
        r = _empty_result("TestCo", "test reason")
        self.assertEqual(r["公司"], "TestCo")
        for k in ("总发货次数", "月均发货量", "前3大供应商", "主要航线", "最近发货日期", "趋势"):
            self.assertEqual(r[k], "N/A", f"{k} should be N/A")


class TestFormatResult(unittest.TestCase):
    """format_result() produces stable text format expected by run_freight.sh Step 10."""

    def test_format_includes_all_keys_in_order(self):
        r = {
            "公司": "IKEA",
            "总发货次数": "1,229,872",
            "月均发货量": "N/A",
            "前3大供应商": "Ikea Industrial Poland",
            "主要航线": "N/A",
            "最近发货日期": "2026-02-24",
            "趋势": "N/A",
        }
        text = format_result(r)
        # All 7 keys appear in order
        keys = ["公司", "总发货次数", "月均发货量", "前3大供应商", "主要航线", "最近发货日期", "趋势"]
        positions = [text.index(k) for k in keys]
        self.assertEqual(
            positions, sorted(positions),
            "format_result keys must appear in stable order",
        )

    def test_format_uses_chinese_colon(self):
        """Step 10 LLM prompt 期待 '关键：值' 格式 (Chinese colon)."""
        r = _empty_result("TestCo")
        text = format_result(r)
        self.assertIn("公司：TestCo", text, "Must use Chinese colon ：")


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.31: structural literal guards on importyeti_scraper.py."""

    @classmethod
    def setUpClass(cls):
        scraper_path = SCRAPER_DIR / "importyeti_scraper.py"
        cls.source = scraper_path.read_text(encoding="utf-8")

    def test_v37_9_31_marker_present(self):
        self.assertIn(
            "V37.9.31", self.source,
            "V37.9.31 attribution comment 必须存在",
        )

    def test_playwright_stealth_import_attempted(self):
        """playwright_stealth 必须 try-import (用户选了专业库方案)."""
        self.assertIn("playwright_stealth", self.source)
        self.assertIn("ImportError", self.source)

    def test_v37_9_32_uses_2x_api_first(self):
        """V37.9.32: 必须先 try import 2.x API (Stealth class), 后 fallback 1.x."""
        # 2.x import must come first
        idx_stealth = self.source.find("from playwright_stealth import Stealth")
        idx_stealth_sync = self.source.find("from playwright_stealth import stealth_sync")
        self.assertGreater(idx_stealth, 0, "V37.9.32: must import Stealth class (2.x API)")
        self.assertGreater(
            idx_stealth_sync, idx_stealth,
            "V37.9.32: 2.x Stealth import must precede 1.x stealth_sync fallback",
        )

    def test_v37_9_32_uses_apply_stealth_sync_method(self):
        """V37.9.32: 2.x API 必须用 .apply_stealth_sync(page) 方法."""
        self.assertIn("apply_stealth_sync(page)", self.source,
                      "V37.9.32: must call Stealth().apply_stealth_sync(page) for 2.x API")

    def test_v37_9_32_call_site_uses_api_agnostic_helper(self):
        """V37.9.32: 调用点必须用 _stealth_apply helper (不直接调 stealth_sync)."""
        # The active call site (inside the if stealth_available block) should
        # use _stealth_apply, not stealth_sync directly. stealth_sync should
        # only appear in 1.x fallback def, not in the call site.
        # Find the "if stealth_available" block and check what it calls
        import re
        m = re.search(
            r'if stealth_available[^\n]*:\s*\n\s*try:\s*\n\s*([_a-zA-Z]+)\(page\)',
            self.source,
        )
        self.assertIsNotNone(m, "if stealth_available try-block not found")
        called_fn = m.group(1)
        self.assertEqual(
            called_fn, "_stealth_apply",
            f"V37.9.32: call site must use _stealth_apply helper, got {called_fn}. "
            f"Direct stealth_sync() call breaks 2.x compat.",
        )

    def test_manual_fallback_present(self):
        """playwright-stealth 缺失时必须有 manual fallback."""
        self.assertIn("manual stealth fallback", self.source.lower())
        # Manual fallback must include navigator.webdriver hide
        self.assertIn("navigator, 'webdriver'", self.source)

    def test_extra_headers_dict_in_source(self):
        """EXTRA_HEADERS dict 必须直接定义在源码中 (非字符串构造)."""
        self.assertIn("EXTRA_HEADERS = {", self.source)

    def test_user_agents_pool_in_source(self):
        self.assertIn("USER_AGENTS = [", self.source)

    def test_no_fixed_3s_inter_company_delay(self):
        """V37.9.31: 不允许 time.sleep(3) 固定值 (V3 反模式)."""
        # Allow time.sleep(N) where N is variable (delay variable, etc.)
        # but reject literal time.sleep(3)
        import re
        # V37.9.31 only allows variable sleep arguments (e.g. time.sleep(seconds), time.sleep(wait_sec))
        # Reject any literal short sleep (1-4 seconds) — V3 fixed 3s is the bad pattern
        for n in (1, 2, 3, 4):
            self.assertNotRegex(
                self.source, rf"time\.sleep\({n}\)",
                f"V37.9.31: time.sleep({n}) literal 是 V3 反模式, 用 _inter_company_delay()",
            )


if __name__ == "__main__":
    unittest.main()

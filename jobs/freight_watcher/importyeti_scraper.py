#!/usr/bin/env python3
"""ImportYeti Playwright scraper — V37.9.31 anti-crawl upgrade.

V37.9.31 changes (vs V3 original):
  - playwright-stealth library (50+ stealth patches vs manual 3)
  - Multi-UA rotation (4 modern Chrome UAs across macOS/Windows)
  - sec-ch-ua / sec-fetch-* / accept-language realistic headers
  - Random viewport (1280-1920 x 720-1080) instead of fixed
  - Cloudflare 5s challenge wait (detect + retry up to 3 attempts)
  - Exponential backoff 30s/60s/120s on Cloudflare hit (was 10s)
  - Inter-company delay 5-12s random (was fixed 3s)
  - Mac Mini install: pip3 install playwright-stealth

V37.9.31 motivation: V37.9.27 rsync_helper bug (fixed in this same release)
masked the ImportYeti behavior — Step 9 wasn't even reached. Now that helper
is fail-open, scraper actually runs every day and Cloudflare may strike. This
upgrade is preventive (no immediate Cloudflare evidence yet, but anti-crawl
is brittle and Cloudflare aggressively rotates challenges).

Used by: jobs/freight_watcher/run_freight.sh Step 9 (line 357-415).

Output format (stdout, one company per block):
    --- IKEA ---
    公司：IKEA
    总发货次数：1,229,872
    月均发货量：N/A
    前3大供应商：Ikea Industrial Poland Z O O, Friul Intagli Industries
    主要航线：N/A
    最近发货日期：2026-02-24
    趋势：N/A

Dependencies:
    pip3 install playwright playwright-stealth
    python3 -m playwright install chromium

CLI:
    python3 importyeti_scraper.py "IKEA"
    python3 importyeti_scraper.py "IKEA" "Volkswagen" "Tesla"

Test mode (skip Cloudflare wait, deterministic timing):
    IMPORTYETI_TEST_MODE=1 python3 importyeti_scraper.py "Acme"
"""
import os
import random
import re
import sys
import time
import urllib.parse


# V37.9.31: 4 modern Chrome UAs to rotate across runs (each call picks one).
# Rationale: ImportYeti's Cloudflare uses UA fingerprinting; constant
# Chrome/131 macOS becomes a tracked signature over time.
USER_AGENTS = [
    # macOS Chrome 131 (most recent stable)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    # macOS Chrome 130
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
    # Windows 11 Chrome 131
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    # macOS Edge 131 (rare but legitimate)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]


# V37.9.31: realistic viewport range (Cloudflare flags exact 1280x720).
VIEWPORTS = [
    {"width": 1280, "height": 800},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]


# V37.9.31: realistic sec-ch-ua / sec-fetch-* headers expected by Cloudflare.
EXTRA_HEADERS = {
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
}


# V37.9.31: backoff schedule for Cloudflare retries.
CF_BACKOFF_SECONDS = [30, 60, 120]
CF_MAX_RETRIES = len(CF_BACKOFF_SECONDS)


def _is_test_mode() -> bool:
    """V37.9.31: deterministic test mode skips sleeps."""
    return os.environ.get("IMPORTYETI_TEST_MODE", "0") == "1"


def _maybe_sleep(seconds: float) -> None:
    """V37.9.31: skip sleep in test mode for deterministic unit tests."""
    if not _is_test_mode():
        time.sleep(seconds)


def _detect_cloudflare_challenge(page_title: str, body: str) -> bool:
    """V37.9.31: detect Cloudflare 5s challenge / managed challenge / blocks.

    Pure function for unit test isolation.

    Patterns observed:
      - "Just a moment..." (5s challenge interstitial)
      - "Verifying you are human" (managed challenge)
      - "challenge" in title (generic)
      - "Cloudflare" in body + small body length (block page)
    """
    title_lower = (page_title or "").lower()
    body_lower = (body or "").lower()
    if "just a moment" in title_lower:
        return True
    if "challenge" in title_lower:
        return True
    if "verifying you are human" in body_lower:
        return True
    if "cf-chl" in body_lower:
        return True
    if "cloudflare" in body_lower and len(body_lower) < 500:
        # Pure cloudflare block page (no actual content)
        return True
    return False


def _empty_result(company_name: str, reason: str = "") -> dict:
    """Return all-N/A result with optional reason logged to stderr."""
    r = {
        "公司": company_name,
        "总发货次数": "N/A",
        "月均发货量": "N/A",
        "前3大供应商": "N/A",
        "主要航线": "N/A",
        "最近发货日期": "N/A",
        "趋势": "N/A",
    }
    if reason:
        print(f"[importyeti] ⚠ {company_name}: {reason}", file=sys.stderr)
    return r


def _parse_search_results(company_name: str, body: str) -> dict:
    """Extract first matching company info from ImportYeti search results."""
    result = {
        "公司": company_name,
        "总发货次数": "N/A",
        "月均发货量": "N/A",
        "前3大供应商": "N/A",
        "主要航线": "N/A",
        "最近发货日期": "N/A",
        "趋势": "N/A",
    }

    shipments_match = re.search(r"Total Shipments\s*\n\s*([\d,]+)", body)
    if shipments_match:
        result["总发货次数"] = shipments_match.group(1)

    recent_match = re.search(r"Most recent shipment\s*\n\s*(\d{2}/\d{2}/\d{4})", body)
    if recent_match:
        raw_date = recent_match.group(1)
        parts = raw_date.split("/")
        if len(parts) == 3:
            result["最近发货日期"] = f"{parts[2]}-{parts[0]}-{parts[1]}"

    suppliers_match = re.search(r"Top Suppliers\s*\n\s*(.+)", body)
    if suppliers_match:
        suppliers = suppliers_match.group(1).strip()
        parts = [s.strip() for s in suppliers.split(",")]
        result["前3大供应商"] = ", ".join(parts[:3])
    else:
        customers_match = re.search(r"Top Customers\s*\n\s*(.+)", body)
        if customers_match:
            customers = customers_match.group(1).strip()
            parts = [s.strip() for s in customers.split(",")]
            result["前3大供应商"] = ", ".join(parts[:3]) + " (客户)"

    return result


def scrape_company(page, company_name: str, retry_count: int = 0) -> dict:
    """V37.9.31: scrape single company with Cloudflare-aware retry.

    On Cloudflare hit: wait CF_BACKOFF_SECONDS[retry_count] then recurse.
    Max CF_MAX_RETRIES (3 attempts → 30s, 60s, 120s).
    """
    slug = urllib.parse.quote(company_name)
    url = f"https://www.importyeti.com/search?q={slug}"

    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # V37.9.31: longer initial wait to let Cloudflare 5s pass naturally
        _maybe_sleep(8)
    except Exception as e:
        return _empty_result(company_name, f"页面加载失败: {e}")

    title = page.title()
    body = page.inner_text("body")

    if _detect_cloudflare_challenge(title, body):
        if retry_count < CF_MAX_RETRIES:
            wait_sec = CF_BACKOFF_SECONDS[retry_count]
            print(
                f"[importyeti] Cloudflare 拦截 (attempt {retry_count + 1}/"
                f"{CF_MAX_RETRIES + 1})，等待 {wait_sec}s 后重试...",
                file=sys.stderr,
            )
            _maybe_sleep(wait_sec)
            return scrape_company(page, company_name, retry_count=retry_count + 1)
        return _empty_result(
            company_name,
            f"Cloudflare 拦截 ({CF_MAX_RETRIES + 1}次重试均失败)",
        )

    if len(body) < 50:
        return _empty_result(company_name, "页面内容为空")

    return _parse_search_results(company_name, body)


def format_result(result: dict) -> str:
    """Format result dict to expected text block."""
    lines = []
    for key in [
        "公司",
        "总发货次数",
        "月均发货量",
        "前3大供应商",
        "主要航线",
        "最近发货日期",
        "趋势",
    ]:
        lines.append(f"{key}：{result[key]}")
    return "\n".join(lines)


def _pick_user_agent() -> str:
    """V37.9.31: pick a random UA from USER_AGENTS pool."""
    return random.choice(USER_AGENTS)


def _pick_viewport() -> dict:
    """V37.9.31: pick a random viewport from VIEWPORTS pool."""
    return random.choice(VIEWPORTS)


def _inter_company_delay() -> float:
    """V37.9.31: random delay 5-12s between companies (was fixed 3s).

    Rationale: rate-limit avoidance — fixed 3s is a tracked signature.
    """
    return random.uniform(5.0, 12.0)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <公司名1> [公司名2] ...", file=sys.stderr)
        return 1

    companies = sys.argv[1:]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "[importyeti] 错误: 需要 playwright (pip3 install playwright)",
            file=sys.stderr,
        )
        return 1

    # V37.9.32: playwright-stealth API compatibility (1.x → 2.x).
    # 2.x replaced the `stealth_sync` function with a `Stealth` class.
    # Try 2.x first (recommended), fall back to 1.x for legacy installs.
    # If neither available, manual STEALTH_JS init script applies (V37.9.31).
    _stealth_apply = None
    try:
        from playwright_stealth import Stealth  # 2.x API
        def _stealth_apply(page):  # type: ignore[no-redef]
            Stealth().apply_stealth_sync(page)
        stealth_available = True
        stealth_api_version = "2.x"
    except ImportError:
        try:
            from playwright_stealth import stealth_sync  # 1.x API
            def _stealth_apply(page):  # type: ignore[no-redef]
                stealth_sync(page)
            stealth_available = True
            stealth_api_version = "1.x"
        except ImportError:
            stealth_available = False
            stealth_api_version = "none"
            print(
                "[importyeti] WARN: playwright-stealth 未安装 "
                "(pip3 install playwright-stealth)，使用 manual stealth fallback",
                file=sys.stderr,
            )

    chosen_ua = _pick_user_agent()
    chosen_viewport = _pick_viewport()
    print(
        f"[importyeti] V37.9.32 stealth: ua={chosen_ua[:50]}... "
        f"viewport={chosen_viewport['width']}x{chosen_viewport['height']} "
        f"stealth_lib={stealth_api_version} "
        f"({'on' if stealth_available else 'off — manual fallback'})",
        file=sys.stderr,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                # V37.9.31: extra Chrome flags to hide automation hints
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
            ],
        )
        context = browser.new_context(
            user_agent=chosen_ua,
            viewport=chosen_viewport,
            locale="en-US",
            extra_http_headers=EXTRA_HEADERS,
        )

        page = context.new_page()

        # V37.9.32: apply playwright-stealth via API-version-aware helper
        # (_stealth_apply selected at import time: 2.x Stealth().apply_stealth_sync,
        # 1.x stealth_sync, or None → manual fallback). Keeps call site agnostic
        # of the API version that's actually installed on the runtime host.
        if stealth_available and _stealth_apply is not None:
            try:
                _stealth_apply(page)
            except Exception as e:
                print(
                    f"[importyeti] WARN: playwright-stealth apply failed "
                    f"(api={stealth_api_version}, err={e})，fallback manual STEALTH_JS",
                    file=sys.stderr,
                )
                stealth_available = False

        if not stealth_available:
            # Manual fallback: same as V3 original but expanded.
            # V37.9.31: more hide points than V3's 3-line stealth.
            page.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});
window.chrome = {runtime: {}, app: {isInstalled: false}};
// Hide Chrome WebDriver extensions
['runtime', 'loadTimes', 'csi'].forEach(k => {
    if (window.chrome) window.chrome[k] = () => {};
});
""")

        for i, company in enumerate(companies):
            print(
                f"[importyeti] 查询 ({i + 1}/{len(companies)}): {company}",
                file=sys.stderr,
            )
            result = scrape_company(page, company)
            print(f"--- {company} ---")
            print(format_result(result))
            print()

            # V37.9.31: random delay 5-12s instead of fixed 3s
            if i < len(companies) - 1:
                delay = _inter_company_delay()
                print(
                    f"[importyeti] inter-company delay {delay:.1f}s",
                    file=sys.stderr,
                )
                _maybe_sleep(delay)

        browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

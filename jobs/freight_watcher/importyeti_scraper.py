#!/usr/bin/env python3
"""ImportYeti Playwright scraper — 替代 openclaw agent browser 调用。

用法:
    python3 importyeti_scraper.py "IKEA"
    python3 importyeti_scraper.py "IKEA" "Volkswagen" "Tesla"

输出格式（stdout，每个公司一段）:
    公司：IKEA
    总发货次数：1,229,872
    月均发货量：N/A
    前3大供应商：Ikea Industrial Poland Z O O, Friul Intagli Industries, Ikea Industrial Portugal
    主要航线：N/A
    最近发货日期：2026-02-24
    趋势：N/A

依赖: pip3 install playwright && python3 -m playwright install chromium
"""
import re
import sys
import time

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
window.chrome = {runtime: {}};
"""


def scrape_company(page, company_name, retry=1):
    """抓取单个公司的 ImportYeti 搜索结果，返回结构化字典。"""
    import urllib.parse

    slug = urllib.parse.quote(company_name)
    url = f"https://www.importyeti.com/search?q={slug}"

    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(6)
    except Exception as e:
        return _empty_result(company_name, f"页面加载失败: {e}")

    title = page.title()
    if "Just a moment" in title or "challenge" in title.lower():
        if retry > 0:
            print(f"[importyeti] Cloudflare 拦截，等待10秒重试...", file=sys.stderr)
            time.sleep(10)
            return scrape_company(page, company_name, retry=retry - 1)
        return _empty_result(company_name, "Cloudflare 拦截")

    body = page.inner_text("body")
    if len(body) < 50:
        return _empty_result(company_name, "页面内容为空")

    return _parse_search_results(company_name, body)


def _parse_search_results(company_name, body):
    """从 ImportYeti 搜索结果页面文本中提取第一个匹配的公司信息。"""
    result = {
        "公司": company_name,
        "总发货次数": "N/A",
        "月均发货量": "N/A",
        "前3大供应商": "N/A",
        "主要航线": "N/A",
        "最近发货日期": "N/A",
        "趋势": "N/A",
    }

    # 提取 Total Shipments（取第一个出现的，通常是最相关的结果）
    shipments_match = re.search(r"Total Shipments\s*\n\s*([\d,]+)", body)
    if shipments_match:
        result["总发货次数"] = shipments_match.group(1)

    # 提取 Most recent shipment
    recent_match = re.search(r"Most recent shipment\s*\n\s*(\d{2}/\d{2}/\d{4})", body)
    if recent_match:
        raw_date = recent_match.group(1)
        parts = raw_date.split("/")
        if len(parts) == 3:
            result["最近发货日期"] = f"{parts[2]}-{parts[0]}-{parts[1]}"

    # 提取 Top Suppliers / Top Customers
    suppliers_match = re.search(r"Top Suppliers\s*\n\s*(.+)", body)
    if suppliers_match:
        suppliers = suppliers_match.group(1).strip()
        # 取前3个（按逗号分隔）
        parts = [s.strip() for s in suppliers.split(",")]
        result["前3大供应商"] = ", ".join(parts[:3])
    else:
        # 如果是供应商页面，提取 Top Customers
        customers_match = re.search(r"Top Customers\s*\n\s*(.+)", body)
        if customers_match:
            customers = customers_match.group(1).strip()
            parts = [s.strip() for s in customers.split(",")]
            result["前3大供应商"] = ", ".join(parts[:3]) + " (客户)"

    return result


def _empty_result(company_name, reason=""):
    """返回全 N/A 的结果。"""
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


def format_result(result):
    """将结果字典格式化为 run_freight.sh Step 10 期望的文本格式。"""
    lines = []
    for key in ["公司", "总发货次数", "月均发货量", "前3大供应商", "主要航线", "最近发货日期", "趋势"]:
        lines.append(f"{key}：{result[key]}")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <公司名1> [公司名2] ...", file=sys.stderr)
        sys.exit(1)

    companies = sys.argv[1:]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[importyeti] 错误: 需要安装 playwright (pip3 install playwright)", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="en-US",
        )
        context.add_init_script(STEALTH_JS)
        page = context.new_page()

        for i, company in enumerate(companies):
            print(f"[importyeti] 查询 ({i+1}/{len(companies)}): {company}", file=sys.stderr)
            result = scrape_company(page, company)
            print(f"--- {company} ---")
            print(format_result(result))
            print()

            # 间隔避免被限速
            if i < len(companies) - 1:
                time.sleep(3)

        browser.close()


if __name__ == "__main__":
    main()

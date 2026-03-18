"""Test: can patchright bypass BOSS CDP detection?"""
import time
from patchright.sync_api import sync_playwright

BOSS_PROFILE = "/mnt/e/0找工作/0大模型全栈知识库/OfferPilot/backend/.playwright/boss"

with sync_playwright() as p:
    print("=== Patchright bypass test ===")
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=BOSS_PROFILE,
        channel="chrome",
        headless=False,
        no_viewport=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # Check cookies first
    cookies = ctx.cookies("https://www.zhipin.com")
    zhipin_cookies = [c for c in cookies if "zhipin" in c.get("domain", "")]
    print(f"zhipin cookies: {len(zhipin_cookies)}")

    # Navigate to BOSS search page
    url = "https://www.zhipin.com/web/geek/job?query=AI&city=101020100"
    print(f"\nNavigating to: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # Monitor URL changes for 12 seconds
    for t in range(6):
        time.sleep(2)
        current = page.url
        title = page.title()
        print(f"  t+{(t+1)*2}s | url={current[:80]} | title={title[:40]}")
        if current == "about:blank":
            print("  >>> FAILED: redirected to about:blank")
            break

    final_url = page.url
    print(f"\nFinal URL: {final_url}")
    print(f"Is about:blank: {final_url == 'about:blank'}")

    if final_url != "about:blank":
        # Try to find job cards
        cards = page.query_selector_all(".job-card-wrapper, .job-card-left, .search-job-result li")
        print(f"Job cards found: {len(cards)}")
        if cards:
            first = cards[0]
            text = first.inner_text()[:200]
            print(f"First card text: {text}")

    page.screenshot(path="/tmp/patchright_boss.png")
    print("\nScreenshot: /tmp/patchright_boss.png")
    ctx.close()
    print("Done.")

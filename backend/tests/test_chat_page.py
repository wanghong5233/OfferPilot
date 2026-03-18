"""Quick test: can patchright load BOSS chat page?"""
import time
from patchright.sync_api import sync_playwright

PROFILE = "/mnt/e/0找工作/0大模型全栈知识库/OfferPilot/backend/.playwright/boss"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE,
        channel="chrome",
        headless=False,
        no_viewport=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    url = "https://www.zhipin.com/web/geek/chat"
    print(f"Navigating to: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    for t in range(10):
        time.sleep(2)
        current_url = page.url
        title = page.title()
        html_len = len(page.content())
        chat_items = page.query_selector_all(".chat-conversation-item, .chat-item, li[role='listitem'], .left-list li")
        print(f"  t+{(t+1)*2:>2}s | url={current_url[:60]} | title={title[:30]} | html={html_len} | items={len(chat_items)}")
        if len(chat_items) > 0:
            print(f"  >>> Found {len(chat_items)} chat items!")
            break

    page.screenshot(path="/tmp/chat_debug.png")
    print(f"\nFinal URL: {page.url}")
    print(f"Screenshot: /tmp/chat_debug.png")

    # Try to dump the visible text on the page
    body_text = page.inner_text("body")[:500] if page.query_selector("body") else "(no body)"
    print(f"Body text: {body_text[:300]}")
    ctx.close()

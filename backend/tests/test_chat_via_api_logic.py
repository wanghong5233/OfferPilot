"""Replicate exact API code path to find where it fails."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

from app.boss_scan import (
    _launch_browser,
    _check_login_required,
    _extract_chat_items_with_retry,
    _BOSS_CHAT_URL,
    _action_delay_ms,
)
from patchright.sync_api import sync_playwright

with sync_playwright() as p:
    print("[1] Launching browser via _launch_browser()...")
    context = _launch_browser(p)
    print(f"    pages count: {len(context.pages)}")
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(20000)

    print(f"[2] Navigating to {_BOSS_CHAT_URL}...")
    page.goto(_BOSS_CHAT_URL, wait_until="domcontentloaded")
    print(f"    URL after goto: {page.url}")
    print(f"    Title: {page.title()}")

    print("[3] Checking login required...")
    login_needed = _check_login_required(page)
    print(f"    login_required: {login_needed}")
    if login_needed:
        print("    >>> LOGIN REQUIRED - this is why API returns empty!")
        page.screenshot(path="/tmp/chat_api_debug.png")
        context.close()
        sys.exit(1)

    print("[4] Waiting for chat items selector...")
    try:
        page.wait_for_selector('li[role="listitem"], .user-list li, .chat-list li', timeout=12000)
        print("    Selector found!")
    except Exception as e:
        print(f"    Selector TIMEOUT: {e}")

    delay_min, delay_max = _action_delay_ms()
    print(f"[5] Action delay: {delay_min}-{delay_max}ms")
    if delay_max > 0:
        import random
        wait = random.randint(delay_min, delay_max)
        print(f"    Waiting {wait}ms...")
        page.wait_for_timeout(wait)

    # Quick count before extraction
    count = page.locator('li[role="listitem"]').count()
    print(f"[6] li[role=listitem] count on page: {count}")

    count2 = page.locator('.user-list li').count()
    print(f"    .user-list li count: {count2}")

    print("[7] Extracting chat items via _extract_chat_items_with_retry...")
    try:
        items = _extract_chat_items_with_retry(page, 5)
        print(f"    Extracted: {len(items)} items")
        for item in items[:3]:
            print(f"      - {item.hr_name}: {item.preview[:60] if item.preview else '(none)'}")
    except Exception as e:
        print(f"    EXTRACTION FAILED: {e}")

    page.screenshot(path="/tmp/chat_api_debug.png")
    print(f"\n[8] Screenshot saved to /tmp/chat_api_debug.png")
    print(f"    Final URL: {page.url}")
    context.close()

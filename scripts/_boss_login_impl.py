"""BOSS Zhipin login helper – opens browser for QR code scan.

Anti-bot bypass: BOSS loads a hidden iframe (zhipinFrame) that fingerprints
the browser and redirects the main frame to about:blank when automation is
detected.  We counter this by:
  1. Removing --enable-automation from Chrome flags
  2. Applying playwright-stealth patches
  3. Blocking the iframe-core.js anti-bot script
  4. Using a MutationObserver to instantly remove zhipinFrame if created
"""

import os
import time

from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import Stealth

    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False

PROFILE_DIR = os.environ["PROFILE_DIR"]
LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"
REAL_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

KILL_ZHIPIN_FRAME_JS = """
(function() {
    if (typeof document === 'undefined') return;
    var observer = new MutationObserver(function(mutations) {
        for (var i = 0; i < mutations.length; i++) {
            var nodes = mutations[i].addedNodes;
            for (var j = 0; j < nodes.length; j++) {
                var node = nodes[j];
                if (node.tagName === 'IFRAME' && (node.name === 'zhipinFrame' || node.id === 'zhipinFrame')) {
                    node.remove();
                }
            }
        }
    });
    if (document.documentElement) {
        observer.observe(document.documentElement, {childList: true, subtree: true});
    } else {
        document.addEventListener('DOMContentLoaded', function() {
            observer.observe(document.documentElement, {childList: true, subtree: true});
        });
    }
})();
"""

print(f"[LOGIN] Profile: {PROFILE_DIR}", flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        user_agent=REAL_UA,
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
    )

    if _HAS_STEALTH:
        stealth = Stealth(
            chrome_runtime=True,
            navigator_languages_override=("zh-CN", "zh", "en-US", "en"),
            navigator_platform_override="Linux x86_64",
            navigator_vendor_override="Google Inc.",
            navigator_user_agent_override=REAL_UA,
        )
        stealth.apply_stealth_sync(ctx)

    ctx.add_init_script(KILL_ZHIPIN_FRAME_JS)
    ctx.route(
        "**/iframe-core*",
        lambda route: route.fulfill(body="", content_type="application/javascript"),
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    print(f"[LOGIN] URL: {page.url}", flush=True)

    time.sleep(3)
    js_url = page.evaluate("window.location.href")
    if "zhipin.com" not in js_url:
        print(f"[LOGIN] URL drifted to {js_url}, retrying...", flush=True)
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

    print(flush=True)
    print("[LOGIN] Browser opened. Please scan QR code to login.", flush=True)
    print("[LOGIN] After login, close the browser window to save session.", flush=True)
    print(flush=True)

    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass

    try:
        ctx.close()
    except Exception:
        pass

print("[LOGIN] Session saved!", flush=True)

"""Dump inner DOM structure of chat list items."""
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
    page.goto("https://www.zhipin.com/web/geek/chat", wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    items = page.evaluate("""
    () => {
        const lis = document.querySelectorAll('li[role="listitem"]');
        return Array.from(lis).slice(0, 5).map((li, idx) => {
            // Get all attributes
            const attrs = {};
            for (const a of li.attributes) {
                attrs[a.name] = a.value?.substring(0, 80);
            }
            // Get inner HTML structure (first level children)
            const children = Array.from(li.children).map(child => ({
                tag: child.tagName,
                class: child.className?.substring(0, 80),
                text: (child.innerText || '').substring(0, 150),
            }));
            // Look for specific sub-elements
            const nameEl = li.querySelector('.name, .nick-name, .username, [class*="name"]');
            const msgEl = li.querySelector('.msg, .last-msg, .message, [class*="msg"]');
            const timeEl = li.querySelector('.time, .date, [class*="time"]');
            const badgeEl = li.querySelector('.badge, .unread, [class*="unread"], [class*="badge"]');
            return {
                idx,
                attrs,
                childCount: li.children.length,
                children,
                outerHTML: li.outerHTML.substring(0, 600),
                nameText: nameEl ? { class: nameEl.className, text: nameEl.innerText?.substring(0, 50) } : null,
                msgText: msgEl ? { class: msgEl.className, text: msgEl.innerText?.substring(0, 100) } : null,
                timeText: timeEl ? { class: timeEl.className, text: timeEl.innerText?.substring(0, 30) } : null,
                badgeText: badgeEl ? { class: badgeEl.className, text: badgeEl.innerText } : null,
            };
        });
    }
    """)
    for item in items:
        print(f"\n=== Item {item['idx']} ===")
        print(f"  Attrs: {item['attrs']}")
        print(f"  Children: {item['childCount']}")
        for c in item['children']:
            print(f"    <{c['tag']} class=\"{c['class']}\"> {c['text'][:80]}")
        print(f"  Name: {item['nameText']}")
        print(f"  Msg:  {item['msgText']}")
        print(f"  Time: {item['timeText']}")
        print(f"  Badge:{item['badgeText']}")
        print(f"  HTML: {item['outerHTML'][:300]}")

    ctx.close()

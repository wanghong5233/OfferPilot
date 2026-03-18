"""Dump real BOSS chat DOM structure to find correct selectors."""
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

    # Test each selector
    test_selectors = [
        ".chat-conversation-item",
        ".chat-item",
        ".geek-item",
        ".list-item",
        ".conversation-item",
        ".chat-list li",
        ".left-list li",
        "li[role='listitem']",
        ".user-list li",
        "ul.chat-list > li",
        "[class*='conversation']",
        "[class*='chat-item']",
        "[class*='geek-item']",
        ".message-list li",
    ]
    for sel in test_selectors:
        count = page.locator(sel).count()
        if count > 0:
            print(f"  {sel}: {count} items")
    
    # Dump actual DOM structure of the chat list area
    dom_info = page.evaluate("""
    () => {
        // Find the left panel / chat list area
        const panels = document.querySelectorAll('.left-list, .chat-list, [class*="list"]');
        const info = [];
        for (const panel of panels) {
            const children = panel.children;
            if (children.length > 2 && children.length < 200) {
                info.push({
                    panelTag: panel.tagName,
                    panelClass: panel.className,
                    childCount: children.length,
                    firstChildTag: children[0]?.tagName,
                    firstChildClass: children[0]?.className?.substring(0, 80),
                    firstChildText: (children[0]?.innerText || '').substring(0, 100),
                    secondChildTag: children[1]?.tagName,
                    secondChildClass: children[1]?.className?.substring(0, 80),
                    secondChildText: (children[1]?.innerText || '').substring(0, 100),
                });
            }
        }
        return info;
    }
    """)
    print("\n=== Panels with multiple children ===")
    for item in dom_info:
        print(f"  Panel: <{item['panelTag']} class=\"{item['panelClass']}\"> ({item['childCount']} children)")
        print(f"    Child 0: <{item['firstChildTag']} class=\"{item['firstChildClass']}\">")
        print(f"      Text: {item['firstChildText'][:80]}")
        print(f"    Child 1: <{item['secondChildTag']} class=\"{item['secondChildClass']}\">")
        print(f"      Text: {item['secondChildText'][:80]}")
        print()

    # Also try to find conversation items with data attributes
    data_items = page.evaluate("""
    () => {
        const allLi = document.querySelectorAll('li');
        const withData = Array.from(allLi).filter(li => 
            li.getAttribute('data-conversation-id') || 
            li.getAttribute('data-id') || 
            li.getAttribute('data-session-id') ||
            li.querySelector('.name, .boss-name, .user-name')
        );
        return withData.slice(0, 3).map(li => ({
            tag: li.tagName,
            class: li.className?.substring(0, 80),
            id: li.id,
            dataAttrs: Object.fromEntries(
                Array.from(li.attributes)
                    .filter(a => a.name.startsWith('data-'))
                    .map(a => [a.name, a.value?.substring(0, 50)])
            ),
            text: (li.innerText || '').substring(0, 150),
        }));
    }
    """)
    print("=== LI items with data attrs or name nodes ===")
    for item in data_items:
        print(f"  <{item['tag']} class=\"{item['class']}\" id=\"{item['id']}\">")
        print(f"    data: {item['dataAttrs']}")
        print(f"    text: {item['text'][:100]}")
        print()

    ctx.close()

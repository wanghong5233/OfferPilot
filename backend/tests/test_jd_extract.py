"""Test: click conversation → find 查看职位 → extract JD from new tab."""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

from app.boss_scan import _get_browser_context, _get_page, _BOSS_CHAT_URL, _navigate_and_check_auth

context = _get_browser_context()
page = _get_page(context)

authed = _navigate_and_check_auth(page, _BOSS_CHAT_URL, operation="JD提取测试")
if not authed:
    print("NOT LOGGED IN!")
    sys.exit(1)

page.wait_for_selector('li[role="listitem"]', timeout=12000)
time.sleep(2)

# Click first conversation
items = page.locator('li[role="listitem"]')
print(f"Chat items: {items.count()}")
items.first.click(timeout=8000)
time.sleep(3)

# Now look for the 查看职位 link
print("\n=== Searching for '查看职位' link ===")

# Try known selectors
test_sels = [
    "a[ka='job-detail']",
    ".position-content a[href*='job_detail']",
    ".job-detail-card a[href*='job_detail']",
    ".info-job a[href*='job_detail']",
    "a[href*='job_detail']",
    "a:has-text('查看职位')",
    "text=查看职位",
    ".chat-job a",
    ".job-card a",
    ".position-title a",
    "a[href*='zhipin.com/job']",
    "[class*='job'] a",
    "[class*='position'] a",
]
for sel in test_sels:
    try:
        loc = page.locator(sel)
        c = loc.count()
        if c > 0:
            href = loc.first.get_attribute("href") or "(no href)"
            text = loc.first.inner_text()[:60]
            print(f"  ✓ {sel}: {c} match(es), href={href[:80]}, text={text}")
    except Exception as e:
        pass

# Broader search: dump all <a> tags in the right panel
print("\n=== All links in conversation panel ===")
links = page.evaluate("""
() => {
    // The right side chat panel
    const panels = document.querySelectorAll('.chat-conversation, .message-wrap, [class*="chat-msg"], [class*="right"], .chat-content');
    const allLinks = [];
    
    // Also search the whole page for links with job-related text/href
    const anchors = document.querySelectorAll('a');
    for (const a of anchors) {
        const text = (a.innerText || '').trim();
        const href = a.href || '';
        if (text.includes('查看职位') || text.includes('职位详情') || 
            href.includes('job_detail') || href.includes('job/') ||
            (a.getAttribute('ka') && a.getAttribute('ka').includes('job'))) {
            allLinks.push({
                tag: a.tagName,
                class: a.className?.substring(0, 80),
                href: href.substring(0, 120),
                text: text.substring(0, 60),
                ka: a.getAttribute('ka'),
                parentClass: a.parentElement?.className?.substring(0, 60),
            });
        }
    }
    return allLinks;
}
""")
for link in links:
    print(f"  <a class=\"{link['class']}\" ka=\"{link['ka']}\" href=\"{link['href']}\">")
    print(f"    text: {link['text']}")
    print(f"    parent: {link['parentClass']}")
    print()

if not links:
    print("  (no job-related links found)")
    # Try dumping the top area of the conversation panel
    print("\n=== Top area of conversation panel HTML ===")
    top_html = page.evaluate("""
    () => {
        const header = document.querySelector('.chat-greet, .job-info, .chat-header, [class*="header"], [class*="info-job"]');
        if (header) return header.outerHTML.substring(0, 500);
        // Try the area above chat messages
        const msgArea = document.querySelector('.message-wrap, .chat-msg, .chat-conversation');
        if (msgArea) {
            const prev = msgArea.previousElementSibling;
            if (prev) return prev.outerHTML.substring(0, 500);
        }
        return '(not found)';
    }
    """)
    print(f"  {top_html[:400]}")

print("\nDone.")

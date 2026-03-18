"""Interactive BOSS login via patchright.

Opens a browser window to zhipin.com.  The user logs in manually (QR scan /
phone / password).  Once the URL leaves the login page, the script
automatically detects login success and saves the session.

Usage:
    python boss_login.py          # normal login
    python boss_login.py --check  # just check if current cookies are valid
"""
import sys
import time
from pathlib import Path

from patchright.sync_api import sync_playwright

PROFILE = str(
    (Path(__file__).resolve().parent / ".playwright" / "boss").resolve()
)
LOGIN_MARKERS = ["/web/user/", "/login", "passport.zhipin.com"]
MAX_WAIT = 300  # 5 minutes for user to complete login


def _is_login_page(url: str) -> bool:
    return any(m in url for m in LOGIN_MARKERS)


def run(*, check_only: bool = False):
    Path(PROFILE).mkdir(parents=True, exist_ok=True)
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
        time.sleep(3)

        current = page.url
        if not _is_login_page(current):
            print(f"[OK] 当前已登录，URL: {current}")
            if check_only:
                ctx.close()
                return True

            # Verify chat items load
            time.sleep(3)
            items = page.query_selector_all('li[role="listitem"]')
            print(f"[OK] 聊天列表加载成功，共 {len(items)} 个对话")
            ctx.close()
            return True

        if check_only:
            print("[FAIL] Cookie 已失效，需要重新登录")
            ctx.close()
            return False

        print("=" * 50)
        print("请在弹出的浏览器窗口中登录 BOSS 直聘")
        print("支持：扫码登录 / 手机验证码 / 密码登录")
        print(f"等待登录... (最长 {MAX_WAIT} 秒)")
        print("=" * 50)

        start = time.time()
        while time.time() - start < MAX_WAIT:
            try:
                current = page.url
            except Exception:
                break
            if not _is_login_page(current) and "zhipin.com" in current:
                print(f"\n[OK] 登录成功！URL: {current}")
                time.sleep(3)
                items = page.query_selector_all('li[role="listitem"]')
                print(f"[OK] 聊天列表: {len(items)} 个对话")
                print("[OK] Cookie 已保存到 profile 目录，后续自动化可直接使用")
                ctx.close()
                return True
            time.sleep(2)

        print("\n[TIMEOUT] 登录超时，请重试")
        ctx.close()
        return False


if __name__ == "__main__":
    check = "--check" in sys.argv
    success = run(check_only=check)
    sys.exit(0 if success else 1)

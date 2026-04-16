"""Browser pool capability for Pulse."""

from .auth import check_login_required, handle_cookie_expired
from .pool import BrowserPool

__all__ = ["BrowserPool", "check_login_required", "handle_cookie_expired"]

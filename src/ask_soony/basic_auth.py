"""
Optional password protection for the dashboard API when it is exposed (for example through a
tunnel to test on a phone). Active only when ASK_DASHBOARD_PASSWORD is set in the process
environment at startup (not read from .env, so local runs and tests stay open).

Two ways in: HTTP basic auth (curl, scripts; any user name) or the /login form, which sets a
long-lived cookie. The cookie is needed for home screen apps on iOS: they show no basic auth
dialog and do not keep basic auth credentials, so a reopened app would get a blank 401 page.
"""

import base64
import hashlib
import hmac
import html
import os
from urllib.parse import parse_qs, quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

# Static PWA files hold no data; browsers (notably iOS) fetch the manifest without credentials,
# and without it the page is not installable and cannot ask for notification permission.
PUBLIC_PATHS = {"/manifest.webmanifest", "/sw.js", "/icon-192.png", "/icon-512.png", "/favicon.svg"}
COOKIE = "ask_session"
COOKIE_DAYS = 30

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0f172a"><title>Ask Soony</title>
<style>body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;display:grid;
place-items:center;min-height:100vh;margin:0}}form{{background:#fff;border:1px solid #e2e8f0;
border-radius:12px;padding:24px;width:min(320px,calc(100vw - 32px));box-sizing:border-box}}
input,button{{width:100%;box-sizing:border-box;font-size:16px;padding:10px;border-radius:8px;
margin-top:12px}}input{{border:1px solid #cbd5e1}}button{{background:#0f172a;color:#fff;border:0}}
.err{{color:#b91c1c;font-size:14px}}</style></head><body>
<form method="post" action="/login"><strong>Ask Soony</strong>{error}
<input type="hidden" name="next" value="{next}">
<input type="password" name="password" placeholder="Password" autofocus
autocomplete="current-password">
<button>Sign in</button></form></body></html>"""


class BasicAuth(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.password = os.environ.get("ASK_DASHBOARD_PASSWORD", "")
        self.token = hmac.new(
            self.password.encode(), b"ask-soony-session", hashlib.sha256
        ).hexdigest()

    def _basic_ok(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except ValueError:
            return False
        return hmac.compare_digest(pw, self.password)

    def _cookie_ok(self, request: Request) -> bool:
        return hmac.compare_digest(request.cookies.get(COOKIE, ""), self.token)

    def _login_page(self, next_url: str, error: str = "", status: int = 200) -> HTMLResponse:
        err = f'<p class="err">{error}</p>' if error else ""
        page = LOGIN_PAGE.format(error=err, next=html.escape(next_url, quote=True))
        return HTMLResponse(page, status_code=status)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Call webhooks authenticate themselves (signature or per-alert token), see calls.py.
        if not self.password or path in PUBLIC_PATHS or path.startswith("/hooks/"):
            return await call_next(request)

        if path == "/login":
            if request.method == "POST":
                raw = parse_qs((await request.body()).decode(errors="replace"))
                form = {k: v[0] for k, v in raw.items()}
                next_url = str(form.get("next") or "/")
                if not next_url.startswith("/") or next_url.startswith("//"):
                    next_url = "/"
                if hmac.compare_digest(str(form.get("password") or ""), self.password):
                    resp = RedirectResponse(next_url, status_code=303)
                    resp.set_cookie(
                        COOKIE,
                        self.token,
                        max_age=COOKIE_DAYS * 86400,
                        httponly=True,
                        secure=True,
                        samesite="lax",
                    )
                    return resp
                return self._login_page(next_url, "Wrong password.", 401)
            return self._login_page(request.query_params.get("next", "/"))

        if self._cookie_ok(request) or self._basic_ok(request):
            return await call_next(request)

        # Pages go to the login form (keeps ?card=..&talk=1 from a push); API calls get 401.
        if request.method == "GET" and not path.startswith("/api/"):
            target = path + (f"?{request.url.query}" if request.url.query else "")
            login = RedirectResponse(f"/login?next={quote(target, safe='/')}", status_code=303)
            login.headers["Cache-Control"] = "no-store"
            return login
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ask-soony"'})

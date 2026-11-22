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
<meta name="theme-color" content="#fbfbfa"><title>Lighthouse</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=2">
<link rel="stylesheet"
href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&display=swap">
<style>*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
background:#fbfbfa;color:#18181a;-webkit-font-smoothing:antialiased;
font-family:"Instrument Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
form{{width:min(340px,calc(100vw - 48px));padding-bottom:12vh}}svg{{display:block}}
h1{{margin:28px 0 0;font-size:27px;line-height:1.2;font-weight:600;letter-spacing:-.025em}}
p{{margin:10px 0 0;font-size:15px;line-height:1.5;color:#5d5d63}}
input,button{{width:100%;height:52px;font:inherit;font-size:17px;border:0;border-radius:14px}}
input{{margin-top:32px;padding:0 16px;background:#f3f3f1;color:#18181a}}
input:focus,button:focus-visible{{outline:2px solid #2348d0;outline-offset:2px}}
button{{margin-top:12px;background:#2348d0;color:#fff;font-weight:500;cursor:pointer;
box-shadow:0 1px 2px rgba(35,72,208,.25)}}button:hover{{background:#1d3db5}}
.err{{color:#c8321f}}
fieldset{{margin:28px 0 0;padding:0;border:0;display:grid;gap:8px}}
legend{{font-size:13px;color:#5d5d63;padding:0 0 8px}}
label{{display:flex;align-items:baseline;gap:10px;padding:12px 14px;border-radius:14px;
background:#f3f3f1;cursor:pointer;font-size:16px}}label span{{color:#5d5d63;font-size:14px}}
label:has(input:checked){{background:#18181a;color:#fff}}
label:has(input:checked) span{{color:#c9c9cc}}
fieldset input{{position:absolute;opacity:0;width:0;height:0;margin:0}}
label:has(input:focus-visible){{outline:2px solid #2348d0;outline-offset:2px}}
fieldset+input{{margin-top:16px}}</style></head><body>
<form method="post" action="/login">
<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path
d="M2.5 8V2.5H8M16 2.5h5.5V8M21.5 16v5.5H16M8 21.5H2.5V16" fill="none" stroke="#18181a"
stroke-width="2.4" stroke-linecap="square"/><rect x="6.5" y="9.5" width="11" height="5" rx="1"
fill="#18181a"/></svg>
<h1>Lighthouse</h1><p>Sign in to see what needs you today.</p>{error}
<input type="hidden" name="next" value="{next}">{who}
<input type="password" name="password" placeholder="Password" aria-label="Password"
autocomplete="current-password">
<button>Sign in</button></form></body></html>"""


ROLES = ("buyer", "approver")
_TITLES = {"buyer": "Buys and chases", "approver": "Signs what costs money"}


def _people() -> dict[str, str]:
    """
    Who signs in, read from the portal like everywhere else: the buyer on most orders and the
    approver of exceptions. Empty when the data cannot say; the form then shows the titles alone.
    """
    try:
        from ask_soony import api

        return {
            "buyer": api.viewer_name(api.portal_root()) or "",
            "approver": api.exception_approver() or "",
        }
    except Exception:  # noqa: BLE001  the sign-in page must render without data
        return {}


def _who_field(chosen: str = "") -> str:
    names = _people()
    chosen = chosen if chosen in ROLES else "buyer"
    rows = []
    for role in ROLES:
        name = (names.get(role) or "").split(",")[0].strip()
        title = _TITLES[role]
        text = f"{html.escape(name)} <span>{title}</span>" if name else title
        checked = " checked" if role == chosen else ""
        rows.append(f'<label><input type="radio" name="as" value="{role}"{checked}>{text}</label>')
    return "<fieldset><legend>Who is signing in</legend>" + "".join(rows) + "</fieldset>"


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

    def _login_page(
        self, next_url: str, error: str = "", status: int = 200, chosen: str = ""
    ) -> HTMLResponse:
        err = f'<p class="err">{error}</p>' if error else ""
        page = LOGIN_PAGE.format(
            error=err, next=html.escape(next_url, quote=True), who=_who_field(chosen)
        )
        return HTMLResponse(page, status_code=status)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Call webhooks authenticate themselves (signature or per-alert token), see calls.py.
        if not self.password or path in PUBLIC_PATHS or path.startswith("/hooks/"):
            return await call_next(request)
        # The count page for the people at the site: no dashboard password, only a valid link
        # (floor.public_path checks the token); its built JS and CSS hold code, no data.
        if path.startswith("/count/") or path.startswith("/api/count/"):
            from ask_soony import floor

            if floor.public_path(path):
                return await call_next(request)
            return Response("This link is not valid any more.", status_code=404)
        # The sign-off page for an approver or an internal recipient: same rule, its own links.
        if path.startswith("/ok/") or path.startswith("/api/ok/"):
            from ask_soony import signoff

            if signoff.public_path(path):
                return await call_next(request)
            return Response("This link is not valid any more.", status_code=404)
        if path.startswith("/assets/"):
            return await call_next(request)

        if path == "/login":
            if request.method == "POST":
                raw = parse_qs((await request.body()).decode(errors="replace"))
                form = {k: v[0] for k, v in raw.items()}
                next_url = str(form.get("next") or "/")
                if not next_url.startswith("/") or next_url.startswith("//"):
                    next_url = "/"
                role = str(form.get("as") or "")
                if hmac.compare_digest(str(form.get("password") or ""), self.password):
                    if role in ROLES:  # the app reads ?as= once, remembers it, drops it
                        next_url += ("&" if "?" in next_url else "?") + f"as={role}"
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
                return self._login_page(next_url, "Wrong password.", 401, role)
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

"""
Optional password protection for the dashboard API when it is exposed (for example through a
tunnel to test on a phone). Active only when ASK_DASHBOARD_PASSWORD is set in the process
environment at startup (not read from .env, so local runs and tests stay open); any user name works.
"""

import base64
import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class BasicAuth(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.password = os.environ.get("ASK_DASHBOARD_PASSWORD", "")

    async def dispatch(self, request: Request, call_next):
        if not self.password:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
            except ValueError:
                pw = ""
            if hmac.compare_digest(pw, self.password):
                return await call_next(request)
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ask-soony"'})

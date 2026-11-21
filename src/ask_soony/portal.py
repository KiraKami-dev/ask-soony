"""Client for the customer portal API: list files with checksums, download one file."""

import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class RemoteFile:
    path: str
    sha256: str
    size: int | None


class PortalError(RuntimeError):
    pass


class PortalClient:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None):
        if not base_url or not token:
            raise PortalError(
                "SOONY_PORTAL_URL and SOONY_PORTAL_TOKEN must be set (see .env.example)"
            )
        self._base = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))
        self._headers = {"Authorization": f"Bearer {token}"}

    def _get(self, url: str, attempts: int = 4) -> httpx.Response:
        """GET with retries on dropped connections and server errors."""
        for attempt in range(attempts):
            try:
                resp = self._http.get(url, headers=self._headers)
                if resp.status_code < 500 or attempt == attempts - 1:
                    return resp
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
            time.sleep(2**attempt)
        raise PortalError(f"GET {url} failed")

    def list_files(self) -> list[RemoteFile]:
        resp = self._get(f"{self._base}/api/v1/files")
        if resp.status_code != 200:
            raise PortalError(f"listing failed: HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        items = body.get("files", body) if isinstance(body, dict) else body
        files = []
        for item in items:
            size = item.get("bytes", item.get("size"))
            files.append(RemoteFile(path=item["path"], sha256=item["sha256"], size=size))
        return files

    def download(self, path: str) -> bytes:
        url = f"{self._base}/api/v1/files/{quote(path, safe='/')}"
        resp = self._get(url)
        if resp.status_code != 200:
            raise PortalError(f"download of {path} failed: HTTP {resp.status_code}")
        return resp.content

    def download_archive(self) -> bytes:
        resp = self._get(f"{self._base}/api/v1/archive.zip")
        if resp.status_code != 200:
            raise PortalError(f"archive download failed: HTTP {resp.status_code}")
        return resp.content

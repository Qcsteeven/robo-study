"""HTTP-клиент робота: только HTTPS, не более N запросов в секунду, повторы с возрастающей паузой."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ..config import SiteSettings


class SiteError(Exception):
    """Страница или документ недоступны после всех попыток."""


class ForbiddenUrl(SiteError):
    """Адрес не разрешён политикой безопасности (не HTTPS или посторонний хост)."""


@dataclass
class Fetched:
    url: str
    status: int
    content: bytes
    headers: dict
    not_modified: bool = False

    @property
    def text(self) -> str:
        enc = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            enc = ctype.split("charset=")[-1].split(";")[0].strip() or enc
        return self.content.decode(enc, errors="replace")


class SiteClient:
    def __init__(self, settings: SiteSettings, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, log=None):
        self.s = settings
        self._sleep = sleep
        self._log = log or (lambda level, msg: None)
        self._lock = threading.Lock()
        self._last = 0.0
        self._client = httpx.Client(
            transport=transport, timeout=settings.timeout, follow_redirects=True,
            headers={"User-Agent": settings.user_agent, "Accept-Language": "ru"})

    def close(self) -> None:
        self._client.close()

    def check_url(self, url: str) -> None:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        base_host = (urlparse(self.s.base_url).hostname or "").lower()
        allowed = {h.lower() for h in self.s.allowed_hosts} | {base_host}
        if host not in allowed:
            raise ForbiddenUrl(f"Хост {host} не входит в список разрешённых")
        if u.scheme == "https":
            return
        if u.scheme == "http" and (not self.s.https_only or host in self.s.allow_http_hosts):
            return
        raise ForbiddenUrl(f"Разрешён только HTTPS: {url}")

    def _throttle(self) -> None:
        if self.s.requests_per_second <= 0:
            return
        interval = 1.0 / self.s.requests_per_second
        with self._lock:
            wait = self._last + interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()

    def get(self, url: str, headers: dict | None = None, max_bytes: int | None = None) -> Fetched:
        self.check_url(url)
        attempts = max(1, self.s.retries + 1)
        last_error = ""
        for attempt in range(attempts):
            if attempt:
                pause = self.s.backoff_base * (2 ** (attempt - 1))
                self._log("WARNING", f"Повтор {attempt}/{attempts - 1} через {pause:g} с: {url} ({last_error})")
                self._sleep(pause)
            self._throttle()
            try:
                with self._client.stream("GET", url, headers=headers or {}) as r:
                    if r.status_code == 304:
                        return Fetched(str(r.url), 304, b"", dict(r.headers), not_modified=True)
                    if r.status_code == 404:
                        raise SiteError(f"HTTP 404 — документ не найден: {url}")
                    if r.status_code >= 400:
                        last_error = f"HTTP {r.status_code}"
                        continue
                    self.check_url(str(r.url))   # редирект не должен уводить с разрешённого хоста
                    chunks, size = [], 0
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if max_bytes and size > max_bytes:
                            raise SiteError(f"Документ больше {max_bytes // 2**20} МБ: {url}")
                        chunks.append(chunk)
                    return Fetched(str(r.url), r.status_code, b"".join(chunks), dict(r.headers))
            except (SiteError, ForbiddenUrl):
                raise
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"
        raise SiteError(f"Не удалось получить {url} после {attempts} попыток ({last_error})")

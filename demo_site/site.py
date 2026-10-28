"""Демо-сайт: маршруты раздела «Образование». Используется веб-приложением (/demo-site/) и тестами (httpx.MockTransport)."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from starlette.requests import Request

from . import render

PREFIX = "/demo-site"


@lru_cache(maxsize=256)
def _doc(slug: str, path: str) -> bytes | None:
    return render.document(slug, path)


def respond(path: str, query: str = "", headers: dict | None = None) -> tuple[int, dict, bytes]:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    if path.startswith(PREFIX):
        path = path[len(PREFIX):]
    path = unquote(path)
    html = {"content-type": "text/html; charset=utf-8"}
    if path in ("/sveden/education/", "/sveden/education"):
        return 200, html, render.education_page().encode()
    if path.rstrip("/") == "/sveden/education/eduop":
        q = parse_qs(query)
        level, year = q.get("level", [""])[0], q.get("year", ["0"])[0]
        return 200, html, render.op_list_page(level, int(year) if year.isdigit() else 0).encode()
    if path.startswith("/sveden/education/docs/"):
        rest = path[len("/sveden/education/docs/"):]
        slug, _, doc_path = rest.partition("/")
        body = _doc(slug, doc_path)
        if body is None:
            return 404, html, b"<h1>404 Not Found</h1>"
        etag = '"' + hashlib.sha1(body).hexdigest() + '"'
        if headers.get("if-none-match") == etag:
            return 304, {"etag": etag}, b""
        return 200, {"content-type": "application/pdf", "etag": etag,
                     "last-modified": "Mon, 05 Oct 2026 08:00:00 GMT"}, body
    return 404, html, b"<h1>404 Not Found</h1>"


def mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        u = urlparse(str(request.url))
        status, headers, body = respond(u.path, u.query, dict(request.headers))
        return httpx.Response(status, headers=headers, content=body)
    return httpx.MockTransport(handler)


def router():
    from fastapi import APIRouter
    from fastapi.responses import Response

    r = APIRouter()

    @r.get(PREFIX + "/{path:path}", include_in_schema=False)
    def demo(path: str, request: "Request"):
        status, headers, body = respond("/" + path + ("/" if request.url.path.endswith("/") and not path.endswith("/") else ""),
                                        request.url.query, dict(request.headers))
        return Response(content=body, status_code=status, headers={k: v for k, v in headers.items() if k != "content-type"},
                        media_type=headers.get("content-type"))
    return r

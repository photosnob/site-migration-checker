"""Pull indexability signals out of raw HTML. JavaScript is not executed."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from migcheck.models import PageSignals
from migcheck.urls import normalize_url

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_WS = re.compile(r"\s+")
_REFRESH_URL = re.compile(r"url\s*=\s*['\"]?([^'\";\s]+)", re.IGNORECASE)


class _Extractor(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.text_parts: list[str] = []
        self.description: str | None = None
        self.canonical: str | None = None
        self.noindex = False
        self.meta_refresh: str | None = None
        self.h1_count = 0
        self._in_title = False
        self._capture_h1 = False
        self._skip = 0
        self._head = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): (value or "") for key, value in attrs}
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "head":
            self._head += 1
        if tag == "title" and self._skip == 0:
            self._in_title = True
        if tag == "h1" and self._skip == 0:
            self.h1_count += 1
            if self.h1_count == 1:
                self._capture_h1 = True
        if tag == "meta" and self._skip == 0:
            self._meta(attr)
        if tag == "link" and self._skip == 0:
            rel = attr.get("rel", "").lower().split()
            href = attr.get("href", "").strip()
            if "canonical" in rel and href and self.canonical is None:
                self.canonical = _absolute(self.page_url, href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._capture_h1 = False
        if tag == "head" and self._head:
            self._head -= 1
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._capture_h1:
            self.h1_parts.append(data)
        if self._head == 0:
            self.text_parts.append(data)

    def _meta(self, attr: dict[str, str]) -> None:
        name = attr.get("name", "").lower()
        equiv = attr.get("http-equiv", "").lower()
        content = attr.get("content", "").strip()
        if name == "description" and content and self.description is None:
            self.description = _clean(content)
        if name in {"robots", "googlebot"} and content:
            tokens = {part.strip().lower() for part in content.split(",")}
            if "noindex" in tokens or "none" in tokens:
                self.noindex = True
        if equiv == "refresh" and content and self.meta_refresh is None:
            target = _refresh_target(content)
            if target:
                self.meta_refresh = _absolute(self.page_url, target)

    def signals(self) -> PageSignals:
        title = _clean("".join(self.title_parts)) or None
        h1 = _clean("".join(self.h1_parts)) or None
        text = _clean(" ".join(self.text_parts))
        return PageSignals(
            title=title,
            description=self.description,
            h1=h1,
            h1_count=self.h1_count,
            canonical=self.canonical,
            noindex=self.noindex,
            meta_refresh=self.meta_refresh,
            word_count=len(text.split()) if text else 0,
        )


def extract_html(html: str, page_url: str) -> PageSignals:
    parser = _Extractor(page_url)
    parser.feed(html)
    parser.close()
    return parser.signals()


def header_noindex(headers: dict[str, str]) -> bool:
    for key, value in headers.items():
        if key.lower() == "x-robots-tag":
            tokens = {part.strip().lower() for part in value.split(",")}
            if "noindex" in tokens or "none" in tokens:
                return True
    return False


def apply_headers(signals: PageSignals, headers: dict[str, str]) -> PageSignals:
    if header_noindex(headers):
        signals.noindex = True
    return signals


def core_title(title: str) -> str:
    """Drop a trailing site name split by a vertical bar or em dash."""
    collapsed = _clean(title)
    parts = re.split(r"\s+[|\u2013\u2014]\s+", collapsed, maxsplit=1)
    return parts[0].strip()


def _absolute(page_url: str, href: str) -> str:
    return normalize_url(urljoin(page_url, href.strip()))


def _clean(value: str) -> str:
    return _WS.sub(" ", value).strip()


def _refresh_target(content: str) -> str | None:
    match = _REFRESH_URL.search(content)
    if match:
        return match.group(1).strip()
    pieces = [part.strip() for part in content.split(";") if part.strip()]
    if len(pieces) >= 2 and pieces[1].lower().startswith(("http://", "https://", "/")):
        return pieces[1]
    return None

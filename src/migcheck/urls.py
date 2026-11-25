"""URL normalization and comparison."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from migcheck.errors import MigcheckError


def display_path(value: str) -> str:
    """Show a path relative to the working directory when that stays readable."""
    if is_http_url(value):
        return value
    try:
        return Path(value).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return value


def is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def parse_origin(value: str) -> str:
    """Return scheme://host for a staging or production origin."""
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise MigcheckError(
            f"Need an absolute http(s) origin, got {value!r}. Example: https://staging.example.com"
        )
    if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
        raise MigcheckError(
            "The new host must be an origin without a path or userinfo "
            f"(got {value!r}). Put subdirectory moves in the redirect map."
        )
    host = parts.hostname.lower()
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme.lower(), netloc, "", "", ""))


def normalize_url(value: str) -> str:
    """Lowercase the scheme and host, drop the fragment and default ports."""
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if parts.port and not (
        (scheme == "http" and parts.port == 80) or (scheme == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    return urlunsplit((scheme, host, path, parts.query, ""))


def strip_userinfo(value: str) -> tuple[str, bool]:
    parts = urlsplit(value.strip())
    if not (parts.username or parts.password):
        return value.strip(), False
    host = (parts.hostname or "").lower()
    netloc = f"{host}:{parts.port}" if parts.port else host
    cleaned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    return cleaned, True


def on_host(url: str, origin: str) -> str:
    """Keep the path and query, replace the scheme and host."""
    base = urlsplit(origin)
    parts = urlsplit(normalize_url(url))
    return urlunsplit((base.scheme, base.netloc, parts.path or "/", parts.query, ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def path_of(url: str) -> str:
    path = urlsplit(url).path or "/"
    query = urlsplit(url).query
    if query:
        return f"{path}?{query}"
    return path


def slash_key(path: str) -> str:
    bare = path.split("?", 1)[0]
    if bare in {"", "/"}:
        return "/"
    return bare.rstrip("/")


def is_home_path(path: str) -> bool:
    bare = path.split("?", 1)[0]
    return bare in {"", "/", "/index.html", "/index.htm", "/index.php"}


def is_home_url(url: str) -> bool:
    return is_home_path(urlsplit(url).path or "/")


def bare_host(host: str) -> str:
    lowered = host.lower()
    if lowered.startswith("www."):
        return lowered[4:]
    return lowered


def same_bare_host(left: str, right: str) -> bool:
    return bare_host(urlsplit(left).netloc) == bare_host(urlsplit(right).netloc)


def sitemap_key(url: str) -> tuple[str, str, str]:
    parts = urlsplit(normalize_url(url))
    return (parts.netloc.lower(), slash_key(parts.path or "/"), parts.query)


def absolutize(target: str, origin: str) -> str:
    """Resolve a redirect target against the new origin."""
    value = target.strip()
    if is_http_url(value):
        return normalize_url(value)
    if value.startswith("//"):
        scheme = urlsplit(origin).scheme
        return normalize_url(f"{scheme}:{value}")
    if not value.startswith("/") and not value.startswith("?"):
        value = "/" + value
    return normalize_url(urljoin(origin.rstrip("/") + "/", value))


def equivalent_page(left: str, right: str) -> bool:
    """Same registrable host, slash-insensitive path, and query. Scheme may differ."""
    a, b = urlsplit(left), urlsplit(right)
    return (
        bare_host(a.netloc) == bare_host(b.netloc)
        and slash_key(a.path or "/") == slash_key(b.path or "/")
        and a.query == b.query
    )

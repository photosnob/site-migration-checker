"""Decide what an observed redirect chain means for one old URL."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from migcheck.catalog import make_issue
from migcheck.fetch import Fetcher
from migcheck.models import FetchResult, Hop, Issue
from migcheck.urls import (
    bare_host,
    is_home_url,
    normalize_url,
    slash_key,
)

_REDIRECTS = {301, 302, 303, 307, 308}
_TEMPORARY = {302, 303, 307}


async def follow(
    fetcher: Fetcher,
    start: str,
    *,
    max_hops: int,
) -> list[tuple[Hop, FetchResult | None]]:
    """Follow redirects one hop at a time, stopping on a loop or the hop cap."""
    url = normalize_url(start)
    seen: set[str] = set()
    hops: list[tuple[Hop, FetchResult | None]] = []
    for _ in range(max_hops + 1):
        if url in seen:
            break
        seen.add(url)
        fetched: FetchResult = await fetcher.get(url)
        location = _safe_location(url, fetched.location)
        hop = Hop(
            url=fetched.url or url, status=fetched.status, location=location, error=fetched.error
        )
        hops.append((hop, fetched))
        if fetched.error or fetched.status not in _REDIRECTS or location is None:
            break
        if location in seen:
            break
        url = location
    return hops


def classify_chain(
    *,
    old_url: str,
    expected_url: str,
    hops: list[Hop],
    max_hops: int,
    forced_status: int | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if not hops:
        return [make_issue("fetch_error", old_url, "No response was recorded.", source="url")]
    final = hops[-1]
    redirect_hops = [hop for hop in hops if hop.status in _REDIRECTS]
    loop = _is_loop(hops)
    if loop:
        issues.append(
            make_issue(
                "redirect_loop",
                old_url,
                f"Redirect loop. Chain: {_trace(hops)}.",
                detail=_trace(hops),
                source="url",
            )
        )
    if any(hop.status in _TEMPORARY for hop in redirect_hops):
        issues.append(
            make_issue(
                "temporary_redirect",
                old_url,
                "A hop uses a temporary redirect (302, 303, or 307). Use 301 or 308 for a permanent move.",
                detail=_trace(hops),
                source="url",
            )
        )
    if len(redirect_hops) >= 2 and not loop:
        issues.append(
            make_issue(
                "redirect_chain",
                old_url,
                f"Redirect chain of {len(redirect_hops)} hops. Collapse it to one 301.",
                detail=_trace(hops),
                source="url",
            )
        )
    if len(redirect_hops) >= max_hops and final.status in _REDIRECTS and not loop:
        issues.append(
            make_issue(
                "too_many_redirects",
                old_url,
                f"Gave up after {max_hops} redirects.",
                detail=_trace(hops),
                source="url",
            )
        )
    if any(hop.status in _REDIRECTS and not hop.location for hop in hops):
        issues.append(
            make_issue(
                "redirect_missing_location",
                old_url,
                "A redirect response had no usable Location header.",
                detail=_trace(hops),
                source="url",
            )
        )
    if any(hop.location and urlsplit(hop.location).scheme not in {"http", "https"} for hop in hops):
        issues.append(
            make_issue(
                "bad_redirect_target",
                old_url,
                "A Location header used a scheme other than http or https.",
                detail=_trace(hops),
                source="url",
            )
        )
    if final.error:
        issues.append(
            make_issue(
                "fetch_error",
                old_url,
                f"Fetch failed for {final.url}: {final.error}",
                detail=_trace(hops),
                source="url",
            )
        )
        return issues
    if loop:
        return issues

    status = final.status
    if forced_status is not None and forced_status >= 400:
        if status == forced_status and not redirect_hops:
            return issues
        issues.append(
            make_issue(
                "forced_status_mismatch",
                old_url,
                f"The redirect map forces {forced_status}, but the server returned {status}.",
                detail=_trace(hops),
                source="url",
            )
        )
        return issues

    if status == 404:
        issues.append(_status_issue("http_404", old_url, final, redirect_hops))
        return issues
    if status == 410:
        issues.append(_status_issue("http_404", old_url, final, redirect_hops))
        return issues
    if status is not None and status >= 500:
        issues.append(_status_issue("http_5xx", old_url, final, redirect_hops))
        return issues
    if status is not None and status >= 400:
        issues.append(_status_issue("http_4xx", old_url, final, redirect_hops))
        return issues
    if status in _REDIRECTS:
        return issues
    if status != 200 or final.url is None:
        issues.append(
            make_issue(
                "unexpected_status",
                old_url,
                f"Final status was {status} at {final.url}.",
                detail=_trace(hops),
                source="url",
            )
        )
        return issues

    issues.extend(_landing_issues(old_url, expected_url, final.url, bool(redirect_hops), hops))
    return issues


def _landing_issues(
    old_url: str,
    expected_url: str,
    final_url: str,
    redirected: bool,
    hops: list[Hop],
) -> list[Issue]:
    if is_home_url(final_url) and not is_home_url(expected_url) and not is_home_url(old_url):
        return [
            make_issue(
                "redirect_homepage",
                old_url,
                (
                    f"Redirects to the homepage ({final_url}). "
                    "Search engines treat many-to-one homepage redirects as soft 404s."
                ),
                detail=_trace(hops),
                source="url",
            )
        ]
    final_parts = urlsplit(final_url)
    expected_parts = urlsplit(expected_url)
    path_exact = (final_parts.path or "/") == (expected_parts.path or "/")
    path_soft = slash_key(final_parts.path or "/") == slash_key(expected_parts.path or "/")
    query_equal = final_parts.query == expected_parts.query
    host_equal = final_parts.netloc.lower() == expected_parts.netloc.lower()
    bare_equal = bare_host(final_parts.netloc) == bare_host(expected_parts.netloc)
    scheme_equal = final_parts.scheme == expected_parts.scheme
    trace = _trace(hops)

    if not bare_equal:
        code = "redirect_offsite" if redirected else "not_redirected"
        message = (
            f"Redirect left the new host. Expected {expected_url} and landed on {final_url}."
            if redirected
            else f"No redirect was served. Expected {expected_url} and the server returned 200 for {final_url}."
        )
        return [make_issue(code, old_url, message, detail=trace, source="url")]
    if not path_soft:
        code = "wrong_target" if redirected else "not_redirected"
        message = (
            f"Redirect landed on {final_url}. Expected {expected_url}."
            if redirected
            else (
                f"No redirect was served. Expected {expected_url} and the server returned 200 for {final_url}."
            )
        )
        return [make_issue(code, old_url, message, detail=trace, source="url")]
    issues: list[Issue] = []
    if not query_equal:
        issues.append(
            make_issue(
                "query_mismatch",
                old_url,
                f"Query string differs. Expected {expected_url} and landed on {final_url}.",
                detail=trace,
                source="url",
            )
        )
        return issues
    if not host_equal:
        issues.append(
            make_issue(
                "host_mismatch",
                old_url,
                f"Host differs by a www prefix. Expected {expected_url} and landed on {final_url}.",
                detail=trace,
                source="url",
            )
        )
    if not path_exact:
        issues.append(
            make_issue(
                "trailing_slash_mismatch",
                old_url,
                f"Trailing slash differs. Expected {expected_url} and landed on {final_url}.",
                detail=trace,
                source="url",
            )
        )
    if not scheme_equal:
        if expected_parts.scheme == "https" and final_parts.scheme == "http":
            issues.append(
                make_issue(
                    "protocol_downgrade",
                    old_url,
                    f"Landed on {final_url} but the new host is https. The redirect drops the connection to cleartext.",
                    detail=trace,
                    source="url",
                )
            )
        elif expected_parts.scheme == "http" and final_parts.scheme == "https":
            issues.append(
                make_issue(
                    "protocol_upgrade",
                    old_url,
                    f"Landed on {final_url}, upgrading the expected http URL to https.",
                    detail=trace,
                    source="url",
                )
            )
    return issues


def _status_issue(code: str, old_url: str, final: Hop, redirect_hops: list[Hop]) -> Issue:
    if redirect_hops:
        message = f"HTTP {final.status} at {final.url} after {len(redirect_hops)} redirect(s)."
    else:
        message = f"HTTP {final.status} at {final.url}."
    return make_issue(code, old_url, message, detail=_trace([*redirect_hops, final]), source="url")


def _is_loop(hops: list[Hop]) -> bool:
    seen: list[str] = []
    for hop in hops:
        if hop.url in seen:
            return True
        seen.append(hop.url)
        if hop.location and hop.location in seen:
            return True
    return False


def _trace(hops: list[Hop]) -> str:
    parts: list[str] = []
    for hop in hops:
        if hop.error:
            parts.append(f"ERR {hop.url} ({hop.error})")
        else:
            arrow = f" -> {hop.location}" if hop.location else ""
            parts.append(f"{hop.status} {hop.url}{arrow}")
    return " | ".join(parts)


def _safe_location(current: str, location: str | None) -> str | None:
    if location is None:
        return None
    raw = location.strip()
    if not raw:
        return None
    absolute = urljoin(current, raw)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"}:
        return absolute
    return normalize_url(absolute)

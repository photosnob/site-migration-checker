"""Issue codes, default severities, and the glossary shown in the report."""

from __future__ import annotations

from migcheck.models import Issue, Severity
from migcheck.util import clip

# Default severity and a one-sentence explanation. A check may pass a stricter
# or milder severity when the same code covers a range (word-count drop, title).
CATALOG: dict[str, tuple[Severity, str]] = {
    "http_404": (
        Severity.CRITICAL,
        "The probed URL, or the page it redirected to, returned 404.",
    ),
    "http_5xx": (
        Severity.CRITICAL,
        "The server returned a 5xx status, so the URL cannot be migrated cleanly.",
    ),
    "http_4xx": (
        Severity.HIGH,
        "The server returned a 4xx status other than 404.",
    ),
    "fetch_error": (
        Severity.HIGH,
        "The URL could not be fetched. Nothing about that URL was verified.",
    ),
    "redirect_loop": (
        Severity.CRITICAL,
        "The redirect chain visits the same URL twice.",
    ),
    "redirect_homepage": (
        Severity.CRITICAL,
        "A non-home URL redirects to the homepage. Search engines treat that pattern as a soft 404.",
    ),
    "redirect_chain": (
        Severity.HIGH,
        "More than one redirect sits between the old URL and the final page. Collapse it to one hop.",
    ),
    "temporary_redirect": (
        Severity.HIGH,
        "A 302, 303, or 307 is a temporary redirect. A permanent move should be 301 or 308.",
    ),
    "too_many_redirects": (
        Severity.CRITICAL,
        "The chain exceeded the hop limit and never settled on a document.",
    ),
    "redirect_missing_location": (
        Severity.HIGH,
        "A redirect status came back without a Location header.",
    ),
    "bad_redirect_target": (
        Severity.HIGH,
        "A Location header used a scheme other than http or https.",
    ),
    "not_redirected": (
        Severity.HIGH,
        "The server returned 200 for the old path instead of redirecting to the mapped page.",
    ),
    "wrong_target": (
        Severity.HIGH,
        "The redirect landed on a different path from the one the map (or the same-path move) expects.",
    ),
    "redirect_offsite": (
        Severity.HIGH,
        "The redirect left the new host.",
    ),
    "host_mismatch": (
        Severity.HIGH,
        "The final host differs from the expected host by a www prefix.",
    ),
    "protocol_downgrade": (
        Severity.HIGH,
        "The final URL is http while the new host is https.",
    ),
    "protocol_upgrade": (
        Severity.INFO,
        "The final URL is https while the expected URL was http.",
    ),
    "trailing_slash_mismatch": (
        Severity.LOW,
        "The final path and the expected path differ only by a trailing slash.",
    ),
    "query_mismatch": (
        Severity.MEDIUM,
        "The path matches, but the query string does not.",
    ),
    "unexpected_status": (
        Severity.MEDIUM,
        "The response status was not a document, a redirect, or an error migcheck classifies.",
    ),
    "forced_status_mismatch": (
        Severity.MEDIUM,
        "A redirect rule forces a 4xx, and the server returned something else.",
    ),
    "noindex": (
        Severity.CRITICAL,
        "The new page sends noindex, in HTML or in an X-Robots-Tag header.",
    ),
    "noindex_kept": (
        Severity.INFO,
        "Both the old page and the new page send noindex.",
    ),
    "canonical_missing": (
        Severity.LOW,
        "The new HTML has no rel=canonical. Google can infer one, but a redesign should set it.",
    ),
    "canonical_mismatch": (
        Severity.HIGH,
        "The canonical URL points at a different host or path than the page itself.",
    ),
    "title_missing": (
        Severity.HIGH,
        "The new page has no title element.",
    ),
    "title_changed": (
        Severity.MEDIUM,
        "The title changed beyond a site-name suffix. Severity falls to low when the titles are still close.",
    ),
    "meta_description_missing": (
        Severity.MEDIUM,
        "The old page had a meta description and the new page does not.",
    ),
    "meta_description_shortened": (
        Severity.LOW,
        "The meta description shrank to 60% or less of the old length.",
    ),
    "h1_missing": (
        Severity.MEDIUM,
        "The new page has no H1.",
    ),
    "h1_changed": (
        Severity.LOW,
        "The H1 text changed.",
    ),
    "multiple_h1": (
        Severity.LOW,
        "The new page has more than one H1.",
    ),
    "word_count_drop": (
        Severity.MEDIUM,
        "Visible word count fell by at least the configured threshold. Drops of 50% or more are high.",
    ),
    "meta_refresh": (
        Severity.MEDIUM,
        "The page returns 200 and uses a meta refresh. Replace it with a 301.",
    ),
    "body_truncated": (
        Severity.INFO,
        "The response body was longer than the byte cap, so the word count was not compared.",
    ),
    "map_duplicate": (
        Severity.LOW,
        "The same source pattern appears twice with the same target and status. First match wins.",
    ),
    "map_conflict": (
        Severity.HIGH,
        "The same source pattern appears twice with a different target or status. First match wins.",
    ),
    "map_cycle": (
        Severity.HIGH,
        "Literal rules send a path back to itself.",
    ),
    "map_shadowed": (
        Severity.MEDIUM,
        "An earlier rule already matches every path this rule could match, so this rule never runs.",
    ),
    "map_target_404": (
        Severity.CRITICAL,
        "A concrete redirect target returns 404 or 410.",
    ),
    "map_target_error": (
        Severity.HIGH,
        "A concrete redirect target could not be fetched or returned an error other than 404.",
    ),
    "map_parse_error": (
        Severity.MEDIUM,
        "A line in the redirect file could not be parsed and was skipped.",
    ),
    "map_unsupported": (
        Severity.INFO,
        "A rule uses a feature migcheck does not simulate (conditions, mid-path stars, or query matches).",
    ),
    "map_temporary": (
        Severity.MEDIUM,
        "A rule uses a temporary status. Permanent moves should be 301 or 308.",
    ),
    "map_offsite_target": (
        Severity.MEDIUM,
        "A rule sends the URL to a host other than the new site.",
    ),
    "map_targets_homepage": (
        Severity.CRITICAL,
        "A rule sends a non-home path to the homepage.",
    ),
    "map_rewrite": (
        Severity.INFO,
        "A rule uses status 200, which is an internal rewrite rather than a client redirect.",
    ),
    "map_force_flag": (
        Severity.INFO,
        "A Netlify or Cloudflare rule uses the force flag. migcheck does not see the file tree.",
    ),
    "robots_disallow": (
        Severity.CRITICAL,
        "robots.txt blocks a URL that the migration expects to be reachable.",
    ),
    "robots_blocks_all": (
        Severity.CRITICAL,
        "robots.txt disallows the whole site.",
    ),
    "robots_missing": (
        Severity.MEDIUM,
        "The new host has no robots.txt. Crawling is allowed, but confirm that was intentional.",
    ),
    "robots_unreadable": (
        Severity.HIGH,
        "robots.txt could not be fetched or parsed.",
    ),
    "sitemap_missing": (
        Severity.MEDIUM,
        "An indexable destination returned 200 and is not in the new sitemap.",
    ),
    "sitemap_missing_file": (
        Severity.HIGH,
        "The new sitemap URL returned 404.",
    ),
    "sitemap_unreadable": (
        Severity.HIGH,
        "The new sitemap was fetched but could not be parsed.",
    ),
    "sitemap_lists_noindex": (
        Severity.MEDIUM,
        "The new sitemap lists a URL that also sends noindex.",
    ),
    "sitemap_extra": (
        Severity.INFO,
        "The new sitemap contains URLs that are not destinations of the old URL list. This is informational.",
    ),
    "invalid_url": (
        Severity.HIGH,
        "A supplied URL was not absolute http or https and was skipped.",
    ),
    "url_credentials": (
        Severity.LOW,
        "A URL contained userinfo. It was stripped before the request.",
    ),
}


def make_issue(
    code: str,
    url: str,
    message: str,
    *,
    detail: str = "",
    severity: Severity | None = None,
    source: str = "url",
) -> Issue:
    default, _explanation = CATALOG[code]
    return Issue(
        code=code,
        severity=severity or default,
        url=url,
        message=message,
        detail=clip(detail),
        source=source,
    )

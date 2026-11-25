"""Run a migration check and return a report. Writers live elsewhere."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from migcheck import __version__
from migcheck.catalog import make_issue
from migcheck.classify import classify_chain, follow
from migcheck.content import content_issues
from migcheck.errors import MigcheckError
from migcheck.extract import apply_headers, extract_html
from migcheck.fetch import FixtureFetcher, HttpFetcher, RateLimiter, make_client
from migcheck.loaders import load_local_records, load_redirects, merge_records, parse_sitemap_xml
from migcheck.models import (
    FetchResult,
    Issue,
    PageResult,
    PageSignals,
    Report,
    RuleView,
    RunConfig,
    Severity,
    UrlRecord,
)
from migcheck.patterns import CompiledRule, concrete_target, lint_rules, match_rules
from migcheck.robots import RobotsFile, blocking_rule, parse_robots
from migcheck.suggest import suggest_redirects
from migcheck.urls import (
    absolutize,
    equivalent_page,
    host_of,
    is_http_url,
    normalize_url,
    on_host,
    sitemap_key,
)

log = logging.getLogger("migcheck")
_REDIRECTS = {301, 302, 303, 307, 308}
_SITEMAP_DEPTH = 3
_SITEMAP_CHILDREN = 50


def execute(config: RunConfig) -> Report:
    return asyncio.run(_execute(config))


async def _execute(config: RunConfig) -> Report:
    if config.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    fetcher = _open_fetcher(config)
    try:
        records, issues = await _load_records(config, fetcher)
        rules: list[CompiledRule] = []
        if config.redirects:
            rules, map_issues = load_redirects(config.redirects, config.redirects_format)
            issues.extend(map_issues)
        issues.extend(lint_rules(rules, config.new_host))
        pages = list(
            await asyncio.gather(
                *(_check_page(config, fetcher, rules, record) for record in records)
            )
        )
        issues.extend(await _check_targets(config, fetcher, rules, pages))
        robots_body, robots_file, robot_issues = await _load_robots(config, fetcher)
        issues.extend(robot_issues)
        sitemap_urls, sitemap_issues = await _load_new_sitemap(config, fetcher)
        issues.extend(sitemap_issues)
        _apply_sitemap(pages, sitemap_urls, issues)
        if robots_file is not None and not robots_file.blocks_all:
            _apply_robots(pages, sitemap_urls, robots_file, issues)
        elif robots_file is not None and robots_file.blocks_all:
            blocked = _important_urls(pages, sitemap_urls)
            issues.append(
                make_issue(
                    "robots_blocks_all",
                    config.new_host + "/",
                    f"robots.txt disallows the whole site ({len(blocked)} important URLs are affected).",
                    source="robots",
                )
            )
        pool = await _suggestion_pool(config, fetcher, sitemap_urls, pages)
        suggestions = (
            suggest_redirects(pages, pool, threshold=config.suggest_threshold)
            if config.suggest
            else []
        )
        report_issues = _collect(issues, pages, records)
        counts = dict.fromkeys(("critical", "high", "medium", "low", "info"), 0)
        for issue in report_issues:
            counts[issue.severity.value] += 1
        readiness, reason = _readiness(report_issues)
        extra = _extra_sitemap_urls(pages, sitemap_urls)
        return Report(
            version=__version__,
            generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            sample=config.sample,
            disclaimer=config.disclaimer,
            mode=config.mode,
            new_host=config.new_host,
            inputs=list(config.inputs),
            url_count=len(records),
            rule_count=len(rules),
            wildcard_rules=sum(1 for rule in rules if rule.wildcard and not rule.unsupported),
            readiness=readiness,
            readiness_reason=reason,
            counts=counts,
            issues=report_issues,
            pages=pages,
            suggestions=suggestions,
            rules=[_rule_view(rule) for rule in rules],
            robots_body=None if robots_body is None else robots_body[:8000],
            new_sitemap_urls=sitemap_urls,
            extra_sitemap_urls=extra,
            fail_on=config.fail_on,
        )
    finally:
        await fetcher.aclose()


def exit_code(report: Report) -> int:
    order = ["critical", "high", "medium", "low", "info"]
    limit = order.index(report.fail_on)
    if any(order.index(issue.severity.value) <= limit for issue in report.issues):
        return 1
    return 0


async def _check_page(
    config: RunConfig,
    fetcher: FixtureFetcher | HttpFetcher,
    rules: list[CompiledRule],
    record: UrlRecord,
) -> PageResult:
    old_url = record.url
    matched = match_rules(old_url, rules)
    probe = on_host(old_url, config.new_host) if config.mode == "staging" else old_url
    forced: int | None = None
    if matched and matched.rule.status >= 400:
        expected = probe
        forced = matched.rule.status
        content_target = None
    elif matched and matched.rule.status == 200:
        expected = probe
        content_target = absolutize(matched.target, config.new_host)
    elif matched:
        expected = absolutize(matched.target, config.new_host)
        content_target = expected
    elif config.mode == "live" and host_of(old_url) != host_of(config.new_host):
        expected = on_host(old_url, config.new_host)
        content_target = expected
    else:
        expected = probe
        content_target = expected

    followed = await follow(fetcher, probe, max_hops=config.max_hops)
    hops = [hop for hop, _fetch in followed]
    final_fetch = followed[-1][1] if followed else None
    issues = classify_chain(
        old_url=old_url,
        expected_url=expected,
        hops=hops,
        max_hops=config.max_hops,
        forced_status=forced,
    )
    final_url = hops[-1].url if hops else None
    new_fetch, expected_status, indexable = await _destination_fetch(
        fetcher,
        expected=expected,
        content_target=content_target,
        final_fetch=final_fetch,
        final_url=final_url,
        rewrite=bool(matched and matched.rule.status == 200),
    )
    if (
        new_fetch is None
        and content_target
        and not any(issue.code == "fetch_error" for issue in issues)
    ):
        checked = await fetcher.get(content_target)
        if checked.error:
            issues.append(
                make_issue(
                    "fetch_error",
                    old_url,
                    f"Could not fetch the expected page {content_target}: {checked.error}",
                    source="url",
                )
            )
    old_signals: PageSignals | None = None
    old_truncated = False
    if config.compare_content:
        old_signals, old_truncated = await _baseline(fetcher, record)
    new_signals: PageSignals | None = None
    truncated = old_truncated
    if new_fetch is not None and _is_html(new_fetch):
        new_signals = apply_headers(extract_html(new_fetch.body, new_fetch.url), new_fetch.headers)
        truncated = truncated or new_fetch.truncated
        compare_url = expected if equivalent_page(new_fetch.url, expected) else new_fetch.url
        issues.extend(
            content_issues(
                old=old_signals if config.compare_content else None,
                new=new_signals,
                old_url=old_url,
                new_url=compare_url,
                max_word_drop=config.max_word_drop,
                min_words=config.min_words,
                truncated=truncated,
            )
        )
    if (
        final_fetch is not None
        and new_fetch is not final_fetch
        and final_fetch.status == 200
        and _is_html(final_fetch)
    ):
        landed = apply_headers(extract_html(final_fetch.body, final_fetch.url), final_fetch.headers)
        if landed.noindex and not any(issue.code == "noindex" for issue in issues):
            kept = old_signals is not None and old_signals.noindex
            issues.append(
                make_issue(
                    "noindex_kept" if kept else "noindex",
                    old_url,
                    f"The landed page {final_fetch.url} sends noindex.",
                    source="content",
                )
            )
    if config.verbose:
        codes = ",".join(issue.code for issue in issues) or "ok"
        log.info("%s %s", old_url, codes)
    return PageResult(
        old_url=old_url,
        probe_url=probe,
        expected_url=expected,
        final_url=final_url,
        hops=hops,
        clicks=record.clicks,
        impressions=record.impressions,
        matched_rule=None if matched is None else matched.rule.raw,
        expected_status=expected_status,
        indexable_url=indexable,
        issues=issues,
        old_signals=old_signals,
        new_signals=new_signals,
    )


async def _destination_fetch(
    fetcher: FixtureFetcher | HttpFetcher,
    *,
    expected: str,
    content_target: str | None,
    final_fetch: FetchResult | None,
    final_url: str | None,
    rewrite: bool,
) -> tuple[FetchResult | None, int | None, str | None]:
    """Pick the HTML we compare, the expected URL's status, and the indexable URL."""
    if (
        not rewrite
        and final_fetch is not None
        and final_fetch.status == 200
        and final_url
        and equivalent_page(final_url, expected)
    ):
        return final_fetch, 200, final_fetch.url

    target = content_target or expected
    followed = await follow(fetcher, target, max_hops=5)
    if not followed:
        return None, None, None
    first = followed[0][0]
    _last_hop, last_fetch = followed[-1]
    indexable = last_fetch.url if last_fetch and last_fetch.status == 200 else None
    new_fetch = last_fetch if last_fetch and last_fetch.status == 200 else None
    return new_fetch, first.status, indexable


async def _baseline(
    fetcher: FixtureFetcher | HttpFetcher, record: UrlRecord
) -> tuple[PageSignals | None, bool]:
    fetched = await fetcher.get(record.url)
    if fetched.status == 200 and _is_html(fetched) and not fetched.error:
        return apply_headers(
            extract_html(fetched.body, fetched.url), fetched.headers
        ), fetched.truncated
    if any(
        (
            record.baseline_title,
            record.baseline_description,
            record.baseline_h1,
            record.baseline_words is not None,
        )
    ):
        return (
            PageSignals(
                title=record.baseline_title,
                description=record.baseline_description,
                h1=record.baseline_h1,
                h1_count=1 if record.baseline_h1 else 0,
                word_count=record.baseline_words or 0,
            ),
            False,
        )
    return None, False


async def _check_targets(
    config: RunConfig,
    fetcher: FixtureFetcher | HttpFetcher,
    rules: list[CompiledRule],
    pages: list[PageResult],
) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[str] = set()
    page_failures = {
        sitemap_key(page.final_url)
        for page in pages
        if page.final_url and any(issue.code == "http_404" for issue in page.issues)
    }
    for rule in rules:
        target = concrete_target(rule)
        if target is None:
            continue
        absolute = absolutize(target, config.new_host)
        if absolute in seen:
            continue
        seen.add(absolute)
        followed = await follow(fetcher, absolute, max_hops=5)
        if not followed:
            continue
        final, fetched = followed[-1]
        if fetched and fetched.error:
            issues.append(
                make_issue(
                    "map_target_error",
                    absolute,
                    f"Redirect target {absolute} could not be fetched: {fetched.error}",
                    detail=rule.raw,
                    source="map",
                )
            )
            continue
        status = final.status
        if status in {404, 410}:
            if sitemap_key(final.url) in page_failures:
                continue
            issues.append(
                make_issue(
                    "map_target_404",
                    absolute,
                    f"Redirect target {absolute} returned {status} (rule line {rule.line}).",
                    detail=rule.raw,
                    source="map",
                )
            )
        elif status is not None and status >= 400:
            issues.append(
                make_issue(
                    "map_target_error",
                    absolute,
                    f"Redirect target {absolute} returned {status} (rule line {rule.line}).",
                    detail=rule.raw,
                    source="map",
                )
            )
    return issues


async def _load_records(
    config: RunConfig, fetcher: FixtureFetcher | HttpFetcher
) -> tuple[list[UrlRecord], list[Issue]]:
    groups: list[list[UrlRecord]] = []
    issues: list[Issue] = []
    local_sitemaps: list[str] = []
    for source in config.old_sitemaps:
        if is_http_url(source):
            pages = await _read_sitemap_url(fetcher, source)
            groups.append([UrlRecord(url=url, origin=f"sitemap:{source}") for url in pages])
        else:
            local_sitemaps.append(source)
    if local_sitemaps:
        records, more = load_local_records(local_sitemaps, kind="sitemap")
        groups.append(records)
        issues.extend(more)
    if config.old_csvs:
        records, more = load_local_records(config.old_csvs, kind="csv")
        groups.append(records)
        issues.extend(more)
    if config.old_url_files:
        records, more = load_local_records(config.old_url_files, kind="urls")
        groups.append(records)
        issues.extend(more)
    merged = merge_records(groups)
    if not merged:
        raise MigcheckError("No usable absolute http(s) URLs were found.")
    if len(merged) > config.max_urls:
        raise MigcheckError(
            f"The URL list has {len(merged)} URLs, above --max-urls {config.max_urls}."
        )
    return merged, issues


async def _load_robots(
    config: RunConfig, fetcher: FixtureFetcher | HttpFetcher
) -> tuple[str | None, RobotsFile | None, list[Issue]]:
    source = config.robots or f"{config.new_host}/robots.txt"
    if not is_http_url(source):
        path = Path(source)
        if not path.is_file():
            return (
                None,
                None,
                [
                    make_issue(
                        "robots_unreadable",
                        source,
                        f"robots file not found: {source}",
                        source="robots",
                    )
                ],
            )
        text = path.read_text(encoding="utf-8")
        return text, parse_robots(text), []
    fetched = await fetcher.get(source)
    if fetched.error:
        return (
            None,
            None,
            [
                make_issue(
                    "robots_unreadable",
                    source,
                    f"Could not fetch robots.txt: {fetched.error}",
                    source="robots",
                )
            ],
        )
    if fetched.status == 404:
        return (
            None,
            None,
            [
                make_issue(
                    "robots_missing",
                    source,
                    f"{source} returned 404. Crawling is allowed until a file is published.",
                    source="robots",
                )
            ],
        )
    if fetched.status != 200:
        return (
            None,
            None,
            [
                make_issue(
                    "robots_unreadable",
                    source,
                    f"{source} returned {fetched.status}.",
                    source="robots",
                )
            ],
        )
    try:
        parsed = parse_robots(fetched.body)
    except ValueError as exc:
        return (
            fetched.body,
            None,
            [
                make_issue(
                    "robots_unreadable",
                    source,
                    f"Could not parse robots.txt: {exc}",
                    source="robots",
                )
            ],
        )
    return fetched.body, parsed, []


async def _load_new_sitemap(
    config: RunConfig, fetcher: FixtureFetcher | HttpFetcher
) -> tuple[list[str], list[Issue]]:
    source = config.new_sitemap or f"{config.new_host}/sitemap.xml"
    try:
        if is_http_url(source):
            urls = await _read_sitemap_url(fetcher, source)
        else:
            path = Path(source)
            if not path.is_file():
                return [], [
                    make_issue(
                        "sitemap_missing_file",
                        source,
                        f"Sitemap file not found: {source}",
                        source="sitemap",
                    )
                ]
            pages, children = parse_sitemap_xml(path.read_text(encoding="utf-8"), origin=str(path))
            urls = list(pages)
            for child in children[:_SITEMAP_CHILDREN]:
                if is_http_url(child):
                    urls.extend(await _read_sitemap_url(fetcher, child))
    except MigcheckError as exc:
        code = "sitemap_missing_file" if "404" in str(exc) else "sitemap_unreadable"
        return [], [make_issue(code, source, str(exc), source="sitemap")]
    return urls, []


async def _read_sitemap_url(
    fetcher: FixtureFetcher | HttpFetcher, url: str, depth: int = 0
) -> list[str]:
    fetched = await fetcher.get(url)
    if fetched.error:
        raise MigcheckError(f"Could not fetch sitemap {url}: {fetched.error}")
    if fetched.status == 404:
        raise MigcheckError(f"Sitemap {url} returned 404.")
    if fetched.status != 200:
        raise MigcheckError(f"Sitemap {url} returned {fetched.status}.")
    try:
        pages, children = parse_sitemap_xml(fetched.body, origin=url)
    except MigcheckError as exc:
        raise MigcheckError(f"Could not parse sitemap {url}: {exc}") from exc
    if depth >= _SITEMAP_DEPTH:
        return pages
    for child in children[:_SITEMAP_CHILDREN]:
        pages.extend(await _read_sitemap_url(fetcher, child, depth + 1))
    return pages


def _apply_sitemap(pages: list[PageResult], sitemap_urls: list[str], issues: list[Issue]) -> None:
    if not sitemap_urls:
        return
    keys = {sitemap_key(url) for url in sitemap_urls}
    listed_noindex: set[tuple[str, str, str]] = set()
    for page in pages:
        if not page.indexable_url:
            continue
        key = sitemap_key(page.indexable_url)
        noindex = bool(page.new_signals and page.new_signals.noindex)
        if noindex and key in keys and key not in listed_noindex:
            listed_noindex.add(key)
            page.issues.append(
                make_issue(
                    "sitemap_lists_noindex",
                    page.old_url,
                    f"{page.indexable_url} is in the new sitemap and sends noindex.",
                    source="sitemap",
                )
            )
        elif not noindex and key not in keys:
            page.issues.append(
                make_issue(
                    "sitemap_missing",
                    page.old_url,
                    f"{page.indexable_url} returned 200 and is missing from the new sitemap.",
                    source="sitemap",
                )
            )
    extras = _extra_sitemap_urls(pages, sitemap_urls)
    if extras:
        preview = ", ".join(extras[:8])
        suffix = "" if len(extras) <= 8 else f" (+{len(extras) - 8} more)"
        issues.append(
            make_issue(
                "sitemap_extra",
                extras[0],
                f"{len(extras)} new-sitemap URL(s) are not destinations of the old list: {preview}{suffix}.",
                source="sitemap",
            )
        )


def _apply_robots(
    pages: list[PageResult],
    sitemap_urls: list[str],
    robots: RobotsFile,
    issues: list[Issue],
) -> None:
    attached: set[tuple[str, str, str]] = set()
    for url in _important_urls(pages, sitemap_urls):
        path = urlsplit(url).path or "/"
        rule = blocking_rule(path, robots)
        if rule is None:
            continue
        key = sitemap_key(url)
        if key in attached:
            continue
        attached.add(key)
        issue = make_issue(
            "robots_disallow",
            url,
            f"robots.txt user-agent {robots.group!r} disallows {rule.path}, which blocks {url}.",
            source="robots",
        )
        owner = next(
            (
                page
                for page in pages
                if sitemap_key(page.expected_url) == key
                or (page.indexable_url and sitemap_key(page.indexable_url) == key)
            ),
            None,
        )
        if owner is None:
            issues.append(issue)
        else:
            owner.issues.append(issue)


def _important_urls(pages: list[PageResult], sitemap_urls: list[str]) -> list[str]:
    values: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    def add(url: str | None) -> None:
        if not url:
            return
        key = sitemap_key(url)
        if key in seen:
            return
        seen.add(key)
        values.append(url)

    for page in pages:
        add(page.expected_url)
        add(page.indexable_url)
    for url in sitemap_urls:
        add(url)
    return values


def _extra_sitemap_urls(pages: list[PageResult], sitemap_urls: list[str]) -> list[str]:
    expected: set[tuple[str, str, str]] = set()
    for page in pages:
        expected.add(sitemap_key(page.expected_url))
        if page.indexable_url:
            expected.add(sitemap_key(page.indexable_url))
    return [url for url in sitemap_urls if sitemap_key(url) not in expected]


async def _suggestion_pool(
    config: RunConfig,
    fetcher: FixtureFetcher | HttpFetcher,
    sitemap_urls: list[str],
    pages: list[PageResult],
) -> list[tuple[str, PageSignals | None]]:
    urls: list[str] = []
    seen: set[str] = set()
    for url in list(sitemap_urls) + [page.indexable_url for page in pages if page.indexable_url]:
        if url is None:
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        urls.append(key)
        if len(urls) >= config.suggest_pool:
            break
    pool: list[tuple[str, PageSignals | None]] = []
    for url in urls:
        fetched = await fetcher.get(url)
        signals = None
        if fetched.status == 200 and _is_html(fetched):
            signals = apply_headers(extract_html(fetched.body, fetched.url), fetched.headers)
        pool.append((fetched.url or url, signals))
    return pool


def _collect(issues: list[Issue], pages: list[PageResult], records: list[UrlRecord]) -> list[Issue]:
    clicks = {record.url: record.clicks or 0 for record in records}
    seen: set[tuple[str, str, str]] = set()
    collected: list[Issue] = []

    def add(issue: Issue) -> None:
        key = (issue.code, issue.url, issue.message)
        if key in seen:
            return
        seen.add(key)
        collected.append(issue)

    for issue in issues:
        add(issue)
    for page in pages:
        for issue in page.issues:
            add(issue)
    collected.sort(
        key=lambda issue: (
            issue.severity.rank,
            -(clicks.get(issue.url) or 0),
            issue.url,
            issue.code,
            issue.message,
        )
    )
    return collected


def _readiness(issues: list[Issue]) -> tuple[str, str]:
    severities = {issue.severity for issue in issues}
    if Severity.CRITICAL in severities:
        return (
            "BLOCKED",
            "Critical issues will drop or hide URLs. Do not cut over yet.",
        )
    if Severity.HIGH in severities:
        return (
            "NEEDS REVIEW",
            "High issues should be decided before cutover. None are critical.",
        )
    if Severity.MEDIUM in severities or Severity.LOW in severities:
        return (
            "READY WITH NOTES",
            "No critical or high issues. Medium and low notes are listed below.",
        )
    return ("READY", "No critical, high, medium, or low issues.")


def _rule_view(rule: CompiledRule) -> RuleView:
    return RuleView(
        line=rule.line,
        source=rule.source,
        target=rule.target,
        status=rule.status,
        wildcard=rule.wildcard,
        unsupported=rule.unsupported,
    )


def _is_html(result: FetchResult) -> bool:
    if result.status != 200 or result.error:
        return False
    ctype = result.content_type.lower()
    if "xml" in ctype and "html" not in ctype:
        return False
    if "html" in ctype:
        return True
    body = result.body.lstrip().lower()
    return body.startswith("<!doctype html") or body.startswith("<html")


def _open_fetcher(config: RunConfig) -> FixtureFetcher | HttpFetcher:
    if config.fixture_dir is not None:
        return _fixture_fetcher(config.fixture_dir)
    client = make_client(
        timeout=config.timeout,
        concurrency=config.concurrency,
        user_agent=config.user_agent,
    )
    return HttpFetcher(
        client,
        RateLimiter(config.rps),
        asyncio.Semaphore(config.concurrency),
        max_bytes=config.max_body_bytes,
    )


def _fixture_fetcher(directory: Path) -> FixtureFetcher:
    payload = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    mapping: dict[str, FetchResult] = {}
    for row in payload["responses"]:
        url = normalize_url(str(row["url"]))
        if "body_file" in row:
            body = (directory / str(row["body_file"])).read_text(encoding="utf-8")
        else:
            body = str(row.get("body", ""))
        headers = {str(key).lower(): str(value) for key, value in row.get("headers", {}).items()}
        mapping[url] = FetchResult(
            url=url,
            status=int(row["status"]) if row.get("status") is not None else None,
            headers=headers,
            body=body,
            content_type=headers.get("content-type", ""),
            location=headers.get("location"),
            error=row.get("error"),
            truncated=False,
        )
    return FixtureFetcher(mapping)

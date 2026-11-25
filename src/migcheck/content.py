"""Compare on-page signals. Word counts are whitespace-separated visible text."""

from __future__ import annotations

from difflib import SequenceMatcher
from urllib.parse import urlsplit

from migcheck.catalog import make_issue
from migcheck.extract import core_title
from migcheck.models import Issue, PageSignals, Severity
from migcheck.urls import equivalent_page, slash_key


def content_issues(
    *,
    old: PageSignals | None,
    new: PageSignals | None,
    old_url: str,
    new_url: str,
    max_word_drop: float,
    min_words: int,
    truncated: bool,
) -> list[Issue]:
    if new is None:
        return []
    issues: list[Issue] = []
    if new.noindex:
        if old is not None and old.noindex:
            issues.append(
                make_issue(
                    "noindex_kept",
                    old_url,
                    f"{new_url} sends noindex, and the old page did too.",
                    source="content",
                )
            )
        else:
            issues.append(
                make_issue(
                    "noindex",
                    old_url,
                    f"{new_url} sends noindex and will not be indexed.",
                    source="content",
                )
            )
    if not new.canonical:
        issues.append(
            make_issue(
                "canonical_missing",
                old_url,
                f"{new_url} has no canonical link.",
                source="content",
            )
        )
    elif not _canonical_matches(new.canonical, new_url):
        issues.append(
            make_issue(
                "canonical_mismatch",
                old_url,
                f"Canonical is {new.canonical}, which does not match {new_url}.",
                source="content",
            )
        )
    issues.extend(_title_issues(old, new, old_url))
    issues.extend(_description_issues(old, new, old_url))
    issues.extend(_h1_issues(old, new, old_url))
    if truncated:
        issues.append(
            make_issue(
                "body_truncated",
                old_url,
                "The response was cut at the body byte cap, so word count was not compared.",
                source="content",
            )
        )
    elif old is not None and old.word_count >= min_words and new.word_count < old.word_count:
        drop = (old.word_count - new.word_count) / old.word_count
        if drop + 1e-9 >= max_word_drop:
            severity = Severity.HIGH if drop >= 0.5 else None
            percent = round(drop * 100)
            threshold = round(max_word_drop * 100)
            issues.append(
                make_issue(
                    "word_count_drop",
                    old_url,
                    (
                        f"Word count fell from {old.word_count} to {new.word_count} "
                        f"({percent}% drop; threshold {threshold}%)."
                    ),
                    severity=severity,
                    source="content",
                )
            )
    if new.meta_refresh and not equivalent_page(new.meta_refresh, new_url):
        issues.append(
            make_issue(
                "meta_refresh",
                old_url,
                f"{new_url} returns 200 and meta-refreshes to {new.meta_refresh}. Replace it with a 301.",
                source="content",
            )
        )
    if new.h1_count > 1:
        issues.append(
            make_issue(
                "multiple_h1",
                old_url,
                f"{new_url} has {new.h1_count} H1 elements.",
                source="content",
            )
        )
    return issues


def _title_issues(old: PageSignals | None, new: PageSignals, old_url: str) -> list[Issue]:
    if not new.title:
        severity = Severity.HIGH if old is not None and old.title else Severity.MEDIUM
        return [
            make_issue(
                "title_missing",
                old_url,
                "The new page has no title element.",
                severity=severity,
                source="content",
            )
        ]
    if old is None or not old.title:
        return []
    old_core = core_title(old.title).casefold()
    new_core = core_title(new.title).casefold()
    if old_core == new_core:
        return []
    ratio = SequenceMatcher(None, old_core, new_core).ratio()
    if ratio >= 0.92:
        return []
    severity = Severity.LOW if ratio >= 0.75 else Severity.MEDIUM
    return [
        make_issue(
            "title_changed",
            old_url,
            f"Title changed from {old.title!r} to {new.title!r} (similarity {ratio:.2f}).",
            severity=severity,
            detail=f"old: {old.title} | new: {new.title}",
            source="content",
        )
    ]


def _description_issues(old: PageSignals | None, new: PageSignals, old_url: str) -> list[Issue]:
    if old is None or not old.description:
        return []
    if not new.description:
        return [
            make_issue(
                "meta_description_missing",
                old_url,
                "The old page had a meta description and the new page does not.",
                detail=f"old: {old.description}",
                source="content",
            )
        ]
    if len(old.description) >= 40 and len(new.description) <= 0.6 * len(old.description):
        return [
            make_issue(
                "meta_description_shortened",
                old_url,
                (
                    f"Meta description shrank from {len(old.description)} to "
                    f"{len(new.description)} characters."
                ),
                source="content",
            )
        ]
    return []


def _h1_issues(old: PageSignals | None, new: PageSignals, old_url: str) -> list[Issue]:
    issues: list[Issue] = []
    if not new.h1:
        issues.append(
            make_issue("h1_missing", old_url, "The new page has no H1.", source="content")
        )
        return issues
    if old is not None and old.h1 and _norm(old.h1) != _norm(new.h1):
        issues.append(
            make_issue(
                "h1_changed",
                old_url,
                f"H1 changed from {old.h1!r} to {new.h1!r}.",
                source="content",
            )
        )
    return issues


def _canonical_matches(canonical: str, page_url: str) -> bool:
    left, right = urlsplit(canonical), urlsplit(page_url)
    return (
        left.scheme == right.scheme
        and left.netloc.lower() == right.netloc.lower()
        and slash_key(left.path or "/") == slash_key(right.path or "/")
        and left.query == right.query
    )


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()

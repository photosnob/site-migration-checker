"""Suggest redirect targets for unmapped URLs that did not land.

Scores are the greater of a slug comparison and a title comparison. A suggestion
is a hint, not a verified mapping, and it does not change the readiness result.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from migcheck.extract import core_title
from migcheck.models import PageResult, PageSignals, Suggestion
from migcheck.urls import equivalent_page, is_home_url, sitemap_key

_EXTENSION = re.compile(r"\.(html?|php|aspx)$", re.IGNORECASE)


def suggest_redirects(
    pages: list[PageResult],
    pool: list[tuple[str, PageSignals | None]],
    *,
    threshold: float,
    limit: int = 3,
) -> list[Suggestion]:
    candidates = [(url, signals) for url, signals in pool if not is_home_url(url)]
    suggestions: list[Suggestion] = []
    for page in pages:
        if page.matched_rule or is_home_url(page.old_url):
            continue
        if _landed(page):
            continue
        suggestions.extend(
            _rank(
                page.old_url,
                page.old_signals,
                candidates,
                threshold=threshold,
                limit=limit,
            )
        )
    suggestions.sort(key=lambda item: (item.old_url, -item.score, item.candidate_url))
    return suggestions


def _landed(page: PageResult) -> bool:
    if not page.final_url or not page.hops:
        return False
    status = page.hops[-1].status
    return status == 200 and equivalent_page(page.final_url, page.expected_url)


def _rank(
    old_url: str,
    old_signals: PageSignals | None,
    candidates: list[tuple[str, PageSignals | None]],
    *,
    threshold: float,
    limit: int,
) -> list[Suggestion]:
    old_slug = slug_text(old_url)
    old_title = core_title(old_signals.title) if old_signals and old_signals.title else ""
    ranked: list[Suggestion] = []
    for candidate_url, signals in candidates:
        if sitemap_key(candidate_url) == sitemap_key(old_url):
            continue
        candidate_slug = slug_text(candidate_url)
        candidate_title = core_title(signals.title) if signals and signals.title else ""
        slug_ratio = _ratio(old_slug, candidate_slug)
        title_ratio = _ratio(old_title, candidate_title)
        score = max(slug_ratio, title_ratio)
        if score < threshold:
            continue
        via, old_label, candidate_label = _labels(
            old_slug,
            candidate_slug,
            old_title,
            candidate_title,
            slug_ratio,
            title_ratio,
            threshold,
        )
        ranked.append(
            Suggestion(
                old_url=old_url,
                candidate_url=candidate_url,
                score=score,
                via=via,
                old_label=old_label,
                candidate_label=candidate_label,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.candidate_url))
    return ranked[:limit]


def slug_text(url: str) -> str:
    path = urlsplit(url).path
    if path in {"", "/"}:
        return ""
    last = path.rstrip("/").split("/")[-1]
    last = _EXTENSION.sub("", last)
    return last.replace("-", " ").replace("_", " ").casefold()


def _ratio(left: str, right: str) -> float:
    if len(left) < 4 or len(right) < 4:
        return 0.0
    return SequenceMatcher(None, left.casefold(), right.casefold()).ratio()


def _labels(
    old_slug: str,
    candidate_slug: str,
    old_title: str,
    candidate_title: str,
    slug_ratio: float,
    title_ratio: float,
    threshold: float,
) -> tuple[str, str, str]:
    slug_hit = slug_ratio >= threshold
    title_hit = title_ratio >= threshold
    if slug_hit and title_hit:
        via = "title+slug"
        old_label = f"slug: {old_slug}; title: {old_title}"
        candidate_label = f"slug: {candidate_slug}; title: {candidate_title}"
    elif title_hit and title_ratio >= slug_ratio:
        via = "title"
        old_label = f"title: {old_title}"
        candidate_label = f"title: {candidate_title}"
    else:
        via = "slug"
        old_label = f"slug: {old_slug}"
        candidate_label = f"slug: {candidate_slug}"
    return via, old_label, candidate_label

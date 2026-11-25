"""Data shared across a migration check."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


@dataclass(frozen=True)
class UrlRecord:
    url: str
    origin: str
    clicks: int | None = None
    impressions: int | None = None
    baseline_title: str | None = None
    baseline_description: str | None = None
    baseline_h1: str | None = None
    baseline_words: int | None = None


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    url: str
    message: str
    detail: str = ""
    source: str = "url"


@dataclass
class PageSignals:
    title: str | None = None
    description: str | None = None
    h1: str | None = None
    h1_count: int = 0
    canonical: str | None = None
    noindex: bool = False
    meta_refresh: str | None = None
    word_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "h1": self.h1,
            "h1_count": self.h1_count,
            "canonical": self.canonical,
            "noindex": self.noindex,
            "meta_refresh": self.meta_refresh,
            "word_count": self.word_count,
        }


@dataclass(frozen=True)
class Hop:
    url: str
    status: int | None
    location: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "status": self.status,
            "location": self.location,
            "error": self.error,
        }


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int | None
    headers: dict[str, str]
    body: str
    content_type: str
    location: str | None
    error: str | None = None
    truncated: bool = False


@dataclass
class PageResult:
    old_url: str
    probe_url: str
    expected_url: str
    final_url: str | None
    hops: list[Hop]
    clicks: int | None = None
    impressions: int | None = None
    matched_rule: str | None = None
    expected_status: int | None = None
    indexable_url: str | None = None
    issues: list[Issue] = field(default_factory=list)
    old_signals: PageSignals | None = None
    new_signals: PageSignals | None = None

    @property
    def result(self) -> str:
        severities = {issue.severity for issue in self.issues}
        if severities & {Severity.CRITICAL, Severity.HIGH}:
            return "fail"
        if severities & {Severity.MEDIUM, Severity.LOW}:
            return "warn"
        return "pass"

    def to_dict(self) -> dict[str, object]:
        return {
            "old_url": self.old_url,
            "probe_url": self.probe_url,
            "expected_url": self.expected_url,
            "final_url": self.final_url,
            "result": self.result,
            "clicks": self.clicks,
            "impressions": self.impressions,
            "matched_rule": self.matched_rule,
            "expected_status": self.expected_status,
            "indexable_url": self.indexable_url,
            "hops": [hop.to_dict() for hop in self.hops],
            "issue_codes": [issue.code for issue in self.issues],
            "old": None if self.old_signals is None else self.old_signals.to_dict(),
            "new": None if self.new_signals is None else self.new_signals.to_dict(),
        }


@dataclass(frozen=True)
class Suggestion:
    old_url: str
    candidate_url: str
    score: float
    via: str
    old_label: str
    candidate_label: str

    def to_dict(self) -> dict[str, object]:
        return {
            "old_url": self.old_url,
            "candidate_url": self.candidate_url,
            "score": round(self.score, 4),
            "via": self.via,
            "old_label": self.old_label,
            "candidate_label": self.candidate_label,
        }


@dataclass(frozen=True)
class RuleView:
    line: int
    source: str
    target: str
    status: int
    wildcard: bool
    unsupported: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "line": self.line,
            "source": self.source,
            "target": self.target,
            "status": self.status,
            "wildcard": self.wildcard,
            "unsupported": self.unsupported,
        }


@dataclass
class Report:
    version: str
    generated_at: str
    sample: bool
    disclaimer: str
    mode: str
    new_host: str
    inputs: list[str]
    url_count: int
    rule_count: int
    wildcard_rules: int
    readiness: str
    readiness_reason: str
    counts: dict[str, int]
    issues: list[Issue]
    pages: list[PageResult]
    suggestions: list[Suggestion]
    rules: list[RuleView]
    robots_body: str | None
    new_sitemap_urls: list[str]
    extra_sitemap_urls: list[str]
    fail_on: str

    def to_dict(self) -> dict[str, object]:
        return {
            "tool": "migcheck",
            "version": self.version,
            "generated_at": self.generated_at,
            "sample": self.sample,
            "disclaimer": self.disclaimer,
            "mode": self.mode,
            "new_host": self.new_host,
            "inputs": self.inputs,
            "url_count": self.url_count,
            "rule_count": self.rule_count,
            "wildcard_rules": self.wildcard_rules,
            "readiness": self.readiness,
            "readiness_reason": self.readiness_reason,
            "counts": self.counts,
            "fail_on": self.fail_on,
            "page_results": _page_counts(self.pages),
            "issues": [_issue_dict(issue) for issue in self.issues],
            "suggestions": [item.to_dict() for item in self.suggestions],
            "rules": [rule.to_dict() for rule in self.rules],
            "robots_body": self.robots_body,
            "new_sitemap_urls": self.new_sitemap_urls,
            "extra_sitemap_urls": self.extra_sitemap_urls,
            "pages": [page.to_dict() for page in self.pages],
        }


def _issue_dict(issue: Issue) -> dict[str, object]:
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "source": issue.source,
        "url": issue.url,
        "message": issue.message,
        "detail": issue.detail,
    }


def _page_counts(pages: list[PageResult]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for page in pages:
        counts[page.result] += 1
    return counts


@dataclass
class RunConfig:
    old_sitemaps: list[str]
    old_csvs: list[str]
    old_url_files: list[str]
    new_host: str
    mode: str
    redirects: str | None
    redirects_format: str
    new_sitemap: str | None
    robots: str | None
    output_dir: Path
    suggest: bool
    write_redirects: bool
    redirects_out: Path | None
    rps: float
    concurrency: int
    timeout: float
    max_hops: int
    max_body_bytes: int
    max_urls: int
    max_word_drop: float
    min_words: int
    suggest_threshold: float
    suggest_pool: int
    fail_on: str
    compare_content: bool
    user_agent: str
    fixture_dir: Path | None
    sample: bool
    disclaimer: str
    inputs: list[str]
    color: bool
    verbose: bool

"""Terminal, CSV, JSON, and suggested _redirects output."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from migcheck.models import PageResult, Report
from migcheck.urls import equivalent_page, host_of, is_home_path, path_of
from migcheck.util import csv_safe


def render_terminal(
    report: Report,
    *,
    output_dir: Path,
    color: bool,
    redirects_out: Path | None,
) -> str:
    passed = sum(1 for page in report.pages if page.result == "pass")
    warned = sum(1 for page in report.pages if page.result == "warn")
    failed = sum(1 for page in report.pages if page.result == "fail")
    lines: list[str] = [f"migcheck {report.version}"]
    if report.sample:
        lines.append("Sample: synthetic fixture, not a measurement of a live site.")
    if report.disclaimer:
        lines.append(report.disclaimer)
    lines.extend(
        [
            f"Mode: {report.mode}",
            f"New host: {report.new_host}",
            f"Old URLs: {report.url_count}",
            f"Redirect rules: {report.rule_count} ({report.wildcard_rules} wildcard)",
            f"Generated: {report.generated_at}",
            "",
            _paint(f"Readiness: {report.readiness}", _readiness_color(report.readiness), color),
            report.readiness_reason,
            "",
            f"Pages: {passed} pass, {warned} warn, {failed} fail",
            "Issues: "
            + ", ".join(
                f"{report.counts[name]} {name}"
                for name in ("critical", "high", "medium", "low", "info")
            ),
            f"Exit: {'failure' if _failed(report) else 'success'} (--fail-on {report.fail_on})",
            "",
            "Issues",
        ]
    )
    shown = report.issues[:80]
    if not shown:
        lines.append("  None.")
    for issue in shown:
        label = _paint(issue.severity.value.upper(), _severity_color(issue.severity.value), color)
        lines.append(f"{label}  {issue.code}")
        if issue.url:
            lines.append(f"  {issue.url}")
        lines.append(f"  {issue.message}")
    remaining = len(report.issues) - len(shown)
    if remaining > 0:
        lines.append(f"  … {remaining} more in the HTML report.")
    lines.extend(["", f"Suggestions: {len(report.suggestions)}"])
    lines.append(f"Wrote report to {output_dir.as_posix()}")
    lines.append(f"HTML report: {(output_dir / 'index.html').as_posix()}")
    if redirects_out is not None:
        lines.append(f"Suggested redirects: {redirects_out.as_posix()}")
    lines.append("")
    return "\n".join(lines)


def render_issues_csv(report: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["severity", "code", "source", "url", "message", "detail"])
    for issue in report.issues:
        writer.writerow(
            [
                csv_safe(issue.severity.value),
                csv_safe(issue.code),
                csv_safe(issue.source),
                csv_safe(issue.url),
                csv_safe(issue.message),
                csv_safe(issue.detail),
            ]
        )
    return buffer.getvalue()


def render_pages_csv(report: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "old_url",
            "probe_url",
            "expected_url",
            "final_url",
            "indexable_url",
            "result",
            "http_status",
            "hops",
            "issue_codes",
            "input_clicks",
            "input_impressions",
            "old_title",
            "new_title",
            "old_description",
            "new_description",
            "old_h1",
            "new_h1",
            "old_words",
            "new_words",
            "canonical",
            "noindex",
            "matched_rule",
        ]
    )
    for page in report.pages:
        old = page.old_signals
        new = page.new_signals
        status = page.hops[-1].status if page.hops else ""
        writer.writerow(
            [
                csv_safe(page.old_url),
                csv_safe(page.probe_url),
                csv_safe(page.expected_url),
                csv_safe(page.final_url),
                csv_safe(page.indexable_url),
                csv_safe(page.result),
                csv_safe(status),
                csv_safe(
                    len([hop for hop in page.hops if hop.status in {301, 302, 303, 307, 308}])
                ),
                csv_safe(" ".join(issue.code for issue in page.issues)),
                csv_safe(page.clicks),
                csv_safe(page.impressions),
                csv_safe(None if old is None else old.title),
                csv_safe(None if new is None else new.title),
                csv_safe(None if old is None else old.description),
                csv_safe(None if new is None else new.description),
                csv_safe(None if old is None else old.h1),
                csv_safe(None if new is None else new.h1),
                csv_safe(None if old is None else old.word_count),
                csv_safe(None if new is None else new.word_count),
                csv_safe(None if new is None else new.canonical),
                csv_safe(None if new is None else new.noindex),
                csv_safe(page.matched_rule),
            ]
        )
    return buffer.getvalue()


def render_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"


def render_redirects(report: Report) -> str:
    lines = [
        f"# Generated by migcheck {report.version}",
        "# Review every line before deploying. This is not a complete redirect map.",
        "# Commented lines are notes, not rules.",
    ]
    if report.sample:
        lines.append("# SAMPLE fixture output. Do not deploy this file.")
    if report.disclaimer:
        lines.append("# " + report.disclaimer)
    lines.append("")
    active: list[str] = []
    notes: list[str] = []
    emitted: set[str] = set()
    for page in report.pages:
        _redirect_for_page(page, report.new_host, active, notes, emitted)
    suggestions: list[str] = []
    for item in report.suggestions:
        source = path_of(item.old_url)
        dest = _on_new(item.candidate_url, report.new_host)
        if source in emitted or source == dest or _home_target(dest):
            notes.append(f"# Suggestion for {source} -> {dest} was not added as a rule.")
            continue
        suggestions.append(
            f"# Suggested by {item.via} similarity {item.score:.2f}. "
            f"{item.old_label} -> {item.candidate_label}. Confirm this page."
        )
        suggestions.append(f"{source} {dest} 301")
        emitted.add(source)
    if active:
        lines.append("# Fixes")
        lines.extend(active)
        lines.append("")
    if suggestions:
        lines.append("# Suggestions")
        lines.extend(suggestions)
        lines.append("")
    if notes:
        lines.append("# Notes")
        lines.extend(notes)
        lines.append("")
    return "\n".join(lines)


def write_outputs(report: Report, output_dir: Path, redirects_out: Path | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(render_json(report), encoding="utf-8")
    (output_dir / "issues.csv").write_text(render_issues_csv(report), encoding="utf-8")
    (output_dir / "pages.csv").write_text(render_pages_csv(report), encoding="utf-8")
    (output_dir / "index.html").write_text(render_html(report), encoding="utf-8")
    (output_dir / "terminal.txt").write_text(
        render_terminal(
            report,
            output_dir=output_dir,
            color=False,
            redirects_out=redirects_out,
        ),
        encoding="utf-8",
    )
    if redirects_out is not None:
        redirects_out.parent.mkdir(parents=True, exist_ok=True)
        redirects_out.write_text(render_redirects(report), encoding="utf-8")


def render_html(report: Report) -> str:
    from migcheck.html_report import render_html as _render

    return _render(report)


def _redirect_for_page(
    page: PageResult,
    new_host: str,
    active: list[str],
    notes: list[str],
    emitted: set[str],
) -> None:
    source = path_of(page.old_url)
    codes = {issue.code for issue in page.issues}
    final_status = page.hops[-1].status if page.hops else None
    landed = bool(
        page.final_url
        and final_status == 200
        and equivalent_page(page.final_url, page.expected_url)
    )
    expected = _on_new(page.expected_url, new_host)
    target_ok = page.expected_status == 200 and not _home_target(expected)

    if "redirect_loop" in codes or "http_5xx" in codes:
        notes.append(f"# {source} did not resolve ({_codes(codes)}). No automatic rule.")
        return
    if "redirect_homepage" in codes:
        notes.append(f"# {source} currently redirects to the homepage.")
        if page.matched_rule and target_ok and source != expected:
            _add(
                active,
                emitted,
                source,
                expected,
                "Server sends this URL to the homepage; the map names another page.",
            )
        return
    if ("redirect_chain" in codes or "temporary_redirect" in codes) and landed:
        if source != expected:
            _add(active, emitted, source, expected, "Collapse the chain to one 301.")
        else:
            notes.append(f"# {source} reaches the right path, but not as a single 301.")
        return
    if "protocol_downgrade" in codes and landed:
        notes.append(
            f"# {source} reaches the right path over http. Redirect to the https URL instead."
        )
        return
    if "trailing_slash_mismatch" in codes and landed and page.final_url:
        dest = _on_new(page.final_url, new_host)
        if source != dest and not _home_target(dest):
            _add(active, emitted, source, dest, "Observed trailing-slash normalization.")
        return
    if page.matched_rule and not landed and target_ok and source != expected:
        _add(
            active,
            emitted,
            source,
            expected,
            "Mapped target was not what the probed host returned.",
        )
        return
    refresh = page.new_signals.meta_refresh if page.new_signals else None
    if "meta_refresh" in codes and refresh:
        dest = _on_new(refresh, new_host)
        if host_of(refresh) == host_of(new_host) and source != dest and not _home_target(dest):
            _add(active, emitted, source, dest, "Replaces a meta refresh.")
            return
    if "http_404" in codes or "http_4xx" in codes:
        notes.append(f"# {source} returned an error.")


def _add(active: list[str], emitted: set[str], source: str, dest: str, comment: str) -> None:
    if source in emitted or source == dest or _home_target(dest):
        return
    active.append(f"# {comment}")
    active.append(f"{source} {dest} 301")
    emitted.add(source)


def _on_new(url: str, new_host: str) -> str:
    if host_of(url) == host_of(new_host):
        return path_of(url)
    return url


def _home_target(target: str) -> bool:
    path = urlsplit(target).path if target.startswith(("http://", "https://")) else target
    return is_home_path(path.split("?", 1)[0])


def _codes(codes: set[str]) -> str:
    return ", ".join(sorted(codes))


def _failed(report: Report) -> bool:
    order = ["critical", "high", "medium", "low", "info"]
    limit = order.index(report.fail_on)
    return any(order.index(issue.severity.value) <= limit for issue in report.issues)


def _paint(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _readiness_color(readiness: str) -> str:
    return {
        "BLOCKED": "1;31",
        "NEEDS REVIEW": "1;33",
        "READY WITH NOTES": "1;36",
        "READY": "1;32",
    }.get(readiness, "0")


def _severity_color(severity: str) -> str:
    return {"critical": "31", "high": "33", "medium": "36", "low": "32", "info": "2"}.get(
        severity, "0"
    )

"""Load URL lists and redirect maps."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from xml.etree import ElementTree

from migcheck.catalog import make_issue
from migcheck.errors import MigcheckError
from migcheck.models import Issue, UrlRecord
from migcheck.patterns import (
    LOCATION_BLOCK,
    RETURN_LINE,
    REWRITE_LINE,
    CompiledRule,
    RawRule,
    compile_path_rules,
    parse_nginx,
)
from migcheck.urls import is_http_url, normalize_url, strip_userinfo

_URL_HEADERS = ("url", "address", "page", "top pages", "loc", "uri")
_CLICK_HEADERS = ("clicks",)
_IMPRESSION_HEADERS = ("impressions",)
_TITLE_HEADERS = ("title 1", "title")
_DESCRIPTION_HEADERS = ("meta description 1", "meta description", "description")
_H1_HEADERS = ("h1-1", "h1")
_WORD_HEADERS = ("word count",)
_SOURCE_HEADERS = ("source", "from", "old", "origin", "url", "path")
_TARGET_HEADERS = ("target", "to", "new", "destination", "dest")
_STATUS_HEADERS = ("status", "code", "status code")

_STATUS_TOKEN = re.compile(r"^(\d{3})(!)?$")


def load_local_records(paths: list[str], *, kind: str) -> tuple[list[UrlRecord], list[Issue]]:
    records: list[UrlRecord] = []
    issues: list[Issue] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise MigcheckError(f"File not found: {raw_path}")
        text = path.read_text(encoding="utf-8-sig")
        if kind == "csv":
            records.extend(_csv_records(text, origin=str(path)))
        elif kind == "urls":
            records.extend(_text_records(text, origin=str(path)))
        elif kind == "sitemap":
            pages, _children = parse_sitemap_xml(text, origin=str(path))
            records.extend(UrlRecord(url=url, origin=f"sitemap:{path}") for url in pages)
        else:
            raise MigcheckError(f"Unknown URL source type: {kind}")
    cleaned, more = _prepare_records(records)
    issues.extend(more)
    return cleaned, issues


def parse_sitemap_xml(text: str, *, origin: str) -> tuple[list[str], list[str]]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise MigcheckError(f"Could not parse sitemap XML ({origin}): {exc}") from exc
    locs = [
        element.text.strip()
        for element in root.iter()
        if _local(element.tag) == "loc" and element.text and element.text.strip()
    ]
    if _local(root.tag) == "sitemapindex":
        return [], locs
    return locs, []


def merge_records(groups: list[list[UrlRecord]]) -> list[UrlRecord]:
    merged: dict[str, UrlRecord] = {}
    order: list[str] = []
    for group in groups:
        for record in group:
            key = normalize_url(record.url)
            existing = merged.get(key)
            if existing is None:
                merged[key] = record
                order.append(key)
                continue
            merged[key] = UrlRecord(
                url=existing.url,
                origin=existing.origin,
                clicks=existing.clicks if existing.clicks is not None else record.clicks,
                impressions=(
                    existing.impressions if existing.impressions is not None else record.impressions
                ),
                baseline_title=existing.baseline_title or record.baseline_title,
                baseline_description=existing.baseline_description or record.baseline_description,
                baseline_h1=existing.baseline_h1 or record.baseline_h1,
                baseline_words=(
                    existing.baseline_words
                    if existing.baseline_words is not None
                    else record.baseline_words
                ),
            )
    return [merged[key] for key in order]


def load_redirects(path: str, fmt: str) -> tuple[list[CompiledRule], list[Issue]]:
    file = Path(path)
    if not file.is_file():
        raise MigcheckError(f"Redirect file not found: {path}")
    text = file.read_text(encoding="utf-8-sig")
    chosen = fmt if fmt != "auto" else detect_redirect_format(file, text)
    if chosen == "csv":
        raw, issues = _csv_rules(text)
        compiled, more = compile_path_rules(raw)
        return compiled, issues + more
    if chosen == "nginx":
        return parse_nginx(text)
    if chosen == "netlify":
        raw, issues = _netlify_rules(text)
        compiled, more = compile_path_rules(raw)
        return compiled, issues + more
    raise MigcheckError(f"Unknown redirect format: {chosen}")


def detect_redirect_format(path: Path, text: str) -> str:
    name = path.name.lower()
    if name.endswith(".csv"):
        return "csv"
    lowered = text.lower()
    if (
        "rewrite " in lowered
        or re.search(r"\blocation\s+", lowered)
        or re.search(r"\breturn\s+30[0-9]", lowered)
    ):
        return "nginx"
    if name in {"_redirects", "redirects"} or name.endswith(".redirects"):
        return "netlify"
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if "," in first and re.search(r"source|from|old|target|destination", first, re.IGNORECASE):
        return "csv"
    return "netlify"


def _prepare_records(records: list[UrlRecord]) -> tuple[list[UrlRecord], list[Issue]]:
    prepared: list[UrlRecord] = []
    issues: list[Issue] = []
    for record in records:
        cleaned, had_secret = strip_userinfo(record.url.strip())
        if had_secret:
            issues.append(
                make_issue(
                    "url_credentials",
                    cleaned,
                    "Removed userinfo from the URL before checking it.",
                    source="url",
                )
            )
        if not is_http_url(cleaned):
            issues.append(
                make_issue(
                    "invalid_url",
                    record.url,
                    "Skipped a URL that is not absolute http or https.",
                    source="url",
                )
            )
            continue
        prepared.append(
            UrlRecord(
                url=normalize_url(cleaned),
                origin=record.origin,
                clicks=record.clicks,
                impressions=record.impressions,
                baseline_title=record.baseline_title,
                baseline_description=record.baseline_description,
                baseline_h1=record.baseline_h1,
                baseline_words=record.baseline_words,
            )
        )
    return prepared, issues


def _csv_records(text: str, *, origin: str) -> list[UrlRecord]:
    reader, rows = _dict_rows(text)
    if reader is None:
        return []
    headers = [_header(name) for name in (reader.fieldnames or [])]
    url_key = _pick(headers, _URL_HEADERS)
    if url_key is None:
        raise MigcheckError(
            f"No URL column in {origin}. Expected one of: {', '.join(_URL_HEADERS)}."
        )
    click_key = _pick(headers, _CLICK_HEADERS)
    impression_key = _pick(headers, _IMPRESSION_HEADERS)
    title_key = _pick(headers, _TITLE_HEADERS)
    description_key = _pick(headers, _DESCRIPTION_HEADERS)
    h1_key = _pick(headers, _H1_HEADERS)
    word_key = _pick(headers, _WORD_HEADERS)
    records: list[UrlRecord] = []
    for row in rows:
        normalized = {_header(key): value for key, value in row.items() if key is not None}
        url = (normalized.get(url_key) or "").strip()
        if not url:
            continue
        records.append(
            UrlRecord(
                url=url,
                origin=f"csv:{origin}",
                clicks=_parse_int(normalized.get(click_key) if click_key else None),
                impressions=_parse_int(normalized.get(impression_key) if impression_key else None),
                baseline_title=_blank(normalized.get(title_key) if title_key else None),
                baseline_description=_blank(
                    normalized.get(description_key) if description_key else None
                ),
                baseline_h1=_blank(normalized.get(h1_key) if h1_key else None),
                baseline_words=_parse_int(normalized.get(word_key) if word_key else None),
            )
        )
    return records


def _text_records(text: str, *, origin: str) -> list[UrlRecord]:
    records: list[UrlRecord] = []
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        records.append(UrlRecord(url=value, origin=f"urls:{origin}"))
    return records


def _netlify_rules(text: str) -> tuple[list[RawRule], list[Issue]]:
    rules: list[RawRule] = []
    issues: list[Issue] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        try:
            tokens = _tokenize(line)
        except MigcheckError as exc:
            issues.append(
                make_issue(
                    "map_parse_error",
                    "",
                    f"Line {line_number}: {exc}",
                    detail=raw_line.strip(),
                    source="map",
                )
            )
            continue
        parsed = _parse_netlify_tokens(tokens, line_number, raw_line.strip())
        if isinstance(parsed, Issue):
            issues.append(parsed)
            continue
        rules.append(parsed)
    return rules, issues


def _parse_netlify_tokens(tokens: list[str], line_number: int, raw: str) -> RawRule | Issue:
    if len(tokens) < 2:
        return make_issue(
            "map_parse_error",
            tokens[0] if tokens else "",
            f"Line {line_number}: expected a source and a target.",
            detail=raw,
            source="map",
        )
    source, target = tokens[0], tokens[1]
    status = 301
    force = False
    extras = tokens[2:]
    if extras:
        matched = _STATUS_TOKEN.match(extras[0])
        if matched:
            status = int(matched.group(1))
            force = matched.group(2) == "!"
            extras = extras[1:]
        elif "=" not in extras[0]:
            return make_issue(
                "map_parse_error",
                source,
                f"Line {line_number}: could not read status {extras[0]!r}.",
                detail=raw,
                source="map",
            )
    if extras:
        return make_issue(
            "map_unsupported",
            source,
            f"Line {line_number}: conditions ({' '.join(extras)}) are not simulated.",
            detail=raw,
            source="map",
        )
    if not 200 <= status <= 599:
        return make_issue(
            "map_parse_error",
            source,
            f"Line {line_number}: status {status} is outside 200-599.",
            detail=raw,
            source="map",
        )
    return RawRule(
        source=source,
        target=target,
        status=status,
        line=line_number,
        force=force,
        raw=raw,
    )


def _csv_rules(text: str) -> tuple[list[RawRule], list[Issue]]:
    reader, rows = _dict_rows(text)
    issues: list[Issue] = []
    if reader is None or not rows:
        plain = _csv_without_header(text)
        if plain is not None:
            return plain
        raise MigcheckError("The redirect CSV is empty.")
    headers = [_header(name) for name in (reader.fieldnames or [])]
    source_key = _pick(headers, _SOURCE_HEADERS)
    target_key = _pick(headers, _TARGET_HEADERS)
    status_key = _pick(headers, _STATUS_HEADERS)
    if source_key is None or target_key is None or source_key == target_key:
        plain = _csv_without_header(text)
        if plain is not None:
            return plain
        raise MigcheckError("The redirect CSV needs source and target columns.")
    rules: list[RawRule] = []
    for offset, row in enumerate(rows, start=2):
        normalized = {_header(key): (value or "").strip() for key, value in row.items()}
        source = normalized.get(source_key, "")
        target = normalized.get(target_key, "")
        if not source and not target:
            continue
        status = 301
        if status_key and normalized.get(status_key):
            try:
                status = int(normalized[status_key])
            except ValueError:
                issues.append(
                    make_issue(
                        "map_parse_error",
                        source,
                        f"Row {offset}: status {normalized[status_key]!r} is not an integer.",
                        source="map",
                    )
                )
                continue
        rules.append(
            RawRule(
                source=source,
                target=target,
                status=status,
                line=offset,
                force=False,
                raw=",".join((source, target, str(status))),
            )
        )
    return rules, issues


def _csv_without_header(text: str) -> tuple[list[RawRule], list[Issue]] | None:
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return None
    rules: list[RawRule] = []
    for line_number, row in enumerate(rows, start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        if line_number == 1 and _header(row[0]) in _SOURCE_HEADERS:
            return None
        if len(row) < 2:
            continue
        status = 301
        if len(row) >= 3 and row[2].strip():
            try:
                status = int(row[2].strip())
            except ValueError:
                continue
        rules.append(
            RawRule(
                source=row[0].strip(),
                target=row[1].strip(),
                status=status,
                line=line_number,
                force=False,
                raw=",".join(row),
            )
        )
    if not rules:
        return None
    return rules, []


def _dict_rows(text: str) -> tuple[csv.DictReader[str] | None, list[dict[str, str | None]]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        return None, []
    return reader, list(reader)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _header(value: str | None) -> str:
    return (value or "").strip().lower()


def _pick(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in headers:
            return candidate
    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _strip_comment(line: str) -> str:
    quote = False
    kept: list[str] = []
    for char in line:
        if char == '"':
            quote = not quote
        if char == "#" and not quote:
            break
        kept.append(char)
    return "".join(kept)


def _tokenize(line: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quote = False
    for char in line:
        if char == '"':
            quote = not quote
            continue
        if char.isspace() and not quote:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if quote:
        raise MigcheckError("unclosed quote")
    if current:
        tokens.append("".join(current))
    return tokens


# Imported names used by nginx parsing live in patterns to keep one compiler.
# Re-export the line patterns for tests that want to read them.
def nginx_line_patterns() -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    return REWRITE_LINE, RETURN_LINE, LOCATION_BLOCK

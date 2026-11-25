"""Compile and lint Netlify-style path patterns and a subset of nginx rules.

Path patterns support literal segments, a single-segment `:placeholder`, and one
trailing `*` splat. The first matching rule wins. Cycle detection follows
literal rules only, plus a pattern that redirects a probe path to itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from migcheck.catalog import make_issue
from migcheck.models import Issue
from migcheck.urls import bare_host, is_home_path, is_http_url

_PLACEHOLDER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NGINX_GROUP = re.compile(r"\$(\d+)")
_NGINX_NAMED = re.compile(r"\$([A-Za-z]|\{)")


@dataclass(frozen=True)
class Seg:
    kind: str  # lit, param, splat
    value: str


@dataclass(frozen=True)
class PathPat:
    segs: tuple[Seg, ...]
    trail: bool


@dataclass(frozen=True)
class RawRule:
    source: str
    target: str
    status: int
    line: int
    force: bool
    raw: str


@dataclass(frozen=True)
class CompiledRule:
    source: str
    target: str
    status: int
    line: int
    force: bool
    raw: str
    kind: str  # path or nginx
    regex: re.Pattern[str] | None
    pattern: PathPat | None
    literal_path: str | None
    host: str | None
    unsupported: str | None
    wildcard: bool


@dataclass(frozen=True)
class RuleMatch:
    rule: CompiledRule
    target: str


def compile_path_rules(rules: list[RawRule]) -> tuple[list[CompiledRule], list[Issue]]:
    compiled: list[CompiledRule] = []
    issues: list[Issue] = []
    for rule in rules:
        item, issue = _compile_path_rule(rule)
        compiled.append(item)
        if issue is not None:
            issues.append(issue)
    return compiled, issues


def match_rules(url: str, rules: list[CompiledRule]) -> RuleMatch | None:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = parts.path or "/"
    for rule in rules:
        if rule.regex is None or rule.unsupported:
            continue
        if rule.host and rule.host != host:
            continue
        matched = rule.regex.search(path) if rule.kind == "nginx" else rule.regex.fullmatch(path)
        if matched is None:
            continue
        return RuleMatch(rule=rule, target=_expand(rule, matched))
    return None


def lint_rules(rules: list[CompiledRule], new_host: str) -> list[Issue]:
    issues: list[Issue] = []
    issues.extend(_duplicate_and_conflict(rules))
    issues.extend(_shadows(rules))
    issues.extend(_cycles(rules))
    issues.extend(_target_shape(rules, new_host))
    if any(rule.force for rule in rules):
        count = sum(1 for rule in rules if rule.force)
        issues.append(
            make_issue(
                "map_force_flag",
                "",
                f"{count} rule(s) use the force flag (!). migcheck still expects the redirect.",
                source="map",
            )
        )
    return issues


def concrete_target(rule: CompiledRule) -> str | None:
    """A target that does not depend on the request path."""
    if rule.unsupported or _has_placeholder(rule.target):
        return None
    if rule.wildcard and _has_placeholder(rule.target):
        return None
    return rule.target


def parse_path_pattern(path: str) -> tuple[PathPat | None, str | None]:
    if not path.startswith("/"):
        return None, "source path must start with /"
    if path == "/":
        return PathPat((), False), None
    trailing = path.endswith("/")
    body = path[1:-1] if trailing else path[1:]
    if body == "":
        return PathPat((), False), None
    segs: list[Seg] = []
    parts = body.split("/")
    for index, part in enumerate(parts):
        if part == "*":
            if index != len(parts) - 1:
                return None, "only a trailing * splat is supported"
            segs.append(Seg("splat", "splat"))
        elif part.startswith(":"):
            name = part[1:]
            if not _PLACEHOLDER.fullmatch(name):
                return None, f"bad placeholder ':{name}'"
            segs.append(Seg("param", name))
        elif "*" in part or ":" in part:
            return None, f"unsupported token '{part}'"
        else:
            segs.append(Seg("lit", part))
    trail = trailing and not (segs and segs[-1].kind == "splat")
    return PathPat(tuple(segs), trail), None


def pattern_regex(pat: PathPat) -> str:
    if not pat.segs:
        return r"^/$"
    chunks = ["^"]
    for seg in pat.segs:
        chunks.append("/")
        if seg.kind == "lit":
            chunks.append(re.escape(seg.value))
        elif seg.kind == "param":
            chunks.append("([^/]+)")
        else:
            chunks.append("(.+)")
    if pat.segs[-1].kind == "splat":
        chunks.append("$")
    elif pat.trail:
        chunks.append("/$")
    else:
        chunks.append("$")
    return "".join(chunks)


def covers(general: PathPat, specific: PathPat) -> bool:
    """True when every path matching `specific` also matches `general`."""
    return _covers(list(general.segs), general.trail, list(specific.segs), specific.trail)


def materialize(pat: PathPat) -> str:
    parts: list[str] = []
    for seg in pat.segs:
        parts.append(seg.value if seg.kind == "lit" else "probe")
    path = "/" + "/".join(parts) if parts else "/"
    if pat.trail and not path.endswith("/"):
        path += "/"
    return path


def _compile_path_rule(rule: RawRule) -> tuple[CompiledRule, Issue | None]:
    host, path, split_issue = _split_source(rule)
    if split_issue is not None or path is None:
        return _unsupported(rule, host, "unreadable source"), split_issue
    pat, reason = parse_path_pattern(path)
    if pat is None or reason is not None:
        issue = make_issue(
            "map_unsupported",
            rule.source,
            f"Line {rule.line}: {reason}",
            detail=rule.raw,
            source="map",
        )
        return _unsupported(rule, host, reason or "unsupported"), issue
    literal = path if all(seg.kind == "lit" for seg in pat.segs) else None
    wildcard = any(seg.kind != "lit" for seg in pat.segs)
    try:
        regex: re.Pattern[str] | None = re.compile(pattern_regex(pat))
    except re.error as exc:
        issue = make_issue(
            "map_parse_error",
            rule.source,
            f"Line {rule.line}: invalid pattern ({exc}).",
            detail=rule.raw,
            source="map",
        )
        return _unsupported(rule, host, str(exc)), issue
    return (
        CompiledRule(
            source=rule.source,
            target=rule.target,
            status=rule.status,
            line=rule.line,
            force=rule.force,
            raw=rule.raw,
            kind="path",
            regex=regex,
            pattern=pat,
            literal_path=literal,
            host=host,
            unsupported=None,
            wildcard=wildcard,
        ),
        None,
    )


def _unsupported(rule: RawRule, host: str | None, reason: str) -> CompiledRule:
    return CompiledRule(
        source=rule.source,
        target=rule.target,
        status=rule.status,
        line=rule.line,
        force=rule.force,
        raw=rule.raw,
        kind="path",
        regex=None,
        pattern=None,
        literal_path=None,
        host=host,
        unsupported=reason,
        wildcard=False,
    )


def _split_source(rule: RawRule) -> tuple[str | None, str | None, Issue | None]:
    source = rule.source.strip()
    if is_http_url(source):
        parts = urlsplit(source)
        if not parts.hostname:
            issue = make_issue(
                "map_parse_error",
                source,
                f"Line {rule.line}: source URL has no host.",
                detail=rule.raw,
                source="map",
            )
            return None, None, issue
        return parts.hostname.lower(), parts.path or "/", None
    if not source.startswith("/"):
        issue = make_issue(
            "map_parse_error",
            source,
            f"Line {rule.line}: source must be a path or an absolute URL.",
            detail=rule.raw,
            source="map",
        )
        return None, None, issue
    path = source.split("?", 1)[0].split(" ", 1)[0]
    if "?" in source or " " in source.strip():
        issue = make_issue(
            "map_unsupported",
            source,
            f"Line {rule.line}: query conditions are not simulated.",
            detail=rule.raw,
            source="map",
        )
        return None, None, issue
    return None, path, None


def _expand(rule: CompiledRule, matched: re.Match[str]) -> str:
    if rule.kind == "nginx":

        def replace(match: re.Match[str]) -> str:
            index = int(match.group(1))
            try:
                value = matched.group(index)
            except IndexError:
                return ""
            return "" if value is None else value

        return _NGINX_GROUP.sub(replace, rule.target)
    target = rule.target
    if rule.pattern is None:
        return target
    pairs: list[tuple[str, str]] = []
    group_index = 1
    for seg in rule.pattern.segs:
        if seg.kind == "lit":
            continue
        value = matched.group(group_index) or ""
        name = "splat" if seg.kind == "splat" else seg.value
        pairs.append((name, value))
        group_index += 1
    for name, value in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        target = target.replace(f":{name}", value)
    return target


def _covers(
    general: list[Seg], general_trail: bool, specific: list[Seg], specific_trail: bool
) -> bool:
    if general and general[0].kind == "splat":
        return bool(specific)
    if not general and not specific:
        return general_trail == specific_trail
    if not general or not specific:
        return False
    left, right = general[0], specific[0]
    if right.kind == "splat":
        return left.kind == "splat"
    if left.kind == "splat":
        return bool(specific)
    if left.kind == "lit":
        if right.kind == "lit" and left.value == right.value:
            return _covers(general[1:], general_trail, specific[1:], specific_trail)
        return False
    if left.kind == "param" and right.kind in {"lit", "param"}:
        return _covers(general[1:], general_trail, specific[1:], specific_trail)
    return False


def _duplicate_and_conflict(rules: list[CompiledRule]) -> list[Issue]:
    issues: list[Issue] = []
    grouped: dict[str, list[CompiledRule]] = {}
    for rule in rules:
        if rule.unsupported:
            continue
        grouped.setdefault(rule.source, []).append(rule)
    for source, group in grouped.items():
        if len(group) < 2:
            continue
        first = group[0]
        for later in group[1:]:
            same = later.target == first.target and later.status == first.status
            code = "map_duplicate" if same else "map_conflict"
            message = (
                f"Line {later.line} repeats line {first.line} ({source})."
                if same
                else (
                    f"Line {later.line} conflicts with line {first.line} for {source}: "
                    f"{first.target} ({first.status}) vs {later.target} ({later.status}). "
                    "The earlier rule wins."
                )
            )
            issues.append(make_issue(code, source, message, detail=later.raw, source="map"))
    return issues


def _shadows(rules: list[CompiledRule]) -> list[Issue]:
    issues: list[Issue] = []
    for index, rule in enumerate(rules):
        if rule.unsupported or rule.regex is None:
            continue
        for earlier in rules[:index]:
            if earlier.unsupported or earlier.regex is None or earlier.source == rule.source:
                continue
            if _shadowed_by(earlier, rule):
                issues.append(
                    make_issue(
                        "map_shadowed",
                        rule.source,
                        (
                            f"Line {rule.line} ({rule.source}) is shadowed by line "
                            f"{earlier.line} ({earlier.source}) and will never run."
                        ),
                        detail=rule.raw,
                        source="map",
                    )
                )
                break
    return issues


def _shadowed_by(earlier: CompiledRule, later: CompiledRule) -> bool:
    if later.literal_path and _rule_matches_path(earlier, later.literal_path):
        return True
    return bool(
        earlier.pattern is not None
        and later.pattern is not None
        and earlier.source != later.source
        and covers(earlier.pattern, later.pattern)
    )


def _rule_matches_path(rule: CompiledRule, path: str) -> bool:
    if rule.regex is None:
        return False
    if rule.kind == "nginx":
        return rule.regex.search(path) is not None
    return rule.regex.fullmatch(path) is not None


def _cycles(rules: list[CompiledRule]) -> list[Issue]:
    issues: list[Issue] = []
    edges: dict[str, str] = {}
    for rule in rules:
        if rule.unsupported or not rule.literal_path or _has_placeholder(rule.target):
            continue
        target_path = _target_path(rule.target)
        if target_path is None:
            continue
        edges.setdefault(rule.literal_path, target_path)
    for cycle in _find_cycles(edges):
        pretty = " -> ".join([*cycle, cycle[0]])
        issues.append(
            make_issue(
                "map_cycle",
                cycle[0],
                f"Redirect rules cycle: {pretty}.",
                source="map",
            )
        )
    for rule in rules:
        if rule.pattern is None or not rule.wildcard or rule.regex is None:
            continue
        probe = materialize(rule.pattern)
        matched = rule.regex.fullmatch(probe)
        if matched is None:
            continue
        target_path = _target_path(_expand(rule, matched))
        if target_path == probe:
            issues.append(
                make_issue(
                    "map_cycle",
                    rule.source,
                    f"Line {rule.line} ({rule.source}) redirects a probe path back to itself.",
                    detail=rule.raw,
                    source="map",
                )
            )
    return issues


def _find_cycles(edges: dict[str, str]) -> list[list[str]]:
    cycles: list[list[str]] = []
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        nxt = edges.get(node)
        if nxt is not None:
            state = color.get(nxt, 0)
            if state == 0:
                visit(nxt)
            elif state == 1 and nxt in stack:
                cycles.append(stack[stack.index(nxt) :])
        stack.pop()
        color[node] = 2

    for node in sorted(edges):
        if color.get(node, 0) == 0:
            visit(node)
    return cycles


def _target_shape(rules: list[CompiledRule], new_host: str) -> list[Issue]:
    issues: list[Issue] = []
    new_bare = bare_host(urlsplit(new_host).netloc)
    for rule in rules:
        if rule.unsupported:
            continue
        if rule.status in {302, 303, 307}:
            issues.append(
                make_issue(
                    "map_temporary",
                    rule.source,
                    f"Line {rule.line} uses status {rule.status}. Permanent moves should be 301 or 308.",
                    detail=rule.raw,
                    source="map",
                )
            )
        if rule.status == 200:
            issues.append(
                make_issue(
                    "map_rewrite",
                    rule.source,
                    f"Line {rule.line} is a 200 rewrite, not a client redirect.",
                    detail=rule.raw,
                    source="map",
                )
            )
        target_path = _target_path(rule.target) if not _has_placeholder(rule.target) else None
        source_path = rule.literal_path
        if (
            target_path
            and is_home_path(target_path)
            and not (source_path and is_home_path(source_path))
        ):
            issues.append(
                make_issue(
                    "map_targets_homepage",
                    rule.source,
                    f"Line {rule.line} sends {rule.source} to the homepage.",
                    detail=rule.raw,
                    source="map",
                )
            )
        if is_http_url(rule.target):
            target_host = bare_host(urlsplit(rule.target).netloc)
            if target_host and target_host != new_bare:
                issues.append(
                    make_issue(
                        "map_offsite_target",
                        rule.source,
                        f"Line {rule.line} points at {rule.target}, which is not the new host.",
                        detail=rule.raw,
                        source="map",
                    )
                )
    return issues


def _target_path(target: str) -> str | None:
    if _has_placeholder(target):
        return None
    if is_http_url(target):
        return urlsplit(target).path or "/"
    if target.startswith("/"):
        return target.split("?", 1)[0]
    return None


def _has_placeholder(target: str) -> bool:
    return bool(re.search(r":[A-Za-z_]", target) or "$" in target)


def nginx_regex_unsupported(target: str) -> str | None:
    if _NGINX_NAMED.search(target):
        return "only numbered $1 captures are supported in nginx targets"
    return None


_REWRITE_LINE = re.compile(
    r"rewrite\s+(?P<pat>\S+)\s+(?P<target>\S+)\s+(?P<flag>permanent|redirect|last|break)\s*;",
    re.IGNORECASE,
)
_RETURN_LINE = re.compile(
    r"return\s+(?P<status>\d{3})\s+(?P<target>\S+)\s*;",
    re.IGNORECASE,
)
_LOCATION_BLOCK = re.compile(
    r"location\s+(?P<mod>=|\^~|~|~\*)?\s*(?P<pat>\"[^\"]+\"|\S+)\s*\{(?P<body>[^{}]*)\}",
    re.IGNORECASE | re.DOTALL,
)
_FLAG_STATUS = {"permanent": 301, "redirect": 302, "last": 200, "break": 200}


def parse_nginx(text: str) -> tuple[list[CompiledRule], list[Issue]]:
    """Parse rewrite lines and location blocks that return a redirect.

    Nested braces are not supported. `last` and `break` are recorded as
    internal rewrites (status 200), not as client redirects.
    """
    issues: list[Issue] = []
    found: list[tuple[int, CompiledRule | Issue]] = []
    occupied = [False] * (text.count("\n") + 1)

    for match in _LOCATION_BLOCK.finditer(text):
        start_line = text[: match.start()].count("\n") + 1
        end_line = text[: match.end()].count("\n") + 1
        for number in range(start_line - 1, end_line):
            if 0 <= number < len(occupied):
                occupied[number] = True
        modifier = (match.group("mod") or "").lower()
        pattern = match.group("pat").strip().strip('"')
        body = match.group("body")
        compiled = _nginx_location(modifier, pattern, body, start_line)
        found.append((start_line, compiled[0] if not isinstance(compiled, Issue) else compiled))
        if isinstance(compiled, tuple):
            rule, issue = compiled
            found[-1] = (start_line, rule)
            if issue is not None:
                issues.append(issue)
        else:
            issues.append(compiled)

    for line_number, raw in enumerate(text.splitlines(), start=1):
        if occupied[line_number - 1]:
            continue
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        rewrite = _REWRITE_LINE.search(line)
        if rewrite:
            rule, issue = _nginx_rewrite(
                rewrite.group("pat"),
                rewrite.group("target"),
                rewrite.group("flag").lower(),
                line_number,
                line,
            )
            found.append((line_number, rule))
            if issue is not None:
                issues.append(issue)
            continue
        if _RETURN_LINE.search(line):
            issues.append(
                make_issue(
                    "map_parse_error",
                    "",
                    f"Line {line_number}: return outside a location block is not supported.",
                    detail=line,
                    source="map",
                )
            )
    found.sort(key=lambda item: item[0])
    rules = [item for _, item in found if isinstance(item, CompiledRule)]
    return rules, issues


def _nginx_location(
    modifier: str, pattern: str, body: str, line: int
) -> tuple[CompiledRule, Issue | None] | Issue:
    returned = _RETURN_LINE.search(body)
    rewrite = _REWRITE_LINE.search(body)
    if returned:
        status = int(returned.group("status"))
        target = returned.group("target")
        regex, literal, reason = _nginx_location_regex(modifier, pattern)
        raw = f"location {modifier} {pattern} {{ return {status} {target}; }}"
        if reason or regex is None:
            issue = make_issue(
                "map_parse_error",
                pattern,
                f"Line {line}: {reason or 'bad location pattern'}.",
                detail=raw,
                source="map",
            )
            return issue
        named = nginx_regex_unsupported(target)
        named_issue: Issue | None = None
        if named:
            named_issue = make_issue(
                "map_unsupported", pattern, f"Line {line}: {named}.", source="map"
            )
        return (
            CompiledRule(
                source=pattern,
                target=target,
                status=status,
                line=line,
                force=False,
                raw=raw.strip(),
                kind="nginx",
                regex=None if named else regex,
                pattern=None,
                literal_path=literal,
                host=None,
                unsupported=named,
                wildcard=literal is None,
            ),
            named_issue,
        )
    if rewrite:
        return _nginx_rewrite(
            rewrite.group("pat"),
            rewrite.group("target"),
            rewrite.group("flag").lower(),
            line,
            rewrite.group(0),
        )
    return make_issue(
        "map_parse_error",
        pattern,
        f"Line {line}: location block has no return or rewrite migcheck understands.",
        source="map",
    )


def _nginx_rewrite(
    pattern: str, target: str, flag: str, line: int, raw: str
) -> tuple[CompiledRule, Issue | None]:
    status = _FLAG_STATUS.get(flag, 301)
    named = nginx_regex_unsupported(target)
    issue: Issue | None = None
    regex: re.Pattern[str] | None
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        regex = None
        issue = make_issue(
            "map_parse_error",
            pattern,
            f"Line {line}: invalid nginx regular expression ({exc}).",
            detail=raw,
            source="map",
        )
    if named and issue is None:
        issue = make_issue(
            "map_unsupported",
            pattern,
            f"Line {line}: {named}.",
            detail=raw,
            source="map",
        )
        regex = None
    literal = _literal_from_anchored(pattern)
    return (
        CompiledRule(
            source=pattern,
            target=target,
            status=status,
            line=line,
            force=False,
            raw=raw,
            kind="nginx",
            regex=regex,
            pattern=None,
            literal_path=literal,
            host=None,
            unsupported=named or (None if regex is not None else "invalid regex"),
            wildcard=literal is None,
        ),
        issue,
    )


def _nginx_location_regex(
    modifier: str, pattern: str
) -> tuple[re.Pattern[str] | None, str | None, str | None]:
    try:
        if modifier in {"~", "~*"}:
            flags = re.IGNORECASE if modifier == "~*" else 0
            return re.compile(pattern, flags), None, None
        if modifier == "=":
            return re.compile("^" + re.escape(pattern) + "$"), pattern, None
        # Prefix match, including `^~`. Nginx also matches the prefix of `/foo` against `/foobar`.
        return re.compile("^" + re.escape(pattern)), None, None
    except re.error as exc:
        return None, None, str(exc)


def _literal_from_anchored(pattern: str) -> str | None:
    match = re.fullmatch(r"\^(/[^$()*+?\\]*)\$", pattern)
    if match is None:
        return None
    return match.group(1)


# Names kept for a small loader helper. Prefer parse_nginx.
REWRITE_LINE = _REWRITE_LINE
RETURN_LINE = _RETURN_LINE
LOCATION_BLOCK = _LOCATION_BLOCK


def compile_nginx_rewrite(
    pattern: str, target: str, flag: str, line: int, raw: str
) -> tuple[CompiledRule, Issue | None]:
    return _nginx_rewrite(pattern, target, flag, line, raw)


def compile_nginx_block(
    modifier: str, pattern: str, body: str, line: int
) -> tuple[CompiledRule, Issue | None] | Issue:
    return _nginx_location(modifier, pattern, body, line)

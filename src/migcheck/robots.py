"""A practical subset of robots.txt as Googlebot would apply it.

The group used is the longest user-agent token that matches "googlebot".
When none does, the `*` group is used. Within the group the longest matching
path wins, and Allow wins over Disallow when the lengths are equal.
`*` in a path matches any string. A trailing `$` anchors the end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_AGENT = "googlebot"


@dataclass(frozen=True)
class RobotsRule:
    allow: bool
    path: str
    regex: re.Pattern[str]

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None


@dataclass(frozen=True)
class RobotsFile:
    rules: tuple[RobotsRule, ...]
    sitemaps: tuple[str, ...]
    group: str
    blocks_all: bool


def parse_robots(text: str) -> RobotsFile:
    groups = _groups(text)
    selected_name, selected = _select(groups)
    compiled_list: list[RobotsRule] = []
    for item in selected:
        rule = _compile(item)
        if rule is not None:
            compiled_list.append(rule)
    compiled = tuple(compiled_list)
    sitemaps = tuple(_sitemap_lines(text))
    blocks_all = _blocks_everything(compiled)
    return RobotsFile(
        rules=compiled,
        sitemaps=sitemaps,
        group=selected_name,
        blocks_all=blocks_all,
    )


def blocking_rule(path: str, robots: RobotsFile) -> RobotsRule | None:
    matches = [rule for rule in robots.rules if rule.matches(path)]
    if not matches:
        return None
    matches.sort(key=lambda rule: (len(rule.path), 1 if rule.allow else 0))
    winner = matches[-1]
    if winner.allow:
        return None
    return winner


def _groups(text: str) -> list[tuple[str, list[tuple[bool, str]]]]:
    groups: list[tuple[str, list[tuple[bool, str]]]] = []
    current_agents: list[str] = []
    current_rules: list[tuple[bool, str]] = []
    started = False

    def flush() -> None:
        nonlocal current_agents, current_rules, started
        if not started:
            return
        agents = current_agents or ["*"]
        for agent in agents:
            groups.append((agent.lower(), list(current_rules)))
        current_agents = []
        current_rules = []
        started = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current_rules:
                flush()
            current_agents.append(value or "*")
            started = True
        elif key in {"allow", "disallow"} and started:
            current_rules.append((key == "allow", value))
        elif key == "sitemap":
            continue
    flush()
    return groups


def _select(groups: list[tuple[str, list[tuple[bool, str]]]]) -> tuple[str, list[tuple[bool, str]]]:
    best_name = "*"
    best_rules: list[tuple[bool, str]] = []
    best_score = -1
    for name, rules in groups:
        if name == "*":
            score = 0
        elif name in _AGENT:
            score = len(name)
        else:
            continue
        if score > best_score:
            best_name = name
            best_rules = rules
            best_score = score
    return best_name, best_rules


def _compile(item: tuple[bool, str]) -> RobotsRule | None:
    allow, path = item
    if path == "" and not allow:
        # An empty Disallow means "allow everything" and adds no block rule.
        return None
    if path == "" and allow:
        return None
    anchored = path.endswith("$")
    body = path[:-1] if anchored else path
    pieces = [re.escape(part) for part in body.split("*")]
    expression = ".*".join(pieces)
    if not expression.startswith("^"):
        expression = "^" + expression
    if anchored:
        expression += "$"
    return RobotsRule(allow=allow, path=path, regex=re.compile(expression))


def _sitemap_lines(text: str) -> list[str]:
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.lower().startswith("sitemap:"):
            value = line.split(":", 1)[1].strip()
            if value:
                found.append(value)
    return found


def _blocks_everything(rules: tuple[RobotsRule, ...]) -> bool:
    file = RobotsFile(rules=rules, sitemaps=(), group="*", blocks_all=False)
    winner = blocking_rule("/", file)
    if winner is None:
        return False
    return winner.path.rstrip("$") in {"/", "/*"}

"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from migcheck import __version__
from migcheck.errors import MigcheckError
from migcheck.models import RunConfig
from migcheck.report import render_terminal, write_outputs
from migcheck.runner import execute, exit_code
from migcheck.urls import display_path, parse_origin

_USER_AGENT = f"migcheck/{__version__} (+https://github.com/photosnob/site-migration-checker)"


def main(argv: list[str] | None = None) -> int:
    try:
        config = build_config(argv)
        report = execute(config)
    except MigcheckError as exc:
        print(f"migcheck: {exc}", file=sys.stderr)
        return 2
    redirects_out = config.redirects_out if config.write_redirects else None
    if redirects_out is None and config.write_redirects:
        redirects_out = config.output_dir / "_redirects"
    sys.stdout.write(
        render_terminal(
            report,
            output_dir=config.output_dir,
            color=config.color,
            redirects_out=redirects_out,
        )
    )
    write_outputs(report, config.output_dir, redirects_out)
    return exit_code(report)


def build_config(argv: list[str] | None = None) -> RunConfig:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("missing command")
    fixture_dir: Path | None = None
    fixture: dict[str, object] = {}
    if args.fixture is not None:
        fixture_dir, fixture = _read_fixture(Path(args.fixture))
    old_sitemaps = list(args.old_sitemap)
    old_csvs = list(args.old_csv)
    old_url_files = list(args.old_urls)
    if fixture_dir is not None:
        if not old_sitemaps and fixture.get("old_sitemap"):
            old_sitemaps = [str(fixture_dir / str(fixture["old_sitemap"]))]
        if not old_csvs and fixture.get("old_csv"):
            old_csvs = [str(fixture_dir / str(fixture["old_csv"]))]
        if not old_url_files and fixture.get("old_urls"):
            old_url_files = [str(fixture_dir / str(fixture["old_urls"]))]
    redirects = args.redirects
    if redirects is None and fixture_dir is not None and fixture.get("redirects"):
        redirects = str(fixture_dir / str(fixture["redirects"]))
    new_host_raw = args.new_host or (
        str(fixture.get("new_host")) if fixture.get("new_host") else None
    )
    if not new_host_raw:
        raise MigcheckError("Pass --new-host, for example https://staging.example.com.")
    new_host = parse_origin(new_host_raw)
    mode = args.mode or str(fixture.get("mode") or "staging")
    if mode not in {"staging", "live"}:
        raise MigcheckError("--mode must be staging or live.")
    if not old_sitemaps and not old_csvs and not old_url_files:
        raise MigcheckError("Pass at least one of --old-sitemap, --old-csv, or --old-urls.")
    _check_ranges(args)
    sample = bool(fixture.get("sample", False)) if fixture_dir is not None else False
    disclaimer = str(fixture.get("disclaimer", "")) if fixture_dir is not None else ""
    inputs = _inputs(
        fixture_dir=fixture_dir,
        old_sitemaps=old_sitemaps,
        old_csvs=old_csvs,
        old_url_files=old_url_files,
        new_host=new_host,
        mode=mode,
        redirects=redirects,
    )
    output_dir = Path(args.output)
    redirects_out = Path(args.redirects_out) if args.redirects_out else output_dir / "_redirects"
    return RunConfig(
        old_sitemaps=old_sitemaps,
        old_csvs=old_csvs,
        old_url_files=old_url_files,
        new_host=new_host,
        mode=mode,
        redirects=redirects,
        redirects_format=args.redirects_format,
        new_sitemap=args.new_sitemap,
        robots=args.robots,
        output_dir=output_dir,
        suggest=not args.no_suggest,
        write_redirects=not args.no_write_redirects,
        redirects_out=redirects_out,
        rps=args.rps,
        concurrency=args.concurrency,
        timeout=args.timeout,
        max_hops=args.max_hops,
        max_body_bytes=args.max_body_bytes,
        max_urls=args.max_urls,
        max_word_drop=args.max_word_drop,
        min_words=args.min_words,
        suggest_threshold=args.suggest_threshold,
        suggest_pool=args.suggest_pool,
        fail_on=args.fail_on,
        compare_content=not args.no_compare_content,
        user_agent=args.user_agent or _USER_AGENT,
        fixture_dir=fixture_dir,
        sample=sample,
        disclaimer=disclaimer,
        inputs=inputs,
        color=_want_color(args.no_color),
        verbose=args.verbose,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migcheck",
        description="Check redirects, indexability, and content parity before a site migration.",
    )
    parser.add_argument("--version", action="version", version=f"migcheck {__version__}")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Check an old URL list against a new host.")
    run.add_argument("--fixture", help="Directory or fixture.json of saved responses. No network.")
    run.add_argument(
        "--old-sitemap", action="append", default=[], help="Sitemap file or URL. Repeatable."
    )
    run.add_argument(
        "--old-csv", action="append", default=[], help="Crawl or Search Console CSV. Repeatable."
    )
    run.add_argument(
        "--old-urls", action="append", default=[], help="Text file with one URL per line."
    )
    run.add_argument("--new-host", help="Origin to probe, such as https://staging.example.com.")
    run.add_argument(
        "--mode",
        choices=["staging", "live"],
        help="staging rewrites the host. live requests old URLs.",
    )
    run.add_argument(
        "--redirects", help="Redirect map: CSV, Netlify/Cloudflare _redirects, or nginx."
    )
    run.add_argument(
        "--redirects-format",
        default="auto",
        choices=["auto", "csv", "netlify", "nginx"],
    )
    run.add_argument(
        "--new-sitemap", help="New sitemap file or URL. Defaults to {new-host}/sitemap.xml."
    )
    run.add_argument("--robots", help="robots.txt file or URL. Defaults to {new-host}/robots.txt.")
    run.add_argument("--output", default="migcheck-report", help="Directory for the report files.")
    run.add_argument("--no-suggest", action="store_true", help="Skip similarity suggestions.")
    run.add_argument(
        "--no-write-redirects",
        action="store_true",
        help="Do not write a suggested _redirects file.",
    )
    run.add_argument(
        "--redirects-out", help="Where to write suggested redirects. Default: {output}/_redirects."
    )
    run.add_argument("--rps", type=float, default=5, help="Maximum new HTTP requests per second.")
    run.add_argument("--concurrency", type=int, default=8)
    run.add_argument("--timeout", type=float, default=20)
    run.add_argument("--max-hops", type=int, default=10)
    run.add_argument("--max-body-bytes", type=int, default=2_000_000)
    run.add_argument("--max-urls", type=int, default=20_000)
    run.add_argument(
        "--max-word-drop",
        type=float,
        default=0.30,
        help="Flag visible-text drops at or above this fraction.",
    )
    run.add_argument(
        "--min-words",
        type=int,
        default=80,
        help="Ignore word-count drops when the old page is shorter.",
    )
    run.add_argument("--suggest-threshold", type=float, default=0.80)
    run.add_argument("--suggest-pool", type=int, default=500)
    run.add_argument(
        "--fail-on",
        default="high",
        choices=["critical", "high", "medium", "low", "info"],
        help="Exit 1 when an issue at this severity or worse is present.",
    )
    run.add_argument("--no-compare-content", action="store_true")
    run.add_argument("--user-agent", default=None)
    run.add_argument("--no-color", action="store_true")
    run.add_argument("--verbose", action="store_true")
    return parser


def _read_fixture(path: Path) -> tuple[Path, dict[str, object]]:
    directory = path if path.is_dir() else path.parent
    payload_path = directory / "fixture.json"
    if not payload_path.is_file():
        raise MigcheckError(f"No fixture.json in {directory}.")
    data = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise MigcheckError("fixture.json must be an object.")
    return directory, data


def _check_ranges(args: argparse.Namespace) -> None:
    if args.rps <= 0:
        raise MigcheckError("--rps must be positive.")
    if args.concurrency < 1:
        raise MigcheckError("--concurrency must be at least 1.")
    if args.timeout <= 0:
        raise MigcheckError("--timeout must be positive.")
    if args.max_hops < 1:
        raise MigcheckError("--max-hops must be at least 1.")
    if args.max_body_bytes < 1 or args.max_urls < 1 or args.min_words < 0:
        raise MigcheckError("Size limits must be positive.")
    if not 0 <= args.max_word_drop <= 1:
        raise MigcheckError("--max-word-drop must be between 0 and 1.")
    if not 0 <= args.suggest_threshold <= 1:
        raise MigcheckError("--suggest-threshold must be between 0 and 1.")


def _inputs(
    *,
    fixture_dir: Path | None,
    old_sitemaps: list[str],
    old_csvs: list[str],
    old_url_files: list[str],
    new_host: str,
    mode: str,
    redirects: str | None,
) -> list[str]:
    items: list[str] = []
    if fixture_dir is not None:
        items.append(f"fixture: {display_path(str(fixture_dir))}")
    items.extend(f"old sitemap: {display_path(item)}" for item in old_sitemaps)
    items.extend(f"old csv: {display_path(item)}" for item in old_csvs)
    items.extend(f"old urls: {display_path(item)}" for item in old_url_files)
    items.append(f"new host: {new_host}")
    items.append(f"mode: {mode}")
    if redirects:
        items.append(f"redirects: {display_path(redirects)}")
    return items


def _want_color(no_color: bool) -> bool:
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()

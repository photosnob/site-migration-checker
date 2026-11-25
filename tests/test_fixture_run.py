import importlib.util
import json
import re
import socket
from pathlib import Path

from migcheck.cli import build_config, main
from migcheck.runner import execute

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "examples" / "wp-to-static"

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def test_fixture_run_finds_the_intentional_faults(monkeypatch) -> None:
    def bomb(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixture mode tried to use the network")

    monkeypatch.setattr(socket, "create_connection", bomb)
    monkeypatch.chdir(REPO)
    config = build_config(
        ["run", "--fixture", "examples/wp-to-static", "--output", "migcheck-report", "--no-color"]
    )
    report = execute(config)
    codes = {issue.code for issue in report.issues}
    assert "fetch_error" not in codes
    assert "robots_unreadable" not in codes
    assert "sitemap_missing_file" not in codes
    assert report.sample
    assert report.readiness == "BLOCKED"
    for required in (
        "redirect_homepage",
        "redirect_loop",
        "redirect_chain",
        "temporary_redirect",
        "http_404",
        "http_5xx",
        "protocol_downgrade",
        "trailing_slash_mismatch",
        "not_redirected",
        "noindex",
        "word_count_drop",
        "canonical_mismatch",
        "meta_refresh",
        "map_conflict",
        "map_cycle",
        "map_shadowed",
        "map_duplicate",
        "map_target_404",
        "robots_disallow",
        "sitemap_missing",
        "sitemap_lists_noindex",
    ):
        assert required in codes, required
    suggestions = {item.old_url: item.candidate_url for item in report.suggestions}
    assert suggestions["https://old.journal.test/pricing/"].endswith("/plans/")
    assert suggestions["https://old.journal.test/archive/field-notes/"].endswith(
        "/journal/field-notes/"
    )
    assert not any(
        issue.code == "robots_disallow" and "/wp-admin/" in issue.url for issue in report.issues
    )
    shipping = next(page for page in report.pages if page.old_url.endswith("/shipping-static/"))
    home = next(page for page in report.pages if page.old_url == "https://old.journal.test/")
    assert shipping.result == "pass"
    assert home.result == "pass"
    assert shipping.old_signals is not None and shipping.new_signals is not None
    assert shipping.old_signals.word_count == shipping.new_signals.word_count
    thin = next(page for page in report.pages if page.old_url.endswith("/thin-page/"))
    assert thin.old_signals is not None and thin.new_signals is not None
    assert thin.new_signals.word_count < thin.old_signals.word_count * 0.5


def test_cli_fixture_exits_nonzero(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(REPO)
    code = main(
        [
            "run",
            "--fixture",
            str(FIXTURE),
            "--output",
            str(tmp_path / "out"),
            "--no-color",
        ]
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "Readiness: BLOCKED" in captured.out
    assert (tmp_path / "out" / "index.html").is_file()
    assert (tmp_path / "out" / "issues.csv").is_file()
    assert (tmp_path / "out" / "pages.csv").is_file()
    assert (tmp_path / "out" / "report.json").is_file()
    assert (tmp_path / "out" / "_redirects").is_file()
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "SAMPLE" in html
    assert "<script>alert" not in html
    redirects = (tmp_path / "out" / "_redirects").read_text(encoding="utf-8")
    assert "/pricing/ /plans/ 301" in redirects
    assert "/archive/field-notes/ /journal/field-notes/ 301" in redirects
    active = [line for line in redirects.splitlines() if line and not line.startswith("#")]
    assert all(line.split()[1] != "/" for line in active)


def test_committed_sample_matches_a_fresh_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(REPO)
    output = tmp_path / "out"
    code = main(
        ["run", "--fixture", "examples/wp-to-static", "--output", str(output), "--no-color"]
    )
    assert code == 1
    fresh = json.loads((output / "report.json").read_text(encoding="utf-8"))
    committed = json.loads((FIXTURE / "sample-report" / "report.json").read_text(encoding="utf-8"))
    fresh.pop("generated_at")
    committed.pop("generated_at")
    assert fresh == committed
    for name in ("index.html", "issues.csv", "pages.csv", "terminal.txt", "_redirects"):
        fresh_text = _scrub((output / name).read_text(encoding="utf-8"), str(output))
        committed_text = _scrub(
            (FIXTURE / "sample-report" / name).read_text(encoding="utf-8"),
            "examples/wp-to-static/sample-report",
        )
        assert fresh_text == committed_text, name


def test_fixture_inputs_match_generator(tmp_path) -> None:
    spec = importlib.util.spec_from_file_location(
        "generate_fixture", FIXTURE / "generate_fixture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.generate(tmp_path)
    for path in sorted(tmp_path.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(tmp_path)
        committed = FIXTURE / relative
        assert committed.is_file(), relative
        assert path.read_bytes() == committed.read_bytes(), relative


def _scrub(text: str, output: str) -> str:
    return _TIMESTAMP.sub("TIMESTAMP", text).replace(output, "OUTPUT")


def test_missing_input_exits_2(capsys) -> None:
    code = main(["run", "--new-host", "https://new.example"])
    assert code == 2
    assert "migcheck:" in capsys.readouterr().err


def test_help_exits_zero() -> None:
    import pytest

    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0

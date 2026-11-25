from migcheck.classify import classify_chain
from migcheck.models import Hop

OLD = "https://old.example/blog/old-post"
EXPECTED = "https://new.example/blog/post"
PROBE = "https://new.example/blog/old-post"


def _hop(url: str, status: int, location: str | None = None) -> Hop:
    return Hop(url=url, status=status, location=location)


def _codes(issues: list[object]) -> set[str]:
    return {issue.code for issue in issues}  # type: ignore[attr-defined]


def test_single_301_passes() -> None:
    hops = [
        _hop(PROBE, 301, EXPECTED),
        _hop(EXPECTED, 200),
    ]
    assert (
        _codes(classify_chain(old_url=OLD, expected_url=EXPECTED, hops=hops, max_hops=10)) == set()
    )


def test_404_chain_and_temporary() -> None:
    hops = [
        _hop(PROBE, 302, "https://new.example/tmp"),
        _hop("https://new.example/tmp", 301, "https://new.example/final"),
        _hop("https://new.example/final", 404),
    ]
    codes = _codes(classify_chain(old_url=OLD, expected_url=EXPECTED, hops=hops, max_hops=10))
    assert {"http_404", "temporary_redirect", "redirect_chain"} <= codes


def test_homepage_soft_404() -> None:
    hops = [_hop(PROBE, 301, "https://new.example/"), _hop("https://new.example/", 200)]
    codes = _codes(classify_chain(old_url=OLD, expected_url=EXPECTED, hops=hops, max_hops=10))
    assert codes == {"redirect_homepage"}


def test_loop() -> None:
    hops = [_hop(PROBE, 301, PROBE)]
    codes = _codes(classify_chain(old_url=OLD, expected_url=EXPECTED, hops=hops, max_hops=10))
    assert "redirect_loop" in codes
    assert "redirect_chain" not in codes


def test_protocol_downgrade_and_slash() -> None:
    hops = [
        _hop("https://new.example/team/", 301, "http://new.example/team"),
        _hop("http://new.example/team", 200),
    ]
    codes = _codes(
        classify_chain(
            old_url="https://old.example/team/",
            expected_url="https://new.example/team/",
            hops=hops,
            max_hops=10,
        )
    )
    assert codes == {"protocol_downgrade", "trailing_slash_mismatch"}


def test_not_redirected() -> None:
    hops = [_hop("https://new.example/category/seo/", 200)]
    codes = _codes(
        classify_chain(
            old_url="https://old.example/category/seo/",
            expected_url="https://new.example/topics/seo/",
            hops=hops,
            max_hops=10,
        )
    )
    assert codes == {"not_redirected"}


def test_www_and_query() -> None:
    hops = [_hop("https://www.new.example/a?x=2", 200)]
    codes = _codes(
        classify_chain(
            old_url="https://old.example/a?x=1",
            expected_url="https://new.example/a?x=1",
            hops=hops,
            max_hops=10,
        )
    )
    assert "host_mismatch" in codes or "query_mismatch" in codes

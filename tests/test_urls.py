import pytest

from migcheck.errors import MigcheckError
from migcheck.urls import (
    absolutize,
    equivalent_page,
    normalize_url,
    on_host,
    parse_origin,
    sitemap_key,
    strip_userinfo,
)


def test_parse_origin_rejects_a_path() -> None:
    with pytest.raises(MigcheckError):
        parse_origin("https://staging.example.com/blog")


def test_normalize_drops_fragment_and_default_port() -> None:
    assert normalize_url("HTTPS://Example.COM:443/a/b/?q=1#x") == "https://example.com/a/b/?q=1"


def test_on_host_keeps_path() -> None:
    assert (
        on_host("https://old.example/a/b/?q=1", "https://new.example")
        == "https://new.example/a/b/?q=1"
    )


def test_equivalent_ignores_scheme_and_one_slash() -> None:
    assert equivalent_page("https://new.example/team/", "http://www.new.example/team")
    assert not equivalent_page("https://new.example/team", "https://new.example/team/page")


def test_sitemap_key_ignores_slash_and_scheme() -> None:
    assert sitemap_key("https://new.example/a/") == sitemap_key("http://new.example/a")


def test_strip_userinfo() -> None:
    cleaned, stripped = strip_userinfo("https://user:secret@example.com/a")
    assert stripped
    assert cleaned == "https://example.com/a"
    assert "secret" not in cleaned


def test_absolutize_relative_target() -> None:
    assert absolutize("/about", "https://new.example") == "https://new.example/about"

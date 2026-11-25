import pytest

from migcheck.errors import MigcheckError
from migcheck.loaders import load_local_records, parse_sitemap_xml


def test_sitemap_namespace_and_index() -> None:
    urlset = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://old.example/a/</loc></url>
    </urlset>
    """
    pages, children = parse_sitemap_xml(urlset, origin="mem")
    assert pages == ["https://old.example/a/"]
    assert children == []
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://old.example/posts.xml</loc></sitemap>
    </sitemapindex>"""
    pages, children = parse_sitemap_xml(index, origin="mem")
    assert pages == []
    assert children == ["https://old.example/posts.xml"]


def test_malformed_sitemap() -> None:
    with pytest.raises(MigcheckError):
        parse_sitemap_xml("<urlset>", origin="mem")


def test_search_console_and_crawl_csv(tmp_path) -> None:
    gsc = tmp_path / "pages.csv"
    gsc.write_text(
        "Top pages,Clicks,Impressions,CTR,Position\nhttps://old.example/a,1,200,10%,4.2\nhttps://user:pw@old.example/b,3,10,1%,1\n",
        encoding="utf-8",
    )
    crawl = tmp_path / "crawl.csv"
    crawl.write_text(
        "Address,Title 1,Meta Description 1,H1-1,Word Count\nhttps://old.example/a,Hello,Desc,Heading,120\n",
        encoding="utf-8",
    )
    gsc_records, gsc_issues = load_local_records([str(gsc)], kind="csv")
    crawl_records, _ = load_local_records([str(crawl)], kind="csv")
    from migcheck.loaders import merge_records

    merged = merge_records([gsc_records, crawl_records])
    assert any(issue.code == "url_credentials" for issue in gsc_issues)
    by_url = {record.url: record for record in merged}
    assert by_url["https://old.example/a"].clicks == 1
    assert by_url["https://old.example/a"].baseline_title == "Hello"
    assert by_url["https://old.example/a"].baseline_words == 120
    assert "pw" not in by_url["https://old.example/b"].url


def test_plain_url_file_skips_comments(tmp_path) -> None:
    path = tmp_path / "urls.txt"
    path.write_text("# comment\n\nhttps://old.example/a\nnot a url\n", encoding="utf-8")
    records, issues = load_local_records([str(path)], kind="urls")
    assert [record.url for record in records] == ["https://old.example/a"]
    assert any(issue.code == "invalid_url" for issue in issues)

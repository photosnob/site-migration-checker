from migcheck.loaders import load_redirects
from migcheck.patterns import lint_rules, match_rules, parse_nginx


def test_netlify_splat_and_shadow(tmp_path) -> None:
    path = tmp_path / "_redirects"
    path.write_text(
        "\n".join(
            [
                "/blog/* /notes/:splat 301",
                "/blog/2016/03/03/specific-post/ /blog/specific-post/ 301",
                "/about/ /about 301",
                "/about/ /about 301",
                "/services/audits/ /services/audits/ 301",
                "/services/audits/ /services/seo-audits/ 301",
                "/legacy/a /legacy/b 301",
                "/legacy/b /legacy/a 301",
                "/promo /offers 302",
                "/old /gone 200",
                "/home-bound / 301",
                "/loop /loop 301",
            ]
        ),
        encoding="utf-8",
    )
    rules, parse_issues = load_redirects(str(path), "netlify")
    assert parse_issues == []
    match = match_rules("https://old.example/blog/2016/03/03/specific-post/", rules)
    assert match is not None
    assert match.target == "/notes/2016/03/03/specific-post/"
    issues = lint_rules(rules, "https://new.example")
    codes = {issue.code for issue in issues}
    assert {
        "map_shadowed",
        "map_duplicate",
        "map_conflict",
        "map_cycle",
        "map_temporary",
        "map_rewrite",
        "map_targets_homepage",
    } <= codes


def test_placeholder_rule() -> None:
    text = "/news/:year/:slug /archive/:year/:slug 301\n"
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "_redirects"
        path.write_text(text, encoding="utf-8")
        rules, issues = load_redirects(str(path), "netlify")
    assert issues == []
    match = match_rules("https://old.example/news/2020/hello", rules)
    assert match is not None
    assert match.target == "/archive/2020/hello"


def test_nginx_rewrite_and_location() -> None:
    text = """
    server {
        listen 80;
        rewrite ^/old$ /new permanent;
        rewrite ^/blog/(.*)$ /news/$1 permanent;
        location = /exact { return 301 /dest; }
        location /prefix { return 302 /pref; }
    }
    """
    rules, issues = parse_nginx(text)
    assert issues == []
    by_source = {rule.source: rule for rule in rules}
    assert by_source["^/old$"].status == 301
    blog = match_rules("https://old.example/blog/hello", rules)
    assert blog is not None
    assert blog.target == "/news/hello"
    exact = match_rules("https://old.example/exact", rules)
    assert exact is not None
    assert exact.target == "/dest"
    prefix = match_rules("https://old.example/prefix/child", rules)
    assert prefix is not None
    assert prefix.rule.status == 302


def test_csv_redirects(tmp_path) -> None:
    path = tmp_path / "redirects.csv"
    path.write_text("source,target,status\n/a,/b,301\n/c,/d,302\n", encoding="utf-8")
    rules, issues = load_redirects(str(path), "csv")
    assert issues == []
    assert [(rule.source, rule.target, rule.status) for rule in rules] == [
        ("/a", "/b", 301),
        ("/c", "/d", 302),
    ]


def test_bad_netlify_line_is_an_issue(tmp_path) -> None:
    path = tmp_path / "_redirects"
    path.write_text("/only-one-token\n/ok /fine 301\n", encoding="utf-8")
    rules, issues = load_redirects(str(path), "netlify")
    assert len(rules) == 1
    assert any(issue.code == "map_parse_error" for issue in issues)

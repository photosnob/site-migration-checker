from migcheck.robots import blocking_rule, parse_robots

ROBOTS = """
User-agent: *
Allow: /topics/seo/public
Disallow: /topics/
Disallow: /wp-admin/

User-agent: AdsBot
Disallow: /

Sitemap: https://new.example/sitemap.xml
"""


def test_longest_match_and_googlebot_group() -> None:
    parsed = parse_robots(ROBOTS)
    assert parsed.group == "*"
    assert parsed.sitemaps == ("https://new.example/sitemap.xml",)
    assert blocking_rule("/topics/seo/", parsed) is not None
    assert blocking_rule("/topics/seo/public", parsed) is None
    assert blocking_rule("/wp-admin/login", parsed) is not None
    assert blocking_rule("/", parsed) is None
    assert not parsed.blocks_all


def test_googlebot_group_wins_over_star() -> None:
    text = "User-agent: *\nDisallow: /secret\n\nUser-agent: Googlebot\nDisallow: /draft\n"
    parsed = parse_robots(text)
    assert parsed.group == "googlebot"
    assert blocking_rule("/draft", parsed) is not None
    assert blocking_rule("/secret", parsed) is None


def test_disallow_root_blocks_all() -> None:
    parsed = parse_robots("User-agent: *\nDisallow: /\n")
    assert parsed.blocks_all
    assert blocking_rule("/blog/post", parsed) is not None

"""Write the synthetic Northwind Journal fixture.

The HTML, sitemap, redirect map, and Search Console file are invented inputs.
Running this script refreshes those inputs. It does not create the sample report;
`migcheck run --fixture examples/wp-to-static` does that.

Some redirect rules and responses are intentionally wrong so the checker has
something to find. This is not a production site.
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from migcheck.urls import normalize_url

OLD = "https://old.journal.test"
NEW = "https://new.journal.test"
SENTENCE = (
    "Northwind ships a fictional essay so migcheck can compare visible copy "
    "during a migration rehearsal today."
)
ARTICLE = [SENTENCE] * 10
THIN = [SENTENCE]
DISCLAIMER = (
    "Synthetic fixture for the fictional Northwind Journal. URLs, HTML, and the "
    "clicks column are invented inputs, not a crawl or a traffic measurement."
)


def generate(dest: Path) -> None:
    bodies = dest / "bodies"
    bodies.mkdir(parents=True, exist_ok=True)
    responses: list[dict[str, object]] = []
    old_pages: list[dict[str, object]] = []

    def add(
        url: str,
        *,
        status: int,
        body: str = "",
        body_name: str | None = None,
        location: str | None = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        headers = {"content-type": content_type}
        if location is not None:
            headers["location"] = location
        row: dict[str, object] = {
            "url": normalize_url(url),
            "status": status,
            "headers": headers,
        }
        if body_name is not None:
            (bodies / body_name).write_text(body, encoding="utf-8")
            row["body_file"] = f"bodies/{body_name}"
        else:
            row["body"] = body
        responses.append(row)

    def document(
        *,
        title: str,
        description: str | None,
        h1: str | None,
        canonical: str | None,
        paragraphs: list[str],
        robots: str | None = None,
        refresh: str | None = None,
    ) -> str:
        metas: list[str] = []
        if description:
            metas.append(
                f'<meta name="description" content="{html.escape(description, quote=True)}">'
            )
        if robots:
            metas.append(f'<meta name="robots" content="{html.escape(robots, quote=True)}">')
        if canonical:
            metas.append(f'<link rel="canonical" href="{html.escape(canonical, quote=True)}">')
        if refresh:
            metas.append(
                f'<meta http-equiv="refresh" content="0;url={html.escape(refresh, quote=True)}">'
            )
        heading = f"<h1>{html.escape(h1)}</h1>" if h1 else ""
        prose = "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
        meta_block = "\n".join(metas)
        return (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f"<title>{html.escape(title)}</title>\n{meta_block}\n</head>\n"
            "<body>\n<header><p>Northwind Journal</p></header>\n<main>\n"
            f"{heading}\n{prose}\n</main>\n</body>\n</html>\n"
        )

    specs: list[dict[str, object]] = [
        {
            "path": "/",
            "title": "Northwind Journal",
            "h1": "Northwind Journal",
            "description": "A fictional journal used only as a migcheck fixture.",
            "clicks": 420,
            "impressions": 12000,
            "kind": "same",
        },
        {
            "path": "/about/",
            "title": "About the journal",
            "h1": "About",
            "description": "How the fictional Northwind Journal is produced.",
            "clicks": 80,
            "impressions": 2400,
            "kind": "about",
        },
        {
            "path": "/blog/2021/05/12/shipping-static/",
            "title": "Shipping static",
            "h1": "Shipping static",
            "description": "A fixture article about moving a journal off WordPress.",
            "clicks": 260,
            "impressions": 6400,
            "kind": "ship",
        },
        {
            "path": "/blog/2020/01/03/wordpress-to-jamstack/",
            "title": "WordPress to a static site",
            "h1": "WordPress to a static site",
            "description": "Fixture notes on permalink changes during a replatform.",
            "clicks": 190,
            "impressions": 4100,
            "kind": "chain",
        },
        {
            "path": "/blog/2019/08/01/old-seo-checklist/",
            "title": "An old SEO checklist",
            "h1": "An old SEO checklist",
            "description": "Fixture checklist kept so a homepage redirect is visible.",
            "clicks": 800,
            "impressions": 15000,
            "kind": "home-redirect",
        },
        {
            "path": "/contact/",
            "title": "Contact",
            "h1": "Contact",
            "description": "Fixture contact page for a redirect that was not deployed.",
            "clicks": 70,
            "impressions": 1800,
            "kind": "contact",
        },
        {
            "path": "/blog/2018/02/11/redirect-loops/",
            "title": "Redirect loops",
            "h1": "Redirect loops",
            "description": "Fixture page whose new server redirects to itself.",
            "clicks": 30,
            "impressions": 900,
            "kind": "loop",
        },
        {
            "path": "/services/audits/",
            "title": "SEO audits",
            "h1": "SEO audits",
            "description": "Fixture service page with a protocol downgrade.",
            "clicks": 110,
            "impressions": 3000,
            "kind": "protocol",
        },
        {
            "path": "/category/seo/",
            "title": "SEO",
            "h1": "SEO",
            "description": "Fixture category page that should redirect to a topic.",
            "clicks": 150,
            "impressions": 3600,
            "kind": "category",
        },
        {
            "path": "/blog/2017/07/04/thin-page/",
            "title": "A longer article about redirects",
            "h1": "A longer article about redirects",
            "description": "A long fixture description that the new template drops entirely from the page.",
            "clicks": 55,
            "impressions": 1600,
            "kind": "thin",
        },
        {
            "path": "/pricing/",
            "title": "Pricing for the print edition",
            "h1": "Pricing for the print edition",
            "description": "Fixture pricing page with no redirect and no replacement path.",
            "clicks": 95,
            "impressions": 2200,
            "kind": "pricing",
        },
        {
            "path": "/welcome.html",
            "title": "Welcome",
            "h1": "Welcome",
            "description": "Fixture page that still uses a meta refresh.",
            "clicks": 15,
            "impressions": 400,
            "kind": "refresh",
        },
        {
            "path": "/account/login/",
            "title": "Account login",
            "h1": "Account login",
            "description": "Fixture account route that the static host answers with an error.",
            "clicks": 5,
            "impressions": 200,
            "kind": "error",
        },
        {
            "path": "/team/",
            "title": "The team",
            "h1": "The team",
            "description": "Fixture team page that only changes its trailing slash.",
            "clicks": 22,
            "impressions": 700,
            "kind": "slash",
        },
        {
            "path": "/archive/field-notes/",
            "title": "Field notes from the pilot",
            "h1": "Field notes from the pilot",
            "description": "Fixture notes whose slug survives under a new directory.",
            "clicks": 44,
            "impressions": 1100,
            "kind": "notes",
        },
    ]

    def old_doc(spec: dict[str, object]) -> str:
        return document(
            title=str(spec["title"]),
            description=str(spec["description"]),
            h1=str(spec["h1"]),
            canonical=OLD + str(spec["path"]),
            paragraphs=ARTICLE,
        )

    def kept(path: str, spec: dict[str, object], *, canonical: str | None) -> str:
        return document(
            title=str(spec["title"]),
            description=str(spec["description"]),
            h1=str(spec["h1"]),
            canonical=canonical,
            paragraphs=ARTICLE,
        )

    for index, spec in enumerate(specs):
        path = str(spec["path"])
        old_url = OLD + path
        old_pages.append(spec)
        name = f"old-{index:02d}.html"
        add(old_url, status=200, body=old_doc(spec), body_name=name)
        kind = spec["kind"]
        probe = NEW + path
        if kind == "same":
            add(
                probe,
                status=200,
                body=kept(path, spec, canonical=NEW + path),
                body_name="new-home.html",
            )
        elif kind == "about":
            add(probe, status=301, location="/about", content_type="text/plain")
            add(
                NEW + "/about",
                status=200,
                body=document(
                    title=str(spec["title"]),
                    description=str(spec["description"]),
                    h1=str(spec["h1"]),
                    canonical=None,
                    paragraphs=ARTICLE,
                ),
                body_name="new-about.html",
            )
        elif kind == "ship":
            add(probe, status=301, location="/blog/shipping-static/", content_type="text/plain")
            add(
                NEW + "/blog/shipping-static/",
                status=200,
                body=kept(path, spec, canonical=NEW + "/blog/shipping-static/"),
                body_name="new-shipping.html",
            )
        elif kind == "chain":
            add(probe, status=302, location="/blog/temp-wordpress/", content_type="text/plain")
            add(
                NEW + "/blog/temp-wordpress/",
                status=301,
                location="/blog/wordpress-to-jamstack/",
                content_type="text/plain",
            )
            add(
                NEW + "/blog/wordpress-to-jamstack/",
                status=200,
                body=kept(path, spec, canonical=NEW + "/blog/wordpress-to-jamstack/"),
                body_name="new-jamstack.html",
            )
        elif kind == "home-redirect":
            add(probe, status=301, location="/", content_type="text/plain")
            add(
                NEW + "/guides/seo-checklist/",
                status=200,
                body=kept(path, spec, canonical=NEW + "/guides/seo-checklist/"),
                body_name="new-guide.html",
            )
        elif kind == "contact":
            add(probe, status=404, body="<html><title>Missing</title><body>missing</body></html>")
            add(
                NEW + "/contact",
                status=200,
                body=kept(path, spec, canonical=NEW + "/contact"),
                body_name="new-contact.html",
            )
        elif kind == "loop":
            add(probe, status=301, location=probe, content_type="text/plain")
            add(
                NEW + "/blog/redirect-loops/",
                status=200,
                body=kept(path, spec, canonical=NEW + "/blog/redirect-loops/"),
                body_name="new-loops.html",
            )
        elif kind == "protocol":
            http_url = "http://new.journal.test/services/seo-audits/"
            add(probe, status=301, location=http_url, content_type="text/plain")
            add(
                http_url,
                status=200,
                body=kept(path, spec, canonical=NEW + "/services/seo-audits/"),
                body_name="new-audits-http.html",
            )
        elif kind == "category":
            add(
                probe,
                status=200,
                body=document(
                    title="SEO",
                    description="The old category URL is still being served.",
                    h1="SEO",
                    canonical=probe,
                    paragraphs=ARTICLE,
                ),
                body_name="new-category-stale.html",
            )
            add(
                NEW + "/topics/seo/",
                status=200,
                body=kept(path, spec, canonical=NEW + "/topics/seo/"),
                body_name="new-topics.html",
            )
        elif kind == "thin":
            add(probe, status=301, location="/blog/thin-page/", content_type="text/plain")
            add(
                NEW + "/blog/thin-page/",
                status=200,
                body=document(
                    title="Notes",
                    description=None,
                    h1=None,
                    canonical=OLD + path,
                    paragraphs=THIN,
                    robots="noindex, nofollow",
                ),
                body_name="new-thin.html",
            )
        elif kind == "pricing":
            add(probe, status=404, body="<html><title>Missing</title><body>missing</body></html>")
        elif kind == "refresh":
            add(
                probe,
                status=200,
                body=document(
                    title=str(spec["title"]),
                    description=str(spec["description"]),
                    h1=str(spec["h1"]),
                    canonical=probe,
                    paragraphs=ARTICLE,
                    refresh="/about",
                ),
                body_name="new-welcome.html",
            )
        elif kind == "error":
            add(probe, status=500, body="fixture error", content_type="text/plain")
        elif kind == "slash":
            add(probe, status=301, location="/team", content_type="text/plain")
            add(
                NEW + "/team",
                status=200,
                body=kept(path, spec, canonical=NEW + "/team"),
                body_name="new-team.html",
            )
        elif kind == "notes":
            add(probe, status=404, body="<html><title>Missing</title><body>missing</body></html>")
        else:
            raise RuntimeError(f"unknown kind {kind}")

    add(
        NEW + "/plans/",
        status=200,
        body=document(
            title="Pricing for the print edition and archive",
            description="Fixture plans page a title match can point at.",
            h1="Pricing for the print edition and archive",
            canonical=NEW + "/plans/",
            paragraphs=ARTICLE,
        ),
        body_name="new-plans.html",
    )
    add(
        NEW + "/journal/field-notes/",
        status=200,
        body=document(
            title="Kitchen inventory",
            description="Fixture page whose slug matches an old field-notes URL.",
            h1="Kitchen inventory",
            canonical=NEW + "/journal/field-notes/",
            paragraphs=ARTICLE,
        ),
        body_name="new-field-notes.html",
    )
    add(
        NEW + "/services/seo-audits/",
        status=200,
        body=document(
            title="SEO audits",
            description="The conflicting redirect target, which does exist.",
            h1="SEO audits",
            canonical=NEW + "/services/seo-audits/",
            paragraphs=ARTICLE,
        ),
        body_name="new-seo-audits.html",
    )
    add(
        NEW + "/services/site-audits/",
        status=200,
        body=document(
            title="Site audits",
            description="The other side of a conflicting redirect rule.",
            h1="Site audits",
            canonical=NEW + "/services/site-audits/",
            paragraphs=ARTICLE,
        ),
        body_name="new-site-audits.html",
    )
    for path, name in (
        ("/legacy/a", "legacy-a"),
        ("/legacy/b", "legacy-b"),
        ("/blog/specific-post/", "specific"),
    ):
        add(
            NEW + path,
            status=200,
            body=document(
                title=name,
                description="Fixture target used only by the redirect map.",
                h1=name,
                canonical=NEW + path,
                paragraphs=ARTICLE,
            ),
            body_name=f"new-{name}.html",
        )
    add(NEW + "/offers/spring/", status=404, body="missing", content_type="text/plain")

    sitemap_paths = [
        "/",
        "/about",
        "/blog/shipping-static/",
        "/blog/thin-page/",
        "/topics/seo/",
        "/plans/",
        "/journal/field-notes/",
        "/services/seo-audits/",
        "/team",
    ]
    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in sitemap_paths:
        sitemap.append("  <url><loc>" + NEW + path + "</loc></url>")
    sitemap.append("</urlset>")
    sitemap_body = "\n".join(sitemap) + "\n"
    (dest / "new-sitemap.xml").write_text(sitemap_body, encoding="utf-8")
    add(NEW + "/sitemap.xml", status=200, body=sitemap_body, content_type="application/xml")

    robots = (
        "User-agent: *\n"
        "Allow: /topics/seo/public\n"
        "Disallow: /topics/\n"
        "Disallow: /wp-admin/\n"
        "\n"
        f"Sitemap: {NEW}/sitemap.xml\n"
    )
    (dest / "robots.txt").write_text(robots, encoding="utf-8")
    add(NEW + "/robots.txt", status=200, body=robots, content_type="text/plain; charset=utf-8")

    old_sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for spec in specs:
        old_sitemap.append("  <url><loc>" + OLD + str(spec["path"]) + "</loc></url>")
    old_sitemap.append("</urlset>")
    (dest / "old-sitemap.xml").write_text("\n".join(old_sitemap) + "\n", encoding="utf-8")

    with (dest / "search-console-pages.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Top pages", "Clicks", "Impressions", "CTR", "Position"])
        for spec in specs:
            clicks = int(spec["clicks"])
            impressions = int(spec["impressions"])
            ctr = f"{(clicks / impressions) * 100:.2f}%"
            writer.writerow([OLD + str(spec["path"]), clicks, impressions, ctr, "4.2"])

    redirects = """# Fixture redirect map for the Northwind Journal migration.
# Some rules are intentionally wrong so migcheck has findings.
# This is not a production ruleset.

/about/ /about 301
/about/ /about 301
/blog/2021/05/12/shipping-static/ /blog/shipping-static/ 301
/blog/2020/01/03/wordpress-to-jamstack/ /blog/wordpress-to-jamstack/ 301
/blog/2019/08/01/old-seo-checklist/ /guides/seo-checklist/ 301
/contact/ /contact 301
/blog/2018/02/11/redirect-loops/ /blog/redirect-loops/ 301
/services/audits/ /services/seo-audits/ 301
/services/audits/ /services/site-audits/ 301
/category/seo/ /topics/seo/ 301
/blog/2017/07/04/thin-page/ /blog/thin-page/ 301
/promo/ /offers/spring/ 301
/legacy/a /legacy/b 301
/legacy/b /legacy/a 301
/blog/* /notes/:splat 301
/blog/2016/03/03/specific-post/ /blog/specific-post/ 301
"""
    (dest / "_redirects").write_text(redirects, encoding="utf-8")

    payload = {
        "sample": True,
        "disclaimer": DISCLAIMER,
        "old_host": OLD,
        "new_host": NEW,
        "mode": "staging",
        "old_sitemap": "old-sitemap.xml",
        "old_csv": "search-console-pages.csv",
        "redirects": "_redirects",
        "responses": responses,
    }
    (dest / "fixture.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parent)

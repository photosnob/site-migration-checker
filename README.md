# migcheck

`migcheck` checks whether a site move will keep old URLs resolvable, indexable, and comparable to the pages they replace. It is a command-line checker for a replatform or redesign: WordPress to a static host, a staging cutover, or a redirect-map review before DNS changes.

It does not estimate traffic, rankings, or Core Web Vitals. Every number in a report is a status code, a hop, an HTML signal, or a similarity score from the inputs you gave it.

## Why migrations lose pages

Search engines keep the old URL until something tells them it moved. The usual failures are mechanical:

- The old path returns **404**, so the indexed URL simply disappears.
- Many old paths **301 to the homepage**. Google treats that pattern as a soft 404, not as a moved page.
- A **302 or 307** says the move is temporary, so the old URL can stay the indexed one. A permanent move is a **301 or 308**.
- **Chains** (more than one hop) waste crawl and are easy to break on the next redesign. One hop is the shape to ship.
- A **loop**, an **https-to-http** hop, or a **www** mismatch sends the crawler somewhere you did not intend.
- The new template ships with **noindex**, a **canonical pointing at the old host**, or a **robots.txt** copied from staging that disallows the new sections.
- The new sitemap omits URLs that return 200, or lists URLs that are noindex.
- The visible page is a thin rewrite of the old one: missing title, missing H1, or a large drop in text.

`migcheck` looks for those conditions in HTTP responses and in the redirect file. It does not run JavaScript.

## What it checks

| Input | What happens |
| --- | --- |
| Old URLs | A sitemap file or URL (including a sitemap index), a CSV crawl or Search Console Pages export, or a text file of URLs. |
| New host | In **staging** mode (the default) each old path is requested on this origin. In **live** mode the old URL is requested as published. |
| Redirect map | Optional. CSV (`source,target,status`), Netlify/Cloudflare `_redirects`, or a practical subset of nginx `rewrite` / `location` + `return`. First match wins. |

For each old URL it records the hop chain and compares the landing page with the page the map expects (or the same path on the new host, if there is no rule):

- 404, 410, and 5xx
- 302 / 303 / 307 temporary redirects
- chains, loops, and redirects that never return a document
- redirects to the homepage
- a 200 on the old path when a different target was expected
- protocol downgrades, www mismatches, trailing-slash differences, query mismatches

The redirect file itself is linted for duplicate sources, conflicting targets, literal cycles, rules shadowed by an earlier pattern, temporary statuses, homepage targets, and concrete targets that return 404.

Where both HTML documents can be fetched, it compares title, meta description, H1, canonical, noindex (including `X-Robots-Tag`), and visible word count. A drop of 30% or more is flagged when the old page has at least 80 words; a drop of 50% or more is high. Head, script, and style text are not counted.

It diffs the new `sitemap.xml` against the indexable destination of each old URL, and it evaluates the `robots.txt` group Googlebot would use (the longest matching user-agent, otherwise `*`). The longest matching path wins; an equal-length `Allow` beats `Disallow`.

Unmapped URLs that did not land can get a **suggestion** from slug or title similarity against the new sitemap. Suggestions are hints. They do not change the readiness result.

## Install

Python 3.11 or newer.

```bash
pip install .
migcheck --version
```

From a checkout, for development:

```bash
pip install -e ".[dev]"
```

The package is not published on PyPI. Install it from this repository.

## Quickstart

Offline, against the bundled fixture (no network):

```bash
migcheck run --fixture examples/wp-to-static --output /tmp/migcheck-sample --no-color
```

Against a real staging host:

```bash
migcheck run \
  --old-sitemap https://old.example.com/sitemap.xml \
  --old-csv search-console-pages.csv \
  --new-host https://staging.example.com \
  --redirects _redirects \
  --output migcheck-report
```

After the new host is actually serving the old names (DNS cut over, or redirects already live on the old origin):

```bash
migcheck run \
  --mode live \
  --old-sitemap old-sitemap.xml \
  --new-host https://www.example.com \
  --redirects _redirects \
  --output migcheck-report
```

Useful flags: `--rps` (default 5), `--concurrency` (default 8), `--max-hops`, `--max-word-drop`, `--suggest-threshold` (default 0.80), `--fail-on` (default `high`), `--no-suggest`, `--no-compare-content`. Pass `--help` on `run` for the rest.

Use a low request rate against any host you do not operate.

### Output

The output directory contains:

| File | Contents |
| --- | --- |
| `terminal.txt` | The same summary printed to the terminal. |
| `index.html` | The full report. Open it locally. It has no external assets. |
| `report.json` | Machine-readable findings. |
| `issues.csv` | One row per finding. |
| `pages.csv` | One row per old URL, including titles and word counts. |
| `_redirects` | Proposed fixes and suggestions. Review every line. It is not a complete map, and it does not overwrite the file you passed in. |

Exit code 0 means nothing at or above `--fail-on` was found. The default is `high`, so critical and high findings exit 1. Bad input exits 2.

### Readiness

| Status | Rule |
| --- | --- |
| BLOCKED | Any critical finding. |
| NEEDS REVIEW | No critical finding, at least one high finding. |
| READY WITH NOTES | Only medium, low, or info findings. |
| READY | No findings, or only info. |

Suggestions and info findings do not by themselves block. The HTML report includes the rule for each issue code.

## Sample report

The fixture in `examples/wp-to-static` is a **synthetic** WordPress-to-static move for a fictional "Northwind Journal". The HTML, the redirect map, and the clicks column are invented inputs. They are not a crawl and not a traffic measurement. Several rules and responses are wrong on purpose.

This summary is the terminal output of:

```bash
migcheck run --fixture examples/wp-to-static --output examples/wp-to-static/sample-report --no-color
```

CI checks that the committed report matches a fresh run of that command (timestamps aside).

```text
migcheck 0.1.0
Sample: synthetic fixture, not a measurement of a live site.
Mode: staging
New host: https://new.journal.test
Old URLs: 15
Redirect rules: 16 (1 wildcard)

Readiness: BLOCKED
Critical issues will drop or hide URLs. Do not cut over yet.

Pages: 2 pass, 3 warn, 10 fail
Issues: 9 critical, 8 high, 11 medium, 3 low, 1 info
Exit: failure (--fail-on high)
```

The full listing, with every URL and message, is [`examples/wp-to-static/sample-report/terminal.txt`](examples/wp-to-static/sample-report/terminal.txt). Open [`examples/wp-to-static/sample-report/index.html`](examples/wp-to-static/sample-report/index.html) in a browser for the same run. What that run actually found:

- `/blog/2019/08/01/old-seo-checklist/` 301s to the homepage even though the map names `/guides/seo-checklist/`.
- `/contact/` is 404 while the map's target `/contact` returns 200. The slash redirect was never deployed.
- `/pricing/` and `/archive/field-notes/` are unmapped 404s. Title similarity suggests `/plans/` (score 0.83). Slug similarity suggests `/journal/field-notes/` (score 1.00).
- `/blog/2018/02/11/redirect-loops/` redirects to itself.
- `/account/login/` returns 500.
- `/blog/thin-page/` is a single 301 to the right path, and the new HTML is noindex, canonicals the old host, drops the H1 and meta description, and falls from 167 to 18 words.
- `/blog/2020/01/03/wordpress-to-jamstack/` is a 302 plus a second hop.
- `/category/seo/` still returns 200. The mapped topic URL is disallowed by `Disallow: /topics/` in robots.txt. A longer `Allow` does not cover it.
- `/services/audits/` reaches the mapped path over **http**.
- The map has a duplicate `/about/` rule, conflicting `/services/audits/` targets, a cycle between `/legacy/a` and `/legacy/b`, a shadowed specific `/blog/` rule under `/blog/*`, and a `/promo` target that 404s.
- `/` and `/blog/2021/05/12/shipping-static/` pass: one clean response, matching title, H1, and word count.

Those sentences are a reading of that fixture run, not a claim about any live site.

## Pre-launch checklist

1. Run `migcheck` against staging in the default mode. Do not cut over while the report is BLOCKED.
2. Clear critical rows first: 404s, homepage redirects, loops, 5xx, noindex on URLs that should rank, `Disallow: /`, and redirect targets that 404.
3. Collapse chains and replace 302/307 with a single 301 or 308 to the final https URL.
4. Read the content table. A redirect can be perfect and the new template still noindex, canonical the old host, or drop the body copy.
5. Confirm `robots.txt` on the new host is the production file, not the staging file. Confirm the new sitemap lists the 200 destinations and does not list noindex URLs.
6. Open `suggested _redirects` and keep only the lines you have checked. Merge them into the real map. Do not deploy the sample file.
7. Point internal links at the final URLs so the new site does not depend on the hops.
8. After DNS (or after the old host starts serving the redirects), run again with `--mode live`.
9. In Search Console, submit the new sitemap. If the move is to a different domain, use the change-of-address tool. Keep the redirects in place for at least a year; Google's site-move guidance asks for that, and this tool does not watch the site after launch.

## Limitations

- Response HTML only. Client-rendered text is invisible to the word count and the title checks. That is the right check for a static build, and the wrong one for an app shell.
- Nginx support is `rewrite`, and `location` blocks that `return` or `rewrite`. Nested braces, named captures (`$uri`), and `map` blocks are not parsed.
- Netlify/Cloudflare conditions (`Country`, `Language`, `Role`), query-string matches, and mid-path `*` tokens are reported and not simulated. A status of 200 is recorded as a rewrite, not followed as a proxy.
- Wildcard targets are not requested with a made-up path. Literal targets are.
- Cycle detection follows literal rules, plus a pattern that sends a probe path back to itself. It does not prove an arbitrary wildcard graph is acyclic.
- Word count includes navigation and footer text that is in the HTML. A shared chrome can hide a thinner article, or the reverse.
- Similarity suggestions collide when two pages share a short title. The report shows the strings it compared.
- One failed request is a finding, not a silent retry. Run again if the network blipped.
- Redirects and robots rules are evaluated as documented above, not by asking Google.

## Roadmap

- hreflang, pagination, and structured-data diffs
- a diff between two `report.json` files
- gzip sitemap bodies and deeper sitemap-index controls
- query-string and country conditions in `_redirects`
- optional rendering for pages whose content is not in the HTML

Core Web Vitals and rank tracking are out of scope.

## Development

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest
```

Regenerate the fixture inputs, then the sample report:

```bash
python examples/wp-to-static/generate_fixture.py
migcheck run --fixture examples/wp-to-static --output examples/wp-to-static/sample-report --no-color
```

Tests use the fixture and mocked HTTP. They do not call live sites. GitHub Actions runs ruff, mypy, and pytest on Python 3.11 and 3.12.

## License

MIT. See [LICENSE](LICENSE).

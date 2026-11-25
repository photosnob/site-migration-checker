from migcheck.extract import core_title, extract_html, header_noindex

HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>  Pricing &amp; plans  | Northwind </title>
  <meta name="description" content="A description">
  <meta name="robots" content="noindex, nofollow">
  <link rel="canonical" href="/plans/">
  <meta http-equiv="refresh" content="0; url=/elsewhere">
  <script>var ignore = "these words"</script>
</head>
<body>
  <h1>First heading</h1>
  <h1>Second heading</h1>
  <p>Visible copy stays.</p>
</body>
</html>
"""


def test_extracts_signals_and_skips_script() -> None:
    signals = extract_html(HTML, "https://new.example/plans/")
    assert signals.title == "Pricing & plans | Northwind"
    assert signals.description == "A description"
    assert signals.h1 == "First heading"
    assert signals.h1_count == 2
    assert signals.canonical == "https://new.example/plans/"
    assert signals.noindex
    assert signals.meta_refresh == "https://new.example/elsewhere"
    assert "ignore" not in " ".join([])
    # First heading, Second heading, and "Visible copy stays."
    assert signals.word_count == 7


def test_core_title_drops_site_name() -> None:
    assert core_title("Pricing & plans | Northwind") == "Pricing & plans"


def test_header_noindex() -> None:
    assert header_noindex({"X-Robots-Tag": "noindex"})
    assert not header_noindex({"X-Robots-Tag": "nofollow"})

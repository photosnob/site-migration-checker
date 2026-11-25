from migcheck.content import content_issues
from migcheck.models import Hop, PageResult, PageSignals
from migcheck.suggest import suggest_redirects


def test_word_drop_noindex_and_canonical() -> None:
    old = PageSignals(
        title="A longer article about redirects", description="x" * 50, h1="Heading", word_count=200
    )
    new = PageSignals(
        title="Notes",
        description=None,
        h1=None,
        canonical="https://old.example/post",
        noindex=True,
        word_count=40,
    )
    issues = content_issues(
        old=old,
        new=new,
        old_url="https://old.example/post",
        new_url="https://new.example/post",
        max_word_drop=0.3,
        min_words=80,
        truncated=False,
    )
    codes = {issue.code: issue.severity.value for issue in issues}
    assert codes["noindex"] == "critical"
    assert codes["word_count_drop"] == "high"
    assert codes["canonical_mismatch"] == "high"
    assert "title_changed" in codes
    assert "h1_missing" in codes
    assert "meta_description_missing" in codes


def test_site_name_suffix_is_not_a_title_change() -> None:
    old = PageSignals(title="Shipping static | Old", h1="Shipping static", word_count=100)
    new = PageSignals(
        title="Shipping static | New",
        h1="Shipping static",
        canonical="https://new.example/a",
        word_count=100,
    )
    issues = content_issues(
        old=old,
        new=new,
        old_url="https://old.example/a",
        new_url="https://new.example/a",
        max_word_drop=0.3,
        min_words=80,
        truncated=False,
    )
    assert "title_changed" not in {issue.code for issue in issues}


def test_suggestions_use_title_or_slug() -> None:
    pricing = PageResult(
        old_url="https://old.example/pricing/",
        probe_url="https://new.example/pricing/",
        expected_url="https://new.example/pricing/",
        final_url="https://new.example/pricing/",
        hops=[Hop(url="https://new.example/pricing/", status=404, location=None)],
        old_signals=PageSignals(title="Pricing for the print edition"),
    )
    notes = PageResult(
        old_url="https://old.example/blog/2015/11/02/field-notes/",
        probe_url="https://new.example/blog/2015/11/02/field-notes/",
        expected_url="https://new.example/blog/2015/11/02/field-notes/",
        final_url="https://new.example/blog/2015/11/02/field-notes/",
        hops=[
            Hop(url="https://new.example/blog/2015/11/02/field-notes/", status=404, location=None)
        ],
        old_signals=PageSignals(title="Field notes from the pilot"),
    )
    pool = [
        (
            "https://new.example/plans/",
            PageSignals(title="Pricing for the print edition and archive"),
        ),
        ("https://new.example/journal/field-notes/", PageSignals(title="Kitchen inventory")),
        ("https://new.example/", PageSignals(title="Home")),
    ]
    found = suggest_redirects([pricing, notes], pool, threshold=0.80)
    by_old = {item.old_url: item for item in found}
    assert by_old[pricing.old_url].candidate_url.endswith("/plans/")
    assert by_old[pricing.old_url].via == "title"
    assert by_old[notes.old_url].candidate_url.endswith("/journal/field-notes/")
    assert by_old[notes.old_url].via == "slug"

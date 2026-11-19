"""Snapshot cleaner: menu and boilerplate lines go, eligibility sentences stay. No network."""

from pathlib import Path

from app.rules.sources import SOURCES_DIR
from app.web.cache import parse_snapshot, skip_text_for
from app.web.fetch import clean_lines, html_to_text

FIXTURE = Path(__file__).parent / "fixtures" / "udyogini_menu.html"
MENU = ["Loading", "Skip to content", "Accessibility", "Login", "About Us", "Board of Directors", "Contact Us", "Sitemap", "Help", "Privacy Policy", "ಕನ್ನಡ"]
ELIGIBILITY = ["Age limit 18 to 55 Years", "Annual Income limit for General / Special Category below Rs.1.50 lakhs"]


def test_the_udyogini_fixture_loses_the_menu_and_keeps_the_eligibility_sentences() -> None:
    _, text = html_to_text(FIXTURE.read_text(encoding="utf-8"), skip_text_for("udyogini_ka"))
    lines = text.splitlines()
    for item in MENU:
        assert item not in lines, item
    for sentence in ELIGIBILITY:
        assert any(sentence in line for line in lines), sentence


def test_the_committed_udyogini_snapshot_is_clean_and_still_valid() -> None:
    [path] = (SOURCES_DIR / "udyogini_ka" / "web").glob("*.txt")
    snap = parse_snapshot(path)  # None if the checksum does not match the body
    assert snap is not None
    lines = snap["text"].splitlines()
    for item in MENU:
        assert item not in lines, item
    assert any("Age limit 18 to 55 Years" in line for line in lines)
    assert any("Annual Income limit" in line for line in lines)


def test_short_lines_before_the_first_sentence_are_dropped_but_longer_headings_are_kept() -> None:
    lines = ["Home", "Menu", "Scheme overview heading here", "This is the first real sentence of the page."]
    assert clean_lines(lines, []) == ["Scheme overview heading here", "This is the first real sentence of the page."]


def test_skip_entries_match_whole_lines_only() -> None:
    lines = ["Login", "Please login to see your application status today."]
    assert clean_lines(lines, ["Login"]) == ["Please login to see your application status today."]

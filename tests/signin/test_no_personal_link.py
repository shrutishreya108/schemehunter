"""The personal link and the PIN are gone for good (LLD signin §8): no route, no template, no text."""

from pathlib import Path

from app.main import app

APP = Path(__file__).resolve().parents[2] / "app"


def test_no_route_under_the_old_link_or_pin() -> None:
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if p.startswith(("/c/", "/pin"))]


def test_no_template_or_text_mentions_the_personal_link_or_pin() -> None:
    hits = []
    for f in APP.rglob("*"):
        if f.suffix not in {".py", ".html", ".txt", ".json"} or "__pycache__" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore").lower()
        for needle in (
            "personal link",
            "personal scheme hunter link",
            '"/c/',
            "'/c/",
            "pin_hash",
            "rotate_link",
        ):
            if needle in text:
                hits.append((str(f.relative_to(APP)), needle))
    assert not hits, hits

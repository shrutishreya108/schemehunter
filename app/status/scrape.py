"""Portal scraper: stubbed for the demo build (LLD v1.1 §0). Schemes fall back to user_forwards."""

from app.status.base import CheckResult


class ScrapeChecker:
    def check(self, app: dict) -> CheckResult:
        raise NotImplementedError("scraper is stubbed in the demo build")

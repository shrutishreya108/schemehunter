"""Offline schemes: nothing to fetch; the chase handler drafts follow-ups."""

from app.status.base import CheckResult


class ManualChecker:
    def check(self, app: dict) -> CheckResult:
        return CheckResult("nothing")

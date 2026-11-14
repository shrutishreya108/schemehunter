"""Offline schemes: nothing to fetch; the chase handler drafts follow-ups."""

from typing import Any

from app.status.base import CheckResult


class ManualChecker:
    def check(self, conn: Any, app: Any) -> CheckResult:
        return CheckResult("nothing")

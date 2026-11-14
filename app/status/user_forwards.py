"""Latest unprocessed inbound message mentioning the ref number or scheme (LLD §14). Slice 8."""

from app.status.base import CheckResult


class UserForwardsChecker:
    def check(self, app: dict) -> CheckResult:
        raise NotImplementedError("slice 8")

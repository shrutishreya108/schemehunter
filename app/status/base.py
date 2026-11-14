"""StatusChecker protocol, CheckResult, get_checker(scheme) (LLD §14)."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class CheckResult:
    kind: Literal["text", "nothing", "ask_user"]
    text: str | None = None
    message_id: str | None = None


class StatusChecker(Protocol):
    def check(self, app: dict) -> CheckResult: ...


def get_checker(status_mode: str) -> StatusChecker:
    from app.status.manual import ManualChecker
    from app.status.scrape import ScrapeChecker
    from app.status.user_forwards import UserForwardsChecker

    return {"user_forwards": UserForwardsChecker, "manual": ManualChecker, "scrape": ScrapeChecker}[
        status_mode
    ]()

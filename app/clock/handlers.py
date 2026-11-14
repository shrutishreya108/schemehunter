"""Job handlers (LLD §10.3). Slice 6 (status_check, chase, doc_nudge, deadline_reminders) and later."""


def status_check(job: dict) -> None:
    raise NotImplementedError("slice 6")


def chase(job: dict) -> None:
    raise NotImplementedError("slice 6")


def doc_nudge(job: dict) -> None:
    raise NotImplementedError("slice 6")


def deadline_reminders(job: dict) -> None:
    raise NotImplementedError("slice 6")


def retry_cycle(job: dict) -> None:
    raise NotImplementedError("slice 6")


def brain_run(job: dict) -> None:
    raise NotImplementedError("slice 6")


def offer_domain(job: dict) -> None:
    raise NotImplementedError("stretch: FEATURE_DOMAIN_OFFER")


def retention_cleanup(job: dict) -> None:
    raise NotImplementedError("slice 11")


HANDLERS = {
    "status_check": status_check,
    "chase": chase,
    "doc_nudge": doc_nudge,
    "deadline_reminders": deadline_reminders,
    "retry_cycle": retry_cycle,
    "brain_run": brain_run,
    "offer_domain": offer_domain,
    "retention_cleanup": retention_cleanup,
}

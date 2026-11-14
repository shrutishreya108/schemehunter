"""Application state machine (LLD §9). Every arrow writes an event.

Pure logic: storage, event logging and side effects are injected through TrackerStore, so this module
never touches the Brain, the scheduler or the database directly.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

STATES = {
    "DISCOVERED",
    "ELIGIBLE",
    "DOCS_PENDING",
    "DOCS_READY",
    "FORM_READY",
    "AWAITING_HUMAN_SUBMIT",
    "SUBMITTED",
    "UNDER_REVIEW",
    "QUERY_RAISED",
    "RESUBMITTED",
    "SANCTIONED",
    "REJECTED",
    "RETRY_NEXT_CYCLE",
    "CLOSED",
    "MISSED",
}
PRE_SUBMIT = ("DISCOVERED", "ELIGIBLE", "DOCS_PENDING", "DOCS_READY", "FORM_READY", "AWAITING_HUMAN_SUBMIT")
TERMINAL = {"SANCTIONED", "CLOSED", "RETRY_NEXT_CYCLE", "MISSED"}


@dataclass(frozen=True)
class Effect:
    """A side effect to run after a transition: schedule a job or create a pending action."""

    kind: str  # "schedule" | "pending"
    name: str  # job type or pending-action kind
    args: dict[str, Any] = field(default_factory=dict)


def sched(job_type: str, **args: Any) -> Effect:
    return Effect("schedule", job_type, args)


def pending(kind: str) -> Effect:
    return Effect("pending", kind)


@dataclass(frozen=True)
class Rule:
    actors: tuple[str, ...]
    effects: tuple[Effect, ...] = ()


TRANSITIONS: dict[tuple[str, str], Rule] = {
    ("DISCOVERED", "ELIGIBLE"): Rule(("agent",), (sched("deadline_reminders"),)),
    ("ELIGIBLE", "DOCS_PENDING"): Rule(("agent",), (sched("doc_nudge", days=1),)),
    ("DOCS_PENDING", "DOCS_READY"): Rule(("agent",)),  # guard: all required docs OK
    ("DOCS_READY", "FORM_READY"): Rule(("agent",)),
    ("FORM_READY", "AWAITING_HUMAN_SUBMIT"): Rule(("agent",), (pending("instruct_submit"),)),
    ("AWAITING_HUMAN_SUBMIT", "SUBMITTED"): Rule(
        ("user",), (sched("status_check", days=1),)
    ),  # guard: ref_number
    ("SUBMITTED", "UNDER_REVIEW"): Rule(
        ("agent", "clock"), (sched("status_check", days=3), sched("chase", days=10))
    ),
    ("UNDER_REVIEW", "QUERY_RAISED"): Rule(("agent",), (sched("doc_nudge", days=1),)),
    ("QUERY_RAISED", "DOCS_PENDING"): Rule(("agent",)),
    ("DOCS_PENDING", "RESUBMITTED"): Rule(("user",), (sched("status_check", days=1),)),
    ("RESUBMITTED", "UNDER_REVIEW"): Rule(("agent", "clock")),
    ("UNDER_REVIEW", "SANCTIONED"): Rule(("agent", "user"), (sched("offer_domain", days=0),)),  # flag-gated
    ("UNDER_REVIEW", "REJECTED"): Rule(("agent", "user"), (sched("brain_run", task="analyze rejection"),)),
    ("REJECTED", "RETRY_NEXT_CYCLE"): Rule(("agent",), (sched("retry_cycle", at="next_window"),)),
    ("REJECTED", "CLOSED"): Rule(("agent", "user")),
    **{(s, "MISSED"): Rule(("clock",), (sched("retry_cycle", at="next_window"),)) for s in PRE_SUBMIT},
}


class IllegalTransition(Exception):
    def __init__(self, from_state: str, to_state: str, actor: str, reason: str = "not allowed"):
        super().__init__(f"{from_state} → {to_state} by {actor}: {reason}")
        self.from_state, self.to_state, self.actor, self.reason = from_state, to_state, actor, reason


@dataclass
class Application:
    id: str
    case_id: str
    scheme_id: str
    state: str
    ref_number: str | None = None


class TrackerStore(Protocol):
    def required_docs_ok(self, app: Application) -> bool: ...
    def save_state(self, app: Application, to_state: str) -> None: ...
    def log_state_change(
        self, app: Application, from_state: str, to_state: str, actor: str, reason: str, payload: dict | None
    ) -> None: ...
    def run_effect(self, app: Application, effect: Effect) -> None: ...


def lookup(from_state: str, to_state: str) -> Rule | None:
    return TRANSITIONS.get((from_state, to_state))


def allowed_next(from_state: str, actor: str | None = None) -> list[str]:
    return sorted(
        to
        for (frm, to), rule in TRANSITIONS.items()
        if frm == from_state and (actor is None or actor in rule.actors)
    )


def run_guards(app: Application, to_state: str, store: TrackerStore) -> None:
    if to_state == "DOCS_READY" and not store.required_docs_ok(app):
        raise IllegalTransition(app.state, to_state, "-", "required documents not all OK")
    if to_state == "SUBMITTED" and not app.ref_number:
        raise IllegalTransition(app.state, to_state, "-", "reference number required")


def transition(
    app: Application, to: str, actor: str, reason: str, store: TrackerStore, payload: dict | None = None
) -> Application:
    """Move app to `to` if the table allows `actor` to, guards pass; then log and run effects."""
    rule = lookup(app.state, to)
    if not rule or actor not in rule.actors:
        raise IllegalTransition(app.state, to, actor)
    run_guards(app, to, store)
    from_state = app.state
    store.save_state(app, to)
    app.state = to
    store.log_state_change(app, from_state, to, actor, reason, payload)
    for effect in rule.effects:
        store.run_effect(app, effect)
    return app

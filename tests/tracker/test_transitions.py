from dataclasses import dataclass, field

import pytest

from app.tracker.states import (
    PRE_SUBMIT,
    STATES,
    TRANSITIONS,
    Application,
    Effect,
    IllegalTransition,
    allowed_next,
    transition,
)


@dataclass
class FakeStore:
    docs_ok: bool = True
    saved: list = field(default_factory=list)
    events: list = field(default_factory=list)
    effects: list = field(default_factory=list)

    def required_docs_ok(self, app):
        return self.docs_ok

    def save_state(self, app, to_state):
        self.saved.append(to_state)

    def log_state_change(self, app, from_state, to_state, actor, reason, payload):
        self.events.append((from_state, to_state, actor, reason))

    def run_effect(self, app, effect):
        self.effects.append(effect)


def app_in(state: str, ref: str | None = None) -> Application:
    return Application(id="a1", case_id="c1", scheme_id="pmegp", state=state, ref_number=ref)


@pytest.mark.parametrize(
    ("pair", "rule"), list(TRANSITIONS.items()), ids=[f"{a}->{b}" for a, b in TRANSITIONS]
)
def test_every_legal_transition_passes(pair, rule) -> None:
    frm, to = pair
    store = FakeStore()
    app = transition(app_in(frm, ref="REF1"), to, rule.actors[0], "test", store)
    assert app.state == to
    assert store.saved == [to]
    assert store.events == [(frm, to, rule.actors[0], "test")]
    assert store.effects == list(rule.effects)


def test_all_states_known() -> None:
    assert {s for pair in TRANSITIONS for s in pair} <= STATES


@pytest.mark.parametrize(
    ("frm", "to", "actor"),
    [
        ("DISCOVERED", "SUBMITTED", "agent"),  # skipping steps
        ("AWAITING_HUMAN_SUBMIT", "SUBMITTED", "agent"),  # only the user presses submit
        ("SANCTIONED", "UNDER_REVIEW", "agent"),  # out of a terminal state
        ("SUBMITTED", "MISSED", "clock"),  # MISSED only before submission
        ("ELIGIBLE", "MISSED", "agent"),  # only the clock declares MISSED
    ],
)
def test_illegal_transitions_raise(frm, to, actor) -> None:
    store = FakeStore()
    with pytest.raises(IllegalTransition):
        transition(app_in(frm, ref="REF1"), to, actor, "test", store)
    assert store.saved == [] and store.events == []


def test_ref_number_guard() -> None:
    store = FakeStore()
    with pytest.raises(IllegalTransition, match="reference number"):
        transition(app_in("AWAITING_HUMAN_SUBMIT"), "SUBMITTED", "user", "pressed submit", store)
    assert store.saved == []


def test_docs_ready_guard() -> None:
    with pytest.raises(IllegalTransition, match="documents"):
        transition(app_in("DOCS_PENDING"), "DOCS_READY", "agent", "x", FakeStore(docs_ok=False))


def test_under_review_schedules_check_and_chase() -> None:
    store = FakeStore()
    transition(app_in("SUBMITTED", ref="R"), "UNDER_REVIEW", "clock", "portal says forwarded", store)
    assert store.effects == [
        Effect("schedule", "status_check", {"days": 3}),
        Effect("schedule", "chase", {"days": 10}),
    ]


def test_query_loop_and_resubmit() -> None:
    store, app = FakeStore(), app_in("UNDER_REVIEW", ref="R")
    for to, actor in [
        ("QUERY_RAISED", "agent"),
        ("DOCS_PENDING", "agent"),
        ("RESUBMITTED", "user"),
        ("UNDER_REVIEW", "agent"),
    ]:
        transition(app, to, actor, "loop", store)
    assert app.state == "UNDER_REVIEW"


def test_missed_from_every_pre_submit_state() -> None:
    for state in PRE_SUBMIT:
        assert "MISSED" in allowed_next(state, "clock")

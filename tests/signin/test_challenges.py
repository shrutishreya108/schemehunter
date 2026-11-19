"""Sign-in challenges against the database: one use, attempt limits, supersede, expiry, parallel sign-up."""

import threading

import pytest

from app.db import connect
from app.signin import challenges as ch
from app.signin.emailnorm import normalize_email

pytestmark = pytest.mark.db
EMAIL = normalize_email("meena@gmail.com")


def new(conn, purpose="signup", email=EMAIL, lang="hi", case_id=None):
    row, code, token = ch.create_challenge(conn, email, purpose, lang, case_id=case_id)
    conn.commit()
    return row, code, token


def test_code_digits_accepts_spaces_dashes_and_devanagari() -> None:
    assert ch.code_digits("482 913") == "482913"
    assert ch.code_digits("482-913") == "482913"
    assert ch.code_digits("४८२९१३") == "482913"
    assert ch.code_digits("48291") is None and ch.code_digits("abcdef") is None
    assert ch.code_digits("4829131") is None and ch.code_digits(None) is None


def test_right_code_works_once(seeded, conn) -> None:
    _, code, _ = new(conn)
    assert ch.verify_code(conn, EMAIL, code) is not None
    conn.commit()
    assert ch.verify_code(conn, EMAIL, code) is None  # consumed


def test_wrong_codes_are_counted_and_the_right_one_then_fails(seeded, conn) -> None:
    _, code, _ = new(conn)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert ch.verify_code(conn, EMAIL, wrong) is None
        conn.commit()
    assert ch.verify_code(conn, EMAIL, code) is None  # locked: the right code no longer works
    conn.commit()
    row = conn.execute("select attempts from login_challenges where email_norm = %s", (EMAIL,)).fetchone()
    assert row["attempts"] == 5  # the counter survived across requests (committed)


def test_resend_supersedes_the_old_code(seeded, conn) -> None:
    _, old_code, _ = new(conn)
    _, new_code, _ = new(conn)
    if old_code != new_code:
        assert ch.verify_code(conn, EMAIL, old_code) is None
        conn.commit()
    assert ch.verify_code(conn, EMAIL, new_code) is not None


def test_expired_challenge_fails_generically(seeded, conn) -> None:
    _, code, _ = new(conn)
    conn.execute(
        "update login_challenges set expires_at = now() - interval '1 second' where email_norm = %s", (EMAIL,)
    )
    conn.commit()
    assert ch.verify_code(conn, EMAIL, code) is None


def test_double_consume_from_two_connections_has_one_winner(seeded, conn, settings_db) -> None:
    _, code, _ = new(conn)
    results = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        with connect(settings_db) as c:
            barrier.wait()
            results.append(ch.verify_code(c, EMAIL, code) is not None)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert sorted(results) == [False, True]


def test_link_peek_consumes_nothing_and_verify_consumes_once(seeded, conn) -> None:
    _, _, token = new(conn)
    assert ch.peek_link(conn, token) is not None
    assert ch.peek_link(conn, token) is not None  # a scanner hit leaves the link usable
    assert ch.verify_link(conn, token) is not None
    conn.commit()
    assert ch.verify_link(conn, token) is None and ch.peek_link(conn, token) is None


def test_unknown_token_is_nothing(seeded, conn) -> None:
    assert ch.peek_link(conn, "not-a-real-token") is None and ch.verify_link(conn, "not-a-real-token") is None


def test_signup_creates_one_case_and_identity(seeded, conn) -> None:
    fresh = normalize_email("new.person@example.in")
    row, _, _ = new(conn, email=fresh)
    case_id = ch.resolve_case(conn, row)
    conn.commit()
    assert case_id
    again = ch.identity_case(conn, fresh)
    assert again == case_id
    assert conn.execute("select state, consent_version from cases where id = %s", (case_id,)).fetchone() == {
        "state": "ONBOARDING",
        "consent_version": ch.CONSENT_VERSION,
    }


def test_parallel_signup_with_one_email_ends_in_one_case(seeded, conn, settings_db) -> None:
    fresh = normalize_email("parallel@example.in")
    row_a, _, _ = new(conn, email=fresh)
    row_b, _, _ = new(conn, email=fresh)  # second tab: supersedes the first challenge, both are "signup"
    ids = []
    barrier = threading.Barrier(2)

    def sign_up(row) -> None:
        with connect(settings_db) as c:
            barrier.wait()
            ids.append(ch.resolve_case(c, row))

    threads = [threading.Thread(target=sign_up, args=(r,)) for r in (row_a, row_b)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert len(ids) == 2 and ids[0] == ids[1] and ids[0] is not None
    n = conn.execute("select count(*) as n from identities where value_norm = %s", (fresh,)).fetchone()["n"]
    assert n == 1


def test_signin_resolves_to_its_case_and_admin_link_is_one_use(seeded, conn) -> None:
    case_id = seeded["meena"]
    row, code, _ = new(conn, purpose="signin", email=EMAIL, case_id=case_id)
    assert ch.resolve_case(conn, row) == case_id
    admin_row, admin_code, admin_token = ch.create_challenge(conn, EMAIL, "admin", "en", case_id=case_id)
    conn.commit()
    assert admin_code is None and admin_row["code_hash"] is None  # admin: link only
    assert ch.verify_code(conn, EMAIL, code) is not None  # an admin link never cancels the user's own code
    _, second_token = ch.create_challenge(conn, EMAIL, "admin", "en", case_id=case_id)[::2]
    conn.commit()
    assert ch.verify_link(conn, admin_token) is None  # the second issue superseded the first
    assert ch.verify_link(conn, second_token) is not None

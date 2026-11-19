"""Email sign-in pages (LLD signin §5): /signin, /signin/code, /signin/link, /logout.

Routes only. The rules live in app.signin (challenges, emailnorm, cookies, mail).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse

from app.access import (
    SESSION_COOKIE,
    SESSION_DAYS,
    check_csrf,
    client_ip,
    optional_case_id,
    rate_limit,
    sign_session,
)
from app.config import get_settings
from app.db import connect
from app.mouth.email import SmtpEmail
from app.routes.render import lang_for, render
from app.signin import challenges as ch
from app.signin import cookies
from app.signin.emailnorm import InvalidEmail, email_key, mask_email, normalize_email
from app.signin.mail import admin_link_email, code_email

log = logging.getLogger(__name__)
router = APIRouter()
SIGNIN_COOKIE = "sh_signin"
HONEYPOT_EMAIL = "bot@invalid.example"  # what a filled honeypot "signs in" as: nothing is created or sent
MSG = {
    "bad_token": {"en": "Please try again.", "hi": "कृपया फिर कोशिश करें।"},
    "wrong_code": {
        "en": "That code didn't work. Check the newest email, or ask for a new code.",
        "hi": "यह कोड सही नहीं है। सबसे नया ईमेल देखिए, या नया कोड माँगिए।",
    },
    "consent": {
        "en": "Please tick the box to agree before we create your account.",
        "hi": "खाता बनाने से पहले कृपया सहमति वाला बॉक्स चुनें।",
    },
    "bad_link": {
        "en": "This link isn't valid. Request a new one.",
        "hi": "यह लिंक मान्य नहीं है। नया लिंक माँगिए।",
    },
    "switch": {
        "en": "You are signed in as another person. Continuing switches to this one.",
        "hi": "आप किसी और के रूप में साइन इन हैं। आगे बढ़ने पर यह खाता खुलेगा।",
    },
}
CONSENT_TEXT = {
    "en": "I agree that Scheme Hunter keeps my details and papers to help with my applications. I can delete them at any time.",
    "hi": "मैं सहमत हूँ कि Scheme Hunter मेरी जानकारी और कागज़ात मेरे आवेदनों में मदद के लिए रखेगा। मैं इन्हें कभी भी हटवा सकता हूँ।",
}


def lang_of(request: Request) -> str:
    return lang_for(request)


def _page(request: Request, name: str, lang: str, status: int = 200, **ctx):
    return render(request, name, lang, None, status_code=status, consent_text=CONSENT_TEXT[lang], **ctx)


def _pending(request: Request) -> tuple[str, str] | None:
    return cookies.read_pending(request.cookies.get(SIGNIN_COOKIE))


def _limits_allow(conn, request: Request, ek: str, *, cooldown: bool) -> bool:
    """Every limit has a backstop. Denied requests look exactly like sent ones to the user."""
    s = get_settings()
    ip = client_ip(request)
    checks = [
        rate_limit(conn, f"signin:ip:{ip}", s.SIGNIN_EMAILS_PER_IP_PER_HOUR, 3600),
        rate_limit(conn, f"signin:email:{ek}", s.SIGNIN_EMAILS_PER_EMAIL_PER_HOUR, 3600),
        rate_limit(conn, "signin:day", s.SIGNIN_EMAILS_PER_DAY, 86400),
    ]
    if cooldown:
        checks.append(rate_limit(conn, f"signin:cooldown:{ek}", 1, s.SIGNIN_RESEND_COOLDOWN_S))
    return all(checks)


def _send_later(challenge_id, email: str, code: str | None, token: str, lang: str) -> None:
    """Runs after the response. The code and token exist only here, in memory."""
    try:
        subject, body = code_email(code, token, lang) if code else admin_link_email(token, lang)
        SmtpEmail.from_settings().send(email, body, subject=subject)
        error = None
    except Exception as exc:  # noqa: BLE001 - recorded for the curator; the user never sees it
        error = type(exc).__name__
        log.warning("sign-in email failed: %s", error)
    with connect() as conn:
        conn.execute(
            "update login_challenges set sent_at = case when %s::text is null then now() end, send_error = %s where id = %s",
            (error, error, challenge_id),
        )


def _request_code(
    request: Request, background: BackgroundTasks, email_norm: str, lang: str, *, cooldown: bool
):
    """Issue a challenge (if the limits allow) and send the code. Always the same redirect."""
    ek = email_key(email_norm)
    with connect() as conn:
        case_id = ch.identity_case(conn, email_norm)
        purpose = "signin" if case_id else "signup"
        if _limits_allow(conn, request, ek, cooldown=cooldown):
            row, code, token = ch.create_challenge(conn, email_norm, purpose, lang, case_id=case_id)
            background.add_task(_send_later, row["id"], email_norm, code, token, lang)
    resp = RedirectResponse("/signin/code", status_code=303)
    resp.set_cookie(
        SIGNIN_COOKIE,
        cookies.sign_pending(email_norm, purpose),
        max_age=15 * 60,
        httponly=True,
        secure=get_settings().APP_BASE_URL.startswith("https"),
        samesite="lax",
        path="/signin",
    )
    resp.background = background
    return resp


# ---------------------------------------------------------------- /signin


@router.get("/signin")
def signin_page(request: Request):
    if optional_case_id(request):
        return RedirectResponse("/", status_code=303)
    lang = lang_of(request)
    return _page(request, "signin.html", lang, token=cookies.form_token(), error=None, email="")


@router.post("/signin")
def signin_submit(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form()] = "",
    website: Annotated[str, Form()] = "",  # honeypot: real people never see or fill it
    form_token: Annotated[str, Form()] = "",
):
    lang = lang_of(request)
    if website:  # a bot: show the normal next page, but create and send nothing
        resp = RedirectResponse("/signin/code", status_code=303)
        resp.set_cookie(
            SIGNIN_COOKIE, cookies.sign_pending(HONEYPOT_EMAIL, "signin"), max_age=900, path="/signin"
        )
        return resp
    if not cookies.form_token_ok(form_token):
        return _page(
            request,
            "signin.html",
            lang,
            status=400,
            token=cookies.form_token(),
            error=MSG["bad_token"][lang],
            email="",
        )
    try:
        email_norm = normalize_email(email)
    except InvalidEmail as exc:
        return _page(
            request, "signin.html", lang, status=400, token=cookies.form_token(), error=str(exc), email=""
        )
    return _request_code(request, background, email_norm, lang, cooldown=True)


# ---------------------------------------------------------------- /signin/code


def _code_page(request: Request, pending: tuple[str, str], error: str | None = None, status: int = 200):
    email_norm, purpose = pending
    lang = lang_of(request)
    return _page(
        request,
        "signin_code.html",
        lang,
        status=status,
        masked=mask_email(email_norm),
        consent_needed=purpose == "signup",
        error=error,
    )


@router.get("/signin/code")
def code_page(request: Request):
    pending = _pending(request)
    if not pending:
        return RedirectResponse("/signin", status_code=303)
    return _code_page(request, pending)


@router.post("/signin/code")
def code_submit(
    request: Request,
    code: Annotated[str, Form()] = "",
    consent: Annotated[str | None, Form()] = None,
):
    pending = _pending(request)
    if not pending:
        return RedirectResponse("/signin", status_code=303)
    email_norm, purpose = pending
    lang = lang_of(request)
    if purpose == "signup" and not consent:  # checked before any attempt is used up
        return _code_page(request, pending, error=MSG["consent"][lang], status=400)
    with connect() as conn:
        allowed = rate_limit(conn, f"verify:ip:{client_ip(request)}", 30, 3600) and rate_limit(
            conn, f"verify:email:{email_key(email_norm)}", 10, 3600
        )
        challenge = ch.verify_code(conn, email_norm, code) if allowed else None
        case_id = ch.resolve_case(conn, challenge) if challenge else None
    if not case_id:
        return _code_page(request, pending, error=MSG["wrong_code"][lang], status=400)
    return _signed_in(case_id)


@router.post("/signin/resend")
def resend(request: Request, background: BackgroundTasks):
    pending = _pending(request)
    if not pending:
        return RedirectResponse("/signin", status_code=303)
    return _request_code(request, background, pending[0], lang_of(request), cooldown=True)


@router.get("/signin/start-over")
def start_over() -> RedirectResponse:
    resp = RedirectResponse("/signin", status_code=303)
    resp.delete_cookie(SIGNIN_COOKIE, path="/signin")
    return resp


# ---------------------------------------------------------------- /signin/link


@router.get("/signin/link")
def link_page(request: Request, t: str = ""):
    lang = lang_of(request)
    with connect() as conn:
        row = ch.peek_link(conn, t) if t else None
        target = None
        if row:
            target = str(row["case_id"]) if row["case_id"] else ch.identity_case(conn, row["email_norm"])
    if not row:
        return _page(
            request,
            "signin_link.html",
            lang,
            status=400,
            error=MSG["bad_link"][lang],
            token="",
            email="",
            consent_needed=False,
            switch=False,
        )
    current = optional_case_id(request)
    return _page(
        request,
        "signin_link.html",
        lang,
        error=None,
        token=t,
        email=mask_email(row["email_norm"]),
        consent_needed=row["purpose"] == "signup",
        switch=bool(current and target and current != target),
        switch_text=MSG["switch"][lang],
    )


@router.post("/signin/link")
def link_submit(
    request: Request, t: Annotated[str, Form()] = "", consent: Annotated[str | None, Form()] = None
):
    lang = lang_of(request)
    with connect() as conn:
        row = ch.peek_link(conn, t) if t else None
    if not row:
        return RedirectResponse("/signin", status_code=303)
    if row["purpose"] == "signup" and not consent:
        return _page(
            request,
            "signin_link.html",
            lang,
            status=400,
            error=MSG["consent"][lang],
            token=t,
            email=mask_email(row["email_norm"]),
            consent_needed=True,
            switch=False,
        )
    with connect() as conn:
        used = ch.verify_link(conn, t)
        case_id = ch.resolve_case(conn, used) if used else None
    if not case_id:
        return _page(
            request,
            "signin_link.html",
            lang,
            status=400,
            error=MSG["bad_link"][lang],
            token="",
            email="",
            consent_needed=False,
            switch=False,
        )
    return _signed_in(case_id)


# ---------------------------------------------------------------- session


def _signed_in(case_id: str) -> RedirectResponse:
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        sign_session(case_id),
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=get_settings().APP_BASE_URL.startswith("https"),
        samesite="lax",
    )
    resp.delete_cookie(SIGNIN_COOKIE, path="/signin")
    return resp


@router.post("/logout")
def logout(request: Request, csrf: Annotated[str | None, Form()] = None) -> RedirectResponse:
    case_id = optional_case_id(request)
    if case_id:
        check_csrf(case_id, request.headers.get("x-csrf-token") or csrf)
    resp = RedirectResponse("/signin", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp

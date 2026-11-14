"""Telegram adapter (no network: httpx MockTransport): send, send_document, errors, update parsing, secrets."""

import json

import httpx
import pytest

from app.access import bind_token, case_for_bind_token
from app.config import get_settings
from app.mouth import telegram
from app.mouth.base import SendError

TOKEN = "123456:SECRET-bot-token"


def channel(handler) -> telegram.TelegramChannel:
    return telegram.TelegramChannel(
        token=TOKEN, base="https://tg.test", transport=httpx.MockTransport(handler)
    )


def test_send_message_posts_to_the_bot_api() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["json"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    assert channel(handler).send("555", "नमस्ते") == "77"
    assert seen["url"] == f"https://tg.test/bot{TOKEN}/sendMessage" and seen["json"] == {
        "chat_id": "555",
        "text": "नमस्ते",
    }


def test_long_text_is_cut_to_the_telegram_limit() -> None:
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    channel(handler).send("1", "x" * 9000)
    assert len(sent["text"]) == telegram.MAX_TEXT


def test_send_document_uploads_from_memory() -> None:
    seen = {}

    def handler(request):
        seen["ct"], seen["body"] = request.headers["content-type"], request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    assert channel(handler).send_document("555", b"%PDF-1.4 pack", "pack.pdf", "your pack") == "9"
    assert (
        seen["ct"].startswith("multipart/form-data")
        and b"%PDF-1.4 pack" in seen["body"]
        and b"pack.pdf" in seen["body"]
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"ok": False, "description": "chat not found"}),
        httpx.Response(200, json={"ok": False}),
        httpx.Response(502, text="bad gateway"),
    ],
)
def test_provider_refusals_become_send_errors_without_leaking_the_token(response) -> None:
    with pytest.raises(SendError) as err:
        channel(lambda request: response).send("555", "hi")
    assert TOKEN not in str(err.value)


def test_network_failure_becomes_a_send_error_without_the_token() -> None:
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(SendError) as err:
        channel(boom).send("555", "hi")
    assert TOKEN not in str(err.value) and "ConnectError" in str(err.value)


def test_missing_token_is_a_send_error_and_repr_hides_it() -> None:
    with pytest.raises(SendError, match="not configured"):
        telegram.TelegramChannel(token="").send("1", "x")
    assert TOKEN not in repr(channel(lambda r: httpx.Response(200)))


@pytest.mark.parametrize(
    ("text", "kind", "extra"),
    [
        ("YES 4471", "decision", {"decision": "YES", "code": "4471"}),
        ("no 0042", "decision", {"decision": "NO", "code": "0042"}),
        ("  Yes   1234  ", "decision", {"decision": "YES", "code": "1234"}),
        ("हाँ 4471", "decision", {"decision": "YES", "code": "4471"}),
        ("नहीं 4471", "decision", {"decision": "NO", "code": "4471"}),
        ("/start abc123", "start", {"token": "abc123"}),
        ("/start@SchemeBot abc123", "start", {"token": "abc123"}),
        ("YES", "text", {}),
        ("YES 44712", "text", {}),
        ("done, number is 4471", "text", {}),
    ],
)
def test_parse_update_kinds(text, kind, extra) -> None:
    parsed = telegram.parse_update(
        {"update_id": 5, "message": {"text": text, "chat": {"id": 99, "type": "private"}}}
    )
    assert parsed["kind"] == kind and parsed["chat_id"] == "99" and parsed["external_id"] == "tg:5"
    assert {k: parsed[k] for k in extra} == extra


@pytest.mark.parametrize(
    "update",
    [
        {},
        {"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}}},  # photo/sticker: no text
        {
            "update_id": 2,
            "message": {"text": "hi", "chat": {"id": -100, "type": "group"}},
        },  # groups are ignored
        {"update_id": 3, "edited_message": {"text": "hi"}},
    ],
)
def test_parse_update_ignores_what_it_cannot_use(update) -> None:
    assert telegram.parse_update(update) == {"kind": "ignore"}


def test_verify_webhook_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret-hook")
    get_settings.cache_clear()
    assert telegram.verify_webhook("s3cret-hook")
    assert (
        not telegram.verify_webhook("wrong")
        and not telegram.verify_webhook(None)
        and not telegram.verify_webhook("")
    )
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    assert not telegram.verify_webhook("") and not telegram.verify_webhook(
        None
    )  # no secret configured = closed
    get_settings.cache_clear()


def test_bind_token_roundtrip_and_tamper_resistance() -> None:
    case_id = "7f08f2cb-4f73-5f0d-a257-1ff6bfb79f57"
    token = bind_token(case_id)
    assert case_for_bind_token(token) == case_id and len(token) <= 64  # fits Telegram's start parameter
    assert case_for_bind_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert case_for_bind_token("x" * 56) is None and case_for_bind_token("") is None
    other = bind_token("bd744c0b-5682-5f8d-847f-6f4348815641")
    assert (
        case_for_bind_token(token[:32] + other[32:]) is None
    )  # a signature for another case doesn't transfer

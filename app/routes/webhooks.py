"""POST /webhooks/telegram (verifies X-Telegram-Bot-Api-Secret-Token). Slice 7."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/webhooks/telegram")
def telegram_webhook() -> None:
    raise NotImplementedError("slice 7")

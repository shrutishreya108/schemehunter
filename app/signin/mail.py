"""Sign-in emails (plain text, hi and en). No case details: only the code or the link. LLD signin §7."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import get_settings

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=False)


def link_for(token: str) -> str:
    return f"{get_settings().APP_BASE_URL.rstrip('/')}/signin/link?t={token}"


def code_email(code: str, token: str, lang: str) -> tuple[str, str]:
    subject = f"आपका Scheme Hunter कोड: {code}" if lang == "hi" else f"Your Scheme Hunter code: {code}"
    body = env.get_template("email/login_code.txt").render(lang=lang, code=code, link=link_for(token))
    return subject, body


def admin_link_email(token: str, lang: str) -> tuple[str, str]:
    subject = "Your Scheme Hunter sign-in link"
    body = env.get_template("email/admin_link.txt").render(lang=lang, link=link_for(token))
    return subject, body

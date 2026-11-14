"""FastAPI app: routers, startup migrations, /healthz."""

from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes import admin, approvals, cases, chat, contact, documents, pages, webhooks

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.DATABASE_URL and settings.RUN_MIGRATIONS_ON_STARTUP:
        from app.db import run_migrations

        applied = run_migrations()
        log.info("migrations", applied=applied)
    yield


app = FastAPI(title="Scheme Hunter", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
for module in (pages, chat, contact, documents, approvals, webhooks, cases, admin):
    app.include_router(module.router)


@app.exception_handler(HTTPException)
async def friendly_http_errors(request: Request, exc: HTTPException):
    """Browsers get a readable page for 401/403/404 on user pages; APIs and /admin keep the default."""
    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and exc.status_code in (401, 403, 404) and not request.url.path.startswith("/admin"):
        from app.routes.render import render

        return render(request, "error.html", "hi", message=str(exc.detail), status_code=exc.status_code)
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def friendly_server_error(request: Request, exc: Exception):
    """A crash is never shown to a user as a stack trace: "I'll get back to you shortly" (LLD §19)."""
    log.exception("unhandled error on %s", request.url.path)
    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and not request.url.path.startswith("/admin"):
        from app.routes.render import render

        return render(request, "error.html", "hi", status_code=500)
    return JSONResponse({"error": "something went wrong"}, status_code=500)


@app.exception_handler(NotImplementedError)
async def not_built_yet(_: Request, exc: NotImplementedError) -> JSONResponse:
    return JSONResponse({"error": "not built yet", "slice": str(exc)}, status_code=501)


@app.get("/healthz")
def healthz() -> dict:
    """Liveness plus a DB ping when DATABASE_URL is configured."""
    settings = get_settings()
    if not settings.DATABASE_URL:
        return {"ok": True, "db": "not configured"}
    from app.db import connect

    try:
        with connect() as conn:
            conn.execute("select 1")
        return {"ok": True, "db": "ok"}
    except psycopg.Error as exc:  # report, don't crash the probe
        return {"ok": False, "db": type(exc).__name__}

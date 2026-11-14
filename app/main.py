"""FastAPI app: the WhatsApp webhook, a health check and the admin page.

Run:  uvicorn app.main:app --host 127.0.0.1 --port 8000
Keep exactly ONE worker process: the queue workers and timers run inside it.
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import admin, config, db, intake, jobs, ops, scheduler, site, sitepage, talk, wa_meta, wa_twilio, worker

log = logging.getLogger("app")
MAX_BODY = 1_000_000  # bytes; a real WhatsApp webhook is a few KB


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()
    for problem in config.problems():
        log.warning("config: %s", problem)
    stop = threading.Event()
    sched = None
    if config.ENABLE_WORKER:
        worker.start(stop)
    if config.ENABLE_SCHEDULER:
        sched = scheduler.start()
    log.info("started (provider=%s, model=%s)", config.WHATSAPP_PROVIDER, config.CLAUDE_MODEL)
    yield
    stop.set()
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin.router)
app.include_router(site.router)
app.include_router(talk.router)
if (_static := site.static_files()) is not None:
    app.mount("/static-site", _static, name="static-site")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in sitepage.security_headers(request.url.path, request.url.scheme == "https").items():
        response.headers[name] = value
    response.headers.setdefault("Content-Security-Policy", sitepage.DEFAULT_CSP)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """A missing page shows the branded 404 to browsers; scripts and the admin keep the plain answer."""
    if (exc.status_code == 404 and not request.url.path.startswith("/admin")
            and sitepage.wants_html(request.headers.get("accept", ""))):
        page = sitepage.not_found()
        if page is not None:
            return site.html_response(page, 404, cache="no-store")
    return await http_exception_handler(request, exc)


@app.get("/health")
def health():
    """200 while the queue moves; 503 when jobs sit waiting for over 10 minutes (point your uptime monitor here)."""
    stuck = [p for p in ops.problems() if "waiting more than" in p]
    return JSONResponse({"ok": not stuck, "queue": jobs.counts(), "problems": stuck}, status_code=503 if stuck else 200)


@app.get("/webhook")
def verify(request: Request):
    """Meta calls this once, when you press 'Verify and save' in the dashboard."""
    q = request.query_params
    token = q.get("hub.verify_token", "")
    if (q.get("hub.mode") == "subscribe" and config.META_VERIFY_TOKEN
            and hmac.compare_digest(token.encode(), config.META_VERIFY_TOKEN.encode())):
        return PlainTextResponse(q.get("hub.challenge", ""))
    return PlainTextResponse("forbidden", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    """Receive messages. Answer 200 fast: the real work happens in the queue workers."""
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return PlainTextResponse("too large", status_code=413)

    if config.WHATSAPP_PROVIDER == "meta":
        if not wa_meta.verify_signature(raw, request.headers.get("x-hub-signature-256", "")):
            log.warning("webhook: bad or missing signature")
            return PlainTextResponse("forbidden", status_code=403)
        try:
            incoming = wa_meta.parse_payload(json.loads(raw))
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
    elif config.WHATSAPP_PROVIDER == "twilio":
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        if not config.PUBLIC_BASE_URL or not wa_twilio.validate(
                config.PUBLIC_BASE_URL.rstrip("/") + "/webhook", params,
                request.headers.get("x-twilio-signature", "")):
            log.warning("webhook: bad Twilio signature, or PUBLIC_BASE_URL is not set (unsigned messages are refused)")
            return PlainTextResponse("forbidden", status_code=403)
        incoming = wa_twilio.parse_form(params)
    else:
        return JSONResponse({"error": "provider not configured"}, status_code=500)

    await run_in_threadpool(intake.intake, incoming)
    return {"ok": True}

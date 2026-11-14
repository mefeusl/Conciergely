"""Routes for the personal voice-setup page (see voicechat.py)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, voicechat
from .site import html_response

router = APIRouter()


@router.get("/talk/{token}", response_class=HTMLResponse)
def talk_page(token: str):
    if not voicechat.enabled():
        return html_response(voicechat.gone_page(), 404, "no-store")
    client = voicechat.client_for(token)
    if not client:
        return html_response(voicechat.gone_page(), 404, "no-store")
    return html_response(voicechat.page(client), 200, "no-store")


@router.post("/talk/{token}/turn")
async def talk_turn(token: str, request: Request):
    if not voicechat.enabled():
        return JSONResponse({"ok": False, "error": "gone"}, status_code=404)
    kind = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    try:
        if int(request.headers.get("content-length") or 0) > config.VOICE_MAX_AUDIO:
            return JSONResponse({"ok": False, "error": "error"}, status_code=413)
    except ValueError:
        return JSONResponse({"ok": False, "error": "error"}, status_code=400)
    body = await request.body()
    if kind == "application/json":
        try:
            text = str((json.loads(body) or {}).get("text") or "")
        except ValueError:
            return JSONResponse({"ok": False, "error": "error"}, status_code=400)
        result = await run_in_threadpool(voicechat.turn, token, None, "", text)
    elif kind.startswith("audio/"):
        result = await run_in_threadpool(voicechat.turn, token, body, kind, "")
    else:
        return JSONResponse({"ok": False, "error": "error"}, status_code=415)
    return JSONResponse(result, status_code=200 if result.get("ok") else (404 if result.get("error") == "gone" else 429 if result.get("error") == "limit" else 400),
                        headers={"Cache-Control": "no-store"})

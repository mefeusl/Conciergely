"""Admin page routes (/admin). Password protected (HTTP Basic), CSRF-checked, rate-limited."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import adminlogic, config, pages, security

router = APIRouter(prefix="/admin")
limiter = security.FailureLimiter()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def guard(request: Request) -> None:
    """Runs before every admin route: disabled without a password, throttled after repeated failures."""
    if not config.ADMIN_PASSWORD:
        raise HTTPException(status_code=404)
    key = _client_key(request)
    if limiter.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
    if not security.check_admin_credentials(request.headers.get("authorization")):
        limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Login required",
                            headers={"WWW-Authenticate": 'Basic realm="admin", charset="UTF-8"'})
    limiter.reset(key)
    if request.method == "POST" and not security.origin_ok(request.headers.get("origin"),
                                                           request.headers.get("host")):
        raise HTTPException(status_code=403, detail="Cross-site request blocked")


def _csrf(token: str) -> None:
    if not security.csrf_ok(token):
        raise HTTPException(status_code=403, detail="Invalid form token, reload the page")


def _page(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(pages.render(name, bot=config.BOT_NAME, csrf=security.csrf_token(), **ctx))


def _home() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=303)


@router.get("", response_class=HTMLResponse, dependencies=[Depends(guard)])
def dashboard():
    return _page("dashboard", **adminlogic.dashboard())


@router.post("/clients", dependencies=[Depends(guard)])
def add_client(phone: str = Form(...), name: str = Form(""), plan: str = Form("basic"), csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.add_client(phone, name, plan)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return _home()


@router.get("/referrals", response_class=HTMLResponse, dependencies=[Depends(guard)])
def referrals_view():
    return _page("referrals", **adminlogic.referrals_page())


@router.get("/settings", response_class=HTMLResponse, dependencies=[Depends(guard)])
def settings_view():
    return _page("settings", **adminlogic.settings_page())


@router.post("/settings/contact", dependencies=[Depends(guard)])
def settings_contact(on: str = Form(""), number: str = Form(""), alert: str = Form(""), csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.save_contact(on == "1", number, alert)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/settings/calling", dependencies=[Depends(guard)])
def settings_calling(on: str = Form(""), provider: str = Form("test"), url: str = Form(""), key: str = Form(""),
                     csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.save_calling(on == "1", provider, url, key)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/settings/backup", dependencies=[Depends(guard)])
def settings_backup(csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.run_backup()
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/settings/rewrap", dependencies=[Depends(guard)])
def settings_rewrap(csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.rewrap_mail()
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/upgrades/{request_id}/done", dependencies=[Depends(guard)])
def upgrade_done(request_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.handle_upgrade(request_id)
    return _home()


@router.post("/clients/{client_id}/paid", dependencies=[Depends(guard)])
def client_paid(client_id: int, plan: str = Form(...), months: int = Form(1), amount: str = Form(""), csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.mark_paid(client_id, plan, months, amount)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/free", dependencies=[Depends(guard)])
def client_free(client_id: int, on: str = Form(""), csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.set_free(client_id, on == "1")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/payments/{payment_id}/approve", dependencies=[Depends(guard)])
def payment_approve(payment_id: int, csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.approve_payment(payment_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return _home()


@router.post("/payments/{payment_id}/reject", dependencies=[Depends(guard)])
def payment_reject(payment_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.reject_payment(payment_id)
    return _home()


@router.post("/clients/{client_id}/calls", dependencies=[Depends(guard)])
def client_calls(client_id: int, on: str = Form(""), csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.set_calls(client_id, on == "1")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/mail/connect", dependencies=[Depends(guard)])
def mail_connect(client_id: int, address: str = Form(""), password: str = Form(""), folder: str = Form("INBOX"),
                 senders: str = Form(""), csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.connect_mail(client_id, address, password, folder, senders)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/mail/scope", dependencies=[Depends(guard)])
def mail_scope(client_id: int, folder: str = Form("INBOX"), senders: str = Form(""), csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.save_mail_scope(client_id, folder, senders)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/mail/disconnect", dependencies=[Depends(guard)])
def mail_disconnect(client_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.disconnect_mail(client_id)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.get("/clients/{client_id}", response_class=HTMLResponse, dependencies=[Depends(guard)])
def client_detail(client_id: int):
    ctx = adminlogic.detail(client_id)
    if ctx is None:
        raise HTTPException(status_code=404)
    return _page("detail", **ctx)


@router.post("/clients/{client_id}/toggle", dependencies=[Depends(guard)])
def toggle(client_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.toggle(client_id)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/plan", dependencies=[Depends(guard)])
def set_plan(client_id: int, plan: str = Form(...), briefing_time: str = Form(""), work_area: str = Form(""),
             csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.set_plan(client_id, plan, briefing_time, work_area)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/trial", dependencies=[Depends(guard)])
def trial(client_id: int, days: int = Form(...), csrf: str = Form("")):
    _csrf(csrf)
    ok, message = adminlogic.set_trial(client_id, days)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/freeze", dependencies=[Depends(guard)])
def freeze(client_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.set_frozen(client_id, True)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/unfreeze", dependencies=[Depends(guard)])
def unfreeze(client_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.set_frozen(client_id, False)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/ping", dependencies=[Depends(guard)])
def ping(client_id: int, csrf: str = Form("")):
    _csrf(csrf)
    if not adminlogic.ping(client_id):
        raise HTTPException(status_code=400, detail="Client is paused or missing")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/clear-memory", dependencies=[Depends(guard)])
def clear_memory(client_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.clear_memory(client_id)
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/delete", dependencies=[Depends(guard)])
def delete_client(client_id: int, confirm: str = Form(""), csrf: str = Form("")):
    _csrf(csrf)
    if not adminlogic.delete_client(client_id, confirm):
        raise HTTPException(status_code=400, detail="The last 4 digits did not match, nothing was deleted")
    return _home()


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(guard)])
def retry_job(job_id: int, csrf: str = Form("")):
    _csrf(csrf)
    adminlogic.retry_job(job_id)
    return _home()

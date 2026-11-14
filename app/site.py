"""Routes for the public site: '/', robots.txt, sitemap.xml, the favicon and the files under /static-site/.

Only site/static/ is served as plain files. The pages themselves are filled in by sitepage.py first.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import sitepage

router = APIRouter()


def html_response(page: sitepage.Page, status: int = 200, cache: str = "public, max-age=300") -> HTMLResponse:
    return HTMLResponse(page.html, status_code=status,
                        headers={"Cache-Control": cache, "Content-Security-Policy": page.csp})


@router.get("/", response_class=HTMLResponse)
def landing():
    page = sitepage.landing()
    if page is None:
        return PlainTextResponse("ok")
    return html_response(page)


@router.get("/privacy", response_class=HTMLResponse)
@router.get("/gizlilik", response_class=HTMLResponse, include_in_schema=False)
def privacy():
    page = sitepage.privacy()
    if page is None:
        return PlainTextResponse("not found", status_code=404)
    return html_response(page)


@router.get("/terms", response_class=HTMLResponse)
@router.get("/sartlar", response_class=HTMLResponse, include_in_schema=False)
def terms():
    page = sitepage.terms()
    if page is None:
        return PlainTextResponse("not found", status_code=404)
    return html_response(page)


@router.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse(sitepage.robots_txt(), headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    xml = sitepage.sitemap_xml()
    if xml is None:
        return PlainTextResponse("not found", status_code=404)
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers ask for /favicon.ico on their own; point them at the SVG."""
    return RedirectResponse("/static-site/favicon.svg", status_code=301)


def static_files() -> StaticFiles | None:
    folder = sitepage.STATIC_DIR
    return StaticFiles(directory=str(folder)) if folder.is_dir() else None

"""Built-in web control surface -- ``GET /ui``.

A dependency-free page for watching and steering the sessions registered with
this bridge: a task board that joins each orchestrator with the venue workers
it supervises (``venue.supervisor_ref``), a session viewer that folds tool
calls into collapsed work blocks, a sessions table, and the ACP agent/session
URLs for an external ACP client such as acp-ui.

The page, script, and stylesheet ship as package data (``ui_static/``) and are
served auth-exempt from a fixed allowlist -- never a path taken from the
request. They hold no data: the page signs in with a one-time login code
(``agent-bridge ui``), keeps the bearer token in ``localStorage``, and calls
the existing token-protected ``/api/v1`` routes. Event content is rendered as
text only, under a Content-Security-Policy that allows same-origin script and
style files and no inline code.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from functools import cache
from importlib import resources
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import ui_tasks

router = APIRouter()
# The UI's task verbs (workspaces / start / resume) ride on this router so the
# app wires the whole control surface with one include.
router.include_router(ui_tasks.router)

#: Same-origin script/style files only (no inline code); data calls only to
#: this origin; no framing, forms, or base-URL changes.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)

#: The only files ``/ui/assets/{name}`` serves, with their content types.
ASSETS: dict[str, str] = {
    "app.js": "text/javascript; charset=utf-8",
    "viewer.js": "text/javascript; charset=utf-8",
    "model.js": "text/javascript; charset=utf-8",
    "dom.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@cache
def _asset(name: str) -> tuple[bytes, str]:
    """Return (bytes, etag) for a packaged ``ui_static`` file."""
    data = resources.files("agent_bridge").joinpath("ui_static", name).read_bytes()
    return data, '"' + hashlib.sha256(data).hexdigest()[:20] + '"'


def _serve(name: str, media_type: str, request: Request) -> Response:
    data, etag = _asset(name)
    # no-cache: revalidate every load so a daemon upgrade is picked up at once.
    headers = {**_SECURITY_HEADERS, "ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=media_type, headers=headers)

#: A login code is single-use and valid this long (seconds).
LOGIN_CODE_TTL = 60.0


def _login_codes(request: Request) -> dict[str, float]:
    codes = getattr(request.app.state, "ui_login_codes", None)
    if codes is None:
        codes = request.app.state.ui_login_codes = {}
    now = time.monotonic()
    for code in [c for c, expiry in codes.items() if expiry <= now]:
        codes.pop(code, None)
    return codes


@router.post("/api/v1/ui/login-codes", include_in_schema=False)
async def create_login_code(request: Request) -> dict[str, Any]:
    """Mint a one-time code that lets a browser sign in to /ui (auth required).

    The URL a browser records carries only this code, never the bearer token:
    history (which may sync) keeps a code that is already used or expired.
    """
    code = secrets.token_urlsafe(24)
    _login_codes(request)[code] = time.monotonic() + LOGIN_CODE_TTL
    return {"code": code, "expires_in": LOGIN_CODE_TTL}


@router.post("/ui/exchange", include_in_schema=False)
async def exchange_login_code(request: Request) -> JSONResponse:
    """Trade a valid, unused login code for the bearer token (auth-exempt; single use)."""
    try:
        code = str((await request.json()).get("code") or "")
    except ValueError:
        code = ""
    if not code or _login_codes(request).pop(code, None) is None:
        return JSONResponse({"detail": "invalid or expired login code"}, status_code=403)
    return JSONResponse(
        {"token": request.app.state.auth_token}, headers={"Cache-Control": "no-store"},
    )

@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def status_ui(request: Request) -> Response:
    """Serve the control-surface page."""
    return _serve("index.html", "text/html; charset=utf-8", request)


@router.get("/ui/assets/{name}", include_in_schema=False)
async def ui_asset(name: str, request: Request) -> Response:
    """Serve one allowlisted script or stylesheet (auth-exempt; static)."""
    media_type = ASSETS.get(name)
    if media_type is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return _serve(name, media_type, request)

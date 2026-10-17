"""Private quiescent authentication and one-time login-code continuity."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fastapi import FastAPI
from zdd.private_protocol import PrivateIPCError, Refusal

from .auth import BearerAuthMiddleware
from .auth_custody import AuthCustody, AuthCustodyMiddleware
from .resolver_runtime import _encode


def _codes(value: object) -> dict[str, float]:
    if type(value) is not dict or len(value) > 256:
        raise PrivateIPCError(Refusal.AUTHORITY)
    if any(
        type(code) is not str or not code or len(code) > 256 or "\0" in code
        or type(expiry) is not float or not math.isfinite(expiry) or expiry < 0
        for code, expiry in value.items()
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    copied = dict(value)
    if len(_encode(copied)) > 65536:
        raise PrivateIPCError(Refusal.SIZE)
    return copied


def _state(app: FastAPI) -> dict[str, object]:
    codes = getattr(app.state, "ui_login_codes", ...)
    if codes is ...:
        return {"schema": 1, "codes_state": "absent", "codes": None}
    if codes is None:
        return {"schema": 1, "codes_state": "none", "codes": None}
    return {"schema": 1, "codes_state": "present", "codes": _codes(codes)}


def verify_auth_consumers(app: FastAPI, custody: AuthCustody, token: str) -> None:
    """Require the actual constructed HTTP stack and WebSocket/UI state token."""
    if (
        type(custody) is not AuthCustody or type(token) is not str or not token or "\0" in token
        or getattr(app.state, "auth_custody", None) is not custody
        or getattr(app.state, "auth_token", None) != token
        or len(app.user_middleware) != 2
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    outer, auth = app.user_middleware
    if (
        outer.cls is not AuthCustodyMiddleware or outer.args
        or outer.kwargs != {"custody": custody}
        or auth.cls is not BearerAuthMiddleware or auth.args or auth.kwargs != {"token": token}
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    current = app.middleware_stack
    reference = FastAPI.build_middleware_stack(app)
    for _ in range(16):
        if type(current) is not type(reference):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if reference is app.router:
            if current is not app.router:
                raise PrivateIPCError(Refusal.AUTHORITY)
            return
        if type(current) is AuthCustodyMiddleware and current.custody is not custody:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if type(current) is BearerAuthMiddleware and current._token != token:
            raise PrivateIPCError(Refusal.AUTHORITY)
        current = current.app
        reference = reference.app
    raise PrivateIPCError(Refusal.AUTHORITY)


def capture_authentication(app: FastAPI) -> dict[str, object]:
    """Snapshot only actual, already constructed and quiescent consumers."""
    try:
        custody = app.state.auth_custody
        if type(custody) is not AuthCustody:
            raise PrivateIPCError(Refusal.AUTHORITY)
        with custody.quiescent():
            verify_auth_consumers(app, custody, app.state.auth_token)
            return _state(app)
    except (AttributeError, TypeError, ValueError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


@dataclass(frozen=True)
class RestoredAuthentication:
    """Detached code storage with fresh ownership, never renewed expiries."""

    custody: AuthCustody = field(repr=False)
    codes_state: str
    codes: dict[str, float] | None = field(repr=False)
    _wire: bytes = field(repr=False)

    def private_payload(self) -> dict[str, object]:
        with self.custody.quiescent():
            value = {
                "schema": 1, "codes_state": self.codes_state,
                "codes": _codes(self.codes) if self.codes_state == "present" else None,
            }
            if _encode(value) != self._wire or (
                self.codes_state != "present" and self.codes is not None
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            return value

    def bind(self, app: FastAPI) -> None:
        self.private_payload()
        app.state.auth_custody = self.custody
        if self.codes_state != "absent":
            app.state.ui_login_codes = self.codes

    def verify(self, app: FastAPI) -> None:
        try:
            with self.custody.quiescent():
                self.private_payload()
                if (
                    getattr(app.state, "auth_custody", None) is not self.custody
                    or (self.codes_state == "present"
                        and getattr(app.state, "ui_login_codes", None) is not self.codes)
                    or _encode(_state(app)) != self._wire
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
        except (AttributeError, TypeError, ValueError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None


def restore_authentication(value: object) -> RestoredAuthentication:
    """Reject partial or coerced code state without fetching any credential."""
    if (
        type(value) is not dict or set(value) != {"schema", "codes_state", "codes"}
        or type(value["schema"]) is not int or value["schema"] != 1
        or type(value["codes_state"]) is not str
        or value["codes_state"] not in ("absent", "none", "present")
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    codes = _codes(value["codes"]) if value["codes_state"] == "present" else None
    if codes is None and value["codes"] is not None:
        raise PrivateIPCError(Refusal.AUTHORITY)
    return RestoredAuthentication(AuthCustody(), value["codes_state"], codes, _encode(value))

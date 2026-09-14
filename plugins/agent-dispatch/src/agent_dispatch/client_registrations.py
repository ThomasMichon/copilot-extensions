"""Supervisor-registration methods for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap; a
pure mechanical extraction with no behavior change. Mixed into
``DispatchClient`` alongside its other method groups.
"""

from __future__ import annotations


class RegistrationClientMixin:
    """Supervisor-registration HTTP calls, mixed into ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    def register_registration(
        self,
        kind: str,
        spec: dict,
        *,
        reg_id: str | None = None,
        machine: str | None = None,
        env: str = "default",
    ) -> dict:
        body = {
            "kind": kind,
            "spec": spec,
            "id": reg_id,
            "machine": machine,
            "env": env,
        }
        return self._unwrap(self._http.post("/registrations", json=body))

    def list_registrations(
        self,
        *,
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[dict]:
        params: dict[str, object] = {"include_paused": include_paused}
        if kind is not None:
            params["kind"] = kind
        if machine is not None:
            params["machine"] = machine
        if env is not None:
            params["env"] = env
        return self._unwrap(self._http.get("/registrations", params=params))

    def get_registration(self, rid: str) -> dict:
        return self._unwrap(self._http.get(f"/registrations/{rid}"))

    def remove_registration(self, rid: str) -> dict:
        return self._unwrap(self._http.delete(f"/registrations/{rid}"))

    def set_registration_status(self, rid: str, status: str) -> dict:
        return self._unwrap(
            self._http.post(f"/registrations/{rid}/status", json={"status": status})
        )

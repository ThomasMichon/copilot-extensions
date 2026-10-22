"""``fail_spawn``/``settle_spawn`` for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_suspend`) rather than grow an
already-baselined file.
"""

from __future__ import annotations


class SpawnTerminalClientMixin:
    """``fail_spawn``/``settle_spawn``/``defer_spawn``, mixed into ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    def defer_spawn(
        self, key: str, *, detail: str | None = None,
        expected_generation: int | None = None,
        startup_revision: int | None = None, cooling_token: str | None = None,
    ) -> dict:
        """Defer startup; generation fencing is for unclaimed restricted attempts only."""
        payload: dict[str, object] = {"detail": detail}
        if expected_generation is not None:
            payload["expected_generation"] = expected_generation
        if startup_revision is not None:
            payload["startup_revision"] = startup_revision
        if cooling_token is not None:
            payload["cooling_token"] = cooling_token
        return self._unwrap(self._http.post(f"/spawn-reservations/{key}/defer", json=payload))

    def claim_spawn_cooling(
        self, key: str, *, startup_revision: int, session_handle: str, state: str,
    ) -> dict:
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/claim-cooling",
            json={
                "startup_revision": startup_revision,
                "session_handle": session_handle, "state": state,
            },
        ))

    def fail_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        force: bool = False,
        confirmed_absent: bool = False,
        release_requested: bool = False,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/fail",
                json={
                    "detail": detail,
                    "conclusion_state": conclusion_state,
                    "conclusion_detail": conclusion_detail,
                    "claim_token": claim_token,
                    "force": force,
                    "confirmed_absent": confirmed_absent,
                    "release_requested": release_requested,
                },
            )
        )

    def settle_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        release_requested: bool = False,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/settle",
                json={
                    "detail": detail,
                    "conclusion_state": conclusion_state,
                    "conclusion_detail": conclusion_detail,
                    "claim_token": claim_token,
                    "release_requested": release_requested,
                },
            )
        )

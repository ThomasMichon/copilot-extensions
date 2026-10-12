"""``fail_spawn``/``settle_spawn`` for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_suspend`) rather than grow an
already-baselined file.
"""

from __future__ import annotations


class SpawnTerminalClientMixin:
    """``fail_spawn``/``settle_spawn``, mixed into ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

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

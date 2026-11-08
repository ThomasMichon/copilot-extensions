"""HTTP client surface for exact housekeeping recovery."""


class CleanupRecoveryClientMixin:
    def record_spawn_conclusion(
        self, key: str, *, conclusion_state: str, conclusion_detail: str,
        detail: str | None = None, claim_token: str | None = None,
        claimant_machine: str | None = None,
    ) -> dict:
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/conclusion",
            json={"conclusion_state": conclusion_state, "conclusion_detail": conclusion_detail,
                  "detail": detail, "claim_token": claim_token,
                  **({"claimant_machine": claimant_machine} if claimant_machine is not None else {})},
        ))

    def claim_spawn_conclusion_retry(self, key: str, *, claimant_machine: str | None = None) -> dict:
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/conclusion/claim",
            **({"json": {"claimant_machine": claimant_machine}} if claimant_machine is not None else {}),
        ))

    def validate_spawn_conclusion_claim(
        self, key: str, claim_token: str, *, claimant_machine: str | None = None,
    ) -> dict:
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/conclusion/validate",
            json={"claim_token": claim_token,
                  **({"claimant_machine": claimant_machine} if claimant_machine is not None else {})},
        ))

    def recover_held_cleanup(
        self, key: str, *, expected_generation: int, current_host: str,
        apply: bool = False, expected_preview: str | None = None,
        reason: str | None = None, request_id: str | None = None,
    ) -> dict:
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/conclusion/recover",
            headers=self._control_headers(),
            json={"expected_generation": expected_generation, "current_host": current_host,
                  "apply": apply, "expected_preview": expected_preview,
                  "reason": reason, "request_id": request_id},
        ))

    def cleanup_recovery_history(self, key: str) -> list[dict]:
        return self._unwrap(self._http.get(
            f"/spawn-reservations/{key}/conclusion/recoveries",
            headers=self._control_headers(),
        ))


class HostBoundConclusionClient:
    """Carry the supervisor's resolved identity, never guess an execution host."""

    def __init__(self, client, machine):
        self._client = client
        self._machine = machine

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _bound_arguments(self, kwargs):
        claimant = kwargs.get("claimant_machine")
        if claimant is not None and claimant != self._machine:
            raise ValueError("claimant machine cannot override the bound supervisor identity")
        return {**kwargs, "claimant_machine": self._machine}

    def claim_spawn_conclusion_retry(self, key, **kwargs):
        return self._client.claim_spawn_conclusion_retry(key, **self._bound_arguments(kwargs))

    def validate_spawn_conclusion_claim(self, key, token, **kwargs):
        return self._client.validate_spawn_conclusion_claim(
            key, token, **self._bound_arguments(kwargs),
        )

    def record_spawn_conclusion(self, key, **kwargs):
        return self._client.record_spawn_conclusion(key, **self._bound_arguments(kwargs))

"""HTTP client surface for exact housekeeping recovery."""


class CleanupRecoveryClientMixin:
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

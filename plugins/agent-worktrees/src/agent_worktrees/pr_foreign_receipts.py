"""Unclaimed foreign-create attempts, fenced independently of checkout PRs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import config as cfg, pr_publish, record_machine_identity, tracking
from .providers.base import ProviderError, PullResult
from .receiving_allocation import serialized_receipt


def failure(detail: str, pull: PullResult | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False, "claimed": False, "pr_identity_verified": False,
        "publication_state": "unknown", "state": "unknown",
    }
    known = ""
    if pull is not None:
        result.update(number=pull.number, url=pull.url, pr_opened=True)
        known = f" Retained unverified PR #{pull.number} ({pull.url!r})."
    result["error"] = (
        detail + " Foreign creation outcome is unknown." + known
        + " Reconcile the retained receipt before retrying; do not force a new PR."
    )
    return result


class Attempt:
    def __init__(
        self, config: cfg.Config, path: Path, record: tracking.WorktreeRecord | None,
        *, provider: str, endpoint: str, target: str, source: str, branch: str,
    ) -> None:
        self.config, self.owner_path, self.record = config, path, record
        self.binding = {
            "provider": provider, "endpoint": endpoint.casefold(),
            "target": target.casefold(), "source": source.casefold(), "branch": branch,
        }
        key = hashlib.sha256(json.dumps(self.binding, sort_keys=True).encode()).hexdigest()
        self.path = path.with_suffix(".foreign-pr-receipts") / (key + ".json")
        self.expected = self._read()

    def _read(self) -> dict[str, Any] | None:
        if self.path.is_symlink() or self.path.parent.resolve() != self.path.parent:
            raise ProviderError("Foreign creation receipt path is redirected.")
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
                or value.get("binding") != self.binding
                or value.get("phase") not in ("publishing", "unknown", "verified")
                or type(value.get("revision")) is not int or value["revision"] < 1
                or not isinstance(value.get("owner"), dict)
                or not isinstance(value.get("history"), list)
                or (value.get("provider_number") is not None and (
                    type(value["provider_number"]) is not int or value["provider_number"] <= 0
                ))
                or not isinstance(value.get("provider_url", ""), str)
            ):
                raise ValueError
            return value
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ProviderError("Foreign creation receipt is unavailable; reconcile it before publication.") from exc

    def pending(self) -> dict[str, Any] | None:
        if self.expected is None:
            return None
        value = self.expected
        if value["phase"] == "verified" and self.record is not None and value["owner"] == self._owner(self.record):
            return None
        number = value.get("provider_number")
        return failure("A prior foreign creation is still unverified.", (
            PullResult(number=number, url=value.get("provider_url", "")) if number else None
        ))

    def _owner(self, record: tracking.WorktreeRecord) -> dict[str, Any]:
        return {
            "subject": list(record_machine_identity.subject_fence(record, self.owner_path)),
            "nonce": record.creation_nonce, "allocation": serialized_receipt(record),
        }

    def require_owner(self, current: tracking.WorktreeRecord) -> None:
        if (
            self.record is None or self._owner(current) != self._owner(self.record)
            or current.status in ("finalizing", "finalized", "archived")
        ):
            raise ProviderError("Foreign creation owner changed; requalify the retained attempt.")

    def _write(self, value: dict[str, Any]) -> None:
        with pr_publish.metadata_lock(self.owner_path.stem, project=self.config.repo_name), tracking._RecordLock(
            self.owner_path, require_sidecar=True,
        ):
            if not self.owner_path.is_file():
                raise ProviderError("Foreign creation needs an existing managed owner.")
            self.require_owner(tracking.load_record(self.owner_path))
            if self._read() != self.expected:
                raise ProviderError("Foreign creation receipt revision changed; reconcile before retrying.")
            raw = json.dumps(value, sort_keys=True)
            if len(raw.encode("utf-8")) > 65536:
                raise ProviderError("Foreign creation receipt history is full; reconcile before publication.")
            tracking._atomic_write(self.path, raw)
            self.expected = value

    def reserve(self, base: str, expected_head: str | None) -> None:
        if self.record is None:
            raise ProviderError("Foreign creation needs an existing managed owner.")
        pending = self.pending()
        if pending is not None:
            raise ProviderError(pending["error"])
        previous = self.expected
        history = [] if previous is None else [
            *previous["history"], {key: value for key, value in previous.items() if key != "history"},
        ]
        self._write({
            "version": 1, "binding": self.binding, "owner": self._owner(self.record),
            "revision": 1 if previous is None else previous["revision"] + 1,
            "phase": "publishing", "base": base, "expected_head": expected_head or "",
            "history": history,
        })

    def finish(self, pull: PullResult | None, *, verified: bool) -> None:
        if self.expected is None or self.expected["phase"] != "publishing":
            raise ProviderError("Foreign creation has no current reserved receipt.")
        if verified:
            from .providers.github_pull_source import validate_url
            if pull is None:
                raise ProviderError("Foreign creation did not return a pull-request identity.")
            validate_url(pull.url, pull.number, self.binding["target"], self.binding["endpoint"])
        self._write({
            **self.expected, "revision": self.expected["revision"] + 1,
            "phase": "verified" if verified else "unknown",
            "provider_number": pull.number if pull is not None else None,
            "provider_url": pull.url if pull is not None else "",
        })

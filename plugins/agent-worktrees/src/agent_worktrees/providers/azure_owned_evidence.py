"""Embedding seam for an already authorized ADO owner; no credential export."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import time
from urllib.parse import urlsplit

from .azure_review_evidence import (
    MAX_EVIDENCE_BYTES, PRINCIPAL_SEMANTICS, _id, authorized_principal, collect_evidence,
)
from .base import ProviderError
from .observation_credentials import ObservationCredentialError

PROFILE = "owner_executed_review_evidence/v1"


@dataclass(frozen=True)
class AdoReadOwner:
    """Trusted adapter input, not an authorizer or a deserializable permission."""

    endpoint: str
    repository: str
    project_id: str
    repository_id: str
    principal_id: str
    context_id: str
    checkpoint: Callable[[], None] = field(repr=False)
    reader_factory: Callable = field(repr=False)

    def __post_init__(self):
        for value in (
            self.endpoint, self.repository, self.project_id, self.repository_id,
            self.principal_id, self.context_id,
        ):
            _id(value)
        try:
            endpoint = urlsplit(self.endpoint)
            invalid = (
                endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
                or endpoint.password or endpoint.query or endpoint.fragment
            )
            _ = endpoint.port
        except ValueError:
            invalid = True
        if invalid or not callable(self.checkpoint) or not callable(self.reader_factory):
            raise ValueError("a configured HTTPS target and live read-owner callbacks are required")


def acquire_owned_evidence(
    owner: AdoReadOwner, number: int, *, budget: float,
    clock=time.monotonic, instant=lambda: datetime.now(timezone.utc).isoformat(),
) -> dict:
    """Call only inside the existing owner's normal protected request pipeline.

    The factory supplies its already authorized reader and must apply the supplied
    checkpoint and remaining budget to each physical request/page, not just each
    collection. No caller-supplied JSON can construct this trusted embedding.
    """
    if (
        not isinstance(owner, AdoReadOwner) or type(number) is not int or number <= 0
        or type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0
    ):
        raise ValueError("a trusted read-owner binding, PR number and finite budget are required")
    started = clock()
    deadline = started + budget
    observed_from = instant()
    checkpoint_count = 0

    def remaining():
        value = deadline - clock()
        if value <= 0:
            raise ProviderError("ADO owner-read budget exhausted", transient=True)
        return value

    def checkpoint():
        nonlocal checkpoint_count
        remaining()
        result = owner.checkpoint()
        if result is not None:
            raise ValueError("ADO read-owner checkpoint must enforce context")
        checkpoint_count += 1
        remaining()

    def perform(call):
        checkpoint()
        value = call()
        checkpoint()
        return value

    checkpoint()
    reader = owner.reader_factory(number, remaining, checkpoint)
    checkpoint()

    def check_identity():
        principal = authorized_principal(perform(reader.identity))
        if principal != owner.principal_id:
            raise ObservationCredentialError("ADO read-owner effective principal mismatch")
        return principal

    principal = check_identity()
    result = collect_evidence(
        reader, owner.repository, number, owner.endpoint.rstrip("/"),
        remaining=remaining, perform=perform,
        get_principal=lambda: principal, get_identity_checks=lambda: 0,
        clock=clock, instant=instant, started=started, observed_from=observed_from,
        source=PROFILE, expected_ids=(owner.project_id, owner.repository_id),
    )
    check_identity()
    checkpoint()
    result["acquisition"].update(
        principal_semantics=PRINCIPAL_SEMANTICS, owner_context_id=owner.context_id,
        owner_checkpoint_count=checkpoint_count, owner_identity_checks=2,
        duration_ms=round((clock() - started) * 1000, 3),
    )
    if len(json.dumps(result, allow_nan=False).encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise ProviderError("ADO owner-read evidence exceeds its byte budget")
    return result

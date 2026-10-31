"""Original local Host-child admission; never a remote database reader or grant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .acquisition_contract import AcquisitionRequest
from .acquisition_custody import decode
from .local_host_admission import child_database, context_fields
from .runtime_custody import RuntimeCustodyError, encode

if TYPE_CHECKING:
    from .acquisition_launch_owner import AcquisitionLaunchOwner

CONTEXT_ENV = "AGENT_BRIDGE_ORIGINAL_ACQUISITION"


def context(owner: AcquisitionLaunchOwner, argv: list[str], cwd: str) -> str:
    from .acquisition_journal import _digest

    return encode({
        **context_fields(owner, argv, cwd),
        "delivery_id": owner.request.delivery_id, "request_digest": _digest(owner.request),
    })


def require_child_admission(
    raw: str, *, cwd: str, session_id: str, argv: list[str] | None = None,
) -> None:
    """The actual Host checks again after scheduling, before spawning its child."""
    from .acquisition_journal import AcquisitionJournal, _digest

    value = decode(raw)
    if set(value) != {
        "version", "installation", "admission", "producer", "database_identity",
        "delivery_id", "request_digest", "handoff_id", "session_id", "argv", "cwd",
    } or type(value["version"]) is not int or value["version"] != 1:
        raise RuntimeCustodyError("Unsupported original acquisition child context")
    with child_database(value, cwd=cwd, session_id=session_id, argv=argv) as (db, admission):
        with db._runtime_transaction(write=False) as conn:
            effect = conn.execute(
                "SELECT e.data_json,e.stage,a.state FROM acquisition_effects e "
                "JOIN acquisition_attempts a USING(delivery_id) WHERE delivery_id=?",
                (value["delivery_id"],),
            ).fetchone()
            if (
                effect is None or effect["state"] != "pending"
                or effect["stage"] not in ("prepared", "launching")
            ):
                raise RuntimeCustodyError("Original acquisition child recovery facts are absent")
            try:
                original = AcquisitionRequest.model_validate(decode(effect["data_json"])["_request"])
            except (TypeError, ValueError, KeyError) as exc:
                raise RuntimeCustodyError("Original acquisition child recovery facts changed") from exc
            if original.delivery_id != value["delivery_id"] or _digest(original) != value["request_digest"]:
                raise RuntimeCustodyError("Original acquisition child request changed")
        report = AcquisitionJournal(db).read_launch(original, value["handoff_id"])
        if (
            report["handoff"]["admission"] != admission.document()
            or report["handoff"]["consumer"]["session_id"] != session_id
            or report["started_at"] is None or report["result"] is not None
        ):
            raise RuntimeCustodyError("Original acquisition child launch is not outstanding")

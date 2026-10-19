"""Explicit worktree-detail reads through the selected provider, never paint."""

from __future__ import annotations


def claims_argv(machine, env, worktree_id, *, transitive=False, **source_identity):
    """Explicit full claim inspection through the selected remote source."""
    return remote_argv(
        machine, env, worktree_id, command="claims transitive" if transitive else "claims",
        capability="claims", **source_identity,
    )


def remote_argv(
    machine, env, worktree_id, *, command, capability, source_id=None,
    expected_instance_id=None, expected_assignment=None, expected_transport_fingerprint=None,
):
    from . import data_ssh as transport

    project = transport._project()
    source = transport._find_source(machine, env, source_id=source_id)
    if source is None:
        return None
    if source.source_kind == transport.SOURCE_KIND_PROVIDER_EXEC:
        if (
            not expected_transport_fingerprint
            or transport._provider_transport_fingerprint(source) != expected_transport_fingerprint
        ):
            return None
        try:
            transport._resolve_provider_source(source, transport._run)
        except (OSError, RuntimeError, ValueError):
            return None
        if (
            not expected_instance_id
            or source.instance_id != expected_instance_id
            or not isinstance(expected_assignment, dict)
            or source.venue.get("assignment") != expected_assignment
        ):
            return None
    if (
        source.local or not source.ready or not source.alias
        or not source.capabilities.get(
            capability, source.source_kind == transport.SOURCE_KIND_MACHINE_SSH,
        )
    ):
        return None
    inner = f"{project} {command} {transport._remote_arg(source.shell, worktree_id)} --json"
    if source.source_kind == transport.SOURCE_KIND_PROVIDER_EXEC:
        return transport._provider_remote_argv(
            source, inner, expected_instance_id=expected_instance_id,
            expected_assignment=expected_assignment,
        )
    return transport._wrap_remote(source.shell, source.alias, inner)


def load_claims(engine, rec):
    from worktree_manager import engine_client

    from .. import context
    from . import data_ssh, maintenance
    from .worktree_assets import claim_details, descendant_details, needs_descendant_details

    wid = (rec.get("raw") or {}).get("id")
    if not isinstance(wid, str) or not wid:
        raise ValueError("no worktree id for claim inspection")
    machine, env = rec.get("machine"), rec.get("env")

    def query(*, transitive: bool = False) -> dict:
        if (machine, env) == getattr(engine.src, "LOCAL", None):
            args = ["claims", *(["transitive"] if transitive else []), wid, "--json"]
            return engine_client.run_json(
                context.project(), args, runner=engine._provider_runner(),
            )
        source = rec.get("source") or {}
        argv = data_ssh.claims_argv(
            machine, env, wid, source_id=rec.get("source_id"),
            **({"transitive": True} if transitive else {}),
            expected_instance_id=source.get("instance_id"),
            expected_assignment=source.get("venue", {}).get("assignment"),
            expected_transport_fingerprint=source.get("transport_fingerprint"),
        )
        if argv is None:
            raise ValueError(f"no claim-inspection route to {machine} {env}")
        return maintenance._ssh_json(argv)

    details = claim_details(query())
    if needs_descendant_details(rec.get("raw") or rec):
        details.extend(descendant_details(query(transitive=True)))
    return details

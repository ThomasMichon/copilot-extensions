"""One remote Session Host preparation barrier for trusted venues."""

from __future__ import annotations

import json
import logging
import shlex
import asyncio
import threading
from pathlib import Path

from plugin_resolve.reflection import build_plan, build_prepare_script, parse_prepared, selection_spec
from plugin_resolve.target import requires_repository_binding
from worktree_identity import execution_machine_from_fields


async def local_plugin_request(target, workspace: str, environment: dict[str, str]) -> dict | None:
    """Build a readonly producer plan for the actual allocated checkout."""
    from plugin_resolve.conventions import SETTINGS_RELS
    from plugin_resolve.registration import _settings_document
    from .related_plugins import control_plane_anchors, is_harness_plugin, related_plugins_for_repo

    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("Local plugin preparation requires the allocated execution checkout")
    checkout = Path(workspace).resolve(strict=True)
    if not checkout.is_dir():
        raise ValueError("Local execution checkout is not a directory")
    if not any((parent / ".git").exists() for parent in (checkout, *checkout.parents)):
        return None  # Legacy Gitless launches have no repository registration surface.
    from .session_manager import _REQUEST_OVERRIDES_KEY

    provenance = (target.venue or {}).get(_REQUEST_OVERRIDES_KEY, {})
    from .privileged_plugins import controller_source

    verified, controller = controller_source(target)
    if not verified:
        controller = await resolve_controller_cwd(
            target, caller_session_id=provenance.get("controller_session_id"),
        )
    roots = list(dict.fromkeys([
        *([Path(controller)] if controller else []), *control_plane_anchors(),
    ]))
    def plan_request():
        for root in roots:
            for relative in SETTINGS_RELS:
                _settings_document(root.joinpath(*relative))
        specs = [
            {"source": ref.source, "enable": ref.enable}
            for ref in related_plugins_for_repo(target.project, anchors=roots)
            if not is_harness_plugin(ref.source)
        ]
        plan = build_plan(
            specs, roots, allow_external=True, local_mode="reference",
            target_checkout=checkout, environment=environment,
        )
        return {"repo": str(checkout), **plan, "local_mode": "reference"}

    return await asyncio.to_thread(plan_request)


async def prepare_local_plugins(
    target, workspace: str, environment: dict[str, str], *, prepared_context: dict | None = None,
) -> list[str] | None:
    """Prepare the actual local checkout, retaining no copied local payloads."""
    from plugin_resolve.preparation import prepare
    from .transport import _find_copilot, _wrap_batch_for_windows

    request = await local_plugin_request(target, workspace, environment)
    if request is None:
        if prepared_context is not None:
            prepared_context.clear()
            prepared_context.update({
                "applicable": False, "cwd": str(Path(workspace).resolve()),
                "plugins": [], "logical_payloads": {},
            })
        return None
    cancelled = threading.Event()

    def check_cancelled():
        if cancelled.is_set():
            raise InterruptedError("Local plugin preparation cancelled")

    def prepare_checkout():
        check_cancelled()
        direct_launch = not target.project or (target.explicit_cwd and not target.native_resume_id)
        # Managed plans may run opaque setup wrappers. Their selected environment
        # supplies the packaging CLI, not proof of the wrapper's eventual backend.
        executable = _find_copilot(
            environment, cwd=workspace,
            explicit_path=target.copilot_path if direct_launch else None,
        )
        installer_environment = dict(environment)
        command = tuple(_wrap_batch_for_windows([executable], installer_environment))
        prepared = prepare(
            request,
            copilot_command=command, environment=installer_environment, check_cancelled=check_cancelled,
            prepared_context=prepared_context,
        )
        return [arg for directory in prepared.values() for arg in ("--plugin-dir", directory)]

    worker = asyncio.create_task(asyncio.to_thread(prepare_checkout))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        # Cancellation cannot leave a mutating thread behind a failed launch.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except InterruptedError:
                break
        try:
            worker.result()
        except InterruptedError:
            pass
        raise


def with_controller_origin(target, origin, *, caller_session_id=None):
    from dataclasses import replace
    from .session_manager import _REQUEST_OVERRIDES_KEY

    venue = target.venue or {}
    return replace(target, venue={
        **venue, _REQUEST_OVERRIDES_KEY: {
            **venue.get(_REQUEST_OVERRIDES_KEY, {}), "controller_worktree_origin": origin,
            **({"controller_session_id": caller_session_id} if caller_session_id is not None else {}),
        },
    })


async def resolve_controller_cwd(
    caller, *, caller_session_id: str | None = None, affinity_id: str | None = None,
) -> str | None:
    """Honor explicit controller context without requiring managed adoption."""
    from .acquisition_authority import worktree_command
    from .caller_project import _OWNER_REF
    from .caller_session import normalize_caller_session_id
    from .session_manager import _REQUEST_OVERRIDES_KEY

    raw_path = getattr(caller, "caller_worktree", None)
    owner = getattr(caller, "caller_owner_ref", None)
    venue = getattr(caller, "venue", None) or {}
    origin = venue.get(_REQUEST_OVERRIDES_KEY, {}).get("controller_worktree_origin")
    if origin not in {None, "explicit", "affinity"}:
        raise RuntimeError("Invalid controller context provenance")
    legacy_affinity = origin is None and owner is None and raw_path is not None and raw_path == affinity_id
    if origin == "affinity" or legacy_affinity:
        try:
            usable = isinstance(raw_path, str) and Path(raw_path).is_absolute() and Path(raw_path).is_dir()
        except OSError:
            usable = False
        if owner is not None or not usable:
            if legacy_affinity and isinstance(raw_path, str) and Path(raw_path).is_absolute():
                logging.getLogger("agent-bridge").warning(
                    "Legacy controller directory unavailable; retaining affinity-only provider discovery"
                )
            raw_path = None
    if origin == "explicit" and raw_path is None:
        raise RuntimeError("Explicit controller context is missing")
    try:
        path = None
        if raw_path is not None:
            if not isinstance(raw_path, str):
                raise ValueError("invalid controller path")
            path = Path(raw_path)
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("unusable controller path")
            path = path.resolve(strict=True)
        if owner is None:
            return str(path) if path is not None else None
        match = _OWNER_REF.fullmatch(owner) if isinstance(owner, str) else None
        if match is None:
            raise ValueError("invalid controller owner")
        if match.group("session") is not None and (
            match.group("session") != normalize_caller_session_id(caller_session_id)
        ):
            raise ValueError("controller owner session differs from caller session")
        machine, project, worktree_id = owner.partition("#")[0].split("/")
        result = await worktree_command(
            ["list", "--json", "--cache-only", "--worktree-id", worktree_id], project,
        )
        listed = result.get("worktrees")
        if not isinstance(listed, list):
            raise ValueError("invalid controller owner response")
        rows = [
            row for row in listed
            if isinstance(row, dict) and row.get("id") == worktree_id
        ]
        if len(rows) != 1:
            raise ValueError("controller worktree is not tracked")
        row = rows[0]
        if (
            row.get("repo") != project or execution_machine_from_fields(row) != machine
            or row.get("status") in {"finalized", "complete", "completed", "archived"}
            or not isinstance(row.get("path"), str)
        ):
            raise ValueError("controller identity does not match its owner")
        canonical = Path(row["path"])
        if not canonical.is_absolute() or not canonical.is_dir():
            raise ValueError("unusable owned controller path")
        canonical = canonical.resolve(strict=True)
        if path is not None and canonical != path:
            raise ValueError("controller path differs from owned checkout")
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError("Venue controller context could not be verified") from exc
    return str(canonical)


async def _registered_target_binding(repository: str) -> dict:
    from plugin_activation import normalize_remote
    from .acquisition_authority import worktree_command
    from .acquisition_contract import AcquisitionRefused

    try:
        inventory = await worktree_command(["repos", "list", "--strict", "--json"], None)
    except AcquisitionRefused as exc:
        if exc.code != "worktrees_unavailable":
            raise
        logging.getLogger("agent-bridge").warning(
            "Target-relative plugin ownership is unavailable without the repository registry",
        )
        return {}
    if inventory.get("inventory_authority") != "complete" or not isinstance(inventory.get("repos"), list):
        raise RuntimeError("Target plugin ownership requires a complete repository registry")
    remote = repository
    if "://" not in remote and ":" not in remote and remote.count("/") == 1:
        remote = f"https://github.com/{remote}"
    identity = normalize_remote(remote)
    if identity and identity.startswith("network:github.com/"):
        identity = identity.casefold()
    bound_roots = []
    for row in inventory["repos"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise RuntimeError("Repository registry returned an invalid target binding")
        registered_remote = normalize_remote(row.get("remote", ""))
        if registered_remote and registered_remote.startswith("network:github.com/"):
            registered_remote = registered_remote.casefold()
        if row["name"] != repository and not (
            identity and not identity.startswith("file-relative:") and registered_remote == identity
        ):
            continue
        found = await worktree_command(
            ["repos", "find", row["name"], "--registered-only", "--json"], None,
        )
        if (
            found.get("registration") != "ready" or found.get("name") != row["name"]
            or found.get("remote") != row.get("remote") or not isinstance(found.get("path"), str)
        ):
            raise RuntimeError("Registered target plugin ownership changed during resolution")
        root = Path(found["path"])
        if not root.is_absolute():
            raise RuntimeError("Registered target plugin source is not an absolute path")
        bound_roots.append(root.resolve(strict=True))
    return {
        "target_repository": identity if identity and not identity.startswith("file-relative:") else repository,
        "target_source_roots": bound_roots,
    } if bound_roots else {}


async def venue_plugin_plan(
    target: dict, *, caller=None, controller_cwd: str | None = None, codespace: bool = False,
) -> dict:
    """Select current-controller plugins independently of the receiver transport."""
    from .related_plugins import control_plane_anchors, is_harness_plugin, related_plugins_for_repo
    from .session_host.container_transport import _run_provider
    from .transport import _reresolve_stale_interpreter

    if target.get("security_profile") == "restricted":
        raise RuntimeError("Restricted venues cannot receive host plugin projection")
    if controller_cwd is None:
        from .spawn_target import REQUEST_OVERRIDES_KEY

        provenance = (getattr(caller, "venue", None) or {}).get(REQUEST_OVERRIDES_KEY, {})
        session_id = provenance.get("controller_session_id")
        controller_cwd = await resolve_controller_cwd(
            caller, **({"caller_session_id": session_id} if session_id is not None else {}),
        )
    plan = {"plugins": []}
    provider = target.get("provider_command")
    codespace = target.get("plugin_plan") or codespace
    if codespace and not provider:
        from .provider_sources import discover_provider_manifests
        manifest = discover_provider_manifests().get("codespace")
        if manifest is None:
            raise RuntimeError("Codespace plugin preparation requires its active provider")
        provider = manifest.command
    if codespace:
        provider = _reresolve_stale_interpreter(list(provider))
        command = [*provider, "plugin-plan", target["name"]]
        if target.get("repo"):
            command += ["--repo", target["repo"]]
        if controller_cwd is not None:
            command += ["--plugin-root", controller_cwd]
        rc, out, _err = await _run_provider(
            command, timeout=120, **({"cwd": controller_cwd} if controller_cwd is not None else {}),
        )
        if rc:
            raise RuntimeError("Venue provider could not select required plugins")
        plan = json.loads(out)
    roots = list(dict.fromkeys(
        [*([Path(controller_cwd)] if controller_cwd is not None else []), *control_plane_anchors()]
    ))
    specs = [selection_spec(value) for value in target.get("related_plugins", [])]
    selected_related = {
        spec["source"]: spec for spec in specs if not is_harness_plugin(spec["source"])
    }
    if target.get("repo"):
        for ref in related_plugins_for_repo(target["repo"], anchors=roots):
            spec = selection_spec({"source": ref.source, "enable": ref.enable})
            if not is_harness_plugin(spec["source"]):
                selected_related.setdefault(spec["source"], spec)
    if selected_related:
        binding = {}
        if target.get("repo") and requires_repository_binding(selected_related.values(), roots):
            binding = await _registered_target_binding(target["repo"])
        related = build_plan(list(selected_related.values()), roots, **binding)
        if (
            plan.get("target_repository") and related.get("target_repository")
            and plan["target_repository"] != related["target_repository"]
        ):
            raise RuntimeError("Provider and related plugins disagree on the execution repository")
        selected = {item["source"]: item for item in plan["plugins"]}
        for item in related["plugins"]:
            provider_item = selected.get(item["source"])
            if provider_item is not None and item.get("resolve_on_target"):
                continue
            selected[item["source"]] = {
                **item, "enable": provider_item["enable"] if provider_item is not None else item["enable"],
            }
        plan = {
            **plan,
            "plugins": list(selected.values()),
            "marketplaces": {**plan.get("marketplaces", {}), **related.get("marketplaces", {})},
            **({"target_repository": related["target_repository"]} if related.get("target_repository") else {}),
        }
    return plan


async def prepare_venue_plugins(
    target: dict, transport, workspace: str, *, prelude: str = "", remote_env: str | None = None,
    caller=None, controller_cwd: str | None = None,
) -> list[str]:
    plan = await venue_plugin_plan(
        target, caller=caller, controller_cwd=controller_cwd,
        codespace=getattr(transport, "boundary", None) == "codespace",
    )
    environment = f". {shlex.quote(remote_env)};\n" if remote_env else ""
    script = environment + prelude + "\n" + build_prepare_script(plan, workspace)
    rc, output, error = await transport.run(
        "bash -ls", input_bytes=script.encode(), timeout=600,
    )
    if rc:
        raise RuntimeError(f"Required venue plugin preparation failed: {error.strip()}")
    return parse_prepared(output)

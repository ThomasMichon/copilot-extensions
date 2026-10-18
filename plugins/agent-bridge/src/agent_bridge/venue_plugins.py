"""One remote Session Host preparation barrier for trusted venues."""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path

from plugin_resolve.reflection import build_plan, build_prepare_script, parse_prepared, selection_spec


def with_controller_origin(target, origin):
    from dataclasses import replace
    from .session_manager import _REQUEST_OVERRIDES_KEY

    venue = target.venue or {}
    return replace(target, venue={
        **venue, _REQUEST_OVERRIDES_KEY: {
            **venue.get(_REQUEST_OVERRIDES_KEY, {}), "controller_worktree_origin": origin,
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
            row.get("repo") != project or row.get("machine") != machine
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


async def prepare_venue_plugins(
    target: dict, transport, workspace: str, *, prelude: str = "", remote_env: str | None = None,
    caller=None, controller_cwd: str | None = None,
) -> list[str]:
    from .related_plugins import control_plane_anchors, is_harness_plugin, related_plugins_for_repo
    from .session_host.container_transport import _run_provider
    from .transport import _reresolve_stale_interpreter

    if target.get("security_profile") == "restricted":
        raise RuntimeError("Restricted venues cannot receive host plugin projection")
    if controller_cwd is None:
        controller_cwd = await resolve_controller_cwd(caller)
    plan = {"plugins": []}
    provider = target.get("provider_command")
    codespace = target.get("plugin_plan") or getattr(transport, "boundary", None) == "codespace"
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
        related = build_plan(list(selected_related.values()), roots)
        selected = {item["source"]: item for item in plan["plugins"]}
        for item in related["plugins"]:
            provider_item = selected.get(item["source"])
            if provider_item is not None and item.get("resolve_on_target"):
                continue
            selected[item["source"]] = {
                **item, "enable": provider_item["enable"] if provider_item is not None else item["enable"],
            }
        plan = {
            "plugins": list(selected.values()),
            "marketplaces": {**plan.get("marketplaces", {}), **related.get("marketplaces", {})},
        }
    environment = f". {shlex.quote(remote_env)};\n" if remote_env else ""
    script = environment + prelude + "\n" + build_prepare_script(plan, workspace)
    rc, output, error = await transport.run(
        "bash -ls", input_bytes=script.encode(), timeout=600,
    )
    if rc:
        raise RuntimeError(f"Required venue plugin preparation failed: {error.strip()}")
    return parse_prepared(output)

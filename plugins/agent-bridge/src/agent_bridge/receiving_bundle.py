"""Compose launch-scoped extensions and canonical pane shells, not guest runtimes."""

from __future__ import annotations

import hashlib
import base64
import io
import json
from pathlib import Path, PurePosixPath
import stat
import shlex
import tempfile
import uuid
import zipfile

_ASSET_PREFIX = "src/agent_bridge/receiving_assets"
_MANIFESTS = (
    ("launch-wrapper-assets.json", "copilot-extensions.launch-wrapper-assets"),
    ("packaged-assets.json", "copilot-extensions.packaged-assets"),
)


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _relative(value: object) -> str:
    if (
        not isinstance(value, str) or not value or "\\" in value or ":" in value
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Receiving support requires safe relative asset paths")
    return value


def _read(root: Path, relative: str) -> bytes:
    target = root.joinpath(*_relative(relative).split("/"))
    for path in (target, *target.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Receiving support must not traverse linked assets")
        if path == root:
            break
    if not target.is_file():
        raise ValueError("Receiving support asset is not a regular file")
    return target.read_bytes()


def support_sources() -> tuple[tuple[str, bytes], ...]:
    """Read the selected source or materialized package, without guest discovery."""
    from agent_remote_driver.bridge_extension import extension_sources

    package = _package_root()
    assets = package / "receiving_assets"
    materialized = assets.exists()
    manifests = assets / "plugins" / "agent-bridge" if materialized else package.parent.parent
    if not materialized and manifests.name != "agent-bridge":
        raise ValueError("Selected Bridge runtime has no packaged receiving support")
    sources: dict[str, bytes] = {}
    for name, schema in _MANIFESTS:
        declaration = json.loads(_read(manifests, name))
        if (
            not isinstance(declaration, dict) or declaration.get("schema") != schema
            or type(declaration.get("version")) is not int
            or declaration["version"] not in (1, 2)
            or declaration["version"] == 2 and schema != "copilot-extensions.packaged-assets"
        ):
            raise ValueError("Unsupported receiving-support asset declaration")
        groups = [declaration] if declaration["version"] == 1 else declaration.get("groups")
        if (
            not isinstance(groups, list) or not 1 <= len(groups) <= 32
            or any(not isinstance(group, dict) for group in groups)
        ):
            raise ValueError("Unsupported receiving-support asset groups")
        for group in groups:
            members = group.get("files")
            if not isinstance(members, list) or not members:
                raise ValueError("Receiving-support assets require explicit members")
            members = [_relative(member) for member in members]
            payload = _relative(group.get("payloadDir"))
            canonical = _relative(group.get("canonicalDir"))
            core = None
            if payload == "extensions/agent-bridge/core":
                # The native payload projection and Python runtime consume the
                # same Core owner; a wheel never searches for its source repo.
                core = dict(extension_sources())
                if (
                    canonical != "libs/copilot-driver-core/src/agent_remote_driver/bridge_extension"
                    or set(members) != core.keys()
                ):
                    raise ValueError("Receiving-support Core declaration differs from its package")
                destination = "plugins/agent-bridge/" + payload
            else:
                prefix = _ASSET_PREFIX + "/"
                if not payload.startswith(prefix):
                    raise ValueError("Receiving-support assets have an unexpected payload location")
                destination = payload[len(prefix):]
            for raw in members:
                relative = _relative(raw)
                archive = destination + "/" + relative
                if archive in sources:
                    raise ValueError("Receiving-support asset declarations overlap")
                sources[archive] = (
                    core[relative] if core is not None
                    else _read(assets, archive) if materialized
                    else _read(manifests.parent.parent, canonical + "/" + relative)
                )
    bridge_manifest = "plugins/agent-bridge/plugin.json"
    metadata = json.loads(sources[bridge_manifest])
    if not isinstance(metadata, dict) or metadata.get("name") != "agent-bridge":
        raise ValueError("Receiving support has no selected Bridge extension identity")
    # This closure contains extensions only: full-suite hooks and skills are
    # neither staged nor activated as a side effect of a portable launch.
    sources[bridge_manifest] = json.dumps({
        key: metadata[key] for key in (
            "name", "version", "description", "author", "license", "repository", "homepage",
        ) if key in metadata
    }, sort_keys=True, separators=(",", ":")).encode()
    return tuple(sorted(sources.items()))


def build_mux_support() -> tuple[bytes, str]:
    """Package the selected compact core and portable assets through their owners."""
    from agent_remote_driver.session_host.bundle import build_session_host_bundle
    from .portable_policy import policy_sources

    with tempfile.TemporaryDirectory(prefix="agent-bridge-mux-core-") as directory:
        core, _ = build_session_host_bundle(directory)
        core_bytes = core.read_bytes()
    sources = (
        *support_sources(), *policy_sources(),
        ("driver.pyz", core_bytes),
        ("receiver.py", Path(__file__).with_name("receiving_mux.py").read_bytes()),
    )
    digest = hashlib.sha256()
    for name, contents in sources:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(contents)
        digest.update(b"\0")
    identity = digest.hexdigest()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = json.dumps({
            "schema": "agent-bridge.mux-support", "version": 1, "source": identity,
        }, sort_keys=True, separators=(",", ":")).encode()
        for name, contents in (*sources, ("support.json", manifest)):
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(entry, contents)
    return output.getvalue(), identity


def build_mux_launch(
    cwd: str, *, state_root: str, scope_id: str, attempt: str,
    session_id: str, copilot_args: list[str] | None = None,
    ensure_mux: bool = False,
    registration: dict | None = None,
    driven_by: str | None = None,
) -> tuple[str, bytes]:
    """Transport the selected receiver over an existing provider stdin boundary."""
    from .portable_policy import selected_context
    from .receiving_mux import _request

    # Validate canonical identities at the caller too; the receiver repeats
    # the complete check independently against its actual execution checkout.
    uuid.UUID(attempt)
    uuid.UUID(session_id)
    if type(ensure_mux) is not bool:
        raise ValueError("Mux prerequisite selection must be explicit")
    support, _identity = build_mux_support()
    request = _request({
        "version": 1, "attempt": attempt, "session_id": session_id, "cwd": cwd,
        "state_root": state_root, "scope_id": scope_id,
        "policy_context": selected_context(), "copilot_args": list(copilot_args or []),
        "support_sha256": hashlib.sha256(support).hexdigest(),
        **({"ensure_mux": True} if ensure_mux else {}),
        **({"registration": registration} if registration is not None else {}),
        **({"driven_by": driven_by} if driven_by is not None else {}),
    })
    receiver = Path(__file__).with_name("receiving_mux.py").read_text(encoding="utf-8")
    command = "python3 -I -S -c " + shlex.quote(receiver)
    return command, json.dumps({
        "request": request, "support": base64.b64encode(support).decode("ascii"),
    }, separators=(",", ":")).encode()

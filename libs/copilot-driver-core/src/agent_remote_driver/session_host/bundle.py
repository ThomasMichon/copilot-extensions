"""Content-addressed receiving export of the independent Host executable."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path

import agent_procutil

_MAIN = "agent_remote_driver.session_host.launcher:main"
_LEGACY_MAIN = "agent_remote_driver.session_host.legacy_launcher:main"
_DRIVER_MODULES = (
    "__init__.py",
    "winjob.py",
    "native_arguments.py",
    "session_preferences.py",
    "preference_attestation.py",
    "preference_exec.py",
    "headed/__init__.py",
    "headed/backend.py",
    "headed/staging.py",
    "headed/launcher.py",
    "headed/receipt.py",
    "headed/recovery.py",
    "headed/recovery_codec.py",
    "headed/extension.mjs",
    "headed/acp-adapter.mjs",
    "headed/host-client.mjs",
    "session_host/__init__.py",
    "session_host/__main__.py",
    "session_host/protocol.py",
    "session_host/client.py",
    "session_host/host.py",
    "session_host/osutil.py",
    "session_host/preference_spawn.py",
    "session_host/launcher.py",
    "session_host/host_state.py",
    "session_host/startup_admission.py",
    "session_host/legacy_launcher.py",
    "session_host/receiver_policy.py",
)
_PROCUTIL_MODULES = (
    "__init__.py", "_async.py", "_caller_owned.py", "_darwin_caller_owned.py", "_job_scopes.py",
    "_daemon_instance_child.py", "_daemon_instance_identity.py", "_daemon_instance_native.py",
    "_daemon_instance_owner.py", "_daemon_instance_clock.py", "_daemon_instance_reconcile.py",
    "_daemon_instance_records.py",
    "_daemon_instance_store.py", "_interpreter.py", "_linux_pidfd.py", "_output_relay.py",
    "_windows_caller_owned.py", "_windows_job_types.py", "python_cache.py",
    "daemon.py", "daemon_broker.py", "daemon_instances.py", "linux_daemon_scope.py", "native_child.py",
    "owned_process.py", "startup.py", "streaming.py",
)


def _pkg_root() -> Path:
    """The independently installed core package, never a caller checkout."""
    return Path(__file__).resolve().parents[1]


def _bundle_sources() -> tuple[tuple[str, Path], ...]:
    """Archive-relative paths and their source files for the host closure."""
    procutil_root = Path(agent_procutil.__file__).resolve().parent
    driver_root = _pkg_root()
    return tuple(
        (f"agent_remote_driver/{rel}", driver_root / rel)
        for rel in _DRIVER_MODULES
    ) + tuple(
        (f"agent_procutil/{name}", procutil_root / name) for name in _PROCUTIL_MODULES
    )


def _policy_sources() -> tuple[tuple[str, bytes], ...]:
    import io
    import zipfile
    from repository_policy.receiving import build_receiving_policy_archive

    _reader, payload = build_receiving_policy_archive()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return tuple((name, archive.read(name)) for name in sorted(archive.namelist()))


def _legacy_sources() -> tuple[tuple[str, bytes], ...]:
    """Import-only compatibility for already supported receiving participants."""
    sources = [
        ("agent_bridge/__init__.py", b'"""Legacy Host import namespace."""\n'),
        ("agent_bridge/session_host/__init__.py",
         b"from agent_remote_driver.session_host import *\n"),
        ("agent_bridge/hosting_policy.py",
         (_pkg_root() / "session_host/receiver_policy.py").read_bytes()),
    ]
    for relative in (
        "winjob", "session_preferences", "preference_attestation", "preference_exec",
        "session_host/protocol", "session_host/host", "session_host/osutil",
        "session_host/preference_spawn", "session_host/receiver_policy", "session_host/launcher",
    ):
        package, _, module = relative.rpartition("/")
        selected = "legacy_launcher" if relative == "session_host/launcher" else module
        selected = selected or relative
        namespace = "agent_remote_driver" + ("." + package.replace("/", ".") if package else "")
        sources.append((
            "agent_bridge/" + relative + ".py",
            (
                "import sys\n"
                f"from {namespace} import {selected} as _implementation\n"
                "sys.modules[__name__] = _implementation\n"
            ).encode("utf-8"),
        ))
    return tuple(sources)


def _sources(legacy: bool) -> tuple[tuple[str, bytes], ...]:
    module, function = (_LEGACY_MAIN if legacy else _MAIN).split(":")
    entrypoint = f"import sys\nfrom {module} import {function}\nsys.exit({function}())\n"
    return tuple(
        (relative, path.read_bytes()) for relative, path in _bundle_sources()
    ) + _policy_sources() + (_legacy_sources() if legacy else ()) + (
        ("__main__.py", entrypoint.encode("utf-8")),
    )


def bundle_source_hash(*, legacy: bool = False) -> str:
    """A stable sha256 over the source, independent of archive representation."""
    h = hashlib.sha256()
    for archive_rel, contents in _sources(legacy):
        h.update(archive_rel.encode("utf-8"))
        h.update(b"\0")
        h.update(contents)
        h.update(b"\0")
    h.update((_LEGACY_MAIN if legacy else _MAIN).encode("utf-8"))
    return h.hexdigest()


def bundle_filename(source_hash: str | None = None, *, legacy: bool = False) -> str:
    """The content-addressed bundle filename (the CS caches under this name)."""
    sha = source_hash or bundle_source_hash(legacy=legacy)
    return f"session-host-{sha[:16]}.pyz"


def build_session_host_bundle(
    dest_dir: str | os.PathLike[str] | None = None,
    *, legacy: bool = False,
) -> tuple[Path, str]:
    """Build (or reuse) the host-side Session Host zipapp.

    Returns ``(path, source_hash)``. The file is named by the source hash and
    reused if already present, so repeated calls (every connect) are cheap and a
    reconnect can skip re-shipping when the CS already has this hash.
    """
    sha = bundle_source_hash(legacy=legacy)
    dest = (
        Path(dest_dir) if dest_dir
        else Path(tempfile.gettempdir()) / "agent-remote-driver-bundles"
    )
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / bundle_filename(sha)
    if out.exists():
        return out, sha

    with tempfile.TemporaryDirectory(prefix="agent-driver-bundle-", dir=dest) as td:
        tmp_out = Path(td) / "bundle.pyz"
        with tmp_out.open("wb") as stream:
            stream.write(b"#!/usr/bin/env python3\n")
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for archive_rel, contents in sorted(_sources(legacy)):
                    entry = zipfile.ZipInfo(archive_rel)
                    entry.create_system = 3
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = (stat.S_IFREG | 0o600) << 16
                    archive.writestr(entry, contents)
        tmp_out.chmod(0o700)
        os.replace(tmp_out, out)
    return out, sha

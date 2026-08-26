"""Stage plugin payloads onto a CodeSpace (egress-free) for ``--plugin-dir``.

The **repo-targeted** plugin lane: agent-bridge decides a set of related-repo
plugins for a dispatch and asks ``agent-codespaces ssh`` to stage them. The host
payload may be a live repo-local directory-marketplace plugin or an installed
plugin under ``~/.copilot/installed-plugins``. Rather than re-installing on the
CodeSpace -- which needs marketplace egress + auth and risks the
``LAUNCH_ACP`` startup hang -- we **tar+base64 the host payload** and extract it
into a per-plugin dir on the CodeSpace, then point ``copilot --acp --plugin-dir``
at it. Dispatch-scoped: no global enablement, no launch-time marketplace fetch.

Pure helpers only (path resolution + remote-command construction) so they are
unit-testable; the actual ``exec_command`` lives in ``__main__``.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Iterable

log = logging.getLogger("agent-codespaces")

# Remote root the staged payloads land in (``$HOME`` expands in the login shell).
STAGE_ROOT = "$HOME/.acp-staged-plugins"
_STAGING_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".test-venvs",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)


def _has_plugin_manifest(plugin_dir: Path) -> bool:
    """True when ``plugin_dir`` holds a plugin manifest (native or Claude layout).

    A payload may carry its manifest at the native ``plugin.json`` (repo root of
    the plugin) OR the Claude ``.claude-plugin/plugin.json`` -- the ``.ai`` local
    marketplaces this harness stages use the latter. Delegates to the shared
    ``plugin_resolve`` convention when available, with a self-contained fallback
    so staging never hard-depends on the import succeeding.
    """
    try:
        from plugin_resolve.conventions import has_plugin_manifest

        return has_plugin_manifest(plugin_dir)
    except Exception:
        return (
            (plugin_dir / "plugin.json").is_file()
            or (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
        )


def _installed_root(copilot_home: Path | None = None) -> Path:
    return (copilot_home or (Path.home() / ".copilot")) / "installed-plugins"


def parse_source(source: str) -> tuple[str, str] | None:
    """Split a ``name@marketplace`` source into ``(name, marketplace)``.

    Returns ``None`` for other source forms (git URL, ``owner/repo``) -- those
    are not resolvable to a host payload dir by this helper (v1 supports the
    marketplace form, which is what the ``codespacePlugins`` / related-repo
    declarations use).
    """
    s = (source or "").strip()
    if "@" not in s:
        return None
    name, _, mkt = s.partition("@")
    name, mkt = name.strip(), mkt.strip()
    if not name or not mkt:
        return None
    return name, mkt


def _local_payload_result(
    source: str, repo_roots: Iterable[Path]
) -> tuple[bool, Path | None]:
    """Return whether a local alias claims ``source`` and its safe payload.

    Marketplace aliases are first-wins across ``repo_roots``, matching
    ``repo_copilot_settings``. A shadowed definition is never consulted when
    the winning marketplace lacks the requested plugin.
    """
    parsed = parse_source(source)
    if parsed is None:
        return False, None
    name, marketplace = parsed
    try:
        from plugin_resolve import (
            load_marketplace,
            local_marketplace_path,
            plugin_dir,
            read_repo_settings,
        )
        from plugin_resolve.conventions import LOCAL_MARKETPLACE_SOURCE_KINDS
    except ImportError as exc:
        log.warning(
            "Cannot resolve repo-local plugin %s because plugin_resolve is "
            "unavailable: %s",
            source,
            exc,
        )
        return False, None

    roots = list(repo_roots)
    winning_settings = None
    winning_root = None
    winning_index = -1
    for index, repo_root in enumerate(roots):
        settings = read_repo_settings(repo_root)
        if marketplace in settings.marketplaces:
            winning_settings = settings
            winning_root = repo_root
            winning_index = index
            break
    if winning_settings is None or winning_root is None:
        return False, None

    marketplace_entry = winning_settings.marketplaces[marketplace]
    marketplace_source = (
        marketplace_entry.get("source")
        if isinstance(marketplace_entry, dict)
        else None
    )
    if not (
        isinstance(marketplace_source, dict)
        and marketplace_source.get("source") in LOCAL_MARKETPLACE_SOURCE_KINDS
    ):
        return False, None

    marketplace_root = local_marketplace_path(
        marketplace, winning_settings, repo_dir=winning_root
    )
    if marketplace_root is None:
        return True, None
    marketplace_root = marketplace_root.resolve()
    repo_root = Path(winning_root).resolve()
    if not marketplace_root.is_relative_to(repo_root):
        log.warning(
            "Refusing local marketplace %s outside declaring repo %s: %s",
            marketplace,
            repo_root,
            marketplace_root,
        )
        return True, None
    manifest = load_marketplace(marketplace_root)
    payload = plugin_dir(manifest, name) if manifest is not None else None
    if payload is not None:
        payload = payload.resolve()
        if not payload.is_relative_to(marketplace_root):
            log.warning(
                "Refusing plugin %s outside marketplace root %s: %s",
                source,
                marketplace_root,
                payload,
            )
            return True, None
        if _has_plugin_manifest(payload):
            return True, payload

    shadowed_roots = []
    for repo_root in roots[winning_index + 1 :]:
        settings = read_repo_settings(repo_root)
        if marketplace in settings.marketplaces:
            shadowed_roots.append(str(repo_root))
    if shadowed_roots:
        log.warning(
            "Plugin %s is absent from first-wins marketplace %s declared by %s; "
            "shadowed definitions were ignored: %s",
            name,
            marketplace,
            winning_root,
            ", ".join(shadowed_roots),
        )
    return True, None


def local_payload_dir(source: str, repo_roots: Iterable[Path]) -> Path | None:
    """Resolve a safe payload from the effective repo-local marketplace."""
    return _local_payload_result(source, repo_roots)[1]


def host_payload_dir(
    source: str,
    copilot_home: Path | None = None,
    *,
    repo_roots: Iterable[Path] = (),
) -> Path | None:
    """Locate the live-local or installed host payload for ``source``.

    A repo-local directory marketplace wins over installed state so a stale
    installed copy cannot shadow edits in the current harness. Otherwise tries
    ``installed-plugins/<marketplace>/<name>`` and then scans all installed
    marketplaces by plugin name.
    """
    local_claimed, local = _local_payload_result(source, repo_roots)
    if local is not None:
        return local
    if local_claimed:
        return None

    root = _installed_root(copilot_home)
    parsed = parse_source(source)
    if parsed:
        name, mkt = parsed
        cand = root / mkt / name
        if _has_plugin_manifest(cand):
            return cand
    target = parsed[0] if parsed else (source or "").strip()
    if target and root.is_dir():
        for mkt_dir in sorted(root.iterdir()):
            if not mkt_dir.is_dir():
                continue
            cand = mkt_dir / target
            if _has_plugin_manifest(cand):
                return cand
    return None


def _leaf(source: str) -> str:
    parsed = parse_source(source)
    base = parsed[0] if parsed else (source or "").strip()
    return re.sub(r"[^\w.-]", "_", base) or "plugin"


def dest_dir(source: str) -> str:
    """Remote ``--plugin-dir`` path a source's payload is staged to."""
    return f"{STAGE_ROOT}/{_leaf(source)}"


def _staging_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Exclude development caches and environment-secret files from staging."""
    path = PurePosixPath(info.name)
    if any(part in _STAGING_EXCLUDED_DIRS for part in path.parts):
        return None
    name = path.name.lower()
    if name == ".env" or (
        name.startswith(".env.")
        and not name.endswith((".example", ".sample", ".template"))
    ):
        return None
    return info


def build_stage_command(payload_dir: Path, dest: str) -> tuple[str, bytes]:
    """Bash to recreate ``payload_dir`` at remote ``dest`` (egress-free), plus the
    payload to feed on **stdin**.

    Tars+gzips the payload in memory and base64-encodes it. Returns
    ``(command, stdin_bytes)`` where ``command`` decodes+extracts the base64 read
    from **stdin** (``base64 -d | tar -xzf -``) and ``stdin_bytes`` is that base64
    text. Piping the payload over stdin -- rather than embedding it in the command
    string -- keeps the SSH command line tiny, so a large plugin no longer overruns
    the Windows ~32 KB ``CreateProcess`` command-line limit (which surfaces as
    ``[WinError 206] The filename or extension is too long``). ``dest`` may contain
    ``$HOME`` (expanded by the remote shell).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(str(payload_dir), arcname=".", filter=_staging_filter)
    b64 = base64.b64encode(buf.getvalue())
    command = (
        f'rm -rf "{dest}" && mkdir -p "{dest}" && '
        f'base64 -d | tar -xzf - -C "{dest}"'
    )
    return command, b64

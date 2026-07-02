"""Stage plugin payloads onto a CodeSpace (egress-free) for ``--plugin-dir``.

The **repo-targeted** plugin lane: agent-bridge decides a set of related-repo
plugins for a dispatch and asks ``agent-codespaces ssh`` to stage them. The host
already has each plugin's payload under
``~/.copilot/installed-plugins/<marketplace>/<plugin>/`` (installed via
``copilot plugin install``). Rather than re-installing on the CodeSpace -- which
needs marketplace egress + ADO auth and risks the ``LAUNCH_ACP`` startup hang --
we **tar+base64 the host payload** and extract it into a per-plugin dir on the
CodeSpace, then point ``copilot --acp --plugin-dir`` at it. Dispatch-scoped: no
global enablement, no launch-time marketplace fetch.

Pure helpers only (path resolution + remote-command construction) so they are
unit-testable; the actual ``exec_command`` lives in ``__main__``.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import tarfile
from pathlib import Path

log = logging.getLogger("agent-codespaces")

# Remote root the staged payloads land in (``$HOME`` expands in the login shell).
STAGE_ROOT = "$HOME/.acp-staged-plugins"


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


def host_payload_dir(source: str, copilot_home: Path | None = None) -> Path | None:
    """Locate the installed host payload dir for ``source``, or ``None``.

    Tries ``installed-plugins/<marketplace>/<name>`` first, then scans all
    marketplaces for a ``<name>/plugin.json`` (covers a marketplace alias that
    differs from the source's ``@`` suffix).
    """
    root = _installed_root(copilot_home)
    parsed = parse_source(source)
    if parsed:
        name, mkt = parsed
        cand = root / mkt / name
        if (cand / "plugin.json").is_file():
            return cand
    target = parsed[0] if parsed else (source or "").strip()
    if target and root.is_dir():
        for mkt_dir in sorted(root.iterdir()):
            if not mkt_dir.is_dir():
                continue
            cand = mkt_dir / target
            if (cand / "plugin.json").is_file():
                return cand
    return None


def _leaf(source: str) -> str:
    parsed = parse_source(source)
    base = parsed[0] if parsed else (source or "").strip()
    return re.sub(r"[^\w.-]", "_", base) or "plugin"


def dest_dir(source: str) -> str:
    """Remote ``--plugin-dir`` path a source's payload is staged to."""
    return f"{STAGE_ROOT}/{_leaf(source)}"


def build_stage_command(payload_dir: Path, dest: str) -> str:
    """Bash to recreate ``payload_dir`` at remote ``dest`` (egress-free).

    Tars+gzips the payload in memory, base64-encodes it, and emits a command
    that decodes+extracts it into a freshly-created ``dest``. ``dest`` may
    contain ``$HOME`` (expanded by the remote shell).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(str(payload_dir), arcname=".")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return (
        f'rm -rf "{dest}" && mkdir -p "{dest}" && '
        f'printf %s {b64} | base64 -d | tar -xzf - -C "{dest}"'
    )

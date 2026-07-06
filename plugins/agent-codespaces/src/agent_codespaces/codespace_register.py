"""Register CodeSpace-scoped plugins into a CodeSpace's user settings (global lane).

The CodeSpace-scoped (a.k.a. "global") plugin lane. A **harness** plugin declares,
via its ``plugin.json`` ``codespacePlugins`` array, the ``<name>-agent`` plugins
that should be active on a CodeSpace it provisions -- see
:mod:`agent_codespaces.codespace_plugins` for the *discovery* half
(:func:`~agent_codespaces.codespace_plugins.resolve_codespace_plugins`).

This module is the **register-into-CodeSpace** half: at connect, agent-codespaces
resolves the applicable specs and writes them into the CodeSpace **user** settings
(``~/.copilot/settings.json``) -- registering each referenced marketplace,
enabling every ``<name>@<marketplace>`` in ``enabledPlugins``, and turning on
``experimental``. Because this is *user-level* settings (not a repo
``settings.local.json`` and not a session ``--plugin-dir``), it is honored in
**every** launch mode -- interactive VS Code, ``copilot -p``, and the
``copilot --acp`` agent-bridge dispatch alike. That is precisely why the
CodeSpace-scoped lane lives in user settings rather than the ``--plugin-dir``
mechanic the *related-repo* lane uses: a human opening the CodeSpace in VS Code
has no agent-bridge to pass ``--plugin-dir``.

Payloads are also pre-installed (``copilot plugin install``) at connect while the
credential relay is up, so a later launch finds them on disk with no launch-time
marketplace fetch -- dodging the ``LAUNCH_ACP`` egress hang the benchmark effort
tied to egress-restricted startup fetches. The pre-install is best-effort: if it
fails, the user-settings enablement still delivers the plugin on the next
interactive launch (which has full network + interactive auth).

Pure helpers only (command construction) so they are unit-testable; the actual
``exec_command`` lives in :mod:`agent_codespaces.__main__`.
"""

from __future__ import annotations

import base64
import json
import shlex
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .codespace_plugins import CodespacePluginSpec, _read_json, default_copilot_home

# The merge script runs on the CodeSpace under python3; it reads a JSON payload
# file (argv[1]) and merges it into ``~/.copilot/settings.json`` idempotently.
# Kept dependency-free (stdlib only) and tolerant of a missing/garbage settings
# file. Written as a temp file (base64-transported) so no fragile ``-c`` quoting
# crosses the ssh/login-shell boundary.
_MERGE_SCRIPT = r'''
import json, os, sys

payload_path = sys.argv[1]
with open(payload_path, "r", encoding="utf-8") as f:
    payload = json.load(f)

settings_path = os.path.expanduser("~/.copilot/settings.json")
os.makedirs(os.path.dirname(settings_path), exist_ok=True)

data = {}
if os.path.exists(settings_path):
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
if not isinstance(data, dict):
    data = {}

if payload.get("experimental"):
    data["experimental"] = True

mk = data.get("extraKnownMarketplaces")
if not isinstance(mk, dict):
    mk = {}
    data["extraKnownMarketplaces"] = mk
for name, definition in (payload.get("marketplaces") or {}).items():
    mk[name] = definition

ep = data.get("enabledPlugins")
if not isinstance(ep, dict):
    ep = {}
    data["enabledPlugins"] = ep
for spec in payload.get("plugins") or []:
    source = spec.get("source")
    if source and spec.get("enable"):
        ep[source] = True

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

print("registered %d plugin(s) in %s" % (len(payload.get("plugins") or []), settings_path))
'''.lstrip()


def host_marketplaces(copilot_home: Path | None = None) -> dict[str, Any]:
    """The harness's ``extraKnownMarketplaces`` map (name -> definition).

    Read from the host ``~/.copilot/settings.json``. Used to copy the definition
    of any marketplace a resolved plugin source references (e.g. ``dev-tmichon``)
    into the CodeSpace so ``<name>@<marketplace>`` resolves there. Returns ``{}``
    when the settings file is absent or malformed.
    """
    data = _read_json((copilot_home or default_copilot_home()) / "settings.json")
    if not isinstance(data, dict):
        return {}
    mk = data.get("extraKnownMarketplaces")
    return mk if isinstance(mk, dict) else {}


def _marketplace_of(source: str) -> str:
    """The ``@marketplace`` suffix of a ``name@marketplace`` source (else ``""``)."""
    return (source or "").strip().partition("@")[2].strip()


def build_register_payload(
    specs: Iterable[CodespacePluginSpec],
    marketplaces: dict[str, Any],
) -> dict[str, Any]:
    """Build the settings-merge payload for the resolved CodeSpace-scoped specs.

    Collects the marketplace definitions actually referenced by the specs (from
    ``marketplaces``, the harness's known marketplaces) so the CodeSpace can
    resolve each ``<name>@<marketplace>``. Deduplicates plugins by source.
    """
    plugins: list[dict[str, Any]] = []
    seen: set[str] = set()
    needed: dict[str, Any] = {}
    for spec in specs:
        if spec.source in seen:
            continue
        seen.add(spec.source)
        plugins.append({"source": spec.source, "enable": bool(spec.enable)})
        mkt = _marketplace_of(spec.source)
        if mkt and mkt in marketplaces and mkt not in needed:
            needed[mkt] = marketplaces[mkt]
    return {
        "experimental": True,
        "marketplaces": needed,
        "plugins": plugins,
    }


def build_register_command(
    specs: Iterable[CodespacePluginSpec],
    marketplaces: dict[str, Any] | None = None,
    *,
    copilot_home: Path | None = None,
    do_install: bool = True,
) -> str | None:
    """Bash to register + enable the CodeSpace-scoped specs in user settings.

    Returns ``None`` when there is nothing to do. Otherwise emits a command that:

    1. base64-transports the settings-merge payload + the merge script to temp
       files on the CodeSpace (no fragile inline-``-c`` quoting), then runs the
       merge under ``python3`` -- registering the marketplaces, enabling each
       ``<name>@<marketplace>``, and setting ``experimental`` in
       ``~/.copilot/settings.json`` (idempotent);
    2. when ``do_install``, pre-installs each plugin's payload
       (``copilot plugin install <source>``) so a later launch needs no
       marketplace fetch. Best-effort (``|| true``) -- a failed pre-install still
       leaves the settings enablement in place for interactive launches.

    ``$HOME`` in the temp paths is expanded by the remote login shell; the caller
    is expected to wrap the returned command in ``bash -l -c`` so ``copilot`` is
    on ``PATH`` and the credential relay env is present for the install step.
    """
    specs = list(specs)
    if not specs:
        return None
    if marketplaces is None:
        marketplaces = host_marketplaces(copilot_home)

    payload = build_register_payload(specs, marketplaces)
    payload_b64 = base64.b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii")
    script_b64 = base64.b64encode(_MERGE_SCRIPT.encode("utf-8")).decode("ascii")

    payload_path = "$HOME/.acp-register-plugins.json"
    script_path = "$HOME/.acp-register-plugins.py"

    # The settings merge is the essential step; its exit code drives the whole
    # command (surfaced as a warning by the caller). Installs run only when the
    # merge succeeded, and are individually best-effort; the temp files are
    # always cleaned up.
    core = (
        f'printf %s {payload_b64} | base64 -d > "{payload_path}" && '
        f'printf %s {script_b64} | base64 -d > "{script_path}" && '
        f'python3 "{script_path}" "{payload_path}"'
    )
    install_block = ""
    if do_install:
        installs = " ; ".join(
            # Best-effort pre-install: warm the payload on disk (relay up) so a
            # later launch performs no marketplace fetch. Never blocks connect.
            f"copilot plugin install {shlex.quote(spec['source'])} || true"
            for spec in payload["plugins"]
        )
        if installs:
            install_block = f" ; if [ $rc -eq 0 ]; then {installs} ; fi"
    return (
        f"{core} ; rc=$?{install_block} ; "
        f'rm -f "{payload_path}" "{script_path}" ; exit $rc'
    )

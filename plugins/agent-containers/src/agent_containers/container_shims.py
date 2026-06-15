"""In-container credential shims deployed at the bridge connection phase.

agent-containers is generic: rather than baking auth into the image, it deploys
thin shims into the running container that fetch tokens on-demand from the host
relay (over ``host.docker.internal``), authenticated with the container's
per-session secret. The patched Azure CLI / rush ``AdoCodespacesAuthCredential``
call ``azure-auth-helper get-access-token <scope>`` on PATH; that resolves to our
shim, which relays the request to the host.

Transport A: TCP to ``LC_GIT_CREDENTIAL_RELAY_HOST:LC_GIT_CREDENTIAL_RELAY`` with
``LC_GIT_CREDENTIAL_RELAY_TOKEN``. (Phase B will switch to a bind-mounted socket.)
"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys

log = logging.getLogger("agent-containers.shims")

_BIN = "/usr/local/bin"
RELAY_CLIENT_PATH = f"{_BIN}/credential-relay-client"
AZURE_HELPER_PATH = f"{_BIN}/azure-auth-helper"

# Generic relay client: speaks the credential-relay wire protocol to the host,
# reading endpoint + token from the environment injected by the exec wrapper.
RELAY_CLIENT = r'''#!/usr/bin/env python3
import os, socket, sys

HOST = os.environ.get("LC_GIT_CREDENTIAL_RELAY_HOST", "host.docker.internal")
PORT = int(os.environ.get("LC_GIT_CREDENTIAL_RELAY", "9857"))
TOKEN = os.environ.get("LC_GIT_CREDENTIAL_RELAY_TOKEN", "")


def _send(request):
    s = socket.create_connection((HOST, PORT), timeout=10)
    try:
        s.sendall(request.encode("utf-8"))
        s.settimeout(30)
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n\n" in buf:
                break
        return buf.decode("utf-8", "replace")
    finally:
        s.close()


def _normalize_resource(scope):
    # rush/az may pass "<res>/.default"; the relay allowlist keys on the base
    # resource with a trailing slash (e.g. https://storage.azure.com/).
    res = scope.split("/.default")[0]
    if res and not res.endswith("/"):
        res += "/"
    return res


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    action = sys.argv[2] if len(sys.argv) > 2 else ""
    if action == "get-access-token":
        if kind == "azure":
            scope = sys.argv[3] if len(sys.argv) > 3 else ""
            resp = _send(
                "get-azure-token\nauth=%s\nresource=%s\n\n"
                % (TOKEN, _normalize_resource(scope))
            )
            for line in resp.split("\n"):
                if line.startswith("token="):
                    sys.stdout.write(line[len("token="):])
                    return 0
            return 1
        # ADO PAT (ungated)
        resp = _send("get-access-token\nauth=%s\n\n" % TOKEN)
        tok = resp.strip()
        if not tok or "quit=1" in resp:
            return 1
        sys.stdout.write(tok)
        return 0
    if action == "get":
        data = sys.stdin.read()
        if not data.endswith("\n\n"):
            data = data.rstrip("\n") + "\n\n"
        sys.stdout.write(_send(data))
        return 0
    return 0  # store / erase / unknown


sys.exit(main())
'''

# azure-auth-helper: thin wrapper invoked by rush / the patched az CLI.
AZURE_HELPER = f'''#!/usr/bin/env bash
exec python3 {RELAY_CLIENT_PATH} azure "$@"
'''

# ado-auth-helper: thin wrapper for ADO PAT + git credential mode (optional).
ADO_HELPER = f'''#!/usr/bin/env bash
exec python3 {RELAY_CLIENT_PATH} ado "$@"
'''


def _docker_write(container: str, path: str, content: str, mode: str = "755") -> None:
    """Write ``content`` to ``path`` in the container (as root) and chmod it."""
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    script = f"echo {b64} | base64 -d > {path} && chmod {mode} {path}"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    res = subprocess.run(
        ["docker", "exec", "-u", "0", container, "bash", "-lc", script],
        capture_output=True, text=True, timeout=30, creationflags=flags,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"shim deploy failed for {path}: {res.stderr.strip() or res.stdout.strip()}"
        )


def deploy(container: str, *, ado: bool = False) -> None:
    """Deploy the relay client + azure-auth-helper (idempotent) into ``container``.

    ``ado`` additionally deploys ado-auth-helper for ADO PAT / git credential
    relay (off by default to avoid disturbing already-working in-container ADO
    auth; the Azure helper is the dev-deploy fix).
    """
    _docker_write(container, RELAY_CLIENT_PATH, RELAY_CLIENT)
    _docker_write(container, AZURE_HELPER_PATH, AZURE_HELPER)
    if ado:
        _docker_write(container, f"{_BIN}/ado-auth-helper", ADO_HELPER)
    log.info("Deployed relay shims into container '%s' (ado=%s)", container, ado)

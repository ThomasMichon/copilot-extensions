"""In-container credential shims deployed at the bridge connection phase.

agent-containers is generic: rather than baking auth into the image, it deploys
thin shims into the running container that fetch tokens on-demand from the host
relay through the trusted venue's SSH ``-R`` loopback forward. The per-container
secret remains request authorization for the shared relay's Azure-token gate;
it is no longer a defense for a host-network-exposed endpoint. The patched Azure
CLI / rush ``AdoCodespacesAuthCredential`` call
``azure-auth-helper get-access-token <scope>`` on PATH; that resolves to our
shim, which relays the request to the host.
"""

from __future__ import annotations

import base64
import logging
import subprocess

from agent_procutil import no_window_flags

log = logging.getLogger("agent-containers.shims")

_BIN = "/usr/local/bin"
RELAY_CLIENT_PATH = f"{_BIN}/credential-relay-client"
AZURE_HELPER_PATH = f"{_BIN}/azure-auth-helper"
ADO_HELPER_PATH = f"{_BIN}/ado-auth-helper"

# Generic relay client: speaks the credential-relay wire protocol to the host,
# reading endpoint + token from the environment injected by the exec wrapper.
RELAY_CLIENT = r'''#!/usr/bin/env python3
import os, socket, sys

HOST = os.environ.get("LC_GIT_CREDENTIAL_RELAY_HOST", "127.0.0.1")
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
    # Retained for back-compat with the (legacy) resource= request form; the
    # azure path now forwards the scope verbatim (see main()).
    res = scope.split("/.default")[0]
    if res and not res.endswith("/"):
        res += "/"
    return res


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    action = sys.argv[2] if len(sys.argv) > 2 else ""
    if action == "get-access-token":
        if kind == "azure":
            # Faithfully forward the official `azure-auth-helper get-access-token
            # "<scope>"` contract: pass the AAD scope through VERBATIM (e.g.
            # https://storage.azure.com/.default) as `scope=`, so the relay's
            # AzLoginSource mints it via `az ... --scope <scope>` -- the same
            # token the official managed-identity broker would. Do NOT downgrade
            # the scope to a `resource=` (dropping `/.default`): the `.default`
            # form requests the principal's full consented permission set, which
            # the resource form does not, and some storage data-plane operations
            # (e.g. user-delegation-key issuance for dev-deploy SAS) depend on it.
            scope = sys.argv[3] if len(sys.argv) > 3 else ""
            resp = _send(
                "get-azure-token\nauth=%s\nscope=%s\n\n" % (TOKEN, scope)
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
        fields = {}
        for line in data.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        # Containers bootstrap GitHub identity explicitly through GH_TOKEN.
        # Serve that token locally instead of asking the host relay/GCM, whose
        # active account may differ from the token selected for this launch.
        if (
            kind == "ado"
            and fields.get("host", "").lower() == "github.com"
            and os.environ.get("GH_TOKEN")
        ):
            sys.stdout.write(
                "protocol=https\n"
                "host=github.com\n"
                "username=x-access-token\n"
                "password=%s\n\n" % os.environ["GH_TOKEN"]
            )
            return 0
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
    res = subprocess.run(
        ["docker", "exec", "-u", "0", container, "bash", "-lc", script],
        capture_output=True, text=True, timeout=30, creationflags=no_window_flags(),
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"shim deploy failed for {path}: {res.stderr.strip() or res.stdout.strip()}"
        )


def git_credential_environment() -> dict[str, str]:
    """Launch-only Git config that makes the trusted helper authoritative."""
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": ADO_HELPER_PATH,
        "GIT_TERMINAL_PROMPT": "0",
    }


def deploy(container: str, *, ado: bool = True) -> None:
    """Deploy the relay client + azure-auth-helper (idempotent) into ``container``.

    ``ado`` deploys the trusted launch's Git/ADO credential helper. It defaults
    on because the launch-only Git config returned by
    :func:`git_credential_environment` makes that helper authoritative without
    modifying the container user's persistent Git configuration.
    """
    _docker_write(container, RELAY_CLIENT_PATH, RELAY_CLIENT)
    _docker_write(container, AZURE_HELPER_PATH, AZURE_HELPER)
    if ado:
        _docker_write(container, ADO_HELPER_PATH, ADO_HELPER)
    log.info("Deployed relay shims into container '%s' (ado=%s)", container, ado)

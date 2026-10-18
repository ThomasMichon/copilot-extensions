"""Namespace resolver for local Docker dev containers.

Implements the agent-bridge ``NamespaceResolver`` interface so that fleet
containers can be addressed as ``container:<name>`` without pre-registration.
Resolution returns a ``SpawnTarget`` that launches a Copilot ACP agent through
the container wrapper. Trusted fleets use OpenSSH; restricted fleets retain the
direct ``docker exec -i`` boundary and expose a stable provider-target identity
without projecting host authority.

Trusted fleets may forward the host ``gh auth token`` into the container as
``GH_TOKEN``. Restricted fleets use a separate command builder that cannot
accept token or relay arguments.

Usage:
    from agent_containers.resolver import ContainerResolver
    resolver.register_namespace_resolver(ContainerResolver())
    # Then: agent-bridge send container:myrepo-1 "run the tests"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_procutil import no_window_flags
from plugin_resolve.reflection import selection_spec

from . import lifecycle
from ._invoke import payload_command_argv
from .config import (
    DISPATCH_ONLY_LABEL,
    DISPATCH_BINDING_LABEL,
    RESTRICTED_PROFILE,
    SECURITY_PROFILE_LABEL,
    TRUSTED_PROFILE,
    ContainersConfig,
    FleetConfig,
    load_config,
)
from .inference import NativeInferenceConfig
from .lease import deploy_hold_status, get_deploy_hold, get_lease
from .lifecycle import (
    get_container,
    inspect_state,
    list_containers,
    restricted_policy_errors,
    start_container,
)
from .private_state import enforce_mode, fsync_directory, private_file_lock, strict_posix_modes
from .ssh_transport import prepare_ssh_config

if TYPE_CHECKING:
    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.transport import SpawnTarget

log = logging.getLogger("agent-containers")
VENUE_SCHEMA_VERSION = 1
VENUE_PROVIDER = "agent-containers"


@dataclass(frozen=True)
class LiveExecTarget:
    """Live, policy-validated execution inputs for one configured container."""

    name: str
    container_id: str
    config: ContainersConfig
    fleet: FleetConfig
    info: object
    actual_profile: str
    user: str
    workspace_folder: str
    acp_command: str


def _creation_flags() -> int:
    return no_window_flags()


def target_repo_for_cli(name: str) -> int:
    print(asyncio.run(ContainerResolver().target_repo(name)) or "")
    return 0


def add_related_plugins(spec: dict, sources: list[str | dict]) -> dict:
    if sources:
        if not spec.get("container"):
            raise RuntimeError("Restricted containers cannot receive host plugin projection")
        spec["container"]["related_plugins"] = [selection_spec(value) for value in sources]
    return spec


def host_gh_token() -> str | None:
    """Fetch the host's GitHub token via ``gh auth token`` (or None)."""
    try:
        res = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_creation_flags(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    token = res.stdout.strip()
    return token or None


def copilot_cli_config_path() -> Path:
    """Resolve this host's own Copilot CLI config.json path.

    ``AGENT_CONTAINERS_COPILOT_CLI_CONFIG`` overrides it (tests, non-default
    installs); otherwise ``~/.copilot/config.json``, matching Copilot CLI's
    own default.
    """
    override = os.environ.get("AGENT_CONTAINERS_COPILOT_CLI_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".copilot" / "config.json"


def _auth_token_for_account(data: object, account: str) -> str | None:
    """Pull ``account``'s token out of an already-parsed config.json body.

    Defensive against a syntactically valid but structurally wrong document
    (e.g. a top-level list, or ``authTokens`` itself not an object) -- both
    must return None, never raise, so callers' shared missing-login error
    fires instead of an unhandled AttributeError.
    """
    if not isinstance(data, dict):
        return None
    auth_tokens = data.get("authTokens")
    if not isinstance(auth_tokens, dict):
        return None
    entry = auth_tokens.get(f"https://github.com:{account}")
    token = entry.get("token") if isinstance(entry, dict) else None
    return token or None


def host_copilot_cli_token(
    account: str, config_path: Path | str | None = None
) -> str | None:
    """Fetch ``account``'s live github.com OAuth token from this host's own
    Copilot CLI login (``~/.copilot/config.json``'s ``authTokens``), or None.

    This is a DIFFERENT credential source than :func:`host_gh_token` (which
    reads ``gh``'s own OAuth store): Copilot CLI keeps its own login state,
    completely independent of ``gh auth status`` -- an operator can be logged
    into Copilot CLI as one account while ``gh`` (git/PR identity) stays a
    different one. This is intentionally NOT vault-managed: it is a tier-3
    interactive-OAuth token (see the facility's own four-tier auth-model
    convention, where applicable) that the operator keeps current themselves
    by running ``copilot login`` as ``account`` on this host; this function
    only ever reads the live current value, never mints or refreshes one.

    The file is JSONC (``//``-prefixed comment lines); those are stripped
    before parsing. Returns None on any missing file, parse failure,
    malformed document shape, or absent/empty entry for ``account`` --
    callers should fail closed rather than launch a container with no
    Copilot inference auth.
    """
    path = Path(config_path) if config_path is not None else copilot_cli_config_path()
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    cleaned = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("//")
    )
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return _auth_token_for_account(data, account)


def copilot_cli_account_missing_hint(account: str, container_name: str) -> str:
    """Shared guidance string for a missing ``copilot_cli_account`` login.

    Used by every launch site that fails (closed or soft) when
    ``copilot_cli_account`` is configured but :func:`host_copilot_cli_token`
    found nothing -- keeps the three call sites' messages identical and out
    of their own line-count budget.
    """
    return (
        f"copilot_cli_account is set to '{account}' but no matching Copilot "
        f"CLI login was found on this host -- run `copilot login` as "
        f"{account} on this host, or `agent-containers copilot-login "
        f"{container_name} --account {account}` to bootstrap one via "
        "device-code auth inside the container"
    )


def resolve_copilot_cli_token_or_raise(config, fleet, container_name: str) -> str | None:
    """Return ``config.copilot_cli_account_for(fleet)``'s live host token.

    None if no account is configured for this fleet (the common case --
    callers should simply skip forwarding ``COPILOT_GITHUB_TOKEN``). Raises
    RuntimeError with the shared missing-login hint if an account IS
    configured but :func:`host_copilot_cli_token` finds nothing. Used by
    EVERY launch site, with no soft-fail variant: silently falling back to
    GH_TOKEN's identity when a separate inference identity was explicitly
    configured would defeat the entire purpose of this setting.
    """
    account = config.copilot_cli_account_for(fleet)
    if not account:
        return None
    token = host_copilot_cli_token(account)
    if not token:
        raise RuntimeError(copilot_cli_account_missing_hint(account, container_name))
    return token


def copilot_cli_token_from_json_text(account: str, raw: str) -> str | None:
    """Extract ``account``'s token from an already-read config.json's text.

    Same JSONC-comment-stripping/parse logic as :func:`host_copilot_cli_token`,
    but takes the text directly -- used by the manual device-code-in-container
    bootstrap flow (``agent-containers copilot-login``), which reads the
    CONTAINER's own freshly-logged-in config.json via ``docker exec``, not a
    local file.
    """
    cleaned = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("//")
    )
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return _auth_token_for_account(data, account)


def import_copilot_cli_token(
    account: str, token: str, config_path: Path | str | None = None
) -> None:
    """Merge ``account``'s token into this HOST's own Copilot CLI config.json.

    Used only by the manual ``agent-containers copilot-login`` bootstrap: once
    an operator completes a device-code login INSIDE a container (because no
    live login for ``account`` existed on this host), this writes that same
    token into the host's own ``authTokens``/``loggedInUsers`` so every future
    launch finds it the normal way (:func:`host_copilot_cli_token`), without
    repeating the in-container login. Preserves every other key in the file
    and any leading ``//`` comment-header lines; round-trips the JSON body
    through ``json.dumps`` (so interior comments elsewhere in the body, if
    any, would NOT survive -- Copilot CLI itself only ever writes a leading
    comment header in this file, confirmed by direct inspection, so this is
    safe in practice). A genuinely missing/empty file initializes a new
    document; an EXISTING file whose body fails to parse (or isn't a JSON
    object) raises instead of silently discarding it -- never clobber a
    real, if malformed, document with an empty one. Writes atomically through
    a per-call uniquely named temp file (``os.getpid()`` + a random hex
    suffix -- never a fixed, predictable name two concurrent invocations or
    a symlink-planting adversary could race) created with ``O_CREAT|O_EXCL``
    with requested mode 0600: the umask may remove owner bits but never
    grant group/world access. Exact owner access is restored before any
    credential bytes are written (ACL-backed platforms retain their
    existing permission model), then the temp file is atomically
    replaced over the real path. Cooperating imports serialize the entire
    read/merge/write transaction using a persistent sibling lock file.
    This lock does not coordinate unrelated writers such as Copilot CLI.
    """
    path = Path(config_path) if config_path is not None else copilot_cli_config_path()
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with private_file_lock(path.with_name(f".{path.name}.copilot-login.lock")):
        _import_copilot_cli_token_locked(account, token, path)


def _import_copilot_cli_token_locked(account: str, token: str, path: Path) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = ""
    lines = raw.splitlines()
    header_lines: list[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("//") or not line.strip():
            header_lines.append(line)
            body_start = i + 1
        else:
            break
    body_text = "\n".join(lines[body_start:]).strip()
    if not body_text:
        # Genuinely missing/empty file: safe to initialize a new document.
        data: dict = {}
    else:
        try:
            data = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"'{path}' exists but its JSON body could not be parsed -- "
                "refusing to overwrite a config.json we can't safely merge "
                f"into (parse error: {exc})"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(
                f"'{path}' exists but its JSON body is not an object -- "
                "refusing to overwrite it"
            )
    auth_tokens = data.setdefault("authTokens", {})
    if not isinstance(auth_tokens, dict):
        auth_tokens = {}
        data["authTokens"] = auth_tokens
    key = f"https://github.com:{account}"
    auth_tokens[key] = {"token": token}
    logged_in = data.get("loggedInUsers")
    if not isinstance(logged_in, list):
        logged_in = []
    if not any(
        isinstance(u, dict)
        and u.get("host") == "https://github.com"
        and u.get("login") == account
        for u in logged_in
    ):
        logged_in.append({"host": "https://github.com", "login": account})
    data["loggedInUsers"] = logged_in

    rendered = "\n".join(header_lines + [json.dumps(data, indent=2)]) + "\n"
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = -1
    created = False
    try:
        fd = os.open(str(tmp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        if os.name != "nt" and strict_posix_modes(tmp_path):
            os.fchmod(fd, 0o600)
        else:
            enforce_mode(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        tmp_path.replace(path)
        created = False  # replaced onto `path` -- nothing left at tmp_path to clean up
        fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        # Only remove a temp path THIS call actually created (and didn't
        # already consume via replace()) -- never unlink on an O_EXCL
        # creation failure, which would mean some other, unrelated file
        # already occupied this (vanishingly unlikely, pid+uuid4-named) path.
        if created:
            tmp_path.unlink(missing_ok=True)


def build_spawn_command(
    container: str,
    user: str,
    acp_command: str,
    forward_token: bool,
    relay_env: list[str] | None = None,
) -> list[str]:
    """Build the ``docker exec`` spawn command (token referenced by name).

    Used by the ``agent-containers exec`` transport wrapper (see __main__),
    NOT returned directly to agent-bridge. ``relay_env`` names additional env
    vars (e.g. LC_GIT_CREDENTIAL_RELAY*) to forward by name from the wrapper's
    process env, so their secret values never land in argv or logs.
    """
    cmd = ["docker", "exec", "-i"]
    if forward_token:
        # Reference by name only -- value comes from the process env, so it is
        # never written to argv or the agent-bridge log.
        cmd += ["-e", "GH_TOKEN"]
    for name in relay_env or []:
        cmd += ["-e", name]
    cmd += ["-u", user, container, "bash", "-lc", acp_command]
    return cmd


def build_restricted_spawn_command(
    container: str,
    user: str,
    acp_command: str,
    *,
    login: bool = True,
) -> list[str]:
    """Build the restricted ``docker exec`` command with no host projection.

    This API deliberately accepts no token, relay, SSH, mount, or network
    arguments. The live Docker posture is validated before this command runs.
    """
    return [
        "docker",
        "exec",
        "-i",
        "-u",
        user,
        container,
        "bash",
        "-lc" if login else "-c",
        acp_command,
    ]


AGENT_CONTAINERS_EXEC_COPILOT_ARGS_ENV = "AGENT_CONTAINERS_EXEC_COPILOT_ARGS"


def build_restricted_inference_spawn_command(
    container: str,
    user: str,
    acp_command: str,
    profile: NativeInferenceConfig,
) -> list[str]:
    """Add only a validated broker origin and nonsecret reference to ACP."""
    command = build_restricted_spawn_command(container, user, acp_command)
    projected: list[str] = []
    for name, value in profile.environment().items():
        projected.extend(["-e", f"{name}={value}"])
    return command[:3] + projected + command[3:]


def build_restricted_acp_spawn_command(
    container: str,
    user: str,
    acp_command: str,
    fleet: FleetConfig,
) -> list[str]:
    """Select the explicit ACP profile without changing generic restricted exec."""
    fleet.validate_restricted()
    if fleet.native_inference is not None:
        return build_restricted_inference_spawn_command(
            container, user, acp_command, fleet.native_inference,
        )
    return build_restricted_spawn_command(container, user, acp_command)


def _append_copilot_args(acp_command: str, extra: list[str] | None) -> str:
    """Append caller-supplied copilot CLI args onto a fleet's configured
    ``acp_command`` shell string, shell-quoting each one.

    This is the container-side half of forwarding a venue/charter overlay
    (``--agent <charter>``, etc.) into a container session -- the other half
    is ``agent_bridge.transport.spawn_raw`` appending ``target.copilot_args``
    onto the ``agent-containers exec`` invocation when spawning a
    container-backed target. Returns ``acp_command`` unchanged when ``extra``
    is empty or ``None``, so this is a no-op for every existing caller that
    never passes extra args.
    """
    if not extra:
        return acp_command
    return acp_command + " " + " ".join(shlex.quote(a) for a in extra)


def resolve_extra_copilot_args(cli_args: list[str] | None) -> list[str] | None:
    """The extra copilot args ``exec`` should forward, env-first.

    ``agent_bridge.transport.spawn_raw`` forwards a charter overlay via
    ``AGENT_CONTAINERS_EXEC_COPILOT_ARGS`` (JSON-encoded), never trailing
    argv -- on Windows the wrapper is often a ``.cmd`` shim routed through
    ``cmd.exe``, which reparses argv metacharacters but passes the
    environment block through untouched. The ``copilot_args`` positional
    stays as a direct-CLI convenience (manual/test invocations), used only
    when the env var is absent.
    """
    raw = os.environ.get(AGENT_CONTAINERS_EXEC_COPILOT_ARGS_ENV)
    if raw:
        decoded = json.loads(raw)
        if not isinstance(decoded, list) or not all(
            isinstance(a, str) for a in decoded
        ):
            raise ValueError(
                f"{AGENT_CONTAINERS_EXEC_COPILOT_ARGS_ENV} must be a JSON "
                f"array of strings, got: {raw!r}"
            )
        return decoded
    return cli_args


def build_wrapper_command(name: str) -> list[str]:
    """Build the spawn command agent-bridge runs for a ``container:`` agent.

    Delegates to ``agent-containers exec --stdio <name>`` rather than docker
    directly. The wrapper fetches the host ``gh`` token at spawn time and
    injects it into the container's environment, so the token NEVER lands in
    the SpawnTarget (which agent-bridge persists to its SQLite DB) or in any log.

    Uses the exact payload-local shim instead of a machine-global command so the
    same selected installation cell owns local and remote execution.
    """
    return [*payload_command_argv(), "exec", "--stdio", name]


def resolve_live_exec_target(
    name: str,
    *,
    config: ContainersConfig | None = None,
    dispatch_binding_id: str | None = None,
    dispatch_management: bool = False,
) -> LiveExecTarget:
    """Resolve one running fleet member and validate its live trust posture."""
    config = config or load_config()
    try:
        info = lifecycle.get_container(config, name)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Container lookup failed for '{name}'; refusing dispatch: {exc}"
        ) from exc
    if info is None:
        raise RuntimeError(
            f"Container '{name}' is not a discovered fleet member; refusing dispatch"
        )

    inspected = lifecycle.inspect_container(name)
    actual_profile = (
        (inspected.get("Config") or {}).get("Labels") or {}
    ).get(SECURITY_PROFILE_LABEL)
    fleet = config.fleets.get(getattr(info, "fleet", "") or "")
    if fleet is None:
        raise RuntimeError(
            f"Container '{name}' has no matching fleet configuration; "
            "refusing dispatch"
        )
    if actual_profile not in {TRUSTED_PROFILE, RESTRICTED_PROFILE}:
        raise RuntimeError(
            f"Container '{name}' has unsupported live security profile "
            f"{actual_profile!r}; refusing dispatch"
        )
    if fleet.security_profile != actual_profile:
        raise RuntimeError(
            f"Container '{name}' security profile does not match its fleet "
            f"(configured={fleet.security_profile!r}, live={actual_profile!r}); "
            "refusing dispatch"
        )
    discovered_id = getattr(info, "container_id", None)
    inspected_id = inspected.get("Id")
    if not discovered_id or not inspected_id or discovered_id != inspected_id:
        raise RuntimeError(
            f"Container '{name}' changed identity during validation; "
            "refusing dispatch"
        )
    state = getattr(info, "state", "running")
    is_running = bool(getattr(info, "is_running", state == "running"))
    if not is_running:
        raise RuntimeError(
            f"Container '{name}' is not ready (state={state!r}); refusing dispatch"
        )

    workspace = fleet.workspace_folder or config.workspace_folder
    user = fleet.exec_user or config.exec_user
    if actual_profile == RESTRICTED_PROFILE:
        posture_errors = lifecycle.restricted_policy_errors(
            info,
            fleet,
            workspace_folder=workspace,
            exec_user=user,
            inspected=inspected,
        )
        if posture_errors:
            raise RuntimeError(
                f"Container '{name}' does not satisfy the restricted "
                f"security policy: {'; '.join(posture_errors)}"
            )
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    dedicated = labels.get(DISPATCH_ONLY_LABEL) == "true"
    if dedicated != fleet.dispatch_only:
        raise RuntimeError("Container dispatch-only creation policy does not match configuration")
    if dedicated and not dispatch_management:
        if not dispatch_binding_id:
            raise RuntimeError("Dispatch-only container requires its retained task binding")
        from .dispatch_projection import verify_binding

        binding = verify_binding(inspected_id, dispatch_binding_id)
        if labels.get(DISPATCH_BINDING_LABEL) != binding["binding_id"]:
            raise RuntimeError("Container dispatch birth identity does not match retained authority")
    elif dispatch_binding_id and not dedicated:
        raise RuntimeError("Task bindings require a dispatch-only container")

    return LiveExecTarget(
        name=name,
        container_id=inspected_id,
        config=config,
        fleet=fleet,
        info=info,
        actual_profile=actual_profile,
        user=user,
        workspace_folder=workspace,
        acp_command=config.acp_command_for(fleet),
    )


class ContainerResolver:
    """Namespace resolver for ``container:<name>`` agent routing."""

    @property
    def prefix(self) -> str:
        return "container"

    async def resolve(self, name: str) -> SpawnTarget:
        """Resolve a container name to a SpawnTarget over its venue transport.

        Thin wrapper over :meth:`resolve_spec` (the agent_bridge-free data path,
        also the ``namespace-resolve`` CLI seam for #892 Inc 3b). Note the narrow
        ``resolve(self, name)`` signature -- containers do not support cross-repo
        dispatch or plugin injection, which agent-bridge honors by introspecting
        this signature.
        """
        from agent_bridge.transport import SpawnTarget

        spec = await self.resolve_spec(name)
        return SpawnTarget(
            type=spec.get("type", "command"),
            spawn_command=spec["spawn_command"],
            user=spec.get("user"),
            container=spec.get("container"),
            venue=spec.get("venue"),
        )

    async def target_repo(self, name: str) -> str | None:
        config = load_config()
        info = await asyncio.to_thread(get_container, config, name)
        fleet = config.fleets.get(info.fleet or "") if info else None
        if getattr(info, "security_profile", None) == RESTRICTED_PROFILE or (
            fleet is not None and fleet.restricted
        ):
            return None
        return (getattr(info, "repo", None) or getattr(fleet, "repo", None)) or None

    async def resolve_spec(self, name: str) -> dict:
        """Resolve a container name to a **plain-dict** spawn spec.

        The agent_bridge-free core of :meth:`resolve` -- returns
        ``{"type","spawn_command","user","workspace_folder","security_profile",
        "venue"}`` using only agent-containers + stdlib, so the
        ``agent-containers namespace-resolve`` CLI can emit it as JSON and
        agent-bridge reconstructs the ``SpawnTarget`` across a process boundary
        (#892 Inc 3b). ``workspace_folder`` is the container's concrete repo
        checkout (for the ACP session cwd); ``security_profile`` is the fleet's
        trust posture (``trusted``/``restricted``) that gates any host->venue
        projection. Raises ``KeyError`` when the container is not in the fleet.
        """
        from .dispatch_target import parse, resolve

        if parse(name) is not None:
            return await asyncio.to_thread(resolve, name)
        config = load_config()
        info = await asyncio.to_thread(get_container, config, name)
        if info is None:
            members = await asyncio.to_thread(list_containers, config)
            raise KeyError(
                f"Container '{name}' not found. "
                f"Fleet members: {[c.name for c in members]}"
            )

        # Advisory lease check -- log, do not block (enforcement=advisory).
        lease = await asyncio.to_thread(get_lease, name)
        if lease is not None:
            log.info(
                "container:%s is leased by effort '%s' (host=%s) -- "
                "dispatching anyway (advisory leases)",
                name, lease.effort, lease.host,
            )

        fleet = config.fleets.get(info.fleet or "")
        if fleet is None:
            raise KeyError(
                f"Container '{name}' has no matching fleet configuration"
            )
        user = (fleet.exec_user if fleet else None) or config.exec_user

        # Spawn the transport wrapper, not docker directly. The wrapper fetches
        # the gh token at spawn time, keeping it out of the persisted SpawnTarget.
        spawn_cmd = build_wrapper_command(name)
        log.info("Resolved container:%s -> %s", name, " ".join(spawn_cmd))
        # Surface the venue's concrete workspace folder + trust posture so the
        # bridge can (a) set the ACP session cwd to the repo checkout instead of
        # the home-dir default, and (b) gate any host->venue projection (repo-own
        # plugins, etc.) on the fleet's trust posture -- a `restricted` fleet is a
        # fail-closed boundary and must never be silently upgraded (venue-parity).
        workspace_folder = fleet.workspace_folder or config.workspace_folder
        spec = {
            "type": "command",
            "spawn_command": spawn_cmd,
            "user": user,
            "workspace_folder": workspace_folder,
            "security_profile": fleet.security_profile,
        }
        actual_profile = getattr(
            info, "security_profile", fleet.security_profile,
        )
        supported_profile = actual_profile in {TRUSTED_PROFILE, RESTRICTED_PROFILE}
        restricted = fleet.restricted or actual_profile != TRUSTED_PROFILE
        hold_status = (
            await asyncio.to_thread(deploy_hold_status, name)
            if restricted
            else {"state": "none", "operation": None, "reason": None}
        )
        effective_profile = RESTRICTED_PROFILE if restricted else actual_profile
        profile_mismatch = (
            not supported_profile or fleet.security_profile != actual_profile
        )
        forward_gh, relay_enabled = config.credentials_for(fleet)
        if restricted or profile_mismatch:
            forward_gh, relay_enabled = False, False
        spec["venue"] = {
            "schema_version": VENUE_SCHEMA_VERSION,
            "provider": VENUE_PROVIDER,
            "kind": self.prefix,
            "target_id": f"{self.prefix}:{name}",
            "scope": "provider-instance",
            "instance_id": getattr(info, "container_id", None),
            "fleet": info.fleet,
            "workspace_folder": workspace_folder,
            # Backward-compatible trust key used by existing SpawnTarget
            # consumers. It is the fail-closed maximum of configured/observed
            # posture, not an attestation that the full policy was inspected.
            "security_profile": effective_profile,
            "configured_security_profile": fleet.security_profile,
            "observed_security_profile": actual_profile,
            "effective_security_profile": effective_profile,
            "state": getattr(info, "state", "unknown"),
            "ready": (
                bool(getattr(info, "is_running", False))
                and not profile_mismatch
                and hold_status["state"] == "none"
            ),
            "posture_verified": False,
            "transport": "provider-exec" if restricted else "ssh",
            "capabilities": {
                "container_local_workspace": True,
                "host_credentials": forward_gh,
                "credential_relay": relay_enabled,
                "session_host": not restricted,
                "ssh_profile": (
                    fleet.security_profile == RESTRICTED_PROFILE
                    and actual_profile == RESTRICTED_PROFILE
                ),
            },
        }
        if restricted:
            spec["venue"]["lifecycle_hold"] = hold_status
        if fleet.dispatch_only:
            spec["venue"]["dispatch_only"] = True
            spec["venue"]["ready"] = False
            spec["venue"]["capabilities"]["task_projection"] = True
        if not restricted:
            ssh_config = await asyncio.to_thread(
                prepare_ssh_config,
                name,
                user,
            )
            spec["container"] = {
                "name": name,
                "repo": getattr(info, "repo", None) or fleet.repo,
                "instance_id": getattr(info, "container_id", None),
                "workspace_folder": workspace_folder,
                "security_profile": fleet.security_profile,
                "user": user,
                "acp_command": config.acp_command_for(fleet),
                "ssh": asdict(ssh_config),
                "provider_command": payload_command_argv(),
                "relay_remote_port": (
                    config.relay_port if relay_enabled else None
                ),
            }
        return spec

    async def list(self) -> list[NamespaceAgentInfo]:
        """List fleet containers as namespace agent info (in-process path)."""
        from agent_bridge.agent_registry import NamespaceAgentInfo

        return [NamespaceAgentInfo(**spec) for spec in await self.list_specs()]

    async def list_specs(self) -> list[dict]:
        """List fleet containers as **plain-dict** agent specs.

        The agent_bridge-free core of :meth:`list` -- the ``namespace-list`` CLI
        seam emits these as JSON and agent-bridge reconstructs
        ``NamespaceAgentInfo`` across a process boundary (#892 Inc 3b).
        """
        config = load_config()
        containers = await asyncio.to_thread(list_containers, config)
        agents = []
        for c in containers:
            lease = await asyncio.to_thread(get_lease, c.name)
            fleet = config.fleets.get(c.fleet or "")
            hold_status = (
                await asyncio.to_thread(deploy_hold_status, c.name)
                if (fleet and fleet.restricted)
                or c.security_profile == RESTRICTED_PROFILE
                else {"state": "none", "operation": None, "reason": None}
            )
            repo = c.repo or (c.fleet or "")
            display = f"{c.name} ({repo})" if repo else c.name
            description = f"Local dev container: {c.image}"
            description += f" — {c.security_profile}"
            if lease:
                description += f" — leased by {lease.effort}"
            # Map docker state to a coarse ready/stopped signal.
            state = "running" if c.is_running else (c.state or "unknown")
            if hold_status["state"] != "none":
                state = "draining"
            agents.append({
                "name": c.name,
                "display_name": display,
                "description": description,
                "icon": "container",
                "state": state,
            })
        return agents

    async def ensure_ready(self, name: str) -> None:
        """Ensure the container exists and is running (start if stopped)."""
        from .dispatch_target import parse, resolve

        if parse(name) is not None:
            await asyncio.to_thread(resolve, name)
            return
        config = load_config()
        info = await asyncio.to_thread(get_container, config, name)
        if info is None:
            raise RuntimeError(f"Container '{name}' is not a discovered fleet member")
        fleet = config.fleets.get(info.fleet or "")
        if fleet is None:
            raise RuntimeError(
                f"Container '{name}' has no matching fleet configuration"
            )
        actual_profile = getattr(info, "security_profile", "")
        if actual_profile not in {TRUSTED_PROFILE, RESTRICTED_PROFILE}:
            raise RuntimeError(
                f"Container '{name}' has unsupported live security profile "
                f"{actual_profile!r}"
            )
        if fleet.security_profile != actual_profile:
            raise RuntimeError(
                f"Container '{name}' security profile does not match its fleet "
                f"(configured={fleet.security_profile!r}, live={actual_profile!r})"
            )
        if fleet.dispatch_only:
            raise RuntimeError("Dispatch-only container requires explicit task preparation, not ordinary readiness")
        if fleet.restricted:
            deploy_hold = await asyncio.to_thread(get_deploy_hold, name)
            if deploy_hold:
                raise RuntimeError(
                    f"Container '{name}' is unavailable while provider "
                    f"{deploy_hold.operation} is in progress"
                )
        if fleet.restricted or info.security_profile == "restricted":
            errors = await asyncio.to_thread(
                restricted_policy_errors,
                info,
                fleet,
                workspace_folder=fleet.workspace_folder or config.workspace_folder,
                exec_user=fleet.exec_user or config.exec_user,
            )
            if errors:
                raise RuntimeError(
                    f"Container '{name}' does not satisfy the restricted "
                    f"security policy: {'; '.join(errors)}"
                )
        state = await asyncio.to_thread(inspect_state, name)
        if state is None:
            raise RuntimeError(f"Container '{name}' not found")
        if state == "running":
            return
        log.info("Container '%s' is '%s' -- starting", name, state)
        await asyncio.to_thread(start_container, name)

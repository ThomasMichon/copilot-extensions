"""One-launch trusted Copilot authentication, never a manager environment."""

from __future__ import annotations

import json
import posixpath
import shlex

from . import ssh_transport as transport
from .copilot_auth_policy import LEGACY, trusted_auth_mode
from .launch_storage import directory_guard, executable_directory
from .resolver import resolve_copilot_cli_token_or_raise

TOKEN_VARIABLE = "COPILOT_GITHUB_TOKEN"


def without_inference_token(environment: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if key != TOKEN_VARIABLE}


def launcher_directory(auth_env: str) -> str:
    return executable_directory(auth_env)


def stage_env(container, user, values, writer, *, require_owner=False, config=None, fleet=None):
    preserve_legacy = (
        config is not None
        and getattr(fleet, "security_profile", "trusted") != "restricted"
        and trusted_auth_mode(config, fleet) == LEGACY
    )
    values = dict(values) if preserve_legacy else without_inference_token(values)
    if require_owner and not values:
        values = {"AGENT_CONTAINERS_LAUNCH_INPUTS": "1"}
    return writer(container, user, values)


def wrap_command(command: str, auth_env: str | None, *, manager: bool = False) -> str:
    if not auth_env:
        return command
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    words = list(lexer)
    if words[:1] == ["cd"]:
        valid_cd = (
            len(words) >= 4 and words[2] == "&&"
            and "$(" not in words[1] and "`" not in words[1]
        )
        words = words[3:] if valid_cd else []
    if words[:1] == ["exec"]:
        words = words[1:]
    copilot_executable = (
        words[:1] == ["copilot"]
        or words[:2] == ["{{target_preference_launcher}}", "copilot"]
    )
    if not manager and (
        not copilot_executable
        or any(word in {";", "&", "&&", "|", "||", "(", ")", "<", ">", ">>"}
               or "$(" in word or "`" in word for word in words[1:])
        or "\n" in command
    ):
        raise RuntimeError(
            "process-only Copilot authentication requires the PATH-resolved "
            "`copilot` command or target-preference launcher, not a custom "
            "or absolute executable override"
        )
    # PATH selects the narrow executable only; neither the Host nor embody gets
    # the inference token. The executable restores PATH before starting Copilot.
    directory = shlex.quote(launcher_directory(auth_env))
    if manager:
        launcher = shlex.quote("--copilot-process-launcher=" + launcher_directory(auth_env) + "/copilot")
        return (
            f"unset {TOKEN_VARIABLE}; {command} {launcher}"
        )
    return f'unset {TOKEN_VARIABLE}; export PATH={directory}:"$PATH"; {command}'


def wrap_manager_shell(command: str, auth_env: str | None, remote_env: str | None) -> str:
    words = shlex.split(command)
    if len(words) == 3 and words[:2] == ["bash", "-lc"]:
        command = words[2]
    if auth_env:
        command += " " + shlex.quote("--launch-result-file=" + launcher_directory(auth_env) + "/result.json")
    return transport.build_remote_command(wrap_command(command, auth_env, manager=True), remote_env)


def created_child(result: object) -> bool:
    if (
        not isinstance(result, dict)
        or type(result.get("created")) is not bool
        or type(result.get("resumed")) is not bool
        or result["created"] == result["resumed"]
    ):
        raise RuntimeError("Copilot launch returned no explicit create-or-resume receipt")
    return result["created"]


def attached_created_child(container: str, user: str, auth_env: str) -> bool:
    result = transport._run([
        "docker", "exec", "-u", user, container,
        "cat", launcher_directory(auth_env) + "/result.json",
    ])
    if result.returncode:
        raise RuntimeError("Copilot attach returned no create-or-resume receipt")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Copilot attach returned an invalid launch receipt") from exc
    if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1:
        raise RuntimeError("Copilot attach returned an unsupported launch receipt")
    return created_child(payload)


def wait_for_consumption(
    container: str, user: str, auth_env: str | None, *, timeout: float = 180,
) -> None:
    if not auth_env:
        return
    path = shlex.quote(launcher_directory(auth_env) + "/consumed")
    attempts = max(1, min(int(timeout), 600))
    result = transport._run([
        "docker", "exec", "-u", user, container, "sh", "-c",
        f"attempt=0; while test \"$attempt\" -lt {attempts}; do if test -f {path} "
        f'&& test "$(cat {path})" = 1; then '
        "echo COPILOT_AUTH_CONSUMED; exit 0; fi; "
        "attempt=$((attempt + 1)); sleep 1; done; exit 1",
    ], timeout=attempts + 5)
    if result.returncode or result.stdout.strip() != "COPILOT_AUTH_CONSUMED":
        raise RuntimeError("Copilot child did not confirm consuming its launch credential")


def prepare_launch(
    config, fleet, container: str, user: str, *, cleanup_owner: str | None = None,
) -> str | None:
    """Re-read the existing account source immediately before a launch."""
    mode = trusted_auth_mode(config, fleet)
    if mode is None or mode == LEGACY:
        return None
    if not config.copilot_cli_account_for(fleet):
        raise RuntimeError(
            "host-copilot mode requires an intended copilot_cli_account; "
            "refusing GH_TOKEN/GITHUB_TOKEN or repository-auth fallback. "
            "Select the intended host Copilot account, or explicitly opt into legacy mode."
        )
    if cleanup_owner:
        checked = transport._run([
            "docker", "exec", "-u", user, container, "sh", "-c",
            directory_guard(posixpath.dirname(cleanup_owner)),
        ])
        if checked.returncode:
            raise RuntimeError("Copilot launch owner is not on private RAM-backed storage")
    token = resolve_copilot_cli_token_or_raise(config, fleet, container)
    if token is None:
        raise RuntimeError("host-copilot source returned no token; refusing repository-auth fallback")
    probe = transport._run([
        "docker", "exec", "-u", user, container, "bash", "-lc",
        "unset COPILOT_GITHUB_TOKEN; "
        "native=$(command -v copilot) && test -n \"$native\" && "
        "help=$(\"$native\" --help) && "
        "printf '%s\\n' \"$help\" | grep -q -- '--secret-env-vars' && "
        "printf '%s\\n' \"$native\"",
    ])
    native = probe.stdout.strip()
    if probe.returncode or not native.startswith("/") or "\n" in native:
        raise RuntimeError(
            "trusted Copilot launch requires a native CLI supporting "
            "--secret-env-vars; the target help probe did not confirm it"
        )
    auth_env = transport.write_remote_env(container, user, {TOKEN_VARIABLE: token})
    if not auth_env:
        raise RuntimeError("could not stage process-only Copilot authentication")
    staged_auth = auth_env
    if cleanup_owner:
        if posixpath.dirname(cleanup_owner) != posixpath.dirname(staged_auth):
            transport.cleanup_remote_env(container, user, staged_auth)
            raise RuntimeError("Copilot authentication and launch owner require the same RAM directory")
        auth_env = cleanup_owner + ".d/auth.env"
    directory = launcher_directory(auth_env)
    script = launcher_script(native, auth_env)
    move = (
        f"ln -T -- {shlex.quote(staged_auth)} {shlex.quote(auth_env)}\n"
        f"rm -- {shlex.quote(staged_auth)}\n"
        if cleanup_owner else ""
    )
    auth_directory = (
        f"mkdir {shlex.quote(posixpath.dirname(auth_env))}\n"
        if cleanup_owner else ""
    )
    try:
        result = transport._run([
            "docker", "exec", "-i", "-u", user, container, "sh", "-c",
            directory_guard(posixpath.dirname(staged_auth)) +
            auth_directory + move +
            directory_guard(posixpath.dirname(directory), create=True, require_ram=False) +
            f"mkdir {shlex.quote(directory)} && "
            f"cat > {shlex.quote(directory + '/copilot')} && "
            f"chmod 700 {shlex.quote(directory + '/copilot')}",
        ], input_text=script)
        if result.returncode:
            raise RuntimeError("could not stage process-only Copilot launcher")
    except BaseException:
        transport.cleanup_remote_env(container, user, staged_auth)
        transport.cleanup_remote_env(container, user, cleanup_owner)
        raise
    return auth_env


def prepare_acp_launch(config, fleet, container, user, command, remote_env):
    auth_env = None
    try:
        auth_env = prepare_launch(config, fleet, container, user, cleanup_owner=remote_env)
        return auth_env, wrap_command(command, auth_env)
    except Exception:
        transport.cleanup_remote_env(container, user, remote_env)
        transport.cleanup_remote_env(container, user, auth_env)
        raise


def launcher_script(native: str, auth_env: str) -> str:
    """Help/version remain credential-free; the real child consumes one token."""
    directory = launcher_directory(auth_env)
    return (
        "#!/bin/sh\nset -eu\n"
        f"launcher_dir={shlex.quote(directory)}\n"
        'case "$PATH" in "$launcher_dir") PATH="";; '
        '"$launcher_dir":*) PATH=${PATH#"$launcher_dir":};; esac\nexport PATH\n'
        f"unset {TOKEN_VARIABLE}\n"
        'case "${1:-}" in --help|--version) '
        f'exec {shlex.quote(native)} "$@";; esac\n'
        'for arg do case "$arg" in --secret-env-vars*|--) '
        "echo 'Copilot launch cannot override required secret-env isolation' >&2; "
        "exit 1;; esac; done\n"
        "umask 077\n"
        "start=$(sed 's/.*) //' /proc/$$/stat | awk '{print $20}')\n"
        "test -n \"$start\" || exit 1\n"
        f'printf "%s %s\\n" "$$" "$start" > {shlex.quote(directory + "/active.pid.tmp")}\n'
        f"mv -- {shlex.quote(directory + '/active.pid.tmp')} {shlex.quote(directory + '/active.pid')}\n"
        f"test -r {shlex.quote(auth_env)} || {{ "
        "echo 'Copilot launch credential has already been consumed' >&2; exit 1; }\n"
        f". {shlex.quote(auth_env)}\n"
        f'test -n "${{{TOKEN_VARIABLE}:-}}" || exit 1\n'
        f"rm -f -- {shlex.quote(auth_env)}\n"
        f"printf '1\\n' > {shlex.quote(directory + '/consumed')}\n"
        f"exec {shlex.quote(native)} "
        f'--secret-env-vars={TOKEN_VARIABLE} "$@"\n'
    )

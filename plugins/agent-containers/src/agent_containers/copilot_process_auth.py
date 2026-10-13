"""One-launch trusted Copilot authentication, never a manager environment."""

from __future__ import annotations

import shlex

from . import ssh_transport as transport
from .resolver import resolve_copilot_cli_token_or_raise

TOKEN_VARIABLE = "COPILOT_GITHUB_TOKEN"


def without_inference_token(environment: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if key != TOKEN_VARIABLE}


def stage_env(container, user, values, writer):
    return writer(container, user, without_inference_token(values))


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
    if not manager and (
        not words or words[0] not in {"copilot", "{{target_preference_launcher}}"}
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
    directory = shlex.quote(auth_env + ".d")
    if manager:
        launcher = shlex.quote("--copilot-process-launcher=" + auth_env + ".d/copilot")
        return (
            f"unset {TOKEN_VARIABLE}; {command} {launcher}"
        )
    return f'unset {TOKEN_VARIABLE}; export PATH={directory}:"$PATH"; {command}'


def wrap_manager_shell(command: str, auth_env: str | None, remote_env: str | None) -> str:
    words = shlex.split(command)
    if len(words) == 3 and words[:2] == ["bash", "-lc"]:
        command = words[2]
    return transport.build_remote_command(wrap_command(command, auth_env, manager=True), remote_env)


def wait_for_consumption(
    container: str, user: str, auth_env: str | None, *, timeout: float = 180,
) -> None:
    if not auth_env:
        return
    path = shlex.quote(auth_env)
    attempts = max(1, min(int(timeout), 600))
    result = transport._run([
        "docker", "exec", "-u", user, container, "sh", "-c",
        f"attempt=0; while test \"$attempt\" -lt {attempts}; do if test ! -e {path}; then "
        "echo COPILOT_AUTH_CONSUMED; exit 0; fi; "
        "attempt=$((attempt + 1)); sleep 1; done; exit 1",
    ], timeout=attempts + 5)
    if result.returncode or result.stdout.strip() != "COPILOT_AUTH_CONSUMED":
        raise RuntimeError("Copilot child did not confirm consuming its launch credential")


def prepare_launch(config, fleet, container: str, user: str) -> str | None:
    """Re-read the existing account source immediately before a launch."""
    token = resolve_copilot_cli_token_or_raise(config, fleet, container)
    if token is None:
        return None
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
    directory = auth_env + ".d"
    script = launcher_script(native, auth_env)
    result = transport._run([
        "docker", "exec", "-i", "-u", user, container, "sh", "-c",
        f"umask 077; mkdir {shlex.quote(directory)} && "
        f"cat > {shlex.quote(directory + '/copilot')} && "
        f"chmod 700 {shlex.quote(directory + '/copilot')}",
    ], input_text=script)
    if result.returncode:
        transport.cleanup_remote_env(container, user, auth_env)
        raise RuntimeError("could not stage process-only Copilot launcher")
    return auth_env


def prepare_acp_launch(config, fleet, container, user, command, remote_env):
    auth_env = None
    try:
        auth_env = prepare_launch(config, fleet, container, user)
        return auth_env, wrap_command(command, auth_env)
    except Exception:
        transport.cleanup_remote_env(container, user, remote_env)
        transport.cleanup_remote_env(container, user, auth_env)
        raise


def launcher_script(native: str, auth_env: str) -> str:
    """Help/version remain credential-free; the real child consumes one token."""
    return (
        "#!/bin/sh\nset -eu\n"
        f"PATH=${{PATH#{shlex.quote(auth_env + '.d')}:}}\nexport PATH\n"
        f"unset {TOKEN_VARIABLE}\n"
        'case "${1:-}" in --help|--version) '
        f'exec {shlex.quote(native)} "$@";; esac\n'
        'for arg do case "$arg" in --secret-env-vars*|--) '
        "echo 'Copilot launch cannot override required secret-env isolation' >&2; "
        "exit 1;; esac; done\n"
        f"test -r {shlex.quote(auth_env)} || {{ "
        "echo 'Copilot launch credential has already been consumed' >&2; exit 1; }\n"
        f". {shlex.quote(auth_env)}\n"
        f"rm -f -- {shlex.quote(auth_env)}\n"
        f'test -n "${{{TOKEN_VARIABLE}:-}}" || exit 1\n'
        f"exec {shlex.quote(native)} "
        f'--secret-env-vars={TOKEN_VARIABLE} "$@"\n'
    )

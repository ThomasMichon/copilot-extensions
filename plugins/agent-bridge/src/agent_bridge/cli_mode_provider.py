"""CLI-mode creation through the selected venue owner's process boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

_CLI_MODE_VENUE_BINSTUBS = {
    "codespace": "agent-codespaces",
    "container": "agent-containers",
    "ssh": "agent-ssh",
}


def _portable_provider_run(argv: list[str], **kwargs):
    from agent_procutil import no_window_kwargs

    if os.name == "nt" and Path(argv[0]).suffix.lower() == ".ps1":
        environment = dict(os.environ)
        environment["AGENT_BRIDGE_PROVIDER_ARGV"] = json.dumps(argv)
        kwargs["env"] = environment
        argv = [
            str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
            "$a=ConvertFrom-Json $env:AGENT_BRIDGE_PROVIDER_ARGV; "
            "$p=$a[0]; $rest=@($a | Select-Object -Skip 1); & $p @rest; exit $LASTEXITCODE",
        ]
    for key, value in no_window_kwargs().items():
        kwargs[key] = kwargs.get(key, 0) | value
    text = kwargs.pop("text", False)
    if text and isinstance(kwargs.get("input"), str):
        kwargs["input"] = kwargs["input"].encode("utf-8")
    result = subprocess.run(argv, **kwargs)
    if text:
        for field in ("stdout", "stderr"):
            value = getattr(result, field, None)
            if isinstance(value, bytes):
                setattr(result, field, value.decode("utf-8"))
    return result


def _cmd_create_cli(
    target: str, prompt: str | None, driver: str | None, *, detach: bool = False,
    worktree_id: str | None = None,
    receiving_support: str | None = None,
) -> None:
    """Deliver CLI-mode creation to its venue owner, retaining stdin seed input."""
    from .session_targeting_cli import _companion_seed_prompt

    prompt = _companion_seed_prompt(prompt)
    prefix, sep, name = target.partition(":")
    binstub = _CLI_MODE_VENUE_BINSTUBS.get(prefix) if sep else None
    if not binstub or not name:
        print(
            f"[FAIL] --cli requires a namespaced venue target "
            f"({'/'.join(f'{p}:<name>' for p in _CLI_MODE_VENUE_BINSTUBS)}), "
            f"got {target!r}.\n"
            "       For a local session, use `agent-worktrees copilot "
            "--anchor` (or `--worktree-id <id>`) directly -- agent-bridge's "
            "mediation isn't needed on this machine.",
            file=sys.stderr,
        )
        sys.exit(2)
    if receiving_support and (receiving_support != "portable" or prefix != "codespace" or not detach or worktree_id):
        print("[FAIL] Portable receiving requires --cli --detach on an ordinary CodeSpace workspace.", file=sys.stderr)
        sys.exit(2)
    provider_command = []
    if receiving_support:
        from ._peer_launch import launch_prefix, validate_owner
        from .session_lifecycle_cli import _agent_bridge_installation_context, _agent_bridge_owner_root

        context = _agent_bridge_installation_context()
        if context:
            own = validate_owner("agent-bridge", _agent_bridge_owner_root(), context)
            provider_command = launch_prefix(
                "agent-bridge", Path(own["pluginRoot"]),
                context, "agent-codespaces",
            )
    resolved = None
    if not provider_command:
        if receiving_support and os.name == "nt":
            resolved = shutil.which(binstub + ".ps1")
        resolved = resolved or shutil.which(binstub)
    if not resolved and not provider_command:
        print(
            f"[FAIL] '{binstub}' not found on PATH -- is it installed?",
            file=sys.stderr,
        )
        sys.exit(1)
    provider_command = provider_command or [resolved]
    if receiving_support and os.name == "nt" and Path(provider_command[0]).suffix.lower() in {".cmd", ".bat"}:
        print("[FAIL] Portable receiving requires the provider's native PowerShell shim on Windows.", file=sys.stderr)
        sys.exit(2)
    run_provider = _portable_provider_run if receiving_support else subprocess.run
    argv = [*provider_command, "copilot", name]
    if receiving_support:
        argv += ["--receiving-support", receiving_support]
    if worktree_id and prefix == "ssh":
        print("[FAIL] SSH CLI selects its configured --workspace; --worktree-id is not supported.", file=sys.stderr)
        sys.exit(2)
    if worktree_id and prefix in {"codespace", "container"}:
        argv += ["--worktree-id", worktree_id]
    if prefix in {"codespace", "container", "ssh"}:
        from .local_cache_refresh import _checkout_root
        from .related_plugins import control_plane_anchors, related_plugins_for_repo
        from agent_procutil import no_window_flags
        from plugin_resolve.reflection import selection_cli_args

        roots = control_plane_anchors()
        try:
            controller = _checkout_root(Path.cwd()).resolve(strict=True)
            if controller.is_dir() and (controller / ".git").exists():
                roots = list(dict.fromkeys([controller, *roots]))
        except OSError:
            pass
        if roots:
            repository = run_provider(
                [*provider_command, "namespace-target-repo", name], capture_output=True, text=True,
                check=True, timeout=90, creationflags=no_window_flags(),
            ).stdout.strip()
            if prefix == "ssh" and repository:
                argv += ["--expected-repository", repository]
            for plugin in related_plugins_for_repo(
                repository, anchors=roots,
                **({"match_registered_remote": True} if prefix == "ssh" else {}),
            ) if repository else []:
                argv += selection_cli_args(plugin.selection())
            for root in roots:
                argv += ["--plugin-root", str(root)]
    if detach:
        argv.append("--detach")
        if prompt:
            argv += ["--seed-file", "-"]
        if driver:
            argv += ["--driver", driver]
        sys.exit(run_provider(argv, input=prompt or None, text=True).returncode)
    if prompt:
        argv += ["--seed", prompt]
    if driver:
        argv += ["--driver", driver]
    sys.exit(subprocess.run(argv).returncode)

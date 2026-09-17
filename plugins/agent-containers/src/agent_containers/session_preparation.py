"""Trusted container SSH, token and credential-relay preparation."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from .ssh_transport import build_remote_command, cleanup_remote_envs, container_environment, prepare_ssh_config, write_remote_env
from .resolver import host_gh_token

def _prepare_session_host(args: argparse.Namespace) -> dict:
    """Prepare endpoint + auth inputs; agent-bridge owns the Host lifecycle."""
    from .__main__ import (
        _trusted_session_host_context, _relay_healthy, build_remote_command,
        cleanup_remote_envs, container_environment, prepare_ssh_config, write_remote_env, host_gh_token,
    )
    from ._invoke import payload_command_argv
    from .container_shims import (
        deploy as deploy_shims,
    )
    from .container_shims import (
        git_credential_environment,
    )
    from .relay_provider import token_for

    from . import native_claims, lease
    with lease._lease_lock():
        native_claims.assert_access(args.name, getattr(args, "native_identity", None))
    config, fleet, user, workspace = _trusted_session_host_context(args.name)
    ssh_config = prepare_ssh_config(args.name, user)
    cleanup_remote_envs(args.name, user)
    launch_env = container_environment(args.name, user)

    forward, relay_enabled = config.credentials_for(fleet)
    if getattr(args, "require_relay", False) and not relay_enabled:
        raise RuntimeError("native hosting requires the configured trusted-container relay")
    if forward:
        github_token = host_gh_token()
        if not github_token:
            raise RuntimeError(
                "forward_gh_token is enabled but `gh auth token` returned nothing"
            )
        launch_env["GH_TOKEN"] = github_token

    reverse_forwards: list[str] = []
    if relay_enabled:
        if not args.host_relay_port or not 1 <= args.host_relay_port <= 65535:
            raise RuntimeError(
                "credential relay is enabled but no valid --host-relay-port "
                "was supplied"
            )
        if not _relay_healthy(args.host_relay_port):
            raise RuntimeError(
                f"credential relay on 127.0.0.1:{args.host_relay_port} "
                "did not answer the identity probe"
            )
        deploy_shims(args.name, ado=True)
        launch_env["LC_GIT_CREDENTIAL_RELAY_HOST"] = "127.0.0.1"
        launch_env["LC_GIT_CREDENTIAL_RELAY"] = str(config.relay_port)
        launch_env["LC_GIT_CREDENTIAL_RELAY_TOKEN"] = token_for(args.name)
        launch_env.update(git_credential_environment())
        reverse_forwards.append(
            f"{config.relay_port}:127.0.0.1:{args.host_relay_port}"
        )

    remote_env = write_remote_env(args.name, user, launch_env)
    acp_command = config.acp_command_for(fleet)
    remote_command = build_remote_command(
        acp_command,
        remote_env,
    )
    return {
        "name": args.name,
        "workspace_folder": workspace,
        "security_profile": fleet.security_profile,
        "user": user,
        "ssh": asdict(ssh_config),
        "acp_command": acp_command,
        "remote_command": remote_command,
        "remote_env": remote_env,
        "reverse_forwards": reverse_forwards,
        "state_command": [*payload_command_argv(), "session-host-state", args.name],
    }

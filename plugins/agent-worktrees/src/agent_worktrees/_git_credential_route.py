"""Read-only update credential router, invoked with the immutable slot interpreter."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping

from .repo_accounts import remote_host
from .update_auth_process import publish_group, run

SECRET_KEY = "pass" + "word"
NATIVE_KEY = "CE_UPDATE_AUTH_NATIVE_CREDENTIAL"
NON_INTERACTIVE = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def parse_attributes(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def format_attributes(attributes: Mapping[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in attributes.items()) + "\n"


def _has_credentials(attributes: Mapping[str, str]) -> bool:
    return SECRET_KEY in attributes or bool(
        attributes.get("authtype") and attributes.get("credential")
    )


def env_token(owner: str, env: Mapping[str, str], host: str = "github.com") -> str:
    host = remote_host(host)
    configured_host = env.get("GH_HOST", "")
    enterprise = host != "github.com"
    if enterprise and (not configured_host or remote_host(configured_host) != host):
        return ""
    key = "GH_TOKEN_" + re.sub(r"[^A-Z0-9]", "_", owner.upper())
    names = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN") if enterprise else ()
    return env.get(key) or next(
        (env[name] for name in (*names, "GH_TOKEN", "GITHUB_TOKEN") if env.get(name)), ""
    )


def helper_timeout(env: Mapping[str, str]) -> float:
    return min(5.0, max(0.01, float(env.get("CE_UPDATE_AUTH_HELPER_TIMEOUT_MS", "5000")) / 1000))


def _config_key(key: str) -> bool:
    return key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS") or key.startswith(
        ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
    )


def original_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Remove only this router's command-scope additions, not the caller's config."""
    raw = env.get("CE_UPDATE_AUTH_BASE_CONFIG")
    if raw is None:
        return dict(env)
    config = json.loads(raw)
    if not isinstance(config, dict) or any(
        not _config_key(key) or not isinstance(value, str) for key, value in config.items()
    ):
        raise ValueError("invalid original Git command configuration")
    return {**{key: value for key, value in env.items() if not _config_key(key)}, **config}


def base_config(env: Mapping[str, str]) -> str:
    return json.dumps(
        {key: value for key, value in original_environment(env).items() if _config_key(key)}
    )


def run_chain(
    operation: str,
    request: str,
    env: Mapping[str, str],
) -> str:
    # Let Git replay its own configuration at the child's actual cwd. Rebuilding
    # a host-only helper list loses URL-specific helpers, includes and reset order.
    # Keep repeated capability/state attributes intact across the native protocol.
    command = {"get": "fill", "store": "approve", "erase": "reject"}[operation]
    result = run(
        ["git", "credential", command],
        env={**original_environment(env), **NON_INTERACTIVE},
        input=request,
        timeout=helper_timeout(env),
    )
    return result.stdout if result.status == 0 and not result.truncated else ""


def route(operation: str, stdin: str, env: Mapping[str, str]) -> str:
    attributes = parse_attributes(stdin)
    host = attributes.get("host", "").lower()
    if attributes.get("protocol", "https") == "https":
        host = remote_host(host)
    owner = attributes.get("path", "").split("/")[0].lower()
    routes = json.loads(env.get("CE_UPDATE_AUTH_ROUTES", "{}"))
    # Host-qualified keys prevent the same owner spelling on another server
    # from receiving a credential intended for this GitHub host.
    repository = attributes.get("path", "").strip("/").removesuffix(".git").lower()
    selected = routes.get(host + "/" + repository)
    if selected is not None:
        if operation != "get" or selected["method"] in ("deny", "anonymous"):
            return ""
        if selected["method"] == "native":
            native = json.loads(env[NATIVE_KEY])
            if not isinstance(native, dict) or set(native) != {"authtype", "credential"} or any(
                not isinstance(value, str) or not value for value in native.values()
            ):
                raise ValueError("invalid native probe credential")
            response = {key: value for key, value in attributes.items() if key != "capability[]"}
            return "capability[]=authtype\n" + format_attributes({**response, **native})
        if selected["method"] == "fill":
            account = selected.get("account")
            request = stdin.rstrip("\n") + f"\nusername={account}\n\n" if account else stdin
            filled = run_chain("get", request, env)
            return filled if _has_credentials(parse_attributes(filled)) else ""
        account = selected.get("account") or "x-access-token"
        if selected["method"] == "env":
            secret = env_token(owner, env, host)
        elif selected["method"] == "gh":
            args = ["gh", "auth", "token", "--hostname", host, "--user", account]
            token_env = {
                key: value
                for key, value in env.items()
                if key
                not in (
                    "GH_TOKEN",
                    "GITHUB_TOKEN",
                    "GH_ENTERPRISE_TOKEN",
                    "GITHUB_ENTERPRISE_TOKEN",
                )
            }
            token = run(args, env=token_env, timeout=helper_timeout(env))
            secret = token.stdout.strip() if token.status == 0 and not token.truncated else ""
        else:
            raise ValueError("unknown update credential route")
        return (
            format_attributes({**attributes, "username": account, SECRET_KEY: secret})
            if (secret)
            else ""
        )
    if operation not in ("get", "store", "erase"):
        return ""
    if operation != "get" and env.get("CE_UPDATE_AUTH_READONLY") == "1":
        return ""
    filled = run_chain(operation, stdin, env)
    response = parse_attributes(filled)
    return (
        filled
        if operation == "get" and (_has_credentials(response) or response.get("quit"))
        else ""
    )


def main() -> int:
    publish_group()
    try:
        response = route(
            sys.argv[1] if len(sys.argv) > 1 else "get", sys.stdin.read(1024 * 1024), os.environ
        )
    except (OSError, ValueError, RuntimeError):
        # Never print helper attributes, argv, or exceptions containing credentials.
        print("update credential routing failed", file=sys.stderr)
        return 1
    sys.stdout.write(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

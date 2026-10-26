"""Read-only source authentication and failure-only, command-scope credential routing."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from ._git_credential_route import (
    NON_INTERACTIVE,
    NATIVE_KEY,
    SECRET_KEY,
    base_config,
    env_token,
    original_environment,
    parse_attributes,
)
from .repo_urls import remote_host
from .update_auth_process import module_argv, run


class ResolverUnavailable(RuntimeError):
    pass


class ProbeUnavailable(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Source:
    id: str
    url: str
    host: str
    owner: str
    repo: str
    required: bool = True
    purpose: str = "marketplace"
    cwd: Path | None = None

    @property
    def route_key(self) -> str:
        return "/".join((self.host, self.owner, self.repo)).lower()


def source(
    id: str,
    url: str,
    *,
    required: bool = True,
    purpose: str = "marketplace",
    cwd: Path | None = None,
) -> Source | None:
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or len(parts) != 2
        or parsed.query
        or parsed.fragment
    ):
        return None
    if not parsed.hostname or any(not re.fullmatch(r"[\w.-]+", part) for part in parts):
        return None
    try:
        host = remote_host(parsed.netloc)
    except ValueError:
        return None
    return Source(id, url, host, *parts, required, purpose, cwd)


@dataclasses.dataclass
class SourceAuth:
    source: Source
    helper_chain: list[str]
    expected_account: str | None = None
    visibility: str = "unknown"
    as_configured: str = "skipped"
    alternative: dict[str, str] | None = None
    status: str = "unavailable"
    fix: str = ""
    duration_ms: int = 0

    def summary(self) -> dict:
        return {
            "id": self.source.id,
            "host": self.source.host,
            "owner": self.source.owner,
            "required": self.source.required,
            "expected_account": self.expected_account,
            "visibility": self.visibility,
            "as_configured": self.as_configured,
            "alternative": self.alternative,
            "status": self.status,
            "fix": self.fix,
            "duration_ms": self.duration_ms,
        }


@dataclasses.dataclass
class AuthReport:
    results: list[SourceAuth]

    @property
    def failing(self) -> list[SourceAuth]:
        return [
            item
            for item in self.results
            if item.source.required and item.status not in ("ok", "anonymous")
        ]

    def summary(self) -> dict:
        return {
            "results": [item.summary() for item in self.results],
            "all_ok": not self.failing,
            "failing": [item.source.id for item in self.failing],
            "fixable": [
                item.source.id for item in self.failing if item.status == "fixable_by_fallback"
            ],
        }


def command_scope(env: Mapping[str, str], entries: Sequence[tuple[str, str]]) -> dict[str, str]:
    base = int(env.get("GIT_CONFIG_COUNT", "0"))
    if base < 0:
        raise ValueError("GIT_CONFIG_COUNT must not be negative")
    for index in range(base):
        if f"GIT_CONFIG_KEY_{index}" not in env or f"GIT_CONFIG_VALUE_{index}" not in env:
            raise ValueError("incomplete inherited Git command configuration")
    result = {"GIT_CONFIG_COUNT": str(base + len(entries))}
    for index, (key, value) in enumerate(entries, base):
        result[f"GIT_CONFIG_KEY_{index}"] = key
        result[f"GIT_CONFIG_VALUE_{index}"] = value
    if "GIT_CONFIG_PARAMETERS" in env:
        # Git applies parameters after counted entries. Append routing last using
        # Git's single-quote encoding, retaining original parameters for replay.
        parameters = " ".join(
            "'" + f"{key}={value}".replace("'", "'\\''") + "'" for key, value in entries
        )
        result["GIT_CONFIG_PARAMETERS"] = env["GIT_CONFIG_PARAMETERS"] + " " + parameters
    return result


def router_helper(python: str = sys.executable) -> str:
    argv = module_argv("agent_worktrees._git_credential_route", python=python)
    argv[0] = argv[0].replace("\\", "/")
    return "!" + shlex.join(argv)


def non_repository_environment(env: Mapping[str, str], cwd: Path) -> dict[str, str]:
    """Keep global/command credentials without discovering a caller's checkout."""
    from . import git_ops

    original = original_environment(env)
    selected = git_ops.repository_identity_env(original)
    selected.update(json.loads(base_config(original)))
    if "GIT_CONFIG_PARAMETERS" in original:
        selected["GIT_CONFIG_PARAMETERS"] = original["GIT_CONFIG_PARAMETERS"]
    selected.pop("CE_UPDATE_AUTH_BASE_CONFIG", None)
    selected["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
    return selected


def helper_chain(source: Source, env: Mapping[str, str], runner=run) -> list[str]:
    # Diagnostic values only: Git itself owns execution and matching in the router.
    result = runner(
        ["git", "config", "--null", "--get-all", "credential.helper"],
        cwd=source.cwd,
        env=env,
    )
    if result.missing:
        raise ResolverUnavailable("git is not available")
    if result.timed_out or result.truncated or result.status not in (0, 1):
        raise ProbeUnavailable("cannot read effective Git credential configuration")
    helpers: list[str] = []
    for value in result.stdout.removesuffix("\0").split("\0") if result.stdout else []:
        if not value:
            helpers.clear()
        else:
            helpers.append(value)
    return helpers


def _has_extra_headers(src: Source, env: Mapping[str, str], runner) -> bool:
    result = runner(
        ["git", "config", "--null", "--get-urlmatch", "http.extraHeader", src.url],
        cwd=src.cwd, env=env,
    )
    if result.missing:
        raise ResolverUnavailable("git is not available")
    if result.timed_out or result.truncated or result.status not in (0, 1):
        raise ProbeUnavailable("cannot read effective Git HTTP header configuration")
    return any(result.stdout.split("\0")) if result.status == 0 else False


def _probe(
    item: SourceAuth,
    env: Mapping[str, str],
    anonymous: bool,
    runner,
    *,
    candidate: str | None = None,
    account: str | None = None,
    native: Mapping[str, str] | None = None,
) -> str:
    src = item.source
    helper = "" if anonymous else router_helper()
    clear_headers = anonymous or candidate is not None or native is not None
    entries = ([(f"http.{src.url}.extraHeader", "")] if clear_headers else []) + [
        (f"credential.https://{src.host}.useHttpPath", "true"),
        ("credential.helper", ""),
        (f"credential.https://{src.host}.helper", ""),
        (f"credential.https://{src.host}.helper", helper),
    ]
    probe_env = {
        **env,
        **NON_INTERACTIVE,
        **command_scope(env, entries),
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "CE_UPDATE_AUTH_READONLY": "1",
        "CE_UPDATE_AUTH_BASE_CONFIG": base_config(env),
        "CE_UPDATE_AUTH_ROUTES": json.dumps({
            src.route_key: {"method": "native" if native is not None else "env", "account": account}
        })
        if candidate is not None or native is not None
        else "{}",
    }
    if native is not None:
        probe_env[NATIVE_KEY] = json.dumps(native)
    if candidate is not None:
        key = "GH_TOKEN_" + re.sub(r"[^A-Z0-9]", "_", src.owner.upper())
        probe_env[key] = candidate
        probe_env["GH_HOST"] = src.host
    result = runner(["git", "ls-remote", src.url, "HEAD"], cwd=src.cwd, env=probe_env)
    if result.missing:
        raise ResolverUnavailable("git is not available")
    return (
        "timeout"
        if result.timed_out
        else ("ok" if result.status == 0 and not result.truncated else "failed")
    )


def _check_token(secret: str, src: Source, expected: str | None, env, runner) -> dict[str, str]:
    token_env = {**env, "GH_TOKEN": secret, "GH_ENTERPRISE_TOKEN": secret, "GH_HOST": src.host}
    login = runner(["gh", "api", "--hostname", src.host, "user", "--jq", ".login"], env=token_env)
    if login.missing:
        return {"status": "unavailable"}
    if login.timed_out:
        return {"status": "timeout"}
    observed = login.stdout.strip()
    if login.status != 0 or not observed or login.truncated:
        return {"status": "denied"}
    if expected and observed.casefold() != expected.casefold():
        return {"status": "mismatch", "observed_login": observed}
    git_status = _probe(
        SourceAuth(src, []), env, False, runner, candidate=secret, account=expected
    )
    return {
        "status": "timeout"
        if git_status == "timeout"
        else ("ok" if git_status == "ok" else "denied"),
        "observed_login": observed,
    }


def _check_native(
    credential: Mapping[str, str], src: Source, expected: str | None, env, runner,
) -> dict[str, str]:
    identity = runner(
        module_argv("agent_worktrees.update_auth_probe"),
        input=json.dumps({"host": src.host, **credential}), env=env,
    )
    if identity.timed_out:
        return {"status": "timeout"}
    if identity.missing or identity.truncated or identity.status != 0:
        return {"status": "unavailable"}
    try:
        payload = json.loads(identity.stdout)
    except ValueError:
        return {"status": "unavailable"}
    if not isinstance(payload, dict):
        return {"status": "unavailable"}
    observed = payload.get("login")
    if payload.get("status") != "ok" or not isinstance(observed, str) or not observed:
        status = payload.get("status")
        return {"status": status if status in ("denied", "timeout", "unavailable") else "unavailable"}
    if expected and observed.casefold() != expected.casefold():
        return {"status": "mismatch", "observed_login": observed}
    contents = _probe(SourceAuth(src, []), env, False, runner, native=credential)
    return {
        "status": "timeout" if contents == "timeout" else "ok" if contents == "ok" else "denied",
        "observed_login": observed,
    }


def _alternatives(item: SourceAuth, env: Mapping[str, str], runner) -> list[dict[str, str]]:
    src, expected = item.source, item.expected_account
    attempts = []
    secret = env_token(src.owner, env, src.host)
    if secret:
        attempts.append({"method": "env", **_check_token(secret, src, expected, env, runner)})
        if attempts[-1]["status"] == "ok":
            return attempts
    if expected:
        token_env = {
            key: value
            for key, value in env.items()
            if key
            not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
        }
        token = runner(
            ["gh", "auth", "token", "--hostname", src.host, "--user", expected], env=token_env
        )
        check = {
            "status": "unavailable"
            if token.missing
            else "timeout"
            if token.timed_out
            else "denied"
        }
        if token.status == 0 and token.stdout.strip() and not token.truncated:
            check = _check_token(token.stdout.strip(), src, expected, env, runner)
        attempts.append({"method": "gh", **check})
        if check["status"] == "ok":
            return attempts
    fill_env = {
        **env,
        **NON_INTERACTIVE,
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "CE_UPDATE_AUTH_READONLY": "1",
        "CE_UPDATE_AUTH_ROUTES": "{}",
        "CE_UPDATE_AUTH_BASE_CONFIG": base_config(env),
    }
    entries = [
        ("credential.helper", ""),
        (f"credential.https://{src.host}.helper", ""),
        (f"credential.https://{src.host}.helper", router_helper()),
        (f"credential.https://{src.host}.useHttpPath", "true"),
    ]
    fill_env.update(command_scope(env, entries))
    parsed = urlsplit(src.url)
    attributes = (
        "capability[]=authtype\n"
        f"protocol=https\nhost={parsed.netloc}\npath={parsed.path.lstrip('/')}\n"
    )
    if expected:
        attributes += f"username={expected}\n"
    fill = runner(
        ["git", "credential", "fill"], env=fill_env, cwd=src.cwd, input=attributes + "\n"
    )
    filled = (
        parse_attributes(fill.stdout)
        if fill.status == 0 and not fill.truncated and not fill.timed_out else {}
    )
    secret = filled.get(SECRET_KEY)
    check = {"status": "timeout" if fill.timed_out else "denied"}
    if filled.get("authtype") and filled.get("credential"):
        check = _check_native(
            {key: filled[key] for key in ("authtype", "credential")}, src, expected, env, runner,
        )
    elif secret:
        check = _check_token(secret, src, expected, env, runner)
    attempts.append({"method": "fill", **check})
    return attempts


def report(
    sources: Sequence[Source],
    *,
    env: Mapping[str, str] | None = None,
    account_for: Callable[[str], str | None] | None = None,
    runner=run,
) -> AuthReport:
    from . import git_ops, repos

    environment = original_environment(os.environ if env is None else env)
    results = []
    for src in sources:
        started = time.monotonic()
        probe_env = dict(environment)
        if src.purpose == "anchor":
            probe_env = git_ops.repository_identity_env(probe_env)
            probe_env.pop("CE_UPDATE_AUTH_BASE_CONFIG", None)
        try:
            chain = helper_chain(src, probe_env, runner)
            public = _probe(SourceAuth(src, chain), probe_env, True, runner) == "ok"
            has_headers = public and _has_extra_headers(src, probe_env, runner)
        except ProbeUnavailable as error:
            item = SourceAuth(src, [], status="unavailable", fix=str(error))
            item.duration_ms = round((time.monotonic() - started) * 1000)
            results.append(item)
            continue
        item = SourceAuth(src, chain)
        if public:
            item.visibility, item.status = "public", "anonymous"
            if has_headers:
                item.as_configured = _probe(item, probe_env, False, runner)
                if item.as_configured != "ok":
                    item.status = "fixable_by_fallback"
                    item.alternative = {"method": "anonymous", "status": "ok"}
        else:
            item.expected_account = (
                account_for(f"{src.owner}/{src.repo}") if account_for is not None
                else repos.account_for_remote(src.url)
            )
            item.as_configured = _probe(item, probe_env, False, runner)
            if item.as_configured == "ok":
                item.visibility, item.status = "private", "ok"
            else:
                attempts = _alternatives(item, probe_env, runner)
                item.alternative = next((a for a in attempts if a["status"] == "ok"), None)
                if item.alternative:
                    item.status = "fixable_by_fallback"
                else:
                    item.alternative = next(iter(attempts), None)
                    item.status = (
                        "mismatch"
                        if any(a["status"] == "mismatch" for a in attempts)
                        else ("timeout" if item.as_configured == "timeout" else "auth_required")
                    )
                    who = f"sign in as {item.expected_account}; " if item.expected_account else ""
                    item.fix = f"gh auth login --hostname {src.host} --web ({who}never switch the active account)"
        item.duration_ms = round((time.monotonic() - started) * 1000)
        results.append(item)
    return AuthReport(results)


@dataclasses.dataclass(frozen=True)
class RunOverlay:
    entries: list[tuple[str, str]]
    variables: dict[str, str]
    applies_to: list[str]
    denies: list[str]

    def environment(self, env: Mapping[str, str]) -> dict[str, str]:
        original = original_environment(env)
        return {
            **original,
            **self.variables,
            **command_scope(original, self.entries),
            "CE_UPDATE_AUTH_BASE_CONFIG": base_config(original),
        }


def fallback(
    auth: AuthReport,
    *,
    apply_fixes: bool = True,
    python: str = sys.executable,
) -> RunOverlay | None:
    if not auth.failing:
        return None
    routes, hosts = {}, set()
    applied, denied = [], []
    entries = [("credential.interactive", "never")]
    for item in auth.failing:
        src = item.source
        if src.host.lower() not in hosts:
            hosts.add(src.host.lower())
            prefix = f"credential.https://{src.host}"
            entries.extend(
                [
                    (prefix + ".useHttpPath", "true"),
                    (prefix + ".helper", ""),
                    (prefix + ".helper", router_helper(python)),
                ]
            )
        fix = apply_fixes and item.status == "fixable_by_fallback"
        if fix:
            entries.append((f"http.{src.url}.extraHeader", ""))
        route = (
            {"account": item.expected_account, "method": item.alternative["method"]}
            if (fix)
            else {"method": "deny"}
        )
        if src.route_key in routes and routes[src.route_key] != route:
            route = {"method": "deny"}  # one owner cannot safely select two accounts
        routes[src.route_key] = route
    for item in auth.failing:
        (denied if routes[item.source.route_key]["method"] == "deny" else applied).append(
            item.source.id
        )
    variables = {
        **NON_INTERACTIVE,
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "CE_UPDATE_AUTH_ROUTES": json.dumps(routes),
    }
    return RunOverlay(entries, variables, applied, denied)

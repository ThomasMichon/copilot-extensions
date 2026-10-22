"""Read-only account-aware authentication state for the pool contribution."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from agent_procutil import no_window_flags

from .config import RUNTIME_DIR
from .auth_preflight import gh_account_failures, parse_gh_account_scopes


def auth_root() -> Path:
    return RUNTIME_DIR / "github-auth"


def authorized_configs() -> dict[str, Path]:
    """Only identity-verified vendor configurations are eligible credentials."""
    result: dict[str, Path] = {}
    for marker in sorted(auth_root().glob("*/accepted-login")):
        login = marker.read_text(encoding="utf-8").strip()
        if login:
            result[login] = marker.parent
    return result


def config_env(path: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env.pop(key, None)
    env["GH_CONFIG_DIR"] = str(path)
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_HOST"] = "github.com"
    return env


def credential_env(login: str) -> dict[str, str] | None:
    return next(
        (config_env(path) for account, path in authorized_configs().items()
         if account.casefold() == login.casefold()), None,
    )


def status(env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        capture_output=True, text=True, timeout=15,
        creationflags=no_window_flags(), env=env,
    )
    return result.returncode, result.stdout + result.stderr


def classify(rc: int, text: str, login: str) -> dict[str, str]:
    scopes = {key.casefold(): value for key, value in parse_gh_account_scopes(text).items()}
    failure = gh_account_failures(text).get(login.casefold())
    if failure == "timeout":
        state, message = "error", "GitHub authentication verification timed out; refresh to retry."
    elif failure == "failed" or login.casefold() not in scopes:
        # A provider/network error is not evidence that authorization is missing.
        if rc and not any(word in text.lower() for word in (
            "not logged", "not authenticated", "log into", "auth login", "failed to log in",
        )):
            state, message = "error", "GitHub authentication could not be verified; refresh to retry."
        else:
            state, message = "auth_required", "Sign in to GitHub to view CodeSpaces."
    elif "codespace" not in {scope.casefold() for scope in scopes[login.casefold()]}:
        state, message = "scope_blocked", "Authorize the codespace scope to view CodeSpaces."
    else:
        state, message = "ready", ""
    return {"status": state, "account": login, "message": message}


def probe() -> dict[str, object]:
    """Derive relevant accounts without listing CodeSpaces or starting OAuth."""
    from . import account_binding, gh_account
    from .config import load_merged_config

    try:
        accounts = list(account_binding.bound_accounts_or_raise())
        ambient = False
        for repo in load_merged_config(include_cwd=False).repos:
            if login := gh_account.account_for_repo(repo):
                accounts.append(login)
            else:
                ambient = True
        global_rc, global_text = status()
        active = _active_login(global_text)
        if active and (ambient or not accounts):
            accounts.append(active)
        configs = authorized_configs()
        accounts.extend(configs)
        accounts = list(dict.fromkeys(accounts))
        if not accounts:
            accounts = [""]
        results = []
        for login in accounts:
            env = credential_env(login) if login else None
            rc, text = status(env) if env is not None else (global_rc, global_text)
            results.append(classify(rc, text, login))
        blocked = next((row for row in results if row["status"] != "ready"), None)
        selected = blocked or results[0]
        return {**selected, "accounts": results}
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        return {
            "status": "error", "account": "", "accounts": [],
            "message": f"GitHub authentication probe failed ({type(exc).__name__}); refresh to retry.",
        }


def _active_login(text: str) -> str | None:
    current = None
    for line in text.splitlines():
        if match := re.search(r"\baccount\s+([A-Za-z0-9][A-Za-z0-9-]*)", line):
            current = match.group(1)
        if "active account: true" in line.lower():
            return current
    accounts = parse_gh_account_scopes(text)
    return next(iter(accounts)) if len(accounts) == 1 else None


def summary(auth: dict[str, object]) -> dict[str, object]:
    return {
        "auth": auth,
        "auth_status": auth["status"],
        "auth_account": auth["account"],
        "banner_text": auth["message"],
        "banner_level": "error" if auth["status"] == "error" else "warn",
    }


def blocked_payload(auth: dict[str, object]) -> dict[str, object]:
    return {"entries": [], "summary": summary(auth)}


def frames(payload: dict[str, object]):
    yield {"type": "begin"}
    for entry in payload["entries"]:
        yield {"type": "row", "entry": entry}
    yield {"type": "summary", "summary": payload["summary"]}
    yield {"type": "done"}

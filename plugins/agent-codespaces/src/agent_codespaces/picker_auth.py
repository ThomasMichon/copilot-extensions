"""Read-only account-aware authentication state for the pool contribution."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent_procutil import no_window_flags
import yaml

from .config import RUNTIME_DIR
from .auth_preflight import gh_account_failures, parse_gh_account_scopes
from . import oauth_device


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


def credential_token(login: str) -> str | None:
    from .credential_store import load

    for account, path in authorized_configs().items():
        if account.casefold() != login.casefold():
            continue
        return load(path, account)
    return None


def status(env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        capture_output=True, text=True, timeout=15,
        creationflags=no_window_flags(), env=env,
    )
    return result.returncode, result.stdout + result.stderr


def classify(rc: int, text: str, login: str) -> dict[str, str]:
    scopes = {key.casefold(): value for key, value in parse_gh_account_scopes(text).items()}
    failures = gh_account_failures(text)
    failure = failures.get(login.casefold())
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
    elif rc and not failures:
        state, message = "error", "GitHub authentication could not be verified; refresh to retry."
    elif "codespace" not in {scope.casefold() for scope in scopes[login.casefold()]}:
        state, message = "scope_blocked", "Authorize the codespace scope to view CodeSpaces."
    else:
        state, message = "ready", ""
    return {"status": state, "account": login, "message": message}


def probe() -> dict[str, object]:
    """Derive relevant accounts without listing CodeSpaces or starting OAuth."""
    from .account_candidates import candidates

    try:
        global_rc, global_text = status()
        results = []
        for account in candidates():
            login = account or ""
            try:
                credential = credential_token(login) if login else None
                if credential is None:
                    results.append(classify(global_rc, global_text, login))
                else:
                    results.append(oauth_device.verify(credential, login))
            except oauth_device.CredentialUnavailable as exc:
                results.append({"status": "auth_required", "account": login, "message": str(exc)})
        blocked = next((row for row in results if row["status"] != "ready"), None)
        selected = blocked or results[0]
        return {**selected, "accounts": results}
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        return {
            "status": "error", "account": "", "accounts": [],
            "message": f"GitHub authentication probe failed ({type(exc).__name__}); refresh to retry.",
        }

def summary(auth: dict[str, object]) -> dict[str, object]:
    message = auth["message"]
    if message and auth["account"]:
        message = f"{auth['account']}: {message}"
    return {
        "auth": auth,
        "auth_status": auth["status"],
        "auth_account": auth["account"],
        "banner_text": message,
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

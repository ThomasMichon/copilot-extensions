"""Use ordinary GitHub credentials only for plugin-management children."""

from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from .execution import GIT_REPOSITORY_SELECTORS, execution_environment, execution_executable
from .target import github_repository_slug
from .receiver import capture

_TOKEN_KEYS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


def _github_url(definition: Mapping[str, object]) -> str | None:
    origin = definition.get("source")
    if not isinstance(origin, dict):
        return None
    kind = origin.get("source")
    location = origin.get("repo" if kind == "github" else "url")
    if not isinstance(location, str):
        return None
    url = f"https://github.com/{location}" if kind == "github" else location
    if kind not in {"github", "git"} or not url.lower().startswith("https://"):
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.hostname is None
            or parsed.hostname.casefold() not in {"github.com", "www.github.com"}
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or github_repository_slug(f"https://{parsed.hostname}{parsed.path}") is None
        ):
            return None
    except ValueError:
        return None
    return url


def _usable_token(value: str | None) -> str | None:
    if (
        value
        and len(value) <= 16384
        and value.isascii()
        and all(character.isprintable() and not character.isspace() for character in value)
    ):
        return value
    return None


def _credential_run(
    arguments: list[str], *, input: str, cwd: Path, env: Mapping[str, str], timeout: float,
) -> subprocess.CompletedProcess[str]:
    request = json.dumps({"arguments": arguments, "input": input})
    program = (
        "import json,subprocess,sys\n"
        f"request=json.loads({request!r})\n"
        "result=subprocess.run(request['arguments'],input=request['input'].encode('utf-8'),"
        "capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        "sys.stdout.buffer.write(result.stdout)\n"
        "sys.stderr.buffer.write(result.stderr)\n"
        "sys.exit(result.returncode)\n"
    )
    stdout, stderr, code = capture(
        base64.b64encode(program.encode("utf-8")).decode("ascii"),
        timeout, cwd=cwd, env=env,
    )
    if code == 124:
        raise subprocess.TimeoutExpired(arguments, timeout)
    return subprocess.CompletedProcess(
        arguments, code, stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _git_token(
    url: str,
    environment: Mapping[str, str],
    cwd: Path,
    *,
    timeout: float,
) -> str | None:
    lookup = execution_environment(
        {key: value for key, value in environment.items() if key not in GIT_REPOSITORY_SELECTORS},
        cwd,
    )
    lookup.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GCM_GUI_PROMPT": "false",
        }
    )
    lookup.pop("GIT_ASKPASS", None)
    lookup.pop("SSH_ASKPASS", None)
    if lookup.get("GH_HOST", "github.com").casefold() not in {
        "github.com",
        "github.com:443",
        "www.github.com",
    }:
        lookup.pop("GH_TOKEN", None)
        lookup.pop("GITHUB_TOKEN", None)
    try:
        git = execution_executable("git", lookup, cwd)
        result = _credential_run(
            [
                git,
                "-c",
                "credential.interactive=false",
                "-c",
                "core.askPass=",
                "credential",
                "fill",
            ],
            input=f"url={url}\n\n",
            cwd=cwd,
            env=lookup,
            timeout=min(10, timeout),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode and "told us to quit" in result.stderr:
        raise RuntimeError("GitHub credential owner refused the selected marketplace")
    if result.returncode or len(result.stdout) > 65536:
        return None
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"password", "credential", "authtype", "quit", "protocol", "host"}:
            if key in fields:
                raise RuntimeError("GitHub credential owner returned an ambiguous response")
            fields[key] = value
    if fields.get("quit") in {"1", "true"}:
        raise RuntimeError("GitHub credential owner refused the selected marketplace")
    if fields.get("protocol", "https") != "https" or fields.get(
        "host", "github.com"
    ).casefold() not in {
        "github.com",
        "github.com:443",
        "www.github.com",
        "www.github.com:443",
    }:
        raise RuntimeError("GitHub credential owner returned a different authority")
    token = fields.get("password")
    if token is None and fields.get("authtype", "").casefold() == "bearer":
        token = fields.get("credential")
    return _usable_token(token)


def plugin_management_environment(
    definition: Mapping[str, object],
    environment: Mapping[str, str],
    cwd: Path,
    *,
    timeout: float = 10,
) -> dict[str, str]:
    """Return child-only auth; Git retains credential selection and storage."""
    child = dict(environment)
    url = _github_url(definition)
    if url is None:
        return child
    host = child.get("GH_HOST", "github.com").casefold()
    if timeout <= 0:
        raise TimeoutError("Plugin management authentication deadline expired")
    token = _git_token(url, child, cwd, timeout=timeout)
    if token is None and host in {"github.com", "github.com:443", "www.github.com"}:
        token = _usable_token(child.get("GH_TOKEN")) or _usable_token(child.get("GITHUB_TOKEN"))
    if token is not None:
        child["COPILOT_GITHUB_TOKEN"] = token
        child["GH_TOKEN"] = token
    return child


def redact_plugin_credentials(text: str, environment: Mapping[str, str]) -> str:
    """Remove exact child credentials before rendering subprocess diagnostics."""
    for key in _TOKEN_KEYS:
        token = environment.get(key)
        if token:
            text = text.replace(token, "[redacted]")
    return text

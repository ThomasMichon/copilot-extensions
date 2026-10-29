"""Disposable native-fixture admission and credential-free OS environment."""

from __future__ import annotations

import os
from pathlib import Path


def admit(root: Path, consent: bool) -> None:
    temporary = os.environ.get("RUNNER_TEMP")
    if (
        not consent or os.name != "nt"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not temporary or not root.resolve().is_relative_to(Path(temporary).resolve())
        or not root.name.startswith("ce-broker-probe-")
    ):
        raise RuntimeError("protocol fixture requires explicit ephemeral Windows CI consent")


def fixture_environment(source: dict[str, str], *, control: bool) -> dict[str, str]:
    allowed = {
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC", "BROKER_FIXTURE_VALUE",
    }
    if control:
        allowed.update({
            "RUNNER_TEMP", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT",
            "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
            "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
            "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
            "SYSTEMDRIVE", "OS", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
        })
    return {name: value for name, value in source.items() if name.upper() in allowed}

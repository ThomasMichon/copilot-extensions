"""Disposable native-fixture admission and credential-free OS environment."""

from __future__ import annotations

import os
import shutil
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


def prepare_bootstrap(bootstrap: Path) -> None:
    source = Path(__file__).resolve().parents[1]
    bootstrap.mkdir(parents=True)
    for helper in (source / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(helper, bootstrap / helper.name)
    from installer_engine_ref import SUPPORT_FILES

    for name in SUPPORT_FILES:
        shutil.copyfile(source / "libs" / "installer-engine" / name, bootstrap / name)
    shutil.copyfile(
        source / "plugins" / "agent-mcp" / "scripts" / "uninstall-profile.json",
        bootstrap / "uninstall-profile.json",
    )
    shutil.copyfile(
        source / "libs" / "installer-engine" / "runtime_launcher.py",
        bootstrap / "runtime_launcher.py",
    )


def mcp_candidate_seed(root: Path, generation: str, python: str) -> tuple[str, str]:
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    code = (
        "import shutil,sys,venv;from pathlib import Path;"
        "target=Path(sys.argv[1]);venv.EnvBuilder(with_pip=False).create(target);"
        "shutil.copytree(sys.argv[2],target/'Lib'/'site-packages',dirs_exist_ok=True)"
    )
    seed = (
        "$seedError=''\n"
        + f"Invoke-Hidden -FilePath {quote(python)} -ArgList @('-I','-c',"
        + f"{quote(code)},$VenvDir,{quote(root / 'versions' / generation / 'Lib' / 'site-packages')}) "
        + "-ErrorOutput ([ref]$seedError) | Out-Null\n"
        + "if ($LASTEXITCODE -ne 0) { throw ('Fixture candidate seeding failed: '+$seedError) }\n"
    )
    return {"first": "1.0", "replacement": "2.0"}[generation], seed

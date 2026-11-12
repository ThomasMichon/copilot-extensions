"""Explicitly trusted, revision-bound repository setup protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit

from agent_procutil import no_window_kwargs

from .git_ops import repository_identity_env

DECLARATION = ".agent-worktrees/repository-setup.json"
ENGINE_CONTEXT = "AGENT_WORKTREES_REPOSITORY_SETUP_ENGINE"
ENGINE_PYTHON = "AGENT_WORKTREES_REPOSITORY_SETUP_PYTHON"
LIMIT = 16 * 1024
STATUSES = frozenset({"ready", "needed", "waiting", "refused", "failed"})


class SetupError(ValueError):
    """An explicitly refused or unqualified setup operation."""


def https_url(value: str) -> str:
    """Validate the narrow acquisition transport without echoing user credentials."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise SetupError("Repository must be an HTTPS Git URL.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SetupError("Repository URL is malformed.") from None
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
        or "?" in value or "#" in value or "\\" in value
        or not parsed.path.strip("/")
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise SetupError("Repository requires credential-free HTTPS without query or fragment.")
    host = parsed.hostname
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise SetupError("Repository URL requires a valid DNS host.")
    path = parsed.path.rstrip("/")
    if not path.strip("/") or any(p in (".", "..", "") for p in path.split("/")[1:]):
        raise SetupError("Repository URL has an invalid path.")
    authority = host.lower() + (f":{port}" if port not in (None, 443) else "")
    return urlunsplit(("https", authority, path, "", ""))


def _object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise SetupError("Setup JSON contains duplicate fields.")
        result[key] = value
    return result


def _json(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise SetupError("Setup JSON exceeds 16 KiB.")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError):
        raise SetupError("Setup JSON must be valid UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise SetupError("Setup JSON must be an object.")
    return value


def _fields(value: object, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise SetupError("Setup JSON has missing or unknown fields.")
    return value


def _version(value: dict) -> None:
    if type(value["version"]) is not int or value["version"] != 1:
        raise SetupError("Unsupported repository setup version.")


def _plain(path: Path) -> None:
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SetupError("Repository setup cannot traverse symlinks or reparse points.")


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--no-replace-objects", "--no-optional-locks",
         "-c", "core.fsmonitor=false", "-C", str(path), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env=repository_identity_env(), **no_window_kwargs(),
    )
    if result.returncode:
        raise SetupError("Repository source could not be qualified by Git.")
    return result.stdout.strip()


def _tracked(path: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str) or not relative or "\\" in relative
        or ":" in relative or relative.startswith("/")
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in relative)
        or any(part in ("", ".", "..") for part in relative.split("/"))
        or PurePosixPath(relative).is_absolute()
    ):
        raise SetupError("Setup script must be a repository-relative regular file.")
    file = path.joinpath(*relative.split("/"))
    if not file.exists() and not file.is_symlink():
        raise SetupError("Setup entry must be a regular file.")
    _plain(file)
    if not file.is_file():
        raise SetupError("Setup entry must be a regular file.")
    entry = _git(path, "--literal-pathspecs", "ls-files", "--stage", "--", relative)
    if not entry.startswith(("100644 ", "100755 ")) or "\n" in entry:
        raise SetupError("Setup entry must be tracked as a regular file.")
    return file


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(65536):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Source:
    path: Path
    url: str
    commit: str


def qualify(path: Path, url: str) -> Source:
    """Read-only qualification; never convert, repair, fetch or reset a checkout."""
    canonical = https_url(url)
    if not path.is_absolute():
        raise SetupError("Repository target must be absolute.")
    _plain(path)
    path = path.resolve()
    if not (path / ".git").is_dir():
        raise SetupError("Repository setup requires an ordinary checkout, not a linked/bare anchor.")
    _plain(path / ".git")
    if Path(_git(path, "rev-parse", "--show-toplevel")).resolve() != path:
        raise SetupError("Repository target is not the checkout root.")
    if https_url(_git(path, "remote", "get-url", "origin")) != canonical:
        raise SetupError("Existing checkout has a different repository identity.")
    if _git(path, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SetupError("Repository contains source changes; preserve them and reconcile first.")
    if any(line and (line[0].islower() or line[0] == "S")
           for line in _git(path, "ls-files", "-v").splitlines()):
        raise SetupError("Repository index hides tracked source changes; reconcile its flags first.")
    commit = _git(path, "rev-parse", "--verify", "HEAD^{commit}")
    return Source(path, canonical, commit)


@dataclass(frozen=True)
class SSHKeySelection:
    argv: tuple[str, ...]
    configuration_root: str
    entry: str
    directory: str
    fingerprint: str
    owner: str | None = None


def _ssh_key(value: object, *, installed: bool = False) -> SSHKeySelection | None:
    if value is None:
        return None
    selector = "owner" if installed else "argv"
    item = _fields(value, {selector, "configuration_root", "entry", "directory", "fingerprint"})
    argv = [] if installed else item["argv"]
    if installed and item["owner"] != "agent-vault":
        raise SetupError("Selected SSH key requires the installed agent-vault owner.")
    if not installed and (
        not isinstance(argv, list) or not 1 <= len(argv) <= 16
        or any(not isinstance(arg, str) or not arg or len(arg) > 2048
               or any(ord(c) < 32 for c in arg) for arg in argv)
        or not Path(argv[0]).expanduser().is_absolute()
        or Path(argv[0]).suffix.lower() in {".ps1", ".cmd", ".bat"}
    ):
        raise SetupError("Selected Vault requires a fully launchable absolute argv, not a bare shell script.")
    for name in ("configuration_root", "directory"):
        reference = item[name]
        if (
            not isinstance(reference, str) or not reference or len(reference) > 2048
            or any(ord(c) < 32 for c in reference)
            or not Path(reference).expanduser().is_absolute()
            or ".." in Path(reference).parts
        ):
            raise SetupError("Selected Vault requires normalized absolute configuration and key references.")
    if (
        not isinstance(item["entry"], str) or not 0 < len(item["entry"]) <= 512
        or any(ord(c) < 32 for c in item["entry"])
        or not isinstance(item["fingerprint"], str)
        or re.fullmatch(r"SHA256:[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]", item["fingerprint"]) is None
    ):
        raise SetupError("Selected Vault requires a bounded entry and canonical public-key fingerprint.")
    return SSHKeySelection(
        tuple(argv), **{name: item[name] for name in item if name != selector},
        owner="agent-vault" if installed else None,
    )


@dataclass(frozen=True)
class ContinuityContext:
    name: str
    provider: str
    source: str
    ssh_setup: str | None = None
    ssh_key: SSHKeySelection | None = None


@dataclass(frozen=True)
class Setup:
    source: Source
    script: Path
    runtime: str
    declaration_digest: str
    script_digest: str
    contexts: tuple[ContinuityContext, ...] = ()

    def verify(self) -> None:
        current = inspect(self.source.path, self.source.url)
        if current != self:
            raise SetupError("Repository source changed; qualify and authorize it again.")


def inspect(path: Path, url: str) -> Setup:
    source = qualify(path, url)
    declaration = _tracked(source.path, DECLARATION)
    value = _json(declaration)
    version = value.get("version")
    if type(version) is not int or version not in (1, 2, 3, 4, 5):
        raise SetupError("Unsupported repository setup version.")
    _fields(value, {"version", "entrypoints"} | ({"contexts"} if version >= 2 else set()))
    contexts = []
    if version >= 2:
        declarations = value["contexts"]
        if not isinstance(declarations, dict) or not 1 <= len(declarations) <= 32:
            raise SetupError("Repository contexts must be a bounded named collection.")
        for name, item in sorted(declarations.items()):
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
                raise SetupError("Repository context name is invalid.")
            item = _fields(item, {"provider", "source"} | ({"ssh_setup"} if version >= 3 else set())
                           | ({"ssh_key"} if version >= 4 else set()))
            if item["provider"] != "local-projection-v1":
                raise SetupError("Unsupported continuity provider.")
            reference = item["source"]
            if (not isinstance(reference, str) or not reference or len(reference) > 2048
                    or any(ord(c) < 32 for c in reference)
                    or not Path(reference).expanduser().is_absolute()):
                raise SetupError("Continuity source requires an absolute or home-relative path.")
            ssh_setup = item.get("ssh_setup")
            if ssh_setup is not None and (
                not isinstance(ssh_setup, str) or not ssh_setup or len(ssh_setup) > 2048
                or any(ord(c) < 32 for c in ssh_setup)
                or not Path(ssh_setup).expanduser().is_absolute()
            ):
                raise SetupError("SSH setup requires an absolute or home-relative reference.")
            ssh_key = _ssh_key(item.get("ssh_key"), installed=version == 5)
            if ssh_key is not None and ssh_setup is None:
                raise SetupError("Selected Vault SSH key requires an explicit SSH setup request.")
            contexts.append(ContinuityContext(name, item["provider"], reference, ssh_setup, ssh_key))
    entries = value["entrypoints"]
    if not isinstance(entries, dict) or not entries or set(entries) - {"windows", "posix"}:
        raise SetupError("Setup entrypoints must name supported platforms only.")
    for platform, entry in entries.items():
        entry = _fields(entry, {"runtime", "script"})
        expected = "powershell" if platform == "windows" else "bash"
        if entry["runtime"] != expected or not isinstance(entry["script"], str):
            raise SetupError("Unsupported repository setup runtime or script.")
        _tracked(source.path, entry["script"])
    platform = "windows" if sys.platform == "win32" else "posix"
    if platform not in entries:
        raise SetupError("Repository does not declare setup for this platform.")
    entry = entries[platform]
    script = _tracked(source.path, entry["script"])
    return Setup(source, script, entry["runtime"], _digest(declaration), _digest(script),
                 tuple(contexts))


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str


def _interpreter(runtime: str) -> str:
    candidates = ("pwsh", "powershell") if runtime == "powershell" else ("bash",)
    for candidate in candidates:
        if resolved := shutil.which(candidate):
            return resolved
    raise SetupError(f"Required {runtime} runtime is unavailable; no interpreter was installed.")


def _terminal() -> bool:
    if (
        sys.stdin is not None and sys.stdin.isatty()
        and sys.stdout is not None and sys.stdout.isatty()
    ):
        if os.name != "nt":
            return True
        import ctypes

        # NUL can report isatty() without a console to inherit.
        if ctypes.windll.kernel32.GetConsoleCP():
            return True
    return False


def _stdio_kwargs() -> dict:
    options = {} if _terminal() else no_window_kwargs()
    if os.name == "nt":
        # Headless Windows children otherwise receive new console handles, not caller pipes.
        options.update(stdin=0, stdout=1, stderr=2)
    return options


def _environment(*, attended: bool) -> dict[str, str]:
    environment = repository_identity_env()
    environment["GIT_TERMINAL_PROMPT"] = (
        os.environ.get("GIT_TERMINAL_PROMPT", "1") if attended else "0"
    )
    if not attended:
        environment.update(
            GIT_ASKPASS="", SSH_ASKPASS="", GCM_INTERACTIVE="0", GCM_GUI_PROMPT="0",
        )
    for variable in ("BASH_ENV", "ENV"):
        environment.pop(variable, None)
    return environment


def _call(setup: Setup, mode: str) -> Outcome:
    setup.verify()
    executable = _interpreter(setup.runtime)
    prefix = "repository-setup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        directory = Path(temporary).resolve(strict=True)
        request = directory / "request.json"
        result = directory / "result.json"
        request.write_text(json.dumps({
            "version": 1, "mode": mode,
            "repository": {
                "url": setup.source.url, "commit": setup.source.commit,
                "path": str(setup.source.path),
            },
            "result_file": str(result),
        }), encoding="utf-8")
        if setup.runtime == "powershell":
            policy = ["-ExecutionPolicy", "Bypass"] if os.name == "nt" else []
            argv = [executable, "-NoLogo", "-NoProfile", *policy, "-File", str(setup.script),
                    "-RequestFile", str(request)]
        else:
            argv = [executable, str(setup.script), "--request-file", str(request)]
        # Inherit handles: a credential owner may need the actual caller terminal.
        environment = _environment(attended=_terminal())
        environment[ENGINE_PYTHON] = os.path.abspath(sys.executable)
        environment[ENGINE_CONTEXT] = json.dumps({
            "schema": "copilot-extensions.repository-setup-engine", "version": 1,
            "argv": [
                environment[ENGINE_PYTHON], "-I",
                *(["-B"] if sys.dont_write_bytecode else []),
                "-m", "agent_worktrees",
            ],
            "capabilities": {
                "new_admission": _json(Path(__file__).with_name("new-admission-v1.json")),
            },
        })
        completed = subprocess.run(
            argv, cwd=setup.source.path, env=environment,
            **_stdio_kwargs(),
        )
        if completed.returncode:
            return Outcome("failed", f"Repository {mode} exited with status {completed.returncode}.")
        setup.verify()
        if not result.is_file():
            raise SetupError("Repository setup returned no result; reconcile through its owner.")
        _plain(result)
        value = _fields(_json(result), {"version", "status", "message"})
        _version(value)
        status_value, message = value["status"], value["message"]
        if not isinstance(status_value, str) or status_value not in STATUSES:
            raise SetupError("Repository setup returned an unknown status.")
        if (
            not isinstance(message, str) or not message.strip() or len(message) > 4096
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in message)
        ):
            raise SetupError("Repository setup result requires a bounded display message.")
        return Outcome(status_value, message)


def run(setup: Setup, *, apply: bool, trusted: bool) -> Outcome:
    """Check/apply/final-check; interrupted apply is never automatically replayed."""
    if not trusted:
        return Outcome("refused", "Explicit trust is required before any repository setup code runs.")
    checked = _call(setup, "check")
    if checked.status != "needed" or not apply:
        return checked
    applied = _call(setup, "apply")
    if applied.status not in ("ready", "needed"):
        return applied
    return _call(setup, "check")


def add_parsers(sub) -> None:
    parser = sub.add_parser(
        "trust-repository-configuration", allow_abbrev=False,
        help="Explicitly trust native Copilot configuration for a qualified repository revision",
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--apply", action="store_true")


def cmd_trust_configuration(args) -> int:
    from . import permissions

    changed = None
    try:
        if not args.apply:
            raise SetupError("Explicit --apply is required; native folder trust was not changed.")
        if (
            len(args.commit) not in (40, 64)
            or re.fullmatch(r"[a-fA-F0-9]+", args.commit) is None
        ):
            raise SetupError("Native configuration trust requires an exact full commit ID.")
        source = qualify(args.repo, args.repository)
        if source.commit != args.commit.lower():
            raise SetupError("Repository revision differs from the selected configuration trust.")
        native_home = os.environ.get("COPILOT_HOME")
        if native_home and Path(native_home).resolve() != permissions._copilot_dir().resolve():
            raise SetupError("Native Copilot home differs from the folder-trust owner.")
        changed = permissions.ensure_trusted_folder(str(source.path))
        if qualify(source.path, source.url) != source:
            raise SetupError("Repository changed after native trust; preserve and reconcile it.")
        result = {
            "schema": "copilot-extensions.repository-configuration-trust", "version": 1,
            "status": "ready", "changed": changed,
        }
    except (SetupError, OSError, subprocess.TimeoutExpired, permissions.FolderTrustError) as error:
        result = {
            "schema": "copilot-extensions.repository-configuration-trust", "version": 1,
            "status": "refused", "message": str(error), "changed": changed,
        }
    print(json.dumps(result))
    return 0 if result["status"] == "ready" else 1

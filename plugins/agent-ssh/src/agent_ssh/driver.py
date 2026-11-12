"""Driver setup/status contract (v1).

A machine in ``machines.yaml`` may declare the transport module that owns its
per-machine SSH setup::

    machines:
      workstation:
        hostname: workstation
        ssh:
          driver: { plugin: example-provider, module: example }

``agent-ssh setup`` / ``agent-ssh status`` resolve the local machine, locate the
driver's ``module.yaml`` and run its ``setup`` / ``status`` entrypoint. The core
stays ignorant of what the driver does: it only validates the structured
result, and (in explicit ``--interactive`` mode) runs operator commands the
driver asked for. Non-interactive runs never prompt and never open windows;
anything needing an operator is reported as ``auth_requests``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import _worktrees, mesh, mesh_refresh

RESULT_SCHEMA = "agent-ssh.driver-result"
RESULT_VERSION = 1
STATE_EXIT = {
    "ready": 0,
    "planned": 0,
    "applied": 0,
    "operator-action-required": 2,
    "failed": 1,
}
MAX_OUTPUT_BYTES = 64 * 1024
MAX_AUTH_REQUESTS = 32
MAX_COMMAND_ARGS = 64
MAX_TEXT = 4096
ENTRYPOINT_TIMEOUT = {"setup": 900, "status": 300}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RESULT_KEYS = {"schema", "version", "state", "detail", "auth_requests"}
_AUTH_KEYS = {"key", "driver", "machine", "reason", "command"}


class DriverError(Exception):
    """A resolution/validation failure reported as a ``failed`` result."""


@dataclass(frozen=True)
class Driver:
    plugin: str
    module: str

    @property
    def label(self) -> str:
        return f"{self.plugin}:{self.module}"


def make_result(state: str, detail: str, auth_requests: list | None = None) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "version": RESULT_VERSION,
        "state": state,
        "detail": detail,
        "auth_requests": list(auth_requests or []),
    }


def exit_code(result: dict[str, Any]) -> int:
    return STATE_EXIT.get(result.get("state"), 1)


# --- topology ---------------------------------------------------------------


def parse_driver(key: str, entry: Any) -> Driver | None:
    """Strictly validate ``machines.<key>.ssh.driver``; ``None`` when undeclared."""
    if not isinstance(entry, dict):
        raise DriverError(f"machine '{key}' must be a mapping")
    ssh = entry.get("ssh")
    if ssh is None:
        return None
    if not isinstance(ssh, dict):
        raise DriverError(f"machine '{key}' ssh must be a mapping")
    if "driver" not in ssh or ssh["driver"] is None:
        return None
    raw = ssh["driver"]
    if not isinstance(raw, dict) or set(raw) != {"plugin", "module"}:
        raise DriverError(f"machine '{key}' ssh.driver must be a mapping with exactly 'plugin' and 'module'")
    for field in ("plugin", "module"):
        value = raw[field]
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            raise DriverError(
                f"machine '{key}' ssh.driver.{field} must be a non-empty name matching {_NAME.pattern}"
            )
    return Driver(raw["plugin"], raw["module"])


def resolve_machines_file(cwd: Path, override: Path | None = None) -> Path:
    """Caller repo topology first, else the bound external state root."""
    if override is not None:
        path = override.expanduser().resolve()
        if not path.is_file():
            raise DriverError(f"machines.yaml not found: {path}")
        return path
    try:
        own = _worktrees.validate_context()
        selected = mesh.find_machines_file(cwd)
        if selected is None:
            external, status = _worktrees.state_root(cwd, own)
            if external is not None:
                selected = mesh._machines_file_at(external)
            if selected is None:
                raise DriverError(f"no machines.yaml in the calling repo or its state root ({status})")
    except _worktrees.ContextRefused as exc:
        raise DriverError(f"topology resolution refused: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise DriverError(f"cannot resolve machines.yaml: {exc}") from exc
    return Path(selected).resolve()


def load_machines(path: Path) -> dict[str, Any]:
    try:
        doc = mesh._load(path, strict=True)
    except (OSError, ValueError, UnicodeError) as exc:
        raise DriverError(str(exc)) from exc
    return doc["machines"]


def select_local_machine(
    machines: dict[str, Any], override: str | None = None, hostname: str | None = None
) -> str:
    """Exactly one entry must be this machine, unless ``override`` names it."""
    if override is not None:
        if override not in machines:
            raise DriverError(f"machine '{override}' is not declared in machines.yaml")
        return override
    local = hostname if hostname is not None else socket.gethostname()
    matches = []
    for key, entry in machines.items():
        declared = entry.get("hostname") if isinstance(entry, dict) else None
        if not isinstance(declared, str) or not declared.strip():
            declared = str(key)
        if mesh_refresh._is_local_machine(declared, local):
            matches.append(str(key))
    if not matches:
        raise DriverError(f"no machines.yaml entry matches local hostname '{local}'; pass --machine")
    if len(matches) > 1:
        raise DriverError(
            f"multiple machines.yaml entries match local hostname '{local}': "
            f"{', '.join(sorted(matches))}; pass --machine"
        )
    return matches[0]


# --- module discovery -------------------------------------------------------


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        from agent_procutil import no_window_kwargs

        done = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            stdin=subprocess.DEVNULL,
            # Inherited GIT_DIR/GIT_WORK_TREE would override -C and select
            # another repository's marketplaces.
            env={key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    return Path(done.stdout.strip())


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def _within(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def _repo_settings(repo_root: Path) -> tuple[Any, Any]:
    """The committed and machine-local (``settings.local.json``) repo settings."""
    folder = repo_root / ".github" / "copilot"
    return _read_jsonc(folder / "settings.json"), _read_jsonc(folder / "settings.local.json")


def _directory_marketplace_roots(repo_root: Path, plugin: str) -> list[tuple[str, Path]]:
    """Directory marketplaces declared by the repo; ``settings.local.json`` overrides by name.

    Committed declarations must stay inside the repository; the untracked,
    operator-authored local override may point anywhere. A plugin's source
    must stay inside its marketplace directory.
    """
    committed, local = _repo_settings(repo_root)
    declared: dict[str, tuple[Any, bool]] = {}
    for settings, is_local in ((committed, False), (local, True)):
        markets = settings.get("extraKnownMarketplaces") if isinstance(settings, dict) else None
        if isinstance(markets, dict):
            for name, spec in markets.items():
                declared[name] = (spec, is_local)
    repo = repo_root.resolve()
    roots: list[tuple[str, Path]] = []
    for name, (spec, is_local) in declared.items():
        source = spec.get("source") if isinstance(spec, dict) else None
        if not isinstance(source, dict) or source.get("source") != "directory":
            continue
        rel = source.get("path")
        if not isinstance(rel, str) or not rel:
            continue
        base = (repo_root / Path(rel).expanduser()).resolve()
        if not is_local and not _within(base, repo):
            continue
        manifest = None
        for candidate in (
            base / ".claude-plugin" / "marketplace.json",
            base / ".github" / "plugin" / "marketplace.json",
        ):
            if candidate.is_file():
                manifest = _read_json(candidate)
                break
        if isinstance(manifest, dict) and isinstance(manifest.get("plugins"), list):
            for entry in manifest["plugins"]:
                if isinstance(entry, dict) and entry.get("name") == plugin:
                    src = entry.get("source")
                    if isinstance(src, str) and src and _within((base / src).resolve(), base):
                        roots.append((name, (base / src).resolve()))
        else:
            roots.append((name, (base / plugin).resolve()))
    return roots


def _copilot_home(home: Path | None = None) -> Path:
    if home is None and os.environ.get("COPILOT_HOME"):
        return Path(os.environ["COPILOT_HOME"])
    return (home or Path.home()) / ".copilot"


def _copilot_config_path(home: Path | None = None) -> Path:
    return _copilot_home(home) / "config.json"


def _read_jsonc(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        return json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")))
    except ValueError:
        return None


def _enabled_value(settings: Any, identity: str) -> bool | None:
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    value = enabled.get(identity) if isinstance(enabled, dict) else None
    return value if isinstance(value, bool) else None


class _Activation:
    """Effective ``<plugin>@<marketplace>`` activation.

    Precedence: repo ``settings.local.json``, repo ``settings.json``, user
    ``settings.json``, then the installed inventory record.
    """

    def __init__(self, home: Path | None, repo_settings: tuple[Any, Any] = (None, None)) -> None:
        self.repo, self.local = repo_settings
        self.user = _read_jsonc(_copilot_home(home) / "settings.json")
        config = _read_jsonc(_copilot_config_path(home))
        records = config.get("installedPlugins") if isinstance(config, dict) else None
        self.inventory: dict[str, bool] = {}
        for record in records if isinstance(records, list) else []:
            if isinstance(record, dict) and isinstance(record.get("name"), str) and isinstance(record.get("marketplace"), str):
                key = f"{record['name']}@{record['marketplace']}"
                self.inventory[key] = record.get("enabled", True) is not False

    def explicit(self, identity: str) -> bool | None:
        for layer in (self.local, self.repo, self.user):
            value = _enabled_value(layer, identity)
            if value is not None:
                return value
        return None

    def installed(self, identity: str) -> bool:
        value = self.explicit(identity)
        return value if value is not None else self.inventory.get(identity, False)


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).expanduser().resolve())))


def repository_trusted(repo_root: Path, home: Path | None = None) -> bool:
    """Whether Copilot's own folder trust (``trustedFolders``) covers ``repo_root``.

    Repository-declared marketplaces are only an executable provider source
    once the operator has trusted that checkout, the same consent Copilot
    requires before loading the repository's own settings.
    """
    config = _read_jsonc(_copilot_config_path(home))
    folders = config.get("trustedFolders") if isinstance(config, dict) else None
    if not isinstance(folders, list):
        return False
    repo = _norm(repo_root)
    for folder in folders:
        if not isinstance(folder, str) or not folder:
            continue
        trusted = _norm(folder)
        if repo == trusted or repo.startswith(trusted.rstrip(os.sep) + os.sep):
            return True
    return False


def _installed_plugin_roots(plugin: str, home: Path | None = None) -> list[tuple[str, Path]]:
    base = _copilot_home(home) / "installed-plugins"
    try:
        markets = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return []
    return [(market.name, market / plugin) for market in markets]


def _payload_root() -> Path | None:
    from . import host_restore

    try:
        return host_restore.payload_root()
    except (RuntimeError, OSError):
        return None


def find_module(
    driver: Driver,
    cwd: Path,
    *,
    home: Path | None = None,
    payload_root: Callable[[], Path | None] = _payload_root,
) -> Path:
    """Locate ``<plugin-root>/transports/<module>/module.yaml``.

    Tiers are searched in order (agent-ssh payload, calling repo's directory
    marketplaces, installed plugins); the first tier with any hit wins and
    must contain exactly one distinct match. Repository marketplaces are
    consulted only when Copilot trusts the checkout (``trustedFolders``),
    and marketplace/installed candidates must be effectively enabled
    (``enabledPlugins`` in repo local, repo, then user settings, else the
    installed inventory record).
    """
    tiers: list[list[Path]] = []
    if driver.plugin == "agent-ssh":
        root = payload_root()
        tiers.append([root] if root is not None else [])
    repo_root = _git_toplevel(cwd)
    trusted = bool(repo_root) and repository_trusted(repo_root, home)
    untrusted_repo = bool(repo_root) and not trusted
    activation = _Activation(home, _repo_settings(repo_root) if trusted else (None, None))
    disabled: list[str] = []
    directory: list[Path] = []
    if trusted:
        for market, root in _directory_marketplace_roots(repo_root, driver.plugin):
            identity = f"{driver.plugin}@{market}"
            if activation.explicit(identity) is True:
                directory.append(root)
            elif (root / "transports" / driver.module / "module.yaml").is_file():
                disabled.append(identity)
    tiers.append(directory)
    installed: list[Path] = []
    for market, root in _installed_plugin_roots(driver.plugin, home):
        identity = f"{driver.plugin}@{market}"
        if activation.installed(identity):
            installed.append(root)
        elif (root / "transports" / driver.module / "module.yaml").is_file():
            disabled.append(identity)
    tiers.append(installed)
    for roots in tiers:
        hits: list[Path] = []
        for root in roots:
            candidate = root / "transports" / driver.module / "module.yaml"
            if candidate.is_file():
                resolved = candidate.resolve()
                if resolved not in hits:
                    hits.append(resolved)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise DriverError(
                f"driver {driver.label} is ambiguous: " + ", ".join(str(h) for h in hits)
            )
    note = (
        "; repository marketplaces were skipped because the checkout is not a trusted Copilot folder"
        if untrusted_repo
        else ""
    )
    if disabled:
        note += "; skipped disabled plugin(s): " + ", ".join(disabled)
    raise DriverError(
        f"driver module {driver.label} not found (transports/{driver.module}/module.yaml){note}"
    )


def load_module(path: Path, driver: Driver) -> dict[str, Any]:
    from . import ssh_profile

    try:
        module = ssh_profile.load_file(path)
    except Exception as exc:  # noqa: BLE001 -- surface any parse failure as a result
        raise DriverError(f"cannot read module {path}: {exc}") from exc
    if not isinstance(module, dict):
        raise DriverError(f"module {path} must be a mapping")
    if module.get("module") != driver.module:
        raise DriverError(f"module {path} declares '{module.get('module')}', expected '{driver.module}'")
    for field in ("capabilities", "entrypoints"):
        if module.get(field) is not None and not isinstance(module[field], dict):
            raise DriverError(f"module {path} {field} must be a mapping")
    return module


def capability(module: dict[str, Any], verb: str) -> bool:
    value = (module.get("capabilities") or {}).get(verb, False)
    if type(value) is not bool:
        raise DriverError(f"capability '{verb}' must be a boolean")
    return value


def entrypoint_script(module_path: Path, module: dict[str, Any], verb: str, *, windows: bool) -> Path:
    spec = (module.get("entrypoints") or {}).get(verb)
    platform = "windows" if windows else "posix"
    rel = spec.get(platform) if isinstance(spec, dict) else None
    if not isinstance(rel, str) or not rel:
        raise DriverError(f"module declares capability '{verb}' without a {platform} entrypoint")
    base = module_path.parent.resolve()
    script = (base / rel).resolve()
    if Path(rel).is_absolute() or (script != base and base not in script.parents):
        raise DriverError(f"entrypoint '{rel}' must stay inside the module directory")
    if windows and script.suffix.lower() in _BATCH_SUFFIXES:
        raise DriverError(f"batch entrypoints are not supported ({rel}); use .ps1, .py or an executable")
    if not script.is_file():
        raise DriverError(f"entrypoint not found: {script}")
    return script


_BATCH_SUFFIXES = {".cmd", ".bat"}
# cmd.exe re-parses the whole command line; these can inject or expand.
_CMD_UNSAFE = re.compile(r'[&|<>^%!"()\r\n]')


def _search_path(name: str, *, windows: bool) -> str | None:
    """Find *name* on PATH only: never the current directory or relative entries."""
    exts = [""]
    if windows and not Path(name).suffix:
        exts = [ext.lower() for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if ext]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        for ext in exts:
            candidate = os.path.join(entry, name + ext)
            if os.path.isfile(candidate):
                return candidate
    return None


def _powershell() -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if root:
        candidate = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.isfile(candidate):
            return candidate
    return _search_path("powershell", windows=True) or "powershell"


def resolve_auth_executable(name: str, module_dir: Path, *, windows: bool) -> str:
    """Resolve an auth command's argv0 to a trusted absolute path.

    Absolute paths are used as-is, paths with a separator are relative to the
    provider's module directory, and bare names come from absolute PATH
    entries only (never the caller's checkout).
    """
    if os.path.isabs(name):
        return name
    if "/" in name or (windows and "\\" in name):
        base = module_dir.resolve()
        path = (base / name).resolve()
        if path != base and base not in path.parents:
            raise DriverError(f"auth command '{name}' must stay inside the module directory")
        return str(path)
    found = _search_path(name, windows=windows)
    if found is None:
        raise FileNotFoundError(f"'{name}' not found on PATH")
    return found


def script_argv(script: Path | str, args: list[str] | tuple[str, ...] = (), *, windows: bool) -> list[str]:
    """Build an argv that runs *script* with *args* without a shell re-parse.

    Batch files (Windows ``.cmd``/``.bat``) can only run through ``cmd /c``,
    which re-parses the command line, so they are refused when the script
    path or any argument contains cmd metacharacters.
    """
    path = str(script)
    rest = list(args)
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return [sys.executable, path, *rest]
    if windows and suffix == ".ps1":
        return [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, *rest]
    if windows and suffix in _BATCH_SUFFIXES:
        if any(_CMD_UNSAFE.search(value) for value in (path, *rest)):
            raise DriverError(f"refusing to run batch file {Path(path).name} with shell metacharacters")
        return [os.environ.get("ComSpec") or "cmd.exe", "/d", "/c", path, *rest]
    return [path, *rest]


# --- result parsing ---------------------------------------------------------


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > MAX_TEXT:
        raise ValueError(f"'{field}' must be a {'' if allow_empty else 'non-empty '}string of at most {MAX_TEXT} chars")
    return value


def validate_result(doc: Any, *, driver: Driver, machine: str) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("result must be a JSON object")
    unknown = set(doc) - _RESULT_KEYS
    if unknown:
        raise ValueError(f"unknown result fields: {', '.join(sorted(unknown))}")
    if doc.get("schema") != RESULT_SCHEMA or doc.get("version") != RESULT_VERSION or type(doc.get("version")) is not int:
        raise ValueError(f"result must declare schema '{RESULT_SCHEMA}' version {RESULT_VERSION}")
    state = doc.get("state")
    if not isinstance(state, str) or state not in STATE_EXIT:
        raise ValueError(f"unknown state {state!r}")
    detail = _text(doc.get("detail"), "detail", allow_empty=True)
    raw_requests = doc.get("auth_requests", [])
    if not isinstance(raw_requests, list) or len(raw_requests) > MAX_AUTH_REQUESTS:
        raise ValueError(f"'auth_requests' must be a list of at most {MAX_AUTH_REQUESTS}")
    requests = []
    for item in raw_requests:
        if not isinstance(item, dict) or set(item) != _AUTH_KEYS:
            raise ValueError(f"each auth request must have exactly: {', '.join(sorted(_AUTH_KEYS))}")
        command = item["command"]
        if (
            not isinstance(command, list)
            or not command
            or len(command) > MAX_COMMAND_ARGS
            or any(not isinstance(arg, str) or len(arg) > MAX_TEXT or "\x00" in arg for arg in command)
            or not command[0]
        ):
            raise ValueError("auth request 'command' must be a non-empty argv list of strings")
        request = {
            "key": _text(item["key"], "key"),
            "driver": _text(item["driver"], "driver"),
            "machine": _text(item["machine"], "machine"),
            "reason": _text(item["reason"], "reason"),
            "command": list(command),
        }
        if request["driver"] != driver.label or request["machine"] != machine:
            raise ValueError("auth request driver/machine must match the invoked driver and machine")
        requests.append(request)
    if state == "operator-action-required" and not requests:
        raise ValueError("operator-action-required requires at least one auth request")
    if state != "operator-action-required" and requests:
        raise ValueError(f"state '{state}' must not carry auth requests")
    return make_result(state, detail, requests)


def parse_result(stdout: bytes | str, returncode: int, *, driver: Driver, machine: str) -> dict[str, Any]:
    """Validate entrypoint output; anything malformed becomes ``failed``."""
    data = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
    if len(data) > MAX_OUTPUT_BYTES:
        return make_result("failed", f"driver output exceeds {MAX_OUTPUT_BYTES} bytes")
    try:
        result = validate_result(json.loads(data.decode("utf-8")), driver=driver, machine=machine)
    except (ValueError, TypeError, UnicodeError) as exc:
        return make_result("failed", f"malformed driver output (exit {returncode}): {exc}")
    if STATE_EXIT[result["state"]] != returncode:
        return make_result(
            "failed",
            f"driver exit code {returncode} does not match state '{result['state']}': {result['detail']}",
        )
    return result


# --- execution --------------------------------------------------------------


@dataclass
class Invocation:
    verb: str
    driver: Driver
    machine: str
    machines_file: Path
    script: Path
    module_dir: Path
    apply: bool = False
    interactive: bool = False


MAX_STDERR_BYTES = 64 * 1024


def bounded_runner(argv: list[str], *, timeout: float, cwd: str) -> Any:
    """Run headless with stdin closed, enforcing byte budgets while reading."""
    from agent_procutil.owned_process import run_caller_owned

    return run_caller_owned(
        argv,
        timeout=timeout,
        max_stdout_bytes=MAX_OUTPUT_BYTES,
        max_stderr_bytes=MAX_STDERR_BYTES,
        cwd=cwd,
        binary=True,
    )


def run_entrypoint(inv: Invocation, *, runner=bounded_runner, windows: bool | None = None) -> dict[str, Any]:
    is_windows = os.name == "nt" if windows is None else windows
    args = ["--machine", inv.machine, "--machines", str(inv.machines_file)]
    if inv.apply:
        args.append("--apply")
    if inv.interactive:
        args.append("--interactive")
    args.append("--json")
    try:
        argv = script_argv(inv.script, args, windows=is_windows)
        done = runner(argv, timeout=ENTRYPOINT_TIMEOUT[inv.verb], cwd=str(inv.module_dir))
    except DriverError as exc:
        return make_result("failed", str(exc))
    except subprocess.TimeoutExpired:
        return make_result("failed", f"driver {inv.verb} timed out after {ENTRYPOINT_TIMEOUT[inv.verb]}s")
    except subprocess.SubprocessError as exc:
        # Includes OutputLimitExceeded: a stream overran its byte budget.
        return make_result("failed", f"driver {inv.verb} failed: {exc}"[:MAX_TEXT])
    except (OSError, ValueError) as exc:
        return make_result("failed", f"cannot run driver {inv.verb}: {exc}"[:MAX_TEXT])
    except RuntimeError as exc:
        # The owned runner reports native execution/cleanup failures this way.
        return make_result("failed", f"driver {inv.verb} failed: {exc}"[:MAX_TEXT])
    result = parse_result(done.stdout or b"", done.returncode, driver=inv.driver, machine=inv.machine)
    if result["state"] == "failed" and done.stderr:
        tail = (done.stderr if isinstance(done.stderr, str) else done.stderr.decode("utf-8", "replace"))
        tail = tail.strip()[-1000:]
        if tail:
            result["detail"] = f"{result['detail']} | stderr: {tail}"[:MAX_TEXT]
    return result


def _stderr_target() -> Any:
    for stream in (sys.stderr, sys.__stderr__):
        try:
            return stream.fileno()
        except (AttributeError, ValueError, OSError):
            continue
    return subprocess.DEVNULL


def run_auth_command(
    command: list[str], module_dir: Path, *, runner=subprocess.run, windows: bool | None = None
) -> int:
    """Run an operator auth command attached to the console.

    It runs from the provider's module directory with argv0 resolved to a
    trusted absolute path. stdin stays interactive; stdout goes to stderr so
    prompts and progress never corrupt the final result printed on stdout.
    """
    is_windows = os.name == "nt" if windows is None else windows
    try:
        head = resolve_auth_executable(command[0], module_dir, windows=is_windows)
        argv = script_argv(head, command[1:], windows=is_windows)
        return runner(argv, check=False, cwd=str(module_dir), stdout=_stderr_target()).returncode
    except DriverError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 126
    except (OSError, ValueError) as exc:
        print(f"[FAIL] cannot run auth command {command[0]}: {exc}", file=sys.stderr)
        return 127


def _console_attached() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


@dataclass
class Request:
    verb: str
    apply: bool = False
    interactive: bool = False
    machine: str | None = None
    machines: Path | None = None
    module: Path | None = None
    cwd: Path | None = None


def run(
    req: Request,
    *,
    runner=bounded_runner,
    auth_runner: Callable[[list[str]], int] | None = None,
    hostname: str | None = None,
    console: Callable[[], bool] = _console_attached,
    home: Path | None = None,
) -> dict[str, Any]:
    """Resolve and run ``setup`` / ``status``; always returns a v1 result."""
    try:
        if req.interactive and not console():
            raise DriverError("--interactive requires an attached console (TTY)")
        cwd = (req.cwd or Path.cwd()).resolve()
        machines_file = resolve_machines_file(cwd, req.machines)
        machines = load_machines(machines_file)
        machine = select_local_machine(machines, req.machine, hostname)
        driver = parse_driver(machine, machines[machine])
        if driver is None:
            return make_result("ready", f"no SSH driver declared for machine '{machine}'; no driver setup")
        module_path = req.module.expanduser().resolve() if req.module else find_module(driver, cwd, home=home)
        if not module_path.is_file():
            raise DriverError(f"module.yaml not found: {module_path}")
        module = load_module(module_path, driver)
        windows = os.name == "nt"
        verb, apply = req.verb, req.apply
        if verb == "status" and not capability(module, "status"):
            verb, apply = "setup", False
        if not capability(module, verb):
            return make_result("ready", f"driver {driver.label} declares no driver setup")
        inv = Invocation(
            verb=verb,
            driver=driver,
            machine=machine,
            machines_file=machines_file,
            script=entrypoint_script(module_path, module, verb, windows=windows),
            module_dir=module_path.parent,
            apply=apply,
            interactive=req.interactive and req.verb == "setup",
        )
    except DriverError as exc:
        return make_result("failed", str(exc))

    result = run_entrypoint(inv, runner=runner)
    if not inv.interactive or result["state"] != "operator-action-required":
        return result
    run_auth = auth_runner or (lambda command: run_auth_command(command, inv.module_dir))
    seen: set[str] = set()
    for request in result["auth_requests"]:
        if request["key"] in seen:
            continue
        seen.add(request["key"])
        print(f"[auth] {safe_text(request['reason'])}", file=sys.stderr)
        code = run_auth(request["command"])
        if code != 0:
            print(f"[auth] command exited {code}: {safe_text(request['command'][0])}", file=sys.stderr)
    return run_entrypoint(inv, runner=runner)


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe_text(value: str) -> str:
    """Escape terminal control characters (ESC, OSC, CSI ...) for human output."""
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", value)


def display_command(command: list[str], *, windows: bool | None = None) -> str:
    is_windows = os.name == "nt" if windows is None else windows
    return subprocess.list2cmdline(command) if is_windows else shlex.join(command)


def render_human(result: dict[str, Any], verb: str) -> str:
    lines = [f"agent-ssh {verb}: {result['state']}" + (f" -- {result['detail']}" if result["detail"] else "")]
    for request in result["auth_requests"]:
        lines.append(f"  auth required [{request['key']}] {request['reason']}")
        lines.append(f"    run: {display_command(request['command'])}")
    return "\n".join(safe_text(line) for line in lines)

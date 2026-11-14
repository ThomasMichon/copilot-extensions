"""Finite native-uninstall controller proofs, bound to the remover's operations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def parse_cli(argv: Sequence[str] | None = None, *, description: str | None = None):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--payload-root", required=True, type=Path)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--shell", required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--check-consumers", action="store_true",
                        help="Also acquire removal custody and refuse consumers during admission")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--remove-runtime", action="store_true")
    return parser.parse_args(argv)


@contextmanager
def native_controller_custody(payload: Path, plugin: str, root: Path, *, root_check, load, error):
    if os.name != "nt" or plugin != "agent-vault" or (
        os.environ.get("COPILOT_EXTENSIONS_NATIVE_INSTALLER") != plugin
    ):
        yield None
        return
    root_check(payload, root)
    driver = load(payload / "scripts/installer_driver.py")
    driver.bootstrap(payload)
    with driver.custody() as owner:
        if owner is None:
            raise error("Native Vault removal requires its authenticated execution owner")
        yield owner


def run_cli(argv=None, *, description, cleanup, root_check, load, error) -> int:
    arguments = parse_cli(argv, description=description)
    try:
        with native_controller_custody(
            arguments.payload_root, arguments.plugin_id, arguments.root,
            root_check=root_check, load=load, error=error,
        ) as owner:
            result = cleanup(
                arguments.payload_root, arguments.plugin_id, arguments.root, arguments.shell,
                profile=arguments.profile, remove_runtime=arguments.remove_runtime,
                check_consumers=arguments.check_consumers,
                **({"controller_custody": owner} if owner is not None else {}),
            )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as failure:
        print(f"Runtime uninstall refused: {failure}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def lifecycle_controllers(rows, payload, root, plugin, *, load, authority_payload,
                          arguments, parent, dependency):
    """Admit only the live teardown owner and its exact native/staging callers."""
    origins, admitted = [payload], set()
    if not os.environ.get("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"):
        return origins, admitted
    binding = load(payload / "scripts" / "command_binding.py")
    authority = authority_payload(payload)
    lease = binding._load_lifecycle().borrowed_lease(
        binding._rules(), binding._load_primitive(payload), operation="teardown",
        payload_root=authority, plugin_id=plugin, legacy_root=root,
        project_root=Path(os.environ.get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT") or Path.cwd()),
        environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(authority)},
    )
    if lease is None:
        return origins, admitted
    if authority != payload and not authority.is_relative_to(root):
        origins.append(authority)
    owner = next((row for row in rows if row["pid"] == lease.ticket["pid"]), None)
    if owner is None or Path(owner["image"]).is_relative_to(root):
        return origins, admitted
    argv = arguments(owner)
    helpers = [index for index, value in enumerate(argv)
               if Path(value) in {origin / "scripts" / "command_binding.py" for origin in origins}]
    if len(helpers) != 1 or "--" not in argv or not re.fullmatch(
        r"(?:python|pythonw|pypy)[0-9.]*", Path(owner["image"]).stem.lower(),
    ):
        return origins, admitted
    index, separator = helpers[0], argv.index("--")
    flags, native = argv[index + 1:separator], argv[separator + 1:]
    if (any(value not in {"-I", "-S", "-B", "-X", "utf8"} for value in argv[1:index])
            or "--teardown" not in flags or "--run-lifecycle" not in flags or not native
            or any(flags.count(key) != 1 or flags.index(key) + 1 >= len(flags)
                   or convert(flags[flags.index(key) + 1]) != value for key, convert, value in (
                       ("--legacy-root", Path, root), ("--plugin-id", str, plugin),
                   ))
            or not any(parent(owner["pid"], native[0], "", origin, root, plugin,
                              owner["pid"], arguments=native) for origin in origins)):
        return origins, admitted
    admitted.add(owner["pid"])
    caller = owner
    for _ in range(4):
        forwarder = next((row for row in rows if row["pid"] == caller["ppid"]), None)
        if forwarder is None or forwarder["pid"] in admitted:
            break
        image = Path(forwarder["image"])
        if (
            not image.is_absolute() or image.is_relative_to(root)
            or not re.fullmatch(r"(?:python|pythonw|pypy)[0-9.]*", image.stem.lower())
            or arguments(forwarder)[1:] != argv[1:]
        ):
            break
        admitted.add(forwarder["pid"])
        caller = forwarder
    admitted.update(row["pid"] for row in rows if row["pid"] == caller["ppid"] and any(
        parent(row["pid"], row["image"], row["command"], origin, root, plugin, row["pid"],
               arguments=row.get("argv"))
        for origin in origins
    ))
    for _ in range(4):
        admitted.update({
            row["pid"] for row in rows if row["ppid"] in admitted and any(
                parent(row["pid"], row["image"], row["command"], origin, root, plugin,
                       row["pid"], arguments=row.get("argv"))
                for origin in origins
            )
        })
    if payload.parent.parent == Path.home() / ("." + plugin) / ".install-stage":
        commands = load(dependency(payload, "installer_engine_commands.py"))
        body = commands._sh(commands._read(payload / "scripts/install.sh"), "INSTALL_WATCHDOG").rstrip("\n")
        # Bash 3.2 joins backslash-newlines in this quoted heredoc substitution.
        bodies = {body, body.replace("\\\n", "")}
        for _ in range(2):
            for row in rows:
                args = arguments(row)
                if (row["ppid"] in admitted and Path(row["image"]).stem.lower() in {"bash", "sh"}
                        and len(args) > 8 and args[1] == "-c" and args[2] in bodies and args[3] == "bash"
                        and Path(args[4]) == payload / "scripts/install.sh" and args[5] == plugin
                        and Path(args[6]) == payload.parent
                        and re.fullmatch(r"-?[0-9]+", args[7])
                        and parent(row["pid"], args[0], "", payload, root, plugin, row["pid"],
                                   arguments=[args[0], args[4], *args[8:]])):
                    admitted.add(row["pid"])
    return origins, admitted


def bind(*, plain, run, error):
    """Keep filesystem/native operations and refusal identity owned by the remover."""
    _plain, _run, UninstallRefused = plain, run, error

    def _publisher_identity(payload: Path, shell: str, chain: list[dict[str, Any]]) -> bool:
        """Revalidate only selected ancestry against its native census identities."""
        if any(not isinstance(row.get("birth"), str)
               or re.fullmatch(r"[0-9]{14}\.[0-9]{6}", row["birth"]) is None for row in chain):
            return False
        if any(parent["birth"] > child["birth"] for child, parent in zip(chain, chain[1:])):
            return False
        # A runner command contains live IPC credentials. Never replicate it into argv.
        expected = json.dumps([
            {**{key: row[key] for key in ("pid", "ppid", "birth", "image")},
             "commandHash": hashlib.sha256(row["command"].encode("utf-8")).hexdigest()}
            for row in chain
        ]).replace("'", "''")
        script = f"$expected=ConvertFrom-Json '{expected}'\n" + r"""
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
try {
    foreach ($entry in $expected) {
        $rows=@(Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)" -ErrorAction Stop)
        if ($rows.Count -ne 1) { throw 'Ambiguous controller identity' }
        $row=$rows[0]
        $owner=Invoke-CimMethod -InputObject $row -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -ne 0 -or $owner.Sid -cne $sid) { throw 'Unknown controller owner' }
        $process=Get-Process -Id $entry.pid -ErrorAction Stop
        try {
            $sha=[Security.Cryptography.SHA256]::Create()
            try {
                $commandHash=([BitConverter]::ToString($sha.ComputeHash(
                    [Text.Encoding]::UTF8.GetBytes([string]$row.CommandLine)))).Replace('-','').ToLowerInvariant()
            } finally { $sha.Dispose() }
            if ($process.StartTime.ToUniversalTime().ToString('yyyyMMddHHmmss.ffffff') -cne $entry.birth -or
                $row.CreationDate.ToUniversalTime().ToString('yyyyMMddHHmmss.ffffff') -cne $entry.birth -or
                $row.ParentProcessId -ne $entry.ppid -or $commandHash -cne $entry.commandHash -or
                ([string]$process.Path).Replace('\','/').ToLowerInvariant() -cne
                    ([string]$entry.image).Replace('\','/').ToLowerInvariant()) {
                throw 'Controller identity changed during inspection'
            }
        } finally { $process.Dispose() }
    }
    'true'
} catch { 'false' }
"""
        return json.loads(_run(payload, [shell, "-NoProfile", "-NonInteractive", "-Command", script])) is True

    def _windows_argv(command: str) -> list[str]:
        import ctypes
        from ctypes import wintypes

        parser = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
        parser.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        parser.restype = ctypes.POINTER(wintypes.LPWSTR)
        free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        free.argtypes = (wintypes.HLOCAL,)
        free.restype = wintypes.HLOCAL
        count = ctypes.c_int()
        pointer = parser(command, ctypes.byref(count))
        if not pointer:
            raise UninstallRefused("Cannot decode native controller arguments")
        try:
            return [pointer[index] for index in range(count.value)]
        finally:
            free(ctypes.cast(pointer, wintypes.HLOCAL))

    def _closed_flags(arguments: Sequence[str], values: set[str], switches: set[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        index = 0
        while index < len(arguments):
            flag = arguments[index].lower()
            if flag in result or flag not in values | switches:
                raise UninstallRefused("Unexpected or duplicate uninstall controller argument")
            if flag in switches:
                result[flag] = True
            else:
                index += 1
                if index == len(arguments) or arguments[index].startswith("-"):
                    raise UninstallRefused("Missing uninstall controller argument")
                result[flag] = arguments[index]
            index += 1
        return result

    def _worktrees_recipe(arguments: Sequence[str], root: Path) -> dict[str, Any]:
        if arguments and arguments[0].lower() == "uninstall":
            arguments = ("-Action", arguments[0], *arguments[1:])
        result = _closed_flags(arguments, {"-action", "-installdir", "-bootstrappython", "-projectname"},
                               {"-removeconfig"})
        if result.get("-action", "").lower() != "uninstall" or Path(result.get("-installdir", "")) != root:
            raise UninstallRefused("Worktrees controller action or root changed")
        result["-action"] = "uninstall"
        result["-installdir"] = str(root)
        if "-bootstrappython" in result:
            python = Path(result["-bootstrappython"])
            _plain(python, file=True)
            if (not python.is_absolute() or ".." in python.parts or not python.is_file()
                    or python.is_relative_to(root) or python.resolve().is_relative_to(root)):
                raise UninstallRefused("Worktrees controller requires an external bootstrap interpreter")
            result["-bootstrappython"] = str(python)
        return result

    def _controller_python(row: dict[str, Any], root: Path) -> list[str]:
        arguments = _windows_argv(row["command"])
        image = Path(row["image"])
        if (not image.is_absolute() or image.is_relative_to(root)
                or re.fullmatch(r"(?:python|pythonw|pypy)[0-9.]*", image.stem.lower()) is None
                or not arguments or Path(arguments[0]) != image):
            raise UninstallRefused("Unexpected or runtime-rooted controller interpreter")
        _plain(image, file=True)
        if not image.is_file():
            raise UninstallRefused("Controller interpreter is unavailable")
        return arguments

    def _worktrees_driver(arguments: Sequence[str], root: Path, recipe: dict[str, Any],
                          role: str, shell: str) -> Path:
        if list(arguments[1:6]) != ["-I", "-S", "-B", "-X", "utf8"] or len(arguments) < 7:
            raise UninstallRefused("Unexpected Worktrees driver entrypoint")
        options = _closed_flags(arguments[7:], {
            "--payload", "--root", "--shell", "--action", "--bootstrap-python", "--project-name",
        }, {"--participant", "--check-removal", "--remove-runtime", "--remove-config"})
        payload = Path(options.get("--payload", ""))
        if (not payload.is_absolute() or ".." in payload.parts
                or Path(arguments[6]) != payload / "scripts" / "runtime_install_driver.py"
                or Path(options.get("--root", "")) != root or Path(options.get("--shell", "")) != Path(shell)):
            raise UninstallRefused("Worktrees driver source or root changed")
        native = []
        for key, flag in (("--action", "-Action"), ("--root", "-InstallDir"),
                          ("--bootstrap-python", "-BootstrapPython"), ("--project-name", "-ProjectName")):
            if key in options:
                native.extend((flag, options[key]))
        if options.get("--remove-config"):
            native.append("-RemoveConfig")
        controls = {key for key in options if key in {"--participant", "--check-removal", "--remove-runtime"}}
        expected = {"--participant"} if role == "participant" else set()
        if role == "inspector":
            if controls not in ({"--check-removal"}, {"--remove-runtime"}):
                raise UninstallRefused("Worktrees inspector is not a removal callback")
        elif controls != expected:
            raise UninstallRefused("Unexpected Worktrees driver role")
        if _worktrees_recipe(native, root) != recipe:
            raise UninstallRefused("Worktrees driver did not forward the admitted recipe")
        return payload

    def _worktrees_shell(row: dict[str, Any], source: Path, root: Path,
                         shell: str, recipe: dict[str, Any]) -> list[str]:
        arguments = _windows_argv(row["command"])
        if (Path(shell).is_relative_to(root) or Path(row["image"]) != Path(shell)
                or not arguments or Path(arguments[0]).name.lower() != Path(shell).name.lower()):
            raise UninstallRefused("Unexpected Worktrees native shell")
        lower = [value.lower() for value in arguments]
        if lower.count("-file") != 1:
            raise UninstallRefused("Worktrees requires a native file entrypoint")
        index = lower.index("-file")
        flags = _closed_flags(arguments[1:index], {"-executionpolicy"}, {"-nologo", "-noprofile", "-noninteractive"})
        if flags.get("-executionpolicy", "bypass").lower() != "bypass":
            raise UninstallRefused("Unexpected Worktrees native shell policy")
        if (index + 1 >= len(arguments) or Path(arguments[index + 1]) != source / "scripts" / "install.ps1"
                or _worktrees_recipe(arguments[index + 2:], root) != recipe):
            raise UninstallRefused("Worktrees native uninstall recipe changed")
        return arguments

    def _controller_row(payload: Path, shell: str, pid: int) -> dict[str, Any]:
        """Read one selected missing ancestor; the command never contains census text."""
        script = f"$selected={int(pid)}\n" + r"""
$ErrorActionPreference='Stop'
$rows=@(Get-CimInstance Win32_Process -Filter "ProcessId=$selected" -ErrorAction Stop)
if ($rows.Count -ne 1) { throw 'Selected controller identity is ambiguous' }
$row=$rows[0]
$owner=Invoke-CimMethod -InputObject $row -MethodName GetOwnerSid -ErrorAction Stop
if ($owner.ReturnValue -ne 0 -or
    $owner.Sid -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
    throw 'Selected controller ownership is unknown'
}
$process=Get-Process -Id $selected -ErrorAction Stop
try {
    $birth=$process.StartTime.ToUniversalTime().ToString('yyyyMMddHHmmss.ffffff')
    if ($birth -cne $row.CreationDate.ToUniversalTime().ToString('yyyyMMddHHmmss.ffffff') -or
        -not $process.Path -or -not $row.CommandLine) { throw 'Selected controller identity changed' }
    [pscustomobject]@{pid=$row.ProcessId;ppid=$row.ParentProcessId;
        image=$process.Path;command=$row.CommandLine;birth=$birth} | ConvertTo-Json -Compress
} finally { $process.Dispose() }
"""
        row = json.loads(_run(payload, [shell, "-NoProfile", "-NonInteractive", "-Command", script]))
        if not isinstance(row, dict) or row.get("pid") != pid:
            raise UninstallRefused("Selected controller is unavailable")
        return row

    def _worktrees_controllers(rows: list[dict[str, Any]], payload: Path, root: Path, shell: str,
                               custody: Any, lease: Any) -> set[int]:
        """Admit only the pinned native-uninstall stack under both existing owners."""
        engine = sys.modules.get("installer_engine")
        core = sys.modules.get("update_driver.core")
        participant = sys.modules.get("update_driver.participant")
        if (os.name != "nt" or engine is None or core is None or participant is None
                or Path(engine.__file__) != payload / "scripts" / "installer_engine.py"):
            raise UninstallRefused("Worktrees removal driver source is unavailable")
        packages = {engine._package_name(path): path for path in engine._package_roots(payload)}
        if not {"agent-worktrees", "agent-update-driver", "agent-procutil"} <= packages.keys():
            raise UninstallRefused("Worktrees removal source closure is incomplete")
        driver = packages["agent-update-driver"] / "src" / "update_driver"
        for module, name in ((engine, "installer_engine.py"), (core, "core.py"), (participant, "participant.py")):
            path = payload / "scripts" / name if module is engine else driver / name
            _plain(path, file=True)
            if not path.is_file() or Path(module.__file__) != path:
                raise UninstallRefused("Worktrees removal driver source changed")
        if type(custody) is not participant.BorrowedCustody:
            raise UninstallRefused("Worktrees removal requires authenticated driver custody")
        custody.assert_owned()
        request = core.status(custody.request_id, root=custody.root)
        if (request.id != custody.request_id or request.state != "running" or request.spec.backend != "engine-native-install"
                or request.spec.project != str(root.resolve())
                or request.spec.source != engine.runtime.fingerprint_source(engine._source_files(tuple(packages.values())))):
            raise UninstallRefused("Worktrees removal has no matching running native request")
        recipe = _worktrees_recipe(request.spec.argv, root)
        original = _worktrees_driver(request.runner.argv, root, recipe, "participant", shell)
        lifecycle = sys.modules.get("_payload_command_lifecycle")
        if (lifecycle is None or Path(lifecycle.__file__) != payload / "scripts" / "command_lifecycle.py"
                or type(lease) is not lifecycle.LifecycleLease or Path(lease.arguments["legacy_root"]) != root
                or lease.arguments["plugin_id"] != "agent-worktrees" or Path(lease.arguments["payload_root"]) != original):
            raise UninstallRefused("Worktrees removal requires validated teardown custody")
        lease.assert_owned()
        lease.assert_operation("teardown")
        sources = {engine._package_name(path): path for path in engine._package_roots(original)}
        if ("agent-worktrees" not in sources
                or request.spec.source != engine.runtime.fingerprint_source(engine._source_files(tuple(sources.values())))):
            raise UninstallRefused("Worktrees original controller source changed")
        for name in ("runtime_install_driver.py", "command_binding.py", "command_lifecycle.py", "install.ps1"):
            for source in {payload, original}:
                path = source / "scripts" / name
                _plain(path, file=True)
                if path.read_bytes() != (payload / "scripts" / name).read_bytes():
                    raise UninstallRefused("Worktrees controller source changed")
        if original != payload:
            if (payload.name != "agent-worktrees" or payload.parent.parent != root / ".install-stage"
                    or original.name != payload.name
                    or original.parent.parent != Path.home() / ".copilot" / "installed-plugins"):
                raise UninstallRefused("Worktrees controller source is not its owned stage")
        witness = participant.participant_environment().get(participant.ENVIRONMENT_KEY)
        if not witness:
            raise UninstallRefused("Worktrees removal has no live scoped participant")
        chain: list[dict[str, Any]] = []

        def take() -> dict[str, Any]:
            pid = os.getpid() if not chain else chain[-1]["ppid"]
            matches = [row for row in rows if row["pid"] == pid]
            if len(matches) > 1 or pid in {row["pid"] for row in chain} or len(chain) == 9:
                raise UninstallRefused("Worktrees controller ancestry is ambiguous")
            row = matches[0] if matches else _controller_row(payload, shell, pid)
            if not row.get("birth"):
                current = _controller_row(payload, shell, pid)
                if any(current[key] != row[key] for key in ("pid", "ppid", "command")) or Path(current["image"]) != Path(row["image"]):
                    raise UninstallRefused("Worktrees controller identity changed")
                row = current
            chain.append(row)
            return row

        inspector = take()
        if inspector["ppid"] != os.getppid() or _worktrees_driver(
            _controller_python(inspector, root), root, recipe, "inspector", shell,
        ) != payload or Path(sys.argv[0]) != payload / "scripts" / "runtime_install_driver.py":
            raise UninstallRefused("Worktrees removal inspector changed")
        native = take()
        _worktrees_shell(native, payload, root, shell, recipe)
        if original != payload:
            publisher = take()
            if re.fullmatch(r"[0-9]{8}T[0-9]{9}-" + str(publisher["pid"]), payload.parent.name) is None:
                raise UninstallRefused("Worktrees stage publisher changed")
            _worktrees_shell(publisher, original, root, shell, recipe)
            native = publisher
        binding = take()
        arguments = _controller_python(binding, root)
        if (binding["pid"] != lease.ticket["pid"] or arguments[1:6] != ["-I", "-S", "-B", "-X", "utf8"]
                or len(arguments) < 8 or Path(arguments[6]) != original / "scripts" / "command_binding.py"
                or arguments.count("--") != 1):
            raise UninstallRefused("Worktrees teardown owner is not its binding controller")
        separator = arguments.index("--")
        flags = _closed_flags(arguments[7:separator], {
            "--payload-root", "--plugin-id", "--legacy-root", "--source-payload-root", "--project-root",
        }, {"--run-lifecycle", "--teardown"})
        # Self-staging changes CWD; the admitted request retains the original project.
        project = dict(request.spec.context).get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT")
        if (not project or not Path(project).is_absolute()
                or Path(lease.arguments["project_root"]) != Path(project)):
            raise UninstallRefused("Worktrees request has no pinned installation project")
        expected = {"--payload-root": str(original), "--plugin-id": "agent-worktrees",
                    "--legacy-root": str(root), "--source-payload-root": str(original),
                    "--project-root": project, "--run-lifecycle": True, "--teardown": True}
        if flags != expected or arguments[separator + 1:] != _windows_argv(native["command"]):
            raise UninstallRefused("Worktrees teardown command was not forwarded")
        _worktrees_shell(take(), original, root, shell, recipe)
        arguments = _controller_python(take(), root)
        if tuple(arguments) != tuple(request.runner.argv):
            raise UninstallRefused("Worktrees participant is not its pinned runner")
        arguments = _controller_python(take(), root)
        runner = sources["agent-update-driver"] / "src" / "update_driver" / "_runner.py"
        procutil = sources["agent-procutil"] / "src" / "agent_procutil" / "__init__.py"
        for path, current in ((runner, driver / "_runner.py"),
                              (procutil, packages["agent-procutil"] / "src" / "agent_procutil" / "__init__.py")):
            _plain(path, file=True)
            _plain(current, file=True)
            if path.read_bytes() != current.read_bytes():
                raise UninstallRefused("Worktrees runner source changed")
        if (len(arguments) == 9 and arguments[1:3] == ["-I", "-X"]
                and arguments[3].startswith("pycache_prefix=")):
            prefix = arguments[3].removeprefix("pycache_prefix=")
            if prefix.startswith("\\\\?\\UNC\\"):
                prefix = "\\\\" + prefix[8:]
            elif prefix.startswith("\\\\?\\"):
                prefix = prefix[4:]
            cache = Path(prefix)
            if (cache.parent == custody.root / "cache" / "python" and ".." not in cache.parts
                    and re.fullmatch(r"(?:installed-[0-9a-f]{64}|unsealed-[0-9a-f]{32})", cache.name)):
                arguments = [*arguments[:2], *arguments[4:]]
        if len(arguments) == 9 and arguments[1:4] == ["-I", "-S", "-B"]:
            arguments = [*arguments[:2], *arguments[4:]]
        elif len(arguments) == 8 and arguments[1:3] == ["-I", "-B"]:
            arguments = [*arguments[:2], *arguments[3:]]
        if (len(arguments) != 7 or arguments[1] != "-I" or Path(arguments[2]) != runner
                or Path(arguments[3]) != custody.root or arguments[4] != request.id
                or arguments[5] != witness or Path(arguments[6]) != procutil):
            raise UninstallRefused("Worktrees execution shim is not its live request")
        if _worktrees_driver(_controller_python(take(), root), root, recipe, "foreground", shell) != original:
            raise UninstallRefused("Worktrees foreground source changed")
        _worktrees_shell(take(), original, root, shell, recipe)
        if not _publisher_identity(payload, shell, chain):
            raise UninstallRefused("Worktrees native controller identity is unverified")
        custody.assert_owned()
        lease.assert_owned()
        current = core.status(custody.request_id, root=custody.root)
        if current.state != "running" or current.spec != request.spec or current.runner != request.runner:
            raise UninstallRefused("Worktrees native request changed during inspection")
        return {row["pid"] for row in chain[1:]}

    def _native_plugin_controllers(rows, payload, root, shell, custody) -> set[int]:
        """Recognize only Vault's authenticated native uninstall ancestry."""
        engine = sys.modules.get("installer_engine")
        core = sys.modules.get("update_driver.core")
        participant = sys.modules.get("update_driver.participant")
        if (os.name != "nt" or engine is None or core is None or participant is None
                or Path(engine.__file__) != payload / "scripts/installer_engine.py"):
            raise UninstallRefused("Native Vault removal source is unavailable")
        packages = {engine._package_name(path): path for path in engine._package_roots(payload)}
        if not {"agent-vault", "agent-update-driver", "agent-procutil"} <= packages.keys():
            raise UninstallRefused("Native Vault removal source closure is incomplete")
        driver = packages["agent-update-driver"] / "src/update_driver"
        for module, name in ((core, "core.py"), (participant, "participant.py")):
            path = driver / name
            _plain(path, file=True)
            if not path.is_file() or Path(module.__file__) != path:
                raise UninstallRefused("Native Vault execution source changed")
        if type(custody) is not participant.BorrowedCustody:
            raise UninstallRefused("Native Vault removal requires authenticated driver custody")
        custody.assert_owned()
        request = core.status(custody.request_id, root=custody.root)
        digest = engine.runtime.fingerprint_source(engine._source_files(tuple(packages.values())))
        if (request.id != custody.request_id or request.state != "running"
                or request.spec.backend != "plugin-native-install"
                or request.spec.project != str(root.resolve()) or request.spec.source != digest):
            raise UninstallRefused("Native Vault removal has no matching running request")
        recipe = _closed_flags(request.spec.argv, {"-action", "-installdir"},
                               {"-noservice", "-purge", "-force"})
        if recipe.get("-action") != "uninstall" or Path(recipe.get("-installdir", "")) != root:
            raise UninstallRefused("Native Vault removal recipe changed")

        def native_driver(arguments, role):
            if arguments[1:6] != ["-I", "-S", "-B", "-X", "utf8"] or len(arguments) < 7:
                raise UninstallRefused("Unexpected native Vault driver entry")
            options, native_options = {}, []
            index = 7
            while index < len(arguments):
                flag = arguments[index]
                if flag in {"--participant", "--check-native", "--remove-broker"}:
                    if flag in options:
                        raise UninstallRefused("Duplicate native Vault controller")
                    options[flag] = True
                else:
                    index += 1
                    if index == len(arguments):
                        raise UninstallRefused("Incomplete native Vault controller")
                    value = arguments[index]
                    if flag == "--option":
                        native_options.append("-" + value.lower())
                    elif flag in {"--payload", "--root", "--shell", "--plugin", "--action"} and flag not in options:
                        options[flag] = value
                    else:
                        raise UninstallRefused("Unexpected native Vault controller argument")
                index += 1
            source = Path(options.pop("--payload", ""))
            expected = {
                "--root": str(root), "--shell": shell, "--plugin": "agent-vault", "--action": "uninstall",
            }
            if role:
                expected["--" + role] = True
            if role == "remove-broker":
                expected["--check-native"] = True
            if (not source.is_absolute() or ".." in source.parts or options != expected
                    or Path(arguments[6]) != source / "scripts/installer_driver.py"
                    or len(native_options) != len(set(native_options))
                    or set(native_options) != set(recipe) - {"-action", "-installdir"}):
                raise UninstallRefused("Native Vault controller did not forward its recipe")
            _plain(Path(arguments[6]), file=True)
            return source

        original = native_driver(list(request.runner.argv), "participant")
        sources = {engine._package_name(path): path for path in engine._package_roots(original)}
        if (set(sources) != set(packages)
                or request.spec.source != engine.runtime.fingerprint_source(
                    engine._source_files(tuple(sources.values())),
                )):
            raise UninstallRefused("Native Vault original source changed")
        witness = participant.participant_environment().get(participant.ENVIRONMENT_KEY)
        if not witness:
            raise UninstallRefused("Native Vault removal has no scoped participant")
        chain = []

        def take():
            pid = os.getpid() if not chain else chain[-1]["ppid"]
            matches = [row for row in rows if row["pid"] == pid]
            if len(matches) > 1 or pid in {row["pid"] for row in chain} or len(chain) == 7:
                raise UninstallRefused("Native Vault controller ancestry is ambiguous")
            row = matches[0] if matches else _controller_row(payload, shell, pid)
            current = _controller_row(payload, shell, pid)
            if (any(current[key] != row[key] for key in ("pid", "ppid", "command"))
                    or Path(current["image"]) != Path(row["image"])):
                raise UninstallRefused("Native Vault controller identity changed")
            chain.append(current)
            return current

        inspector = take()
        arguments = _controller_python(inspector, root)
        if Path(sys.argv[0]) == payload / "scripts/installer_driver.py":
            if native_driver(arguments, "remove-broker") != payload:
                raise UninstallRefused("Native Vault broker callback changed")
        else:
            if len(arguments) < 4:
                raise UninstallRefused("Native Vault finite removal entry is incomplete")
            flags = _closed_flags(arguments[4:], {
                "--payload-root", "--plugin-id", "--root", "--shell",
            }, {"--remove-runtime", "--check-consumers", "--check"})
            if (arguments[1:3] != ["-I", "-B"]
                    or Path(arguments[3]) != payload / "scripts/installer_engine_uninstall.py"
                    or Path(sys.argv[0]) != Path(arguments[3])
                    or flags.get("--payload-root") != str(payload) or flags.get("--plugin-id") != "agent-vault"
                    or flags.get("--root") != str(root) or flags.get("--shell") != shell
                    or not (flags.get("--remove-runtime") or flags.get("--check-consumers"))):
                raise UninstallRefused("Native Vault finite removal callback changed")
        if inspector["ppid"] != os.getppid():
            raise UninstallRefused("Native Vault callback parent changed")

        def native_shell(row, source):
            arguments = _windows_argv(row["command"])
            lower = [value.lower() for value in arguments]
            if (not arguments or Path(row["image"]) != Path(shell) or lower.count("-file") != 1
                    or Path(arguments[0]).name.lower() != Path(shell).name.lower()):
                raise UninstallRefused("Native Vault shell changed")
            index = lower.index("-file")
            if index + 1 >= len(arguments):
                raise UninstallRefused("Native Vault shell entry is incomplete")
            flags = _closed_flags(arguments[1:index], {"-executionpolicy"},
                                  {"-nologo", "-noprofile", "-noninteractive"})
            tail = arguments[index + 2:]
            if tail and tail[0] == "uninstall":
                tail = ["-Action", "uninstall", *tail[1:]]
            if (flags.get("-executionpolicy", "bypass").lower() != "bypass"
                    or Path(arguments[index + 1]) != source / "scripts/install.ps1"
                    or _closed_flags(tail, {"-action", "-installdir"},
                                     {"-noservice", "-purge", "-force"}) != recipe):
                raise UninstallRefused("Native Vault shell did not forward its recipe")

        native_shell(take(), payload)
        if payload != original:
            parent = take()
            if (payload.name != "agent-vault"
                    or payload.parent.parent != Path.home() / ".agent-vault/.install-stage"
                    or re.fullmatch(r"[0-9]{8}T[0-9]{9}-" + str(parent["pid"]), payload.parent.name) is None):
                raise UninstallRefused("Native Vault source is not its owned stage")
            native_shell(parent, original)
        if tuple(_controller_python(take(), root)) != request.runner.argv:
            raise UninstallRefused("Native Vault participant is not its pinned runner")
        arguments = _controller_python(take(), root)
        runner = sources["agent-update-driver"] / "src/update_driver/_runner.py"
        procutil = sources["agent-procutil"] / "src/agent_procutil/__init__.py"
        if (len(arguments) == 10 and arguments[1:4] == ["-I", "-S", "-X"]
                and arguments[4].startswith("pycache_prefix=")):
            prefix = arguments[4].removeprefix("pycache_prefix=")
            if prefix.startswith("\\\\?\\UNC\\"):
                prefix = "\\\\" + prefix[8:]
            elif prefix.startswith("\\\\?\\"):
                prefix = prefix[4:]
            cache = Path(prefix)
            if (cache.parent == custody.root / "cache/python" and ".." not in cache.parts
                    and re.fullmatch(r"(?:installed-[0-9a-f]{64}|unsealed-[0-9a-f]{32})", cache.name)):
                arguments = [*arguments[:3], *arguments[5:]]
        if len(arguments) == 9 and arguments[1:4] == ["-I", "-S", "-B"]:
            arguments = [*arguments[:3], *arguments[4:]]
        if (len(arguments) != 8 or arguments[1:3] != ["-I", "-S"] or Path(arguments[3]) != runner
                or Path(arguments[4]) != custody.root or arguments[5] != request.id
                or arguments[6] != witness or Path(arguments[7]) != procutil):
            raise UninstallRefused("Native Vault execution shim is not its live request")
        if native_driver(_controller_python(take(), root), "") != original:
            raise UninstallRefused("Native Vault foreground controller changed")
        original_host = take()
        if Path(original_host["image"]) != Path(shell) or Path(shell).is_relative_to(root):
            raise UninstallRefused("Native Vault original host changed")
        if not _publisher_identity(payload, shell, chain):
            raise UninstallRefused("Native Vault controller identity is unverified")
        custody.assert_owned()
        current = core.status(custody.request_id, root=custody.root)
        if (current.state != "running" or current.spec != request.spec or current.runner != request.runner
                or request.spec.source != engine.runtime.fingerprint_source(
                    engine._source_files(tuple(packages.values())),
                ) or request.spec.source != engine.runtime.fingerprint_source(
                    engine._source_files(tuple(sources.values())),
                )):
            raise UninstallRefused("Native Vault request changed during inspection")
        return {row["pid"] for row in chain[1:]}

    return SimpleNamespace(publisher_identity=_publisher_identity, worktrees_controllers=_worktrees_controllers,
                           native_plugin_controllers=_native_plugin_controllers)

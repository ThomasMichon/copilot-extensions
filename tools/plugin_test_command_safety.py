"""Reject external test commands that can control resources outside their scope."""

from __future__ import annotations

import ast
import base64
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys
import uuid
from collections.abc import Mapping, Sequence
from functools import lru_cache

SANDBOX_ENV = "COPILOT_EXTENSIONS_TEST_SANDBOX"
_MUX = {"tmux", "psmux"}
_SHELLS = {"sh", "bash", "dash", "zsh", "cmd", "powershell", "pwsh"}
_EXEC_VECTOR = {"execv", "execve", "execvp", "execvpe", "posix_spawn", "posix_spawnp"}
_EXEC_LIST = {"execl", "execle", "execlp", "execlpe"}
_SPAWN_VECTOR = {"spawnv", "spawnve", "spawnvp", "spawnvpe"}
_SPAWN_LIST = {"spawnl", "spawnle", "spawnlp", "spawnlpe"}
_SINKS = {
    "run", "Popen", "call", "check_call", "check_output", "system",
    "create_subprocess_exec", "create_subprocess_shell", "command", "mux", "_mux",
    "kill", "killpg",
} | _EXEC_VECTOR | _EXEC_LIST | _SPAWN_VECTOR | _SPAWN_LIST
_SOURCE_MARKERS = re.compile(
    r"\b(?:pkill|killall|taskkill|kill|stop|restart|shutdown|terminate)|\bwsl\b|\bdocker\b|\bpodman\b|"
    r"\bsystemctl\b|\bsc\b|\bnet\b|\bservice\b|\b(?:pwsh|powershell|spps|spsv)\b|\\(?:[xuUN]|[0-7])",
    re.IGNORECASE,
)
_ADJACENT_LITERALS = re.compile(r"""['"][^'"\r\n]+['"]\s*[bruBRU]?['"][^'"\r\n]+['"]""")
_installed = False
_UNKNOWN_CWD = object()


class UnsafeTestCommand(RuntimeError):
    """The command cannot safely target resources on a shared test host."""


def _within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def _prefix(sandbox: Path) -> str:
    identity = os.path.normcase(str(sandbox.resolve())).encode("utf-8")
    return "ce-test-" + hashlib.sha256(identity).hexdigest()[:12] + "-"


def private_mux_args(root: Path) -> list[str]:
    """Allocate an explicit mux namespace inside the current runner's scope."""
    sandbox = os.environ.get(SANDBOX_ENV)
    if not sandbox or not _within(root, Path(sandbox)):
        raise UnsafeTestCommand("mux fixture root must be inside the runner sandbox")
    if os.name == "nt":
        return ["-L", _prefix(Path(sandbox)) + uuid.uuid4().hex]
    return ["-S", str(root.resolve() / "mux.sock")]


@lru_cache(maxsize=1)
def _windows_parser():
    import ctypes
    from ctypes import wintypes

    parse = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    return parse, free


def _tokens(command: object) -> list[str]:
    if isinstance(command, (str, bytes)):
        text = os.fsdecode(command)
        if "\0" in text:
            raise UnsafeTestCommand("cannot inspect a test command containing NUL")
        if not text.strip():
            return []
        if os.name == "nt":
            import ctypes

            parse, free = _windows_parser()
            count = ctypes.c_int()
            values = parse(text, ctypes.byref(count))
            if not values:
                raise UnsafeTestCommand("cannot inspect a Windows test command")
            try:
                return [values[index] for index in range(count.value)]
            finally:
                free(ctypes.cast(values, ctypes.c_void_p))
        try:
            return shlex.split(text)
        except ValueError:
            raise UnsafeTestCommand("cannot inspect a malformed test command") from None
    if isinstance(command, Sequence):
        return [os.fsdecode(part) for part in command]
    raise UnsafeTestCommand("cannot inspect the test command")


def _name(value: str) -> str:
    return re.split(r"[\\/]", value)[-1].lower().removesuffix(".exe")


def _global_problem(parts: list[str]) -> str | None:
    if not parts:
        return None
    name = _name(parts[0])
    words = [part.lower() for part in parts[1:]]
    if name in _MUX and "kill-server" in words:
        return "server-wide mux cleanup is forbidden; retire only owned sessions/processes"
    if name in {"pkill", "killall"}:
        return "name/pattern-based process termination is forbidden"
    if name == "taskkill" and "/?" not in words:
        if (
            "/pid" not in words or any(word.startswith(("/im", "/fi")) for word in words)
            or any(
                index + 1 >= len(words) or not words[index + 1].isdigit()
                or int(words[index + 1]) <= 0
                for index, word in enumerate(words) if word == "/pid"
            )
        ):
            return "bulk/image-name process termination is forbidden; use owned process handles"
    if name == "stop-process" and any(re.match(r"^-(?:n|na|nam|name)(?::|$)", word) for word in words):
        return "name-based process termination is forbidden; use owned process handles"
    if name == "kill" and any(word in {"-1", "0", "*", "--all"} for word in words):
        return "implicit/global process-group termination is forbidden"
    if name == "systemctl" and any(word in {"stop", "restart", "kill", "reboot", "poweroff"} for word in words):
        return "test cleanup must not stop shared host services"
    if name in {"sc", "net", "service"} and any(word in {"stop", "restart"} for word in words):
        return "test cleanup must not stop shared host services"
    if name in {"stop-service", "restart-service", "spsv"}:
        return "test cleanup must not stop shared host services"
    if name == "wsl" and any(word in {"--shutdown", "--terminate", "-t"} for word in words):
        return "test cleanup must not stop a shared WSL runtime"
    if name in {"docker", "podman"} and "prune" in words:
        return "host-wide container/image pruning is forbidden"
    if name in {"docker", "podman"} and any(word in {"stop", "kill", "rm"} for word in words):
        if any(word in {"--all", "-a"} or "$(" in word or "*" in word for word in words):
            return "bulk container cleanup is forbidden; target an owned fixture"
    return None


def _command_lookup(parts: list[str]) -> bool:
    if not parts or _name(parts[0]) != "command":
        return False
    for flag in parts[1:]:
        if not flag.startswith("-") or not flag[1:] or set(flag[1:]) - set("pvV"):
            break
        if "v" in flag or "V" in flag:
            return True
    return False


def _launcher_problem(
    parts: list[str], *, cwd: str | Path | object | None,
    environment: Mapping[str, str] | None, sandbox: Path | None,
    check_mux_scope: bool,
) -> str | None:
    name = _name(parts[0])
    if _command_lookup(parts):
        return None
    effective = dict(os.environ if environment is None else environment)
    nested = parts[1:]
    if name == "ssh":
        text = " ".join(nested)
        if check_mux_scope and re.search(r"\b(?:psmux|tmux)(?:\.exe)?\b", text, re.IGNORECASE):
            return "remote mux commands require independently verified remote fixture scope"
        return _shell_problem(text, check_mux_scope=False)
    if name in {"sudo", "doas"}:
        # Privilege launchers may replace the environment. An inherited TMUX
        # socket is not scope evidence after crossing that boundary.
        effective = {}
    index = 0
    while index < len(nested):
        argument = nested[index]
        if argument == "--":
            index += 1
            break
        if name == "env" and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argument):
            break
        if argument in {"--help", "--version"}:
            return None
        if not argument.startswith("-") or argument == "-":
            if name == "env" and argument == "-":
                effective.clear()
                index += 1
                continue
            break
        if name == "env" and argument in {"-i", "--ignore-environment"}:
            effective.clear()
            index += 1
            continue
        option, separator, attached = argument.partition("=")
        takes_value = {
            "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
            "timeout": {"-k", "--kill-after", "-s", "--signal"},
            "sudo": {"-u", "--user", "-g", "--group", "-p", "--prompt", "-D", "--chdir"},
            "doas": {"-u", "-C"},
            "exec": {"-a"},
        }.get(name, set())
        if name == "env" and argument[:2] in {"-u", "-C", "-S"} and len(argument) > 2:
            option, separator, attached = argument[:2], "=", argument[2:]
        if option in takes_value:
            if not separator and index + 1 >= len(nested):
                return "cannot inspect a launcher option without its value"
            value = attached if separator else nested[index + 1]
            index += 1 if separator else 2
            if name == "env" and option in {"-u", "--unset"}:
                for key in list(effective):
                    if key == value or os.name == "nt" and key.upper() == value.upper():
                        effective.pop(key)
            elif option in {"-D", "--chdir"} or name == "env" and option in {"-C", "--chdir"}:
                path = Path(value)
                if path.is_absolute():
                    cwd = path
                elif cwd is not _UNKNOWN_CWD:
                    cwd = (Path(os.fsdecode(cwd)) if cwd is not None else Path.cwd()) / path
            elif name == "env" and option in {"-S", "--split-string"}:
                try:
                    nested[index:index] = shlex.split(value)
                except ValueError:
                    return "cannot inspect a malformed env split-string"
            continue
        flags = {
            "env": {"-v", "--debug", "--null", "-0"},
            "timeout": {"--foreground", "--preserve-status", "-v", "--verbose"},
            "sudo": {"-n", "--non-interactive", "-E", "--preserve-env", "-H", "-S"},
            "doas": {"-n"},
            "nohup": set(),
            "command": {"-p"},
            "exec": {"-l", "-c"},
        }.get(name, set())
        if argument not in flags:
            return "cannot inspect an unsupported " + name + " launcher option"
        if name == "exec" and argument == "-c":
            effective.clear()
        index += 1
    if name == "env":
        while index < len(nested) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", nested[index]):
            key, value = nested[index].split("=", 1)
            if os.name == "nt":
                for previous in list(effective):
                    if previous.upper() == key.upper():
                        effective.pop(previous)
            effective[key] = value
            index += 1
    elif name == "timeout" and index < len(nested):
        index += 1  # The duration precedes the executable.
    return _command_problem(
        nested[index:], cwd=cwd, environment=effective, sandbox=sandbox,
        check_mux_scope=check_mux_scope,
    )


def _shell_problem(
    text: str, *, cwd: str | Path | object | None = None,
    environment: Mapping[str, str] | None = None, sandbox: Path | None = None,
    check_mux_scope: bool = True, powershell: bool = False,
) -> str | None:
    # Shell strings include PowerShell pipelines and command substitution. A
    # conservative refusal is preferable to executing an ambiguous cleanup.
    if not re.search(r"[\r\n;|&`$()<>]", text):
        try:
            if _command_lookup(shlex.split(text)):
                return None
        except ValueError:
            pass
    patterns = (
        (r"\b(?:psmux|tmux)(?:\.exe)?\b[^\r\n;|]*\bkill-server\b", "server-wide mux cleanup"),
        (r"\b(?:pkill|killall)\b", "name/pattern-based process termination"),
        (r"\btaskkill(?:\.exe)?\b[^\r\n;|]*/(?:im|fi)\b", "bulk/image-name process termination"),
        (r"\bstop-process\b[^\r\n;|]*-(?:n|na|nam|name)\b", "name-based process termination"),
        (r"\bwsl(?:\.exe)?\b[^\r\n;|]*(?:--(?:shutdown|terminate)|-t)\b", "shared WSL shutdown"),
        (r"\b(?:docker|podman)\b[^\r\n;|]*\bprune\b", "host-wide container/image pruning"),
        (r"\bsystemctl\b[^\r\n;|]*\b(?:stop|restart|kill|reboot|poweroff)\b", "shared service shutdown"),
        (r"\b(?:stop-service|restart-service|spsv)\b", "shared service shutdown"),
        (r"\b(?:net|sc|service)(?:\.exe)?\b[^\r\n;|]*\b(?:stop|restart)\b", "shared service shutdown"),
        (r"\b(?:docker|podman)\b[^\r\n;|]*\b(?:stop|kill|rm)\b[^\r\n;|]*(?:--all|\$\(|\*)", "bulk container cleanup"),
    )
    for pattern, reason in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return reason + " is forbidden in test shell commands"
    termination = r"(?:stop-process|spps|kill)" if powershell else r"(?:stop-process|spps)"
    for match in re.finditer(
        r"(?:^|[;\r\n{|&])\s*" + termination + r"\b([^\r\n;|]*)",
        text, re.IGNORECASE,
    ):
        if not re.search(r"-(?:id|inputobject)\b", match[1], re.IGNORECASE):
            return "PowerShell termination requires explicit owned -Id/-InputObject targets"
    if re.search(
        r"(?:^|[\s;{(])(?:cd|chdir|pushd|popd|set-location|sl|push-location|pop-location)\b",
        text, re.IGNORECASE,
    ):
        cwd = _UNKNOWN_CWD
    if re.search(
        r"(?:^|[\s;{(])(?:unset|unsetenv|setenv|export|set|declare|typeset|local|readonly)\b"
        r"|\$env:|\b(?:env|TMUX)\s*=|SetEnvironmentVariable|(?:Remove|Set|Clear)-Item\b[^\r\n;|]*\bEnv:",
        text, re.IGNORECASE,
    ):
        environment = {}
    for segment in re.split(r"[\r\n;|&]+", text):
        if not check_mux_scope and not _SOURCE_MARKERS.search(segment):
            continue
        try:
            parts = _tokens(segment.strip())
        except UnsafeTestCommand:
            continue  # A separator may be inside a quoted, non-command argument.
        candidates = [parts]
        try:
            candidates.append(shlex.split(segment.strip()))
        except ValueError:
            pass
        for candidate in candidates:
            if candidate:
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", candidate[0]):
                    candidate = ["env", *candidate]
                problem = _command_problem(
                    candidate, cwd=cwd, environment=environment, sandbox=sandbox,
                    check_mux_scope=check_mux_scope,
                )
                if problem:
                    return problem
    return None


def command_problem(
    command: object, *, cwd: str | Path | None = None,
    environment: Mapping[str, str] | None = None, sandbox: Path | None = None,
) -> str | None:
    """Inspect an actual external command, not a mocked command construction."""
    return _command_problem(command, cwd=cwd, environment=environment, sandbox=sandbox)


def _command_problem(
    command: object, *, cwd: str | Path | object | None = None,
    environment: Mapping[str, str] | None = None, sandbox: Path | None = None,
    check_mux_scope: bool = True,
) -> str | None:
    parts = _tokens(command)
    problem = _global_problem(parts)
    if problem or not parts:
        return problem
    name = _name(parts[0])
    if name in {"ssh", "sudo", "doas", "env", "timeout", "nohup", "command", "exec"}:
        return _launcher_problem(
            parts, cwd=cwd, environment=environment, sandbox=sandbox,
            check_mux_scope=check_mux_scope,
        )
    if name in _SHELLS:
        is_powershell = name in {"pwsh", "powershell"}
        index = 1
        while index < len(parts):
            flag = parts[index].lower()
            if is_powershell:
                flag = "-" + flag[1:] if flag.startswith("/") else flag
            if is_powershell and flag.startswith("-f") and "-file".startswith(flag):
                break
            if flag == "--" or not flag.startswith(("-", "/")):
                break
            if is_powershell and (
                flag in {"-e", "-ec"} or flag.startswith("-en") and "-encodedcommand".startswith(flag)
            ):
                try:
                    script = base64.b64decode(parts[index + 1], validate=True).decode("utf-16-le")
                except (IndexError, ValueError, UnicodeError):
                    return "cannot inspect an encoded PowerShell test command"
                return _shell_problem(
                    script, cwd=cwd, environment=environment, sandbox=sandbox, powershell=True,
                    check_mux_scope=check_mux_scope,
                )
            command_flag = (
                is_powershell and flag.startswith("-c") and "-command".startswith(flag)
                or name == "cmd" and flag in {"/c", "/k"}
                or not is_powershell and name != "cmd"
                and re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", flag) is not None
            )
            if command_flag:
                return _shell_problem(
                    " ".join(parts[index + 1:]),
                    cwd=cwd, environment=environment, sandbox=sandbox, powershell=is_powershell,
                    check_mux_scope=check_mux_scope,
                )
            value_flags = (
                {"-executionpolicy", "-ep", "-inputformat", "-outputformat", "-windowstyle",
                 "-version", "-workingdirectory", "-wd", "-settingsfile"}
                if is_powershell else {"-o", "--rcfile", "--init-file"}
            )
            index += 2 if flag in value_flags or parts[index] == "-O" else 1
    if (
        not check_mux_scope or name not in _MUX
        or parts[1:] in (["-V"], ["--version"], ["--help"], ["-h"])
    ):
        return None
    if sandbox is None:
        return "real mux commands require the runner's verified private namespace"
    base = (
        None if cwd is _UNKNOWN_CWD
        else Path(os.fsdecode(cwd)) if cwd is not None else Path.cwd()
    )
    selectors = []
    index = 1
    while index < len(parts):
        flag = parts[index]
        if flag == "--" or not flag.startswith("-"):
            break
        if flag in {"-S", "-L"}:
            if index + 1 >= len(parts) or not parts[index + 1]:
                return "mux namespace is missing"
            selectors.append((flag, parts[index + 1]))
            index += 2
        elif "S" in flag or "L" in flag:
            return "ambiguous mux namespace: use separate -S or -L arguments"
        else:
            index += 2 if flag in {"-f", "-c", "-T"} else 1
    if len(selectors) > 1:
        return "ambiguous mux namespace: supply exactly one separate -S or -L selector"
    for flag, value in selectors:
        if flag == "-S":
            if os.name == "nt":
                return "Windows mux fixtures require a runner-owned -L namespace"
            socket = Path(value)
            if not socket.is_absolute():
                if base is None:
                    return "relative mux scope cannot be verified after a shell directory change"
                socket = base / socket
            if _within(socket, sandbox):
                return None
        elif re.fullmatch(re.escape(_prefix(sandbox)) + r"[a-f0-9]{32}", value):
            return None
        return "mux namespace does not belong to this runner sandbox"
    # POSIX tmux specifies this socket in its documented TMUX protocol. HOME,
    # USERPROFILE and PSMUX_DATA_DIR do not prove Windows mux routing.
    if os.name != "nt" and name == "tmux":
        inherited = (environment if environment is not None else os.environ).get("TMUX", "")
        socket_name = inherited.split(",", 1)[0]
        if socket_name:
            socket = Path(socket_name)
            if not socket.is_absolute():
                if base is None:
                    return "relative inherited mux scope requires a verified working directory"
                socket = base / socket
            if _within(socket, sandbox):
                return None
    return "unscoped mux command: use a verified private -S socket or runner-owned -L namespace"


def validate_private_mux_args(
    arguments: object, *, sandbox: Path, cwd: Path | None = None,
) -> list[str]:
    """Reject absent, default or foreign transport before a native fixture starts."""
    if (
        not isinstance(arguments, list) or len(arguments) != 2
        or any(not isinstance(part, str) for part in arguments)
        or arguments[0] not in {"-S", "-L"}
    ):
        raise UnsafeTestCommand("native fixture requires an explicit private mux namespace")
    problem = command_problem(
        ["psmux", *arguments, "has-session", "-t", "fixture"],
        sandbox=sandbox, cwd=cwd,
    )
    if problem:
        raise UnsafeTestCommand(problem)
    return arguments


def _literal(node: ast.AST | None) -> object:
    try:
        return ast.literal_eval(node) if node is not None else None
    except (ValueError, TypeError):
        return None


def _source_invocation(node: ast.Call, function: str) -> tuple[object, ast.AST | None]:
    argument = node.args[0] if node.args else next(
        (keyword.value for keyword in node.keywords if keyword.arg == "args"), None,
    )
    value = _literal(argument)
    if function in _EXEC_VECTOR | _EXEC_LIST | _SPAWN_VECTOR | _SPAWN_LIST:
        index = 1 if function in _SPAWN_VECTOR | _SPAWN_LIST else 0
        executable = _literal(node.args[index]) if len(node.args) > index else None
        if function in _EXEC_LIST | _SPAWN_LIST:
            arguments = node.args[index + 1:]
            if function.endswith("e"):
                arguments = arguments[:-1]
            argument = ast.List(elts=arguments, ctx=ast.Load())
            argv = [_literal(part) for part in arguments]
        else:
            argument = node.args[index + 1] if len(node.args) > index + 1 else None
            argv = _literal(argument)
    else:
        executable = next(
            (_literal(keyword.value) for keyword in node.keywords if keyword.arg == "executable"),
            None,
        )
        argv = value
    if isinstance(executable, (str, bytes)):
        if isinstance(argv, (list, tuple)) and all(isinstance(part, (str, bytes)) for part in argv):
            return [executable, *argv[1:]], argument
        return [executable], argument
    if function in _EXEC_VECTOR | _EXEC_LIST | _SPAWN_VECTOR | _SPAWN_LIST:
        return argv, argument
    return value, argument


def source_problems(path: Path) -> list[str]:
    """Catch literal destructive cleanup in child scripts before test collection."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise UnsafeTestCommand(f"cannot inspect test helper {path}") from error
    if not _SOURCE_MARKERS.search(text) and not _ADJACENT_LITERALS.search(text):
        return []
    if path.suffix.lower() != ".py":
        reason = _shell_problem(text, check_mux_scope=False, powershell=path.suffix.lower() == ".ps1")
        return [f"{path}:1: {reason}"] if reason else []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                script = node.value.value
                if "\n" in script and (reason := _shell_problem(
                    script, check_mux_scope=False,
                )):
                    problems.append(f"{path}:{node.lineno}: {reason}")
        if not isinstance(node, ast.Call):
            continue
        function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if function not in _SINKS:
            continue
        value, argument = _source_invocation(node, function)
        if function in {"kill", "killpg"} and isinstance(value, int) and value <= 0:
            reason = "implicit/global process termination is forbidden"
        elif value in ("kill-server", b"kill-server"):
            reason = "server-wide mux cleanup is forbidden"
        elif isinstance(value, (str, bytes)):
            reason = _shell_problem(os.fsdecode(value), check_mux_scope=False)
        elif isinstance(value, (list, tuple)) and all(isinstance(part, (str, bytes)) for part in value):
            reason = _command_problem(
                [os.fsdecode(part) for part in value], check_mux_scope=False,
            )
        else:
            reason = None
        if reason is None and isinstance(argument, (ast.List, ast.Tuple)) and _literal(argument) is None:
            if any(
                isinstance(part, ast.Constant) and part.value in ("kill-server", b"kill-server")
                for part in argument.elts
            ):
                reason = "server-wide mux cleanup is forbidden"
        if reason:
            problems.append(f"{path}:{node.lineno}: {reason}")
    return problems


def check_test_sources(root: Path) -> None:
    """Fail before executing tests, including helpers launched with Python -I/-S."""
    problems = [
        problem for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".py", ".ps1", ".sh", ".cmd", ".bat"}
        and "__pycache__" not in path.parts
        for problem in source_problems(path)
    ]
    if problems:
        raise UnsafeTestCommand("unsafe test cleanup:\n" + "\n".join(problems))


def install_command_guard(sandbox: Path | None = None) -> None:
    """Reject dangerous Python subprocess launches before the OS sees them."""
    global _installed
    if _installed:
        return
    _installed = True

    def guard(event: str, arguments: tuple) -> None:
        if event == "subprocess.Popen":
            executable, command, cwd, environment = arguments
            parts = _tokens(command)
            problem = command_problem(parts, cwd=cwd, environment=environment, sandbox=sandbox)
            if problem is None and executable and (
                not parts or _name(os.fsdecode(executable)) != _name(parts[0])
            ):
                problem = command_problem(
                    [os.fsdecode(executable), *parts[1:]], cwd=cwd,
                    environment=environment, sandbox=sandbox,
                )
        elif event == "os.system":
            problem = _shell_problem(os.fsdecode(arguments[0]), sandbox=sandbox)
        elif event in {"os.exec", "os.posix_spawn"}:
            executable, command, environment = arguments
            parts = _tokens(command)
            problem = command_problem(
                [os.fsdecode(executable), *parts[1:]], environment=environment, sandbox=sandbox,
            )
        elif event == "os.spawn":
            _mode, executable, command, environment = arguments
            parts = _tokens(command)
            problem = command_problem(
                [os.fsdecode(executable), *parts[1:]], environment=environment, sandbox=sandbox,
            )
        elif event in {"os.kill", "os.killpg"}:
            problem = (
                "implicit/global process termination is forbidden"
                if arguments[0] <= 0 else None
            )
        else:
            return
        if problem:
            raise UnsafeTestCommand(problem)

    sys.addaudithook(guard)

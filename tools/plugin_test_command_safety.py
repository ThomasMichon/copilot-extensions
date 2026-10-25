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
_SINKS = {
    "run", "Popen", "call", "check_call", "check_output", "system",
    "create_subprocess_exec", "create_subprocess_shell", "command", "mux", "_mux",
    "kill", "killpg",
}
_SOURCE_MARKERS = re.compile(
    r"kill|stop|restart|shutdown|terminate|\bwsl\b|\bdocker\b|\bpodman\b|"
    r"\bsystemctl\b|\bsc\b|\bnet\b|\bservice\b|\\(?:[xuUN]|[0-7])",
    re.IGNORECASE,
)
_ADJACENT_LITERALS = re.compile(r"""['"][^'"\r\n]+['"]\s*[bruBRU]?['"][^'"\r\n]+['"]""")
_installed = False


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
    if name in {"ssh", "sudo", "doas", "env", "timeout", "nohup"}:
        return _shell_problem(" ".join(parts[1:]), check_mux_scope=False)
    return None


def _shell_problem(
    text: str, *, cwd: str | Path | None = None,
    environment: Mapping[str, str] | None = None, sandbox: Path | None = None,
    check_mux_scope: bool = True, powershell: bool = False,
) -> str | None:
    # Shell strings include PowerShell pipelines and command substitution. A
    # conservative refusal is preferable to executing an ambiguous cleanup.
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
    if powershell:
        for match in re.finditer(
            r"(?:^|[;\r\n{|&])\s*(?:stop-process|spps|kill)\b([^\r\n;|]*)",
            text, re.IGNORECASE,
        ):
            if not re.search(r"-(?:id|inputobject)\b", match[1], re.IGNORECASE):
                return "PowerShell termination requires explicit owned -Id/-InputObject targets"
    if not check_mux_scope:
        return None
    for segment in re.split(r"[\r\n;|&]+", text):
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
            if candidate and _name(candidate[0]) not in _SHELLS:
                problem = _global_problem(candidate)
                if problem:
                    return problem
                if _name(candidate[0]) in _MUX:
                    problem = command_problem(candidate, cwd=cwd, environment=environment, sandbox=sandbox)
                    if problem:
                        return problem
    return None


def command_problem(
    command: object, *, cwd: str | Path | None = None,
    environment: Mapping[str, str] | None = None, sandbox: Path | None = None,
) -> str | None:
    """Inspect an actual external command, not a mocked command construction."""
    parts = _tokens(command)
    problem = _global_problem(parts)
    if problem or not parts:
        return problem
    name = _name(parts[0])
    if name in _SHELLS:
        lowered = [part.lower() for part in parts]
        is_powershell = name in {"pwsh", "powershell"}
        for index, flag in enumerate(lowered[1:], start=1):
            if flag in {"-file", "-f", "-command", "-c", "/c", "/k"}:
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
                )
        for flag in ("-command", "-c", "/c", "/k"):
            if flag in lowered:
                return _shell_problem(
                    " ".join(parts[lowered.index(flag) + 1:]),
                    cwd=cwd, environment=environment, sandbox=sandbox, powershell=is_powershell,
                )
    if name not in _MUX or parts[1:] in (["-V"], ["--version"], ["--help"], ["-h"]):
        return None
    if sandbox is None:
        return "real mux commands require the runner's verified private namespace"
    base = Path(os.fsdecode(cwd)) if cwd is not None else Path.cwd()
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
                if "\n" in script and (reason := _shell_problem(script, check_mux_scope=False)):
                    problems.append(f"{path}:{node.lineno}: {reason}")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if function not in _SINKS:
            continue
        argument = node.args[0]
        try:
            value = ast.literal_eval(argument)
        except (ValueError, TypeError):
            value = None
        if function in {"kill", "killpg"} and isinstance(value, int) and value <= 0:
            reason = "implicit/global process termination is forbidden"
        elif value in ("kill-server", b"kill-server"):
            reason = "server-wide mux cleanup is forbidden"
        elif isinstance(value, (str, bytes)):
            reason = _shell_problem(os.fsdecode(value), check_mux_scope=False)
        elif isinstance(value, (list, tuple)) and all(isinstance(part, (str, bytes)) for part in value):
            reason = _global_problem([os.fsdecode(part) for part in value])
        else:
            reason = None
            if isinstance(argument, (ast.List, ast.Tuple)):
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

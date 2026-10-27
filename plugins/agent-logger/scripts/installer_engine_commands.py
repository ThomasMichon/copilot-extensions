"""Exact legacy global-command ownership, without executing installer sources.

The caller supplies trusted payload sources and already-read ordinary command
files. Templates come only from the declared current native writers below.
This does not recognize Index, historical guesses, project shortcuts, or
namespaced Containers bindings. Delegating cmd wrappers require their owned
PowerShell peer; MCP/Machines standalone cmd programs have no such dependency.
"""

from __future__ import annotations

import json
import re
import shlex
import stat
from pathlib import Path


_PRODUCTS = (
    "agent-bridge", "agent-dispatch", "agent-vault", "budget-guidance",
    "agent-mcp", "agent-logger", "agent-codespaces", "agent-ssh",
    "agent-pull-requests", "agent-machines", "agent-containers", "agent-worktrees",
)
_INIT = {"agent-mcp", "agent-machines", "agent-containers"}
_STANDALONE_CMD = {"agent-mcp", "agent-machines"}
_SIMPLE = {"agent-vault", "agent-ssh", "agent-pull-requests"}
_DEFAULT_ONLY = {
    "agent-bridge", "agent-dispatch", "budget-guidance", "agent-mcp",
    "agent-logger", "agent-machines", "agent-worktrees",
}
_AUXILIARY = (
    "session-sync", "collate-session", "read-session-digest",
    "prepare-session-log", "ramp-up-session",
)


def _read(path: Path) -> str:
    try:
        info = path.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if not stat.S_ISREG(info.st_mode) or attributes & 0x400:
            raise ValueError(f"Command template is not an ordinary file: {path}")
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Cannot read trusted command template {path}: {exc}") from exc


def _one(matches: list[str], label: str) -> str:
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {label}; found {len(matches)}")
    return matches[0]


def _function(source: str, name: str, ps: bool = False) -> str:
    opening = rf"function {re.escape(name)} \{{" if ps else rf"{re.escape(name)}\(\) \{{"
    starts = list(re.finditer(rf"(?m)^{opening}\n", source))
    if len(starts) != 1:
        raise RuntimeError(f"Expected one native writer {name}; found {len(starts)}")
    lines = source[starts[0].end():].splitlines(keepends=True)
    body = []
    delimiter = None
    for line in lines:
        if delimiter is not None:
            body.append(line)
            if (ps and line.startswith(delimiter)) or line.rstrip("\n") == delimiter:
                delimiter = None
            continue
        if line.rstrip("\n") == "}":
            return "".join(body)
        body.append(line)
        if ps:
            match = re.search(r"= @(['\"])\s*$", line.rstrip("\n"))
            if match:
                delimiter = match[1] + "@"
        else:
            match = re.search(r"<<\s*'?([A-Za-z_][A-Za-z_0-9]*)'?", line)
            if match:
                delimiter = match[1]
    raise RuntimeError(f"Unterminated native writer {name}")


def _ps(source: str, variable: str) -> tuple[str, bool]:
    matches = re.findall(
        rf"(?m)^\s*\${re.escape(variable)} = @(['\"])\n", source,
    )
    if len(matches) != 1:
        raise RuntimeError(f"Expected one PowerShell template ${variable}; found {len(matches)}")
    quote = matches[0]
    body = _one(re.findall(
        rf"(?ms)^\s*\${re.escape(variable)} = @{quote}\n(.*?)^{quote}@",
        source,
    ), f"PowerShell body ${variable}")
    if not body.endswith("\n"):
        raise RuntimeError(f"Unterminated PowerShell template ${variable}")
    return body[:-1], quote == '"'


def _heredocs(source: str, delimiter: str) -> list[str]:
    """Extract only complete here-docs with the specified declared delimiter."""
    pattern = (
        rf"(?m)^[^\n]*<<\s*(?:'{re.escape(delimiter)}'|{re.escape(delimiter)})"
        rf"(?:[ \t]*(?:\|\| return 1)?)?[ \t]*\n"
    )
    bodies = []
    for match in re.finditer(pattern, source):
        end = re.search(rf"(?m)^{re.escape(delimiter)}$", source[match.end():])
        if end is None:
            raise RuntimeError(f"Unterminated Bash template {delimiter}")
        bodies.append(source[match.end():match.end() + end.start()])
    return bodies


def _sh(source: str, delimiter: str) -> str:
    return _one(_heredocs(source, delimiter), f"Bash body {delimiter}")


def _expand(body: str, values: dict[str, str], ps: bool = False) -> str:
    """Interpret only native here-string escapes and explicitly declared variables."""
    result = []
    i = 0
    escape = "`" if ps else "\\"
    while i < len(body):
        char = body[i]
        if char == escape and i + 1 < len(body):
            following = body[i + 1]
            escapes = {'n': "\n", 'r': "\r", 't': "\t"} if ps else {}
            if ps or following in "$`\\\n":
                result.append(escapes.get(following, following) if following != "\n" else "")
                i += 2
                continue
        if char == "$":
            match = re.match(r"\$([A-Za-z_][A-Za-z_0-9]*)", body[i:])
            if match is None or match[1] not in values:
                raise RuntimeError(f"Unsupported native template substitution near {body[i:i + 40]!r}")
            result.append(values[match[1]])
            i += len(match[0])
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _render_ps(source: str, variable: str, values: dict[str, str] | None = None) -> str:
    body, expandable = _ps(source, variable)
    if expandable:
        return _expand(body, values or {}, ps=True)
    for old, new in (values or {}).items():
        if old not in body:
            raise RuntimeError(f"Missing declared PowerShell placeholder {old!r} in ${variable}")
        body = body.replace(old, new)
    return body


def _printf(source: str, values: tuple[str, ...]) -> str:
    fmt = _one(re.findall(
        r"(?m)^\s*printf '(#!/usr/bin/env bash(?:[^'\\]|\\.)*)'", source,
    ), "Bash command prefix printf")
    # These writers declare only literal text, newlines, and %q/%s.
    fmt = fmt.replace(r"\n", "\n")
    slots = re.findall(r"%[qs]", fmt)
    if len(slots) != len(values) or "\\" in fmt:
        raise RuntimeError("Unsupported Bash command prefix format")
    iterator = iter(values)
    return re.sub(r"%[qs]", lambda match: (
        _bash_quote(next(iterator)) if match[0] == "%q" else next(iterator)
    ), fmt)


def _bash_quote(value: str) -> str:
    """Bash printf %q for printable paths, including native Windows separators."""
    if not value:
        return "''"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Command binding paths must not contain control characters")
    special = " !\"#$&'()*;<>?[\\]^`{|}~"
    return "".join("\\" + char if char in special else char for char in value)


def _shell_paths(path: Path) -> tuple[str, ...]:
    values = [str(path), path.as_posix()]
    # Git Bash's cygpath -u canonicalization of a native drive-qualified path.
    if len(path.drive) == 2 and path.drive[1] == ":":
        values.append("/" + path.drive[0].lower() + path.as_posix()[2:])
    return tuple(dict.fromkeys(values))


def _shell_literal(value: str, style: str = "ssh") -> str:
    quote = "'\\''" if style == "ssh" else "'\"'\"'"
    return "'" + value.replace("'", quote) + "'"


def _ps_array(source: str, values: dict[str, str] | None = None) -> str:
    body = _one(re.findall(
        r'(?ms)^\s*\$stubContent = @\(\n(.*?)^\s*\) -join "`n"', source,
    ), "PowerShell Bash array")
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if line.endswith(","):
            line = line[:-1]
        if len(line) < 2 or line[0] not in "'\"" or line[-1] != line[0]:
            raise RuntimeError("Unsupported PowerShell Bash array element")
        lines.append(
            line[1:-1].replace("''", "'") if line[0] == "'"
            else _expand(line[1:-1], values or {}, ps=True)
        )
    return "\n".join(lines)


def _native(payload: Path, product: str, ext: str) -> str:
    return _read(payload / "scripts" / (("init" if product in _INIT else "install") + "." + ext))


def _engine(payload: Path, ext: str) -> str:
    local = payload / "scripts" / ("installer-engine." + ext)
    if local.exists() or local.is_symlink():
        return _read(local)
    # The source checkout's own canonical shared library, never a sibling plugin.
    canonical = payload.parent.parent / "libs" / "installer-engine" / local.name
    return _read(canonical)


def _simple(payload: Path, root: Path, product: str, module: str) -> dict[str, tuple[str, ...]]:
    sh_install = _native(payload, product, "sh")
    ps_install = _native(payload, product, "ps1")
    no_provision = product.replace("-", "_").upper() + "_NO_SELFPROVISION"
    # Admission metadata is checked against the actual calls, not inferred from
    # a matching executable substring in an operator's command.
    calls = re.findall(
        r"(?m)^[ \t]*write_simple_binstub \\\n((?:[^\n]*\\\n)*[^\n]*)", sh_install,
    )
    if not calls:
        raise RuntimeError(f"Missing declared Bash simple-writer call for {product}")
    expected = [
        product, module, "$INSTALL_DIR", "$LOCAL_BIN", "$INSTALL_DIR/bin",
        "scripts/install.sh", no_provision,
    ]
    for call in calls:
        try:
            arguments = shlex.split(call.replace("\\\n", ""))
        except ValueError as exc:
            raise RuntimeError(f"Malformed Bash simple-writer call for {product}") from exc
        if arguments[:7] != expected:
            raise RuntimeError(f"Unsupported Bash simple-writer arguments for {product}")
    calls = re.findall(
        r"(?m)^[ \t]*Write-SimpleBinstub `\n((?:[^\n]*`\n)*[^\n]*)", ps_install,
    )
    if not calls:
        raise RuntimeError(f"Missing declared PowerShell simple-writer call for {product}")
    expected_ps = {
        "CommandName": "'" + product + "'", "ModuleName": "'" + module + "'",
        "RuntimeRoot": "$InstallDir", "LocalBin": "$LocalBin",
        "InstallBinDir": "(Join-Path $InstallDir 'bin')",
        "SnapshotInstallerPath": r"'scripts\install.ps1'",
        "NoSelfProvisionEnv": "'" + no_provision + "'",
    }
    for call in calls:
        for parameter, value in expected_ps.items():
            if not re.search(
                rf"(?m)^[ \t]*-{parameter} {re.escape(value)}(?:[ \t]*`)?[ \t]*$",
                call,
            ):
                raise RuntimeError(f"Unsupported PowerShell simple-writer parameter {parameter} for {product}")
    sh = _function(_engine(payload, "sh"), "write_simple_binstub")
    ps = _function(_engine(payload, "ps1"), "Write-SimpleBinstub", True)
    branch = ("agent-vault", "agent-ssh", "agent-pull-requests").index(product)
    cold_sh = _heredocs(sh, "DISPATCH")
    cold_ps = re.findall(r"(?ms)^\s*\$coldDispatcher = @'\n(.*?)^'@", ps)
    if len(cold_sh) != 3 or len(cold_ps) != 3:
        raise RuntimeError("Expected three declared simple-writer cold dispatchers")
    shell_bodies = tuple(_expand(_sh(sh, "EOF"), {
        "command_name": product, "module_name": module,
        "runtime_root_literal": _bash_quote(path),
        "snapshot_installer_literal": "scripts/install.sh",
        "no_self_provision_env": no_provision,
        "cold_dispatch": cold_sh[branch].removesuffix("\n"),
    }) for path in _shell_paths(root))
    values = {
        "runtimeRootLiteral": str(root).replace("'", "''"),
        "snapshotInstallerLiteral": r"scripts\install.ps1",
        "moduleLiteral": module, "commandLiteral": product,
        "NoSelfProvisionEnv": no_provision, "CommandName": product,
        "coldDispatcher": cold_ps[branch].removesuffix("\n"),
    }
    result = {
        product: shell_bodies,
        product + ".ps1": (_render_ps(ps, "ps1Content", values),),
        product + ".cmd": (_render_ps(ps, "cmdContent", values),),
    }
    if product == "agent-ssh":
        writer = _function(ps_install, "Write-Binstubs", True)
        result[product] += tuple(_ps_array(writer, {
            "installDirShellLiteral": _shell_literal(path),
        }) for path in _shell_paths(root))
    return result


def _templates(payload: Path, root: Path, product: str, *,
               publisher_root: Path | None = None) -> dict[str, tuple[str, ...]]:
    module = product.replace("-", "_")
    if product in _SIMPLE:
        return _simple(payload, root, product, module)
    if product == "agent-worktrees":
        # Both native installers copy these exact payload-owned files.
        return {name: (_read(payload / "bin" / name),) for name in (
            product, product + ".ps1", product + ".cmd",
            "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
        )} | {
            "agent-worktrees-update-json.awk": (
                _read(payload / "scripts" / "installation-context" / "json-query.awk"),
            ),
        }
    sh = _native(payload, product, "sh")
    ps = _native(payload, product, "ps1")
    sh_name = "deploy_binstub"
    ps_name = "Deploy-SelfProvisioningBinstub"
    if product == "agent-bridge":
        ps_name = "Write-Binstubs"
    sh = _function(sh, sh_name)
    ps = _function(ps, ps_name, True)
    root_ps = str(root).replace("'", "''")
    publisher = publisher_root or payload
    payload_ps = str(publisher).replace("'", "''")
    if product == "agent-bridge":
        bash = tuple(_printf(sh, (r, p, qualified)) + _sh(sh, "STUB")
                     for r in _shell_paths(root) for p in _shell_paths(publisher)
                     for qualified in ("", "1"))
        power = tuple(_render_ps(ps, "ps1", {
            "__ROOT__": root_ps, "__SOURCE__": payload_ps,
            "__QUALIFIED_SOURCE__": qualified,
        }) for qualified in ("$false", "$true"))
        cmd = _render_ps(ps, "cmd", {
            "__PS1__": str(Path.home() / ".local" / "bin" / (product + ".ps1")),
        })
    elif product == "agent-dispatch":
        bash = tuple(_printf(sh, (p, r + "/bin/runtime-gate.sh")) + _sh(sh, "STUBEOF")
                     for r in _shell_paths(root) for p in _shell_paths(publisher))
        power = (_render_ps(ps, "ps1Content", {
            "@INSTALL_DIR@": root_ps, "@PAYLOAD_ROOT@": payload_ps,
        }),)
        cmd = _render_ps(ps, "stubContent")
    elif product == "agent-codespaces":
        bash = tuple(_printf(sh, (_bash_quote(r),)) + _sh(sh, "STUB")
                     for r in _shell_paths(root))
        power = (_render_ps(ps, "ps1Content", {"__RUNTIME_ROOT__": root_ps}),)
        cmd = _render_ps(ps, "stubContent")
    elif product == "agent-containers":
        bash = tuple(_printf(sh, (r, p, "", mode)) + _sh(sh, "STUBEOF")
                     for r in _shell_paths(root) for p in _shell_paths(publisher)
                     for mode in ("legacy", "unknown"))
        bash += tuple(_render_ps(ps, "stubContent", {
            "__ROOT__": _shell_literal(r, "containers"),
            "__SOURCE__": _shell_literal(p, "containers"),
            "__CONTEXT__": "''", "__MODE__": mode,
        }) for r in _shell_paths(root) for p in _shell_paths(publisher)
            for mode in ("legacy", "unknown"))
        power = tuple(_render_ps(ps, "ps1Content", {
            "__ROOT__": root_ps, "__SOURCE__": payload_ps,
            "__CONTEXT__": "", "__MODE__": mode,
        }) for mode in ("legacy", "unknown"))
        cmd = _render_ps(ps, "cmdContent")
    else:
        bash = (_sh(sh, "STUBEOF"),)
        if product == "budget-guidance":
            bash += (_ps_array(ps),)
        else:
            bash += (_render_ps(ps, "stubContent"),)
        if product in _STANDALONE_CMD:
            return {product: bash, product + ".cmd": (_render_ps(ps, "cmdContent"),)}
        power = (_render_ps(ps, "ps1Content"),)
        cmd = _render_ps(ps, "cmdContent")
    result = {product: bash, product + ".ps1": power, product + ".cmd": (cmd,)}
    if product == "agent-dispatch":
        board = product + "-board"
        result[board] = tuple(body.replace(
            "AGENT_DISPATCH_COMMAND_MODULE=agent_dispatch\n",
            "AGENT_DISPATCH_COMMAND_MODULE=agent_dispatch.board_cli\n",
        ) for body in bash)
        result[board + ".ps1"] = tuple(body.replace(
            "AGENT_DISPATCH_COMMAND_MODULE = 'agent_dispatch'",
            "AGENT_DISPATCH_COMMAND_MODULE = 'agent_dispatch.board_cli'",
        ) for body in power)
        result[board + ".cmd"] = (cmd.replace(
            "%~dp0agent-dispatch.ps1", "%~dp0agent-dispatch-board.ps1",
        ),)
    if product == "agent-logger":
        install_sh = _native(payload, product, "sh")
        install_ps = _native(payload, product, "ps1")
        expected_names = "@(" + ", ".join(
            "'" + name + "'" for name in (_AUXILIARY[0], product, *_AUXILIARY[1:])
        ) + ")"
        if "$BinstubNames = " + expected_names not in install_ps:
            raise RuntimeError("Logger auxiliary command declaration changed")
        auxiliary_sh = _function(install_sh, "deploy_auxiliary_compatibility_binstubs")
        auxiliary_ps = _function(install_ps, "Deploy-AuxiliaryCompatibilityBinstubs", True)
        for name in _AUXILIARY:
            result[name] = (_sh(auxiliary_sh, "STUBEOF"),)
            result[name + ".ps1"] = (_render_ps(auxiliary_ps, "ps1Content", {"__COMMAND__": name}),)
            result[name + ".cmd"] = (_render_ps(auxiliary_ps, "cmdContent", {"__COMMAND__": name}),)
    return result


def _text(value: str) -> str:
    # No whitespace trimming, case folding, comment stripping, or program
    # normalization: only native newline encodings and a single UTF-8 BOM.
    return value.removeprefix("\ufeff").replace("\r\n", "\n")


def owned_local_names(
    payload: Path, root: Path, module: str, contents: dict[str, str],
    *, publisher_root: Path | None = None,
) -> tuple[str, ...]:
    """Return only exact current legacy command names owned by this installation.

    Empty candidate maps require only module/path validation, with no source I/O.
    Missing/malformed trusted sources raise when candidate commands exist.
    A custom-root uninstall never owns fixed-default-root wrappers. Writers
    that actually bind custom roots (Simple, Codespaces, Containers) are rendered
    against that precise root. Publisher-bearing forms also bind this payload
    or the installed deployment's recorded artifact path. That path supplies
    literal template data only, never executable code or project-command ownership.
    The caller remains responsible for physical-file and installation custody.
    """
    product = module.replace("_", "-")
    if product not in _PRODUCTS or product.replace("-", "_") != module:
        raise ValueError(f"Unsupported command ownership module: {module!r}")
    if not payload.is_absolute() or not root.is_absolute():
        raise ValueError("Command ownership requires absolute payload and installation roots")
    if ".." in payload.parts or ".." in root.parts:
        raise ValueError("Command ownership roots must not contain parent traversal")
    if not contents:
        return ()
    try:
        manifest = json.loads(_read(payload / "plugin.json"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Malformed trusted command payload manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("name") != product:
        raise ValueError(f"Command payload manifest does not identify {product}")
    templates = _templates(payload, root, product)
    if publisher_root is not None and product in {
        "agent-bridge", "agent-dispatch", "agent-containers",
    }:
        if (not publisher_root.is_absolute() or ".." in publisher_root.parts
                or any(char in str(publisher_root) for char in "\r\n\0")):
            raise ValueError("Installed command publisher must be an absolute non-traversing path")
        installed = _templates(payload, root, product, publisher_root=publisher_root)
        templates = {name: bodies + installed.get(name, ()) for name, bodies in templates.items()}
    if product in _DEFAULT_ONLY and root != Path.home() / ("." + product):
        return ()
    selected = {
        name for name, text in contents.items()
        if _text(text) in tuple(_text(body) for body in templates.get(name, ()))
    }
    selected = {
        name for name in selected
        if not name.endswith(".cmd") or name[:-4] + ".ps1" in selected
        or (product in _STANDALONE_CMD and name == product + ".cmd")
    }
    if product == "agent-vault":
        askpass = _sh(_function(_native(payload, product, "sh"), "_write_askpass"), "EOF")
        if product in selected and _text(contents.get("vault-askpass", "")) == _text(askpass):
            selected.add("vault-askpass")
    return tuple(name for name in contents if name in selected)

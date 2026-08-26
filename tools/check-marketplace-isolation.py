#!/usr/bin/env python3
"""Inventory operative surfaces that still assume one global plugin install.

The marketplace-installation-cells migration must qualify runtime roots,
commands, sibling launches, and lifecycle identities by marketplace provenance.
This guard inventories the legacy assumptions without blocking CI:

    python tools/check-marketplace-isolation.py
    python tools/check-marketplace-isolation.py --json
    python tools/check-marketplace-isolation.py --strict

An intentional compatibility seam may use an inline
``marketplace-isolation: allow <reason>`` marker. The reason is required.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ALLOW = "marketplace-isolation: allow"
_ALLOW_REASON = re.compile(r"marketplace-isolation:\s*allow\s+\S+", re.IGNORECASE)

_CODE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".cjs",
    ".js",
    ".json",
    ".mjs",
    ".ps1",
    ".psd1",
    ".psm1",
    ".py",
    ".service",
    ".sh",
    ".yaml",
    ".yml",
}
_SKIP_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "docs",
    "examples",
    "fixtures",
    "node_modules",
    "snapshots",
    "tests",
    "venv",
}

_UNQUALIFIED_ROOT = re.compile(
    r"""(?:["']|[\\/])\.agent-[a-z0-9-]+(?:["']|[\\/])""",
    re.IGNORECASE,
)
_GLOBAL_BIN = re.compile(r"""\.local[\\/]bin""", re.IGNORECASE)
_PROCESS_LAUNCH = re.compile(
    r"""subprocess\.(?:run|call|check_call|check_output|Popen)"""
    r"""|create_subprocess_(?:exec|shell)|Start-Process|exec\s+agent-"""
    r"""|&\s*["']?agent-|["']command["']\s*:"""
    r"""|(?:exec|execFile|spawn)(?:Sync)?\s*\(""",
    re.IGNORECASE,
)
_FIXED_UNIT = re.compile(
    r"""agent-[a-z0-9-]+\.service|\\\\\.\\pipe\\[a-z0-9_.-]+"""
    r"""|(?<![\w"'`])\$?[a-z0-9_]*"""
    r"""(?:service|task|unit|mutex|pipe|socket|lease|endpoint)[a-z0-9_]*"""
    r"""\s*=\s*["'][^"']+["']""",
    re.IGNORECASE,
)
_FIXED_ENDPOINT = re.compile(
    r"""(?:127\.0\.0\.1|localhost):\d{2,5}|["'][^"']*agent-[^"']*\.sock["']""",
    re.IGNORECASE,
)
_CELL_QUALIFIER = re.compile(
    r"""marketplace(?:_id)?|installation(?:_id)?|cell(?:_id)?""",
    re.IGNORECASE,
)
_JS_FUNCTION = re.compile(
    r"""(?:export\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"""
    r"""|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"""
    r"""(?:async\s*)?\([^)]*\)\s*=>\s*\{"""
)
_JS_PROCESS_API = re.compile(
    r"""\b(?:exec|execFile|spawn)(?:Sync)?\s*\(""",
    re.IGNORECASE,
)
@dataclass(frozen=True)
class Finding:
    category: str
    path: str
    line: int
    text: str
    reason: str


@dataclass(frozen=True)
class CommandPatterns:
    payload_commands: frozenset[str]
    token: str
    command: re.Pattern[str]
    path_lookup: re.Pattern[str]
    process_launch: re.Pattern[str]
    inline: re.Pattern[str]
    fence: re.Pattern[str]
    multiline_inline: re.Pattern[str]


def _payload_commands(root: Path) -> set[str]:
    commands: set[str] = set()
    for manifest in (root / "plugins").glob("*/payload-invocation.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        raw_commands = data.get("commands")
        if isinstance(raw_commands, list):
            for command in raw_commands:
                if isinstance(command, dict) and isinstance(command.get("command"), str):
                    commands.add(command["command"])
        elif isinstance(data.get("command"), str):
            commands.add(data["command"])
    return commands


def _command_patterns(root: Path) -> CommandPatterns:
    all_payload_commands = _payload_commands(root)
    payload_commands = sorted(
        command
        for command in all_payload_commands
        if not command.startswith("agent-")
    )
    alternatives = [r"agent-[a-z0-9-]+", *(re.escape(command) for command in payload_commands)]
    token = "(?:" + "|".join(alternatives) + ")"
    plain_payload = (
        "|^\\s*(?-i:(?:"
        + "|".join(re.escape(command) for command in payload_commands)
        + "))\\s+\\S+"
        if payload_commands
        else ""
    )
    return CommandPatterns(
        payload_commands=frozenset(all_payload_commands),
        token=token,
        command=re.compile(
            rf"""(?<![\w.-]){token}(?=\s|["'`]|$)""",
            re.IGNORECASE,
        ),
        path_lookup=re.compile(
            rf"""(?:command\s+-v|which|Get-Command)\s+["']?{token}"""
            rf"""|shutil\.which\(\s*["']{token}""",
            re.IGNORECASE,
        ),
        process_launch=re.compile(
            rf"""\bexec\s+{token}|&\s*["']?{token}""",
            re.IGNORECASE,
        ),
        inline=re.compile(
            (
                rf"""`{token}\s+[^`]+`"""
                rf"""|^\s*[-*]\s+{token}\s+\S+"""
                r"""|^\s*agent-[a-z0-9-]+\s+\S+"""
                + plain_payload
            ),
            re.IGNORECASE,
        ),
        fence=re.compile(
            rf"""^\s*(?:(?:\$|PS>|>)\s+)?(?:exec\s+)?{token}\s+\S+""",
            re.IGNORECASE,
        ),
        multiline_inline=re.compile(
            rf"""(?<!`)`(?!`)\s*{token}[ \t]+[^`\n]+\n[^`]*(?<!`)`(?!`)""",
            re.IGNORECASE,
        ),
    )


def _iter_targets(root: Path) -> list[Path]:
    plugins = root / "plugins"
    if not plugins.is_dir():
        return []

    targets: set[Path] = set()
    for plugin in sorted(path for path in plugins.iterdir() if path.is_dir()):
        for path in plugin.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(plugin).parts
            if any(part in _SKIP_PARTS for part in rel_parts):
                continue
            if path.suffix.lower() == ".md":
                if "skills" in rel_parts or "agents" in rel_parts:
                    targets.add(path)
                continue
            if path.suffix.lower() in _CODE_SUFFIXES or not path.suffix:
                targets.add(path)

    return sorted(targets)


def _strip_ps_block_comments(line: str, in_block: bool) -> tuple[str, bool]:
    out: list[str] = []
    index = 0
    while index < len(line):
        if in_block:
            end = line.find("#>", index)
            if end == -1:
                return "".join(out), True
            in_block = False
            index = end + 2
            continue
        start = line.find("<#", index)
        if start == -1:
            out.append(line[index:])
            return "".join(out), False
        out.append(line[index:start])
        in_block = True
        index = start + 2
    return "".join(out), in_block


def _strip_c_block_comments(line: str, in_block: bool) -> tuple[str, bool]:
    """Remove JavaScript ``/* ... */`` comment spans from one line."""
    out: list[str] = []
    index = 0
    while index < len(line):
        if in_block:
            end = line.find("*/", index)
            if end == -1:
                return "".join(out), True
            in_block = False
            index = end + 2
            continue
        start = line.find("/*", index)
        if start == -1:
            out.append(line[index:])
            return "".join(out), False
        out.append(line[index:start])
        in_block = True
        index = start + 2
    return "".join(out), in_block


def _strip_inline_comment(
    line: str, marker: str, *, backslash_escapes_single: bool = False
) -> str:
    """Strip an inline comment marker outside single or double quotes."""
    quote = ""
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and (
            quote == '"' or (quote == "'" and backslash_escapes_single)
        ):
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if line.startswith(marker, index) and (
            index == 0 or line[index - 1].isspace()
        ):
            return line[:index]
        index += 1
    return line


def _python_code_lines(text: str) -> list[str]:
    """Return Python source lines with comments and docstrings blanked."""
    lines = text.splitlines()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            row, column = token.start
            lines[row - 1] = lines[row - 1][:column]
    except (IndentationError, tokenize.TokenError):
        pass

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return lines

    containers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for container in containers:
        body = getattr(container, "body", None)
        if not body:
            continue
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        end = getattr(first, "end_lineno", first.lineno)
        for row in range(first.lineno, end + 1):
            lines[row - 1] = ""
    return lines


def _python_launch_lines(text: str, payload_commands: frozenset[str]) -> set[int]:
    """Return lines where an agent command argv is built for a later launch."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()

    command_assignments: dict[str, int] = {}
    launched_names: set[str] = set()
    direct_launch_lines: set[int] = set()
    launch_methods = {
        "call",
        "check_call",
        "check_output",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "Popen",
        "run",
    }

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if (
                isinstance(value, (ast.List, ast.Tuple))
                and value.elts
                and isinstance(value.elts[0], ast.Constant)
                and isinstance(value.elts[0].value, str)
                and (
                    value.elts[0].value.startswith("agent-")
                    or value.elts[0].value in payload_commands
                )
            ):
                for target in targets:
                    if isinstance(target, ast.Name):
                        command_assignments[target.id] = node.lineno

        if not isinstance(node, ast.Call):
            continue
        func = node.func
        method = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name) else ""
        )
        if method not in launch_methods:
            continue
        for argument in node.args:
            if isinstance(argument, ast.Name):
                launched_names.add(argument.id)
                continue
            command_node: ast.AST | None = None
            if (
                isinstance(argument, (ast.List, ast.Tuple))
                and argument.elts
            ):
                command_node = argument.elts[0]
            elif isinstance(argument, ast.Constant):
                command_node = argument
            if (
                isinstance(command_node, ast.Constant)
                and isinstance(command_node.value, str)
                and (
                    command_node.value.startswith("agent-")
                    or command_node.value in payload_commands
                )
            ):
                direct_launch_lines.add(command_node.lineno)

    return direct_launch_lines | {
        line
        for name, line in command_assignments.items()
        if name in launched_names
    }


def _javascript_launch_lines(text: str, command_token: str) -> set[int]:
    """Return wrapper call lines whose command resolves through a process API."""
    lines = text.splitlines()
    wrappers: set[str] = set()

    for index, line in enumerate(lines):
        match = _JS_FUNCTION.search(line)
        if not match:
            continue
        name = match.group(1) or match.group(2)
        depth = line.count("{") - line.count("}")
        body = [line]
        cursor = index + 1
        while depth > 0 and cursor < len(lines):
            body.append(lines[cursor])
            depth += lines[cursor].count("{") - lines[cursor].count("}")
            cursor += 1
        if _JS_PROCESS_API.search("\n".join(body)):
            wrappers.add(name)

    if not wrappers:
        return set()
    call = re.compile(
        rf"""\b(?:{'|'.join(re.escape(name) for name in sorted(wrappers))})"""
        rf"""\s*\(\s*["']{command_token}["']""",
        re.IGNORECASE,
    )
    return {
        text.count("\n", 0, match.start()) + 1
        for match in call.finditer(text)
    }


def _is_markdown_command(
    path: Path,
    line: str,
    in_fence: bool,
    patterns: CommandPatterns,
) -> bool:
    if path.suffix.lower() != ".md":
        return False
    return (in_fence and bool(patterns.fence.search(line))) or bool(
        patterns.inline.search(line)
    )


def _line_findings(
    path: Path,
    relative: str,
    number: int,
    line: str,
    code: str,
    in_fence: bool,
    python_launch_lines: set[int],
    patterns: CommandPatterns,
) -> list[Finding]:
    if _ALLOW_REASON.search(line):
        return []

    snippet = line.strip()[:200]
    findings: list[Finding] = []

    def add(category: str, reason: str) -> None:
        if any(finding.category == category for finding in findings):
            return
        findings.append(Finding(category, relative, number, snippet, reason))

    if _UNQUALIFIED_ROOT.search(code):
        add("unqualified-runtime-root", "plugin-owned path is not cell-qualified")

    if _GLOBAL_BIN.search(code) and (
        patterns.command.search(code)
        or path.parent.name in {"bin", "scripts"}
        or "binstub" in code.lower()
    ):
        add("global-plugin-binstub", "global command directory may be shared by cells")

    if number in python_launch_lines or patterns.path_lookup.search(code) or (
        patterns.command.search(code)
        and (_PROCESS_LAUNCH.search(code) or patterns.process_launch.search(code))
    ):
        add("path-sibling-launch", "plugin command may resolve through ambient PATH")

    if (
        (_FIXED_UNIT.search(code) or _FIXED_ENDPOINT.search(code))
        and not _CELL_QUALIFIER.search(code)
    ):
        add("fixed-service-identity", "lifecycle or endpoint identity is not cell-qualified")

    if _is_markdown_command(path, line, in_fence, patterns):
        add("bare-agent-command", "operative instruction uses a bare global plugin command")

    if ALLOW in line.lower() and not _ALLOW_REASON.search(line):
        add("invalid-allow", "allow marker requires a reason")

    return findings


def _scan_file(
    path: Path,
    root: Path,
    patterns: CommandPatterns | None = None,
) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    is_ps = path.suffix.lower() in {".ps1", ".psd1", ".psm1"}
    is_md = path.suffix.lower() == ".md"
    is_python = path.suffix.lower() == ".py"
    is_javascript = path.suffix.lower() in {".cjs", ".js", ".mjs"}
    uses_hash_comments = path.suffix.lower() in {
        ".ps1",
        ".psd1",
        ".psm1",
        ".sh",
        ".yaml",
        ".yml",
    }
    patterns = patterns or _command_patterns(root)
    code_lines = _python_code_lines(text) if is_python else None
    launch_lines = (
        _python_launch_lines(text, patterns.payload_commands)
        if is_python
        else set()
    )
    if is_javascript:
        launch_lines = _javascript_launch_lines(text, patterns.token)
    in_ps_block = False
    in_js_block = False
    in_fence = False
    findings: list[Finding] = []
    relative = path.relative_to(root).as_posix()

    for number, line in enumerate(text.splitlines(), 1):
        if is_md and line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue

        code = code_lines[number - 1] if code_lines is not None else line
        if is_ps:
            code, in_ps_block = _strip_ps_block_comments(line, in_ps_block)
        if is_javascript:
            code, in_js_block = _strip_c_block_comments(code, in_js_block)
            code = _strip_inline_comment(
                code, "//", backslash_escapes_single=True
            )
        elif uses_hash_comments:
            code = _strip_inline_comment(code, "#")
        stripped = code.strip()
        if not stripped:
            continue
        if not is_md and (
            stripped.startswith("#")
            or stripped.startswith("//")
            or stripped.startswith(";")
        ):
            continue

        findings.extend(
            _line_findings(
                path,
                relative,
                number,
                line,
                code,
                in_fence,
                launch_lines,
                patterns,
            )
        )

    if is_md:
        for match in patterns.multiline_inline.finditer(text):
            snippet = " ".join(match.group(0).split())[:200]
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            span = text[start:] if end == -1 else text[start:end]
            if _ALLOW_REASON.search(span):
                continue
            findings.append(
                Finding(
                    "bare-agent-command",
                    relative,
                    text.count("\n", 0, match.start()) + 1,
                    snippet,
                    "operative multiline instruction uses a bare global plugin command",
                )
            )

    return findings


def scan(root: Path = REPO) -> tuple[int, list[Finding]]:
    targets = _iter_targets(root)
    patterns = _command_patterns(root)
    findings: list[Finding] = []
    for path in targets:
        findings.extend(_scan_file(path, root, patterns))
    return len(targets), findings


def _counts(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1
    return dict(sorted(counts.items()))


def _print_human(
    files_scanned: int, findings: list[Finding], *, verbose: bool
) -> None:
    if not findings:
        print(
            "check-marketplace-isolation: OK "
            f"({files_scanned} operative file(s), no legacy assumptions)."
        )
        return

    if verbose:
        for finding in findings:
            print(
                f"{finding.path}:{finding.line}: [{finding.category}] "
                f"{finding.text}"
            )
        print()
    print(
        "check-marketplace-isolation: "
        f"{len(findings)} report-only finding(s) in {files_scanned} file(s):"
    )
    for category, count in _counts(findings).items():
        print(f"  {category}: {count}")
    print(
        "These findings are the migration baseline for #1102. "
        "Use '--strict' only after the producing phases have landed."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero when findings exist")
    parser.add_argument("--json", action="store_true",
                        help="emit stable machine-readable JSON")
    parser.add_argument("--verbose", action="store_true",
                        help="print each finding before the category summary")
    parser.add_argument("--root", type=Path, default=REPO,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    files_scanned, findings = scan(root)
    if args.json:
        print(
            json.dumps(
                {
                    "guard": "marketplace-isolation",
                    "mode": "strict" if args.strict else "report",
                    "files_scanned": files_scanned,
                    "findings": [asdict(finding) for finding in findings],
                    "counts": _counts(findings),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_human(files_scanned, findings, verbose=args.verbose)
    return 1 if args.strict and findings else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None

"""Bounded, data-only parsing of release-controller YAML and Bash.

This is not a shell interpreter. Literal calls, literal base argument arrays,
and the controller's exact runpy trampoline are supported. Variables remain
opaque words; conditional array additions never establish required inputs.
Unsupported helper loaders and ambiguous array state raise ValueError.
"""
from __future__ import annotations

import ast
import io
import json
import re
import shlex
import tokenize

_NAME = r"[A-Za-z0-9_-]+\.py"
_REFERENCE = re.compile(r"reviewed-helpers/tools/(" + _NAME + r")\b")
_FILE = re.compile(r"(?:(?:.*?/)?reviewed-helpers/tools/|tools/)?(" + _NAME + r")$")
_RUN = re.compile(r"^( *)(?:- +)?run: *(.*)$")
_ARRAY = re.compile(r"^([A-Za-z_]\w*)(\+?)=\((.*)\)$", re.S)
_EXPANSION = re.compile(r"^\$\{([A-Za-z_]\w*)\[@\]\}$")
_HEREDOC = re.compile(r"<< *(['\"]?)([A-Za-z_]\w*)\1")
_WORD = re.compile(r"""(?:[^\s'"\\]|\\[\s\S]|'[^']*'|"(?:[^"\\]|\\[\s\S])*")+""")


def controller_scripts(text: str) -> list[str]:
    """Extract plain scalar and literal-block run values without loading YAML.

    Folded/flow run values, multiline plain/quoted scalars and explicit block
    indentation indicators are deliberately refused.
    """
    if not re.search(r"(?m)^(?:jobs|name|on|permissions|concurrency):", text):
        if re.search(r"(?m)^ *(?:- +)?run:", text) is None:
            return [text] if text.strip() else []
    lines = text.splitlines()
    scripts: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if "\t" in line[:len(line) - len(line.lstrip())]:
            raise ValueError("controller YAML uses tab indentation")
        match = _RUN.match(line)
        if not match:
            if re.search(r"""(?:^|[ {,])["']?run["']?\s*:""", line) and not line.lstrip().startswith("#"):
                raise ValueError("unsupported controller run key")
            # Do not mistake the content of another block scalar for YAML.
            block = re.match(r"^( *)(?:- +)?[^#:\n]+: *[|>][+-]? *(?:#.*)?$", line)
            if block:
                indent = len(block[1])
                index += 1
                while index < len(lines) and (
                    not lines[index].strip()
                    or len(lines[index]) - len(lines[index].lstrip()) > indent
                ):
                    index += 1
                continue
            index += 1
            continue
        indent, value = len(match[1]), match[2]
        index += 1
        end = index
        while end < len(lines) and (
            not lines[end].strip()
            or len(lines[end]) - len(lines[end].lstrip()) > indent
        ):
            end += 1
        if re.fullmatch(r"\|[+-]? *(?:#.*)?", value):
            content = lines[index:end]
            nonempty = [len(row) - len(row.lstrip()) for row in content if row.strip()]
            margin = nonempty[0] if nonempty else indent + 1
            if any(width < margin for width in nonempty):
                raise ValueError("ambiguous controller block indentation")
            scripts.append("\n".join(row[margin:] if row.strip() else "" for row in content) + "\n")
        else:
            if not value or value.startswith(("|", ">", "{", "[", "&", "*", "!")):
                raise ValueError("unsupported controller run scalar style")
            if any(row.strip() and not row.lstrip().startswith("#") for row in lines[index:end]):
                raise ValueError("unsupported multiline controller run scalar")
            if value.startswith("'"):
                if not re.fullmatch(r"'(?:[^']|'')*' *(?:#.*)?", value):
                    raise ValueError("unsupported quoted controller run scalar")
                value = re.match(r"'((?:[^']|'')*)'", value)[1].replace("''", "'")
            elif value.startswith('"'):
                try:
                    value, offset = json.JSONDecoder().raw_decode(value)
                except json.JSONDecodeError as exc:
                    raise ValueError("unsupported quoted controller run scalar") from exc
                if not isinstance(value, str) or match[2][offset:].strip():
                    raise ValueError("unsupported quoted controller run scalar")
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0]
                if ": " in value:
                    raise ValueError("ambiguous plain controller run scalar")
            scripts.append(value)
        index = end
    return scripts


def _mask(script: str) -> str:
    """Mask shell quotes/comments, retaining offsets for shell operators."""
    result = list(script)
    quote = ""
    index = 0
    while index < len(script):
        char = script[index]
        if char == "\\" and quote != "'":
            result[index] = " "
            if index + 1 < len(script):
                result[index + 1] = " "
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ""
            if char != "\n":
                result[index] = " "
        elif char in "'\"":
            quote = char
            result[index] = " "
        elif char == "#" and (index == 0 or script[index - 1].isspace() or script[index - 1] in ";|&"):
            end = script.find("\n", index)
            end = len(script) if end < 0 else end
            result[index:end] = " " * (end - index)
            index = end
            continue
        index += 1
    return "".join(result)


def _prepare(script: str) -> tuple[str, dict[str, str]]:
    """Remove heredoc data before looking for shell command boundaries."""
    lines = script.splitlines(keepends=True)
    output: list[str] = []
    bodies: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        mask = _mask(line)
        position = mask.find("<<")
        if position < 0:
            output.append(line)
            continue
        match = _HEREDOC.match(line, position)
        if match is None or "<<" in mask[position + 2:]:
            raise ValueError("unsupported shell heredoc")
        body: list[str] = []
        while index < len(lines) and lines[index].rstrip("\r\n") != match[2]:
            body.append(lines[index])
            index += 1
        if index == len(lines):
            raise ValueError("unterminated shell heredoc")
        index += 1
        marker = f"__CONTROLLER_HEREDOC_{len(bodies)}__"
        if marker in script:
            raise ValueError("reserved controller heredoc marker")
        bodies[marker] = "".join(body)
        header = line[:position] + " " + marker + line[match.end():]
        capture = re.match(r"^ *[A-Za-z_]\w*=\$\(", header)
        if capture:
            header = header[capture.end():]
            if index == len(lines) or lines[index].strip() != ")":
                raise ValueError("unsupported captured heredoc command")
            index += 1
        output.append(header)
    return "".join(output), bodies


def _commands(script: str) -> list[str]:
    script = script.replace("\\\n", "")
    mask = _mask(script)
    result: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(script):
        char = mask[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                # Case-pattern terminators are not command arguments.
                start = index + 1
                depth = 0
        elif not depth and char in "\n;|&":
            command = script[start:index].strip()
            if command:
                result.append(command)
            start = index + 1
        index += 1
    if depth:
        raise ValueError("unbalanced controller shell grouping")
    if script[start:].strip():
        result.append(script[start:].strip())
    return result


def _words(command: str) -> list[str]:
    try:
        words: list[str] = []
        index = 0
        while index < len(command):
            if command[index].isspace():
                index += 1
                continue
            if command[index] == "#":
                end = command.find("\n", index)
                index = len(command) if end < 0 else end
                continue
            match = _WORD.match(command, index)
            if match is None:
                raise ValueError("unclosed shell word")
            raw = match[0]
            parsed = shlex.split(raw, comments=False, posix=True)
            if len(parsed) != 1:
                raise ValueError("ambiguous shell word")
            word = parsed[0]
            if ("[@" in word or "[*" in word) and raw != f'"{word}"':
                raise ValueError("unsupported unquoted or literal array expansion")
            words.append(word)
            index = match.end()
        return words
    except ValueError as exc:
        raise ValueError("unsupported controller shell quoting") from exc


def helper_references(script: str) -> set[str]:
    """Find literal reviewed helper identities, including unsupported calls."""
    shell, bodies = _prepare(script)
    uncommented = "\n".join(" ".join(_words(command)) for command in _commands(shell))
    references = set(_REFERENCE.findall(uncommented))
    references.update(re.findall(r"""tools\s*/\s*["'](""" + _NAME + r""")["']""", uncommented))
    for body in bodies.values():
        try:
            tokens = tokenize.generate_tokens(io.StringIO(body).readline)
            clean = tokenize.untokenize(
                token for token in tokens if token.type != tokenize.COMMENT
            )
        except (tokenize.TokenError, IndentationError) as exc:
            raise ValueError("unsupported controller heredoc data") from exc
        references.update(_REFERENCE.findall(clean))
        references.update(re.findall(r"""tools\s*/\s*["'](""" + _NAME + r""")["']""", clean))
    return references


def _runpy_helper(body: str) -> str | None:
    if "runpy" not in body:
        return None
    try:
        tree = ast.parse(body)
    except SyntaxError as exc:
        raise ValueError("unsupported indirect helper loader") from exc
    names = [
        node.right.value for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        and isinstance(node.left, ast.Name) and node.left.id == "tools"
        and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
        and re.fullmatch(_NAME, node.right.value)
    ]
    if len(names) != 1:
        raise ValueError("unsupported indirect helper loader")
    expected = ast.parse(
        "import runpy\nimport sys\nfrom pathlib import Path\n"
        "tools = Path(sys.argv.pop(1)).resolve(strict=True)\n"
        "sys.path.insert(0, str(tools))\n"
        f'runpy.run_path(str(tools / "{names[0]}"), run_name="__main__")\n'
    )
    if ast.dump(tree) != ast.dump(expected):
        raise ValueError("unsupported indirect helper loader")
    return names[0]


def _expand(words: list[str], arrays: dict[str, list[str] | None]) -> list[str]:
    expanded: list[str] = []
    for word in words:
        match = _EXPANSION.fullmatch(word)
        if match:
            if arrays.get(match[1]) is None:
                raise ValueError(f"unknown or ambiguous controller array: {match[1]}")
            expanded.extend(arrays[match[1]])
        elif "[@" in word or "[*" in word or "$(" in word or "`" in word:
            raise ValueError("unsupported dynamic controller argument")
        elif word.startswith("$") and (not expanded or not expanded[-1].startswith("--")):
            raise ValueError("unsupported dynamic controller argument position")
        else:
            expanded.append(word)
    return expanded


def script_invocations(script: str) -> list[tuple[str, list[str]]]:
    """Return each supported Python helper's own argv, never adjacent words."""
    shell, bodies = _prepare(script)
    arrays: dict[str, list[str] | None] = {}
    helper_variables: set[str] = set()
    invocations: list[tuple[str, list[str]]] = []
    conditional = 0
    for command in _commands(shell):
        words = _words(command)
        if not words:
            continue
        if words[0] in ("fi", "done", "esac"):
            conditional = max(0, conditional - 1)
        if words[0] in ("if", "for", "while", "until", "case", "select"):
            conditional += 1
        command = re.sub(r"^(?:then|else|do|if|elif)\s+", "", command)
        words = _words(command)
        if ("$(" in command or "`" in command) and (
            _REFERENCE.search(command) or "runpy" in command
            or any(
                spelling in command for variable in helper_variables
                for spelling in ("$" + variable, "${" + variable + "}")
            )
        ):
            raise ValueError("unsupported helper command substitution")
        array = _ARRAY.fullmatch(command)
        if array:
            name, append, content = array.groups()
            if append:
                if arrays.get(name) is None or not conditional:
                    raise ValueError(f"unsupported controller array mutation: {name}")
                # A conditional mode is not an unconditional required input.
                _expand(_words(content), arrays)
            elif conditional or name in arrays:
                raise ValueError(f"conditional or reassigned controller array: {name}")
            else:
                arrays[name] = _expand(_words(content), arrays)
            continue
        assignment = re.match(r"^([A-Za-z_]\w*)=", command)
        if assignment:
            arrays[assignment[1]] = None
            if _REFERENCE.search(command):
                helper_variables.add(assignment[1])
        mutation = re.match(r"^([A-Za-z_]\w*)(?:\[[^]]*\]|\+)=", command)
        if mutation:
            if arrays.get(mutation[1]) is not None:
                raise ValueError("unsupported controller array mutation")
            arrays[mutation[1]] = None
        if (
            words[0] in ("unset", "read", "mapfile", "declare", "local", "export")
            and any(re.match(r"^" + re.escape(name) + r"(?:$|[=(])", word)
                    for name, value in arrays.items() if value is not None for word in words[1:])
        ):
            raise ValueError("unsupported controller array mutation")
        interpreter = words[0]
        if not re.fullmatch(r"(?:python(?:3(?:\.\d+)?)?|(?:/|\$)[^\s]*?/python(?:3(?:\.\d+)?)?)", interpreter):
            if not assignment and words[0] not in ("echo", "printf", "[", "[[") and _REFERENCE.search(command):
                raise ValueError("unsupported helper command")
            continue
        offset = 1
        while offset < len(words) and words[offset] in ("-I", "-B", "-u"):
            offset += 1
        if offset == len(words):
            continue
        file = words[offset]
        marker = next((word for word in words if word in bodies), None)
        if file == "-" and marker:
            helper = _runpy_helper(bodies[marker])
            if helper is None:
                if _REFERENCE.search(bodies[marker]) or re.search(
                    r"""tools\s*/\s*["']""" + _NAME, bodies[marker],
                ):
                    raise ValueError("unsupported indirect helper loader")
                continue
            tail = words[offset + 1:]
            if len(tail) < 2 or tail[0] != "$GITHUB_WORKSPACE/reviewed-helpers/tools" or tail[-1] != marker:
                raise ValueError("unsupported indirect helper arguments")
            invocations.append((helper, _expand(tail[1:-1], arrays)))
        elif (match := _FILE.fullmatch(file)) is not None:
            if marker or any(word in ("<", ">", ">>", "<<") for word in words[offset + 1:]):
                raise ValueError("unsupported helper redirection")
            invocations.append((match[1], _expand(words[offset + 1:], arrays)))
        elif _REFERENCE.search(command):
            raise ValueError("unsupported Python helper invocation")
        elif "runpy" in command:
            raise ValueError("unsupported indirect helper loader")
        elif file in {
            spelling for variable in helper_variables
            for spelling in ("$" + variable, "${" + variable + "}")
        }:
            raise ValueError("unsupported indirect helper file")
    return invocations

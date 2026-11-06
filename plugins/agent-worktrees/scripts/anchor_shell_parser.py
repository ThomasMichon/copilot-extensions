"""Shell segmentation helpers for :mod:`anchor_write_guard`."""

from __future__ import annotations

import re

GIT_INTRINSIC_NONWRITE_COMMANDS = frozenset({
    "blame", "cat-file", "describe", "diff", "diff-files", "diff-index",
    "diff-tree", "fetch", "grep", "help", "log", "ls-files", "ls-remote",
    "ls-tree", "merge-base", "name-rev", "rev-list", "rev-parse", "show",
    "show-ref", "status", "version", "whatchanged",
})

_GIT_BRANCH_SAFE_LONG_FLAG = re.compile(
    r"""^(?:
        --list|--all|--remotes|--verbose|--show-current|
        --column(?:=\S+)?|--no-column|--ignore-case|--omit-empty|
        --no-abbrev|--no-color|--color(?:=\S+)?|--sort=\S+|--format=\S+|
        --abbrev=\S+|--points-at=\S+|--contains=\S+|--no-contains=\S+|
        --merged(?:=\S+)?|--no-merged(?:=\S+)?
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
_GIT_BRANCH_SAFE_SHORT_CLUSTER = re.compile(r"^-[varil]+$", re.IGNORECASE)


def git_branch_invocation_is_readonly(args: list[str]) -> bool:
    """Whether every branch argument is a known read-only flag."""
    return all(
        not token
        or _GIT_BRANCH_SAFE_LONG_FLAG.match(token)
        or _GIT_BRANCH_SAFE_SHORT_CLUSTER.match(token)
        for token in args
    )


def git_tokens(seg: str, tool: str, *, split_redirects: bool = False) -> list[str]:
    """Tokenize literal arguments with the selected shell's quoting rules."""
    posix_escapes = tool.lower() in {"bash", "sh"}
    powershell_escapes = tool.lower() in {"powershell", "pwsh"}
    tokens: list[str] = []
    current: list[str] = []
    token_started = False
    quote = None
    index = 0
    while index < len(seg):
        char = seg[index]
        if quote:
            if (
                powershell_escapes
                and quote == "'"
                and char == "'"
                and index + 1 < len(seg)
                and seg[index + 1] == "'"
            ):
                current.append("'")
                index += 1
            elif char == quote:
                quote = None
            elif (
                posix_escapes
                and char == "\\"
                and quote == '"'
                and index + 1 < len(seg)
                and seg[index + 1] in {'"', "\\", "$", "`"}
            ):
                current.append(seg[index + 1])
                index += 1
            elif (
                powershell_escapes
                and quote == '"'
                and char == "`"
                and index + 1 < len(seg)
            ):
                current.append(seg[index + 1])
                index += 1
            else:
                current.append(char)
        elif char in {"'", '"'}:
            token_started = True
            quote = char
        elif char.isspace():
            if token_started:
                tokens.append("".join(current))
                current = []
                token_started = False
        elif split_redirects and char == ">":
            if token_started:
                tokens.append("".join(current))
                current = []
                token_started = False
            if index + 1 < len(seg) and seg[index + 1] == ">":
                tokens.append(">>")
                index += 1
            else:
                tokens.append(">")
        elif (
            posix_escapes
            and char == "\\"
            and index + 1 < len(seg)
            and (
                seg[index + 1].isspace() or seg[index + 1] in {"'", '"', "\\"}
                or split_redirects and seg[index + 1] in {">", "<", "|", "&"}
            )
        ):
            current.append(seg[index + 1])
            token_started = True
            index += 1
        elif powershell_escapes and char == "`" and index + 1 < len(seg):
            current.append(seg[index + 1])
            token_started = True
            index += 1
        else:
            current.append(char)
            token_started = True
        index += 1
    if token_started:
        tokens.append("".join(current))
    return tokens


def literal_write_targets(seg: str, tool: str) -> list[str]:
    """Extract literal redirect operands and target-bearing shell arguments."""
    tokens = git_tokens(seg, tool, split_redirects=True)
    targets = []
    for index, token in enumerate(tokens):
        if token in {">", ">>"}:
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("&"):
                targets.append(tokens[index + 1])
            continue
        if token.lower().startswith("of="):
            token = token[3:]
        elif tool.lower() in {"powershell", "pwsh"}:
            token = re.sub(
                r"^-(?:literalpath|path|filepath|destination):", "", token,
                flags=re.IGNORECASE,
            )
        targets.append(token)
    return targets


def shell_segments(cmd: str, tool: str) -> list[str]:
    lower_tool = tool.lower()
    posix_escapes = lower_tool in {"bash", "sh"}
    powershell_escapes = lower_tool in {"powershell", "pwsh"}
    cmd_escapes = lower_tool == "cmd"
    quote_chars = {'"'} if cmd_escapes else {"'", '"'}
    segments: list[str] = []
    current: list[str] = []
    quote = None
    substitution_depth = 0
    backtick_substitution = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(cmd):
        char = cmd[index]
        if block_comment:
            if cmd.startswith("#>", index):
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if line_comment:
            if char in {"\r", "\n"}:
                line_comment = False
                if substitution_depth or backtick_substitution:
                    current.append(char)
                else:
                    segment = "".join(current)
                    if segment.strip():
                        segments.append(segment)
                    current = []
            index += 1
            continue
        continuation_escape = (
            (posix_escapes and char == "\\")
            or (powershell_escapes and char == "`")
            or (cmd_escapes and char == "^")
        )
        if (
            quote != "'"
            and continuation_escape
            and index + 1 < len(cmd)
            and cmd[index + 1] in {"\r", "\n"}
        ):
            index += 2
            if (
                index < len(cmd)
                and cmd[index - 1] == "\r"
                and cmd[index] == "\n"
            ):
                index += 1
            continue
        if quote:
            current.append(char)
            if (
                powershell_escapes
                and quote == "'"
                and char == "'"
                and index + 1 < len(cmd)
                and cmd[index + 1] == "'"
            ):
                current.append(cmd[index + 1])
                index += 1
            elif (
                posix_escapes
                and quote == '"'
                and char == "\\"
                and index + 1 < len(cmd)
            ):
                current.append(cmd[index + 1])
                index += 1
            elif (
                powershell_escapes
                and quote == '"'
                and char == "`"
                and index + 1 < len(cmd)
            ):
                current.append(cmd[index + 1])
                index += 1
            elif char == quote:
                quote = None
        elif powershell_escapes and cmd.startswith("<#", index):
            block_comment = True
            index += 1
        elif (
            char == "#"
            and (
                powershell_escapes or posix_escapes
            )
            and (
                index == 0
                or cmd[index - 1].isspace()
                or cmd[index - 1] in {";", "|", "&", "(", ")", "{", "}"}
            )
        ):
            line_comment = True
        elif (
            (
                lower_tool in {"bash", "sh", "powershell", "pwsh"}
                and cmd.startswith("$(", index)
            )
            or (
                powershell_escapes
                and cmd.startswith("@(", index)
            )
            or (
                posix_escapes
                and (
                    cmd.startswith("<(", index)
                    or cmd.startswith(">(", index)
                )
            )
        ):
            substitution_depth += 1
            current.extend((char, "("))
            index += 1
        elif substitution_depth and char == "(":
            substitution_depth += 1
            current.append(char)
        elif powershell_escapes and char == "(":
            substitution_depth += 1
            current.append(char)
        elif substitution_depth and char == ")":
            substitution_depth -= 1
            current.append(char)
        elif posix_escapes and char == "`":
            backtick_substitution = not backtick_substitution
            current.append(char)
        elif char in quote_chars:
            quote = char
            current.append(char)
        elif (
            (posix_escapes and char == "\\")
            or (powershell_escapes and char == "`")
            or (cmd_escapes and char == "^")
        ) and index + 1 < len(cmd):
            current.extend((char, cmd[index + 1]))
            index += 1
        elif (
            not substitution_depth
            and not backtick_substitution
            and char in {";", "|", "&", "\n", "\r"}
        ):
            segment = "".join(current)
            if segment.strip():
                segments.append(segment)
            current = []
            if (
                index + 1 < len(cmd)
                and cmd[index + 1] == char
                and char in {"|", "&"}
            ):
                index += 1
        else:
            current.append(char)
        index += 1
    segment = "".join(current)
    if segment.strip():
        segments.append(segment)
    return segments


def shell_command_substitutions(seg: str, tool: str) -> list[str]:
    lower_tool = tool.lower()
    if lower_tool not in {"bash", "sh", "powershell", "pwsh"}:
        return []
    escape = "\\" if lower_tool in {"bash", "sh"} else "`"
    substitutions: list[str] = []
    quote = None
    index = 0
    while index < len(seg):
        char = seg[index]
        if char == escape and quote != "'" and index + 1 < len(seg):
            index += 2
            continue
        if (
            lower_tool in {"powershell", "pwsh"}
            and quote == "'"
            and char == "'"
            and index + 1 < len(seg)
            and seg[index + 1] == "'"
        ):
            index += 2
            continue
        if char == "'" and quote != '"':
            quote = None if quote == "'" else "'"
            index += 1
            continue
        if char == '"' and quote != "'":
            quote = None if quote == '"' else '"'
            index += 1
            continue
        substitution_prefix = None
        if quote != "'":
            if seg.startswith("$(", index):
                substitution_prefix = "$("
            elif (
                lower_tool in {"powershell", "pwsh"}
                and seg.startswith("@(", index)
            ):
                substitution_prefix = "@("
            elif lower_tool in {"powershell", "pwsh"} and char == "(":
                substitution_prefix = "("
            elif (
                lower_tool in {"bash", "sh"}
                and (
                    seg.startswith("<(", index)
                    or seg.startswith(">(", index)
                )
            ):
                substitution_prefix = seg[index:index + 2]
        if substitution_prefix:
            start = index + len(substitution_prefix)
            cursor = start
            depth = 1
            inner_quote = None
            while cursor < len(seg):
                current = seg[cursor]
                if (
                    current == escape
                    and inner_quote != "'"
                    and cursor + 1 < len(seg)
                ):
                    cursor += 2
                    continue
                if current == "'" and inner_quote != '"':
                    inner_quote = None if inner_quote == "'" else "'"
                elif current == '"' and inner_quote != "'":
                    inner_quote = None if inner_quote == '"' else '"'
                elif (
                    inner_quote != "'"
                    and (
                        seg.startswith("$(", cursor)
                        or (
                            lower_tool in {"powershell", "pwsh"}
                            and seg.startswith("@(", cursor)
                        )
                    )
                ):
                    depth += 1
                    cursor += 1
                elif inner_quote is None:
                    if current == "(":
                        depth += 1
                    elif current == ")":
                        depth -= 1
                        if depth == 0:
                            substitutions.append(seg[start:cursor])
                            index = cursor + 1
                            break
                cursor += 1
            else:
                index += len(substitution_prefix)
            continue
        if quote != "'" and lower_tool in {"bash", "sh"} and char == "`":
            start = index + 1
            cursor = start
            while cursor < len(seg):
                if seg[cursor] == "\\" and cursor + 1 < len(seg):
                    cursor += 2
                    continue
                if seg[cursor] == "`":
                    substitutions.append(seg[start:cursor])
                    index = cursor + 1
                    break
                cursor += 1
            else:
                index += 1
            continue
        index += 1
    return substitutions

"""Conservative recognition of the native CLI's single-question choice panel."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

MAX_SCREEN = 128 * 1024
MAX_TEXT = 2000
HEADER = "Copilot needs information."
FOOTER = "select · enter accept"
CURSOR = "❯"
OTHER = "Other (type your answer)"
OTHER_SELECTED = "Other (type your answer below)"


class MuxQuestionError(ValueError):
    """The inspected pane cannot safely accept the requested fallback."""


def answer_text(value: str) -> str:
    """Require one bounded printable line, never terminal control input."""
    if (
        not isinstance(value, str) or not value.strip() or value.strip() == ";" or len(value) > MAX_TEXT
        or any(unicodedata.category(char).startswith("C") for char in value)
        or "\u2028" in value or "\u2029" in value
    ):
        raise MuxQuestionError("mux answers require one nonempty printable line of at most 2000 characters")
    return value


@dataclass(frozen=True)
class Form:
    """One positively recognized form and its current input state."""

    question: tuple[str, ...]
    options: tuple[str, ...]
    cursor: int
    other: int | None
    typed: str = ""

    def same_question(self, other: Form) -> bool:
        return (
            self.question == other.question and self.options == other.options
        )


def parse_form(screen: str, *, typed: str = "") -> Form | None:
    """Reject unknown layouts; missing positive form markers never permit keys."""
    if len(screen) > MAX_SCREEN or "\x1b" in screen or "\x00" in screen:
        raise MuxQuestionError("invalid or oversized mux capture")
    lines = screen.splitlines()
    headers = [i for i, line in enumerate(lines) if line.strip() == HEADER]
    footers = [i for i, line in enumerate(lines) if FOOTER in line]
    if not headers and not footers:
        return None
    if len(headers) != 1 or len(footers) != 1 or footers[0] <= headers[0]:
        raise MuxQuestionError("ambiguous or incomplete question panel")
    start, end = headers[0], footers[0]
    body = [line.strip() for line in lines[start + 1:end] if line.strip()]
    # These are not the single native choice form; never infer permission from
    # a shared Enter key or from text quoted by another tool.
    if any(re.search(r"\b(?:allow tool|permission|multi-select|select all|question \d+ of)\b", line, re.I)
           for line in body):
        raise MuxQuestionError("permission or multi-question panels are not mux-answerable")
    cursors = [i for i, line in enumerate(body) if line.startswith(CURSOR)]
    if len(cursors) != 1:
        raise MuxQuestionError("one unambiguous choice cursor is required")
    cursor_line = cursors[0]
    headings = [i for i, line in enumerate(body[:cursor_line]) if line.endswith(":")]
    if len(headings) != 1:
        raise MuxQuestionError("only one labeled question is supported")
    split = headings[0] + 1
    question = tuple(body[:split])
    raw_options = body[split:]
    if not question or not 2 <= len(raw_options) <= 12:
        raise MuxQuestionError("unsupported question or choice count")
    cursor = cursor_line - split
    options = []
    other = None
    separate_input = False
    for index, line in enumerate(raw_options):
        label = line.removeprefix(CURSOR).strip()
        if label in {OTHER, OTHER_SELECTED}:
            if other is not None:
                raise MuxQuestionError("ambiguous freeform option")
            other = index
            separate_input = label == OTHER_SELECTED
            label = OTHER
        elif label.startswith(OTHER):
            raise MuxQuestionError("pre-existing freeform text is not answerable")
        if not label or label.endswith(":") or CURSOR in label:
            raise MuxQuestionError("ambiguous choice rendering")
        options.append(label)
    if len(set(options)) != len(options) or not 0 <= cursor < len(options):
        raise MuxQuestionError("ambiguous choices")
    if separate_input:
        inputs = [
            line.strip().removeprefix(CURSOR).strip()
            for line in lines[end + 1:] if line.strip().startswith(CURSOR)
        ]
        if cursor != other or inputs != [typed if typed else "Type your answer..."]:
            raise MuxQuestionError("freeform input changed or is not the expected native field")
    elif typed:
        raise MuxQuestionError("typed answer is not visible in the native freeform field")
    return Form(question, tuple(options), cursor, other, typed)


def stable_screen(screen: str) -> str:
    """Exclude only the native pending clock and greeting's blinking eyes."""
    lines = screen.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if line.strip() == HEADER]
    if len(headers) != 1:
        return screen
    for index in range(headers[0]):
        lines[index] = re.sub(
            r"^(│ +█ )(?:▘▝|╴╶)( █ +Check for mistakes\.)", r"\1<eyes>\2", lines[index],
        )
    for index in range(headers[0] - 1, -1, -1):
        text = lines[index].rstrip("\r\n")
        if not text.strip() or set(text.strip()) == {"─"}:
            continue
        match = re.fullmatch(
            r"([ \t]*○ Asking user Waiting for response)(?: *(?:[0-9]+m )?[0-9]+s)?[ \t]*",
            text,
        )
        if match:
            lines[index] = match[1] + " <elapsed>" + lines[index][len(text):]
        break
    return "".join(lines)


def completion_echo(before: str, after: str, form: Form, answer: str) -> bool:
    """Require a newly rendered matching answer, never an old success echo."""
    marker = "Asked user " + " ".join(form.question[:-1])
    expected = form.question[-1] + " " + answer
    header = re.compile(re.escape(marker) + r"(?: {2,}(?:[0-9]+m )?[0-9]+s)?")

    def matching_echoes(screen: str) -> int:
        lines = screen.splitlines()
        count = 0
        for index, line in enumerate(lines[:-1]):
            if not header.fullmatch(line.strip().removeprefix("●").strip()):
                continue
            content = lines[index + 1].strip().removeprefix("└").strip()
            if content == expected:
                count += 1
        return count

    return matching_echoes(after) > matching_echoes(before)

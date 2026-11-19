"""Finite candidate restriction for an explicitly limited flat WIQL grammar."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_QUERY_CHARS = 32_000
_MAX_CANDIDATES = 100
_TOKEN = re.compile(
    r"(?P<space>[ \t\r\n]+)"
    r"|(?P<field>\[[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*\])"
    r"|(?P<string>'[^'\\\x00-\x1f]*')"
    r"|(?P<number>-?[0-9]+(?:\.[0-9]+)?)(?![A-Za-z_0-9.])"
    r"|(?P<operator><>|<=|>=|=|<|>)"
    r"|(?P<word>[A-Za-z]+)(?![A-Za-z_0-9])"
    r"|(?P<punct>[(),])",
)


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    start: int
    end: int


def _unsupported() -> ValueError:
    return ValueError(
        "Azure DevOps bounded WIQL requires a supported flat literal query; "
        "unsupported syntax needs separate explicit acquisition",
    )


def _tokens(query: str) -> list[_Token]:
    result: list[_Token] = []
    offset = 0
    while offset < len(query):
        match = _TOKEN.match(query, offset)
        if match is None:
            raise _unsupported()
        kind = match.lastgroup
        if kind is None:
            raise _unsupported()
        if kind != "space":
            result.append(_Token(kind, match.group(), offset, match.end()))
            if len(result) > 4096:
                raise ValueError("Azure DevOps bounded WIQL exceeds the token budget")
        offset = match.end()
    result.append(_Token("end", "", len(query), len(query)))
    return result


class _Parser:
    def __init__(self, query: str) -> None:
        self.tokens = _tokens(query)
        self.index = 0

    def accept(self, text: str) -> bool:
        if self.tokens[self.index].text.casefold() != text:
            return False
        self.index += 1
        return True

    def expect(self, text: str) -> _Token:
        token = self.tokens[self.index]
        if not self.accept(text):
            raise _unsupported()
        return token

    def kind(self, *kinds: str) -> _Token:
        token = self.tokens[self.index]
        if token.kind not in kinds:
            raise _unsupported()
        self.index += 1
        return token

    def fields(self, *, ordering: bool = False) -> None:
        while True:
            self.kind("field")
            if ordering and not self.accept("asc"):
                self.accept("desc")
            if not self.accept(","):
                return

    def expression(self, depth: int = 0) -> None:
        self.conjunction(depth)
        while self.accept("or"):
            self.conjunction(depth)

    def conjunction(self, depth: int) -> None:
        self.condition(depth)
        while self.accept("and"):
            self.condition(depth)

    def condition(self, depth: int) -> None:
        if self.accept("("):
            if depth >= 32:
                raise ValueError("Azure DevOps bounded WIQL exceeds the grouping budget")
            self.expression(depth + 1)
            self.expect(")")
            return
        self.kind("field")
        negated = self.accept("not")
        if self.accept("in"):
            self.expect("(")
            self.kind("string", "number")
            while self.accept(","):
                self.kind("string", "number")
            self.expect(")")
        elif negated:
            raise _unsupported()
        else:
            self.kind("operator")
            self.kind("string", "number")

    def boundaries(self) -> tuple[int, int]:
        self.expect("select")
        self.fields()
        self.expect("from")
        self.expect("workitems")
        start = self.expect("where").end
        self.expression()
        end = self.tokens[self.index - 1].end
        if self.accept("order"):
            self.expect("by")
            self.fields(ordering=True)
        self.kind("end")
        return start, end


def restrict_wiql_to_candidates(query: str, item_ids: list[int] | tuple[int, ...]) -> str:
    """Intersect a supported flat query with 1-100 candidate work-item IDs.

    Supported predicates use bracketed reference names, numeric or simple
    single-quoted literals, comparisons, IN/NOT IN, AND/OR and parentheses.
    SELECT columns and optional ORDER BY are retained. Escaped quotes,
    backslashes, macros, historical/link queries and other syntax refuse.
    This performs no provider I/O or field-type/coverage qualification and
    does not enable a connector capability or change the supplied query.
    """
    if not isinstance(query, str) or len(query) > _MAX_QUERY_CHARS or not query.strip():
        raise ValueError(
            "Azure DevOps bounded WIQL requires a nonempty query of at most 32000 chars"
        )
    if (
        type(item_ids) not in (list, tuple)
        or not 1 <= len(item_ids) <= _MAX_CANDIDATES
        or any(type(value) is not int or not 1 <= value <= 2_147_483_647 for value in item_ids)
    ):
        raise ValueError("Azure DevOps bounded WIQL requires 1-100 positive int32 candidate IDs")
    parser = _Parser(query)
    start, end = parser.boundaries()
    ids = ", ".join(str(value) for value in sorted(set(item_ids)))
    result = (
        f"{query[:start]} ({query[start:end].strip()}) AND [System.Id] IN ({ids}){query[end:]}"
    )
    if len(result) > _MAX_QUERY_CHARS:
        raise ValueError("Azure DevOps bounded WIQL candidate restriction exceeds 32000 chars")
    return result

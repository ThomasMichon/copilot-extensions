"""Copilot-independent frontdoor for the packaged coordinator implementation."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from agent_dispatch.__main__ import main as backend

    return backend(argv)


if __name__ == "__main__":
    raise SystemExit(main())

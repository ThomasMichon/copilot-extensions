#!/usr/bin/env python3
"""Measure and inspect the compatibility-root (core()/monkeypatch-on-root)
migration for one plugin -- see docs/patterns/compatibility-root-decoupling.md
and efforts/active/compatibility-root-decoupling/README.md for the design
invariant and migration plan this tool supports.

Two modes:

  python tools/compat-root-migration.py --plugin agent-worktrees --progress
      Aggregate counts: how many core()/_core() accessors remain, how many
      <alias>.<name>(...) / <alias>().<name>(...) call sites, how many
      distinct names are still monkeypatched on the root module, and how
      many total patch-site occurrences. Re-run after each migration slice
      to see the number drop.

  python tools/compat-root-migration.py --plugin agent-worktrees --name _json_output
      For one name: where it's actually defined, every call site reaching
      it through a root-module alias (both the `core()`-style lazy
      function-call accessor and a plain `from . import __main__ as X`
      import alias), and every test monkeypatch targeting the root module
      for that name. A migration slice for a name is "done" when this
      command reports zero call sites and zero monkeypatch sites.

Root module alias detection: a file's own import line
(`from <pkg> import __main__ as X` or `import <pkg>.__main__ as X`,
possibly indented inside a function body) decides which alias(es) to search
against in *that* file -- not hardcoded to one name. This repo's own source
and test files use several: `core`, `_core`, `m`, `main`, `cli` all observed
in agent-worktrees, both at module level and function-local.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CORE_ACCESSOR_DEF_RE = re.compile(r"^def (_?core)\(\)\s*:", re.MULTILINE)
ROOT_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+[\w.]+\s+import\s+__main__\s+as\s+(\w+)"
    r"|import\s+[\w.]+\.__main__\s+as\s+(\w+))",
    re.MULTILINE,
)


def _package_name(plugin: str) -> str:
    return plugin.replace("-", "_")


def _src_dir(plugin: str) -> Path:
    return REPO / "plugins" / plugin / "src" / _package_name(plugin)


def _tests_dir(plugin: str) -> Path:
    return REPO / "plugins" / plugin / "tests"


def _iter_py_files(root: Path):
    if not root.is_dir():
        return
    yield from root.rglob("*.py")


def _root_aliases_in_file(text: str) -> set[str]:
    return {g1 or g2 for g1, g2 in ROOT_IMPORT_RE.findall(text)}


def find_definition_sites(plugin: str, name: str) -> list[str]:
    pattern = re.compile(rf"^def {re.escape(name)}\(", re.MULTILINE)
    hits = []
    for path in _iter_py_files(_src_dir(plugin)):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path.relative_to(REPO)}:{line}")
    return hits


def find_core_call_sites(plugin: str, name: str) -> list[str]:
    hits = []
    for path in _iter_py_files(_src_dir(plugin)):
        text = path.read_text(encoding="utf-8", errors="replace")
        patterns = []
        # The lazy-accessor-function shape: callers always invoke the
        # wrapper FUNCTION by its own defined name (conventionally
        # `_core()`), never the internal variable name that function's own
        # `from . import __main__ as X` binds (that name is only used
        # inside the wrapper's own body, never by callers) -- so this is
        # checked independent of _root_aliases_in_file, keyed only on the
        # wrapper's own definition.
        accessor_match = CORE_ACCESSOR_DEF_RE.search(text)
        if accessor_match:
            patterns.append(rf"\b{re.escape(accessor_match.group(1))}\(\)\.")
        # The plain-alias-as-variable shape (e.g. `core = _core()` then
        # `core.attr(...)`, or a direct top-level
        # `from . import __main__ as X` used without a wrapper function):
        # needs a word boundary so a short alias like `m` never matches as
        # the tail of an unrelated longer identifier (e.g. `this_platform`).
        aliases = _root_aliases_in_file(text)
        if aliases:
            alias_group = "|".join(re.escape(a) for a in aliases)
            patterns.append(rf"\b(?:{alias_group})(?:\(\))?\.")
        if not patterns:
            continue
        pattern = re.compile(rf"(?:{'|'.join(patterns)}){re.escape(name)}\(")
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path.relative_to(REPO)}:{line}")
    return hits


def find_monkeypatch_sites(plugin: str, name: str) -> list[str]:
    hits = []
    # Four independent dotted/object-attribute shapes all target the root
    # module for a given name:
    #   1. unittest.mock.patch("<pkg>.__main__.<name>") -- dotted string,
    #      unconditional (no alias import needed since it's a literal path).
    #   2. monkeypatch.setattr("<pkg>.__main__.<name>", replacement) --
    #      pytest's monkeypatch also accepts a single dotted-string target
    #      (resolved internally), not just an (object, "attr") pair; also
    #      unconditional.
    #   3. monkeypatch.setattr(<alias>, "<name>", ...) -- alias-gated (the
    #      alias must come from this file's own `as X` import).
    #   4. unittest.mock.patch.object(<alias>, "<name>", ...) -- the
    #      object-attribute sibling of patch(); also alias-gated.
    dotted_patterns = [
        re.compile(rf'patch\(\s*"[\w.]+\.__main__\.{re.escape(name)}"'),
        re.compile(
            rf'monkeypatch\.setattr\(\s*"[\w.]+\.__main__\.{re.escape(name)}"'
        ),
    ]
    for path in _iter_py_files(_tests_dir(plugin)):
        text = path.read_text(encoding="utf-8", errors="replace")
        aliases = _root_aliases_in_file(text)
        if aliases:
            alias_group = "|".join(re.escape(a) for a in aliases)
            alias_patterns = [
                re.compile(
                    rf'monkeypatch\.setattr\(\s*(?:{alias_group})\s*,\s*\n?\s*"{re.escape(name)}"'
                ),
                re.compile(
                    rf'patch\.object\(\s*(?:{alias_group})\s*,\s*\n?\s*"{re.escape(name)}"'
                ),
            ]
            for pattern in alias_patterns:
                for m in pattern.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{path.relative_to(REPO)}:{line}")
        for dotted_pattern in dotted_patterns:
            for m in dotted_pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path.relative_to(REPO)}:{line}")
    return hits


def cmd_name(plugin: str, name: str) -> int:
    defs = find_definition_sites(plugin, name)
    calls = find_core_call_sites(plugin, name)
    patches = find_monkeypatch_sites(plugin, name)

    if not defs and not calls and not patches:
        print(
            f"error: {name!r} has no definition site, root-alias call site, "
            f"or root-patch site anywhere in {plugin} -- check for a typo. "
            "Refusing to report a vacuous '[done]' for a name this tool "
            "never saw at all.",
            file=sys.stderr,
        )
        return 1

    print(f"=== {name} ({plugin}) ===")
    print(f"\nDefinition site(s) ({len(defs)}):")
    for h in defs:
        print(f"  {h}")
    print(f"\nRoot-alias call sites ({len(calls)}):")
    for h in calls:
        print(f"  {h}")
    print(f"\nRoot-patch site(s) ({len(patches)}):")
    for h in patches:
        print(f"  {h}")

    if not calls and not patches:
        print(f"\n[done] {name} has zero root-alias call sites and zero root "
              "monkeypatches -- this name's migration is complete.")
    else:
        print(f"\n[open] {len(calls)} call site(s) and {len(patches)} "
              "monkeypatch site(s) remain for this name.")
    return 0


def cmd_progress(plugin: str) -> int:
    src_files = list(_iter_py_files(_src_dir(plugin)))
    test_files = list(_iter_py_files(_tests_dir(plugin)))

    accessor_count = 0
    call_names: Counter[str] = Counter()
    for path in src_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        accessor_match = CORE_ACCESSOR_DEF_RE.search(text)
        if accessor_match:
            accessor_count += 1
        patterns = []
        if accessor_match:
            patterns.append(rf"\b{re.escape(accessor_match.group(1))}\(\)\.")
        aliases = _root_aliases_in_file(text)
        if aliases:
            alias_group = "|".join(re.escape(a) for a in aliases)
            patterns.append(rf"\b(?:{alias_group})(?:\(\))?\.")
        if not patterns:
            continue
        call_re = re.compile(rf"(?:{'|'.join(patterns)})(\w+)\(")
        for name in call_re.findall(text):
            call_names[name] += 1

    patch_names: Counter[str] = Counter()
    dotted_patterns = [
        re.compile(r'patch\(\s*"[\w.]+\.__main__\.(\w+)"'),
        re.compile(r'monkeypatch\.setattr\(\s*"[\w.]+\.__main__\.(\w+)"'),
    ]
    for path in test_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        aliases = _root_aliases_in_file(text)
        if aliases:
            alias_group = "|".join(re.escape(a) for a in aliases)
            alias_patterns = [
                re.compile(
                    rf'monkeypatch\.setattr\(\s*(?:{alias_group})\s*,\s*\n?\s*"(\w+)"'
                ),
                re.compile(
                    rf'patch\.object\(\s*(?:{alias_group})\s*,\s*\n?\s*"(\w+)"'
                ),
            ]
            for pattern in alias_patterns:
                for name in pattern.findall(text):
                    patch_names[name] += 1
        # Dotted-string targets -- unconditional, independent of any alias
        # import in the file (see find_monkeypatch_sites).
        for dotted_pattern in dotted_patterns:
            for name in dotted_pattern.findall(text):
                patch_names[name] += 1

    total_calls = sum(call_names.values())
    total_patch_sites = sum(patch_names.values())

    print(f"=== compatibility-root migration progress: {plugin} ===")
    print(f"core()/_core() accessor definitions: {accessor_count}")
    print(f"root-alias call sites (all aliases, both core()./core. shapes): {total_calls}")
    print(f"distinct names monkeypatched on the root module: {len(patch_names)}")
    print(f"total monkeypatch patch-site occurrences: {total_patch_sites}")

    print("\nTop names by call-site traffic:")
    for name, count in call_names.most_common(15):
        patch_count = patch_names.get(name, 0)
        print(f"  {name}: {count} call site(s), {patch_count} monkeypatch site(s)")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True, help="plugin name, e.g. agent-worktrees")
    parser.add_argument("--name", help="inspect one name's migration status")
    parser.add_argument("--progress", action="store_true", help="aggregate progress counts")
    args = parser.parse_args(argv)

    if not args.name and not args.progress:
        parser.error("pass --name <name> or --progress")

    src_dir = _src_dir(args.plugin)
    if not src_dir.is_dir():
        parser.error(
            f"--plugin {args.plugin!r} has no source directory at "
            f"{src_dir.relative_to(REPO)} -- check the plugin name "
            "(e.g. a typo, or a plugin with no src/ layout). Refusing to "
            "silently report an empty-but-'successful' scan."
        )

    if args.name:
        return cmd_name(args.plugin, args.name)
    return cmd_progress(args.plugin)


if __name__ == "__main__":
    sys.exit(main())

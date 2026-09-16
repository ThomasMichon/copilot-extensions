#!/usr/bin/env python3
"""Hoist enabled directory-marketplace plugin agents into `.github/agents/`.

**Why this exists.** Some Copilot CLI runtime versions cannot reach a
marketplace/plugin-defined custom agent from a delegated/background sub-agent
(e.g. the `task` tool's `agent_type`), and a freshly spawned nested `copilot`
process does not inherit the parent session's resolved `enabledPlugins`
either -- it must be told about every plugin explicitly via `--plugin-dir`.
Repo-local `.github/agents/*.agent.md` files remain natively discoverable from
every one of those surfaces, independent of plugin/marketplace resolution.

This script copies (never moves, never disables) each *enabled* plugin's
`agents/*.agent.md` from an in-repo **directory** marketplace (e.g.
`extraKnownMarketplaces.<name>.source == {"source": "directory", "path": ...}`)
into a repo-local output directory (default `.github/agents/`), rewriting only
the relative markdown links each file carries so they still resolve back to
the plugin's real `agents/` directory. Plugin-owned skills, MCP bridge
configs, and marketplace registration are untouched -- this produces a
regenerable parallel entry point, not a fork. It intentionally covers only
directory-source marketplaces (in-repo plugin trees): an installed
(github/npm/etc.) marketplace payload lives outside the repository, so a
relative link back to it cannot be committed portably.

Run `sync <repo-root>` to (re)generate, or `scan <repo-root>` to verify the
checked-in copies are still in sync (wire this into your own repo's CI/build
validation). Retire your adopting repo's copy once your Copilot CLI runtime
version restores marketplace-agent delegation from every delegated/background
surface.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path, PurePosixPath


DEFAULT_OUTPUT_DIR = PurePosixPath(".github/agents")
SETTINGS_CANDIDATES = (
    PurePosixPath(".github/copilot/settings.json"),
    PurePosixPath(".claude/settings.json"),
)

_LINK_PATTERN = re.compile(r"(\]\()([^)\s]+)(\))")
_ABSOLUTE_SCHEMES = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

_GENERATED_BANNER = """
<!-- GENERATED -- hoisted by hoist_plugin_agents.py from {source}
(plugin version {version}). This is a workaround for a Copilot CLI limitation
where delegated/background sub-agents and freshly spawned nested `copilot`
processes cannot resolve marketplace/plugin-defined custom agents. Do not
hand-edit this copy; edit the source file and re-run the script. Retire this
output once your Copilot CLI runtime restores marketplace-agent delegation
from every delegated/background surface. -->
"""


class HoistError(ValueError):
    """Malformed settings, an agent-name conflict, or an unsafe path."""


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise HoistError(f"{path} must contain a JSON object")
    return value


def load_settings(repo_root: Path) -> dict:
    """Merge `enabledPlugins`/`extraKnownMarketplaces` across settings files.

    Later files in `SETTINGS_CANDIDATES` win per-key on conflict, mirroring
    Copilot CLI settings layering; most repos only have one such file.
    """
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    found = False
    for relative in SETTINGS_CANDIDATES:
        path = repo_root / relative
        if not path.is_file():
            continue
        found = True
        value = _load_json(path)
        plugin_map = value.get("enabledPlugins")
        if plugin_map is not None:
            if not isinstance(plugin_map, dict) or any(
                not isinstance(k, str) or not isinstance(v, bool)
                for k, v in plugin_map.items()
            ):
                raise HoistError(
                    f"{relative} enabledPlugins must map strings to booleans"
                )
            enabled.update(plugin_map)
        marketplace_map = value.get("extraKnownMarketplaces")
        if marketplace_map is not None:
            if not isinstance(marketplace_map, dict) or any(
                not isinstance(k, str) or not isinstance(v, dict)
                for k, v in marketplace_map.items()
            ):
                raise HoistError(
                    f"{relative} extraKnownMarketplaces must map strings to "
                    "objects"
                )
            marketplaces.update(marketplace_map)
    if not found:
        names = ", ".join(str(candidate) for candidate in SETTINGS_CANDIDATES)
        raise HoistError(f"no settings file found ({names})")
    return {"enabledPlugins": enabled, "extraKnownMarketplaces": marketplaces}


def _directory_marketplace_path(marketplace_name: str, entry: dict) -> str | None:
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "directory":
        return None
    path = source.get("path")
    if not isinstance(path, str) or not path:
        raise HoistError(
            f"marketplace {marketplace_name!r} directory source is missing "
            "'path'"
        )
    return path


def enabled_plugin_agent_dirs(repo_root: Path) -> list[tuple[str, str, Path]]:
    """`(marketplace, plugin, agents_dir)` for enabled directory-marketplace
    plugins that ship an `agents/` directory. Non-directory marketplace
    sources (github, npm, ...) are skipped -- see module docstring."""
    settings = load_settings(repo_root)
    resolved_root = repo_root.resolve()
    results: list[tuple[str, str, Path]] = []
    for key, active in settings["enabledPlugins"].items():
        if not active or "@" not in key:
            continue
        plugin_name, _, marketplace_name = key.rpartition("@")
        entry = settings["extraKnownMarketplaces"].get(marketplace_name)
        if entry is None:
            continue
        rel_path = _directory_marketplace_path(marketplace_name, entry)
        if rel_path is None:
            continue
        plugin_dir = (repo_root / rel_path / plugin_name).resolve()
        try:
            plugin_dir.relative_to(resolved_root)
        except ValueError as exc:
            raise HoistError(
                f"marketplace {marketplace_name!r} path escapes the "
                "repository root"
            ) from exc
        agents_dir = plugin_dir / "agents"
        if agents_dir.is_dir():
            results.append((marketplace_name, plugin_name, agents_dir))
    return sorted(results)


def source_agent_files(repo_root: Path) -> list[tuple[str, str, Path]]:
    """`(marketplace, plugin, agent_md_path)` for every enabled agent file."""
    pairs: list[tuple[str, str, Path]] = []
    for marketplace, plugin, agents_dir in enabled_plugin_agent_dirs(repo_root):
        for path in sorted(agents_dir.glob("*.agent.md")):
            pairs.append((marketplace, plugin, path))
    return pairs


def _is_relative_link(target: str) -> bool:
    if target.startswith(("#", "/", "mailto:")):
        return False
    return not _ABSOLUTE_SCHEMES.match(target)


def _rewrite_link(target: str, repo_root: Path, output_dir: PurePosixPath,
                   source: Path) -> str:
    if not _is_relative_link(target):
        return target
    path_part, sep, anchor = target.partition("#")
    absolute_target = (source.parent / path_part).resolve()
    try:
        relative_to_root = absolute_target.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise HoistError(
            f"{source}: relative link {target!r} escapes the repository root"
        ) from exc
    depth = len(output_dir.parts)
    rewritten = posixpath.normpath(
        posixpath.join(*([".."] * depth), relative_to_root.as_posix())
    )
    return rewritten + (sep + anchor if sep else "")


def rewrite_relative_links(body: str, repo_root: Path, output_dir: PurePosixPath,
                            source: Path) -> str:
    """Rewrite `source`-relative markdown links for the hoisted output dir."""
    return _LINK_PATTERN.sub(
        lambda match: (
            match.group(1)
            + _rewrite_link(match.group(2), repo_root, output_dir, source)
            + match.group(3)
        ),
        body,
    )


def _plugin_version(plugin_dir: Path) -> str:
    manifest_path = plugin_dir.parent / "plugin.json"
    if not manifest_path.is_file():
        return "unknown"
    manifest = _load_json(manifest_path)
    version = manifest.get("version")
    return version if isinstance(version, str) and version else "unknown"


def split_frontmatter(text: str, source: Path) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise HoistError(f"{source} is missing a leading YAML frontmatter block")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise HoistError(f"{source} frontmatter block is not terminated")
    return text[: end + len("\n---\n")], text[end + len("\n---\n"):]


def render(repo_root: Path, source: Path, output_dir: PurePosixPath) -> bytes:
    text = source.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text, source)
    body = rewrite_relative_links(body, repo_root, output_dir, source)
    banner = _GENERATED_BANNER.format(
        source=source.relative_to(repo_root.resolve()).as_posix()
        if source.is_absolute()
        else source.as_posix(),
        version=_plugin_version(source.parent),
    )
    return (frontmatter + banner + body).encode("utf-8")


def desired(repo_root: Path, output_dir: PurePosixPath) -> dict[Path, bytes]:
    output: dict[Path, bytes] = {}
    claimed_by: dict[Path, str] = {}
    resolved_root = repo_root.resolve()
    for marketplace, plugin, source in source_agent_files(repo_root):
        target = resolved_root / output_dir / source.name
        if target in claimed_by:
            new_owner = f"{marketplace}:{plugin}"
            raise HoistError(
                f"hoisted agent filename conflict: {source.name} is claimed "
                f"by both {claimed_by[target]!r} and {new_owner!r}"
            )
        claimed_by[target] = f"{marketplace}:{plugin}"
        output[target] = render(repo_root, source, output_dir)
    return output


def existing_output_files(repo_root: Path, output_dir: PurePosixPath) -> set[Path]:
    directory = repo_root.resolve() / output_dir
    if not directory.is_dir():
        return set()
    return set(directory.glob("*.agent.md"))


def sync(repo_root: Path, output_dir: PurePosixPath) -> tuple[list[Path], list[Path]]:
    """Write the hoisted copies; return (written, removed-stale)."""
    expected = desired(repo_root, output_dir)
    stale = existing_output_files(repo_root, output_dir) - set(expected)
    written: list[Path] = []
    for path, content in expected.items():
        if path.is_file() and path.read_bytes() == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        written.append(path)
    for path in stale:
        path.unlink()
    return written, sorted(stale)


def scan(repo_root: Path, output_dir: PurePosixPath) -> tuple[list[Path], list[Path]]:
    """Report (out-of-date, stale) without writing anything."""
    expected = desired(repo_root, output_dir)
    stale = existing_output_files(repo_root, output_dir) - set(expected)
    mismatches = [
        path
        for path, content in expected.items()
        if not path.is_file() or path.read_bytes() != content
    ]
    return sorted(mismatches), sorted(stale)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("sync", "scan"):
        subparser = subparsers.add_parser(operation)
        subparser.add_argument("root", nargs="?", default=".")
        subparser.add_argument(
            "--output-dir",
            default=str(DEFAULT_OUTPUT_DIR),
            help="repository-relative output directory (default: .github/agents)",
        )
        subparser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.root).expanduser()
    if not repo_root.is_dir():
        print(f"error: {repo_root} is not a directory", file=sys.stderr)
        return 2
    output_dir = PurePosixPath(args.output_dir)

    try:
        if args.operation == "sync":
            written, stale = sync(repo_root, output_dir)
            mismatches = written
        else:
            mismatches, stale = scan(repo_root, output_dir)
    except HoistError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "operation": args.operation,
                    "changed": [str(p) for p in mismatches],
                    "stale": [str(p) for p in stale],
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif mismatches or stale:
        action = "updated" if args.operation == "sync" else "out of date"
        print(f"hoisted plugin agents {action}:")
        for path in mismatches:
            print(f"  {path}")
        for path in stale:
            print(f"  {path} (stale)")
    else:
        print("hoisted plugin agents are synchronized")

    if args.operation == "scan" and (mismatches or stale):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build filtered, topology-preserving local marketplace subsets."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
from collections.abc import Mapping
from pathlib import Path

from .marketplace import load_marketplace, plugin_dir
from .payload import dependencies, plugin_manifest, publication_metadata, report_diagnostic
from .settings import local_marketplace_path, marketplace_manifest_name, read_repo_settings, split_source

_EXCLUDED = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".test-venvs"}
_RELATIVE = re.compile(r"(?:\.\./)+[A-Za-z0-9_./-]+")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def selection_spec(value) -> dict:
    """Retain explicit booleans without imposing plugin identity syntax."""
    if isinstance(value, str):
        source, enable = value, True
    elif isinstance(value, Mapping):
        source, enable = value.get("source"), value.get("enable", True)
    else:
        raise ValueError("Plugin selection must be a source string or mapping")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Plugin selection source must be a non-empty string")
    if not isinstance(enable, bool):
        raise ValueError("Plugin selection enable must be a boolean")
    return {"source": source, "enable": enable}


def selection_cli_args(value) -> list[str]:
    """Encode install-only selections without overloading source strings."""
    spec = selection_spec(value)
    return ["--stage-plugin" if spec["enable"] else "--stage-plugin-disabled", spec["source"]]


def identity(source: str) -> tuple[str, str]:
    name, marketplace = split_source(source)
    if not _IDENTITY.fullmatch(name) or not _IDENTITY.fullmatch(marketplace):
        raise ValueError(f"Expected qualified plugin identity: {source}")
    return name, marketplace


def _allowed(path: Path) -> bool:
    name = path.name.lower()
    return not any(part in _EXCLUDED for part in path.parts) and not (
        name == ".env" or name.startswith(".env.")
        and not name.endswith((".example", ".sample", ".template"))
    )


def subset(root: Path, payload: Path) -> bytes:
    """Preserve existing sibling references without flattening plugin trees.

    Required skill/reference and runtime paths extend the closure. Top-level
    documentation is copied as payload, never crawled for contribution links.
    References never authorize leaving the declaring marketplace.
    """
    root = root.resolve(strict=True)
    pending = [payload]
    files: dict[str, Path] = {}
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if not _allowed(path.relative_to(root)) or path in visited:
            continue
        visited.add(path)
        if path.is_symlink() or getattr(path.lstat(), "st_reparse_tag", 0):
            raise ValueError(f"Linked payload paths cannot be reflected: {path.name}")
        path.resolve(strict=True).relative_to(root)
        if path.is_dir():
            pending.extend(sorted(path.iterdir()))
            continue
        if not path.is_file():
            raise ValueError(f"Non-file payload member: {path.name}")
        files[path.relative_to(root).as_posix()] = path
        relative = path.relative_to(root)
        if path.name.lower() in {"readme.md", "contributing.md", "license"}:
            continue
        if path.name != "plugin.json" and not set(relative.parts).intersection(
            {"skills", "agents", "instructions", "references", "scripts", "hooks", "extensions"}
        ):
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        for match in _RELATIVE.finditer(text):
            for base in (path.parent, payload):
                candidate = Path(os.path.normpath(base / match.group().rstrip(".")))
                if candidate.exists():
                    resolved = candidate.resolve(strict=True)
                    # Script root expressions are context, not requests to
                    # recursively reflect the enclosing control checkout.
                    if candidate.is_dir() and payload.resolve().is_relative_to(resolved):
                        continue
                    resolved.relative_to(root)
                    pending.append(candidate)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, path in sorted(files.items()):
            info = archive.gettarinfo(str(path), arcname=name)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    return gzip.compress(buffer.getvalue(), mtime=0)


def _plan_item(
    spec, owner, declarations: dict, *, allow_external: bool = False,
    existing_root: Path | None = None, required: bool = True,
) -> dict | None:
    """Plan against supplied authority, without re-reading a generated overlay."""
    item = selection_spec(spec)
    source = item["source"]
    name, marketplace = identity(source)
    local = local_marketplace_path(marketplace, owner[1], repo_dir=owner[0]) if owner else None
    if local is not None:
        local = local.resolve(strict=True)
        base = owner[0].resolve()
        if not local.is_relative_to(base):
            if not allow_external:
                raise ValueError(f"Local marketplace escapes declaring checkout: {marketplace}")
            base = local
        manifest = load_marketplace(local)
        if manifest is not None and manifest.name != marketplace_manifest_name(marketplace, owner[1]):
            raise ValueError(f"Required local marketplace identity mismatch: {marketplace}")
        payload = plugin_dir(manifest, name) if manifest else None
        if payload is None:
            if not required:
                return None
            raise ValueError(f"Required local plugin is absent: {source}")
        declared = plugin_manifest(payload)
        item["catalog"], item["entry"] = publication_metadata(local, source)
        item["requirements"] = dependencies(declared, marketplace)
        if existing_root is not None and payload.is_relative_to(existing_root):
            item["local_path"] = str(payload)
        else:
            content = subset(base, payload)
            item.update(
                archive=base64.b64encode(content).decode("ascii"),
                digest=hashlib.sha256(content).hexdigest(),
                relative=payload.relative_to(base).as_posix(),
            )
    else:
        definition = declarations.get(marketplace)
        if not definition:
            item["resolve_on_target"] = True
            return item
        kind = definition.get("source", {}).get("source")
        if kind in {"directory", "local"}:
            raise ValueError(f"Local marketplace has no declaring checkout: {marketplace}")
        item["marketplace"] = definition
    return item


def build_plan(
    specs: list[dict], repo_roots: list[Path], *, marketplaces: dict | None = None,
    allow_external: bool = False, existing_root: Path | None = None,
) -> dict:
    """Resolve local sources first-wins; remote sources remain install requests."""
    declarations = dict(marketplaces or {})
    owners = {}
    for root in repo_roots:
        settings = read_repo_settings(root)
        for key, definition in settings.marketplaces.items():
            if key not in owners:
                owners[key] = (root, settings)
                declarations[key] = definition
    plugins = []
    visited = set()
    for spec in specs:
        normalized = selection_spec(spec)
        source = normalized["source"]
        if source in visited:
            continue
        visited.add(source)
        _name, marketplace = identity(source)
        plugins.append(_plan_item(
            normalized, owners.get(marketplace), declarations,
            allow_external=allow_external, existing_root=existing_root,
        ))
    return {"plugins": plugins, "marketplaces": declarations}


def build_prepare_script(plan: dict, repo: str) -> str:
    """Ship the stdlib-only engine over an existing script/stdin transport."""
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("Plugin preparation requires the execution checkout")
    package = Path(__file__).parent
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(package.glob("*.py")):
            archive.add(path, arcname=f"plugin_resolve/{path.name}")
    encoded = base64.b64encode(gzip.compress(buffer.getvalue(), mtime=0)).decode("ascii")
    request = base64.b64encode(json.dumps({
        "repo": repo, **plan,
    }).encode()).decode("ascii")
    # stdin is the script, not an unbounded SSH argv payload.
    return (
        "set -e\npython3 - <<'VENUE_PLUGIN_PREPARE'\n"
        "import base64,io,tarfile,pathlib,hashlib,sys,json,uuid,os\n"
        f"content=base64.b64decode({encoded!r})\n"
        "root=pathlib.Path.home()/'.acp-staged-plugins'/'helpers'/hashlib.sha256(content).hexdigest()\n"
        "root.mkdir(parents=True,exist_ok=True)\n"
        "with tarfile.open(fileobj=io.BytesIO(content)) as archive:\n"
        "    for member in archive.getmembers():\n"
        "        destination=root/member.name\n"
        "        content=archive.extractfile(member).read()\n"
        "        if not destination.exists() or destination.read_bytes()!=content:\n"
        "            destination.parent.mkdir(parents=True,exist_ok=True)\n"
        "            pending=destination.with_name(destination.name+'.'+uuid.uuid4().hex)\n"
        "            pending.write_bytes(content)\n"
        "            os.replace(pending,destination)\n"
        "sys.path.insert(0,str(root))\n"
        "from plugin_resolve.preparation import prepare\n"
        f"request=json.loads(base64.b64decode({request!r}))\n"
        "diagnostics=[]\n"
        "prepared=prepare(request,diagnostics=diagnostics)\n"
        "if diagnostics: print('__VENUE_REQUIREMENTS__'+json.dumps(diagnostics))\n"
        "print('__VENUE_PLUGINS__'+json.dumps(prepared))\n"
        "VENUE_PLUGIN_PREPARE\n"
    )


def parse_prepared(output: str) -> list[str]:
    for line in output.splitlines():
        if line.startswith("__VENUE_REQUIREMENTS__"):
            for item in json.loads(line.removeprefix("__VENUE_REQUIREMENTS__")):
                report_diagnostic(item)
    for line in reversed(output.splitlines()):
        if line.startswith("__VENUE_PLUGINS__"):
            return list(json.loads(line.removeprefix("__VENUE_PLUGINS__")).values())
    raise RuntimeError("Target preparation returned no plugin result")


def parse_execution_checkout(output: str) -> str:
    """Read the existing Worktrees `resolve --json --dry-run` launch contract."""
    data = json.loads(output)
    if not isinstance(data, dict):
        raise ValueError("Venue worktree resolver returned a non-object execution plan")
    launch = data.get("launch", data)
    if not isinstance(launch, dict):
        raise ValueError("Venue worktree resolver returned a non-object launch plan")
    path = launch.get("work_dir")
    if launch.get("action") != "exec" or not launch.get("worktree_id") or not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Venue worktree resolver did not return a local execution checkout")
    return path

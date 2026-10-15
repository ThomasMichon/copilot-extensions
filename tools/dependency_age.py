"""Audit dependency declarations, not installer selections, against public age metadata.

Every Python lower bound/pin must name a seasoned release, and the complete
specifier must admit a seasoned release. Unbounded declarations require that
same existence proof. Markers are intentionally not evaluated on the CI host:
dependencies for other platforms and optional extras are still declarations.
Repository path sources are exempt only after contained path/name attribution;
their external closure is audited through the canonical manifest inventory.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import url2pathname

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from seasoned_public_feed import DEFAULT_MIN_AGE_DAYS, _default_fetch

MIN_AGE_SECONDS = DEFAULT_MIN_AGE_DAYS * 86400


class DeclarationError(ValueError):
    """A declaration cannot prove its age or source attribution."""


@dataclass(frozen=True)
class Declaration:
    path: Path
    section: str
    requirement: str
    ecosystem: str = "python"


def manifests(root: Path) -> list[Path]:
    """Canonical projects, including standalone/server projects, not vendor copies."""
    paths = set(root.glob("*/pyproject.toml"))
    paths.update(root.glob("libs/*/pyproject.toml"))
    for directory in ("plugins", "services"):
        paths.update(
            p for p in (root / directory).rglob("pyproject.toml")
            if not {"libs", ".venv", "node_modules", "__pycache__"} & set(p.relative_to(root).parts)
        )
    return sorted(paths)


def _toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def contained_path(path: Path, root: Path) -> Path:
    """Require real repository entries, not escaping/repointed links or junctions."""
    lexical = Path(os.path.abspath(path))
    if not lexical.is_relative_to(root):
        raise DeclarationError("source path escapes the repository")
    current = lexical
    while current != root:
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise DeclarationError("repository source path traverses a symlink or junction")
        current = current.parent
    resolved = lexical.resolve()
    if not resolved.is_relative_to(root):
        raise DeclarationError("resolved source path escapes the repository")
    return resolved


def _python_declarations(path: Path, data: dict) -> list[Declaration]:
    project = data.get("project", {})
    uv = data.get("tool", {}).get("uv", {})
    if not isinstance(uv, dict):
        raise DeclarationError("native UV settings must be a table")
    if set(project.get("dynamic", [])) & {"dependencies", "optional-dependencies"}:
        raise DeclarationError(f"{path.name}: dynamic dependencies cannot be audited")
    sections = {
        "build-system.requires": data.get("build-system", {}).get("requires", []),
        "project.dependencies": project.get("dependencies", []),
        "tool.uv.dev-dependencies": uv.get("dev-dependencies", []),
    }
    for key in ("constraint-dependencies", "override-dependencies", "build-constraint-dependencies"):
        sections[f"tool.uv.{key}"] = uv.get(key, [])
    extra_build = uv.get("extra-build-dependencies", {})
    if not isinstance(extra_build, dict):
        raise DeclarationError("extra build dependencies must be a table")
    for target, values in extra_build.items():
        if not isinstance(values, list):
            raise DeclarationError("extra build dependencies must be lists")
        requirements = [target]
        for value in values:
            if isinstance(value, dict):
                if set(value) - {"requirement", "match-runtime"} or not isinstance(value.get("requirement"), str):
                    raise DeclarationError("unsupported extra build dependency record")
                if "match-runtime" in value and not isinstance(value["match-runtime"], bool):
                    raise DeclarationError("extra build match-runtime must be boolean")
                value = value["requirement"]
            requirements.append(value)
        sections[f"tool.uv.extra-build-dependencies.{target}"] = requirements
    metadata = uv.get("dependency-metadata", [])
    if not isinstance(metadata, list):
        raise DeclarationError("dependency metadata must be an array of tables")
    for index, entry in enumerate(metadata):
        if (
            not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
            or set(entry) - {"name", "version", "requires-dist", "requires-python", "provides-extras"}
        ):
            raise DeclarationError("unsupported dependency metadata record")
        target = entry["name"]
        if "version" in entry:
            if not isinstance(entry["version"], str):
                raise DeclarationError("dependency metadata version must be a literal PEP 440 version")
            try:
                version = Version(entry["version"])
            except InvalidVersion as exc:
                raise DeclarationError("dependency metadata version must be a literal PEP 440 version") from exc
            target += "==" + str(version)
        sections[f"tool.uv.dependency-metadata.{index}.target"] = [target]
        sections[f"tool.uv.dependency-metadata.{index}.requires-dist"] = entry.get("requires-dist", [])
    sections.update({
        f"project.optional-dependencies.{name}": values
        for name, values in project.get("optional-dependencies", {}).items()
    })
    sections.update({
        f"dependency-groups.{name}": values
        for name, values in data.get("dependency-groups", {}).items()
    })
    declarations = []
    groups = data.get("dependency-groups", {})
    for section, values in sections.items():
        if not isinstance(values, list):
            raise DeclarationError(f"{section}: expected a dependency list")
        for value in values:
            if section.startswith("dependency-groups.") and isinstance(value, dict) and set(value) == {"include-group"}:
                if value["include-group"] not in groups:
                    raise DeclarationError(f"{section}: unknown included dependency group")
                continue  # Every group is independently traversed above.
            if not isinstance(value, str):
                raise DeclarationError(f"{section}: unsupported dependency declaration")
            declarations.append(Declaration(path, section, value))
    return declarations


def _requirements(path: Path, root: Path, seen: set[Path]) -> list[Declaration]:
    path = contained_path(path, root)
    if path in seen:
        raise DeclarationError("requirements include escapes repository or contains a cycle")
    seen = seen | {path}
    declarations = []
    text = re.sub(r"\\\r?\n", " ", path.read_text(encoding="utf-8"))
    for line in text.splitlines():
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:-r|--requirement|-c|--constraint)\s+(.+)", line)
        if match:
            declarations.extend(_requirements(path.parent / match[1], root, seen))
            continue
        line = re.sub(r"\s+--hash=sha256:[0-9a-fA-F]{64}", "", line)
        if line.startswith("-") or re.search(r"\s--", line):
            raise DeclarationError("unsupported requirements option; age cannot be established")
        declarations.append(Declaration(path, "requirements", line))
    return declarations


def inventory(root: Path) -> tuple[list[Declaration], dict[Path, dict]]:
    """Collect every declared category without platform/extra filtering."""
    root = root.resolve()
    paths = [contained_path(p, root) for p in manifests(root)]
    projects = {p: _toml(p) for p in paths}
    declarations = [
        declaration for path, data in projects.items()
        for declaration in _python_declarations(path, data)
    ]
    for path in sorted(root.glob("tools/*requirements*.txt")):
        declarations.extend(_requirements(path, root, set()))
    npm_paths = set(root.glob("*/package.json"))
    # Action packages are canonical CI projects; other hidden configuration is not.
    for directory in ("plugins", "services", "worktree-manager", "tools", ".github/actions"):
        npm_paths.update(
            p for p in (root / directory).rglob("package.json")
            if not {"node_modules", ".venv", "libs"} & set(p.relative_to(root).parts)
        )
    for path in sorted(npm_paths):
        path = contained_path(path, root)
        data = json.loads(path.read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            for name, spec in data.get(section, {}).items():
                if not isinstance(spec, str):
                    raise DeclarationError("npm dependency range must be a string")
                declarations.append(Declaration(path, section, f"{name} {spec}", "npm"))
    return declarations, projects


def _literal_repo_source(node: ast.AST) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name) and node.id == "REPO":
        return ()
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 1:
        return _literal_repo_source(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
        parent = _literal_repo_source(node.left)
        return (*parent, node.right.value) if parent is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        suffixes = (node.right.body, node.right.orelse) if isinstance(node.right, ast.IfExp) else (node.right,)
        if all(isinstance(s, ast.Constant) and isinstance(s.value, str)
               and (s.value == "" or re.fullmatch(r"\[[a-zA-Z0-9_,.-]+\]", s.value)) for s in suffixes):
            return _literal_repo_source(node.left)
    return None


def _plugin_contract_sources(path: Path, root: Path) -> list[Path]:
    runner = root / "tools" / "run-plugin-tests.py"
    if not runner.is_file():
        return []
    tree = ast.parse(contained_path(runner, root).read_text(encoding="utf-8"))
    prepare = next((node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_ensure_venv"), None)
    sources = []
    for branch in ast.walk(prepare) if prepare is not None else ():
        condition = branch.test if isinstance(branch, ast.If) else None
        if (isinstance(condition, ast.Compare) and isinstance(condition.left, ast.Name)
                and condition.left.id == "name" and len(condition.ops) == 1
                and isinstance(condition.ops[0], ast.Eq) and len(condition.comparators) == 1
                and isinstance(condition.comparators[0], ast.Constant)
                and type(condition.comparators[0].value) is str
                and path.parent == root / "plugins" / condition.comparators[0].value):
            for statement in branch.body:
                call = statement.value if isinstance(statement, ast.Expr) else None
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name) and call.func.value.id == "cmd"
                        and call.func.attr == "extend" and len(call.args) == 1
                        and isinstance(call.args[0], ast.List)):
                    values = call.args[0].elts
                    if len(values) == 2 and isinstance(values[0], ast.Constant) and values[0].value == "-e":
                        parts = _literal_repo_source(values[1])
                        if parts is not None:
                            sources.append(root.joinpath(*parts) / "pyproject.toml")
    return sources


def contract_source(req: Requirement, path: Path, projects: dict[Path, dict], root: Path) -> Path | None:
    """Attribute existing first-party source installs without changing UV packaging.

    Test runners explicitly supply canonical path sources where published
    metadata intentionally has no UV pointer. Extract those literal
    paths from the exact consumer's prepare branch, never a package-name
    whitelist. The nested engine's installers explicitly preinstall its parent
    package on both platforms. Canonical inventory supplies each external
    closure; absence, replacement or drift of this evidence revokes exemption.
    """
    consumer = path.parent.relative_to(root).as_posix()
    runner = root / "tools" / "run-standalone-tests.py"
    registry = root / "tools" / "standalone_consumers.py"
    sources = _plugin_contract_sources(path, root)
    if runner.is_file() and registry.is_file():
        registry_tree = ast.parse(contained_path(registry, root).read_text(encoding="utf-8"))
        consumers = next((
            ast.literal_eval(node.value) for node in registry_tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "STANDALONE_CONSUMERS" for target in node.targets)
        ), ())
        if consumer in consumers:
            tree = ast.parse(contained_path(runner, root).read_text(encoding="utf-8"))
            prepare = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "prepare"), None)
            for branch in ast.walk(prepare) if prepare is not None else ():
                condition = branch.test if isinstance(branch, ast.If) else None
                if (
                    isinstance(condition, ast.Compare)
                    and isinstance(condition.left, ast.Name) and condition.left.id == "component"
                    and len(condition.ops) == 1 and isinstance(condition.ops[0], ast.Eq)
                    and len(condition.comparators) == 1
                    and isinstance(condition.comparators[0], ast.Constant)
                    and condition.comparators[0].value == consumer
                ):
                    for assignment in branch.body:
                        if (
                            isinstance(assignment, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "sources" for t in assignment.targets)
                            and isinstance(assignment.value, ast.List)
                        ):
                            for element in assignment.value.elts:
                                parts = _literal_repo_source(element)
                                if parts is not None:
                                    sources.append(root.joinpath(*parts) / "pyproject.toml")
    parent = path.parent.parent / "pyproject.toml"
    if path.parent.name == "server" and parent in projects:
        scripts = path.parent.parent / "scripts"
        evidence = True
        for filename, interpreter, base, child in (
            ("install.sh", '"$ENGINE_VENV_PYTHON"', '"$PLUGIN_DIR"', '"$PLUGIN_DIR/server"'),
            ("install.ps1", "$EngineVenvPython", '"$PluginDir"', '"$PluginDir\\server"'),
        ):
            script = scripts / filename
            if not script.is_file():
                evidence = False
                break
            lines = [
                line for line in contained_path(script, root).read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            ]
            if not any(interpreter in line and base in line and "pip" in line.lower() for line in lines):
                evidence = False
            if not any(child in line for line in lines):
                evidence = False
        if evidence:
            sources.append(parent)
    for source in sources:
        source = contained_path(source, root)
        if not source.is_file():
            raise DeclarationError("source contract points to a missing project")
        source_data = projects.get(source) or _toml(source)
        if canonicalize_name(source_data.get("project", {}).get("name", "")) != canonicalize_name(req.name):
            continue
        if source not in projects:
            raise DeclarationError("source contract does not point to an inventoried canonical project")
        return source
    return None


def local_source(req: Requirement, path: Path, projects: dict[Path, dict], root: Path) -> bool:
    """Only attributable repository path sources/self-extra references are exempt."""
    data = projects[path]
    own_name = data.get("project", {}).get("name", "")
    if req.url:
        parsed = urlparse(req.url)
        if parsed.query or parsed.fragment or parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
            raise DeclarationError("direct URL dependencies lack public release-age evidence")
        target = Path(url2pathname(parsed.path)) if parsed.scheme == "file" else path.parent / unquote(parsed.path)
        return attributed_project(req, target, projects, root)
    if canonicalize_name(req.name) == canonicalize_name(own_name):
        return True
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    source = next((v for k, v in sources.items() if canonicalize_name(k) == canonicalize_name(req.name)), None)
    if source is None:
        return contract_source(req, path, projects, root) is not None
    if not isinstance(source, dict) or set(source) - {"path", "editable"} or not isinstance(source.get("path"), str):
        raise DeclarationError("non-path source override lacks public release-age evidence")
    return attributed_project(req, path.parent / source["path"], projects, root)


def attributed_project(req: Requirement, target: Path, projects: dict[Path, dict], root: Path) -> bool:
    """Prove a local path/URL's identity and canonical external closure."""
    target = contained_path(target, root)
    manifest = contained_path(target / "pyproject.toml", root)
    if not manifest.is_file():
        raise DeclarationError("path source is not a contained repository project")
    target_data = _toml(manifest)
    if canonicalize_name(target_data.get("project", {}).get("name", "")) != canonicalize_name(req.name):
        raise DeclarationError("path source project name does not match dependency")
    if manifest not in projects:
        # Existing vendored copies must resolve back to a canonical project,
        # including identical dependency metadata, not merely a familiar name.
        candidates = [(p, d) for p, d in projects.items() if d.get("project", {}).get("name") == target_data.get("project", {}).get("name")]
        parts = manifest.relative_to(root).parts
        vendor_layout = (
            (len(parts) == 5 and parts[0] == "plugins" and parts[2] == "libs")
            or (len(parts) == 4 and parts[1] == "libs" and parts[0] not in {"plugins", "services"})
        )
        if (
            len(candidates) != 1 or not vendor_layout
            or candidates[0][0].parent.parent != root / "libs"
            or target.name != candidates[0][0].parent.name
            or _python_declarations(manifest, candidates[0][1]) != _python_declarations(manifest, target_data)
        ):
            raise DeclarationError("path source has no identical canonical dependency closure")
    return True


def uv_cutoff(value: object, now: datetime) -> datetime:
    """Conservatively accept explicit UTC/date cutoffs or simple UV durations."""
    if not isinstance(value, str) or not value.strip():
        raise DeclarationError("native UV cutoff is missing, disabled or invalid")
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        value += "T00:00:00+00:00"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        timestamp = None
    if timestamp is not None:
        if timestamp.tzinfo is None:
            raise DeclarationError("native UV absolute cutoff must have a timezone")
        return timestamp
    units = {
        "week": 604800, "w": 604800, "day": 86400, "d": 86400,
        "hour": 3600, "h": 3600, "minute": 60, "min": 60, "m": 60,
        "second": 1, "sec": 1, "s": 1,
    }
    tokens = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(weeks?|days?|hours?|minutes?|mins?|seconds?|secs?|[wdhms])", value))
    end, total = 0, Decimal(0)
    for token in tokens:
        if value[end:token.start()].strip():
            raise DeclarationError("unsupported native UV duration")
        unit = token[2]
        unit = unit[:-1] if unit.endswith("s") and len(unit) > 1 else unit
        total += Decimal(token[1]) * units[unit]
        end = token.end()
    if not tokens or value[end:].strip():
        raise DeclarationError("unsupported native UV duration")
    try:
        return now - timedelta(seconds=float(total))
    except OverflowError as exc:
        raise DeclarationError("native UV duration is out of range") from exc


def check_uv_policy(data: dict, now: datetime) -> None:
    """Reject missing/weaker defaults, disabled package policies and index bypasses."""
    uv = data.get("tool", {}).get("uv", {})
    if not isinstance(uv, dict):
        raise DeclarationError("native UV settings must be a table")
    global_cutoff = uv_cutoff(uv.get("exclude-newer"), now)
    if global_cutoff > now - timedelta(seconds=MIN_AGE_SECONDS):
        raise DeclarationError("native UV default must exclude uploads newer than 7 days")

    def overrides(settings: dict, reference: datetime):
        if "exclude-newer" in settings and uv_cutoff(settings["exclude-newer"], now) > reference:
            raise DeclarationError("native UV override weakens the global cutoff")
        packages = settings.get("exclude-newer-package", {})
        if not isinstance(packages, dict):
            raise DeclarationError("native UV package overrides must be a table")
        for value in packages.values():
            if uv_cutoff(value, now) > reference:
                raise DeclarationError("native UV package override weakens the global cutoff")

    overrides(uv, global_cutoff)
    pip = uv.get("pip", {})
    if not isinstance(pip, dict):
        raise DeclarationError("native UV pip settings must be a table")
    pip_cutoff = uv_cutoff(pip["exclude-newer"], now) if "exclude-newer" in pip else global_cutoff
    overrides(pip, global_cutoff)
    overrides(pip, pip_cutoff)
    for value in uv.get("exclude-newer-package", {}).values():
        if uv_cutoff(value, now) > pip_cutoff:
            raise DeclarationError("native UV package override weakens the pip cutoff")
    indexes = uv.get("index", [])
    if not isinstance(indexes, list) or any(not isinstance(index, dict) for index in indexes):
        raise DeclarationError("native UV indexes must be an array of tables")
    if any("exclude-newer" in index or "exclude-newer-package" in index for index in indexes):
        # UV's index-specific cutoff may admit files with unknown upload time,
        # even when nominally stricter. A stronger timestamp is not sufficient.
        raise DeclarationError("index-specific UV cutoff overrides are unsupported")


def aged(timestamp: object, now: datetime) -> bool:
    """Exactly seven days is eligible; missing, naive and future dates are not."""
    if not isinstance(timestamp, str):
        return False
    try:
        uploaded = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return uploaded.tzinfo is not None and (now - uploaded).total_seconds() >= MIN_AGE_SECONDS
    except (ValueError, OverflowError):
        return False


def python_release_eligible(files: object, now: datetime) -> bool:
    return isinstance(files, list) and bool(files) and all(
        isinstance(file, dict) and file.get("yanked") is False
        and aged(file.get("upload_time_iso_8601"), now)
        for file in files
    )


def check_python(req: Requirement, metadata: dict, now: datetime) -> None:
    """Check literal floors/pins plus satisfiability using standard PEP 440."""
    releases = metadata.get("releases")
    if not isinstance(releases, dict):
        raise DeclarationError("public PyPI metadata has no releases object")
    versions = {}
    for raw, files in releases.items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if not (version.is_prerelease or version.is_devrelease or version.local):
            versions.setdefault(version, []).extend(files if isinstance(files, list) else [{}])
    for spec in req.specifier:
        if spec.operator in {">=", ">", "~=", "==", "==="}:
            if spec.version.endswith(".*"):
                # A wildcard has no literal release. Its least matching public
                # release is the declaration's floor, not a freshly chosen latest.
                matches = [v for v in versions if spec.contains(v)]
                literal = min(matches) if matches else None
            else:
                literal = Version(spec.version)
            if literal not in versions or not python_release_eligible(versions[literal], now):
                raise DeclarationError(f"literal floor/pin {spec} is missing, unseasoned or yanked")
    if not any(req.specifier.contains(v) and python_release_eligible(files, now) for v, files in versions.items()):
        raise DeclarationError("specifier admits no seasoned stable, non-yanked public release")


def npm_versions(spec: str, versions: list[str]) -> tuple[list[str], list[str]]:
    """Use npm's bundled semver, not an approximate handwritten range parser."""
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise DeclarationError("node and npm are required to validate npm declarations")
    script = """
const fs = require('fs'), path = require('path');
let semver;
try { semver = require('semver'); }
catch (_) {
  const base = path.dirname(fs.realpathSync(process.argv[1]));
  for (const candidate of [path.join(base, 'node_modules', 'npm', 'node_modules', 'semver'),
      path.join(base, '..', 'node_modules', 'semver')]) {
    try { semver = require(candidate); break; } catch (_) {}
  }
  if (!semver) throw new Error('npm bundled semver unavailable');
}
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const range = new semver.Range(input.spec);
const floors = range.set.map(set => semver.minVersion(new semver.Range(
  set.map(c => c.value).join(' '))).version);
console.log(JSON.stringify({floors, matches: input.versions.filter(
  v => semver.valid(v) && !semver.prerelease(v) && semver.satisfies(v, range))}));
"""
    try:
        result = subprocess.run(
            [node, "-e", script, npm], input=json.dumps({"spec": spec, "versions": versions}),
            text=True, capture_output=True, timeout=15, check=True,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        data = json.loads(result.stdout)
        return data["floors"], data["matches"]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        raise DeclarationError("npm semver could not validate the declared range") from exc


def check_npm(spec: str, metadata: dict, now: datetime, semver=npm_versions) -> None:
    versions, times = metadata.get("versions"), metadata.get("time")
    if not isinstance(versions, dict) or not isinstance(times, dict):
        raise DeclarationError("public npm metadata lacks versions or release timestamps")
    floors, matches = semver(spec, list(versions))

    def eligible(version):
        record = versions.get(version)
        return isinstance(record, dict) and record.get("yanked", False) is False and aged(times.get(version), now)

    # Each OR branch is a separate declaration; a seasoned branch cannot hide
    # an explicitly new floor in another branch.
    for floor in floors:
        if floor != "0.0.0" and not eligible(floor):
            raise DeclarationError("npm literal floor/pin is missing, unseasoned or yanked")
    if not any(eligible(version) for version in matches):
        raise DeclarationError("npm range admits no seasoned public release")


def npm_command() -> list[str]:
    """Reuse the repository's Node/attributable npm-cli.js selector, without installing."""
    root = Path(__file__).resolve().parent.parent
    source = contained_path(root / "worktree-manager" / "scripts" / "picker-snapshot" / "install.py", root)
    try:
        spec = importlib.util.spec_from_file_location("declaration_npm_selector", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.npm_command()
    except (OSError, ImportError, AttributeError, RuntimeError, ValueError) as exc:
        raise DeclarationError("cannot select node and npm's attributable CLI") from exc


def npm_public_metadata(name: str) -> bytes:
    """Bounded read-only public metadata query using npm's existing trusted CA.

    Registry selection is per-query only, never a persisted configuration change
    or an installation command. Scoped registry configuration is also explicitly
    shadowed for this query, so private-feed metadata cannot substitute for public.
    """
    if os.environ.get("NODE_TLS_REJECT_UNAUTHORIZED") == "0":
        raise DeclarationError("secure public npm metadata transport unavailable")
    command = npm_command()
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # Modern Node adds the OS-owned trust store; older Node ignores this
    # environment switch and retains its bundled roots. npm's configured
    # CA/cafile remains untouched, as does TLS verification.
    environment = dict(os.environ, NODE_USE_SYSTEM_CA="1")

    def run(*args, timeout=15):
        return subprocess.run([*command, *args], capture_output=True, text=True,
                              timeout=timeout, check=True, env=environment, **flags).stdout.strip()

    try:
        if json.loads(run("config", "get", "strict-ssl", "--json")) is not True:
            raise DeclarationError("npm TLS verification must remain enabled")
        registry = ["--registry=https://registry.npmjs.org/"]
        if name.startswith("@"):
            registry.append("--" + name.split("/")[0] + ":registry=https://registry.npmjs.org/")
        data = json.loads(run("view", name, "time", "versions", "--json",
                              "--prefer-online", "--fetch-retries=0", "--fetch-timeout=30000",
                              *registry, timeout=40))
        if not isinstance(data, dict):
            raise DeclarationError("npm metadata must be an object")
        versions = data.get("versions")
        if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
            raise DeclarationError("npm metadata has no published versions list")
        return json.dumps({"time": data.get("time"), "versions": {v: {} for v in versions}}).encode()
    except (subprocess.SubprocessError, OSError, ValueError, TypeError) as exc:
        if isinstance(exc, DeclarationError):
            raise
        if isinstance(exc, subprocess.CalledProcessError) and "ERR_SSL_SSL/TLS_ALERT_HANDSHAKE_FAILURE" in (exc.stderr or ""):
            raise DeclarationError("public npm TLS handshake failed with certificate validation enabled") from exc
        raise DeclarationError("secure public npm metadata transport unavailable") from exc


def fetch_release_metadata(url: str) -> bytes:
    """Use npm's CA-aware CLI directly; avoid known-bad urllib npm TLS retries."""
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc == "registry.npmjs.org":
        return npm_public_metadata(unquote(parsed.path.lstrip("/")))
    return _default_fetch(url)


def audit(root: Path, *, fetch=fetch_release_metadata, now: datetime | None = None, semver=npm_versions, ecosystem: str = "all") -> tuple[int, list[str]]:
    """Return audited declaration count and fail-closed, public-safe findings."""
    root = root.resolve()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise DeclarationError("audit time must be timezone-aware")
    declarations, projects = inventory(root)
    if ecosystem not in {"all", "python", "npm"}:
        raise DeclarationError("unknown ecosystem selector")
    if ecosystem != "all":
        declarations = [d for d in declarations if d.ecosystem == ecosystem]
    cache, errors = {}, []
    if ecosystem != "npm":
        for path, data in projects.items():
            try:
                check_uv_policy(data, now)
            except (ValueError, TypeError) as exc:
                reason = str(exc) if isinstance(exc, DeclarationError) else "invalid native UV policy"
                errors.append(f"{path.relative_to(root)} [tool.uv]: {reason}")
    for declaration in declarations:
        name = "dependency"
        try:
            if declaration.ecosystem == "python":
                req = Requirement(declaration.requirement)
                name = canonicalize_name(req.name)
                if declaration.path in projects and local_source(req, declaration.path, projects, root):
                    continue
                if req.url:
                    raise DeclarationError("direct URL dependencies lack public release-age evidence")
                url = f"https://pypi.org/pypi/{name}/json"
            else:
                name, spec = declaration.requirement.split(" ", 1)
                url = "https://registry.npmjs.org/" + quote(name, safe="")
            if url not in cache:
                try:
                    cache[url] = json.loads(fetch(url))
                    if not isinstance(cache[url], dict):
                        raise ValueError("expected object")
                except DeclarationError as exc:
                    cache[url] = exc
                except Exception:
                    cache[url] = DeclarationError("public release metadata unavailable or malformed")
            if isinstance(cache[url], DeclarationError):
                raise cache[url]
            if declaration.ecosystem == "python":
                check_python(req, cache[url], now)
            else:
                check_npm(spec, cache[url], now, semver)
        except (ValueError, TypeError) as exc:
            # No URLs, credentials, raw HTTP exceptions or dependency URL text.
            reason = str(exc) if isinstance(exc, DeclarationError) else "invalid dependency declaration"
            errors.append(f"{declaration.path.relative_to(root)} [{declaration.section}]: {name}: {reason}")
    return len(declarations), errors

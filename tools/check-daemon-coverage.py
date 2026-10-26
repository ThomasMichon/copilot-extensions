"""Check the callable-scoped lifecycle census, including non-plugin services."""

from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "tools" / "daemon-coverage.json"
CLASSIFICATIONS = {
    "resident", "supervised-service-child", "finite-worker",
    "caller-contained", "venue-agent", "native",
}
APIS = {
    "Popen", "create_subprocess_exec", "create_subprocess_shell", "spawn_daemon",
    "spawn_keeper", "spawn_instance", "spawn_finite_worker", "spawn_dispatch_daemon",
    "fork", "serve_forever", "run_app", "start_server",
    "uvicorn.run", "HTTPServer", "ThreadingHTTPServer",
    "posix_spawn", "posix_spawnp",
}
SKIP = {
    "tests", "libs", "__pycache__", "node_modules", ".venv", ".test-venvs",
    ".venv-test", "build", "dist", "obj", ".git",
    ".test-runs", ".pytest_cache",
}
NATIVE = re.compile(
    r"(?<![\w-])(Start-Process|New-ScheduledTaskAction|Start-NativeDaemon|"
    r"start_native_daemon|nohup)(?![\w-])|^\s*(ExecStart|ENTRYPOINT)\s*(?:=|\[)"
)


def source_files(root: Path):
    import os

    for owner in ("plugins", "worktree-manager", "fleet-manager", "services", "tools"):
        for directory, dirs, files in os.walk(root / owner):
            dirs[:] = sorted(name for name in dirs if name not in SKIP)
            for name in sorted(files):
                if name.startswith(("test_", "versioned_runtime", "installer_engine")):
                    continue
                path = Path(directory) / name
                if path.suffix in {".py", ".ps1", ".sh", ".js", ".mjs", ".ts"} or name.startswith("Dockerfile"):
                    yield path


def python_sites(text: str, filename: str, *, finite: bool = False):
    tree = ast.parse(text, filename=filename)
    aliases = {}

    def api_name(value):
        if isinstance(value, ast.Attribute):
            if finite and isinstance(value.value, ast.Name) and value.value.id == "subprocess" and value.attr == "run":
                return "subprocess.run"
            if isinstance(value.value, ast.Name) and value.value.id == "uvicorn" and value.attr == "run":
                return "uvicorn.run"
            return value.attr
        if isinstance(value, ast.Name):
            return aliases.get(value.id, value.id)
        if (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "getattr" and len(value.args) >= 2
            and isinstance(value.args[1], ast.Constant) and isinstance(value.args[1].value, str)
        ):
            return value.args[1].value
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module == "uvicorn" and alias.name == "run":
                    aliases[alias.asname or alias.name] = "uvicorn.run"
                elif alias.name in APIS:
                    aliases[alias.asname or alias.name] = alias.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            name = api_name(node.value)
            if name in APIS:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = name
    sites = Counter()
    functions = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            functions[".".join(self.scope)] = node
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_Call(self, node):
            name = api_name(node.func)
            if name in APIS or finite and name == "subprocess.run":
                sites[(".".join(self.scope) or "<module>", name)] += 1
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites, functions


def native_sites(text: str, *, docker: bool = False):
    sites = Counter()
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("#", "//", "*")):
            continue
        for match in NATIVE.finditer(line):
            sites[("<script>", match.group(1) or match.group(2))] += 1
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    if "ProcessStartInfo" in code:
        count = len(re.findall(r"(?:\]::|\$\w+\.)Start\s*\(", code))
        if count:
            sites[("<script>", "Process.Start")] = count
    count = len(re.findall(
        r"\bInvoke-CimMethod\b[^\n]*\bWin32_Process\b[^\n]*-MethodName\s+['\"]?Create\b",
        code, flags=re.IGNORECASE,
    ))
    if count:
        sites[("<script>", "Win32_Process.Create")] = count
    if docker:
        healthcheck = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("HEALTHCHECK"):
                healthcheck = stripped.endswith("\\")
            elif re.match(r"CMD\s+(?:\[|\S)", stripped):
                sites[("<script>", "HEALTHCHECK" if healthcheck else "CMD")] += 1
                healthcheck = False
            elif stripped and not stripped.startswith("#"):
                healthcheck = healthcheck and stripped.endswith("\\")
    return sites


def javascript_sites(text: str):
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)
    names = {"spawn", "fork", "createServer", "listen"}
    names.update(re.findall(r"\bspawn\s+as\s+(\w+)", text))
    sites = Counter()
    for name in names:
        count = len(re.findall(rf"\b{name}\s*\(", text))
        if count:
            sites[("<script>", name if name in {"fork", "createServer", "listen"} else "spawn")] += count
    return sites


def native_executable_text(text: str) -> str:
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    lines = []
    delimiter = None
    for line in text.splitlines():
        if delimiter is not None:
            if line.startswith(delimiter):
                delimiter = None
            continue
        if re.search(r"@(['\"])\s*$", line):
            delimiter = "'@" if line.rstrip().endswith("@'") else '"@'
            continue
        if not line.lstrip().startswith("#"):
            lines.append(line)
    return "\n".join(lines)


def admitted(node: ast.AST, service: str, text: str, *, production: bool = False) -> bool:
    """Only executable shared admission counts, never a comment or detach flag."""
    def admission_import(statement):
        if isinstance(statement, ast.Try):
            if (
                len(statement.body) != 1 or len(statement.handlers) != 1
                or statement.orelse or statement.finalbody
            ):
                return None
            handler = statement.handlers[0]
            if (
                not isinstance(handler.type, ast.Name)
                or handler.type.id not in {"ImportError", "ModuleNotFoundError"}
                or len(handler.body) != 1 or not isinstance(handler.body[0], ast.Raise)
            ):
                return None
            cause = handler.body[0].cause
            if cause is not None and not (
                isinstance(cause, ast.Constant) and cause.value is None
            ):
                return None
            error = handler.body[0].exc
            if error is not None and not (
                isinstance(error, ast.Call) and isinstance(error.func, ast.Name)
                and error.func.id.endswith("Error") and not error.keywords
                and all(isinstance(arg, ast.Constant) for arg in error.args)
            ):
                return None
            statement = statement.body[0]
        return statement if (
            isinstance(statement, ast.ImportFrom)
            and statement.module == "agent_procutil.daemon_instances"
        ) else None

    imports = {}
    module = ast.parse(text)
    for item in [*module.body, *getattr(node, "body", [])]:
        imported = admission_import(item)
        if imported is not None:
            imports.update({alias.asname or alias.name: alias.name for alias in imported.names})
    for item in module.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            imports.pop(item.name, None)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    imports.pop(target.id, None)

    def has_source(call):
        return any(
            keyword.arg == "source" and ast.unparse(keyword.value) in {
                "__file__", "Path(__file__)", "Path(__file__).resolve()",
                "Path(__file__).absolute()",
            }
            for keyword in call.keywords
        )

    def has_purpose(call):
        values = [keyword.value for keyword in call.keywords if keyword.arg == "purpose"]
        if production:
            return len(values) == 1 and isinstance(values[0], ast.Constant) and values[0].value == "production"
        return not values or len(values) == 1 and isinstance(values[0], ast.Constant) and values[0].value is None
    for candidate in getattr(node, "decorator_list", []):
        if (
            isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Name)
            and imports.get(candidate.func.id) == "daemon_entrypoint"
            and candidate.args and isinstance(candidate.args[0], ast.Constant)
            and candidate.args[0].value == service
            and has_source(candidate)
            and has_purpose(candidate)
        ):
            return True
    body = getattr(node, "body", [])
    body = body[1:] if body and isinstance(body[0], ast.Expr) and (
        isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)
    ) else body
    pure_setup = {
        "argparse.ArgumentParser", "parser.add_argument", "parser.parse_args",
        "sys.path.insert", "str", "int", "Path",
    }
    for statement in body:
        if isinstance(statement, ast.Try) and admission_import(statement) is not None:
            continue
        call = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign)) else None
        if isinstance(statement, (ast.With, ast.AsyncWith)) and len(statement.items) == 1:
            call = statement.items[0].context_expr
        if (
            isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and imports.get(call.func.id) == "admit_daemon_instance"
            and call.args and (
                isinstance(call.args[0], ast.Constant) and call.args[0].value == service
                or service.startswith("$") and ast.unparse(call.args[0]) == service[1:]
            )
            and has_source(call)
            and has_purpose(call)
        ):
            return True
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
            break
        calls = [item for item in ast.walk(statement) if isinstance(item, ast.Call)]
        if not calls or any(ast.unparse(item.func) not in pure_setup for item in calls):
            break
    return False


def verify(root: Path = ROOT, census_path: Path | None = None) -> list[str]:
    path = census_path or root / "tools" / "daemon-coverage.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        prefixes = document["prefixes"]
        def expand(key):
            prefix, tail = key.split(":", 1)
            expanded = ("" if prefixes[prefix] == "." else prefixes[prefix] + "/") + tail
            filename = Path(expanded.split("::", 1)[0])
            if filename.is_absolute() or ".." in filename.parts:
                raise ValueError("census sources must remain repository-relative")
            return expanded

        entries = []
        for classification, sites in document["sites"].items():
            for key, count in sites.items():
                entries.append({
                    "site": expand(key),
                    "classification": classification, "count": count,
                })
        entrypoints = {expand(key): service for key, service in document["entrypoints"].items()}
        context_required = {expand(key) for key in document.get("context_required", [])}
        owners = {
            expand(key): [expand(target) for target in targets]
            for key, targets in document.get("admission_owners", {}).items()
        }
        native_entries = {
            expand(key): service for key, service in document.get("native_entrypoints", {}).items()
        }
    except (OSError, ValueError, KeyError) as error:
        return [f"invalid daemon census: {error}"]
    problems = []
    actual = {}
    functions = {}
    source_text = {}
    for file in source_files(root):
        relative = file.relative_to(root).as_posix()
        text = file.read_text(encoding="utf-8-sig")
        source_text[relative] = text
        try:
            if file.suffix == ".py":
                sites, bodies = python_sites(text, relative)
                functions.update({f"{relative}::{name}": node for name, node in bodies.items()})
                source_text[relative] = text
            elif file.suffix in {".js", ".mjs", ".ts"}:
                sites = javascript_sites(text)
            else:
                sites = native_sites(text, docker=file.name.startswith("Dockerfile"))
        except SyntaxError as error:
            problems.append(f"{relative}: cannot verify source: {error}")
            continue
        actual.update({f"{relative}::{name}::{api}": count for (name, api), count in sites.items()})
    expected = {}
    for entry in entries:
        key = entry.get("site", "")
        kind = entry.get("classification")
        if key in expected or kind not in CLASSIFICATIONS:
            problems.append(f"{key}: duplicate or invalid lifecycle classification")
        expected[key] = entry
        if actual.get(key) != entry.get("count"):
            problems.append(f"{key}: census count {entry.get('count')} != actual {actual.get(key, 0)}")
        if kind in {"resident", "supervised-service-child"}:
            targets = owners.get(key.split("::", 1)[0], [])
            if not targets or any(target not in entrypoints and target not in native_entries for target in targets):
                problems.append(f"{key}: persistent launch needs an admitted startup owner")
    for key, service in entrypoints.items():
        body = functions.get(key)
        if body is None:
            problems.append(f"{key}: missing entrypoint")
        elif not admitted(
            body, service, source_text[key.split("::", 1)[0]],
            production=key not in context_required,
        ):
            problems.append(f"{key}: entrypoint lacks shared startup admission ({service})")
    for filename, service in native_entries.items():
        text = source_text.get(filename, "")
        code = native_executable_text(text)
        code = code.replace("`\n", " ")
        pattern = (
            r"(?m)^\s*\$\w+\s*=\s*Enter-DaemonInstance\s+-Service\s+"
            + re.escape("'" + service + "'")
        )
        match = re.search(pattern, code)
        invocation = code[match.start():].splitlines()[0] if match else ""
        if (
            match is None or "daemon_instances.ps1" not in code
            or not re.search(r"-Purpose\s+production\b", invocation)
            or re.search(r"(?i)\bfunction\s+Enter-DaemonInstance\b", code)
        ):
            problems.append(f"{filename}: native entrypoint lacks shared startup admission ({service})")
    for helper in document.get("native_helpers", []):
        source = root / helper["source"]
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            problems.append(f"{helper['source']}: native helper source leaves the repository")
            continue
        try:
            code = source.read_text(encoding="utf-8")
        except OSError:
            problems.append(f"{helper['source']}: missing native helper entrypoint")
            continue
        if (
            helper.get("owner") != "agent_procutil.daemon_instances"
            or helper.get("protocol") != 1
            or not re.search(r"\bint\s+" + re.escape(helper["entrypoint"]) + r"\s*\(", code)
            or any(kind not in CLASSIFICATIONS for kind in helper["commands"].values())
        ):
            problems.append(f"{helper['source']}: invalid shared native helper classification")
        commands = set(re.findall(r'strcmp\s*\(\s*argv\[1\]\s*,\s*"([^"]+)"', code))
        if commands != set(helper["commands"]):
            problems.append(f"{helper['source']}: native command census differs: {sorted(commands)}")
    for key, helper in document.get("finite_helpers", {}).items():
        filename = expand(key)
        source = root / filename
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            problems.append(f"{filename}: finite helper source leaves the repository")
            continue
        try:
            sites, _ = python_sites(source.read_text(encoding="utf-8"), filename, finite=True)
        except (OSError, SyntaxError) as error:
            problems.append(f"{filename}: cannot verify finite helper: {error}")
            continue
        commands = {name: count for (name, api), count in sites.items() if api == "subprocess.run"}
        if (
            helper.get("classification") not in {"finite-worker", "caller-contained"}
            or commands != helper.get("commands")
            or any(api != "subprocess.run" for _, api in sites)
        ):
            problems.append(f"{filename}: finite helper command census differs")
    for key in actual.keys() - expected.keys():
        problems.append(f"{key}: unclassified shipped launch/service site")
    return sorted(problems)


def main() -> int:
    problems = verify()
    if problems:
        print("\n".join(problems))
        return 1
    print("check-daemon-coverage: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

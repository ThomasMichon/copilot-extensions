"""Conservative, data-only discovery of release helper file-loading edges."""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import PurePosixPath

from promotion_unit_contract import UnitEvidenceError, relative_file

_SPEC = "importlib.util.spec_from_file_location"
_MODULE = "importlib.util.module_from_spec"
_UNSUPPORTED = {"SourceFileLoader", "SourcelessFileLoader", "load_module"}
_ACTOR_PRIVATE = {"_load_resource", "_supervise_windows"}
_SENSITIVE = _UNSUPPORTED | _ACTOR_PRIVATE | {
    "spec_from_file_location", "module_from_spec", "exec_module", "_load_module",
}
_ADAPTER = ast.parse(
    "def _load_module(path, name):\n"
    "    spec = importlib.util.spec_from_file_location(name, path)\n"
    "    module = importlib.util.module_from_spec(spec)\n"
    "    assert spec.loader is not None\n"
    "    spec.loader.exec_module(module)\n"
    "    return module\n"
).body[0]
_TOOL_ROOT = ast.parse('tool_root = (trusted_source or scratch) / "tools"').body[0]
_ACTOR_SOURCE = "libs/installer-engine/installer_engine_process.py"
_RESOURCE_ADAPTER = ast.parse(
    "def _load_resource(name, path):\n"
    "    spec = importlib.util.spec_from_file_location(name, path)\n"
    "    if spec is None or spec.loader is None:\n"
    '        raise ImportError("installer guardian resource could not be loaded")\n'
    "    module = importlib.util.module_from_spec(spec)\n"
    "    sys.modules[name] = module\n"
    "    spec.loader.exec_module(module)\n"
    "    return module\n"
).body[0]
# Product/runtime actors are not repository implementation edges. These are
# the only resource calls admitted through the exact private adapter above.
_EXTERNAL_RUNTIME_LOADS = tuple(
    ast.parse(expression, mode="eval").body for expression in (
        '_load_resource("installer_guardian_runtime", args.runtime_path)',
        '_load_resource("installer_guardian_procutil", args.procutil_path)',
    )
)
_OWNED_RUNTIME_MAIN = ast.parse(
    "_supervise_windows(args, args.command[1:])", mode="eval",
).body
_MAIN_GUARD = ast.parse('__name__ == "__main__"', mode="eval").body
_SUPERVISOR_ARGS = ast.parse("def _supervise_windows(args, argv): pass").body[0].args
_DYNAMIC_OPERATIONS = {
    "importlib.import_module", "builtins.__import__", "runpy.run_path",
    "runpy.run_module", "builtins.exec", "builtins.eval",
}
_DYNAMIC_NAMESPACES = {"importlib", "builtins", "runpy"}
_DYNAMIC_NAMESPACE_ESCAPES = {
    f"{namespace}.{attribute}" for namespace in _DYNAMIC_NAMESPACES
    for attribute in ("__dict__", "__getattribute__", "__getattr__")
}
_DIRECT_EXEC = "@direct-builtin-exec"


def _spelling(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _spelling(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _scope_nodes(scope: ast.AST):
    """Keep bindings in their lexical scope, including definition-time inputs."""
    yield scope
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield child
            for field, value in ast.iter_fields(child):
                if field == "body":
                    continue
                if isinstance(value, ast.AST):
                    yield from ast.walk(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, ast.AST):
                            yield from ast.walk(item)
        else:
            yield from _scope_nodes(child)


class _Discovery:
    def __init__(self, tree: ast.Module, relative: str):
        self.tree = tree
        self.relative = relative_file(relative)
        if ":" in relative:
            raise UnitEvidenceError(f"noncanonical release implementation path: {relative}")
        self.parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        self.result: set[str] = set()
        self.handled: set[ast.Call] = set()
        self.actor_adapter: ast.FunctionDef | None = None
        self.actor_supervisor: ast.FunctionDef | None = None
        self.actor_calls: set[ast.Call] = set()

    def fail(self, node: ast.AST, reason: str):
        raise UnitEvidenceError(
            f"unsupported file-loading release implementation: {self.relative}:"
            f"{getattr(node, 'lineno', 0)}: {reason}"
        )

    def runtime_boundary(self) -> None:
        """Admit only the existing owned Windows product/runtime actor seam."""
        if self.relative != _ACTOR_SOURCE:
            return
        definitions = [
            node for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in _ACTOR_PRIVATE
        ]
        if not definitions:
            return
        adapters = [node for node in definitions if node.name == "_load_resource"]
        supervisors = [node for node in definitions if node.name == "_supervise_windows"]
        if (
            len(adapters) != 1 or len(supervisors) != 1
            or not all(
                isinstance(node, ast.FunctionDef) and self.parents[node] is self.tree
                and not node.decorator_list for node in definitions
            )
        ):
            self.fail(self.tree, "private runtime boundary requires unique top-level functions")
        adapter, supervisor = adapters[0], supervisors[0]
        if (
            ast.dump(ast.Module(body=adapter.body, type_ignores=[]))
            != ast.dump(ast.Module(body=_RESOURCE_ADAPTER.body, type_ignores=[]))
            or ast.dump(adapter.args) != ast.dump(_RESOURCE_ADAPTER.args)
            or ast.dump(supervisor.args) != ast.dump(_SUPERVISOR_ARGS)
        ):
            self.fail(adapter, "external runtime adapter body or signature changed")
        expected = Counter(ast.dump(call) for call in _EXTERNAL_RUNTIME_LOADS)
        observed: Counter[str] = Counter()
        main_calls = 0
        for node in ast.walk(self.tree):
            if (
                isinstance(node, ast.Name) and node.id in _ACTOR_PRIVATE
                and isinstance(node.ctx, (ast.Store, ast.Del))
                or isinstance(node, ast.arg) and node.arg in _ACTOR_PRIVATE
                or isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
                and node.name in _ACTOR_PRIVATE
                or isinstance(node, ast.MatchMapping) and node.rest in _ACTOR_PRIVATE
                or isinstance(node, (ast.Global, ast.Nonlocal))
                and _ACTOR_PRIVATE.intersection(node.names)
            ):
                self.fail(node, "private runtime boundary is rebound")
            if not (
                isinstance(node, ast.Name) and node.id in _ACTOR_PRIVATE
                and isinstance(node.ctx, ast.Load)
            ):
                continue
            call = self.parents.get(node)
            if not isinstance(call, ast.Call) or call.func is not node:
                self.fail(node, "private runtime boundary cannot be aliased")
            ancestors = []
            current = self.parents.get(call)
            while current is not None:
                ancestors.append(current)
                current = self.parents.get(current)
            lexical = next((
                parent for parent in ancestors
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
            ), self.tree)
            if node.id == "_load_resource":
                key = ast.dump(call)
                if lexical is not supervisor or key not in expected:
                    self.fail(call, "unrecognized external runtime resource call")
                observed[key] += 1
            elif (
                lexical is not self.tree or ast.dump(call) != ast.dump(_OWNED_RUNTIME_MAIN)
                or not any(
                    isinstance(parent, ast.If) and self.parents.get(parent) is self.tree
                    and ast.dump(parent.test) == ast.dump(_MAIN_GUARD)
                    and (call if index == 0 else ancestors[index - 1]) in parent.body
                    for index, parent in enumerate(ancestors)
                )
            ):
                self.fail(call, "private runtime supervisor is callable only from owned main")
            else:
                main_calls += 1
            self.actor_calls.add(call)
        if observed != expected or main_calls != 1:
            self.fail(self.tree, "external runtime boundary requires exactly two resource loads and owned main")
        self.actor_adapter = adapter
        self.actor_supervisor = supervisor

    def dominates(self, binding: ast.AST, use: ast.AST) -> bool:
        parent = self.parents.get(binding)
        if parent is None:
            return False
        for _, value in ast.iter_fields(parent):
            if isinstance(value, list) and binding in value:
                current = use
                while current in self.parents and self.parents[current] is not parent:
                    current = self.parents[current]
                return current in value and value.index(binding) < value.index(current)
        return False

    def binding(self, call: ast.Call, writes: Counter) -> tuple[str, ast.AST]:
        parent = self.parents.get(call)
        if isinstance(parent, ast.Assign) and parent.value is call and len(parent.targets) == 1:
            target = parent.targets[0]
        elif isinstance(parent, ast.AnnAssign) and parent.value is call:
            target = parent.target
        else:
            self.fail(call, "loader result requires a direct single-name binding")
        if not isinstance(target, ast.Name) or writes[target.id] != 1:
            self.fail(call, "loader binding is reassigned or unsupported")
        return target.id, parent

    def path(self, node: ast.AST, aliases: dict[str, str], tool_root: bool) -> PurePosixPath:
        if isinstance(node, ast.Name) and node.id == "tool_root" and tool_root:
            return PurePosixPath("tools")
        if (
            isinstance(node, ast.Call) and self.resolve(node.func, aliases) == "pathlib.Path"
            and len(node.args) == 1 and not node.keywords
            and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__"
        ):
            return PurePosixPath(self.relative)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            base = self.path(node.value, aliases, tool_root)
            if base == PurePosixPath("."):
                self.fail(node, "self-file path escapes repository")
            return base.parent
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "resolve" and not node.args and not node.keywords:
                return self.path(node.func.value, aliases, tool_root)
            if node.func.attr == "with_name" and len(node.args) == 1 and not node.keywords:
                name = self.literal_path(node.args[0])
                if "/" in name:
                    self.fail(node, "with_name requires one literal filename")
                base = self.path(node.func.value, aliases, tool_root)
                if base == PurePosixPath("."):
                    self.fail(node, "with_name requires a self-file path")
                return base.with_name(name)
        if (
            isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents" and isinstance(node.slice, ast.Constant)
            and type(node.slice.value) is int and node.slice.value >= 0
        ):
            base = self.path(node.value.value, aliases, tool_root)
            if node.slice.value >= len(base.parents):
                self.fail(node, "self-file parents index escapes repository")
            return base.parents[node.slice.value]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.path(node.left, aliases, tool_root) / self.literal_path(node.right)
        self.fail(node, "file path is not a supported self-file-relative expression")

    def literal_path(self, node: ast.AST) -> str:
        if (
            not isinstance(node, ast.Constant) or not isinstance(node.value, str)
            or not node.value or "\\" in node.value or ":" in node.value
            or node.value.startswith("/")
            or any(part.casefold() in ("", ".", "..", ".git") for part in node.value.split("/"))
        ):
            self.fail(node, "file path must be a non-escaping literal relative path")
        return node.value

    @staticmethod
    def resolve(node: ast.AST, aliases: dict[str, str]) -> str:
        spelling = _spelling(node)
        root, separator, suffix = spelling.partition(".")
        if root not in aliases:
            return ""
        return aliases[root] + (separator + suffix if separator else "")

    def dynamic_modules(
        self, nodes: list[ast.AST], writes: Counter,
        imports: dict[str, set[str]], inherited: dict[str, set[str]] | None,
    ) -> dict[str, set[str]]:
        """Refuse tools-only dynamic graphs while preserving parent-owned exec."""
        if not self.relative.startswith("tools/"):
            return {}
        origins = {
            name: set(targets) for name, targets in (
                inherited if inherited is not None else {
                    "__import__": {"builtins.__import__"},
                    "eval": {"builtins.eval"},
                    "exec": {_DIRECT_EXEC},
                }
            ).items()
        }
        for name, targets in imports.items():
            relevant = targets.intersection(
                _DYNAMIC_NAMESPACES | _DYNAMIC_OPERATIONS | _DYNAMIC_NAMESPACE_ESCAPES,
            )
            if relevant:
                origins.setdefault(name, set()).update(relevant)
        if writes["exec"] and _DIRECT_EXEC in origins.get("exec", set()):
            origins["exec"].remove(_DIRECT_EXEC)
            origins["exec"].add("builtins.exec")
        for node in nodes:
            if (
                isinstance(node, ast.ImportFrom) and not node.level
                and node.module in _DYNAMIC_NAMESPACES
                and any(item.name == "*" for item in node.names)
            ):
                self.fail(node, "wildcard dynamic module or executable import")
            if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(node.ctx, ast.Load):
                continue
            spelling = _spelling(node)
            root, separator, suffix = spelling.partition(".")
            targets = {
                target + (separator + suffix if separator else "")
                for target in origins.get(root, ())
            }
            parent = self.parents.get(node)
            # Only an unaliased, unbound builtin call is delegated. The parent
            # guard owns its registered native-build input, not this analyzer.
            direct_exec = (
                isinstance(node, ast.Name) and node.id == "exec"
                and targets == {_DIRECT_EXEC}
                and isinstance(parent, ast.Call) and parent.func is node
            )
            if direct_exec:
                continue
            if (
                targets.intersection(_DYNAMIC_OPERATIONS | {_DIRECT_EXEC})
                or spelling in _DYNAMIC_OPERATIONS
            ):
                self.fail(node, "dynamic module loading or executable alias is not admitted")
            if targets.intersection(_DYNAMIC_NAMESPACE_ESCAPES) or (
                targets.intersection(_DYNAMIC_NAMESPACES)
                and not (isinstance(parent, ast.Attribute) and parent.value is node)
            ):
                self.fail(node, "dynamic module or executable namespace cannot escape")
        return origins

    def scope(
        self, scope: ast.AST, inherited: dict[str, str],
        inherited_dynamic: dict[str, set[str]] | None = None,
    ) -> None:
        nodes = list(_scope_nodes(scope))
        writes = Counter(
            node.id for node in nodes
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        )
        imports: dict[str, str] = {}
        import_counts: Counter[str] = Counter()
        import_targets: dict[str, set[str]] = {}

        def imported(local: str, target: str) -> None:
            imports[local] = target
            import_targets.setdefault(local, set()).add(target)
            import_counts[local] += 1
            writes[local] += 1

        for node in nodes:
            if isinstance(node, ast.Import):
                for item in node.names:
                    local = item.asname or item.name.split(".")[0]
                    if item.name.rsplit(".", 1)[-1] in _ACTOR_PRIVATE or local in _ACTOR_PRIVATE:
                        self.fail(node, "private runtime boundary cannot be imported")
                    imported(local, item.name if item.asname else local)
            elif isinstance(node, ast.ImportFrom):
                for item in node.names:
                    if item.name in _ACTOR_PRIVATE or item.asname in _ACTOR_PRIVATE:
                        self.fail(node, "private runtime boundary cannot be imported")
                    if item.name == "*" and node.module in ("importlib", "importlib.util", "importlib.machinery"):
                        self.fail(node, "wildcard loader import")
                    local = item.asname or item.name
                    imported(local, f"{node.module}.{item.name}" if not node.level else "")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not scope:
                writes[node.name] += 1
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                writes[node.name] += 1
            elif isinstance(node, ast.MatchMapping) and node.rest:
                writes[node.rest] += 1
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for node in ast.walk(scope.args):
                if isinstance(node, ast.arg):
                    writes[node.arg] += 1
        dynamic_origins = self.dynamic_modules(nodes, writes, import_targets, inherited_dynamic)
        aliases = {name: target for name, target in inherited.items() if not writes[name]}
        aliases.update({
            name: target for name, target in imports.items()
            if writes[name] == import_counts[name] and len(import_targets[name]) == 1
        })
        # Shadowed imports must not turn a known loader alias into an invisible call.
        tainted = {name for name in inherited | imports if writes[name] and name not in aliases}
        for node in nodes:
            if isinstance(node, ast.Call) and _spelling(node.func).split(".")[0] in tainted:
                root = _spelling(node.func).split(".")[0]
                targets = import_targets.get(root, set()) | {inherited.get(root, "")}
                leaf = _spelling(node.func).rsplit(".", 1)[-1]
                if leaf in _SENSITIVE | {"Path"} or any(
                    target.rsplit(".", 1)[-1] in _SENSITIVE | {"Path"} for target in targets
                ):
                    self.fail(node, "loader or path import is shadowed")
        if writes["__file__"]:
            for node in nodes:
                if isinstance(node, ast.Name) and node.id == "__file__" and isinstance(node.ctx, ast.Load):
                    self.fail(node, "self-file identity is rebound")
        if scope is self.actor_adapter:
            if aliases.get("importlib") != "importlib" or aliases.get("sys") != "sys":
                self.fail(scope, "external runtime adapter imports are rebound")
            self.handled.update(node for node in nodes if isinstance(node, ast.Call))
            return
        if scope is self.actor_supervisor:
            if writes["args"] != 1 or any(
                isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))
                and _spelling(node).split(".")[0] == "args"
                or isinstance(node, (ast.Global, ast.Nonlocal)) and "args" in node.names
                for node in nodes
            ):
                self.fail(scope, "external runtime resource argument is rebound")
        adapter = (
            self.relative == "tools/promote_release.py" and isinstance(scope, ast.FunctionDef)
            and scope.name == "_load_module" and self.parents.get(scope) is self.tree
        )
        if adapter:
            if (
                ast.dump(ast.Module(body=scope.body, type_ignores=[]))
                != ast.dump(ast.Module(body=_ADAPTER.body, type_ignores=[]))
                or scope.decorator_list or [arg.arg for arg in scope.args.args] != ["path", "name"]
                or scope.args.posonlyargs or scope.args.kwonlyargs or scope.args.defaults
                or scope.args.vararg or scope.args.kwarg
                or aliases.get("importlib") != "importlib"
                or any(
                    isinstance(node, ast.Name) and node.id == "_load_module"
                    and isinstance(node.ctx, (ast.Store, ast.Del))
                    or isinstance(node, (ast.Import, ast.ImportFrom)) and any(
                        (item.asname or item.name.split(".")[0]) == "_load_module"
                        for item in node.names
                    )
                    for node in _scope_nodes(self.tree)
                )
            ):
                self.fail(scope, "canonical adapter body or bindings changed")
            self.handled.update(node for node in nodes if isinstance(node, ast.Call))
            return
        tool_root = False
        if (
            self.relative == "tools/promote_release.py" and isinstance(scope, ast.FunctionDef)
            and scope.name == "consume_pending_changes" and self.parents.get(scope) is self.tree
            and writes["tool_root"] == 1
        ):
            tool_root = any(ast.dump(node) == ast.dump(_TOOL_ROOT) for node in nodes)
        calls = [node for node in nodes if isinstance(node, ast.Call)]
        specs: dict[str, ast.AST] = {}
        modules: dict[str, tuple[str, ast.AST]] = {}
        for call in calls:
            if call in self.actor_calls:
                self.handled.add(call)
                continue
            operation = self.resolve(call.func, aliases)
            leaf = getattr(call.func, "attr", getattr(call.func, "id", ""))
            if leaf in _UNSUPPORTED or operation.rsplit(".", 1)[-1] in _UNSUPPORTED:
                self.fail(call, "legacy or explicit file loader is not admitted")
            if operation == _SPEC:
                if len(call.args) != 2 or call.keywords:
                    self.fail(call, "spec requires exactly name and file path")
                path = self.path(call.args[1], aliases, tool_root=False)
                self.result.add(relative_file(path.as_posix()))
                name, statement = self.binding(call, writes)
                specs[name] = statement
                self.handled.add(call)
            elif leaf == "_load_module" or operation == "promote_release._load_module":
                local_adapter = (
                    self.relative == "tools/promote_release.py"
                    and isinstance(call.func, ast.Name) and call.func.id == "_load_module"
                    and writes["_load_module"] == 0
                    and sum(
                        isinstance(node, ast.FunctionDef) and node.name == "_load_module"
                        for node in self.tree.body
                    ) == 1
                )
                if not (local_adapter or operation == "promote_release._load_module"):
                    self.fail(call, "unbound canonical adapter")
                if len(call.args) != 2 or call.keywords:
                    self.fail(call, "adapter requires exactly path and name")
                if tool_root:
                    root_binding = next(node for node in nodes if ast.dump(node) == ast.dump(_TOOL_ROOT))
                    allowed_root = self.dominates(root_binding, call)
                else:
                    allowed_root = False
                path = self.path(call.args[0], aliases, allowed_root)
                self.result.add(relative_file(path.as_posix()))
                self.handled.add(call)
        for call in calls:
            if self.resolve(call.func, aliases) != _MODULE:
                continue
            if (
                len(call.args) != 1 or call.keywords or not isinstance(call.args[0], ast.Name)
                or call.args[0].id not in specs
                or not self.dominates(specs[call.args[0].id], call)
            ):
                self.fail(call, "module_from_spec has no preceding supported spec binding")
            name, statement = self.binding(call, writes)
            modules[name] = (call.args[0].id, statement)
            self.handled.add(call)
        for call in calls:
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "exec_module":
                continue
            loader = call.func.value
            if (
                not isinstance(loader, ast.Attribute) or loader.attr != "loader"
                or not isinstance(loader.value, ast.Name) or loader.value.id not in specs
                or len(call.args) != 1 or call.keywords or not isinstance(call.args[0], ast.Name)
                or call.args[0].id not in modules
            ):
                self.fail(call, "exec_module has no supported spec/module provenance")
            spec_name = loader.value.id
            module_spec, statement = modules[call.args[0].id]
            if module_spec != spec_name or not self.dominates(statement, call):
                self.fail(call, "exec_module spec/module bindings differ or do not dominate")
            self.handled.add(call)
        for node in nodes:
            if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                resolved = self.resolve(node, aliases)
                parent = self.parents.get(node)
                if resolved.rsplit(".", 1)[-1] in _SENSITIVE:
                    if not (
                        isinstance(parent, ast.Call) and parent.func is node and parent in self.handled
                    ):
                        self.fail(node, "unsupported loader reference or alias")
                if resolved in ("importlib", "importlib.util", "importlib.machinery", "promote_release"):
                    if not (isinstance(parent, ast.Attribute) and parent.value is node):
                        self.fail(node, "loader namespace alias is not admitted")
            if isinstance(node, ast.Name) and node.id in specs and isinstance(node.ctx, ast.Load):
                parent = self.parents.get(node)
                current = parent
                while current is not None and not isinstance(current, ast.stmt):
                    current = self.parents.get(current)
                if not (
                    isinstance(parent, ast.Attribute) and parent.attr == "loader"
                    or isinstance(parent, ast.Call) and parent in self.handled
                    and self.resolve(parent.func, aliases) == _MODULE
                    or isinstance(current, ast.Assert)
                ):
                    self.fail(node, "spec references or aliases are not admitted")
            if isinstance(node, ast.Attribute) and node.attr == "loader":
                parent = self.parents.get(node)
                if not (
                    isinstance(parent, ast.Attribute) and parent.attr == "exec_module"
                    and isinstance(self.parents.get(parent), ast.Call)
                    and self.parents[parent] in self.handled
                ):
                    current = parent
                    while current is not None and not isinstance(current, ast.stmt):
                        current = self.parents.get(current)
                    if not isinstance(current, ast.Assert):
                        self.fail(node, "loader references or aliases are not admitted")
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                root = _spelling(node).split(".")[0]
                if root in specs | modules or aliases.get(root, "").startswith(
                    ("importlib", "promote_release", "pathlib"),
                ):
                    self.fail(node, "loader namespace or spec/module provenance is mutated")
        for child in nodes:
            if child is not scope and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            ):
                self.scope(child, aliases, dynamic_origins)

    def run(self) -> set[str]:
        if any(
            isinstance(node, ast.Name) and node.id == "__file__"
            and isinstance(node.ctx, (ast.Store, ast.Del))
            or isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
            and node.name == "__file__"
            or isinstance(node, ast.MatchMapping) and node.rest == "__file__"
            for node in ast.walk(self.tree)
        ):
            self.fail(self.tree, "self-file identity is rebound")
        self.runtime_boundary()
        self.scope(self.tree, {})
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                if getattr(node, "id", getattr(node, "attr", "")) in _SENSITIVE:
                    parent = self.parents.get(node)
                    if not isinstance(parent, ast.Call) or parent.func is not node or parent not in self.handled:
                        self.fail(node, "unsupported loader reference or alias")
        return self.result


def dynamic_loads(tree: ast.Module, relative: str) -> set[str]:
    """Return bounded file-loader edges without importing or executing source.

    Self-file Path expressions and the exact promotion adapter yield source
    edges. The exact private Windows guardian seam admits two external runtime
    actor loads, not repository edges. Tools-only dynamic import/executable
    aliases are refused; direct builtin exec remains the parent guard's
    registered build-input boundary. Unsupported provenance raises
    UnitEvidenceError rather than silently omitting an implementation edge.
    """
    return _Discovery(tree, relative).run()

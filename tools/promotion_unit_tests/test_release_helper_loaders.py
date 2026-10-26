"""File-loader source admission uses AST data, never imported release actors."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from promotion_unit_contract import UnitEvidenceError
from release_helper_loaders import dynamic_loads

_IMPORTS = "import importlib.util\nfrom pathlib import Path\n"
_CHAIN = (
    'spec = importlib.util.spec_from_file_location("helper", PATH)\n'
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
)
_ADAPTER = (
    "def _load_module(path, name):\n"
    "    spec = importlib.util.spec_from_file_location(name, path)\n"
    "    module = importlib.util.module_from_spec(spec)\n"
    "    assert spec.loader is not None\n"
    "    spec.loader.exec_module(module)\n"
    "    return module\n"
)
_ACTOR_SOURCE = "libs/installer-engine/installer_engine_process.py"
_RESOURCE_ADAPTER = (
    "def _load_resource(name, path):\n"
    "    spec = importlib.util.spec_from_file_location(name, path)\n"
    "    if spec is None or spec.loader is None:\n"
    '        raise ImportError("installer guardian resource could not be loaded")\n'
    "    module = importlib.util.module_from_spec(spec)\n"
    "    sys.modules[name] = module\n"
    "    spec.loader.exec_module(module)\n"
    "    return module\n"
)
_ACTOR_BOUNDARY = _IMPORTS + "import sys\n" + _RESOURCE_ADAPTER + (
    "def _supervise_windows(args, argv):\n"
    '    runtime = _load_resource("installer_guardian_runtime", args.runtime_path)\n'
    '    procutil = _load_resource("installer_guardian_procutil", args.procutil_path)\n'
    'if __name__ == "__main__":\n'
    "    _supervise_windows(args, args.command[1:])\n"
)


def _discover(source: str, relative: str = "tools/controller.py") -> set[str]:
    return dynamic_loads(ast.parse(source), relative)


@pytest.mark.parametrize(("expression", "expected"), [
    ('Path(__file__).with_name("new_helper.py")', "tools/new_helper.py"),
    ('Path(__file__).parent / "new_helper.py"', "tools/new_helper.py"),
    ('Path(__file__).resolve().parent / "new_helper.py"', "tools/new_helper.py"),
    ('Path(__file__).parents[0] / "new_helper.py"', "tools/new_helper.py"),
    ('Path(__file__).parents[1] / "libs" / "helper.py"', "libs/helper.py"),
    ('Path(__file__).parent / "clean-room/lib/helper.py"', "tools/clean-room/lib/helper.py"),
])
def test_self_file_spec_enrolls_implementation(expression, expected):
    assert _discover(_IMPORTS + _CHAIN.replace("PATH", expression)) == {expected}


@pytest.mark.parametrize("expression", [
    "unknown_path", '"/tmp/helper.py"', 'Path("/tmp/helper.py")',
    'Path(__file__).parent / "../helper.py"',
    'Path(__file__).parent / "/tmp/helper.py"',
    'Path(__file__).parent / "C:/helper.py"',
    'Path(__file__).parent / "a\\\\helper.py"',
    'Path(__file__).parent / "a//helper.py"',
    'Path(__file__).with_name("a/helper.py")',
    'Path(__file__).parents[2] / "helper.py"',
    'Path(__file__).parent.parent.parent / "helper.py"',
    'Path(__file__).parent / "helper.pyc"',
    'Path(__file__).parent / ".git/helper.py"',
    'Path(__file__).parent / filename',
    'Path(__file__).resolve(strict=True).parent / "helper.py"',
])
def test_unknown_absolute_or_escaping_file_paths_are_refused(expression):
    with pytest.raises(UnitEvidenceError):
        _discover(_IMPORTS + _CHAIN.replace("PATH", expression))


@pytest.mark.parametrize(("imports", "spec", "module", "path"), [
    (
        "from importlib.util import spec_from_file_location as load, module_from_spec as make\n"
        "from pathlib import Path as P\n",
        "load", "make", 'P(__file__).parent / "new_helper.py"',
    ),
    (
        "import importlib.util as util\nimport pathlib as paths\n",
        "util.spec_from_file_location", "util.module_from_spec",
        'paths.Path(__file__).with_name("new_helper.py")',
    ),
    (
        "from importlib import util as u\nfrom pathlib import Path\n",
        "u.spec_from_file_location", "u.module_from_spec",
        'Path(__file__).parent / "new_helper.py"',
    ),
])
def test_import_aliases_cannot_hide_loader_edges(imports, spec, module, path):
    source = _CHAIN.replace("importlib.util.spec_from_file_location", spec)
    source = source.replace("importlib.util.module_from_spec", module).replace("PATH", path)
    assert _discover(imports + source) == {"tools/new_helper.py"}
    with pytest.raises(UnitEvidenceError):
        _discover(imports + source.replace(path, "unknown_path"))


@pytest.mark.parametrize("source", [
    "module = importlib.util.module_from_spec(spec)\n",
    "spec.loader.exec_module(module)\n",
    "loader.exec_module(module)\n",
    "module = importlib.util.module_from_spec(spec)\n" + _CHAIN,
    _CHAIN.replace("module = ", "spec = None\nmodule = "),
    _CHAIN.replace("spec.loader.exec_module", "module = None\nspec.loader.exec_module"),
    _CHAIN.replace("module = ", "other = spec\nmodule = "),
    _CHAIN.replace("module = ", "callback(spec)\nmodule = "),
    _CHAIN.replace("spec.loader.exec_module(module)", "loader = spec.loader\nloader.exec_module(module)"),
    _CHAIN.replace("spec.loader.exec_module(module)", "execute = spec.loader.exec_module\nexecute(module)"),
    _CHAIN.replace("spec.loader.exec_module(module)", "spec.loader = other\nspec.loader.exec_module(module)"),
    _CHAIN.replace("module = ", "spec.origin = other\nmodule = "),
    _CHAIN.replace("module = ", "try:\n    pass\nexcept Error as spec:\n    pass\nmodule = "),
    _CHAIN.replace("module = ", "match data:\n    case spec:\n        pass\nmodule = "),
    _CHAIN.replace("module = ", "match data:\n    case [*spec]:\n        pass\nmodule = "),
    _CHAIN.replace("module = ", "match data:\n    case {**spec}:\n        pass\nmodule = "),
    _CHAIN.replace("spec.loader.exec_module(module)", "other.loader.exec_module(module)"),
    _CHAIN.replace("spec.loader.exec_module(module)", "spec.loader.exec_module(other)"),
    _CHAIN.replace("module = ", 'other = importlib.util.spec_from_file_location("other", PATH)\nmodule = ')
    .replace("spec.loader.exec_module(module)", "other.loader.exec_module(module)"),
    "if flag:\n    " + _CHAIN.splitlines()[0] + "\n" + "\n".join(_CHAIN.splitlines()[1:]),
    _CHAIN.splitlines()[0] + "\ndef later():\n    " + "\n    ".join(_CHAIN.splitlines()[1:]),
    _CHAIN.replace('("helper", PATH)', '("helper", PATH, loader=custom)'),
    "spec = importlib.util.spec_from_file_location('helper', PATH)\nspec += other\n",
])
def test_unbound_reassigned_or_aliased_provenance_is_refused(source):
    source = source.replace("PATH", 'Path(__file__).parent / "helper.py"')
    with pytest.raises(UnitEvidenceError):
        _discover(_IMPORTS + source)


@pytest.mark.parametrize("source", [
    "from importlib.machinery import SourceFileLoader as Load\nLoad('helper', 'helper.py')",
    "import importlib.machinery as machinery\nmachinery.SourcelessFileLoader('helper', 'helper.pyc')",
    "loader.load_module('helper')",
    "from importlib.util import spec_from_file_location as load\nalias = load\nalias('helper', unknown)",
    "alias = importlib.util.spec_from_file_location\nalias('helper', unknown)",
    "alias = importlib.util\nalias.spec_from_file_location('helper', unknown)",
    "from importlib import util\nalias = util\nalias.spec_from_file_location('helper', unknown)",
    "from importlib.util import *\nspec_from_file_location('helper', unknown)",
    "from importlib.util import spec_from_file_location as load\nload = custom\nload('helper', unknown)",
    "importlib = custom\nimportlib.util.spec_from_file_location('helper', unknown)",
    "importlib.util.spec_from_file_location = custom\n" + _CHAIN,
    "del importlib.util.spec_from_file_location\n" + _CHAIN,
    "from pathlib import Path\nPath = custom\n" + _CHAIN.replace("PATH", 'Path(__file__).parent / "helper.py"'),
    "__file__ = custom\n" + _CHAIN.replace("PATH", 'Path(__file__).parent / "helper.py"'),
])
def test_unsupported_loader_constructors_and_alias_escapes_are_refused(source):
    source = source.replace("PATH", 'Path(__file__).parent / "helper.py"')
    with pytest.raises(UnitEvidenceError):
        _discover(_IMPORTS + source)


def test_canonical_adapter_and_scoped_tool_root_callers_are_supported():
    source = _IMPORTS + _ADAPTER + (
        "def consume_pending_changes(scratch, trusted_source):\n"
        '    tool_root = (trusted_source or scratch) / "tools"\n'
        '    a = _load_module(tool_root / "changefile.py", "a")\n'
        '    b = _load_module(tool_root / "accumulate_bumps.py", "b")\n'
        '    c = _load_module(tool_root / "materialize_main.py", "c")\n'
    )
    assert _discover(source, "tools/promote_release.py") == {
        "tools/changefile.py", "tools/accumulate_bumps.py", "tools/materialize_main.py",
    }


@pytest.mark.parametrize("fault", [
    "other-module", "other-function", "other-root", "changed-adapter",
    "adapter-extra-loader", "root-reassigned", "root-after-call", "missing-adapter",
    "adapter-reassigned",
])
def test_adapter_exemptions_do_not_extend_to_other_scopes_or_bodies(fault):
    source = _IMPORTS + _ADAPTER + (
        "def consume_pending_changes(scratch, trusted_source):\n"
        '    tool_root = (trusted_source or scratch) / "tools"\n'
        '    a = _load_module(tool_root / "changefile.py", "a")\n'
    )
    relative = "tools/promote_release.py"
    if fault == "other-module":
        relative = "tools/other.py"
    elif fault == "other-function":
        source = source.replace("def consume_pending_changes", "def elsewhere")
    elif fault == "other-root":
        source = source.replace("(trusted_source or scratch)", "arbitrary")
    elif fault == "changed-adapter":
        source = source.replace("return module", "return other")
    elif fault == "adapter-extra-loader":
        source = source.replace("return module", "other = importlib.util.spec_from_file_location(name, extra)\n    return module")
    elif fault == "root-reassigned":
        source += "    tool_root = arbitrary\n"
    elif fault == "root-after-call":
        lines = source.splitlines()
        lines[-2], lines[-1] = lines[-1], lines[-2]
        source = "\n".join(lines)
    elif fault == "missing-adapter":
        source = source.replace(_ADAPTER, "")
    else:
        source += "\n_load_module = arbitrary\n"
    with pytest.raises(UnitEvidenceError):
        _discover(source, relative)


@pytest.mark.parametrize("path", [
    'Path(__file__).with_name("build_python_artifacts.py")',
    'Path(__file__).parent / "build_python_artifacts.py"',
])
def test_imported_canonical_adapter_resolves_self_file_paths(path):
    source = _IMPORTS + "import promote_release as promotion\n"
    source += f'builder = promotion._load_module({path}, "builder")\n'
    assert _discover(source, "tools/promotion_python_artifacts.py") == {"tools/build_python_artifacts.py"}


@pytest.mark.parametrize(("relative", "expected"), [
    ("tools/promote_release.py", {
        "tools/changefile.py", "tools/accumulate_bumps.py", "tools/materialize_main.py",
    }),
    ("tools/accumulate_bumps.py", {"tools/check-version-bump.py"}),
    ("tools/materialize_main.py", {"tools/clean-room/lib/materialized_provenance.py"}),
    ("tools/promotion_python_artifacts.py", {"tools/build_python_artifacts.py"}),
    (_ACTOR_SOURCE, set()),
])
def test_existing_loader_files_enroll_exact_paths_without_importing_them(relative, expected):
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / relative).read_bytes(), filename=relative)
    assert dynamic_loads(tree, relative) == expected


def test_inert_source_is_not_executed_by_discovery():
    source = "raise RuntimeError('source must never execute')\n" + _IMPORTS
    source += _CHAIN.replace("PATH", 'Path(__file__).parent / "new_helper.py"')
    assert _discover(source) == {"tools/new_helper.py"}


@pytest.mark.parametrize("source", [
    'spec = importlib.util.spec_from_file_location("helper", Path(__file__).parent / "helper.py")',
    "__file__ = unknown\ndef load():\n    " + _CHAIN.replace("\n", "\n    "),
])
def test_unimported_constructor_or_rebound_self_file_in_parent_scope_is_refused(source):
    source = source.replace("PATH", 'Path(__file__).parent / "helper.py"')
    with pytest.raises(UnitEvidenceError):
        _discover("from pathlib import Path\n" + source)


def test_loader_bindings_must_dominate_execution_but_may_live_in_one_branch():
    chain = _CHAIN.replace("PATH", 'Path(__file__).parent / "helper.py"')
    assert _discover(_IMPORTS + "if flag:\n    " + chain.replace("\n", "\n    ")) == {"tools/helper.py"}
    source = _IMPORTS + chain.replace(
        "spec.loader.exec_module(module)",
        "def callback(spec):\n    spec.loader.exec_module(module)",
    )
    with pytest.raises(UnitEvidenceError):
        _discover(source)


def test_alias_of_canonical_adapter_is_supported_but_unknown_adapter_is_not():
    source = _IMPORTS + "from promote_release import _load_module as load\n"
    source += 'helper = load(Path(__file__).parent / "helper.py", "helper")\n'
    assert _discover(source) == {"tools/helper.py"}
    with pytest.raises(UnitEvidenceError):
        _discover(_IMPORTS + '_load_module(Path(__file__).parent / "helper.py", "helper")')


@pytest.mark.parametrize("expression", [
    "def declared(value: load('helper', unknown)): pass",
    "def declared(value=load('helper', unknown)): pass",
    "def declared() -> load('helper', unknown): pass",
    "class Declared(load('helper', unknown)): pass",
    "class Declared(metaclass=load('helper', unknown)): pass",
    "@load('helper', unknown)\ndef declared(): pass",
])
def test_definition_time_aliased_loaders_cannot_escape_scope_analysis(expression):
    source = "from importlib.util import spec_from_file_location as load\n" + expression
    with pytest.raises(UnitEvidenceError):
        _discover(source)


def test_pattern_binding_cannot_replace_self_file_for_a_nested_function():
    source = _IMPORTS + "match value:\n    case __file__: pass\ndef nested():\n    "
    source += _CHAIN.replace("PATH", 'Path(__file__).parent / "helper.py"').replace("\n", "\n    ")
    with pytest.raises(UnitEvidenceError):
        _discover(source)


def test_identical_branch_reimports_do_not_taint_ordinary_promotion_calls():
    source = (
        "def main(checkout, validate):\n"
        "    if checkout:\n"
        "        import promote_release as promotion\n"
        "        promotion._git(['worktree'])\n"
        "    if validate:\n"
        "        import promote_release as promotion\n"
        "        promotion._git(['read-tree'])\n"
    )
    assert _discover(source, "tools/promotion_frozen_candidate.py") == set()


@pytest.mark.parametrize("imports", [
    "import importlib.util\nimport importlib.util\nfrom pathlib import Path\n",
    "from importlib import util\nfrom importlib import util\nfrom pathlib import Path\n",
    "from importlib.util import spec_from_file_location as load\n"
    "from importlib.util import spec_from_file_location as load\n"
    "from importlib.util import module_from_spec as make\nfrom pathlib import Path\n",
    "import importlib.util\nfrom pathlib import Path\nfrom pathlib import Path\n",
])
def test_identical_reimports_preserve_file_loader_provenance(imports):
    chain = _CHAIN.replace("PATH", 'Path(__file__).parent / "helper.py"')
    if "from importlib import util" in imports:
        chain = chain.replace("importlib.util.", "util.")
    elif "as load" in imports:
        chain = chain.replace("importlib.util.spec_from_file_location", "load")
        chain = chain.replace("importlib.util.module_from_spec", "make")
    assert _discover(imports + chain) == {"tools/helper.py"}


@pytest.mark.parametrize("rebind", [
    "promotion = unknown",
    "del promotion",
    "import other_module as promotion",
])
def test_reimport_does_not_hide_real_adapter_rebinding(rebind):
    source = (
        "from pathlib import Path\n"
        "import promote_release as promotion\n"
        f"{rebind}\n"
        "import promote_release as promotion\n"
        'promotion._load_module(Path(__file__).parent / "helper.py", "helper")\n'
    )
    with pytest.raises(UnitEvidenceError):
        _discover(source)


def test_non_loader_calls_do_not_require_file_loader_provenance():
    source = "import promote_release as promotion\npromotion = other\npromotion._git([])\n"
    assert _discover(source) == set()


def test_exact_owned_external_runtime_boundary_does_not_invent_repository_edges():
    assert _discover(_ACTOR_BOUNDARY, _ACTOR_SOURCE) == set()


@pytest.mark.parametrize("fault", [
    "third-resource", "missing-resource", "duplicate-resource", "wrong-name",
    "wrong-path-argument", "wrong-path-variable", "keyword-resource", "other-caller",
    "nested-caller", "changed-adapter", "extra-raw-spec-in-adapter", "adapter-decorator",
    "adapter-default", "supervisor-default", "adapter-rebound", "supervisor-rebound",
    "adapter-import-rebound", "resource-argument-rebound", "resource-path-rebound",
    "sys-rebound", "importlib-rebound", "adapter-alias", "supervisor-alias",
    "other-supervisor-caller", "unguarded-main", "main-else-branch", "wrong-main-args", "other-module",
])
def test_external_runtime_boundary_requires_exact_body_calls_and_custody(fault):
    source = _ACTOR_BOUNDARY
    relative = _ACTOR_SOURCE
    resource = '    procutil = _load_resource("installer_guardian_procutil", args.procutil_path)\n'
    if fault == "third-resource":
        source = source.replace(resource, resource + '    extra = _load_resource("extra", args.extra_path)\n')
    elif fault == "missing-resource":
        source = source.replace(resource, "")
    elif fault == "duplicate-resource":
        source = source.replace(resource, resource * 2)
    elif fault == "wrong-name":
        source = source.replace('"installer_guardian_procutil"', '"different_actor"')
    elif fault == "wrong-path-argument":
        source = source.replace("args.procutil_path", "args.runtime_path")
    elif fault == "wrong-path-variable":
        source = source.replace("args.procutil_path", "unknown")
    elif fault == "keyword-resource":
        source = source.replace("args.procutil_path)", "path=args.procutil_path)")
    elif fault == "other-caller":
        source += '\ndef other(args):\n    _load_resource("installer_guardian_runtime", args.runtime_path)\n'
    elif fault == "nested-caller":
        source = source.replace(resource, "    def nested():\n    " + resource)
    elif fault == "changed-adapter":
        source = source.replace("return module", "return other")
    elif fault == "extra-raw-spec-in-adapter":
        source = source.replace("return module", 'other = importlib.util.spec_from_file_location("extra", unknown)\n    return module')
    elif fault == "adapter-decorator":
        source = source.replace("def _load_resource", "@decorate\ndef _load_resource")
    elif fault == "adapter-default":
        source = source.replace("def _load_resource(name, path)", "def _load_resource(name, path=None)")
    elif fault == "supervisor-default":
        source = source.replace("def _supervise_windows(args, argv)", "def _supervise_windows(args, argv=None)")
    elif fault == "adapter-rebound":
        source += "\n_load_resource = other\n"
    elif fault == "supervisor-rebound":
        source += "\n_supervise_windows = other\n"
    elif fault == "adapter-import-rebound":
        source += "\nfrom other import resource as _load_resource\n"
    elif fault == "resource-argument-rebound":
        source = source.replace(resource, "    args = other\n" + resource)
    elif fault == "resource-path-rebound":
        source = source.replace(resource, "    args.procutil_path = unknown\n" + resource)
    elif fault == "sys-rebound":
        source += "\nsys = other\n"
    elif fault == "importlib-rebound":
        source += "\nimportlib = other\n"
    elif fault == "adapter-alias":
        source += "\nresource = _load_resource\n"
    elif fault == "supervisor-alias":
        source += "\nsupervisor = _supervise_windows\n"
    elif fault == "other-supervisor-caller":
        source += "\ndef other(args):\n    _supervise_windows(args, args.command[1:])\n"
    elif fault == "unguarded-main":
        source = source.replace('if __name__ == "__main__":', "if True:")
    elif fault == "main-else-branch":
        source = source.replace('if __name__ == "__main__":', 'if __name__ == "__main__":\n    pass\nelse:')
    elif fault == "wrong-main-args":
        source = source.replace("_supervise_windows(args, args.command[1:])", "_supervise_windows(other, args.command[1:])")
    else:
        relative = "tools/other.py"
    with pytest.raises(UnitEvidenceError):
        _discover(source, relative)


@pytest.mark.parametrize("source", [
    "from installer_engine_process import _load_resource",
    "from installer_engine_process import _load_resource as resource",
    "from installer_engine_process import _supervise_windows as supervise",
    'import installer_engine_process as process\nprocess._load_resource("actor", unknown)',
    "import installer_engine_process as process\nprocess._supervise_windows(args, argv)",
    "import installer_engine_process as process\nresource = process._load_resource",
    "import installer_engine_process as process\nsupervise = process._supervise_windows",
])
def test_other_modules_cannot_import_alias_or_invoke_private_runtime_boundary(source):
    with pytest.raises(UnitEvidenceError):
        _discover(source)


def test_public_runtime_operator_remains_an_ordinary_import_not_a_file_loader():
    assert _discover(
        "from installer_engine_process import run_owned\nrun_owned(argv, cwd=root)\n",
    ) == set()
    assert _discover(
        "import installer_engine_process as process\nprocess.run_owned(argv, cwd=root)\n",
    ) == set()


@pytest.mark.parametrize("scope", ["module", "other-function", "supervisor"])
def test_new_static_specs_inside_actor_source_still_enroll_repository_edges(scope):
    chain = _CHAIN.replace("PATH", 'Path(__file__).parent / "new_helper.py"')
    if scope == "module":
        source = _ACTOR_BOUNDARY + chain
    elif scope == "other-function":
        source = _ACTOR_BOUNDARY + "\ndef helper():\n    " + chain.replace("\n", "\n    ")
    else:
        source = _ACTOR_BOUNDARY.replace(
            'if __name__ == "__main__":',
            "    " + chain.replace("\n", "\n    ").rstrip() + '\nif __name__ == "__main__":',
        )
    assert _discover(source, _ACTOR_SOURCE) == {"libs/installer-engine/new_helper.py"}


@pytest.mark.parametrize("scope", ["module", "other-function", "supervisor"])
def test_third_unresolved_raw_spec_in_actor_source_is_not_exempt(scope):
    raw = 'spec = importlib.util.spec_from_file_location("extra", unknown)\n'
    if scope == "module":
        source = _ACTOR_BOUNDARY + raw
    elif scope == "other-function":
        source = _ACTOR_BOUNDARY + "\ndef helper():\n    " + raw
    else:
        source = _ACTOR_BOUNDARY.replace('if __name__ == "__main__":', "    " + raw + 'if __name__ == "__main__":')
    with pytest.raises(UnitEvidenceError):
        _discover(source, _ACTOR_SOURCE)


@pytest.mark.parametrize("source", [
    'from importlib import import_module as load\nload("hidden")',
    'import importlib\nimportlib.import_module("hidden")',
    'import importlib as imports\nimports.import_module("hidden")',
    'from builtins import __import__ as load\nload("hidden")',
    'import builtins\nbuiltins.__import__("hidden")',
    'import builtins as native\nnative.__import__("hidden")',
    '__import__("hidden")',
    'from runpy import run_path as load\nload("hidden.py")',
    'from runpy import run_module as load\nload("hidden")',
    'import runpy as scripts\nscripts.run_path("hidden.py")',
    'import runpy as scripts\nscripts.run_module("hidden")',
    'import runpy\nrunpy.run_module("hidden")',
    'from builtins import exec as execute\nexecute("hidden()")',
    'from builtins import eval as evaluate\nevaluate("hidden()")',
    'from builtins import exec\nexec("hidden()")',
    'from builtins import eval\neval("hidden()")',
    'import builtins as native\nnative.exec("hidden()")',
    'import builtins as native\nnative.eval("hidden()")',
    'eval("hidden()")',
])
def test_tools_dynamic_module_and_executable_aliases_are_refused(source):
    with pytest.raises(UnitEvidenceError, match="dynamic module loading or executable alias"):
        _discover(source)


@pytest.mark.parametrize("binding", [
    "load = callback", "del load", "from other import callback as load",
    "def load(value): pass",
    "try:\n    pass\nexcept Error as load:\n    pass",
    "match value:\n    case load: pass",
])
def test_rebinding_imported_dynamic_callable_does_not_erase_its_origin(binding):
    source = "from importlib import import_module as load\n" + binding + '\nload("hidden")'
    with pytest.raises(UnitEvidenceError):
        _discover(source)


@pytest.mark.parametrize("source", [
    'from importlib import import_module as load\ndef nested():\n    load("hidden")',
    'from importlib import import_module as load\nload = callback\ndef nested():\n    load("hidden")',
    'from importlib import import_module as load\ndef nested(load):\n    load("hidden")',
    'from importlib import import_module as load\ndef nested():\n    load = callback\n    def deeper():\n        load("hidden")',
    'from importlib import import_module as load\ndef nested(load=callback):\n    return lambda: load("hidden")',
    'import importlib as namespace\ndef nested(namespace):\n    namespace.import_module("hidden")',
    'import runpy as namespace\nnamespace = callback\ndef nested():\n    namespace.run_module("hidden")',
    'import builtins as namespace\ndef nested():\n    namespace = callback\n    namespace.exec("hidden()")',
    'from importlib import import_module as load\ndef nested(value=load("hidden")): pass',
    'from importlib import import_module as load\nclass Nested(load("hidden")): pass',
])
def test_nested_scope_and_shadowing_cannot_hide_dynamic_loader_aliases(source):
    with pytest.raises(UnitEvidenceError):
        _discover(source)


@pytest.mark.parametrize("source", [
    'from importlib import import_module as load\nalias = load\nalias("hidden")',
    'import importlib as namespace\nalias = namespace\nalias.import_module("hidden")',
    'import runpy as namespace\nalias = namespace\nalias.run_module("hidden")',
    'import builtins as namespace\nalias = namespace\nalias.exec("hidden()")',
    'import runpy\nload = runpy.run_path\nload("hidden.py")',
    'import builtins\nexecute = builtins.exec\nexecute("hidden()")',
    'execute = exec\nexecute("hidden()")',
    'load = __import__\nload("hidden")',
    'import importlib\nload = getattr(importlib, "import_module")\nload("hidden")',
    'import runpy\nnamespace = runpy.__dict__\nnamespace["run_module"]("hidden")',
    'from importlib import __dict__ as namespace\nnamespace["import_module"]("hidden")',
    'import importlib\nload = importlib.__getattribute__("import_module")\nload("hidden")',
    'from runpy import *\nrun_module("hidden")',
    'from builtins import *\n__import__("hidden")',
])
def test_tools_dynamic_namespace_and_callable_escapes_are_refused(source):
    with pytest.raises(UnitEvidenceError):
        _discover(source)


@pytest.mark.parametrize("source", [
    'from importlib import import_module as load\nload("runtime_product")',
    'import importlib\nimportlib.import_module("runtime_product")',
    'from builtins import __import__ as load\nload("runtime_product")',
    'from runpy import run_path as load\nload("runtime_product.py")',
    'import runpy as scripts\nscripts.run_module("runtime_product")',
    'from builtins import exec as execute\nexecute(callback)',
    'import builtins as native\nnative.eval(callback)',
    'def callback(exec, eval, __import__):\n    exec(value)\n    eval(value)\n    __import__(value)',
])
def test_library_runtime_callbacks_do_not_acquire_tools_only_dynamic_restrictions(source):
    assert _discover(source, "libs/runtime/runtime_callbacks.py") == set()


def test_direct_native_compile_exec_remains_the_parent_registered_input_boundary():
    source = 'exec(compile(helper.read_bytes(), str(helper), "exec"), owner.__dict__)'
    assert _discover(source, "tools/native_daemon_artifacts.py") == set()
    assert _discover(
        "def build():\n    " + source,
        "tools/native_daemon_artifacts.py",
    ) == set()
    with pytest.raises(UnitEvidenceError):
        _discover(
            "exec = callback\n" + source,
            "tools/native_daemon_artifacts.py",
        )
    with pytest.raises(UnitEvidenceError):
        _discover(
            "from builtins import exec\n" + source,
            "tools/native_daemon_artifacts.py",
        )

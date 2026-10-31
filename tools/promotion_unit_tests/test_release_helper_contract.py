"""Pure source admission and controller compatibility, without release actors."""
from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

import promotion_release_helpers as helpers
from promotion_unit_contract import UnitEvidenceError, admit_manifest, relative_file
import release_controller_contract as control


def _workflow():
    jobs = {}
    for name in ("gate", *control.HELPER_JOBS):
        jobs[name] = {
            "needs": ["gate"],
            "steps": [{
                "uses": "actions/checkout@v4",
                "with": {
                    "ref": control.CONTROL_REF if name == "gate" else control.SOURCE_REF,
                    "path": "reviewed-helpers", "persist-credentials": False,
                },
            }],
        }
    jobs["gate"]["permissions"] = {"contents": "read", "actions": "read", "pull-requests": "read"}
    jobs["gate"]["steps"].append({
        "run": 'sha="$(git rev-parse origin/dev)"\ngit merge-base --is-ancestor "$sha" origin/dev',
    })
    jobs["source-units"]["permissions"] = {"contents": "read"}
    jobs["python-artifacts"]["needs"].append("source-units")
    jobs["publish-python-artifacts"]["permissions"] = {"attestations": "write", "id-token": "write"}
    jobs["publish-python-artifacts"]["steps"].extend([
        {"run": 'python promotion_source_archive.py --directory "$RUNNER_TEMP/complete-source"'},
        {"uses": "actions/attest-build-provenance@exact-action",
         "with": {"subject-path": "${{ runner.temp }}/complete-source/*"}},
        {"id": "publish",
         "run": 'python publish_python_artifacts.py publish-candidate --complete-source-dir "$RUNNER_TEMP/complete-source"'},
    ])
    return {"jobs": jobs}


def test_source_selected_producer_and_verifier_follow_one_admitted_revision():
    workflow = _workflow()
    control.verify_helper_selection(workflow)
    for name in ("build-python-artifacts", "python-artifacts"):
        assert workflow["jobs"][name]["steps"][0]["with"]["ref"] == control.SOURCE_REF
    control.verify_controller_inputs(workflow, {
        "promotion_source_archive.py": b'parser.add_argument("--directory", required=True)',
        "publish_python_artifacts.py": b'parser.add_argument("--complete-source-dir")',
    })


@pytest.mark.parametrize("fault", [
    "mutable-source", "candidate-helpers", "manual-pin", "credentials",
    "missing-source", "unqualified-signer", "always-signer", "bootstrap-write", "unit-write",
    "dispatch-source", "missing-ancestry",
])
def test_unqualified_or_wrong_source_implementation_is_refused(fault):
    workflow = _workflow()
    jobs = workflow["jobs"]
    checkout = jobs["python-artifacts"]["steps"][0]["with"]
    if fault == "mutable-source":
        checkout["ref"] = "dev"
    elif fault == "candidate-helpers":
        checkout["ref"] = "${{ needs.promote.outputs.candidate_sha }}"
    elif fault == "manual-pin":
        workflow["env"] = {"RELEASE_HELPERS_SHA": "a" * 40}
    elif fault == "credentials":
        checkout["persist-credentials"] = True
    elif fault == "missing-source":
        jobs["record-promotion"]["needs"] = ["merge-promotion"]
    elif fault == "unqualified-signer":
        jobs["python-artifacts"]["needs"].remove("source-units")
    elif fault == "always-signer":
        jobs["python-artifacts"]["if"] = "always()"
    elif fault == "bootstrap-write":
        jobs["gate"]["permissions"]["contents"] = "write"
    elif fault == "unit-write":
        jobs["source-units"]["permissions"]["id-token"] = "write"
    elif fault == "dispatch-source":
        jobs["gate"]["steps"][-1]["run"] += "\nsha=${{ github.event.client_payload.sha }}"
    else:
        jobs["gate"]["steps"][-1]["run"] = 'sha="$(git rev-parse origin/dev)"'
    with pytest.raises(UnitEvidenceError):
        control.verify_helper_selection(workflow)


@pytest.mark.parametrize("fault", ["input", "producer", "attestation", "order", "authority"])
def test_complete_source_controller_mismatch_is_caught_before_publication(fault):
    workflow = _workflow()
    publisher = workflow["jobs"]["publish-python-artifacts"]
    if fault == "input":
        publisher["steps"][-1]["run"] = "python publish_python_artifacts.py publish-candidate"
    elif fault == "producer":
        publisher["steps"].pop(1)
    elif fault == "attestation":
        publisher["steps"].pop(2)
    elif fault == "order":
        publisher["steps"][1], publisher["steps"][2] = publisher["steps"][2], publisher["steps"][1]
    else:
        publisher["permissions"]["id-token"] = "read"
    with pytest.raises(UnitEvidenceError, match="complete source"):
        control.verify_controller_inputs(workflow, {})


def test_new_required_helper_input_is_automatically_a_controller_contract():
    workflow = _workflow()
    source = b'parser.add_argument("--future-required-input", required=True)'
    with pytest.raises(UnitEvidenceError, match="future-required-input"):
        control.verify_controller_inputs(workflow, {"publish_python_artifacts.py": source})
    workflow["jobs"]["publish-python-artifacts"]["steps"][-1]["run"] += " --future-required-input value"
    control.verify_controller_inputs(workflow, {"publish_python_artifacts.py": source})


def test_required_helper_inputs_expand_bounded_literal_declarations_only():
    source = b'for name in ("source", "base"):\n    parser.add_argument(f"--{name}", required=True)\n'
    assert control.required_arguments(source) == {"--source", "--base"}
    with pytest.raises(UnitEvidenceError, match="bounded literal loop"):
        control.required_arguments(b"parser.add_argument(unreviewed_name, required=True)")


def test_active_source_coherent_controller_mismatch_is_not_a_bootstrap_warning():
    active = _workflow()
    source = b'parser.add_argument("--future-required-input", required=True)'
    prepared = deepcopy(active)
    prepared["jobs"]["publish-python-artifacts"]["steps"][-1]["run"] += " --future-required-input value"
    implementations = {"publish_python_artifacts.py": source}
    control.verify_controller_inputs(prepared, implementations)
    with pytest.raises(UnitEvidenceError, match="future-required-input"):
        control.active_controller_compatibility(active, implementations)
    assert control.active_controller_compatibility(prepared, implementations) == (True, "")


@pytest.mark.parametrize("provided", [False, True], ids=["missing", "matching"])
def test_real_cli_validates_main_only_helpers_against_main_inventory(
    tmp_path, monkeypatch, capsys, provided,
):
    import importlib.util
    import json
    from pathlib import Path
    import subprocess
    import sys

    import yaml

    script = Path(__file__).resolve().parents[1] / "check-release-controller-compatibility.py"
    spec = importlib.util.spec_from_file_location("synthetic_controller_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    prepared = _workflow()
    main = deepcopy(prepared)
    main["jobs"]["main-only"] = {
        "needs": ["gate", "source-units"],
        "steps": [
            deepcopy(main["jobs"]["python-artifacts"]["steps"][0]),
            {"run": "python reviewed-helpers/tools/main_only.py" + (" --main-input value" if provided else "")},
        ],
    }
    _source(tmp_path, ".github/workflows/validate-and-promote.yml", yaml.safe_dump(prepared).encode())
    _source(tmp_path, "tools/main_only.py", b'parser.add_argument("--main-input", required=True)')
    _source(tmp_path, "tools/promotion-unit-manifest.json", b"{}")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "verify_helper_contract", lambda *args, **kwargs: set())
    monkeypatch.setattr(sys, "argv", ["check-controller", "--main-ref", "origin/main"])

    def read_git(arguments, **kwargs):
        assert arguments[:2] == ["git", "--no-replace-objects"]
        result = "a" * 40 + "\n" if arguments[2] == "rev-parse" else yaml.safe_dump(main).encode()
        return subprocess.CompletedProcess(arguments, 0, result, "")

    monkeypatch.setattr(subprocess, "run", read_git)
    if provided:
        assert cli.main() == 0
        assert json.loads(capsys.readouterr().out)["active_compatible"] is True
    else:
        with pytest.raises(UnitEvidenceError, match="main_only.py missing required inputs: --main-input"):
            cli.main()


@pytest.mark.parametrize("separator", ["\n", "; ", " && ", " || "],
                         ids=["newline", "semicolon", "and", "or"])
def test_required_inputs_are_checked_for_each_helper_invocation(separator):
    workflow = _workflow()
    workflow["jobs"]["compose-candidate"]["steps"].append({
        "run": "python reviewed-helpers/tools/entry.py" + separator
        + "python reviewed-helpers/tools/entry.py --input value",
    })
    with pytest.raises(UnitEvidenceError, match="entry.py missing required inputs: --input"):
        control.verify_controller_inputs(workflow, {
            "entry.py": b'parser.add_argument("--input", required=True)',
        })


def test_captured_call_with_a_valid_later_call_is_not_controller_compatibility():
    workflow = _workflow()
    workflow["jobs"]["compose-candidate"]["steps"].append({
        "run": "result=$(python reviewed-helpers/tools/entry.py)\n"
        "python reviewed-helpers/tools/entry.py --input value",
    })
    with pytest.raises(UnitEvidenceError, match="helper command substitution"):
        control.verify_controller_inputs(workflow, {
            "entry.py": b'parser.add_argument("--input", required=True)',
        })


@pytest.mark.parametrize("tail", ["# --input value", "echo --input value", "python other.py --input value"])
def test_other_commands_or_comments_cannot_supply_a_helper_input(tail):
    workflow = _workflow()
    workflow["jobs"]["compose-candidate"]["steps"].append({
        "run": "python reviewed-helpers/tools/entry.py\n" + tail,
    })
    with pytest.raises(UnitEvidenceError, match="missing required inputs"):
        control.verify_controller_inputs(workflow, {
            "entry.py": b'parser.add_argument("--input", required=True)',
        })


@pytest.mark.parametrize("arguments,valid", [
    (["--input"], False), (["--input", "--another"], False),
    (["--input="], False), (["--input=value"], True), (["--alias", "value"], True),
])
def test_required_argument_values_and_aliases_match_the_own_invocation(arguments, valid):
    source = b'parser.add_argument("--input", "--alias", required=True)'
    assert control.required_arguments(source) == {"--input"}
    assert control._has_input(source, "--input", arguments) is valid


@pytest.mark.parametrize("mode", ["legacy", "zero-pin", "hybrid"])
def test_only_explicit_legacy_helper_selection_can_await_bootstrap(mode):
    active = _workflow()
    active["env"] = {"RELEASE_HELPERS_SHA": ("0" if mode == "zero-pin" else "a") * 40}
    for name in ("gate", *control.HELPER_JOBS):
        active["jobs"][name]["steps"][0]["with"]["ref"] = "${{ env.RELEASE_HELPERS_SHA }}"
    if mode == "hybrid":
        active["jobs"]["python-artifacts"]["steps"][0]["with"]["ref"] = control.SOURCE_REF
    if mode == "legacy":
        compatible, reason = control.active_controller_compatibility(active, {})
        assert compatible is False and "manual helper pin" in reason
    else:
        with pytest.raises(UnitEvidenceError, match="manual helper pin"):
            control.active_controller_compatibility(active, {})


def _source(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def test_transitive_and_literal_dynamic_helper_imports_are_discovered(tmp_path):
    for name, raw in {
        "tools/entry.py": (
            b'from pathlib import Path\nfrom promote_release import _load_module\nimport helper\n'
            b'_load_module(Path(__file__).with_name("dynamic.py"), "loaded")'
        ),
        "tools/helper.py": b"from library import decision",
        "tools/dynamic.py": b"def build(): return 1",
        "libs/example/library.py": b"def decision(): return True",
    }.items():
        _source(tmp_path, name, raw)
    sources, decisions = helpers.helper_closure(tmp_path, entrypoints=("entry",))
    assert sources == {
        "tools/entry.py", "tools/helper.py", "tools/dynamic.py", "libs/example/library.py",
    }
    assert decisions == {"tools/dynamic.py"}


@pytest.mark.parametrize("raw", [
    b"_load_module(unreviewed_path, 'loaded')",
    b"exec(downloaded_code)", b"importlib.import_module(selected_name)",
])
def test_unregistered_executable_input_cannot_hide_outside_qualification(tmp_path, raw):
    _source(tmp_path, "tools/entry.py", raw)
    with pytest.raises(UnitEvidenceError, match="release implementation"):
        helpers.helper_closure(tmp_path, entrypoints=("entry",))


def test_file_spec_implementation_is_in_the_required_source_and_decision_closure(tmp_path):
    _source(tmp_path, "tools/entry.py", (
        b"import importlib.util\nfrom pathlib import Path\n"
        b'spec = importlib.util.spec_from_file_location("loaded", Path(__file__).with_name("loaded.py"))\n'
        b"module = importlib.util.module_from_spec(spec)\n"
        b"spec.loader.exec_module(module)\n"
    ))
    _source(tmp_path, "tools/loaded.py", b"def decide():\n    return True\n")
    sources, decisions = helpers.helper_closure(tmp_path, entrypoints=("entry",))
    assert sources == {"tools/entry.py", "tools/loaded.py"}
    assert decisions == {"tools/loaded.py"}


@pytest.mark.parametrize("relative", ["libs/example/loaded.py", "other/loaded.py"])
def test_file_loaded_decisions_require_coverage_outside_tools(tmp_path, relative):
    _source(tmp_path, "tools/entry.py", (
        "import importlib.util\nfrom pathlib import Path\n"
        f'spec = importlib.util.spec_from_file_location("loaded", Path(__file__).parents[1] / "{relative}")\n'
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
    ).encode())
    _source(tmp_path, relative, b"def decide():\n    return True\n")
    sources, decisions = helpers.helper_closure(tmp_path, entrypoints=("entry",))
    assert sources == {"tools/entry.py", relative}
    assert decisions == {relative}


def test_dynamic_edge_enrolls_a_decision_already_visited_as_a_static_import(tmp_path):
    _source(tmp_path, "tools/entry.py", b"import loader\nimport loaded\n")
    _source(tmp_path, "tools/loader.py", (
        b"import importlib.util\nfrom pathlib import Path\n"
        b'spec = importlib.util.spec_from_file_location("loaded", Path(__file__).parents[1] / "libs/example/loaded.py")\n'
        b"module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
    ))
    relative = "libs/example/loaded.py"
    _source(tmp_path, relative, b"def decide():\n    return True\n")
    sources, decisions = helpers.helper_closure(tmp_path, entrypoints=("entry",))
    assert relative in sources and relative in decisions


@pytest.mark.parametrize("relative", ["tools/native_daemon_artifacts.py", "tools/unrelated.py"])
@pytest.mark.parametrize("body", [
    'exec(Path("helper.py").read_text())',
    'exec(compile(Path("helper.py").read_bytes(), "helper.py", "exec"))',
])
def test_release_helpers_have_no_native_builder_exec_exception(tmp_path, relative, body):
    import ast

    raw = "from pathlib import Path\n\ndef build():\n    " + body + "\n"
    tree = ast.parse(raw)
    assert helpers._registered_execs(tree, relative) == set()
    _source(tmp_path, relative, raw.encode())
    module = relative.removeprefix("tools/").removesuffix(".py")
    with pytest.raises(UnitEvidenceError, match="unregistered dynamic release implementation"):
        helpers.helper_closure(tmp_path, entrypoints=(module,))


def _contract(root):
    sources = {}
    _source(root, ".github/workflows/validate-and-promote.yml", b"jobs: {}\n")
    for name in helpers.ENTRYPOINTS:
        relative = f"tools/{name}.py"
        _source(root, relative, b"def decision():\n    return True\n")
        sources[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    for name in set(helpers.BUILD_INPUTS) | helpers.BUILD_DECISIONS:
        _source(root, name, b"def build():\n    return True\n")
        sources[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    return {
        "sources": sources, "coverage_targets": [
            *[f"tools/{name}.py" for name in helpers.ENTRYPOINTS],
            *sorted(helpers.BUILD_DECISIONS),
        ], "nodes": ["tools/promotion_unit_tests/test_example.py::test_decision"],
    }


def test_hash_only_non_tools_file_loaded_helper_is_not_qualified(tmp_path):
    manifest = _contract(tmp_path)
    entry = "tools/tag_promotion.py"
    loaded = "libs/example/loaded.py"
    _source(tmp_path, entry, (
        b"import importlib.util\nfrom pathlib import Path\n"
        b'spec = importlib.util.spec_from_file_location("loaded", Path(__file__).parents[1] / "libs/example/loaded.py")\n'
        b"module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
        b"def decision():\n    return True\n"
    ))
    _source(tmp_path, loaded, b"def decide():\n    return True\n")
    for relative in (entry, loaded):
        manifest["sources"][relative] = hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest()
    with pytest.raises(UnitEvidenceError, match="uncovered=libs/example/loaded.py"):
        helpers.verify_helper_contract(tmp_path, manifest)
    manifest["coverage_targets"].append(loaded)
    assert loaded in helpers.verify_helper_contract(tmp_path, manifest)
    coverage = {"files": {
        name: {"contexts": {"2": [manifest["nodes"][0] + "|call"]}}
        for name in manifest["coverage_targets"] if name != loaded
    }}
    with pytest.raises(UnitEvidenceError, match="positive call coverage: libs/example/loaded.py"):
        helpers.verify_helper_coverage(tmp_path, manifest, coverage)


@pytest.mark.parametrize("fault", ["none", "source", "coverage", "build-input", "changed"])
def test_new_helper_and_native_build_inputs_require_binding_and_coverage(tmp_path, fault):
    manifest = _contract(tmp_path)
    path = "tools/tag_promotion.py"
    if fault == "source":
        manifest["sources"].pop(path)
    elif fault == "coverage":
        manifest["coverage_targets"].remove(path)
    elif fault == "build-input":
        manifest["sources"].pop("libs/agent-procutil/setup.py")
    elif fault == "changed":
        (tmp_path / path).write_bytes(b"def changed(): return False")
    if fault == "none":
        assert path in helpers.verify_helper_contract(tmp_path, manifest)
    else:
        with pytest.raises(UnitEvidenceError):
            helpers.verify_helper_contract(tmp_path, manifest)


def test_hash_binding_or_import_only_coverage_is_not_decision_qualification(tmp_path):
    manifest = _contract(tmp_path)
    coverage = {"files": {
        name: {"contexts": {"2": [manifest["nodes"][0] + "|call"]}}
        for name in manifest["coverage_targets"]
    }}
    helpers.verify_helper_coverage(tmp_path, manifest, coverage)
    untested = deepcopy(coverage)
    untested["files"]["tools/tag_promotion.py"]["contexts"] = {"1": [manifest["nodes"][0] + "|call"]}
    with pytest.raises(UnitEvidenceError, match="positive call coverage"):
        helpers.verify_helper_coverage(tmp_path, manifest, untested)


def test_controller_discovered_command_cannot_escape_qualified_source_or_coverage(tmp_path):
    manifest = _contract(tmp_path)
    new = "tools/new_release_command.py"
    _source(tmp_path, new, b"def publish():\n    return True\n")
    development = tmp_path / "development"
    _source(
        development, ".github/workflows/validate-and-promote.yml",
        b"jobs:\n  publisher:\n    steps:\n      - run: python reviewed-helpers/tools/new_release_command.py\n",
    )
    with pytest.raises(UnitEvidenceError, match="unbound=.*new_release_command"):
        helpers.verify_helper_contract(tmp_path, manifest, development_root=development)
    manifest["sources"][new] = hashlib.sha256((tmp_path / new).read_bytes()).hexdigest()
    with pytest.raises(UnitEvidenceError, match="uncovered=.*new_release_command"):
        helpers.verify_helper_contract(tmp_path, manifest, development_root=development)
    manifest["coverage_targets"].append(new)
    assert new in helpers.verify_helper_contract(tmp_path, manifest, development_root=development)
    controller = (development / ".github/workflows/validate-and-promote.yml").read_text()
    assert new in helpers.verify_helper_contract(tmp_path, manifest, controllers=(controller,))


def test_import_inside_a_test_is_not_executable_decision_body_coverage(tmp_path):
    import coverage as collector
    import importlib.util

    manifest = _contract(tmp_path)
    name = "tools/tag_promotion.py"
    context = manifest["nodes"][0] + "|call"
    cov = collector.Coverage(
        data_file=None, config_file=False, include=[str(tmp_path / name)], context=context,
    )
    with cov.collect():
        spec = importlib.util.spec_from_file_location("synthetic_release_helper", tmp_path / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    files = {
        target: {"contexts": {"2": [context]}} for target in manifest["coverage_targets"]
    }
    files[name] = {"contexts": {
        str(line): values for line, values in cov.get_data().contexts_by_lineno(str(tmp_path / name)).items()
    }}
    assert files[name]["contexts"] == {"1": [context]}
    with pytest.raises(UnitEvidenceError, match="positive call coverage"):
        helpers.verify_helper_coverage(tmp_path, manifest, {"files": files})
    with cov.collect():
        assert module.decision() is True
    files[name]["contexts"] = {
        str(line): values for line, values in cov.get_data().contexts_by_lineno(str(tmp_path / name)).items()
    }
    helpers.verify_helper_coverage(tmp_path, manifest, {"files": files})


def test_native_compiler_source_has_no_release_unit_admission(tmp_path):
    native = "libs/agent-procutil/src/agent_procutil/daemon_instance_native.c"
    with pytest.raises(UnitEvidenceError):
        relative_file(native)
    test = "tools/promotion_unit_tests/test_example.py"
    manifest = {
        "profile": "copilot-extensions.promotion-units/v1",
        "sources": {test: "a" * 64, native: "b" * 64}, "nodes": [test + "::test_case"],
        "coverage_targets": [native], "max_seconds": 300,
    }
    with pytest.raises(UnitEvidenceError):
        admit_manifest(manifest)
    with pytest.raises(UnitEvidenceError):
        relative_file("tools/arbitrary.c")


def test_legacy_inventory_requires_a_literal_complete_portfolio(tmp_path):
    import candidate_qualification_inventory as inventory

    _source(tmp_path, ".github/workflows/ci.yml", b"name: CI\njobs:\n  checks:\n    name: checks\n")
    with pytest.raises(inventory.CandidateValidationError, match="literal portfolio"):
        inventory.required_jobs(tmp_path)
    _source(tmp_path, "tools/ci_plugin_partitions.py", b'_SMOKE_PARTITIONS = {"alpha": 2}\n')
    rows, _ = inventory._smoke_rows(tmp_path, {"alpha", "beta"})
    assert rows == ["alpha (partition 1/2)", "alpha (partition 2/2)", "beta"]


def test_helper_git_environment_cannot_redirect_repository_selection(monkeypatch):
    from coverage_guided_selection.ancestor_resolution import scrubbed_git_env
    from coverage_guided_selection.correlation import (
        BaselineCorrelationError, build_pointer, require_measured_commit,
    )

    monkeypatch.setenv("GIT_DIR", "foreign")
    monkeypatch.setenv("git_work_tree", "foreign")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "foreign")
    monkeypatch.setenv("RELEASE_UNIT_TEST_VALUE", "retained")
    clean = scrubbed_git_env()
    assert not {"GIT_DIR", "git_work_tree", "GIT_CONFIG_KEY_0"} & clean.keys()
    assert clean["GIT_TERMINAL_PROMPT"] == "0"
    assert clean["RELEASE_UNIT_TEST_VALUE"] == "retained"
    pointer = build_pointer("example", "a" * 40)
    assert require_measured_commit(pointer) == "a" * 40
    assert pointer["release_tag"].endswith("a" * 40)
    with pytest.raises(BaselineCorrelationError):
        require_measured_commit({})


def test_dynamic_decisions_cannot_be_hash_only_or_diverge_from_a_tested_copy(tmp_path):
    manifest = _contract(tmp_path)
    path = "libs/installer-engine/installer_dependency_config.py"
    manifest["coverage_targets"].remove(path)
    with pytest.raises(UnitEvidenceError, match="uncovered"):
        helpers.verify_helper_contract(tmp_path, manifest)
    manifest["coverage_targets"].append(path)
    copied = "plugins/agent-containers/scripts/standalone_build.py"
    (tmp_path / copied).write_bytes(b"def foreign(): return True")
    manifest["sources"][copied] = hashlib.sha256((tmp_path / copied).read_bytes()).hexdigest()
    with pytest.raises(UnitEvidenceError, match="qualified origin"):
        helpers.verify_helper_contract(tmp_path, manifest)


def test_mode_required_optional_argument_is_shared_with_runtime_validation():
    declaration = (
        (("publish-candidate",), ("candidate-units-v3",), ("--future-mode-input",)),
    )
    source = (
        "CONTROLLER_REQUIRED_ARGUMENTS = " + repr(declaration)
        + '\nparser.add_argument("--future-mode-input")\n'
        + 'parser.add_argument("--qualification-profile", default="legacy-ci-v1")\n'
    ).encode()
    workflow = _workflow()
    publish = workflow["jobs"]["publish-python-artifacts"]["steps"][-1]
    publish["run"] += " --qualification-profile candidate-units-v3"
    with pytest.raises(UnitEvidenceError, match="future-mode-input"):
        control.verify_controller_inputs(workflow, {"publish_python_artifacts.py": source})
    assert control.required_mode_arguments("publish-candidate", "candidate-units-v3", declaration) == {
        "--future-mode-input",
    }
    publish["run"] += " --future-mode-input value"
    control.verify_controller_inputs(workflow, {"publish_python_artifacts.py": source})
    publish["run"] = publish["run"].replace("candidate-units-v3", '"$UNREVIEWED_PROFILE"')
    with pytest.raises(UnitEvidenceError, match="profile is not a literal"):
        control.verify_controller_inputs(workflow, {"publish_python_artifacts.py": source})


def test_real_publisher_refuses_missing_mode_input_before_any_release_actor(tmp_path, capsys):
    import publish_python_artifacts as publisher

    with pytest.raises(SystemExit) as rejected:
        publisher.main([
            "publish-candidate", "--directory", str(tmp_path), "--repository", "example/plugins",
            "--signer-digest", "a" * 40, "--source-digest", "a" * 40, "--source-ref", "refs/heads/main",
            "--candidate-commit", "b" * 40, "--dev-head", "c" * 40, "--main-before", "d" * 40,
            "--promotion-run", "123-1", "--candidate-root", str(tmp_path),
            "--qualification-receipt", str(tmp_path / "receipt"), "--unit-packet-dir", str(tmp_path),
            "--dev-root", str(tmp_path),
            "--qualification-profile", "candidate-units-v3",
        ])
    assert rejected.value.code == 2
    assert "--unit-manifest" in capsys.readouterr().err

"""Data-only compatibility between Main's controller and source implementations."""
from __future__ import annotations

import ast
import re

from promotion_unit_contract import UnitEvidenceError
from release_controller_shell import controller_scripts, helper_references, script_invocations

SOURCE_REF = "${{ needs.gate.outputs.sha }}"
CONTROL_REF = "${{ github.workflow_sha }}"
HELPER_JOBS = (
    "compose-candidate", "source-units", "build-python-artifacts", "python-artifacts",
    "promote", "qualify-candidate", "publish-python-artifacts", "record-promotion",
)


def verify_helper_selection(workflow: dict) -> None:
    """Only the read-only bootstrap executes Main's own implementation."""
    if "RELEASE_HELPERS_SHA" in workflow.get("env", {}):
        raise UnitEvidenceError("release implementations must not require a manual helper pin")
    jobs = workflow["jobs"]
    selected = {
        name for name, job in jobs.items()
        if any(step.get("with", {}).get("path") == "reviewed-helpers" for step in job.get("steps", []))
    }
    if not {"gate", *HELPER_JOBS} <= selected:
        raise UnitEvidenceError("a required release implementation checkout is missing")
    for name in selected:
        job = jobs[name]
        helpers = [
            step["with"] for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
            and step.get("with", {}).get("path") == "reviewed-helpers"
        ]
        expected = CONTROL_REF if name == "gate" else SOURCE_REF
        if len(helpers) != 1 or helpers[0] != {
            "ref": expected, "path": "reviewed-helpers", "persist-credentials": False,
        }:
            raise UnitEvidenceError(f"{name}: release implementation is not tied to its admitted identity")
        if name != "gate":
            needs = job["needs"]
            if "gate" not in ([needs] if isinstance(needs, str) else needs):
                raise UnitEvidenceError(f"{name}: admitted source output is not a direct prerequisite")
    if jobs["gate"]["permissions"] != {
        "contents": "read", "actions": "read", "pull-requests": "read",
    }:
        raise UnitEvidenceError("bootstrap implementation must remain read-only")
    signer = jobs["python-artifacts"]
    if "source-units" not in signer["needs"] or re.search(
        r"\b(?:always|failure|cancelled)\s*\(", signer.get("if", ""),
    ):
        raise UnitEvidenceError("signing implementation must wait for successful source qualification")
    if (
        jobs["source-units"]["permissions"] != {"contents": "read"}
        or jobs["source-units"].get("continue-on-error", False) is not False
        or any(step.get("continue-on-error", False) is not False
               for step in jobs["source-units"]["steps"])
    ):
        raise UnitEvidenceError("source qualification cannot have release authority")
    checks = "\n".join(step.get("run", "") for step in jobs["gate"]["steps"])
    if (
        'git merge-base --is-ancestor "$sha" origin/dev' not in checks
        or 'sha="$(git rev-parse origin/dev)"' not in checks
        or "github.event.client_payload" in checks
    ):
        raise UnitEvidenceError("source implementation selection requires Main's live Dev ancestry admission")


def required_arguments(source: bytes) -> set[str]:
    """Discover new required CLI inputs without executing a source helper."""
    required = set()
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        if any(
            keyword.arg == "required" and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True for keyword in node.keywords
        ):
            flags = [value.value for value in node.args
                     if isinstance(value, ast.Constant) and isinstance(value.value, str)
                     and value.value.startswith("--")]
            expanded_loop = False
            if not flags and node.args and isinstance(node.args[0], ast.JoinedStr):
                expression = node.args[0].values
                parent = parents.get(node)
                while parent is not None and not isinstance(parent, ast.For):
                    parent = parents.get(parent)
                if (
                    len(expression) == 2 and isinstance(expression[0], ast.Constant)
                    and expression[0].value == "--" and isinstance(expression[1], ast.FormattedValue)
                    and expression[1].conversion == -1 and expression[1].format_spec is None
                    and isinstance(expression[1].value, ast.Name)
                    and isinstance(parent, ast.For) and isinstance(parent.target, ast.Name)
                    and parent.target.id == expression[1].value.id
                    and isinstance(parent.iter, (ast.Tuple, ast.List))
                    and all(isinstance(value, ast.Constant) and isinstance(value.value, str)
                            for value in parent.iter.elts)
                ):
                    flags = ["--" + value.value for value in parent.iter.elts]
                    expanded_loop = True
            if not flags:
                raise UnitEvidenceError("required release CLI input has no literal long option or bounded literal loop")
            required.update(flags if expanded_loop else flags[:1])
    return required


def required_mode_arguments(operation: str, profile: str, declaration: tuple) -> set[str]:
    """The same mode requirements drive the real CLI and controller checking."""
    return {
        flag for operations, profiles, flags in declaration
        if operation in operations and (not profiles or profile in profiles)
        for flag in flags
    }


def helper_implementations(workflow: dict | str) -> set[str]:
    try:
        scripts = controller_scripts(workflow) if isinstance(workflow, str) else [
            step.get("run", "") for job in workflow["jobs"].values() for step in job.get("steps", [])
        ]
        return {name for script in scripts for name in helper_references(script)}
    except ValueError as error:
        raise UnitEvidenceError(f"unsupported controller helper discovery: {error}") from error


def _mode_inputs(source: bytes, name: str, arguments: list[str]) -> set[str]:
    tree = ast.parse(source)
    declarations = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in (
                "CONTROLLER_REQUIRED_ARGUMENTS", "SOURCE_CANDIDATE_REQUIRED_ARGUMENTS",
            ):
                if target.id in declarations:
                    raise UnitEvidenceError(f"{name}: ambiguous conditional controller input declaration")
                declarations[target.id] = node.value
    if not declarations:
        return set()
    declaration = tuple(row for value in declarations.values() for row in ast.literal_eval(value))
    if not arguments or not re.fullmatch(r"[a-z][a-z0-9_-]*", arguments[0]):
        raise UnitEvidenceError(f"{name}: controller operation is not a literal")
    profiles = []
    for index, argument in enumerate(arguments):
        if argument == "--qualification-profile":
            if index + 1 == len(arguments):
                raise UnitEvidenceError(f"{name}: conditional controller profile has no value")
            profiles.append(arguments[index + 1])
        elif argument.startswith("--qualification-profile="):
            profiles.append(argument.split("=", 1)[1])
    if any(not re.fullmatch(r"[a-z0-9-]+", value) for value in profiles):
        raise UnitEvidenceError(f"{name}: conditional controller profile is not a literal")
    selected = profiles[-1] if profiles else None
    if selected is None:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--qualification-profile"
            ):
                selected = next((
                    keyword.value.value for keyword in node.keywords
                    if keyword.arg == "default" and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ), None)
    if selected is None and any(profiles for _, profiles, _ in declaration):
        raise UnitEvidenceError(f"{name}: conditional controller profile is not a literal")
    return required_mode_arguments(arguments[0], selected or "", declaration)


def _has_input(source: bytes, flag: str, arguments: list[str]) -> bool:
    aliases, takes_value = {flag}, True
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        names = {value.value for value in node.args
                 if isinstance(value, ast.Constant) and isinstance(value.value, str)}
        if flag not in names:
            continue
        aliases = {name for name in names if name.startswith("-")}
        fields = {item.arg: item.value.value for item in node.keywords
                  if isinstance(item.value, ast.Constant)}
        takes_value = fields.get("action") not in (
            "store_true", "store_false", "store_const", "count", "help", "version",
        ) and fields.get("nargs") not in (0, "?", "*")
        break
    found = False
    for index, argument in enumerate(arguments):
        matching = next((alias for alias in aliases if argument == alias
                         or argument.startswith(alias + "=")), None)
        if matching is None:
            continue
        found = True
        if argument != matching:
            if not takes_value or not argument.split("=", 1)[1]:
                return False
        elif takes_value:
            if index + 1 == len(arguments) or not arguments[index + 1]:
                return False
            value = arguments[index + 1]
            if value.startswith("-") and value != "-" and not re.fullmatch(r"-\d+(?:\.\d+)?", value):
                return False
    return found


def verify_controller_inputs(workflow: dict, implementations: dict[str, bytes]) -> None:
    """Check every helper call and the complete-source producer/signer boundary."""
    jobs = workflow["jobs"]
    calls = []
    for job_name, job in jobs.items():
        for step in job.get("steps", []):
            try:
                calls.extend((job_name, name, argv) for name, argv in script_invocations(step.get("run", "")))
            except ValueError as error:
                raise UnitEvidenceError(f"{job_name}: unsupported helper invocation: {error}") from error
    for name, source in implementations.items():
        required = required_arguments(source)
        invocations = [
            (job_name, argv) for job_name, helper, argv in calls if helper == name
        ]
        if not invocations:
            raise UnitEvidenceError(f"controller never invokes required implementation: {name}")
        for job_name, argv in invocations:
            missing = [
                flag for flag in sorted(required | _mode_inputs(source, name, argv))
                if not _has_input(source, flag, argv)
            ]
            if missing:
                raise UnitEvidenceError(f"{job_name}: {name} missing required inputs: {','.join(missing)}")
    publisher = jobs["publish-python-artifacts"]
    steps = publisher["steps"]
    build = [index for index, step in enumerate(steps)
             if "promotion_source_archive.py" in step.get("run", "")]
    attest = [index for index, step in enumerate(steps)
              if step.get("uses", "").startswith("actions/attest-build-provenance@")
              and step.get("with", {}).get("subject-path") == "${{ runner.temp }}/complete-source/*"]
    publish = [(index, step) for index, step in enumerate(steps) if step.get("id") == "publish"]
    if (
        len(build) != 1 or len(attest) != 1 or len(publish) != 1
        or not build[0] < attest[0] < publish[0][0]
        or '--complete-source-dir "$RUNNER_TEMP/complete-source"' not in publish[0][1]["run"]
        or publisher["permissions"].get("attestations") != "write"
        or publisher["permissions"].get("id-token") != "write"
    ):
        raise UnitEvidenceError("controller must construct, attest and pass complete source before publication")


def _legacy_bootstrap_controller(workflow: dict) -> bool:
    pin = workflow.get("env", {}).get("RELEASE_HELPERS_SHA")
    if (
        not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{40}", pin)
        or pin == "0" * 40
    ):
        return False
    selected = [
        (name, step["with"]) for name, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if step.get("with", {}).get("path") == "reviewed-helpers"
    ]
    return (
        {"gate", *HELPER_JOBS} <= {name for name, _ in selected}
        and all(checkout.get("ref") == "${{ env.RELEASE_HELPERS_SHA }}"
                and checkout.get("persist-credentials") is False for _, checkout in selected)
    )


def active_controller_compatibility(workflow: dict, implementations: dict[str, bytes]) -> tuple[bool, str]:
    """Only the explicit legacy pin generation may await a separate bootstrap."""
    try:
        verify_controller_inputs(workflow, implementations)
        verify_helper_selection(workflow)
    except UnitEvidenceError as error:
        if _legacy_bootstrap_controller(workflow):
            return False, str(error)
        raise
    return True, ""

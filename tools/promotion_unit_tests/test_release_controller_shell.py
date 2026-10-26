"""Positive, data-only unit coverage for controller invocation boundaries."""
from __future__ import annotations

import pytest

from release_controller_shell import controller_scripts, helper_references, script_invocations


PROMOTER = '''args=(--repo "$GITHUB_WORKSPACE" --dev-ref "$DEV_SHA" --main-ref origin/main
  --python-artifacts-dir python-artifacts
  --expected-candidate-sha "$EXPECTED_CANDIDATE_SHA"
  --frozen-candidate-dir "$RUNNER_TEMP/frozen-candidate")
if [ "$QUALIFICATION_ONLY" = "true" ]; then args+=(--qualification-only); fi
if [ "${{ github.event_name }}" = "workflow_dispatch" ] && [ "$PUSH_INPUT" = "false" ]; then
  land=false
else
  land=true
  args+=(--push --candidate-branch "$CANDIDATE_BRANCH"
    --promotion-time "$PROMOTION_TIME" --github-output "$GITHUB_OUTPUT")
fi
output=$("$RUNNER_TEMP/artifact-validator/bin/python" -I -B - "$GITHUB_WORKSPACE/reviewed-helpers/tools" "${args[@]}" <<'PY'
import runpy
import sys
from pathlib import Path

tools = Path(sys.argv.pop(1)).resolve(strict=True)
sys.path.insert(0, str(tools))
runpy.run_path(str(tools / "promote_release.py"), run_name="__main__")
PY
)
echo "$output"
echo "land=$land" >> "$GITHUB_OUTPUT"
'''


@pytest.mark.parametrize("separator", ["\n", "; ", " && ", " || "],
                         ids=["newline", "semicolon", "and", "or"])
def test_same_helper_calls_do_not_share_required_inputs(separator):
    script = separator.join([
        "python reviewed-helpers/tools/helper.py verify",
        "python reviewed-helpers/tools/helper.py verify --input candidate.json",
    ])
    assert script_invocations(script) == [
        ("helper.py", ["verify"]),
        ("helper.py", ["verify", "--input", "candidate.json"]),
    ]


@pytest.mark.parametrize("donor", [
    "# --input candidate.json",
    "echo --input candidate.json",
    "printf '%s' '--input candidate.json'",
    "python unrelated.py --input candidate.json",
], ids=["comment", "echo", "printf", "unrelated-python"])
def test_comments_and_unrelated_commands_do_not_donate_arguments(donor):
    result = script_invocations("python reviewed-helpers/tools/helper.py verify\n" + donor)
    assert result[0] == ("helper.py", ["verify"])
    assert all("--input" not in argv for name, argv in result if name == "helper.py")


@pytest.mark.parametrize("interpreter", [
    "python", "python3", '"/usr/bin/python3"',
    '"$RUNNER_TEMP/artifact-validator/bin/python"',
], ids=["python", "python3", "absolute", "quoted-runner"])
def test_direct_calls_preserve_literal_profiles_and_continuations(interpreter):
    script = (
        f'{interpreter} -I -B reviewed-helpers/tools/publish_python_artifacts.py verify \\\n'
        '  --qualification-profile "source-candidate" \\\n'
        '  --input "$RUNNER_TEMP/input.json" # --bogus ignored\n'
    )
    assert script_invocations(script) == [("publish_python_artifacts.py", [
        "verify", "--qualification-profile", "source-candidate",
        "--input", "$RUNNER_TEMP/input.json",
    ])]


@pytest.mark.parametrize("file", [
    "publish_python_artifacts.py", "promotion_source_archive.py", "tools/name.py",
], ids=["bare-publisher", "bare-archive", "tools-relative"])
def test_fixture_file_tokens_are_supported(file):
    assert script_invocations(f"python {file} --input candidate.json") == [
        (file.rsplit("/", 1)[-1], ["--input", "candidate.json"]),
    ]


def test_actual_promoter_runpy_and_only_unconditional_base_arguments():
    assert helper_references(PROMOTER) == {"promote_release.py"}
    assert script_invocations(PROMOTER) == [("promote_release.py", [
        "--repo", "$GITHUB_WORKSPACE", "--dev-ref", "$DEV_SHA", "--main-ref", "origin/main",
        "--python-artifacts-dir", "python-artifacts",
        "--expected-candidate-sha", "$EXPECTED_CANDIDATE_SHA",
        "--frozen-candidate-dir", "$RUNNER_TEMP/frozen-candidate",
    ])]


def test_direct_base_array_snapshot_and_conditional_append():
    script = '''args=(verify --qualification-profile 'source-candidate'
  --input "$INPUT" --label "a;b && c")
python reviewed-helpers/tools/helper.py "${args[@]}"
if [ "$MODE" = "extra" ]; then args+=(--optional "$EXTRA"); fi
python reviewed-helpers/tools/helper.py "${args[@]}"
'''
    expected = ["verify", "--qualification-profile", "source-candidate",
                "--input", "$INPUT", "--label", "a;b && c"]
    result = script_invocations(script)
    assert result == [("helper.py", expected), ("helper.py", expected)]
    assert result[0][1] is not result[1][1]


@pytest.mark.parametrize("script", [
    'python reviewed-helpers/tools/helper.py "${unknown[@]}"',
    'args=("$dynamic"); args=(--input x); python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=$(make_args); python reviewed-helpers/tools/helper.py "${args[@]}"',
    'if true; then args=(--input x); fi; python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=(--input x); args=(--other y); python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=(--input x); args=other; python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=(--input x); args[0]=--other; python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=(--input x); unset args; python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=(--input x); declare -a args=(--other y); python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=(--input x); args+=(--other y); python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=($(make_args)); python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=("$DYNAMIC_FLAGS"); python reviewed-helpers/tools/helper.py "${args[@]}"',
    'args=(--input x); python reviewed-helpers/tools/helper.py ${args[@]}',
    """args=(--input x); python reviewed-helpers/tools/helper.py '${args[@]}'""",
    'args=(--input x); python reviewed-helpers/tools/helper.py "${args[*]}"',
], ids=["unknown", "scalar-base-replaced", "subprocess-base", "conditional-base", "reassigned",
        "scalar-reassignment", "indexed-reassignment", "unset", "declare-reassignment",
        "unconditional-append", "dynamic-base", "dynamic-flags", "unquoted-expansion",
        "literal-expansion", "joined-expansion"])
def test_unsupported_dynamic_arrays_are_visibly_refused(script):
    with pytest.raises(ValueError):
        script_invocations(script)


@pytest.mark.parametrize("replacement", [
    'runpy.run_path(str(tools / name), run_name="__main__")',
    'runpy.run_module("promote_release", run_name="__main__")',
    'sys.argv += ["--input", "donated"]\nrunpy.run_path(str(tools / "promote_release.py"), run_name="__main__")',
    'runpy.run_path(str(tools / "promote_release.py"), run_name="not-main")',
], ids=["dynamic-path", "module-loader", "argv-mutation", "wrong-run-name"])
def test_unsupported_runpy_loaders_are_visibly_refused(replacement):
    script = PROMOTER.replace(
        'runpy.run_path(str(tools / "promote_release.py"), run_name="__main__")', replacement,
    )
    with pytest.raises(ValueError, match="indirect helper loader"):
        script_invocations(script)


def test_references_keep_literal_assignments_but_not_comments():
    script = '''# reviewed-helpers/tools/comment.py
helper="reviewed-helpers/tools/assigned.py" # reviewed-helpers/tools/ignored.py
echo "reviewed-helpers/tools/data.py"
python "$helper" --input x
'''
    assert helper_references(script) == {"assigned.py", "data.py"}
    with pytest.raises(ValueError, match="indirect helper file"):
        script_invocations(script)


def test_literal_helper_assignment_without_call_remains_a_reference():
    script = 'helper="reviewed-helpers/tools/assigned.py"\n'
    assert helper_references(script) == {"assigned.py"}
    assert script_invocations(script) == []


@pytest.mark.parametrize("capture", [
    'result=$(python reviewed-helpers/tools/helper.py)',
    'result="$(python reviewed-helpers/tools/helper.py)"',
    'result=`python reviewed-helpers/tools/helper.py`',
    'export result=$(python reviewed-helpers/tools/helper.py)',
    'echo "$(python reviewed-helpers/tools/helper.py)"',
    'result=$(python "$helper")',
    '''result=$(python -c 'import runpy; runpy.run_path(str(tools / "helper.py"))')''',
], ids=["unquoted", "quoted", "backticks", "export", "echo", "variable", "inline-runpy"])
def test_captured_helper_cannot_hide_beside_a_valid_call(capture):
    script = ('helper="reviewed-helpers/tools/helper.py"\n' + capture
              + "\npython reviewed-helpers/tools/helper.py --input x")
    with pytest.raises(ValueError, match="helper command substitution"):
        script_invocations(script)


def test_inline_runpy_loader_is_discovered_but_refused():
    script = '''python -c 'import runpy; runpy.run_path(str(tools / "helper.py"))'
'''
    assert helper_references(script) == {"helper.py"}
    with pytest.raises(ValueError, match="indirect helper loader"):
        script_invocations(script)


@pytest.mark.parametrize("prefix", ["env MODE=x", "$PYTHON", "uv run python"],
                         ids=["env-wrapper", "variable-interpreter", "uv-wrapper"])
def test_unsupported_helper_wrappers_do_not_hide_beside_a_valid_call(prefix):
    script = (f"{prefix} reviewed-helpers/tools/helper.py\n"
              "python reviewed-helpers/tools/helper.py --input x")
    with pytest.raises(ValueError, match="unsupported helper command"):
        script_invocations(script)


def test_if_and_then_python_calls_have_separate_argv():
    script = ("if python reviewed-helpers/tools/helper.py verify; then "
              "python reviewed-helpers/tools/helper.py verify --input x; fi")
    assert script_invocations(script) == [
        ("helper.py", ["verify"]), ("helper.py", ["verify", "--input", "x"]),
    ]


def test_scalar_assignment_and_export_are_not_array_mutation():
    script = '''ADMITTED_MAIN="$(git rev-parse origin/main)"
export ADMITTED_MAIN
python reviewed-helpers/tools/helper.py --input "$ADMITTED_MAIN"
'''
    assert script_invocations(script) == [("helper.py", ["--input", "$ADMITTED_MAIN"])]


def test_unrelated_associative_array_does_not_establish_helper_arguments():
    script = '''declare -A DISPATCHED=()
DISPATCHED[$NUM]=1
python reviewed-helpers/tools/helper.py --input x
'''
    assert script_invocations(script) == [("helper.py", ["--input", "x"])]


def test_echo_and_heredoc_data_are_not_python_calls():
    script = '''echo "python reviewed-helpers/tools/data.py --input x"
cat <<'DATA'
python reviewed-helpers/tools/not_a_call.py --input x
DATA
python reviewed-helpers/tools/real.py
'''
    assert script_invocations(script) == [("real.py", [])]


def test_python_heredoc_comments_are_not_references():
    script = PROMOTER.replace("import runpy", '# tools / "ignored.py"\nimport runpy')
    assert helper_references(script) == {"promote_release.py"}
    assert script_invocations(script)[0][0] == "promote_release.py"


def test_yaml_extracts_only_run_scalars_and_literal_blocks():
    workflow = '''name: Example
jobs:
  unit:
    steps:
      - name: first
        run: python reviewed-helpers/tools/first.py --input x
      - run: |
          python reviewed-helpers/tools/second.py \\
            --input y
          # run: this is shell data
      - name: example
        description: |
          run: not a step
        run: python tools/third.py
'''
    scripts = controller_scripts(workflow)
    assert len(scripts) == 3
    assert [call for script in scripts for call in script_invocations(script)] == [
        ("first.py", ["--input", "x"]), ("second.py", ["--input", "y"]), ("third.py", []),
    ]


@pytest.mark.parametrize("value", [
    ">", "|2", "", "{script: x}", "[python, helper.py]", "&anchor python helper.py",
], ids=["folded", "explicit-indent", "empty", "flow-map", "flow-list", "anchor"])
def test_unsupported_yaml_styles_are_visibly_refused(value):
    with pytest.raises(ValueError, match="run scalar"):
        controller_scripts(f"jobs:\n  example:\n    run: {value}\n      python helper.py\n")


@pytest.mark.parametrize("text", [
    "jobs: {example: {run: python helper.py}}",
    'jobs:\n  example:\n    "run": python helper.py\n',
    "jobs:\n  example:\n    run: python helper.py\n      --input x\n",
], ids=["inline-run", "quoted-key", "multiline-plain"])
def test_ambiguous_yaml_run_shapes_are_refused(text):
    with pytest.raises(ValueError):
        controller_scripts(text)


@pytest.mark.parametrize("value, expected", [
    ("'python tools/example.py --label ''literal'''", "python tools/example.py --label 'literal'"),
    ('"python tools/example.py --input x"', "python tools/example.py --input x"),
    ("python tools/example.py --input x # YAML comment", "python tools/example.py --input x"),
], ids=["single-quoted", "double-quoted", "plain-comment"])
def test_yaml_single_line_scalar_values(value, expected):
    assert controller_scripts(f"jobs:\n  example:\n    run: {value}\n") == [expected]


def test_empty_jobs_and_plain_shell_fallback():
    assert controller_scripts("jobs: {}") == []
    script = "python tools/example.py --input x\n"
    assert controller_scripts(script) == [script]
    assert controller_scripts("") == []

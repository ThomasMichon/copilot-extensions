"""Copied Bash consumer controls; no live operator mux or terminal."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "bin" / "launch-session.sh"


def _bash():
    if os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        if candidate.is_file():
            return str(candidate)
    elif binary := shutil.which("bash"):
        return binary
    pytest.skip("Bash is unavailable")


def _path(path):
    value = Path(path).as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


def _helper():
    source = SOURCE.read_text("utf-8")
    start = source.index("_aw_mux_core() {")
    return source[start:source.index("\n}\n", start) + 3]


def _run(script, tmp_path):
    path = tmp_path / "consumer.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run(
        [_bash(), "--noprofile", "--norc", _path(path)],
        capture_output=True, text=True, encoding="utf-8", timeout=25,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )


@pytest.mark.parametrize("outcome", ["created", "joined", "rejected", "unknown", "lost", "flood"])
@pytest.mark.parametrize("ahp", [False, True])
def test_finite_core_consumer_preserves_literal_request_and_auth_scrub(tmp_path, outcome, ahp):
    provider = tmp_path / "finite provider.py"
    captured = tmp_path / "request.json"
    provider.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        f"Path({str(captured)!r}).write_text(json.dumps({{"
        "'request':json.loads(sys.stdin.read()),"
        "'environment':{key:os.environ.get(key) for key in "
        "('GH_TOKEN','GITHUB_TOKEN','AGENT_WORKTREES_AHP_AUTH_TOKEN','COPILOT_CLI_ENABLED_FEATURE_FLAGS')}}),"
        "encoding='utf-8')\n"
        f"outcome={outcome!r}\n"
        "if outcome=='lost': raise SystemExit(17)\n"
        "if outcome=='flood': sys.stderr.write('x'*131072)\n"
        "print(json.dumps({'contract':'copilot-driver.mux-creation','version':1,'status':outcome}))\n"
        "raise SystemExit(0 if outcome in ('created','joined') else 3)\n",
        encoding="utf-8",
    )
    descriptor = json.dumps({"python": sys.executable, "entrypoint": str(provider)})
    argv = [
        "new-session", "-d", "-s", "wt-fixture", "-c", "a directory",
        "-e", "LITERAL=two words", "child", "", 'quote "inside"', "literal\nline", "tail\\", "$(not-a-command)",
    ]
    script = (
        "set -euo pipefail\n"
        f"PYTHON={shlex.quote(_path(sys.executable))}\n"
        f"_AHP_RESUME_AVAILABLE={int(ahp)}\n"
        "export GH_TOKEN=fixture GITHUB_TOKEN=fixture AGENT_WORKTREES_AHP_AUTH_TOKEN=fixture\n"
        "export COPILOT_CLI_ENABLED_FEATURE_FLAGS=fixture\n"
        + _helper() + "\n"
        "code=0\n"
        f"status=$(_aw_mux_core create {shlex.quote(descriptor)} /usr/bin/tmux wt-fixture "
        + shlex.join(argv) + ") || code=$?\n"
        'printf "%s|%s\\n" "$code" "$status"\n'
    )
    result = _run(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = f"{0 if outcome in ('created', 'joined') else 3}|"
    expected += outcome if outcome not in ("lost", "flood") else ""
    assert result.stdout == expected + "\n"
    assert not result.stderr
    observed = json.loads(captured.read_text("utf-8"))
    assert observed["request"] == {
        "version": 1, "binary": "/usr/bin/tmux", "session": "wt-fixture", "argv": argv,
    }
    assert set(observed["environment"].values()) == ({None} if ahp else {"fixture"})


@pytest.mark.parametrize("outcome", ["created", "joined", "rejected", "unknown", "lost", "legacy"])
def test_selected_core_never_enters_legacy_retry_or_cleanup(tmp_path, outcome):
    source = SOURCE.read_text("utf-8")
    start = source.index("        TMUX_CREATE_MAX_ATTEMPTS=3")
    end = source.index('\n        if [[ "$TMUX_CREATE_EXIT" -ne 0 ]]; then', start)
    script = (
        "set -uo pipefail\n"
        f"AW_MUX_CORE={shlex.quote('' if outcome == 'legacy' else 'selected')}\n"
        f"FIXTURE_OUTCOME={outcome}\n"
        "TMUX_COMMAND=(_fixture_legacy)\nTMUX_SESS=wt-fixture\nWORK_DIR='a directory'\n"
        "TMUX_ENV_FLAGS=(-e 'FIXTURE=two words')\nPANE_CMD=(child '' 'literal argument')\n"
        "COPILOT_PASSTHROUGH=()\n_SHOW_LAUNCH_STATUS=0\nAHP_TOKEN_FILE=''\n"
        "calls=0\ncleanups=0\n"
        "aw_assert_cold_resume() { :; }\n"
        "setup_log() { :; }\n"
        "type() { printf '/usr/bin/tmux\\n'; }\n"
        "_aw_cleanup_owned_tmux_session() { cleanups=$((cleanups+1)); }\n"
        "_aw_join_existing_tmux() { printf 'joined\\n'; exit 0; }\n"
        "_fixture_legacy() { calls=$((calls+1)); return 0; }\n"
        "_aw_mux_core() {\n"
        ' [[ "$1" == create && "$2" == selected ]] || return 91\n'
        ' [[ "$FIXTURE_OUTCOME" != lost ]] || return 3\n'
        ' printf "%s\\n" "$FIXTURE_OUTCOME"\n'
        ' [[ "$FIXTURE_OUTCOME" == created || "$FIXTURE_OUTCOME" == joined ]] || return 3\n'
        "}\n"
        + source[start:end]
        + '\nprintf "%s|%s|%s|%s|%s\\n" "$calls" "$cleanups" "$TMUX_CREATE_TOTAL_ATTEMPTS" '
          '"$TMUX_CREATE_EXIT" "$TMUX_CREATION_UNCERTAIN"\n'
    )
    if outcome == "legacy":
        script = script.replace("calls=0\n", "TMUX_CREATION_UNCERTAIN=0\ncalls=0\n")
    result = _run(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    if outcome == "joined":
        assert result.stdout == "joined\n"
    else:
        assert result.stdout == (
            "1|0|1|0|0\n" if outcome == "legacy"
            else f"0|0|1|{0 if outcome == 'created' else 3}|"
                 f"{int(outcome in ('unknown', 'lost'))}\n"
        )


@pytest.mark.parametrize("uncertain", [False, True])
def test_unknown_creation_retains_the_original_ahp_handoff(tmp_path, uncertain):
    source = SOURCE.read_text("utf-8")
    start = source.index("            trap '\n", source.index("TMUX_CREATION_UNCERTAIN=0"))
    end = source.index("' EXIT", start) + len("' EXIT")
    directory = tmp_path / "owned handoff"
    directory.mkdir()
    token = directory / "fixture"
    token.write_text("fixture", encoding="utf-8")
    result = _run(
        "set -euo pipefail\n"
        f"AHP_TOKEN_FILE={shlex.quote(_path(token))}\n"
        f"AHP_TOKEN_DIR={shlex.quote(_path(directory))}\n"
        f"TMUX_CREATION_UNCERTAIN={int(uncertain)}\n"
        + source[start:end] + "\nexit 0\n",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert token.exists() is uncertain
    assert directory.exists() is uncertain


def test_unknown_creation_reports_uncertainty_without_suggesting_automatic_recovery(tmp_path):
    source = SOURCE.read_text("utf-8")
    start = source.index('\n        if [[ "$TMUX_CREATE_EXIT" -ne 0 ]]; then', source.index("TMUX_CREATE_MAX_ATTEMPTS=3"))
    end = source.index("\n            RECOVERY_PROJECT=", start)
    result = _run(
        "set -euo pipefail\n"
        "TMUX_CREATE_EXIT=3\nTMUX_CREATION_UNCERTAIN=1\nWORKTREE_ID=fixture\n"
        "setup_log() { :; }\nactivity_log() { :; }\n"
        + source[start:end] + "\nfi\nexit 91\n",
        tmp_path,
    )
    assert result.returncode == 3, result.stderr
    assert not result.stdout
    assert result.stderr == (
        "ERROR: tmux creation outcome is unconfirmed. The worktree and original handoffs are retained; "
        "inspect them before retrying.\n"
    )

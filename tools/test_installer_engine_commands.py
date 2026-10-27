"""Source-derived native writer fixtures; no installer or runtime is executed."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_engine_commands",
    REPO / "libs" / "installer-engine" / "installer_engine_commands.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
PRODUCTS = (
    "agent-bridge", "agent-dispatch", "agent-vault", "budget-guidance",
    "agent-mcp", "agent-logger", "agent-codespaces", "agent-ssh",
    "agent-pull-requests", "agent-machines", "agent-containers", "agent-worktrees",
)
AUXILIARY = (
    "session-sync", "collate-session", "read-session-digest",
    "prepare-session-log", "ramp-up-session",
)


def source(product, ext):
    action = "init" if product in {"agent-mcp", "agent-machines", "agent-containers"} else "install"
    return (REPO / "plugins" / product / "scripts" / (action + "." + ext)).read_text(
        encoding="utf-8-sig",
    )


def here(source_text, variable, writer):
    text = source_text.split("function " + writer + " {", 1)[1]
    match = re.search(rf"\${variable} = @(['\"])\n(.*?)^\1@", text, re.M | re.S)
    assert match, (writer, variable)
    return match[2][:-1], match[1] == '"'


def heredoc(source_text, delimiter, writer, ordinal=0):
    text = source_text.split(writer + "() {", 1)[1]
    starts = list(re.finditer(rf"<<\s*'?{delimiter}'?[^\n]*\n", text))
    assert starts, (writer, delimiter)
    return text[starts[ordinal].end():].split("\n" + delimiter + "\n", 1)[0] + "\n"


def quote(value):
    if not value:
        return "''"
    return re.sub(r"([^a-zA-Z0-9_@%+=:,./\-\u0080-\uffff])", r"\\\1", value)


def interpolate(text, values, ps=False):
    # Independent fixture renderer: protect native escaped characters before
    # substituting known installer variables; inserted values stay uninterpreted.
    tokens = {}
    pattern = r"`(.)" if ps else r"\\([$`\\]|\n)"

    def protect(match):
        key = f"\x00{len(tokens)}\x00"
        tokens[key] = {"n": "\n", "r": "\r", "t": "\t"}.get(match[1], match[1]) if ps else match[1]
        return key

    text = re.sub(pattern, protect, text)
    text = re.sub(r"\$([A-Za-z_][A-Za-z_0-9]*)", lambda m: values[m[1]], text)
    for key, value in tokens.items():
        text = text.replace(key, value)
    return text


def ps_template(text, variable, writer, values=None):
    body, expandable = here(text, variable, writer)
    if expandable:
        return interpolate(body, values or {}, ps=True)
    for old, new in (values or {}).items():
        body = body.replace(old, new)
    return body


def prefix(text, writer, values):
    text = text.split(writer + "() {", 1)[1]
    fmt = re.search(r"printf '(#!/usr/bin/env bash[^']*)'", text)[1].replace(r"\n", "\n")
    iterator = iter(values)
    return re.sub(r"%([qs])", lambda m: quote(next(iterator)) if m[1] == "q" else next(iterator), fmt)


def array(text, values=None):
    body = re.search(r'\$stubContent = @\(\n(.*?)\n\s*\) -join "`n"', text, re.S)[1]
    lines = []
    for line in body.splitlines():
        literal = line.strip().removesuffix(",")
        lines.append(literal[1:-1] if literal[0] == "'" else interpolate(
            literal[1:-1], values or {}, ps=True,
        ))
    return "\n".join(lines)


def fixtures(product, root=None, root_text=None, payload_text=None, publisher=None):
    """Render the actual declared output bodies independently of the helper."""
    root = root or Path.home() / ("." + product)
    payload = REPO / "plugins" / product
    root_sh = root_text or str(root)
    publisher = publisher or payload
    payload_sh = payload_text or str(publisher)
    sh, ps = source(product, "sh"), source(product, "ps1")
    module = product.replace("-", "_")
    root_ps, payload_ps = str(root).replace("'", "''"), str(publisher).replace("'", "''")
    result = []
    writer = "Deploy-SelfProvisioningBinstub"
    if product in {"agent-vault", "agent-ssh", "agent-pull-requests"}:
        engine_sh = (REPO / "libs" / "installer-engine" / "installer-engine.sh").read_text()
        engine_ps = (REPO / "libs" / "installer-engine" / "installer-engine.ps1").read_text()
        # The checked-out suite has vendored copies too. The canonical writer
        # supplies fixtures, and the production helper selects payload-local.
        branch = ("agent-vault", "agent-ssh", "agent-pull-requests").index(product)
        sh_writer = engine_sh.split("write_simple_binstub() {", 1)[1]
        cold_sh = re.findall(r"<<'DISPATCH'\n(.*?)\nDISPATCH", sh_writer, re.S)[branch]
        ps_writer = engine_ps.split("function Write-SimpleBinstub {", 1)[1]
        cold_ps = re.findall(r"\$coldDispatcher = @'\n(.*?)\n'@", ps_writer, re.S)[branch]
        values_sh = {
            "command_name": product, "module_name": module, "runtime_root_literal": quote(root_sh),
            "snapshot_installer_literal": "scripts/install.sh",
            "no_self_provision_env": product.replace("-", "_").upper() + "_NO_SELFPROVISION",
            "cold_dispatch": cold_sh,
        }
        result.append((product, interpolate(
            heredoc(engine_sh, "EOF", "write_simple_binstub"), values_sh,
        )))
        values_ps = {
            "runtimeRootLiteral": root_ps, "snapshotInstallerLiteral": r"scripts\install.ps1",
            "moduleLiteral": module, "commandLiteral": product, "CommandName": product,
            "NoSelfProvisionEnv": values_sh["no_self_provision_env"], "coldDispatcher": cold_ps,
        }
        result += [
            (product + ".ps1", ps_template(engine_ps, "ps1Content", "Write-SimpleBinstub", values_ps)),
            (product + ".cmd", ps_template(engine_ps, "cmdContent", "Write-SimpleBinstub", values_ps)),
        ]
        if product == "agent-ssh":
            result.append((product, array(ps, {
                "installDirShellLiteral": "'" + root_sh.replace("'", "'\\''") + "'",
            })))
        if product == "agent-vault":
            result.append(("vault-askpass", heredoc(sh, "EOF", "_write_askpass")))
        return result
    if product == "agent-worktrees":
        return [(name, (payload / "bin" / name).read_text()) for name in (
            product, product + ".ps1", product + ".cmd",
            "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
        )] + [("agent-worktrees-update-json.awk", (
            payload / "scripts" / "installation-context" / "json-query.awk",
        )[0].read_text())]
    if product == "agent-bridge":
        writer = "Write-Binstubs"
        for qualified in ("", "1"):
            result.append((product, prefix(sh, "deploy_binstub", (
                root_sh, payload_sh, qualified,
            )) + heredoc(sh, "STUB", "deploy_binstub")))
        for qualified in ("$false", "$true"):
            result.append((product + ".ps1", ps_template(ps, "ps1", writer, {
                "__ROOT__": root_ps, "__SOURCE__": payload_ps, "__QUALIFIED_SOURCE__": qualified,
            })))
        result.append((product + ".cmd", ps_template(ps, "cmd", writer, {
            "__PS1__": str(Path.home() / ".local" / "bin" / (product + ".ps1")),
        })))
    elif product == "agent-dispatch":
        result.append((product, prefix(sh, "deploy_binstub", (
            payload_sh, root_sh + "/bin/runtime-gate.sh",
        )) + heredoc(sh, "STUBEOF", "deploy_binstub")))
        result.append((product + ".ps1", ps_template(ps, "ps1Content", writer, {
            "@INSTALL_DIR@": root_ps, "@PAYLOAD_ROOT@": payload_ps,
        })))
        result.append((product + ".cmd", ps_template(ps, "stubContent", writer)))
        for name, text in tuple(result):
            result.append((name.replace(product, product + "-board"), text.replace(
                "AGENT_DISPATCH_COMMAND_MODULE=agent_dispatch\n",
                "AGENT_DISPATCH_COMMAND_MODULE=agent_dispatch.board_cli\n",
            ).replace(
                "AGENT_DISPATCH_COMMAND_MODULE = 'agent_dispatch'",
                "AGENT_DISPATCH_COMMAND_MODULE = 'agent_dispatch.board_cli'",
            ).replace("%~dp0agent-dispatch.ps1", "%~dp0agent-dispatch-board.ps1")))
    elif product == "agent-codespaces":
        result.append((product, prefix(sh, "deploy_binstub", (
            quote(root_sh),
        )) + heredoc(sh, "STUB", "deploy_binstub")))
        result.append((product + ".ps1", ps_template(ps, "ps1Content", writer, {
            "__RUNTIME_ROOT__": root_ps,
        })))
        result.append((product + ".cmd", ps_template(ps, "stubContent", writer)))
    elif product == "agent-containers":
        for mode in ("legacy", "unknown"):
            result.append((product, prefix(sh, "deploy_binstub", (
                root_sh, payload_sh, "", mode,
            )) + heredoc(sh, "STUBEOF", "deploy_binstub")))
            result.append((product, ps_template(ps, "stubContent", writer, {
                "__ROOT__": "'" + root_sh.replace("'", "'\"'\"'") + "'",
                "__SOURCE__": "'" + payload_sh.replace("'", "'\"'\"'") + "'",
                "__CONTEXT__": "''", "__MODE__": mode,
            })))
            result.append((product + ".ps1", ps_template(ps, "ps1Content", writer, {
                "__ROOT__": root_ps, "__SOURCE__": payload_ps, "__CONTEXT__": "", "__MODE__": mode,
            })))
        result.append((product + ".cmd", ps_template(ps, "cmdContent", writer)))
    else:
        result.append((product, heredoc(sh, "STUBEOF", "deploy_binstub")))
        result.append((product, array(ps) if product == "budget-guidance"
                       else ps_template(ps, "stubContent", writer)))
        if product not in {"agent-mcp", "agent-machines"}:
            result.append((product + ".ps1", ps_template(ps, "ps1Content", writer)))
        result.append((product + ".cmd", ps_template(ps, "cmdContent", writer)))
        if product == "agent-logger":
            for name in AUXILIARY:
                result.append((name, heredoc(sh, "STUBEOF", "deploy_auxiliary_compatibility_binstubs")))
                for ext, var in ((".ps1", "ps1Content"), (".cmd", "cmdContent")):
                    result.append((name + ext, ps_template(
                        ps, var, "Deploy-AuxiliaryCompatibilityBinstubs", {"__COMMAND__": name},
                    )))
    return result


CASES = [(product, name, text) for product in PRODUCTS for name, text in fixtures(product)]
ADMITTED = CASES
ADMITTED_IDS = [p + ":" + n for p, n, _ in ADMITTED]
WRAPPERS = [
    (p, n, t) for p, n, t in ADMITTED
    if n.endswith(".cmd") and p not in {"agent-mcp", "agent-machines"}
]


def context(product, name, text):
    contents = {name: text}
    if name.endswith(".cmd") and product not in {"agent-mcp", "agent-machines"}:
        peer = name[:-4] + ".ps1"
        contents[peer] = next(body for command, body in fixtures(product) if command == peer)
    if name == "vault-askpass":
        contents[product] = next(body for command, body in fixtures(product) if command == product)
    return contents


def owned(product, contents, root=None, payload=None, module=None, publisher=None):
    return MODULE.owned_local_names(
        payload or REPO / "plugins" / product,
        root or Path.home() / ("." + product),
        module or product.replace("-", "_"), contents, publisher_root=publisher,
    )


@pytest.mark.parametrize("product,name,text", ADMITTED, ids=ADMITTED_IDS)
@pytest.mark.parametrize("encoding", ("lf", "crlf", "bom-lf", "bom-crlf"))
def test_every_current_admitted_native_writer(product, name, text, encoding):
    if "crlf" in encoding:
        text = text.replace("\n", "\r\n")
    if encoding.startswith("bom"):
        text = "\ufeff" + text
    assert name in owned(product, context(product, name, text))


@pytest.mark.parametrize("product,name,text", ADMITTED, ids=ADMITTED_IDS)
@pytest.mark.parametrize("appendix", (
    "\nexec operator-customized-command\n",
    "\n# resolve-runtime agent_bridge agent_dispatch operator customization\n",
    "\n",
))
def test_modified_programs_are_retained(product, name, text, appendix):
    assert name not in owned(product, context(product, name, text + appendix))


@pytest.mark.parametrize("product", PRODUCTS)
def test_comments_and_marker_substrings_are_not_ownership(product):
    root = Path.home() / ("." + product)
    fake = (
        "#!/usr/bin/env bash\n"
        f"# {root} resolve-runtime {product.replace('-', '_')}\n"
        "exec operator-customized-command\n"
    )
    assert owned(product, {product: fake, product + ".ps1": fake, product + ".cmd": fake}) == ()


@pytest.mark.parametrize("product", PRODUCTS)
def test_wrong_root_is_retained(product):
    assert owned(product, dict(fixtures(product)), root=Path.home() / "another-installation") == ()


@pytest.mark.parametrize("product", PRODUCTS)
def test_wrong_module_is_an_explicit_error(product):
    other = "agent_bridge" if product != "agent-bridge" else "agent_vault"
    with pytest.raises(ValueError, match="manifest"):
        owned(product, dict(fixtures(product)), module=other)


@pytest.mark.parametrize("product,name,text", WRAPPERS,
                         ids=[p + ":" + n for p, n, _ in WRAPPERS])
@pytest.mark.parametrize("peer", ("missing", "customized", "wrong-module", "wrong-root"))
def test_cmd_requires_its_exact_owned_ps_peer(product, name, text, peer):
    contents = context(product, name, text)
    ps_name = name[:-4] + ".ps1"
    if peer == "missing":
        contents.pop(ps_name)
    elif peer == "customized":
        contents[ps_name] += "\n# operator customization\n"
    elif peer == "wrong-module":
        contents[ps_name] += "\n& python -m foreign_module @args\n"
    else:
        contents[ps_name] = contents[ps_name].replace(".agent-", ".foreign-agent-").replace(
            ".budget-guidance", ".foreign-budget-guidance",
        )
    assert name not in owned(product, contents)


@pytest.mark.parametrize("product", ("agent-mcp", "agent-machines"))
@pytest.mark.parametrize("peer", ("absent", "customized"))
def test_documented_standalone_cmd_does_not_require_a_ps_peer(product, peer):
    command = product + ".cmd"
    body = next(text for name, text in fixtures(product) if name == command)
    contents = {command: body}
    if peer == "customized":
        contents[product + ".ps1"] = "# customized peer\n"
    assert owned(product, contents) == (command,)


@pytest.mark.parametrize("product", ("agent-mcp", "agent-machines"))
@pytest.mark.parametrize("change", ("module", "root", "comment", "customexec"))
def test_standalone_cmd_modified_or_foreign_program_is_retained(product, change):
    command = product + ".cmd"
    body = next(text for name, text in fixtures(product) if name == command)
    if change == "module":
        body = body.replace(product.replace("-", "_"), "foreign_module")
    elif change == "root":
        body = body.replace("." + product, ".foreign-installation")
    elif change == "comment":
        body += f"\r\nrem resolve-runtime {product.replace('-', '_')} operator customization\r\n"
    else:
        body += "\r\noperator-customized-command.exe %*\r\n"
    assert owned(product, {command: body}) == ()


@pytest.mark.parametrize("peer", ("absent", "foreign", "modified"))
def test_askpass_requires_owned_vault_command(peer):
    contents = dict(fixtures("agent-vault"))
    contents.pop("agent-vault")
    if peer != "absent":
        contents["agent-vault"] = "# resolve-runtime agent_vault\n" if peer == "foreign" else (
            fixtures("agent-vault")[0][1] + "\n# customization\n"
        )
    assert "vault-askpass" not in owned("agent-vault", contents)


@pytest.mark.parametrize("product", ("agent-vault", "agent-ssh", "agent-pull-requests", "agent-codespaces", "agent-containers"))
def test_actual_custom_root_binding_and_escaping(product):
    root = Path.home() / "runtime's $binding & (space) \u00e9\u2013"
    for name, text in fixtures(product, root):
        if name == "vault-askpass":
            continue
        contents = {name: text}
        if name.endswith(".cmd"):
            contents[name[:-4] + ".ps1"] = next(
                t for n, t in fixtures(product, root) if n == name[:-4] + ".ps1"
            )
        assert name in owned(product, contents, root=root)
        assert name not in owned(product, contents)


@pytest.mark.parametrize("product", ("agent-bridge", "agent-dispatch", "agent-containers"))
def test_publisher_binding_rejects_another_payload(product):
    for name, text in fixtures(product):
        if name.endswith(".cmd"):
            continue
        changed = text.replace(str(REPO / "plugins" / product), str(REPO / "foreign" / product))
        changed = changed.replace(quote(str(REPO / "plugins" / product)), quote(str(REPO / "foreign" / product)))
        assert changed != text
        assert owned(product, {name: changed}) == ()


@pytest.mark.parametrize("product", ("agent-bridge", "agent-dispatch", "agent-containers"))
def test_installed_publisher_is_literal_data_not_an_available_source(tmp_path, product):
    publisher = tmp_path / "removed publisher's $artifact & (source)"
    assert not publisher.exists()
    for name, text in fixtures(product, publisher=publisher):
        contents = {name: text}
        if name.endswith(".cmd"):
            contents[name[:-4] + ".ps1"] = next(
                body for command, body in fixtures(product, publisher=publisher)
                if command == name[:-4] + ".ps1"
            )
        assert name in owned(product, contents, publisher=publisher)
        if not name.endswith(".cmd"):
            assert name not in owned(product, contents)
            assert name not in owned(product, contents, publisher=tmp_path / "foreign")
            assert name not in owned(product, {name: text + "\n# customization\n"},
                                     publisher=publisher)
    assert not publisher.exists()


@pytest.mark.parametrize("publisher", ("relative", "../other", "line\nbreak"))
def test_installed_publisher_requires_absolute_nontraversing_path(publisher):
    with pytest.raises(ValueError, match="publisher"):
        owned("agent-bridge", dict(fixtures("agent-bridge")), publisher=Path(publisher))


def test_containers_namespaced_binding_is_not_legacy_ownership():
    body = next(text for name, text in fixtures("agent-containers") if name.endswith(".ps1"))
    body = body.replace("$_boundContext = ''", "$_boundContext = 'foreign-install.json'").replace(
        "$_boundMode = 'legacy'", "$_boundMode = 'namespaced'",
    )
    assert owned("agent-containers", {"agent-containers.ps1": body}) == ()


@pytest.mark.parametrize("module", ("agent_index", "unknown", "agent-bridge", "agent_bridge.board_cli"))
def test_excluded_or_unknown_modules_fail_explicitly(module):
    with pytest.raises(ValueError, match="Unsupported"):
        owned("agent-bridge", {}, module=module)


def test_relative_or_traversing_roots_fail():
    with pytest.raises(ValueError, match="absolute"):
        owned("agent-bridge", {}, root=Path("relative"))
    with pytest.raises(ValueError, match="traversal"):
        owned("agent-bridge", {}, root=Path.home() / ".." / ".agent-bridge")


@pytest.mark.parametrize("product", PRODUCTS)
def test_missing_trusted_source_with_candidates_is_not_an_empty_success(monkeypatch, product):
    original = MODULE._read

    def missing(path):
        if path.name == "plugin.json":
            return original(path)
        raise RuntimeError("Cannot read trusted command template: unavailable")

    monkeypatch.setattr(MODULE, "_read", missing)
    with pytest.raises(RuntimeError, match="template"):
        owned(product, {product: "candidate"})


@pytest.mark.parametrize("product", PRODUCTS)
def test_empty_contents_never_reads_irrelevant_sources(monkeypatch, product):
    def forbidden(path):
        pytest.fail(f"Empty candidate map read a source: {path.name}")

    monkeypatch.setattr(MODULE, "_read", forbidden)
    assert owned(product, {}) == ()


@pytest.mark.parametrize("product", [p for p in PRODUCTS if p != "agent-worktrees"])
def test_missing_writer_is_explicit(monkeypatch, product):
    original = MODULE._native
    monkeypatch.setattr(MODULE, "_native", lambda payload, p, ext: original(
        payload, p, ext,
    ).replace("deploy_binstub() {", "removed_writer() {").replace(
        "_deploy_binstub() {", "_removed_writer() {",
    ).replace("write_simple_binstub", "removed_simple_binstub"))
    with pytest.raises(RuntimeError, match="writer|call"):
        owned(product, {product: "candidate"})


def test_unknown_template_substitution_is_explicit():
    with pytest.raises(RuntimeError, match="substitution"):
        MODULE._expand("$undeclared", {})


@pytest.mark.parametrize("product", ("agent-vault", "agent-ssh", "agent-pull-requests"))
@pytest.mark.parametrize("ext", ("sh", "ps1"))
def test_changed_simple_writer_declaration_is_explicit(monkeypatch, product, ext):
    original = MODULE._native

    def changed(payload, p, language):
        text = original(payload, p, language)
        if language == ext:
            text = text.replace("scripts/install.sh", "scripts/foreign.sh") if ext == "sh" else (
                text.replace(r"scripts\install.ps1", r"scripts\foreign.ps1")
            )
        return text

    monkeypatch.setattr(MODULE, "_native", changed)
    with pytest.raises(RuntimeError, match="arguments|parameter"):
        owned(product, {product: "candidate"})


@pytest.mark.parametrize("product", (
    "agent-bridge", "agent-dispatch", "agent-vault", "agent-ssh",
    "agent-pull-requests", "agent-codespaces", "agent-containers",
))
@pytest.mark.parametrize("style", ("slash", "git-bash"))
def test_native_bash_drive_path_encodings(product, style):
    root = Path.home() / ("." + product)
    payload = REPO / "plugins" / product

    def native(path):
        result = path.as_posix()
        if style == "git-bash" and path.drive.endswith(":"):
            result = "/" + path.drive[0].lower() + result[2:]
        return result

    for name, text in fixtures(product, root, native(root), native(payload)):
        if name == product:
            assert owned(product, {name: text}) == (name,)


@pytest.mark.parametrize("product,name,text", [case for case in ADMITTED if case[1].endswith(".ps1")],
                         ids=[p + ":" + n for p, n, _ in ADMITTED if n.endswith(".ps1")])
def test_changed_powershell_body_retains_both_peers(product, name, text):
    changed = text.replace("'1'", "'2'", 1)
    if changed == text:
        changed = text.replace("'Stop'", "'Continue'", 1)
    assert changed != text
    contents = {name: changed}
    cmd_name = name[:-4] + ".cmd"
    cmd_body = next((t for n, t in fixtures(product) if n == cmd_name), None)
    if cmd_body is not None:
        contents[cmd_name] = cmd_body
    selected = owned(product, contents)
    assert name not in selected
    assert cmd_name not in selected


def test_missing_literal_placeholder_is_an_explicit_source_error():
    with pytest.raises(RuntimeError, match="placeholder"):
        MODULE._render_ps("$body = @'\nunchanged\n'@", "body", {"__ROOT__": "root"})


def test_unterminated_templates_are_explicit():
    with pytest.raises(RuntimeError, match="Unterminated"):
        MODULE._sh("cat <<EOF\nbody\n", "EOF")
    with pytest.raises(RuntimeError, match="body"):
        MODULE._ps("$body = @'\nbody\n", "body")


def test_name_allowlist_order_and_no_independent_runtime_imports():
    contents = dict(fixtures("agent-dispatch"))
    contents["foreign-command"] = contents["agent-dispatch"]
    selected = owned("agent-dispatch", contents)
    assert selected == tuple(name for name in contents if name != "foreign-command")
    assert not any(name in sys.modules for name in (
        "agent_bridge", "agent_dispatch", "agent_vault", "agent_ssh",
    ))

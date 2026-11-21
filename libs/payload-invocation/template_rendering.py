"""Expand command templates with direct or prepared uv launch substitutions."""
from __future__ import annotations

import hashlib
import re


def template_values(uv: bool) -> dict[str, str]:
    """Return launch substitutions while retaining legacy direct defaults."""
    values = {
        "POSIX_SHELL_OPTIONS": "eu",
        "UV_RUNTIME_PRELUDE_POSIX": "",
        "UV_RUNTIME_PRELUDE_POWERSHELL": "",
        "UV_RUNTIME_FINALLY_POWERSHELL": "",
        "PREPARED_RUNTIME_POSIX": "",
        "PREPARED_RUNTIME_POWERSHELL": "",
        "RUNTIME_PYTHON_POSIX": '"$AGENT_RT_PY"',
        "CACHE_PYTHON_POWERSHELL": "$Python",
        "RUNTIME_PYTHON_POWERSHELL": "$_py",
        "RUNTIME_ARGUMENTS_POWERSHELL": "@args",
        "PROVISIONED_PYTHON_POWERSHELL": "$_provisionedPy",
    }
    if uv:
        uv_arguments = (
            "run --no-project --no-config --no-env-file --no-python-downloads "
            "--managed-python --python"
        )
        values.update({
            "POSIX_SHELL_OPTIONS": "u",
            "RUNTIME_PYTHON_POSIX": f'"$_uv" {uv_arguments} "$AGENT_RT_PY" python',
            "CACHE_PYTHON_POWERSHELL": f"$_uv {uv_arguments} $Python python",
            "RUNTIME_PYTHON_POWERSHELL": f"$_uv {uv_arguments} $_py python",
            "RUNTIME_ARGUMENTS_POWERSHELL": "@_uvForwardedArguments",
            "PROVISIONED_PYTHON_POWERSHELL": f"$_uv {uv_arguments} $_provisionedPy python",
            "UV_RUNTIME_PRELUDE_POSIX": (
                "unset UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON "
                "VIRTUAL_ENV CONDA_PREFIX UV_WORKING_DIR UV_WORKING_DIRECTORY UV_ISOLATED UV_ENV_FILE "
                "UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC UV_CONFIG_FILE\n"
                "_uv=\"$(command -v uv 2>/dev/null || true)\"\n"
                "case \"$_uv\" in /*) [ -x \"$_uv\" ] || _uv='' ;; *) _uv='' ;; esac\n"
                "if [ -z \"$_uv\" ]; then\n"
                "    printf 'uv is unavailable; prepare the command prerequisites before launch.\\n' >&2\n"
                "    exit 127\nfi\n"
            ),
            "UV_RUNTIME_PRELUDE_POWERSHELL": (
                "$_savedUvEnvironment = @{}\n"
                "foreach ($key in @('UV_PYTHON', 'UV_PYTHON_PREFERENCE', 'UV_MANAGED_PYTHON', "
                "'UV_NO_MANAGED_PYTHON', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'UV_WORKING_DIR', "
                "'UV_WORKING_DIRECTORY', 'UV_ISOLATED', 'UV_ENV_FILE', "
                "'UV_PROJECT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC', 'UV_CONFIG_FILE')) {\n"
                "    $_savedUvEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')\n"
                "}\n"
                "try {\n"
                "foreach ($key in $_savedUvEnvironment.Keys) {\n"
                "    # Older CLR versions cannot restore empty values; leave those untouched.\n"
                "    if ([Environment]::Version.Major -ge 9 -or $_savedUvEnvironment[$key] -cne '') {\n"
                "        [Environment]::SetEnvironmentVariable($key, [NullString]::Value, 'Process')\n"
                "    }\n"
                "}\n"
                "$_uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source\n"
                "if (-not $_uv) {\n"
                "    [Console]::Error.WriteLine('uv is unavailable; prepare the command prerequisites before launch.')\n"
                "    exit 127\n}\n"
                "$_uvForwardedArguments = @($args)\n"
                "$_nativeArgumentMode = Get-Variable PSNativeCommandArgumentPassing -ErrorAction SilentlyContinue\n"
                "if ($null -eq $_nativeArgumentMode -or $_nativeArgumentMode.Value -eq 'Legacy') {\n"
                "    # Windows PowerShell counts escaped quotes when deciding whether to wrap an argument.\n"
                "    $_nativeQuoteReplacement = if ($PSVersionTable.PSVersion.Major -lt 6) { '$1$1\"\"' } else { '$1$1\\\"' }\n"
                "    $_uvForwardedArguments = @($args | ForEach-Object {\n"
                "        if ($_ -and $_ -notmatch '[\\s\"]') { $_ }\n"
                "        else { '\"' + [regex]::Replace([regex]::Replace($_, '(\\\\*)\"', $_nativeQuoteReplacement), '(\\\\+)$', '$1$1') + '\"' }\n"
                "    })\n"
                "}\n"
                "# Keep a literal stop-parsing marker as data, including when arguments are splatted.\n"
                "for ($_index = 0; $_index -lt $_uvForwardedArguments.Count; $_index++) {\n"
                "    if ($_uvForwardedArguments[$_index] -ceq '--%') {\n"
                "        $_uvForwardedArguments[$_index] = [Text.StringBuilder]::new('--%')\n"
                "    }\n"
                "}\n"
            ),
            "UV_RUNTIME_FINALLY_POWERSHELL": (
                "\n} finally {\n"
                "    foreach ($key in $_savedUvEnvironment.Keys) {\n"
                "        if ([Environment]::Version.Major -ge 9 -or $_savedUvEnvironment[$key] -cne '') {\n"
                "            $value = $_savedUvEnvironment[$key]\n"
                "            if ($null -eq $value) { $value = [NullString]::Value }\n"
                "            [Environment]::SetEnvironmentVariable($key, $value, 'Process')\n"
                "        }\n"
                "    }\n"
                "}"
            ),
            "PREPARED_RUNTIME_POSIX": (
                "printf '[%s] runtime is not prepared; run the owning scripts/install.sh install.\\n' \"$_command\" >&2\n"
                "exit 1\n"
            ),
            "PREPARED_RUNTIME_POWERSHELL": (
                "[Console]::Error.WriteLine(\"[$_command] runtime is not prepared; run the owning scripts/install.ps1 install.\")\n"
                "exit 1\n"
            ),
        })
    return values


def render(template: str, values: dict[str, str], uv: bool) -> str:
    """Expand the closed token table and seal an in-process catalog digest."""
    values.update(template_values(uv))
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"@@{key}@@", value)
    digest_placeholder = "@@CATALOG_FILE_SHA256@@"
    if digest_placeholder in rendered:
        unsigned = rendered.replace(digest_placeholder, "")
        rendered = rendered.replace(
            digest_placeholder,
            hashlib.sha256(unsigned.encode("utf-8")).hexdigest(),
        )
    remaining = sorted(set(re.findall(r"@@[A-Z_]+@@", rendered)))
    if remaining:
        raise ValueError(f"unresolved template fields: {', '.join(remaining)}")
    return rendered

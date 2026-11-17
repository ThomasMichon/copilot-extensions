"""KeePassXC command-line backend for agent-vault."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

from agent_procutil import no_window_kwargs
from .credential_fields import attribute_name


def _staging_title() -> str:
    return f".agent-vault-tmp-{uuid.uuid4().hex[:12]}"


def _parse_attachment_names(output: str) -> list[str] | None:
    """Parse ``show -a Title --show-attachments`` output into attachment names.

    The CLI prints the title, a blank line, a header translated into its
    configured language (so its text is not matched) and one indented
    ``name (size)`` line per attachment. Returns None when the output does
    not have that shape, so callers fail closed instead of dropping
    attachments.
    """
    lines = output.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    end = len(lines)
    while end and lines[end - 1].startswith("  "):
        end -= 1
    header = end - 1
    if header < 2 or not lines[header].strip() or lines[header - 1].strip():
        return None
    names: list[str] = []
    for line in lines[end:]:
        name, sep, size = line[2:].rpartition(" (")
        if not sep or not name or not size.endswith(")"):
            return None
        names.append(name)
    return names


class PasswordMutationAmbiguous(RuntimeError):
    """The password subprocess failed after its commit state became unknown."""


class KeePassXCBackend:
    """keepassxc-cli backend - full access with master password."""

    def __init__(self) -> None:
        self._master_pass: dict[str, str] = {}
        self._cli_path: str | None = self._find_cli()

    @staticmethod
    def _find_cli() -> str | None:
        win_path = r"C:\Program Files\KeePassXC\keepassxc-cli.exe"
        if os.path.isfile(win_path):
            return win_path
        return shutil.which("keepassxc-cli")

    @property
    def available(self) -> bool:
        return self._cli_path is not None

    def unlocked_vaults(self) -> list[str]:
        """Return database paths with cached master passwords."""
        return sorted(self._master_pass)

    def has_password(self, kpdb: str) -> bool:
        """Return whether a master password is cached for this database."""
        return bool(kpdb and kpdb in self._master_pass)

    def status(self, kpdb: str | None = None) -> str:
        """Return backend status for one database, or any database when omitted."""
        if not self._cli_path:
            return "not_found"
        if kpdb:
            return "unlocked" if self.has_password(kpdb) else "locked"
        return "unlocked" if self._master_pass else "locked"

    def set_password(self, kpdb: str, password: str) -> None:
        """Cache a master password for a database."""
        if kpdb:
            self._master_pass[kpdb] = password

    def get_password(self, kpdb: str) -> str | None:
        """Return the cached password for a database."""
        return self._master_pass.get(kpdb)

    def clear_password(self, kpdb: str | None = None) -> None:
        """Clear one cached password, or all cached passwords when omitted."""
        if kpdb is None:
            self._master_pass.clear()
        else:
            self._master_pass.pop(kpdb, None)

    def verify_password(self, kpdb: str, password: str) -> bool:
        """Verify a master password against a database."""
        if not self._cli_path or not kpdb:
            return False
        try:
            r = subprocess.run(
                [self._cli_path, "ls", "-q", kpdb, "/"],
                input=password + "\n",
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),
            )
            return r.returncode == 0
        except Exception:
            return False

    # -- credential access ---------------------------------------------------

    def _run(
        self,
        kpdb: str,
        args: list[str],
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[str] | None:
        if not self._cli_path or not self.has_password(kpdb):
            return None
        try:
            return subprocess.run(
                [self._cli_path, *args],
                input=self._master_pass[kpdb] + "\n",
                capture_output=True,
                text=True,
                timeout=timeout,
                **no_window_kwargs(),
            )
        except Exception:
            return None

    def get_entry(self, kpdb: str, entry_path: str, field: str = "password") -> str | None:
        attr = attribute_name(field)
        r = self._run(kpdb, ["show", "-q", "-s", kpdb, entry_path, "-a", attr])
        if r and r.returncode == 0:
            return r.stdout.strip()
        return None

    def has_entry(self, kpdb: str, entry_path: str) -> bool:
        r = self._run(kpdb, ["show", "-q", "-s", kpdb, entry_path])
        return bool(r and r.returncode == 0)

    def _identity_run(
        self, kpdb: str, args: list[str], deadline: float,
    ) -> subprocess.CompletedProcess[str] | None:
        from .identity_key import IdentityKeyError

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IdentityKeyError("operation_expired", "Owner-key operation expired; inspect vault before retrying.")
        return self._run(kpdb, args, timeout=min(10, remaining))

    def _identity_listing(self, kpdb: str, deadline: float) -> set[str]:
        from .identity_key import IdentityKeyError

        result = self._identity_run(kpdb, ["ls", "-q", "-R", "-f", kpdb, "/"], deadline)
        if result is None or result.returncode != 0:
            raise IdentityKeyError("vault_read_failed", "Authoritative vault listing failed; no entry was created.")
        paths: set[str] = set()
        for line in result.stdout.splitlines():
            if line == "[empty]" and len(result.stdout.splitlines()) == 1:
                continue
            path = line
            parts = path.rstrip("/").split("/")
            if (
                not path or path != path.strip()
                or any(part in ("", ".", "..") for part in parts)
                or any(ord(char) < 32 for char in path)
                or path in paths or path.rstrip("/") in paths
                or path + "/" in paths
            ):
                raise IdentityKeyError("ambiguous_entry", "Malformed or duplicate vault paths; no entry was created.")
            paths.add(path)
        for path in paths:
            segments = path.rstrip("/").split("/")
            if any("/".join(segments[:index]) + "/" not in paths for index in range(1, len(segments))):
                raise IdentityKeyError("ambiguous_entry", "Flattened paths have undeclared parents; no entry was created.")
        return paths

    def read_identity_key(self, kpdb: str, entry_path: str, deadline: float) -> str | None:
        """Absence is established only by a successful recursive root listing."""
        from .identity_key import IdentityKeyError

        paths = self._identity_listing(kpdb, deadline)
        if entry_path + "/" in paths:
            raise IdentityKeyError("ambiguous_entry", "Owner entry resolves to a group; no entry was created.")
        if entry_path not in paths:
            return None
        result = self._identity_run(kpdb, ["show", "-q", "-s", kpdb, entry_path, "-a", "Password"], deadline)
        if result is None or result.returncode != 0:
            raise IdentityKeyError("vault_read_failed", "Authoritative owner-key read failed; no entry was created.")
        return result.stdout.removesuffix("\n").removesuffix("\r")

    def create_identity_key(self, kpdb: str, entry_path: str, value: str, deadline: float) -> None:
        from .identity_key_lock import database_creation_lock

        with database_creation_lock(kpdb, deadline):
            self._create_identity_key_locked(kpdb, entry_path, value, deadline)

    def _create_identity_key_locked(
        self, kpdb: str, entry_path: str, value: str, deadline: float,
    ) -> None:
        """Create only; never edit, retry an add, or report subprocess diagnostics."""
        from .identity_key import IdentityKeyError

        paths = self._identity_listing(kpdb, deadline)
        if entry_path in paths:
            return
        if entry_path + "/" in paths:
            raise IdentityKeyError("ambiguous_entry", "Owner entry path is already a group.")
        parts = entry_path.split("/")
        for index in range(1, len(parts)):
            group = "/".join(parts[:index])
            if group in paths:
                raise IdentityKeyError("ambiguous_entry", "Owner path parent is an entry, not a group.")
            if group + "/" not in paths:
                result = self._identity_run(kpdb, ["mkdir", "-q", kpdb, group], deadline)
                paths = self._identity_listing(kpdb, deadline)
                if result is None or result.returncode != 0 or group + "/" not in paths:
                    raise IdentityKeyError("group_create_failed", "Could not create owner-entry group; inspect vault before retrying.")
        if not self._cli_path or not self.has_password(kpdb):
            raise IdentityKeyError("needs_unlock", "Vault locked; explicitly unlock and retry.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IdentityKeyError("operation_expired", "Owner-key operation expired before creation.")
        try:
            subprocess.run(
                [self._cli_path, "add", "-q", kpdb, entry_path, "-p"],
                input=self._master_pass[kpdb] + "\n" + value + "\n",
                capture_output=True,
                text=True,
                timeout=min(10, remaining),
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            # Completion is unknown. The caller must reread, never retry add.
            return

    def search(self, kpdb: str, query: str) -> list[str]:
        r = self._run(kpdb, ["search", "-q", kpdb, query])
        if r and r.returncode == 0:
            return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        return []

    # -- mutations -----------------------------------------------------------

    def _ensure_parent_groups(self, kpdb: str, entry_path: str) -> None:
        """Create any missing parent groups for a slash-delimited entry path.

        keepassxc-cli ``add`` fails if the entry's parent group does not exist,
        so create each intermediate group first. ``mkdir`` on an already-present
        group fails harmlessly and is ignored (best-effort).
        """
        parts = [p for p in entry_path.strip("/").split("/") if p]
        if len(parts) < 2:
            return
        if not self._cli_path or not self.has_password(kpdb):
            return
        cumulative = ""
        for seg in parts[:-1]:
            cumulative = f"{cumulative}/{seg}" if cumulative else seg
            try:
                subprocess.run(
                    [self._cli_path, "mkdir", "-q", kpdb, cumulative],
                    input=self._master_pass[kpdb] + "\n",
                    capture_output=True,
                    text=True,
                    timeout=10,
                    **no_window_kwargs(),
                )
            except Exception:
                pass

    def add_entry(
        self,
        kpdb: str,
        entry_path: str,
        *,
        username: str | None = None,
        url: str | None = None,
        password: str | None = None,
        generate: bool = False,
    ) -> tuple[bool, str]:
        """Create a new KeePass entry. Returns (success, message)."""
        if not self._cli_path or not self.has_password(kpdb):
            return False, "CLI not available or vault locked"
        self._ensure_parent_groups(kpdb, entry_path)
        args = ["add", "-q", kpdb, entry_path]
        if username:
            args.extend(["-u", username])
        if url:
            args.extend(["--url", url])
        if generate:
            args.append("-g")
        stdin = self._master_pass[kpdb] + "\n"
        if password and not generate:
            args.append("-p")
            stdin += password + "\n"
        try:
            r = subprocess.run(
                [self._cli_path, *args],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),
            )
            if r.returncode == 0:
                return True, "Entry created"
            return False, r.stderr.strip() or "keepassxc-cli add failed"
        except Exception as e:
            # str() of e.g. TimeoutExpired embeds the full argv.
            return False, f"keepassxc-cli add failed: {type(e).__name__}"

    def edit_password(
        self,
        kpdb: str,
        entry_path: str,
        password: str,
    ) -> tuple[bool, str]:
        """Update an entry password.

        Returns ``(success, message)`` for a definitive result and raises
        :class:`PasswordMutationAmbiguous` when subprocess completion is unknown.
        """
        if not self._cli_path or not self.has_password(kpdb):
            return False, "CLI not available or vault locked"
        stdin = self._master_pass[kpdb] + "\n" + password + "\n"
        try:
            r = subprocess.run(
                [self._cli_path, "edit", "-q", kpdb, entry_path, "-p"],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),
            )
            if r.returncode == 0:
                return True, "Password updated"
            return False, r.stderr.strip() or "keepassxc-cli edit failed"
        except Exception as e:
            raise PasswordMutationAmbiguous(str(e)) from e

    def import_attachment(
        self,
        kpdb: str,
        entry_path: str,
        attachment_name: str,
        data: bytes,
        *,
        overwrite: bool = True,
    ) -> tuple[bool, str]:
        """Import an attachment into an entry. Returns (success, message)."""
        import tempfile

        if not self._cli_path or not self.has_password(kpdb):
            return False, "CLI not available or vault locked"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{attachment_name}") as f:
                tmp_path = f.name
                f.write(data)
            r = subprocess.run(
                [
                    self._cli_path,
                    "attachment-import",
                    "-q",
                    *(["-f"] if overwrite else []),
                    kpdb,
                    entry_path,
                    attachment_name,
                    tmp_path,
                ],
                input=self._master_pass[kpdb] + "\n",
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),
            )
            if r.returncode == 0:
                return True, f"Imported {attachment_name}"
            return False, r.stderr.strip() or "attachment-import failed"
        except Exception as e:
            return False, str(e)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    if not overwrite:
                        raise

    def import_attachment_create_only(
        self, kpdb: str, entry_path: str, attachment_name: str, data: bytes,
    ) -> tuple[bool, str]:
        return self.import_attachment(
            kpdb, entry_path, attachment_name, data, overwrite=False,
        )

    def export_attachment(
        self,
        kpdb: str,
        entry_path: str,
        attachment_name: str,
        *,
        max_bytes: int | None = None,
    ) -> tuple[bytes | None, str]:
        """Export an attachment from an entry. Returns (data, message)."""
        import tempfile

        if not self._cli_path or not self.has_password(kpdb):
            return None, "CLI not available or vault locked"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{attachment_name}") as f:
                tmp_path = f.name
            r = subprocess.run(
                [
                    self._cli_path,
                    "attachment-export",
                    "-q",
                    kpdb,
                    entry_path,
                    attachment_name,
                    tmp_path,
                ],
                input=self._master_pass[kpdb] + "\n",
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),
            )
            if r.returncode == 0:
                with open(tmp_path, "rb") as f:
                    data = f.read() if max_bytes is None else f.read(max_bytes + 1)
                if max_bytes is not None and not 1 <= len(data) <= max_bytes:
                    return None, "Attachment size is outside the supported bound"
                return data, f"Exported {attachment_name}"
            return None, r.stderr.strip() or "attachment-export failed"
        except Exception as e:
            return None, str(e)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    if max_bytes is not None:
                        raise

    def export_attachment_bounded(
        self, kpdb: str, entry_path: str, attachment_name: str,
    ) -> tuple[bytes | None, str]:
        """Read a small attachment; temporary-file cleanup is part of success."""
        from .attachment_import import MAX_ATTACHMENT_BYTES

        return self.export_attachment(
            kpdb, entry_path, attachment_name, max_bytes=MAX_ATTACHMENT_BYTES,
        )

    # -- listing & lifecycle -------------------------------------------------

    def list_entries(
        self,
        kpdb: str,
        group: str = "/",
        *,
        recursive: bool = False,
        flatten: bool = False,
    ) -> list[str] | None:
        """List entries under a group. Returns None when the vault is locked."""
        args = ["ls", "-q"]
        if recursive:
            args.append("-R")
        if flatten:
            args.append("-f")
        args += [kpdb, group]
        r = self._run(kpdb, args)
        if r is None:
            return None
        if r.returncode == 0:
            return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        return []

    def show_entry(
        self,
        kpdb: str,
        entry_path: str,
        *,
        show_protected: bool = False,
    ) -> str | None:
        """Show all fields of an entry. Returns raw output, or None on failure."""
        args = ["show", "-q", "--all"]
        if show_protected:
            args.append("-s")
        args += [kpdb, entry_path]
        r = self._run(kpdb, args)
        if r and r.returncode == 0:
            return r.stdout
        return None

    def edit_username(
        self,
        kpdb: str,
        entry_path: str,
        username: str,
    ) -> tuple[bool, str]:
        """Update the username of an existing entry. Returns (success, message).

        Unlike a password change, the new username is passed as the ``-u``
        argument value, so only the master password is fed on stdin.
        """
        r = self._run(kpdb, ["edit", "-q", kpdb, entry_path, "-u", username])
        if r is None:
            return False, "CLI not available or vault locked"
        if r.returncode == 0:
            return True, "Username updated"
        return False, r.stderr.strip() or "keepassxc-cli edit failed"

    def remove_entry(self, kpdb: str, entry_path: str) -> tuple[bool, str]:
        """Remove an entry from the database. Returns (success, message)."""
        r = self._run(kpdb, ["rm", "-q", kpdb, entry_path])
        if r is None:
            return False, "CLI not available or vault locked"
        if r.returncode == 0:
            return True, "Entry removed"
        return False, r.stderr.strip() or "keepassxc-cli rm failed"

    def group_exists(self, kpdb: str, group_path: str) -> bool:
        """Return True when ``group_path`` names an existing group."""
        if not group_path.strip("/"):
            return True
        r = self._run(kpdb, ["ls", "-q", kpdb, group_path])
        return bool(r and r.returncode == 0)

    def _run_raw(self, kpdb: str, args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
        """Like ``_run`` but returns undecoded output, so line endings survive."""
        if not self._cli_path or not self.has_password(kpdb):
            return None
        try:
            return subprocess.run(
                [self._cli_path, *args],
                input=(self._master_pass[kpdb] + "\n").encode("utf-8"),
                capture_output=True,
                timeout=10,
                **no_window_kwargs(),
            )
        except Exception:
            return None

    def _read_fields_raw(
        self, kpdb: str, entry_path: str, attrs: tuple[str, ...],
    ) -> dict[str, str] | None:
        """Read attributes exactly, or None when they cannot be read losslessly.

        The CLI appends a platform line terminator to each value. It is learned
        from the entry's known title so that only it is removed, keeping any
        carriage return that belongs to the value itself.
        """
        title = self._split_path(entry_path)[1].encode("utf-8")
        probe = self._run_raw(kpdb, ["show", "-q", kpdb, entry_path, "-a", "Title"])
        if probe is None or probe.returncode != 0 or not probe.stdout.startswith(title):
            return None
        terminator = probe.stdout[len(title):]
        if terminator not in (b"\n", b"\r\n"):
            return None
        values: dict[str, str] = {}
        for attr in attrs:
            r = self._run_raw(kpdb, ["show", "-q", "-s", kpdb, entry_path, "-a", attr])
            if r is None or r.returncode != 0 or not r.stdout.endswith(terminator):
                return None
            try:
                values[attr] = r.stdout[:-len(terminator)].decode("utf-8")
            except UnicodeDecodeError:
                return None
        return values

    def list_attachments(self, kpdb: str, entry_path: str) -> list[str] | None:
        """Return attachment names for an entry, or None on failure."""
        r = self._run(
            kpdb, ["show", "-q", "--show-attachments", "-a", "Title", kpdb, entry_path],
        )
        if r is None or r.returncode != 0:
            return None
        return _parse_attachment_names(r.stdout)

    def _split_path(self, entry_path: str) -> tuple[str, str]:
        parent, _, title = entry_path.rstrip("/").rpartition("/")
        return parent, title

    def _rename_entry(self, kpdb: str, entry_path: str, title: str) -> tuple[bool, str]:
        r = self._run(kpdb, ["edit", "-q", kpdb, entry_path, "-t", title])
        if r is None:
            return False, "CLI not available or vault locked"
        if r.returncode == 0:
            return True, "Entry renamed"
        return False, r.stderr.strip() or "keepassxc-cli edit failed"

    def move_entry(self, kpdb: str, entry_path: str, dest_group: str) -> tuple[bool, str]:
        """Move an entry to a different group. Returns (success, message)."""
        r = self._run(kpdb, ["mv", "-q", kpdb, entry_path, dest_group])
        if r is None:
            return False, "CLI not available or vault locked"
        if r.returncode == 0:
            return True, "Entry moved"
        return False, r.stderr.strip() or "keepassxc-cli mv failed"

    def copy_entry(
        self, kpdb: str, entry_path: str, dest_path: str, *,
        skip_notes: bool = False, skip_url: bool = False,
    ) -> tuple[bool, str]:
        """Copy an entry to a new full entry path. Returns (success, message).

        Refuses an existing destination. Copies the title (from ``dest_path``),
        username, password, URL and attachments into a staged entry, then
        renames it into place. keepassxc-cli
        only accepts notes and URLs as command-line arguments, which other
        local users may be able to read, so a non-empty value is refused unless
        ``skip_notes``/``skip_url`` asks to copy without it.
        keepassxc-cli cannot write custom attributes, so those (and TOTP, tags,
        expiry, icons and history) are not copied.
        """
        if self.has_entry(kpdb, dest_path):
            return False, f"Destination entry '{dest_path}' already exists"
        fields = self._read_fields_raw(kpdb, entry_path, ("UserName", "Password", "URL", "Notes"))
        if fields is None:
            return False, f"Could not read the fields of '{entry_path}' exactly"
        if fields["Notes"] and not skip_notes:
            return False, (
                f"'{entry_path}' has notes, which cannot be copied without exposing "
                "them on a command line; resend with skip_notes to copy without them")
        if any(c in fields[a] for a in ("UserName", "Password") for c in "\r\n"):
            # add_entry() sends the password through a line-oriented prompt,
            # which would silently drop a line break or carriage return.
            return False, (
                f"'{entry_path}' has a multi-line or carriage-return username or "
                "password, which cannot be copied intact; copy it manually")
        if fields["URL"] and not skip_url:
            return False, (
                f"'{entry_path}' has a URL, which cannot be copied without exposing "
                "it on a command line; resend with skip_url to copy without it")
        attachments = self.list_attachments(kpdb, entry_path)
        if attachments is None:
            return False, f"Could not list attachments of '{entry_path}'"
        dest_parent, _ = self._split_path(dest_path)
        temp = _staging_title()
        staged = f"{dest_parent}/{temp}" if dest_parent else temp
        ok, msg = self.add_entry(
            kpdb, staged,
            username=fields["UserName"] or None,
            password=fields["Password"] or None,
        )
        if not ok:
            # A timeout can follow a committed add, so clear any staged copy.
            return False, msg + self._discard_staged(kpdb, staged)
        error = None
        for name in attachments:
            data, emsg = self.export_attachment(kpdb, entry_path, name)
            if data is None:
                error = f"Attachment '{name}' export failed: {emsg}"
                break
            ok, imsg = self.import_attachment(kpdb, staged, name, data)
            if not ok:
                error = f"Attachment '{name}' import failed: {imsg}"
                break
        if error is None:
            dest_title = self._split_path(dest_path)[1]
            ok, msg = self._rename_entry(kpdb, staged, dest_title)
            if ok:
                return True, "Entry copied"
            # A timeout can follow a committed rename; the group listing settles it.
            titles = self._group_titles(kpdb, dest_parent)
            if titles is not None and dest_title in titles and temp not in titles:
                return True, "Entry copied"
            error = f"Rename failed: {msg}"
        return False, error + self._discard_staged(kpdb, staged)

    def _discard_staged(self, kpdb: str, staged: str) -> str:
        """Remove a staged copy; return a note naming it unless it is gone."""
        removed, _ = self.remove_entry(kpdb, staged)
        if removed:
            return ""
        parent, title = self._split_path(staged)
        titles = self._group_titles(kpdb, parent)
        if titles is not None and title not in titles:
            return ""
        return f"; a staged copy may remain at '{staged}' and should be removed"

    def _group_titles(self, kpdb: str, group: str) -> set[str] | None:
        """Return the entry and subgroup names listed in ``group``, or None."""
        r = self._run(kpdb, ["ls", "-q", kpdb, group or "/"])
        if r is None or r.returncode != 0:
            return None
        return set(r.stdout.splitlines())

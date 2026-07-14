"""KeePassXC command-line backend for agent-vault."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess


class KeePassXCBackend:
    """keepassxc-cli backend - full access with master password."""

    def __init__(self):
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
            )
            return r.returncode == 0
        except Exception:
            return False

    # -- credential access ---------------------------------------------------

    def _run(
        self,
        kpdb: str,
        args: list[str],
        timeout: int = 10,
    ) -> subprocess.CompletedProcess | None:
        if not self._cli_path or not self.has_password(kpdb):
            return None
        try:
            return subprocess.run(
                [self._cli_path, *args],
                input=self._master_pass[kpdb] + "\n",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except Exception:
            return None

    def get_entry(self, kpdb: str, entry_path: str, field: str = "password") -> str | None:
        attr_map = {
            "password": "Password",
            "username": "UserName",
            "url": "URL",
            "title": "Title",
            "notes": "Notes",
        }
        attr = attr_map.get(field, field)
        r = self._run(kpdb, ["show", "-q", "-s", kpdb, entry_path, "-a", attr])
        if r and r.returncode == 0:
            return r.stdout.strip()
        return None

    def has_entry(self, kpdb: str, entry_path: str) -> bool:
        r = self._run(kpdb, ["show", "-q", "-s", kpdb, entry_path])
        return bool(r and r.returncode == 0)

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
            )
            if r.returncode == 0:
                return True, "Entry created"
            return False, r.stderr.strip() or "keepassxc-cli add failed"
        except Exception as e:
            return False, str(e)

    def edit_password(
        self,
        kpdb: str,
        entry_path: str,
        password: str,
    ) -> tuple[bool, str]:
        """Update the password of an existing entry. Returns (success, message)."""
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
            )
            if r.returncode == 0:
                return True, "Password updated"
            return False, r.stderr.strip() or "keepassxc-cli edit failed"
        except Exception as e:
            return False, str(e)

    def import_attachment(
        self,
        kpdb: str,
        entry_path: str,
        attachment_name: str,
        data: bytes,
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
                    "-f",
                    kpdb,
                    entry_path,
                    attachment_name,
                    tmp_path,
                ],
                input=self._master_pass[kpdb] + "\n",
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return True, f"Imported {attachment_name}"
            return False, r.stderr.strip() or "attachment-import failed"
        except Exception as e:
            return False, str(e)
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    def export_attachment(
        self,
        kpdb: str,
        entry_path: str,
        attachment_name: str,
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
            )
            if r.returncode == 0:
                with open(tmp_path, "rb") as f:
                    data = f.read()
                return data, f"Exported {attachment_name}"
            return None, r.stderr.strip() or "attachment-export failed"
        except Exception as e:
            return None, str(e)
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

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

    def move_entry(self, kpdb: str, entry_path: str, dest_group: str) -> tuple[bool, str]:
        """Move an entry to a different group. Returns (success, message)."""
        r = self._run(kpdb, ["mv", "-q", kpdb, entry_path, dest_group])
        if r is None:
            return False, "CLI not available or vault locked"
        if r.returncode == 0:
            return True, "Entry moved"
        return False, r.stderr.strip() or "keepassxc-cli mv failed"

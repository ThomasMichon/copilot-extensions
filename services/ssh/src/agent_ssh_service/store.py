"""Durable public bindings and enrollment correlation; never private-key custody."""

from __future__ import annotations

from contextlib import contextmanager
import os
import sqlite3

from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy
from fleet_manager.gateway_ssh_provider import directory_snapshot, public_key
from fleet_manager.planning import canonical, decode, identifier, integer
from fleet_manager.private_state import create_private_file

from .config import ServiceConfig, absolute, check

MAX_REQUESTS = 4096
MAX_RESPONSE = 32 * 1024


class DirectoryStore:
    """The host must hold its canonical single-writer lease for this lifetime."""

    def __init__(self, config: ServiceConfig):
        self.config = config
        self.root = absolute(config.to_dict()["data_dir"])
        check(self.root.is_dir(), "service-data-directory-unavailable")
        self.path = self.root / "directory.sqlite3"
        created = False
        try:
            descriptor = create_private_file(self.path)
        except FileExistsError:
            absolute(str(self.path))
            check(self.path.is_file() and self.path.stat().st_nlink == 1,
                  "invalid-directory-store")
        else:
            os.close(descriptor)
            created = True
        with self._transaction() as db:
            if created:
                for statement in (
                    """CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1),
                        authority BLOB NOT NULL, revision INTEGER NOT NULL)""",
                    """CREATE TABLE enrollments (
                        machine TEXT NOT NULL, environment TEXT NOT NULL,
                        generation INTEGER NOT NULL, public_key TEXT NOT NULL,
                        host_key TEXT, PRIMARY KEY(machine, environment, generation))""",
                    """CREATE TABLE requests (
                        principal TEXT NOT NULL, request_id TEXT NOT NULL,
                        content BLOB NOT NULL, PRIMARY KEY(principal, request_id))""",
                    """CREATE TABLE generations (
                        machine TEXT NOT NULL, environment TEXT NOT NULL,
                        generation INTEGER NOT NULL, principal TEXT NOT NULL,
                        PRIMARY KEY(machine, environment))""",
                ):
                    db.execute(statement)
                db.execute("INSERT INTO metadata VALUES (1, ?, 1)",
                           (canonical(config.authority),))
                for binding in config.authority["bindings"]:
                    db.execute("INSERT INTO generations VALUES (?,?,?,?)", (
                        binding["machine"], binding["environment"], binding["generation"],
                        binding["principal"],
                    ))
            self._accept_configuration(db)
            db.execute("""CREATE TABLE IF NOT EXISTS relay_keys (
                machine TEXT NOT NULL, environment TEXT NOT NULL,
                generation INTEGER NOT NULL, public_key TEXT NOT NULL,
                PRIMARY KEY(machine, environment, generation))""")
            db.execute("""CREATE TABLE IF NOT EXISTS enrollment_pending (
                principal TEXT NOT NULL, request_id TEXT NOT NULL,
                request BLOB NOT NULL, candidate BLOB NOT NULL,
                fingerprint TEXT NOT NULL, decision BLOB,
                PRIMARY KEY(principal, request_id))""")
            self._relay_snapshot(db)
        from .approval import PeerApproval

        self.approval = PeerApproval(self)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _accept_configuration(self, db) -> None:
        row = db.execute("SELECT authority FROM metadata WHERE id=1").fetchone()
        check(row is not None, "interrupted-store-initialization")
        prior = decode(row[0])
        selected = self.config.authority
        check(all(prior[name] == selected[name] for name in (
            "fleet", "gateway", "slot", "controller",
        )), "store-owner-mismatch")
        check(selected["revision"] >= prior["revision"], "configuration-rollback")
        if selected == prior:
            return
        check(selected["revision"] > prior["revision"], "configuration-revision-conflict")
        for binding in selected["bindings"]:
            target = (binding["machine"], binding["environment"])
            previous = db.execute(
                "SELECT generation,principal FROM generations WHERE machine=? AND environment=?",
                target,
            ).fetchone()
            if previous is not None:
                check(binding["generation"] >= previous[0], "enrollment-rollback")
                check(binding["principal"] == previous[1]
                      or binding["generation"] > previous[0],
                      "principal-change-requires-new-generation")
            db.execute("INSERT OR REPLACE INTO generations VALUES (?,?,?,?)", (
                *target, binding["generation"], binding["principal"],
            ))
        db.execute("UPDATE metadata SET authority=?, revision=revision+1 WHERE id=1",
                   (canonical(selected),))
        self._snapshot(db)

    def _snapshot(self, db) -> dict:
        authority, revision = db.execute(
            "SELECT authority, revision FROM metadata WHERE id=1").fetchone()
        check(decode(authority) == self.config.authority, "configuration-owner-changed")
        selected = self.config.to_dict()
        machines = []
        for binding in selected["bindings"]:
            row = db.execute(
                "SELECT public_key,host_key FROM enrollments "
                "WHERE machine=? AND environment=? AND generation=?",
                (binding["machine"], binding["environment"], binding["generation"]),
            ).fetchone()
            if row is not None:
                machines.append({
                    **{name: binding[name] for name in (
                        "machine", "environment", "alias", "user", "reverse_port",
                    )},
                    "public_key": row[0], "host_key": row[1],
                })
        policy = GatewaySSHPolicy.from_dict(selected["policy"])
        directory = directory_snapshot({
            "schema": "fleet-manager.gateway-ssh-directory", "version": 1,
            "controller": selected["controller"], "revision": revision,
            "policy_digest": policy.policy_digest, "machines": machines,
        }, policy)
        result = {
            "schema": "agent-ssh.service-directory", "version": 1,
            "policy": policy.to_dict(), "directory": directory,
        }
        check(len(canonical(result)) <= MAX_RESPONSE, "directory-response-limit")
        return result

    def snapshot(self) -> dict:
        with self._transaction() as db:
            return self._snapshot(db)

    def _relay_snapshot(self, db) -> list[dict]:
        snapshot = self._snapshot(db)
        rows = []
        keys = {row["public_key"] for row in snapshot["directory"]["machines"]}
        policy = GatewaySSHPolicy.from_dict(snapshot["policy"])
        for row in snapshot["directory"]["machines"]:
            binding = next(item for item in self.config.authority["bindings"] if (
                item["machine"], item["environment"]
            ) == (row["machine"], row["environment"]))
            relay = db.execute(
                "SELECT public_key FROM relay_keys "
                "WHERE machine=? AND environment=? AND generation=?",
                (row["machine"], row["environment"], binding["generation"]),
            ).fetchone()
            if relay is not None:
                check(relay[0] not in keys, "relay-key-must-be-distinct")
                keys.add(relay[0])
            decision = policy.resolve(row["machine"], row["environment"]).to_dict()
            if decision["state"] != "configured":
                continue
            check(relay is not None or "relay" not in self.config.to_dict(),
                  "relay-public-key-required")
            if relay is not None:
                rows.append({**row, "relay_public_key": relay[0]})
        return rows

    def relay_snapshot(self) -> list[dict]:
        with self._transaction() as db:
            return self._relay_snapshot(db)

    def _validate_enrollment(self, principal: str, request: dict) -> dict:
        required = {
            "request_id", "machine", "environment", "generation", "public_key", "host_key",
        }
        check(isinstance(request, dict) and required <= request.keys()
              and not request.keys() - required - {"relay_public_key", "enrollment_policy"},
              "invalid-enrollment")
        check(request.get("enrollment_policy", "configured-bindings")
              == self.config.enrollment_mode, "enrollment-policy-mismatch")
        identifier(request["request_id"], "SSH enrollment request")
        integer(request["generation"], "SSH enrollment generation", minimum=1)
        binding = self.config.binding(principal)
        check(all(request[name] == binding[name] for name in (
            "machine", "environment", "generation",
        )), "enrollment-binding-mismatch")
        decision = GatewaySSHPolicy.from_dict(self.config.to_dict()["policy"]).resolve(
            binding["machine"], binding["environment"],
        ).to_dict()
        check(decision["state"] == "configured", "machine-policy-not-configured")
        public_key(request["public_key"])
        if request["host_key"] is not None:
            public_key(request["host_key"])
        check(not decision["effective_inbound"] or request["host_key"] is not None,
              "inbound-host-pin-required")
        relay_key = request.get("relay_public_key")
        if relay_key is not None:
            public_key(relay_key)
            check(relay_key != request["public_key"], "relay-key-must-be-distinct")
        check("relay" not in self.config.to_dict() or relay_key is not None,
              "relay-public-key-required")
        return binding

    def _activate(self, db, principal: str, request: dict) -> tuple[dict, bool]:
        binding = self._validate_enrollment(principal, request)
        relay_key = request.get("relay_public_key")
        encoded = canonical(request)
        self._remember_request(db, principal, request["request_id"], encoded)
        row = db.execute(
            "SELECT public_key,host_key FROM enrollments "
            "WHERE machine=? AND environment=? AND generation=?",
            (binding["machine"], binding["environment"], binding["generation"]),
        ).fetchone()
        check(row is None or row == (request["public_key"], request["host_key"]),
              "key-change-requires-new-generation")
        previous_relay = db.execute(
            "SELECT public_key FROM relay_keys "
            "WHERE machine=? AND environment=? AND generation=?",
            (binding["machine"], binding["environment"], binding["generation"]),
        ).fetchone()
        check(row is None or (previous_relay[0] if previous_relay else None) == relay_key,
              "relay-key-change-requires-new-generation")
        changed = row is None
        if changed:
            db.execute("INSERT INTO enrollments VALUES (?,?,?,?,?)", (
                binding["machine"], binding["environment"], binding["generation"],
                request["public_key"], request["host_key"],
            ))
            db.execute("UPDATE metadata SET revision=revision+1 WHERE id=1")
            if relay_key is not None:
                db.execute("INSERT INTO relay_keys VALUES (?,?,?,?)", (
                    binding["machine"], binding["environment"], binding["generation"], relay_key,
                ))
        result = self._snapshot(db)
        self._relay_snapshot(db)
        return result, changed

    def _remember_request(self, db, principal: str, request_id: str, encoded: bytes) -> None:
        previous = db.execute(
            "SELECT content FROM requests WHERE principal=? AND request_id=?",
            (principal, request_id),
        ).fetchone()
        check(previous is None or previous[0] == encoded, "request-content-conflict")
        if previous is None:
            count = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            check(count < MAX_REQUESTS, "enrollment-journal-full")
            db.execute("INSERT INTO requests VALUES (?,?,?)", (principal, request_id, encoded))

    def enroll(self, principal: str, request: dict, *, checkpoint=lambda: None) -> tuple[dict, bool]:
        self._validate_enrollment(principal, request)
        with self._transaction() as db:
            checkpoint()
            if self.config.enrollment_mode == "peer-approval":
                result, changed = self.approval.submit(db, principal, request)
            else:
                result, changed = self._activate(db, principal, request)
            checkpoint()
            return result, changed

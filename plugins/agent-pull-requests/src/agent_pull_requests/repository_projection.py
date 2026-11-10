"""Current facts and corresponding events share ReplayStore's atomic transaction."""

from __future__ import annotations

import copy
import uuid

from .repository_contract import MAX_EVENTS, MAX_PULLS, RepositoryIdentity
from .watch_replay import ObservationTarget

FACTS = (
    "provider", "pr_state", "merged", "head_sha", "base_ref", "base_sha", "mergeable",
    "checks_state", "checks_complete", "title", "draft", "author",
)


def validate_identity(snapshot, identity, number):
    """Fence the selected backend before either replay or projection consumes evidence."""
    native = snapshot.native_snapshot
    if not isinstance(native, dict) or (
        native.get("provider"), native.get("api_base"), native.get("repo"), native.get("pr"),
    ) != (identity.provider, identity.endpoint, identity.repo, number):
        raise ValueError("snapshot differs from configured full repository identity")
    return native


def normalized(snapshot, identity, number):
    """Keep bounded current facts without volatile timestamps or cursors."""
    native = validate_identity(snapshot, identity, number)
    facts = {name: copy.deepcopy(native.get(name)) for name in FACTS}
    facts["reviews"] = sorted([{
        name: review.get(name) for name in ("id", "state", "user", "commit_id", "dismissed")
    } for review in native.get("reviews", [])], key=lambda review: (
        str(review["id"]), str(review["user"]), str(review["commit_id"]),
    ))
    facts["check_failures"] = sorted([{
        name: failure.get(name) for name in ("name", "state", "source", "url")
    } for failure in native.get("check_failures", [])], key=lambda failure: (
        str(failure["name"]), str(failure["source"]), str(failure["url"]),
    ))
    facts["fingerprints"] = copy.deepcopy((native.get("observation") or {}).get("fingerprints"))
    from .repository_state import facts as validate_facts
    validate_facts(facts)
    return facts


def _active(state, now):
    return [row for row in state["registrations"] if not row["cancelled"] and row["expires_at"] > now]


def _event(state, target, number, kind, facts, now):
    if len(state["events"]) == MAX_EVENTS:
        first = state["events"][0]
        if any(row["target"] == first["target"] and row["acknowledged"] < first["sequence"]
               for row in _active(state, now)):
            raise ValueError("repository event backpressure; unresolved delivery retained")
        state["events"].pop(0)
    state["events"].append({
        "sequence": state["next_sequence"], "event_id": uuid.uuid4().hex,
        "target": target, "number": number, "kind": kind, "facts": copy.deepcopy(facts),
    })
    state["next_sequence"] += 1


def _new_repository(identity):
    return {
        "identity": identity.document(), "pulls": [],
        "inventory": {"outcome": "unknown", "observed_at": None, "last_complete_at": None,
                      "error": "", "pages": 0},
    }


class RepositoryProjection:
    """One selected namespace's current-state/event view over ReplayStore custody."""

    def __init__(self, store, identities):
        self.store = store
        self.identities = {identity.key: identity for identity in identities}
        if not store.repository_cache or any(
            identity.namespace != store.namespace for identity in self.identities.values()
        ):
            raise ValueError("repository projection requires its selected namespace owner")

    def _transaction(self, operation, *, expires_at, checkpoint=None, persist=False):
        def selected(document):
            return operation(document["repository_cache"])
        return self.store._review_transaction(
            selected, expires_at=expires_at, checkpoint=checkpoint, persist=persist,
        )

    def plans(self, inventories, **authority):
        """Plan members and unresolved departures without acquiring forge evidence."""
        def plan(state):
            targets = {}
            for key, identity in self.identities.items():
                previous = next((row for row in state["repositories"]
                                 if RepositoryIdentity.parse(row["identity"]).key == key), None)
                numbers = set(inventories[key]["numbers"])
                if previous is not None:
                    numbers.update(pull["number"] for pull in previous["pulls"] if (
                        pull["member"] or pull["facts"] is None
                        or pull["facts"]["pr_state"] == "open" and not pull["facts"]["merged"]
                    ))
                for number in sorted(numbers)[:MAX_PULLS * 2]:
                    targets[(key, identity.repo, number)] = ObservationTarget(
                        key, identity.repo, number, frozenset(),
                    )
            return targets
        return self._transaction(plan, **authority)

    def apply(self, inventories, snapshots, **authority):
        """Commit current facts and their durable events in one owner replacement."""
        prepared = {}
        for key, snapshot in snapshots.items():
            try:
                prepared[key] = normalized(snapshot, self.identities[key[0]], key[2]) if (
                    snapshot is not None
                ) else None
            except (ValueError, TypeError):
                prepared[key] = None
        return self.apply_facts(inventories, prepared, **authority)

    def apply_facts(self, inventories, snapshots, *, observed_at=None, **authority):
        """Publish a bounded prepared batch, retaining atomic state/event pairs."""
        from .repository_state import facts as validate_facts
        observed_at = int(self.store.clock()) if observed_at is None else observed_at
        if type(observed_at) is not int or not 0 <= observed_at <= self.store.clock():
            raise ValueError("invalid observation cycle timestamp")
        def apply(state):
            now = int(self.store.clock())
            for key, identity in self.identities.items():
                row = next((row for row in state["repositories"]
                            if RepositoryIdentity.parse(row["identity"]).key == key), None)
                if row is None:
                    row = _new_repository(identity)
                    state["repositories"].append(row)
                initial = row["inventory"]["last_complete_at"] is None and not row["pulls"]
                scan = inventories[key]
                coverage = {
                    "outcome": scan["outcome"], "observed_at": observed_at, "pages": scan["pages"],
                    "error": scan["error"], "last_complete_at": (
                        observed_at if scan["outcome"] == "complete" else row["inventory"]["last_complete_at"]
                    ),
                }
                if (coverage["outcome"], coverage["error"]) != (
                    row["inventory"]["outcome"], row["inventory"]["error"],
                ):
                    _event(state, key, None, "coverage", {
                        "outcome": coverage["outcome"], "error": coverage["error"],
                    }, now)
                row["inventory"] = coverage
                known = {pull["number"]: pull for pull in row["pulls"]}
                for number in scan["numbers"]:
                    if number not in known:
                        if len(row["pulls"]) >= MAX_PULLS * 2:
                            raise ValueError("retained repository summary capacity reached")
                        pull = {"number": number, "member": True, "departed_at": None,
                                "observed_at": None, "error": "", "facts": None}
                        row["pulls"].append(pull)
                        known[number] = pull
                        _event(state, key, number, "baseline" if initial else "entry", None, now)
                    elif not known[number]["member"]:
                        known[number].update(
                            member=True, departed_at=None, error="selection_reentry_unconfirmed",
                        )
                        _event(state, key, number, "entry", None, now)
                for number, pull in known.items():
                    if scan["outcome"] == "complete" and number not in scan["numbers"] and pull["member"]:
                        pull.update(member=False, departed_at=now)
                        _event(state, key, number, "departure", pull["facts"], now)
                    selected = (key, identity.repo, number)
                    if selected not in snapshots:
                        continue
                    facts = copy.deepcopy(snapshots[selected])
                    try:
                        if facts is not None:
                            validate_facts(facts)
                            if facts["provider"] != identity.provider:
                                raise ValueError("normalized provider binding mismatch")
                    except (ValueError, TypeError):
                        facts = None
                    if facts is None:
                        pull["error"] = "snapshot_unavailable"
                        continue
                    previous = pull["facts"]
                    if previous != facts:
                        kind = "baseline" if previous is None else (
                            "terminal" if facts["pr_state"] == "closed" or facts["merged"] else "change"
                        )
                        _event(state, key, number, kind, facts, now)
                    pull.update(facts=facts, observed_at=observed_at, error="")
            return {"status": "projected", "cursor": state["next_sequence"] - 1}
        return self._transaction(apply, persist=True, **authority)

    def query(self, key, *, after=0, limit=16, number=None, **authority):
        """Read a bounded current-state page without triggering provider acquisition."""
        if key not in self.identities or type(after) is not int or after < 0 or (
            type(limit) is not int or not 1 <= limit <= 16
        ) or number is not None and (type(number) is not int or number < 1):
            raise ValueError("invalid bounded repository query")
        def query(state):
            row = next((row for row in state["repositories"]
                        if RepositoryIdentity.parse(row["identity"]).key == key), None)
            row = row or _new_repository(self.identities[key])
            rows = sorted((pull for pull in row["pulls"] if pull["number"] > after
                           and (number is None or pull["number"] == number)),
                          key=lambda pull: pull["number"])
            return {
                "identity": row["identity"], "inventory": row["inventory"],
                "pulls": rows[:limit], "more": len(rows) > limit,
                "cursor": state["next_sequence"] - 1, "now": int(self.store.clock()),
            }
        return self._transaction(query, **authority)

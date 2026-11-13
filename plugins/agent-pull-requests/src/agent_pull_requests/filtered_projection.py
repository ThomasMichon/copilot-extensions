"""Coalesced population demand and independent membership in ReplayStore custody."""

import copy
import uuid

from pr_filters import Definitions, Expression, MATCH, NONMATCH, PROTOCOL as FILTER_PROTOCOL
from pr_filters.acquisition import plan

from .filtered_state import facts as validate_facts, release_inactive, revision, trim
from .repository_contract import MAX_PULLS, RepositoryBackpressure
from .repository_projection import _new_repository


class FilteredProjection:
    def __init__(self, store, identities, *, capabilities, definitions=None):
        if not store.repository_filtered:
            raise ValueError("filtered projection requires selected storage generation")
        self.store = store
        self.identities = {identity.key: identity for identity in identities}
        if any(identity.namespace != store.namespace for identity in self.identities.values()):
            raise ValueError("filtered namespace differs")
        self.capabilities = copy.deepcopy(capabilities)
        if set(self.capabilities) != set(self.identities):
            raise ValueError("each filtered repository requires its executing driver declaration")
        self.definitions = definitions or Definitions.parse({"protocol": FILTER_PROTOCOL, "sets": {}})

    def repository(self, state, key):
        identity = self.identities[key]
        row = next((r for r in state["repositories"] if r["identity"] == identity.document()), None)
        if row is None:
            row = _new_repository(identity)
            state["repositories"].append(row)
        return row

    def require(self, key, expression):
        identity = self.identities[key]
        support = self.capabilities[key]
        expression.require(support, {identity.repo})
        if identity.provider == "azure-devops":
            plan(expression, support.get("acquisition", {}))

    @staticmethod
    def expression(row):
        return Expression.parse(row["request"]["filter"])

    def seed(self, state, row, now):
        repository = self.repository(state, row["target"])
        selected = self.expression(row)
        row["members"] = [
            p["number"] for p in repository["pulls"] if selected.evaluate(p["facts"], now=now) == MATCH
        ]
        row["revisions"] = {str(p["number"]): revision(p["facts"]) for p in repository["pulls"]
                            if p["number"] in row["members"]}
        row["coverage"] = {
            "outcome": "unknown", "observed_at": None, "last_complete_at": None, "pages": 0, "error": "",
        }

    def demands(self, **authority):
        """One union per configured repository, containing only live interest."""
        def read(document):
            state, now = document["repository_cache"], int(self.store.clock())
            result = {}
            for key, identity in self.identities.items():
                rows = [r for r in state["registrations"] if r["target"] == key
                        and not r["cancelled"] and r["expires_at"] > now]
                if not rows:
                    continue
                expression = self.union(rows)
                self.require(key, expression)
                result[key] = {
                    "filter": expression, "registrations": {
                        row["registration_id"]: (self.expression(row).revision, row["generation"]) for row in rows
                    },
                }
            return result
        return self.store._review_transaction(read, persist=False, **authority)

    def union(self, rows):
        selections = {self.expression(row).canonical: self.expression(row) for row in rows}
        if len(selections) == 1:
            return next(iter(selections.values()))
        return Expression.parse({
            "protocol": FILTER_PROTOCOL, "where": {"any": [
                e.document()["where"] for e in selections.values()
            ]},
        })

    @staticmethod
    def event(state, row, number, kind, facts):
        state["events"].append({
            "sequence": state["next_sequence"], "event_id": uuid.uuid4().hex,
            "target": row["target"], "registration_id": row["registration_id"],
            "number": number, "kind": kind, "facts": copy.deepcopy(facts),
        })
        state["next_sequence"] += 1

    def apply(self, key, demand, scan, *, observed_at, **authority):
        """A cancelled/expired/new interest cannot borrow an in-flight acquisition."""
        identity = self.identities[key]
        prepared = {}
        for pull in scan["pulls"]:
            number = pull["number"]
            if type(number) is not int or number < 1 or number in prepared:
                raise ValueError("invalid or duplicate acquired population identity")
            validate_facts(pull["facts"], identity)
            prepared[number] = copy.deepcopy(pull["facts"])
        if len(prepared) > MAX_PULLS:
            raise ValueError("population acquisition capacity exceeded")

        def apply(document):
            state, now = document["repository_cache"], int(self.store.clock())
            if type(observed_at) is not int or not 0 <= observed_at <= now:
                raise ValueError("invalid population cycle instant")
            release_inactive(state, now)
            rows = [r for r in state["registrations"] if r["target"] == key
                    and demand["registrations"].get(r["registration_id"]) == (
                        self.expression(r).revision, r["generation"],
                    )
                    and not r["cancelled"] and r["expires_at"] > now]
            if not rows:
                return {"status": "withdrawn", "observed": 0}
            repository = self.repository(state, key)
            old = {p["number"]: p["facts"] for p in repository["pulls"]}
            known = {**old, **prepared}
            first_new = state["next_sequence"]
            for row in rows:
                selected = self.expression(row)
                previous = set(row["members"])
                members, unknown = set(previous), False
                for number, facts in known.items():
                    # Incomplete discovery never proves absence. Cached facts
                    # may still prove clock-driven departure without a new head.
                    result = selected.evaluate(facts, now=observed_at)
                    if number not in prepared and scan["outcome"] == "complete":
                        result = NONMATCH
                    if result == MATCH:
                        if number in prepared or number in previous:
                            members.add(number)
                    elif result == NONMATCH:
                        members.discard(number)
                    else:
                        unknown = True
                    if number in members and number not in previous:
                        self.event(state, row, number, "entry", facts)
                    elif number not in members and number in previous:
                        self.event(state, row, number, "departure", facts)
                    elif number in members and row["revisions"].get(str(number)) != revision(facts):
                        self.event(state, row, number, "change", facts)
                    if number in members:
                        row["revisions"][str(number)] = revision(facts)
                    else:
                        row["revisions"].pop(str(number), None)
                coverage = {
                    "outcome": "partial" if unknown and scan["outcome"] == "complete" else scan["outcome"],
                    "error": "facts_unknown" if unknown and scan["outcome"] == "complete" else scan["error"],
                    "pages": scan["pages"], "observed_at": observed_at,
                    "last_complete_at": row["coverage"]["last_complete_at"],
                }
                if coverage["outcome"] == "complete":
                    coverage["last_complete_at"] = observed_at
                if (coverage["outcome"], coverage["error"]) != (
                    row["coverage"]["outcome"], row["coverage"]["error"],
                ):
                    self.event(state, row, None, "coverage", coverage)
                row.update(members=sorted(members), coverage=coverage)
            retained = set(prepared).union(*(set(row["members"]) for row in state["registrations"]
                                            if row["target"] == key))
            known = {number: facts for number, facts in known.items() if number in retained}
            if len(known) > MAX_PULLS * 2:
                raise RepositoryBackpressure("filtered retained membership exceeds capacity")
            repository["pulls"] = [{
                "number": number, "facts": facts,
                "observed_at": observed_at if number in prepared else next(
                    p["observed_at"] for p in repository["pulls"] if p["number"] == number
                ),
            } for number, facts in sorted(known.items())]
            repository["inventory"] = {
                "outcome": scan["outcome"], "observed_at": observed_at, "error": scan["error"],
                "pages": scan["pages"], "last_complete_at": (
                    observed_at if scan["outcome"] == "complete"
                    else repository["inventory"]["last_complete_at"]
                ),
            }
            trim(state, new_sequence=first_new, now=now)
            return {"status": "projected", "observed": len(prepared)}
        return self.store._review_transaction(apply, persist=True, **authority)

"""Task-purpose metadata, independent of execution and routing."""

from __future__ import annotations

import hashlib
import json

from .queue_records import TaskError

CATEGORIES = ("work", "maintenance")


def category_value(value: str | None, *, selection: bool = False) -> str | None:
    if selection and value in (None, "all"):
        return None
    if value not in CATEGORIES:
        raise TaskError("category must be work or maintenance" + (" or all" if selection else ""))
    return value


class CategoryQueueMixin:
    def classify_maintenance(
        self,
        *,
        repo: str,
        source: str,
        origin_ref: str,
        labels: list[str],
        apply: bool = False,
        preview_token: str | None = None,
        actor: str = "operator",
        limit: int = 500,
    ) -> dict:
        """Preview/apply an exact, corroborated provenance selection; never replay work."""
        repo = self._canonical_repo(repo)
        if not repo or not source.strip() or not origin_ref.strip() or not labels:
            raise TaskError("classification requires repo, exact source, origin_ref and labels")
        if any(not isinstance(label, str) or not label.strip() for label in labels):
            raise TaskError("classification labels must be non-empty strings")
        if not actor.strip() or limit < 1:
            raise TaskError("classification requires an actor and positive limit")
        labels = sorted(set(labels))
        safe_labels = "CASE WHEN json_valid(tasks.labels) THEN tasks.labels ELSE '[]' END"
        predicates = [
            "repo = ?",
            "source = ?",
            "origin_ref = ?",
            f"json_type({safe_labels}) = 'array'",
            f"NOT EXISTS (SELECT 1 FROM json_each({safe_labels}) WHERE type <> 'text')",
        ]
        for _label in labels:
            predicates.append(f"EXISTS (SELECT 1 FROM json_each({safe_labels}) WHERE value = ?)")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            selected = conn.execute(
                f"SELECT id, category, status FROM tasks WHERE {' AND '.join(predicates)} "
                "ORDER BY id LIMIT ?",
                (repo, source, origin_ref, *labels, limit + 1),
            ).fetchall()
            if len(selected) > limit:
                raise TaskError(
                    "classification exceeds limit; narrow provenance or raise the limit"
                )
            evidence = {
                "repo": repo,
                "source": source,
                "origin_ref": origin_ref,
                "labels": labels,
                "ids": [row["id"] for row in selected],
            }
            token = hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if apply and preview_token != token:
                raise TaskError("classification requires the current preview_token; preview again")
            changed = 0
            for row in selected:
                if apply and row["category"] == "work":
                    conn.execute(
                        "UPDATE tasks SET category = 'maintenance' WHERE id = ?", (row["id"],)
                    )
                    self._audit(
                        conn,
                        row["id"],
                        ts=self._now(None),
                        from_status=row["status"],
                        to_status=row["status"],
                        worker=actor,
                        note=json.dumps(
                            {
                                "operation": "category-classification",
                                "from": "work",
                                "to": "maintenance",
                                "preview_token": token,
                                "provenance": {
                                    key: value for key, value in evidence.items() if key != "ids"
                                },
                            },
                            sort_keys=True,
                        ),
                    )
                    changed += 1
            conn.execute("COMMIT")
        return {
            "applied": apply,
            "changed": changed,
            "preview_token": token,
            "provenance": evidence,
            "tasks": [
                {"id": row["id"], "category": "maintenance" if apply else row["category"]}
                for row in selected
            ],
        }

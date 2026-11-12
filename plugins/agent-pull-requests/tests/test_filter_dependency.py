"""The independently packaged PR consumer uses the canonical expression owner."""

from pr_filters import Expression, PROTOCOL, UNKNOWN


def test_consumer_import_retains_canonical_driver_scope_and_unknown_semantics():
    selected = Expression.parse({
        "protocol": PROTOCOL,
        "where": {"repo": ["Project/repository"], "from": {"users": ["user-id"]}},
    })
    selected.require({"protocol": PROTOCOL, "fields": ["repo", "from"]}, {"Project/repository"})
    assert selected.evaluate({"repo": "Project/repository"}, now=0) == UNKNOWN

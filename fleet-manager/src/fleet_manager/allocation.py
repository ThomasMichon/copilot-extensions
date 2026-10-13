"""Shared local release-history schema and indexed allocation-generation lookup."""

from .planning import PlanError


RELEASE_INDEX = "allocation_release_generations"


def create_release_history(connection):
    connection.execute(
        "CREATE TABLE allocation_releases("
        "operation_id INTEGER PRIMARY KEY REFERENCES operations(id),"
        "resource TEXT NOT NULL,generation INTEGER NOT NULL,proof BLOB NOT NULL)"
    )
    connection.execute(
        f"CREATE INDEX {RELEASE_INDEX} ON allocation_releases(resource,generation DESC)"
    )


def verify_release_index(connection):
    row = connection.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (RELEASE_INDEX,),
    ).fetchone()
    listed = [entry[2:] for entry in connection.execute("PRAGMA index_list(allocation_releases)")
              if entry[1] == RELEASE_INDEX]
    columns = tuple(entry[2:] for entry in
                    connection.execute(f"PRAGMA index_xinfo({RELEASE_INDEX})"))
    if (row != ("allocation_releases",) or listed != [(0, "c", 0)]
            or columns != (("resource", 0, "BINARY", 1), ("generation", 1, "BINARY", 1),
                           (None, 0, "BINARY", 0))):
        raise PlanError("unrecognized allocation release index")


def check_released_generation(connection, generation, resource):
    row = connection.execute(
        "SELECT generation FROM allocation_releases WHERE resource=? "
        "ORDER BY generation DESC LIMIT 1", (resource,),
    ).fetchone()
    if row is not None and generation <= row[0]:
        raise PlanError("allocation generation must exceed released generations")

"""Extract and harden the reviewed replication/HA source bundle."""

from __future__ import annotations

import base64
import hashlib
import io
import shutil
import tarfile
from pathlib import Path
from pathlib import PurePosixPath

ENCODED_SHA256 = "7ebf35734355f7ee01b2e32dfb54b02371c7f429ee5fcee6daa090a93744cc0f"
ARCHIVE_SHA256 = "148ba7f90107558ba1a5ffcb2f40dd040d068b1e5c84fcf7a09607bc0bd34964"


def extract_source() -> None:
    encoded = b"".join(path.read_bytes() for path in sorted(Path(".replication-transfer").glob("part-*")))
    if hashlib.sha256(encoded).hexdigest() != ENCODED_SHA256:
        raise RuntimeError("replication source Base64 checksum mismatch")
    compressed = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(compressed).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("replication source archive checksum mismatch")

    for target in (
        Path("src/postgres_mcp/replication"),
        Path("tests/unit/replication"),
        Path("tests/unit/ha"),
        Path("tests/integration/replication"),
    ):
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    with tarfile.open(fileobj=io.BytesIO(compressed), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeError(f"unsupported archive entry: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f"unexpected archive entry: {member.name}")
        archive.extractall(path=".", filter="data")


def guard_recovery_only_functions() -> None:
    source_path = Path("src/postgres_mcp/replication/postgres.py")
    source = source_path.read_text()
    old = """    pg_catalog.pg_is_wal_replay_paused() AS replay_paused,
        CASE WHEN pg_catalog.pg_is_in_recovery() THEN NULL ELSE pg_catalog.pg_current_wal_lsn()::text END AS current_wal_lsn,
        pg_catalog.pg_last_wal_receive_lsn()::text AS received_wal_lsn,
        pg_catalog.pg_last_wal_replay_lsn()::text AS replayed_wal_lsn,
"""
    new = """    CASE
            WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_is_wal_replay_paused()
            ELSE false
        END AS replay_paused,
        CASE WHEN pg_catalog.pg_is_in_recovery() THEN NULL ELSE pg_catalog.pg_current_wal_lsn()::text END AS current_wal_lsn,
        CASE
            WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_receive_lsn()::text
            ELSE NULL
        END AS received_wal_lsn,
        CASE
            WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_replay_lsn()::text
            ELSE NULL
        END AS replayed_wal_lsn,
"""
    if source.count(old) != 1:
        raise RuntimeError("expected one unguarded replication metadata block")
    source_path.write_text(source.replace(old, new, 1))

    test_path = Path("tests/unit/replication/test_replication_postgres.py")
    tests = test_path.read_text()
    marker = "\ndef test_row_conversion_builds_complete_topology() -> None:\n"
    contract = '''

def test_metadata_query_guards_recovery_only_functions() -> None:
    from postgres_mcp.replication.postgres import _METADATA_SQL

    normalized = " ".join(_METADATA_SQL.split())
    assert "WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_is_wal_replay_paused()" in normalized
    assert "WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_receive_lsn()::text" in normalized
    assert "WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_replay_lsn()::text" in normalized
'''
    if tests.count(marker) != 1:
        raise RuntimeError("expected one replication row-conversion test marker")
    test_path.write_text(tests.replace(marker, contract + marker, 1))


if __name__ == "__main__":
    extract_source()
    guard_recovery_only_functions()

from __future__ import annotations

import base64
import hashlib
import io
import tarfile
from pathlib import Path, PurePosixPath

TEXT_SHA256 = "76d5ed8657925962fe751b81bbe55a8d9c8324b912302902ccba5323dabd1f15"
ARCHIVE_SHA256 = "6ddfdc1c3a0afe5216f698048368c1e395f6f683bfde2b80624377365b220a50"


def replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text()
    if content.count(old) != 1:
        raise RuntimeError(f"expected one replacement target in {path}")
    path.write_text(content.replace(old, new, 1))


def extract_bundle() -> None:
    encoded = b"".join(path.read_bytes() for path in sorted(Path(".maintenance-transfer").glob("part-*")))
    if hashlib.sha256(encoded).hexdigest() != TEXT_SHA256:
        raise RuntimeError("maintenance source bundle text checksum mismatch")

    archive = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("maintenance source bundle archive checksum mismatch")

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe maintenance bundle path: {member.name!r}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"links are not permitted in maintenance bundle: {member.name!r}")
        source.extractall(".", filter="data")


def apply_replay_fix() -> None:
    backend = Path("src/postgres_mcp/maintenance/postgres.py")
    old = '''                        snapshot = await self._inspect_on_connection(connection, plan.target)
                        self._verify_snapshot(plan, snapshot)
                        await self._ensure_ledger(connection)
                        await self._validate_ledger(connection)
                        existing = await self._get_by_name(connection, plan.name)
                        if existing is not None:
                            stored_plan = self._verified_plan(existing)
                            if stored_plan.review_hash != plan.review_hash:
                                raise MaintenanceConflictError(f"maintenance operation {plan.name!r} already exists with different reviewed content")
                            existing_status = MaintenanceOperationStatus(str(existing["status"]))
                            if existing_status in _TERMINAL_SUCCESS:
                                return MaintenanceOperationResult(
                                    MaintenanceOperationStatus.ALREADY_SUCCEEDED,
                                    _record_from_row(existing),
                                )
                            if existing_status in {
                                MaintenanceOperationStatus.RUNNING,
                                MaintenanceOperationStatus.UNKNOWN,
                            }:
                                raise MaintenanceConflictError(f"maintenance operation {plan.name!r} has an unresolved outcome")
                            active_row = await self._restart_record(connection, plan)
                        else:
                            active_row = await self._insert_running_record(connection, plan)
'''
    new = '''                        ledger_exists = await self._ledger_exists(connection)
                        existing: dict[str, Any] | None = None
                        if ledger_exists:
                            await self._validate_ledger(connection)
                            existing = await self._get_by_name(connection, plan.name)
                            if existing is not None:
                                stored_plan = self._verified_plan(existing)
                                if stored_plan.review_hash != plan.review_hash:
                                    raise MaintenanceConflictError(
                                        f"maintenance operation {plan.name!r} already exists with different reviewed content"
                                    )
                                existing_status = MaintenanceOperationStatus(str(existing["status"]))
                                if existing_status in _TERMINAL_SUCCESS:
                                    return MaintenanceOperationResult(
                                        MaintenanceOperationStatus.ALREADY_SUCCEEDED,
                                        _record_from_row(existing),
                                    )
                                if existing_status in {
                                    MaintenanceOperationStatus.RUNNING,
                                    MaintenanceOperationStatus.UNKNOWN,
                                }:
                                    raise MaintenanceConflictError(f"maintenance operation {plan.name!r} has an unresolved outcome")

                        snapshot = await self._inspect_on_connection(connection, plan.target)
                        self._verify_snapshot(plan, snapshot)
                        if not ledger_exists:
                            await self._ensure_ledger(connection)
                            await self._validate_ledger(connection)
                        if existing is not None:
                            active_row = await self._restart_record(connection, plan)
                        else:
                            active_row = await self._insert_running_record(connection, plan)
'''
    replace_once(backend, old, new)

    tests = Path("tests/unit/maintenance/test_maintenance_postgres.py")
    replace_once(
        tests,
        '    monkeypatch.setattr(adapter, "_ensure_ledger", AsyncMock())\n',
        '    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=existing is not None))\n'
        '    monkeypatch.setattr(adapter, "_ensure_ledger", AsyncMock())\n',
    )
    replace_once(
        tests,
        '''    result = await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert result.status is MaintenanceOperationStatus.ALREADY_SUCCEEDED
    cursor.execute.assert_not_awaited()
    finish.assert_not_awaited()
''',
        '''    inspect = AsyncMock(return_value=snapshot(oid=99))
    monkeypatch.setattr(adapter, "_inspect_on_connection", inspect)

    result = await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert result.status is MaintenanceOperationStatus.ALREADY_SUCCEEDED
    inspect.assert_not_awaited()
    cursor.execute.assert_not_awaited()
    finish.assert_not_awaited()
''',
    )
    replace_once(
        tests,
        '''    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_inspect_on_connection", AsyncMock(return_value=snapshot(oid=99)))
''',
        '''    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(adapter, "_inspect_on_connection", AsyncMock(return_value=snapshot(oid=99)))
''',
    )


def remove_superseded_tests() -> None:
    for path in (
        Path("tests/unit/maintenance/test_domain.py"),
        Path("tests/unit/maintenance/test_postgres.py"),
        Path("tests/unit/maintenance/test_service.py"),
    ):
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    extract_bundle()
    remove_superseded_tests()
    apply_replay_fix()

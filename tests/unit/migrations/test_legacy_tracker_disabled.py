"""The legacy split migration/ledger API must not remain usable."""

from __future__ import annotations

import pytest

from postgres_mcp.migrations.migration_tracker import MigrationTracker


def test_legacy_tracker_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="atomic reviewed migration service"):
        MigrationTracker(object())  # type: ignore[arg-type]

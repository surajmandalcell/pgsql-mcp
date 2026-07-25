"""Retired compatibility surface for the historical split migration tracker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from typing_extensions import TypedDict

MIGRATION_TABLE_NAME = "_postgres_mcp_migrations"


class MigrationStatusEntry(TypedDict):
    name: str
    applied_at: str
    batch: int


class MigrationStatus(TypedDict):
    total_applied: int
    latest_batch: int
    migrations: list[MigrationStatusEntry]


@dataclass
class MigrationRecord:
    id: int
    name: str
    applied_at: datetime
    checksum: str
    batch: int


class MigrationTracker:
    """Disabled legacy API that could split DDL from ledger bookkeeping."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("MigrationTracker is retired; use the atomic reviewed migration service so DDL and ledger state commit together")

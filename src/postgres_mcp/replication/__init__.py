"""Structured PostgreSQL replication and failover-readiness diagnostics."""

from .domain import MAX_REPLICATION_ROWS
from .domain import FailoverReadinessAssessment
from .domain import FindingSeverity
from .domain import LogicalSubscription
from .domain import NodeRole
from .domain import Publication
from .domain import ReplicationError
from .domain import ReplicationExecutionError
from .domain import ReplicationFinding
from .domain import ReplicationSlot
from .domain import ReplicationStandby
from .domain import ReplicationThresholds
from .domain import ReplicationTopology
from .domain import ReplicationValidationError
from .domain import WalReceiver
from .domain import assess_failover_readiness
from .postgres import PostgresReplicationRepository
from .service import ReplicationRepository
from .service import ReplicationService

__all__ = [
    "MAX_REPLICATION_ROWS",
    "FailoverReadinessAssessment",
    "FindingSeverity",
    "LogicalSubscription",
    "NodeRole",
    "PostgresReplicationRepository",
    "Publication",
    "ReplicationError",
    "ReplicationExecutionError",
    "ReplicationFinding",
    "ReplicationRepository",
    "ReplicationService",
    "ReplicationSlot",
    "ReplicationStandby",
    "ReplicationThresholds",
    "ReplicationTopology",
    "ReplicationValidationError",
    "WalReceiver",
    "assess_failover_readiness",
]

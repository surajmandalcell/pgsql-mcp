"""Deterministic mutation gate for pgsql-mcp safety invariants.

The gate first proves the focused tests pass unchanged. It then applies one
semantic mutation at a time and requires pytest to fail with an ordinary test
failure. Source text must match exactly once, so upstream drift cannot silently
skip a mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from typing import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Mutation:
    """One exact semantic source mutation and the tests expected to kill it."""

    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MutationResult:
    """Machine-readable evidence for one mutation run."""

    name: str
    path: str
    status: str
    returncode: int
    duration_seconds: float
    output_tail: str


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "runtime_rejects_zero_timeout",
        "src/postgres_mcp/runtime.py",
        "if self.timeout_seconds <= 0:",
        "if self.timeout_seconds < 0:",
        ("tests/unit/runtime/test_runtime.py",),
    ),
    Mutation(
        "runtime_accepts_exact_absolute_row_ceiling",
        "src/postgres_mcp/runtime.py",
        "if row_limit > self.absolute_max_rows:",
        "if row_limit >= self.absolute_max_rows:",
        ("tests/unit/runtime/test_runtime.py",),
    ),
    Mutation(
        "query_guard_rejects_non_readonly_statements",
        "src/postgres_mcp/sql/query_guard.py",
        "if not isinstance(statement, _PUBLIC_READONLY_STATEMENTS):",
        "if False and not isinstance(statement, _PUBLIC_READONLY_STATEMENTS):",
        ("tests/unit/sql/test_query_guard.py",),
    ),
    Mutation(
        "query_guard_rejects_session_mutation",
        "src/postgres_mcp/sql/query_guard.py",
        "if unqualified in _SESSION_MUTATING_FUNCTIONS:",
        "if False and unqualified in _SESSION_MUTATING_FUNCTIONS:",
        ("tests/unit/sql/test_query_guard.py",),
    ),
    Mutation(
        "transaction_rejects_data_modifying_cte",
        "src/postgres_mcp/sql/transaction.py",
        "if isinstance(statement, SelectStmt) and _contains_nested_mutation(statement, root=statement):",
        "if False and isinstance(statement, SelectStmt) and _contains_nested_mutation(statement, root=statement):",
        ("tests/unit/sql/test_transaction.py",),
    ),
    Mutation(
        "transaction_requires_where_clause",
        "src/postgres_mcp/sql/transaction.py",
        'if isinstance(statement, (UpdateStmt, DeleteStmt)) and getattr(statement, "whereClause", None) is None:',
        'if False and isinstance(statement, (UpdateStmt, DeleteStmt)) and getattr(statement, "whereClause", None) is None:',
        ("tests/unit/sql/test_transaction.py",),
    ),
    Mutation(
        "transaction_requires_mutation_ceiling",
        "src/postgres_mcp/sql/transaction.py",
        "if step.max_affected_rows is None:",
        "if False and step.max_affected_rows is None:",
        ("tests/unit/sql/test_transaction.py",),
    ),
    Mutation(
        "result_encoding_preserves_json_safe_integer_boundary",
        "src/postgres_mcp/sql/results.py",
        "if abs(value) <= _JSON_SAFE_INTEGER:",
        "if abs(value) < _JSON_SAFE_INTEGER:",
        ("tests/unit/sql/test_results.py",),
    ),
    Mutation(
        "migration_requires_exact_review_hash",
        "src/postgres_mcp/migrations/service.py",
        "if normalized_hash != plan.review_hash:",
        "if False and normalized_hash != plan.review_hash:",
        ("tests/unit/migrations/test_migration_service.py",),
    ),
    Mutation(
        "migration_rejects_nontransactional_plan",
        "src/postgres_mcp/migrations/service.py",
        "if not plan.applyable:",
        "if False and not plan.applyable:",
        ("tests/unit/migrations/test_migration_service.py",),
    ),
    Mutation(
        "migration_lock_timeout_cannot_exceed_operation_timeout",
        "src/postgres_mcp/migrations/service.py",
        "if lock_timeout_seconds > timeout_seconds:",
        "if False and lock_timeout_seconds > timeout_seconds:",
        ("tests/unit/migrations/test_migration_service.py",),
    ),
    Mutation(
        "maintenance_requires_constant_time_review_hash_match",
        "src/postgres_mcp/maintenance/service.py",
        "if not hmac.compare_digest(plan.review_hash, supplied_hash):",
        "if False and not hmac.compare_digest(plan.review_hash, supplied_hash):",
        ("tests/unit/maintenance/test_maintenance_service.py",),
    ),
    Mutation(
        "maintenance_lock_timeout_cannot_exceed_operation_timeout",
        "src/postgres_mcp/maintenance/service.py",
        "if lock_timeout_seconds > timeout_seconds:",
        "if False and lock_timeout_seconds > timeout_seconds:",
        ("tests/unit/maintenance/test_maintenance_service.py",),
    ),
)

BASELINE_TESTS: tuple[str, ...] = tuple(dict.fromkeys(test for mutation in MUTATIONS for test in mutation.tests))


def _pytest_command(tests: Sequence[str], *, cache_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-X",
        f"pycache_prefix={cache_dir}",
        "-m",
        "pytest",
        "-q",
        "--disable-warnings",
        "--maxfail=1",
        *tests,
    ]


def run_pytest(
    tests: Sequence[str],
    *,
    cache_dir: Path,
    timeout_seconds: int = 180,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Run focused tests in a fresh bytecode cache against the current source."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(REPOSITORY_ROOT / "src"), str(REPOSITORY_ROOT / "tests"), environment.get("PYTHONPATH")])
    )
    return runner(
        _pytest_command(tests, cache_dir=cache_dir),
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _replace_exactly_once(path: Path, old: str, new: str) -> str:
    original = path.read_text()
    count = original.count(old)
    if count != 1:
        raise RuntimeError(f"mutation target {path} expected exactly one match, found {count}")
    path.write_text(original.replace(old, new, 1))
    return original


def run_mutation(mutation: Mutation, *, timeout_seconds: int = 180) -> MutationResult:
    """Apply one mutation, require an ordinary test failure, and restore source."""
    target = REPOSITORY_ROOT / mutation.path
    original = _replace_exactly_once(target, mutation.old, mutation.new)
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="pgsql-mcp-mutant-cache-") as cache:
            completed = run_pytest(mutation.tests, cache_dir=Path(cache), timeout_seconds=timeout_seconds)
    finally:
        target.write_text(original)

    duration = time.monotonic() - started
    output = (completed.stdout + "\n" + completed.stderr).strip()
    tail = "\n".join(output.splitlines()[-40:])
    if completed.returncode == 0:
        status = "survived"
    elif completed.returncode == 1:
        status = "killed"
    else:
        status = "infrastructure_error"
    return MutationResult(mutation.name, mutation.path, status, completed.returncode, duration, tail)


def run_gate(*, selected: set[str] | None = None, timeout_seconds: int = 180) -> dict[str, object]:
    """Run a clean baseline and require every selected mutant to be killed."""
    catalog = {mutation.name: mutation for mutation in MUTATIONS}
    if selected is not None:
        unknown = selected.difference(catalog)
        if unknown:
            raise ValueError(f"unknown mutations: {', '.join(sorted(unknown))}")
        mutations = tuple(catalog[name] for name in sorted(selected))
    else:
        mutations = MUTATIONS

    with tempfile.TemporaryDirectory(prefix="pgsql-mcp-baseline-cache-") as cache:
        baseline = run_pytest(BASELINE_TESTS, cache_dir=Path(cache), timeout_seconds=timeout_seconds)
    if baseline.returncode != 0:
        output = (baseline.stdout + "\n" + baseline.stderr).strip()
        raise RuntimeError(f"mutation baseline failed before source changes:\n{output[-8000:]}")

    results = [run_mutation(mutation, timeout_seconds=timeout_seconds) for mutation in mutations]
    survivors = [result.name for result in results if result.status == "survived"]
    infrastructure_errors = [result.name for result in results if result.status == "infrastructure_error"]
    return {
        "schema_version": 1,
        "baseline": "passed",
        "mutation_count": len(results),
        "killed": sum(result.status == "killed" for result in results),
        "survivors": survivors,
        "infrastructure_errors": infrastructure_errors,
        "passed": not survivors and not infrastructure_errors,
        "results": [asdict(result) for result in results],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutation", action="append", dest="mutations", help="Run only a named mutation; repeatable")
    parser.add_argument("--output", type=Path, default=Path("mutation-results.json"))
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--list", action="store_true", help="List mutation names and exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list:
        for mutation in MUTATIONS:
            print(mutation.name)
        return 0
    if args.timeout_seconds < 1:
        raise ValueError("timeout-seconds must be positive")
    evidence = run_gate(selected=set(args.mutations) if args.mutations else None, timeout_seconds=args.timeout_seconds)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {key: evidence[key] for key in ("mutation_count", "killed", "survivors", "infrastructure_errors", "passed")},
            sort_keys=True,
        )
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

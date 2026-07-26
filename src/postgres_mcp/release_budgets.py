"""Deterministic release-budget measurements for pgsql-mcp artifacts."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Protocol

MIB = 1024 * 1024
DEFAULT_REPETITIONS = 5


class CommandRunner(Protocol):
    """Port used by release measurements that execute local commands."""

    def __call__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


def _run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


@dataclass(frozen=True, slots=True)
class ReleaseBudgetLimits:
    """Blocking upper bounds for release artifacts and cold imports."""

    core_process_ms: float = 750.0
    core_rss_mib: float = 64.0
    lite_process_ms: float = 1_500.0
    lite_rss_mib: float = 128.0
    wheel_mib: float = 2.0
    image_mib: float = 300.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a positive number")


@dataclass(frozen=True, slots=True)
class ImportMeasurement:
    """Median cold-process and resident-memory cost for one import target."""

    module: str
    process_ms: float
    import_ms: float
    rss_mib: float
    imported_modules: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "process_ms": round(self.process_ms, 3),
            "import_ms": round(self.import_ms, 3),
            "rss_mib": round(self.rss_mib, 3),
            "imported_modules": list(self.imported_modules),
        }


@dataclass(frozen=True, slots=True)
class ReleaseMeasurements:
    """Measured release evidence consumed by the budget evaluator."""

    core_import: ImportMeasurement
    lite_import: ImportMeasurement
    wheel_bytes: int
    image_bytes: int

    def __post_init__(self) -> None:
        if isinstance(self.wheel_bytes, bool) or self.wheel_bytes <= 0:
            raise ValueError("wheel_bytes must be a positive integer")
        if isinstance(self.image_bytes, bool) or self.image_bytes <= 0:
            raise ValueError("image_bytes must be a positive integer")

    def to_payload(self) -> dict[str, Any]:
        return {
            "core_import": self.core_import.to_payload(),
            "lite_import": self.lite_import.to_payload(),
            "wheel_bytes": self.wheel_bytes,
            "wheel_mib": round(self.wheel_bytes / MIB, 3),
            "image_bytes": self.image_bytes,
            "image_mib": round(self.image_bytes / MIB, 3),
        }


@dataclass(frozen=True, slots=True)
class ReleaseBudgetReport:
    """Complete release-budget verdict with machine-readable violations."""

    limits: ReleaseBudgetLimits
    measurements: ReleaseMeasurements
    violations: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "limits": asdict(self.limits),
            "measurements": self.measurements.to_payload(),
            "violations": list(self.violations),
        }


_CHILD_PROBE = r"""
from __future__ import annotations

import importlib
import json
import resource
import sys
import time

module = sys.argv[1]
started = time.perf_counter()
importlib.import_module(module)
import_ms = (time.perf_counter() - started) * 1000
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
rss_bytes = int(rss if sys.platform == "darwin" else rss * 1024)
loaded = sorted(
    name
    for name in sys.modules
    if name.startswith("postgres_mcp")
)
print(json.dumps({"import_ms": import_ms, "rss_bytes": rss_bytes, "imported_modules": loaded}))
"""


def measure_cold_import(
    module: str,
    *,
    repetitions: int = DEFAULT_REPETITIONS,
    runner: CommandRunner = _run_command,
    python_executable: str = sys.executable,
) -> ImportMeasurement:
    """Measure one import in independent child processes and return medians."""
    if not module or not module.strip():
        raise ValueError("module must not be empty")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions <= 0:
        raise ValueError("repetitions must be a positive integer")

    process_samples: list[float] = []
    import_samples: list[float] = []
    rss_samples: list[float] = []
    imported_modules: tuple[str, ...] = ()
    environment = os.environ.copy()

    for _ in range(repetitions):
        started = time.perf_counter()
        completed = runner(
            [python_executable, "-c", _CHILD_PROBE, module],
            env=environment,
        )
        process_samples.append((time.perf_counter() - started) * 1000)
        payload = json.loads(completed.stdout)
        import_samples.append(float(payload["import_ms"]))
        rss_samples.append(int(payload["rss_bytes"]) / MIB)
        imported_modules = tuple(str(name) for name in payload["imported_modules"])

    return ImportMeasurement(
        module=module,
        process_ms=statistics.median(process_samples),
        import_ms=statistics.median(import_samples),
        rss_mib=statistics.median(rss_samples),
        imported_modules=imported_modules,
    )


def measure_wheel_bytes(path: Path) -> int:
    """Return the exact compressed wheel size after validating the artifact."""
    if not path.is_file() or path.suffix != ".whl":
        raise ValueError(f"wheel artifact does not exist or is not a .whl file: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("wheel artifact must not be empty")
    return size


def measure_docker_image_bytes(
    image: str,
    *,
    runner: CommandRunner = _run_command,
) -> int:
    """Return Docker's exact uncompressed image-size measurement."""
    if not image or not image.strip():
        raise ValueError("image must not be empty")
    completed = runner(
        ["docker", "image", "inspect", "--format", "{{.Size}}", image],
        env=None,
    )
    try:
        size = int(completed.stdout.strip())
    except ValueError as exc:
        raise ValueError("docker image inspection returned an invalid size") from exc
    if size <= 0:
        raise ValueError("docker image size must be positive")
    return size


def evaluate_release_budgets(
    measurements: ReleaseMeasurements,
    limits: ReleaseBudgetLimits | None = None,
) -> ReleaseBudgetReport:
    """Evaluate all release budgets without performing external work."""
    effective_limits = limits or ReleaseBudgetLimits()
    violations: list[str] = []
    _append_limit_violation(violations, "core cold process", measurements.core_import.process_ms, effective_limits.core_process_ms, "ms")
    _append_limit_violation(violations, "core import RSS", measurements.core_import.rss_mib, effective_limits.core_rss_mib, "MiB")
    _append_limit_violation(violations, "lite cold process", measurements.lite_import.process_ms, effective_limits.lite_process_ms, "ms")
    _append_limit_violation(violations, "lite import RSS", measurements.lite_import.rss_mib, effective_limits.lite_rss_mib, "MiB")
    _append_limit_violation(violations, "wheel size", measurements.wheel_bytes / MIB, effective_limits.wheel_mib, "MiB")
    _append_limit_violation(violations, "container image size", measurements.image_bytes / MIB, effective_limits.image_mib, "MiB")

    forbidden_core = {
        "postgres_mcp.server",
        "postgres_mcp.lite_server",
        "postgres_mcp.migrations",
        "postgres_mcp.data_ops",
        "postgres_mcp.database_health",
    }
    eagerly_loaded = sorted(forbidden_core.intersection(measurements.core_import.imported_modules))
    if eagerly_loaded:
        violations.append(f"core import eagerly loaded advanced modules: {', '.join(eagerly_loaded)}")

    forbidden_lite_prefixes = (
        "postgres_mcp.migrations",
        "postgres_mcp.data_ops",
        "postgres_mcp.database_health",
        "postgres_mcp.llm_index_advisor",
        "postgres_mcp.maintenance",
    )
    lite_leaks = sorted(name for name in measurements.lite_import.imported_modules if name.startswith(forbidden_lite_prefixes))
    if lite_leaks:
        violations.append(f"lite import loaded excluded modules: {', '.join(lite_leaks)}")

    return ReleaseBudgetReport(limits=effective_limits, measurements=measurements, violations=tuple(violations))


def _append_limit_violation(
    violations: list[str],
    label: str,
    measured: float,
    maximum: float,
    unit: str,
) -> None:
    if measured > maximum:
        violations.append(f"{label} is {measured:.3f} {unit}; maximum is {maximum:.3f} {unit}")

"""Contracts for deterministic release-budget measurements."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from postgres_mcp.release_budgets import ImportMeasurement
from postgres_mcp.release_budgets import ReleaseBudgetLimits
from postgres_mcp.release_budgets import ReleaseMeasurements
from postgres_mcp.release_budgets import evaluate_release_budgets
from postgres_mcp.release_budgets import measure_cold_import
from postgres_mcp.release_budgets import measure_docker_image_bytes
from postgres_mcp.release_budgets import measure_wheel_bytes


def import_measurement(
    module: str,
    *,
    process_ms: float = 10.0,
    rss_mib: float = 20.0,
    imported_modules: tuple[str, ...] = (),
) -> ImportMeasurement:
    return ImportMeasurement(module, process_ms, 5.0, rss_mib, imported_modules)


def measurements(**overrides) -> ReleaseMeasurements:
    values = {
        "core_import": import_measurement("postgres_mcp"),
        "lite_import": import_measurement("postgres_mcp.lite_server", process_ms=20.0, rss_mib=30.0),
        "wheel_bytes": 100_000,
        "image_bytes": 100_000_000,
    }
    values.update(overrides)
    return ReleaseMeasurements(**values)


def test_release_limits_and_measurements_reject_nonpositive_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        ReleaseBudgetLimits(core_process_ms=0)
    with pytest.raises(ValueError, match="wheel_bytes"):
        measurements(wheel_bytes=0)
    with pytest.raises(ValueError, match="image_bytes"):
        measurements(image_bytes=0)


def test_default_command_runner_executes_a_real_isolated_probe() -> None:
    result = measure_cold_import("json", repetitions=1)

    assert result.module == "json"
    assert result.process_ms > 0
    assert result.import_ms >= 0
    assert result.rss_mib > 0


def test_budget_evaluator_accepts_bounded_lazy_artifacts() -> None:
    report = evaluate_release_budgets(measurements())

    assert report.passed is True
    assert report.violations == ()
    assert report.to_payload()["measurements"]["wheel_mib"] > 0


def test_budget_evaluator_reports_every_limit_and_import_leak() -> None:
    result = measurements(
        core_import=import_measurement(
            "postgres_mcp",
            process_ms=800.0,
            rss_mib=80.0,
            imported_modules=("postgres_mcp.server",),
        ),
        lite_import=import_measurement(
            "postgres_mcp.lite_server",
            process_ms=1_600.0,
            rss_mib=140.0,
            imported_modules=("postgres_mcp.maintenance",),
        ),
        wheel_bytes=3 * 1024 * 1024,
        image_bytes=400 * 1024 * 1024,
    )

    report = evaluate_release_budgets(result)

    assert report.passed is False
    rendered = "\n".join(report.violations)
    for expected in (
        "core cold process",
        "core import RSS",
        "lite cold process",
        "lite import RSS",
        "wheel size",
        "container image size",
        "advanced modules",
        "excluded modules",
    ):
        assert expected in rendered


def test_cold_import_uses_independent_processes_and_medians() -> None:
    samples = iter(
        [
            {"import_ms": 3, "rss_bytes": 10 * 1024 * 1024, "imported_modules": ["postgres_mcp"]},
            {"import_ms": 5, "rss_bytes": 12 * 1024 * 1024, "imported_modules": ["postgres_mcp"]},
            {"import_ms": 4, "rss_bytes": 11 * 1024 * 1024, "imported_modules": ["postgres_mcp"]},
        ]
    )

    def runner(command, **kwargs):
        assert command[-1] == "postgres_mcp"
        assert isinstance(kwargs["env"], dict)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(next(samples)), stderr="")

    result = measure_cold_import("postgres_mcp", repetitions=3, runner=runner)

    assert result.import_ms == 4
    assert result.rss_mib == 11
    assert result.imported_modules == ("postgres_mcp",)


def test_cold_import_and_artifact_inputs_are_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="module"):
        measure_cold_import(" ")
    with pytest.raises(ValueError, match="repetitions"):
        measure_cold_import("postgres_mcp", repetitions=0)
    with pytest.raises(ValueError, match="wheel artifact"):
        measure_wheel_bytes(tmp_path / "missing.whl")

    wheel = tmp_path / "package.whl"
    wheel.touch()
    with pytest.raises(ValueError, match="must not be empty"):
        measure_wheel_bytes(wheel)

    wheel.write_bytes(b"wheel")
    assert measure_wheel_bytes(wheel) == 5


def test_docker_image_size_parses_and_rejects_invalid_output() -> None:
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="12345\n", stderr=""))
    assert measure_docker_image_bytes("pgsql-mcp:test", runner=runner) == 12345

    with pytest.raises(ValueError, match="image must not be empty"):
        measure_docker_image_bytes(" ", runner=runner)

    runner.return_value = subprocess.CompletedProcess([], 0, stdout="not-a-size", stderr="")
    with pytest.raises(ValueError, match="invalid size"):
        measure_docker_image_bytes("pgsql-mcp:test", runner=runner)

    runner.return_value = subprocess.CompletedProcess([], 0, stdout="0", stderr="")
    with pytest.raises(ValueError, match="must be positive"):
        measure_docker_image_bytes("pgsql-mcp:test", runner=runner)

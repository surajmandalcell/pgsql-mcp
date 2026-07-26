#!/usr/bin/env python3
"""Measure and enforce pgsql-mcp release budgets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from postgres_mcp.release_budgets import ReleaseBudgetLimits
from postgres_mcp.release_budgets import ReleaseMeasurements
from postgres_mcp.release_budgets import evaluate_release_budgets
from postgres_mcp.release_budgets import measure_cold_import
from postgres_mcp.release_budgets import measure_docker_image_bytes
from postgres_mcp.release_budgets import measure_wheel_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path, help="Built wheel artifact")
    parser.add_argument("--image", required=True, help="Built Docker image tag")
    parser.add_argument("--json-output", type=Path, help="Optional machine-readable report path")
    parser.add_argument("--repetitions", type=int, default=5, help="Independent cold-import process count")
    parser.add_argument("--core-process-ms", type=float, default=750.0)
    parser.add_argument("--core-rss-mib", type=float, default=64.0)
    parser.add_argument("--lite-process-ms", type=float, default=1_500.0)
    parser.add_argument("--lite-rss-mib", type=float, default=128.0)
    parser.add_argument("--wheel-mib", type=float, default=2.0)
    parser.add_argument("--image-mib", type=float, default=300.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    limits = ReleaseBudgetLimits(
        core_process_ms=args.core_process_ms,
        core_rss_mib=args.core_rss_mib,
        lite_process_ms=args.lite_process_ms,
        lite_rss_mib=args.lite_rss_mib,
        wheel_mib=args.wheel_mib,
        image_mib=args.image_mib,
    )
    measurements = ReleaseMeasurements(
        core_import=measure_cold_import("postgres_mcp", repetitions=args.repetitions),
        lite_import=measure_cold_import("postgres_mcp.lite_server", repetitions=args.repetitions),
        wheel_bytes=measure_wheel_bytes(args.wheel),
        image_bytes=measure_docker_image_bytes(args.image),
    )
    report = evaluate_release_budgets(measurements, limits)
    rendered = json.dumps(report.to_payload(), indent=2, sort_keys=True)
    print(rendered)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

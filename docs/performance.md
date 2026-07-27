# Performance release budgets

The release workflow measures startup, memory, package size, and container size.

## Measured profiles

The workflow measures the core package and the lite server in separate cold processes.

It records median process time, import time, and peak resident memory.

## Package budgets

The workflow checks the built wheel size.

It also checks the runtime container image size.

## Import boundaries

The core package must not import full-server modules eagerly.

The lite server must not import write, migration, maintenance, health, provider, extension, or LLM modules.

## Evidence

Each run uploads machine-readable JSON and command output.

A budget violation blocks the release.

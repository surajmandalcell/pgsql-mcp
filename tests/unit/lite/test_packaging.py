"""Packaging contract tests for the lightweight installation path."""

from __future__ import annotations

from pathlib import Path

import tomllib


def project_configuration() -> dict[str, object]:
    """Read the repository's single source of package metadata."""
    with Path("pyproject.toml").open("rb") as source:
        return tomllib.load(source)["project"]


def test_llm_stack_is_not_a_core_dependency() -> None:
    """A normal or lite install must not pull the optional LLM client graph."""
    project = project_configuration()
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)
    assert all(not str(dependency).startswith("instructor") for dependency in dependencies)


def test_llm_stack_is_available_as_an_explicit_extra() -> None:
    """Full users retain a documented opt-in path for LLM index analysis."""
    project = project_configuration()
    optional = project["optional-dependencies"]
    assert isinstance(optional, dict)
    assert optional["llm"] == ["instructor>=1.7.9"]


def test_both_console_entry_points_are_published() -> None:
    """The full and lite profiles must ship from one versioned distribution."""
    project = project_configuration()
    scripts = project["scripts"]
    assert isinstance(scripts, dict)
    assert scripts == {
        "pgsql-mcp": "postgres_mcp:main",
        "pgsql-mcp-lite": "postgres_mcp:lite_main",
    }

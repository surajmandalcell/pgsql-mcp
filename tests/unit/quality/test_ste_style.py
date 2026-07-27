"""Tests for the repository ASD-STE100 project-profile checker."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_checker() -> ModuleType:
    """Load the checker as a test module."""
    path = Path(__file__).parents[3] / "scripts" / "check_ste_docs.py"
    spec = importlib.util.spec_from_file_location("check_ste_docs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checker() -> ModuleType:
    """Return the loaded checker module."""
    return load_checker()


def test_markdown_lists_and_tables_are_checked(tmp_path: Path, checker: ModuleType) -> None:
    """Check list items and table cells as visible prose."""
    path = tmp_path / "guide.md"
    path.write_text(
        "# Guide\n\n"
        "- This sentence contains more than twenty five words because it repeats unnecessary words "
        "and continues with extra detail that an operator does not need during normal work today.\n\n"
        "| Control | Description |\n"
        "|---|---|\n"
        "| Mode | utilize a safe role |\n",
        encoding="utf-8",
    )

    errors = checker.check_surfaces(checker.markdown_surfaces(path))

    assert any("maximum is 25" in error for error in errors)
    assert any("prohibited phrase 'utilize'" in error for error in errors)


def test_code_fences_and_badges_are_ignored(tmp_path: Path, checker: ModuleType) -> None:
    """Ignore code and HTML badge lines."""
    path = tmp_path / "guide.md"
    path.write_text(
        "# Guide\n\n"
        "<img alt=\"badge\" src=\"https://example.com/badge.svg\">\n\n"
        "```python\n"
        "text = 'utilize this string in order to test code'\n"
        "```\n\n"
        "Use the safe role.\n",
        encoding="utf-8",
    )

    errors = checker.check_surfaces(checker.markdown_surfaces(path))

    assert errors == []


def test_python_public_descriptions_are_checked(tmp_path: Path, checker: ModuleType) -> None:
    """Check public Python descriptions and help text."""
    path = tmp_path / "tool.py"
    path.write_text(
        "from pydantic import Field\n"
        "value = Field(description='utilize the supplied value')\n",
        encoding="utf-8",
    )

    errors = checker.check_surfaces(checker.python_public_surfaces(path))

    assert any("prohibited phrase 'utilize'" in error for error in errors)


def test_workflow_names_are_checked(tmp_path: Path, checker: ModuleType) -> None:
    """Check workflow names and input descriptions."""
    path = tmp_path / "workflow.yml"
    path.write_text(
        "name: Build & test\n"
        "description: utilize the release input\n",
        encoding="utf-8",
    )

    errors = checker.check_surfaces(checker.workflow_surfaces(path))

    assert any("ampersand" in error for error in errors)
    assert any("prohibited phrase 'utilize'" in error for error in errors)

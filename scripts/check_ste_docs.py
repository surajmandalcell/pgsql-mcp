#!/usr/bin/env python3
"""Check repository documentation against the project ASD-STE100 profile."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAX_WORDS = 25
EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ste-transfer",
    ".venv",
    "build",
    "dist",
    "node_modules",
}
PROHIBITED = {
    "in order to": "use 'to'",
    "it should be noted": "state the fact directly",
    "please note": "state the fact directly",
    "utilize": "use 'use'",
    "utilized": "use 'used'",
    "utilizes": "use 'uses'",
}
PUBLIC_KEYWORDS = {"description", "help", "summary", "title"}
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
MARKDOWN_LIST_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True, slots=True)
class TextSurface:
    """One repository text surface that requires a style check."""

    path: Path
    line: int
    kind: str
    text: str


def is_excluded(path: Path) -> bool:
    """Return true when a path is outside the checked repository surfaces."""
    return any(part in EXCLUDED_DIRS for part in path.parts)


def clean_markdown(text: str) -> str:
    """Remove Markdown syntax that does not form visible prose."""
    cleaned = IMAGE_RE.sub(" ", text)
    cleaned = INLINE_CODE_RE.sub(" CODE ", cleaned)
    cleaned = LINK_RE.sub(r"\1", cleaned)
    cleaned = URL_RE.sub(" URL ", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = cleaned.replace("~~", "")
    return " ".join(cleaned.split())


def markdown_surfaces(path: Path) -> list[TextSurface]:
    """Return visible prose from headings, paragraphs, lists, quotes, and tables."""
    text = path.read_text(encoding="utf-8")
    result: list[TextSurface] = []
    in_fence = False
    in_html_comment = False

    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("<!--"):
            in_html_comment = not stripped.endswith("-->")
            continue
        if in_html_comment:
            if stripped.endswith("-->"):
                in_html_comment = False
            continue
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith("<") and stripped.endswith(">"):
            continue

        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading.endswith((".", "!", "?", ";", ":")):
                result.append(TextSurface(path, number, "heading-error", "Heading must not end with punctuation"))
            if heading:
                result.append(TextSurface(path, number, "heading", clean_markdown(heading)))
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            for cell in cells:
                if not cell or TABLE_SEPARATOR_RE.fullmatch(cell.replace(" ", "")):
                    continue
                result.append(TextSurface(path, number, "table", clean_markdown(cell)))
            continue

        content = stripped.lstrip(">").strip()
        content = MARKDOWN_LIST_RE.sub("", content)
        if content:
            result.append(TextSurface(path, number, "markdown", clean_markdown(content)))

    return result


def constant_string(node: ast.AST | None) -> str | None:
    """Return a static string value from one AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        values = [value.value for value in node.values if isinstance(value, ast.Constant) and isinstance(value.value, str)]
        return " ".join(values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = constant_string(node.left)
        right = constant_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def python_public_surfaces(path: Path) -> list[TextSurface]:
    """Return user-facing descriptions and command help from Python source."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [TextSurface(path, exc.lineno or 1, "python-error", f"Python parse failed: {exc.msg}")]

    result: list[TextSurface] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg not in PUBLIC_KEYWORDS:
                continue
            value = constant_string(keyword.value)
            if value:
                result.append(TextSurface(path, getattr(keyword.value, "lineno", node.lineno), "python", value))

    return result


def workflow_surfaces(path: Path) -> list[TextSurface]:
    """Return visible workflow names and input descriptions."""
    result: list[TextSurface] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith(("name:", "description:")):
            _, value = stripped.split(":", 1)
            value = value.strip().strip('"\'')
            if value:
                result.append(TextSurface(path, number, "workflow", value))
    return result


def project_metadata_surfaces(root: Path) -> list[TextSurface]:
    """Return visible package metadata from pyproject.toml."""
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    description = data.get("project", {}).get("description")
    if not isinstance(description, str) or not description:
        return []
    line = next(
        (number for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1) if raw.startswith("description =")),
        1,
    )
    return [TextSurface(path, line, "metadata", description)]


def repository_surfaces(root: Path) -> list[TextSurface]:
    """Collect all repository text surfaces in the project profile."""
    result: list[TextSurface] = []
    for path in sorted(root.rglob("*.md")):
        if not is_excluded(path):
            result.extend(markdown_surfaces(path))
    source_root = root / "src" / "postgres_mcp"
    if source_root.exists():
        for path in sorted(source_root.rglob("*.py")):
            if not is_excluded(path):
                result.extend(python_public_surfaces(path))
    workflow_root = root / ".github" / "workflows"
    if workflow_root.exists():
        for suffix in ("*.yml", "*.yaml"):
            for path in sorted(workflow_root.glob(suffix)):
                result.extend(workflow_surfaces(path))
    result.extend(project_metadata_surfaces(root))
    return result


def check_text(surface: TextSurface) -> list[str]:
    """Check one visible text surface against deterministic project rules."""
    if surface.kind.endswith("error"):
        return [f"{surface.path}:{surface.line}: {surface.text}"]
    text = " ".join(surface.text.split())
    if not text:
        return []

    errors: list[str] = []
    lowered = text.lower()
    if ";" in text:
        errors.append(f"{surface.path}:{surface.line}: semicolon in {surface.kind} prose")
    if "&" in text and not re.search(r"\bR&D\b", text):
        errors.append(f"{surface.path}:{surface.line}: ampersand in {surface.kind} prose; use 'and'")
    for phrase, replacement in PROHIBITED.items():
        if phrase in lowered:
            errors.append(f"{surface.path}:{surface.line}: prohibited phrase {phrase!r}; {replacement}")

    sentences = SENTENCE_RE.split(text)
    for sentence in sentences:
        words = WORD_RE.findall(sentence)
        if len(words) > MAX_WORDS:
            errors.append(
                f"{surface.path}:{surface.line}: {surface.kind} sentence has {len(words)} words; maximum is {MAX_WORDS}"
            )
    return errors


def check_surfaces(surfaces: Iterable[TextSurface]) -> list[str]:
    """Check all collected text surfaces."""
    errors: list[str] = []
    for surface in surfaces:
        errors.extend(check_text(surface))
    return errors


def main() -> int:
    """Run the repository ASD-STE100 project-profile check."""
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    surfaces = repository_surfaces(root)
    errors = check_surfaces(surfaces)
    if errors:
        print("\n".join(errors))
        return 1
    markdown_count = sum(1 for path in root.rglob("*.md") if not is_excluded(path))
    print(f"ASD-STE100 project profile passed for {markdown_count} Markdown files and {len(surfaces)} text surfaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

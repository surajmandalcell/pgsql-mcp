#!/usr/bin/env python3
"""Check repository Markdown for the project Simplified Technical English rules."""

from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_WORDS = 25
EXCLUDED_DIRS = {".git", ".venv", ".pytest_cache", ".ste-transfer", "node_modules"}
PROHIBITED = {
    "in order to": "use 'to'",
    "utilize": "use 'use'",
    "utilizes": "use 'uses'",
    "utilized": "use 'used'",
    "please note": "state the fact directly",
    "it should be noted": "state the fact directly",
}
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def markdown_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*.md"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        result.append(path)
    return sorted(result)


def prose_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    in_fence = False
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith("#"):
            if stripped.endswith((".", "!", "?", ";")):
                result.append((number, "STE heading must not end with punctuation"))
            continue
        if stripped.startswith(("-", "*", "+", ">", "|")):
            continue
        if re.match(r"^\d+\.\s", stripped):
            continue
        if stripped.startswith("<") and stripped.endswith(">"):
            continue
        cleaned = re.sub(r"`[^`]*`", " CODE ", stripped)
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        result.append((number, cleaned))
    return result


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    for number, line in prose_lines(text):
        if line.startswith("STE heading"):
            errors.append(f"{path}:{number}: {line}")
            continue
        lowered = line.lower()
        if ";" in line:
            errors.append(f"{path}:{number}: semicolon in prose")
        for phrase, replacement in PROHIBITED.items():
            if phrase in lowered:
                errors.append(f"{path}:{number}: prohibited phrase {phrase!r}; {replacement}")
        for sentence in SENTENCE_RE.split(line):
            words = WORD_RE.findall(sentence)
            if len(words) > MAX_WORDS:
                errors.append(f"{path}:{number}: descriptive sentence has {len(words)} words; maximum is {MAX_WORDS}")
    return errors


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    files = markdown_files(root)
    errors: list[str] = []
    for path in files:
        errors.extend(check_file(path))
    if errors:
        print("\n".join(errors))
        return 1
    print(f"ASD-STE100 style check passed for {len(files)} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

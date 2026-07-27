#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MAX_WORDS = 25
BANNED = {
    "and/or": "Use 'and' or 'or'.",
    "in order to": "Use 'to'.",
    "prior to": "Use 'before'.",
    "subsequent to": "Use 'after'.",
    "utilize": "Use 'use'.",
    "utilizes": "Use 'uses'.",
    "utilized": "Use 'used'.",
    "currently": "Use 'now' or remove the word.",
    "at this time": "Use 'now'.",
    "via": "Use 'through', 'with', or 'by'.",
    "in the event that": "Use 'if'.",
    "with respect to": "Use 'for' or 'about'.",
    "due to the fact that": "Use 'because'.",
    "for the purpose of": "Use 'to'.",
    "make use of": "Use 'use'.",
    "is able to": "Use 'can'.",
    "are able to": "Use 'can'.",
    "will be able to": "Use 'can'.",
    "as well as": "Use 'and'.",
    "etc.": "List the items or use a defined class.",
}
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`])")
WORD_RE = re.compile(r"\b[\w][\w'/-]*\b")
INLINE_CODE_RE = re.compile(r"`[^`]+`")
LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")


def prose_lines(path: Path):
    in_fence = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith("|") or stripped.startswith("<!--"):
            continue
        if stripped.startswith("#"):
            yield number, stripped.lstrip("#").strip(), "heading"
            continue
        line = re.sub(r"^[-*+]\s+", "", stripped)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = INLINE_CODE_RE.sub("TECHNICAL_TERM", line)
        line = LINK_TARGET_RE.sub("]", line)
        yield number, line, "prose"


def check(path: Path) -> list[str]:
    errors: list[str] = []
    for number, line, kind in prose_lines(path):
        lower = line.lower()
        for phrase, help_text in BANNED.items():
            pattern = r"(?<![A-Za-z0-9_])" + re.escape(phrase) + r"(?![A-Za-z0-9_])"
            if re.search(pattern, lower):
                errors.append(f"{path}:{number}: prohibited phrase {phrase!r}. {help_text}")
        if kind == "heading":
            if line.endswith((".", ":", ";")):
                errors.append(f"{path}:{number}: heading must not end with punctuation")
            continue
        for sentence in SENTENCE_RE.split(line):
            sentence = sentence.strip()
            if not sentence:
                continue
            count = len(WORD_RE.findall(sentence))
            if count > MAX_WORDS:
                errors.append(
                    f"{path}:{number}: sentence has {count} words; maximum is {MAX_WORDS}: "
                    f"{sentence[:140]}"
                )
            if ";" in sentence:
                errors.append(f"{path}:{number}: do not use a semicolon; use two sentences")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    files: list[Path] = []
    for item in args.paths:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.md") if ".venv" not in p.parts))
        elif path.suffix.lower() == ".md":
            files.append(path)
    errors: list[str] = []
    for path in sorted(set(files)):
        errors.extend(check(path))
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} ASD-STE100 style error(s)")
        return 1
    print(f"ASD-STE100 style check passed for {len(set(files))} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

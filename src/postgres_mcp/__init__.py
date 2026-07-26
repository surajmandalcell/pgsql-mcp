"""Public entry points for the observable full and reliability-focused lite servers."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from types import ModuleType


def _configure_runtime() -> None:
    """Configure process behavior shared by all console entry points."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _run(module_name: str) -> None:
    """Import one server lazily and run its asynchronous entry point."""
    _configure_runtime()
    module = importlib.import_module(module_name, __name__)
    asyncio.run(module.main())


def main() -> None:
    """Run the full pgsql-mcp server with privacy-preserving observability."""
    _run(".observed_server")


def lite_main() -> None:
    """Run the minimal, read-only pgsql-mcp-lite server."""
    _run(".lite_server")


def __getattr__(name: str) -> ModuleType:
    """Preserve historical module exports without eager advanced imports."""
    if name in {"server", "lite_server", "top_queries"}:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["lite_main", "main"]

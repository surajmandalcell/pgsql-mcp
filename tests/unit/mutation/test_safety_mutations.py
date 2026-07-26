"""Contracts for the deterministic safety mutation runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.mutation import safety_mutations as mutations


def completed(returncode: int, output: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["pytest"], returncode, output, "")


def test_catalog_names_are_unique_and_targets_are_exact() -> None:
    assert len({item.name for item in mutations.MUTATIONS}) == len(mutations.MUTATIONS)
    assert len(mutations.MUTATIONS) >= 10
    for mutation in mutations.MUTATIONS:
        source = (mutations.REPOSITORY_ROOT / mutation.path).read_text()
        assert source.count(mutation.old) == 1
        assert mutation.old != mutation.new
        assert mutation.tests


def test_pytest_runner_uses_fresh_cache_and_repository_source(tmp_path: Path) -> None:
    runner = Mock(return_value=completed(0))
    result = mutations.run_pytest(("tests/unit/runtime/test_runtime.py",), cache_dir=tmp_path, runner=runner)
    assert result.returncode == 0
    command = runner.call_args.args[0]
    assert command[:2] == [mutations.sys.executable, "-X"]
    assert str(tmp_path) in command[2]
    assert runner.call_args.kwargs["cwd"] == mutations.REPOSITORY_ROOT
    assert str(mutations.REPOSITORY_ROOT / "src") in runner.call_args.kwargs["env"]["PYTHONPATH"]


def test_exact_replacement_restores_original_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = True\n")
    monkeypatch.setattr(mutations, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(mutations, "run_pytest", lambda *_args, **_kwargs: completed(1, "failed as expected"))
    mutation = mutations.Mutation("example", "module.py", "True", "False", ("tests/test_example.py",))

    result = mutations.run_mutation(mutation)

    assert result.status == "killed"
    assert target.read_text() == "value = True\n"


def test_survivors_and_infrastructure_errors_are_distinguished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = True\n")
    monkeypatch.setattr(mutations, "REPOSITORY_ROOT", tmp_path)
    mutation = mutations.Mutation("example", "module.py", "True", "False", ("tests/test_example.py",))

    monkeypatch.setattr(mutations, "run_pytest", lambda *_args, **_kwargs: completed(0))
    assert mutations.run_mutation(mutation).status == "survived"
    monkeypatch.setattr(mutations, "run_pytest", lambda *_args, **_kwargs: completed(2))
    assert mutations.run_mutation(mutation).status == "infrastructure_error"


def test_gate_rejects_unknown_selection_and_failed_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="unknown mutations"):
        mutations.run_gate(selected={"missing"})

    monkeypatch.setattr(mutations, "run_pytest", lambda *_args, **_kwargs: completed(1, "baseline failure"))
    with pytest.raises(RuntimeError, match="baseline failed"):
        mutations.run_gate(selected=set())


def test_cli_parser_defaults_and_timeout_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parser = mutations.build_parser()
    args = parser.parse_args([])
    assert args.output == Path("mutation-results.json")
    assert args.timeout_seconds == 180

    monkeypatch.setattr(
        mutations,
        "build_parser",
        lambda: Mock(
            parse_args=lambda: Mock(
                list=False,
                timeout_seconds=0,
                mutations=None,
                output=tmp_path / "out.json",
            )
        ),
    )
    with pytest.raises(ValueError, match="positive"):
        mutations.main()

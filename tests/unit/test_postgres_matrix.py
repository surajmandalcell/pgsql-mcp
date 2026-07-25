"""Contracts for deterministic PostgreSQL compatibility test selection."""

from __future__ import annotations

import pytest

from utils import DEFAULT_POSTGRES_IMAGES
from utils import POSTGRES_IMAGE_ENV
from utils import SUPPORTED_POSTGRES_IMAGES
from utils import configured_postgres_images


def test_matrix_defaults_to_the_fast_pull_request_pair() -> None:
    assert configured_postgres_images({}) == DEFAULT_POSTGRES_IMAGES
    assert DEFAULT_POSTGRES_IMAGES == ("postgres:15", "postgres:16")


@pytest.mark.parametrize("image", SUPPORTED_POSTGRES_IMAGES)
def test_matrix_accepts_each_supported_major(image: str) -> None:
    assert configured_postgres_images({POSTGRES_IMAGE_ENV: image}) == (image,)


def test_matrix_trims_the_configured_image() -> None:
    assert configured_postgres_images({POSTGRES_IMAGE_ENV: "  postgres:18  "}) == ("postgres:18",)


def test_matrix_rejects_unadvertised_images() -> None:
    with pytest.raises(RuntimeError, match=POSTGRES_IMAGE_ENV):
        configured_postgres_images({POSTGRES_IMAGE_ENV: "postgres:13"})

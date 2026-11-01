"""Pytest wiring: make tests/synthetic.py importable from any test dir."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from synthetic import build_synthetic_source  # noqa: E402


@pytest.fixture()
def synthetic_source(tmp_path: Path) -> Path:
    return build_synthetic_source(tmp_path / "fake-kms")

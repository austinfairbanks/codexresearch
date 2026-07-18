from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_soleresearch_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from reading or writing the operator's persistent configuration."""
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "global-config"))

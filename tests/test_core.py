"""Unit tests for core.config.Config (merged from test_config.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from m4aforge.core import Config, ConfigError


def test_load_defaults_when_file_missing(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "no_such_config.json")
    assert config.dry_run is False
    assert config.overwrite_existing is False


def test_load_reads_valid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"folder": str(tmp_path), "dry_run": True}))

    config = Config.load(config_path)

    assert config.folder == tmp_path
    assert config.dry_run is True


def test_load_raises_on_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not valid json")

    with pytest.raises(ConfigError):
        Config.load(config_path)


def test_load_ignores_unknown_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"some_future_field": "x"}))

    config = Config.load(config_path)

    assert config.dry_run is False


def test_apply_cli_overrides(tmp_path: Path) -> None:
    config = Config()
    updated = config.apply_cli_overrides(folder=str(tmp_path), dry_run=True, verbose=True)

    assert updated.folder == tmp_path
    assert updated.dry_run is True
    assert updated.verbose is True


def test_validate_raises_on_bad_timeout() -> None:
    config = Config(request_timeout_seconds=0)
    with pytest.raises(ConfigError):
        config.validate()
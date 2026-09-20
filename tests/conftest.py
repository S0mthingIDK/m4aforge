"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def sample_m4a(tmp_path: Path) -> Path:
    """Return a real, minimal, valid .m4a file for tests that need to
    open it with mutagen. Skips if ffmpeg isn't available."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available to generate a sample .m4a fixture")

    path = tmp_path / "sample.m4a"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
            "-t", "1", "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path
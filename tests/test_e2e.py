"""End-to-end test: run the real preprocessor against a committed synthetic fixture.

Unlike the unit tests, this exercises the actual ffmpeg pipeline (remux/reencode,
subtitle extraction, thumbnail, manifest). It runs the tool as a subprocess because
config is read from env vars at import time. It is skipped if ffmpeg/ffprobe are not
on PATH, or if the fixture has not been generated (see example/synthetic/generate.sh).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent.parent / "example" / "synthetic" / "clock.mkv"

pytestmark = [
    pytest.mark.skipif(
        not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
        reason="ffmpeg/ffprobe not on PATH",
    ),
    pytest.mark.skipif(not FIXTURE.exists(), reason="synthetic fixture not generated"),
]


def test_processes_synthetic_mkv(tmp_path: Path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    shutil.copy(FIXTURE, input_dir / "clock.mkv")

    env = {
        "OTT_FILEUPLOAD_BASE_URL": "http://example.test/media",
        "OTT_INPUT_DIR": str(input_dir),
        "OTT_OUTPUT_DIR": str(output_dir),
        "PATH": os.environ.get("PATH", ""),
    }
    cp = subprocess.run(
        [sys.executable, "-m", "ott_customjson_preprocessor"],
        capture_output=True, text=True, env=env,
    )
    assert cp.returncode == 0, f"tool failed:\n{cp.stdout}\n{cp.stderr}"

    expected = [
        "clock.mkv.mp4",
        "clock.mkv.json",
        "clock.mkv.thumb.jpg",
        "clock.mkv.eng.ass",
        "clock.mkv.eng.vtt",
    ]
    for name in expected:
        assert (output_dir / name).is_file(), f"missing output {name}\n{cp.stdout}"

    # manifest is valid JSON
    manifest = json.loads((output_dir / "clock.mkv.json").read_text())
    assert isinstance(manifest, dict)

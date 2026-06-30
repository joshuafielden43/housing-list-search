"""Contract tests for run_daily.sh cron wrapper."""

from __future__ import annotations

import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_daily.sh"


def test_run_daily_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert ".venv/bin/activate" in text
    assert "flock" in text
    assert "LOG_DIR" in text
    assert "tee -a" in text


def test_run_daily_exits_when_venv_missing(tmp_path):
    script_copy = tmp_path / "run_daily.sh"
    shutil.copy(SCRIPT, script_copy)
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IEXEC)

    result = subprocess.run(
        [str(script_copy)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert ".venv" in combined


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock not available")
def test_run_daily_flock_blocks_second_instance(tmp_path):
    script_copy = tmp_path / "run_daily.sh"
    shutil.copy(SCRIPT, script_copy)
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IEXEC)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "activate").write_text("# stub\n", encoding="utf-8")

    # Replace python invocation with a slow no-op so the lock stays held.
    text = script_copy.read_text(encoding="utf-8")
    text = text.replace("python main.py --run", "sleep 2")
    script_copy.write_text(text, encoding="utf-8")

    first = subprocess.Popen([str(script_copy)], cwd=tmp_path)
    time.sleep(0.3)
    second = subprocess.run(
        [str(script_copy)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    first.wait(timeout=10)

    assert second.returncode == 1
    assert "already running" in (second.stdout + second.stderr).lower()
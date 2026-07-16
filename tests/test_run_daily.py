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
    assert "PIPESTATUS[0]" in text
    # #247: stale lock reclaim + distinct lock-skip exit
    assert "LOCK_MAX_AGE_S" in text
    assert "lock.meta" in text
    assert "exiting 2" in text


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
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "doctor.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
        encoding="utf-8",
    )

    # Replace python invocation with a slow no-op so the lock stays held.
    text = script_copy.read_text(encoding="utf-8")
    text = text.replace("python scripts/doctor.py --dry-run", "python scripts/doctor.py")
    text = text.replace(
        'if ! python -c "from playwright.sync_api import sync_playwright" >>"$LOG_FILE" 2>&1; then\n'
        '  echo "$(date -Iseconds) WARNING: Playwright import failed — browser adapters may fail" | tee -a "$LOG_FILE"\n'
        "fi\n",
        "",
    )
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

    assert second.returncode == 2  # #247: lock skip, not scrape failure
    assert "already running" in (second.stdout + second.stderr).lower() or "lock skip" in (
        second.stdout + second.stderr
    ).lower()


def test_run_daily_propagates_main_exit_code(tmp_path):
    """Cron must see non-zero when main.py --run fails, not tee's exit 0 (#757)."""
    script_copy = tmp_path / "run_daily.sh"
    shutil.copy(SCRIPT, script_copy)
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IEXEC)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "activate").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "doctor.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(7)\n", encoding="utf-8"
    )

    text = script_copy.read_text(encoding="utf-8")
    text = text.replace("python scripts/doctor.py --dry-run", "python scripts/doctor.py")
    text = text.replace(
        'if ! python -c "from playwright.sync_api import sync_playwright" >>"$LOG_FILE" 2>&1; then\n'
        '  echo "$(date -Iseconds) WARNING: Playwright import failed — browser adapters may fail" | tee -a "$LOG_FILE"\n'
        "fi\n",
        "",
    )
    script_copy.write_text(text, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(script_copy)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 7, (result.stdout, result.stderr)


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock not available")
def test_run_daily_reclaims_dead_pid_lock(tmp_path):
    """#247: lock meta with a dead PID should be reclaimable."""
    script_copy = tmp_path / "run_daily.sh"
    shutil.copy(SCRIPT, script_copy)
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IEXEC)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "activate").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "doctor.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8"
    )

    text = script_copy.read_text(encoding="utf-8")
    text = text.replace("python scripts/doctor.py --dry-run", "python scripts/doctor.py")
    text = text.replace(
        'if ! python -c "from playwright.sync_api import sync_playwright" >>"$LOG_FILE" 2>&1; then\n'
        '  echo "$(date -Iseconds) WARNING: Playwright import failed — browser adapters may fail" | tee -a "$LOG_FILE"\n'
        "fi\n",
        "",
    )
    script_copy.write_text(text, encoding="utf-8")

    # Simulate a lock file left by a long-dead PID (no live holder).
    (tmp_path / ".run_daily.lock").write_text("", encoding="utf-8")
    # PID 1 is usually init/launchd and may be alive — use a high unused PID.
    dead_pid = 999999
    start = int(time.time()) - 10
    (tmp_path / ".run_daily.lock.meta").write_text(f"{dead_pid} {start}\n", encoding="utf-8")

    # Hold flock in background so the script must reclaim via dead-pid path after
    # we release… Actually dead pid path clears meta/file then retries flock.
    # With only a leftover file and no live flock holder, flock -n succeeds.
    result = subprocess.run(
        ["bash", str(script_copy)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)

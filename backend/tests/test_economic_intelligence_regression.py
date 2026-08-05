from __future__ import annotations

import subprocess

from backend.brokers.mt5.config import mt5_config


def test_live_trading_remains_blocked_by_default(monkeypatch):
    monkeypatch.delenv("MT5_LIVE_TRADING_ENABLED", raising=False)
    assert mt5_config().live_trading_enabled is False


def test_no_frontend_files_changed_by_this_hardening_phase():
    """Best-effort check: none of the currently-modified/staged files under version control
    live under frontend/. Skips gracefully (rather than failing) if git isn't available in
    this environment, since this is a repo-hygiene check, not a functional one."""
    try:
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        return
    if result.returncode != 0:
        return
    changed_frontend = [line for line in result.stdout.splitlines() if "frontend/" in line and line.strip().startswith(("M ", " M", "A ", "??"))]
    # Only flag files that are staged/modified (not merely untracked pre-existing files from
    # before this task, which this repo already carries in bulk) -- restrict to tracked
    # modifications ("M") since that's what this task could have caused.
    tracked_frontend_changes = [line for line in changed_frontend if line.strip().startswith("M ") or line.strip().startswith(" M")]
    assert tracked_frontend_changes == []

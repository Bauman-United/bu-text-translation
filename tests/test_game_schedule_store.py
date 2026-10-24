"""
On 2026-10-24 the server disk filled up and saving a match URL truncated
game_schedules.json to 0 bytes: write_text() opens the file for writing (which
empties it) before failing with ENOSPC. Every scheduled game was lost.
"""

import errno
from datetime import datetime
from pathlib import Path

import pytest

import utils.game_schedule as gs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "_get_store_path", lambda: tmp_path / "schedules.json")


def _full_disk_write_text(self, *args, **kwargs):
    """Mimic ENOSPC: the target is opened (and so truncated), then the write fails."""
    open(self, "w").close()
    raise OSError(errno.ENOSPC, "No space left on device")


def test_failed_save_keeps_existing_schedules(monkeypatch):
    schedule = gs.add_game_schedule(datetime(2026, 10, 24, 20, 30, tzinfo=gs.SERBIA_TZ))

    with monkeypatch.context() as m, pytest.raises(OSError):
        m.setattr(Path, "write_text", _full_disk_write_text)
        gs.update_game_parse_mode(schedule.id, "site", "https://example.test/match/1")

    assert [s.id for s in gs.list_game_schedules()] == [schedule.id]

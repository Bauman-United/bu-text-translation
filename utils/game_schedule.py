"""
Persistence and window calculations for scheduled games.

We store multiple datetimes (one per scheduled game) and define an active monitoring
window for each:
  window_start = game_datetime - 10 minutes
  window_end   = game_datetime + 2 hours
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

SERBIA_TZ = ZoneInfo("Europe/Belgrade")
UTC = timezone.utc


@dataclass(frozen=True)
class GameSchedule:
    id: str
    # Stored in UTC with offset, e.g. "2026-03-18T20:30:00+00:00"
    game_datetime_utc_iso: str

    @property
    def game_datetime_utc(self) -> datetime:
        dt = datetime.fromisoformat(self.game_datetime_utc_iso)
        if dt.tzinfo is None:
            # Backward-compat safety: assume stored value was UTC naive.
            dt = dt.replace(tzinfo=UTC)
        return dt

    @property
    def game_datetime(self) -> datetime:
        """
        Serbian-local datetime for display.
        """
        return self.game_datetime_utc.astimezone(SERBIA_TZ)


def _get_store_path() -> Path:
    # .../bu-text-translation/utils/game_schedule.py -> repo root is one level up
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = repo_root / "data"
    return data_dir / "game_schedules.json"


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_raw() -> List[dict]:
    path = _get_store_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("items", []) if isinstance(raw, dict) else []
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _save_raw(items: List[dict]) -> None:
    path = _get_store_path()
    _ensure_parent_dir(path)
    payload = {"version": 1, "items": items}
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def list_game_schedules() -> List[GameSchedule]:
    items = _load_raw()
    schedules: List[GameSchedule] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        schedule_id = it.get("id")
        utc_iso = it.get("game_datetime_utc_iso")
        # Backward-compat: older records used game_datetime_iso as Serbian-local naive.
        legacy_iso = it.get("game_datetime_iso")
        if not schedule_id:
            continue

        if utc_iso:
            schedules.append(
                GameSchedule(id=str(schedule_id), game_datetime_utc_iso=str(utc_iso))
            )
        elif legacy_iso:
            # Interpret legacy time as Serbian local time (naive).
            legacy_dt = datetime.fromisoformat(str(legacy_iso))
            if legacy_dt.tzinfo is None:
                legacy_dt = legacy_dt.replace(tzinfo=SERBIA_TZ)
            legacy_utc = legacy_dt.astimezone(UTC)
            schedules.append(
                GameSchedule(id=str(schedule_id), game_datetime_utc_iso=legacy_utc.isoformat())
            )
    # newest first (game datetime)
    schedules.sort(key=lambda s: s.game_datetime_utc, reverse=True)
    return schedules


def add_game_schedule(game_datetime: datetime) -> GameSchedule:
    """
    Add a schedule.
    
    `game_datetime` is expected to be Serbian-local time (timezone-aware preferred).
    We'll store it internally as UTC.
    """
    dt_local = game_datetime
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=SERBIA_TZ)
    dt_utc = dt_local.astimezone(UTC)

    schedules = list_game_schedules()
    new_id = uuid.uuid4().hex
    item = {"id": new_id, "game_datetime_utc_iso": dt_utc.isoformat()}
    # Save append to raw store (keeps existing items)
    current_items = _load_raw()
    current_items.append(item)
    _save_raw(current_items)
    return GameSchedule(id=new_id, game_datetime_utc_iso=item["game_datetime_utc_iso"])


def delete_game_schedule(schedule_id: str) -> bool:
    current_items = _load_raw()
    new_items = [it for it in current_items if isinstance(it, dict) and str(it.get("id")) != str(schedule_id)]
    if len(new_items) == len(current_items):
        return False
    _save_raw(new_items)
    return True


def get_monitor_windows(now: datetime) -> List[Tuple[datetime, datetime]]:
    """
    Return list of active windows (UTC) that contain `now`.
    
    `now` should be timezone-aware in UTC.
    """
    active: List[Tuple[datetime, datetime]] = []
    for s in list_game_schedules():
        start = s.game_datetime_utc - timedelta(minutes=10)
        end = s.game_datetime_utc + timedelta(hours=2)
        if start <= now <= end:
            active.append((start, end))
    # If overlaps, order doesn't really matter, but keep deterministic
    active.sort(key=lambda w: w[0])
    return active


def is_time_in_any_window(moment: datetime) -> bool:
    return len(get_monitor_windows(moment)) > 0


def get_next_window_end(moment: datetime) -> Optional[datetime]:
    """
    If we are inside any window, returns the farthest end datetime among active windows.
    Otherwise returns None.
    """
    active = get_monitor_windows(moment)
    if not active:
        return None
    return max(end for _, end in active)


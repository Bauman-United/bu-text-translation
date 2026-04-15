"""
Persistence and window calculations for scheduled games.

We store multiple datetimes (one per scheduled game) and define an active monitoring
window for each:
  window_start = game_datetime - 30 minutes
  window_end   = game_datetime + 2 hours

Each game has a parse_mode ("comments" for VK live comments, "site" for match page
scraping) and optionally a match_url + seen_scores for the site mode.
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
    game_datetime_utc_iso: str
    parse_mode: str = "comments"  # "comments" | "site"
    match_url: Optional[str] = None
    seen_scores: tuple = ()  # score strings already posted (for site mode)

    @property
    def game_datetime_utc(self) -> datetime:
        dt = datetime.fromisoformat(self.game_datetime_utc_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt

    @property
    def game_datetime(self) -> datetime:
        return self.game_datetime_utc.astimezone(SERBIA_TZ)

    @property
    def parse_mode_label(self) -> str:
        if self.parse_mode == "site":
            return "🌐 Сайт"
        return "📺 VK комментарии"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _get_store_path() -> Path:
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


def _item_to_schedule(it: dict) -> Optional[GameSchedule]:
    """Convert a raw JSON dict into a GameSchedule (or None)."""
    if not isinstance(it, dict):
        return None
    schedule_id = it.get("id")
    utc_iso = it.get("game_datetime_utc_iso")
    legacy_iso = it.get("game_datetime_iso")
    if not schedule_id:
        return None

    resolved_utc_iso: Optional[str] = None
    if utc_iso:
        resolved_utc_iso = str(utc_iso)
    elif legacy_iso:
        legacy_dt = datetime.fromisoformat(str(legacy_iso))
        if legacy_dt.tzinfo is None:
            legacy_dt = legacy_dt.replace(tzinfo=SERBIA_TZ)
        resolved_utc_iso = legacy_dt.astimezone(UTC).isoformat()

    if not resolved_utc_iso:
        return None

    return GameSchedule(
        id=str(schedule_id),
        game_datetime_utc_iso=resolved_utc_iso,
        parse_mode=str(it.get("parse_mode", "comments")),
        match_url=it.get("match_url"),
        seen_scores=tuple(it.get("seen_scores", [])),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_game_schedules() -> List[GameSchedule]:
    items = _load_raw()
    schedules: List[GameSchedule] = []
    for it in items:
        s = _item_to_schedule(it)
        if s:
            schedules.append(s)
    schedules.sort(key=lambda s: s.game_datetime_utc, reverse=True)
    return schedules


def get_game_schedule(schedule_id: str) -> Optional[GameSchedule]:
    for s in list_game_schedules():
        if s.id == str(schedule_id):
            return s
    return None


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

    new_id = uuid.uuid4().hex
    item = {
        "id": new_id,
        "game_datetime_utc_iso": dt_utc.isoformat(),
        "parse_mode": "comments",
    }
    current_items = _load_raw()
    current_items.append(item)
    _save_raw(current_items)
    return GameSchedule(id=new_id, game_datetime_utc_iso=item["game_datetime_utc_iso"])


def update_game_parse_mode(
    schedule_id: str,
    parse_mode: str,
    match_url: Optional[str] = None,
) -> bool:
    items = _load_raw()
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id")) != str(schedule_id):
            continue
        item["parse_mode"] = parse_mode
        if match_url is not None:
            item["match_url"] = match_url
        if parse_mode == "comments":
            item.pop("match_url", None)
            item.pop("seen_scores", None)
        _save_raw(items)
        return True
    return False


def update_game_seen_scores(schedule_id: str, seen_scores: List[str]) -> bool:
    items = _load_raw()
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id")) != str(schedule_id):
            continue
        item["seen_scores"] = seen_scores
        _save_raw(items)
        return True
    return False


def delete_game_schedule(schedule_id: str) -> bool:
    current_items = _load_raw()
    new_items = [it for it in current_items if isinstance(it, dict) and str(it.get("id")) != str(schedule_id)]
    if len(new_items) == len(current_items):
        return False
    _save_raw(new_items)
    return True


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------

WINDOW_BEFORE = timedelta(minutes=30)
WINDOW_AFTER = timedelta(hours=2)


def get_monitor_windows(
    now: datetime,
    parse_mode: Optional[str] = None,
) -> List[Tuple[datetime, datetime]]:
    """
    Return list of active windows (UTC) that contain `now`.

    `now` should be timezone-aware in UTC.
    If `parse_mode` is given, only schedules with that mode are considered.
    """
    active: List[Tuple[datetime, datetime]] = []
    for s in list_game_schedules():
        if parse_mode is not None and s.parse_mode != parse_mode:
            continue
        start = s.game_datetime_utc - WINDOW_BEFORE
        end = s.game_datetime_utc + WINDOW_AFTER
        if start <= now <= end:
            active.append((start, end))
    active.sort(key=lambda w: w[0])
    return active


def is_time_in_any_window(
    moment: datetime,
    parse_mode: Optional[str] = None,
) -> bool:
    return len(get_monitor_windows(moment, parse_mode=parse_mode)) > 0


def get_schedules_in_window(
    moment: datetime,
    parse_mode: Optional[str] = None,
) -> List[GameSchedule]:
    """Return schedules whose monitoring window contains `moment`."""
    result: List[GameSchedule] = []
    for s in list_game_schedules():
        if parse_mode is not None and s.parse_mode != parse_mode:
            continue
        start = s.game_datetime_utc - WINDOW_BEFORE
        end = s.game_datetime_utc + WINDOW_AFTER
        if start <= moment <= end:
            result.append(s)
    return result


def get_next_window_end(moment: datetime) -> Optional[datetime]:
    """
    If we are inside any window, returns the farthest end datetime among active windows.
    Otherwise returns None.
    """
    active = get_monitor_windows(moment)
    if not active:
        return None
    return max(end for _, end in active)

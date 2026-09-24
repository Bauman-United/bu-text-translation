"""Stream discovery via video.get and the site monitor's early close."""

from datetime import datetime, timedelta, timezone

import pytest

from api import vk_client as vc
from api.vk_client import VKClient
from monitors import group_stream_monitor as gsm
from monitors import match_site_monitor as msm
from utils.match_parser import MatchParseResult

GROUP_ID = "211231295"


def _video(video_id, live_status, live=1):
    return {
        "id": video_id,
        "owner_id": -int(GROUP_ID),
        "title": "Live: Bauman United",
        "live": live,
        "live_status": live_status,
        "is_mobile_live": True,
        "type": "video",
    }


# ---------------------------------------------------------------------------
# is_live_stream
# ---------------------------------------------------------------------------

@pytest.fixture
def vk(monkeypatch):
    monkeypatch.setattr(vc, "load_tokens", lambda: None)
    return VKClient("static-token")


def test_finished_stream_is_not_live_even_with_live_flag(vk):
    # This is what video.get returns for past broadcasts.
    assert vk.is_live_stream(_video(1, "finished")) is False


def test_started_stream_is_live(vk):
    assert vk.is_live_stream(_video(1, "started")) is True


# ---------------------------------------------------------------------------
# VKGroupStreamMonitor.check_for_new_streams
# ---------------------------------------------------------------------------

class FakeVK:
    def __init__(self, videos):
        self.videos = videos
        self.calls = 0

    async def get_group_recent_videos(self, group_id, count=10):
        self.calls += 1
        return self.videos

    is_live_stream = VKClient.is_live_stream
    get_video_id = VKClient.get_video_id
    get_video_url = VKClient.get_video_url


@pytest.fixture
def monitor(monkeypatch):
    import handlers.telegram_commands as tc

    active = {}
    monkeypatch.setattr(tc, "get_active_translations", lambda: active)
    monkeypatch.setattr(gsm, "get_schedules_in_window", lambda now: [])
    monkeypatch.setattr(gsm, "is_time_in_any_window", lambda now, parse_mode=None: True)

    m = gsm.VKGroupStreamMonitor.__new__(gsm.VKGroupStreamMonitor)
    m.group_id = GROUP_ID
    m.seen_streams = set()
    m._last_known_modes = {}
    m.started = []

    async def handle_new_stream(video):
        m.started.append(video["id"])

    m.handle_new_stream = handle_new_stream
    return m


async def test_discovers_live_stream_from_video_list(monitor):
    monitor.vk_client = FakeVK(
        [_video(456239381, "started"), _video(456239380, "finished")]
    )

    assert await monitor.check_for_new_streams() is True
    assert monitor.started == [456239381]

    # Same stream on the next poll is not started twice.
    await monitor.check_for_new_streams()
    assert monitor.started == [456239381]


async def test_ignores_finished_streams(monitor):
    monitor.vk_client = FakeVK([_video(456239380, "finished"), _video(456239379, "finished")])

    await monitor.check_for_new_streams()
    assert monitor.started == []
    assert monitor.vk_client.calls == 1


# ---------------------------------------------------------------------------
# MatchSiteMonitor: close at match end when the protocol stayed empty
# ---------------------------------------------------------------------------

class FakeClock:
    def __init__(self, start):
        self.now = start

    def datetime_cls(self):
        clock = self

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now

        return _DT


def _site_monitor(monkeypatch, game_time, event_counts):
    """Build a site monitor whose page returns `event_counts` poll by poll."""
    clock = FakeClock(game_time - timedelta(minutes=5))
    monkeypatch.setattr(msm, "datetime", clock.datetime_cls())

    async def fake_sleep(seconds):
        clock.now += timedelta(seconds=seconds)

    monkeypatch.setattr(msm.asyncio, "sleep", fake_sleep)

    counts = iter(event_counts)

    def fake_parse(html):
        return MatchParseResult(
            goals=[], home_team="A", away_team="Bauman United",
            our_team_position=2, timeline_present=True, event_count=next(counts, 0),
        )

    monkeypatch.setattr(msm, "parse_match_page", fake_parse)
    monkeypatch.setattr(msm, "fetch_match_html", lambda url: "")

    m = msm.MatchSiteMonitor.__new__(msm.MatchSiteMonitor)
    m.schedule_id = "s1"
    m.match_url = "https://example/match/1"
    m.game_datetime_utc = game_time
    m.is_active = True
    m.seen_scores = set()
    m.protocol_updated = False
    m.notifications = []

    async def notify(text):
        m.notifications.append(text)

    m._send_user_notification = notify
    m._cleanup = lambda: None
    m.polls = 0
    original = m.check_for_new_goals

    async def counting_check():
        m.polls += 1
        await original()

    m.check_for_new_goals = counting_check
    return m, clock


async def test_site_monitor_closes_at_match_end_without_protocol_updates(monkeypatch):
    game_time = datetime(2026, 9, 20, 18, 30, tzinfo=timezone.utc)
    m, clock = _site_monitor(monkeypatch, game_time, event_counts=[])

    await m.start_monitoring()

    assert game_time + timedelta(hours=1) < clock.now <= game_time + timedelta(hours=1, minutes=2)
    assert m.polls <= 67
    assert any("закрыт по окончании" in n for n in m.notifications)


async def test_site_monitor_keeps_extra_time_when_protocol_was_updated(monkeypatch):
    game_time = datetime(2026, 9, 20, 18, 30, tzinfo=timezone.utc)
    # First event appears 20 polls in (~15 min into the match).
    m, clock = _site_monitor(monkeypatch, game_time, event_counts=[0] * 20 + [1] * 200)

    await m.start_monitoring()

    assert clock.now > game_time + timedelta(hours=2)
    assert not any("закрыт по окончании" in n for n in m.notifications)

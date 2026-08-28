"""Tests for the shared goal announcer."""

import pytest

from services.goal_announcer import (
    GoalAnnouncer,
    ScoreTracker,
    get_celebration_video_path,
    get_channel_tracker,
    reset_channel_tracker,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeBot:
    def __init__(self):
        self.messages = []
        self.videos = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text))

    async def send_video(self, chat_id, video, caption=None, parse_mode=None):
        self.videos.append((chat_id, caption))


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()


class FakeGPT:
    """Stands in for GPTCommentaryService."""

    def __init__(self, reply="ГОООЛ! Шева открывает счёт! 1-0", available=True):
        self.reply = reply
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    async def generate_commentary(self, history, score, is_our_goal=True, scorer_surname=None):
        self.calls.append((list(history), score, is_our_goal, scorer_surname))
        return self.reply


def make_announcer(gpt=None, tracker=None, channel="-100999"):
    app = FakeApp()
    return GoalAnnouncer(app, channel, user_id=1, gpt_service=gpt, tracker=tracker), app


# ---------------------------------------------------------------------------
# ScoreTracker
# ---------------------------------------------------------------------------

def test_tracker_detects_our_goal():
    t = ScoreTracker()
    assert t.register(1, 0) == "ours"
    assert t.score == (1, 0)


def test_tracker_detects_opponent_goal():
    t = ScoreTracker()
    assert t.register(0, 1) == "theirs"


def test_tracker_ignores_repeat_of_same_score():
    """A second source reporting an already-announced score must stay silent."""
    t = ScoreTracker()
    assert t.register(1, 0) == "ours"
    assert t.register(1, 0) is None


def test_tracker_ignores_score_going_backwards():
    t = ScoreTracker()
    t.register(3, 1)
    assert t.register(2, 1) is None
    assert t.score == (3, 1)


def test_tracker_can_start_from_a_given_score():
    t = ScoreTracker(3, 2)
    assert t.score == (3, 2)
    assert t.register(3, 2) is None
    assert t.register(4, 2) == "ours"


def test_tracker_history_is_capped():
    t = ScoreTracker()
    for i in range(15):
        t.remember(f"msg {i}")
    assert len(t.history) == 10
    assert t.history[-1] == "msg 14"


def test_channel_trackers_are_shared_and_resettable():
    reset_channel_tracker("chan-a")
    a1 = get_channel_tracker("chan-a")
    a2 = get_channel_tracker("chan-a")
    assert a1 is a2, "both sources must see the same tracker for one channel"
    assert get_channel_tracker("chan-b") is not a1
    reset_channel_tracker("chan-a", 2, 1)
    assert get_channel_tracker("chan-a").score == (2, 1)


# ---------------------------------------------------------------------------
# Celebration videos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("surname,expected", [
    ("богомолов", "celebrations/богомолов.mp4"),
    ("БАГИЧ", "celebrations/богомолов.mp4"),
    ("Шевченко", "celebrations/шевченко.mp4"),
    ("шева", "celebrations/шевченко.mp4"),
    ("панфёров", "celebrations/панферов.mp4"),
    ("Писарев", "celebrations/писарев.mp4"),
    ("Заночуев", "celebrations/заночуев.mp4"),
    ("Гангелин", "celebrations/другие.mp4"),
])
def test_celebration_video_lookup_is_case_insensitive(surname, expected):
    assert get_celebration_video_path(surname) == expected


# ---------------------------------------------------------------------------
# GoalAnnouncer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_our_goal_posts_video_with_gpt_caption():
    gpt = FakeGPT()
    ann, app = make_announcer(gpt=gpt, tracker=ScoreTracker())
    msg = await ann.announce(1, 0, scorer_surname="Шевченко")
    assert msg == "ГОООЛ! Шева открывает счёт! 1-0"
    assert app.bot.videos == [("-100999", "ГОООЛ! Шева открывает счёт! 1-0")]
    assert app.bot.messages == []
    assert gpt.calls[0][1] == "1-0"
    assert gpt.calls[0][2] is True


@pytest.mark.asyncio
async def test_opponent_goal_posts_plain_message():
    ann, app = make_announcer(gpt=FakeGPT(reply="Недолго музыка играла... 1-1"), tracker=ScoreTracker(1, 0))
    msg = await ann.announce(1, 1)
    assert msg == "Недолго музыка играла... 1-1"
    assert app.bot.videos == []
    assert app.bot.messages == [("-100999", "Недолго музыка играла... 1-1")]


@pytest.mark.asyncio
async def test_falls_back_to_template_when_gpt_unavailable():
    ann, app = make_announcer(gpt=None, tracker=ScoreTracker())
    msg = await ann.announce(2, 1, scorer_surname="писарев")
    assert msg == "⚽ Забиваем! Гол забил Писарев. Счет: 2-1"


@pytest.mark.asyncio
async def test_falls_back_to_template_when_gpt_returns_nothing():
    ann, app = make_announcer(gpt=FakeGPT(reply=None), tracker=ScoreTracker())
    assert await ann.announce(1, 0) == "⚽ Забиваем! Счет: 1-0"


@pytest.mark.asyncio
async def test_prefers_full_name_in_fallback_when_available():
    ann, _ = make_announcer(gpt=None, tracker=ScoreTracker())
    msg = await ann.announce(1, 0, scorer_name="Шевченко Егор", scorer_surname="Шевченко")
    assert msg == "⚽ Забиваем! Гол забил Шевченко Егор. Счет: 1-0"


@pytest.mark.asyncio
async def test_duplicate_score_from_second_source_is_skipped():
    """The whole point of a shared tracker: no double posts for one goal."""
    tracker = ScoreTracker()
    ann, app = make_announcer(gpt=None, tracker=tracker)
    assert await ann.announce(1, 0, scorer_surname="Шевченко") is not None
    assert await ann.announce(1, 0, scorer_surname="Шевченко") is None
    assert len(app.bot.videos) == 1


@pytest.mark.asyncio
async def test_history_feeds_gpt_context():
    gpt = FakeGPT()
    ann, _ = make_announcer(gpt=gpt, tracker=ScoreTracker())
    await ann.announce(1, 0, scorer_surname="Шевченко")
    await ann.announce(2, 0, scorer_surname="Писарев")
    assert gpt.calls[0][0] == []
    assert gpt.calls[1][0] == ["ГОООЛ! Шева открывает счёт! 1-0"]


@pytest.mark.asyncio
async def test_missing_celebration_file_falls_back_to_text():
    ann, app = make_announcer(gpt=None, tracker=ScoreTracker())
    ann._video_root = "no/such/dir"
    msg = await ann.announce(1, 0, scorer_surname="Шевченко")
    assert app.bot.videos == []
    assert app.bot.messages == [("-100999", msg)]

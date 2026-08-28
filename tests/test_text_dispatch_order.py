"""
The catch-all text handler serves three flows at once. Order matters:
"21:30" is a valid score pattern, so a game time being entered must reach the
schedule and never the channel.
"""

import pytest

import handlers.manual_translation as mt
import handlers.telegram_commands as tc
import utils.game_schedule as gs
import utils.manual_translation_store as store
from services.goal_announcer import get_channel_tracker, reset_channel_tracker

CHANNEL = "-100test"
OWNER = 4242


class FakeBot:
    def __init__(self):
        self.posts = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.posts.append((chat_id, text))

    async def send_video(self, chat_id, video, caption=None, parse_mode=None):
        self.posts.append((chat_id, caption))


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.effective_user = type("U", (), {"id": OWNER})()


class FakeContext:
    def __init__(self, app, user_data=None):
        self.application = app
        self.args = []
        self.user_data = user_data if user_data is not None else {}


class FakeConfig:
    MY_ID = str(OWNER)
    TELEGRAM_CHANNEL_ID = CHANNEL

    @property
    def is_openai_configured(self):
        return False


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_get_store_path", lambda: tmp_path / "manual.json")
    monkeypatch.setattr(gs, "_get_store_path", lambda: tmp_path / "schedules.json")
    monkeypatch.setattr(mt, "Config", FakeConfig)
    reset_channel_tracker(CHANNEL)
    yield
    store.clear_state()


async def test_game_time_wins_over_active_translation():
    """The bug this ordering prevents: 21:30 posted to the channel as 21-30."""
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(""), FakeContext(app))
    assert store.is_active()

    # User is midway through /set_game: weekday chosen, time expected.
    context = FakeContext(app, user_data={tc.GAME_DAY_PENDING_KEY: 2})
    update = FakeUpdate("21:30")
    await tc.game_time_input_handler(update, context)

    assert app.bot.posts == [], "game time must never reach the channel"
    assert len(gs.list_game_schedules()) == 1
    assert get_channel_tracker(CHANNEL).score == (0, 0)


async def test_score_is_handled_when_no_pending_flow():
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(""), FakeContext(app))

    update = FakeUpdate("1-0 Шевченко")
    await tc.game_time_input_handler(update, FakeContext(app))

    assert app.bot.posts == [(CHANNEL, "⚽ Забиваем! Гол забил Шевченко. Счет: 1-0")]
    assert gs.list_game_schedules() == []


async def test_plain_text_without_any_flow_does_nothing():
    app = FakeApp()
    update = FakeUpdate("привет")
    await tc.game_time_input_handler(update, FakeContext(app))
    assert app.bot.posts == []
    assert update.message.replies == []

"""Tests for the manual Telegram translation mode."""

import pytest

import handlers.manual_translation as mt
import utils.manual_translation_store as store
from services.goal_announcer import get_channel_tracker, reset_channel_tracker

CHANNEL = "-100test"
OWNER = 4242
STRANGER = 9999


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

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

    async def reply_text(self, text, parse_mode=None):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text="", user_id=OWNER):
        self.message = FakeMessage(text)
        self.effective_user = type("U", (), {"id": user_id})()


class FakeContext:
    def __init__(self, app, args=None):
        self.application = app
        self.args = args or []
        self.user_data = {}


class FakeConfig:
    MY_ID = str(OWNER)
    TELEGRAM_CHANNEL_ID = CHANNEL

    @property
    def is_openai_configured(self):
        return False


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep every test off the real data/ directory."""
    monkeypatch.setattr(store, "_get_store_path", lambda: tmp_path / "manual_translation.json")
    monkeypatch.setattr(mt, "Config", FakeConfig)
    reset_channel_tracker(CHANNEL)
    yield
    store.clear_state()


# ---------------------------------------------------------------------------
# /start_translation
# ---------------------------------------------------------------------------

async def test_start_defaults_to_zero_zero():
    app = FakeApp()
    update = FakeUpdate()
    await mt.start_translation_command(update, FakeContext(app))
    assert store.is_active()
    assert get_channel_tracker(CHANNEL).score == (0, 0)
    assert "0-0" in update.message.replies[0]


async def test_start_accepts_an_initial_score():
    """Joining mid-match must not announce the score you started from."""
    app = FakeApp()
    update = FakeUpdate()
    await mt.start_translation_command(update, FakeContext(app, args=["3-2"]))
    assert get_channel_tracker(CHANNEL).score == (3, 2)


async def test_start_rejects_a_malformed_score():
    app = FakeApp()
    update = FakeUpdate()
    await mt.start_translation_command(update, FakeContext(app, args=["хрень"]))
    assert not store.is_active()
    assert "Не понял счёт" in update.message.replies[0]


async def test_only_the_owner_can_start():
    app = FakeApp()
    update = FakeUpdate(user_id=STRANGER)
    await mt.start_translation_command(update, FakeContext(app))
    assert not store.is_active()
    assert "только владельцу" in update.message.replies[0]


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

async def test_score_message_is_posted_to_channel():
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(), FakeContext(app))

    update = FakeUpdate("1-0 Шевченко")
    consumed = await mt.handle_translation_message(update, FakeContext(app))

    assert consumed is True
    assert app.bot.posts == [(CHANNEL, "⚽ Забиваем! Гол забил Шевченко. Счет: 1-0")]
    assert get_channel_tracker(CHANNEL).score == (1, 0)


async def test_non_score_message_is_ignored():
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(), FakeContext(app))

    update = FakeUpdate("пойду поем")
    consumed = await mt.handle_translation_message(update, FakeContext(app))

    assert consumed is False
    assert app.bot.posts == []


async def test_messages_ignored_when_translation_not_started():
    app = FakeApp()
    consumed = await mt.handle_translation_message(FakeUpdate("1-0"), FakeContext(app))
    assert consumed is False
    assert app.bot.posts == []


async def test_stranger_messages_are_ignored_even_while_active():
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(), FakeContext(app))
    consumed = await mt.handle_translation_message(
        FakeUpdate("5-0", user_id=STRANGER), FakeContext(app)
    )
    assert consumed is False
    assert app.bot.posts == []


async def test_repeated_score_is_not_posted_twice():
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(), FakeContext(app))
    await mt.handle_translation_message(FakeUpdate("1-0 Шевченко"), FakeContext(app))

    update = FakeUpdate("1-0 Шевченко")
    await mt.handle_translation_message(update, FakeContext(app))

    assert len(app.bot.posts) == 1
    assert "уже объявлен" in update.message.replies[0]


async def test_goal_reported_by_another_source_first_is_not_repeated():
    """The shared channel tracker is what stops VK and manual double-posting."""
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(), FakeContext(app))

    # Simulate the VK monitor announcing 1-0 through the shared tracker.
    get_channel_tracker(CHANNEL).register(1, 0)

    await mt.handle_translation_message(FakeUpdate("1-0 Шевченко"), FakeContext(app))
    assert app.bot.posts == []


# ---------------------------------------------------------------------------
# /end_translation and persistence
# ---------------------------------------------------------------------------

async def test_end_reports_final_score_and_stops_listening():
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(), FakeContext(app))
    await mt.handle_translation_message(FakeUpdate("2-1"), FakeContext(app))

    update = FakeUpdate()
    await mt.end_translation_command(update, FakeContext(app))

    assert not store.is_active()
    assert "2-1" in update.message.replies[0]
    assert await mt.handle_translation_message(FakeUpdate("3-1"), FakeContext(app)) is False


async def test_end_without_a_running_translation():
    app = FakeApp()
    update = FakeUpdate()
    await mt.end_translation_command(update, FakeContext(app))
    assert "и так не запущена" in update.message.replies[0]


async def test_session_survives_a_restart():
    app = FakeApp()
    await mt.start_translation_command(FakeUpdate(), FakeContext(app, args=["3-2"]))
    await mt.handle_translation_message(FakeUpdate("4-2 Писарев"), FakeContext(app))

    # Restart: in-memory trackers are gone, the store is not.
    reset_channel_tracker(CHANNEL, 0, 0)
    mt.restore_session(app)

    tracker = get_channel_tracker(CHANNEL)
    assert tracker.score == (4, 2)
    assert tracker.history == ["⚽ Забиваем! Гол забил Писарев. Счет: 4-2"]

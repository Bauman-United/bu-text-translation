"""When should the bot nag about the VK token?"""

import time
from datetime import datetime, timedelta, timezone

from services.token_watchdog import WarningKind, evaluate_token
from utils.game_schedule import GameSchedule
from utils.vk_token_store import VKTokens

# Anchored to the real clock: token_for() builds expiry from time.time(), and
# a frozen NOW would drift past it within a day.
NOW = datetime.now(timezone.utc)


def game_in(hours, mode="comments"):
    return GameSchedule(
        id=f"g{hours}",
        game_datetime_utc_iso=(NOW + timedelta(hours=hours)).isoformat(),
        parse_mode=mode,
    )


def token_for(hours):
    """A token that dies `hours` from NOW."""
    return VKTokens("tok", expires_at=time.time() + hours * 3600)


def test_no_token_at_all_is_reported():
    w = evaluate_token(None, [game_in(5)], NOW)
    assert w.kind is WarningKind.MISSING


def test_expired_token_is_reported():
    w = evaluate_token(VKTokens("tok", expires_at=time.time() - 60), [game_in(5)], NOW)
    assert w.kind is WarningKind.EXPIRED


def test_token_dying_before_kickoff_is_reported():
    """The whole point: find out the day before, not at kickoff."""
    w = evaluate_token(token_for(3), [game_in(10)], NOW)
    assert w.kind is WarningKind.DIES_BEFORE_GAME
    assert "g10" in w.key


def test_token_outliving_the_game_is_fine():
    assert evaluate_token(token_for(30), [game_in(10)], NOW).kind is WarningKind.NONE


def test_healthy_token_with_no_games_is_fine():
    assert evaluate_token(token_for(20), [], NOW).kind is WarningKind.NONE


def test_missing_token_without_games_stays_quiet():
    """Nothing is scheduled, so there is nothing to be late for."""
    assert evaluate_token(None, [], NOW).kind is WarningKind.NONE


def test_past_games_are_ignored():
    assert evaluate_token(token_for(3), [game_in(-5)], NOW).kind is WarningKind.NONE


def test_only_the_nearest_upcoming_game_matters():
    w = evaluate_token(token_for(3), [game_in(50), game_in(8), game_in(20)], NOW)
    assert "g8" in w.key


def test_site_mode_games_do_not_need_a_vk_token():
    """Site parsing never touches VK, so a dead token is not a problem for it."""
    assert evaluate_token(token_for(3), [game_in(10, mode="site")], NOW).kind is WarningKind.NONE


def test_token_without_known_expiry_is_not_nagged_about():
    assert evaluate_token(VKTokens("tok", expires_at=0), [game_in(10)], NOW).kind is WarningKind.NONE


def test_refreshable_token_is_never_nagged_about():
    """A code-flow token set renews itself — even expired it is fine."""
    expired_but_refreshable = VKTokens(
        "tok", refresh_token="ref", device_id="dev", expires_at=time.time() - 60
    )
    assert evaluate_token(expired_but_refreshable, [game_in(10)], NOW).kind is WarningKind.NONE


def test_warning_key_is_stable_so_it_is_sent_once():
    a = evaluate_token(token_for(3), [game_in(10)], NOW)
    b = evaluate_token(token_for(3), [game_in(10)], NOW + timedelta(minutes=30))
    assert a.key == b.key

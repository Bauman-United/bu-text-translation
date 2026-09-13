"""VK flood control (error 9) must pause polling instead of hammering VK."""

import asyncio
import time

import pytest
import vk_api

from api import vk_client as vc
from api.vk_client import VKClient, VKFloodControl


class _FakeApi:
    """Stands in for vk_api's method proxy: `api.wall.get(**params)`."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    @property
    def wall(self):
        return self

    def get(self, **params):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _flood_error():
    return vk_api.exceptions.ApiError(
        None, "wall.get", {}, None, {"error_code": 9, "error_msg": "Flood control"}
    )


@pytest.fixture
def client(monkeypatch):
    VKClient._flood_until = 0.0
    VKClient._flood_backoff = 0.0
    VKClient._flood_reported = False
    monkeypatch.setattr(vc, "load_tokens", lambda: None)

    notifications = []

    async def notifier(service, request_info, code, message):
        notifications.append((code, message))

    c = VKClient("static-token", error_notifier=notifier)

    async def no_wait():
        return None

    monkeypatch.setattr(c.rate_limiter, "wait_if_needed", no_wait)
    monkeypatch.setattr(c.rate_limiter, "mark_call_complete", no_wait)
    c.notifications = notifications
    return c


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_flood_control_pauses_calls_and_notifies_once(client, monkeypatch):
    fake = _FakeApi([_flood_error(), {"items": []}])
    monkeypatch.setattr(client, "_rebuild_session", lambda token: setattr(client, "vk_api", fake))

    with pytest.raises(VKFloodControl):
        _run(client._call("wall.get", "wall.get(test)"))
    assert fake.calls == 1
    assert len(client.notifications) == 1
    assert VKClient._flood_until > time.time()

    # Within the cooldown: no network call, no second notification.
    with pytest.raises(VKFloodControl):
        _run(client._call("wall.get", "wall.get(test)"))
    assert fake.calls == 1
    assert len(client.notifications) == 1


def test_flood_backoff_doubles_and_resets_on_success(client, monkeypatch):
    fake = _FakeApi([_flood_error(), _flood_error(), {"items": []}])
    monkeypatch.setattr(client, "_rebuild_session", lambda token: setattr(client, "vk_api", fake))

    with pytest.raises(VKFloodControl):
        _run(client._call("wall.get", "wall.get(test)"))
    assert VKClient._flood_backoff == vc.FLOOD_BACKOFF_INITIAL

    VKClient._flood_until = 0.0  # pretend the cooldown elapsed
    with pytest.raises(VKFloodControl):
        _run(client._call("wall.get", "wall.get(test)"))
    assert VKClient._flood_backoff == vc.FLOOD_BACKOFF_INITIAL * 2

    VKClient._flood_until = 0.0
    assert _run(client._call("wall.get", "wall.get(test)")) == {"items": []}
    assert VKClient._flood_backoff == 0.0
    assert VKClient._flood_reported is False
    assert fake.calls == 3

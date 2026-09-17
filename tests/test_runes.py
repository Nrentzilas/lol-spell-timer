"""The rune lookup: its status handling, and the retry schedule.

The real thing waits minutes between attempts. These drive it with a schedule
of milliseconds and a fake Riot API, and call the worker synchronously so
there is nothing to wait on.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runes
from runes import COSMIC_INSIGHT_ID, ERROR, FORBIDDEN, NOT_FOUND, OK, RuneLookup

PLAYERS = [{"name": "Someone", "tag": "EUW"}]
FAST = (0.001, 0.001)          # two retries, near-instant


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeRiot:
    """Answers account and spectator calls, and counts what was asked."""

    def __init__(self, spectator, puuid="puuid-1"):
        # `spectator` is a list of responses, one per attempt.
        self.spectator = list(spectator)
        self.puuid = puuid
        self.account_calls = 0
        self.spectator_calls = 0

    def get(self, url, **kwargs):
        if "/riot/account/" in url:
            self.account_calls += 1
            if self.puuid is None:
                return FakeResponse(404)
            return FakeResponse(200, {"puuid": self.puuid})
        if "/spectator/" in url:
            self.spectator_calls += 1
            i = min(self.spectator_calls - 1, len(self.spectator) - 1)
            return FakeResponse(*self.spectator[i])
        raise AssertionError(f"unexpected request to {url}")


def active_game(perks=(COSMIC_INSIGHT_ID,), champion_id=103):
    return (200, {"participants": [
        {"championId": champion_id, "perks": {"perkIds": list(perks)}}]})


@pytest.fixture
def riot(monkeypatch):
    def install(fake):
        monkeypatch.setattr(runes.requests, "get", fake.get)
        monkeypatch.setattr(runes.ChampionIndex, "get",
                            lambda self: {103: "Ahri", 64: "LeeSin"})
        return fake
    return install


def run(lookup, players=PLAYERS):
    """Drive the worker on this thread, the way start() would on its own."""
    lookup._busy.acquire()
    lookup._run(players)
    return lookup.poll()


@pytest.fixture
def lookup():
    return RuneLookup(api_key="RGAPI-test", region="euw", retry_delays=FAST)


# -- a lookup that works -------------------------------------------------

def test_a_found_game_reports_who_has_cosmic_insight(lookup, riot):
    riot(FakeRiot([active_game()]))
    results = run(lookup)
    assert len(results) == 1
    status, found, final = results[0]
    assert (status, final) == (OK, True)
    assert found == {"Ahri": True}


def test_a_participant_without_the_rune_is_reported_as_false(lookup, riot):
    riot(FakeRiot([active_game(perks=(8210, 8234))]))
    status, found, _ = run(lookup)[0]
    assert status == OK
    assert found == {"Ahri": False}


def test_success_is_not_retried(lookup, riot):
    fake = riot(FakeRiot([active_game()]))
    run(lookup)
    assert fake.spectator_calls == 1


# -- the retry schedule --------------------------------------------------

def test_a_game_the_spectator_endpoint_has_not_seen_yet_is_retried(lookup, riot):
    """The common case: the lookup runs before the game is visible."""
    fake = riot(FakeRiot([(404, {}), (404, {}), active_game()]))
    results = run(lookup)
    assert [(s, f) for s, _, f in results] == [
        (NOT_FOUND, False),     # keep waiting
        (NOT_FOUND, False),     # keep waiting
        (OK, True),             # got there
    ]
    assert fake.spectator_calls == 3


def test_giving_up_is_reported_as_final(lookup, riot):
    riot(FakeRiot([(404, {})]))
    results = run(lookup)
    assert len(results) == 1 + len(FAST)
    assert [f for _, _, f in results] == [False, False, True]
    assert all(s == NOT_FOUND for s, _, _ in results)


def test_a_transient_error_is_retried_too(lookup, riot):
    riot(FakeRiot([(500, {}), active_game()]))
    statuses = [s for s, _, _ in run(lookup)]
    assert statuses == [ERROR, OK]


def test_a_rejected_key_is_not_retried(lookup, riot):
    """A 403 will not fix itself, and burning the schedule on it is rude."""
    fake = riot(FakeRiot([(403, {})]))
    results = run(lookup)
    assert results == [(FORBIDDEN, {}, True)]
    assert fake.spectator_calls == 1


def test_an_unknown_region_is_not_retried(riot):
    riot(FakeRiot([active_game()]))
    lookup = RuneLookup(api_key="RGAPI-test", region="nowhere",
                        retry_delays=FAST)
    results = run(lookup)
    assert [s for s, _, _ in results] == [runes.NO_REGION]


def test_the_puuid_is_looked_up_once_and_reused(lookup, riot):
    """Retries should repeat the spectator call, not the whole chain."""
    fake = riot(FakeRiot([(404, {}), (404, {}), active_game()]))
    run(lookup)
    assert fake.account_calls == 1
    assert fake.spectator_calls == 3


def test_a_player_the_account_endpoint_does_not_know_yields_not_found(lookup, riot):
    riot(FakeRiot([active_game()], puuid=None))
    assert all(s == NOT_FOUND for s, _, _ in run(lookup))


# -- abandoning ----------------------------------------------------------

def test_stop_before_the_first_attempt_does_nothing_at_all(lookup, riot):
    fake = riot(FakeRiot([active_game()]))
    lookup.stop()
    assert run(lookup) == []
    assert fake.spectator_calls == 0


def test_stop_ends_the_schedule_early(lookup, riot):
    fake = riot(FakeRiot([(404, {})]))
    original = lookup._lookup

    def once_then_stop(players):
        lookup.stop()
        return original(players)

    lookup._lookup = once_then_stop
    results = run(lookup)
    assert len(results) == 1
    assert fake.spectator_calls == 1


def test_the_lock_is_released_even_when_everything_fails(lookup, riot):
    riot(FakeRiot([(500, {})]))
    run(lookup)
    # Released, so a later match can start a fresh schedule.
    assert lookup._busy.acquire(blocking=False) is True


def test_a_disabled_lookup_never_starts(riot):
    fake = riot(FakeRiot([active_game()]))
    lookup = RuneLookup(api_key="", region="euw")
    assert lookup.enabled is False
    lookup.start(PLAYERS)
    assert lookup.poll() == []
    assert fake.spectator_calls == 0

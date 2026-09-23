"""The update check: version comparison and reading GitHub's answer."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import updates
from settings import Settings


@pytest.mark.parametrize("text, expected", [
    ("1.4.2", (1, 4, 2)),
    ("v1.4.2", (1, 4, 2)),
    ("V2.0", (2, 0, 0)),
    (" v3 ", (3, 0, 0)),
    ("v1.5.0-beta", None),
    ("latest", None),
    ("", None),
    (None, None),
])
def test_parse_version(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize("latest, current, expected", [
    ("v1.4.3", "1.4.2", True),
    ("v1.10.0", "1.9.9", True),    # numeric, not string, comparison
    ("v2.0.0", "1.99.99", True),
    ("v1.4.2", "1.4.2", False),
    ("v1.4.1", "1.4.2", False),    # a local build ahead of the last release
    ("nonsense", "1.4.2", False),
    ("v1.5.0", "dev", False),
])
def test_is_newer(latest, current, expected):
    assert updates.is_newer(latest, current) is expected


def release(**overrides):
    data = {"tag_name": "v1.5.0",
            "html_url": "https://github.com/Nrentzilas/lol-spell-timer/releases/tag/v1.5.0",
            "draft": False, "prerelease": False}
    data.update(overrides)
    return data


def test_a_release_is_read():
    assert updates.UpdateChecker.parse_release(release()) == {
        "version": "1.5.0",
        "url": "https://github.com/Nrentzilas/lol-spell-timer/releases/tag/v1.5.0",
    }


@pytest.mark.parametrize("overrides", [
    {"draft": True}, {"prerelease": True}, {"tag_name": "nightly"}, {"tag_name": None},
])
def test_releases_worth_ignoring(overrides):
    assert updates.UpdateChecker.parse_release(release(**overrides)) is None


def test_only_a_github_link_is_ever_opened():
    got = updates.UpdateChecker.parse_release(release(html_url="https://evil.example/x"))
    assert got["url"] == updates.RELEASES_PAGE


def test_garbage_is_not_a_release():
    assert updates.UpdateChecker.parse_release(["not", "a", "dict"]) is None


class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def run_check(monkeypatch, response, current="1.4.2"):
    def fake_get(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(updates.requests, "get", fake_get)
    checker = updates.UpdateChecker(current=current)
    checker._run()
    return checker.poll()


def test_a_newer_release_is_reported(monkeypatch):
    got = run_check(monkeypatch, FakeResponse(200, release()))
    assert [r["version"] for r in got] == ["1.5.0"]


def test_being_up_to_date_says_nothing(monkeypatch):
    assert run_check(monkeypatch, FakeResponse(200, release()), current="1.5.0") == []


@pytest.mark.parametrize("response", [
    ConnectionError("offline"),
    FakeResponse(403, {"message": "rate limited"}),
    FakeResponse(404, {}),
    FakeResponse(200, ValueError("not json")),
])
def test_a_failed_check_is_silent(monkeypatch, response):
    assert run_check(monkeypatch, response) == []


def test_a_disabled_checker_never_asks(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not have been called")
    monkeypatch.setattr(updates.requests, "get", boom)
    checker = updates.UpdateChecker(enabled=False)
    checker.start()
    assert checker._thread is None


def test_update_settings_roundtrip(tmp_path):
    path = str(tmp_path / "config.json")
    s = Settings.load(path)
    assert s.check_updates is True and s.update_notified == ""
    s.check_updates = False
    s.update_notified = "1.5.0"
    s.save()
    again = Settings.load(path)
    assert again.check_updates is False
    assert again.update_notified == "1.5.0"

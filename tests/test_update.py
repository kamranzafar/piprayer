# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Telling when there's a new version, and updating by itself."""
import io
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pipray import __version__, update
from pipray.cli import main
from pipray.config import Config, ConfigError
from pipray.schedule import prayer_times
from pipray.state import State
from pipray.update import (Release, UpdateState, Updater, apply, available, fetch_latest, how_to_update, is_newer,
                           parse_version, quiet_time, update_file)
from pipray.web import status_html
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")
NEWER = Release("99.0", "v99.0", "https://github.com/kamranzafar/piprayer/releases/tag/v99.0")
DAY = datetime(2026, 11, 12, tzinfo=MELBOURNE)


def at(hour, minute=0, day=0):
    return DAY + timedelta(days=day, hours=hour, minutes=minute)


def times(config, day=0):
    return dict(prayer_times(config, (DAY + timedelta(days=day)).date(), ["fajr", "isha"]))


def save(path, **fields):
    UpdateState(**fields).save(update_file(path))


# ---------------------------------------------------------------- versions

@pytest.mark.parametrize("text, version", [("v1.2", (1, 2)), ("1.10", (1, 10)), ("v2", (2,)), ("1.2-beta", None),
                                           ("", None), (None, None)])
def test_parse_version(text, version):
    assert parse_version(text) == version


def test_is_newer():
    assert is_newer("1.10", "1.9")
    assert not is_newer("1.0", "1.0")
    assert not is_newer("0.9", "1.0")
    assert not is_newer("nonsense", "1.0")


# ---------------------------------------------------------------- asking GitHub

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_fetch_latest(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(json.dumps({"tag_name": "v1.2", "html_url": "https://example.org/v1.2",
                                        "body": "Fixes\n<!-- needs-setup -->"}).encode())

    monkeypatch.setattr(update.urllib.request, "urlopen", urlopen)
    release = fetch_latest()
    assert release == Release("1.2", "v1.2", "https://example.org/v1.2", needs_setup=True)

    request, timeout = requests[0]
    assert request.full_url == "https://api.github.com/repos/kamranzafar/piprayer/releases/latest"
    assert timeout == update.FETCH_TIMEOUT
    # only says it is PiPrayer, and which version
    assert dict(request.header_items()) == {"User-agent": "PiPrayer/" + __version__,
                                            "Accept": "application/vnd.github+json"}


def test_fetch_latest_rejects_odd_tags(monkeypatch):
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda request, timeout: FakeResponse(b'{"tag_name": "nightly"}'))
    with pytest.raises(ValueError):
        fetch_latest()


# ---------------------------------------------------------------- the daily check

@pytest.fixture
def config(write_config):
    def make(extra=""):
        path = write_config(MELBOURNE_CONFIG + extra)
        return path, Config(path)
    return make


class Fetch:
    def __init__(self, release=NEWER, error=None):
        self.release = release
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.release


def test_checks_once_a_day_and_keeps_the_answer(config, caplog):
    path, cfg = config()
    fetch = Fetch()
    updater = Updater(path, run=lambda work: work(), fetch=fetch)
    caplog.set_level(logging.INFO, logger="piprayer")

    updater.tick(cfg, at(10))
    updater.tick(cfg, at(20))
    assert fetch.calls == 1
    assert available(path, cfg) == NEWER
    assert caplog.text.count("PiPrayer 99.0 is available") == 1

    updater.tick(cfg, at(10, day=1))
    assert fetch.calls == 2
    assert caplog.text.count("PiPrayer 99.0 is available") == 1  # said once


def test_failure_is_logged_at_debug_and_tried_again_tomorrow(config, caplog):
    path, cfg = config()
    fetch = Fetch(error=OSError("Network is unreachable"))
    updater = Updater(path, run=lambda work: work(), fetch=fetch)
    caplog.set_level(logging.DEBUG, logger="piprayer")

    updater.tick(cfg, at(10))
    updater.tick(cfg, at(11))
    assert fetch.calls == 1
    assert [r.levelname for r in caplog.records] == ["DEBUG"]
    assert "Network is unreachable" in caplog.text
    assert available(path, cfg) is None

    updater.tick(cfg, at(10, day=1))
    assert fetch.calls == 2


def test_update_check_off_asks_nothing_and_shows_nothing(config):
    path, cfg = config("update-check = off\n")
    save(path, checked=at(9).timestamp(), latest=NEWER)
    fetch = Fetch()
    Updater(path, run=lambda work: work(), fetch=fetch).tick(cfg, at(10))
    assert fetch.calls == 0
    assert available(path, cfg) is None


def test_the_same_or_an_older_version_isnt_available(config):
    path, cfg = config()
    save(path, checked=1, latest=Release(__version__, "v" + __version__, "u"))
    assert available(path, cfg) is None


def test_options_are_checked(write_config):
    with pytest.raises(ConfigError, match="Invalid auto-update: sometimes, use on or off"):
        Config(write_config(MELBOURNE_CONFIG + "auto-update = sometimes\n"))
    cfg = Config(write_config(MELBOURNE_CONFIG))
    assert (cfg.update_check, cfg.auto_update) == (True, False)


# ---------------------------------------------------------------- updating by itself

def test_quiet_time_is_from_an_hour_after_isha_until_fajr(config):
    path, cfg = config()
    isha, fajr = times(cfg)["isha"], times(cfg, 1)["fajr"]
    assert not quiet_time(cfg, isha + timedelta(minutes=59), fajr)
    assert quiet_time(cfg, isha + timedelta(minutes=61), fajr)
    assert quiet_time(cfg, fajr - timedelta(minutes=30), fajr)
    assert not quiet_time(cfg, fajr - timedelta(minutes=9), fajr)  # too close to the azaan
    assert not quiet_time(cfg, at(12), None)  # midday
    assert not quiet_time(cfg, fajr + timedelta(minutes=5), None)  # after Fajr


class Apply:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, release):
        self.calls.append(release)
        return self.error


@pytest.fixture
def auto(config):
    """An updater with auto-update on, that has already found a newer version."""
    def make(release=NEWER, error=None, extra=""):
        path, cfg = config("auto-update = on\n" + extra)
        save(path, checked=at(8).timestamp(), latest=release)
        applied = Apply(error)
        return path, cfg, Updater(path, run=lambda work: work(), fetch=Fetch(release), update=applied), applied
    return make


def test_updates_at_a_quiet_time_once(auto):
    path, cfg, updater, applied = auto()
    isha, fajr = times(cfg)["isha"], times(cfg, 1)["fajr"]

    updater.tick(cfg, at(12), isha)
    updater.tick(cfg, isha + timedelta(minutes=30), fajr)
    assert applied.calls == []

    updater.tick(cfg, isha + timedelta(minutes=70), fajr)
    assert applied.calls == [NEWER]
    updater.tick(cfg, isha + timedelta(minutes=71), fajr)
    assert len(applied.calls) == 1


def test_failed_update_is_tried_again_the_next_night_and_said(auto):
    path, cfg, updater, applied = auto(error="PiPrayer's folder has changes of its own")
    isha, fajr = times(cfg)["isha"], times(cfg, 1)["fajr"]
    updater.tick(cfg, isha + timedelta(minutes=70), fajr)
    updater.tick(cfg, isha + timedelta(minutes=90), fajr)
    assert len(applied.calls) == 1
    assert update.describe(path, cfg).startswith(
        "PiPrayer 99.0 is available: PiPrayer couldn't update itself, as PiPrayer's folder has changes of its own.")

    state = UpdateState.load(update_file(path))
    state.checked = (isha + timedelta(days=1)).timestamp()  # as if checked again the next day
    state.save(update_file(path))
    updater.tick(cfg, isha + timedelta(days=1, minutes=70), fajr + timedelta(days=1))
    assert len(applied.calls) == 2


def test_doesnt_update_when_auto_update_is_off(config):
    path, cfg = config()
    save(path, checked=at(8).timestamp(), latest=NEWER)
    applied = Apply()
    isha, fajr = times(cfg)["isha"], times(cfg, 1)["fajr"]
    Updater(path, update=applied).tick(cfg, isha + timedelta(minutes=70), fajr)
    assert applied.calls == []


def test_release_that_needs_setup_isnt_applied(auto):
    release = Release("99.0", "v99.0", "u", needs_setup=True)
    path, cfg, updater, applied = auto(release)
    isha, fajr = times(cfg)["isha"], times(cfg, 1)["fajr"]
    updater.tick(cfg, isha + timedelta(minutes=70), fajr)
    assert applied.calls == []
    assert update.describe(path, cfg) == \
        "PiPrayer 99.0 is available: update with git pull, then ./setup-piprayer.sh. See u"


def test_install_from_home_assistant_doesnt_wait_for_night_but_for_the_azaan(auto):
    path, cfg, updater, applied = auto()
    update.request_install(path)
    updater.tick(cfg, at(12), at(12, 5))  # 5 minutes before an azaan
    assert applied.calls == []
    updater.tick(cfg, at(12, 20), at(13, 5))
    assert applied.calls == [NEWER]
    assert UpdateState.load(update_file(path)).install is False


def test_service_updates_itself_overnight(write_config, fake_clock, monkeypatch):
    applied = []
    monkeypatch.setattr(update, "fetch_latest", lambda: NEWER)
    monkeypatch.setattr(update, "apply", lambda release: applied.append(clock_.local_now()))
    clock_ = fake_clock(datetime(2026, 11, 12, 12, 0), MELBOURNE)
    clock_.play_duration = timedelta(seconds=10)
    path = write_config(MELBOURNE_CONFIG + "auto-update = on\n")
    clock_.run_service(path, hours=20)

    cfg = Config(path)
    assert len(applied) == 1
    isha, fajr = times(cfg)["isha"], times(cfg, 1)["fajr"]
    assert isha + timedelta(hours=1) <= applied[0] < fajr - timedelta(minutes=10)


def test_a_broken_update_check_doesnt_stop_the_azaan(write_config, fake_clock, monkeypatch):
    def broken(*args):
        raise RuntimeError("bug")

    monkeypatch.setattr(Updater, "tick", broken)
    clock_ = fake_clock(datetime(2026, 11, 12, 0, 0), MELBOURNE)
    clock_.run_service(write_config(), hours=26)
    assert len(clock_.played) >= 5


# ---------------------------------------------------------------- git

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", "-c", "init.defaultBranch=main",
                    "-c", "advice.detachedHead=false"] + list(args), cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A clone of a repository with releases v1.0 and v99.0, set as PiPrayer's folder."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q")
    (origin / "piprayer.py").write_text("1.0\n")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "1.0")
    git(origin, "tag", "v1.0")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    (origin / "piprayer.py").write_text("99.0\n")
    git(origin, "commit", "-qam", "99.0")
    git(origin, "tag", "v99.0")
    monkeypatch.setattr(update, "SCRIPT_DIR", str(clone))
    return clone


@needs_git
def test_updates_a_clean_clone_on_main(clone):
    (clone / ".piprayer").write_text("ignored, as git doesn't know it")
    assert apply(NEWER) is None
    assert (clone / "piprayer.py").read_text() == "99.0\n"


@needs_git
def test_updates_a_clone_at_a_release_to_the_new_release(clone):
    git(clone, "checkout", "-q", "v1.0")
    assert how_to_update(NEWER)[0] == [("fetch", "--quiet", "--tags", "origin"), ("checkout", "--quiet", "v99.0")]
    assert apply(NEWER) is None
    assert (clone / "piprayer.py").read_text() == "99.0\n"


@needs_git
def test_doesnt_update_a_clone_with_changes(clone):
    (clone / "piprayer.py").write_text("mine\n")
    assert apply(NEWER) == "PiPrayer's folder has changes of its own"
    assert (clone / "piprayer.py").read_text() == "mine\n"


@needs_git
def test_doesnt_update_another_branch(clone):
    git(clone, "checkout", "-qb", "mine")
    assert apply(NEWER) == "PiPrayer's folder isn't on main or a release"


def test_doesnt_update_outside_a_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "SCRIPT_DIR", str(tmp_path))
    assert apply(NEWER) == "PiPrayer's folder isn't a git clone"


# ---------------------------------------------------------------- telling

def test_status_page_links_to_the_new_version(config):
    path, cfg = config()
    html = status_html(cfg, at(14), State(), update=NEWER)
    assert 'PiPrayer {} · <a href="{}">PiPrayer 99.0 is available</a>'.format(__version__, NEWER.url) in html
    assert "is available" not in status_html(cfg, at(14), State())


def run_times(monkeypatch, capsys, at_time, path, *args):
    at_time(at(14))
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", path, "times"] + list(args))
    code = main()
    return code, capsys.readouterr().out


def test_times_says_there_is_a_new_version(config, monkeypatch, capsys, at_time):
    path, cfg = config()
    save(path, checked=1, latest=NEWER)
    code, out = run_times(monkeypatch, capsys, at_time, path)
    assert code == 0
    assert out.endswith("\nPiPrayer 99.0 is available: update with git pull. See {}\n".format(NEWER.url))

    code, out = run_times(monkeypatch, capsys, at_time, path, "--format", "json")
    assert json.loads(out)["update"] == {"version": "99.0", "url": NEWER.url, "needs_setup": False}


def test_times_without_a_new_version_says_nothing(config, monkeypatch, capsys, at_time):
    path, cfg = config()
    code, out = run_times(monkeypatch, capsys, at_time, path)
    assert "available" not in out
    code, out = run_times(monkeypatch, capsys, at_time, path, "--format", "json")
    assert json.loads(out)["update"] is None


def test_update_file_sits_next_to_the_configuration(tmp_path):
    assert update_file(str(tmp_path / ".piprayer")) == str(tmp_path / ".piprayer-update")
    assert os.path.basename(update_file(".piprayer")) == ".piprayer-update"

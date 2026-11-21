# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Telling when there's a new version of PiPrayer, and optionally updating by itself.

Once a day the service asks GitHub for the latest release, and keeps the answer in .piprayer-update. The status and
settings pages, piprayer.py times and Home Assistant say when it is newer than this one. With auto-update = on, the
service updates with git at a quiet time, then restarts as after any update. update-check = off stops the check.
"""
import json
import logging
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Optional

from . import __version__, clock
from .config import SCRIPT_DIR
from .schedule import prayer_times

log = logging.getLogger("piprayer")

RELEASES_URL = "https://api.github.com/repos/kamranzafar/piprayer/releases/latest"
CHECK_SECONDS = 24 * 3600
FETCH_TIMEOUT = 15
# A release whose notes have this needs ./setup-piprayer.sh after updating, so isn't applied automatically
NEEDS_SETUP = "<!-- needs-setup -->"
# Updates by themselves this long after Isha, and never this close to an azaan, reminder or suhoor alarm
AFTER_ISHA = timedelta(hours=1)
CLEAR_OF_EVENTS = timedelta(minutes=10)
GIT_TIMEOUT = 120

VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)$")


def update_file(config_path):
    """e.g. .piprayer-update for .piprayer."""
    return config_path + "-update"


def parse_version(text):
    """(1, 2) for "v1.2" or "1.2", None for anything else."""
    match = VERSION.match((text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(version, than=__version__):
    new, old = parse_version(version), parse_version(than)
    return bool(new and old and new > old)


@dataclass
class Release:
    version: str  # e.g. "1.2"
    tag: str  # e.g. "v1.2"
    url: str  # the release notes
    needs_setup: bool = False

    def describe(self):
        return "PiPrayer {} is available".format(self.version)


@dataclass
class UpdateState:
    """What .piprayer-update keeps: when GitHub was last asked, and what it said."""
    checked: float = 0  # seconds since the epoch, 0 for never
    latest: Optional[Release] = None
    tried: Optional[str] = None  # the version auto-update last tried, and when
    tried_at: float = 0
    error: Optional[str] = None  # why updating to it failed
    install: bool = False  # Home Assistant asked to install it

    @classmethod
    def load(cls, path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            latest = data.pop("latest", None)
            state = cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})
            state.latest = Release(**latest) if latest else None
            return state
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path):
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(temporary, path)


def available(config_path, config):
    """The newer release, or None. Also None when update-check is off."""
    if not config.update_check:
        return None
    latest = UpdateState.load(update_file(config_path)).latest
    return latest if latest and is_newer(latest.version) else None


def describe_available(release, auto_update=False, error=None):
    """A line saying a release is available and how to get it, e.g. for piprayer.py times."""
    if release.needs_setup:
        how = "update with git pull, then ./setup-piprayer.sh"
    elif auto_update and error:
        how = "PiPrayer couldn't update itself, as {}. Update with git pull".format(error)
    elif auto_update:
        how = "PiPrayer updates itself tonight"
    else:
        how = "update with git pull"
    return "{}: {}. See {}".format(release.describe(), how, release.url)


def describe(config_path, config):
    """A line saying a newer release is available, or None."""
    release = available(config_path, config)
    if not release:
        return None
    state = UpdateState.load(update_file(config_path))
    return describe_available(release, config.auto_update, state.error if state.tried == release.version else None)


def fetch_latest(timeout=FETCH_TIMEOUT):
    """Ask GitHub for the latest release. Raises OSError or ValueError when it can't be told."""
    request = urllib.request.Request(RELEASES_URL, headers={
        "User-Agent": "PiPrayer/" + __version__, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    tag = data.get("tag_name") or ""
    if not parse_version(tag):
        raise ValueError("unexpected release tag {!r}".format(tag))
    return Release(version=tag.lstrip("v"), tag=tag, url=data.get("html_url") or "",
                   needs_setup=NEEDS_SETUP in (data.get("body") or ""))


def request_install(config_path):
    """Ask the service to update now, e.g. from Home Assistant's Install. Done within a minute, if allowed."""
    path = update_file(config_path)
    state = UpdateState.load(path)
    state.install = True
    state.save(path)


# ------------------------------------------------------------------ git

def git(*args, timeout=30):
    """Run git in PiPrayer's folder. Returns (exit code, output)."""
    try:
        result = subprocess.run(["git", "-C", SCRIPT_DIR] + list(args), capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return result.returncode, (result.stdout + result.stderr).strip()


def how_to_update(release):
    """The git commands that update to the release, or why PiPrayer can't update itself.

    Only a clean clone, on main or a release tag, is updated, so nobody's changes are lost.
    """
    code, changes = git("status", "--porcelain", "--untracked-files=no")
    if code != 0:
        return None, "PiPrayer's folder isn't a git clone"
    if changes:
        return None, "PiPrayer's folder has changes of its own"
    code, branch = git("symbolic-ref", "-q", "--short", "HEAD")
    if code == 0:  # on a branch
        if branch == "main":
            return [("pull", "--ff-only", "--quiet")], None
        return None, "PiPrayer's folder isn't on main or a release"
    code, tag = git("describe", "--tags", "--exact-match")
    if code == 0 and parse_version(tag):
        return [("fetch", "--quiet", "--tags", "origin"), ("checkout", "--quiet", release.tag)], None
    return None, "PiPrayer's folder isn't on main or a release"


def apply(release):
    """Update to the release with git. Returns None, or why it didn't."""
    commands, reason = how_to_update(release)
    if reason:
        return reason
    for command in commands:
        code, output = git(*command, timeout=GIT_TIMEOUT)
        if code != 0:
            return "git {} failed: {}".format(command[0], output.splitlines()[-1] if output else code)
    return None


# ------------------------------------------------------------------ the service

def in_background(work):
    threading.Thread(target=work, name="update-check", daemon=True).start()


def quiet_time(config, now, next_at):
    """Whether it is a good time to update: from an hour after Isha until Fajr, and not close to the next event."""
    if next_at is not None and next_at - now < CLEAR_OF_EVENTS:
        return False
    days = [now.date() + timedelta(days=offset) for offset in (-1, 0, 1)]
    times = [(at, prayer) for day in days for prayer, at in prayer_times(config, day, ["fajr", "isha"])]
    ishas = [at for at, prayer in times if prayer == "isha" and at <= now]
    if not ishas:
        return False
    isha = max(ishas)
    fajr = min((at for at, prayer in times if prayer == "fajr" and at > isha), default=None)
    return isha + AFTER_ISHA <= now and (fajr is None or now < fajr)


class Updater:
    """Checks for a new version once a day, from the service's loop, and updates by itself when allowed."""

    def __init__(self, config_path, run=None, fetch=None, update=None):
        self.path = update_file(config_path)
        # looked up when used, so tests can replace them
        self.run = run or (lambda work: in_background(work))
        self.fetch = fetch or (lambda: fetch_latest())
        self.update = update or (lambda release: apply(release))
        self.checking = False
        self.lock = threading.Lock()

    def tick(self, config, now, next_at=None):
        """Called about every minute while waiting. next_at is when the next event plays."""
        if not config.update_check:
            return
        state = UpdateState.load(self.path)
        if now.timestamp() - state.checked >= CHECK_SECONDS:
            self.check(now)
        elif config.auto_update and state.latest and is_newer(state.latest.version):
            self.maybe_update(config, now, next_at, state)

    def check(self, now):
        with self.lock:
            if self.checking:
                return
            self.checking = True

        def work():
            try:
                state = UpdateState.load(self.path)
                try:
                    release = self.fetch()
                except (OSError, ValueError) as e:  # offline, rate limited, ...
                    log.debug("Can't check for a new version of PiPrayer, trying again tomorrow: %s", e)
                else:
                    if is_newer(release.version) and (not state.latest or state.latest.version != release.version):
                        log.info("%s", describe_available(release))
                    state.latest = release
                state.checked = now.timestamp()
                state.save(self.path)
            except OSError as e:
                log.debug("Can't save %s: %s", self.path, e)
            finally:
                with self.lock:
                    self.checking = False

        self.run(work)

    def maybe_update(self, config, now, next_at, state):
        release = state.latest
        if release.needs_setup:
            if state.install:
                state.install = False
                state.error = "it needs ./setup-piprayer.sh, so update with git pull, then ./setup-piprayer.sh"
                state.save(self.path)
            return
        asked = state.install
        if not asked:
            if state.tried == release.version and now.timestamp() - state.tried_at < CHECK_SECONDS:
                return  # tried today already
            if not quiet_time(config, now, next_at):
                return
        if next_at is not None and next_at - now < CLEAR_OF_EVENTS:
            return

        log.info("Updating PiPrayer to %s", release.version)
        error = self.update(release)
        state.install = False
        state.tried, state.tried_at, state.error = release.version, now.timestamp(), error
        state.save(self.path)
        if error:
            log.warning("Can't update PiPrayer to %s: %s. Update it with git pull", release.version, error)

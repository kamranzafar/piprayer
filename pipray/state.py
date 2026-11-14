# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Skip and mute, saved next to the configuration so they survive restarts."""
import json
import logging
import os
from datetime import datetime, time, timedelta

from .schedule import upcoming_azaans

log = logging.getLogger("piprayer")

# Longest mute, in days
MAX_MUTE_DAYS = 30


def state_file(config_path):
    """e.g. .piprayer-state for .piprayer."""
    return config_path + "-state"


class State:
    """Azaans to skip, and the time until which every azaan is muted."""

    def __init__(self, skip=(), mute_until=None):
        self.skip = sorted(skip)
        self.mute_until = mute_until

    @classmethod
    def load(cls, path):
        try:
            with open(path) as f:
                data = json.load(f)
            mute_until = data.get("mute_until")
            return cls([datetime.fromisoformat(at) for at in data.get("skip", [])],
                       datetime.fromisoformat(mute_until) if mute_until else None)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("Ignoring %s, it can't be read: %s", path, e)
            return cls()

    def save(self, path):
        """Write the state, or remove the file when nothing is skipped or muted."""
        if not self.skip and not self.mute_until:
            if os.path.exists(path):
                os.remove(path)
            return
        data = {
            "skip": [at.isoformat() for at in sorted(self.skip)],
            "mute_until": self.mute_until.isoformat() if self.mute_until else None,
        }
        # Write then rename, so the service never reads a half written file
        with open(path + ".new", "w") as f:
            json.dump(data, f, indent=2)
        os.replace(path + ".new", path)

    def silenced(self, azaan_at):
        """Why the azaan at this time won't play: "muted", "skipped", or None if it will."""
        if self.mute_until and azaan_at < self.mute_until:
            return "muted"
        if azaan_at in self.skip:
            return "skipped"
        return None

    def muted_at(self, now):
        """The end of the mute, or None if nothing is muted now."""
        return self.mute_until if self.mute_until and self.mute_until > now else None

    def tidy(self, now):
        """Forget skips and mutes that are over. Returns True if anything changed."""
        skip = [at for at in self.skip if at > now]
        mute_until = self.muted_at(now)
        changed = skip != self.skip or mute_until != self.mute_until
        self.skip, self.mute_until = skip, mute_until
        return changed


def tidy_state(path, now):
    state = State.load(path)
    if state.tidy(now):
        state.save(path)


def next_playing(config, now, state):
    """The next azaan that isn't skipped or muted, or None."""
    days = (state.mute_until.date() - now.date()).days + 2 if state.mute_until else 2
    return next((e for e in upcoming_azaans(config, now, state, days) if not e.silenced), None)


def skip_next(config, path, now):
    """Skip the next azaan that would play, with its reminder. Returns its Entry, or None if there is none."""
    state = State.load(path)
    state.tidy(now)
    entry = next_playing(config, now, state)
    if entry:
        state.skip.append(entry.at)
        state.save(path)
    return entry


def mute(config, path, now, last_day):
    """Mute every azaan and reminder up to the end of last_day. Returns when the mute ends."""
    state = State.load(path)
    state.tidy(now)
    state.mute_until = datetime.combine(last_day + timedelta(days=1), time(0), config.tz)
    state.save(path)
    return state.mute_until


def mute_days(config, path, now, days):
    """Mute for a number of days counting today, given as text, e.g. from the status page or MQTT."""
    days = str(days).strip()
    if not days.isdigit() or not 1 <= int(days) <= MAX_MUTE_DAYS:
        raise ValueError("days must be a whole number from 1 to {}".format(MAX_MUTE_DAYS))
    return mute(config, path, now, now.date() + timedelta(days=int(days) - 1))


def resume(path):
    """Clear every skip and mute."""
    State().save(path)

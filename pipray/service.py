# SPDX-License-Identifier: GPL-3.0-or-later
"""The service: wait for each azaan and reminder, and play it. setup-piprayer.sh installs it with systemd."""
import logging
import os
from datetime import timedelta

from . import clock
from .config import DEFAULT_AZAAN, Config
from .player import play_event
from .schedule import next_event
from .state import State, state_file, tidy_state
from .mqtt import MqttBridge
from .web import StatusPage

log = logging.getLogger("piprayer")

# Skip an azaan if we only notice its time after this long, e.g. when the
# clock jumps forward after boot (the Pi has no hardware clock)
MAX_LATENESS = timedelta(minutes=5)

# Longest single sleep while waiting, so clock changes and config edits are noticed
MAX_SLEEP_SECONDS = 60


def run(config_path, default_azaan):
    """Wait for each event and play it. Runs until stopped."""
    log.info("PiPrayer started with configuration %s", config_path)
    state_path = state_file(config_path)
    status_page = StatusPage(config_path, default_azaan)
    mqtt = MqttBridge(config_path, default_azaan)
    started = None
    # Events already played or passed over. Another may fall due while one plays, e.g. Fajr at the same minute
    # as Isha on a short summer night, so the next event is looked for from a little before now.
    handled = set()

    try:
        while True:
            mtime = config_mtime(config_path)
            config = Config(config_path, default_azaan)
            now = clock.now(config.tz)
            started = started or now
            status_page.serve(config.status_port)
            mqtt.configure(config.mqtt)
            tidy_state(state_path, now)

            handled = {key for key in handled if key[0] > now - timedelta(days=1)}
            event = next_event(config, max(started, now - MAX_LATENESS), exclude=handled)
            if event is None:
                log.warning("No prayer times found for today or tomorrow, checking again in an hour")
                clock.sleep(3600)
            elif wait_and_play(config_path, config, mtime, state_path, event):
                handled.add(event.key)
            else:
                log.info("Configuration changed, reloading")
    finally:
        status_page.serve(None)
        mqtt.configure(None)


def wait_and_play(config_path, config, mtime, state_path, event):
    """Wait for the event and play it. Returns False if the configuration changed while waiting."""
    log.info("Next: %s", event)

    while True:
        remaining = (event.at - clock.now(config.tz)).total_seconds()
        if remaining <= 0:
            break
        if config_mtime(config_path) != mtime:
            return False
        clock.sleep(min(remaining, MAX_SLEEP_SECONDS))

    late = clock.now(config.tz) - event.at
    if late > MAX_LATENESS:
        log.warning("Skipping %s, noticed %d minutes late (clock changed?)",
                    event, late.total_seconds() // 60)
        return True

    # Read just before playing, so skip and mute apply however recently they were set
    silenced = State.load(state_path).silenced(event.azaan_at)
    if silenced:
        log.info("Not playing %s, it is %s", event, silenced)
        return True

    play_event(config, event)
    return True


def config_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def service_file():
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "systemd", "user", "piprayer.service")


def installed_azaan():
    """The azaan the installed service was set up with, or the default."""
    try:
        with open(service_file()) as f:
            for line in f:
                if line.startswith("ExecStart=") and "--azaan" in line:
                    return int(line.split("--azaan")[1].split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return DEFAULT_AZAAN

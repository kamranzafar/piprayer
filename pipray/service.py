# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The service: wait for each azaan and reminder, and play it. setup-piprayer.sh installs it with systemd."""
import glob
import logging
import os
from datetime import timedelta

from . import clock
from .config import DEFAULT_AZAAN, SCRIPT_DIR, Config, ConfigError
from . import player
from .schedule import next_event
from .mqtt import MqttBridge
from .state import (FAILED, MISSED, PLAYED, PLAYED_LOCALLY, STOPPED, LastAzaan, Playing, State, last_file,
                    playing_file, state_file, tidy_state)
from .web import StatusPage

log = logging.getLogger("piprayer")

# Skip an azaan if we only notice its time after this long, e.g. when the
# clock jumps forward after boot (the Pi has no hardware clock)
MAX_LATENESS = timedelta(minutes=5)

# Longest single sleep while waiting, so clock changes and config edits are noticed
MAX_SLEEP_SECONDS = 60

# Connect the speaker this long before an azaan or reminder, so it starts on time
CONNECT_AHEAD_SECONDS = 30

# When the speaker doesn't connect or the sound doesn't play, try again after this long, while the azaan is still
# within MAX_LATENESS of its time
RETRY_SECONDS = 30

# After an update, wait until the code has stayed the same this long before restarting, so a git pull has finished
UPDATE_SETTLE_SECONDS = 5


class ClockWatch:
    """Logs when the clock stops or starts being set from the network."""

    def __init__(self):
        self.synchronised = None

    def check(self):
        synchronised = clock.synchronised()
        if synchronised is False and self.synchronised is not False:
            log.warning("%s", clock.NOT_SYNCHRONISED)
        elif synchronised and self.synchronised is False:
            log.info("The clock is set from the network again")
        self.synchronised = synchronised


class Updated(Exception):
    """PiPrayer's code changed, e.g. after git pull, so the service should restart to run it."""


def code_version():
    """The size and modification time of each of PiPrayer's Python files, which change when it is updated."""
    paths = [os.path.join(SCRIPT_DIR, "piprayer.py"), os.path.join(SCRIPT_DIR, "praytimes.py")] + \
        sorted(glob.glob(os.path.join(SCRIPT_DIR, "pipray", "*.py"))) + \
        sorted(glob.glob(os.path.join(SCRIPT_DIR, "pipray", "static", "*")))
    version = []
    for path in paths:
        try:
            stat = os.stat(path)
            version.append((path, stat.st_size, stat.st_mtime_ns))
        except OSError:
            version.append((path, None, None))
    return tuple(version)


def check_for_update(started_version):
    """Raise Updated once the code differs from the version that is running and has stopped changing."""
    if code_version() == started_version:
        return
    while True:
        version = code_version()
        clock.sleep(UPDATE_SETTLE_SECONDS)
        if code_version() == version:
            raise Updated


def run(config_path, default_azaan):
    """Wait for each event and play it. Runs until stopped, or raises Updated when PiPrayer has been updated."""
    log.info("PiPrayer started with configuration %s", config_path)
    version = code_version()
    state_path = state_file(config_path)
    status_page = StatusPage(config_path, default_azaan)
    mqtt = MqttBridge(config_path, default_azaan)
    clock_watch = ClockWatch()
    Playing.clear(playing_file(config_path))  # left over if the service was stopped while playing
    config = None
    started = None
    # Events already played or passed over. Another may fall due while one plays, e.g. Fajr at the same minute
    # as Isha on a short summer night, so the next event is looked for from a little before now.
    handled = set()

    try:
        while True:
            mtime = config_mtime(config_path)
            try:
                config = load_config(config_path, default_azaan, config)
            except ConfigError:
                # Nothing to play yet: wait for the configuration to be fixed
                wait_for_change(config_path, mtime, version)
                continue
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
            elif wait_and_play(config_path, config, mtime, state_path, event, version, clock_watch, mqtt.refresh):
                handled.add(event.key)
            else:
                log.info("Configuration changed, reloading")
    finally:
        status_page.serve(None)
        mqtt.configure(None)


def load_config(config_path, default_azaan, previous=None):
    """Read the configuration, logging its warnings.

    If it is invalid, e.g. after a mistake while editing it, the error is logged and the previous configuration is
    used until it is fixed, so the azaan still plays. With no previous configuration, raises the ConfigError.
    """
    try:
        config = Config(config_path, default_azaan)
    except ConfigError as e:
        if previous:
            log.error("Can't use the new settings in %s, keeping the previous ones until it is fixed: %s",
                      config_path, e)
            return previous
        log.error("Can't start: %s. Waiting for %s to be fixed", e, config_path)
        raise
    for warning in config.warnings:
        log.warning("%s", warning)
    return config


def wait_for_change(config_path, mtime, version=None):
    """Wait until the configuration file changes. Raises Updated if PiPrayer is updated meanwhile."""
    while config_mtime(config_path) == mtime:
        if version:
            check_for_update(version)
        clock.sleep(MAX_SLEEP_SECONDS)


def wait_and_play(config_path, config, mtime, state_path, event, version=None, clock_watch=None, notify=None):
    """Wait for the event and play it. Returns False if the configuration changed while waiting.

    The speaker is connected a little ahead, so the azaan starts on time. Raises Updated if PiPrayer's code changed
    from the given version while waiting. notify is called when playing starts and ends, e.g. to update MQTT.
    """
    log.info("Next: %s", event)
    tried_connecting = False
    connected = None  # the speaker connected ahead, until play-azaan.sh disconnects it after playing

    try:
        while True:
            remaining = (event.at - clock.now(config.tz)).total_seconds()
            if remaining <= 0:
                break
            if config_mtime(config_path) != mtime:
                return False
            if version:
                check_for_update(version)
            if clock_watch:
                clock_watch.check()
            if remaining <= CONNECT_AHEAD_SECONDS and not tried_connecting:
                tried_connecting = True
                connected = connect_ahead(config, state_path, event)
                continue
            ahead = 0 if tried_connecting else CONNECT_AHEAD_SECONDS
            clock.sleep(min(remaining - ahead, MAX_SLEEP_SECONDS))

        late = clock.now(config.tz) - event.at
        if late > MAX_LATENESS:
            log.warning("Skipping %s, noticed %d minutes late (clock changed?)",
                        event, late.total_seconds() // 60)
            record(config_path, event, MISSED, "noticed {} minutes late, the clock may have changed"
                   .format(int(late.total_seconds() // 60)))
            return True

        # Read just before playing, so skip and mute apply however recently they were set
        silenced = State.load(state_path).silenced(event.azaan_at)
        if silenced:
            log.info("Not playing %s, it is %s", event, silenced)
            record(config_path, event, silenced)
            return True

        connected = None  # play-azaan.sh disconnects it once it has played
        play_with_retries(config_path, config, state_path, event, notify)
        return True
    finally:
        if connected:
            player.disconnect(connected)


def connect_ahead(config, state_path, event):
    """Connect the speaker for an event that will play. Returns its address, or None."""
    if State.load(state_path).silenced(event.azaan_at):
        return None
    speaker = player.connect(config.speaker)
    if speaker:
        log.info("Connected to speaker %s", speaker)
    else:
        log.info("Can't connect to the speaker yet, trying again at %s", event.at.strftime("%H:%M"))
    return speaker


def play_with_retries(config_path, config, state_path, event, notify=None):
    """Play the event, trying again while it is within MAX_LATENESS of its time, and record how it went.

    While it plays, it can be stopped with piprayer.py stop, the status page or Home Assistant.
    """
    playing_path = playing_file(config_path)
    Playing(event.prayer, event.reminder, event.at).save(playing_path)
    if notify:
        notify()
    try:
        while True:
            played = player.play_event(config, event, on_start=lambda pid: started_playing(playing_path, pid))
            if played.stopped or stop_requested(playing_path):
                log.info("Stopped %s", event)
                record(config_path, event, STOPPED)
                return
            if played.ok:
                record(config_path, event, PLAYED_LOCALLY if played.local else PLAYED,
                       "played on the Raspberry Pi's own audio output, as the speaker couldn't be reached"
                       if played.local else None)
                return
            if not played.worth_retrying or \
                    clock.now(config.tz) + timedelta(seconds=RETRY_SECONDS) - event.at > MAX_LATENESS:
                log.error("Couldn't play %s: %s", event, played.message)
                record(config_path, event, FAILED, played.message)
                return

            log.warning("Couldn't play %s: %s. Trying again in %d seconds", event, played.message, RETRY_SECONDS)
            clock.sleep(RETRY_SECONDS)
            if stop_requested(playing_path):
                log.info("Not trying %s again, it was stopped", event)
                record(config_path, event, STOPPED)
                return
            silenced = State.load(state_path).silenced(event.azaan_at)
            if silenced:
                log.info("Not trying %s again, it is %s now", event, silenced)
                record(config_path, event, silenced)
                return
    finally:
        Playing.clear(playing_path)
        if notify:
            notify()


def started_playing(path, pid):
    """Remember play-azaan.sh's process, so it can be stopped. Stop it straight away if that was asked already."""
    playing = Playing.load(path)
    if playing is None:
        return
    playing.pid = pid
    playing.save(path)
    if playing.stopped:
        player.terminate(pid)


def stop_requested(path):
    playing = Playing.load(path)
    return bool(playing and playing.stopped)


def record(config_path, event, result, error=None):
    """Remember how an azaan went. Reminders aren't recorded."""
    if not event.reminder:
        LastAzaan(event.prayer, event.at, result, error).save(last_file(config_path))


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

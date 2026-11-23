# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Playing an azaan, the reminder chime, the suhoor alarm or Surah Al-Kahf, and connecting the speaker, through
pipray.audio in a process of its own."""
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from .audio import NOT_CONNECTED, NOT_PLAYED, PLAYED_LOCALLY, STOPPED, TIME_UP
from .config import DEFAULT_VOLUME, DUA_FILE, MEDIA_DIR, SCRIPT_DIR
from .schedule import KAHF, REMINDER, SUHOOR
from .state import Playing, playing_file
from . import clock, systemd

# How the player is run, and how its process is told apart from others
PLAYER_MODULE = "pipray.audio"
PLAYER = [sys.executable, "-m", PLAYER_MODULE]
REMINDER_SOUND = os.path.join(MEDIA_DIR, "reminder.wav")
SUHOOR_SOUND = os.path.join(MEDIA_DIR, "suhoor.opus")
# While the player runs, tell systemd's watchdog the service is alive this often
WATCHDOG_SECONDS = 30
# The player stops a sound with a time limit by itself. If it is still running this long after, it is stopped
OVERRUN_SECONDS = 20

log = logging.getLogger("piprayer")


@dataclass
class Played:
    """How playing went."""
    returncode: int
    message: str = ""  # the last thing the player said, e.g. why it failed
    local: bool = False  # played on the Raspberry Pi's own audio output, as no speaker connected
    time_up: bool = False  # stopped before the end, as its time was up

    @property
    def ok(self):
        return self.returncode == 0

    @property
    def stopped(self):
        return self.returncode == STOPPED

    @property
    def worth_retrying(self):
        """The speaker didn't connect or the sound didn't play, which may work a little later."""
        return self.returncode in (NOT_CONNECTED, NOT_PLAYED)


def run_player(args, on_start=None, seconds=None):
    """Run the player, python -m pipray.audio. Returns its exit code and the lines it printed.

    It runs in its own process group, so stopping it stops paplay too. on_start is called with its process id. With
    seconds, the time limit the player was given, it is stopped if it runs well over it.
    """
    path = os.environ.get("PYTHONPATH")
    env = dict(os.environ, PYTHONPATH=SCRIPT_DIR + (os.pathsep + path if path else ""))
    env.pop("NOTIFY_SOCKET", None)  # only the service itself talks to systemd
    process = subprocess.Popen(PLAYER + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               start_new_session=True, env=env)
    if on_start:
        on_start(process.pid)
    overrun = clock.monotonic() + seconds + OVERRUN_SECONDS if seconds is not None else None
    try:
        while True:
            # The service tells systemd's watchdog it is still alive while a long azaan, with the dua, plays
            systemd.watchdog()
            try:
                output, _ = process.communicate(timeout=WATCHDOG_SECONDS)
                break
            except subprocess.TimeoutExpired:
                if overrun is not None and clock.monotonic() > overrun:
                    log.warning("%s is still playing after its time was up, stopping it", PLAYER_MODULE)
                    terminate(process.pid)
                    overrun = None
    except BaseException:
        # e.g. Ctrl+C on piprayer.py play, which a process in its own group doesn't get
        terminate(process.pid)
        process.wait()
        raise
    return process.returncode, [line.strip() for line in (output or "").splitlines() if line.strip()]


def play(sound, volume=DEFAULT_VOLUME, description=None, speaker=None, fallback=False, on_start=None, fade_in=0,
         dua=False, until=None, once=False):
    """Play an azaan (by number) or a sound file, on the given speaker or any paired one.

    With fallback, play on the Raspberry Pi's own audio output when no speaker connects. fade_in is the seconds to
    raise the volume over at the start. With dua, the dua after the azaan plays straight after it, in the same
    connection. until is when to fade out and stop, e.g. so a recitation stops before the azaan. With once, it isn't
    played again from the start when the speaker drops. on_start is called with the player's process id. Returns a
    Played.
    """
    seconds = None
    if until is not None:
        seconds = int((until - clock.now(until.tzinfo)).total_seconds())
        if seconds < 1:
            log.info("Not playing %s, its time is up", description or sound)
            return Played(0, TIME_UP, time_up=True)
    log.info("Playing %s at %d%% volume%s%s%s%s", description or "azaan " + str(sound), volume,
             ", fading in over {} seconds".format(fade_in) if fade_in else "", ", then the dua" if dua else "",
             " until " + until.strftime("%H:%M") if until else "", " on " + speaker if speaker else "")
    code, lines = run_player((["--fallback"] if fallback else []) + (["--fade-in", str(fade_in)] if fade_in else []) +
                             (["--then", DUA_FILE] if dua else []) +
                             (["--for", str(seconds)] if seconds is not None else []) + (["--once"] if once else []) +
                             [str(sound), str(volume)] + ([speaker] if speaker else []), on_start, seconds)
    for line in lines:
        log.info("%s", line)
    if code not in (0, STOPPED):
        log.error("%s failed with exit code %d", PLAYER_MODULE, code)
    return Played(code, lines[-1] if lines else "", code == 0 and PLAYED_LOCALLY in lines,
                  code == 0 and TIME_UP in lines)


def play_event(config, event, on_start=None):
    volume = config.volume_for(event.prayer)
    if event.kind == SUHOOR:
        return play(SUHOOR_SOUND, volume, "suhoor alarm", config.speaker, config.fallback_local, on_start)
    if event.kind == KAHF:
        return play(config.kahf_file, volume, "Surah Al-Kahf", config.speaker, config.fallback_local, on_start,
                    until=event.until, once=True)
    if event.kind == REMINDER:
        return play(REMINDER_SOUND, volume, "reminder for " + event.prayer, config.speaker, config.fallback_local,
                    on_start)
    azaan = config.azaan_for(event.prayer)
    return play(azaan, volume, "azaan {} for {}".format(azaan, event.prayer), config.speaker,
                config.fallback_local, on_start, config.fade_in_for(event.prayer), config.dua_for(event.prayer))


def connect(speaker=None):
    """Connect the given speaker, or any paired one, ahead of playing. Returns its address, or None."""
    code, lines = run_player(["--connect"] + ([speaker] if speaker else []))
    return lines[-1] if code == 0 and lines else None


def disconnect(speaker):
    run_player(["--disconnect", speaker])


def is_player(pid):
    """Whether the process is the player, so a process id left over from before a restart isn't stopped.

    Where /proc isn't available, the process is assumed to be it.
    """
    try:
        with open("/proc/{}/cmdline".format(pid), "rb") as f:
            return PLAYER_MODULE.encode() in f.read()
    except FileNotFoundError:
        return not os.path.isdir("/proc/self")
    except OSError:
        return False


def terminate(pid):
    """Stop the player and paplay. It disconnects the speaker as it stops."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def stop(config_path):
    """Stop the azaan, reminder, suhoor alarm or Surah Al-Kahf the service is playing, and don't try it again.

    Returns its Playing, or None if nothing is playing.
    """
    path = playing_file(config_path)
    playing = Playing.load(path)
    if playing is None:
        return None
    playing.stopped = True
    playing.save(path)
    if playing.pid and is_player(playing.pid):
        terminate(playing.pid)
    log.info("Stopping %s", playing.describe())
    return playing

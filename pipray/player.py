# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Playing an azaan or the reminder chime, and connecting the speaker, through play-azaan.sh."""
import logging
import os
import signal
import subprocess
from dataclasses import dataclass

from .config import DEFAULT_VOLUME, MEDIA_DIR, SCRIPT_DIR
from .state import Playing, playing_file

PLAYER = os.path.join(SCRIPT_DIR, "play-azaan.sh")
REMINDER_SOUND = os.path.join(MEDIA_DIR, "reminder.wav")

# Exit codes of play-azaan.sh
INVALID = 1
NOT_CONNECTED = 2
NOT_PLAYED = 3
STOPPED = 4

# What play-azaan.sh says when it played on the Raspberry Pi's own audio output
PLAYED_LOCALLY = "Played on the local audio output"

log = logging.getLogger("piprayer")


@dataclass
class Played:
    """How playing went."""
    returncode: int
    message: str = ""  # the last thing play-azaan.sh said, e.g. why it failed
    local: bool = False  # played on the Raspberry Pi's own audio output, as no speaker connected

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


def run_player(args, on_start=None):
    """Run play-azaan.sh. Returns its exit code and the lines it printed.

    It runs in its own process group, so stopping it stops paplay too. on_start is called with its process id.
    """
    process = subprocess.Popen([PLAYER] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               start_new_session=True)
    if on_start:
        on_start(process.pid)
    try:
        output, _ = process.communicate()
    except BaseException:
        # e.g. Ctrl+C on piprayer.py play, which a process in its own group doesn't get
        terminate(process.pid)
        process.wait()
        raise
    return process.returncode, [line.strip() for line in (output or "").splitlines() if line.strip()]


def play(sound, volume=DEFAULT_VOLUME, description=None, speaker=None, fallback=False, on_start=None):
    """Play an azaan (by number) or a sound file, on the given speaker or any paired one.

    With fallback, play on the Raspberry Pi's own audio output when no speaker connects. on_start is called with
    play-azaan.sh's process id. Returns a Played.
    """
    log.info("Playing %s at %d%% volume%s", description or "azaan " + str(sound), volume,
             " on " + speaker if speaker else "")
    code, lines = run_player((["--fallback"] if fallback else []) + [str(sound), str(volume)] +
                             ([speaker] if speaker else []), on_start)
    for line in lines:
        log.info("%s", line)
    if code not in (0, STOPPED):
        log.error("%s failed with exit code %d", os.path.basename(PLAYER), code)
    return Played(code, lines[-1] if lines else "", code == 0 and PLAYED_LOCALLY in lines)


def play_event(config, event, on_start=None):
    volume = config.volume_for(event.prayer)
    if event.reminder:
        return play(REMINDER_SOUND, volume, "reminder for " + event.prayer, config.speaker, config.fallback_local,
                    on_start)
    azaan = config.azaan_for(event.prayer)
    return play(azaan, volume, "azaan {} for {}".format(azaan, event.prayer), config.speaker,
                config.fallback_local, on_start)


def connect(speaker=None):
    """Connect the given speaker, or any paired one, ahead of playing. Returns its address, or None."""
    code, lines = run_player(["--connect"] + ([speaker] if speaker else []))
    return lines[-1] if code == 0 and lines else None


def disconnect(speaker):
    run_player(["--disconnect", speaker])


def is_player(pid):
    """Whether the process is play-azaan.sh, so a process id left over from before a restart isn't stopped.

    Where /proc isn't available, the process is assumed to be it.
    """
    try:
        with open("/proc/{}/cmdline".format(pid), "rb") as f:
            return os.path.basename(PLAYER).encode() in f.read()
    except FileNotFoundError:
        return not os.path.isdir("/proc/self")
    except OSError:
        return False


def terminate(pid):
    """Stop play-azaan.sh and paplay. It disconnects the speaker as it stops."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def stop(config_path):
    """Stop the azaan or reminder the service is playing, and don't try it again.

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

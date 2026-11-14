# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Playing an azaan or the reminder chime, and connecting the speaker, through play-azaan.sh."""
import logging
import os
import subprocess
from dataclasses import dataclass

from .config import DEFAULT_VOLUME, MEDIA_DIR, SCRIPT_DIR

PLAYER = os.path.join(SCRIPT_DIR, "play-azaan.sh")
REMINDER_SOUND = os.path.join(MEDIA_DIR, "reminder.wav")

# Exit codes of play-azaan.sh
INVALID = 1
NOT_CONNECTED = 2
NOT_PLAYED = 3

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
    def worth_retrying(self):
        """The speaker didn't connect or the sound didn't play, which may work a little later."""
        return self.returncode in (NOT_CONNECTED, NOT_PLAYED)


def run_player(args):
    """Run play-azaan.sh, logging what it says. Returns its exit code and the lines it printed."""
    result = subprocess.run([PLAYER] + args, capture_output=True, text=True)
    lines = [line.strip() for line in (result.stdout or "").splitlines() + (result.stderr or "").splitlines()
             if line.strip()]
    return result.returncode, lines


def play(sound, volume=DEFAULT_VOLUME, description=None, speaker=None, fallback=False):
    """Play an azaan (by number) or a sound file, on the given speaker or any paired one.

    With fallback, play on the Raspberry Pi's own audio output when no speaker connects. Returns a Played.
    """
    log.info("Playing %s at %d%% volume%s", description or "azaan " + str(sound), volume,
             " on " + speaker if speaker else "")
    code, lines = run_player((["--fallback"] if fallback else []) + [str(sound), str(volume)] +
                             ([speaker] if speaker else []))
    for line in lines:
        log.info("%s", line)
    if code != 0:
        log.error("%s failed with exit code %d", os.path.basename(PLAYER), code)
    return Played(code, lines[-1] if lines else "", code == 0 and PLAYED_LOCALLY in lines)


def play_event(config, event):
    volume = config.volume_for(event.prayer)
    if event.reminder:
        return play(REMINDER_SOUND, volume, "reminder for " + event.prayer, config.speaker, config.fallback_local)
    azaan = config.azaan_for(event.prayer)
    return play(azaan, volume, "azaan {} for {}".format(azaan, event.prayer), config.speaker,
                config.fallback_local)


def connect(speaker=None):
    """Connect the given speaker, or any paired one, ahead of playing. Returns its address, or None."""
    code, lines = run_player(["--connect"] + ([speaker] if speaker else []))
    return lines[-1] if code == 0 and lines else None


def disconnect(speaker):
    run_player(["--disconnect", speaker])

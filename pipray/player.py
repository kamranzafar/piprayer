# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Playing an azaan or the reminder chime through play-azaan.sh."""
import logging
import os
import subprocess

from .config import DEFAULT_VOLUME, MEDIA_DIR, SCRIPT_DIR

PLAYER = os.path.join(SCRIPT_DIR, "play-azaan.sh")
REMINDER_SOUND = os.path.join(MEDIA_DIR, "reminder.wav")

log = logging.getLogger("piprayer")


def play(sound, volume=DEFAULT_VOLUME, description=None, speaker=None):
    """Play an azaan (by number) or a sound file, on the given speaker or any paired one. Returns the exit code."""
    log.info("Playing %s at %d%% volume%s", description or "azaan " + str(sound), volume,
             " on " + speaker if speaker else "")
    result = subprocess.run([PLAYER, str(sound), str(volume)] + ([speaker] if speaker else []))
    if result.returncode != 0:
        log.error("%s failed with exit code %d", os.path.basename(PLAYER), result.returncode)
    return result.returncode


def play_event(config, event):
    volume = config.volume_for(event.prayer)
    if event.reminder:
        return play(REMINDER_SOUND, volume, "reminder for " + event.prayer, config.speaker)
    azaan = config.azaan_for(event.prayer)
    return play(azaan, volume, "azaan {} for {}".format(azaan, event.prayer), config.speaker)

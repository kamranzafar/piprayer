# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The current time and sleeping. Everything goes through here, so tests can run a day in milliseconds."""
import time
from datetime import datetime


def now(tz):
    return datetime.now(tz)


def sleep(seconds):
    time.sleep(seconds)


def monotonic():
    """Seconds from an arbitrary start, for measuring how long since something, unaffected by clock changes."""
    return time.monotonic()

# SPDX-License-Identifier: GPL-3.0-or-later
"""The current time and sleeping. Everything goes through here, so tests can run a day in milliseconds."""
import time
from datetime import datetime


def now(tz):
    return datetime.now(tz)


def sleep(seconds):
    time.sleep(seconds)

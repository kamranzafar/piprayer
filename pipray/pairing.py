# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding and pairing speakers from the settings page, in the background.

Scanning takes about 15 seconds and pairing up to 40, longer than a request should wait, so they run in a thread of
their own and the page asks how they are going every 2 seconds. Only one runs at a time, and not while an Azaan
plays, as scanning can disturb the speaker's audio.
"""
import logging
import threading

from . import bluetooth

log = logging.getLogger("piprayer")

SCANNING = "scanning"
PAIRING = "pairing"


def in_background(work):
    threading.Thread(target=work, name="pairing", daemon=True).start()


class Pairing:
    """The scan or pairing that is running, and how the last one went."""

    def __init__(self, playing=lambda: False, run=in_background):
        self.playing = playing  # whether an Azaan plays now
        self.run = run
        self.lock = threading.Lock()
        self.task = None  # SCANNING or PAIRING while one runs
        self.mac = None  # the speaker being paired
        self.name = None
        self.found = []  # [(mac, name)] from the last scan
        self.scanned = False
        self.message = None  # how the last one went, e.g. "Paired Echo Pop"
        self.error = False
        self.paired = None  # the speaker just paired, to offer to use it

    @property
    def busy(self):
        return self.task is not None

    def _start(self, task, work, mac=None, name=None):
        """Run work in the background, unless something else runs. Returns None, or why it can't start."""
        with self.lock:
            if self.task:
                return "Wait until the speaker search or pairing has finished."
            if self.playing():
                self.message = "Wait until the Azaan has finished, as looking for speakers can interrupt it."
                self.error = True
                return self.message
            self.task = task
            self.mac, self.name = mac, name
            self.message = None
            self.error = False
            self.paired = None

        def go():
            try:
                work()
            except Exception as e:  # noqa: BLE001, the page must not keep waiting for it
                log.exception("Speaker %s failed", task)
                self.done("Something went wrong: {}".format(e), error=True)

        self.run(go)
        return None

    def done(self, message, error=False, paired=None):
        with self.lock:
            self.task = None
            self.message = message
            self.error = error
            self.paired = paired

    def scan(self):
        def work():
            found = bluetooth.scan_speakers()
            self.found = found
            self.scanned = True
            self.done(None if found else "No speakers found. Is the speaker in pairing mode, and close by?",
                      error=not found)

        return self._start(SCANNING, work)

    def pair(self, mac, name=None, again=False):
        """Pair a speaker found by the last scan, or pair again one that was paired before."""
        name = name or dict(self.found).get(mac) or mac

        def work():
            error = bluetooth.pair_again(mac) if again else bluetooth.pair_speaker(mac)
            if error:
                self.done("Couldn't pair {}: {}".format(name, error), error=True)
            else:
                self.found = [(found, label) for found, label in self.found if found != mac]
                self.done("Paired {}.".format(name), paired=mac)

        return self._start(PAIRING, work, mac, name)

    def forget(self, mac, name=None):
        """Remove a paired speaker. Quick, so it doesn't run in the background. Returns None, or why it failed."""
        if self.busy:
            return "Wait until the speaker search or pairing has finished."
        name = name or mac
        error = bluetooth.remove_speaker(mac)
        with self.lock:
            self.message = "Couldn't forget {}: {}".format(name, error) if error else "Forgot {}.".format(name)
            self.error = bool(error)
            self.paired = None
        return error

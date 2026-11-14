# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings page PIN, saved in .piprayer as a hash rather than as it is typed, and the login cookie."""
import hashlib
import hmac
import re
import secrets
import time

PIN_DIGITS = (4, 8)
PIN = re.compile(r"^[0-9]{4,8}$")

# e.g. pbkdf2-sha256$100000$<salt>$<hash>. Slow to work out, so a copy of .piprayer doesn't give the PIN away quickly
HASH = re.compile(r"^pbkdf2-sha256\$([0-9]+)\$([0-9a-f]+)\$([0-9a-f]{64})$")
ITERATIONS = 100000

# How long a login lasts
SESSION_SECONDS = 30 * 24 * 3600
COOKIE = "piprayer_settings"

# After a wrong PIN, the next try waits this long, doubling with each wrong one, up to the most
FIRST_WAIT_SECONDS = 1
MOST_WAIT_SECONDS = 15 * 60


def valid_pin(pin):
    return bool(PIN.match(pin))


def is_hash(value):
    return bool(HASH.match(value))


def hash_pin(pin, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), iterations).hex()
    return "pbkdf2-sha256${}${}${}".format(iterations, salt, digest)


def check_pin(pin, saved):
    """True if the PIN matches the saved one, a hash, or digits not hashed yet, e.g. typed into .piprayer."""
    match = HASH.match(saved)
    if match:
        iterations, salt, _ = match.groups()
        return hmac.compare_digest(hash_pin(pin, salt, int(iterations)), saved)
    return hmac.compare_digest(pin.encode(), saved.encode())


def session_cookie(saved, now=None):
    """A login, signed with the saved PIN, so it ends when the PIN is changed. Valid until it expires."""
    expires = int((time.time() if now is None else now) + SESSION_SECONDS)
    return "{}.{}".format(expires, sign(saved, expires))


def valid_session(cookie, saved, now=None):
    expires, _, signature = (cookie or "").partition(".")
    if not expires.isdigit() or int(expires) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(signature, sign(saved, int(expires)))


def sign(saved, expires):
    return hmac.new(saved.encode(), str(expires).encode(), hashlib.sha256).hexdigest()


class Throttle:
    """Makes each try after a wrong PIN wait longer, so a PIN can't be found by trying them all.

    Kept for all who try, rather than for each address, which could be changed to try more. The clock is
    time.monotonic, unless given, e.g. in tests.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.wrong = 0
        self.until = 0

    def wait(self):
        """Seconds to wait before the next try, 0 when it can be tried now."""
        return max(0, self.until - self.clock())

    def failed(self):
        self.wrong += 1
        self.until = self.clock() + min(FIRST_WAIT_SECONDS * 2 ** (self.wrong - 1), MOST_WAIT_SECONDS)

    def succeeded(self):
        self.wrong = 0
        self.until = 0

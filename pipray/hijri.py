# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Hijri date by the Umm al-Qura calendar, shifted by hijri-adjust, and changing at Maghrib."""
import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .schedule import prayer_times

MONTHS = ["Muharram", "Safar", "Rabi al-Awwal", "Rabi al-Thani", "Jumada al-Awwal", "Jumada al-Thani", "Rajab",
          "Shaban", "Ramadan", "Shawwal", "Dhu al-Qadah", "Dhu al-Hijjah"]

# 1 Muharram 1400
FIRST_YEAR = 1400
FIRST_DAY = date(1979, 11, 20)

# The length of each month of the Umm al-Qura calendar from 1400 to 1500 (1979 to 2077), three hex digits a year:
# bit n is set when month n + 1 has 30 days, otherwise it has 29. From the Umm al-Qura month starts compiled in
# hijri-converter (https://github.com/dralshehri/hijri-converter, MIT licence).
MONTH_LENGTHS = (
    "d4b52aa574ae97656cb55aaaa554ad95d2da5d9db2ba4b4aa552b5575b6a"  # 1400
    "bd2bc4b89a9552d5adb6a6d4dc9d92aa69562ae56d36ab55aaa94d49d95d"  # 1420
    "2ba5b55aad55a9a92e26e55dada6d46a554ba9754eaae5acba9d92b2564b"  # 1440
    "cab55ab556d2ea5e4aa9552daad36c7596d269552da5b4ba9ba3b4b69b52"  # 1460
    "aa64b696d2ec6d9eb2d54d2aa564ae96dd6ab54b29a9352ba57536ab56aa"  # 1480
    "e93"  # 1500
)


def month_starts():
    """The day number (date.toordinal) of the first of each month, and of the day after the last month."""
    starts = [FIRST_DAY.toordinal()]
    for year in range(len(MONTH_LENGTHS) // 3):
        thirties = int(MONTH_LENGTHS[year * 3:year * 3 + 3], 16)
        for month in range(12):
            starts.append(starts[-1] + 29 + (thirties >> month & 1))
    return starts


MONTH_STARTS = month_starts()


@dataclass(frozen=True)
class HijriDate:
    year: int
    month: int  # 1 to 12
    day: int

    @property
    def month_name(self):
        return MONTHS[self.month - 1]

    def __str__(self):
        """e.g. "23 Jumada al-Awwal 1448"."""
        return "{} {} {}".format(self.day, self.month_name, self.year)

    @classmethod
    def from_gregorian(cls, day):
        """The Hijri date of a Gregorian date, or None outside 1979 to 2077."""
        number = day.toordinal()
        if not MONTH_STARTS[0] <= number < MONTH_STARTS[-1]:
            return None
        months = bisect.bisect_right(MONTH_STARTS, number) - 1
        return cls(FIRST_YEAR + months // 12, months % 12 + 1, number - MONTH_STARTS[months] + 1)


@dataclass(frozen=True)
class HijriDay:
    """The Hijri date at some moment, as shown."""
    date: HijriDate
    evening: bool  # after Maghrib, so the date is the next day's
    adjust: int  # days added to the Umm al-Qura date

    def __str__(self):
        """e.g. "23 Jumada al-Awwal 1448", or "Evening of 1 Ramadan 1448" from Maghrib the day before."""
        return ("Evening of " if self.evening else "") + str(self.date)

    def to_json(self):
        return {
            "day": self.date.day,
            "month": self.date.month,
            "month_name": self.date.month_name,
            "year": self.date.year,
            "evening": self.evening,
            "adjust": self.adjust,
            "text": str(self),
        }


def hijri_day(config, day, evening=False):
    """The Hijri date for a Gregorian date, or for the evening before the next one, or None outside 1979 to 2077."""
    hijri = HijriDate.from_gregorian(day + timedelta(days=config.hijri_adjust + (1 if evening else 0)))
    return HijriDay(hijri, evening, config.hijri_adjust) if hijri else None


def hijri_now(config, now) -> Optional[HijriDay]:
    """The Hijri date now. The Islamic day starts at Maghrib, so from then on it is the next day's date."""
    return hijri_day(config, now.date(), after_maghrib(config, now))


def after_maghrib(config, now):
    """True from Maghrib until midnight, even when the azaan isn't played for Maghrib."""
    times = prayer_times(config, now.date(), ["maghrib"])
    return bool(times) and now >= times[0][1] and times[0][1].date() == now.date()


def describe_adjust(adjust):
    """e.g. "1 day behind the Umm al-Qura calendar"."""
    if not adjust:
        return "Umm al-Qura calendar"
    return "{} day{} {} the Umm al-Qura calendar".format(abs(adjust), "" if abs(adjust) == 1 else "s",
                                                         "ahead of" if adjust > 0 else "behind")

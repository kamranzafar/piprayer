"""
    PiPrayer - Azaan for Raspberry Pi
    Copyright (C) 2021  Kamran Zafar

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import sys
import configparser
import praytimes
from datetime import date, datetime, time

LAT = "lat"
LNG = "lng"
TIMEZONE = "timezone"
DST = "dst"
GMT_OFFSET = "gmt-offset"
METHOD = "method"
ASR_TIME = "asr-time"
PRAYERS = "prayers"

if __name__ == "__main__":
    if len(sys.argv) <= 1:
        print('Require configuration file as argument')
        exit(1)

    config = configparser.ConfigParser()

    if not config.read(sys.argv[1]):
        print('File not found or is inaccessible')
        exit(1)

    if not config.has_section('Default'):
        print("Prayer configuration is missing")
        exit(1)

    prayerConfig = config['Default']

    offsets = {}
    prayers = []

    # Each entry is "Name" or "Name:offset", offset in minutes (default 0)
    for p in prayerConfig[PRAYERS].split(","):
        if not p.strip():
            continue

        name, _, offset = p.partition(":")
        prayer = name.strip().lower()

        if prayer not in praytimes.PrayTimes.timeNames:
            print('Unknown prayer: ' + name.strip())
            exit(1)

        try:
            offsets[prayer] = int(offset) if offset.strip() else 0
        except ValueError:
            print('Invalid offset for ' + name.strip() + ': ' + offset.strip())
            exit(1)

        prayers.append(prayer)

    prayTimes = praytimes.PrayTimes(prayerConfig[METHOD], prayerConfig[ASR_TIME])
    prayTimes.tune(offsets)

    today = date.today()

    if TIMEZONE in prayerConfig:
        # Derive the UTC offset (including daylight saving) from the IANA timezone.
        # Measured at midday so a DST change in the early hours is already applied.
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            tz = ZoneInfo(prayerConfig[TIMEZONE].strip())
        except (ZoneInfoNotFoundError, ValueError):
            print('Unknown timezone: ' + prayerConfig[TIMEZONE])
            exit(1)

        gmtOffset = datetime.combine(today, time(12), tz).utcoffset().total_seconds() / 3600
        dst = 0
    else:
        # Legacy configuration: fixed offset and manual daylight saving flag
        gmtOffset = int(prayerConfig[GMT_OFFSET])
        dst = int(prayerConfig[DST])

    times = prayTimes.getTimes(today,
                               (float(prayerConfig[LAT]),
                                float(prayerConfig[LNG])),
                               gmtOffset,
                               dst)

    for p in prayers:
        print(p + '-' + times[p])

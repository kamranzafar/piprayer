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
from datetime import date

LAT = "lat"
LNG = "lng"
DST = "dst"
GMT_OFFSET = "gmt-offset"
METHOD = "method"
ASR_TIME = "asr-time"
PRAYERS = "prayers"

if __name__ == "__main__":
    if len(sys.argv) <= 1:
        print('Require configuration file as argument')
        exit(1)

    try:
        f = open(sys.argv[1])
    except IOError:
        print('File not found or is inaccessible')
        exit(1)
    finally:
        f.close()

    config = configparser.ConfigParser()
    config.sections()
    config.read(sys.argv[1])

    if not config.has_section('Default'):
        print("Prayer configuration is missing")
        exit(1)

    prayerConfig = config['Default']

    # for key in prayerConfig:
    # 	print(key + ': '+prayerConfig[key])

    offsets = {}
    prayers = []

    for p in prayerConfig[PRAYERS].split(","):
        prayer = p.strip().split(":")[0].lower()
        offset = p.strip().split(":")[1]

        offsets[prayer] = int(offset)
        prayers.append(prayer)

    prayTimes = praytimes.PrayTimes(prayerConfig[METHOD], prayerConfig[ASR_TIME])
    prayTimes.tune(offsets)

    times = prayTimes.getTimes(date.today(),
                               (float(prayerConfig[LAT]),
                                float(prayerConfig[LNG])),
                               int(prayerConfig[GMT_OFFSET]),
                               int(prayerConfig[DST]))

    for p in prayers:
        print(p + '-' + times[p])

#!/bin/bash

#    PiPrayer - Azaan for Raspberry Pi
#    Copyright (C) 2021  Kamran Zafar
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

BLUETOOTH_MAC=$(hcitool con | grep -o "[[:xdigit:]:]\{11,17\}")

if [ -z "$BLUETOOTH_MAC" ]; then
    BLUETOOTH_MAC=$(grep '^defaults.bluealsa.device' ~/.asoundrc | awk '{gsub(/"/, "", $2); print $2}')

    if [ -z "$BLUETOOTH_MAC" ]; then
      echo "Can't connect to bluetooth"
      exit 1
    fi

    echo -e "connect $BLUETOOTH_MAC \nquit" | bluetoothctl
    sleep 5
fi

aplay -D bluealsa $(dirname "$0")/media/azaan-"$1".wav
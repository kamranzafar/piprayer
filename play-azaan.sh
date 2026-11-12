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

#    Usage: play-azaan.sh <azaan number | sound file> [volume percent]

SOUND=$1
VOLUME=${2:-100}

if [[ "$SOUND" =~ ^[0-9]+$ ]]; then
  SOUND_FILE="$(dirname "$0")/media/azaan-$SOUND.wav"
else
  SOUND_FILE=$SOUND
fi

if [ ! -f "$SOUND_FILE" ]; then
  echo "Sound file not found: $SOUND_FILE"
  exit 1
fi

if ! [[ "$VOLUME" =~ ^[0-9]+$ ]] || [ "$VOLUME" -gt 100 ]; then
  echo "Invalid volume: $VOLUME, use a number from 0 to 100"
  exit 1
fi

device_info() {
  bluetoothctl info "$1" 2>/dev/null
}

is_connected() {
  device_info "$1" | grep -q "Connected: yes"
}

DEVICES=$(bluetoothctl devices | awk '/^Device/ {print $2}')
BLUETOOTH_MAC=""

# Use a device that is already connected, if there is one
for mac in $DEVICES; do
  if is_connected "$mac"; then
    BLUETOOTH_MAC=$mac
    break
  fi
done

# Otherwise connect to the first paired device that accepts the connection
if [ -z "$BLUETOOTH_MAC" ]; then
  for mac in $DEVICES; do
    device_info "$mac" | grep -q "Paired: yes" || continue

    echo "connect $mac" | bluetoothctl >/dev/null
    sleep 5

    if is_connected "$mac"; then
      BLUETOOTH_MAC=$mac
      break
    fi
  done
fi

if [ -z "$BLUETOOTH_MAC" ]; then
  echo "Can't connect to bluetooth"
  exit 1
fi

# 65536 is 100%, on the same scale as the system volume control
paplay --volume=$((VOLUME * 65536 / 100)) "$SOUND_FILE"
echo "disconnect $BLUETOOTH_MAC" | bluetoothctl >/dev/null

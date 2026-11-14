#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

#    PiPrayer - Azaan for Raspberry Pi
#    Copyright (C) 2021-2026  Kamran Zafar
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

#    Usage: play-azaan.sh [--fallback] <azaan number | sound file> [volume percent] [speaker address]
#           play-azaan.sh --connect [speaker address]
#           play-azaan.sh --disconnect <speaker address>
#
#    Without a speaker address, any paired speaker is used. With --fallback, the sound plays on the Raspberry Pi's
#    own audio output (headphone jack or HDMI) when no speaker connects. --connect connects a speaker ahead of
#    time, so the azaan starts on time, and prints its address.
#
#    Exit codes: 0 played, 1 invalid arguments, 2 no speaker connected, 3 the sound didn't play

# How long to wait for a speaker to connect, and then for its audio output to appear
CONNECT_SECONDS=10

MODE=play
FALLBACK=""
case "$1" in
  --connect) MODE=connect; shift ;;
  --disconnect) MODE=disconnect; shift ;;
  --fallback) FALLBACK=1; shift ;;
esac

if [ "$MODE" = play ]; then
  SOUND=$1
  VOLUME=${2:-100}
  SPEAKER_ARG=$3
else
  SPEAKER_ARG=$1
fi
SPEAKER=$(echo "$SPEAKER_ARG" | tr "[:lower:]" "[:upper:]")

if [ -n "$SPEAKER" ] && ! [[ "$SPEAKER" =~ ^([0-9A-F]{2}:){5}[0-9A-F]{2}$ ]]; then
  echo "Invalid speaker: $SPEAKER_ARG, use the Bluetooth address, e.g. AA:BB:CC:DD:EE:FF"
  exit 1
fi

device_info() {
  bluetoothctl info "$1" 2>/dev/null
}

is_connected() {
  device_info "$1" | grep -q "Connected: yes"
}

# Print the address of a connected speaker: one that is already connected, otherwise the first paired one that
# accepts the connection
connect_speaker() {
  local devices mac i
  if [ -n "$SPEAKER" ]; then
    devices=$SPEAKER
  else
    devices=$(bluetoothctl devices | awk '/^Device/ {print $2}')
  fi

  for mac in $devices; do
    if is_connected "$mac"; then
      echo "$mac"
      return 0
    fi
  done

  for mac in $devices; do
    device_info "$mac" | grep -q "Paired: yes" || continue
    echo "connect $mac" | bluetoothctl >/dev/null
    for ((i = 0; i < CONNECT_SECONDS; i++)); do
      if is_connected "$mac"; then
        echo "$mac"
        return 0
      fi
      sleep 1
    done
  done
  return 1
}

# Print the audio output of a connected speaker, waiting for it to appear, e.g. bluez_output.AA_BB_CC_DD_EE_FF.1
# with PipeWire. Prints nothing when it can't be found, and the sound then plays on the default output.
speaker_sink() {
  local id=${1//:/_} sink i
  command -v pactl >/dev/null || return 1
  for ((i = 0; i < CONNECT_SECONDS; i++)); do
    sink=$(pactl list short sinks 2>/dev/null | awk -v id="$id" 'index($2, id) {print $2; exit}')
    if [ -n "$sink" ]; then
      echo "$sink"
      return 0
    fi
    sleep 1
  done
  return 1
}

# Print the Raspberry Pi's own audio output: the first one that isn't a Bluetooth device
local_sink() {
  command -v pactl >/dev/null || return 1
  pactl list short sinks 2>/dev/null | awk '$2 !~ /^bluez/ {print $2; exit}'
}

not_connected() {
  echo "Can't connect to bluetooth${SPEAKER:+ speaker $SPEAKER}"
}

if [ "$MODE" = disconnect ]; then
  if [ -z "$SPEAKER" ]; then
    echo "Missing speaker address"
    exit 1
  fi
  echo "disconnect $SPEAKER" | bluetoothctl >/dev/null
  exit 0
fi

if [ "$MODE" = connect ]; then
  if ! BLUETOOTH_MAC=$(connect_speaker); then
    not_connected
    exit 2
  fi
  speaker_sink "$BLUETOOTH_MAC" >/dev/null
  echo "$BLUETOOTH_MAC"
  exit 0
fi

if [[ "$SOUND" =~ ^[0-9]+$ ]]; then
  SOUND_FILE="$(dirname "$0")/media/azaan-$SOUND.opus"
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

if BLUETOOTH_MAC=$(connect_speaker); then
  DEVICE=$(speaker_sink "$BLUETOOTH_MAC")
  WHERE="speaker $BLUETOOTH_MAC"
elif [ -n "$FALLBACK" ]; then
  echo "$(not_connected), playing on the local audio output instead"
  BLUETOOTH_MAC=""
  DEVICE=$(local_sink)
  WHERE="the local audio output"  # PiPrayer looks for "Played on the local audio output"
else
  not_connected
  exit 2
fi

# 65536 is 100%, on the same scale as the system volume control
paplay --volume=$((VOLUME * 65536 / 100)) ${DEVICE:+"--device=$DEVICE"} "$SOUND_FILE"
STATUS=$?
if [ -n "$BLUETOOTH_MAC" ]; then
  echo "disconnect $BLUETOOTH_MAC" | bluetoothctl >/dev/null
fi

if [ "$STATUS" -ne 0 ]; then
  echo "paplay failed with exit code $STATUS"
  exit 3
fi
echo "Played on $WHERE"

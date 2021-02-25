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

CURR_PATH=$(dirname $(realpath "$0"))
CONF_FILE="$CURR_PATH/.piprayer"
CRON_FILE="$CURR_PATH/.piprayercron"
CRON_BLOCK="## PI PRAYER ##"
AZAAN=1

if [ $# -ne 0 ]; then
  AZAAN=$1
fi

if [ ! -f "$CONF_FILE" ]; then
  echo "Configuration file $CONF_FILE not found"
  exit 1
fi

PRAYER_TIMES=$(python "$CURR_PATH"/piprayer.py "$CONF_FILE")

if [ "$?" -ne "0" ]; then
  echo "$PRAYER_TIMES"
  exit 1
fi

#echo "$PRAYER_TIMES"

NEW_CRON="$CRON_BLOCK"
for prayer in $PRAYER_TIMES; do
  PRAYER_NAME=$(echo "$prayer" | cut -f1 -d-)
  PRAYER_TIME_H=$(echo "$prayer" | cut -f2 -d- | cut -f1 -d:)
  PRAYER_TIME_M=$(echo "$prayer" | cut -f2 -d- | cut -f2 -d:)

  NEW_CRON="$NEW_CRON\n$PRAYER_TIME_M $PRAYER_TIME_H * * * $CURR_PATH/play-azaan.sh $AZAAN #$PRAYER_NAME"
done

NEW_CRON="$NEW_CRON\n0 0 * * * $CURR_PATH/$(basename "$0") $AZAAN"
NEW_CRON="$NEW_CRON\n$CRON_BLOCK"

#echo -e "$NEW_CRON"

crontab -l >"$CRON_FILE"
cp "$CRON_FILE" "$CRON_FILE.bak"
cat "$CRON_FILE.bak" | sed '/## PI PRAYER ##/,/## PI PRAYER ##/d' >"$CRON_FILE"
echo -e "$NEW_CRON" >>"$CRON_FILE"
crontab <"$CRON_FILE"
rm -f "$CRON_FILE"

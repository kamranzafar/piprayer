#!/bin/bash

CONF_FILE="./.piprayer"
CRON_FILE="./.piprayercron"

if [ ! -f "$CONF_FILE" ]; then
  echo "Configuration file $CONF_FILE not found"
  exit 1
fi

PRAYER_TIMES=$(python ./piprayer.py $CONF_FILE)

if [ "$?" -ne "0" ]; then
  echo "$PRAYER_TIMES"
  exit 1
fi

#echo "$PRAYER_TIMES"

NEW_CRON="## PI PRAYER ###"
for prayer in $PRAYER_TIMES
do
  PRAYER_NAME=$(echo "$prayer" | cut -f1 -d-)
  PRAYER_TIME_H=$(echo "$prayer" | cut -f2 -d- | cut -f1 -d:)
  PRAYER_TIME_M=$(echo "$prayer" | cut -f2 -d- | cut -f2 -d:)

  NEW_CRON="$NEW_CRON\n$PRAYER_TIME_M $PRAYER_TIME_H * * * play-azaan.sh 1 #$PRAYER_NAME"
done
NEW_CRON="$NEW_CRON\n## PI PRAYER ###"

echo -e "$NEW_CRON"

crontab -l > "$CRON_FILE"

if [ "$?" -ne "0" ]; then
  exit 1
fi

cp "$CRON_FILE" "$CRON_FILE.bak"
sed -in '/## PI PRAYER ##/,/## PI PRAYER ##/d' "$CRON_FILE"
echo -e "$NEW_CRON" >> "$CRON_FILE"
crontab "$CRON_FILE"
rm -f "$CRON_FILE"
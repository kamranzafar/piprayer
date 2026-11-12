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

#    Usage:
#      ./setup-piprayer.sh [1-15]      install (or update) PiPrayer, optionally choosing the azaan
#      ./setup-piprayer.sh --uninstall remove PiPrayer

CURR_PATH=$(dirname "$(realpath "$0")")
CONF_FILE="$CURR_PATH/.piprayer"
SERVICE_NAME="piprayer.service"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$SERVICE_DIR/$SERVICE_NAME"
CRON_BLOCK="## PI PRAYER ##"
AZAAN=1

# systemctl --user needs this, it may be missing when run from cron or sudo
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Remove the schedule installed by older, cron based versions of PiPrayer
remove_cron() {
  local current
  current=$(crontab -l 2>/dev/null) || return

  if echo "$current" | grep -q "$CRON_BLOCK"; then
    echo "$current" | sed "/$CRON_BLOCK/,/$CRON_BLOCK/d" | crontab -
    echo "Removed old PiPrayer cron jobs"
  fi
}

if [ "$1" = "--uninstall" ]; then
  systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null
  rm -f "$SERVICE_FILE"
  systemctl --user daemon-reload
  remove_cron
  echo "PiPrayer uninstalled"
  exit 0
fi

if [ $# -ne 0 ]; then
  AZAAN=$1
fi

if [ ! -f "$CURR_PATH/media/azaan-$AZAAN.wav" ]; then
  echo "Azaan $AZAAN not found, choose a number between 1 and 15"
  exit 1
fi

if [ ! -f "$CONF_FILE" ]; then
  echo "Configuration file $CONF_FILE not found"
  exit 1
fi

PYTHON=$(command -v python3)
if [ -z "$PYTHON" ]; then
  echo "python3 not found, install it with: sudo apt install python3"
  exit 1
fi

# Check the configuration before installing
PRAYER_TIMES=$("$PYTHON" "$CURR_PATH/piprayer.py" -c "$CONF_FILE" times) || exit 1

mkdir -p "$SERVICE_DIR"
cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=PiPrayer - Azaan for Raspberry Pi
After=pipewire-pulse.service pulseaudio.service

[Service]
ExecStart="$PYTHON" "$CURR_PATH/piprayer.py" -c "$CONF_FILE" run --azaan $AZAAN
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload &&
  systemctl --user enable "$SERVICE_NAME" &&
  systemctl --user restart "$SERVICE_NAME"

if [ $? -ne 0 ]; then
  echo "Failed to start $SERVICE_NAME"
  exit 1
fi

remove_cron

# Lingering starts the user's services at boot, without having to log in
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "Enabling lingering so PiPrayer starts at boot (may ask for your sudo password)"
  if ! sudo loginctl enable-linger "$USER"; then
    echo "Warning: could not enable lingering, PiPrayer will only run while you are logged in."
    echo "Run this to fix it: sudo loginctl enable-linger $USER"
  fi
fi

echo
echo "PiPrayer installed with azaan $AZAAN. Today's azaan times:"
echo "$PRAYER_TIMES"
echo
echo "Status: systemctl --user status piprayer"
echo "Logs:   journalctl --user-unit piprayer -f"

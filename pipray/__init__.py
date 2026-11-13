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

Modules, each depending only on the ones above it:

    clock      the current time and sleeping, replaced in tests
    config     reading and checking .piprayer
    schedule   prayer times, and the azaans and reminders to play
    state      skip and mute, saved in .piprayer-state
    output     the times as a table, text or JSON
    player     playing through play-azaan.sh
    web        the optional status page
    mqtt       optional MQTT for Home Assistant
    service    the loop that waits for each azaan and plays it
    bluetooth  finding and pairing speakers with bluetoothctl
    wizard     piprayer.py config
    cli        the command line
"""

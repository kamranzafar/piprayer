#!/usr/bin/env python3
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

Run "python3 piprayer.py --help" for the commands. The code is in the pipray package.
"""
import sys

from pipray.cli import main

if __name__ == "__main__":
    sys.exit(main())

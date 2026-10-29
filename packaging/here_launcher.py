"""PyInstaller entry point of RefrainHere.exe (SPEC §29): the `refrain` keyword in File Explorer runs it."""

import sys

from music_desk.here import main

sys.exit(main())

"""PyInstaller entry point of MusicDeskHere.exe (SPEC §29): the `musicdesk` keyword in File Explorer runs it."""

import sys

from music_desk.here import main

sys.exit(main())

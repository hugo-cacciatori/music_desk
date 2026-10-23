"""PyInstaller entry point (an absolute import, unlike __main__.py run as a script)."""

import sys

from music_desk.app import main

sys.exit(main())

"""PyInstaller entry point (an absolute import, unlike __main__.py run as a script)."""

import sys

from refrain.app import main

sys.exit(main())

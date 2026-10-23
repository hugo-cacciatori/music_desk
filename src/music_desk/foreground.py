"""Handing the focus to another process (SPEC §29). No Qt: MusicDeskHere.exe uses it too."""

ASFW_ANY = -1


def allow_foreground() -> None:
    """Let another process (the running MusicDesk) take the focus: Windows only lets the foreground process hand
    it over."""
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except (AttributeError, OSError):
        pass  # not Windows

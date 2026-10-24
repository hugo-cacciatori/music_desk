"""The right-click Clean up with MusicDesk not open (SPEC §31): the folders are read on a worker, then the Redundant
files window opens alone, as in the app; nothing found says so in a small window. The entry ends (`closed`) when
that window closes.
"""

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from shiboken6 import isValid

from music_desk.context_menu.notice import NoticeWindow
from music_desk.context_menu.tidy_entry import reveal
from music_desk.core import fsops
from music_desk.core.path_locks import PathLocks
from music_desk.ui.controllers.cleanup import INTRO, OWNER, CleanupFinder, Found, where_text
from music_desk.ui.recycle_window import RecycleWindow
from music_desk.ui.tools.tool_cards import CLEANUP, ToolCards


class CleanupEntry(QObject):
    closed = Signal()
    finished = Signal(object)  # list[Path]: the folders recycled from; an open main window's index catches up

    def __init__(self, cards: ToolCards, locks: PathLocks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cards = cards
        self.locks = locks
        self.finder = CleanupFinder(self)
        self.finder.found.connect(self._found)
        self.recycle = fsops.send_to_recycle_bin  # the window's Delete selected; tests replace it (a recorder)
        self.check_recycle = fsops.can_recycle
        self.offer: RecycleWindow | None = None
        self.notice: NoticeWindow | None = None
        self._token: int | None = None

    @property
    def running(self) -> bool:
        return self.offer is not None and self.offer.state == "deleting"

    def start(self, paths: list[str]) -> bool:
        """Read the folders; the window follows. False when nothing was read: a small window says why."""
        folders = [Path(path) for path in paths]
        if not folders:
            self._say("Nothing was clicked.")
            return False
        if self.cards.problems(CLEANUP):
            self._say("The Clean up options need fixing. They are in the right-click menu's MusicDesk > Settings.")
            return False
        self._token = self.finder.find(folders, self.cards.cleanup_options())
        return True

    def _found(self, found: Found) -> None:
        if found.token != self._token:
            return
        if found.error is not None:
            self._say(f"Clean up couldn't read {found.folder}: {found.error}")
            return
        if not any(item.kept is None for item in found.items):
            self._say(f"Nothing to clean up in {where_text(found.folders)}.")
            return
        self.offer = RecycleWindow(None, found.folder, found.items, locks=self.locks, owner=OWNER, intro=INTRO,
                                   recycle=self.recycle, check_recycle=self.check_recycle)
        self.offer.finished.connect(lambda report: self.finished.emit(report.folders))
        self.offer.revealRequested.connect(reveal)
        self.offer.closed.connect(self.closed)
        self.offer.closed.connect(self.offer.deleteLater)  # parentless: freed while Qt runs
        self.offer.show()
        self.offer.raise_()
        self.offer.activateWindow()

    def _say(self, text: str) -> None:
        self.notice = NoticeWindow("Clean up", text)
        self.notice.closed.connect(self.closed)
        self.notice.closed.connect(self.notice.deleteLater)
        self.notice.bring_to_front()

    def shutdown(self) -> None:
        for window in (self.offer, self.notice):
            if window is not None and isValid(window):  # not yet freed by its own close
                window.hide()
                window.deleteLater()
        self.finder.close()

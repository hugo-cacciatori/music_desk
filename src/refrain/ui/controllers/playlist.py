"""The playlist builder's controller (SPEC §15): a narrow column between the centre and the side panel, hidden until
asked, kept in `playlist.json` beside the settings, played through a temporary playlist and exported as a .m3u8."""

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog

from refrain.core import playlists
from refrain.core.playlist_builder import Builder
from refrain.ui.controllers import STATUS_MS
from refrain.ui.playlist_panel import PlaylistPanel


class PlaylistController(QObject):
    exported = Signal(str, int)  # the saved file, its track count (emitted from the shell thread)

    def __init__(self, window, conn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        settings = window.settings
        self.panel = PlaylistPanel(conn)
        self.file: Path | None = (settings.path.with_name("playlist.json")
                                  if settings.path is not None else None)  # None: not kept (tests)
        self._save = QTimer(self, singleShot=True, interval=500)
        self._save.timeout.connect(self.save)
        self.panel.model.changed.connect(self._save.start)
        self._refresh = QTimer(self, singleShot=True, interval=1000)  # the index changed: rows follow
        self._refresh.timeout.connect(self.panel.model.refresh)
        self.panel.model.added.connect(self.show_added)
        self.panel.playRequested.connect(self.play)
        self.panel.exportRequested.connect(self.export)
        self.panel.relative_paths.setChecked(settings.playlist_relative)
        self.panel.relative_paths.toggled.connect(lambda on: setattr(settings, "playlist_relative", on))
        self.exported.connect(self._exported)
        self.action = QAction("Playlist builder", window, checkable=True, shortcut=QKeySequence("Ctrl+Shift+P"))
        self.action.setToolTip("Show or hide the playlist builder (Ctrl+Shift+P)")
        self.action.toggled.connect(self.set_visible)

    def set_visible(self, visible: bool) -> None:
        """Show or hide the panel. It takes its width from the centre only, so the tree and the side panel keep
        theirs (the list scrolls sideways if its columns no longer fit), and gives it back when hidden."""
        if visible == self.panel.isVisible():
            return
        settings, splitter = self.window.settings, self.window.splitter
        sizes = splitter.sizes()
        if visible:
            width = min(max(settings.playlist_width, 200), max(0, sizes[1] - 200))
            sizes[1] -= width
            sizes[2] = width
            self.panel.show()
        else:
            if sizes[2] > 0:
                settings.playlist_width = sizes[2]
            sizes[1] += sizes[2]
            sizes[2] = 0
            self.panel.hide()
        splitter.setSizes(sizes)
        settings.playlist_visible = visible
        if self.action.isChecked() != visible:
            self.action.setChecked(visible)

    def show_added(self, added: int, skipped: int) -> None:
        if not added and not skipped:
            text = "Nothing to add: there is no music in what was dropped."
        else:
            text = f"Added {added:,} track{'s' if added != 1 else ''} to the playlist."
            if skipped:
                text += f" {skipped:,} already in it {'were' if skipped != 1 else 'was'} skipped."
        self.window.statusBar().showMessage(text, STATUS_MS)

    def play(self) -> None:
        """Through a temporary playlist, as Play selection (SPEC §14); tracks gone from the library are left out."""
        entries = self.panel.entries()
        self.window.play(entries, str(entries[0].path) if entries else "", False)

    def choose_export_file(self, suggested: str) -> str:
        """Replaced in tests. The dialog asks before replacing a file."""
        target, _filter = QFileDialog.getSaveFileName(self.window, "Export the playlist", suggested,
                                                      "M3U8 playlist (*.m3u8)")
        return target

    def export(self) -> None:
        """Save the playlist as a .m3u8 where the user chooses (a Ground rule 1 exception), off the UI thread."""
        settings = self.window.settings
        entries = self.panel.entries()
        if not entries:
            self.window.statusBar().showMessage("The playlist has no tracks to export.", STATUS_MS)
            return
        folder = Path(settings.playlist_export_dir or Path.home() / "Music")
        chosen = self.choose_export_file(str(folder / "Playlist.m3u8"))
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() != ".m3u8":
            target = target.with_name(target.name + ".m3u8")
        settings.playlist_export_dir = str(target.parent)
        relative = self.panel.relative_paths.isChecked()

        def run() -> None:
            playlists.export_playlist(entries, target, relative)
            self.exported.emit(str(target), len(entries))

        self.window.shell.run(run, f"The playlist couldn't be saved as {target}.", str(target.parent), True)

    def _exported(self, target: str, count: int) -> None:
        text = f"Exported {count:,} track{'s' if count != 1 else ''} to {target}."
        missing = self.panel.model.rowCount() - count
        if missing > 0:
            text += f" {missing} not in the library {'were' if missing != 1 else 'was'} left out."
        self.window.statusBar().showMessage(text, STATUS_MS)

    def restore(self) -> None:
        """Once the window's splitter exists: the columns, the tracks and the visibility as last left."""
        self.panel.restore_layout(self.window.settings.playlist_header)
        if self.file is not None:
            self.panel.model.set_builder(Builder.load(self.file))
        if self.window.settings.playlist_visible:
            self.set_visible(True)

    def save(self) -> None:
        if self.file is not None:
            self.panel.model.builder.save(self.file)

    def refresh_later(self) -> None:
        if self.panel.model.rowCount():
            self._refresh.start()

    def save_state(self) -> None:
        """The splitter's state is saved without the panel; its width, visibility and columns are kept apart."""
        settings = self.window.settings
        if self.panel.isVisible():
            self.set_visible(False)
            settings.playlist_visible = True
        settings.playlist_header = self.panel.save_layout()

    def shutdown(self) -> None:
        self._save.stop()
        self.save()

"""The main window's context menus (SPEC §5): the file list's and the folder tree's, with Explorer's right-click
entries as their first group (SPEC §31, §35)."""

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMenu

from refrain.context_menu.entries import OPEN, Selection, menu_entries
from refrain.context_menu.request import SETTINGS
from refrain.core.index import queries
from refrain.core.index.store import path_of
from refrain.ui.hover import with_status_tips


class MenuController(QObject):
    def __init__(self, window, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window

    def list_menu(self, ids: list[int]) -> QMenu:
        """The file list's menu: for the selection, or for the open folder when the click was on the background."""
        window = self.window
        file_view = window.file_view
        menu = QMenu(window)
        if not window._library_open():
            return with_status_tips(menu)
        if not ids:
            if window.folder_id is not None:
                self.add_entries(menu, [window.folder_id], background=True)
                self.add_folder_actions(menu, window.folder_id, include_open=False)
            return with_status_tips(menu)
        self.add_entries(menu, ids)
        if len(ids) == 1 and file_view.is_dir(ids[0]):
            self.add_folder_actions(menu, ids[0], rename=lambda: file_view.start_rename(ids[0]),
                                    delete=lambda: window.delete.delete_entries(ids, file_view.view))
            return with_status_tips(menu)
        open_action = menu.addAction("Open", lambda: window.open_files(ids))
        open_action.setEnabled(len(ids) == 1)
        play = menu.addAction("Play selection", lambda: window.play_selection(ids))
        play.setEnabled(bool(queries.playlist_for_ids(window.open_library.conn, ids)))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: window.reveal(ids[0]))
        menu.addAction("Copy path" if len(ids) == 1 else "Copy paths", lambda: window.copy_paths(ids))
        menu.addSeparator()
        if len(ids) == 1:
            menu.addAction("Rename\tF2", lambda: file_view.start_rename(ids[0]))
            menu.addSeparator()
        menu.addAction("Delete\tDel", lambda: window.delete.delete_entries(ids, file_view.view))
        return with_status_tips(menu)

    def add_folder_actions(self, menu: QMenu, entry_id: int, include_open: bool = True, rename=None,
                           delete=None) -> None:
        window = self.window
        if include_open:
            menu.addAction("Open", lambda: window.open_folder(entry_id))
        menu.addAction("Play folder", lambda: window.play_folder(entry_id, recursive=False))
        menu.addAction("Play folder and subfolders", lambda: window.play_folder(entry_id, recursive=True))
        menu.addSeparator()
        menu.addAction("Open in File Explorer", lambda: window.reveal(entry_id))
        menu.addAction("Copy path", lambda: window.copy_paths([entry_id]))
        menu.addSeparator()
        is_root = queries.parent_folder(window.open_library.conn, entry_id) is None
        if rename is not None and not is_root:
            menu.addAction("Rename\tF2", rename)
        menu.addAction("Rescan", lambda: window.open_library.indexer.request_folder(entry_id))
        if delete is not None and not is_root:
            menu.addSeparator()
            menu.addAction("Delete\tDel", delete)  # never a library root (Remove from library is below)

    def add_entries(self, menu: QMenu, ids: list[int], background: bool = False) -> None:
        """Explorer's right-click entries for these items (SPEC §31, §35: the same rules, labels and windows), as
        items of this menu itself, one group then a separator: no Refrain submenu in the app (Hugo, 2026-10-25).
        `background`: the open folder, as empty space. Open in Refrain and Settings are Explorer's only."""
        window = self.window
        paths, folders = self.entry_paths(ids)
        if not paths:
            return
        selection = Selection.of(paths, background, is_dir=folders.__contains__)  # the index knows, not the disk
        added = False
        for item in menu_entries(selection, window.tools.cards.replaygain_card.options().target):
            if item.entry in (OPEN, SETTINGS):
                continue
            action = menu.addAction(item.label, lambda entry=item.entry: window.entryRequested.emit(entry, paths))
            action.setStatusTip(item.help)
            added = True
        if added:
            menu.addSeparator()

    def entry_paths(self, ids: list[int]) -> tuple[list[str], set[str]]:
        """These entries' paths, in order, and which of them are folders (from the index: the disk isn't asked)."""
        conn = self.window.open_library.conn
        paths, folders = [], set()
        for entry_id in ids:
            row = conn.execute("SELECT is_dir FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                continue
            path = str(path_of(conn, entry_id))
            paths.append(path)
            if row[0]:
                folders.add(path)
        return paths, folders

    def tree_menu(self, menu: QMenu, entry_id: int) -> None:
        """The folder tree's menu: the entries for its selection if the clicked folder is in it, else for that
        folder (as Delete), then the folder's own actions."""
        tree = self.window.sidebar.tree
        selected = tree.selected_ids()
        self.add_entries(menu, selected if entry_id in selected else [entry_id])
        self.add_folder_actions(menu, entry_id, rename=lambda: tree.start_rename(entry_id),
                                delete=lambda: self._tree_delete(entry_id))

    def _tree_delete(self, entry_id: int) -> None:
        """Delete from the tree's menu: its selection if the clicked folder is in it, else that folder."""
        tree = self.window.sidebar.tree
        selected = tree.selected_ids()
        self.window.delete.delete_entries(selected if entry_id in selected else [entry_id], tree)

"""The main window's toolbars (SPEC §30, §33): the search row with the … menu, the navigation row with the address
bar, and the tools row with Tidy in evidence; their actions and keyboard shortcuts."""

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolBar, QToolButton, QWidget

from refrain.core.index import queries
from refrain.ui import theme
from refrain.ui.address_bar import AddressBar
from refrain.ui.hover import with_status_tips
from refrain.ui.qt_util import shortcut
from refrain.ui.toolbar_icons import tool_icon
from refrain.ui.tools.tool_cards import CLEANUP, RENAME, REPLAYGAIN, TIDY


class Toolbars(QObject):
    def __init__(self, window, conn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window
        self.back_action = QAction("Back", window)
        # Explicit keys: Qt's standard Back also binds Backspace, which goes up here (SPEC §5), and a key bound
        # twice is ambiguous and fires nothing.
        self.back_action.setShortcut(QKeySequence("Alt+Left"))
        self.back_action.triggered.connect(window.go_back)
        self.back_action.setToolTip("Go back to the folder you were in before (Alt+Left)")
        self.forward_action = QAction("Forward", window)
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(window.go_forward)
        self.forward_action.setToolTip("Go forward again to the folder you went back from (Alt+Right)")
        self.up_action = QAction("Up", window)
        self.up_action.setShortcut(QKeySequence("Alt+Up"))
        self.up_action.triggered.connect(window.go_up)
        self.up_action.setToolTip("Open the folder that contains this one (Alt+Up)")
        self.refresh_action = QAction("Rescan folder", window)
        self.refresh_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Refresh))
        self.refresh_action.triggered.connect(window.rescan_folder)
        self.refresh_action.setToolTip("Read this folder from the disk again now, to show changes made outside "
                                       "Refrain (F5)")
        # The folder tree is a side function (SPEC §33): hidden by default, one click shows it.
        self.folders_action = QAction("Folders", window, checkable=True, shortcut=QKeySequence("Ctrl+Shift+F"))
        self.folders_action.setToolTip("Show or hide the folder tree (Ctrl+Shift+F)")
        self.folders_action.toggled.connect(window.set_tree_visible)
        self.explorer_action = QAction("Open in File Explorer", window)
        self.explorer_action.setToolTip("Open this folder in File Explorer")
        self.explorer_action.triggered.connect(
            lambda: window.reveal(window.folder_id) if window.folder_id is not None else None)
        self.address_action = QAction("Edit address", window, shortcut=QKeySequence("Ctrl+L"))
        self.address_action.triggered.connect(lambda: self.address_bar.edit())
        window.addAction(self.address_action)

        self.address_bar = AddressBar(
            lambda entry_id: [(f.id, f.name) for f in queries.child_folders(
                conn(), entry_id, only_music=window.settings.hide_folders_without_music)] if conn() else [])
        self.address_bar.folderRequested.connect(window.open_folder)
        self.address_bar.pathEntered.connect(window.go_to_path)

        # The search first, on a row of its own, big and centred (SPEC §33); the address bar below, alone.
        search_bar = QToolBar("Search")
        search_bar.setMovable(False)
        search_bar.setObjectName("search")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)  # the toggle joined to the field
        layout.addStretch(1)
        layout.addWidget(window.search.modes)
        layout.addWidget(window.search_field, 6)  # most of the row, up to its widest
        layout.addStretch(1)
        self.more_button = QToolButton()
        self.more_button.setObjectName("moreButton")
        self.more_button.setAutoRaise(True)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setToolTip("Options…, About and Exit")
        layout.addWidget(self.more_button)
        # No menu bar (SPEC §36 task 3): the … button at the search row's right end holds Options…, About and Exit.
        # No Add folder… or Rescan library since 0.8.4 (SPEC §30); every option in one window, the Options… window.
        self.options_action = QAction("&Options…", window)
        self.options_action.triggered.connect(lambda: window.tools.open_options())
        self.about_action = QAction("&About Refrain", window)
        self.about_action.triggered.connect(window.open_about)
        self.exit_action = QAction("E&xit", window)
        self.exit_action.triggered.connect(window.close)
        more = QMenu(window)
        more.addAction(self.options_action)
        more.addAction(self.about_action)
        more.addSeparator()
        more.addAction(self.exit_action)
        self.more_button.setMenu(with_status_tips(more))
        search_bar.addWidget(row)
        self.search_row = row
        self.search_bar = search_bar
        self.search_action = QAction("Search library", window, shortcut=QKeySequence("Ctrl+K"))
        self.search_action.triggered.connect(window.search.open)
        window.addAction(self.search_action)
        window.addToolBar(search_bar)
        window.addToolBarBreak()

        toolbar = QToolBar("Navigation")
        toolbar.setMovable(False)
        toolbar.setObjectName("navigation")
        toolbar.addAction(self.folders_action)
        toolbar.addSeparator()
        for action in (self.back_action, self.forward_action, self.up_action, self.refresh_action):
            toolbar.addAction(action)
        toolbar.addWidget(self.address_bar)
        toolbar.addAction(self.explorer_action)
        for action in (self.folders_action, self.explorer_action):  # labelled: clear at a glance
            button = toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        window.addToolBar(toolbar)
        self.navigation_bar = toolbar
        window.addToolBarBreak()
        self.advanced_search_action = QAction("Advanced search", window)
        self.advanced_search_action.setToolTip("Open a window listing the albums to fix by hand, like those without "
                                               "ReplayGain tags or not split yet")
        self.advanced_search_action.triggered.connect(window.open_advanced_search)
        self.advanced_search_action.setEnabled(window._library_open())
        self._build_tools_bar()

        # Backspace goes up, but only from the lists (it edits text everywhere else).
        for view in (window.file_view.view, window.sidebar.tree):
            shortcut(QKeySequence(Qt.Key.Key_Backspace), view, window.go_up, Qt.ShortcutContext.WidgetShortcut)
        # Del sends the focused view's selection to the Recycle Bin. Shift+Del does the same: the app never
        # deletes permanently (SPEC §19). The playlist's own Del only removes tracks from the playlist.
        for view, selected in ((window.file_view.view, window.file_view.selected_ids),
                               (window.sidebar.tree, window.sidebar.tree.selected_ids),
                               (window.search_view.view, window.search_view.selected_ids)):
            for key in ("Del", "Shift+Del"):
                shortcut(QKeySequence(key), view,
                         lambda view=view, selected=selected: window.delete.delete_entries(selected(), view),
                         Qt.ShortcutContext.WidgetShortcut)

    def _build_tools_bar(self) -> None:
        """The second row (SPEC §30), always visible: Tidy, Rename, ReplayGain and Clean up, then the playlist
        builder and Advanced search. Tidy, Rename and Clean up are plain buttons starting their right-click entry on
        the selection or the open folder; ReplayGain lists its entries (SPEC §35). The options are in File >
        Options… only. Icons with a short label; Tidy first and in evidence (SPEC §33)."""
        window = self.window
        bar = QToolBar("Tools")
        bar.setMovable(False)
        bar.setObjectName("tools")
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools_bar = bar
        self.tool_actions: dict[str, QAction] = {}
        for tool, label in ((TIDY, "Tidy"), (RENAME, "Rename"), (REPLAYGAIN, "ReplayGain"), (CLEANUP, "Clean up")):
            action = QAction(label, window)
            if tool == REPLAYGAIN:  # its entries, as the right-click menu lists them (Hugo, 2026-10-25)
                entries = QMenu(window)
                entries.aboutToShow.connect(lambda entries=entries: window.tools.fill_replaygain_menu(entries))
                action.setMenu(entries)
            else:
                action.triggered.connect(lambda _=False, tool=tool: window.tools.start(tool))
            action.hovered.connect(lambda tool=tool: self.update_tool_tip(tool))
            bar.addAction(action)
            if tool == REPLAYGAIN and isinstance(button := bar.widgetForAction(action), QToolButton):
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)  # a click opens the list
            self.tool_actions[tool] = action
            self.update_tool_tip(tool)
        self.tidy_action = self.tool_actions[TIDY]
        self._set_tidy_in_evidence(bar)
        bar.addSeparator()
        window.playlist_action.setText("Playlist")
        bar.addAction(window.playlist_action)
        bar.addAction(self.advanced_search_action)
        window.addToolBar(bar)
        self.tint_icons()
        if (current := theme.current()) is not None:
            current.changed.connect(self.tint_icons)  # light and dark, or a new accent colour

    def tint_icons(self) -> None:
        """The toolbars' line drawings in the text colour, Tidy's in the colour that reads on the accent."""
        window = self.window
        color = window.palette().color(QPalette.ColorRole.ButtonText)
        for tool, action in self.tool_actions.items():
            action.setIcon(tool_icon(tool, theme.on_accent() if tool == TIDY else color))
        for name, action in (("playlist", window.playlist_action), ("advanced_search", self.advanced_search_action),
                             ("folders", self.folders_action), ("back", self.back_action),
                             ("forward", self.forward_action), ("up", self.up_action),
                             ("rescan", self.refresh_action), ("explorer", self.explorer_action)):
            action.setIcon(tool_icon(name, color))
        self.more_button.setIcon(tool_icon("more", color))
        window.search.search_icon.setIcon(tool_icon("search", color))

    def _set_tidy_in_evidence(self, bar: QToolBar) -> None:
        """Tidy, the tool the app is built around (SPEC §33, Hugo 2026-10-24): larger than the other buttons and
        filled with Windows' accent colour (theme.style_sheet), its icon and label in white or black, whichever
        reads on it."""
        button = bar.widgetForAction(self.tidy_action)
        if not isinstance(button, QToolButton):
            return
        button.setObjectName("tidyButton")
        button.setIconSize(bar.iconSize() * 1.5)
        font = theme.scaled(button.font(), theme.SCALE_TIDY)
        font.setBold(True)
        button.setFont(font)

    def update_tool_tip(self, tool: str) -> None:
        """The hover text names the target, or says why a click can't start, read as the mouse comes over the
        button (SPEC §30). The button stays enabled: a click says the same in the status bar."""
        self.tool_actions[tool].setToolTip(self.window.tools.hover_text(tool))

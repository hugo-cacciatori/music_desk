"""Typed settings, persisted as JSON in %LOCALAPPDATA%\\MusicDesk\\settings.json (SPEC §12).

Unknown keys are ignored and a value of the wrong type falls back to its default, so an old or hand-edited file
never stops the app. Saving writes a temporary file and replaces the old one, so a crash can't leave half a file.
There is no Settings dialog: each preference is set where it is used (a menu, a Tools card); the UI state
fields are kept by the windows themselves.
"""

import json
import logging
import os
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path

from music_desk import paths

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # Library
    hide_folders_without_music: bool = False
    # Lyrics
    lyrics_offline: bool = False
    lyrics_auto_save: bool = False
    # Tools (SPEC §10): the cards' options, as core.rename / core.replaygain options_to_json write them
    rename_options: dict = field(default_factory=dict)
    replaygain_options: dict = field(default_factory=dict)
    # UI state
    window_geometry: str = ""  # base64 of QWidget.saveGeometry()
    splitter_state: str = ""
    file_list_header: str = ""  # base64 of QHeaderView.saveState(): column order, sizes, hidden, sort
    expanded_folders: list[str] = field(default_factory=list)  # folder paths expanded in the tree
    last_folder: str = ""
    playlist_visible: bool = False  # the playlist builder's panel (SPEC §15)
    playlist_width: int = 300
    playlist_header: str = ""  # base64 of the panel's QHeaderView.saveState()

    path: Path | None = field(default=None, repr=False, compare=False)  # where save() writes; None: not saved

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or paths.app_data_dir() / "settings.json"
        settings = cls(path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return settings
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s): using the defaults", path, e)
            return settings
        if not isinstance(data, dict):
            return settings
        for f in fields(cls):
            if f.name == "path" or f.name not in data:
                continue
            value = data[f.name]
            default = f.default if f.default is not MISSING else f.default_factory()
            if isinstance(value, type(default)) and not (isinstance(value, bool) and not isinstance(default, bool)):
                setattr(settings, f.name, value)
            else:
                log.warning("settings: ignoring %s=%r (expected %s)", f.name, value, type(default).__name__)
        return settings

    def save(self) -> None:
        if self.path is None:
            return
        data = {k: v for k, v in asdict(self).items() if k != "path"}
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as e:
            log.warning("could not save %s: %s", self.path, e)

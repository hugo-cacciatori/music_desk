"""The third-party components shipped with the app and their licences (SPEC §12 About, §13). The texts are in
resources/licenses/, bundled with the app; the About dialog lists these and opens that folder."""

import platform
from dataclasses import dataclass
from importlib import metadata

from music_desk import paths


@dataclass(frozen=True)
class Component:
    name: str
    licence: str
    files: tuple[str, ...]  # in resources/licenses/
    distribution: str | None = None  # the Python package, for its installed version
    version: str | None = None  # when it isn't a Python package

    def installed_version(self) -> str:
        if self.version:
            return self.version
        try:
            return metadata.version(self.distribution) if self.distribution else ""
        except metadata.PackageNotFoundError:
            return ""


COMPONENTS: tuple[Component, ...] = (
    Component("Python", "PSF License 2.0", ("python-PSF.txt",), version=platform.python_version()),
    # Qt for Python is used under the LGPL 3.0, which supplements the GPL 3.0: both texts are shipped.
    Component("Qt for Python (PySide6, shiboken6)", "LGPL-3.0", ("LGPL-3.0.txt", "GPL-3.0.txt"),
              "PySide6-Essentials"),
    Component("mutagen", "GPL-2.0-or-later", ("mutagen-GPL-2.0.txt",), "mutagen"),
    Component("watchdog", "Apache-2.0", ("watchdog-Apache-2.0.txt", "watchdog-NOTICE.txt"), "watchdog"),
    Component("natsort", "MIT", ("natsort-MIT.txt",), "natsort"),
    Component("Send2Trash", "BSD-3-Clause", ("send2trash-BSD.txt",), "Send2Trash"),
    Component("rsgain", "BSD-2-Clause", ("rsgain-BSD-2-Clause.txt",), version="3.6"),
    Component("CRC++ (in rsgain)", "BSD-3-Clause", ("CRCpp-BSD.txt",)),
)


def licenses_dir():
    return paths.resource_path("licenses")

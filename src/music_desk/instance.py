"""One MusicDesk at a time (SPEC §29): a second start hands its folder to the running one and exits.

The running app listens on a local socket (a named pipe on Windows) named per Windows user; a run from source uses
its own name, so it never hands over to an installed MusicDesk. A message is one JSON line: {"folder": str | null}.
"""

import getpass
import json
import logging
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from music_desk.foreground import allow_foreground

log = logging.getLogger(__name__)

CONNECT_MS = 500  # a running app answers at once; a stale name times out quickly


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name: one name for all
        user = "user"
    suffix = "" if getattr(sys, "frozen", False) else "-dev"
    return f"MusicDesk-{user}{suffix}"


def hand_over(folder: str | None, name: str | None = None) -> bool:
    """Send `folder` (or None: just come to the front) to a running MusicDesk. False when none is running."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(CONNECT_MS):
        return False
    allow_foreground()
    socket.write(json.dumps({"folder": folder}).encode("utf-8") + b"\n")
    socket.flush()
    socket.waitForBytesWritten(CONNECT_MS)
    socket.disconnectFromServer()
    log.info("handed %s over to the running MusicDesk", folder or "nothing")
    return True


class InstanceServer(QObject):
    """Listens for later starts. `folderRequested` carries a folder path, or None (come to the front)."""

    folderRequested = Signal(object)

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._connected)
        self.listening = self.server.listen(name or server_name())
        if not self.listening:
            log.warning("single-instance server couldn't listen: %s", self.server.errorString())

    def close(self) -> None:
        self.server.close()

    def _connected(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()

            def read(socket: QLocalSocket = socket, buffer: bytearray = buffer) -> None:
                buffer.extend(bytes(socket.readAll().data()))
                if b"\n" not in buffer:
                    return
                line = bytes(buffer).split(b"\n", 1)[0]
                buffer.clear()
                socket.disconnectFromServer()
                try:
                    message = json.loads(line.decode("utf-8"))
                    folder = message.get("folder") if isinstance(message, dict) else None
                except (ValueError, UnicodeDecodeError):
                    log.warning("ignored a malformed message from another start")
                    return
                self.folderRequested.emit(folder if isinstance(folder, str) and folder else None)

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                read()

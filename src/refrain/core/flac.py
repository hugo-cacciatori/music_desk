"""flac.exe wrapper for the cue split (SPEC §13, §22): the startup self-check, cutting a track, and decoding to raw
PCM for the proof and the silence check.

flac runs like rsgain: no console window, below-normal priority, its output read by the calling thread, and
killed on Stop. Decoding a range of any source (FLAC, WAV or AIFF) pipes `flac -c -0` into `flac -d`, so every
source is read by flac itself, exactly as the tracks were cut from it.
"""

import logging
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

from refrain.core.cancel import CancelToken

log = logging.getLogger(__name__)

VERSION_TIMEOUT_S = 10
CHUNK = 1 << 20  # bytes of raw PCM read at a time; Stop is checked between chunks
POLL_S = 0.1
FLAGS = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
RAW = ("--force-raw-format", "--endian=little", "--sign=signed")  # the layout the FLAC STREAMINFO MD5 hashes


class FlacError(Exception):
    """flac failed or was stopped; the message is flac's own when it gave one."""


class Cancelled(FlacError):
    def __init__(self) -> None:
        super().__init__("Stopped.")


def flac_version(exe: Path) -> str | None:
    """`flac --version` ("flac 1.5.0"), or None if flac can't run (the split is then disabled)."""
    try:
        result = subprocess.run([str(exe), "--version"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=VERSION_TIMEOUT_S, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("flac self-check failed for %s: %s", exe, e)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        log.warning("flac self-check: %s exited with code %s: %s", exe, result.returncode, result.stderr.strip())
        return None
    return result.stdout.strip().splitlines()[0]


def _message(stderr: str) -> str:
    """flac's last meaningful line, without its own file-name prefix noise."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else "flac failed without a message"


class FlacRunner:
    """The real flac.exe. Tests use a fake with the same three methods."""

    def __init__(self, exe: Path) -> None:
        self.exe = exe

    def encode(self, source: Path, start: int, end: int | None, output: Path,
               cancel: CancelToken | None = None) -> None:
        """Cut samples [start, end) of `source` into `output` (FLAC, -8, verified while encoding with -V)."""
        command = [str(self.exe), "-8", "-V", "--silent", f"--skip={start}"]
        if end is not None:
            command.append(f"--until={end}")
        command += ["-o", str(output), str(source)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, creationflags=FLAGS)
        stderr = _Drain(process.stderr)
        while True:
            try:
                process.wait(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.cancelled:
                    process.kill()
                    process.wait()
                    raise Cancelled() from None
        if process.returncode != 0:
            raise FlacError(_message(stderr.result()))

    def decode(self, path: Path, cancel: CancelToken | None = None) -> Iterator[bytes]:
        """A FLAC file's samples as raw PCM (little-endian, signed, interleaved), in chunks."""
        return self._stream([[str(self.exe), "-d", "-c", "--silent", *RAW, str(path)]], cancel)

    def decode_range(self, source: Path, start: int = 0, end: int | None = None,
                     cancel: CancelToken | None = None) -> Iterator[bytes]:
        """Samples [start, end) of any source flac reads, as raw PCM, through flac itself."""
        encode = [str(self.exe), "-c", "-0", "--silent", f"--skip={start}"]
        if end is not None:
            encode.append(f"--until={end}")
        encode.append(str(source))
        return self._stream([encode, [str(self.exe), "-d", "-c", "--silent", *RAW, "-"]], cancel)

    @staticmethod
    def _stream(commands: list[list[str]], cancel: CancelToken | None) -> Iterator[bytes]:
        processes, drains = [], []
        stdin = subprocess.DEVNULL
        try:
            for command in commands:
                process = subprocess.Popen(command, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           creationflags=FLAGS)
                if processes:
                    processes[-1].stdout.close()  # only the next process reads it now
                processes.append(process)
                drains.append(_Drain(process.stderr))
                stdin = process.stdout
            out = processes[-1].stdout
            while chunk := out.read(CHUNK):
                if cancel is not None and cancel.cancelled:
                    raise Cancelled()
                yield chunk
            for process in processes:
                process.wait()
            for process, drain in zip(processes, drains, strict=True):
                if process.returncode != 0:
                    raise FlacError(_message(drain.result()))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            for process in processes:
                if process.stdout:
                    process.stdout.close()


class _Drain:
    """Reads a stream to its end on a thread, so a full stderr pipe never blocks flac."""

    def __init__(self, stream) -> None:
        self._data: list[bytes] = []
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream) -> None:
        for line in stream:
            self._data.append(line)

    def result(self) -> str:
        self._thread.join()
        return b"".join(self._data).decode("utf-8", errors="replace")

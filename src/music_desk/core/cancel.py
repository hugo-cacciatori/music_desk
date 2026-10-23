"""Cooperative cancellation for long tasks (SPEC §16: every long task takes a CancelToken)."""

import threading


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

"""A scripted stand-in for urllib.request.urlopen, so lyrics tests never reach LRCLIB."""

import io
import json
import threading
import urllib.error
import urllib.parse


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://lrclib.net/api", code, f"HTTP {code}", headers, None)


class FakeUrlopen:
    """Answers each request with the next scripted item: a JSON value, or an exception to raise. With `routes`,
    answers by endpoint ("get" or "search") instead, each with its own script."""

    def __init__(self, *script, routes: dict | None = None) -> None:
        self.script = list(script)
        self.routes = {k: list(v) for k, v in (routes or {}).items()}
        self.urls: list[str] = []
        self.user_agents: list[str] = []
        self.timeouts: list[float] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        self.user_agents.append(request.get_header("User-agent"))
        self.timeouts.append(timeout)
        if self.routes:
            endpoint = url.split("?", 1)[0].rsplit("/", 1)[1]
            queue = self.routes[endpoint]
        else:
            queue = self.script
        if not queue:
            raise AssertionError(f"unexpected request: {url}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return io.BytesIO(json.dumps(item).encode())


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def no_jitter(_low, _high) -> float:
    return 0.0


class ScriptedLrclib:
    """A thread-safe scripted LRCLIB: `answer(endpoint, params)` returns the JSON or raises."""

    def __init__(self, answer=None) -> None:
        self.answer = answer or found_for({"First"})
        self.requests: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, request, timeout=None):
        url = request.full_url
        endpoint = url.split("?", 1)[0].rsplit("/", 1)[1]
        params = {k: v[0] for k, v in urllib.parse.parse_qs(url.split("?", 1)[1]).items()}
        with self._lock:
            self.requests.append((endpoint, params))
        return io.BytesIO(json.dumps(self.answer(endpoint, params)).encode())

    def titles(self) -> list[str]:
        """Track titles in the order they were first asked for."""
        with self._lock:
            seen = []
            for _endpoint, params in self.requests:
                if params["track_name"] not in seen:
                    seen.append(params["track_name"])
            return seen

    def gets(self, title: str) -> int:
        with self._lock:
            return sum(1 for endpoint, p in self.requests if endpoint == "get" and p["track_name"] == title)


def found_for(titles: set[str]):
    def answer(endpoint, params):
        if endpoint == "get" and params["track_name"] in titles:
            return {"id": 1, "artistName": params["artist_name"], "trackName": params["track_name"],
                    "duration": 1, "instrumental": False, "plainLyrics": "la la", "syncedLyrics": None}
        if endpoint == "get":
            raise http_error(404)
        return []
    return answer


def unreachable(_endpoint, _params):
    raise urllib.error.URLError("no route")


def collect(signal) -> list:
    """The signal's emissions, as argument tuples. (qtbot.waitSignal can race with a worker thread that emits
    while the blocker is being set up.)"""
    received = []
    signal.connect(lambda *args: received.append(args))
    return received

"""A scripted stand-in for urllib.request.urlopen, so lyrics tests never reach LRCLIB."""

import io
import json
import urllib.error


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
